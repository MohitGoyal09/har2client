#!/usr/bin/env python3
"""Case 3 analysis: effort curve, regression and patch size, repair vs scratch (H12), terse vs verbose (H13),
effort returns (H14), compound vs single changes (H15), split by `identifiable`.

Usage:
  uv run python scripts/analyze_heal.py --scored results/heal_core_scored.csv [more.csv ...] \
      [--scratch results/heal48_scratch.csv] --out results/analysis/heal_core [--n-boot 10000] [--seed 20261024]

`--scored`: CSVs from `scripts/score_heal.py` (one row per attempt: task, effort, run, status, pass_rate, tokens, and the
columns set, mutations, error_detail, evidence, identifiable, regression_rate, patch_lines, patch_ast_nodes).
`--scratch`: results CSVs of the case 1 twins (`runs/heal48_scratch`, task ids `scratch_NN_...`); they may also sit in
`--scored`. Task factors come from the CSV columns, then from `--manifest` (default `data/heal_suite.jsonl`), then from
the task id (heal48 ids). A twin is paired with the repair task of the same row (`--configs`, default
`data/heal_configs.jsonl`, maps `scratch_id` to the row index).

Unit of analysis = the task cluster: one row of `heal_configs.jsonl` (its repair task, its terse and single-channel variants,
its from-scratch twin); a compound task is its own cluster. Per-task score at an effort = mean over that task's runs.
CIs are 95% percentile intervals of a cluster bootstrap: clusters are drawn with replacement, and a task with more than one
run at an effort draws its runs with replacement. Pure standard library. Writes `summary.md`, `results.csv` (every
estimate with its CI) and `hypotheses.json` into `--out`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "heal_suite.jsonl"
DEFAULT_CONFIGS = ROOT / "data" / "heal_configs.jsonl"
DEFAULT_N_BOOT = 10000
DEFAULT_SEED = 20261024
CI_LEVEL = 0.95
EFFORT_ORDER = ("low", "medium", "high")
DROPPED_EFFORTS = ("xhigh",)  # not reported (PREREG.md deviations, 2026-10-27); data rows of these efforts are ignored
DROPPED_NOTE = "xhigh dropped 2026-10-27, see PREREG.md deviations"
PRIMARY_EFFORT = "high"  # H12, H13 and H15 are pre-registered for Beam high
POINTS = 100.0
TOKENS_PER_K = 1000.0
MUTATION_NAMES = (
    "add_hidden_header", "rename_hidden_header", "apikey_to_bearer_login", "offset_to_cursor", "none_to_link_header",
    "add_envelope", "add_etag", "add_token_expiry",
)
RAISING = ("add_hidden_header", "rename_hidden_header", "apikey_to_bearer_login", "add_etag", "add_token_expiry")  # header, auth, etag, expiry
SILENT = ("offset_to_cursor", "none_to_link_header", "add_envelope")  # paging, envelope
HEADER_MUTATIONS = ("add_hidden_header", "rename_hidden_header")
METRICS = ("pass", "full", "reg", "lines", "nodes", "tok")
ID_FILTERS = ("all", "yes", "no")

# thresholds copied verbatim from PREREG.md
PREREG = {
    "H12": {
        "title": "Repair is easier than building",
        "supported": "repair >= build + 10 points; `add_hidden_header` and `rename_hidden_header` are the hardest mutations",
        "rejected": "repair <= build",
    },
    "H13": {
        "title": "Repair depends on error detail",
        "supported": "terse >= 15 points below verbose on mutations that raise errors (header, auth, etag, expiry); < 5 points difference on silent ones (paging, envelope)",
        "rejected": "difference < 5 points on raising mutations",
    },
    "H14": {
        "title": "Effort has diminishing returns on repair",
        "supported": "n/a (not tested: " + DROPPED_NOTE + ")",
        "rejected": "n/a (not tested: " + DROPPED_NOTE + ")",
    },
    "H15": {
        "title": "Two changes are harder than one, beyond independence",
        "supported": "share of compound tasks fully repaired (pass_rate = 1.0) is >= 10 points below the product of the two single-change full-repair shares (same bases)",
        "rejected": "compound >= product",
    },
}
SUPPORTED, NOT_SUPPORTED, NOT_TESTABLE = "supported", "not supported", "not yet testable"
NOT_TESTED = "not tested"


# ------------------------------------------------------------------ data


@dataclass(frozen=True)
class Run:
    pass_rate: float
    reg: float | None
    lines: float | None
    nodes: float | None
    tok: float | None

    @property
    def full(self) -> float:
        return float(self.pass_rate >= 1.0)


@dataclass
class Task:
    id: str
    kind: str  # "repair" or "scratch"
    set: str
    mutations: tuple[str, ...]
    error_detail: str
    evidence: str
    identifiable: bool | None
    cluster: str
    runs: dict[str, list[Run]] = field(default_factory=lambda: defaultdict(list))


def _num(text: object) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_bool(text: object) -> bool | None:
    value = str(text).strip().lower()
    return True if value in ("true", "yes", "1") else False if value in ("false", "no", "0") else None


def read_csvs(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
    return rows


def read_jsonl(path: Path | None) -> list[dict]:
    if path is None or not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def mutations_in_id(task_id: str) -> tuple[str, ...]:
    found = sorted((task_id.find(n), n) for n in MUTATION_NAMES if n in task_id)
    return tuple(n for _, n in found)


def infer_set(names: tuple[str, ...], error_detail: str, evidence: str) -> str:
    if len(names) > 1:
        return "compound"
    return "terse" if error_detail == "terse" else ("core" if evidence == "both" else "evidence")


def _row_index(task_id: str) -> str | None:
    m = re.match(r"^[a-z]+_(\d+)_", task_id)
    return str(int(m.group(1))) if m else None  # "03" and 3 (the index in the manifest and the configs) name one cluster


def make_task(task_id: str, first_row: dict, manifest: dict[str, dict], scratch_index: dict[str, int]) -> Task:
    """Factors of one task: CSV columns, then the manifest, then the id."""
    man = manifest.get(task_id, {})
    if task_id.startswith("scratch_"):
        idx = scratch_index.get(task_id)
        idx = str(idx) if idx is not None else _row_index(task_id)
        return Task(task_id, "scratch", "scratch", mutations_in_id(task_id), "verbose", "both", None, f"row{idx}" if idx is not None else task_id)
    csv_names = tuple(n for n in str(first_row.get("mutations") or "").split("+") if n)
    names = csv_names or tuple(man.get("mutations") or ()) or mutations_in_id(task_id)
    error_detail = first_row.get("error_detail") or man.get("error_detail") or ("terse" if task_id.startswith("terse_") else "verbose")
    evidence = first_row.get("evidence") or man.get("evidence") or "both"
    set_name = first_row.get("set") or man.get("set") or infer_set(names, error_detail, evidence)
    ident = parse_bool(first_row.get("identifiable"))
    if ident is None and man:
        ident = man.get("identifiable")
    if len(names) > 1:
        cluster = f"cmp:{task_id}"
    else:
        idx = man.get("base_index", _row_index(task_id))
        cluster = f"row{idx}" if idx is not None else task_id
    return Task(task_id, "repair", set_name, names, error_detail, evidence, ident, cluster)


API_ERROR = "api_error"  # run_batch.py status for a failed model call: not a model result


def load_tasks(rows: list[dict], manifest_rows: list[dict], config_rows: list[dict], reasoning_separate: bool = False,
               excluded: dict[str, int] | None = None) -> list[Task]:
    """Tasks with their runs. An `api_error` row (the model call failed) is not a run: it is left out and,
    when `excluded` is given, counted there by effort."""
    manifest = {r["task_id"]: r for r in manifest_rows}
    scratch_index = {r["scratch_id"]: r["index"] for r in config_rows if "scratch_id" in r}
    tasks: dict[str, Task] = {}
    for r in rows:
        if r.get("status") == API_ERROR:
            if excluded is not None:
                excluded[r["effort"]] = excluded.get(r["effort"], 0) + 1
            continue
        task = tasks.get(r["task"]) or tasks.setdefault(r["task"], make_task(r["task"], r, manifest, scratch_index))
        ok = r.get("status", "ok") == "ok"
        tok = _num(r.get("completion_tokens"))
        if tok is not None and reasoning_separate:
            tok += _num(r.get("reasoning_tokens")) or 0.0
        task.runs[r["effort"]].append(Run(
            (_num(r.get("pass_rate")) or 0.0) if ok else 0.0, _num(r.get("regression_rate")), _num(r.get("patch_lines")),
            _num(r.get("patch_ast_nodes")), tok,
        ))
    return sorted(tasks.values(), key=lambda t: t.id)


def efforts_of(tasks: list[Task]) -> list[str]:
    seen = {e for t in tasks for e in t.runs if e not in DROPPED_EFFORTS}
    return sorted(seen, key=lambda e: (EFFORT_ORDER.index(e) if e in EFFORT_ORDER else len(EFFORT_ORDER), e))


# ------------------------------------------------------------------ numeric helpers


def mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def quantile(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return math.nan
    pos = q * (len(sorted_xs) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def interval(values: list[float | None]) -> tuple[float, float]:
    """95% percentile interval of the defined values (nan, nan when there are none)."""
    xs = sorted(v for v in values if v is not None and not math.isnan(v))
    tail = (1 - CI_LEVEL) / 2
    return quantile(xs, tail), quantile(xs, 1 - tail)


def ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


# ------------------------------------------------------------------ resampling


def _cell_metrics(runs: list[Run]) -> dict[str, float | None]:
    return {
        "pass": mean(r.pass_rate for r in runs), "full": mean(r.full for r in runs), "reg": mean(r.reg for r in runs),
        "lines": mean(r.lines for r in runs), "nodes": mean(r.nodes for r in runs), "tok": mean(r.tok for r in runs),
    }


@dataclass
class View:
    """A task in one (re)sample: its per-effort metric means."""
    task: Task
    cells: dict[str, dict[str, float | None]]
    slot: int = 0  # which draw of its cluster in a resample (one cluster can be drawn twice); its variants share the slot


class Sampler:
    """Cluster bootstrap over task clusters. `point()` is the unresampled sample."""

    def __init__(self, tasks: list[Task]):
        by_cluster: dict[str, list[Task]] = defaultdict(list)
        for t in tasks:
            by_cluster[t.cluster].append(t)
        self.clusters = [by_cluster[k] for k in sorted(by_cluster)]
        self._point = {t.id: {e: _cell_metrics(runs) for e, runs in t.runs.items() if runs} for t in tasks}

    def _view(self, task: Task, rng: random.Random | None, slot: int = 0) -> View:
        cells = {}
        for e, runs in task.runs.items():
            if not runs:
                continue
            if rng is not None and len(runs) > 1:
                cells[e] = _cell_metrics([runs[rng.randrange(len(runs))] for _ in runs])
            else:
                cells[e] = self._point[task.id][e]
        return View(task, cells, slot)

    def point(self) -> list[View]:
        return [View(t, self._point[t.id]) for cluster in self.clusters for t in cluster]

    def draw(self, rng: random.Random) -> list[View]:
        n = len(self.clusters)
        return [self._view(t, rng, slot) for slot in range(n) for t in self.clusters[rng.randrange(n)]]


def bootstrap(sampler: Sampler, stats_fn, n_boot: int, seed: int) -> dict[str, tuple[float | None, float, float]]:
    """key -> (point estimate, CI low, CI high)."""
    point = stats_fn(sampler.point())
    rng = random.Random(seed)
    draws: dict[str, list[float | None]] = defaultdict(list)
    for _ in range(n_boot):
        for key, value in stats_fn(sampler.draw(rng)).items():
            draws[key].append(value)
    return {key: (point[key], *interval(draws.get(key, []))) for key in point}


# ------------------------------------------------------------------ statistics on a sample


def col(views: list[View], effort: str, metric: str) -> list[float | None]:
    return [v.cells[effort][metric] for v in views if effort in v.cells]


def _ident_ok(task: Task, flag: str) -> bool:
    return flag == "all" or (flag == "yes" and task.identifiable is True) or (flag == "no" and task.identifiable is False)


def _pairs(views: list[View], base, other, effort: str, flag: str = "all") -> list[tuple[View, View]]:
    """(base view, other view) of the same cluster that both have `effort`; `flag` filters the `other` view by identifiable."""
    bases = {(v.task.cluster, v.slot): v for v in views if base(v.task) and effort in v.cells}
    keys = [(v, (v.task.cluster, v.slot)) for v in views if other(v.task) and effort in v.cells and _ident_ok(v.task, flag)]
    return [(bases[key], v) for v, key in keys if key in bases]


def _diff_pp(pairs: list[tuple[View, View]], effort: str, metric: str = "pass") -> float | None:
    d = mean((a.cells[effort][metric] - b.cells[effort][metric]) for a, b in pairs
             if a.cells[effort][metric] is not None and b.cells[effort][metric] is not None)
    return None if d is None else d * POINTS


def is_core(t: Task) -> bool:
    return t.kind == "repair" and t.set == "core"


def stats(views: list[View], efforts: list[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    core = [v for v in views if is_core(v.task)]
    scale = {"pass": POINTS, "full": POINTS, "reg": POINTS, "lines": 1.0, "nodes": 1.0, "tok": 1.0}
    for e in efforts:
        for m in METRICS:
            value = mean(col(core, e, m))
            out[f"core.{m}|{e}"] = None if value is None else value * scale[m]
        out.update(_mutation_stats(core, e))
        out.update(_set_stats(views, e))
    for a, b in zip(efforts, efforts[1:]):
        out.update(_adjacent(core, a, b))
    for e in efforts:
        for part in (_h12(views, core, e), _h13(views, e), _evidence(views, e), _h15(views, core, e)):
            out.update(part)
    return out


def _mutation_stats(core: list[View], e: str) -> dict[str, float | None]:
    out = {}
    for m in MUTATION_NAMES:
        value = mean(col([v for v in core if v.task.mutations == (m,)], e, "pass"))
        out[f"mut.pass|{m}|{e}"] = None if value is None else value * POINTS
    return out


def _set_stats(views: list[View], e: str) -> dict[str, float | None]:
    """Pass rate and full-repair share by set x identifiable (repair tasks only)."""
    out = {}
    for s in ("core", "terse", "evidence", "compound"):
        for flag in ID_FILTERS:
            sel = [v for v in views if v.task.kind == "repair" and v.task.set == s and _ident_ok(v.task, flag)]
            for m in ("pass", "full"):
                value = mean(col(sel, e, m))
                out[f"set.{m}|{s}|{flag}|{e}"] = None if value is None else value * POINTS
    return out


def _adjacent(core: list[View], a: str, b: str) -> dict[str, float | None]:
    both = [v for v in core if a in v.cells and b in v.cells]
    gain = mean(v.cells[b]["pass"] - v.cells[a]["pass"] for v in both)
    toks = [v for v in both if v.cells[a]["tok"] is not None and v.cells[b]["tok"] is not None]
    dtok = mean(v.cells[b]["tok"] - v.cells[a]["tok"] for v in toks)
    gain_pp = None if gain is None else gain * POINTS
    return {
        f"adj.gain|{a}>{b}": gain_pp, f"adj.dtok|{a}>{b}": dtok,
        f"adj.per1k|{a}>{b}": ratio(gain_pp, None if dtok is None or dtok <= 0 else dtok / TOKENS_PER_K),
        f"adj.tokratio|{a}>{b}": ratio(mean(v.cells[b]["tok"] for v in toks), mean(v.cells[a]["tok"] for v in toks)),
    }


def _h12(views: list[View], core: list[View], e: str) -> dict[str, float | None]:
    scratch = lambda t: t.kind == "scratch"  # noqa: E731
    pairs = _pairs(views, is_core, scratch, e)  # base = repair, other = scratch
    out: dict[str, float | None] = {f"h12.diff|{e}": _diff_pp(pairs, e), f"h12.repair|{e}": None, f"h12.build|{e}": None}
    if pairs:
        out[f"h12.repair|{e}"] = mean(a.cells[e]["pass"] for a, _ in pairs) * POINTS
        out[f"h12.build|{e}"] = mean(b.cells[e]["pass"] for _, b in pairs) * POINTS
    for m in MUTATION_NAMES:
        sel = [(a, b) for a, b in pairs if a.task.mutations == (m,)]
        out[f"h12.mut|{m}|{e}"] = _diff_pp(sel, e)
    return out


def _h13(views: list[View], e: str) -> dict[str, float | None]:
    """verbose - terse on the same base x mutation pairs, split raising / silent and by identifiable."""
    groups = {"all": MUTATION_NAMES, "raising": RAISING, "silent": SILENT}
    out = {}
    for g, names in groups.items():
        for flag in ID_FILTERS:
            pairs = [(a, b) for a, b in _pairs(views, is_core, lambda t: t.kind == "repair" and t.set == "terse", e, flag) if a.task.mutations[0] in names]
            out[f"h13.diff|{g}|{flag}|{e}"] = _diff_pp(pairs, e)
            out[f"h13.verbose|{g}|{flag}|{e}"] = None if not pairs else mean(a.cells[e]["pass"] for a, _ in pairs) * POINTS
            out[f"h13.terse|{g}|{flag}|{e}"] = None if not pairs else mean(b.cells[e]["pass"] for _, b in pairs) * POINTS
    return out


def _evidence(views: list[View], e: str) -> dict[str, float | None]:
    """verbose/both minus the same pair with one evidence channel (exploratory; no pre-registered hypothesis)."""
    out = {}
    for ev in ("failure_only", "recording_only"):
        for flag in ID_FILTERS:
            pairs = _pairs(views, is_core, lambda t, ev=ev: t.kind == "repair" and t.set == "evidence" and t.evidence == ev, e, flag)
            out[f"ev.diff|{ev}|{flag}|{e}"] = _diff_pp(pairs, e)
            out[f"ev.pass|{ev}|{flag}|{e}"] = None if not pairs else mean(b.cells[e]["pass"] for _, b in pairs) * POINTS
    return out


def _h15(views: list[View], core: list[View], e: str) -> dict[str, float | None]:
    """Share of compound tasks fully repaired vs the mean product of the two single-change full-repair shares."""
    single = {m: mean(col([v for v in core if v.task.mutations == (m,)], e, "full")) for m in MUTATION_NAMES}
    out = {}
    for flag in ID_FILTERS:
        comp = [v for v in views if v.task.kind == "repair" and len(v.task.mutations) == 2 and e in v.cells and _ident_ok(v.task, flag)]
        usable = [v for v in comp if all(single.get(m) is not None for m in v.task.mutations)]
        compound = mean(v.cells[e]["full"] for v in usable)
        expected = mean(single[v.task.mutations[0]] * single[v.task.mutations[1]] for v in usable)
        out[f"h15.compound|{flag}|{e}"] = None if compound is None else compound * POINTS
        out[f"h15.expected|{flag}|{e}"] = None if expected is None else expected * POINTS
        out[f"h15.diff|{flag}|{e}"] = None if compound is None else (compound - expected) * POINTS
    return out


# ------------------------------------------------------------------ verdicts

Est = tuple  # (point, lo, hi)


def _get(res: dict, key: str) -> Est | None:
    est = res.get(key)
    return est if est is not None and est[0] is not None else None


def _clears(est: Est, threshold: float, direction: str) -> bool:
    """Does the whole CI sit on the supported side of the threshold?"""
    lo, hi = est[1], est[2]
    if math.isnan(lo) or math.isnan(hi):
        return False
    return lo >= threshold if direction == ">=" else hi <= threshold if direction == "<=" else hi < threshold


def verdict_h12(res: dict, pe: str) -> dict:
    diff = _get(res, f"h12.diff|{pe}")
    if diff is None:
        return {"verdict": NOT_TESTABLE, "reason": f"needs repair and from-scratch results at effort {pe} on the same rows"}
    means = {m: _get(res, f"mut.pass|{m}|{pe}") for m in MUTATION_NAMES}
    known = {m: e[0] for m, e in means.items() if e is not None}
    hardest_known = len(known) == len(MUTATION_NAMES)
    hardest = hardest_known and max(known[m] for m in HEADER_MUTATIONS) <= min(v for m, v in known.items() if m not in HEADER_MUTATIONS)
    base = f"repair - build = {diff[0]:+.1f} points (95% CI {diff[1]:+.1f} to {diff[2]:+.1f})"
    if diff[0] <= 0:
        return {"verdict": NOT_SUPPORTED, "reason": f"{base}; the rejection rule (repair <= build) holds", "rejected": True}
    if diff[0] < 10:
        return {"verdict": NOT_SUPPORTED, "reason": f"{base}; the gap is below 10 points", "rejected": False}
    if not hardest_known:
        return {"verdict": NOT_TESTABLE, "reason": f"{base}; but only {len(known)} of 8 mutations have repair results, so 'hardest' cannot be judged"}
    note = "" if _clears(diff, 10.0, ">=") else "; the CI does not clear 10 points"
    if not hardest:
        order = ", ".join(f"{m} {v * 1:.0f}" for m, v in sorted(known.items(), key=lambda kv: kv[1])[:3])
        return {"verdict": NOT_SUPPORTED, "reason": f"{base}; but the hardest mutations are not the two header ones (lowest repair pass rates: {order})", "rejected": False}
    return {"verdict": SUPPORTED, "reason": f"{base}; the two header mutations are the hardest{note}", "rejected": False}


def verdict_h13(res: dict, pe: str) -> dict:
    raising, silent = _get(res, f"h13.diff|raising|all|{pe}"), _get(res, f"h13.diff|silent|all|{pe}")
    if raising is None:
        return {"verdict": NOT_TESTABLE, "reason": f"needs terse tasks and the same base x mutation pairs in the core set at effort {pe}"}
    text = f"verbose - terse on raising mutations = {raising[0]:+.1f} points (95% CI {raising[1]:+.1f} to {raising[2]:+.1f})"
    if raising[0] < 5:
        return {"verdict": NOT_SUPPORTED, "reason": f"{text}; the rejection rule (difference < 5 points on raising mutations) holds", "rejected": True}
    if silent is None:
        return {"verdict": NOT_TESTABLE, "reason": f"{text}; no silent-mutation pairs, so the second half of the rule cannot be judged"}
    text += f"; on silent mutations {silent[0]:+.1f} points (95% CI {silent[1]:+.1f} to {silent[2]:+.1f})"
    if raising[0] >= 15 and abs(silent[0]) < 5:
        note = "" if _clears(raising, 15.0, ">=") else "; the raising CI does not clear 15 points"
        return {"verdict": SUPPORTED, "reason": text + note, "rejected": False}
    return {"verdict": NOT_SUPPORTED, "reason": text + "; the thresholds (>= 15 raising and < 5 silent) are not both met", "rejected": False}


def verdict_h14() -> dict:
    """Not tested: H14 compares xhigh with high, and the xhigh level was dropped."""
    return {"verdict": NOT_TESTED, "reason": "not tested: " + DROPPED_NOTE}


def verdict_h15(res: dict, pe: str) -> dict:
    diff = _get(res, f"h15.diff|all|{pe}")
    if diff is None:
        return {"verdict": NOT_TESTABLE, "reason": f"needs compound tasks and the single-change core tasks of their mutations at effort {pe}"}
    comp, exp = _get(res, f"h15.compound|all|{pe}"), _get(res, f"h15.expected|all|{pe}")
    text = (f"compound fully repaired {comp[0]:.1f}% vs product of singles {exp[0]:.1f}%: difference {diff[0]:+.1f} points "
            f"(95% CI {diff[1]:+.1f} to {diff[2]:+.1f})")
    if diff[0] >= 0:
        return {"verdict": NOT_SUPPORTED, "reason": f"{text}; the rejection rule (compound >= product) holds", "rejected": True}
    if diff[0] <= -10:
        note = "" if _clears(diff, -10.0, "<=") else "; the CI does not clear -10 points"
        return {"verdict": SUPPORTED, "reason": text + note, "rejected": False}
    return {"verdict": NOT_SUPPORTED, "reason": f"{text}; the gap is smaller than 10 points", "rejected": False}


# ------------------------------------------------------------------ report


def fmt(est: Est | None, digits: int = 1, signed: bool = False) -> str:
    if est is None or est[0] is None:
        return "n/a"
    p, lo, hi = est
    sign = "+" if signed else ""
    if math.isnan(lo):
        return f"{p:{sign}.{digits}f}"
    return f"{p:{sign}.{digits}f} [{lo:{sign}.{digits}f}, {hi:{sign}.{digits}f}]"


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|", *["| " + " | ".join(r) + " |" for r in rows], ""]


def count_cells(tasks: list[Task], efforts: list[str]) -> dict[str, tuple[int, int]]:
    """effort -> (core tasks with runs, min runs per core cell)."""
    out = {}
    for e in efforts:
        runs = [len(t.runs[e]) for t in tasks if is_core(t) and t.runs.get(e)]
        out[e] = (len(runs), min(runs) if runs else 0)
    return out


def render_summary(res: dict, tasks: list[Task], efforts: list[str], verdicts: dict, sources: list[str], n_boot: int, seed: int, pe: str,
                   api_errors: dict[str, int] | None = None) -> str:
    get = lambda k: res.get(k)  # noqa: E731
    sets = {s: sum(1 for t in tasks if t.kind == "repair" and t.set == s) for s in ("core", "terse", "evidence", "compound")}
    n_scratch = sum(1 for t in tasks if t.kind == "scratch")
    cells = count_cells(tasks, efforts)
    L = ["# Case 3: self-healing clients, analysis", ""]
    L += [
        f"Inputs: {', '.join(sources)}. Tasks: " + ", ".join(f"{s} {n}" for s, n in sets.items()) + f", from-scratch twins {n_scratch}. Efforts: {', '.join(efforts)}.",
        f"Every number is a mean over tasks (a task's score is the mean over its runs). Brackets are 95% CIs from a cluster bootstrap "
        f"({n_boot} resamples, seed {seed}); clusters are the task rows, so a task and its variants are resampled together. "
        "Pass rates are percentages (points); `pass` is the v2 `pass_rate`.", "",
    ]
    if api_errors and any(api_errors.values()):
        parts = [f"{e} {api_errors[e]}" for e in [*efforts, *sorted(set(api_errors) - set(efforts))] if api_errors.get(e)]
        L += [f"api_error rows excluded: {', '.join(parts)} (failed model calls, not counted as 0).", ""]
    L += ["## Verdicts on the pre-registered hypotheses", ""]
    rows = [[h, PREREG[h]["title"], verdicts[h]["verdict"].upper()] for h in ("H12", "H13", "H14", "H15")]
    L += table(["ID", "Hypothesis", "Verdict"], rows)
    L += [f"H12, H13 and H15 use effort `{pe}` (pre-registered: Beam high). A verdict uses the point estimate against the "
          "pre-registered threshold; the CI is given next to it, and a note says when the CI does not clear the threshold. "
          "`not supported` includes the case where the rejection rule holds.", ""]

    L += ["## Effort curve (core tasks)", ""]
    rows = []
    for e in efforts:
        n, runs = cells[e]
        rows.append([e, str(n), str(runs), fmt(get(f"core.pass|{e}")), fmt(get(f"core.full|{e}")), fmt(get(f"core.tok|{e}"), 0)])
    L += table(["effort", "tasks", "min runs per task", "mean pass rate (points)", "fully repaired (share of attempts, %)", "mean output tokens (reasoning included)"], rows)
    if len(efforts) > 1:
        rows = []
        for a, b in zip(efforts, efforts[1:]):
            rows.append([f"{a} -> {b}", fmt(get(f"adj.gain|{a}>{b}"), 1, True), fmt(get(f"adj.dtok|{a}>{b}"), 0, True), fmt(get(f"adj.per1k|{a}>{b}"), 2, True)])
        L += ["Gain between adjacent efforts, on the tasks that have both:", ""]
        L += table(["step", "points gained", "extra output tokens", "points per 1K extra tokens"], rows)
        L += ["Points per 1K extra tokens is the mean gain divided by the mean extra tokens, shown only when the extra tokens are positive.", ""]

    L += ["## Mean pass rate by mutation and effort (core tasks, points)", ""]
    present = [m for m in MUTATION_NAMES if any(get(f"mut.pass|{m}|{e}") and get(f"mut.pass|{m}|{e}")[0] is not None for e in efforts)]
    rows = [[m, *[fmt(get(f"mut.pass|{m}|{e}"), 1) for e in efforts]] for m in present]
    L += table(["mutation", *efforts], rows)

    L += ["## Regression and patch size by effort (core tasks)", "",
          "Regression rate = share of the hidden tests that `client_v1.py` passed on v2 and the patched client fails. "
          "Patch size = changed lines / AST nodes against `client_v1.py` (reported, not rewarded). Attempts without a client are left out of these three means.", ""]
    rows = [[e, fmt(get(f"core.reg|{e}"), 1), fmt(get(f"core.lines|{e}"), 1), fmt(get(f"core.nodes|{e}"), 1)] for e in efforts]
    L += table(["effort", "regression rate (% of tests)", "patch lines", "patch AST nodes"], rows)

    L += _section_h12(res, efforts, verdicts["H12"], pe)
    L += _section_h13(res, efforts, verdicts["H13"], pe)
    L += _section_h14(verdicts["H14"])
    L += _section_h15(res, efforts, verdicts["H15"], pe)
    L += _section_identifiable(res, efforts)
    L += _section_evidence(res, efforts)
    return "\n".join(L).rstrip() + "\n"


def _verdict_block(h: str, v: dict) -> list[str]:
    return [f"## {h}: {PREREG[h]['title']}", "", f"**{v['verdict'].upper()}.** {v['reason']}", "",
            f"- Supported if: {PREREG[h]['supported']}", f"- Rejected if: {PREREG[h]['rejected']}", ""]


def _section_h12(res, efforts, v, pe) -> list[str]:
    L = _verdict_block("H12", v)
    rows = [[e, fmt(res.get(f"h12.repair|{e}")), fmt(res.get(f"h12.build|{e}")), fmt(res.get(f"h12.diff|{e}"), 1, True)] for e in efforts if res.get(f"h12.diff|{e}", (None,))[0] is not None]
    if rows:
        L += ["Paired by v2 config (the same API built from scratch is the twin):", ""]
        L += table(["effort", "repair (points)", "build from scratch (points)", "repair - build"], rows)
        mrows = [[m, fmt(res.get(f"mut.pass|{m}|{pe}")), fmt(res.get(f"h12.mut|{m}|{pe}"), 1, True)] for m in MUTATION_NAMES if res.get(f"h12.mut|{m}|{pe}", (None,))[0] is not None]
        L += [f"By mutation at `{pe}` (hardest first for repair):", ""]
        mrows.sort(key=lambda r: float(r[1].split()[0]) if r[1] != "n/a" else 101.0)
        L += table(["mutation", "repair pass rate", "repair - build"], mrows)
    return L


def _section_h13(res, efforts, v, pe) -> list[str]:
    L = _verdict_block("H13", v)
    rows = []
    for g in ("raising", "silent", "all"):
        for e in efforts:
            if res.get(f"h13.diff|{g}|all|{e}", (None,))[0] is not None:
                rows.append([g, e, fmt(res.get(f"h13.verbose|{g}|all|{e}")), fmt(res.get(f"h13.terse|{g}|all|{e}")), fmt(res.get(f"h13.diff|{g}|all|{e}"), 1, True)])
    if rows:
        L += ["Same base x mutation pairs, verbose (core) vs terse errors. Raising: header, auth, etag, expiry. Silent: paging, envelope.", ""]
        L += table(["mutations", "effort", "verbose (points)", "terse (points)", "verbose - terse"], rows)
        by_id = [[flag, fmt(res.get(f"h13.diff|{g}|{flag}|{pe}"), 1, True)] for g in ("raising",) for flag in ("yes", "no") if res.get(f"h13.diff|{g}|{flag}|{pe}", (None,))[0] is not None]
        if by_id:
            L += [f"Raising-mutation difference at `{pe}` split by `identifiable`:", ""] + table(["identifiable", "verbose - terse"], by_id)
    return L


def _section_h14(v) -> list[str]:
    return _verdict_block("H14", v) + ["The effort curve and the points-per-1K-token table above hold the numbers.", ""]


def _section_h15(res, efforts, v, pe) -> list[str]:
    L = _verdict_block("H15", v)
    rows = []
    for flag, label in (("all", "all"), ("yes", "identifiable"), ("no", "not identifiable")):
        for e in efforts:
            if res.get(f"h15.diff|{flag}|{e}", (None,))[0] is not None:
                rows.append([label, e, fmt(res.get(f"h15.compound|{flag}|{e}")), fmt(res.get(f"h15.expected|{flag}|{e}")), fmt(res.get(f"h15.diff|{flag}|{e}"), 1, True)])
    if rows:
        L += ["Share of compound tasks with pass_rate = 1.0, against the mean over compound tasks of (full-repair share of single change 1) x (share of single change 2). "
              "The single shares come from the core tasks of those mutations (the compound tasks use new bases, so the singles are not on exactly the same bases).", ""]
        L += table(["compound tasks", "effort", "fully repaired (%)", "product of singles (%)", "compound - product"], rows)
    return L


def _section_identifiable(res, efforts) -> list[str]:
    rows = []
    for s in ("core", "terse", "evidence", "compound"):
        for flag, label in (("yes", "yes"), ("no", "no")):
            for e in efforts:
                if res.get(f"set.pass|{s}|{flag}|{e}", (None,))[0] is not None:
                    rows.append([s, label, e, fmt(res.get(f"set.pass|{s}|{flag}|{e}")), fmt(res.get(f"set.full|{s}|{flag}|{e}"))])
    if not rows:
        return []
    return ["## Results split by `identifiable`", "",
            "`identifiable = no` means the visible evidence cannot show a fact the fix needs (see `heal/identify.py`).", ""] + table(
        ["set", "identifiable", "effort", "mean pass rate (points)", "fully repaired (%)"], rows)


def _section_evidence(res, efforts) -> list[str]:
    rows = []
    for ev in ("failure_only", "recording_only"):
        for e in efforts:
            if res.get(f"ev.diff|{ev}|all|{e}", (None,))[0] is not None:
                rows.append([ev, e, fmt(res.get(f"ev.pass|{ev}|all|{e}")), fmt(res.get(f"ev.diff|{ev}|all|{e}"), 1, True)])
    if not rows:
        return []
    return ["## Evidence channel (exploratory, no pre-registered hypothesis)", "",
            "One evidence file removed, same base x mutation pairs. Difference = core (both channels) minus the one-channel task.", ""] + table(
        ["evidence shown", "effort", "pass rate (points)", "core - one channel"], rows)


# ------------------------------------------------------------------ main


def analyze(tasks: list[Task], n_boot: int, seed: int, primary: str) -> tuple[dict, list[str], dict]:
    efforts = efforts_of(tasks)
    sampler = Sampler(tasks)

    def stats_fn(views):
        s = stats(views, efforts)
        core = [v for v in views if is_core(v.task)]
        if "low" in efforts and "high" in efforts:  # the low -> high rise (efforts that are not adjacent)
            adj = _adjacent(core, "low", "high")
            s["gain.low>high"], s["tokratio.low>high"] = adj["adj.gain|low>high"], adj["adj.tokratio|low>high"]
        return s

    res = bootstrap(sampler, stats_fn, n_boot, seed)
    verdicts = {
        "H12": verdict_h12(res, primary), "H13": verdict_h13(res, primary), "H14": verdict_h14(),
        "H15": verdict_h15(res, primary),
    }
    return res, efforts, verdicts


def write_outputs(out: Path, res: dict, summary: str, verdicts: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key", "estimate", "ci_low", "ci_high"])
        for key in sorted(res):
            p, lo, hi = res[key]
            writer.writerow([key, "" if p is None else round(p, 6), "" if math.isnan(lo) else round(lo, 6), "" if math.isnan(hi) else round(hi, 6)])
    (out / "hypotheses.json").write_text(json.dumps({h: {**v, "supported_if": PREREG[h]["supported"], "rejected_if": PREREG[h]["rejected"]} for h, v in verdicts.items()}, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scored", nargs="+", type=Path, required=True, help="scored CSV(s) from score_heal.py")
    p.add_argument("--scratch", nargs="*", type=Path, default=[], help="results CSV(s) of the from-scratch twins (runs/heal48_scratch)")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--configs", type=Path, default=DEFAULT_CONFIGS)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--primary-effort", default=PRIMARY_EFFORT, help="effort for H12, H13, H15 (default high)")
    p.add_argument("--reasoning-separate", action="store_true", help="add reasoning_tokens to completion_tokens (default: completion_tokens already includes reasoning)")
    p.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args(argv)

    rows = read_csvs([*args.scored, *args.scratch])
    api_errors: dict[str, int] = {}
    tasks = load_tasks(rows, read_jsonl(args.manifest), read_jsonl(args.configs), args.reasoning_separate, api_errors)
    if not tasks:
        print("error: no rows in the input CSVs", file=sys.stderr)
        return 2
    res, efforts, verdicts = analyze(tasks, args.n_boot, args.seed, args.primary_effort)
    summary = render_summary(res, tasks, efforts, verdicts, [str(p) for p in [*args.scored, *args.scratch]], args.n_boot, args.seed, args.primary_effort, api_errors)
    write_outputs(args.out, res, summary, verdicts)
    print(f"wrote {args.out / 'summary.md'}; " + ", ".join(f"{h} {v['verdict']}" for h, v in verdicts.items()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
