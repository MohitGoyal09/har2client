"""Shaping milestones, read from the grading log (the engine's request log and the per-test results)."""

from __future__ import annotations

EXPIRED_CODES = ("token_expired", "token_invalid")  # the engine's verbose / terse error code of an expired session
MILESTONES = ("logged_in", "sent_hidden_header", "fetched_second_page", "sent_if_match", "relogin_after_401", "unwrapped_envelope")


def applicable(spec: dict) -> list[str]:
    """Milestones that exist for a task: those whose switch is on."""
    cfg = spec["config"]
    on = {
        "logged_in": cfg["auth"] != "apikey_header",
        "sent_hidden_header": cfg["hidden_header"] == "on",
        "fetched_second_page": cfg["pagination"] != "none",
        "sent_if_match": cfg["etag_update"] == "on",
        "relogin_after_401": cfg["token_expiry"] == "on",
        "unwrapped_envelope": cfg["envelope"] == "on",
    }
    return [name for name in MILESTONES if on[name]]


def _later_page(style: str, values: list[str]) -> bool:
    """True when the page parameter asks for a page after the first (`offset=0` and `page=1` are the first page)."""
    if style == "cursor":
        return any(v for v in values)
    return any(v.isdigit() and int(v) > (1 if style == "link_header" else 0) for v in values)


def _expired(entry: dict) -> bool:
    return entry["status"] == 401 and ((entry.get("response") or {}).get("error") in EXPIRED_CODES)


def reached(spec: dict, results: list[dict], log: list[dict]) -> dict[str, bool]:
    """{milestone: reached} for the applicable milestones. `log` is the engine's request log of one grading run
    (entries with method, query, headers, op_id, status); `results` the per-test results of the grader."""
    hidden = spec.get("hidden_header")
    page_param = spec["page"]["param"]
    style = spec["config"]["pagination"]
    first_401 = next((i for i, e in enumerate(log) if _expired(e) and e["op_id"] not in (None, "login")), None)
    checks = {
        "logged_in": any(e["op_id"] == "login" and e["status"] == 200 for e in log),
        "sent_hidden_header": bool(hidden) and any(e["op_id"] and e["headers"].get(hidden["name"].lower()) == hidden["value"] for e in log),
        "fetched_second_page": any(e["op_id"] == "list" and e["status"] == 200 and _later_page(style, e["query"].get(page_param, [])) for e in log),
        "sent_if_match": any(e["op_id"] == "update" and 200 <= e["status"] < 300 and "if-match" in e["headers"] for e in log),
        "relogin_after_401": first_401 is not None and any(e["op_id"] == "login" and e["status"] == 200 for e in log[first_401 + 1:]),
        "unwrapped_envelope": any(r["passed"] and r["method"].split("_")[0] in ("get", "create", "update") for r in results),
    }
    return {name: checks[name] for name in applicable(spec)}
