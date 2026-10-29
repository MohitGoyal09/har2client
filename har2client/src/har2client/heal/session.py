"""The short v2 recording of a repair task: the v2 reference client plays 4-10 API calls on a small v2 instance."""

from __future__ import annotations

import random
from typing import Sequence
from urllib.parse import urlparse

import httpx

from har2client.browser import Site, request_headers
from har2client.har import rewrite_host
from har2client.tierb import domains
from har2client.tierb.build import _rewrite_response_origins
from har2client.tierb.config import switch
from har2client.tierb.engine import TierBEngine
from har2client.tierb.player import REQUEST_GAP_S, REQUEST_TIMEOUT_S, THINK_TIME_S, TierBRecorder, load_reference_client
from har2client.tierb.spec import OPS, drift_fields, extra_fields, method_name

MIN_CALLS, MAX_CALLS = 4, 10
EXPIRED_CODES = ("token_expired", "token_invalid")  # verbose and terse error body of an expired session
RECORDING_ITEMS = (12, 20)  # two pages when the API pages (page size 10)
HARD_MAX_CALLS = 40  # v3: retries after a 429, job polls and a refresh call add requests to the 4-10 of a plain session
MAX_EXTRA_CALLS = 8
# v3 `partial` scope: the one operation whose requests show each change (drift: the list shows the wire form; a create shows
# the idempotency header and the job answer; a get shows the new item path; the rate limit and the refresh show on any call,
# so the list is repeated until they do)
PARTIAL_OP = {
    "add_drift_rename": "list", "add_drift_cents": "list", "add_drift_epoch": "list", "add_drift_nest": "list", "add_drift_enum": "list",
    "add_rate_limit": "list", "add_refresh_rotation": "list", "add_idempotency_key": "create", "add_async_create": "create", "add_moved": "get",
}


def _hooked_client(spec: dict, base_url: str, creds: dict, rng: random.Random, recorder: TierBRecorder):
    client = load_reference_client(spec)(base_url, creds["username"], creds["password"])
    client._http.close()
    hooks = {
        "request": [lambda request: recorder.tick(rng.uniform(*REQUEST_GAP_S)), *recorder.hooks["request"]],
        "response": recorder.hooks["response"],
    }
    client._http = httpx.Client(
        trust_env=False, timeout=REQUEST_TIMEOUT_S, event_hooks=hooks,
        headers=request_headers("fetch", recorder.site.api_host, recorder.site, include_host=False),
    )
    return client


def _play(spec: dict, base_url: str, creds: dict, rng: random.Random, recorder: TierBRecorder) -> dict:
    """list (all pages), get, create, update, delete: every call carries the changed behaviour (header, auth, paging,
    wrapper, ETag round trip) and, with token expiry, the session runs out once and the client logs in again."""
    client = _hooked_client(spec, base_url, creds, rng, recorder)
    call = lambda op, **kwargs: (recorder.tick(rng.uniform(*THINK_TIME_S)), getattr(client, method_name(spec, op))(**kwargs))[1]  # noqa: E731
    try:
        listed = call("list")
        first, target, doomed = rng.sample([i["id"] for i in listed], 3)
        call("get", id=first)
        call("create", body=domains.make_body(spec["domain"], rng))
        item = next(i for i in listed if i["id"] == target)
        call("update", id=target, changes=domains.make_changes(spec["domain"], rng, item))
        call("delete", id=doomed)
    finally:
        client.close()
    return {"listed": len(listed)}


def partial_ops(names: Sequence[str]) -> tuple[str, ...]:
    """The operations a `partial` recording of this release plays (in `OPS` order): the one operation of each change."""
    wanted = {PARTIAL_OP[n] for n in names}
    return tuple(op for op in OPS if op in wanted)


def _evidence_seen(spec: dict, log: list[dict]) -> bool:
    """A rate limit shows only when a request got its 429, a refresh rotation only when the refresh call happened."""
    cfg = spec["config"]
    if switch(cfg, "rate_limit") == "on" and not any(e["status"] == 429 for e in log):
        return False
    return not (switch(cfg, "refresh_rotation") == "on" and not any(e["op_id"] == "refresh" for e in log))


def _play_ops(spec: dict, base_url: str, creds: dict, rng: random.Random, recorder: TierBRecorder, engine: TierBEngine, ops: Sequence[str]) -> dict:
    """The user plays only `ops` (list, get, create, update, delete in that order), on items of the server's own data; when a
    rate limit or a refresh has not shown yet, the first read-only operation is repeated (up to `MAX_EXTRA_CALLS` times)."""
    client = _hooked_client(spec, base_url, creds, rng, recorder)
    call = lambda op, **kwargs: (recorder.tick(rng.uniform(*THINK_TIME_S)), getattr(client, method_name(spec, op))(**kwargs))[1]  # noqa: E731
    ids = [i["id"] for i in engine.initial]
    first, second, target, doomed = rng.sample(ids, 4)
    listed: list = []
    try:
        for op in (o for o in OPS if o in ops):
            if op == "list":
                listed = call("list")
            elif op == "get":
                call("get", id=first)
            elif op == "create":
                call("create", body=domains.make_body(spec["domain"], rng, extra_fields(spec)))
            elif op == "update":
                call("update", id=target, changes=domains.make_changes(spec["domain"], rng, engine.store[target], extra=extra_fields(spec), prefer=drift_fields(spec)))
            else:
                call("delete", id=doomed)
        repeat = "list" if "list" in ops else "get"
        for _ in range(MAX_EXTRA_CALLS):
            if _evidence_seen(spec, engine.log):
                break
            call(repeat, **({} if repeat == "list" else {"id": second}))
    finally:
        client.close()
    return {"listed": len(listed)}


def record_session(
    spec: dict, creds: dict, seed: int, rng: random.Random, *, ops: Sequence[str] | None = None, n_items: int | None = None
) -> tuple[dict, dict]:
    """(HAR of the API calls without noise, summary {requests, listed, expired, items}). Raises ValueError when the call
    count is outside 4-10 or, with token expiry, the session did not run out exactly once.
    v3 (`ops` given): the user plays only those operations; the call count may be up to `HARD_MAX_CALLS` and the expiry count is
    not checked (a token expiry of the base API is not one of the changes). `n_items` fixes the size of the recording's data."""
    n_items = rng.randint(*RECORDING_ITEMS) if n_items is None else n_items
    engine = TierBEngine(spec, seed, creds, n_items=n_items)
    base_url = engine.start()
    recorder = TierBRecorder(latency_rng=random.Random(rng.random()), site=Site.from_url(spec["server_url"]))
    try:
        summary = _play(spec, base_url, creds, rng, recorder) if ops is None else _play_ops(spec, base_url, creds, rng, recorder, engine, ops)
    finally:
        engine.stop()
    raw = recorder.har()
    summary["items"] = n_items
    summary["requests"] = len(raw["log"]["entries"])
    summary["expired"] = sum(1 for e in engine.log if isinstance(e["response"], dict) and e["response"].get("error") in EXPIRED_CODES)
    if ops is not None:
        if summary["requests"] > HARD_MAX_CALLS:
            raise ValueError(f"recording has {summary['requests']} API calls, expected at most {HARD_MAX_CALLS}")
        if not _evidence_seen(spec, engine.log):
            raise ValueError("the recording does not show the rate limit or the token refresh")
    else:
        if not MIN_CALLS <= summary["requests"] <= MAX_CALLS:
            raise ValueError(f"recording has {summary['requests']} API calls, expected {MIN_CALLS}-{MAX_CALLS}")
        if spec["config"]["token_expiry"] == "on" and summary["expired"] != 1:
            raise ValueError(f"expected exactly one token expiry in the recording, saw {summary['expired']}")
    har = rewrite_host(raw, spec["server_url"])
    port = urlparse(base_url).port
    return _rewrite_response_origins(har, f"http://127.0.0.1:{port}", f"https://{spec['host']}"), summary
