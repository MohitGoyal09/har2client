"""Build every table and sentence the Space shows. Every number comes from the files in `data/` (copies of the
committed `har2client/results/` CSVs and analysis files); nothing is typed in by hand. Pure standard library + pandas.
"""

from __future__ import annotations

import csv
import json
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import stats

HERE = Path(__file__).resolve().parent
REPO_RESULTS = HERE.parent.parent / "har2client" / "results"
REPO_CONFIGS = HERE.parent.parent / "har2client" / "data" / "tierb_configs.jsonl"


def data_path(*parts: str) -> Path:
    """Bundled copy (data/, written by sync_space_data.py) first; the repo's results folder when run from a checkout."""
    bundled = HERE / "data"
    if (bundled / "results").is_dir():
        return bundled.joinpath(*parts)
    if parts[0] == "results":
        return REPO_RESULTS.joinpath(*parts[1:])
    if parts[0] == "analysis":
        return (REPO_RESULTS / "analysis").joinpath(*parts[1:])
    if parts[0] == "tierb_configs.jsonl":
        return REPO_CONFIGS
    return REPO_RESULTS.joinpath(*parts)


SYSTEM = "Beam-1.0-501B-A23B"
_V3_EFFORTS = ("low", "medium", "high")
# High effort is the mean of 3 runs per task (PREREG.md deviation 2026-10-27): runs 2 and 3 are the `_r2` and `_r3` CSVs.
_HIGH_REPEATS = ("_r2", "_r3")
# tier key -> (folder, CSV files (one per effort step, merged), label). Case 3 v3 has one CSV per arm and effort.
TIERS = {
    "A": ("results", ("set30_v1_tierA.csv",), "Tier A: 30 public APIs"),
    "B": ("results", ("tierb50_v1.csv", "tierb50_medium.csv", "tierb50_high_r2.csv", "tierb50_high_r3.csv"), "Tier B: 50 invented stateful APIs"),
    "C-base": ("results", tuple(f"heal_v3_eval_base_{e}.csv" for e in _V3_EFFORTS) + tuple(f"heal_v3_eval_base_high{r}.csv" for r in _HIGH_REPEATS), "Case 3 v3, base prompt: 64 repair tasks"),
    "C-hint": ("results", tuple(f"heal_v3_eval_hint_{e}.csv" for e in _V3_EFFORTS) + tuple(f"heal_v3_eval_hint_high{r}.csv" for r in _HIGH_REPEATS), "Case 3 v3, with hint: 64 repair tasks"),
}
A_PUBLISHED_LABEL = "Tier A: 27 published tasks (3 restricted-licence tasks left out)"
FLOOR_JSON = ("results", "heal_v3_eval_v1_floor.json")
HEAL_SUMMARY = ("analysis", "heal_v3_eval", "summary.json")
CONTROLS_SUMMARY = ("analysis", "heal_controls", "summary.json")
FAMILIES = ("single", "release3", "release4", "partial", "sdk", "misleading", "terse")
VARIANTS_CSV = ("results", "set30v_high_partial.csv")
# Display names for the Tier A control variants. "reworded" is the first, flawed version (names only; PREREG.md, Deviations).
VARIANT_LABELS = {
    "exact": "exact names (original task)",
    "opaque": "opaque names, with argument descriptions",
    "reworded": "reworded names only (flawed design, not used for H6)",
    "twin": "renamed twin (API surface renamed)",
    "closed": "closed book (goal only, empty recording)",
}
SWITCHES = ("auth", "pagination", "token_expiry", "etag_update", "hidden_header", "envelope")
# switch -> (reference value, other values), as in the committed analysis (summary.md).
SWITCH_LEVELS = {
    "auth": ("apikey_header", ("bearer_login", "cookie_csrf")),
    "pagination": ("none", ("offset", "cursor", "link_header")),
    "token_expiry": ("off", ("on",)),
    "etag_update": ("off", ("on",)),
    "hidden_header": ("off", ("on",)),
    "envelope": ("off", ("on",)),
}


def r1(x: float) -> float:
    """Round to one decimal, halves up (96.25 -> 96.3), so tables agree with how people round by hand."""
    return float(Decimal(repr(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def fmt_ci(ci: tuple[float, float], digits: int = 1) -> str:
    return f"[{ci[0]:,.{digits}f}, {ci[1]:,.{digits}f}]"


def load_merged(names: tuple[str, ...], folder: str = "results") -> stats.Runs:
    """Runs of several CSVs (one per effort step) merged into one task -> effort -> runs table."""
    merged: stats.Runs = {}
    for name in names:
        for task, per_effort in stats.load_runs(data_path(folder, name)).items():
            for effort, runs in per_effort.items():
                merged.setdefault(task, {}).setdefault(effort, []).extend(runs)
    return merged


def load_families(names: tuple[str, ...]) -> dict[str, dict[str, list[float]]]:
    """effort -> family -> pass rates of that family's tasks (case 3 CSVs carry a `set` column)."""
    out: dict[str, dict[str, list[float]]] = {}
    for name in names:
        with open(data_path("results", name), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rate = float(r["pass_rate"]) if r.get("status", "ok") == "ok" and r.get("pass_rate") else 0.0
                out.setdefault(r["effort"], {}).setdefault(r["set"], []).append(rate)
    return out


def csv_meta(path: Path) -> dict:
    with open(path, newline="", encoding="utf-8") as fh:
        first = next(csv.DictReader(fh))
    return {"har_mode": first.get("har_mode", "")}


@dataclass
class Bundle:
    runs: dict[str, stats.Runs]
    summaries: dict[str, dict[str, dict]]
    contrasts: dict[str, dict | None]
    configs: dict[str, dict]
    variants: stats.Runs
    tasks: dict[str, dict]
    audit_md: str
    coefficients: pd.DataFrame
    har_mode: str
    published_a: dict[str, dict]  # Tier A summary over the tasks that are in the published dataset (empty without tasks.json)
    families: dict[str, dict[str, dict[str, list[float]]]]  # tier key -> effort -> family -> pass rates
    heal_summary: dict
    controls: dict  # analysis/heal_controls/summary.json: controls, repeat runs, H10
    floor: dict


def load_bundle() -> Bundle:
    runs, summaries, contrasts = {}, {}, {}
    har_mode = ""
    for tier, (folder, names, _label) in TIERS.items():
        runs[tier] = load_merged(names, folder)
        summaries[tier] = stats.effort_summary(runs[tier])
        contrasts[tier] = stats.effort_contrast(runs[tier])
        har_mode = csv_meta(data_path(folder, names[0]))["har_mode"]
    configs = {}
    for line in data_path("tierb_configs.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        configs[row["task_id"]] = row["config"]
    tasks_file = data_path("tasks.json")
    tasks = json.loads(tasks_file.read_text(encoding="utf-8")) if tasks_file.is_file() else {}
    published = [t for t, rec in tasks.items() if rec["tier"] == "A" and t in runs["A"]]
    return Bundle(
        published_a=stats.effort_summary(runs["A"], published) if published else {},
        families={t: load_families(TIERS[t][1]) for t in ("C-base", "C-hint")},
        heal_summary=json.loads(data_path(*HEAL_SUMMARY).read_text(encoding="utf-8")),
        controls=json.loads(data_path(*CONTROLS_SUMMARY).read_text(encoding="utf-8")),
        floor=json.loads(data_path(*FLOOR_JSON).read_text(encoding="utf-8"))["summary"],
        runs=runs, summaries=summaries, contrasts=contrasts, configs=configs,
        variants=stats.load_runs(data_path(*VARIANTS_CSV)), tasks=tasks,
        audit_md=data_path("grader_audit_set30.md").read_text(encoding="utf-8"),
        coefficients=pd.read_csv(data_path("analysis", "tierb50_v1", "coefficients.csv")),
        har_mode=har_mode,
    )


# ---------------------------------------------------------------- leaderboard


def leaderboard(b: Bundle, tier: str = "all", effort: str = "all") -> pd.DataFrame:
    rows = []
    blocks = [(t, label, b.summaries[t], ", ".join(names)) for t, (_f, names, label) in TIERS.items()]
    if b.published_a:  # the 27 tasks that are in the dataset; the 30-task rows above are the published results
        blocks.insert(1, ("A", A_PUBLISHED_LABEL, b.published_a, TIERS["A"][1][0] + " (27 tasks)"))
    for t, label, summary, source in blocks:
        if tier not in ("all", t):
            continue
        for e, s in summary.items():
            if effort not in ("all", e):
                continue
            lo, hi = s["runs_per_task"]
            rows.append({
                "System": f"{SYSTEM} ({'bare prompt + contract hint' if t == 'C-hint' else 'bare prompt'}, {b.har_mode} HAR)",
                "Tier": label,
                "Effort": e,
                "Tasks": s["n_tasks"],
                "Runs / task": str(lo) if lo == hi else f"{lo}-{hi}",
                "Mean pass rate (%)": r1(s["mean_pct"]),
                "95% CI (%)": fmt_ci(s["ci_pct"]),
                "Perfect tasks": f"{s['perfect']}/{s['n_tasks']}",
                "Output tokens / run": round(s["out_tokens"]),
                "Prompt tokens / run": round(s["prompt_tokens"]),
                "Source": source,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- findings


def switch_groups(b: Bundle, switch: str) -> dict[str, list[str]]:
    ref, others = SWITCH_LEVELS[switch]
    groups = {ref: [t for t, c in b.configs.items() if c[switch] == ref]}
    for o in others:
        groups[o] = [t for t, c in b.configs.items() if c[switch] == o]
    return groups


def switch_table(b: Bundle) -> pd.DataFrame:
    """Marginal mean pass rate of Tier B tasks, one row per switch value and effort (other switches vary freely)."""
    rows = []
    for sw in SWITCHES:
        groups = switch_groups(b, sw)
        for effort in stats.efforts_of(b.runs["B"]):
            res = stats.group_means(b.runs["B"], groups, effort)
            ref = SWITCH_LEVELS[sw][0]
            for value, r in res.items():
                rows.append({
                    "Switch": sw, "Value": value + (" (reference)" if value == ref else ""), "Effort": effort, "Tasks": r["n_tasks"],
                    "Mean pass rate (%)": r1(r["mean_pct"]), "95% CI (%)": fmt_ci(r["ci_pct"]),
                    "Change vs reference (points)": "" if value == ref else f"{r['mean_pct'] - res[ref]['mean_pct']:+.1f}",
                })
    return pd.DataFrame(rows)


def header_result(b: Bundle) -> dict[str, dict[str, dict]]:
    """effort -> {'off': stats, 'on': stats} for the hidden-header switch."""
    groups = switch_groups(b, "hidden_header")
    return {e: stats.group_means(b.runs["B"], groups, e) for e in stats.efforts_of(b.runs["B"])}


def coefficient_table(b: Bundle) -> pd.DataFrame:
    df = b.coefficients
    df = df[(df["model"] == "per_effort") & (~df["term"].str.startswith(("domain=", "intercept")))]
    out = pd.DataFrame({
        "Effort": df["effort"], "Switch turned on": df["term"],
        "Effect (points)": df["estimate_pp"].map("{:+.1f}".format),
        "95% CI (points)": [fmt_ci((lo, hi)) for lo, hi in zip(df["ci_low_pp"], df["ci_high_pp"])],
        "CI excludes 0": ["yes" if (lo > 0 or hi < 0) else "no" for lo, hi in zip(df["ci_low_pp"], df["ci_high_pp"])],
    })
    return out.reset_index(drop=True)


def effort_table(b: Bundle) -> pd.DataFrame:
    rows = []
    for t, (_f, _n, label) in TIERS.items():
        for e, s in b.summaries[t].items():
            rows.append({"Tier": label, "Effort": e, "Mean pass rate (%)": r1(s["mean_pct"]),
                         "Output tokens / run": round(s["out_tokens"]), "of which reasoning": round(s["reasoning_tokens"]),
                         "Prompt tokens / run": round(s["prompt_tokens"])})
    return pd.DataFrame(rows)


def effort_sentences(b: Bundle) -> list[str]:
    out = []
    for t, (_f, _n, label) in TIERS.items():
        c = b.contrasts[t]
        if not c:
            continue
        out.append(
            f"- {label}: high minus low is {c['gain_pp']:+.1f} points {fmt_ci(c['gain_ci'])} for "
            f"{c['extra_out_tokens']:,.0f} extra output tokens per run {fmt_ci(c['extra_out_ci'], 0)} "
            f"(paired over {c['n_tasks']} tasks, 95% bootstrap interval).")
    return out


def variant_table(b: Bundle) -> pd.DataFrame:
    """Tier A name and memory controls (partial run). Each variant is compared with the exact tasks it shares."""
    runs = b.variants
    by_variant: dict[str, dict[str, float]] = {}
    for task, per in runs.items():
        base, _, variant = task.partition("__")
        by_variant.setdefault(variant or "exact", {})[base] = stats.mean(r.pass_rate for r in per["high"])
    exact = by_variant.get("exact", {})
    rows = []
    for name, vals in by_variant.items():
        shared = [t for t in vals if t in exact]
        rows.append({
            "Variant": VARIANT_LABELS.get(name, name), "Tasks run": len(vals), "Mean pass rate (%)": r1(stats.mean(vals.values()) * 100),
            "Tasks shared with exact": len(shared),
            "Exact on those tasks (%)": "" if name == "exact" or not shared else r1(stats.mean(exact[t] for t in shared) * 100),
            "Variant on those tasks (%)": "" if name == "exact" or not shared else r1(stats.mean(vals[t] for t in shared) * 100),
        })
    order = {label: i for i, label in enumerate(VARIANT_LABELS.values())}
    return pd.DataFrame(sorted(rows, key=lambda r: order.get(r["Variant"], 99)))


def audit_section(b: Bundle) -> str:
    """The grader audit's oracle, trivial-agent and kill-rate sections, verbatim from the committed audit file."""
    head = b.audit_md.split("## Survivors")[0]
    return head.replace("# Grader-validity audit", "").replace("## ", "#### ")


# ---------------------------------------------------------------- case 3


def case3_table(b: Bundle) -> pd.DataFrame:
    """Case 3 v3 (64 tasks): one row per arm and effort. Full repair = pass rate 1.0 (all 17 tests)."""
    rows = []
    for t, arm in (("C-base", "base (goal as built)"), ("C-hint", "hint (goal plus contract paragraph)")):
        for e, s in b.summaries[t].items():
            rows.append({"Arm": arm, "Effort": e, "Mean pass rate (%)": r1(s["mean_pct"]), "95% CI (%)": fmt_ci(s["ci_pct"]),
                         "Full repair": f"{s['perfect']}/{s['n_tasks']}", "Output tokens / task": round(s["out_tokens"]),
                         "Source": ", ".join(n for n in TIERS[t][1] if f"_{e}." in n or f"_{e}_r" in n)})
    return pd.DataFrame(rows)


def case3_gap_table(b: Bundle) -> pd.DataFrame:
    """Paired contrasts (points, 95% bootstrap CI over tasks), read from the committed analysis (analysis/heal_v3_eval/summary.json)."""
    h = b.heal_summary
    rows = [{"Contrast": f"hint minus base, {e} effort", "Points": f"{h['hint_minus_base'][e]['pass']['mean']:+.1f}",
             "95% CI": fmt_ci((h["hint_minus_base"][e]["pass"]["ci_low"], h["hint_minus_base"][e]["pass"]["ci_high"]))}
            for e in _V3_EFFORTS]
    for arm in ("base", "hint"):
        c = h["effort_contrasts"][f"{arm}: high - low"]
        rows.append({"Contrast": f"{arm} arm, high minus low effort (output tokens x{c['output_token_ratio']:.1f})",
                     "Points": f"{c['pass']['mean']:+.1f}", "95% CI": fmt_ci((c["pass"]["ci_low"], c["pass"]["ci_high"]))})
    return pd.DataFrame(rows)


def case3_controls_table(b: Bundle) -> pd.DataFrame:
    """Case 3 control arms at low effort: paired contrasts (points, 95% bootstrap CI over tasks), from analysis/heal_controls/summary.json."""
    verdicts = {v["id"]: v["status"] for v in b.controls["verdicts"]}
    note = {"placebo - base": f"H22 {verdicts['H22']}", "notice - base": f"H23 {verdicts['H23']}", "hint - placebo": f"H22 {verdicts['H22']}"}
    rows = [{"Contrast (low effort)": c["contrast"].replace(" - ", " minus "), "Points": f"{c['pass']['mean']:+.1f}",
             "95% CI": fmt_ci((c["pass"]["ci_low"], c["pass"]["ci_high"])), "Pre-registered test": note.get(c["contrast"], "")}
            for c in b.controls["contrasts"]]
    return pd.DataFrame(rows)


def case3_repeat_table(b: Bundle) -> pd.DataFrame:
    """Repeat runs: the second run of the low-effort base and hint cells, and the three runs at high (case 3 and Tier B).

    Flip rate = share of tasks whose full-repair status differs between runs (low: run 1 against run 2; high: averaged over run pairs)."""
    labels = {"base": "case 3 base, low", "hint": "case 3 hint, low", "base_high": "case 3 base, high",
              "hint_high": "case 3 hint, high", "tierb_high": "Tier B, high"}
    rows = [{"Arm": labels.get(arm, arm), "Run 1 mean (%)": r1(r["mean_r1"] * 100), "Run 2 mean (%)": r1(r["mean_r2"] * 100),
             "Run 3 mean (%)": r1(r["mean_r3"] * 100) if "mean_r3" in r else "",
             "Run 2 minus run 1": f"{r['r2_minus_r1']['mean']:+.1f}", "95% CI": fmt_ci((r["r2_minus_r1"]["ci_low"], r["r2_minus_r1"]["ci_high"])),
             "Tasks whose full-repair status flips (%)": r1(r["flip_rate"] * 100)}
            for arm, r in b.controls["repeat_runs"].items()]
    return pd.DataFrame(rows)


def h10_table(b: Bundle) -> pd.DataFrame:
    """H10: the 25 hidden-header Tier B tasks at high effort, as built against a hint and against evidence in the recording."""
    h = b.controls["h10"]
    labels = {"a": "as built", "b": "hint in the goal", "c": "failed call in the recording (evidence)"}
    rows = [{"Arm": labels[k], "Tasks": s["finished"], "Mean pass rate (%)": r1(s["pass_rate"] * 100)} for k, s in h["steps"].items()]
    return pd.DataFrame(rows)


def case3_family_table(b: Bundle) -> pd.DataFrame:
    """Mean pass rate (%) per task family, arm and effort. Families hold 8 to 14 tasks; no interval is drawn."""
    rows = []
    for fam in FAMILIES:
        row: dict = {"Family": fam}
        n = len(b.families["C-base"]["low"].get(fam, []))
        row["Tasks"] = n
        for t, arm in (("C-base", "base"), ("C-hint", "hint")):
            for e in _V3_EFFORTS:
                row[f"{arm} {e}"] = r1(stats.mean(b.families[t][e][fam]) * 100)
        rows.append(row)
    return pd.DataFrame(rows)


def case3_floor_sentence(b: Bundle) -> str:
    f = b.floor
    return (f"Floor, measured with no model call: the unchanged `client_v1.py` scores a mean of {f['v1_mean'] * 100:.1f}% on the "
            f"{f['n_tasks']} tasks (above 0 on {f['v1_tasks_above_zero']} tasks, best {f['v1_max'] * 100:.0f}%), and an empty client scores "
            f"{f['empty_mean'] * 100:.1f}%.")


# ---------------------------------------------------------------- model comparison


COMPARISON_SUMMARY = ("analysis", "model_comparison", "summary.json")
CASE2_CSV = ("results", "case2_probe", "main_full", "results.csv")
# suite -> column label. Case 2 is compared on the placebo (probe0) and P5 (probe5) arms only.
COMPARISON_SUITES = {"tierb50": "Case 1 (Tier B)", "v3base": "Case 3 base", "v3hint": "Case 3 + hint", "probe0": "Case 2 placebo", "probe5": "Case 2 P5"}
CASE2_ARMS = {"probe0": "placebo", "probe5": "P5"}
_BEAM_TIERS = {"tierb50": "B", "v3base": "C-base", "v3hint": "C-hint"}  # suite -> key in Bundle.summaries
# (key in summary.json, label); the key of a model setting is also its folder in results/model_comparison/.
COMPARISON_ROWS = (
    ("beam-low", "Beam (low)"), ("beam-medium", "Beam (medium)"), ("beam-high", "Beam (high)"),
    ("gpt-oss-120b-low", "GPT-OSS-120B (low)"), ("gpt-oss-120b-medium", "GPT-OSS-120B (medium)"), ("gpt-oss-120b-high", "GPT-OSS-120B (high)"),
    ("qwen3.5-397b-thinking", "Qwen3.5-397B-A17B (thinking on)"),
)


def _case2_cells() -> dict[tuple[str, str], tuple[float, float]]:
    """(arm, effort) -> (mean pass rate in points, mean output tokens) over the tasks of Beam's case 2 run (1 run per cell; no code counts as 0)."""
    per_cell: dict[tuple[str, str], list[tuple[float, float]]] = {}
    with open(data_path(*CASE2_CSV), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rate = stats._num(r.get("pass_rate")) if r.get("status") == "ok" else 0.0
            per_cell.setdefault((r["arm"], r["effort"]), []).append((rate, stats._num(r.get("completion_tokens"))))
    return {k: (stats.mean(v[0] for v in vals) * 100, stats.mean(v[1] for v in vals)) for k, vals in per_cell.items()}


def comparison_cells(b: Bundle) -> dict[tuple[str, str], dict]:
    """(row key, suite) -> {pass_pct, out_tokens}. Beam: the Beam CSVs (high on cases 1 and 3 is the mean of 3 runs) and case 2 main_full. Others: their CSVs."""
    cells: dict[tuple[str, str], dict] = {}
    case2 = _case2_cells()
    for key, _label in COMPARISON_ROWS:
        for suite in COMPARISON_SUITES:
            if key.startswith("beam-"):
                effort = key.removeprefix("beam-")
                if suite in _BEAM_TIERS:
                    s = b.summaries[_BEAM_TIERS[suite]][effort]
                    cells[(key, suite)] = {"pass_pct": s["mean_pct"], "out_tokens": s["out_tokens"]}
                else:
                    pct, tokens = case2[(CASE2_ARMS[suite], effort)]
                    cells[(key, suite)] = {"pass_pct": pct, "out_tokens": tokens}
                continue
            runs = stats.load_runs(data_path("results", "model_comparison", key, suite, "results.csv"))
            per_task = [stats.mean(r.pass_rate for r in per_effort_runs) for per_effort in runs.values() for per_effort_runs in per_effort.values()]
            tokens = [r.completion_tokens for per_effort in runs.values() for rs in per_effort.values() for r in rs]
            cells[(key, suite)] = {"pass_pct": stats.mean(per_task) * 100, "out_tokens": stats.mean(tokens)}
    return cells


def comparison_summary() -> dict:
    return json.loads(data_path(*COMPARISON_SUMMARY).read_text(encoding="utf-8"))


def _comparison_frame(cells: dict, value) -> pd.DataFrame:
    rows = []
    for key, label in COMPARISON_ROWS:
        rows.append({"Model (setting)": label, **{name: value(key, suite) for suite, name in COMPARISON_SUITES.items()}})
    return pd.DataFrame(rows)


def comparison_table(b: Bundle) -> pd.DataFrame:
    """Mean pass rate (%) of Beam, GPT-OSS-120B and Qwen3.5-397B-A17B on the five suites."""
    cells = comparison_cells(b)
    return _comparison_frame(cells, lambda k, s: r1(cells[(k, s)]["pass_pct"]))


def comparison_tokens_table(b: Bundle) -> pd.DataFrame:
    """Mean output tokens per call (reasoning included) for the same cells."""
    cells = comparison_cells(b)
    return _comparison_frame(cells, lambda k, s: round(cells[(k, s)]["out_tokens"]))


def comparison_gap_table() -> pd.DataFrame:
    """Difference from Beam (high) in points with the 95% bootstrap interval over tasks, from `analysis/model_comparison/summary.json`."""
    paired = {(r["key"], r["suite"]): r["paired_vs_beam_high"] for r in comparison_summary()["rows"]}

    def cell(key: str, suite: str) -> str:
        p = paired.get((key, suite))
        return "-" if not p else f"{p['diff']:+.1f} [{p['ci_low']:+.1f}, {p['ci_high']:+.1f}]"
    return _comparison_frame({}, cell)


def comparison_sentences(b: Bundle) -> list[str]:
    """One bullet per suite: GPT-OSS-120B (high) and Qwen3.5-397B-A17B against Beam (high), with the interval and whether it excludes 0."""
    cells = comparison_cells(b)
    paired = {(r["key"], r["suite"]): r["paired_vs_beam_high"] for r in comparison_summary()["rows"]}
    labels = dict(COMPARISON_ROWS)
    out = []
    for suite, name in COMPARISON_SUITES.items():
        parts = []
        for key in ("gpt-oss-120b-high", "qwen3.5-397b-thinking"):
            p = paired[(key, suite)]
            verdict = "interval excludes 0" if p["excludes_zero"] else "interval includes 0"
            parts.append(f"{labels[key]} {cells[(key, suite)]['pass_pct']:.1f}% ({p['diff']:+.1f} [{p['ci_low']:+.1f}, {p['ci_high']:+.1f}], {verdict})")
        out.append(f"- **{name}:** Beam (high) {cells[('beam-high', suite)]['pass_pct']:.1f}%. " + "; ".join(parts) + ".")
    return out


# ---------------------------------------------------------------- explore


HINT_SUFFIX = "#hint"  # the hint arm of a case 3 task is keyed "<task id>#hint" in tasks.json (same task id as the base arm)
SUITE_LABELS = {"tier_a": "Tier A", "tier_b": "Tier B", "heal_v3": "Case 3 base", "heal_v3_hint": "Case 3 hint"}


def task_choices(b: Bundle) -> list[str]:
    def label(rec: dict) -> str:
        return SUITE_LABELS.get(rec.get("suite", ""), rec["tier"])
    return [f"{label(rec)} | {tid}" for tid, rec in sorted(b.tasks.items(), key=lambda kv: (label(kv[1]), kv[0]))]


def task_results(b: Bundle, task_id: str) -> pd.DataFrame:
    rows = []
    hint = task_id.endswith(HINT_SUFFIX)
    task_id = task_id.removesuffix(HINT_SUFFIX)
    for tier, per_task in b.runs.items():
        if tier.startswith("C-") and tier != ("C-hint" if hint else "C-base"):
            continue
        for e, runs in sorted(per_task.get(task_id, {}).items()):
            rows.append({"Effort": e, "Pass rate (%)": r1(stats.mean(r.pass_rate for r in runs) * 100),
                         "Output tokens": round(stats.mean(r.completion_tokens for r in runs)), "Runs": len(runs)})
    return pd.DataFrame(rows)


def feature_table(rec: dict, task_id: str, b: Bundle) -> pd.DataFrame:
    if rec["tier"] == "B" and rec.get("suite", "tier_b") == "tier_b":
        cfg = b.configs.get(task_id) or rec["config"]
        return pd.DataFrame([{"Switch": k, "Setting": v, "On": "ON" if v not in ("off", "none", "apikey_header") else "off"} for k, v in cfg.items()])
    return pd.DataFrame([{"Feature": f} for f in rec["features"]])
