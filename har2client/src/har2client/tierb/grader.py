"""Semantic grading for Tier B: judge return values and server state, not request shape (DESIGN-TIERB B2)."""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from har2client import runner
from har2client.grade import strict_flags
from har2client.tierb.engine import TierBEngine

MAX_INVENTED_LISTED = 20
# The last request of a failed call decides the reason when it was an error: that error is why the call did not recover.
STATUS_REASONS = {
    400: "bad_request_400", 401: "auth_401", 403: "auth_401", 404: "not_found_404", 412: "precondition_428", 428: "precondition_428",
    308: "moved_308", 422: "unprocessable_422", 429: "rate_limited_429",
}


def _same(a: object, b: object) -> bool:
    """JSON-exact equality (True != 1, 1 != 1.0)."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _windows(results: list[dict]) -> dict[int, tuple[float, float]]:
    ordered = sorted(results, key=lambda r: r["index"])
    out = {}
    for i, r in enumerate(ordered):
        out[r["index"]] = (r["t_start"], ordered[i + 1]["t_start"] if i + 1 < len(ordered) else float("inf"))
    return out


def _success(entry: dict, op: str) -> bool:
    return entry["op_id"] == op and 200 <= entry["status"] < 300


def _is_write(entry: dict) -> bool:
    """A write the engine really did: a 2xx answer to a write op that is not the replay of an `Idempotency-Key` answer."""
    return entry["op_id"] in WRITE_OPS and 200 <= entry["status"] < 300 and not entry.get("replay")


WRITE_OPS = ("create", "update", "delete")


def _stray_writes(test: dict, entries: list[dict]) -> list[dict]:
    """Successful writes in the call's window that this test did not ask for: a client must change only the item it was
    told to (an update that also creates or deletes something is wrong, even if the returned item is right)."""
    op, target = test["op"], test["kwargs"].get("id")
    return [
        e for e in entries
        if _is_write(e) and (e["op_id"] != op or (op != "create" and e["path_params"].get("id") != target))
    ]


def _check_list(test: dict, spec: dict, result: dict, entries: list[dict], store: dict, initial: list[dict]):
    field, value = spec["filter"], test["kwargs"].get(spec["filter"])
    expected = [i for i in initial if value is None or i[field] == value]
    returned = result["return"]
    if _same(returned, expected):
        return None, ""
    if isinstance(returned, list) and len(returned) < len(expected) and all(x in expected for x in returned):
        return "incomplete_list", f"got {len(returned)} of {len(expected)} items"
    return "return_mismatch", "returned list differs from the server's full matching list"


def _check_get(test: dict, spec: dict, result: dict, entries: list[dict], store: dict, initial: list[dict]):
    expected = next(i for i in initial if i["id"] == test["kwargs"]["id"])
    if _same(result["return"], expected):
        return None, ""
    return "return_mismatch", "returned item differs from the stored item"


def _check_create(test: dict, spec: dict, result: dict, entries: list[dict], store: dict, initial: list[dict]):
    made = {e["created_id"] for e in entries if e.get("created_id")}  # also for `async_create`: the job poll that made the item
    if len(made) != 1:
        return "state_mismatch", f"{len(made)} items created, expected exactly 1"
    stored = store.get(next(iter(made)))
    if stored is None or any(stored.get(k) != v for k, v in test["kwargs"]["body"].items()):
        return "state_mismatch", "the created item is not stored with the requested fields"
    if not _same(result["return"], stored):
        return "return_mismatch", "returned item differs from the stored item"
    return None, ""


def _check_update(test: dict, spec: dict, result: dict, entries: list[dict], store: dict, initial: list[dict]):
    target, changes = test["kwargs"]["id"], test["kwargs"]["changes"]
    stored = store.get(target)
    patched = [e for e in entries if _success(e, "update") and e["path_params"].get("id") == target]
    if not patched or stored is None or any(stored.get(k) != v for k, v in changes.items()):
        return "state_mismatch", "the stored item does not have the changes"
    if not _same(result["return"], stored):
        return "return_mismatch", "returned item differs from the stored item"
    return None, ""


def _check_delete(test: dict, spec: dict, result: dict, entries: list[dict], store: dict, initial: list[dict]):
    target = test["kwargs"]["id"]
    if not any(_success(e, "delete") and e["path_params"].get("id") == target for e in entries) or target in store:
        return "state_mismatch", "the item is still stored"
    if result["return"] is not None:
        return "return_mismatch", "delete must return None"
    return None, ""


_CHECKS = {"list": _check_list, "get": _check_get, "create": _check_create, "update": _check_update, "delete": _check_delete}


def judge(test: dict, spec: dict, result: dict | None, entries: list[dict], store: dict, initial: list[dict]) -> tuple[str | None, str]:
    """(failure reason or None, detail) for one test call."""
    if result is None:
        return "timeout", "no result before the time limit"
    if not result["ok"]:
        reason, detail = "exception", result.get("error", "")
    elif not entries:
        reason, detail = "no_request", "client sent nothing"
    elif result.get("unserializable"):
        reason, detail = "return_mismatch", "returned value is not JSON"
    else:
        reason, detail = _CHECKS[test["op"]](test, spec, result, entries, store, initial)
        stray = _stray_writes(test, entries) if reason is None else []
        if stray:
            reason, detail = "state_mismatch", "unrequested write: " + ", ".join(f"{e['method']} {e['path']}" for e in stray)
    if reason is None:
        return None, ""
    if entries and entries[-1]["status"] in STATUS_REASONS:
        return STATUS_REASONS[entries[-1]["status"]], f"{entries[-1]['status']} on the last request ({reason}: {detail})"
    return reason, detail


def _secret_needles(creds: dict) -> list[str]:
    return [v for v in (creds["password"], creds["username"]) if v]


def grade_tierb(task_dir: str | Path, client_path: str | Path, timeout: float = runner.DEFAULT_TIMEOUT_S, task: dict | None = None) -> dict:
    task_dir, client_path = Path(task_dir), Path(client_path)
    task = task or json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    spec = json.loads((task_dir / "engine.json").read_text(encoding="utf-8"))
    tests = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
    creds = task["credentials"]["test"]
    started = time.time()

    engine = TierBEngine(spec, task["seeds"]["test"], creds)
    base_url = engine.start()
    try:
        run = runner.run_client(client_path, base_url, creds["password"], tests, timeout_s=timeout, ctor_args=[creds["username"], creds["password"]])
    finally:
        engine.stop()
    log = list(engine.log)

    by_index = {r["index"]: r for r in run["results"]}
    windows = _windows(run["results"])
    stray = strict_flags(len(tests), by_index, windows, log, lambda e: e["op_id"] is None)  # login and the five ops are legitimate
    results = []
    for index, test in enumerate(tests):
        result = by_index.get(index)
        entries = []
        if result is not None:
            lo, hi = windows[index]
            entries = [e for e in log if lo <= e["ts"] < hi]
        reason, detail = judge(test, spec, result, entries, engine.store, engine.initial)
        results.append(
            {
                "index": index, "method": test["method"], "kwargs": test["kwargs"], "passed": reason is None, "reason": reason,
                "detail": detail, "invented_calls": stray[index], "strict_passed": reason is None and stray[index] == 0,
            }
        )

    invented = [e for e in log if e["op_id"] is None]
    passed = sum(r["passed"] for r in results)
    code = client_path.read_text(encoding="utf-8", errors="replace")
    return {
        "task_id": task["task_id"],
        "tier": "B",
        "config": task["config"],
        "client": str(client_path),
        "pass_rate": passed / len(results) if results else 0.0,
        "strict_pass_rate": sum(r["strict_passed"] for r in results) / len(results) if results else 0.0,
        "passed": passed,
        "total": len(results),
        "failure_counts": dict(Counter(r["reason"] for r in results if r["reason"])),
        "results": results,
        "invented_endpoint_calls": len(invented),
        "invented_endpoints": sorted({f"{e['method']} {e['path']}" for e in invented})[:MAX_INVENTED_LISTED],
        "hardcoded_secret": any(n in code for n in _secret_needles(task["credentials"]["recording"])),
        "hardcoded_host": bool(spec["host"]) and spec["host"] in code,
        "runner": {"timed_out": run["timed_out"], "returncode": run["returncode"], "stderr": run["stderr"]},
        "requests_logged": len(log),
        "duration_s": round(time.time() - started, 3),
    }
