#!/usr/bin/env python3
"""Statistics for the Tier B paper: feature effects, effort contrast, reliability, header ablation.

Usage:
  uv run python scripts/analyze_tierb.py --runs results/tierb50_v1.csv [more.csv ...] \
      --tasks runs/tierb50 --out results/analysis/tierb50_v1 [--n-boot 5000] [--seed 20261024]
  ... --header-arms base.csv hint.csv evidence.csv        (H10 mode; --runs is then optional)

Every row of a results CSV is one run of one task at one effort. Unit of analysis = task. Per-task score at an
effort = mean pass_rate over that task's runs. Confidence intervals are 95% percentile intervals from a cluster
bootstrap: each resample draws tasks with replacement, and (when a task has more than one run) draws that task's
runs with replacement. Pure standard library, no numpy.

Writes `summary.md` and `coefficients.csv` into --out.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from dataclasses import dataclass, field
from operator import mul
from pathlib import Path

DEFAULT_N_BOOT = 5000
DEFAULT_SEED = 20261024
CI_LEVEL = 0.95
RIDGE = 1e-9  # keeps the normal equations solvable in a resample that lacks a feature level
EFFORT_ORDER = ("low", "medium", "high")
ARM_SUFFIXES = ("__hint", "__evidence")
API_ERROR = "api_error"  # run_batch.py status for a failed model call: not a model result
# (factor, reference level, other levels in report order); binary factors use "off" as reference
CATEGORICAL = (("auth", "apikey_header", ("bearer_login", "cookie_csrf")), ("pagination", "none", ("offset", "cursor", "link_header")))
BINARY = ("token_expiry", "etag_update", "hidden_header", "envelope")
POINTS = 100.0


# ------------------------------------------------------------------ data


@dataclass(frozen=True)
class Run:
    pass_rate: float
    out_tokens: float
    total_tokens: float


def _num(text: str | None) -> float:
    try:
        return float(text) if text not in (None, "") else 0.0
    except ValueError:
        return 0.0


def strip_arm(task: str) -> str:
    for suffix in ARM_SUFFIXES:
        if task.endswith(suffix):
            return task[: -len(suffix)]
    return task


def load_runs(paths: list[Path], har_mode: str | None = None, strip: bool = False,
              excluded: dict[str, int] | None = None) -> dict[str, dict[str, list[Run]]]:
    """task -> effort -> runs, from one or more results CSVs (rows of all files count as runs).

    A row with status `api_error` (the model call failed) is not a run: it is left out, and when `excluded`
    is given its effort is counted there."""
    rows: list[dict] = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
    modes = sorted({r.get("har_mode", "") for r in rows})
    if har_mode is None and len(modes) > 1:
        raise SystemExit(f"error: results mix har_mode values {modes}; pass --har-mode to pick one")
    out: dict[str, dict[str, list[Run]]] = {}
    for r in rows:
        if har_mode is not None and r.get("har_mode", "") != har_mode:
            continue
        if r.get("status") == API_ERROR:
            if excluded is not None:
                excluded[r["effort"]] = excluded.get(r["effort"], 0) + 1
            continue
        task = strip_arm(r["task"]) if strip else r["task"]
        ok = r.get("status", "ok") == "ok"
        prompt, completion = _num(r.get("prompt_tokens")), _num(r.get("completion_tokens"))
        run = Run(_num(r.get("pass_rate")) if ok else 0.0, completion, prompt + completion)
        out.setdefault(task, {}).setdefault(r["effort"], []).append(run)
    return out


def load_task_features(tasks_dir: Path) -> dict[str, dict]:
    """task id (directory name) -> {"domain": ..., "config": {...}} from each task.json."""
    out = {}
    for d in sorted(Path(tasks_dir).iterdir()):
        if (d / "task.json").is_file():
            info = json.loads((d / "task.json").read_text(encoding="utf-8"))
            out[d.name] = {"domain": info["domain"], "config": info["config"]}
    return out


def efforts_of(obs: dict[str, dict[str, list[Run]]]) -> list[str]:
    seen = {e for per in obs.values() for e in per}
    return sorted(seen, key=lambda e: (EFFORT_ORDER.index(e) if e in EFFORT_ORDER else len(EFFORT_ORDER), e))


# ------------------------------------------------------------------ small numeric helpers


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else math.nan


def quantile(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return math.nan
    pos = q * (len(sorted_xs) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def interval(values: list[float | None]) -> tuple[float, float]:
    """95% percentile interval of the finite values."""
    xs = sorted(v for v in values if v is not None and not math.isnan(v))
    tail = (1 - CI_LEVEL) / 2
    return quantile(xs, tail), quantile(xs, 1 - tail)


def cholesky_solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            if i == j:
                low[i][i] = math.sqrt(s) if s > 0 else 1e-12
            else:
                low[i][j] = s / low[j][j]
    z = [0.0] * n
    for i in range(n):
        z[i] = (b[i] - sum(low[i][k] * z[k] for k in range(i))) / low[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (z[i] - sum(low[k][i] * x[k] for k in range(i + 1, n))) / low[i][i]
    return x


# ------------------------------------------------------------------ regression design


def feature_vector(feat: dict, domains: list[str]) -> tuple[list[str], list[float]]:
    """Switch columns (one-hot vs reference level) then domain dummies (first domain is the reference)."""
    names, values = [], []
    for factor, _ref, levels in CATEGORICAL:
        for level in levels:
            names.append(f"{factor}={level}")
            values.append(float(feat["config"][factor] == level))
    for factor in BINARY:
        names.append(f"{factor}=on")
        values.append(float(feat["config"][factor] == "on"))
    for dom in domains[1:]:
        names.append(f"domain={dom}")
        values.append(float(feat["domain"] == dom))
    return names, values


class Design:
    """Least squares on rows (task, effort). `fit(instances)` takes bootstrap instances: (task index, {effort: y}).
    X'X is the weighted sum of per-task Gram matrices (weights = how often a task was drawn); X'y is summed per row."""

    def __init__(self, names: list[str], rows_by_task: list[list[tuple[str, list[float]]]]):
        self.names = names
        self.rows = rows_by_task
        self.k = len(names)
        self.tri = [(i, j) for i in range(self.k) for j in range(i, self.k)]
        self.gram_t = [
            [sum(x[i] * x[j] for _e, x in rows) for (i, j) in self.tri] for rows in rows_by_task
        ]
        self.gram_by_entry = [list(col) for col in zip(*self.gram_t)] if self.gram_t else []

    def fit(self, instances: list[tuple[int, dict[str, float]]]) -> list[float]:
        counts = [0] * len(self.rows)
        for i, _ in instances:
            counts[i] += 1
        flat = [sum(map(mul, counts, col)) for col in self.gram_by_entry]
        a = [[0.0] * self.k for _ in range(self.k)]
        for (i, j), v in zip(self.tri, flat):
            a[i][j] = a[j][i] = v
        for i in range(1, self.k):
            a[i][i] += RIDGE
        a[0][0] += RIDGE
        b = [0.0] * self.k
        for i, ymap in instances:
            for effort, x in self.rows[i]:
                y = ymap.get(effort)
                if y is not None:
                    for j in range(self.k):
                        b[j] += x[j] * y
        return cholesky_solve(a, b)


def build_designs(tasks: list[str], feats: dict[str, dict], efforts_by_task: list[list[str]], efforts: list[str]):
    """({effort: Design}, Design | None for pooled, notes). Constant columns are dropped (listed in notes)."""
    domains = sorted({feats[t]["domain"] for t in tasks})
    names, matrix = None, []
    for t in tasks:
        names, vec = feature_vector(feats[t], domains)
        matrix.append(vec)
    keep = [j for j in range(len(names)) if len({row[j] for row in matrix}) > 1]
    notes = [f"column dropped (constant in this data): {names[j]}" for j in range(len(names)) if j not in keep]
    kept = [names[j] for j in keep]
    xs = [[row[j] for j in keep] for row in matrix]
    per = {}
    for e in efforts:
        rows = [[(e, [1.0, *x])] if e in have else [] for x, have in zip(xs, efforts_by_task)]
        per[e] = Design(["intercept", *kept], rows)
    pooled = None
    if len(efforts) == 2:
        high = efforts[1]
        pooled_names = ["intercept", f"effort={high}", *kept, *[f"effort={high} x {n}" for n in kept if not n.startswith("domain=")]]
        rows = []
        for x, have in zip(xs, efforts_by_task):
            row = []
            for e in efforts:
                if e not in have:
                    continue
                is_high = float(e == high)
                sw = [v for n, v in zip(kept, x) if not n.startswith("domain=")]
                row.append((e, [1.0, is_high, *x, *[is_high * v for v in sw]]))
            rows.append(row)
        pooled = Design(pooled_names, rows)
    return per, pooled, notes


# ------------------------------------------------------------------ resampling


class Sampler:
    """Draws bootstrap instances from obs[task][effort] -> runs. `point()` is the unresampled version."""

    def __init__(self, tasks: list[str], obs: dict[str, dict[str, list[Run]]], efforts: list[str]):
        self.tasks, self.obs, self.efforts = tasks, obs, efforts

    def _instance(self, i: int, rng: random.Random | None):
        ymap = {}
        for e in self.efforts:
            runs = self.obs[self.tasks[i]].get(e)
            if not runs:
                continue
            if rng is not None and len(runs) > 1:
                runs = [runs[rng.randrange(len(runs))] for _ in runs]
            ymap[e] = (mean(r.pass_rate for r in runs), mean(r.out_tokens for r in runs), mean(r.total_tokens for r in runs))
        return i, ymap

    def point(self):
        return [self._instance(i, None) for i in range(len(self.tasks))]

    def draw(self, rng: random.Random):
        n = len(self.tasks)
        return [self._instance(rng.randrange(n), rng) for _ in range(n)]


def y_only(instances):
    return [(i, {e: v[0] for e, v in ymap.items()}) for i, ymap in instances]


# ------------------------------------------------------------------ statistics on instances


def effort_means(instances, efforts) -> dict[str, float]:
    return {e: mean(v[e][0] for _i, v in instances if e in v) for e in efforts}


def contrast(instances, low: str, high: str) -> dict[str, float | None]:
    both = [v for _i, v in instances if low in v and high in v]
    if not both:
        return {}
    gain = mean(v[high][0] - v[low][0] for v in both)
    d_out = mean(v[high][1] - v[low][1] for v in both)
    d_tot = mean(v[high][2] - v[low][2] for v in both)
    ok = gain > 0
    return {
        "gain_pp": gain * POINTS,
        "extra_out_tokens": d_out,
        "extra_total_tokens": d_tot,
        "out_tokens_per_pp": d_out / (gain * POINTS) if ok else None,
        "total_tokens_per_pp": d_tot / (gain * POINTS) if ok else None,
    }


def passk_stats(tasks: list[str], obs, effort: str, idx: list[int], k: int) -> dict[str, float]:
    """Over the tasks in `idx` (with repeats): mean pass@1, share of runs fully passing, pass^k (all k runs fully pass)."""
    runs = [obs[tasks[i]][effort][:k] for i in idx]
    return {
        "mean_pass_at_1": mean(r.pass_rate for rs in runs for r in rs) * POINTS,
        "full_pass_at_1": mean(float(r.pass_rate >= 1.0) for rs in runs for r in rs) * POINTS,
        "pass_hat_k": mean(float(all(r.pass_rate >= 1.0 for r in rs)) for rs in runs) * POINTS,
    }


# ------------------------------------------------------------------ main analysis


@dataclass
class Analysis:
    tasks: list[str]
    efforts: list[str]
    n_boot: int
    seed: int
    notes: list[str]
    task_means: dict[str, dict[str, float]]
    effort_mean: dict[str, tuple[float, float, float]]
    coefs: list[dict]
    contrast: dict[str, tuple[float | None, float, float]]
    contrast_notes: list[str]
    passk: dict[str, dict]
    runs_per_cell: dict[str, tuple[int, int]]
    api_errors: dict[str, int] = field(default_factory=dict)  # effort -> api_error rows left out


def analyze(obs, feats, n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED) -> Analysis:
    tasks = sorted(t for t in obs if t in feats)
    missing = sorted(set(obs) - set(feats))
    notes = [f"no task.json for {len(missing)} task(s) in results; left out: {missing[:5]}"] if missing else []
    obs = {t: obs[t] for t in tasks}
    efforts = efforts_of(obs)
    have = [[e for e in efforts if obs[t].get(e)] for t in tasks]
    per, pooled, design_notes = build_designs(tasks, feats, have, efforts)
    notes += design_notes
    sampler = Sampler(tasks, obs, efforts)
    point = sampler.point()
    models: list[tuple[str, str, Design]] = [("per_effort", e, d) for e, d in per.items()]
    if pooled is not None:
        models.append(("pooled", "all", pooled))
    est = {(m, e): d.fit(y_only(point)) for m, e, d in models}
    point_means = effort_means(point, efforts)
    low, high = (efforts[0], efforts[-1]) if len(efforts) >= 2 else (None, None)
    point_contrast = contrast(point, low, high) if low else {}

    rng = random.Random(seed)
    boot_est = {key: [] for key in est}
    boot_means = {e: [] for e in efforts}
    boot_contrast: dict[str, list] = {}
    for _ in range(n_boot):
        inst = sampler.draw(rng)
        ys = y_only(inst)
        for (m, e, d) in models:
            boot_est[(m, e)].append(d.fit(ys))
        for e, v in effort_means(inst, efforts).items():
            boot_means[e].append(v)
        for key, v in (contrast(inst, low, high) if low else {}).items():
            boot_contrast.setdefault(key, []).append(v)

    coefs = []
    for m, e, d in models:
        n_tasks = sum(1 for rows in d.rows if rows)
        for j, term in enumerate(d.names):
            lo, hi = interval([b[j] * POINTS for b in boot_est[(m, e)]])
            coefs.append({"model": m, "effort": e, "term": term, "estimate_pp": est[(m, e)][j] * POINTS,
                          "ci_low_pp": lo, "ci_high_pp": hi, "n_tasks": n_tasks})
    effort_mean = {e: (point_means[e] * POINTS, *[x * POINTS for x in interval(boot_means[e])]) for e in efforts}
    contrast_out, contrast_notes = {}, []
    for key, value in point_contrast.items():
        lo, hi = interval(boot_contrast.get(key, []))
        contrast_out[key] = (value, lo, hi)
        if key.endswith("_per_pp"):
            share = sum(v is None for v in boot_contrast[key]) / max(1, len(boot_contrast[key]))
            if share:
                contrast_notes.append(f"{key}: {share:.1%} of resamples had no gain (ratio undefined); interval uses the rest")

    task_means = {t: {e: mean(r.pass_rate for r in obs[t][e]) for e in efforts if obs[t].get(e)} for t in tasks}
    passk, per_cell = {}, {}
    for e in efforts:
        counts = [len(obs[t][e]) for t in tasks if obs[t].get(e)]
        per_cell[e] = (min(counts), max(counts))
        k = min(counts)
        if k < 2:
            continue
        ids = [i for i, t in enumerate(tasks) if obs[t].get(e)]
        base = passk_stats(tasks, obs, e, ids, k)
        boots = {key: [] for key in base}
        rng_k = random.Random(f"{seed}:passk:{e}")
        for _ in range(n_boot):
            draw = [ids[rng_k.randrange(len(ids))] for _ in ids]
            for key, v in passk_stats(tasks, obs, e, draw, k).items():
                boots[key].append(v)
        boots["gap"] = [x - y for x, y in zip(boots["pass_hat_k"], boots["full_pass_at_1"])]
        gap = base["pass_hat_k"] - base["full_pass_at_1"]
        passk[e] = {"k": k, "n_tasks": len(ids), "gap": (gap, *interval(boots["gap"])),
                    **{key: (v, *interval(boots[key])) for key, v in base.items()}}
    return Analysis(tasks, efforts, n_boot, seed, notes, task_means, {e: effort_mean[e] for e in efforts}, coefs,
                    contrast_out, contrast_notes, passk, per_cell)


# ------------------------------------------------------------------ header ablation (H10)


def header_arms(base, hint, evidence, n_boot: int, seed: int, feats: dict | None = None) -> dict:
    """Paired per-task differences (arm - base) per effort and over efforts, with cluster-bootstrap CIs.
    Tasks missing from an arm are left out and listed in `dropped`; with `feats`, tasks whose config has no hidden header are listed too."""
    arms = {"base": base, "hint": hint, "evidence": evidence}
    tasks = sorted(set(base) & set(hint) & set(evidence))
    if not tasks:
        raise SystemExit("error: no task appears in all three header arms (check the task names and the arm suffixes)")
    dropped = sorted((set(base) | set(hint) | set(evidence)) - set(tasks))
    no_hidden = [t for t in tasks if feats is not None and (feats.get(t) or {}).get("config", {}).get("hidden_header") != "on"]
    efforts = [e for e in efforts_of({t: base[t] for t in tasks}) if all(arms[a][t].get(e) for a in arms for t in tasks)]

    def task_mean(arm: str, i: int, e: str, rng: random.Random | None) -> float:
        runs = arms[arm][tasks[i]][e]
        if rng is not None and len(runs) > 1:
            runs = [runs[rng.randrange(len(runs))] for _ in runs]
        return mean(r.pass_rate for r in runs)

    def stats(idx: list[int], rng: random.Random | None) -> dict[str, float]:
        out = {}
        for e in efforts:
            vals = {a: [task_mean(a, i, e, rng) for i in idx] for a in arms}
            for a in arms:
                out[f"{a}|{e}|mean"] = mean(vals[a]) * POINTS
                out[f"{a}|{e}|perfect"] = mean(float(v >= 1.0) for v in vals[a]) * POINTS
            for a in ("hint", "evidence"):
                out[f"{a}-base|{e}"] = mean(x - y for x, y in zip(vals[a], vals["base"])) * POINTS
        for a in ("hint", "evidence"):
            out[f"{a}-base|all"] = mean(out[f"{a}-base|{e}"] for e in efforts)
        out["evidence-hint|all"] = out["evidence-base|all"] - out["hint-base|all"]
        for e in efforts:
            out[f"evidence-hint|{e}"] = out[f"evidence-base|{e}"] - out[f"hint-base|{e}"]
        return out

    n = len(tasks)
    point = stats(list(range(n)), None)
    rng = random.Random(f"{seed}:header")
    boots: dict[str, list[float]] = {k: [] for k in point}
    for _ in range(n_boot):
        for k, v in stats([rng.randrange(n) for _ in range(n)], rng).items():
            boots[k].append(v)
    return {"tasks": tasks, "efforts": efforts, "dropped": dropped, "no_hidden_header": no_hidden,
            "stats": {k: (v, *interval(boots[k])) for k, v in point.items()}}


# ------------------------------------------------------------------ reports


def _ci(t: tuple[float, float, float], digits: int = 1) -> str:
    return f"{t[0]:+.{digits}f} [{t[1]:+.{digits}f}, {t[2]:+.{digits}f}]"


def _level(t: tuple[float, float, float]) -> str:
    return f"{t[0]:.1f} [{t[1]:.1f}, {t[2]:.1f}]"


def _num_ci(t: tuple[float | None, float, float], digits: int = 0) -> str:
    if t[0] is None:
        return "no gain (undefined)"
    return f"{t[0]:,.{digits}f} [{t[1]:,.{digits}f}, {t[2]:,.{digits}f}]"


def write_coefficients(coefs: list[dict], path: Path) -> None:
    fields = ["model", "effort", "term", "estimate_pp", "ci_low_pp", "ci_high_pp", "n_tasks"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for c in coefs:
            writer.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()})


def coef_table(coefs: list[dict], model: str, effort: str) -> list[str]:
    lines = ["| term | effect (points) [95% CI] |", "|---|---|"]
    for c in coefs:
        if c["model"] == model and c["effort"] == effort:
            lines.append(f"| {c['term']} | {_ci((c['estimate_pp'], c['ci_low_pp'], c['ci_high_pp']))} |")
    return lines


def _api_error_line(counts: dict[str, int], efforts: list[str]) -> list[str]:
    """One line naming the api_error rows left out per effort; nothing when there were none."""
    if not any(counts.values()):
        return []
    parts = [f"{e} {counts[e]}" for e in [*efforts, *sorted(set(counts) - set(efforts))] if counts.get(e)]
    return [f"Note: api_error rows excluded: {', '.join(parts)} (failed model calls, not counted as 0).", ""]


def render_summary(a: Analysis | None, sources: list[str], header: dict | None, n_boot: int, seed: int) -> str:
    out = ["# Tier B analysis", ""]
    if sources:
        out += [f"Sources: {', '.join(sources)}.", f"Tasks: {len(a.tasks)}."]
    out += [f"Resamples: {n_boot:,}. Seed: {seed}. All intervals are 95% percentile intervals from a bootstrap "
            "that redraws whole tasks (and, when a task has several runs, redraws its runs). Effects are in percentage points of pass rate.", ""]
    if a:
        out += _api_error_line(a.api_errors, a.efforts) + _main_sections(a)
    if header:
        out += _header_section(header) + _api_error_line(header.get("api_errors", {}), header["efforts"])
    return "\n".join(out).rstrip() + "\n"


def _main_sections(a: Analysis) -> list[str]:
    out: list[str] = []
    for note in a.notes:
        out.append(f"Note: {note}.")
    out += ["", "## Runs per task and effort", "", "| effort | fewest runs on a task | most runs on a task |", "|---|---|---|"]
    out += [f"| {e} | {lo} | {hi} |" for e, (lo, hi) in a.runs_per_cell.items()]
    out += ["", "## Mean pass rate by effort", "",
            "Mean over tasks of the task's mean pass rate (points, 0-100).", "",
            "| effort | mean pass rate [95% CI] |", "|---|---|"]
    out += [f"| {e} | {_level(a.effort_mean[e])} |" for e in a.efforts]
    out += ["", "## Per-task mean pass rate", "", f"| task | {' | '.join(a.efforts)} |", f"|---|{'---|' * len(a.efforts)}"]
    for t in a.tasks:
        out.append(f"| {t} | " + " | ".join(f"{a.task_means[t][e] * POINTS:.0f}" if e in a.task_means[t] else "-" for e in a.efforts) + " |")
    out += ["", "## Which switches change the pass rate", "",
            "Linear regression of each task's mean pass rate on the six switches plus domain (fixed effects). Reference task: "
            "auth = apikey_header, pagination = none, other switches off, first domain alphabetically. A coefficient is the "
            "change in pass rate (points) when that one switch is turned on, others held fixed. An interval that excludes 0 "
            "is evidence of an effect; an interval that includes 0 means the data cannot tell. This is a linear model of a 0-1 rate, so fitted values can pass 100; read it as average differences, not probabilities."]
    for e in a.efforts:
        out += ["", f"### Effort {e}", ""] + coef_table(a.coefs, "per_effort", e)
    if any(c["model"] == "pooled" for c in a.coefs):
        high = a.efforts[-1]
        out += ["", "### Both efforts in one model", "",
                f"`effort={high}` is the change from {a.efforts[0]} to {high} on the reference task. "
                f"A row `effort={high} x <switch>` is how much that switch's effect differs at {high} compared with {a.efforts[0]}. "
                "Domain effects are shared by both efforts.", ""] + coef_table(a.coefs, "pooled", "all")
    if a.contrast:
        low, high = a.efforts[0], a.efforts[-1]
        c = a.contrast
        out += ["", f"## Effort contrast ({high} minus {low})", "",
                "Paired by task. Tokens are output tokens (`completion_tokens`, which include reasoning) and total tokens "
                "(prompt + completion), averaged per task.", "",
                "| quantity | value [95% CI] |", "|---|---|",
                f"| pass-rate gain (points) | {_ci(c['gain_pp'])} |",
                f"| extra output tokens per run | {_num_ci(c['extra_out_tokens'])} |",
                f"| extra total tokens per run | {_num_ci(c['extra_total_tokens'])} |",
                f"| output tokens per point gained | {_num_ci(c['out_tokens_per_pp'], 1)} |",
                f"| total tokens per point gained | {_num_ci(c['total_tokens_per_pp'], 1)} |"]
        out += [f"\nNote: {n}." for n in a.contrast_notes]
    out += ["", "## Reliability across runs (pass^k)", ""]
    if not a.passk:
        out.append("Every task has a single run at every effort, so pass^k is not defined here.")
    else:
        out += ["A run is fully passing when it passes every test. pass^k is the share of tasks where all k runs fully pass. "
                "Compare it with the same-run share of fully passing single runs. Gap = pass^k minus that share (points).", "",
                "| effort | k | tasks | mean pass@1 | fully passing single runs | pass^k | gap |", "|---|---|---|---|---|---|---|"]
        for e, s in a.passk.items():
            out.append(f"| {e} | {s['k']} | {s['n_tasks']} | {_level(s['mean_pass_at_1'])} | {_level(s['full_pass_at_1'])} | "
                       f"{_level(s['pass_hat_k'])} | {_ci(s['gap'])} |")
    return out


def _header_section(h: dict) -> list[str]:
    s = h["stats"]
    out = ["", "## Header ablation (H10)", "",
           f"{len(h['tasks'])} tasks with a hidden header. `hint`: goal.md gains one line about custom headers. "
           "`evidence`: the HAR gains one failed call (400) without the header before the call that works. "
           "Differences are paired by task, in points of pass rate.", "",
           "| effort | base mean | hint mean | evidence mean | hint - base | evidence - base | evidence - hint |", "|---|---|---|---|---|---|---|"]
    for e in h["efforts"]:
        out.append(f"| {e} | {_level(s[f'base|{e}|mean'])} | {_level(s[f'hint|{e}|mean'])} | {_level(s[f'evidence|{e}|mean'])} | "
                   f"{_ci(s[f'hint-base|{e}'])} | {_ci(s[f'evidence-base|{e}'])} | {_ci(s[f'evidence-hint|{e}'])} |")
    out.append(f"| all efforts (average) | - | - | - | {_ci(s['hint-base|all'])} | {_ci(s['evidence-base|all'])} | {_ci(s['evidence-hint|all'])} |")
    out += ["", "Share of tasks with a perfect score (points):", "", "| effort | base | hint | evidence |", "|---|---|---|---|"]
    for e in h["efforts"]:
        out.append(f"| {e} | {_level(s[f'base|{e}|perfect'])} | {_level(s[f'hint|{e}|perfect'])} | {_level(s[f'evidence|{e}|perfect'])} |")
    if h.get("dropped"):
        out += ["", f"Note: {len(h['dropped'])} task(s) are not in all three arms and are left out: {', '.join(h['dropped'][:10])}."]
    if h.get("no_hidden_header"):
        out += ["", f"Note: {len(h['no_hidden_header'])} task(s) have no hidden header in task.json: {', '.join(h['no_hidden_header'][:10])}."]
    return out


# ------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", nargs="+", type=Path, help="results CSV(s); all rows count as runs")
    p.add_argument("--tasks", required=True, type=Path, help="task dir with <task>/task.json (features)")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--header-arms", nargs=3, type=Path, metavar=("BASE", "HINT", "EVIDENCE"))
    p.add_argument("--har-mode", default=None)
    p.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args(argv)
    if not args.runs and not args.header_arms:
        p.error("give --runs and/or --header-arms")
    args.out.mkdir(parents=True, exist_ok=True)
    feats = load_task_features(args.tasks)
    analysis = None
    if args.runs:
        errors: dict[str, int] = {}
        analysis = analyze(load_runs(args.runs, args.har_mode, excluded=errors), feats, args.n_boot, args.seed)
        analysis.api_errors = errors
        write_coefficients(analysis.coefs, args.out / "coefficients.csv")
    header = None
    if args.header_arms:
        arm_errors: dict[str, int] = {}
        arms = [load_runs([path], args.har_mode, strip=True, excluded=arm_errors) for path in args.header_arms]
        header = {**header_arms(*arms, n_boot=args.n_boot, seed=args.seed, feats=feats), "api_errors": arm_errors}
    text = render_summary(analysis, [str(r) for r in args.runs or []], header, args.n_boot, args.seed)
    if analysis is None:
        write_coefficients([], args.out / "coefficients.csv")
    (args.out / "summary.md").write_text(text, encoding="utf-8")
    print(f"wrote {args.out / 'summary.md'} and {args.out / 'coefficients.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
