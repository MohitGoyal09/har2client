"""Grade a client.py against a fresh mock of the task's API (see DESIGN.md)."""

from __future__ import annotations

import base64
import json
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from har2client import runner
from har2client.mock import MockServer
from har2client.player import wire

MAX_INVENTED_LISTED = 20


def _same(a: object, b: object) -> bool:
    """JSON-exact equality (True != 1, 1 != 1.0)."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _credential_values(secret: str) -> set[str]:
    """Values that count as 'the credential the client was given' (whole secret, or either basic-auth part)."""
    return {secret, *secret.split(":")} - {""}


def _args_ok(op: dict, auth: dict, kwargs: dict, entry: dict, secret: str = "") -> str | None:
    """None when the logged request matches the call arguments, else a short reason."""
    for param in op["params"]:
        if param["in"] == "path" and entry["path_params"].get(param["name"]) != wire(kwargs.get(param["py_name"])):
            return f"path param {param['name']}"
    expected: dict[str, list[str]] = {}
    for param in op["params"]:
        value = kwargs.get(param["py_name"])
        if param["in"] == "query" and value is not None and value != []:  # an empty list puts nothing on the wire
            expected[param["name"]] = [wire(v) for v in value] if isinstance(value, list) else [wire(value)]
    creds = _credential_values(secret)

    def is_auth_param(k: str, v: list[str]) -> bool:
        if auth["type"] == "apiKey" and auth["in"] == "query" and k == auth["name"]:
            return True
        return k not in expected and bool(v) and all(x in creds for x in v)  # credential in an unlisted param

    actual = {k: v for k, v in entry["query"].items() if not is_auth_param(k, v)}
    if actual != expected:
        return f"query {actual} != {expected}"
    if "body" in kwargs:
        body_ok = _same(entry["body"], kwargs["body"])
    else:
        body_ok = entry["body"] is None or entry["body"] == {}
    if not body_ok:
        return "body differs"
    return None


def _judge(test: dict, op: dict, auth: dict, result: dict | None, entries: list[dict], secret: str = "") -> tuple[str | None, str]:
    """(failure reason or None, detail)."""
    if result is None:
        return "timeout", "no result before the time limit"
    mine = [e for e in entries if e["op_id"] == op["op_id"]]
    good = [e for e in mine if 200 <= e["status"] < 300]
    if not good:
        if not entries:
            return ("exception", result.get("error", "")) if not result["ok"] else ("no_request", "client sent nothing")
        for status, reason in ((401, "auth_401"), (400, "bad_request_400"), (404, "not_found_404")):
            if any(e["status"] == status for e in mine):
                return reason, f"{status} on {op['op_id']}"
        if any(e["op_id"] is None for e in entries):
            return "not_found_404", "request matched no route"
        return "wrong_op", "requests went to " + ", ".join(sorted({str(e["op_id"]) for e in entries}))
    problems = [_args_ok(op, auth, test["kwargs"], e, secret) for e in good]
    if all(problems):
        return "args_mismatch", problems[0]
    entry = good[problems.index(None)]
    if not result["ok"]:
        return "exception", result.get("error", "")
    if result.get("unserializable") or not _same(result["return"], entry["response"]):
        return "return_mismatch", "returned value differs from the server response"
    return None, ""


def _windows(results: list[dict]) -> dict[int, tuple[float, float]]:
    ordered = sorted(results, key=lambda r: r["index"])
    out = {}
    for i, r in enumerate(ordered):
        end = ordered[i + 1]["t_start"] if i + 1 < len(ordered) else float("inf")
        out[r["index"]] = (r["t_start"], end)
    return out


def strict_flags(tests_total: int, by_index: dict[int, dict], windows: dict[int, tuple[float, float]], log: list[dict], is_invented) -> dict[int, int]:
    """Invented requests per test index, by time window. Requests logged before the first test starts
    (client constructor) count for the first test, so building the client cannot hide a stray call."""
    first = min(windows, default=None)
    counts = {}
    for index in range(tests_total):
        if index not in by_index:
            counts[index] = 0
            continue
        lo, hi = windows[index]
        lo = float("-inf") if index == first else lo
        counts[index] = sum(1 for e in log if lo <= e["ts"] < hi and is_invented(e))
    return counts


def _secret_needles(secret: str) -> list[str]:
    needles = [secret, base64.b64encode(secret.encode()).decode()]
    if ":" in secret:
        needles += secret.split(":")
    return [n for n in needles if n]


def grade(task_dir: str | Path, client_path: str | Path, timeout: float = runner.DEFAULT_TIMEOUT_S) -> dict:
    task_dir, client_path = Path(task_dir), Path(client_path)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    if task.get("tier") == "B":
        from har2client.tierb.grader import grade_tierb  # imported here: tierb needs `runner`, not this module

        return grade_tierb(task_dir, client_path, timeout, task=task)
    table = json.loads((task_dir / "op_table.json").read_text(encoding="utf-8"))
    tests = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
    by_id = {o["op_id"]: o for o in table["operations"]}
    started = time.time()

    mock = MockServer(table, seed=task["seeds"]["test"], secret=task["test_secret"])
    base_url = mock.start()
    try:
        run = runner.run_client(client_path, base_url, task["test_secret"], tests, timeout_s=timeout)
    finally:
        mock.stop()
    log = list(mock.log)

    by_index = {r["index"]: r for r in run["results"]}
    windows = _windows(run["results"])
    goal = set(task["goal_ops"])

    def is_invented(e: dict) -> bool:
        return e["op_id"] is None or e["op_id"] not in goal

    stray = strict_flags(len(tests), by_index, windows, log, is_invented)
    results = []
    for index, test in enumerate(tests):
        result = by_index.get(index)
        entries = []
        if result is not None:
            lo, hi = windows[index]
            entries = [e for e in log if lo <= e["ts"] < hi]
        reason, detail = _judge(test, by_id[test["method"]], table["security"], result, entries, task["test_secret"])
        results.append(
            {
                "index": index, "method": test["method"], "kwargs": test["kwargs"], "passed": reason is None, "reason": reason,
                "detail": detail, "invented_calls": stray[index], "strict_passed": reason is None and stray[index] == 0,
            }
        )

    invented = [e for e in log if is_invented(e)]
    passed = sum(r["passed"] for r in results)
    code = client_path.read_text(encoding="utf-8", errors="replace")
    host = urlparse(table["server_url"]).hostname or ""
    return {
        "task_id": task["task_id"],
        "client": str(client_path),
        "pass_rate": passed / len(results) if results else 0.0,
        "strict_pass_rate": sum(r["strict_passed"] for r in results) / len(results) if results else 0.0,
        "passed": passed,
        "total": len(results),
        "failure_counts": dict(Counter(r["reason"] for r in results if r["reason"])),
        "results": results,
        "invented_endpoint_calls": len(invented),
        "invented_endpoints": sorted({f"{e['method']} {e['path']}" for e in invented})[:MAX_INVENTED_LISTED],
        "hardcoded_secret": any(n in code for n in _secret_needles(task["recording_secret"])),
        "hardcoded_host": bool(host) and host in code,
        "runner": {"timed_out": run["timed_out"], "returncode": run["returncode"], "stderr": run["stderr"]},
        "requests_logged": len(log),
        "duration_s": round(time.time() - started, 3),
    }
