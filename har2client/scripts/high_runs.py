"""Per-task mean over the high-effort runs (PREREG.md deviation 2026-10-27, "two more runs (r2, r3) at high").

At high effort a task's score is the mean over runs 1, 2 and 3. Low and medium stay single-run. This module only checks and
joins rows: the analysis scripts already average the rows of one task (`analyze_heal_v3.build_step`,
`analyze_tierb_effort.build_steps`). A run that does not hold exactly one scored row for every task of run 1 is an error, so that
no task silently has fewer runs than the others.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

API_ERROR = "api_error"
EXTRA_RUNS = (2, 3)


class RunMismatchError(ValueError):
    """The runs of a step do not cover the same tasks, or a task has no scored row in a run."""


def label(effort: str, runs: int) -> str:
    """`high (mean of 3 runs)` for a step with several runs per task, else the effort name."""
    return f"{effort} (mean of {runs} runs)" if runs > 1 else effort


def extra_run_paths(directory: Path, stem: str) -> list[Path]:
    """`<dir>/<stem>_r2.csv` and `_r3.csv` that exist. A repeat without the one before it is an error."""
    found = {n: Path(directory) / f"{stem}_r{n}.csv" for n in EXTRA_RUNS}
    present = {n: p for n, p in found.items() if p.is_file()}
    if 3 in present and 2 not in present:
        raise RunMismatchError(f"{found[3].name} found but {found[2].name} is missing: r3 without r2")
    return [present[n] for n in EXTRA_RUNS if n in present]


def _check_run(rows: Sequence[dict], number: int) -> set[str]:
    seen: set[str] = set()
    for r in rows:
        if r.get("status") == API_ERROR:
            raise RunMismatchError(f"run {number}: task {r['task']!r} is an api_error row; the task would have fewer runs than the others")
        if r["task"] in seen:
            raise RunMismatchError(f"run {number}: task {r['task']!r} appears twice")
        seen.add(r["task"])
    return seen


def merge_runs(runs: Sequence[Sequence[dict]], expected: int | None = None) -> list[dict]:
    """Rows of every run in one list, the `run` column set to 1, 2, ... in order. Raises `RunMismatchError` when the runs differ.

    Each run must hold one non-`api_error` row per task, and every run the same tasks (as many as `expected` when given).
    The input rows are not changed.
    """
    if not runs:
        raise RunMismatchError("no runs to merge")
    task_sets = [_check_run(rows, n) for n, rows in enumerate(runs, start=1)]
    union = set().union(*task_sets)
    for n, tasks in enumerate(task_sets, start=1):
        if tasks != union:
            raise RunMismatchError(f"run {n}: missing {len(union - tasks)} task(s) that another run has: {sorted(union - tasks)[:5]}")
    if expected is not None and len(union) != expected:
        raise RunMismatchError(f"runs hold {len(union)} tasks, expected {expected}")
    return [{**r, "run": str(n)} for n, rows in enumerate(runs, start=1) for r in rows]
