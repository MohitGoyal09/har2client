#!/usr/bin/env python3
"""Case 2 probing analysis: does Beam test its hypotheses, and does testing help? Report, summary, two figures.

Usage:
  uv run --extra plots python scripts/analyze_probing.py
  # a quick look, with fewer resamples and no figures:
  uv run --extra plots python scripts/analyze_probing.py --n-boot 2000 --no-figures

Reads (read-only, no API call):
  results/case2_probe/main_full/results.csv       Beam Case 2, arms placebo (0 probes) and P5 (up to 5 probes), 3 efforts x 50 tasks, 1 run.
                                                  Any other arm in that file is dropped on load.
  results/model_comparison/<slug>/{probe0,probe5}/results.csv   GPT-OSS-120B low/medium/high and Qwen3.5 thinking. These files hold no
                                                  `hidden_header` and no `probes_*` column; the hidden-header flag is joined by task name
                                                  from the Beam file (same 50 Tier B tasks), the probe columns are reported as missing.
  results/tierb50_v1.csv, tierb50_medium.csv, heal_v3_eval_{base,hint}_{low,medium,high}.csv and the `_high_r2/_r3` repeats
                                                  Beam Case 1 and Case 3 per-task rows, for the failure kinds (`fail_*` columns).
  results/case2_probe/ext/R1_P5_high_r2r3/results.csv   Exploratory extension (protocol case2-ext-v1, results/case2_probe/ext/PROTOCOL.md):
  results/case2_probe/ext/R2_P1P2_high/results.csv      P5 high runs 2 and 3 (its `run` column says 1 and 2), arms P1 and P2 at high, and
  results/case2_probe/ext/R3_P5_max/results.csv         P5 at effort max. R3 is optional: it is used only when the file holds at least
                                                  50 scored rows; otherwise it is skipped and the report says so. R1 and R2 are required.
Pass rate in the CSVs is 0..1; every number here is in percent. CI = 95% percentile bootstrap over tasks (same method and
seed as analyze_models.py, through heal_v3_verdicts.bootstrap_mean).

Writes results/analysis/probing/{summary.json,report.md} and results/figures/final_probing.{png,svg,json} and
final_probe_curve.{png,svg,json} (exploratory extension) and final_failure_kinds.{png,svg,json}.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_models as am  # noqa: E402
import heal_v3_verdicts as hv  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results"
DEFAULT_OUT = ROOT / "results" / "analysis" / "probing"
DEFAULT_FIG_DIR = ROOT / "results" / "figures"
SOURCE_NOTE = "Data: results/analysis/probing/summary.json."
EXT_DIR = Path("case2_probe") / "ext"
EXT_R1, EXT_R2, EXT_R3 = (EXT_DIR / n / "results.csv" for n in ("R1_P5_high_r2r3", "R2_P1P2_high", "R3_P5_max"))
EXT_PROTOCOL = "case2-ext-v1"
MIN_R3_ROWS = 50  # R3 is used only when every task has a scored row
HIGH = "high"
HEADLINE_KEY = "models:probe5:beam-high"  # the bootstrap key analyze_models.py uses for this cell, so both reports give the same CI
EFFORTS = am.EFFORTS
ARMS = ("placebo", "P5")  # the only Case 2 arms read; any other arm of the file is dropped
SUITE_ARM = {"probe0": "placebo", "probe5": "P5"}
SPLITS = ("on", "off", "all")  # hidden_header on / off / every task
PROBE_COLUMNS = ("probes_header_ablation", "probes_auth_test", "probes_paging_test", "probes_etag_test", "probes_other",
                 "probes_refused")
NO_PROBE_COLUMNS = ("hidden_header", "probes_used", *PROBE_COLUMNS, "ablated_hidden_header")
TESTED_FLAG = "ablated_hidden_header"
PCT = am.PCT
FAILURE_SUITES = {"case1": "tierb50", "case3_base": "v3base", "case3_hint": "v3hint"}
KIND_LABEL = {
    "fail_bad_request_400": "400 bad request", "fail_exception": "code raised an exception", "fail_return_mismatch": "wrong return value",
    "fail_state_mismatch": "wrong server state", "fail_rate_limited_429": "429 rate limited", "fail_incomplete_list": "incomplete list",
    "fail_not_found_404": "404 not found", "fail_timeout": "timeout", "fail_precondition_428": "428 precondition",
    "fail_auth_401": "401 auth", "fail_args_mismatch": "wrong arguments", "fail_no_request": "no request sent",
    "fail_wrong_op": "wrong operation",
}
FAILURE_TITLE = "At higher effort Beam's Case 1 failures are almost all rejected requests; in Case 3 the mix shifts only a little"
FIGURE_KINDS_MIN_SHARE = 0.05  # a kind below this share in every bar of the figure is merged into "other"
MIN_LABEL_SHARE = 6.0  # a stacked segment smaller than this (percent) gets no inside label


# ---- loading ---------------------------------------------------------------


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("run") or "1") == "1" and r.get("status") != am.API_ERROR]


def read_all_runs(path: Path) -> list[dict]:
    """Every scored row (any run) of a results CSV; `api_error` rows are left out."""
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("status") != am.API_ERROR]


def check_once(rows: list[dict], tasks: set[str], what: str) -> None:
    """Every (task, arm, effort, run) of `rows` appears exactly once, and each (arm, effort, run) covers exactly `tasks`."""
    seen: dict[tuple, int] = {}
    for r in rows:
        k = (r["task"], r["arm"], r["effort"], r["run"])
        seen[k] = seen.get(k, 0) + 1
    twice = [k for k, n in seen.items() if n > 1]
    if twice:
        raise SystemExit(f"error: {what}: (task, arm, effort, run) appears more than once after dropping api_error: {twice[:3]}")
    groups: dict[tuple, set[str]] = {}
    for t, a, e, ru in seen:
        groups.setdefault((a, e, ru), set()).add(t)
    for g, got in groups.items():
        if got != tasks:
            raise SystemExit(f"error: {what}: {g} holds {len(got)} tasks, expected {len(tasks)} (missing {sorted(tasks - got)[:3]})")


def _num(row: dict, col: str) -> float:
    return am._num(row.get(col)) or 0.0


def load_beam(results: Path) -> list[dict]:
    """Case 2 rows of the arms placebo and P5; every other arm is dropped here."""
    return [r for r in _read(results / am.CASE2_CSV) if r["arm"] in ARMS]


def model_name(slug: str) -> tuple[str, str]:
    """(display name, effort) of a model_comparison directory: `gpt-oss-120b-low` -> ("GPT-OSS-120B", "low"), Qwen -> thinking."""
    if slug.startswith("gpt-oss-120b-"):
        return "GPT-OSS-120B", slug.rsplit("-", 1)[1]
    if slug.startswith("qwen3.5-397b"):
        return "Qwen3.5-397B-A17B", "thinking"
    return slug, "-"


def load_other(results: Path) -> dict[str, dict]:
    """slug -> {label, effort, rows per arm}, for every model directory that holds both Case 2 files."""
    root = results / "model_comparison"
    out = {}
    for model_dir in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        files = {s: model_dir / s / "results.csv" for s in SUITE_ARM}
        if not all(f.is_file() for f in files.values()):
            continue
        label, effort = model_name(model_dir.name)
        out[model_dir.name] = {"label": label, "effort": effort, "rows": {SUITE_ARM[s]: _read(f) for s, f in files.items()}}
    return out


# ---- numbers ---------------------------------------------------------------


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def cell_stats(rows: list[dict], key: str, n_boot: int, seed: int, with_probes: bool, split: str) -> dict:
    """Stats of one group of tasks. `with_probes` is false for files that lack the probe columns: those fields are None."""
    values = [_num(r, "pass_rate") * PCT for r in rows]
    est = hv.bootstrap_mean(values, n_boot, seed, key)
    out = {"n": len(rows), "pass_rate": est.mean if est else None, "ci_low": est.lo if est else None, "ci_high": est.hi if est else None,
           "invented_endpoint_calls_gt0": sum(_num(r, "invented_endpoint_calls") > 0 for r in rows),
           "hardcoded_secret": sum(str(r.get("hardcoded_secret")).strip().lower() == "true" for r in rows),
           "mean_completion_tokens": _mean([_num(r, "completion_tokens") for r in rows]),
           "mean_probes_used": None, "mean_probes": None, "tested_header_n": None, "tested_header_share": None}
    if with_probes:
        out["mean_probes_used"] = _mean([_num(r, "probes_used") for r in rows])
        out["mean_probes"] = {c.removeprefix("probes_"): _mean([_num(r, c) for r in rows]) for c in PROBE_COLUMNS}
        out["tested_header_n"] = sum(str(r.get(TESTED_FLAG)) == "True" for r in rows)
        if split == "on" and rows:  # the share only means something on tasks that hide the header
            out["tested_header_share"] = out["tested_header_n"] / len(rows) * PCT
    return out


def split_rows(rows: list[dict], split: str, hidden: dict[str, str]) -> list[dict]:
    return [r for r in rows if split == "all" or hidden[r["task"]] == split]


def beam_block(rows: list[dict], hidden: dict[str, str], n_boot: int, seed: int) -> dict:
    return {effort: {arm: {s: cell_stats(split_rows([r for r in rows if r["effort"] == effort and r["arm"] == arm], s, hidden),
                                         f"probing:beam:{effort}:{arm}:{s}", n_boot, seed, True, s) for s in SPLITS}
                     for arm in ARMS} for effort in EFFORTS}


def other_block(models: dict[str, dict], hidden: dict[str, str], n_boot: int, seed: int) -> dict:
    return {slug: {"label": m["label"], "effort": m["effort"],
                   "arms": {arm: {s: cell_stats(split_rows(rows, s, hidden), f"probing:{slug}:{arm}:{s}", n_boot, seed, False, s)
                                  for s in SPLITS} for arm, rows in m["rows"].items()}}
            for slug, m in models.items()}


def conditional_block(rows: list[dict], hidden: dict[str, str], n_boot: int, seed: int) -> list[dict]:
    """Beam P5, hidden-header tasks: pass rate when the header was tested vs not, with the placebo on the same tasks as a control.

    Tested tasks are not a random sample (a model that tests may differ), so each group also reports the placebo pass rate on
    the very same tasks: the control that shows how hard those tasks are without probing.
    """
    out = []
    for effort in EFFORTS:
        p5 = [r for r in rows if r["effort"] == effort and r["arm"] == "P5" and hidden[r["task"]] == "on"]
        placebo = {r["task"]: r for r in rows if r["effort"] == effort and r["arm"] == "placebo"}
        groups = {}
        for tested in (True, False):
            sel = [r for r in p5 if (r.get(TESTED_FLAG) == "True") is tested]
            est = hv.bootstrap_mean([_num(r, "pass_rate") * PCT for r in sel], n_boot, seed, f"probing:cond:{effort}:{tested}")
            groups["tested" if tested else "not_tested"] = {
                "n": len(sel), "pass_rate": est.mean if est else None, "ci_low": est.lo if est else None, "ci_high": est.hi if est else None,
                "placebo_pass_rate_same_tasks": _mean([_num(placebo[r["task"]], "pass_rate") * PCT for r in sel])}
        out.append({"model": "Beam", "effort": effort, "arm": "P5", "hidden_header_tasks": len(p5), **groups})
    return out


def failure_block(results: Path) -> dict:
    """Beam failure kinds per case and effort: failing hidden tests summed per `fail_*` column over tasks (and runs), as shares.

    Case 1 and Case 3 high are the 3 runs pooled; low and medium are 1 run. Pooling keeps the shares equal to the mean of runs.
    """
    sources = am.beam_sources(results)
    out = {}
    for name, suite in FAILURE_SUITES.items():
        out[name] = {}
        for effort in EFFORTS:
            if (suite, effort) not in sources:
                continue
            paths = [sources[(suite, effort)], *am.beam_repeats(results, suite, effort)]
            rows = [r for p in paths for r in am._read_rows(p, effort, None) if r.get("status") != am.API_ERROR]
            kinds = sorted({c for r in rows for c in r if c.startswith("fail_")})
            counts = {k: sum(_num(r, k) for r in rows) for k in kinds}
            total = sum(counts.values())
            out[name][effort] = {
                "runs": len(paths), "task_rows": len(rows), "failed_tests": total,
                "no_code_rows": sum(r.get("status") == "no_code" for r in rows),
                "counts": counts, "shares": {k: v / total * PCT if total else None for k, v in counts.items()}}
    return out


def by_run(rows: list[dict], arm: str, effort: str) -> list[list[dict]]:
    """Rows of one arm and effort, one list per run value, in run order."""
    sel = [r for r in rows if r["arm"] == arm and r["effort"] == effort]
    return [[r for r in sel if r["run"] == n] for n in sorted({r["run"] for r in sel}, key=int)]


def multi_run_cell(runs: list[list[dict]], hidden: dict[str, str], n_boot: int, seed: int, key: str) -> dict:
    """Stats of one arm over several runs: per-task means over the runs, CI by bootstrap over tasks (one run: the plain run)."""
    def per_task(rs: list[list[dict]], only_hidden: bool) -> list[float]:
        tasks = sorted(t for t in {r["task"] for r in rs[0]} if not only_hidden or hidden[t] == "on")
        score = [{r["task"]: _num(r, "pass_rate") * PCT for r in run} for run in rs]
        return [_mean([sc[t] for sc in score]) for t in tasks]

    on = [[r for r in run if hidden[r["task"]] == "on"] for run in runs]
    all_est = hv.bootstrap_mean(per_task(runs, False), n_boot, seed, key)
    on_est = hv.bootstrap_mean(per_task(runs, True), n_boot, seed, f"{key}:hidden_on")
    flat = [r for run in runs for r in run]
    tested = [sum(r.get(TESTED_FLAG) == "True" for r in run) for run in on]
    return {
        "runs": len(runs), "n": len(runs[0]), "n_rows": len(flat),
        "pass_rate": all_est.mean, "ci_low": all_est.lo, "ci_high": all_est.hi,
        "pass_rate_per_run": [_mean([_num(r, "pass_rate") * PCT for r in run]) for run in runs],
        "mean_probes_used": _mean([_num(r, "probes_used") for r in flat]),
        "mean_completion_tokens": _mean([_num(r, "completion_tokens") for r in flat]),
        "hidden_header_tasks": len(on[0]),
        "tested_header_n_per_run": tested, "tested_header_n": _mean([float(t) for t in tested]),
        "hidden_pass_rate": on_est.mean, "hidden_ci_low": on_est.lo, "hidden_ci_high": on_est.hi,
        "hidden_pass_rate_per_run": [_mean([_num(r, "pass_rate") * PCT for r in run]) for run in on]}


def load_extension(results: Path, tasks: set[str]) -> dict:
    """Rows of the exploratory runs (R1, R2, and R3 when complete), each checked for one scored row per (task, arm, run)."""
    ext = {"r3_note": None}
    for name, rel in (("r1", EXT_R1), ("r2", EXT_R2)):
        if not (results / rel).is_file():
            raise SystemExit(f"error: missing {results / rel}")
        ext[name] = read_all_runs(results / rel)
        check_once(ext[name], tasks, rel.as_posix())
    r3_path = results / EXT_R3
    r3 = read_all_runs(r3_path) if r3_path.is_file() else []
    if len(r3) >= MIN_R3_ROWS:
        check_once(r3, tasks, EXT_R3.as_posix())
        ext["r3"] = r3
    else:
        ext["r3_note"] = (f"{EXT_R3.as_posix()} is missing" if not r3_path.is_file()
                          else f"{EXT_R3.as_posix()} holds {len(r3)} scored rows (fewer than {MIN_R3_ROWS})") + ": the max-effort result is skipped"
    return ext


def extension_blocks(beam: list[dict], ext: dict, hidden: dict[str, str], n_boot: int, seed: int) -> dict:
    """`p5_high_3runs`, `probe_curve_high` and (when R3 is complete) `max_effort`."""
    check_once(beam, set(hidden), am.CASE2_CSV.as_posix())
    main_p5 = by_run(beam, "P5", HIGH)  # protocol run 1
    p5_runs = [*main_p5, *by_run(ext["r1"], "P5", HIGH)]  # runs 2 and 3 are the R1 file's runs 1 and 2
    if len(p5_runs) != 3:
        raise SystemExit(f"error: expected 3 runs of P5 high, found {len(p5_runs)}")
    p5 = multi_run_cell(p5_runs, hidden, n_boot, seed, HEADLINE_KEY)
    placebo = multi_run_cell(by_run(beam, "placebo", HIGH), hidden, n_boot, seed, "probing:curve:placebo")
    curve = [("placebo", 0, placebo, "results/" + am.CASE2_CSV.as_posix(), "1 run"),
             ("P1", 1, multi_run_cell(by_run(ext["r2"], "P1", HIGH), hidden, n_boot, seed, "probing:curve:P1"), "results/" + EXT_R2.as_posix(), "1 run"),
             ("P2", 2, multi_run_cell(by_run(ext["r2"], "P2", HIGH), hidden, n_boot, seed, "probing:curve:P2"), "results/" + EXT_R2.as_posix(), "1 run"),
             ("P5", 5, p5, f"results/{am.CASE2_CSV.as_posix()} (run 1) and results/{EXT_R1.as_posix()} (runs 2 and 3)", "mean of 3 runs")]
    out = {
        "p5_high_3runs": {
            "protocol": EXT_PROTOCOL, "exploratory": True, "effort": HIGH, "arm": "P5",
            "source": f"run 1: results/{am.CASE2_CSV.as_posix()}; runs 2 and 3: results/{EXT_R1.as_posix()} (its `run` column says 1 and 2)",
            "ci_method": f"percentile bootstrap over tasks of per-task means over the 3 runs, {n_boot} resamples, key {HEADLINE_KEY}",
            **p5},
        "probe_curve_high": [{"budget": b, "arm": a, "runs_label": label, "source": src, **c} for a, b, c, src, label in curve],
        "extension": {"protocol": EXT_PROTOCOL, "exploratory": True, "r3_included": "r3" in ext, "r3_note": ext["r3_note"]}}
    if "r3" in ext:
        mx = multi_run_cell(by_run(ext["r3"], "P5", "max"), hidden, n_boot, seed, "probing:max:P5")
        out["max_effort"] = {"protocol": EXT_PROTOCOL, "exploratory": True, "arm": "P5", "source": "results/" + EXT_R3.as_posix(),
                             "max": mx, "high_mean_of_3": p5}
    return out


def build_summary(results: Path, n_boot: int, seed: int) -> dict:
    beam = load_beam(results)
    hidden = {r["task"]: r["hidden_header"] for r in beam if r["arm"] == "P5" and r["effort"] == "low"}
    others = load_other(results)
    unknown = {r["task"] for m in others.values() for rows in m["rows"].values() for r in rows} - set(hidden)
    if unknown:
        raise SystemExit(f"error: model tasks missing from the Beam Case 2 file: {sorted(unknown)[:5]}")
    first_rows = [next(iter(m["rows"].values()))[0] for m in others.values()]
    return {
        "n_boot": n_boot, "seed": seed, "pass_rate_unit": "percent (CSV scale is 0..1, multiplied by 100)",
        "sources": {"beam": f"results/{am.CASE2_CSV.as_posix()}", "other_models": "results/model_comparison/<slug>/{probe0,probe5}/results.csv",
                    "failure_kinds": "results/tierb50_v1.csv, tierb50_medium.csv, heal_v3_eval_{base,hint}_{low,medium,high}.csv, *_high_r2/_r3"},
        "hidden_header_tasks": sum(v == "on" for v in hidden.values()), "tasks": len(hidden),
        "other_models_missing_columns": [c for c in NO_PROBE_COLUMNS if any(c not in r for r in first_rows)],
        "beam": beam_block(beam, hidden, n_boot, seed),
        "other_models": other_block(others, hidden, n_boot, seed),
        "conditional_on_testing": conditional_block(beam, hidden, n_boot, seed),
        "failure_kinds": failure_block(results),
        **extension_blocks(beam, load_extension(results, set(hidden)), hidden, n_boot, seed),
    }


# ---- report ----------------------------------------------------------------


def _p(x: float | None) -> str:
    return "-" if x is None else f"{x:.1f}%"


def _ci(c: dict) -> str:
    return "-" if c["pass_rate"] is None else f"{c['pass_rate']:.1f}% [{c['ci_low']:.1f}, {c['ci_high']:.1f}]"


def _f(x: float | None, d: int = 2) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def beam_table(s: dict) -> list[str]:
    lines = ["| effort | arm | tasks | n | pass rate [95% CI] | probes used | header ablation | auth test | paging test | etag test | other | refused "
             "| tested header | invented endpoint >0 | hardcoded secret | mean completion tokens |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for effort in EFFORTS:
        for arm in ARMS:
            for split in SPLITS:
                c = s["beam"][effort][arm][split]
                pr = c["mean_probes"]
                tested = f"{c['tested_header_n']}/{c['n']} ({c['tested_header_share']:.0f}%)" if c["tested_header_share"] is not None else "-"
                lines.append(f"| {effort} | {arm} | hidden header {split} | {c['n']} | {_ci(c)} | {_f(c['mean_probes_used'])} | "
                             + " | ".join(_f(pr[k]) for k in ("header_ablation", "auth_test", "paging_test", "etag_test", "other", "refused"))
                             + f" | {tested} | {c['invented_endpoint_calls_gt0']} | {c['hardcoded_secret']} | {c['mean_completion_tokens']:,.0f} |")
    return lines


def other_table(s: dict) -> list[str]:
    lines = ["| model | effort | arm | tasks | n | pass rate [95% CI] | invented endpoint >0 | hardcoded secret | mean completion tokens |",
             "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for slug, m in s["other_models"].items():
        for arm in ARMS:
            for split in SPLITS:
                c = m["arms"][arm][split]
                lines.append(f"| {m['label']} | {m['effort']} | {arm} | hidden header {split} | {c['n']} | {_ci(c)} | {c['invented_endpoint_calls_gt0']} "
                             f"| {c['hardcoded_secret']} | {c['mean_completion_tokens']:,.0f} |")
    return lines


def conditional_table(s: dict) -> list[str]:
    lines = ["| model | effort | tested the header: n | pass rate [95% CI] | placebo, same tasks | did not test: n | pass rate [95% CI] | placebo, same tasks |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for c in s["conditional_on_testing"]:
        t, u = c["tested"], c["not_tested"]
        lines.append(f"| {c['model']} | {c['effort']} | {t['n']} | {_ci(t)} | {_p(t['placebo_pass_rate_same_tasks'])} | {u['n']} | {_ci(u)} "
                     f"| {_p(u['placebo_pass_rate_same_tasks'])} |")
    return lines


def failure_table(s: dict) -> list[str]:
    lines = []
    for name, by_effort in s["failure_kinds"].items():
        kinds = sorted({k for e in by_effort.values() for k, v in e["counts"].items() if v},
                       key=lambda k: -sum(e["counts"].get(k, 0) for e in by_effort.values()))
        lines += [f"### {name}", "", "| effort | runs | failed tests | " + " | ".join(KIND_LABEL.get(k, k) for k in kinds) + " |",
                  "|---|---:|---:|" + "---:|" * len(kinds)]
        for effort, e in by_effort.items():
            lines.append(f"| {effort} | {e['runs']} | {e['failed_tests']:.0f} | " + " | ".join(_p(e["shares"].get(k)) for k in kinds) + " |")
        lines.append("")
    return lines


def _runs(xs: list[float], d: int = 1) -> str:
    return " / ".join(f"{x:.{d}f}" for x in xs)


def extension_report(s: dict) -> list[str]:
    """Sections for the exploratory extension (protocol case2-ext-v1): P5 high in 3 runs, the probe budget curve, max effort."""
    p5, curve = s["p5_high_3runs"], s["probe_curve_high"]
    n_hid = p5["hidden_header_tasks"]
    lines = [
        "## Exploratory extension (protocol case2-ext-v1)", "",
        "Added after the main results, not pre-registered, and testing no pre-registered hypothesis (see `PREREG.md`, deviation of 2026-10-31). "
        "Same tasks, prompts and grader as the main run; only new probe budgets were added (`results/case2_probe/ext/PROTOCOL.md`). "
        "High effort, 50 tasks. `api_error` rows are left out; each (task, arm, run) has exactly one scored row.", "",
        "### P5 at high effort, 3 runs", "",
        f"Run 1 is the main run; runs 2 and 3 are `{EXT_R1.as_posix()}`. Mean of 3 = per-task mean over the runs, then the mean over tasks; "
        f"CI = percentile bootstrap over tasks ({s['n_boot']:,} resamples).", "",
        "| | run 1 | run 2 | run 3 | mean of 3 runs [95% CI] |", "|---|---:|---:|---:|---:|",
        f"| pass rate, all 50 tasks | " + " | ".join(f"{x:.1f}%" for x in p5["pass_rate_per_run"]) + f" | {_ci(p5)} |",
        f"| pass rate, {n_hid} hidden-header tasks | " + " | ".join(f"{x:.1f}%" for x in p5["hidden_pass_rate_per_run"])
        + f" | {p5['hidden_pass_rate']:.1f}% [{p5['hidden_ci_low']:.1f}, {p5['hidden_ci_high']:.1f}] |",
        f"| tested the header (of {n_hid}) | " + " | ".join(str(x) for x in p5["tested_header_n_per_run"]) + f" | {p5['tested_header_n']:.1f} |", "",
        "### Probe budget curve (high effort)", "",
        "Budget 0 is the placebo arm of the main run. Budgets 1 and 2 are 1 run (`" + EXT_R2.as_posix() + "`). Budget 5 is the mean of 3 runs; "
        "its tested-header count is the mean of the three runs.", "",
        f"| probe budget | runs | pass rate [95% CI] | mean probes used | tested header (of {n_hid}) | pass on hidden-header tasks [95% CI] |",
        "|---:|---|---:|---:|---:|---:|"]
    for c in curve:
        t = c["tested_header_n"]
        tested = f"{t:.0f}" if c["runs"] == 1 else f"{t:.1f} ({'/'.join(str(x) for x in c['tested_header_n_per_run'])})"
        lines.append(f"| {c['budget']} | {c['runs_label']} | {_ci(c)} | {_f(c['mean_probes_used'])} | {tested} "
                     f"| {c['hidden_pass_rate']:.1f}% [{c['hidden_ci_low']:.1f}, {c['hidden_ci_high']:.1f}] |")
    lines.append("")
    if "max_effort" in s:
        mx, hi = s["max_effort"]["max"], s["max_effort"]["high_mean_of_3"]
        lines += ["### P5 at max effort", "",
                  f"`{EXT_R3.as_posix()}`, 1 run. High is the mean of 3 runs from above. Tokens are mean completion tokens per task.", "",
                  f"| effort | runs | pass rate [95% CI] | mean probes used | tested header (of {n_hid}) | mean completion tokens |",
                  "|---|---|---:|---:|---:|---:|",
                  f"| max | 1 | {_ci(mx)} | {_f(mx['mean_probes_used'])} | {mx['tested_header_n']:.0f} | {mx['mean_completion_tokens']:,.0f} |",
                  f"| high | mean of 3 | {_ci(hi)} | {_f(hi['mean_probes_used'])} | {hi['tested_header_n']:.1f} "
                  f"({'/'.join(str(x) for x in hi['tested_header_n_per_run'])}) | {hi['mean_completion_tokens']:,.0f} |", ""]
    else:
        lines += ["### P5 at max effort", "", f"Not included: {s['extension']['r3_note']}.", ""]
    return lines


def render_report(s: dict) -> str:
    miss = s["other_models_missing_columns"]
    lines = [
        "# Case 2 probing: does Beam test its hypotheses?", "",
        f"Beam Case 2, arms placebo (no probes) and P5 (up to 5 probes), 1 run per task, {s['tasks']} Tier B tasks of which "
        f"{s['hidden_header_tasks']} hide a required header (`hidden_header` on). Pass rate = mean over tasks of the share of hidden tests "
        f"passed, in percent (the CSV scale is 0 to 1). CI = 95% percentile bootstrap over tasks ({s['n_boot']:,} resamples, seed {s['seed']}). "
        "Probe columns are mean counts per task. `tested header` = the model sent a header-ablation probe (`ablated_hidden_header`), "
        "counted on hidden-header tasks only. A missing code submission counts as 0.", "",
        "## Beam by effort", "", *beam_table(s), "",
        "## Other models", "",
        ("These files lack: " + ", ".join(f"`{c}`" for c in miss) + ". The hidden-header flag is joined by task name from the Beam file "
         "(same 50 tasks). Probe counts and the header-test flag are not recorded, so only pass rate, tokens and the two flag counts are "
         "compared, and no tested-vs-not-tested split is possible for these models."), "",
        *other_table(s), "",
        "## Pass rate when Beam tested the header, and when it did not", "",
        "Beam, arm P5, hidden-header tasks. The placebo column of each group is the no-probe pass rate on the same tasks: "
        "tasks where Beam chose to test are not a random sample, so the placebo column shows how hard those tasks are without probing. "
        "Groups are small at low and medium effort: read the CIs.", "",
        *conditional_table(s), "",
        "## Failure kinds (Beam, Case 1 and Case 3)", "",
        "Share of failing hidden tests by kind: the `fail_*` columns summed over tasks (over the 3 runs at high effort on these cases), "
        "divided by the total.", "",
        *failure_table(s),
        *extension_report(s)]
    return "\n".join(lines).rstrip() + "\n"


# ---- figures ---------------------------------------------------------------


def fig_probing(s: dict):
    import plot_final as pff
    from matplotlib.patches import Patch

    pff.SOURCE_NOTE = SOURCE_NOTE
    beam = pff.MODEL_COLOR["beam"]
    n = s["hidden_header_tasks"]
    title = "At higher effort Beam tests its hypotheses"
    fig, top, bottom = pff.frame(
        11.5, 5.4, title,
        f"Beam, Case 2, the {n} tasks whose recording hides a required header. Left: how often Beam sent a request that tests the header. "
        "Right: pass rate with no probes against up to 5 probes.",
        f"1 run per task, {n} tasks per bar. Tested = at least one header-ablation probe. GPT-OSS and Qwen logs do not record probe types, "
        "so they are not shown. Pass rate = share of hidden tests passed.", bottom_in=0.6)
    gs = fig.add_gridspec(1, 2, left=0.07, right=0.985, top=top, bottom=bottom, wspace=0.22)
    ax_l, ax_r = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
    data = {"left": [], "right": []}
    for ax in (ax_l, ax_r):
        pff.style_axes(ax, "y")
        ax.set_xlim(-0.6, 2.6)
        ax.set_ylim(0, 112)
        ax.set_xticks(range(3), list(EFFORTS), fontsize=9.5, color=pff.INK)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_xlabel("reasoning effort", fontsize=9, color=pff.INK_2)
    for i, e in enumerate(EFFORTS):
        c = s["beam"][e]["P5"]["on"]
        share = c["tested_header_share"]
        ax_l.bar(i, share, width=0.55, color=beam, edgecolor=beam, linewidth=1.3, zorder=3)
        ax_l.text(i, share + 1.5, f"{share:.0f}%\n{c['tested_header_n']}/{c['n']} tasks", ha="center", va="bottom", fontsize=8.5,
                  fontweight="bold", color=pff.INK, zorder=5, linespacing=1.2)
        data["left"].append({"effort": e, "tested_header_share": share, "tested_header_n": c["tested_header_n"], "n": c["n"]})
    width = 0.38
    for j, (arm, name) in enumerate((("placebo", "no probes"), ("P5", "up to 5 probes"))):
        for i, e in enumerate(EFFORTS):
            c = s["beam"][e][arm]["on"]
            x = i + (j - 0.5) * width
            ax_r.bar(x, c["pass_rate"], width=width * 0.94, color=beam if j else pff.BLUE_RAMP[1], edgecolor=beam, linewidth=1.3, zorder=3)
            ax_r.text(x, c["pass_rate"] + 1.5, f"{c['pass_rate']:.1f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold",
                      color=pff.INK, zorder=5)
            data["right"].append({"effort": e, "arm": name, "pass_rate": c["pass_rate"], "n": c["n"]})
    ax_r.legend(handles=[Patch(facecolor=pff.BLUE_RAMP[1], edgecolor=beam, label="no probes"),
                         Patch(facecolor=beam, edgecolor=beam, label="up to 5 probes")],
                loc="upper left", frameon=True, facecolor=pff.SURFACE, edgecolor=pff.GRID, fontsize=8.5, labelcolor=pff.INK, ncol=2,
                columnspacing=1.0, handlelength=1.2)
    ax_l.set_ylabel("tasks where Beam tested the header (%)", fontsize=9, color=pff.INK_2)
    ax_r.set_ylabel("pass rate on these tasks (%)", fontsize=9, color=pff.INK_2)
    ax_l.set_title("Beam tests the header more often", loc="left", fontsize=11, fontweight="bold", color=pff.INK, pad=6)
    ax_r.set_title("and passes more of these tasks", loc="left", fontsize=11, fontweight="bold", color=pff.INK, pad=6)
    return fig, {"title": title, **data}


CURVE_TITLE = "Each extra probe helps Beam; from two probes on it tests the header"


def fig_probe_curve(s: dict):
    import plot_final as pff
    from matplotlib.lines import Line2D

    pff.SOURCE_NOTE = SOURCE_NOTE
    beam, dark = pff.MODEL_COLOR["beam"], pff.BLUE_RAMP[4]
    curve, p5 = s["probe_curve_high"], s["p5_high_3runs"]
    n_hid = p5["hidden_header_tasks"]
    mx = s.get("max_effort", {}).get("max")
    note = ("Beam, Case 2, high effort, 50 tasks. 1 run per point except 5 probes, which is the mean of 3 runs (dots = the single runs). "
            "Exploratory runs (protocol case2-ext-v1). 95% CI = bootstrap over tasks "
            f"({s['n_boot']:,} resamples). Tested = at least one header-ablation probe.")
    if mx:
        note += " The max-effort point is 1 run."
    fig, top, bottom = pff.frame(
        11.5, 5.4, CURVE_TITLE,
        f"Left: pass rate by probe budget. Right: of the {n_hid} tasks whose recording hides a required header, how many Beam tested the header.",
        note, bottom_in=0.6)
    gs = fig.add_gridspec(1, 2, left=0.07, right=0.985, top=top, bottom=bottom, wspace=0.2)
    ax_l, ax_r = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
    xs = list(range(len(curve)))
    labels = ["0\n(no probes)", "1", "2", "5\n(mean of 3 runs)"]
    for ax in (ax_l, ax_r):
        pff.style_axes(ax, "y")
        ax.set_xlim(-0.6, len(curve) - 0.4 + (0.45 if mx else 0))
        ax.set_xticks(xs, labels, fontsize=9.5, color=pff.INK)
        ax.set_xlabel("probe budget (requests Beam may send to the practice server)", fontsize=9, color=pff.INK_2)
    ax_l.set_ylim(0, 112)
    ax_l.set_yticks([0, 25, 50, 75, 100])
    ax_r.set_ylim(0, n_hid * 1.2)
    ax_r.set_yticks(range(0, n_hid + 1, 5))
    ax_l.plot(xs, [c["pass_rate"] for c in curve], color=beam, lw=2, zorder=3)
    data = {"left": [], "right": []}
    for i, c in enumerate(curve):
        last = i == len(curve) - 1
        ax_l.errorbar(i, c["pass_rate"], yerr=[[c["pass_rate"] - c["ci_low"]], [c["ci_high"] - c["pass_rate"]]], fmt="D" if last else "o",
                      color=beam, ecolor=beam, elinewidth=1.4, capsize=4, ms=pff.MS + (1.5 if last else 0), mfc=dark if last else beam,
                      mec=beam, zorder=4)
        ax_l.text(i + 0.1, c["pass_rate"] - 3, f"{c['pass_rate']:.1f}", ha="left", va="top", fontsize=9, fontweight="bold", color=pff.INK, zorder=5)
        data["left"].append({"budget": c["budget"], "runs": c["runs"], "pass_rate": c["pass_rate"], "ci_low": c["ci_low"], "ci_high": c["ci_high"]})
        t = c["tested_header_n"]
        ax_r.bar(i, t, width=0.55, color=dark if last else beam, edgecolor=beam, linewidth=1.3, zorder=3)
        if last:
            ax_r.scatter([i] * c["runs"], c["tested_header_n_per_run"], s=26, color=pff.SURFACE, edgecolor=pff.INK, linewidth=1, zorder=6)
        text = f"{t:.1f}/{n_hid}" if last else f"{t:.0f}/{n_hid}"
        ax_r.text(i, max([t, *c["tested_header_n_per_run"]]) + 0.7, text, ha="center", va="bottom", fontsize=9, fontweight="bold", color=pff.INK, zorder=5)
        data["right"].append({"budget": c["budget"], "runs": c["runs"], "tested_header_n": t, "per_run": c["tested_header_n_per_run"],
                              "hidden_header_tasks": n_hid})
    handles = [Line2D([], [], marker="D", ls="", color=beam, mfc=dark, ms=8, label="5 probes: mean of 3 runs")]
    if mx:
        x = len(curve) - 1 + 0.45
        ax_l.errorbar(x, mx["pass_rate"], yerr=[[mx["pass_rate"] - mx["ci_low"]], [mx["ci_high"] - mx["pass_rate"]]], fmt="s", color=pff.SLATE[2],
                      ecolor=pff.SLATE[2], elinewidth=1.4, capsize=4, ms=pff.MS, zorder=4)
        ax_l.annotate(f"5 probes at max effort\n{mx['pass_rate']:.1f} (1 run)", (x, mx["pass_rate"]), xytext=(x - 0.1, max(mx["pass_rate"] - 22, 8)),
                      ha="center", va="top", fontsize=8.5, color=pff.INK, arrowprops=pff.LEADER)
        handles.append(Line2D([], [], marker="s", ls="", color=pff.SLATE[2], ms=8, label="5 probes, max effort (1 run)"))
        data["max_effort"] = {"pass_rate": mx["pass_rate"], "ci_low": mx["ci_low"], "ci_high": mx["ci_high"], "runs": 1,
                              "tested_header_n": mx["tested_header_n"]}
    ax_l.legend(handles=handles, loc="upper left", frameon=True, facecolor=pff.SURFACE, edgecolor=pff.GRID, fontsize=8.5, labelcolor=pff.INK)
    ax_l.set_ylabel("pass rate on all 50 tasks (%)", fontsize=9, color=pff.INK_2)
    ax_r.set_ylabel(f"hidden-header tasks where Beam tested the header (of {n_hid})", fontsize=9, color=pff.INK_2)
    ax_l.set_title("More probes, higher pass rate", loc="left", fontsize=11, fontweight="bold", color=pff.INK, pad=6)
    ax_r.set_title("From two probes on, Beam tests the header", loc="left", fontsize=11, fontweight="bold", color=pff.INK, pad=6)
    return fig, {"title": CURVE_TITLE, "protocol": EXT_PROTOCOL, **data}


def figure_kinds(failure: dict) -> list[str]:
    """Kinds that reach FIGURE_KINDS_MIN_SHARE in at least one bar of Case 1 or Case 3 base, most common first."""
    shown: dict[str, float] = {}
    for name in ("case1", "case3_base"):
        for e in failure[name].values():
            for k, v in e["shares"].items():
                if v is not None and v / PCT >= FIGURE_KINDS_MIN_SHARE:
                    shown[k] = shown.get(k, 0) + e["counts"][k]
    return sorted(shown, key=lambda k: -shown[k])


def fig_failure_kinds(s: dict):
    import plot_final as pff
    from matplotlib.patches import Patch

    pff.SOURCE_NOTE = SOURCE_NOTE
    failure = s["failure_kinds"]
    kinds = figure_kinds(failure)
    palette = [pff.pf.SERIES_COLORS[i] for i in (0, 1, 2, 3, 6, 4, 5)]
    colors = dict(zip(kinds, palette))
    colors["other"] = pff.SLATE[2]
    title = FAILURE_TITLE
    fig, top, bottom = pff.frame(
        12, 5.6, title,
        "Beam only. Each bar splits the failing hidden tests by kind; the number above a bar is how many tests failed.",
        "Case 1 Tier B (50 tasks) and Case 3 repair, no hint (64 tasks). High effort pools 3 runs; low and medium are 1 run. "
        "Kinds under 5% in every bar are merged into other.", bottom_in=0.9)
    gs = fig.add_gridspec(1, 2, left=0.055, right=0.985, top=top, bottom=bottom, wspace=0.12)
    data = []
    for g, (name, label) in enumerate((("case1", "Case 1: build"), ("case3_base", "Case 3: repair, no hint"))):
        ax = fig.add_subplot(gs[0, g])
        pff.style_axes(ax, "y")
        for i, e in enumerate(EFFORTS):
            row = failure[name][e]
            base = 0.0
            for k in [*kinds, "other"]:
                v = (row["shares"].get(k) if k != "other" else sum(x for kk, x in row["shares"].items() if kk not in kinds and x)) or 0.0
                if v <= 0:
                    continue
                ax.bar(i, v, bottom=base, width=0.62, color=colors[k], edgecolor=pff.SURFACE, linewidth=1.5, zorder=3)
                if v >= MIN_LABEL_SHARE:
                    ax.text(i, base + v / 2, f"{v:.0f}%", ha="center", va="center", fontsize=8.5, fontweight="bold",
                            color=pff.text_on(colors[k]), zorder=5)
                data.append({"case": name, "effort": e, "kind": k, "share": v})
                base += v
            ax.text(i, 101.5, f"{row['failed_tests']:.0f}", ha="center", va="bottom", fontsize=8.5, color=pff.INK_2)
        ax.set_xlim(-0.6, 2.6)
        ax.set_ylim(0, 108)
        ax.set_xticks(range(3), list(EFFORTS), fontsize=9.5, color=pff.INK)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_xlabel("reasoning effort", fontsize=9, color=pff.INK_2)
        ax.set_title(label, loc="left", fontsize=11, fontweight="bold", color=pff.INK, pad=6)
        if g == 0:
            ax.set_ylabel("share of failing tests (%)", fontsize=9, color=pff.INK_2)
    handles = [Patch(facecolor=colors[k], label=KIND_LABEL.get(k, k)) for k in [*kinds, "other"]]
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), bbox_to_anchor=(0.5, 0.115), frameon=False, fontsize=8.5,
               labelcolor=pff.INK, handlelength=1.2, columnspacing=1.2)
    return fig, {"title": title, "kinds": kinds, "bars": data}


def write_figures(s: dict, fig_dir: Path) -> list[str]:
    import plot_final as pff

    names = []
    makers = [("final_probing", fig_probing), ("final_probe_curve", fig_probe_curve), ("final_failure_kinds", fig_failure_kinds)]
    for name, maker in makers:
        fig, data = maker(s)
        pff.save(fig, fig_dir, name, data)
        names.append(name)
    return names


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--fig-dir", type=Path, default=DEFAULT_FIG_DIR)
    p.add_argument("--n-boot", type=int, default=hv.DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=hv.DEFAULT_SEED)
    p.add_argument("--no-figures", action="store_true")
    args = p.parse_args(argv)
    summary = build_summary(args.results_dir, args.n_boot, args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (args.out / "report.md").write_text(render_report(summary), encoding="utf-8")
    print(f"wrote {args.out / 'summary.json'} and {args.out / 'report.md'}")
    if not args.no_figures:
        for name in write_figures(summary, args.fig_dir):
            print(f"wrote {args.fig_dir / name}.{{png,svg,json}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
