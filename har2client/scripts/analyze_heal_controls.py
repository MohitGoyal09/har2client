#!/usr/bin/env python3
"""Case 3 controls, repeat runs and the H10 header run (PREREG.md H22, H23, H10; added 2026-10-26): one command, any subset of steps.

Usage:
  uv run python scripts/analyze_heal_controls.py [--batch-root runs/batch] [--tasks-tierb runs/tierb50]
      [--tierb-v1 results/tierb50_v1.csv] [--tierb-medium results/tierb50_medium.csv] [--results-dir results]
      [--out results/analysis/heal_controls]

Reads (read-only) `<batch-root>/v3eval_{base,hint,notice,placebo}_low`, `v3eval_{base,hint}_low_r2` and `tierb50_header_high`
(`results.csv` in each), plus the Tier B CSVs. Writes `<out>/report.md` and `<out>/summary.json`. A step that is not there is
marked "not run yet". Pass rates come from `analyze_heal_v3.build_step` (the case 3 suite path), `api_error` rows are left out and
counted. Paired by task; 95% CI by paired bootstrap over tasks (10,000 resamples, seed 20261026).

High effort (PREREG.md deviation 2026-10-27): `<results-dir>/heal_v3_eval_{base,hint}_high{,_r2,_r3}.csv` and the Tier B high rows of
`--tierb-v1` plus `<results-dir>/tierb50_high_r{2,3}.csv` give the "repeat runs at high" table (per-run means, r2 - r1, r3 - r1,
per-task SD). The as-built high arm `a` of H10 and the high row of the header on/off table use the per-task mean of those runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_heal_v3 as ah  # noqa: E402
import heal_v3_verdicts as hv  # noqa: E402
import high_runs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH_ROOT = ah.DEFAULT_BATCH_ROOT
DEFAULT_TASKS_CASE3 = ROOT / "runs" / "heal_v3_eval"
DEFAULT_TASKS_TIERB = ROOT / "runs" / "tierb50"
DEFAULT_TIERB_V1 = ROOT / "results" / "tierb50_v1.csv"
DEFAULT_TIERB_MEDIUM = ROOT / "results" / "tierb50_medium.csv"
DEFAULT_RESULTS_DIR = ROOT / "results"
DEFAULT_OUT = ROOT / "results" / "analysis" / "heal_controls"
DEFAULT_SEED = 20261026
CASE3_DIRS = {"base": "v3eval_base_low", "hint": "v3eval_hint_low", "notice": "v3eval_notice_low", "placebo": "v3eval_placebo_low",
              "base_r2": "v3eval_base_low_r2", "hint_r2": "v3eval_hint_low_r2"}
HEADER_DIR = "tierb50_header_high"
LOW, HIGH = "low", "high"
CONTRASTS = (("placebo", "base"), ("notice", "base"), ("hint", "notice"), ("hint", "placebo"))
H10_ARMS = {"b": "hint", "c": "evidence"}  # a = the arm as built
OP_ARMS = ("base", "placebo", "notice", "hint")
OP_GROUPS = {"reads": ("list", "get"), "writes": ("create", "update"), "delete": ("delete",)}  # by the method-name prefix in grade.json
READS_CONTRASTS = (("notice", "base"), ("placebo", "base"))
PROMPT_NOTE = "headline is a prompting effect"
RULES = {
    "H22": "Supported if placebo - base < 5 points AND hint - placebo >= 20 points with a CI that excludes 0. Rejected if placebo - base >= 20 points. Otherwise inconclusive.",
    "H23": "Supported if notice - base >= 20 points with a CI that excludes 0. Rejected if notice - base < 5 points (and the CI upper end < 10). Otherwise inconclusive.",
    "H10": "Supported if (c) evidence - (a) as built >= 20 points and (b) hint - (a) >= 20 points (report which is larger); add the note "
           f"\"{PROMPT_NOTE}\" if (b) reaches 90%. Otherwise not supported.",
}
READING_RULES = (
    "Unit = task; 1 run per cell, except high effort in the H10 as-built arm and the header table (mean of 3 runs per task). Points = percentage points of `pass_rate`. `api_error` rows are not model results: left out and counted.",
    "A step is used only when every task has a row (`finished >= expected`); a partial step is shown and not used.",
    "\"CI excludes 0\" means the 95% paired-bootstrap lower end is above 0. Thresholds on a mean are tested on the point estimate.",
    "Full repair = a task with pass_rate 1.0. Flip rate = share of tasks whose full-repair status differs between r1 and r2.",
    "If the mean absolute per-task difference between r1 and r2 is above 5 points, single-run differences below that size are within noise.",
    "Repeat runs at high: per-task SD = sample standard deviation of a task's pass rate over its runs, in points, averaged over tasks; "
    "mean abs diff and flip rate are averaged over the run pairs (r1-r2, r1-r3, r2-r3).",
    "Header on/off is descriptive context: independent groups of tasks (hidden_header on vs off in `runs/tierb50`), no verdict.",
)


def ge(x: float, threshold: float) -> bool:
    return x >= threshold - hv.EPS


def read_rows(path: Path) -> list[dict] | None:
    if not Path(path).is_file():
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_case3(batch_root: Path, expected: int) -> dict[tuple[str, str], hv.Step]:
    steps = {}
    for name, dirname in CASE3_DIRS.items():
        rows = read_rows(Path(batch_root) / dirname / "results.csv")
        if rows is not None:
            steps[name, LOW] = ah.build_step(name, LOW, rows, expected)
    return steps


def hidden_header_tasks(tasks_dir: Path) -> tuple[set[str], set[str]]:
    """(tasks with hidden_header on, all tasks) from the task.json files of a Tier B tasks dir."""
    on, every = set(), set()
    for p in sorted(Path(tasks_dir).glob("*/task.json")) if Path(tasks_dir).is_dir() else []:
        every.add(p.parent.name)
        if json.loads(p.read_text(encoding="utf-8")).get("config", {}).get("hidden_header") == "on":
            on.add(p.parent.name)
    return on, every


def _est(est: hv.Est | None) -> str:
    return "n/a" if est is None else f"{est.mean:+.1f} [{est.lo:+.1f}, {est.hi:+.1f}]"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _pass(step: hv.Step | None) -> float | None:
    return hv.mean(s.pass_rate for s in step.scores.values()) if step is not None else None


def _verdict(hid: str, status: str, evidence: str) -> dict:
    return {"id": hid, "status": status, "evidence": evidence, "rule": RULES[hid]}


def _not_yet(hid: str, an: hv.Analysis, cells: list[str]) -> dict | None:
    missing = an.missing(*[(c, LOW) for c in cells]) if cells else []
    return _verdict(hid, hv.NOT_YET, "waiting for step(s): " + ", ".join(missing)) if missing else None


def verdict_h22(an: hv.Analysis) -> dict:
    if (waiting := _not_yet("H22", an, ["base", "placebo", "hint"])) is not None:
        return waiting
    pb, hp = an.contrast(("placebo", LOW), ("base", LOW)), an.contrast(("hint", LOW), ("placebo", LOW))
    text = f"placebo - base {_est(pb)} pts (n={pb.n}); hint - placebo {_est(hp)} pts (n={hp.n})"
    if ge(pb.mean, 20):
        return _verdict("H22", hv.REJECTED, text)
    if pb.mean < 5 and ge(hp.mean, 20) and hp.lo > 0:
        return _verdict("H22", hv.SUPPORTED, text)
    return _verdict("H22", hv.INCONCLUSIVE, text)


def verdict_h23(an: hv.Analysis) -> dict:
    if (waiting := _not_yet("H23", an, ["base", "notice"])) is not None:
        return waiting
    nb = an.contrast(("notice", LOW), ("base", LOW))
    text = f"notice - base {_est(nb)} pts (n={nb.n})"
    if ge(nb.mean, 20) and nb.lo > 0:
        return _verdict("H23", hv.SUPPORTED, text)
    if nb.mean < 5 and nb.hi < 10:
        return _verdict("H23", hv.REJECTED, text)
    return _verdict("H23", hv.INCONCLUSIVE, text)


def contrast_rows(an: hv.Analysis) -> list[dict]:
    out = []
    for x, y in CONTRASTS:
        est, full = an.contrast((x, LOW), (y, LOW)), an.contrast((x, LOW), (y, LOW), "full")
        out.append({"contrast": f"{x} - {y}", "pass": est.to_dict() if est else None, "full": full.to_dict() if full else None,
                    "pass_x": _pass(an.step(x, LOW)), "pass_y": _pass(an.step(y, LOW))})
    return out


def repeat_run(an: hv.Analysis, arm: str) -> dict | None:
    """r1 (existing step) vs r2 of one arm, over the tasks both runs scored."""
    s1, s2 = an.step(arm, LOW), an.step(f"{arm}_r2", LOW)
    if s1 is None or s2 is None:
        return None
    common = sorted(set(s1.scores) & set(s2.scores))
    if not common:
        return None
    one, two = [s1.scores[t] for t in common], [s2.scores[t] for t in common]
    f1, f2 = [s.full > 0.5 for s in one], [s.full > 0.5 for s in two]
    n = len(common)
    diff = an.contrast((f"{arm}_r2", LOW), (arm, LOW))
    return {
        "n": n, "mean_r1": hv.mean(s.pass_rate for s in one), "mean_r2": hv.mean(s.pass_rate for s in two),
        "r2_minus_r1": diff.to_dict(), "flip_rate": sum(a != b for a, b in zip(f1, f2)) / n,
        "mean_abs_diff_points": hv.POINTS * hv.mean(abs(a.pass_rate - b.pass_rate) for a, b in zip(one, two)),
        "pass_at_2": sum(a and b for a, b in zip(f1, f2)) / n, "pass_at_1": (sum(f1) + sum(f2)) / (2 * n),
    }


def high_run_rows(results_dir: Path, stem: str, first: list[dict]) -> list[list[dict]]:
    """[run 1 rows, run 2 rows, ...] at high: `first` and every `<results-dir>/<stem>_r2.csv`, `_r3.csv` that exists (each read as one run)."""
    return [first, *[rows for path in high_runs.extra_run_paths(results_dir, stem) if (rows := read_rows(path)) is not None]]


def repeat_high(runs: list[list[dict]], n_boot: int, seed: int, key: str) -> dict | None:
    """Run-to-run stability at high: per-run means, r_k - r1 (paired bootstrap), per-task SD, pair-averaged flip rate and abs diff.

    `runs` = one row list per run; they must cover the same tasks (`high_runs.RunMismatchError` otherwise). None with fewer than 2 runs.
    """
    if len(runs) < 2:
        return None
    merged = high_runs.merge_runs(runs)
    n_tasks = len(runs[0])
    per_run = [ah.build_step(key, HIGH, [r for r in merged if r["run"] == str(k)], n_tasks).scores for k in range(1, len(runs) + 1)]
    tasks = sorted(per_run[0])
    passes = [[sc[t].pass_rate for t in tasks] for sc in per_run]
    fulls = [[sc[t].full > 0.5 for t in tasks] for sc in per_run]
    pairs = list(combinations(range(len(runs)), 2))
    out = {"n": len(tasks), "runs": len(runs), "mean_of_runs": hv.mean(hv.mean(col) for col in zip(*passes)),
           "per_task_sd_points": hv.POINTS * hv.mean(statistics.stdev(col) for col in zip(*passes)),
           "mean_abs_diff_points": hv.POINTS * hv.mean(hv.mean(abs(x - y) for x, y in zip(passes[i], passes[j])) for i, j in pairs),
           "flip_rate": hv.mean(hv.mean(x != y for x, y in zip(fulls[i], fulls[j])) for i, j in pairs)}
    for k in range(len(runs)):
        out[f"mean_r{k + 1}"] = hv.mean(passes[k])
    for k in range(1, len(runs)):
        est = hv.bootstrap_mean([hv.POINTS * (x - y) for x, y in zip(passes[k], passes[0])], n_boot, seed, f"{key}-r{k + 1}-r1")
        out[f"r{k + 1}_minus_r1"] = est.to_dict()
    return out


def high_series(args: argparse.Namespace, v1: list[dict]) -> dict[str, list[list[dict]]]:
    """Per series (`base`, `hint`, `tierb`) the rows of each high run that exists; a series without run 1 is left out."""
    out = {}
    for arm in ("base", "hint"):
        stem = f"heal_v3_eval_{arm}_{HIGH}"
        first = read_rows(Path(args.results_dir) / f"{stem}.csv")
        if first is not None:
            out[arm] = high_run_rows(args.results_dir, stem, [r for r in first if r.get("run", "1") == "1"])
    tierb_first = [r for r in v1 if r["effort"] == HIGH and r.get("run", "1") == "1"]
    if tierb_first:
        out["tierb"] = high_run_rows(args.results_dir, f"tierb50_{HIGH}", tierb_first)
    return out


def op_shares(cell: Path) -> dict[str, float] | None:
    """Pass share of the hidden tests per operation group in one cell's grade.json (`method` = `<op>_<entity>`); None without a grade."""
    try:
        results = json.loads((cell / "grade.json").read_text(encoding="utf-8"))["results"]
    except (OSError, ValueError, KeyError):
        return None
    out = {}
    for group, ops in OP_GROUPS.items():
        passed = [bool(r["passed"]) for r in results if str(r.get("method", "")).split("_")[0] in ops]
        if passed:
            out[group] = sum(passed) / len(passed)
    return out


def per_operation(batch_root: Path, an: hv.Analysis, n_boot: int, seed: int) -> dict:
    """Per low arm: mean pass share by operation group (descriptive); notice and placebo minus base on reads, paired by task."""
    per_arm: dict[str, dict[str, dict[str, float]]] = {}
    for arm in OP_ARMS:
        rows = read_rows(Path(batch_root) / CASE3_DIRS[arm] / "results.csv")
        if rows is None or an.step(arm, LOW) is None:
            continue
        cells = {r["task"]: op_shares(Path(batch_root) / CASE3_DIRS[arm] / r["task"] / f"{r['effort']}-{r['har_mode']}-r{r['run']}")
                 for r in rows if r.get("status") != ah.API_ERROR}
        per_arm[arm] = {t: v for t, v in cells.items() if v is not None}
    arms = {arm: {g: {"n_tasks": len(v), "pass_share": hv.mean(v)} for g in OP_GROUPS
                  if (v := [d[g] for d in tasks.values() if g in d])} for arm, tasks in per_arm.items()}
    reads = {}
    for x, y in READS_CONTRASTS:
        if x in per_arm and y in per_arm:
            common = sorted(t for t in set(per_arm[x]) & set(per_arm[y]) if "reads" in per_arm[x][t] and "reads" in per_arm[y][t])
            est = hv.bootstrap_mean([hv.POINTS * (per_arm[x][t]["reads"] - per_arm[y][t]["reads"]) for t in common], n_boot, seed, f"reads-{x}-{y}")
            reads[f"{x} - {y}"] = est.to_dict() if est else None
    return {"arms": arms, "reads_contrasts": reads}


def h10_analysis(header_rows: list[dict] | None, a_rows: list[dict], hidden: set[str], n_boot: int, seed: int) -> dict:
    """Arm a (as built, high, run 1) vs b `__hint` and c `__evidence` copies, over the hidden-header tasks, paired by base task."""
    if header_rows is None:
        return {"state": "not run yet"}
    steps = {("a", HIGH): ah.build_step("a", HIGH, [r for r in a_rows if r["task"] in hidden], len(hidden))}
    for key, suffix in H10_ARMS.items():
        rows = [{**r, "task": r["task"][: -len(suffix) - 2]} for r in header_rows if r["task"].endswith(f"__{suffix}")]
        steps[key, HIGH] = ah.build_step(key, HIGH, rows, len(hidden))
    an = hv.Analysis(steps, n_boot, seed)
    info = {k: {"finished": s.finished, "expected": s.expected, "api_error_rows": s.api_errors, "pass_rate": _pass(an.step(k, HIGH)), "runs_per_task": s.runs}
            for (k, _), s in steps.items()}
    if missing := an.missing(*[(k, HIGH) for k in ("a", *H10_ARMS)]):
        return {"state": "incomplete", "steps": info, "verdict": _verdict("H10", hv.NOT_YET, "waiting for: " + ", ".join(missing))}
    ba, ca = (an.contrast((k, HIGH), ("a", HIGH)) for k in ("b", "c"))
    larger = "evidence" if ca.mean > ba.mean else "hint" if ba.mean > ca.mean else "equal"
    ok = ge(ca.mean, 20) and ge(ba.mean, 20)
    text = f"hint - as built {_est(ba)} pts; evidence - as built {_est(ca)} pts (n={ba.n}); larger: {larger}; hint arm {_pct(info['b']['pass_rate'])}"
    if info["b"]["pass_rate"] >= 0.9 - hv.EPS:
        text += f"; {PROMPT_NOTE}"
    return {"state": "complete", "steps": info, "b_minus_a": ba.to_dict(), "c_minus_a": ca.to_dict(), "larger": larger,
            "verdict": _verdict("H10", hv.SUPPORTED if ok else "not supported", text)}


def header_on_off(rows_by_effort: dict[str, list[dict] | None], on: set[str], every: set[str], n_boot: int, seed: int) -> dict[str, dict]:
    out = {}
    for effort, rows in rows_by_effort.items():
        if rows is None:
            continue
        step = ah.build_step("tierb50", effort, rows, len(every))
        vals = {k: [s.pass_rate * hv.POINTS for t, s in step.scores.items() if (t in on) == (k == "on")] for k in ("on", "off")}
        diff = hv.bootstrap_diff(vals["on"], vals["off"], n_boot, seed, f"header-{effort}")
        out[effort] = {"n_on": len(vals["on"]), "n_off": len(vals["off"]), "pass_on": hv.mean(v / 100 for v in vals["on"]),
                       "pass_off": hv.mean(v / 100 for v in vals["off"]), "on_minus_off": diff.to_dict() if diff else None,
                       "api_error_rows": step.api_errors}
    return out


# ------------------------------------------------------------ report


def _d(est: dict | None) -> str:
    return "n/a" if est is None else _est(hv.Est(est["n"], est["mean"], est["ci_low"], est["ci_high"]))


HIGH_SERIES_NAME = {f"base_{HIGH}": "case 3 base", f"hint_{HIGH}": "case 3 hint", f"tierb_{HIGH}": "case 1 Tier B (as built)"}


def render_repeat_high(repeats: dict) -> list[str]:
    """Repeat runs at high (r1, r2, r3 of the same tasks): the table that comes with the mean-of-3-runs rule."""
    keys = [k for k in HIGH_SERIES_NAME if k in repeats]
    out = ["### Repeat runs at high (run-to-run stability; the per-task mean of these runs is the high score)", ""]
    if not keys:
        return out + ["not run yet", ""]
    rows = []
    for k in keys:
        r = repeats[k]
        if r is None:
            rows.append([HIGH_SERIES_NAME[k], "needs 2 or more runs", "", "", "", "", "", "", "", ""])
            continue
        rows.append([HIGH_SERIES_NAME[k], f"{r['n']} x {r['runs']}", *[_pct(r.get(f"mean_r{n}")) for n in (1, 2, 3)], _d(r["r2_minus_r1"]),
                     _d(r.get("r3_minus_r1")), f"{r['per_task_sd_points']:.1f}", f"{r['mean_abs_diff_points']:.1f}", _pct(r["flip_rate"])])
    return out + ah.md_table(["series", "tasks x runs", "mean r1", "mean r2", "mean r3", "r2 - r1 [CI]", "r3 - r1 [CI]", "per-task SD (pts)",
                              "mean abs diff (pts)", "flip rate (full repair)"], rows)


def render_report(summary: dict, header_table_note: str) -> str:
    out = ["# Case 3 controls, repeat runs and H10: analysis", "",
           f"`api_error` rows excluded from every number: {summary['api_error_rows_total']}. 95% paired bootstrap CI, "
           f"{summary['n_boot']:,} resamples, seed {summary['seed']}; points.", "", "## Steps", ""]
    out += ah.md_table(["step", "state", "tasks finished", "api_error rows (excluded)", "pass rate", "full repair"],
                  [[n, s["state"], s["finished"], str(s["api_error_rows"]), _pct(s["pass_rate"]), _pct(s["full_repair"])]
                   for n, s in summary["steps"].items()])
    out += ["## Verdicts", ""]
    out += ah.md_table(["test", "verdict", "evidence"], [[v["id"], f"**{v['status']}**", v["evidence"]] for v in summary["verdicts"]])
    out += [f"- **{v['id']}** rule: {v['rule']}" for v in summary["verdicts"]] + [""]
    out += ["## Case 3 contrasts at low (paired by task)", ""]
    out += ah.md_table(["contrast", "paired tasks", "x pass", "y pass", "pass diff [CI]", "full-repair diff [CI]"],
                  [[c["contrast"], str(c["pass"]["n"]) if c["pass"] else "0", _pct(c["pass_x"]), _pct(c["pass_y"]), _d(c["pass"]), _d(c["full"])]
                   for c in summary["contrasts"]])
    out += ["## Repeat runs (r1 = existing low step, r2 = repeat)", ""]
    rows = [[arm, "not run yet", "", "", "", "", "", ""] if r is None else
            [arm, str(r["n"]), _pct(r["mean_r1"]), _pct(r["mean_r2"]), _d(r["r2_minus_r1"]), _pct(r["flip_rate"]),
             f"{r['mean_abs_diff_points']:.1f}", f"{_pct(r['pass_at_2'])} vs {_pct(r['pass_at_1'])}"]
            for arm, r in summary["repeat_runs"].items() if not arm.endswith(f"_{HIGH}")]
    out += ah.md_table(["arm", "tasks", "mean r1", "mean r2", "r2 - r1 [CI]", "flip rate (full repair)", "mean abs diff (pts)", "pass^2 vs pass@1"], rows)
    out += render_repeat_high(summary["repeat_runs"])
    ops = summary["per_operation"]
    out += ["## Pass share by operation type, low (descriptive, no verdict)", "",
            "Pass share of the hidden tests per group (reads = list/get, writes = create/update, delete), from each cell's saved grade record; "
            "mean over the tasks that have tests of the group. Reads depend only on return-shape translation.", ""]
    out += ah.md_table(["arm", *[f"{g} (tasks)" for g in OP_GROUPS]],
                  [[a, *[f"{_pct(v[g]['pass_share'])} ({v[g]['n_tasks']})" if g in v else "n/a" for g in OP_GROUPS]] for a, v in ops["arms"].items()]
                  or [["not run yet", "", "", ""]])
    out += ah.md_table(["contrast on reads only", "paired tasks", "diff [CI]"],
                  [[c, str(e["n"]) if e else "0", _d(e)] for c, e in ops["reads_contrasts"].items()] or [["not yet testable", "", ""]])
    out += ["## H10: hidden-header Tier B tasks, high", ""]
    h10 = summary["h10"]
    if h10["state"] == "not run yet":
        out += [f"`{HEADER_DIR}` not run yet.", ""]
    else:
        out += ah.md_table(["arm", "runs per task", "tasks finished", "api_error rows (excluded)", "pass rate"],
                      [[k, str(s["runs_per_task"]), f"{s['finished']}/{s['expected']}", str(s["api_error_rows"]), _pct(s["pass_rate"])] for k, s in h10["steps"].items()])
        runs_a = h10["steps"]["a"]["runs_per_task"]
        out += ["a = as built (`results/tierb50_v1.csv` high" + (f" and the repeats: mean of {runs_a} runs per task" if runs_a > 1 else ", run 1") + "), b = `__hint`, c = `__evidence` (1 run each).", ""]
        if "b_minus_a" in h10:
            out += [f"b - a {_d(h10['b_minus_a'])}; c - a {_d(h10['c_minus_a'])}; larger: {h10['larger']}.", ""]
    out += ["## Hidden header on vs off, Tier B (descriptive)", "", header_table_note, ""]
    out += ah.md_table(["effort", "tasks on", "tasks off", "pass on", "pass off", "on - off [CI, independent groups]", "api_error rows"],
                  [[e, str(h["n_on"]), str(h["n_off"]), _pct(h["pass_on"]), _pct(h["pass_off"]), _d(h["on_minus_off"]), str(h["api_error_rows"])]
                   for e, h in summary["header_on_off"].items()] or [["no Tier B data", "", "", "", "", "", ""]])
    return "\n".join([*out, "## Reading rules", "", *[f"- {r}" for r in READING_RULES], ""]) + "\n"


def step_row(name: str, step: hv.Step | None) -> dict:
    if step is None:
        return {"state": "not run yet", "finished": "", "api_error_rows": 0, "pass_rate": None, "full_repair": None}
    state = "complete" if step.complete else "partial (not used)"
    return {"state": state, "finished": f"{step.finished}/{step.expected}", "api_error_rows": step.api_errors,
            "pass_rate": _pass(step), "full_repair": hv.mean(s.full for s in step.scores.values())}


def build_summary(args: argparse.Namespace) -> dict:
    expected = ah.count_tasks(args.tasks_case3)
    steps = load_case3(args.batch_root, expected)
    an = hv.Analysis(steps, args.n_boot, args.seed)
    on, every = hidden_header_tasks(args.tasks_tierb)
    v1, medium = read_rows(args.tierb_v1) or [], read_rows(args.tierb_medium)
    header_rows = read_rows(Path(args.batch_root) / HEADER_DIR / "results.csv")
    hidden = {r["task"][:-len(f"__{H10_ARMS['b']}")] for r in header_rows or [] if r["task"].endswith(f"__{H10_ARMS['b']}")} or on
    a_rows = [r for r in v1 if r["effort"] == HIGH and r.get("run", "1") == "1"]
    series = high_series(args, v1)
    if len(series.get("tierb", [])) > 1:  # the as-built high arm is the per-task mean of its runs (the repeats are all 50 tasks)
        a_rows = high_runs.merge_runs(series["tierb"])
    single = lambda rows: [r for r in rows or [] if r.get("run", "1") == "1"] or None  # noqa: E731  low and medium: 1 run
    by_effort = {LOW: single([r for r in v1 if r["effort"] == LOW]), "medium": single(medium), HIGH: a_rows or None}
    h10 = h10_analysis(header_rows, a_rows, hidden, args.n_boot, args.seed)
    verdicts = [verdict_h22(an), verdict_h23(an)] + ([h10["verdict"]] if "verdict" in h10 else [_verdict("H10", hv.NOT_YET, f"`{HEADER_DIR}` not run yet")])
    named = {n: steps.get((n, LOW)) for n in CASE3_DIRS}
    return {
        "n_boot": args.n_boot, "seed": args.seed, "steps": {n: step_row(n, s) for n, s in named.items()},
        "api_error_rows_total": sum(s.api_errors for s in steps.values()) + sum(x["api_error_rows"] for x in h10.get("steps", {}).values()),
        "contrasts": contrast_rows(an), "verdicts": verdicts,
        "repeat_runs": {**{arm: repeat_run(an, arm) for arm in ("base", "hint")},
                        **{f"{name}_{HIGH}": repeat_high(runs, args.n_boot, args.seed, f"high-{name}") for name, runs in series.items()}},
        "high_runs_per_task": {name: len(runs) for name, runs in series.items()}, "per_operation": per_operation(args.batch_root, an, args.n_boot, args.seed),
        "h10": h10,
        "header_on_off": header_on_off(by_effort, on, every, args.n_boot, args.seed),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT, help="dir with the batch steps (read-only)")
    p.add_argument("--tasks-case3", type=Path, default=DEFAULT_TASKS_CASE3, help="case 3 task dir (counts the expected tasks)")
    p.add_argument("--tasks-tierb", type=Path, default=DEFAULT_TASKS_TIERB, help="Tier B task dir (hidden_header config per task)")
    p.add_argument("--tierb-v1", type=Path, default=DEFAULT_TIERB_V1, help="Tier B run 1 (low and high)")
    p.add_argument("--tierb-medium", type=Path, default=DEFAULT_TIERB_MEDIUM)
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR, help="committed results: high repeats r2, r3 are read from here")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--n-boot", type=int, default=hv.DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args(argv)
    summary = build_summary(args)
    tb_runs = summary["high_runs_per_task"].get("tierb", 1)
    note = (f"Tasks with `hidden_header` on vs off in `{args.tasks_tierb.name}`; the arm as built (no hint, no evidence). "
            + (f"High = mean of {tb_runs} runs per task; low and medium = 1 run." if tb_runs > 1 else "1 run per task."))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(render_report(summary, note), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"-> {args.out / 'report.md'}")
    for v in summary["verdicts"]:
        print(f"  {v['id']}: {v['status']}")
    print("effort | tasks on/off | pass on | pass off | on - off")
    for e, h in summary["header_on_off"].items():
        print(f"{e} | {h['n_on']}/{h['n_off']} | {_pct(h['pass_on'])} | {_pct(h['pass_off'])} | {_d(h['on_minus_off'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
