#!/usr/bin/env python3
"""Build the H10 header controls (PREREG.md, H10) from the Tier B task set.

Usage:
  uv run python scripts/make_header_controls.py --src runs/tierb50 --out runs/tierb50_header [--no-verify]

For every task with config.hidden_header == "on" write two copies into --out:
  <task>__hint      goal.md gains one rules line; HAR unchanged.
  <task>__evidence  goal.md unchanged; HAR gains one failed API exchange (400, header missing) before the first API call.
All other files (task.json, engine.json, tests.json, ref_client.py) are copied unchanged, so grading is identical.
Then, unless --no-verify, the reference client is graded on every copy (must score 1.0). Exit 0 when all pass.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path

from starlette.responses import JSONResponse

from har2client.har import _iso, _parse
from har2client.tierb.engine import TierBEngine

HINT_LINE = "- Replicate any custom (non-browser) request headers the API calls use."
FAIL_WAIT_MS = (15.0, 60.0)  # a rejected call is fast: no work behind it
RETRY_GAP_MS = (300.0, 900.0)  # a person (or app) retries after a short pause
DATE_HEADER, LENGTH_HEADER = "date", "content-length"


def engine_error_body(hidden: dict) -> tuple[int, str]:
    """(status, body text) the engine sends when the hidden header is missing: taken from the engine's own code path."""
    status, payload, _extra = TierBEngine._error(400, "missing_header", f"header {hidden['name']} is required")
    return status, JSONResponse(payload, status_code=status).body.decode("utf-8")


def add_hint(goal: str) -> str:
    """Append HINT_LINE as the last line of the rules section (the last section of goal.md)."""
    return goal.rstrip("\n") + "\n" + HINT_LINE + "\n"


def _has_header(entry: dict, name: str) -> bool:
    return any(h["name"].lower() == name.lower() for h in entry["request"]["headers"])


def first_api_index(entries: list[dict], hidden: dict) -> int:
    for i, entry in enumerate(entries):
        if _has_header(entry, hidden["name"]):
            return i
    raise ValueError(f"no entry carries header {hidden['name']}")


def failed_entry(entry: dict, prev_start: str | None, hidden: dict, rng: random.Random) -> dict:
    """The same request without the hidden header, answered by the engine's 400, timed just before `entry`."""
    fail = copy.deepcopy(entry)
    name = hidden["name"].lower()
    fail["request"]["headers"] = [h for h in fail["request"]["headers"] if h["name"].lower() != name]
    t0 = _parse(entry["startedDateTime"])
    wait = round(rng.uniform(*FAIL_WAIT_MS), 3)
    gap = rng.uniform(*RETRY_GAP_MS)
    start = t0 - timedelta(milliseconds=wait + gap)
    if prev_start is not None and start <= _parse(prev_start):  # keep HAR order: fall between neighbour and original
        start = _parse(prev_start) + (t0 - _parse(prev_start)) / 2
        wait = round(min(wait, (t0 - start).total_seconds() * 800), 3)
    status, text = engine_error_body(hidden)
    resp = fail["response"]
    resp["status"], resp["statusText"] = status, "Bad Request"
    resp["content"] = {"size": len(text.encode()), "mimeType": "application/json", "text": text}
    resp["bodySize"] = len(text.encode())
    for h in resp["headers"]:
        if h["name"] == DATE_HEADER:
            h["value"] = format_datetime(start, usegmt=True)
        elif h["name"] == LENGTH_HEADER:
            h["value"] = str(len(text.encode()))
    fail["startedDateTime"] = _iso(start)
    fail["time"] = wait
    fail["timings"] = {**fail["timings"], "wait": wait}
    return fail


def add_evidence(har: dict, hidden: dict, seed: str) -> dict:
    out = copy.deepcopy(har)
    entries = out["log"]["entries"]
    i = first_api_index(entries, hidden)
    prev = entries[i - 1]["startedDateTime"] if i else None
    entries.insert(i, failed_entry(entries[i], prev, hidden, random.Random(seed)))
    return out


def _copy_task(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def build_pair(src: Path, out: Path) -> list[Path]:
    hidden = json.loads((src / "engine.json").read_text())["hidden_header"]
    hint, evidence = out / f"{src.name}__hint", out / f"{src.name}__evidence"
    _copy_task(src, hint)
    (hint / "goal.md").write_text(add_hint((src / "goal.md").read_text(encoding="utf-8")), encoding="utf-8")
    _copy_task(src, evidence)
    har = json.loads((src / "session.har").read_text(encoding="utf-8"))
    new = add_evidence(har, hidden, f"{src.name}:evidence")
    (evidence / "session.har").write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    return [hint, evidence]


def hidden_header_tasks(src: Path) -> list[Path]:
    keep = [d for d in sorted(src.iterdir()) if (d / "task.json").is_file()]
    return [d for d in keep if json.loads((d / "task.json").read_text())["config"]["hidden_header"] == "on"]


def verify(task_dir: Path) -> float:
    """Grade the task's reference client with the CLI (in a scratch dir so no grade.json lands in the task)."""
    with tempfile.TemporaryDirectory() as tmp:
        client = Path(tmp) / "client.py"
        shutil.copy(task_dir / "ref_client.py", client)
        done = subprocess.run(["har2client", "grade", str(task_dir), str(client)], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip() or done.stdout.strip())
        return json.loads((Path(tmp) / "grade.json").read_text())["pass_rate"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--no-verify", action="store_true")
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    for task in hidden_header_tasks(args.src):
        built += build_pair(task, args.out)
    print(f"built {len(built)} task copies in {args.out}")
    if args.no_verify:
        return 0
    bad = []
    for d in built:
        rate = verify(d)
        print(f"  {d.name}: ref pass_rate={rate:.3f}")
        if rate != 1.0:
            bad.append(d.name)
    print(f"ref verified {len(built) - len(bad)}/{len(built)}" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
