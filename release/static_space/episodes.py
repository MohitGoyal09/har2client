#!/usr/bin/env python3
"""Write release/static_space/episodes/episodes.json: three real Case 2 episodes shown as an action replay on the Space.

Usage:  python3 release/static_space/episodes.py [--source DIR]   (DIR holds runs/ext, default: ~/Code/case2-runtime-ext)

Only observable actions are read: the probes sent to the practice server (request, reply, label), the submitted client.py and the
pass rate of the run (results.csv). The model's reasoning and message text are never loaded; captions are fixed templates built
from the probe label and the request data. Every credential, cookie, token, CSRF and API-key value is masked as "•••".
Standard library only. build.py copies the committed JSON into the site; run this script only to regenerate it.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "episodes" / "episodes.json"
SOURCE_DEFAULT = Path.home() / "Code" / "case2-runtime-ext"
MASK = "•••"
EXCERPT_CHARS = 160
BODY_CHARS = 120
SNIPPET_LINES = 12
MAX_JSON_KB = 30
# (episode id, run folder, task, run cell folder, results.csv arm) - the first two are Beam with 5 probes, the third is the one-probe contrast.
CHOICES = [
    ("E1", "R1_P5_high_r2r3", "tierb_17_shipments", "P5-high-raw-r2", "P5"),
    ("E2", "R1_P5_high_r2r3", "tierb_46_samples", "P5-high-raw-r1", "P5"),
    ("E3", "R2_P1P2_high", "tierb_25_tickets", "P1-high-raw-r1", "P1"),
]
NOTE_E3 = "Only one probe allowed. Beam does not spend it on the header."
SECRET_NAME = re.compile(r"auth|cookie|token|csrf|api[-_]?key|secret|session|passw|credential|username|^user$", re.I)
SECRET_VALUE = re.compile(r"\b(?:sk|tok|pw|user|sess|csrf)_[0-9A-Za-z]{6,}|eyJ[A-Za-z0-9_-]{6,}|\bhf_[A-Za-z0-9]{6,}|Bearer\s+[A-Za-z0-9._-]{6,}")
RAW_PAIR = re.compile(r'("(?:[^"]*(?:token|secret|passw|key|cookie|csrf|session|user)[^"]*)"\s*:\s*")([^"]*)(")', re.I)
REQUEST_HEADERS = re.compile(r"(?<!response\.)\bheaders\s*(=|\.update|\[)|\bheaders=")
NOT_SECRET_KEYS = {"token_type", "expires_in"}


def canonical(name: str) -> str:
    return "-".join(part.capitalize() for part in name.split("-"))


def scrub(text: str) -> str:
    return SECRET_VALUE.sub(MASK, text)


def mask_json(node, key: str = ""):
    """Mask the value of every secret-looking key in parsed JSON (recursively)."""
    if isinstance(node, dict):
        return {k: mask_json(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [mask_json(v, key) for v in node]
    if isinstance(node, str) and key.lower() not in NOT_SECRET_KEYS and SECRET_NAME.search(key):
        return MASK
    return node


def mask_text(text: str) -> str:
    """Mask a request or reply body: JSON by key, form data by key, anything else by value pattern."""
    try:
        return scrub(json.dumps(mask_json(json.loads(text)), separators=(",", ":"), ensure_ascii=False))
    except ValueError:
        pass
    if "=" in text and "{" not in text:
        pairs = [(k, MASK if SECRET_NAME.search(k) else v) for k, _, v in (p.partition("=") for p in text.split("&"))]
        return scrub("&".join(f"{k}={v}" for k, v in pairs))
    return scrub(RAW_PAIR.sub(lambda m: m.group(1) + MASK + m.group(3), text))


def cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def mask_path(path: str) -> str:
    base, _, query = path.partition("?")
    if not query:
        return scrub(base)
    pairs = [(k, MASK if SECRET_NAME.search(k) else v) for k, _, v in (p.partition("=") for p in query.split("&"))]
    return scrub(base + "?" + "&".join(f"{k}={v}" for k, v in pairs))


def load_probes(cell: Path) -> list[dict]:
    """Read probes.jsonl keeping only the observable fields; refused probes were never sent and are dropped."""
    rows = []
    for line in (cell / "probes.jsonl").read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if not raw.get("sent") or "response" not in raw:
            continue
        rows.append({"request": raw["request"], "response": raw["response"], "label": raw["label"], "ablated": raw.get("ablated_header")})
    return rows


def hidden_header_name(source: Path, task: str) -> str:
    """The hidden header of a task: the one a practice server names as missing (400, missing_header) in any run of the task."""
    for run in ("R1_P5_high_r2r3", "R3_P5_max"):
        for cell in sorted((source / "runs" / "ext" / run / task).glob("*/probes.jsonl")):
            for probe in load_probes(cell.parent):
                if probe["label"] != "header_ablation" or not probe["ablated"]:
                    continue
                if "missing_header" in probe["response"].get("body", ""):
                    return probe["ablated"]
    raise SystemExit(f"error: no run of {task} names its hidden header")


def results_row(csv_path: Path, task: str, arm: str, run: str) -> dict:
    for row in csv.DictReader(csv_path.open(encoding="utf-8")):
        if row["task"] == task and row["arm"] == arm and row["run"] == run:
            return row
    raise SystemExit(f"error: no results row for {task} {arm} run {run}")


def probe_caption(label: str, ablated: str | None, path: str = "") -> str:
    if "openapi" in path.lower() or "swagger" in path.lower():
        return "Looks for a machine-readable API description."  # the harness labels it by its missing credentials, not its purpose
    if label == "header_ablation" and ablated:
        return f"Sends a recorded request again without the `{canonical(ablated)}` header, to test whether the server needs it."
    return {"auth_test": "Tests the login flow.", "paging_test": "Tests pagination.", "etag_test": "Tests the update precondition."}.get(
        label, "Sends a request to the practice server.")


def reply_caption(label: str, status: int) -> str:
    if label == "header_ablation" and status >= 400:
        return f"The server rejects the request without the header ({status})."
    if label == "header_ablation":
        return f"The server accepts the request without the header ({status})."
    if status == 404:
        return f"The server has no such route ({status})."
    if status < 300:
        return f"The server accepts the request ({status})."
    if status < 500:
        return f"The server rejects the request ({status})."
    return f"The server fails ({status})."


def probe_step(n: int, probe: dict, hidden: str) -> list[dict]:
    req, resp, label = probe["request"], probe["response"], probe["label"]
    headers = [{"name": canonical(k), "value": MASK if SECRET_NAME.search(k) else scrub(str(v)), "tested": k.lower() == hidden.lower()}
               for k, v in req.get("headers", {}).items()]
    body = req.get("body")
    probe_out = {"kind": "probe", "n": n, "label": label, "method": req["method"], "path": mask_path(req["path"]), "headers": headers,
                 "removed": canonical(probe["ablated"]) if probe["ablated"] else None,
                 "body": cut(mask_text(body if isinstance(body, str) else json.dumps(body)), BODY_CHARS) if body else None,
                 "caption": probe_caption(label, probe["ablated"], req["path"])}
    if label == "other" and any(h["tested"] for h in headers):
        probe_out["caption"] = f"Sends a request that includes the `{canonical(hidden)}` header."
    status = int(resp["status"])
    reply_out = {"kind": "reply", "n": n, "status": status, "body": cut(mask_text(resp.get("body", "")), EXCERPT_CHARS),
                 "caption": reply_caption(label, status)}
    return [probe_out, reply_out]


def client_snippet(client: Path, hidden: str) -> tuple[str, bool]:
    """Up to 12 lines of client.py around the first use of the header; else around the first place it builds request headers."""
    lines = client.read_text(encoding="utf-8").splitlines()
    needle = hidden.lower()
    hit = next((i for i, ln in enumerate(lines) if needle in ln.lower()), None)
    has_header = hit is not None
    if hit is None:
        hit = next((i for i, ln in enumerate(lines) if REQUEST_HEADERS.search(ln)), 0)
    start = next((i for i in range(hit, -1, -1) if lines[i].lstrip().startswith(("def ", "async def "))), hit - 4)
    if hit - start > SNIPPET_LINES - 3:  # the enclosing method is too long to show whole: end the window just after the header line
        start = hit - 5
    start = max(0, min(start, len(lines) - SNIPPET_LINES))
    window = lines[start:start + SNIPPET_LINES]
    while len(window) > 1 and (not window[-1].strip() or window[-1].lstrip().startswith(("def ", "async def "))):
        window.pop()  # never end on a blank line or a method signature whose body is cut off
    indent = min((len(ln) - len(ln.lstrip()) for ln in window if ln.strip()), default=0)
    return scrub("\n".join(ln[indent:] for ln in window)), has_header


def build_episode(source: Path, spec: tuple[str, str, str, str, str]) -> dict:
    ep_id, run, task, cell_name, arm = spec
    root = source / "runs" / "ext" / run
    cell = root / task / cell_name
    row = results_row(root / "results.csv", task, arm, cell_name.rsplit("-r", 1)[1])
    hidden = hidden_header_name(source, task)
    probes = load_probes(cell)
    if len(probes) != int(row["probes_used"]):
        raise SystemExit(f"error: {task}: {len(probes)} sent probes but results say {row['probes_used']}")
    pass_rate = float(row["pass_rate"])
    if row["hidden_header"] != "on":
        raise SystemExit(f"error: {task} has no hidden header")
    if ep_id == "E3":
        if row["ablated_hidden_header"] == "True" or pass_rate >= 1.0:
            raise SystemExit("error: the contrast episode must not test the header and must not pass fully")
    elif row["ablated_hidden_header"] != "True" or pass_rate != 1.0:
        raise SystemExit(f"error: {ep_id} must test the header and pass fully")
    steps: list[dict] = [{"kind": "note", "text": NOTE_E3}] if ep_id == "E3" else []
    for n, probe in enumerate(probes, 1):
        steps += probe_step(n, probe, hidden)
    code, has_header = client_snippet(cell / "client.py", hidden)
    if (ep_id == "E3") == has_header:
        raise SystemExit(f"error: {ep_id}: header presence in client.py does not match the episode")
    steps.append({"kind": "submit", "code": code, "has_header": has_header,
                  "caption": "Submits a client that always sends the header." if has_header else "Submits a client without the header."})
    steps.append({"kind": "result", "pass_rate": pass_rate, "text": f"Hidden tests passed: {pass_rate * 100:.0f}%"})
    return {"id": ep_id, "task": task, "domain": task.split("_", 2)[2], "effort": row["effort"], "header": canonical(hidden),
            "probe_budget": int(row["probe_budget"]), "probes_used": int(row["probes_used"]), "pass_rate": pass_rate,
            "contrast": ep_id == "E3", "steps": steps}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    episodes = [build_episode(args.source, spec) for spec in CHOICES]
    text = json.dumps({"episodes": episodes}, ensure_ascii=False, indent=1) + "\n"
    if len(text.encode()) > MAX_JSON_KB * 1024:
        raise SystemExit(f"error: episodes.json is {len(text.encode()) / 1024:.1f} KB, limit {MAX_JSON_KB} KB")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    for e in episodes:
        print(f"{e['id']} {e['task']} header={e['header']} probes={e['probes_used']}/{e['probe_budget']} pass={e['pass_rate']} "
              f"status={[s['status'] for s in e['steps'] if s['kind'] == 'reply']}")
    print(f"wrote {args.out} ({len(text.encode()) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
