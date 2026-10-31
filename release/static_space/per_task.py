"""Per-task scores of the 7 settings x 5 benchmarks, for the task explorer of the static Space. Standard library only.

The loading rules are not re-implemented here: scores come from `har2client/scripts/analyze_models.py` (`load_beam`, `load_models`),
the same functions that write `results/analysis/model_comparison/summary.json`, so a per-task number is the one behind the
leaderboard cell (Beam high on Case 1 and Case 3 is the per-task mean of 3 runs; Case 2 arms are mapped by `CASE2_ARMS`).
The most common failure kind of a cell is read from the `fail_*` columns of the same rows, through the same helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path

from h2c_release import H2C_DIR, RESULTS_DIR

sys.path.insert(0, str(H2C_DIR / "scripts"))
import analyze_models as am  # noqa: E402
import high_runs  # noqa: E402

MODELS_ROOT = RESULTS_DIR / "model_comparison"
MODELS_FILE = H2C_DIR / "data" / "comparison_models.json"
SCORE_DECIMALS = 1
MEAN_TOLERANCE = 0.05  # points: the shipped per-task scores must average to the leaderboard pass rate within this
# Which benchmarks a task of each dataset suite has results for (benchmark ids of build.BENCHMARKS).
BENCH_BY_SUITE = {"tier_b": ("c1", "c2a", "c2b"), "heal_v3": ("c3a", "c3b"), "heal_v3_hint": ("c3a", "c3b")}


def load_entries() -> dict[tuple[str, str], am.Entry]:
    entries = am.load_beam(RESULTS_DIR) + am.load_models(MODELS_ROOT, MODELS_FILE)
    return {(e.key, e.suite): e for e in entries}


def entry_rows(entry: am.Entry) -> list[dict]:
    """The scored (not api_error) CSV rows behind an entry, joined across runs the way `load_beam` joins them."""
    if entry.kind == "beam":
        arm = am.CASE2_ARMS.get(entry.suite)
        path = am.beam_sources(RESULTS_DIR)[(entry.suite, entry.effort)]
        rows = am._read_rows(path, entry.effort, arm)
        repeats = am.beam_repeats(RESULTS_DIR, entry.suite, entry.effort)
        if repeats:
            rows = high_runs.merge_runs([rows, *(am._read_rows(p, entry.effort, arm) for p in repeats)])
    else:
        rows = am._read_rows(MODELS_ROOT / entry.key / entry.suite / "results.csv", None, None)
    return [r for r in rows if r.get("status") != am.API_ERROR]


def _count(value: object) -> int:
    n = am._num(value)
    return int(n) if n else 0


def failure_kinds(rows: list[dict]) -> dict[str, str]:
    """task -> most common failure kind over its rows (summed `fail_*` counts), or the non-ok status if no test failed that way."""
    totals: dict[str, dict[str, int]] = {}
    status: dict[str, str] = {}
    for row in rows:
        counts = totals.setdefault(row["task"], {})
        for col, val in row.items():
            if col.startswith("fail_") and _count(val):
                label = col[len("fail_"):].replace("_", " ")
                counts[label] = counts.get(label, 0) + _count(val)
        if row.get("status") not in (None, "", "ok"):
            status[row["task"]] = row["status"].replace("_", " ")
    out = {}
    for task, counts in totals.items():
        kind = max(sorted(counts), key=counts.get) if counts else status.get(task)
        if kind:
            out[task] = kind
    return out


def build_per_task(setting_keys: list[str], bench: list[dict]) -> dict:
    """{"settings": keys, "fail_kinds": labels, "scores": {bench id: {task: [one score or None per setting]}},
    "fails": {bench id: {task: [index into fail_kinds or -1 per setting]}}}. Scores are pass %, 0 to 100."""
    entries = load_entries()
    kinds: list[str] = []
    scores: dict[str, dict[str, list]] = {}
    fails: dict[str, dict[str, list]] = {}
    for b in bench:
        per_setting_scores, per_setting_fail = [], []
        for key in setting_keys:
            entry = entries.get((key, b["suite"]))
            if entry is None:
                raise SystemExit(f"error: no scored entry for {key} / {b['suite']} (analyze_models loaders)")
            per_setting_scores.append(entry.scores)
            fk = failure_kinds(entry_rows(entry))
            per_setting_fail.append({t: fk.get(t) for t in entry.scores if entry.scores[t] < 1.0 - am.SOLVED_EPS})
        tasks = sorted(set().union(*per_setting_scores))
        scores[b["id"]], fails[b["id"]] = {}, {}
        for t in tasks:
            scores[b["id"]][t] = [round(s[t] * am.PCT, SCORE_DECIMALS) if t in s else None for s in per_setting_scores]
            row = []
            for f in per_setting_fail:
                kind = f.get(t)
                if kind and kind not in kinds:
                    kinds.append(kind)
                row.append(kinds.index(kind) if kind else -1)
            fails[b["id"]][t] = row
    return {"settings": list(setting_keys), "fail_kinds": kinds, "scores": scores, "fails": fails}


def attach(tasks: list[dict], per_task: dict) -> None:
    """Add `results` {bench id: [score or None per setting]} and `fails` (same shape, only if a cell has a failure kind) to each task."""
    seen: dict[str, set[str]] = {b: set() for b in per_task["scores"]}
    for t in tasks:
        res, fl = {}, {}
        for b in BENCH_BY_SUITE.get(t["suite"], ()):
            row = per_task["scores"][b].get(t["id"])
            if row is None:
                continue
            seen[b].add(t["id"])
            res[b] = row
            if any(i >= 0 for i in per_task["fails"][b][t["id"]]):
                fl[b] = per_task["fails"][b][t["id"]]
        if res:
            t["results"] = res
        if fl:
            t["fails"] = fl
    for b, ids in seen.items():
        missing = sorted(set(per_task["scores"][b]) - ids)
        if missing:
            raise SystemExit(f"error: scored tasks of {b} not in the task explorer data: {missing[:3]}")


def check_means(tasks: list[dict], per_task: dict, comparison: dict, bench: list[dict]) -> int:
    """Acceptance check on the data as shipped: the mean over tasks of each setting's per-task score equals the leaderboard pass rate."""
    by = {(r["key"], r["suite"]): r["pass_rate"] for r in comparison["rows"]}
    checked = 0
    for b in bench:
        for i, key in enumerate(per_task["settings"]):
            rows = {t["id"]: t["results"][b["id"]] for t in tasks if b["id"] in t.get("results", {})}  # Case 3 ids repeat in two suites
            vals = [r[i] for r in rows.values() if r[i] is not None]
            mean = sum(vals) / len(vals)
            want = by[(key, b["suite"])]
            if len(vals) != b["n"] or abs(mean - want) > MEAN_TOLERANCE:
                raise SystemExit(f"error: per-task scores of {key} / {b['suite']}: n={len(vals)}, mean {mean:.3f}, leaderboard {want:.3f}")
            checked += 1
    return checked
