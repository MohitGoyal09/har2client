#!/usr/bin/env python3
"""Case 3 v3 evaluation suite: score every finished (arm, effort) step, test H16-H21 and the saturation rule, write the report.

Usage (one command, any subset of the 6 steps may be present):
  uv run python scripts/analyze_heal_v3.py [--batch-root runs/batch] [--tasks-base runs/heal_v3_eval]
      [--tasks-hint runs/heal_v3_eval_hint] [--out results/analysis/heal_v3_eval]

Reads `<batch-root>/v3eval_{base,hint}_{low,medium,high}/results.csv` and the cells beside it (read-only). For each present
step it writes the scored CSV `results/heal_v3_eval_<arm>_<effort>.csv` (`scripts/score_heal.py`, with the per-change credit
column). At high, the repeats `<batch-root>/v3eval_<arm>_high_r2` and `_r3` are scored the same way (written as
`heal_v3_eval_<arm>_high_r2.csv`, `_r3.csv`) and the per-task score is the mean of the runs found (3 in the release; PREREG.md
deviation 2026-10-27); a repeat that misses a task is an error. Low and medium stay single-run. Then `<out>/report.md`, `<out>/summary.json` and the chart `results/figures/heal_v3_eval_frontier.{png,svg,json}`.
Pre-registered tests and reading rules: `scripts/heal_v3_verdicts.py` and PREREG.md ("Case 3 v3 evaluation suite").
`api_error` rows are not model results: they are left out of every number and counted.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import heal_v3_verdicts as hv  # noqa: E402
import high_runs  # noqa: E402
import plot_frontier as pf  # noqa: E402
import score_heal  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH_ROOT = ROOT / "runs" / "batch"
DEFAULT_TASKS = {"base": ROOT / "runs" / "heal_v3_eval", "hint": ROOT / "runs" / "heal_v3_eval_hint"}
DEFAULT_OUT = ROOT / "results" / "analysis" / "heal_v3_eval"
DEFAULT_SCORED_DIR = ROOT / "results"
DEFAULT_FIGURE = ROOT / "results" / "figures" / "heal_v3_eval_frontier"
DEFAULT_FAMILY_FIGURE = ROOT / "results" / "figures" / "heal_v3_eval_families"
API_ERROR = "api_error"
HIGH_EFFORT = "high"
SERIES_LABEL = {"base": "base", "hint": "+ contract hint"}
FAMILY_ORDER = ("single", "release3", "release4", "partial", "sdk", "misleading", "terse")


# ------------------------------------------------------------ loading


def _num(value: object) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except ValueError:
        return None


def _credit(row: dict) -> dict[str, float]:
    try:
        data = json.loads(row.get("change_credit") or "{}")
    except ValueError:
        return {}
    return {k: float(v) for k, v in data.items()}


def _task_score(runs: list[dict]) -> hv.TaskScore:
    """One task at one step: the mean over its runs (1 run per cell in the suite)."""
    passes = [_num(r.get("pass_rate")) or 0.0 for r in runs]  # a failed run without a score counts as 0
    tokens = [t for t in (_num(r.get("completion_tokens")) for r in runs) if t is not None]
    credit: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        for change, value in _credit(r).items():
            credit[change].append(value)
    return hv.TaskScore(
        family=runs[0].get("set") or "", pass_rate=hv.mean(passes),
        strict=hv.mean([_num(r.get("strict_pass_rate")) or 0.0 for r in runs]),
        full=hv.mean([1.0 if p >= 1.0 - hv.EPS else 0.0 for p in passes]),
        tokens=hv.mean(tokens), credit={c: hv.mean(v) for c, v in sorted(credit.items())},
    )


def build_step(arm: str, effort: str, rows: list[dict], expected: int) -> hv.Step:
    """A step from its scored rows. `finished` counts every task with a row; `api_error` rows give no score."""
    by_task: dict[str, list[dict]] = defaultdict(list)
    finished, errors = set(), 0
    for r in rows:
        finished.add(r["task"])
        if r.get("status") == API_ERROR:
            errors += 1
        else:
            by_task[r["task"]].append(r)
    scores = {task: _task_score(runs) for task, runs in sorted(by_task.items())}
    runs = max((len(r) for r in by_task.values()), default=1)
    return hv.Step(arm, effort, expected, len(finished), errors, scores, runs)


def count_tasks(tasks_dir: Path) -> int:
    return sum(1 for p in Path(tasks_dir).iterdir() if (p / "task.json").is_file()) if Path(tasks_dir).is_dir() else 0


def _with_repeat_runs(arm: str, run1: list[dict], batch_root: Path, tasks_dir: Path, scored_dir: Path, score: Callable,
                      scorer, expected: int) -> list[dict]:
    """Rows of run 1 and of every repeat `v3eval_<arm>_high_r2`, `_r3` that has a results.csv (each scored and written), checked and joined."""
    runs = [run1]
    for n in high_runs.EXTRA_RUNS:
        cell_root = Path(batch_root) / f"v3eval_{arm}_{HIGH_EFFORT}_r{n}"
        if not (cell_root / "results.csv").is_file():
            continue
        columns, rows = score(tasks_dir, cell_root, cell_root / "results.csv", scorer)
        score_heal.write_csv(Path(scored_dir) / f"heal_v3_eval_{arm}_{HIGH_EFFORT}_r{n}.csv", columns, rows)
        runs.append(rows)
    return high_runs.merge_runs(runs, expected) if len(runs) > 1 else run1


def load_steps(batch_root: Path, tasks: dict[str, Path], scored_dir: Path,
               score_fn: Callable | None = None) -> dict[tuple[str, str], hv.Step]:
    """Score each present step (writing its CSV) and build its Step. The v1 baseline is graded once per task and arm.

    `score_fn(tasks_dir, runs_dir, results_csv, scorer)` defaults to `score_heal.score_runs`; tests pass a fake.
    """
    steps = {}
    for arm in hv.ARMS:
        scorer = score_heal.Scorer(tasks[arm]) if score_fn is None else None
        score = score_fn or score_heal.score_runs
        for effort in hv.EFFORTS:
            cell_root = Path(batch_root) / f"v3eval_{arm}_{effort}"
            results = cell_root / "results.csv"
            if not results.is_file():
                continue
            columns, rows = score(tasks[arm], cell_root, results, scorer)
            score_heal.write_csv(Path(scored_dir) / f"heal_v3_eval_{arm}_{effort}.csv", columns, rows)
            expected = count_tasks(tasks[arm])
            if effort == HIGH_EFFORT:
                rows = _with_repeat_runs(arm, rows, batch_root, tasks[arm], scored_dir, score, scorer, expected)
            steps[(arm, effort)] = build_step(arm, effort, rows, expected)
    return steps


# ------------------------------------------------------------ numbers


def step_summary(step: hv.Step) -> dict:
    scores = list(step.scores.values())
    return {
        "tasks_finished": step.finished, "tasks_expected": step.expected, "complete": step.complete, "api_error_rows": step.api_errors,
        "runs_per_task": step.runs,
        "n_scored": len(scores), "pass_rate": hv.mean(s.pass_rate for s in scores), "strict_pass_rate": hv.mean(s.strict for s in scores),
        "full_repair_share": hv.mean(s.full for s in scores), "mean_output_tokens": hv.mean(s.tokens for s in scores if s.tokens is not None),
    }


def family_summary(step: hv.Step) -> dict[str, dict]:
    out = {}
    for family in sorted({s.family for s in step.scores.values()}, key=_family_key):
        scores = [s for s in step.scores.values() if s.family == family]
        out[family] = {
            "n": len(scores), "pass_rate": hv.mean(s.pass_rate for s in scores), "strict_pass_rate": hv.mean(s.strict for s in scores),
            "full_repair_share": hv.mean(s.full for s in scores), "mean_output_tokens": hv.mean(s.tokens for s in scores if s.tokens is not None),
        }
    return out


def _family_key(name: str) -> tuple[int, str]:
    return (FAMILY_ORDER.index(name) if name in FAMILY_ORDER else len(FAMILY_ORDER), name)


def credit_by_change(step: hv.Step) -> dict[str, dict]:
    """Per change kind: the mean per-task credit over the tasks whose release holds the change."""
    values: dict[str, list[float]] = defaultdict(list)
    for s in step.scores.values():
        for change, v in s.credit.items():
            values[change].append(v)
    return {c: {"n_tasks": len(v), "credit": hv.mean(v)} for c, v in sorted(values.items())}


def hint_minus_base(a: hv.Analysis) -> dict[str, dict]:
    out = {}
    for effort in hv.EFFORTS:
        row = {m: a.hint_minus_base(effort, m) for m in ("pass", "strict", "full")}
        if row["pass"] is not None:
            out[effort] = {m: est.to_dict() for m, est in row.items()}
    return out


def effort_contrasts(a: hv.Analysis) -> dict[str, dict]:
    out = {}
    for arm in hv.ARMS:
        for hi, lo in (("high", "low"),):
            est = a.effort_contrast(arm, hi, lo)
            if est is not None:
                full = a.effort_contrast(arm, hi, lo, "full")
                out[f"{arm}: {hi} - {lo}"] = {"pass": est.to_dict(), "full": full.to_dict(), "output_token_ratio": a.ratio(arm, hi, lo)}
    return out


def build_summary(a: hv.Analysis, verdicts: list[dict]) -> dict:
    present = sorted(a.steps, key=lambda k: (k[0], hv.EFFORTS.index(k[1])))
    return {
        "n_boot": a.n_boot, "seed": a.seed, "metric": "pass_rate (primary); points are percentage points",
        "steps": {f"{arm}-{effort}": {**step_summary(a.steps[arm, effort]), "usable_for_verdicts": a.step(arm, effort) is not None}
                  for arm, effort in present},
        "families": {f"{arm}-{effort}": family_summary(a.steps[arm, effort]) for arm, effort in present},
        "hint_minus_base": hint_minus_base(a),
        "effort_contrasts": effort_contrasts(a),
        "change_credit": {f"{arm}-{effort}": credit_by_change(a.steps[arm, effort]) for arm, effort in present},
        "api_error_rows": {f"{arm}-{effort}": a.steps[arm, effort].api_errors for arm, effort in present},
        "verdicts": verdicts,
    }


# ------------------------------------------------------------ report


def _eff(step: hv.Step, effort: str | None = None) -> str:
    """Effort name; a step with several runs per task reads `high (mean of 3 runs)`."""
    return high_runs.label(effort or step.effort, step.runs)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _tok(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


def _est(est: hv.Est | None) -> str:
    return "n/a" if est is None else f"{est.mean:+.1f} [{est.lo:+.1f}, {est.hi:+.1f}]"


def md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|", *["| " + " | ".join(r) + " |" for r in rows], ""]


def _present(a: hv.Analysis) -> list[tuple[str, str]]:
    return sorted(a.steps, key=lambda k: (k[0], hv.EFFORTS.index(k[1])))


def section_steps(a: hv.Analysis) -> list[str]:
    rows = []
    for arm in hv.ARMS:
        for effort in hv.EFFORTS:
            s = a.steps.get((arm, effort))
            if s is None:
                rows.append([arm, effort, "not run yet", "", ""])
                continue
            state = "complete" if s.complete else "partial (not used: a step must finish every task)"
            rows.append([arm, _eff(s), state, f"{s.finished}/{s.expected}", str(s.api_errors)])
    return ["## Steps", "", *md_table(["arm", "effort", "state", "tasks finished", "api_error rows (excluded)"], rows)]


def section_verdicts(verdicts: list[dict]) -> list[str]:
    out = ["## Verdicts (pre-registered tests, PREREG.md)", "", *md_table(["test", "verdict", "evidence"], [[v["id"], f"**{v['status']}**", v["evidence"]] for v in verdicts])]
    for v in verdicts:
        out += [f"- **{v['id']}** {v['title']}. Test: {v['test']}. Supported if: {v['supported_if']}. Rejected if: {v['rejected_if']}."]
    return [*out, ""]


def section_overall(a: hv.Analysis) -> list[str]:
    rows = []
    for arm, effort in _present(a):
        s = step_summary(a.steps[arm, effort])
        flag = "" if s["complete"] else " (partial)"
        rows.append([arm, _eff(a.steps[arm, effort]) + flag, str(s["n_scored"]), _pct(s["pass_rate"]), _pct(s["strict_pass_rate"]), _pct(s["full_repair_share"]),
                     _tok(s["mean_output_tokens"])])
    return ["## Per arm and effort", "", *md_table(["arm", "effort", "tasks scored", "pass rate", "strict pass rate", "full repair", "mean output tokens"], rows)]


def section_families(a: hv.Analysis) -> list[str]:
    rows = []
    for arm, effort in _present(a):
        for family, f in family_summary(a.steps[arm, effort]).items():
            rows.append([arm, _eff(a.steps[arm, effort]), family, str(f["n"]), _pct(f["pass_rate"]), _pct(f["strict_pass_rate"]), _pct(f["full_repair_share"]),
                         _tok(f["mean_output_tokens"])])
    return ["## Per family", "", *md_table(["arm", "effort", "family", "n", "pass rate", "strict", "full repair", "mean output tokens"], rows)]


def section_hint(a: hv.Analysis) -> list[str]:
    rows = []
    for effort in hv.EFFORTS:
        est = {m: a.hint_minus_base(effort, m) for m in ("pass", "strict", "full")}
        if est["pass"] is None:
            continue
        base, hint = (hv.mean(s.pass_rate for s in a.steps[arm, effort].scores.values()) for arm in hv.ARMS)
        rows.append([hv.effort_label(a, effort), str(est["pass"].n), _pct(base), _pct(hint), _est(est["pass"]), _est(est["strict"]), _est(est["full"])])
    note = f"Paired by task; 95% CI by paired bootstrap over tasks ({a.n_boot:,} resamples, fixed seed {a.seed}); points."
    return ["## Hint - base (paired by task)", "", note, "",
            *md_table(["effort", "paired tasks", "base pass", "hint pass", "pass diff [CI]", "strict diff [CI]", "full-repair diff [CI]"], rows)]


def _est_dict(d: dict) -> str:
    return _est(hv.Est(d["n"], d["mean"], d["ci_low"], d["ci_high"]))


def section_effort(a: hv.Analysis) -> list[str]:
    rows = []
    for name, c in effort_contrasts(a).items():
        ratio = c["output_token_ratio"]
        rows.append([name.replace("high", hv.effort_label(a, "high", (name.split(":")[0],)), 1), str(c["pass"]["n"]), _est_dict(c["pass"]), _est_dict(c["full"]), "n/a" if ratio is None else f"{ratio:.2f}x"])
    if not rows:
        rows = [["none yet (needs high and low in one arm)", "", "", "", ""]]
    return ["## Effort contrasts (paired by task, per arm)", "",
            *md_table(["contrast", "paired tasks", "pass diff [CI]", "full-repair diff [CI]", "output tokens ratio"], rows)]


def section_credit(a: hv.Analysis) -> list[str]:
    present = _present(a)
    per_step = {k: credit_by_change(a.steps[k]) for k in present}
    changes = sorted({c for d in per_step.values() for c in d})
    rows = [[c, *[(_pct(per_step[k][c]["credit"]) + f" (n={per_step[k][c]['n_tasks']})") if c in per_step[k] else "" for k in present]] for c in changes]
    note = "Per-change credit = pass share over the hidden tests whose `needs` list holds the change; mean over the tasks whose release holds it."
    return ["## Per-change credit, by change kind", "", note, "", *md_table(["change", *[f"{arm}-{_eff(a.steps[arm, e])}" for arm, e in present]], rows)]


def section_notes() -> list[str]:
    return ["## Reading rules", "", *[f"- {rule}" for rule in hv.READING_RULES], ""]


def render_report(a: hv.Analysis, verdicts: list[dict]) -> str:
    api = sum(s.api_errors for s in a.steps.values())
    head = ["# Case 3 v3 evaluation suite: analysis", "",
            f"Steps present: {len(a.steps)} of {len(hv.ARMS) * len(hv.EFFORTS)}. `api_error` rows excluded from every number: {api}.", ""]
    multi = sorted({st.runs for st in a.steps.values() if st.runs > 1})
    if multi:
        head += [f"Runs per task: steps marked \"mean of {multi[-1]} runs\" score each task as the mean over its runs (PREREG.md deviation "
                 "2026-10-27); every other step is 1 run per task. Their CIs are over tasks only.", ""]
    parts = [head, section_steps(a), section_verdicts(verdicts), section_overall(a), section_hint(a), section_effort(a), section_families(a),
             section_credit(a), section_notes()]
    return "\n".join(line for part in parts for line in part).rstrip() + "\n"


# ------------------------------------------------------------ chart


def frontier_points(a: hv.Analysis, n_boot: int, seed: int) -> tuple[list[pf.Point], dict]:
    """One point per usable step, one series per arm. Each step is computed on its own tasks (they may hold different task counts)."""
    points, series = [], {}
    for arm in hv.ARMS:
        label, counts = SERIES_LABEL[arm], {}
        for effort in hv.EFFORTS:
            step = a.step(arm, effort)
            if step is None:
                continue
            rows = [pf.Row(t, effort, "1", "ok", s.pass_rate, None, s.tokens) for t, s in step.scores.items()]
            pts, _ = pf.compute_series_points(label, rows, "output", n_boot, seed)
            points += pts
            counts[effort] = len(rows)
        if counts:
            top = max(counts.values())
            text = top if all(n == top for n in counts.values()) else str(top) + "".join(f"; {e} {n}" for e, n in counts.items() if n != top)
            run_counts = [a.steps[arm, e].runs for e in counts]
            series[label] = {"n_tasks": text, "dropped_unpaired_tasks": 0, "tasks_by_effort": counts, "runs_per_task_min": min(run_counts),
                             "runs_per_task_max": max(run_counts), "runs_by_effort": {e: a.steps[arm, e].runs for e in counts},
                             "api_error_rows_excluded": {e: a.steps[arm, e].api_errors for e in counts if a.steps[arm, e].api_errors}}
    flags = pf.pareto_flags(points)
    points = [dataclasses.replace(p, on_pareto=f) for p, f in zip(points, flags)]
    meta = {"x_mode": "output", "resamples": n_boot, "seed": seed, "ci_level": pf.CI_LEVEL, "series": series, "warnings": []}
    return points, meta


def write_chart(a: hv.Analysis, out: Path) -> list[pf.Point]:
    points, meta = frontier_points(a, pf.DEFAULT_RESAMPLES, pf.DEFAULT_SEED)
    if not points:
        return []
    pf.write_json(out, points, meta)
    pf.plot(points, meta, "Case 3 v3 evaluation suite: pass rate vs output tokens", out)
    return points


# ------------------------------------------------------------ main


def main(argv: list[str] | None = None, score_fn: Callable | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT, help="dir with v3eval_<arm>_<effort>/ steps (read-only)")
    p.add_argument("--tasks-base", type=Path, default=DEFAULT_TASKS["base"])
    p.add_argument("--tasks-hint", type=Path, default=DEFAULT_TASKS["hint"])
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report.md and summary.json go here")
    p.add_argument("--scored-dir", type=Path, default=DEFAULT_SCORED_DIR, help="where heal_v3_eval_<arm>_<effort>.csv are written")
    p.add_argument("--figure", type=Path, default=DEFAULT_FIGURE, help="chart path prefix (no extension)")
    p.add_argument("--family-figure", type=Path, default=DEFAULT_FAMILY_FIGURE, help="pass-rate-by-family chart path prefix (no extension)")
    p.add_argument("--n-boot", type=int, default=hv.DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=hv.DEFAULT_SEED)
    args = p.parse_args(argv)

    steps = load_steps(args.batch_root, {"base": args.tasks_base, "hint": args.tasks_hint}, args.scored_dir, score_fn)
    if not steps:
        print(f"no v3eval_<arm>_<effort>/results.csv under {args.batch_root}", file=sys.stderr)
        return 2
    analysis = hv.Analysis(steps, args.n_boot, args.seed)
    verdicts = hv.all_verdicts(analysis)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(render_report(analysis, verdicts), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(build_summary(analysis, verdicts), indent=2) + "\n", encoding="utf-8")
    try:
        write_chart(analysis, args.figure)
        from plot_heal_families import write_family_chart  # lazy: it imports this module

        write_family_chart(analysis, args.family_figure)
    except ImportError as exc:  # matplotlib is an optional extra
        print(f"warning: chart skipped ({exc}); run with `uv run --extra plots`", file=sys.stderr)
    print(f"{len(steps)} step(s) -> {args.out / 'report.md'}")
    for v in verdicts:
        print(f"  {v['id']}: {v['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
