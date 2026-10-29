"""Play a scripted user session against a mock server; also builds API requests from op args."""

from __future__ import annotations

import base64
import random
import re
from typing import Any
from urllib.parse import quote

import httpx

from har2client.browser import DEFAULT_SERVER_URL, Site, request_headers
from har2client.datagen import generate, pin_first_option
from har2client.har import HarRecorder

OPTIONAL_P = 0.5
MIN_CALLS, MAX_CALLS = 1, 3
THINK_TIME_S = (0.3, 4.0)
REQUEST_TIMEOUT_S = 10.0
_PATH_VAR = re.compile(r"\{([^}]+)\}")


def wire(value: Any) -> str:
    """String form of a scalar as sent on the wire (matches httpx query encoding)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _path_safe(value: Any) -> Any:
    text = wire(value).replace("/", "-")
    if text in ("", ".", ".."):
        return "x"
    return value if text == wire(value) else text


def gen_args(op: dict, rng: random.Random, optional_p: float = OPTIONAL_P, include: frozenset | set = frozenset()) -> dict:
    """Fresh call arguments keyed by py_name (plus `body`). Required always; optional with p."""
    kwargs: dict[str, Any] = {}
    for param in op["params"]:
        if param["required"] or param["py_name"] in include or rng.random() < optional_p:
            value = generate(pin_first_option(param["schema"]), rng, allow_null=False)
            kwargs[param["py_name"]] = _path_safe(value) if param["in"] == "path" else value
    body = op["body"]
    if body and (body["required"] or "body" in include or rng.random() < optional_p):
        value = generate(body["schema"], rng, allow_null=False)
        kwargs["body"] = value if isinstance(value, dict) else {}
    return kwargs


def build_request(op_table: dict, op: dict, kwargs: dict, secret: str) -> dict:
    """HTTP request for one call: method, path (percent-encoded), query list, json body, headers."""
    by_name = {p["name"]: p for p in op["params"] if p["in"] == "path"}
    values = {name: kwargs[p["py_name"]] for name, p in by_name.items()}
    path = _PATH_VAR.sub(lambda m: quote(wire(values[m.group(1)]), safe=""), op["path"])
    query: list[tuple[str, str]] = []
    for param in op["params"]:
        if param["in"] != "query" or param["py_name"] not in kwargs:
            continue
        value = kwargs[param["py_name"]]
        for item in value if isinstance(value, list) else [value]:
            query.append((param["name"], wire(item)))
    headers: dict[str, str] = {}
    sec = op_table["security"]
    if sec["type"] == "apiKey" and sec["in"] == "query":
        query.append((sec["name"], secret))
    elif sec["type"] == "apiKey":
        headers[sec["name"]] = secret
    elif sec["type"] == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif sec["type"] == "basic":
        headers["Authorization"] = "Basic " + base64.b64encode(secret.encode()).decode()
    return {"method": op["method"], "path": path, "query": query, "body": kwargs.get("body"), "headers": headers}


def _session_calls(op: dict, rng: random.Random) -> list[dict]:
    """1-3 calls; the last one adds every optional arg not yet seen so the HAR shows all params."""
    count = rng.randint(MIN_CALLS, MAX_CALLS)
    optional = {p["py_name"] for p in op["params"] if not p["required"]}
    if op["body"] and not op["body"]["required"]:
        optional.add("body")
    seen: set[str] = set()
    calls = []
    for i in range(count):
        include = optional - seen if i == count - 1 else frozenset()
        kwargs = gen_args(op, rng, include=include)
        seen |= set(kwargs)
        calls.append(kwargs)
    return calls


def play_session(
    op_table: dict,
    ops: list,
    base_url: str,
    secret: str,
    rng: random.Random,
    *,
    recorder: HarRecorder | None = None,
    site: Site | None = None,
) -> list[dict]:
    """Call each op 1-3 times, with the browser fetch headers of `site` (default: the recorder's). `ops` holds op ids or op dicts. Returns one dict per call:
    {op_id, kwargs, status, response}. Raises RuntimeError on a non-2xx answer."""
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    resolved = [by_id[o] if isinstance(o, str) else o for o in ops]
    hooks = recorder.hooks if recorder else {}
    site = site or (recorder.site if recorder else Site.from_url(DEFAULT_SERVER_URL))
    results = []
    with httpx.Client(
        trust_env=False,
        timeout=REQUEST_TIMEOUT_S,
        headers=request_headers("fetch", site.api_host, site, include_host=False),
        event_hooks=hooks,
    ) as client:
        for op in resolved:
            for kwargs in _session_calls(op, rng):
                if recorder:
                    recorder.tick(rng.uniform(*THINK_TIME_S))
                req = build_request(op_table, op, kwargs, secret)
                resp = client.request(
                    req["method"],
                    base_url.rstrip("/") + req["path"],
                    params=req["query"] or None,
                    json=req["body"],
                    headers=req["headers"],
                )
                if not resp.is_success:
                    raise RuntimeError(f"session call {op['op_id']} got {resp.status_code}: {resp.text[:200]}")
                results.append(
                    {
                        "op_id": op["op_id"],
                        "kwargs": kwargs,
                        "status": resp.status_code,
                        "response": resp.json() if resp.content else None,
                    }
                )
    return results
