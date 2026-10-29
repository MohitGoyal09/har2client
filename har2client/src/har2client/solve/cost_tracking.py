"""Append-only request accounting. Token-price estimates are not billing invoices."""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def numeric(obj):
    try:
        number = float(obj)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


class CostLimitReached(RuntimeError):
    pass


class CostTracker:
    def __init__(self, path, prices, *, limit_usd=None):
        self.path, self.prices = Path(path), prices
        self.limit_usd = limit_usd
        self.lock, self.local = threading.Lock(), threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.summary()
        self.estimated_usd = summary['estimated_cost_usd']
        self.budget_used_usd = summary['budget_charge_usd']
        self.reserved_usd = 0
        if limit_usd is not None and (summary['unbounded_cost_requests'] or summary['unreadable_ledger_lines']):
            raise ValueError('Existing ledger contains unbounded unknown costs; reconcile before using a capped budget.')

    def upper_bound(self, request):
        rates = self.prices.get(request.get('model'), {})
        context = numeric(rates.get('context_length'))
        output = numeric(request.get('max_tokens') or request.get('max_completion_tokens'))
        price_in, price_out = numeric(rates.get('input')), numeric(rates.get('output'))
        if None in (context, output, price_in, price_out):
            return None
        return (context * price_in + output * price_out) / 1_000_000

    @contextmanager
    def context(self, **fields):
        previous = getattr(self.local, 'fields', {})
        self.local.fields = fields
        try:
            yield
        finally:
            self.local.fields = previous

    def wrap(self, client):
        tracker = self

        def create(**request):
            bound = tracker.upper_bound(request)
            request_id = uuid.uuid4().hex
            with tracker.lock:
                if tracker.limit_usd is not None:
                    if bound is None:
                        raise CostLimitReached('No provider/context/output bounds available; refusing an unbounded paid request.')
                    if tracker.budget_used_usd + tracker.reserved_usd + bound > tracker.limit_usd:
                        raise CostLimitReached('Reserved tariff budget reached; no new paid request sent.')
                tracker.reserved_usd += bound or 0
                # A crash/interrupt cannot erase an in-flight request's budget.
                reservation = dict(event_id=request_id, state='reserved', requested_model=request.get('model'),
                                   budget_charge_usd=bound, estimated_cost_usd=None,
                                   timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                   **getattr(tracker.local, 'fields', {}))
                with tracker.path.open('a', encoding='utf-8') as fh:
                    fh.write(json.dumps(reservation) + '\n')
                    fh.flush()
            start = time.monotonic()
            try:
                try:
                    raw = client.chat.completions.with_raw_response.create(**request)
                    response = raw.parse()
                except BaseException as exc:
                    headers = getattr(getattr(exc, 'response', None), 'headers', {}) or {}
                    tracker.record(request, None, headers, start, 'error', getattr(exc, 'status_code', None), bound, request_id)
                    raise
                tracker.record(request, response, getattr(raw, 'headers', {}) or {}, start, 'response', 200, bound, request_id)
                return raw
            finally:
                with tracker.lock:
                    tracker.reserved_usd -= bound or 0

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create))))

    def record(self, request, response, headers, start, state, http_status, bound, request_id):
        model = request.get('model')
        rates = self.prices.get(model, {})
        usage = value(response, 'usage')
        prompt, output = numeric(value(usage, 'prompt_tokens')), numeric(value(usage, 'completion_tokens'))
        details = value(usage, 'prompt_tokens_details')
        cached = numeric(value(details, 'cached_tokens'))
        reasoning = numeric(value(value(usage, 'completion_tokens_details'), 'reasoning_tokens'))
        price_in, price_out = numeric(rates.get('input')), numeric(rates.get('output'))
        estimate = None
        if None not in (prompt, output, price_in, price_out):
            # completion_tokens already includes reasoning. Cache discounts are
            # not assumed without an explicit cached-input tariff.
            estimate = (prompt * price_in + output * price_out) / 1_000_000
        event = dict(event_id=request_id, timestamp_utc=datetime.now(timezone.utc).isoformat(),
                     state=state, http_status=http_status, requested_model=model,
                     response_model=value(response, 'model'), reasoning_effort=request.get('reasoning_effort'),
                     request_id=headers.get('x-request-id') or headers.get('x-amzn-requestid') or value(response, 'id'),
                     prompt_tokens=prompt, completion_tokens=output, reasoning_tokens=reasoning,
                     cached_prompt_tokens=cached, estimated_cost_usd=estimate,
                     reserved_upper_usd=bound, budget_charge_usd=estimate if estimate is not None else bound,
                     price_in_per_mtok=price_in, price_out_per_mtok=price_out,
                     pricing_source=rates.get('source'), pricing_as_of=rates.get('as_of'),
                     provider=rates.get('provider'), billed_cost_usd=None,
                     wall_time_s=round(time.monotonic() - start, 3),
                     **getattr(self.local, 'fields', {}))
        with self.lock:
            with self.path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(event) + '\n')
                fh.flush()
            self.estimated_usd += estimate or 0
            self.budget_used_usd += event['budget_charge_usd'] or 0

    def summary(self):
        events = []
        unreadable = 0
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    unreadable += 1
        events = list({e['event_id']: e for e in events}.values())
        totals = dict(request_attempts=len(events), error_attempts=sum(e.get('state') == 'error' for e in events),
                      pending_or_interrupted_requests=sum(e.get('state') == 'reserved' for e in events),
                      estimated_cost_usd=sum(e.get('estimated_cost_usd') or 0 for e in events),
                      requests_with_unknown_cost=sum(e.get('estimated_cost_usd') is None for e in events),
                      prompt_tokens=sum(e.get('prompt_tokens') or 0 for e in events),
                      completion_tokens=sum(e.get('completion_tokens') or 0 for e in events),
                      reasoning_tokens=sum(e.get('reasoning_tokens') or 0 for e in events),
                      unreadable_ledger_lines=unreadable, billed_cost_usd=None,
                      budget_charge_usd=sum(e.get('budget_charge_usd', e.get('estimated_cost_usd')) or 0 for e in events),
                      unbounded_cost_requests=sum(e.get('budget_charge_usd', e.get('estimated_cost_usd')) is None for e in events),
                      note='Token-rate estimate; reasoning included in output; no cache discount assumed. '
                           'Unknown/error-attempt costs are not zero. Reconcile final charges with HF billing.')
        groups = {}
        for event in events:
            key = event.get('model_slug') or event.get('requested_model') or 'unknown'
            group = groups.setdefault(key, dict(request_attempts=0, estimated_cost_usd=0, unknown_cost_requests=0))
            group['request_attempts'] += 1
            group['estimated_cost_usd'] += event.get('estimated_cost_usd') or 0
            group['unknown_cost_requests'] += event.get('estimated_cost_usd') is None
        totals['by_model'] = groups
        return totals

    def write_summary(self):
        report = self.summary()
        self.path.with_suffix('.summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return report
