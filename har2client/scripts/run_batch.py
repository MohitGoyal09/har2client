#!/usr/bin/env python3
"""Run the Beam baseline over many tasks, grade every client, and write results.csv.

Usage:
  uv run python scripts/run_batch.py --tasks runs/pilot --out runs/batch/<name> \\
      --effort low high --har-mode raw --runs 1 --concurrency 4
  # an open model through an OpenAI-compatible endpoint (default: Hugging Face router, key HF_TOKEN):
  uv run python scripts/run_batch.py --tasks runs/tierb50 --out runs/oss120b \\
      --solver openai_compat --model openai/gpt-oss-120b:cheapest --effort high

One cell = task x effort x har_mode x run. Each cell writes
`<out>/<task>/<effort>-<har_mode>-r<i>/{client.py,solve_log.json,grade.json}`.
A cell whose `grade.json` exists is skipped, so re-running the same command resumes.
A `no_code` result is recorded (pass_rate 0). An API error is written too (status
`api_error`) but is not a model result: the next run retries that cell, and the means leave it out.
A spent daily allowance stops the batch; that cell is not recorded, so the next run retries it.
Exit code: 0 done, 2 bad setup, 3 stopped by the daily limit.
"""

from __future__ import annotations

import argparse
import csv
import functools
import json
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from har2client import runner
from har2client.grade import grade as grade_client
from har2client.solve import beam, openai_compat
from har2client.solve.envfile import load_env_key  # also called as rb.load_env_key by run_models, run_rae, run_probe_batch

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXIT_OK, EXIT_SETUP, EXIT_STOPPED = 0, 2, 3
SOLVERS = ("beam", "openai_compat")
REMAINING_LISTED = 20
ERROR_CHARS = 500
LIMIT_HEADER = "x-ratelimit-remaining-output-tokens-day"
KNOWN_REASONS = (
    "timeout", "exception", "no_request", "auth_401", "bad_request_400",
    "not_found_404", "wrong_op", "args_mismatch", "return_mismatch",
)
HEAD_COLUMNS = ["task", "api_id", "auth_type", "effort", "har_mode", "run", "status", "pass_rate", "strict_pass_rate", "n_tests"]
TAIL_COLUMNS = [
    "invented_endpoint_calls", "hardcoded_secret", "hardcoded_host", "prompt_tokens",
    "completion_tokens", "reasoning_tokens", "wall_time_s", "remaining_output_tokens_day",
]


@dataclass(frozen=True)
class Cell:
    task_dir: Path
    effort: str
    har_mode: str
    run: int
    dir: Path

    @property
    def client_path(self) -> Path:
        return self.dir / "client.py"

    @property
    def label(self) -> str:
        return f"{self.task_dir.name}/{self.dir.name}"


@dataclass
class BatchResult:
    rows: list[dict]
    remaining: list[Cell]
    stopped: bool


# ---- setup ----------------------------------------------------------------


def find_tasks(tasks_dir: Path) -> list[Path]:
    return sorted(d for d in Path(tasks_dir).iterdir() if (d / "task.json").is_file())


def plan_cells(task_dirs: list[Path], efforts: list[str], har_modes: list[str], runs: int, out: Path) -> list[Cell]:
    return [
        Cell(task, effort, mode, i, Path(out) / task.name / f"{effort}-{mode}-r{i}")
        for task in task_dirs
        for effort in efforts
        for mode in har_modes
        for i in range(1, runs + 1)
    ]


# ---- one cell -------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


API_ERROR = "api_error"  # the model call failed: not a model result, so a resume retries the cell


def is_recorded(cell: Cell) -> bool:
    """True when the cell has a readable `grade.json` (a file cut short by a kill is not a result).

    A cell recorded as `api_error` counts as not done, so the next run retries it.
    """
    grade = _read_json(cell.dir / "grade.json")
    return bool(grade) and grade.get("status") != API_ERROR


def _stub_grade(cell: Cell, status: str, error: str | None = None) -> dict:
    tests = _read_json(cell.task_dir / "tests.json")
    stub: dict[str, Any] = {
        "task_id": cell.task_dir.name,
        "client": str(cell.client_path),
        "status": status,
        "pass_rate": 0.0,
        "strict_pass_rate": 0.0,
        "passed": 0,
        "total": len(tests) if isinstance(tests, list) else 0,
        "failure_counts": {},
    }
    if error:
        stub["error"] = error[:ERROR_CHARS]
    return stub


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def run_cell(cell: Cell, solve_fn: Callable, grade_fn: Callable, client: Any, timeout: float, stop: threading.Event) -> str:
    """Returns "done", "limit" (daily allowance spent) or "skipped" (batch is stopping)."""
    grade_path = cell.dir / "grade.json"
    if is_recorded(cell):
        return "done"
    if stop.is_set():
        return "skipped"
    cell.dir.mkdir(parents=True, exist_ok=True)
    try:
        solve_fn(cell.task_dir, cell.client_path, effort=cell.effort, har_mode=cell.har_mode, client=client)
    except beam.DailyAllowanceExhausted:
        stop.set()
        return "limit"
    except beam.MissingApiKeyError:
        raise
    except Exception as exc:  # an API error is a result, not a crash
        _write_json(grade_path, _stub_grade(cell, API_ERROR, _error_text(exc)))
        return "done"
    status = _read_json(cell.dir / "solve_log.json").get("status") or "no_code"
    if status != "ok" or not cell.client_path.is_file():
        _write_json(grade_path, _stub_grade(cell, status if status != "ok" else "no_code"))
        return "done"
    try:
        result = {**grade_fn(cell.task_dir, cell.client_path, timeout=timeout), "status": "ok"}
    except Exception as exc:
        result = _stub_grade(cell, "grade_error", _error_text(exc))
    _write_json(grade_path, result)
    return "done"


# ---- results --------------------------------------------------------------


def _auth_type(task: dict) -> str | None:
    return next((f.split(":", 1)[1] for f in task.get("features", []) if f.startswith("auth:")), None)


def read_row(cell: Cell) -> dict | None:
    """Result row from the files on disk; None when the cell was not recorded."""
    grade = _read_json(cell.dir / "grade.json")
    if not grade:
        return None
    task = _read_json(cell.task_dir / "task.json")
    log = _read_json(cell.dir / "solve_log.json")
    usage = log.get("usage") or {}
    row: dict[str, Any] = {
        "task": cell.task_dir.name,
        "api_id": task.get("api_id"),
        "auth_type": _auth_type(task),
        "effort": cell.effort,
        "har_mode": cell.har_mode,
        "run": cell.run,
        "status": grade.get("status", "ok"),
        "pass_rate": grade.get("pass_rate", 0.0),
        "strict_pass_rate": grade.get("strict_pass_rate"),
        "n_tests": grade.get("total"),
        "invented_endpoint_calls": grade.get("invented_endpoint_calls"),
        "hardcoded_secret": grade.get("hardcoded_secret"),
        "hardcoded_host": grade.get("hardcoded_host"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "wall_time_s": log.get("wall_time_s"),
        "remaining_output_tokens_day": (log.get("rate_limit_headers") or {}).get(LIMIT_HEADER),
    }
    for reason, count in (grade.get("failure_counts") or {}).items():
        row[f"fail_{reason}"] = count
    return row


def write_csv(path: Path, rows: list[dict]) -> None:
    observed = {k for r in rows for k in r if k.startswith("fail_")}
    fail_columns = sorted({f"fail_{r}" for r in KNOWN_REASONS} | observed)
    columns = HEAD_COLUMNS + fail_columns + TAIL_COLUMNS
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        for row in rows:
            filled = {c: row.get(c) for c in columns}
            for column in fail_columns:
                if filled[column] is None and row.get("status") in ("ok", None):
                    filled[column] = 0  # graded: an absent reason means zero
            writer.writerow({k: "" if v is None else v for k, v in filled.items()})


def summarize(rows: list[dict]) -> str:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    errors = sum(1 for row in rows if row.get("status") == API_ERROR)
    for row in rows:
        if row.get("status") == API_ERROR:
            continue  # the model call failed: not a model result
        groups[(row["effort"], row["har_mode"])].append(row)
    order = {e: i for i, e in enumerate(beam.EFFORTS)}
    lines = [
        "Mean pass rate by effort x har_mode (strict: no invented-endpoint request inside the test)",
        f"{'effort':<8} {'har_mode':<9} {'cells':>5} {'mean':>6} {'strict':>6}",
    ]
    for (effort, mode), group in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 99), kv[0][1])):
        rates = [float(r["pass_rate"] or 0.0) for r in group]
        strict = [float(r["strict_pass_rate"]) for r in group if r.get("strict_pass_rate") not in (None, "")]
        strict_text = f"{sum(strict) / len(strict):.3f}" if strict else "-"
        lines.append(f"{effort:<8} {mode:<9} {len(rates):>5} {sum(rates) / len(rates):>6.3f} {strict_text:>6}")
    totals = {k: sum(r.get(f"{k}_tokens") or 0 for r in rows) for k in ("prompt", "completion", "reasoning")}
    lines.append("Total tokens: " + " ".join(f"{k}={v}" for k, v in totals.items()))
    lines.append(f"api_error rows excluded: {errors}")
    return "\n".join(lines)


# ---- batch ----------------------------------------------------------------


def run_batch(
    tasks_dir: Path,
    out_dir: Path,
    *,
    efforts: list[str],
    har_modes: list[str],
    runs: int,
    concurrency: int,
    solve_fn: Callable,
    grade_fn: Callable = grade_client,
    client: Any = None,
    timeout: float = runner.DEFAULT_TIMEOUT_S,
    log: Callable[..., None] = print,
) -> BatchResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = plan_cells(find_tasks(tasks_dir), efforts, har_modes, runs, out_dir)
    stop, lock = threading.Event(), threading.Lock()
    finished = [0]

    def work(cell: Cell) -> str:
        try:
            outcome = run_cell(cell, solve_fn, grade_fn, client, timeout, stop)
        except BaseException:  # the worker must stop the queue itself: the main thread learns of it too late
            stop.set()
            raise
        with lock:
            finished[0] += 1
            row = read_row(cell) if outcome == "done" else None
            detail = f"{row['status']} pass_rate={float(row['pass_rate']):.3f}" if row else outcome
            log(f"[{finished[0]}/{len(cells)}] {cell.label} {detail}")
        return outcome

    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            try:
                list(pool.map(work, cells))
            except BaseException:  # Ctrl-C or a crash: queued cells must not start (each one is a paid call)
                stop.set()
                raise
    finally:  # also on Ctrl-C or a crash: keep what is done
        rows = [r for r in (read_row(c) for c in cells) if r]
        write_csv(out_dir / "results.csv", rows)
    remaining = [c for c in cells if not is_recorded(c)]
    return BatchResult(rows=rows, remaining=remaining, stopped=stop.is_set())


# ---- CLI ------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Batch-run the Beam baseline and grade every client.")
    p.add_argument("--tasks", required=True, type=Path, help="directory of task dirs (each has task.json)")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--effort", nargs="+", default=["high"], choices=beam.EFFORTS)
    p.add_argument("--har-mode", nargs="+", default=["raw"], choices=beam.HAR_MODES)
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--timeout", type=float, default=runner.DEFAULT_TIMEOUT_S, help="grading time limit per task, seconds")
    p.add_argument("--solver", choices=SOLVERS, default="beam",
                   help="beam (default) or openai_compat (any OpenAI-compatible endpoint, default: Hugging Face router)")
    p.add_argument("--model", default=None, help="openai_compat only: model id, e.g. openai/gpt-oss-120b:cheapest")
    p.add_argument("--base-url", default=None, help=f"openai_compat only (default {openai_compat.DEFAULT_BASE_URL})")
    p.add_argument("--api-key-env", default=None,
                   help=f"openai_compat only: env var that holds the key (default {openai_compat.DEFAULT_API_KEY_ENV})")
    return p


def _make_solver(args: argparse.Namespace) -> tuple[Callable, Any] | str:
    """(solve_fn, client) for the chosen solver, or an error message when the setup is wrong."""
    if args.solver == "beam":
        extra = [f for f, v in (("--model", args.model), ("--base-url", args.base_url), ("--api-key-env", args.api_key_env)) if v]
        if extra:
            return f"{', '.join(extra)} only apply with --solver openai_compat"
        load_env_key(ENV_PATH, os.environ)
        if not os.environ.get(beam.API_KEY_ENV):
            return f"{beam.API_KEY_ENV} is not set (environment or {ENV_PATH})"
        return beam.solve, beam.make_client()
    if not args.model:
        return "--model is required with --solver openai_compat"
    base_url = args.base_url or openai_compat.DEFAULT_BASE_URL
    key_env = args.api_key_env or openai_compat.DEFAULT_API_KEY_ENV
    load_env_key(ENV_PATH, os.environ, key_env)
    if not os.environ.get(key_env):
        return f"{key_env} is not set (environment or {ENV_PATH})"
    solve_fn = functools.partial(openai_compat.solve, base_url=base_url, model=args.model, api_key_env=key_env)
    return solve_fn, openai_compat.make_client(base_url, key_env)


def main(argv: list[str] | None = None, *, solve_fn: Callable | None = None, client: Any = None) -> int:
    args = _parser().parse_args(argv)
    if not args.tasks.is_dir() or not find_tasks(args.tasks):
        print(f"error: no task dirs (with task.json) in {args.tasks}", file=sys.stderr)
        return EXIT_SETUP
    if args.runs < 1 or args.concurrency < 1:
        print("error: --runs and --concurrency must be at least 1", file=sys.stderr)
        return EXIT_SETUP
    if solve_fn is None:
        made = _make_solver(args)
        if isinstance(made, str):
            print(f"error: {made}", file=sys.stderr)
            return EXIT_SETUP
        solve_fn, client = made
    result = run_batch(
        args.tasks, args.out, efforts=args.effort, har_modes=args.har_mode, runs=args.runs,
        concurrency=args.concurrency, solve_fn=solve_fn, client=client, timeout=args.timeout,
    )
    print()
    print(summarize(result.rows))
    print(f"results: {args.out / 'results.csv'}")
    if result.remaining:
        why = "stopped: daily allowance spent" if result.stopped else "not recorded"
        print(f"\n{len(result.remaining)} cells remaining ({why}). Run the same command to resume:")
        for cell in result.remaining[:REMAINING_LISTED]:
            print(f"  {cell.label}")
        if len(result.remaining) > REMAINING_LISTED:
            print(f"  ... and {len(result.remaining) - REMAINING_LISTED} more")
    return EXIT_STOPPED if result.stopped else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
