#!/usr/bin/env python3
"""Floor of the case 3 v3 evaluation: what the unchanged v1 client and an empty client score on the v2 API.

Usage:
  uv run python scripts/heal_v1_floor.py [--tasks runs/heal_v3_eval] [--out results/heal_v3_eval_v1_floor.json] [--workers 4]

For each task dir it grades `client_v1.py` (unchanged) and an empty client (the right methods, each returns None) with
`har2client.grade.grade`, the grader the evaluation uses, on the task's fresh local mock server. No model call, no network
beyond the local mock servers. Writes per-task `v1_pass_rate` and `empty_pass_rate`, the means, the number of tasks where v1
scores above 0, and the maximum.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from har2client import grade as grading

DEFAULT_TASKS = Path("runs/heal_v3_eval")
DEFAULT_OUT = Path("results/heal_v3_eval_v1_floor.json")
DEFAULT_WORKERS = 4


def empty_client_source(methods: list[str]) -> str:
    """A client with the constructor of a Tier B task and every method returning None (it sends nothing)."""
    body = "".join(f"    def {name}(self, **kwargs):\n        return None\n" for name in sorted(set(methods)))
    return f"class Client:\n    def __init__(self, base_url, username, password):\n        pass\n{body}"


def floor_row(task_dir: Path, grade_fn: Callable = grading.grade) -> dict:
    tests = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="h2c_floor_") as tmp:
        empty = Path(tmp) / "client.py"
        empty.write_text(empty_client_source([t["method"] for t in tests]), encoding="utf-8")
        v1 = grade_fn(task_dir, task_dir / "client_v1.py")
        nothing = grade_fn(task_dir, empty)
    return {"task": task_dir.name, "n_tests": v1["total"], "v1_pass_rate": v1["pass_rate"], "empty_pass_rate": nothing["pass_rate"]}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    v1 = [r["v1_pass_rate"] for r in rows]
    empty = [r["empty_pass_rate"] for r in rows]
    return {
        "n_tasks": n,
        "v1_mean": sum(v1) / n if n else None,
        "v1_max": max(v1, default=None),
        "v1_max_task": max(rows, key=lambda r: r["v1_pass_rate"])["task"] if rows else None,
        "v1_tasks_above_zero": sum(1 for x in v1 if x > 0),
        "empty_mean": sum(empty) / n if n else None,
        "empty_max": max(empty, default=None),
    }


def run_floor(tasks: Path, workers: int = DEFAULT_WORKERS, grade_fn: Callable = grading.grade) -> dict:
    dirs = sorted(d for d in Path(tasks).iterdir() if (d / "client_v1.py").is_file() and (d / "tests.json").is_file())
    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(lambda d: floor_row(d, grade_fn), dirs))
    return {"tasks_dir": str(tasks), "summary": summarize(rows), "per_task": rows}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = p.parse_args(argv)
    result = run_floor(args.tasks, args.workers)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    print(f"-> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
