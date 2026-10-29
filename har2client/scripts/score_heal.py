#!/usr/bin/env python3
"""Case 3 scoring: join a batch's results.csv with the repair-task factors, the regression rate and the patch size.

Usage:
  uv run python scripts/score_heal.py --tasks runs/heal_suite --runs runs/batch/heal_high --out results/heal_high_scored.csv
      [--results <runs>/results.csv]

Per attempt (one row of results.csv; its client is `<runs>/<task>/<effort>-<har_mode>-r<run>/client.py`) it adds:
- `regression_rate`: the share of the hidden tests that `client_v1.py` passes on v2 and the patched client fails (broke what
  worked), with `regression_count` (those tests), `v1_pass_count` (tests `client_v1.py` passes on v2) and `n_tests`;
- `patch_lines` (lines removed + added) and `patch_ast_nodes` (AST nodes removed + added) between `client_v1.py` and the
  submission (empty when it does not parse); reported, never rewarded;
- the task factors: `set`, `mutations` ("+"-joined), `changes`, `error_detail`, `evidence`, `identifiable`;
- `v1_pass_rate`: the score of `client_v1.py` on v2 (the no-op baseline);
- `change_credit` (v3 tasks, whose tests.json carries `needs`): a JSON object, change -> pass share over the hidden tests whose
  `needs` list holds that change (empty for older tasks). It is extra output: no other column depends on it.
Rows without a client keep empty score columns. The patched client's per-test result is read from the cell's grade.json
(the batch graded it on the task's fresh v2 server); a client without per-test results is graded again.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from har2client import grade as grading
from har2client.heal import patch

FACTOR_COLUMNS = ["set", "mutations", "changes", "error_detail", "evidence", "identifiable"]
SCORE_COLUMNS = ["regression_rate", "regression_count", "v1_pass_count", "n_tests", "patch_lines", "patch_ast_nodes", "v1_pass_rate", "change_credit"]


def regression(v1_passed: list[bool], patched_passed: list[bool]) -> dict:
    """Tests (by position) that client_v1 passes on v2 and the patched client fails."""
    if len(v1_passed) != len(patched_passed):
        raise ValueError(f"{len(v1_passed)} tests for client_v1 but {len(patched_passed)} for the patched client")
    n = len(v1_passed)
    broke = sum(1 for before, after in zip(v1_passed, patched_passed) if before and not after)
    return {"regression_count": broke, "v1_pass_count": sum(v1_passed), "n_tests": n, "regression_rate": broke / n if n else None}


def change_credit(tests: list[dict], grade: dict) -> dict[str, float]:
    """Per change: the share of the tests that need it (`needs` in tests.json, by test position) that the client passes."""
    passed = _passed(grade)
    if len(tests) != len(passed):
        raise ValueError(f"{len(tests)} tests in tests.json but {len(passed)} in the grade")
    totals: dict[str, list[bool]] = {}
    for test, ok in zip(tests, passed):
        for change in test.get("needs") or []:
            totals.setdefault(change, []).append(ok)
    return {change: sum(oks) / len(oks) for change, oks in sorted(totals.items())}


def task_factors(task: dict) -> dict:
    """Factors of a task from its task.json (heal48 tasks, built before the factors existed, are core tasks)."""
    heal = task["heal"]
    names = heal.get("mutations") or [heal["mutation"]]
    return {
        "set": heal.get("set", "core"), "mutations": "+".join(names), "changes": len(names), "error_detail": heal.get("error_detail", "verbose"),
        "evidence": heal.get("evidence", "both"), "identifiable": heal.get("identifiable", True),
    }


def _passed(grade: dict) -> list[bool]:
    return [bool(r["passed"]) for r in sorted(grade["results"], key=lambda r: r["index"])]


class Scorer:
    """Scores attempts of one tasks dir; the `client_v1.py` result on v2 is graded once per task."""

    def __init__(self, tasks_dir: Path, grade_fn=grading.grade):
        self.tasks_dir = Path(tasks_dir)
        self.grade_fn = grade_fn
        self._v1: dict[str, list[bool]] = {}

    def v1_passed(self, task: str) -> list[bool]:
        if task not in self._v1:
            d = self.tasks_dir / task
            self._v1[task] = _passed(self.grade_fn(d, d / "client_v1.py"))
        return self._v1[task]

    @staticmethod
    def _tests(task_dir: Path) -> list[dict]:
        path = task_dir / "tests.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def score(self, task: str, client_path: Path | None, patched_grade: dict | None = None) -> dict:
        d = self.tasks_dir / task
        out = {**task_factors(json.loads((d / "task.json").read_text(encoding="utf-8")))}
        v1 = self.v1_passed(task)
        out["v1_pass_rate"] = sum(v1) / len(v1) if v1 else None
        if client_path is None or not Path(client_path).is_file():
            return out
        client_path = Path(client_path)
        grade = patched_grade if patched_grade and patched_grade.get("results") else self.grade_fn(d, client_path)
        out.update(regression(v1, _passed(grade)))
        credit = change_credit(self._tests(d), grade)
        if credit:
            out["change_credit"] = json.dumps(credit)
        size = patch.patch_size((d / "client_v1.py").read_text(encoding="utf-8"), client_path.read_text(encoding="utf-8", errors="replace"))
        out.update(patch_lines=size["changed_lines"], patch_ast_nodes=size["changed_ast_nodes"])
        return out


def score_runs(tasks_dir: Path, runs_dir: Path, results_csv: Path, scorer: Scorer | None = None) -> tuple[list[str], list[dict]]:
    scorer = scorer or Scorer(tasks_dir)
    with open(results_csv, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns, rows = list(reader.fieldnames or []), list(reader)
    out = []
    for row in rows:
        cell = Path(runs_dir) / row["task"] / f"{row['effort']}-{row['har_mode']}-r{row['run']}"
        try:
            grade = json.loads((cell / "grade.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            grade = None
        out.append({**row, **scorer.score(row["task"], cell / "client.py", grade)})
    return [*columns, *[c for c in (*FACTOR_COLUMNS, *SCORE_COLUMNS) if c not in columns]], out


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row.get(k) is None else row[k] for k in columns})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tasks", required=True, type=Path, help="the repair task dirs (runs/heal_suite)")
    p.add_argument("--runs", required=True, type=Path, help="the batch out dir (cells and results.csv)")
    p.add_argument("--out", required=True, type=Path, help="the scored CSV")
    p.add_argument("--results", type=Path, help="results CSV (default: <runs>/results.csv)")
    args = p.parse_args(argv)
    results = args.results or args.runs / "results.csv"
    if not results.is_file():
        print(f"no results CSV at {results}", file=sys.stderr)
        return 2
    columns, rows = score_runs(args.tasks, args.runs, results)
    write_csv(args.out, columns, rows)
    scored = sum(1 for r in rows if r.get("regression_rate") is not None)
    print(f"scored {scored}/{len(rows)} attempts -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
