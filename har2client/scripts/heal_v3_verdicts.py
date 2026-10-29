"""Statistics and pre-registered verdicts (H16-H21, saturation rule; H18 is not tested since xhigh was dropped) for the case 3 v3 evaluation suite.

Pure functions over per-step task scores; no file or network access. `scripts/analyze_heal_v3.py` loads the data and renders it.
The thresholds are copied verbatim from PREREG.md, section "Case 3 v3 evaluation suite (frozen 2026-10-24, before its first run)".

Reading rules: see READING_RULES (they are printed in the report).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Mapping

EFFORTS = ("low", "medium", "high")
ARMS = ("base", "hint")
DROPPED_NOTE = "xhigh dropped 2026-10-27, see PREREG.md deviations"
POINTS = 100.0
EPS = 1e-9
CI_LEVEL = 0.95
DEFAULT_N_BOOT = 10000
DEFAULT_SEED = 20261024

READING_RULES = (
    "Unit = task; the per-task score is the mean over that task's runs. `api_error` rows are not model results and are left out.",
    "Runs per task: high = 3 runs (every number at high is a \"mean of 3 runs\" per task; PREREG.md deviation 2026-10-27), low = 1 run "
    "(the repeat runs are in `heal_controls`), medium = 1 run. H16, H17, H19-H21 and the saturation rule use these per-task means; "
    "a full-repair share at high is the mean over runs of the 0/1 full-repair flag.",
    "Points are percentage points of `pass_rate` (the primary metric) unless a row says full-repair share.",
    "H19 names the full-repair share. H20 and H21 name no metric, so they use the primary metric `pass_rate`; their full-repair "
    "contrasts are shown beside them and not used for the verdict. Their CIs (independent groups of tasks) are information only.",
    '"CI excludes 0" (H16) is a 95% paired bootstrap CI whose lower end is above 0, required at both low and high.',
    '"Both within 3 points" (H20) means |difference| <= 3 for partial and for misleading (terse is shown, not used).',
    "A threshold on a mean is tested on the point estimate; \"not yet testable\" means a required step is missing.",
    "A step that has not finished every task counts as not yet run.",
    "Saturation uses the mean pass rate (0-1) of base-high on its scored tasks; the xhigh clauses of the rule were dropped with xhigh "
    "(2026-10-27, see PREREG.md deviations).",
    "H18 (xhigh - high) is not tested: " + DROPPED_NOTE + ".",
)

SUPPORTED, REJECTED, INCONCLUSIVE, NOT_YET = "supported", "rejected", "inconclusive", "not yet testable"
NOT_TESTED = "not tested"
TRIGGERED, NOT_TRIGGERED = "triggered", "not triggered"

PREREG = {
    "H16": {
        "title": "Beam fails to notice a silent interface break more than it fails to fix it",
        "test": "hint - base, paired by task, at each effort",
        "supported_if": ">= 20 points at low and at high, CI excludes 0",
        "rejected_if": "< 5 points at low and at high",
    },
    "H17": {
        "title": "Effort does not fix noticing",
        "test": "(high - low) in the base arm",
        "supported_if": "< 5 points, while high output >= 3x low",
        "rejected_if": ">= 10 points",
    },
    "H18": {
        "title": "Effort has diminishing returns on repair (H14 on v3; per arm)",
        "test": "not tested: " + DROPPED_NOTE,
        "supported_if": "n/a",
        "rejected_if": "n/a",
    },
    "H19": {
        "title": "More changes per release are harder (H15 on v3)",
        "test": "full-repair share by changes: single (1) vs release3 (3) vs release4 (4), hint arm, high",
        "supported_if": "strictly falls with the number of changes, release4 >= 15 points below single",
        "rejected_if": "release4 >= single",
    },
    "H20": {
        "title": "Missing or wrong side information costs more than more code (k = 3 families, hint arm, high)",
        "test": "partial, misleading, terse vs release3",
        "supported_if": "each of partial and misleading >= 10 points below release3",
        "rejected_if": "both within 3 points of release3",
    },
    "H21": {
        "title": "A layered SDK client is harder to repair than a compact one (k = 3)",
        "test": "sdk vs release3, both arms, high",
        "supported_if": "sdk >= 10 points below release3 in both arms",
        "rejected_if": "sdk >= release3 in both arms",
    },
    "saturation": {
        "title": "Saturation rule (added 2026-10-24 after the low-effort steps)",
        "test": "mean pass rate of the base arm at high (the xhigh clauses were dropped; " + DROPPED_NOTE + ")",
        "supported_if": "base arm >= 0.85 at high: the suite is reported as near saturation for Beam",
        "rejected_if": "base arm < 0.85 at high",
    },
}


@dataclass(frozen=True)
class TaskScore:
    family: str
    pass_rate: float
    strict: float
    full: float
    tokens: float | None
    credit: Mapping[str, float] = field(default_factory=dict)


METRICS: dict[str, Callable[[TaskScore], float | None]] = {
    "pass": lambda s: s.pass_rate,
    "strict": lambda s: s.strict,
    "full": lambda s: s.full,
    "tokens": lambda s: s.tokens,
}
SCALED = ("pass", "strict", "full")  # shares, reported in points


@dataclass(frozen=True)
class Step:
    """One (arm, effort) cell of the suite: every task graded so far."""

    arm: str
    effort: str
    expected: int
    finished: int
    api_errors: int
    scores: Mapping[str, TaskScore]
    runs: int = 1  # runs per task behind each score (high: 3)

    @property
    def name(self) -> str:
        return f"{self.arm}-{self.effort}"

    @property
    def complete(self) -> bool:
        return self.finished >= self.expected

    @property
    def usable(self) -> bool:
        return bool(self.scores) and self.complete


@dataclass(frozen=True)
class Est:
    n: int
    mean: float
    lo: float
    hi: float

    def to_dict(self) -> dict:
        return {"n": self.n, "mean": self.mean, "ci_low": self.lo, "ci_high": self.hi}


def mean(xs) -> float | None:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def _percentile_ci(draws: list[float]) -> tuple[float, float]:
    draws.sort()
    n, tail = len(draws), (1.0 - CI_LEVEL) / 2.0
    return draws[int(tail * n)], draws[min(n - 1, int((1.0 - tail) * n))]


def bootstrap_mean(values: list[float], n_boot: int, seed: int, key: str) -> Est | None:
    """Mean of `values` with a 95% percentile CI over resamples of the units (paired data: pass the differences)."""
    n = len(values)
    if n == 0:
        return None
    rng = random.Random(f"{seed}:{key}")
    draws = [sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)]
    lo, hi = _percentile_ci(draws)
    return Est(n, sum(values) / n, lo, hi)


def bootstrap_diff(xs: list[float], ys: list[float], n_boot: int, seed: int, key: str) -> Est | None:
    """mean(xs) - mean(ys) for two independent groups; each group is resampled on its own."""
    if not xs or not ys:
        return None
    rng = random.Random(f"{seed}:{key}")
    nx, ny = len(xs), len(ys)
    draws = [
        sum(xs[rng.randrange(nx)] for _ in range(nx)) / nx - sum(ys[rng.randrange(ny)] for _ in range(ny)) / ny
        for _ in range(n_boot)
    ]
    lo, hi = _percentile_ci(draws)
    return Est(nx + ny, sum(xs) / nx - sum(ys) / ny, lo, hi)


def _scale(metric: str) -> float:
    return POINTS if metric in SCALED else 1.0


def _common(a: Step, b: Step, metric: str) -> list[tuple[float, float]]:
    """(a, b) metric values for the tasks that both steps have, in task order."""
    get, out = METRICS[metric], []
    for task in sorted(set(a.scores) & set(b.scores)):
        x, y = get(a.scores[task]), get(b.scores[task])
        if x is not None and y is not None:
            out.append((x, y))
    return out


def token_ratio(a: Step, b: Step) -> float | None:
    """mean output tokens of a / of b over the tasks both have (paired)."""
    pairs = _common(a, b, "tokens")
    mb = mean(y for _, y in pairs)
    return mean(x for x, _ in pairs) / mb if pairs and mb else None


def family_values(step: Step, family: str, metric: str) -> list[float]:
    get = METRICS[metric]
    return [v * _scale(metric) for s in step.scores.values() if s.family == family and (v := get(s)) is not None]


def _ge(x: float, threshold: float) -> bool:
    return x >= threshold - EPS


def _le(x: float, threshold: float) -> bool:
    return x <= threshold + EPS


class Analysis:
    """Steps by (arm, effort) plus cached estimates; a step that is not usable behaves as missing."""

    def __init__(self, steps: Mapping[tuple[str, str], Step], n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED):
        self.steps, self.n_boot, self.seed = dict(steps), n_boot, seed
        self._memo: dict = {}

    def step(self, arm: str, effort: str) -> Step | None:
        s = self.steps.get((arm, effort))
        return s if s is not None and s.usable else None

    def missing(self, *cells: tuple[str, str]) -> list[str]:
        return [f"{arm}-{effort}" for arm, effort in cells if self.step(arm, effort) is None]

    def contrast(self, a: tuple[str, str], b: tuple[str, str], metric: str = "pass") -> Est | None:
        """Paired (a - b) over the tasks both steps have, in points for shares; None when a step is missing."""
        key = (a, b, metric)
        if key not in self._memo:
            sa, sb = self.step(*a), self.step(*b)
            est = None
            if sa is not None and sb is not None:
                scale = _scale(metric)
                diffs = [(x - y) * scale for x, y in _common(sa, sb, metric)]
                est = bootstrap_mean(diffs, self.n_boot, self.seed, f"{a}-{b}-{metric}")
            self._memo[key] = est
        return self._memo[key]

    def hint_minus_base(self, effort: str, metric: str = "pass") -> Est | None:
        return self.contrast(("hint", effort), ("base", effort), metric)

    def effort_contrast(self, arm: str, high: str, low: str, metric: str = "pass") -> Est | None:
        return self.contrast((arm, high), (arm, low), metric)

    def ratio(self, arm: str, high: str, low: str) -> float | None:
        sa, sb = self.step(arm, high), self.step(arm, low)
        return token_ratio(sa, sb) if sa is not None and sb is not None else None

    def family_mean(self, arm: str, effort: str, family: str, metric: str = "pass") -> tuple[int, float | None]:
        step = self.step(arm, effort)
        values = family_values(step, family, metric) if step is not None else []
        return len(values), mean(values)

    def family_diff(self, arm: str, effort: str, family: str, ref: str, metric: str = "pass") -> Est | None:
        """family - ref (points, independent groups): families hold different tasks."""
        step = self.step(arm, effort)
        if step is None:
            return None
        return bootstrap_diff(family_values(step, family, metric), family_values(step, ref, metric), self.n_boot, self.seed,
                              f"{arm}-{effort}-{family}-{ref}-{metric}")


# ------------------------------------------------------------ verdicts


def _verdict(hid: str, status: str, evidence: str, missing: list[str] | None = None) -> dict:
    return {"id": hid, **PREREG[hid.split(" ")[0]], "status": status, "evidence": evidence, "missing": missing or []}


def _not_yet(hid: str, missing: list[str]) -> dict:
    return _verdict(hid, NOT_YET, "waiting for step(s): " + ", ".join(missing), missing)


def effort_label(a: "Analysis", effort: str, arms=ARMS) -> str:
    """`high (mean of 3 runs)` when the usable steps of `arms` at `effort` hold several runs per task, else the effort name."""
    runs = [st.runs for arm in arms if (st := a.step(arm, effort)) is not None]
    return f"{effort} (mean of {min(runs)} runs)" if runs and min(runs) > 1 else effort


def _fmt(est: Est) -> str:
    return f"{est.mean:+.1f} pts [95% CI {est.lo:+.1f}, {est.hi:+.1f}] (n={est.n})"


def _ratio_text(r: float | None) -> str:
    return "n/a" if r is None else f"{r:.2f}x"


def verdict_h16(a: Analysis) -> dict:
    cells = [(arm, e) for e in ("low", "high") for arm in ARMS]
    if missing := a.missing(*cells):
        return _not_yet("H16", missing)
    est = {e: a.hint_minus_base(e) for e in ("low", "high")}
    text = "; ".join(f"{effort_label(a, e)}: hint - base {_fmt(v)}" for e, v in est.items())
    if all(v is not None and _ge(v.mean, 20) and v.lo > 0 for v in est.values()):
        return _verdict("H16", SUPPORTED, text)
    if all(v is not None and v.mean < 5 for v in est.values()):
        return _verdict("H16", REJECTED, text)
    return _verdict("H16", INCONCLUSIVE, text)


def verdict_h17(a: Analysis) -> dict:
    if missing := a.missing(("base", "low"), ("base", "high")):
        return _not_yet("H17", missing)
    est, ratio = a.effort_contrast("base", "high", "low"), a.ratio("base", "high", "low")
    text = f"base, {effort_label(a, 'high', ('base',))} - low {_fmt(est)}; output high/low = {_ratio_text(ratio)}"
    if _ge(est.mean, 10):
        return _verdict("H17", REJECTED, text)
    if est.mean < 5 and ratio is not None and _ge(ratio, 3):
        return _verdict("H17", SUPPORTED, text)
    return _verdict("H17", INCONCLUSIVE, text)


def verdict_h18(a: Analysis, arm: str) -> dict:
    """Not tested: the xhigh level was dropped, so the xhigh - high contrast no longer exists."""
    return _verdict(f"H18 ({arm})", NOT_TESTED, "not tested: " + DROPPED_NOTE)


def verdict_h19(a: Analysis) -> dict:
    if missing := a.missing(("hint", "high")):
        return _not_yet("H19", missing)
    fams = ("single", "release3", "release4")
    stats = {f: a.family_mean("hint", "high", f, "full") for f in fams}
    if any(n == 0 for n, _ in stats.values()):
        return _not_yet("H19", [f"hint-high tasks of {f}" for f, (n, _) in stats.items() if n == 0])
    one, three, four = (stats[f][1] for f in fams)
    text = f"full-repair share, hint, {effort_label(a, 'high', ('hint',))}: " + ", ".join(f"{f} {m:.1f}% (n={n})" for f, (n, m) in stats.items())
    if four >= one - EPS:
        return _verdict("H19", REJECTED, text)
    if one > three > four and _ge(one - four, 15):
        return _verdict("H19", SUPPORTED, text)
    return _verdict("H19", INCONCLUSIVE, text)


def _family_gap(a: Analysis, arm: str, family: str, ref: str) -> tuple[float | None, str]:
    n, m = a.family_mean(arm, "high", family)
    n_ref, m_ref = a.family_mean(arm, "high", ref)
    if m is None or m_ref is None:
        return None, f"{family}: no tasks"
    est = a.family_diff(arm, "high", family, ref)
    full = a.family_diff(arm, "high", family, ref, "full")
    return m - m_ref, f"{family} - {ref} {_fmt(est)} (n={n} vs {n_ref}); full-repair {full.mean:+.1f} pts"


def verdict_h20(a: Analysis) -> dict:
    if missing := a.missing(("hint", "high")):
        return _not_yet("H20", missing)
    gaps = {f: _family_gap(a, "hint", f, "release3") for f in ("partial", "misleading", "terse")}
    text = f"pass rate, hint, {effort_label(a, 'high', ('hint',))}: " + "; ".join(t for _, t in gaps.values()) + " (terse is shown, not used)"
    p, m = gaps["partial"][0], gaps["misleading"][0]
    if p is None or m is None:
        return _not_yet("H20", ["hint-high tasks of partial, misleading and release3"])
    if _le(p, -10) and _le(m, -10):
        return _verdict("H20", SUPPORTED, text)
    if abs(p) <= 3 + EPS and abs(m) <= 3 + EPS:
        return _verdict("H20", REJECTED, text)
    return _verdict("H20", INCONCLUSIVE, text)


def verdict_h21(a: Analysis) -> dict:
    if missing := a.missing(("base", "high"), ("hint", "high")):
        return _not_yet("H21", missing)
    gaps = {arm: _family_gap(a, arm, "sdk", "release3") for arm in ARMS}
    text = f"pass rate, {effort_label(a, 'high')}: " + "; ".join(f"{arm}: {t}" for arm, (_, t) in gaps.items())
    if any(g is None for g, _ in gaps.values()):
        return _not_yet("H21", ["tasks of sdk and release3 in both arms"])
    if all(_le(g, -10) for g, _ in gaps.values()):
        return _verdict("H21", SUPPORTED, text)
    if all(g >= -EPS for g, _ in gaps.values()):
        return _verdict("H21", REJECTED, text)
    return _verdict("H21", INCONCLUSIVE, text)


SATURATION_CHECKS = (("base", "high", 0.85),)  # the xhigh clauses were dropped with xhigh (PREREG.md deviations, 2026-10-27)


def verdict_saturation(a: Analysis) -> dict:
    hits, seen, missing = [], [], a.missing(*[(arm, e) for arm, e, _ in SATURATION_CHECKS])
    for arm, effort, threshold in SATURATION_CHECKS:
        step = a.step(arm, effort)
        if step is None:
            continue
        value = mean(s.pass_rate for s in step.scores.values())
        seen.append(f"{arm}-{effort_label(a, effort, (arm,))} {value:.3f} (threshold {threshold:.2f}, n={len(step.scores)})")
        if value >= threshold - EPS:
            hits.append(f"{arm}-{effort}")
    text = "; ".join(seen) or "no step at high yet"
    text += f" [{DROPPED_NOTE}: the xhigh clauses are not evaluated]"
    if hits:
        return _verdict("saturation", TRIGGERED, f"near saturation for Beam: {', '.join(hits)} reached its threshold. {text}")
    if missing:
        return _verdict("saturation", NOT_YET, f"not triggered so far; waiting for step(s): {', '.join(missing)}. {text}", missing)
    return _verdict("saturation", NOT_TRIGGERED, text)


def all_verdicts(a: Analysis) -> list[dict]:
    return [verdict_h16(a), verdict_h17(a), verdict_h18(a, "base"), verdict_h18(a, "hint"), verdict_h19(a), verdict_h20(a),
            verdict_h21(a), verdict_saturation(a)]
