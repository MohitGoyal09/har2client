#!/usr/bin/env python3
"""Is the HAR2Client reward a usable RL signal? Five checks on the per-task rewards that already exist (no model call).

Usage:
  uv run --extra plots python scripts/analyze_learnability.py [--n-boot 10000] [--no-figure]

The reward of a rollout is the share of hidden tests it passes, in [0, 1] (the `pass_rate` column of the scored CSVs).
The data are the 7 settings (Beam, GPT-OSS-120B at low / medium / high effort, Qwen3.5-397B thinking on) on 5 benchmarks
(tierb50, probe0, probe5, v3base, v3hint), loaded with the loaders of scripts/analyze_models.py (`load_beam`, `load_models`),
the same code that writes results/analysis/model_comparison/summary.json. The script checks that each pass rate it rebuilds
equals that leaderboard. Beam high has 3 runs per task on tierb50, probe5, v3base and v3hint: a task's score for orderings,
win rates and the none / some / all split is the mean of its runs (the leaderboard number); the reward histogram and the
variance check use every run as its own rollout. Every other setting has 1 run.

Metrics: (1) dense reward, (2) separation of policies, (3) not saturated and not impossible, (4) within-policy variance
(GRPO), (5) headroom. Intervals are 95% bootstrap CIs over tasks (scripts/heal_v3_verdicts.bootstrap_mean).

Writes results/analysis/learnability/{summary.json,report.md} and results/figures/final_learnability.{png,svg,json}.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_models as am  # noqa: E402
import heal_v3_verdicts as hv  # noqa: E402
import high_runs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results"
DEFAULT_OUT = ROOT / "results" / "analysis" / "learnability"
DEFAULT_FIG_DIR = ROOT / "results" / "figures"
MODELS_FILE = ROOT / "data" / "comparison_models.json"
SUMMARY_COMPARISON = Path("analysis") / "model_comparison" / "summary.json"
FIGURE_NAME = "final_learnability"

BENCHMARKS = (("tierb50", "Case 1: build"), ("probe0", "Case 2: no probes"), ("probe5", "Case 2: 5 probes"),
              ("v3base", "Case 3: repair, no hint"), ("v3hint", "Case 3: repair, with hint"))
SUITES = tuple(s for s, _ in BENCHMARKS)
BENCH_LABEL = dict(BENCHMARKS)
# (key, model, effort) in display order. Effort rank: low < medium < high within a model.
SETTINGS = (("beam-low", "beam", "low"), ("beam-medium", "beam", "medium"), ("beam-high", "beam", "high"),
            ("gpt-oss-120b-low", "gpt", "low"), ("gpt-oss-120b-medium", "gpt", "medium"), ("gpt-oss-120b-high", "gpt", "high"),
            ("qwen3.5-397b-thinking", "qwen", "thinking"))
KEYS = tuple(k for k, _, _ in SETTINGS)
SETTING_LABEL = {"beam-low": "Beam low", "beam-medium": "Beam medium", "beam-high": "Beam high",
                 "gpt-oss-120b-low": "GPT-OSS low", "gpt-oss-120b-medium": "GPT-OSS medium", "gpt-oss-120b-high": "GPT-OSS high",
                 "qwen3.5-397b-thinking": "Qwen3.5 thinking"}
EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}
MODEL_NAME = {"beam": "Beam", "gpt": "GPT-OSS-120B"}
# Pairs for win rates: (label, A, B).
PAIRS = (("Beam high vs Beam low", "beam-high", "beam-low"), ("GPT-OSS high vs GPT-OSS low", "gpt-oss-120b-high", "gpt-oss-120b-low"),
         ("Beam high vs Qwen3.5", "beam-high", "qwen3.5-397b-thinking"))
# Reward bins: exactly 0, four partial-credit bins, exactly 1.
BIN_LABELS = ("0", "(0, 0.25]", "(0.25, 0.5]", "(0.5, 0.75]", "(0.75, 1)", "1")
EPS = 1e-9
MEAN_TOLERANCE = 1e-6  # points: rebuilt pass rate vs the leaderboard summary
PCT = 100.0


# ---- loading ---------------------------------------------------------------


def entry_rows(entry: am.Entry, results_dir: Path) -> list[dict]:
    """Scored (not api_error) rows behind an entry, runs joined the way `load_beam` joins them (same as release/static_space/per_task.py)."""
    if entry.kind == "beam":
        arm = am.CASE2_ARMS.get(entry.suite)
        rows = am._read_rows(am.beam_sources(results_dir)[(entry.suite, entry.effort)], entry.effort, arm)
        repeats = am.beam_repeats(results_dir, entry.suite, entry.effort)
        if repeats:
            rows = high_runs.merge_runs([rows, *(r for p in repeats for r in am.read_repeat_runs(p, entry.effort, arm))])
    else:
        rows = am._read_rows(results_dir / "model_comparison" / entry.key / entry.suite / "results.csv", None, None)
    return [r for r in rows if r.get("status") != am.API_ERROR]


def load_rollouts(results_dir: Path) -> dict[tuple[str, str], dict[str, list[float]]]:
    """{(setting key, suite): {task: [reward of each run]}} for the 7 settings x 5 benchmarks.

    Checks that every task-mean equals the entry's score (the leaderboard number) and that all settings of a suite share one task set.
    """
    entries = am.load_beam(results_dir) + am.load_models(results_dir / "model_comparison", MODELS_FILE)
    by = {(e.key, e.suite): e for e in entries}
    out: dict[tuple[str, str], dict[str, list[float]]] = {}
    for suite in SUITES:
        for key in KEYS:
            entry = by.get((key, suite))
            if entry is None:
                raise SystemExit(f"error: no scored entry for {key} / {suite} (analyze_models loaders)")
            runs: dict[str, list[tuple[int, float]]] = {}
            for row in entry_rows(entry, results_dir):
                runs.setdefault(row["task"], []).append((int(row.get("run") or 1), am._num(row.get("pass_rate")) or 0.0))
            cells = {t: [v for _, v in sorted(rs)] for t, rs in runs.items()}
            if set(cells) != set(entry.scores) or any(abs(sum(v) / len(v) - entry.scores[t]) > EPS for t, v in cells.items()):
                raise SystemExit(f"error: per-run rewards of {key} / {suite} do not average to the loader scores")
            if len({len(v) for v in cells.values()}) != 1 or len(next(iter(cells.values()))) != entry.runs:
                raise SystemExit(f"error: {key} / {suite}: runs per task differ from the entry's {entry.runs}")
            out[(key, suite)] = cells
        if len({frozenset(out[(k, suite)]) for k in KEYS}) != 1:
            raise SystemExit(f"error: the settings of {suite} do not share one task set")
    return out


def check_leaderboard(rollouts: dict, results_dir: Path) -> int:
    """Rebuilt pass rates must equal the leaderboard summary (model_comparison). Returns the number of cells checked."""
    doc = json.loads((results_dir / SUMMARY_COMPARISON).read_text(encoding="utf-8"))
    want = {(r["key"], r["suite"]): r["pass_rate"] for r in doc["rows"]}
    checked = 0
    for (key, suite), cells in rollouts.items():
        mean = PCT * sum(sum(v) / len(v) for v in cells.values()) / len(cells)
        if abs(mean - want[(key, suite)]) > MEAN_TOLERANCE:
            raise SystemExit(f"error: {key} / {suite}: rebuilt pass rate {mean:.4f} vs leaderboard {want[(key, suite)]:.4f}")
        checked += 1
    return checked


# ---- helpers ---------------------------------------------------------------


def task_means(rollouts: dict, key: str, suite: str, tasks: list[str]) -> list[float]:
    cells = rollouts[(key, suite)]
    return [sum(cells[t]) / len(cells[t]) for t in tasks]


def boot(values: list[float], n_boot: int, seed: int, label: str) -> dict:
    """Mean of `values` (already in the unit to report) with a 95% bootstrap CI over tasks."""
    est = hv.bootstrap_mean(values, n_boot, seed, f"learnability:{label}")
    return {"n": est.n, "value": est.mean, "ci_low": est.lo, "ci_high": est.hi}


def share(flags: list[bool], n_boot: int, seed: int, label: str) -> dict:
    return boot([PCT * float(f) for f in flags], n_boot, seed, label)


def avg_ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=xs.__getitem__)
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    rx, ry = avg_ranks(x), avg_ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def strata_spearman(strata: list[list[list[float]]]) -> float | None:
    """Spearman of effort rank vs score. `strata` = one list of task rows per benchmark; a row = scores at low, medium, high."""
    x, y = [], []
    for rows in strata:
        for row in rows:
            x += [0, 1, 2]
            y += [round(v, 9) for v in row]
    return spearman(x, y)


def spearman_boot(strata: list[list[list[float]]], n_boot: int, seed: int, label: str) -> dict:
    """Spearman with a 95% CI from resampling tasks inside each benchmark."""
    rng = random.Random(f"{seed}:learnability:{label}")
    draws = []
    for _ in range(n_boot):
        resampled = [[rows[rng.randrange(len(rows))] for _ in rows] for rows in strata]
        if (r := strata_spearman(resampled)) is not None:
            draws.append(r)
    lo, hi = hv._percentile_ci(draws) if draws else (None, None)
    return {"n": sum(len(s) for s in strata), "value": strata_spearman(strata), "ci_low": lo, "ci_high": hi}


def reward_bin(v: float) -> int:
    if v <= EPS:
        return 0
    if v >= 1 - EPS:
        return len(BIN_LABELS) - 1
    return min(1 + int(v / 0.25), 4)


def pstdev_sample(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# ---- the five metrics ------------------------------------------------------


def tasks_of(rollouts: dict, suite: str) -> list[str]:
    return sorted(rollouts[(KEYS[0], suite)])


def metric_dense(rollouts: dict, n_boot: int, seed: int) -> dict:
    out = {}
    for suite in SUITES:
        tasks = tasks_of(rollouts, suite)
        per_task_mid, per_task_bins = [], []
        for t in tasks:
            vals = [v for k in KEYS for v in rollouts[(k, suite)][t]]
            bins = [0] * len(BIN_LABELS)
            for v in vals:
                bins[reward_bin(v)] += 1
            per_task_bins.append([b / len(vals) for b in bins])
            per_task_mid.append(PCT * sum(0 < reward_bin(v) < len(BIN_LABELS) - 1 for v in vals) / len(vals))
        rollout_count = sum(len(rollouts[(k, suite)][t]) for k in KEYS for t in tasks)
        out[suite] = {
            "tasks": len(tasks), "rollouts": rollout_count,
            "partial_credit_share": boot(per_task_mid, n_boot, seed, f"dense:{suite}"),
            "histogram_pct": {lab: PCT * sum(r[i] for r in per_task_bins) / len(tasks) for i, lab in enumerate(BIN_LABELS)},
        }
    return out


def metric_separation(rollouts: dict, n_boot: int, seed: int) -> dict:
    ordering, pairwise = {}, {}
    for suite in SUITES:
        tasks = tasks_of(rollouts, suite)
        rates = {k: boot([PCT * v for v in task_means(rollouts, k, suite, tasks)], n_boot, seed, f"rate:{suite}:{k}") for k in KEYS}
        order = sorted(KEYS, key=lambda k: -rates[k]["value"])
        monotone = {m: all(rates[f"{slug}-{a}"]["value"] <= rates[f"{slug}-{b}"]["value"] + EPS
                           for a, b in (("low", "medium"), ("medium", "high")))
                    for m, slug in (("beam", "beam"), ("gpt", "gpt-oss-120b"))}
        ordering[suite] = {"n": len(tasks), "pass_rate": rates, "order_best_to_worst": order, "effort_monotone": monotone}
        pairwise[suite] = {}
        for label, a, b in PAIRS:
            sa, sb = task_means(rollouts, a, suite, tasks), task_means(rollouts, b, suite, tasks)
            wins = [x > y + EPS for x, y in zip(sa, sb)]
            losses = [y > x + EPS for x, y in zip(sa, sb)]
            ties = [not w and not l for w, l in zip(wins, losses)]
            decided = sum(wins) + sum(losses)
            pairwise[suite][label] = {
                "n": len(tasks), "win": share(wins, n_boot, seed, f"win:{suite}:{label}"),
                "loss": share(losses, n_boot, seed, f"loss:{suite}:{label}"), "tie_pct": PCT * sum(ties) / len(tasks),
                "win_share_of_decided_pct": PCT * sum(wins) / decided if decided else None}
    spear = {}
    for slug, model in (("beam", "beam"), ("gpt-oss-120b", "gpt")):
        by_suite = {}
        for suite in SUITES:
            tasks = tasks_of(rollouts, suite)
            cols = [task_means(rollouts, f"{slug}-{e}", suite, tasks) for e in ("low", "medium", "high")]
            by_suite[suite] = [list(r) for r in zip(*cols)]
        spear[model] = {"per_benchmark": {s: spearman_boot([by_suite[s]], n_boot, seed, f"spearman:{model}:{s}") for s in SUITES},
                        "pooled": spearman_boot([by_suite[s] for s in SUITES], n_boot, seed, f"spearman:{model}:pooled")}
    return {"ordering": ordering, "pairwise": pairwise, "spearman_effort_vs_reward": spear}


def metric_middle(rollouts: dict, n_boot: int, seed: int) -> dict:
    out = {}
    for suite in SUITES:
        tasks = tasks_of(rollouts, suite)
        means = [task_means(rollouts, k, suite, tasks) for k in KEYS]
        none = [all(m[i] <= EPS for m in means) for i in range(len(tasks))]
        every = [all(m[i] >= 1 - EPS for m in means) for i in range(len(tasks))]
        middle = [not a and not b for a, b in zip(none, every)]
        out[suite] = {"n": len(tasks), "solved_by_no_setting": share(none, n_boot, seed, f"none:{suite}"),
                      "solved_by_every_setting": share(every, n_boot, seed, f"all:{suite}"),
                      "middle": share(middle, n_boot, seed, f"middle:{suite}"),
                      "none_count": sum(none), "all_count": sum(every), "middle_count": sum(middle)}
    return out


def metric_variance(rollouts: dict, n_boot: int, seed: int) -> dict:
    out, pooled_flags, pooled_std = {}, [], []
    for suite in SUITES:
        cells = rollouts[("beam-high", suite)]
        tasks = sorted(cells)
        if len(cells[tasks[0]]) < 2:
            out[suite] = {"runs_per_task": 1, "note": "1 run per task: no within-task variance"}
            continue
        disagree = [max(cells[t]) - min(cells[t]) > EPS for t in tasks]
        stds = [pstdev_sample(cells[t]) for t in tasks]
        agree_zero = [not d and max(cells[t]) <= EPS for d, t in zip(disagree, tasks)]
        agree_one = [not d and min(cells[t]) >= 1 - EPS for d, t in zip(disagree, tasks)]
        pooled_flags += disagree
        pooled_std += stds
        with_signal = [s for s, d in zip(stds, disagree) if d]
        out[suite] = {"runs_per_task": len(cells[tasks[0]]), "n": len(tasks),
                      "groups_with_signal": share(disagree, n_boot, seed, f"signal:{suite}"),
                      "mean_within_task_std": boot(stds, n_boot, seed, f"std:{suite}"),
                      "mean_std_where_runs_differ": sum(with_signal) / len(with_signal) if with_signal else None,
                      "all_runs_zero_count": sum(agree_zero), "all_runs_one_count": sum(agree_one),
                      "all_runs_equal_other_count": len(tasks) - sum(disagree) - sum(agree_zero) - sum(agree_one)}
    out["pooled"] = {"n": len(pooled_flags), "groups_with_signal": share(pooled_flags, n_boot, seed, "signal:pooled"),
                     "mean_within_task_std": boot(pooled_std, n_boot, seed, "std:pooled")}
    return out


def metric_headroom(summary_sep: dict, rollouts: dict) -> dict:
    out = {}
    for suite in SUITES:
        rates = summary_sep["ordering"][suite]["pass_rate"]
        best = summary_sep["ordering"][suite]["order_best_to_worst"][0]
        tasks = tasks_of(rollouts, suite)
        solved = sum(v >= 1 - EPS for v in task_means(rollouts, best, suite, tasks))
        out[suite] = {"best_setting": best, "best_label": SETTING_LABEL[best], "best_pass_rate": rates[best]["value"],
                      "ci_low": rates[best]["ci_low"], "ci_high": rates[best]["ci_high"],
                      "gap_to_100": PCT - rates[best]["value"], "best_fully_solves_pct": PCT * solved / len(tasks), "n": len(tasks)}
    return out


def build_summary(rollouts: dict, n_boot: int, seed: int, checked: int) -> dict:
    separation = metric_separation(rollouts, n_boot, seed)
    return {
        "n_boot": n_boot, "seed": seed, "leaderboard_cells_checked": checked,
        "benchmarks": {s: {"label": BENCH_LABEL[s], "n_tasks": len(tasks_of(rollouts, s))} for s in SUITES},
        "settings": {k: SETTING_LABEL[k] for k in KEYS},
        "runs_note": "Beam high has 3 runs per task on tierb50, probe5, v3base, v3hint; every other cell is 1 run.",
        "dense_reward": metric_dense(rollouts, n_boot, seed), "separation": separation,
        "saturation": metric_middle(rollouts, n_boot, seed), "within_policy_variance": metric_variance(rollouts, n_boot, seed),
        "headroom": metric_headroom(separation, rollouts),
    }


# ---- report ----------------------------------------------------------------


def f1(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}"


def with_ci(d: dict, unit: str = "%") -> str:
    return f"{d['value']:.1f}{unit} [{d['ci_low']:.1f}, {d['ci_high']:.1f}]"


def sp(d: dict) -> str:
    return "n/a" if d["value"] is None else f"{d['value']:.2f} [{d['ci_low']:.2f}, {d['ci_high']:.2f}]"


def table(head: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|", *("| " + " | ".join(r) + " |" for r in rows), ""]


def render_report(s: dict) -> str:
    n = {k: v["n_tasks"] for k, v in s["benchmarks"].items()}
    L = ["# Is the reward learnable?", "",
         "This report checks, from evaluation data that already exists, whether the HAR2Client reward can drive RL. "
         "No model was called and nothing was trained.", "",
         "The reward of one rollout is the share of hidden tests it passes, from 0 to 1. "
         f"The data are 7 settings (Beam low, medium, high; GPT-OSS-120B low, medium, high; Qwen3.5-397B thinking on) on 5 benchmarks "
         f"(n = {', '.join(f'{n[k]}' for k in SUITES)} tasks, in the order below). "
         "Beam high has 3 runs per task on Case 1, Case 2 with 5 probes and both Case 3 arms. Every other cell has 1 run. "
         "Intervals in square brackets are 95% bootstrap CIs over tasks.", "",
         f"Rebuilt pass rates match the leaderboard (`results/analysis/model_comparison/summary.json`) in all {s['leaderboard_cells_checked']} checked cells.", ""]

    L += ["## 1. Dense reward", "",
          "Share of rollouts with a reward strictly between 0 and 1. A reward that is mostly 0 or 1 is sparse. Partial credit gives RL a gradient. "
          "Each run is one rollout, so Beam high counts 3 times per task.", ""]
    d = s["dense_reward"]
    L += table(["Benchmark", "Tasks", "Rollouts", "Partial credit (0 < r < 1)"],
               [[BENCH_LABEL[k], str(d[k]["tasks"]), str(d[k]["rollouts"]), with_ci(d[k]["partial_credit_share"])] for k in SUITES])
    L += ["Reward histogram, share of rollouts per bin (%):", ""]
    L += table(["Benchmark", *BIN_LABELS], [[BENCH_LABEL[k], *(f1(d[k]["histogram_pct"][b]) for b in BIN_LABELS)] for k in SUITES])

    sep = s["separation"]
    L += ["## 2. The reward separates policies", "",
          "Pass rate per setting, best to worst. A setting that thinks harder should score higher.", ""]
    for suite in SUITES:
        o = sep["ordering"][suite]
        L += [f"**{BENCH_LABEL[suite]}** (n = {o['n']}): " + " > ".join(f"{SETTING_LABEL[k]} {o['pass_rate'][k]['value']:.1f}" for k in o["order_best_to_worst"]), ""]
    mono_total = len(SUITES) * 2
    mono_count = sum(sep["ordering"][k]["effort_monotone"][mm] for k in SUITES for mm in ("beam", "gpt"))
    L += [f"Pass rate is monotone in effort (low <= medium <= high) in {mono_count} of {mono_total} model-benchmark curves. "
          "The exceptions are listed in the orderings above.", ""]
    L += ["Spearman rank correlation between effort (low < medium < high) and task reward, within one model:", ""]
    spear = sep["spearman_effort_vs_reward"]
    L += table(["Benchmark", "Beam", "GPT-OSS-120B"],
               [[BENCH_LABEL[k], sp(spear["beam"]["per_benchmark"][k]), sp(spear["gpt"]["per_benchmark"][k])] for k in SUITES]
               + [["All 5 pooled", sp(spear["beam"]["pooled"]), sp(spear["gpt"]["pooled"])]])
    L += ["The pooled CI resamples tasks inside each benchmark. Case 1 and Case 2 share tasks, and so do the two Case 3 arms, so the pooled interval is a little too narrow.", "",
          "Pairwise win rate: share of tasks where setting A scores strictly higher than B. The rest are ties.", ""]
    rows = []
    for label, _, _ in PAIRS:
        for suite in SUITES:
            p = sep["pairwise"][suite][label]
            rows.append([label, BENCH_LABEL[suite], with_ci(p["win"]), with_ci(p["loss"]), f1(p["tie_pct"]) + "%",
                         "n/a" if p["win_share_of_decided_pct"] is None else f"{p['win_share_of_decided_pct']:.0f}%"])
    L += table(["Pair (A vs B)", "Benchmark", "A wins", "B wins", "Ties", "A share of decided"], rows)

    m = s["saturation"]
    L += ["## 3. Not saturated, not impossible", "",
          "Share of tasks that no setting solves (all 7 score 0), that every setting solves (all 7 score 1), and the middle. "
          "The middle is where a policy can still learn: some settings pass and some do not.", ""]
    L += table(["Benchmark", "Tasks", "Solved by none", "Solved by all", "Middle (useful for learning)"],
               [[BENCH_LABEL[k], str(m[k]["n"]), f"{m[k]['solved_by_no_setting']['value']:.1f}% ({m[k]['none_count']})",
                 f"{m[k]['solved_by_every_setting']['value']:.1f}% ({m[k]['all_count']})", with_ci(m[k]["middle"]) + f" ({m[k]['middle_count']})"]
                for k in SUITES])

    v = s["within_policy_variance"]
    L += ["## 4. Within-policy variance (what GRPO needs)", "",
          "GRPO compares samples drawn for the same task. If all samples get the same reward, the advantage is zero and that group teaches nothing. "
          "Beam high has 3 runs per task, so the share of tasks where the 3 runs differ is a proxy for groups with signal. "
          "Std is the sample standard deviation over the 3 runs. Case 2 with no probes has 1 run, so it is left out.", ""]
    rows = [[BENCH_LABEL[k], str(v[k]["n"]), with_ci(v[k]["groups_with_signal"]), f"{v[k]['mean_within_task_std']['value']:.3f}",
             f"{v[k]['all_runs_zero_count']} / {v[k]['all_runs_one_count']} / {v[k]['all_runs_equal_other_count']}"]
            for k in SUITES if "groups_with_signal" in v[k]]
    p = v["pooled"]
    rows.append(["All 4 pooled", str(p["n"]), with_ci(p["groups_with_signal"]), f"{p['mean_within_task_std']['value']:.3f}", ""])
    L += table(["Benchmark", "Tasks", "Runs differ (groups with signal)", "Mean within-task std", "No signal: all 0 / all 1 / same partial"], rows)

    h = s["headroom"]
    L += ["## 5. Headroom", "", "Pass rate of the best setting on each benchmark. The gap to 100% is room left to learn.", ""]
    L += table(["Benchmark", "Best setting", "Pass rate", "Gap to 100", "Tasks it fully solves"],
               [[BENCH_LABEL[k], h[k]["best_label"], with_ci({"value": h[k]["best_pass_rate"], "ci_low": h[k]["ci_low"], "ci_high": h[k]["ci_high"]}),
                 f"{h[k]['gap_to_100']:.1f}", f"{h[k]['best_fully_solves_pct']:.1f}%"] for k in SUITES])

    mids = [m[k]["middle"]["value"] for k in SUITES]
    parts = {k: d[k]["partial_credit_share"]["value"] for k in SUITES}
    L += ["## What this means for RL", "",
          f"- Graded reward: partial credit is {min(parts.values()):.0f}% to {max(parts.values()):.0f}% of rollouts. It is high on Case 3 repair and low ({', '.join(f'{parts[k]:.0f}%' for k in SUITES if parts[k] < 20)}) on Case 1 and Case 2, which are mostly 0 or 1.",
          f"- Separation: harder thinking helps, but the rank correlation is modest (pooled Spearman {spear['beam']['pooled']['value']:.2f} for Beam, {spear['gpt']['pooled']['value']:.2f} for GPT-OSS) and effort is monotone in {mono_count} of {mono_total} model-benchmark curves.",
          f"- Useful middle: {min(mids):.0f}% to {max(mids):.0f}% of tasks are solved by some settings and not others, and the best setting is {min(h[k]['gap_to_100'] for k in SUITES):.0f} to {max(h[k]['gap_to_100'] for k in SUITES):.0f} points below 100%.",
          f"- GRPO groups: Beam high's 3 runs differ on {p['groups_with_signal']['value']:.0f}% of tasks ({v['pooled']['groups_with_signal']['ci_low']:.0f}% to {v['pooled']['groups_with_signal']['ci_high']:.0f}%). The other tasks would give a zero advantage with 3 samples.",
          "",
          "Scope: this is evidence of a usable signal from evaluation data. It is not a training run, and it does not show that a policy improves when trained on this reward.", ""]
    return "\n".join(L)


# ---- figure ----------------------------------------------------------------


def draw_figure(s: dict, fig_dir: Path) -> str:
    from matplotlib.patches import Patch

    import plot_final as plf  # imported late: it needs matplotlib (the `plots` extra)

    plt = plf.setup()
    ink, ink2, surface, grid, slate = plf.INK, plf.INK_2, plf.SURFACE, plf.GRID, plf.SLATE
    beam, gpt = plf.MODEL_COLOR["beam"], plf.MODEL_COLOR["gpt"]
    plf.SOURCE_NOTE = "Data: results/analysis/learnability/summary.json (per-task pass rates behind the model_comparison leaderboard)."
    n = [s["benchmarks"][k]["n_tasks"] for k in SUITES]
    title = "The reward is graded, separates strong from weak settings, and leaves headroom"
    subtitle = (f"7 settings (Beam, GPT-OSS-120B at low / medium / high effort, Qwen3.5-397B) on 5 benchmarks, n = {n[0]}, {n[1]}, {n[2]}, {n[3]}, {n[4]} tasks; "
                "reward = share of hidden tests passed")
    note = ("Beam high = 3 runs per task on Case 1, Case 2 with 5 probes and Case 3 (both arms); every other setting = 1 run. "
            f"(a) every run is a rollout, {sum(s['dense_reward'][k]['rollouts'] for k in SUITES)} in all. "
            f"95% CI = bootstrap over tasks ({s['n_boot']:,} resamples).")
    fig, top, bottom = plf.frame(11, 8.4, title, subtitle, note, bottom_in=0.75)
    gs = fig.add_gridspec(2, 2, left=0.15, right=0.975, top=top, bottom=bottom, hspace=0.62, wspace=0.42)
    ax_a, ax_b, ax_c, ax_d = (fig.add_subplot(gs[i // 2, i % 2]) for i in range(4))
    names = [BENCH_LABEL[k].replace(": ", ":\n") for k in SUITES]
    ypos = list(range(len(SUITES)))
    data: dict = {"title": title}

    def stacked(ax, parts, colors, labels, x_label, key):
        plf.style_axes(ax, "x")
        out = []
        for i, suite in enumerate(SUITES):
            left, row = 0.0, {}
            for (name, vals), color in zip(parts, colors):
                v = vals[suite]
                ax.barh(i, v, left=left, height=0.62, color=color, edgecolor=surface, linewidth=1.5, zorder=3)
                if v >= 7:
                    ax.text(left + v / 2, i, f"{v:.0f}", ha="center", va="center", fontsize=8.5, color=plf.text_on(color), zorder=4)
                row[name] = v
                left += v
            out.append({"suite": suite, **row})
        ax.set_xlim(0, 100)
        ax.set_ylim(len(SUITES) - 0.4, -0.6)
        ax.set_yticks(ypos, names, fontsize=8.5, color=ink)
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel(x_label, fontsize=9, color=ink2)
        ax.legend(handles=[Patch(color=c, label=l) for c, l in zip(colors, labels)], loc="upper center", bbox_to_anchor=(0.45, -0.2),
                  ncol=3, frameon=False, fontsize=8, labelcolor=ink, handlelength=1.1, columnspacing=1.0)
        return out

    # (a) reward histogram
    hist = s["dense_reward"]
    partial = [hist[k]["partial_credit_share"]["value"] for k in SUITES]
    lo_partial, hi_partial = min(partial), max(partial)
    parts = [(lab, {k: hist[k]["histogram_pct"][lab] for k in SUITES}) for lab in BIN_LABELS]
    colors = [slate[1], "#c4daf4", "#8dbaea", "#4f93de", "#2a78d6", "#0a2d5c"]
    data["a_reward_histogram_pct"] = stacked(ax_a, parts, colors, ["r = 0", "0 < r <= 0.25", "0.25 to 0.5", "0.5 to 0.75", "0.75 to <1", "r = 1"],
                                             "share of rollouts (%)", "a")
    ax_a.set_title(f"(a) Graded reward: {lo_partial:.0f}% to {hi_partial:.0f}% partial credit", loc="left", fontsize=10.5, fontweight="bold", color=ink, pad=8)

    # (b) none / some / all
    sat = s["saturation"]
    parts = [("none", {k: sat[k]["solved_by_no_setting"]["value"] for k in SUITES}), ("some", {k: sat[k]["middle"]["value"] for k in SUITES}),
             ("all", {k: sat[k]["solved_by_every_setting"]["value"] for k in SUITES})]
    data["b_tasks_solved_by_pct"] = stacked(ax_b, parts, [slate[2], "#eda100", slate[4]],
                                            ["no setting solves it", "some do (the middle)", "every setting solves it"], "share of tasks (%)", "b")
    ax_b.set_title("(b) Not saturated, not impossible", loc="left", fontsize=10.5, fontweight="bold", color=ink, pad=8)
    ax_b.set_yticks([])

    # (c) reward by effort
    plf.style_axes(ax_c, "y")
    xs = range(3)
    marker = plf.EFFORT_MARKER
    eff = ("low", "medium", "high")
    curves = {}
    for model, slug, color in (("beam", "beam", beam), ("gpt", "gpt-oss-120b", gpt)):
        per_bench = {k: [s["separation"]["ordering"][k]["pass_rate"][f"{slug}-{e}"]["value"] for e in eff] for k in SUITES}
        for k, ys in per_bench.items():
            ax_c.plot(xs, ys, color=color, lw=0.9, alpha=0.45, zorder=2)
            ax_c.scatter(xs, ys, s=16, color=color, alpha=0.6, zorder=3, linewidths=0)
        mean = [sum(per_bench[k][i] for k in SUITES) / len(SUITES) for i in range(3)]
        ax_c.plot(xs, mean, color=color, lw=2.4, zorder=4)
        for i, y in enumerate(mean):
            ax_c.plot(i, y, marker=marker[eff[i]], ms=plf.MS, color=color, mfc=surface if i < 2 else color, mew=2, zorder=5)
        curves[model] = {"per_benchmark": per_bench, "mean_of_5_benchmarks": mean}
    ends = plf.spread([curves["beam"]["mean_of_5_benchmarks"][2], curves["gpt"]["mean_of_5_benchmarks"][2]], 8.0, 0, 100)
    for name, y in zip(("Beam", "GPT-OSS"), ends):
        ax_c.text(2.15, y, name, fontsize=9, color=ink, va="center", path_effects=plf.halo())
    ax_c.set_xlim(-0.2, 2.75)
    ax_c.set_ylim(0, 100)
    ax_c.set_xticks(list(xs), eff, fontsize=9)
    ax_c.set_yticks([0, 25, 50, 75, 100])
    ax_c.set_ylabel("pass rate (%)", fontsize=9, color=ink2)
    ax_c.set_xlabel("reasoning effort (thin = each benchmark, thick = mean of the 5)", fontsize=8.5, color=ink2)
    ax_c.set_title("(c) Reward rises with effort, on average", loc="left", fontsize=10.5, fontweight="bold", color=ink, pad=8)
    data["c_pass_rate_by_effort"] = curves

    # (d) share of tasks where Beam high's 3 runs disagree
    var = s["within_policy_variance"]
    plf.style_axes(ax_d, "x")
    rows = [(BENCH_LABEL[k].replace(": ", ":\n"), var[k]["groups_with_signal"]) for k in SUITES if "groups_with_signal" in var[k]]
    rows.append(("All 4\npooled", var["pooled"]["groups_with_signal"]))
    out_d = []
    for i, (name, d) in enumerate(rows):
        pooled = i == len(rows) - 1
        ax_d.barh(i, d["value"], height=0.62, color=beam if not pooled else "#14509a", zorder=3)
        ax_d.plot([d["ci_low"], d["ci_high"]], [i, i], color=ink, lw=1.4, zorder=4, solid_capstyle="butt")
        for xe in (d["ci_low"], d["ci_high"]):
            ax_d.plot([xe, xe], [i - 0.12, i + 0.12], color=ink, lw=1.4, zorder=4)
        ax_d.text(min(d["ci_high"] + 2, 88), i, f"{d['value']:.0f}%", va="center", ha="left", fontsize=9, fontweight="bold", color=ink, path_effects=plf.halo())
        out_d.append({"label": name.replace("\n", " "), **d})
    ax_d.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=8.5, color=ink)
    ax_d.tick_params(axis="y", length=0)
    ax_d.set_ylim(len(rows) - 0.4, -0.6)
    ax_d.set_xlim(0, 100)
    ax_d.set_xlabel("tasks where Beam high's 3 runs differ (%), whisker = 95% CI", fontsize=8.5, color=ink2)
    ax_d.set_title("(d) Variance a GRPO group can use", loc="left", fontsize=10.5, fontweight="bold", color=ink, pad=8)
    data["d_beam_high_runs_differ"] = out_d
    data["headroom_best_pass_rate"] = {k: s["headroom"][k]["best_pass_rate"] for k in SUITES}

    plf.save(fig, fig_dir, FIGURE_NAME, data)
    return FIGURE_NAME


# ---- main ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS, help="scored CSVs and model_comparison/ (read-only)")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report.md and summary.json go here")
    p.add_argument("--fig-dir", type=Path, default=DEFAULT_FIG_DIR)
    p.add_argument("--n-boot", type=int, default=hv.DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=hv.DEFAULT_SEED)
    p.add_argument("--no-figure", action="store_true")
    args = p.parse_args(argv)

    rollouts = load_rollouts(args.results_dir)
    checked = check_leaderboard(rollouts, args.results_dir)
    summary = build_summary(rollouts, args.n_boot, args.seed, checked)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (args.out / "report.md").write_text(render_report(summary), encoding="utf-8")
    print(f"{checked} cells match the leaderboard -> {args.out / 'report.md'}")
    if not args.no_figure:
        try:
            print(f"figure: {args.fig_dir / draw_figure(summary, args.fig_dir)}.png")
        except ImportError as exc:  # matplotlib is an optional extra
            print(f"warning: figure skipped ({exc}); run with `uv run --extra plots`", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
