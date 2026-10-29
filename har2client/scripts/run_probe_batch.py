#!/usr/bin/env python3
"""Run the case-2 arms (scientist agent: probe, then write) over many tasks, grade every client, write results.csv.

Usage:
  uv run python scripts/run_probe_batch.py --tasks runs/tierb50 --out runs/probe/<name> \\
      --arms placebo P5 P20 --effort high --runs 1 --concurrency 4
  uv run python scripts/run_probe_batch.py --tasks runs/tierb50 --limit 2 --out /tmp/probe_dry --dry-run   # no Beam calls

Arms (DESIGN-CASES.md): `placebo` = budget 0, no tool, same prompt text; `P5` = 5 probes; `P20` = 20 probes.
One cell = task x arm x effort x har_mode x run, in `<out>/<task>/<arm>-<effort>-<har_mode>-r<i>/`
(`client.py`, `solve_log.json`, `probes.jsonl`, `grade.json`). A cell with a readable `grade.json` is skipped, so
re-running the same command resumes; an `api_error` cell is retried and left out of the means. A spent daily
allowance stops the batch; that cell is not recorded.
Exit code: 0 done, 2 bad setup, 3 stopped by the daily limit.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_batch as rb  # noqa: E402  (reused by import; scripts/ is not a package)

from har2client import runner  # noqa: E402
from har2client.env.probe import LABELS  # noqa: E402
from har2client.grade import grade as grade_client  # noqa: E402
from har2client.solve import beam, beam_agent  # noqa: E402

ARMS = {"placebo": 0, "P5": 5, "P20": 20}
HEAD_COLUMNS = [
    "task", "api_id", "auth_type", "hidden_header", "arm", "effort", "har_mode", "run", "status", "pass_rate", "strict_pass_rate", "n_tests",
    "probe_budget", "probes_used", "probes_refused", *[f"probes_{label}" for label in LABELS], "ablated_hidden_header", "turns",
]
TAIL_COLUMNS = [
    "invented_endpoint_calls", "hardcoded_secret", "hardcoded_host", "prompt_tokens", "completion_tokens", "reasoning_tokens",
    "cached_tokens", "cached_share", "wall_time_s", "remaining_output_tokens_day",
]


@dataclass(frozen=True)
class ProbeCell(rb.Cell):
    arm: str = "placebo"


def plan_cells(task_dirs: list[Path], arms: list[str], efforts: list[str], har_modes: list[str], runs: int, out: Path) -> list[ProbeCell]:
    return [
        ProbeCell(task, effort, mode, i, Path(out) / task.name / f"{arm}-{effort}-{mode}-r{i}", arm)
        for task in task_dirs for arm in arms for effort in efforts for mode in har_modes for i in range(1, runs + 1)
    ]


# ---- one cell -------------------------------------------------------------


def run_cell(cell: ProbeCell, grade_fn: Callable, client: Any, timeout: float, stop: threading.Event) -> str:
    """Returns "done", "limit" (daily allowance spent) or "skipped" (batch is stopping)."""
    solve_fn = partial(beam_agent.solve_agent, budget=ARMS[cell.arm])
    return rb.run_cell(cell, solve_fn, grade_fn, client, timeout, stop)


# ---- results --------------------------------------------------------------


def read_row(cell: ProbeCell) -> dict | None:
    row = rb.read_row(cell)
    if row is None:
        return None
    log = rb._read_json(cell.dir / "solve_log.json")
    usage = log.get("usage") or {}
    labels = log.get("probe_labels") or {}
    row.update(
        arm=cell.arm, hidden_header=(rb._read_json(cell.task_dir / "task.json").get("config") or {}).get("hidden_header"),
        probe_budget=ARMS[cell.arm], probes_used=log.get("probes_used", 0), probes_refused=log.get("probes_refused", 0),
        ablated_hidden_header=log.get("ablated_hidden_header", False), turns=len(log.get("turns") or []),
        cached_tokens=usage.get("cached_tokens"), cached_share=usage.get("cached_share"),
        **{f"probes_{label}": labels.get(label, 0) for label in LABELS},
    )
    return row


def write_csv(path: Path, rows: list[dict]) -> None:
    observed = {k for r in rows for k in r if k.startswith("fail_")}
    fail_columns = sorted({f"fail_{r}" for r in rb.KNOWN_REASONS} | observed)
    columns = HEAD_COLUMNS + fail_columns + TAIL_COLUMNS
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        for row in rows:
            filled = {c: row.get(c) for c in columns}
            for column in fail_columns:
                if filled[column] is None and row.get("status") in ("ok", None):
                    filled[column] = 0
            writer.writerow({k: "" if v is None else v for k, v in filled.items()})


def _mean(values: list[float]) -> str:
    return f"{sum(values) / len(values):.3f}" if values else "-"


def summarize(rows: list[dict]) -> str:
    """Mean pass rate by arm (and by hidden-header on/off, the split of H11), with probes used and cached share."""
    lines = [
        "Mean pass rate by arm (strict: no invented-endpoint request inside the test)",
        f"{'arm':<8} {'hidden':<7} {'cells':>5} {'pass':>6} {'strict':>6} {'probes':>6} {'cached':>6}",
    ]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    errors = sum(1 for row in rows if row.get("status") == rb.API_ERROR)
    for row in rows:
        if row.get("status") == rb.API_ERROR:
            continue  # the model call failed: not a model result
        groups[(row["arm"], "all")].append(row)
        groups[(row["arm"], str(row.get("hidden_header") or "?"))].append(row)
    for (arm, hidden), group in sorted(groups.items(), key=lambda kv: (list(ARMS).index(kv[0][0]), kv[0][1])):
        strict = [float(r["strict_pass_rate"]) for r in group if r.get("strict_pass_rate") not in (None, "")]
        cached = [float(r["cached_share"]) for r in group if r.get("cached_share") not in (None, "")]
        lines.append(
            f"{arm:<8} {hidden:<7} {len(group):>5} {_mean([float(r['pass_rate'] or 0) for r in group]):>6} {_mean(strict):>6} "
            f"{_mean([float(r.get('probes_used') or 0) for r in group]):>6} {_mean(cached):>6}"
        )
    totals = {k: sum(r.get(f"{k}_tokens") or 0 for r in rows) for k in ("prompt", "completion", "reasoning", "cached")}
    lines.append("Total tokens: " + " ".join(f"{k}={v}" for k, v in totals.items()))
    lines.append(f"api_error rows excluded: {errors}")
    return "\n".join(lines)


# ---- batch ----------------------------------------------------------------


def run_batch(
    tasks_dir: Path, out_dir: Path, *, arms: list[str], efforts: list[str], har_modes: list[str], runs: int, concurrency: int,
    grade_fn: Callable = grade_client, client: Any = None, timeout: float = runner.DEFAULT_TIMEOUT_S, limit: int | None = None,
    log: Callable[..., None] = print,
) -> rb.BatchResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = plan_cells(rb.find_tasks(tasks_dir)[:limit], arms, efforts, har_modes, runs, out_dir)
    stop, lock, finished = threading.Event(), threading.Lock(), [0]

    def work(cell: ProbeCell) -> str:
        try:
            outcome = run_cell(cell, grade_fn, client, timeout, stop)
        except BaseException:  # the worker must stop the queue itself: the main thread learns of it too late
            stop.set()
            raise
        with lock:
            finished[0] += 1
            row = read_row(cell) if outcome == "done" else None
            detail = f"{row['status']} pass_rate={float(row['pass_rate']):.3f} probes={row['probes_used']}" if row else outcome
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
    remaining = [c for c in cells if not rb.is_recorded(c)]
    return rb.BatchResult(rows=rows, remaining=remaining, stopped=stop.is_set())


# ---- dry run: a scripted fake client (no network) ----------------------------


class DryRunClient:
    """Fake OpenAI client: with tools it probes once (an unauthenticated list request) and then submits the
    reference client; without tools it answers in one turn. Stateless, so it is safe across threads."""

    def __init__(self, tasks_dir: Path):
        self.tasks = {d.name: d for d in rb.find_tasks(tasks_dir)}
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create)))

    def _task_of(self, user: str) -> Path:
        titles = {d: (d / "goal.md").read_text(encoding="utf-8").splitlines()[0] for d in self.tasks.values()}
        return next(d for d, title in titles.items() if title in user)

    def _create(self, **request: Any) -> Any:
        import json
        messages = request["messages"]
        task = self._task_of(messages[1]["content"])
        ref = (task / "ref_client.py").read_text(encoding="utf-8")
        word = json.loads((task / "engine.json").read_text(encoding="utf-8"))["path_word"].strip("/")
        probed = any(m["role"] == "tool" for m in messages)
        if "tools" not in request:
            message = SimpleNamespace(content=f"```python\n{ref}```", tool_calls=None, reasoning_content="fake")
        elif not probed:
            call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="http_request", arguments=json.dumps({"method": "GET", "path": f"/{word}"})))
            message = SimpleNamespace(content=None, tool_calls=[call], reasoning_content="fake")
        else:
            call = SimpleNamespace(id="call_2", function=SimpleNamespace(name="submit_client", arguments=json.dumps({"code": ref})))
            message = SimpleNamespace(content=None, tool_calls=[call], reasoning_content="fake")
        used = sum(len(str(m)) for m in messages) // 4
        usage = SimpleNamespace(
            prompt_tokens=used, completion_tokens=50, completion_tokens_details=SimpleNamespace(reasoning_tokens=20),
            prompt_tokens_details=SimpleNamespace(cached_tokens=used // 2 if probed else 0),
        )
        parsed = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)
        return SimpleNamespace(parse=lambda: parsed, headers={})


# ---- CLI ------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Batch-run the case-2 arms (placebo, P5, P20) and grade every client.")
    p.add_argument("--tasks", required=True, type=Path, help="directory of task dirs (each has task.json)")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    p.add_argument("--effort", nargs="+", default=["high"], choices=beam.EFFORTS)
    p.add_argument("--har-mode", nargs="+", default=["raw"], choices=beam.HAR_MODES)
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--limit", type=int, default=None, help="use only the first N tasks (sorted by name)")
    p.add_argument("--timeout", type=float, default=runner.DEFAULT_TIMEOUT_S, help="grading time limit per task, seconds")
    p.add_argument("--dry-run", action="store_true", help="use a scripted fake client: no Beam calls; prints the results CSV")
    return p


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    args = _parser().parse_args(argv)
    if not args.tasks.is_dir() or not rb.find_tasks(args.tasks):
        print(f"error: no task dirs (with task.json) in {args.tasks}", file=sys.stderr)
        return rb.EXIT_SETUP
    if args.runs < 1 or args.concurrency < 1 or (args.limit is not None and args.limit < 1):
        print("error: --runs, --concurrency and --limit must be at least 1", file=sys.stderr)
        return rb.EXIT_SETUP
    manifest_path = args.out / "run_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("error: unreadable run manifest; use a new output directory", file=sys.stderr)
            return rb.EXIT_SETUP
        if manifest.get("dry_run") != args.dry_run:
            print("error: cannot mix synthetic dry-run and real model results; use a new --out", file=sys.stderr)
            return rb.EXIT_SETUP
        if manifest.get("tasks") not in (None, str(args.tasks.resolve())):
            print("error: this output belongs to another task directory; use a new --out", file=sys.stderr)
            return rb.EXIT_SETUP
    elif args.out.exists() and any(args.out.glob("*/*/grade.json")):
        print("error: existing results have no provenance manifest; use a new --out", file=sys.stderr)
        return rb.EXIT_SETUP
    if args.dry_run:
        client = DryRunClient(args.tasks)
    elif client is None:
        rb.load_env_key(rb.ENV_PATH, os.environ)
        if not os.environ.get(beam.API_KEY_ENV):
            print(f"error: {beam.API_KEY_ENV} is not set (environment or {rb.ENV_PATH})", file=sys.stderr)
            return rb.EXIT_SETUP
        client = beam.make_client()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"dry_run": args.dry_run, "model": "scripted_reference" if args.dry_run else beam.MODEL,
                                         "tasks": str(args.tasks.resolve())}, indent=2) + "\n", encoding="utf-8")
    result = run_batch(
        args.tasks, args.out, arms=args.arms, efforts=args.effort, har_modes=args.har_mode, runs=args.runs,
        concurrency=args.concurrency, client=client, timeout=args.timeout, limit=args.limit,
    )
    print()
    print(summarize(result.rows))
    print(f"results: {args.out / 'results.csv'}")
    if args.dry_run:
        print()
        print((args.out / "results.csv").read_text(encoding="utf-8"), end="")
    if result.remaining:
        why = "stopped: daily allowance spent" if result.stopped else "not recorded"
        print(f"\n{len(result.remaining)} cells remaining ({why}). Run the same command to resume:")
        for cell in result.remaining[: rb.REMAINING_LISTED]:
            print(f"  {cell.label}")
        if len(result.remaining) > rb.REMAINING_LISTED:
            print(f"  ... and {len(result.remaining) - rb.REMAINING_LISTED} more")
    return rb.EXIT_STOPPED if result.stopped else (1 if result.remaining else rb.EXIT_OK)


if __name__ == "__main__":
    sys.exit(main())
