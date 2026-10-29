"""Data layer for the story figures: load scored CSVs, compute the numbers. No plotting, no Beam API calls.

Low and medium use 1 run per task; high uses the per-task mean of 3 runs (PREREG.md deviation 2026-10-27; the repeats
`results/heal_v3_eval_<arm>_high_r{2,3}.csv` and `results/tierb50_high_r{2,3}.csv` are joined by `raw_step_rows`). Pass rate = mean over
tasks of the task's `pass_rate`. A task is "solved" when its pass rate is 1.0 (at high: in every one of its runs). 95% CIs are percentile bootstraps over tasks only, the same helper as the frontier charts.
"""

from __future__ import annotations

import csv
import json
import random
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze_heal_controls as ac  # noqa: E402
import analyze_heal_v3 as ah  # noqa: E402
import heal_v3_verdicts as hv  # noqa: E402
import high_runs  # noqa: E402
import plot_frontier as pf  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EFFORTS = ("low", "medium", "high")
CASE1, BASE, HINT = "case1", "base", "hint"
SERIES_NAME = {CASE1: "Case 1 Tier B", BASE: "Case 3 base", HINT: "Case 3 + hint"}
PLACEBO, NOTICE = "placebo", "notice"
DECOMP_ARMS = (BASE, PLACEBO, NOTICE, HINT)
DECOMP_EFFORT = "low"
SOLVED_EPS = 1e-9
SEG_SOLVED, SEG_ABOVE, SEG_BELOW, SEG_PARTIAL, SEG_ZERO = "solved", "partial_above_v1", "partial_at_or_below_v1", "partial", "zero"
CAPTION = "Low, medium: 1 run per task; high: mean of 3 runs per task; CI over tasks only."
CAPTION_ONE_RUN = "1 run per task; CI over tasks only."

Tasks = Mapping[str, hv.TaskScore]


class Skip(Exception):
    """A figure cannot be built yet (missing data). The message is printed."""


@dataclass(frozen=True)
class Dataset:
    steps: Mapping[tuple[str, str], Tasks]  # (series, effort) -> task -> score
    header_on: frozenset[str] = frozenset()  # case 1 tasks that carry the hidden header
    header_known: bool = False
    floor: Mapping[str, float] | None = None  # case 3 task -> unchanged-v1 pass rate
    floor_note: str = ""
    sources: Mapping[str, str] = field(default_factory=dict)
    results_dir: Path | None = None
    high_runs: int = 1  # runs per task behind every score at high (3 in the release)

    def tasks(self, series: str, effort: str) -> Tasks:
        if (series, effort) not in self.steps:
            raise Skip(f"missing data for {series} {effort}")
        return self.steps[series, effort]


# ------------------------------------------------------------ loading


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_floor(path: Path, tasks: set[str]) -> tuple[dict[str, float] | None, str]:
    """Per-task unchanged-v1 pass rate, or (None, reason) when the file is absent or does not cover every case 3 task."""
    if not Path(path).is_file():
        return None, f"{Path(path).name} not found: partial split into above/below v1 is not drawn"
    per_task = {r["task"]: float(r["v1_pass_rate"]) for r in json.loads(Path(path).read_text(encoding="utf-8")).get("per_task", [])}
    if tasks and not tasks <= set(per_task):
        return None, f"{Path(path).name} misses {len(tasks - set(per_task))} task(s): partial split is not drawn"
    return per_task, f"partial split by the unchanged-v1 score from {Path(path).name}"


def _with_repeats(results_dir: Path, stem: str, first: list[dict]) -> list[dict]:
    """High effort: run 1 rows and every `<stem>_r2.csv`, `_r3.csv` that exists, joined as runs 1, 2, 3 (a repeat that misses a task raises)."""
    runs = [first, *[[r for r in _read(p) if r.get("effort", "high") == "high"] for p in high_runs.extra_run_paths(results_dir, stem)]]
    return high_runs.merge_runs(runs) if len(runs) > 1 else first


def raw_step_rows(results_dir: Path) -> dict[tuple[str, str], list[dict]]:
    """Scored CSV rows per (series, effort): case 1 Tier B and the case 3 v3 eval, base and hint.

    Run 1 only at low and medium; at high run 1 plus the repeat runs r2, r3 when their CSVs exist (the step then averages per task).
    """
    results_dir, out = Path(results_dir), {}
    v1, medium = results_dir / "tierb50_v1.csv", results_dir / "tierb50_medium.csv"
    for effort, path in (("low", v1), ("medium", medium), ("high", v1)):
        if path.is_file():
            rows = [r for r in _read(path) if r.get("effort", effort) == effort and r.get("run", "1") == "1"]
            out[CASE1, effort] = _with_repeats(results_dir, "tierb50_high", rows) if effort == "high" else rows
    for arm in (BASE, HINT):
        for effort in EFFORTS:
            path = results_dir / f"heal_v3_eval_{arm}_{effort}.csv"
            if path.is_file():
                rows = [r for r in _read(path) if r.get("run", "1") == "1"]
                out[arm, effort] = _with_repeats(results_dir, f"heal_v3_eval_{arm}_high", rows) if effort == "high" else rows
    return out


def load_dataset(results_dir: Path, tasks_tierb: Path | None = None, floor_path: Path | None = None) -> Dataset:
    results_dir = Path(results_dir)
    rows = raw_step_rows(results_dir)
    steps = {k: ah.build_step(k[0], k[1], r, len({x["task"] for x in r})).scores for k, r in rows.items()}
    on, every = ac.hidden_header_tasks(tasks_tierb) if tasks_tierb else (set(), set())
    case3_tasks = {t for (s, _), v in steps.items() if s in (BASE, HINT) for t in v}
    floor, note = load_floor(floor_path or results_dir / "heal_v3_eval_v1_floor.json", case3_tasks)
    runs_at_high = min((max(r["run"] for r in rs) for (s, e), rs in rows.items() if e == "high" and rs), default="1")
    return Dataset(steps, frozenset(on), bool(every), floor, note, {f"{s}-{e}": "scored csv" for s, e in rows}, results_dir, int(runs_at_high))


# ------------------------------------------------------------ basic numbers


def pass_pct(tasks: Tasks) -> float:
    return 100.0 * hv.mean(s.pass_rate for s in tasks.values())


def tokens_per_task(tasks: Tasks) -> float:
    return hv.mean(s.tokens or 0.0 for s in tasks.values())


def is_solved(score: hv.TaskScore) -> bool:
    return score.pass_rate >= 1.0 - SOLVED_EPS


def pass_ci(tasks: Tasks, n_boot: int, seed: int, key: str) -> tuple[float, float]:
    """95% bootstrap CI (points) over tasks of the mean pass rate."""
    return pf.bootstrap_ci([[s.pass_rate] for s in tasks.values()], n_boot, seed, f"story:{key}")


def subset(tasks: Tasks, names: set[str] | frozenset[str], keep: bool) -> dict[str, hv.TaskScore]:
    return {t: s for t, s in tasks.items() if (t in names) == keep}


# ------------------------------------------------------------ F1, F5


def effort_curve(tasks_by_effort: Mapping[str, Tasks], n_boot: int, seed: int, key: str, ci: bool = True) -> list[dict]:
    out = []
    for effort, tasks in tasks_by_effort.items():
        lo, hi = pass_ci(tasks, n_boot, seed, f"{key}:{effort}") if ci else (None, None)
        out.append({"effort": effort, "n": len(tasks), "tokens": tokens_per_task(tasks), "pass": pass_pct(tasks), "ci_low": lo, "ci_high": hi})
    return out


def header_gap(ds: Dataset, n_boot: int, seed: int) -> list[dict]:
    """Case 1 per effort: pass rate with the hidden header off and on, CI each, gap = on minus off in points."""
    if not ds.header_known:
        raise Skip("no Tier B task.json files (hidden_header flag) found")
    rows = []
    for effort in EFFORTS:
        tasks = ds.tasks(CASE1, effort)
        groups = {"off": subset(tasks, ds.header_on, False), "on": subset(tasks, ds.header_on, True)}
        if not groups["off"] or not groups["on"]:
            raise Skip("hidden_header flag does not split the Tier B tasks into two groups")
        row = {"effort": effort}
        for name, g in groups.items():
            lo, hi = pass_ci(g, n_boot, seed, f"header:{name}:{effort}")
            row[name] = {"n": len(g), "pass": pass_pct(g), "ci_low": lo, "ci_high": hi}
        row["gap_points"] = row["on"]["pass"] - row["off"]["pass"]
        rows.append(row)
    return rows


# ------------------------------------------------------------ F2


def cost_per_solve(tasks: Tasks, n_boot: int, seed: int, key: str) -> dict:
    """Output tokens per fully solved task = (mean tokens x n tasks) / (number of tasks with pass rate 1.0).

    The CI redraws tasks with replacement and recomputes the ratio; a draw with no solved task has no ratio and is
    skipped (counted in `undefined_draws`).
    """
    pairs = [(s.tokens or 0.0, is_solved(s)) for s in tasks.values()]
    n, solved = len(pairs), sum(p for _, p in pairs)
    total = sum(t for t, _ in pairs)
    out = {"n": n, "solved": solved, "mean_tokens": total / n, "total_tokens": total,
           "value": total / solved if solved else None, "ci_low": None, "ci_high": None, "undefined_draws": 0}
    if not solved:
        return out
    rng = random.Random(f"story:{seed}:cost:{key}")
    draws = []
    for _ in range(n_boot):
        pick = [pairs[rng.randrange(n)] for _ in range(n)]
        k = sum(p for _, p in pick)
        if k:
            draws.append(sum(t for t, _ in pick) / k)
    out["undefined_draws"] = n_boot - len(draws)
    draws.sort()
    out["ci_low"], out["ci_high"] = draws[int(0.025 * len(draws))], draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    return out


# ------------------------------------------------------------ F3


def outcome_counts(tasks: Tasks, floor: Mapping[str, float] | None = None) -> dict:
    """Counts and shares of solved / partial / zero. With `floor`, partial is split by the task's unchanged-v1 pass rate."""
    names = (SEG_SOLVED, SEG_ABOVE, SEG_BELOW, SEG_ZERO) if floor is not None else (SEG_SOLVED, SEG_PARTIAL, SEG_ZERO)
    counts = dict.fromkeys(names, 0)
    for t, s in tasks.items():
        if is_solved(s):
            counts[SEG_SOLVED] += 1
        elif s.pass_rate <= SOLVED_EPS:
            counts[SEG_ZERO] += 1
        elif floor is None:
            counts[SEG_PARTIAL] += 1
        else:
            counts[SEG_ABOVE if s.pass_rate > floor[t] + SOLVED_EPS else SEG_BELOW] += 1
    n = len(tasks)
    return {"n": n, "counts": counts, "shares": {k: v / n for k, v in counts.items()}}


# ------------------------------------------------------------ F4


def family_matrix(ds: Dataset) -> dict:
    """Case 3 mean pass rate (%) per family (rows, FAMILY_ORDER) and (arm, effort) column (base low..high, hint low..high)."""
    cols = [(arm, e) for arm in (BASE, HINT) for e in EFFORTS]
    steps = {c: ds.tasks(*c) for c in cols}
    families = sorted({s.family for t in steps.values() for s in t.values()}, key=ah._family_key)
    n = {f: sum(1 for s in steps[cols[0]].values() if s.family == f) for f in families}
    matrix = [[100.0 * hv.mean(s.pass_rate for s in steps[c].values() if s.family == f) for c in cols] for f in families]
    return {"rows": [{"family": f, "n": n[f]} for f in families], "cols": [{"arm": a, "effort": e} for a, e in cols], "matrix": matrix}


# ------------------------------------------------------------ F6


def change_kind_credit(ds: Dataset, effort: str = "high") -> list[dict]:
    """Per change kind: mean credit (%) for base and hint, n tasks whose release holds the change. Sorted by base, high to low."""
    cred = {arm: ah.credit_by_change(hv.Step(arm, effort, 0, 0, 0, dict(ds.tasks(arm, effort)))) for arm in (BASE, HINT)}
    kinds = sorted(set(cred[BASE]) | set(cred[HINT]))
    rows = [{"change": k, "n_base": cred[BASE].get(k, {}).get("n_tasks", 0), "n_hint": cred[HINT].get(k, {}).get("n_tasks", 0),
             BASE: 100.0 * cred[BASE][k]["credit"] if k in cred[BASE] else None,
             HINT: 100.0 * cred[HINT][k]["credit"] if k in cred[HINT] else None} for k in kinds]
    return sorted(rows, key=lambda r: (-(r[BASE] if r[BASE] is not None else -1.0), r["change"]))


# ------------------------------------------------------------ F7


def load_decomposition(batch_root: Path, expected: int) -> dict[str, Tasks]:
    """base / placebo / notice / hint at low from the batch steps, read-only. Skip until every step has a row for every task."""
    steps, waiting = {}, []
    for arm in DECOMP_ARMS:
        results = Path(batch_root) / ac.CASE3_DIRS[arm] / "results.csv"
        step = ah.build_step(arm, DECOMP_EFFORT, _read(results), expected) if results.is_file() else None
        if step is None or step.finished < expected or not step.scores:
            waiting.append(arm)
        else:
            steps[arm] = step.scores
    if waiting:
        names = "/".join(a for a in waiting if a in (PLACEBO, NOTICE)) or "/".join(waiting)
        raise Skip(f"waiting for {names} runs")
    return steps


def decomposition(steps: Mapping[str, Tasks], n_boot: int, seed: int) -> dict:
    """Bars with CI per arm, and the paired gain (points, bootstrap CI over tasks) between neighbouring bars."""
    bars = {a: {"n": len(steps[a]), "pass": pass_pct(steps[a]), **dict(zip(("ci_low", "ci_high"), pass_ci(steps[a], n_boot, seed, f"decomp:{a}")))}
            for a in DECOMP_ARMS}
    gains = []
    for lo_arm, hi_arm in zip(DECOMP_ARMS, DECOMP_ARMS[1:]):
        common = sorted(set(steps[lo_arm]) & set(steps[hi_arm]))
        est = hv.bootstrap_mean([100.0 * (steps[hi_arm][t].pass_rate - steps[lo_arm][t].pass_rate) for t in common], n_boot, seed, f"decomp:{lo_arm}:{hi_arm}")
        gains.append({"from": lo_arm, "to": hi_arm, "n": len(common), "gain_points": est.mean, "ci_low": est.lo, "ci_high": est.hi})
    return {"effort": DECOMP_EFFORT, "bars": bars, "gains": gains}


# ------------------------------------------------------------ F8, F9, F10


def hint_vs_effort(ds: Dataset, n_boot: int, seed: int) -> dict:
    """Case 3 base and hint at each effort (mean, CI), the paired hint minus base gain, and the base-high reference level."""
    out = {"efforts": {}}
    for e in EFFORTS:
        base, hint = ds.tasks(BASE, e), ds.tasks(HINT, e)
        common = sorted(set(base) & set(hint))
        gain = hv.bootstrap_mean([100.0 * (hint[t].pass_rate - base[t].pass_rate) for t in common], n_boot, seed, f"f8:gain:{e}")
        arms = {}
        for arm, tasks in ((BASE, base), (HINT, hint)):
            lo, hi = pass_ci(tasks, n_boot, seed, f"f8:{arm}:{e}")
            arms[arm] = {"n": len(tasks), "pass": pass_pct(tasks), "ci_low": lo, "ci_high": hi, "tokens": tokens_per_task(tasks)}
        out["efforts"][e] = {**arms, "gain": {"n": len(common), "points": gain.mean, "ci_low": gain.lo, "ci_high": gain.hi}}
    ref, hl = out["efforts"]["high"][BASE], out["efforts"]["low"][HINT]
    out["base_high_pass"] = ref["pass"]
    out["hint_low_beats_base_high"] = hl["ci_low"] > ref["ci_high"]
    out["token_ratio_base_high_over_hint_low"] = ref["tokens"] / hl["tokens"] if hl["tokens"] else None
    return out


def _fnum(value: object) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except ValueError:
        return None


def token_split(rows: list[dict]) -> dict:
    """Mean per task of reasoning and answer (completion minus reasoning) output tokens.

    A task's value is the mean over its rows that hold both numbers (1 run per task). Rows that miss either column are
    counted in `rows_missing_reasoning` and left out; a task with no usable row is left out of the means (`tasks_without_split`).
    `rows_reasoning_over_completion` counts rows whose reasoning exceeds the completion tokens (should be 0).
    """
    by_task: dict[str, list[tuple[float, float]]] = {}
    missing = over = 0
    tasks = set()
    for r in rows:
        if r.get("status") == ah.API_ERROR:
            continue
        tasks.add(r["task"])
        comp, reas = _fnum(r.get("completion_tokens")), _fnum(r.get("reasoning_tokens"))
        if comp is None or reas is None:
            missing += 1
            continue
        over += reas > comp + 1e-9
        by_task.setdefault(r["task"], []).append((comp, reas))
    per_task = [(hv.mean(c for c, _ in v), hv.mean(x for _, x in v)) for v in by_task.values()]
    n = len(per_task)
    comp = hv.mean(c for c, _ in per_task) if n else None
    reas = hv.mean(x for _, x in per_task) if n else None
    return {"n_tasks": len(tasks), "n_with_split": n, "tasks_without_split": len(tasks) - n, "rows_missing_reasoning": missing,
            "rows_reasoning_over_completion": over, "completion": comp, "reasoning": reas,
            "answer": None if comp is None else comp - reas, "reasoning_share": None if not comp else reas / comp}


def tokens_split_all(ds: Dataset) -> dict:
    if ds.results_dir is None:
        raise Skip("no results dir for the token split")
    rows = raw_step_rows(ds.results_dir)
    cells = {s: {e: token_split(rows[s, e]) for e in EFFORTS if (s, e) in rows} for s in (CASE1, BASE, HINT)}
    if not all(len(v) == len(EFFORTS) for v in cells.values()):
        raise Skip("missing steps for the token split")
    return cells


def flip_counts(first: Tasks, second: Tasks) -> dict:
    """Tasks that went not-full to full (gains) and full to not-full (losses) between two steps, over the tasks in both."""
    common = sorted(set(first) & set(second))
    gains = sum(1 for t in common if not is_solved(first[t]) and is_solved(second[t]))
    losses = sum(1 for t in common if is_solved(first[t]) and not is_solved(second[t]))
    return {"n": len(common), "gains": gains, "losses": losses, "net": gains - losses,
            "solved_before": sum(is_solved(first[t]) for t in common), "solved_after": sum(is_solved(second[t]) for t in common)}


def flips_all(ds: Dataset) -> list[dict]:
    out = []
    for s in (CASE1, BASE, HINT):
        for a, b in zip(EFFORTS, EFFORTS[1:]):
            out.append({"series": s, "step": f"{a} to {b}", **flip_counts(ds.tasks(s, a), ds.tasks(s, b))})
    return out
