"""Numbers for the Space, computed only from the committed results CSVs (standard library).

The bootstrap copies `har2client/scripts/analyze_tierb.py`: 95% percentile interval, whole tasks redrawn with
replacement (and a task's runs redrawn when it has several), 5,000 resamples, seed 20261024. For the Tier B CSV this
reproduces the intervals in `results/analysis/tierb50_v1/summary.md`.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path

N_BOOT = 5000
SEED = 20261024
CI_LEVEL = 0.95
POINTS = 100.0
EFFORT_ORDER = ("low", "medium", "high")


@dataclass(frozen=True)
class Run:
    pass_rate: float
    prompt_tokens: float
    completion_tokens: float
    reasoning_tokens: float


Runs = dict[str, dict[str, list[Run]]]  # task -> effort -> runs


def _num(text: str | None) -> float:
    try:
        return float(text) if text not in (None, "") else 0.0
    except ValueError:
        return 0.0


def load_runs(path: Path) -> Runs:
    out: Runs = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ok = r.get("status", "ok") == "ok"
            run = Run(_num(r.get("pass_rate")) if ok else 0.0, _num(r.get("prompt_tokens")),
                      _num(r.get("completion_tokens")), _num(r.get("reasoning_tokens")))
            out.setdefault(r["task"], {}).setdefault(r["effort"], []).append(run)
    return out


def efforts_of(runs: Runs) -> list[str]:
    seen = {e for per in runs.values() for e in per}
    return sorted(seen, key=lambda e: (EFFORT_ORDER.index(e) if e in EFFORT_ORDER else len(EFFORT_ORDER), e))


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else math.nan


def quantile(sorted_xs: list[float], q: float) -> float:
    pos = q * (len(sorted_xs) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def interval(values: list[float]) -> tuple[float, float]:
    xs = sorted(v for v in values if not math.isnan(v))
    tail = (1 - CI_LEVEL) / 2
    return quantile(xs, tail), quantile(xs, 1 - tail)


def _task_value(runs: list[Run], rng: random.Random | None, field: str) -> float:
    if rng is not None and len(runs) > 1:
        runs = [runs[rng.randrange(len(runs))] for _ in runs]
    return mean(getattr(r, field) for r in runs)


def effort_summary(runs: Runs, tasks: list[str] | None = None, n_boot: int = N_BOOT, seed: int | str = SEED) -> dict[str, dict]:
    """effort -> {n_tasks, runs_per_task, mean_pct, ci_pct, perfect, out_tokens, prompt_tokens, reasoning_tokens}."""
    tasks = sorted(tasks if tasks is not None else runs)
    efforts = efforts_of({t: runs[t] for t in tasks})
    rng = random.Random(seed)
    boots: dict[str, list[float]] = {e: [] for e in efforts}
    for _ in range(n_boot):
        draw = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        for e in efforts:
            vals = [_task_value(runs[t][e], rng, "pass_rate") for t in draw if runs[t].get(e)]
            if vals:
                boots[e].append(mean(vals))
    out = {}
    for e in efforts:
        have = [t for t in tasks if runs[t].get(e)]
        per_task = [mean(r.pass_rate for r in runs[t][e]) for t in have]
        counts = [len(runs[t][e]) for t in have]
        lo, hi = interval(boots[e])
        out[e] = {
            "n_tasks": len(have),
            "runs_per_task": (min(counts), max(counts)),
            "mean_pct": mean(per_task) * POINTS,
            "ci_pct": (lo * POINTS, hi * POINTS),
            "perfect": sum(1 for v in per_task if v >= 1.0 - 1e-12),
            "out_tokens": mean(r.completion_tokens for t in have for r in runs[t][e]),
            "reasoning_tokens": mean(r.reasoning_tokens for t in have for r in runs[t][e]),
            "prompt_tokens": mean(r.prompt_tokens for t in have for r in runs[t][e]),
        }
    return out


def effort_contrast(runs: Runs, low: str = "low", high: str = "high", n_boot: int = N_BOOT, seed: int | str = SEED) -> dict | None:
    """Paired (by task) high-minus-low: pass-rate gain in points and extra output tokens per run, with 95% CIs."""
    tasks = sorted(t for t in runs if runs[t].get(low) and runs[t].get(high))
    if not tasks:
        return None

    def stat(draw: list[str], rng: random.Random | None) -> tuple[float, float]:
        gain = mean(_task_value(runs[t][high], rng, "pass_rate") - _task_value(runs[t][low], rng, "pass_rate") for t in draw)
        d_out = mean(_task_value(runs[t][high], rng, "completion_tokens") - _task_value(runs[t][low], rng, "completion_tokens") for t in draw)
        return gain * POINTS, d_out

    rng = random.Random(seed)
    boots = [stat([tasks[rng.randrange(len(tasks))] for _ in tasks], rng) for _ in range(n_boot)]
    gain, d_out = stat(tasks, None)
    return {
        "n_tasks": len(tasks),
        "gain_pp": gain, "gain_ci": interval([b[0] for b in boots]),
        "extra_out_tokens": d_out, "extra_out_ci": interval([b[1] for b in boots]),
    }


def group_means(runs: Runs, groups: dict[str, list[str]], effort: str, n_boot: int = N_BOOT, seed: int | str = SEED) -> dict[str, dict]:
    """Mean pass rate (points) with a task bootstrap interval for named groups of tasks at one effort."""
    out = {}
    for name, members in groups.items():
        tasks = sorted(t for t in members if runs.get(t, {}).get(effort))
        if not tasks:
            continue
        rng = random.Random(f"{seed}:{name}:{effort}")
        boots = [mean(_task_value(runs[t][effort], rng, "pass_rate") for t in [tasks[rng.randrange(len(tasks))] for _ in tasks])
                 for _ in range(n_boot)]
        lo, hi = interval(boots)
        out[name] = {"n_tasks": len(tasks), "mean_pct": mean(mean(r.pass_rate for r in runs[t][effort]) for t in tasks) * POINTS,
                     "ci_pct": (lo * POINTS, hi * POINTS)}
    return out
