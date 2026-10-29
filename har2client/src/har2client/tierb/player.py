"""Play the recorded Tier B session: the reference client drives the engine through a browser-like, HAR-recording HTTP client."""

from __future__ import annotations

import random
from collections import Counter

import httpx

from har2client.browser import request_headers
from har2client.har import HarRecorder
from har2client.tierb import domains
from har2client.tierb.refclient import generate_client_source
from har2client.tierb.spec import drift_fields, extra_fields, method_name

THINK_TIME_S = (0.3, 4.0)
REQUEST_GAP_S = (0.05, 0.6)
REQUEST_TIMEOUT_S = 10.0
_BASE_HEADERS = {"date", "server", "content-length", "content-type", "cache-control", "access-control-allow-origin"}


class TierBRecorder(HarRecorder):
    """HarRecorder that also keeps the response headers the API features use (etag, link, set-cookie, csrf, ...)."""

    def _on_response(self, response: httpx.Response) -> None:
        super()._on_response(response)
        extra = [{"name": k, "value": v} for k, v in response.headers.multi_items() if k.lower() not in _BASE_HEADERS]
        self._entries[-1]["response"]["headers"].extend(extra)


def load_reference_client(spec: dict, flaws: tuple[str, ...] = ()):
    namespace: dict = {}
    exec(compile(generate_client_source(spec, flaws), "<tierb_ref_client>", "exec"), namespace)  # noqa: S102 - our own generated source
    return namespace["Client"]


def play_session(spec: dict, base_url: str, credentials: dict, rng: random.Random, recorder: TierBRecorder) -> dict:
    """Login (lazily), list with and without the filter (all pages), get 2 items, create, update, delete.
    Returns {requests, listed, filtered, created, updated, deleted_id}."""
    client = load_reference_client(spec)(base_url, credentials["username"], credentials["password"])
    client._http.close()
    hooks = {
        "request": [lambda request: recorder.tick(rng.uniform(*REQUEST_GAP_S)), *recorder.hooks["request"]],
        "response": recorder.hooks["response"],
    }
    client._http = httpx.Client(
        trust_env=False, timeout=REQUEST_TIMEOUT_S, event_hooks=hooks,
        headers=request_headers("fetch", recorder.site.api_host, recorder.site, include_host=False),
    )
    call = lambda op, **kwargs: (recorder.tick(rng.uniform(*THINK_TIME_S)), getattr(client, method_name(spec, op))(**kwargs))[1]  # noqa: E731
    try:
        field = spec["filter"]
        listed = call("list")
        biggest = Counter(i[field] for i in listed).most_common(1)[0][0]
        filtered = call("list", **{field: biggest})
        first, second, target, doomed = rng.sample([i["id"] for i in listed], 4)
        call("get", id=first)
        call("get", id=second)
        extra = extra_fields(spec)
        created = call("create", body=domains.make_body(spec["domain"], rng, extra))
        item = next(i for i in listed if i["id"] == target)
        updated = call("update", id=target, changes=domains.make_changes(spec["domain"], rng, item, extra=extra, prefer=drift_fields(spec)))
        call("delete", id=doomed)
    finally:
        client.close()
    return {"listed": listed, "filtered": filtered, "created": created, "updated": updated, "deleted_id": doomed}
