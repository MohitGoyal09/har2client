"""`failure.txt`: what running `client_v1.py` against a practice v2 instance looks like.

The practice instance is a separate engine with its own seed, data and credentials (never the grading instance).
The test calls run in the hidden-test order; the report shows the first call that fails: the exception (frames of the
client file only) or, when the call returned a wrong result without raising, what is wrong, and the last request
and response of that call."""

from __future__ import annotations

import copy
import importlib.util
import json
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from har2client.tierb.engine import TierBEngine
from har2client.tierb.grader import judge

BODY_CHARS = 1500
REQUEST_BODY_CHARS = 600
MIN_REDACTED_CHARS = 8
# request headers a browser-less HTTP library adds on its own; not what the client chose
_BORING_REQUEST_HEADERS = {"host", "accept-encoding", "connection", "user-agent", "content-length", "accept"}
_BORING_RESPONSE_HEADERS = {"content-length", "content-type"}


class NoFailure(RuntimeError):
    """The client passed every practice call (a repair task needs a failing v1 client)."""


class _PracticeEngine(TierBEngine):
    """TierBEngine that also logs the response headers (Link, ETag, WWW-Authenticate, ...) of every request."""

    def _decide(self, request, entry, *args):
        reply = super()._decide(request, entry, *args)
        entry["response_headers"] = dict(reply[2])
        return reply


def _load_client_class(path: Path):
    spec = importlib.util.spec_from_file_location("client_v1_practice", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Client


def _call(client: Any, test: dict) -> dict:
    result: dict[str, Any] = {"t_start": time.time()}
    try:
        value = getattr(client, test["method"])(**test["kwargs"])
        try:
            json.dumps(value)
            result.update(ok=True, **{"return": value})
        except (TypeError, ValueError):
            result.update(ok=True, unserializable=True, **{"return": repr(value)[:200]})
    except Exception as exc:  # noqa: BLE001 - the client is under test and can raise anything
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}", exception=exc)
    return result


def _client_frames(exc: BaseException, path: Path, shown_name: str) -> str:
    """Traceback text with only the frames of the client file."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == str(path)]
    lines = "".join(traceback.format_list(frames)).replace(str(path), shown_name)
    last = "".join(traceback.format_exception_only(type(exc), exc)).rstrip()
    return f"Traceback (most recent call last):\n{lines}{last}"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"... (cut, {len(text)} chars in all)"


def _render_request(entry: dict, spec: dict) -> str:
    query = ("?" + urlencode(entry["query"], doseq=True)) if entry["query"] else ""
    lines = [f"{entry['method']} {spec['server_url']}{entry['path']}{query}"]
    lines += [f"{k}: {v}" for k, v in entry["headers"].items() if k.lower() not in _BORING_REQUEST_HEADERS]
    if entry["body"] not in (None, {}, ""):
        lines += ["", _cut(json.dumps(entry["body"]) if not isinstance(entry["body"], str) else entry["body"], REQUEST_BODY_CHARS)]
    return "\n".join(lines)


def _render_response(entry: dict) -> str:
    lines = [f"HTTP {entry['status']}"]
    lines += [f"{k}: {v}" for k, v in entry.get("response_headers", {}).items() if k.lower() not in _BORING_RESPONSE_HEADERS]
    if entry["response"] is not None:
        lines += ["", _cut(json.dumps(entry["response"]), BODY_CHARS)]
    return "\n".join(lines)


def _call_text(test: dict) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in test["kwargs"].items())
    return f"client.{test['method']}({args})"


VALUE_REASONS = ("return_mismatch", "incomplete_list", "state_mismatch")


def _expected(test: dict, spec: dict, entries: list[dict], engine: TierBEngine) -> Any:
    """What the web app shows after this call: the canonical (old-API-shaped) value; None when there is nothing to show."""
    op, kwargs = test["op"], test["kwargs"]
    if op == "list":
        field, value = spec["filter"], kwargs.get(spec["filter"])
        return [i for i in engine.initial if value is None or i[field] == value]
    if op == "get":
        return next((i for i in engine.initial if i["id"] == kwargs["id"]), None)
    if op == "create":
        made = [e["created_id"] for e in entries if e.get("created_id")]
        return engine.store.get(made[0]) if made else None
    if op == "update":
        return engine.store.get(kwargs["id"])
    return engine.store.get(kwargs["id"])  # delete: the item, when it is still there


def _value_call(test: dict) -> str:
    return f"{test['method']}({', '.join(repr(v) for v in test['kwargs'].values())})"


def _first_difference(returned: Any, expected: Any) -> tuple[Any, Any, str]:
    """(returned, expected, where) of a list result: the first item that differs and its position."""
    for n, (r, e) in enumerate(zip(returned, expected)):
        if r != e:
            return r, e, f"item {n}"
    n = min(len(returned), len(expected))
    return (returned[n] if n < len(returned) else None), (expected[n] if n < len(expected) else None), f"item {n}"


def _value_report(first: dict, spec: dict) -> str:
    """`get_x('id') returned {...}; the web app shows {...}`: a silent change shows as a wrong value, never as its cause."""
    test, returned, expected = first["test"], first["result"]["return"], first["expected"]
    call = _value_call(test)
    if test["op"] == "list" and isinstance(returned, list) and isinstance(expected, list):
        r, e, where = _first_difference(returned, expected)
        return (f"{call} returned {len(returned)} items; {where} is {_cut(json.dumps(r), BODY_CHARS)}. "
                f"The web app shows {len(expected)} items; {where} is {_cut(json.dumps(e), BODY_CHARS)}.")
    if test["op"] == "delete":
        return f"{call} returned {json.dumps(returned)}; the web app still shows the {spec['singular']}."
    shown = _cut(json.dumps(expected), BODY_CHARS) if expected is not None else f"no new {spec['singular']}"
    return f"{call} returned {_cut(json.dumps(returned), BODY_CHARS)}; the web app shows {shown}."


def capture_failure(
    spec: dict, client_path: str | Path, seed: int, creds: dict, tests: list[dict], shown_name: str = "client_v1.py", *, show_values: bool = False
) -> dict:
    """Run `client_path` against a practice instance. Returns {text, index, reason, detail} for the first failing call.
    Raises NoFailure when every call passes.
    `show_values` (v3): a first failure that is a wrong value, not an exception, is shown as `get_x('id') returned {...}; the web
    app shows {...}` (the canonical value of the practice instance) and without the HTTP exchange, which would show the cause."""
    client_path = Path(client_path)
    engine = _PracticeEngine(spec, seed, creds)
    base_url = engine.start()
    first: dict | None = None
    try:
        client = _load_client_class(client_path)(base_url, creds["username"], creds["password"])
        for index, test in enumerate(tests):
            before = len(engine.log)
            result = _call(client, test)
            entries = engine.log[before:]
            reason, detail = judge(test, spec, result, entries, engine.store, engine.initial)
            if reason is not None and first is None:
                first = {"index": index, "test": test, "result": result, "entries": list(entries), "reason": reason, "detail": detail}
                if show_values and result["ok"] and reason in VALUE_REASONS:
                    first["expected"] = copy.deepcopy(_expected(test, spec, entries, engine))
        if hasattr(client, "close"):
            client.close()
    finally:
        engine.stop()
    if first is None:
        raise NoFailure(f"{client_path.name} passed every practice call")
    parts = urlsplit(base_url)
    text = _redact(_render(first, spec, client_path, shown_name, len(tests)), spec, creds, f"{parts.scheme}://{parts.netloc}")
    return {"text": text, "index": first["index"], "reason": first["reason"], "detail": first["detail"]}


def _render(first: dict, spec: dict, client_path: Path, shown_name: str, total: int) -> str:
    result, entries = first["result"], first["entries"]
    out = [
        f"Ran {shown_name} against a practice server of the changed API: {spec['server_url']}",
        "(The practice server has its own data and its own login. Grading uses a different server.)",
        "",
        f"Call {first['index'] + 1} of {total} failed:",
        f"  {_call_text(first['test'])}",
        "",
    ]
    if not result["ok"]:
        out += ["Exception:", _indent(_client_frames(result["exception"], client_path, shown_name))]
    elif "expected" in first:
        return "\n".join([*out, "The call returned without an exception, but the result is wrong:", _indent(_value_report(first, spec))]) + "\n"
    else:
        out += [f"The call returned without an exception, but the result is wrong: {first['detail']}."]
    out.append("")
    if entries:
        out += ["Last request of this call:", _indent(_render_request(entries[-1], spec)), "", "Its response:", _indent(_render_response(entries[-1]))]
    else:
        out.append("The client sent no request during this call.")
    return "\n".join(out) + "\n"


def _indent(text: str) -> str:
    return "\n".join("  " + line if line else line for line in text.splitlines())


def _redact(text: str, spec: dict, creds: dict, loopback_origin: str) -> str:
    text = text.replace(loopback_origin, f"https://{spec['host']}").replace(loopback_origin.split("//", 1)[1], spec["host"])
    for value, label in ((creds["password"], "<password>"), (creds["username"], "<username>")):
        if len(value) >= MIN_REDACTED_CHARS:  # generated credentials are long; a short value would garble the text
            text = text.replace(value, label)
    return text
