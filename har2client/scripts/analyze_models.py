#!/usr/bin/env python3
"""Score the open models run through the Hugging Face router next to Beam: report, summary, two figures.

Usage:
  # after scripts/run_models.py has written runs/batch/models/<slug>/<suite>/results.csv:
  uv run --extra plots python scripts/analyze_models.py
  # same, with another results directory or fewer resamples (a quick look):
  uv run --extra plots python scripts/analyze_models.py --models-root runs/batch/models --n-boot 2000

Reads (read-only): the scored Beam CSVs in results/ (tierb50_v1.csv low and high, tierb50_medium.csv, and
heal_v3_eval_{base,hint}_{low,medium,high}.csv; each Beam effort is its own row, "Beam (low)" and so on). Beam high on
tierb50, v3base and v3hint is the per-task mean of 3 runs (run 1 plus `*_high_r2.csv`, `_r3.csv`, joined by scripts/high_runs.py);
Beam low and medium are 1 run. Beam Case 2 (probe0, probe5, probe20 = placebo, P5, P20) is 1 run, read from
results/case2_probe/main_full/results.csv. It also reads every
`<models-root>/<slug>/<suite>/results.csv` (suites: tierb50 = case 1 Tier B, v3base = case 3 base, v3hint = case 3 + hint).
Labels and the reasoning effort of a model come from data/models.json. With no model results the script still runs and writes
Beam-only outputs that say "no other models run yet".

Writes `results/analysis/models/{report.md,summary.json}` and `results/figures/models_leaderboard.{png,svg,json}` and
`models_score_vs_tokens.{png,svg,json}`.

Per model and suite: n tasks, pass rate with a 95% bootstrap CI over tasks (10,000 resamples), the share of fully solved tasks
(pass rate 1.0), mean output tokens per task (reasoning tokens included) and the count of `api_error` cells (a failed call is not
a model result: it is left out of every number). The report adds the paired difference (model minus Beam (high),
points) with its CI over the tasks both have; a partial suite is paired on its shared tasks and marked partial. The Pareto line and the shaded "fewer tokens, higher score" area are
drawn only when a panel holds 3 or more distinct models (Beam counts as one).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import heal_v3_verdicts as hv  # noqa: E402
import high_runs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results"
DEFAULT_MODELS_ROOT = ROOT / "runs" / "batch" / "models"
DEFAULT_MODELS_FILE = ROOT / "data" / "models.json"
DEFAULT_OUT = ROOT / "results" / "analysis" / "models"
DEFAULT_FIG_DIR = ROOT / "results" / "figures"
API_ERROR = "api_error"
SUITES = {"tierb50": "Case 1 Tier B", "v3base": "Case 3 base", "v3hint": "Case 3 + hint",
          "probe0": "Case 2 no probes", "probe5": "Case 2 up to 5 probes", "probe20": "Case 2 up to 20 probes"}
CASE2_ARMS = {"probe0": "placebo", "probe5": "P5", "probe20": "P20"}  # suite -> `arm` column of the Case 2 CSV
CASE2_CSV = Path("case2_probe") / "main_full" / "results.csv"  # complete Beam Case 2 run: 3 arms x 3 efforts x 50 tasks, 1 run
HIGH = "high"
FIGURE_SUITES = ("tierb50", "v3base")  # the figures show case 1 and case 3 base
EFFORTS = ("low", "medium", "high")
REFERENCE = "Beam (high)"
MIN_MODELS_FOR_PARETO = 3
BEAM_COLOR_INDEX = 0  # pf.SERIES_COLORS[0]
OTHER_COLOR = "#8d8c88"  # neutral ink-gray for every non-Beam model
SOLVED_EPS = 1e-9
FOOTER_IN, FOOTER_GAP_IN = 0.1, 0.2  # footer baselines, inches from the bottom edge
TICKS_TOKENS = (200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000)
PCT = 100.0


@dataclass(frozen=True)
class Entry:
    """One row of the leaderboard: a Beam effort or an other model, on one suite."""

    key: str  # "beam-high" or the model slug
    label: str
    kind: str  # "beam" or "model"
    suite: str
    effort: str | None
    scores: Mapping[str, float]  # task -> pass rate (0..1), api_error cells left out
    tokens: Mapping[str, float]  # task -> output tokens, where known
    api_errors: int
    model_id: str | None = None
    runs: int = 1  # runs averaged per task: 3 for Beam high on tierb50, v3base, v3hint; 1 elsewhere


# ---- loading ---------------------------------------------------------------


def _num(value: object) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except ValueError:
        return None


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _read_rows(path: Path, effort: str | None, arm: str | None) -> list[dict]:
    """Run-1 rows of one results CSV, limited to `effort` and `arm` when given."""
    with open(path, newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(fh)
                if (effort is None or row.get("effort", effort) == effort)
                and (arm is None or row.get("arm") == arm)
                and (row.get("run") or "1") == "1"]


def read_cells(path: Path, effort: str | None = None, arm: str | None = None, repeats: tuple[Path, ...] = (),
               ) -> tuple[dict[str, float], dict[str, float], int]:
    """(task -> pass rate, task -> output tokens, api_error count) from one results CSV (run 1 only).

    With `repeats` (further run CSVs of the same cells) the rows are joined by `high_runs.merge_runs`, which raises
    `high_runs.RunMismatchError` unless every run holds one scored row per task, and each task is the mean over the runs.
    A row that is not `api_error` counts, `no_code` and `no_submit` included, with a missing pass rate as 0.
    """
    rows = _read_rows(path, effort, arm)
    if repeats:
        rows = high_runs.merge_runs([rows, *(_read_rows(p, effort, arm) for p in repeats)])
    passes: dict[str, list[float]] = {}
    tokens: dict[str, list[float]] = {}
    errors = 0
    for row in rows:
        if row.get("status") == API_ERROR:
            errors += 1
            continue
        task = row["task"]
        passes.setdefault(task, []).append(_num(row.get("pass_rate")) or 0.0)
        if (tok := _num(row.get("completion_tokens"))) is not None:
            tokens.setdefault(task, []).append(tok)
    return ({t: _mean(v) for t, v in passes.items()}, {t: _mean(v) for t, v in tokens.items()}, errors)


def beam_sources(results_dir: Path) -> dict[tuple[str, str], Path]:
    """(suite, effort) -> scored Beam CSV that exists. Case 2 comes from the one complete `case2_probe/main_full` CSV."""
    results_dir = Path(results_dir)
    found = {}
    for effort in EFFORTS:
        candidates = {
            "tierb50": results_dir / ("tierb50_medium.csv" if effort == "medium" else "tierb50_v1.csv"),
            "v3base": results_dir / f"heal_v3_eval_base_{effort}.csv",
            "v3hint": results_dir / f"heal_v3_eval_hint_{effort}.csv",
        }
        found.update({(suite, effort): p for suite, p in candidates.items() if p.is_file()})
        if (case2 := results_dir / CASE2_CSV).is_file():
            found.update({(suite, effort): case2 for suite in CASE2_ARMS})
    return found


def beam_repeats(results_dir: Path, suite: str, effort: str) -> list[Path]:
    """Run 2 and 3 CSVs (`*_high_r2.csv`, `_r3.csv`) of a Beam high step that has them; low, medium and Case 2 have none."""
    stems = {"tierb50": "tierb50_high", "v3base": "heal_v3_eval_base_high", "v3hint": "heal_v3_eval_hint_high"}
    return high_runs.extra_run_paths(Path(results_dir), stems[suite]) if effort == HIGH and suite in stems else []


def load_beam(results_dir: Path) -> list[Entry]:
    entries = []
    for (suite, effort), path in sorted(beam_sources(results_dir).items()):
        repeats = tuple(beam_repeats(results_dir, suite, effort))
        scores, tokens, errors = read_cells(path, effort, CASE2_ARMS.get(suite), repeats)
        if scores or errors:
            entries.append(Entry(f"beam-{effort}", f"Beam ({effort})", "beam", suite, effort, scores, tokens, errors,
                                 runs=1 + len(repeats)))
    return entries


def load_model_meta(models_file: Path) -> dict[str, dict]:
    try:
        models = json.loads(Path(models_file).read_text(encoding="utf-8")).get("models", [])
    except (OSError, ValueError):
        return {}
    return {m["slug"]: m for m in models if "slug" in m}


def load_models(models_root: Path, models_file: Path) -> list[Entry]:
    meta = load_model_meta(models_file)
    root = Path(models_root)
    entries = []
    for model_dir in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        info = meta.get(model_dir.name, {})
        for suite in SUITES:
            path = model_dir / suite / "results.csv"
            if not path.is_file():
                continue
            scores, tokens, errors = read_cells(path)
            if scores or errors:
                entries.append(Entry(model_dir.name, info.get("label") or model_dir.name, "model", suite,
                                     info.get("reasoning_effort"), scores, tokens, errors,
                                     (info.get('id') or model_dir.name).split(':')[0]))
    return entries


# ---- numbers ---------------------------------------------------------------


def display_label(label: str, runs: int) -> str:
    """`Beam (high, mean of 3 runs)` for a Beam step with several runs per task, else the label."""
    return f"{label[:-1]}, mean of {runs} runs)" if runs > 1 and label.endswith(")") else label


def entry_stats(entry: Entry, n_boot: int, seed: int) -> dict:
    values = [v * PCT for _, v in sorted(entry.scores.items())]
    est = hv.bootstrap_mean(values, n_boot, seed, f"models:{entry.suite}:{entry.key}")
    tokens = list(entry.tokens.values())
    return {
        "key": entry.key, "label": entry.label, "kind": entry.kind, "suite": entry.suite, "effort": entry.effort,
        "model_id": entry.model_id, "runs": entry.runs,
        "display_label": display_label(entry.label, entry.runs) if entry.kind == "beam" else entry.label,
        "n": len(values),
        "pass_rate": est.mean if est else None,
        "ci_low": est.lo if est else None,
        "ci_high": est.hi if est else None,
        "full_solve_pct": PCT * sum(v >= 1.0 - SOLVED_EPS for v in entry.scores.values()) / len(values) if values else None,
        "mean_output_tokens": _mean(tokens) if tokens else None,
        "api_errors": entry.api_errors,
        "paired_vs_beam_high": None,
    }


def paired_vs_reference(entry: Entry, ref: Entry | None, n_boot: int, seed: int) -> dict | None:
    """Model minus Beam (high), in points, over the tasks both have. `partial` is true when that is not every task of both.

    None when there is no reference or no task in common. A partial suite (Qwen `v3hint`) pairs on the shared tasks only.
    """
    if ref is None or entry.key == ref.key or not (shared := sorted(set(entry.scores) & set(ref.scores))):
        return None
    diffs = [(entry.scores[t] - ref.scores[t]) * PCT for t in shared]
    est = hv.bootstrap_mean(diffs, n_boot, seed, f"models-diff:{entry.suite}:{entry.key}")
    return {"n": est.n, "n_model": len(entry.scores), "n_ref": len(ref.scores),
            "partial": len(shared) < max(len(entry.scores), len(ref.scores)),
            "diff": est.mean, "ci_low": est.lo, "ci_high": est.hi,
            "excludes_zero": est.lo > 0 or est.hi < 0} if est else None


def pareto_frontier(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Points (tokens, score) that no other point beats: fewer or equal tokens and higher or equal score, one strict."""
    keep = [p for p in points
            if not any(q != p and q[0] <= p[0] and q[1] >= p[1] for q in points)]
    return sorted(set(keep))


def build_stats(entries: list[Entry], n_boot: int, seed: int) -> list[dict]:
    refs = {e.suite: e for e in entries if e.label == REFERENCE}
    same_effort = {(e.suite, e.effort): e for e in entries if e.kind == 'beam'}
    rows = []
    for entry in entries:
        row = entry_stats(entry, n_boot, seed)
        row["paired_vs_beam_high"] = paired_vs_reference(entry, refs.get(entry.suite), n_boot, seed)
        matched = same_effort.get((entry.suite, entry.effort)) if entry.kind == 'model' and entry.effort else None
        row['paired_vs_beam_same_effort'] = paired_vs_reference(entry, matched, n_boot, seed)
        rows.append(row)
    return rows


def pareto_info(entries: list[Entry], stats: list[dict]) -> dict[str, dict]:
    """Per figure suite: how many distinct models it holds, whether the Pareto line is drawn, and its points."""
    out = {}
    for suite in FIGURE_SUITES:
        rows = [r for r in stats if r["suite"] == suite and r["n"] and r["mean_output_tokens"]]
        models = {(r.get('model_id') or r['key']) if r["kind"] == "model" else "beam" for r in rows}
        drawn = len(models) >= MIN_MODELS_FOR_PARETO
        points = [(r["mean_output_tokens"], r["pass_rate"]) for r in rows]
        frontier = pareto_frontier(points) if drawn else []
        out[suite] = {"distinct_models": len(models), "drawn": drawn,
                      "frontier": [{"tokens": x, "pass_rate": y} for x, y in frontier]}
    return out


# ---- report ----------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.1f}%"


def _tok(x: float | None) -> str:
    return "-" if x is None else f"{x:,.0f}"


def _ci(row: dict) -> str:
    return "-" if row["pass_rate"] is None else f"{row['pass_rate']:.1f}% [{row['ci_low']:.1f}, {row['ci_high']:.1f}]"


def _paired(d: dict) -> str:
    text = f"{d['diff']:+.1f} [{d['ci_low']:+.1f}, {d['ci_high']:+.1f}]"
    return f"{text}, partial: n={d['n']} shared of {d['n_ref']} Beam" if d["partial"] else text


def _diff(row: dict, suite_has_ref: bool) -> str:
    d = row["paired_vs_beam_high"]
    if d:
        return _paired(d)
    if row["label"] == REFERENCE or not suite_has_ref:
        return "-"
    return "no shared tasks"


def render_report(stats: list[dict], pareto: dict[str, dict], n_boot: int, seed: int) -> str:
    others = sorted({r["key"] for r in stats if r["kind"] == "model"})
    lines = ["# Open models vs Beam", ""]
    if others:
        identities = {r.get('model_id') or r['key'] for r in stats if r['kind'] == 'model'}
        lines.append(f"{len(identities)} other model(s), {len(others)} setting(s) scored: {', '.join(others)}.")
    else:
        lines.append("No other models run yet. Only the Beam rows are scored; run `scripts/run_models.py` and this script again.")
    lines += ["", "Pass rate = mean over tasks of the share of hidden tests passed. Runs per task: Beam (high) on Case 1 and Case 3 is the "
              "mean of 3 runs (PREREG.md deviation 2026-10-27); Beam low and medium, Beam Case 2 and every other model are 1 run. "
              "Full solve = share of tasks at "
              f"pass rate 1.0. CI = 95% percentile bootstrap over tasks ({n_boot:,} resamples, seed {seed}). Output tokens include "
              "reasoning tokens. `api_error` cells are not model results: they are left out and counted. "
              f"Difference = model minus {REFERENCE}, in points, paired over the tasks both have; a partial suite is paired on its shared "
              "tasks only and marked partial. A missing code submission (`no_code`, `no_submit`) scores 0.", ""]
    for suite, title in SUITES.items():
        rows = sorted((r for r in stats if r["suite"] == suite), key=lambda r: -(r["pass_rate"] if r["pass_rate"] is not None else -1))
        if not rows:
            continue
        has_ref = any(r["label"] == REFERENCE for r in rows)
        lines += [f"## {title} (`{suite}`)", "",
                  *([f"Beam: 1 run per task (`results/{CASE2_CSV.as_posix()}`, arm {CASE2_ARMS[suite]}).", ""] if suite in CASE2_ARMS else []),
                  "| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs " + REFERENCE + " [95% CI] |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for r in rows:
            lines.append(f"| {r['display_label']} | {r['n']} | {_ci(r)} | {_pct(r['full_solve_pct'])} | {_tok(r['mean_output_tokens'])} "
                         f"| {r['api_errors']} | {_diff(r, has_ref)} |")
        if suite in pareto:
            p = pareto[suite]
            note = (f"Pareto line drawn: {len(p['frontier'])} frontier point(s)." if p["drawn"] else
                    f"No Pareto line: {p['distinct_models']} distinct model(s), need {MIN_MODELS_FOR_PARETO}.")
            lines += ["", note]
        lines.append("")
        matched = [r for r in rows if r.get('paired_vs_beam_same_effort')]
        if matched:
            lines += ['Matched-effort comparisons (model minus Beam at the same setting):', '']
            for r in matched:
                d = r['paired_vs_beam_same_effort']
                lines.append(f"- {r['label']}: {_paired(d)} points.")
            lines.append('')
    lines += ["## Sources", "",
              "- Beam rows: `results/tierb50_v1.csv` (low, high), `results/tierb50_medium.csv`, `results/heal_v3_eval_{base,hint}_{low,medium,high}.csv`; "
              "Beam high also `results/tierb50_high_r{2,3}.csv`, `results/heal_v3_eval_{base,hint}_high_r{2,3}.csv` (mean of 3 runs).",
              f"- Beam Case 2: `results/{CASE2_CSV.as_posix()}` (1 run).",
              "- Other models: `runs/batch/models/<slug>/<suite>/results.csv`, written by `scripts/run_models.py`.", ""]
    return "\n".join(lines)


def build_summary(stats: list[dict], pareto: dict[str, dict], n_boot: int, seed: int) -> dict:
    others = sorted({r["key"] for r in stats if r["kind"] == "model"})
    identities = sorted({r.get('model_id') or r['key'] for r in stats if r['kind'] == 'model'})
    return {"n_boot": n_boot, "seed": seed, "reference": REFERENCE, "other_models": others,
            "other_model_ids": identities,
            "note": None if others else "no other models run yet", "pareto": pareto, "rows": stats}


# ---- figures ---------------------------------------------------------------


def _footer(stats: list[dict]) -> str:
    import story_figs.style as S

    n = {s: max((r["n"] for r in stats if r["suite"] == s), default=0) for s in FIGURE_SUITES}
    coverage = ', '.join(f'{suite}: n={count}' for suite, count in n.items())
    return (f"{S.MODEL_NAME} and open models via the HF router · {coverage} · "
            f"Beam high: mean of 3 runs, other rows: 1 run per task · 95% CI over tasks · HAR2Client {S.git_sha()}")


def _finish(fig, out_dir: Path, name: str, data: dict, stats: list[dict], note: str, n_boot: int, seed: int, bars: bool = True) -> None:
    import matplotlib.pyplot as plt
    import story_figs.style as S

    footer = _footer(stats)
    height = fig.get_size_inches()[1]
    fig.text(0.012, FOOTER_IN / height, footer, fontsize=7.5, color=S.INK_2, ha="left", va="bottom")
    fig.text(0.012, (FOOTER_IN + FOOTER_GAP_IN) / height, (f"{note} Error bars: 95% bootstrap over tasks ({n_boot:,} resamples, seed {seed})." if bars else note),
             fontsize=7.5, color=S.INK_2, ha="left", va="bottom")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{name}.png", dpi=S.DPI, facecolor=S.SURFACE)
    fig.savefig(out_dir / f"{name}.svg", facecolor=S.SURFACE)
    (out_dir / f"{name}.json").write_text(json.dumps({**data, "footer": footer}, indent=2) + "\n", encoding="utf-8")
    plt.close(fig)


def _panel_stats(stats: list[dict]) -> dict[str, list[dict]]:
    panels = {s: [r for r in stats if r["suite"] == s and r["n"]] for s in FIGURE_SUITES}
    return {s: rows for s, rows in panels.items() if rows}


def draw_leaderboard(stats: list[dict], out_dir: Path, n_boot: int, seed: int) -> str | None:
    """Ranked horizontal bars with CI, one panel per suite. Returns the file stem, or None when there is nothing to draw."""
    panels = _panel_stats(stats)
    if not panels:
        return None
    import story_figs.style as S
    from matplotlib.ticker import FuncFormatter

    plt = S.setup()
    beam_color = S.pf.SERIES_COLORS[BEAM_COLOR_INDEX]
    others = {r["key"] for r in stats if r["kind"] == "model"}
    rows_max = max(len(rows) for rows in panels.values())
    height = 2.4 + 0.4 * rows_max
    fig, axes = plt.subplots(1, len(panels), figsize=(5.8 * len(panels) + 1.0, height), squeeze=False)
    fig.subplots_adjust(left=0.115 if len(panels) > 1 else 0.2, right=0.93, top=1 - 1.05 / height, bottom=0.95 / height, wspace=0.52)
    subtitle = "Higher is better. Share of hidden tests passed, mean over tasks."
    if not others:
        subtitle += " No other models run yet: Beam only."
    S.headline(fig, "Pass rate by model", subtitle)
    data = {"title": "Pass rate by model", "panels": {}}
    for ax, (suite, rows) in zip(axes[0], panels.items()):
        rows = sorted(rows, key=lambda r: -r["pass_rate"])
        S.style_axes(ax, "x")
        ys = list(range(len(rows)))
        colors = [beam_color if r["kind"] == "beam" else OTHER_COLOR for r in rows]
        ax.barh(ys, [r["pass_rate"] for r in rows], height=0.62, color=colors, zorder=3)
        ax.errorbar([r["pass_rate"] for r in rows], ys,
                    xerr=[[r["pass_rate"] - r["ci_low"] for r in rows], [r["ci_high"] - r["pass_rate"] for r in rows]],
                    fmt="none", ecolor=S.INK, elinewidth=1.1, capsize=2.5, zorder=4)
        for y, r, color in zip(ys, rows, colors):
            ax.text(r["ci_high"] + 1.4, y, f"{r['pass_rate']:.1f}%", va="center", ha="left", fontsize=8.5, zorder=5,
                    color=S.INK, fontweight="bold" if r["kind"] == "beam" else "normal", clip_on=False)
        ax.set_yticks(ys)
        ax.set_yticklabels([r["display_label"] for r in rows], fontsize=9, color=S.INK)
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.set_title(f"{SUITES[suite]} (n={max(r['n'] for r in rows)})", loc="left", fontsize=10.5, fontweight="bold", color=S.INK)
        data["panels"][suite] = [{k: r[k] for k in ("label", "display_label", "kind", "n", "runs", "pass_rate", "ci_low", "ci_high")} for r in rows]
    _finish(fig, out_dir, "models_leaderboard", data, stats,
            "Beam efforts in blue, other models in gray. Beam high: mean of 3 runs; all else 1 run; CI over tasks only.", n_boot, seed)
    return "models_leaderboard"


Y_PAD = 10.0  # points of room above and below the data in the score-vs-tokens panels
LABEL_OFFSETS = ((8, -14, "left"), (8, 6, "left"), (-8, 6, "right"), (-8, -14, "right"), (0, 11, "center"), (0, -17, "center"),
                 (8, -26, "left"), (8, 18, "left"), (-8, -26, "right"), (-8, 18, "right"))  # (dx, dy, ha), in points
MARKER_BOX_PX = 14


def _place_labels(ax, items: list[tuple[float, float, str, bool]]) -> None:
    """Annotate each (x, y, text, bold) at the first offset whose text box hits no earlier label and no marker."""
    import story_figs.style as S
    from matplotlib.transforms import Bbox

    fig = ax.figure
    renderer = fig.canvas.get_renderer()
    boxes = []
    for x, y, *_ in items:
        px, py = ax.transData.transform((x, y))
        boxes.append(Bbox.from_extents(px - MARKER_BOX_PX / 2, py - MARKER_BOX_PX / 2, px + MARKER_BOX_PX / 2, py + MARKER_BOX_PX / 2))
    for x, y, text, bold in items:
        best = None
        for dx, dy, ha in LABEL_OFFSETS:
            note = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, fontsize=8.5, color=S.INK,
                               fontweight="bold" if bold else "normal", path_effects=S.halo(), zorder=5)
            box = note.get_window_extent(renderer)
            axes_box = ax.get_window_extent(renderer)
            hits = sum(box.overlaps(b) for b in boxes) + (0 if axes_box.x0 <= box.x0 and box.x1 <= axes_box.x1 else 1)
            if best is None or hits < best[0]:
                if best is not None:
                    best[1].remove()
                best = (hits, note, box)
            else:
                note.remove()
            if best[0] == 0:
                break
        boxes.append(best[2])


def draw_score_vs_tokens(stats: list[dict], pareto: dict[str, dict], out_dir: Path, n_boot: int, seed: int) -> str | None:
    """x = output tokens per task (log), y = pass rate. Beam efforts joined by a line, other models as labeled points."""
    panels = {s: [r for r in rows if r["mean_output_tokens"]] for s, rows in _panel_stats(stats).items()}
    panels = {s: rows for s, rows in panels.items() if rows}
    if not panels:
        return None
    import story_figs.style as S
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

    plt = S.setup()
    beam_color = S.pf.SERIES_COLORS[BEAM_COLOR_INDEX]
    markers = S.pf.SERIES_MARKERS
    others = {r["key"] for r in stats if r["kind"] == "model"}
    fig, axes = plt.subplots(1, len(panels), figsize=(5.8 * len(panels) + 1.0, 5.4), squeeze=False)
    fig.subplots_adjust(left=0.085 if len(panels) > 1 else 0.14, right=0.97, top=0.82, bottom=0.17, wspace=0.2)
    subtitle = "Higher and further left is better: more tests passed for fewer output tokens per task (log scale)."
    if not others:
        subtitle += " No other models run yet: Beam only."
    S.headline(fig, "Score against output tokens", subtitle)
    data = {"title": "Score against output tokens", "panels": {}}
    for ax, (suite, rows) in zip(axes[0], panels.items()):
        S.style_axes(ax, "both")
        xs_all = [r["mean_output_tokens"] for r in rows]
        ys_all = [r["pass_rate"] for r in rows]
        lo, hi = min(xs_all) / 1.7, max(xs_all) * 2.2
        y_lo, y_hi = max(0.0, (min(ys_all) - Y_PAD) // 10 * 10), min(100.0, -(-(max(ys_all) + Y_PAD) // 10) * 10)
        ax.set_xscale("log")
        ax.set_xlim(lo, hi)
        ax.set_ylim(y_lo, y_hi)
        p = pareto.get(suite, {})
        if p.get("drawn") and p["frontier"]:
            fx = [lo] + [f["tokens"] for f in p["frontier"]] + [hi]
            fy = [y_lo] + [f["pass_rate"] for f in p["frontier"]] + [p["frontier"][-1]["pass_rate"]]
            ax.fill_between(fx, fy, y_hi, step="post", color=S.pf.REGION_COLOR, alpha=0.2, linewidth=0, zorder=1)
            ax.step(fx[1:], fy[1:], where="post", color=S.INK_2, linewidth=1.2, linestyle=(0, (4, 3)), zorder=2)
            ax.text(lo * 1.06, y_hi - 0.03 * (y_hi - y_lo), "fewer tokens, higher score", fontsize=8.5, color=S.INK_2, va="top",
                    ha="left", style="italic", zorder=2)
        beam = sorted((r for r in rows if r["kind"] == "beam"), key=lambda r: EFFORTS.index(r["effort"]))
        if beam:
            bx, by = [r["mean_output_tokens"] for r in beam], [r["pass_rate"] for r in beam]
            ax.plot(bx, by, color=beam_color, linewidth=2, zorder=3)
            ax.plot(bx, by, linestyle="none", marker="o", markersize=8.5, markerfacecolor=beam_color,
                    markeredgecolor=S.SURFACE, markeredgewidth=1.8, zorder=4, clip_on=False)
        model_rows = sorted((r for r in rows if r["kind"] == "model"), key=lambda r: r["mean_output_tokens"])
        for i, r in enumerate(model_rows):
            ax.plot([r["mean_output_tokens"]], [r["pass_rate"]], linestyle="none", marker=markers[(i + 1) % len(markers)], markersize=8.5,
                    markerfacecolor=OTHER_COLOR, markeredgecolor=S.SURFACE, markeredgewidth=1.5, zorder=4, clip_on=False)
        fig.canvas.draw()  # the label placer needs the final axes limits
        _place_labels(ax, [(r["mean_output_tokens"], r["pass_rate"], f"Beam {r['effort']}" + (f" (mean of {r['runs']} runs)" if r["runs"] > 1 else ""), True) for r in beam]
                      + [(r["mean_output_tokens"], r["pass_rate"], r["label"], False) for r in model_rows])
        ax.xaxis.set_major_locator(FixedLocator([t for t in TICKS_TOKENS if lo <= t <= hi]))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: S.fmt_k(v)))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.set_xlabel("output tokens per task (log)", fontsize=9, color=S.INK_2)
        if ax is axes[0][0]:
            ax.set_ylabel("pass rate", fontsize=9, color=S.INK_2)
        ax.set_title(f"{SUITES[suite]} (n={max(r['n'] for r in rows)})", loc="left", fontsize=10.5, fontweight="bold", color=S.INK)
        data["panels"][suite] = {
            "points": [{k: r[k] for k in ("label", "display_label", "kind", "effort", "runs", "mean_output_tokens", "pass_rate")} for r in rows],
            "pareto": p}
    _finish(fig, out_dir, "models_score_vs_tokens", data, stats,
            "Mean output tokens per task include reasoning tokens. Pareto line only with 3 or more models.", n_boot, seed, bars=False)
    return "models_score_vs_tokens"


# ---- CLI -------------------------------------------------------------------


def analyze(results_dir: Path, models_root: Path, models_file: Path, n_boot: int, seed: int,
            suites: tuple[str, ...] | None = None) -> tuple[list[Entry], list[dict], dict]:
    entries = load_beam(results_dir) + load_models(models_root, models_file)
    if suites:
        entries = [e for e in entries if e.suite in suites]
    stats = build_stats(entries, n_boot, seed)
    return entries, stats, pareto_info(entries, stats)


def main(argv: list[str] | None = None) -> int:
    global FIGURE_SUITES
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS, help="scored Beam CSVs (read-only)")
    p.add_argument("--models-root", type=Path, default=DEFAULT_MODELS_ROOT, help="<slug>/<suite>/results.csv of the other models")
    p.add_argument("--models-file", type=Path, default=DEFAULT_MODELS_FILE)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report.md and summary.json go here")
    p.add_argument("--fig-dir", type=Path, default=DEFAULT_FIG_DIR)
    p.add_argument("--n-boot", type=int, default=hv.DEFAULT_N_BOOT)
    p.add_argument("--seed", type=int, default=hv.DEFAULT_SEED)
    p.add_argument("--no-figures", action="store_true")
    p.add_argument("--suites", nargs="+", choices=list(SUITES), help="suites to include in the report, summary.json and figures (default: all available)")
    p.add_argument('--figure-suites', nargs='+', choices=list(SUITES), help='suites to plot; report always includes all available suites')
    args = p.parse_args(argv)
    if args.figure_suites:
        FIGURE_SUITES = tuple(args.figure_suites)
    if args.suites:
        FIGURE_SUITES = tuple(s for s in FIGURE_SUITES if s in args.suites)

    entries, stats, pareto = analyze(args.results_dir, args.models_root, args.models_file, args.n_boot, args.seed,
                                     tuple(args.suites) if args.suites else None)
    if not entries:
        print(f"error: no Beam CSVs in {args.results_dir} and no model results in {args.models_root}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(render_report(stats, pareto, args.n_boot, args.seed), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(build_summary(stats, pareto, args.n_boot, args.seed), indent=2) + "\n", encoding="utf-8")
    others = sorted({r["key"] for r in stats if r["kind"] == "model"})
    print(f"{len(stats)} row(s), {len(others)} other model setting(s)" + ("" if others else ": no other models run yet") + f" -> {args.out / 'report.md'}")
    if not args.no_figures:
        try:
            for stem in (draw_leaderboard(stats, args.fig_dir, args.n_boot, args.seed),
                         draw_score_vs_tokens(stats, pareto, args.fig_dir, args.n_boot, args.seed)):
                if stem:
                    print(f"figure: {args.fig_dir / stem}.png")
        except ImportError as exc:  # matplotlib is an optional extra
            print(f"warning: figures skipped ({exc}); run with `uv run --extra plots`", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
