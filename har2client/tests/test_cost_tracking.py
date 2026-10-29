import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest

from har2client.solve.cost_tracking import CostLimitReached, CostTracker

MODEL = 'org/model:provider'
PRICES = {MODEL: dict(input=1, output=2, source='test catalogue', as_of='2026-10-28', provider='provider')}


def client(fail=False, usage=True):
    def create(**request):
        if fail:
            raise RuntimeError('upstream failed')
        data = NS(model='served-model', id='response-id', usage=None)
        if usage:
            data.usage = NS(prompt_tokens=1000, completion_tokens=100,
                            prompt_tokens_details=NS(cached_tokens=500),
                            completion_tokens_details=NS(reasoning_tokens=75))
        return NS(parse=lambda: data, headers={'x-request-id': 'request-id'})
    return NS(chat=NS(completions=NS(with_raw_response=NS(create=create))))


def send(tracker, upstream=None):
    with tracker.context(task_id='task-1', model_slug='model-high'):
        return tracker.wrap(upstream or client()).chat.completions.with_raw_response.create(
            model=MODEL, messages=[{'role': 'user', 'content': 'private prompt'}], reasoning_effort='high')


def test_estimate_counts_reasoning_once_and_does_not_claim_invoice_cost(tmp_path):
    ledger = CostTracker(tmp_path / 'ledger.jsonl', PRICES)
    send(ledger)
    row = json.loads(ledger.path.read_text().splitlines()[-1])
    assert row['estimated_cost_usd'] == pytest.approx(.0012)
    assert row['billed_cost_usd'] is None and row['reasoning_tokens'] == 75
    assert row['request_id'] == 'request-id' and row['task_id'] == 'task-1'
    assert 'private prompt' not in ledger.path.read_text()


def test_retries_and_failed_requests_are_retained_as_unknown_cost(tmp_path):
    ledger = CostTracker(tmp_path / 'ledger.jsonl', PRICES)
    with pytest.raises(RuntimeError):
        send(ledger, client(fail=True))
    send(ledger)
    summary = ledger.write_summary()
    assert summary['request_attempts'] == 2 and summary['error_attempts'] == 1
    assert summary['requests_with_unknown_cost'] == 1
    assert summary['estimated_cost_usd'] == pytest.approx(.0012)


def test_resume_does_not_reset_observed_cost_and_enforces_soft_limit(tmp_path):
    path = tmp_path / 'ledger.jsonl'
    send(CostTracker(path, PRICES))
    resumed = CostTracker(path, PRICES, limit_usd=.001)
    with pytest.raises(CostLimitReached):
        send(resumed)
    assert resumed.summary()['request_attempts'] == 1


def test_missing_usage_or_prices_does_not_become_zero_cost(tmp_path):
    tracker = CostTracker(tmp_path / 'ledger.jsonl', {})
    send(tracker)
    send(tracker, client(usage=False))
    assert tracker.summary()['requests_with_unknown_cost'] == 2
    assert all(json.loads(line)['estimated_cost_usd'] is None for line in tracker.path.read_text().splitlines())


def test_concurrent_calls_keep_every_attempt_and_thread_context(tmp_path):
    tracker = CostTracker(tmp_path / 'ledger.jsonl', PRICES)
    def call(i):
        with tracker.context(task_id=str(i)):
            tracker.wrap(client()).chat.completions.with_raw_response.create(model=MODEL)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(call, range(20)))
    rows = [json.loads(line) for line in tracker.path.read_text().splitlines()]
    assert len(rows) == 40 and {r['task_id'] for r in rows} == {str(i) for i in range(20)}
    assert tracker.summary()['request_attempts'] == 20


def test_malformed_ledger_line_is_reported(tmp_path):
    path = tmp_path / 'ledger.jsonl'
    path.write_text('{broken}\n')
    tracker = CostTracker(path, PRICES)
    send(tracker)
    assert tracker.summary()['unreadable_ledger_lines'] == 1


def test_inflight_request_reservation_blocks_concurrent_overspend(tmp_path):
    import threading

    started, release = threading.Event(), threading.Event()
    prices = {MODEL: dict(input=1, output=2, context_length=1000)}
    tracker = CostTracker(tmp_path / 'ledger.jsonl', prices, limit_usd=.0015)
    def create(**kwargs):
        started.set()
        assert release.wait(5)
        return client().chat.completions.with_raw_response.create(**kwargs)
    upstream = NS(chat=NS(completions=NS(with_raw_response=NS(create=create))))
    wrapped = tracker.wrap(upstream)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(wrapped.chat.completions.with_raw_response.create, model=MODEL, max_tokens=100)
        try:
            assert started.wait(5)
            with pytest.raises(CostLimitReached):
                wrapped.chat.completions.with_raw_response.create(model=MODEL, max_tokens=100)
        finally:
            release.set()
        first.result()
    assert tracker.summary()['request_attempts'] == 1
    assert tracker.summary()['budget_charge_usd'] == pytest.approx(.0012)


def test_unfinished_request_is_still_reserved_after_restart(tmp_path):
    path = tmp_path / 'ledger.jsonl'
    path.write_text(json.dumps(dict(event_id='pending', state='reserved', budget_charge_usd=.9)) + '\n')
    tracker = CostTracker(path, {MODEL: dict(input=1, output=2, context_length=1000)}, limit_usd=.9)
    with pytest.raises(CostLimitReached):
        tracker.wrap(client()).chat.completions.with_raw_response.create(model=MODEL, max_tokens=100)
    assert tracker.summary()['pending_or_interrupted_requests'] == 1


def test_unknown_failed_cost_reserves_full_request_bound(tmp_path):
    tracker = CostTracker(tmp_path / 'ledger.jsonl',
                          {MODEL: dict(input=1, output=2, context_length=1000)}, limit_usd=.01)
    with pytest.raises(RuntimeError):
        tracker.wrap(client(fail=True)).chat.completions.with_raw_response.create(model=MODEL, max_tokens=100)
    assert tracker.summary()['estimated_cost_usd'] == 0
    assert tracker.summary()['budget_charge_usd'] == pytest.approx(.0012)
    assert tracker.summary()['requests_with_unknown_cost'] == 1
