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
Pass rate in the CSVs is 0..1; every number here is in percent. CI = 95% percentile bootstrap over tasks (same method and
seed as analyze_models.py, through heal_v3_verdicts.bootstrap_mean).

Writes results/analysis/probing/{summary.json,report.md} and results/figures/final_probing.{png,svg,json} and
final_failure_kinds.{png,svg,json}.
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
        *failure_table(s)]
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
    for name, maker in (("final_probing", fig_probing), ("final_failure_kinds", fig_failure_kinds)):
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
