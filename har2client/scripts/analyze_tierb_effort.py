#!/usr/bin/env python3
"""Case 1 (Tier B, 50 tasks): the three-point effort curve (low, medium, high), one command, any subset of steps.

Usage:
  uv run --extra plots python scripts/analyze_tierb_effort.py [--results results/tierb50_v1.csv ...]
      [--high-runs results/tierb50_high_r2.csv results/tierb50_high_r3.csv]
      [--batch-root runs/batch] [--tasks runs/tierb50] [--tier-a results/set30_v1_tierA.csv]
      [--out results/analysis/tierb50_effort] [--figure results/figures/tierb50_frontier]

Reads (read-only): the --results CSVs (run 1 holds low and high), the --high-runs CSVs (repeat runs 2 and 3 of the as-built
tasks at high; a task's score at high is the mean over the runs found, "mean of 3 runs" in the release, PREREG.md deviation
2026-10-27; a repeat that misses a task is an error), then `<batch-root>/tierb50_<effort>/results.csv` for every effort
the --results files do not have (medium, written by `scripts/run_batch.py`). Writes `<out>/report.md`,
`<out>/summary.json` and the chart `<figure>.{png,svg,json}` (one point per finished effort). Missing steps are marked
"not run yet"; the pre-registered verdict (PREREG.md, "Case 1 Tier B effort curve": test as H18) says "not tested" (xhigh
was dropped 2026-10-27, see PREREG.md deviations). `api_error` rows are not model results: they are left out of every number and counted.
Reading rules for ambiguous criteria are printed in the report (READING_RULES below).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_tierb as atb  # noqa: E402
import high_runs  # noqa: E402
import plot_frontier as pf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = [ROOT / "results" / "tierb50_v1.csv"]
DEFAULT_HIGH_RUNS = [ROOT / "results" / f"tierb50_high_r{n}.csv" for n in high_runs.EXTRA_RUNS]
DEFAULT_BATCH_ROOT = ROOT / "runs" / "batch"
HIGH = "high"
DEFAULT_TASKS = ROOT / "runs" / "tierb50"
DEFAULT_TIER_A = ROOT / "results" / "set30_v1_tierA.csv"
DEFAULT_STRICT_JSON = [ROOT / "results" / "tierb50_v1_regrade_strict.json", ROOT / "results" / "set30_v1_regrade_newtests.json"]
DEFAULT_OUT = ROOT / "results" / "analysis" / "tierb50_effort"
DEFAULT_FIGURE = ROOT / "results" / "figures" / "tierb50_frontier"

EFFORTS = ("low", "medium", "high")
CONTRAST_PAIRS = (("medium", "low"), ("high", "medium"), ("high", "low"))
SWITCH_ORDER = ("auth", "pagination", "token_expiry", "etag_update", "hidden_header", "envelope", "error_detail")
DROPPED_NOTE = "xhigh dropped 2026-10-27, see PREREG.md deviations"
POINTS = 100.0
EPS = 1e-9
DEFAULT_N_BOOT = 10000
DEFAULT_SEED = atb.DEFAULT_SEED
SERIES = "Beam"
CHART_TITLE = "Tier B: pass rate vs tokens"

SUPPORTED, REJECTED, INCONCLUSIVE, NOT_YET = "supported", "rejected", "inconclusive", "not yet testable"
NOT_TESTED = "not tested"
VERDICT_ID = "Tier B effort curve (PREREG: test as H18)"
VERDICT_TEXT = {
    "test": "not tested: " + DROPPED_NOTE,
    "supported_if": "n/a",
    "rejected_if": "n/a",
}

READING_RULES = (
    "Unit = task. A task's score at an effort is the mean over its runs: high = 3 runs (\"mean of 3 runs\"; PREREG.md deviation "
    "2026-10-27), low and medium = 1 run. Contrasts that involve high use these per-task means. Pass rate, strict pass rate and "
    "full-correct share are shares in 0-1; contrasts are in percentage points of `pass_rate` (the primary metric).",
    "`api_error` rows (failed model calls) are left out of every number and counted per effort. Any other failed status "
    "(`no_code`, `grade_error`, ...) counts as pass rate 0.",
    "Full-correct share = share of tasks with pass_rate = 1.0 (all runs). Strict pass rate comes from the CSV column when it has one, "
    "else from the saved strict regrade JSON (`strict_pass_rate` per cell); a task without a strict value is left out of that mean.",
    "95% CIs are percentile intervals from a bootstrap over tasks (paired differences for contrasts), fixed seed; the "
    "resample count is printed in the header.",
    "Output tokens = `completion_tokens` (includes reasoning). A token ratio is the mean output of the first effort over the mean "
    "output of the second, on the tasks both have.",
    "The pre-registered verdict (test as H18, xhigh - high) is not tested: " + DROPPED_NOTE + ".",
    "A step is complete when every task in `runs/tierb50` has a row (`api_error` rows count as finished but give no score). A "
    "partial step is shown in the step table but is not used in contrasts, switch table or chart, so a result never rests on a "
    "few early tasks.",
    "Medium is read from `<batch-root>/tierb50_<effort>/results.csv`; an effort that the --results files already hold "
    "(low, high) is not read again from the batch root.",
    "H4 (simple vs hard tasks) and H9 (pass^3) are not tested here: H9 needs 3 runs per task and this curve has 1; H4 needs the "
    "\"simple\" task definition applied to Tier B switches, which PREREG does not map. The switch table is descriptive (no CIs; switches overlap across tasks).",
    "Tier A (`set30_v1_tierA.csv`, low and high) is a table only: the saturated baseline, not part of the verdict or the chart.",
)


# ------------------------------------------------------------ statistics


@dataclass(frozen=True)
class Stat:
    n: int
    mean: float
    lo: float
    hi: float

    def to_dict(self) -> dict:
        return {"n": self.n, "mean": self.mean, "ci_low": self.lo, "ci_high": self.hi}


def _mean(xs) -> float | None:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def boot_ci(values: list[float], n_boot: int, seed: int, key: str) -> Stat | None:
    """Mean of `values` (one per task, or one paired difference per task) with a 95% percentile CI over task resamples."""
    n = len(values)
    if n == 0:
        return None
    rng = random.Random(f"{seed}:{key}")  # str seeds hash deterministically
    draws = [sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)]
    lo, hi = atb.interval(draws)
    return Stat(n, sum(values) / n, lo, hi)


# ------------------------------------------------------------ loading


@dataclass(frozen=True)
class TaskScore:
    pass_rate: float
    strict: float | None
    full: float
    tokens: float | None
    runs: int


@dataclass(frozen=True)
class Step:
    """One effort: every task with a row so far. `finished` counts api_error rows too; they give no score."""

    effort: str
    expected: int
    finished: int
    api_errors: int
    scores: Mapping[str, TaskScore]

    @property
    def runs(self) -> int:
        """Runs per task behind each score (the most any task has)."""
        return max((s.runs for s in self.scores.values()), default=1)

    @property
    def label(self) -> str:
        return high_runs.label(self.effort, self.runs)

    @property
    def complete(self) -> bool:
        return self.finished >= self.expected

    @property
    def usable(self) -> bool:
        return bool(self.scores) and self.complete


def _num(value: object) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except ValueError:
        return None


def load_rows(paths: list[Path], har_mode: str | None) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
    modes = sorted({r.get("har_mode", "") for r in rows})
    if har_mode is None and len(modes) > 1:
        raise SystemExit(f"error: results mix har_mode values {modes}; pass --har-mode to pick one")
    return [r for r in rows if har_mode is None or r.get("har_mode", "") == har_mode]


def load_strict_map(paths: list[Path]) -> dict[tuple[str, str, str], float]:
    """(task, effort, run) -> strict_pass_rate from regrade JSONs (`cell` = `<effort>-<har_mode>-r<run>`). Missing files are skipped."""
    out: dict[tuple[str, str, str], float] = {}
    for path in paths:
        if not Path(path).is_file():
            continue
        for item in json.loads(Path(path).read_text(encoding="utf-8")):
            effort, run = item["cell"].split("-")[0], item["cell"].rsplit("-r", 1)[-1]
            if item.get("strict_pass_rate") is not None:
                out[(item["task"], effort, run)] = float(item["strict_pass_rate"])
    return out


def _task_score(runs: list[dict], effort: str, strict_map: Mapping[tuple[str, str, str], float]) -> TaskScore:
    passes, stricts, tokens = [], [], []
    for r in runs:
        ok = r.get("status", "ok") == "ok"
        p = (_num(r.get("pass_rate")) or 0.0) if ok else 0.0
        passes.append(p)
        s = 0.0 if not ok else _num(r.get("strict_pass_rate"))
        stricts.append(strict_map.get((r["task"], effort, str(r.get("run", "")).strip())) if s is None else s)
        if (t := _num(r.get("completion_tokens"))) is not None:
            tokens.append(t)
    return TaskScore(
        pass_rate=sum(passes) / len(passes),
        strict=None if any(s is None for s in stricts) else sum(stricts) / len(stricts),
        full=sum(1.0 if p >= 1.0 - EPS else 0.0 for p in passes) / len(passes),
        tokens=_mean(tokens), runs=len(runs),
    )


def build_steps(rows: list[dict], expected: int, strict_map: Mapping[tuple[str, str, str], float] | None = None) -> dict[str, Step]:
    """effort -> Step, for the efforts that have rows. Rows of other efforts are ignored."""
    strict_map = strict_map or {}
    by_cell: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    finished: dict[str, set[str]] = defaultdict(set)
    errors: dict[str, int] = defaultdict(int)
    for r in rows:
        effort = r["effort"]
        if effort not in EFFORTS:
            continue
        finished[effort].add(r["task"])
        if r.get("status") == atb.API_ERROR:
            errors[effort] += 1
        else:
            by_cell[effort][r["task"]].append(r)
    return {
        e: Step(e, expected, len(finished[e]), errors[e],
                {t: _task_score(runs, e, strict_map) for t, runs in sorted(by_cell[e].items())})
        for e in EFFORTS if e in finished
    }


def step_summary(step: Step, n_boot: int, seed: int) -> dict:
    scores = list(step.scores.values())
    strict = [s.strict for s in scores if s.strict is not None]
    tokens = [s.tokens for s in scores if s.tokens is not None]
    key = step.effort
    return {
        "tasks_finished": step.finished, "tasks_expected": step.expected, "n_scored": len(scores), "api_error_rows": step.api_errors,
        "pass_rate": boot_ci([s.pass_rate for s in scores], n_boot, seed, f"{key}:pass"),
        "strict_pass_rate": boot_ci(strict, n_boot, seed, f"{key}:strict"),
        "full_correct_share": boot_ci([s.full for s in scores], n_boot, seed, f"{key}:full"),
        "mean_output_tokens": boot_ci(tokens, n_boot, seed, f"{key}:tokens"),
    }


# ------------------------------------------------------------ analysis


@dataclass(frozen=True)
class Contrast:
    pass_diff: Stat
    full_diff: Stat
    token_ratio: float | None


def paired_contrast(a: Step, b: Step, n_boot: int, seed: int, key: str) -> Contrast | None:
    """a - b over the tasks both steps scored: points of pass rate and of full-correct share, and the output-token ratio a/b."""
    common = sorted(set(a.scores) & set(b.scores))
    if not common:
        return None
    pass_diff = boot_ci([(a.scores[t].pass_rate - b.scores[t].pass_rate) * POINTS for t in common], n_boot, seed, f"{key}:pass")
    full_diff = boot_ci([(a.scores[t].full - b.scores[t].full) * POINTS for t in common], n_boot, seed, f"{key}:full")
    both = [t for t in common if a.scores[t].tokens is not None and b.scores[t].tokens is not None]
    mb = _mean(b.scores[t].tokens for t in both)
    ratio = _mean(a.scores[t].tokens for t in both) / mb if both and mb else None
    return Contrast(pass_diff, full_diff, ratio)


class Analysis:
    """Steps by effort; a step that is not usable behaves as missing."""

    def __init__(self, steps: Mapping[str, Step], n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED):
        self.steps, self.n_boot, self.seed = dict(steps), n_boot, seed

    def step(self, effort: str) -> Step | None:
        s = self.steps.get(effort)
        return s if s is not None and s.usable else None

    def usable_efforts(self) -> list[str]:
        return [e for e in EFFORTS if self.step(e) is not None]

    def state(self, effort: str) -> str:
        s = self.steps.get(effort)
        if s is None:
            return "not run yet"
        if s.complete:
            return "complete" if s.scores else "complete (no scored task)"
        return f"partial ({s.finished}/{s.expected} tasks; not used)"

    def contrasts(self) -> dict[str, Contrast | None]:
        out: dict[str, Contrast | None] = {}
        for hi, lo in CONTRAST_PAIRS:
            a, b = self.step(hi), self.step(lo)
            out[f"{hi}-{lo}"] = paired_contrast(a, b, self.n_boot, self.seed, f"{hi}-{lo}") if a is not None and b is not None else None
        return out


def _verdict(status: str, evidence: str, missing: list[str] | None = None) -> dict:
    return {"id": VERDICT_ID, **VERDICT_TEXT, "status": status, "evidence": evidence, "missing": missing or []}


def effort_verdict(a: Analysis) -> dict:
    """Not tested: the xhigh level was dropped, so the xhigh - high contrast no longer exists."""
    return _verdict(NOT_TESTED, "not tested: " + DROPPED_NOTE)


def _switch_keys(feats: Mapping[str, dict]) -> list[str]:
    keys = {k for f in feats.values() for k in f["config"]}
    return [k for k in SWITCH_ORDER if k in keys] + sorted(keys - set(SWITCH_ORDER))


def switch_table(a: Analysis, feats: Mapping[str, dict]) -> list[dict]:
    """One row per (switch, level): task count and, per usable effort, n and mean pass rate (0-1) of the scored tasks at that level."""
    rows = []
    for switch in _switch_keys(feats):
        for level in sorted({str(f["config"][switch]) for f in feats.values() if switch in f["config"]}):
            members = {t for t, f in feats.items() if str(f["config"].get(switch)) == level}
            by_effort = {}
            for effort in a.usable_efforts():
                vals = [s.pass_rate for t, s in a.steps[effort].scores.items() if t in members]
                if vals:
                    by_effort[effort] = {"n": len(vals), "pass_rate": sum(vals) / len(vals)}
            rows.append({"switch": switch, "level": level, "n_tasks": len(members), "by_effort": by_effort})
    return rows


# ------------------------------------------------------------ summary and report


def _stat(s: Stat | None) -> dict | None:
    return None if s is None else s.to_dict()


def _summary_dict(step: Step, n_boot: int, seed: int) -> dict:
    return {k: (_stat(v) if isinstance(v, Stat) or v is None else v) for k, v in step_summary(step, n_boot, seed).items()}


def build_summary(a: Analysis, feats: Mapping[str, dict], tier_a: Mapping[str, Step], verdict: dict) -> dict:
    contrasts = {
        name: None if c is None else {"pass_diff": c.pass_diff.to_dict(), "full_diff": c.full_diff.to_dict(), "output_token_ratio": c.token_ratio}
        for name, c in a.contrasts().items()
    }
    steps = {}
    for effort in EFFORTS:
        s = a.steps.get(effort)
        steps[effort] = {"state": a.state(effort), "usable_for_verdict": a.step(effort) is not None,
                         **({} if s is None else _summary_dict(s, a.n_boot, a.seed))}
    return {
        "n_boot": a.n_boot, "seed": a.seed,
        "metric": "steps: shares in 0-1 and mean output tokens; contrasts: percentage points of pass_rate / full-correct share",
        "steps": steps, "contrasts": contrasts, "switches": switch_table(a, feats),
        "tier_a": {e: _summary_dict(s, a.n_boot, a.seed) for e, s in tier_a.items()},
        "verdict": verdict, "reading_rules": list(READING_RULES),
    }


def _share(s: Stat | None) -> str:
    return "n/a" if s is None else f"{s.mean:.3f} [{s.lo:.3f}, {s.hi:.3f}]"


def _tokens(s: Stat | None) -> str:
    return "n/a" if s is None else f"{s.mean:,.0f} [{s.lo:,.0f}, {s.hi:,.0f}]"


def _points(s: Stat | None) -> str:
    return "n/a" if s is None else f"{s.mean:+.1f} [{s.lo:+.1f}, {s.hi:+.1f}]"


def md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|", *["| " + " | ".join(r) + " |" for r in rows], ""]


def _effort_rows(a: Analysis, steps: Mapping[str, Step]) -> list[list[str]]:
    rows = []
    for effort in EFFORTS:
        s = steps.get(effort)
        if s is None:
            rows.append([effort, "not run yet", "", "", "", "", "", ""])
            continue
        m = step_summary(s, a.n_boot, a.seed)
        rows.append([s.label, a.state(effort), f"{m['n_scored']}/{s.expected}", _share(m["pass_rate"]), _share(m["strict_pass_rate"]),
                     _share(m["full_correct_share"]), _tokens(m["mean_output_tokens"]), str(s.api_errors)])
    return rows


EFFORT_HEADER = ["effort", "state", "tasks scored", "pass rate [95% CI]", "strict pass rate", "full-correct share", "mean output tokens", "api_error rows (excluded)"]


def section_verdict(v: dict) -> list[str]:
    return ["## Verdict (pre-registered, PREREG.md)", "", *md_table(["test", "verdict", "evidence"], [[v["id"], f"**{v['status']}**", v["evidence"]]]),
            f"Test: {v['test']}. Supported if: {v['supported_if']}. Rejected if: {v['rejected_if']}.", ""]


def _contrast_label(a: Analysis, name: str) -> str:
    """`high-low` -> `high (mean of 3 runs)-low` when a step has several runs per task."""
    return "-".join(a.steps[e].label if e in a.steps else e for e in name.split("-"))


def section_contrasts(a: Analysis) -> list[str]:
    rows = []
    for name, c in a.contrasts().items():
        if c is None:
            hi, lo = name.split("-")
            missing = [e for e in (hi, lo) if a.step(e) is None]
            rows.append([_contrast_label(a, name), "not yet testable (needs " + ", ".join(missing) + ")" if missing else "no paired task", "", "", ""])
        else:
            ratio = "n/a" if c.token_ratio is None else f"{c.token_ratio:.2f}x"
            rows.append([_contrast_label(a, name), str(c.pass_diff.n), _points(c.pass_diff), _points(c.full_diff), ratio])
    return ["## Paired contrasts (by task, points)", "",
            f"95% CI by paired bootstrap over tasks ({a.n_boot:,} resamples, fixed seed {a.seed}).", "",
            *md_table(["contrast", "paired tasks", "pass diff [CI]", "full-correct diff [CI]", "output tokens ratio"], rows)]


def section_switches(a: Analysis, feats: Mapping[str, dict]) -> list[str]:
    efforts = a.usable_efforts()
    rows = []
    for r in switch_table(a, feats):
        cells = [f"{r['by_effort'][e]['pass_rate']:.3f} (n={r['by_effort'][e]['n']})" if e in r["by_effort"] else "-" for e in efforts]
        rows.append([r["switch"], r["level"], str(r["n_tasks"]), *cells])
    return ["## Pass rate by switch level and effort", "",
            "Mean pass rate (0-1) of the scored tasks at that level; descriptive, no CIs.", "",
            *md_table(["switch", "level", "tasks", *[a.steps[e].label for e in efforts]], rows)]


def section_tier_a(a: Analysis, tier_a: Mapping[str, Step]) -> list[str]:
    if not tier_a:
        return ["## Tier A baseline", "", "Tier A results not found.", ""]
    return ["## Tier A baseline (saturated, table only)", "",
            "Tier A: 30 public-API tasks, original CSV; the saturated baseline for this curve (PREREG Deviations: Tier A alone does not separate systems).", "",
            *md_table(EFFORT_HEADER, [[f"Tier A {r[0]}", *r[1:]] for r in _effort_rows(a, tier_a) if r[1] != "not run yet"])]


def render_report(a: Analysis, feats: Mapping[str, dict], tier_a: Mapping[str, Step], verdict: dict, notes: list[str]) -> str:
    api = sum(s.api_errors for s in a.steps.values())
    head = ["# Case 1 Tier B effort curve: analysis", "",
            f"Steps present: {len(a.steps)} of {len(EFFORTS)}. Resamples: {a.n_boot:,}. Seed: {a.seed}. "
            f"`api_error` rows excluded from every number: {api}.", "", *[f"Note: {n}" for n in notes], *([""] if notes else [])]
    per_effort = ["## Per effort", "", "Shares in 0-1; 95% task-bootstrap CI in brackets.", "",
                  *md_table(EFFORT_HEADER, _effort_rows(a, a.steps))]
    parts = [head, section_verdict(verdict), per_effort, section_contrasts(a), section_switches(a, feats), section_tier_a(a, tier_a),
             ["## Reading rules", "", *[f"- {r}" for r in READING_RULES], ""]]
    return "\n".join(line for part in parts for line in part).rstrip() + "\n"


# ------------------------------------------------------------ chart


def frontier_points(a: Analysis) -> tuple[list[pf.Point], dict]:
    """One point per usable effort; each effort uses the tasks it has scored. y = pass rate in points, x = mean output tokens."""
    points, runs, counts, errors = [], [], {}, {}
    for effort in a.usable_efforts():
        step = a.steps[effort]
        m = step_summary(step, a.n_boot, a.seed)
        stat, tok = m["pass_rate"], m["mean_output_tokens"]
        points.append(pf.Point(SERIES, effort, tok.mean if tok else 0.0, stat.mean * POINTS, stat.lo * POINTS, stat.hi * POINTS, stat.n))
        runs += [s.runs for s in step.scores.values()]
        counts[effort] = stat.n
        if step.api_errors:
            errors[effort] = step.api_errors
    if not points:
        return [], {}
    top = max(counts.values())
    n_tasks = top if all(n == top for n in counts.values()) else str(top) + "".join(f"; {e} {n}" for e, n in counts.items() if n != top)
    series = {"n_tasks": n_tasks, "dropped_unpaired_tasks": 0, "tasks_by_effort": counts, "runs_per_task_min": min(runs),
              "runs_per_task_max": max(runs), **({"api_error_rows_excluded": errors} if errors else {})}
    flags = pf.pareto_flags(points)
    points = [dataclasses.replace(p, on_pareto=f) for p, f in zip(points, flags)]
    meta = {"x_mode": "output", "resamples": a.n_boot, "seed": a.seed, "ci_level": pf.CI_LEVEL, "series": {SERIES: series}, "warnings": []}
    return points, meta


def write_chart(a: Analysis, out: Path) -> list[pf.Point]:
    points, meta = frontier_points(a)
    if not points:
        return []
    pf.write_json(out, points, meta)
    pf.plot(points, meta, CHART_TITLE, out)
    return points


# ------------------------------------------------------------ main


def merge_high_runs(rows: list[dict], repeat_paths: list[Path], har_mode: str | None, notes: list[str]) -> list[dict]:
    """Rows with the high-effort rows of run 1 and of every repeat CSV that exists joined as runs 1, 2, 3 (the per-task mean is taken later).

    A repeat that misses a task of run 1, or has an `api_error` row, raises `high_runs.RunMismatchError`. Missing repeat files are
    noted and skipped, so that a partly finished repeat set still gives the runs that exist.
    """
    found = [p for p in repeat_paths if Path(p).is_file()]
    notes += [f"high repeat file not found, skipped: {p}" for p in repeat_paths if not Path(p).is_file()]
    high = [r for r in rows if r["effort"] == HIGH]
    if not found or not high:
        return rows
    first = [r for r in high if str(r.get("run", "1")).strip() == "1"]
    runs = [first, *[[r for r in load_rows([p], har_mode) if r["effort"] == HIGH] for p in found]]
    try:
        merged = high_runs.merge_runs(runs)
    except high_runs.RunMismatchError as exc:
        raise high_runs.RunMismatchError(f"high repeat runs {[Path(p).name for p in found]}: {exc}") from exc
    notes.append(f"high effort: each task is the mean of {len(runs)} runs ({', '.join(Path(p).name for p in found)} are runs 2 onwards); low and medium: 1 run.")
    return [r for r in rows if r["effort"] != HIGH] + merged


def _gather_rows(results: list[Path], batch_root: Path, har_mode: str | None, notes: list[str],
                 high_repeats: list[Path] | None = None) -> list[dict]:
    present = [p for p in results if Path(p).is_file()]
    notes += [f"results file not found, skipped: {p}" for p in results if not Path(p).is_file()]
    rows = load_rows(present, har_mode) if present else []
    have = {r["effort"] for r in rows}
    extra = [Path(batch_root) / f"tierb50_{e}" / "results.csv" for e in EFFORTS if e not in have]
    extra = [p for p in extra if p.is_file()]
    rows = rows + (load_rows(extra, har_mode) if extra else [])
    return merge_high_runs(rows, high_repeats, har_mode, notes) if high_repeats else rows


def _load_tier_a(path: Path, har_mode: str | None, strict_map: Mapping, notes: list[str]) -> dict[str, Step]:
    if not Path(path).is_file():
        notes.append(f"Tier A results not found, baseline row skipped: {path}")
        return {}
    rows = load_rows([path], har_mode)
    return build_steps(rows, len({r["task"] for r in rows}), strict_map)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--results", nargs="+", type=Path, default=DEFAULT_RESULTS, help="results CSV(s) holding low/high (run 1)")
    p.add_argument("--high-runs", nargs="*", type=Path, default=DEFAULT_HIGH_RUNS,
                   help="repeat runs 2 and 3 of the as-built tasks at high (the per-task score at high is the mean over the runs found)")
    p.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT, help="dir with tierb50_<effort>/results.csv (read-only)")
    p.add_argument("--tasks", type=Path, default=DEFAULT_TASKS, help="dir with <task>/task.json (the 50 Tier B tasks)")
    p.add_argument("--tier-a", type=Path, default=DEFAULT_TIER_A)
    p.add_argument("--strict-json", nargs="*", type=Path, default=DEFAULT_STRICT_JSON, help="regrade JSONs that carry strict_pass_rate per cell")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report.md and summary.json go here")
    p.add_argument("--figure", type=Path, default=DEFAULT_FIGURE, help="chart path prefix (no extension)")
    p.add_argument("--har-mode", default="raw")
    p.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args(argv)

    if not args.tasks.is_dir():
        raise SystemExit(f"error: task dir not found: {args.tasks}")
    feats = atb.load_task_features(args.tasks)
    notes: list[str] = []
    rows = _gather_rows(args.results, args.batch_root, args.har_mode, notes, args.high_runs)
    unknown = sorted({r["task"] for r in rows} - set(feats))
    if unknown:
        notes.append(f"{len(unknown)} task(s) in results have no task.json and are left out: {unknown[:5]}")
    rows = [r for r in rows if r["task"] in feats]
    strict_map = load_strict_map(args.strict_json)
    steps = build_steps(rows, len(feats), strict_map)
    if not steps:
        raise SystemExit(f"error: no results found in {[str(r) for r in args.results]} or under {args.batch_root}/tierb50_<effort>/")
    tier_a = _load_tier_a(args.tier_a, args.har_mode, strict_map, notes)

    analysis = Analysis(steps, args.n_boot, args.seed)
    verdict = effort_verdict(analysis)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(render_report(analysis, feats, tier_a, verdict, notes), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(build_summary(analysis, feats, tier_a, verdict), indent=2) + "\n", encoding="utf-8")
    try:
        write_chart(analysis, args.figure)
    except ImportError as exc:  # matplotlib is an optional extra
        print(f"warning: chart skipped ({exc}); run with `uv run --extra plots`", file=sys.stderr)

    print(f"{len(steps)} of {len(EFFORTS)} step(s) -> {args.out / 'report.md'}")
    for effort in EFFORTS:
        s = analysis.steps.get(effort)
        if s is None:
            print(f"  {effort:7s} not run yet")
            continue
        m = step_summary(s, args.n_boot, args.seed)
        pr, tok = m["pass_rate"], m["mean_output_tokens"]
        pass_text = "n/a" if pr is None else f"{pr.mean:.3f} [{pr.lo:.3f}, {pr.hi:.3f}]"
        tok_text = "n/a" if tok is None else f"{tok.mean:,.0f}"
        print(f"  {s.label:22s} {analysis.state(effort)}: pass {pass_text} output {tok_text} api_error {s.api_errors}")
    print(f"  verdict: {verdict['status']} ({verdict['evidence']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
