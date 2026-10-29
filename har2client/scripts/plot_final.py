#!/usr/bin/env python3
"""Final-results figures: Beam, GPT-OSS-120B and Qwen3.5-397B on the three cases (5 benchmarks).

Usage:
  uv run --extra plots python scripts/plot_final.py [--out-dir results/figures]

Reads (read-only, no number is typed in here):
  results/analysis/model_comparison/summary.json   written by scripts/analyze_models.py: pass rate, 95% CI over tasks,
                                                   mean output tokens and the paired difference to Beam (high) per setting
  results/analysis/tierb50_effort/summary.json     Beam hidden-header split (Case 1)
  results/analysis/heal_controls/summary.json      H10: header hidden + hint / + evidence
Beam high on Cases 1 and 3 is the per-task mean of 3 runs (scripts/high_runs.py via analyze_models.py); every other cell is 1 run.
Case 2 P20 (probe20) is dropped on load and appears in no figure.

Writes results/figures/final_{scoreboard,leaderboard,effort_curves,information_gain,gap_vs_beam,beam_missing_info,cost_vs_score}
as .png (200 dpi), .svg and .json (the plotted numbers).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import plot_frontier as pf  # noqa: E402
from story_figs.style import DPI, GRID, INK, INK_2, SLATE, SURFACE, halo, setup, style_axes, text_on  # noqa: E402

SUMMARY = ROOT / "results" / "analysis" / "model_comparison" / "summary.json"
EFFORT_SUMMARY = ROOT / "results" / "analysis" / "tierb50_effort" / "summary.json"
CONTROLS_SUMMARY = ROOT / "results" / "analysis" / "heal_controls" / "summary.json"
DEFAULT_OUT = ROOT / "results" / "figures"
SOURCE_NOTE = "Data: results/analysis/model_comparison/summary.json."

BENCHMARKS = (("tierb50", "Case 1: build"), ("probe0", "Case 2: no probes"), ("probe5", "Case 2: 5 probes"),
              ("v3base", "Case 3: repair, no hint"), ("v3hint", "Case 3: repair, with hint"))
SUITES = tuple(s for s, _ in BENCHMARKS)
BENCH_LABEL = dict(BENCHMARKS)
MODELS = ("beam", "gpt", "qwen")
MODEL_NAME = {"beam": "Beam", "gpt": "GPT-OSS-120B", "qwen": "Qwen3.5-397B"}
MODEL_SHORT = {"beam": "Beam", "gpt": "GPT-OSS", "qwen": "Qwen3.5"}
MODEL_COLOR = dict(zip(MODELS, pf.SERIES_COLORS))  # blue, orange, green: fixed per model in every figure
EFFORTS = ("low", "medium", "high")
EFFORT_MARKER = {"low": "o", "medium": "s", "high": "^", "thinking": "D"}
EFFORT_SHORT = {"low": "low", "medium": "med", "high": "high"}
BEST = {"beam": "high", "gpt": "high", "qwen": "thinking"}  # the setting shown when a figure has one point per model
SETTING_NAME = {"beam": "Beam (high)", "gpt": "GPT-OSS-120B (high)", "qwen": "Qwen3.5-397B (thinking on)"}
ROW_ORDER = tuple((m, e) for m in ("beam", "gpt") for e in EFFORTS) + (("qwen", "thinking"),)
BLUE_RAMP = ("#eaf2fc", "#a9c8ef", "#5a98e0", "#2a78d6", "#14509a", "#0a2d5c")  # one hue, light to dark
BEAM_BETTER, OTHER_BETTER, NEUTRAL = pf.SERIES_COLORS[0], pf.SERIES_COLORS[7], SLATE[2]
LEADER = {"arrowstyle": "-", "color": SLATE[2], "lw": 0.8, "shrinkA": 0, "shrinkB": 7}
MS = 8.5  # marker size in points (at least 8 px)
MINUS = "−"


# ---- data ------------------------------------------------------------------


def classify(key: str) -> tuple[str, str]:
    """`beam-high` -> ("beam", "high"); `gpt-oss-120b-low` -> ("gpt", "low"); `qwen...` -> ("qwen", "thinking")."""
    if key.startswith("beam-"):
        return "beam", key.split("-", 1)[1]
    if key.startswith("gpt-oss"):
        return "gpt", key.rsplit("-", 1)[1]
    if key.startswith("qwen"):
        return "qwen", "thinking"
    raise ValueError(f"unknown model key {key!r}")


def load_rows(path: Path = SUMMARY) -> tuple[dict[tuple[str, str, str], dict], dict]:
    """{(model, effort, suite): summary row} for the five benchmarks only (probe20 is dropped), and the file's header."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = {(*classify(r["key"]), r["suite"]): r for r in doc["rows"] if r["suite"] in SUITES}
    missing = [(m, e, s) for s in SUITES for m, e in ROW_ORDER if (m, e, s) not in rows]
    if missing:
        raise SystemExit(f"summary.json lacks cells: {missing[:4]}")
    return rows, doc


def cell(rows: dict, model: str, effort: str, suite: str) -> dict:
    r = rows[(model, effort, suite)]
    return {"model": model, "effort": effort, "suite": suite, "n": r["n"], "runs": r["runs"], "pass_rate": r["pass_rate"],
            "ci_low": r["ci_low"], "ci_high": r["ci_high"], "mean_output_tokens": r["mean_output_tokens"],
            "vs_beam_high": r["paired_vs_beam_high"]}


def fmt_diff(d: float) -> str:
    return f"{d:+.1f}".replace("-", MINUS)


# ---- shared drawing --------------------------------------------------------


def frame(w: float, h: float, title: str, subtitle: str, note: str = "", bottom_in: float = 0.5):
    """Figure with a wrapped takeaway title, a subtitle and a footer. Returns (fig, top, bottom) as figure fractions."""
    plt = setup()
    fig = plt.figure(figsize=(w, h), dpi=100, facecolor=SURFACE)
    y = h - 0.2
    t_lines = textwrap.wrap(title, int((w - 0.6) / 0.142))
    fig.text(0.3 / w, y / h, "\n".join(t_lines), fontsize=15, fontweight="bold", color=INK, ha="left", va="top", linespacing=1.15)
    y -= 0.27 * len(t_lines) + 0.06
    s_lines = textwrap.wrap(subtitle, int((w - 0.6) / 0.072))
    fig.text(0.3 / w, y / h, "\n".join(s_lines), fontsize=10, color=INK_2, ha="left", va="top", linespacing=1.3)
    y -= 0.19 * len(s_lines) + 0.12
    foot = textwrap.wrap(f"{note} {SOURCE_NOTE}".strip(), int((w - 0.6) / 0.058))
    fig.text(0.3 / w, 0.12 / h, "\n".join(foot), fontsize=7.8, color=INK_2, ha="left", va="bottom", linespacing=1.3)
    return fig, (y - 0.2) / h, (0.12 + 0.145 * len(foot) + bottom_in) / h


def ci_note(doc: dict) -> str:
    return (f"95% CI = bootstrap over tasks ({doc['n_boot']:,} resamples). Pass rate = share of hidden tests passed. "
            "Beam high on Cases 1 and 3 = mean of 3 runs; every other cell = 1 run.")


def n_text(rows: dict) -> str:
    ns = {s: rows[("beam", "high", s)]["n"] for s in SUITES}
    return f"n = {ns['tierb50']} tasks in Case 1, {ns['probe0']} in Case 2, {ns['v3base']} in Case 3"


def pct(v: float) -> str:
    return f"{v:.1f}%"


def fmt_k(v: float) -> str:
    return f"{v / 1000:.1f}k"


def model_handles(plt, with_effort: bool = False):
    from matplotlib.lines import Line2D

    out = [Line2D([], [], color=MODEL_COLOR[m], lw=2, marker=EFFORT_MARKER["high"] if m != "qwen" else "D", ms=MS,
                  label=SETTING_NAME[m] if not with_effort else {"beam": "Beam", "gpt": "GPT-OSS-120B",
                                                                  "qwen": "Qwen3.5-397B (thinking on)"}[m]) for m in MODELS]
    return out


def effort_handles():
    from matplotlib.lines import Line2D

    return [Line2D([], [], color=INK_2, lw=0, marker=EFFORT_MARKER[e], ms=MS, mfc="none" if e != "high" else INK_2, mew=1.6, label=e)
            for e in EFFORTS]


def legend_cell(ax, groups: list[tuple[str, list]]) -> None:
    """Fill an empty grid cell with stacked legends, one per (title, handles)."""
    ax.axis("off")
    fig = ax.figure
    y = 0.98
    for title, handles in groups:
        leg = ax.legend(handles=handles, title=title, loc="upper left", bbox_to_anchor=(0.0, y), frameon=True, facecolor=SURFACE,
                        edgecolor=GRID, fontsize=9.5, title_fontsize=9.5, labelcolor=INK, borderpad=0.8, labelspacing=0.7)
        leg.get_title().set_color(INK_2)
        leg.get_title().set_fontweight("bold")
        ax.add_artist(leg)
        fig.canvas.draw()
        y = leg.get_window_extent().transformed(ax.transAxes.inverted()).y0 - 0.05


def save(fig, out_dir: Path, name: str, data: dict) -> None:
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{name}.png", dpi=DPI, facecolor=SURFACE)
    fig.savefig(out_dir / f"{name}.svg", facecolor=SURFACE)
    (out_dir / f"{name}.json").write_text(json.dumps({"figure": name, **data}, indent=2) + "\n", encoding="utf-8")
    plt.close(fig)


def grid_2x3(plt, fig, top: float, bottom: float, hspace: float = 0.5, wspace: float = 0.34):
    gs = fig.add_gridspec(2, 3, left=0.085, right=0.975, top=top, bottom=bottom, hspace=hspace, wspace=wspace)
    return [fig.add_subplot(gs[i // 3, i % 3]) for i in range(6)]


# ---- 1. scoreboard ---------------------------------------------------------


def ramp(v: float):
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("blue", BLUE_RAMP)(min(max(v / 100.0, 0.0), 1.0))


def fig_scoreboard(rows: dict, doc: dict):
    from matplotlib.patches import Rectangle

    cols_x = {"tierb50": 0.0, "probe0": 1.25, "probe5": 2.25, "v3base": 3.5, "v3hint": 4.5}
    row_y = {("beam", "low"): 0, ("beam", "medium"): 1, ("beam", "high"): 2, ("gpt", "low"): 3.45, ("gpt", "medium"): 4.45,
             ("gpt", "high"): 5.45, ("qwen", "thinking"): 6.9}
    w, h = 10.0, 6.0
    title = "Beam is best on {k} of 5 benchmarks, and far ahead once it is given the missing information"
    best = {s: max(rows[(m, e, s)]["pass_rate"] for m, e in ROW_ORDER) for s in SUITES}
    k = sum(best[s] == max(rows[("beam", e, s)]["pass_rate"] for e in EFFORTS) for s in SUITES)
    fig, top, bottom = frame(w, h, title.format(k=k),
                             f"pass rate = share of hidden tests passed; Beam high = mean of 3 runs (Cases 1 and 3), others 1 run; {n_text(rows)}",
                             "Outlined cell = highest in its column; gaps of a few points are within noise (see the gap chart). " + ci_note(doc),
                             bottom_in=0.1)
    ax = fig.add_axes([0.14, bottom, 0.84, top - bottom])
    ax.set_facecolor(SURFACE)
    ax.axis("off")
    ax.set_xlim(-2.1, 5.5)
    ax.set_ylim(7.95, -1.5)
    data = []
    for (m, e), y in row_y.items():
        for s, x in cols_x.items():
            r = rows[(m, e, s)]
            v = r["pass_rate"]
            fc = ramp(v)
            ax.add_patch(Rectangle((x, y), 0.96, 0.9, facecolor=fc, edgecolor="none", zorder=2))
            ax.text(x + 0.48, y + 0.45, pct(v), ha="center", va="center", fontsize=11.5, fontweight="bold", color=text_on(fc), zorder=4)
            if v == best[s]:
                ax.add_patch(Rectangle((x - 0.03, y - 0.03), 1.02, 0.96, facecolor="none", edgecolor=INK, lw=2.6, zorder=5))
            data.append({**cell(rows, m, e, s), "best_in_column": v == best[s]})
        if e != "thinking":
            ax.text(-0.12, y + 0.45, e, ha="right", va="center", fontsize=10.5, color=INK)
    for m, ys in (("beam", (0, 2.9)), ("gpt", (3.45, 6.35)), ("qwen", (6.9, 7.8))):
        ax.add_patch(Rectangle((-2.08, ys[0]), 0.09, ys[1] - ys[0], facecolor=MODEL_COLOR[m], edgecolor="none"))
    ax.text(-1.85, 1.45, "Beam", fontsize=12, fontweight="bold", color=INK, va="center")
    ax.text(-1.85, 4.9, "GPT-OSS\n-120B", fontsize=12, fontweight="bold", color=INK, va="center", linespacing=1.1)
    ax.text(-1.85, 7.35, "Qwen3.5\n(thinking on)", fontsize=12, fontweight="bold", color=INK, va="center", linespacing=1.1)
    for s, x in cols_x.items():
        head, rest = BENCH_LABEL[s].split(": ")
        ax.text(x + 0.48, -0.2, "\n".join([head + ":", *textwrap.wrap(rest, 9)]), ha="center", va="bottom", fontsize=10.5, fontweight="bold",
                color=INK, linespacing=1.2)
    return fig, {"title": title.format(k=k), "benchmarks": [BENCH_LABEL[s] for s in SUITES], "cells": data}


# ---- 2. leaderboard --------------------------------------------------------


def fig_leaderboard(rows: dict, doc: dict):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt = setup()
    title = "Beam leads once it gets the missing information; GPT-OSS-120B leads without it"
    fig, top, bottom = frame(10, 6.7, title, f"each model at its best setting: Beam high, GPT-OSS-120B high, Qwen3.5 thinking on; {n_text(rows)}",
                             ci_note(doc), bottom_in=0.6)
    axes = grid_2x3(plt, fig, top, bottom, hspace=0.55, wspace=0.42)
    data = []
    for ax, (suite, label) in zip(axes, BENCHMARKS):
        style_axes(ax, "x")
        for i, m in enumerate(MODELS):
            c = cell(rows, m, BEST[m], suite)
            ax.barh(i, c["pass_rate"], height=0.5, color=MODEL_COLOR[m], zorder=3)
            ax.plot([c["ci_low"], c["ci_high"]], [i, i], color=INK, lw=1.6, zorder=4, solid_capstyle="butt")
            for xe in (c["ci_low"], c["ci_high"]):
                ax.plot([xe, xe], [i - 0.13, i + 0.13], color=INK, lw=1.6, zorder=4)
            ax.text(c["ci_high"] + 3.5, i, pct(c["pass_rate"]), va="center", ha="left", fontsize=10, fontweight="bold", color=INK, zorder=5,
                    path_effects=halo())
            data.append(c)
        ax.set_ylim(2.6, -0.6)
        ax.set_xlim(0, 126)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_yticks(range(3), [MODEL_SHORT[m] for m in MODELS], fontsize=9.5, color=INK)
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel("pass rate (%)", fontsize=9, color=INK_2)
        ax.set_title(label, loc="left", fontsize=11, fontweight="bold", color=INK, pad=6)
    legend_cell(axes[5], [("Model and setting", [Patch(color=MODEL_COLOR[m], label=SETTING_NAME[m]) for m in MODELS]),
                          ("Error bar", [Line2D([], [], color=INK, lw=1.6, label="95% CI over tasks")])])
    return fig, {"title": title, "benchmarks": [BENCH_LABEL[s] for s in SUITES], "bars": data}


# ---- 3. effort curves ------------------------------------------------------


def spread(ys: list[float], gap: float, lo: float, hi: float) -> list[float]:
    """Move label heights apart so neighbours are at least `gap` apart (order kept)."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in zip(order, order[1:]):
        out[b] = max(out[b], out[a] + gap)
    over = out[order[-1]] - hi
    if over > 0:
        out = [v - over for v in out]
    return [max(v, lo) for v in out]


def fig_effort_curves(rows: dict, doc: dict):
    plt = setup()
    title = "More thinking effort helps, but how much depends on the benchmark and the model"
    fig, top, bottom = frame(10, 7.0, title, f"pass rate at low, medium and high reasoning effort; Qwen3.5 has one setting (thinking on); {n_text(rows)}",
                             ci_note(doc), bottom_in=0.65)
    axes = grid_2x3(plt, fig, top, bottom, hspace=0.5, wspace=0.28)
    data = []
    qx = 3.75
    for ax, (suite, label) in zip(axes, BENCHMARKS):
        style_axes(ax, "y")
        ends = []
        for m in ("beam", "gpt"):
            cs = [cell(rows, m, e, suite) for e in EFFORTS]
            ax.plot(range(3), [c["pass_rate"] for c in cs], color=MODEL_COLOR[m], lw=2, zorder=3)
            for i, c in enumerate(cs):
                ax.plot(i, c["pass_rate"], marker=EFFORT_MARKER[EFFORTS[i]], ms=MS, color=MODEL_COLOR[m], mfc=SURFACE if i < 2 else MODEL_COLOR[m],
                        mew=2, zorder=4)
            ends.append((m, cs[-1]["pass_rate"]))
            data += cs
        q = cell(rows, "qwen", "thinking", suite)
        ax.plot(qx, q["pass_rate"], marker="D", ms=MS, color=MODEL_COLOR["qwen"], zorder=4)
        ax.text(qx, q["pass_rate"] - 6, f"Qwen3.5\n{q['pass_rate']:.1f}", ha="center", va="top", fontsize=8.5, color=INK, linespacing=1.15,
                path_effects=halo())
        data.append(q)
        ys = spread([v for _, v in ends], 9.0, 0, 100)
        for (m, v), y in zip(ends, ys):
            ax.text(2.2, y, f"{MODEL_SHORT[m]} {v:.1f}", ha="left", va="center", fontsize=8.5, color=INK, path_effects=halo())
        ax.axvline(3.05, color=GRID, lw=1, zorder=1)
        ax.set_xlim(-0.35, 4.3)
        ax.set_ylim(0, 105)
        ax.set_xticks([0, 1, 2, qx], ["low", "med", "high", "think"], fontsize=9)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_ylabel("pass rate (%)", fontsize=9, color=INK_2)
        ax.set_title(label, loc="left", fontsize=11, fontweight="bold", color=INK, pad=6)
    legend_cell(axes[5], [("Model", model_handles(plt, True)), ("Reasoning effort (marker)", effort_handles())])
    return fig, {"title": title, "benchmarks": [BENCH_LABEL[s] for s in SUITES], "points": data}


# ---- 4. information gain ---------------------------------------------------

GAIN_PAIRS = (("Case 3: add a hint", "v3base", "v3hint"), ("Case 2: allow up to 5 probes", "probe0", "probe5"))


def fig_information_gain(rows: dict, doc: dict):
    setup()
    gains = {(m, a): rows[(m, BEST[m], b)]["pass_rate"] - rows[(m, BEST[m], a)]["pass_rate"] for _, a, b in GAIN_PAIRS for m in MODELS}
    most = all(gains[("beam", a)] == max(gains[(m, a)] for m in MODELS) for _, a, _b in GAIN_PAIRS)
    title = "Beam gains the most from added information" if most else "Gain in pass rate from added information, by model"
    fig, top, bottom = frame(9.5, 5.4, title,
                             "each model at its best setting (Beam high, GPT-OSS-120B high, Qwen3.5 thinking on); arrow runs from the "
                             f"score without the information to the score with it; {n_text(rows)}", ci_note(doc), bottom_in=0.5)
    ax = fig.add_axes([0.2, bottom, 0.63, top - bottom])
    style_axes(ax, "x")
    xmax = 100.0
    row_y, data, y = {}, [], 0.9
    for gtitle, a, b in GAIN_PAIRS:
        ax.text(0.0, y - 0.62, gtitle, fontsize=11, fontweight="bold", color=INK, va="center", ha="left")
        for m in MODELS:
            row_y[(a, m)] = y
            y += 1
        y += 0.9
    ax.set_ylim(y - 0.5, -0.3)
    ax.set_xlim(0, xmax + 10)
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    for gtitle, a, b in GAIN_PAIRS:
        ymid = row_y[(a, "beam")]
        ax.axhspan(ymid - 0.45, ymid + 0.45, color=MODEL_COLOR["beam"], alpha=0.08, zorder=0, lw=0)
        for m in MODELS:
            yy = row_y[(a, m)]
            v0, v1 = rows[(m, BEST[m], a)]["pass_rate"], rows[(m, BEST[m], b)]["pass_rate"]
            col = MODEL_COLOR[m]
            if abs(v1 - v0) >= 3:
                ax.annotate("", xy=(v1, yy), xytext=(v0, yy), arrowprops={"arrowstyle": "-|>", "color": col, "lw": 2.4, "shrinkA": 6,
                                                                          "shrinkB": 6, "mutation_scale": 16}, zorder=3)
            else:
                ax.plot([v0, v1], [yy, yy], color=col, lw=2.4, zorder=3)
            ax.plot(v0, yy, "o", ms=MS + 1, mfc=SURFACE, mec=col, mew=2.2, zorder=4)
            ax.plot(v1, yy, "o", ms=MS + 1, color=col, zorder=4)
            up = v1 >= v0
            ax.text(v0 + (-3.2 if up else 3.2), yy, f"{v0:.1f}", ha="right" if up else "left", va="center", fontsize=9.5, color=INK_2)
            ax.text(v1 + (3.2 if up else -3.2), yy, f"{v1:.1f}", ha="left" if up else "right", va="center", fontsize=9.5, color=INK,
                    fontweight="bold")
            ax.text(1.04, yy, f"{fmt_diff(v1 - v0)} pts", transform=ax.get_yaxis_transform(), ha="left", va="center", fontsize=11,
                    fontweight="bold" if m == "beam" else "normal", color=INK)
            data.append({"model": m, "setting": BEST[m], "information": gtitle, "from_suite": a, "to_suite": b, "from": v0, "to": v1,
                         "gain_points": v1 - v0, "n": rows[(m, BEST[m], a)]["n"]})
    ax.text(1.04, -0.05, "gain", transform=ax.get_yaxis_transform(), ha="left", va="bottom", fontsize=9.5, color=INK_2)
    ax.set_yticks([row_y[(a, m)] for _, a, _b in GAIN_PAIRS for m in MODELS],
                  [SETTING_NAME[m].replace("-120B", "").replace("-397B", "").replace(" on)", ")") for _, a, _b in GAIN_PAIRS for m in MODELS], fontsize=10, color=INK)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("pass rate (%)", fontsize=9.5, color=INK_2)
    from matplotlib.lines import Line2D

    ax.legend(handles=[Line2D([], [], color=INK_2, marker="o", ms=MS, mfc=SURFACE, mew=2, lw=0, label="without the information"),
                       Line2D([], [], color=INK_2, marker="o", ms=MS, lw=0, label="with it")], loc="lower right", ncol=1, frameon=True,
              facecolor=SURFACE, edgecolor=GRID, fontsize=9.5, labelcolor=INK)
    return fig, {"title": title, "gains": data}


# ---- 5. gap vs Beam --------------------------------------------------------


def verdict(d: dict) -> str:
    if d["ci_low"] > 0:
        return "other_better"
    if d["ci_high"] < 0:
        return "beam_better"
    return "no_clear_difference"


def fig_gap_vs_beam(rows: dict, doc: dict):
    from matplotlib.lines import Line2D

    setup()
    title = "Without extra information GPT-OSS-120B matches or beats Beam; with it, Beam pulls ahead"
    fig, top, bottom = frame(10, 6.8, title,
                             "paired difference in pass rate, model minus Beam (high), in points, with 95% CI over the same tasks; "
                             "Beam high is the mean of 3 runs on Cases 1 and 3", ci_note(doc), bottom_in=1.0)
    ax = fig.add_axes([0.25, bottom, 0.5, top - bottom - 0.04])
    style_axes(ax, "x")
    colors = {"beam_better": BEAM_BETTER, "other_better": OTHER_BETTER, "no_clear_difference": NEUTRAL}
    shapes = {"gpt": "o", "qwen": "D"}
    lo = min(rows[(m, BEST[m], s)]["paired_vs_beam_high"]["ci_low"] for m in ("gpt", "qwen") for s in SUITES)
    hi = max(rows[(m, BEST[m], s)]["paired_vs_beam_high"]["ci_high"] for m in ("gpt", "qwen") for s in SUITES)
    xlo, xhi = 10 * (lo // 10), 10 * (hi // 10 + 1)
    ax.axvspan(xlo, 0, color=BEAM_BETTER, alpha=0.06, lw=0, zorder=0)
    ax.axvspan(0, xhi, color=OTHER_BETTER, alpha=0.06, lw=0, zorder=0)
    ax.axvline(0, color=INK_2, lw=1.4, zorder=2)
    data, ticks, labels = [], [], []
    y = 0.0
    for suite, label in BENCHMARKS:
        ax.text(xlo + 1.5, y - 0.08, label, fontsize=10.5, fontweight="bold", color=INK, va="center", ha="left", zorder=5, path_effects=halo())
        y += 0.95
        for m in ("gpt", "qwen"):
            d = rows[(m, BEST[m], suite)]["paired_vs_beam_high"]
            v = verdict(d)
            ax.plot([d["ci_low"], d["ci_high"]], [y, y], color=colors[v], lw=2.2, zorder=3, solid_capstyle="butt")
            ax.plot(d["diff"], y, shapes[m], ms=MS + 1, color=colors[v], mec=SURFACE, mew=1.2, zorder=4)
            ax.text(1.03, y, f"{fmt_diff(d['diff'])}  [{fmt_diff(d['ci_low'])}, {fmt_diff(d['ci_high'])}]", transform=ax.get_yaxis_transform(),
                    ha="left", va="center", fontsize=9.5, color=INK)
            ticks.append(y)
            labels.append("GPT-OSS high" if m == "gpt" else "Qwen3.5 thinking")
            data.append({"model": m, "setting": BEST[m], "suite": suite, "diff": d["diff"], "ci_low": d["ci_low"], "ci_high": d["ci_high"],
                         "n": d["n"], "verdict": v})
            y += 0.9
        y += 0.35
    ax.set_ylim(y - 0.5, -0.5)
    ax.set_xlim(xlo, xhi)
    ax.set_yticks(ticks, labels, fontsize=9.5, color=INK)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("difference in pass rate, model minus Beam (high), points", fontsize=9.5, color=INK_2)
    ax.text(0.0, 1.01, f"{chr(0x2190)} Beam better", transform=ax.transAxes, ha="left", va="bottom", fontsize=10, fontweight="bold", color=INK)
    ax.text(1.0, 1.01, f"other model better {chr(0x2192)}", transform=ax.transAxes, ha="right", va="bottom", fontsize=10, fontweight="bold",
            color=INK)
    ax.text(1.03, 1.01, "difference [95% CI]", transform=ax.transAxes, ha="left", va="bottom", fontsize=9.5, color=INK_2)
    handles = [Line2D([], [], color=BEAM_BETTER, lw=2.2, label="Beam better (CI excludes 0)"),
               Line2D([], [], color=NEUTRAL, lw=2.2, label="no clear difference (CI includes 0)"),
               Line2D([], [], color=OTHER_BETTER, lw=2.2, label="other model better (CI excludes 0)"),
               Line2D([], [], color=INK_2, lw=0, marker="o", ms=MS, label="GPT-OSS-120B (high)"),
               Line2D([], [], color=INK_2, lw=0, marker="D", ms=MS, label="Qwen3.5-397B (thinking on)")]
    fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, fontsize=9, labelcolor=INK, bbox_to_anchor=(0.5, bottom - 0.065))
    return fig, {"title": title, "differences": data}


# ---- 6. what Beam does with the missing piece ------------------------------


def hidden_header_pass(effort_doc: dict, level: str) -> dict:
    sw = next(s for s in effort_doc["switches"] if s["switch"] == "hidden_header" and s["level"] == level)
    return {"pass_rate": sw["by_effort"]["high"]["pass_rate"] * 100, "n": sw["by_effort"]["high"]["n"]}


def fig_beam_missing_info(rows: dict, doc: dict, effort_doc: dict, controls: dict):
    from matplotlib.patches import Patch

    setup()
    title = "Beam builds clients well when the recording shows everything, and recovers when given the missing piece"
    off, on = hidden_header_pass(effort_doc, "off"), hidden_header_pass(effort_doc, "on")
    h10 = controls["h10"]["steps"]
    c2 = {s: rows[("beam", "high", s)] for s in ("probe0", "probe5", "v3base", "v3hint")}
    bars = [
        ("Case 1: Tier B, 25 tasks per bar", None),
        ("header visible", {"v": off["pass_rate"], "n": off["n"], "runs": 3, "full": True}),
        ("header hidden", {"v": on["pass_rate"], "n": on["n"], "runs": 3, "full": False}),
        ("header hidden + hint (H10)", {"v": h10["b"]["pass_rate"] * 100, "n": h10["b"]["finished"], "runs": h10["b"]["runs_per_task"], "full": True}),
        ("header hidden + evidence (H10)", {"v": h10["c"]["pass_rate"] * 100, "n": h10["c"]["finished"], "runs": h10["c"]["runs_per_task"], "full": True}),
        ("Case 2: Beam may probe the live server", None),
        ("no probes", {"v": c2["probe0"]["pass_rate"], "n": c2["probe0"]["n"], "runs": 1, "full": False}),
        ("up to 5 probes (P5)", {"v": c2["probe5"]["pass_rate"], "n": c2["probe5"]["n"], "runs": 1, "full": True}),
        ("Case 3: repair a broken client", None),
        ("no hint", {"v": c2["v3base"]["pass_rate"], "n": c2["v3base"]["n"], "runs": c2["v3base"]["runs"], "full": False}),
        ("with hint", {"v": c2["v3hint"]["pass_rate"], "n": c2["v3hint"]["n"], "runs": c2["v3hint"]["runs"], "full": True}),
    ]
    fig, top, bottom = frame(10, 6.9, title, "Beam at high effort; bars show the share of hidden tests passed. Light bars: the missing piece is withheld; "
                             "solid bars: it is in the recording or supplied", "Case 1 header bars: 25 tasks each, mean of 3 runs; H10 hint and evidence: "
                             "same 25 tasks, 1 run; Case 2: 50 tasks, 1 run; Case 3: 64 tasks, mean of 3 runs. "
                             "Whiskers are omitted where the analysis files hold no per-bar CI.", bottom_in=0.95)
    ax = fig.add_axes([0.27, bottom, 0.7, top - bottom])
    style_axes(ax, "x")
    data = []
    ylab, ypos = [], []
    for i, (name, spec) in enumerate(bars):
        if spec is None:
            ax.text(-0.275, i, name, transform=ax.get_yaxis_transform(), ha="left", va="center", fontsize=10.5, fontweight="bold", color=INK,
                    clip_on=False)
            continue
        col = MODEL_COLOR["beam"]
        ax.barh(i, spec["v"], height=0.58, color=col if spec["full"] else BLUE_RAMP[1], edgecolor=col, linewidth=1.4, zorder=3)
        ax.text(spec["v"] + 1.5, i, pct(spec["v"]), va="center", ha="left", fontsize=11.5, fontweight="bold", color=INK, path_effects=halo(),
                zorder=5)
        ylab.append(name)
        ypos.append(i)
        data.append({"label": name, "pass_rate": spec["v"], "n": spec["n"], "runs": spec["runs"], "information_available": spec["full"]})
    ax.set_ylim(len(bars) - 0.5, -0.6)
    ax.set_xlim(0, 112)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_yticks(ypos, ylab, fontsize=10, color=INK)
    ax.tick_params(axis="y", length=0, pad=6)
    ax.set_xlabel("pass rate (%)", fontsize=9.5, color=INK_2)
    fig.legend(handles=[Patch(facecolor=BLUE_RAMP[1], edgecolor=MODEL_COLOR["beam"], label="missing piece withheld"),
                        Patch(facecolor=MODEL_COLOR["beam"], edgecolor=MODEL_COLOR["beam"], label="piece in the recording or supplied")],
               loc="upper center", ncol=2, frameon=False, fontsize=9.5, labelcolor=INK, bbox_to_anchor=(0.62, bottom - 0.055))
    return fig, {"title": title, "bars": data}


# ---- 7. cost vs score ------------------------------------------------------


def place_labels(ax, fig, pts: list[tuple[float, float, str]]) -> None:
    """Put each label at the first of several offsets where it overlaps no other label, marker or the axes edge."""
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    px = fig.dpi / 72.0
    from matplotlib.transforms import Bbox

    marks = []
    for x, y, _ in pts:
        cx, cy = ax.transData.transform((x, y))
        marks.append(Bbox.from_extents(cx - 6 * px, cy - 6 * px, cx + 6 * px, cy + 6 * px))
    placed: list = list(marks)
    cands = [(9, 0, "left", "center"), (-9, 0, "right", "center"), (0, 9, "center", "bottom"), (0, -9, "center", "top"),
             (8, 8, "left", "bottom"), (-8, 8, "right", "bottom"), (8, -8, "left", "top"), (-8, -8, "right", "top"),
             (18, 0, "left", "center"), (-18, 0, "right", "center"), (0, 20, "center", "bottom"), (0, -20, "center", "top"),
             (16, 16, "left", "bottom"), (-16, 16, "right", "bottom"), (16, -16, "left", "top"), (-16, -16, "right", "top"),
             (30, 0, "left", "center"), (-30, 0, "right", "center"), (0, 32, "center", "bottom"), (0, -32, "center", "top"),
             (26, 26, "left", "bottom"), (-26, 26, "right", "bottom"), (26, -26, "left", "top"), (-26, -26, "right", "top")]
    axbox = ax.get_window_extent(rend)

    def make(x, y, text, off, leader):
        dx, dy, ha, va = off
        return ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, fontsize=8, color=INK,
                           path_effects=halo(), zorder=6, arrowprops=LEADER if leader else None)

    for (x, y, text), mark in zip(pts, marks):
        chosen = cands[0]
        bb = None
        for off in cands:
            t = make(x, y, text, off, False)  # measured without the leader line: an annotation with an arrow reports a different box
            cand_bb = t.get_window_extent(rend).expanded(1.04, 1.1)
            t.remove()
            inside = axbox.x0 <= cand_bb.x0 and cand_bb.x1 <= axbox.x1 and axbox.y0 <= cand_bb.y0 and cand_bb.y1 <= axbox.y1
            if inside and not any(cand_bb.overlaps(p) for p in placed if p is not mark):
                chosen, bb = off, cand_bb
                break
        t = make(x, y, text, chosen, max(abs(chosen[0]), abs(chosen[1])) > 10)
        placed.append(bb if bb is not None else make(x, y, text, chosen, False).get_window_extent(rend))


def point_label(m: str, e: str) -> str:
    return "Qwen3.5" if m == "qwen" else f"{MODEL_SHORT[m]} {EFFORT_SHORT[e]}"


def fig_cost_vs_score(rows: dict, doc: dict):
    from matplotlib.ticker import FuncFormatter, NullFormatter

    plt = setup()
    fewer = sum(rows[("beam", "high", s)]["mean_output_tokens"] < rows[("gpt", "high", s)]["mean_output_tokens"] for s in SUITES)
    title = f"Higher scores cost more output tokens; Beam high uses fewer than GPT-OSS high on {fewer} of 5 benchmarks"
    fig, top, bottom = frame(11, 8.2, title, f"one point per setting; x = mean output tokens per task (reasoning included, log scale), "
                             f"y = pass rate; {n_text(rows)}", ci_note(doc), bottom_in=0.6)
    axes = grid_2x3(plt, fig, top, bottom, hspace=0.45, wspace=0.26)
    data = []
    for ax, (suite, label) in zip(axes, BENCHMARKS):
        style_axes(ax, "both")
        pts = []
        for m in ("beam", "gpt"):
            cs = [cell(rows, m, e, suite) for e in EFFORTS]
            ax.plot([c["mean_output_tokens"] for c in cs], [c["pass_rate"] for c in cs], color=MODEL_COLOR[m], lw=1.6, alpha=0.45, zorder=2)
        for m, e in ROW_ORDER:
            c = cell(rows, m, e, suite)
            ax.plot(c["mean_output_tokens"], c["pass_rate"], marker=EFFORT_MARKER[e], ms=MS, color=MODEL_COLOR[m],
                    mfc=SURFACE if e in ("low", "medium") else MODEL_COLOR[m], mew=2, ls="none", zorder=4)
            pts.append((c["mean_output_tokens"], c["pass_rate"], point_label(m, e)))
            data.append(c)
        ax.set_xscale("log")
        ax.set_xlim(850, 17000)
        ax.set_ylim(20, 103)
        ax.set_xticks([1000, 2000, 5000, 10000])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:g}k"))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_yticks([20, 40, 60, 80, 100])
        ax.set_xlabel("mean output tokens per task", fontsize=9, color=INK_2)
        ax.set_ylabel("pass rate (%)", fontsize=9, color=INK_2)
        ax.set_title(label, loc="left", fontsize=11, fontweight="bold", color=INK, pad=6)
        place_labels(ax, fig, pts)
    legend_cell(axes[5], [("Model (colour)", model_handles(plt, True)), ("Reasoning effort (marker)", effort_handles())])
    return fig, {"title": title, "points": data}


# ---- entry point -----------------------------------------------------------

FIGURES = ("final_scoreboard", "final_leaderboard", "final_effort_curves", "final_information_gain", "final_gap_vs_beam",
           "final_beam_missing_info", "final_cost_vs_score")


def build_all(out_dir: Path = DEFAULT_OUT, summary: Path = SUMMARY, effort_summary: Path = EFFORT_SUMMARY,
              controls: Path = CONTROLS_SUMMARY) -> list[str]:
    rows, doc = load_rows(summary)
    effort_doc = json.loads(Path(effort_summary).read_text(encoding="utf-8"))
    controls_doc = json.loads(Path(controls).read_text(encoding="utf-8"))
    makers = {
        "final_scoreboard": lambda: fig_scoreboard(rows, doc),
        "final_leaderboard": lambda: fig_leaderboard(rows, doc),
        "final_effort_curves": lambda: fig_effort_curves(rows, doc),
        "final_information_gain": lambda: fig_information_gain(rows, doc),
        "final_gap_vs_beam": lambda: fig_gap_vs_beam(rows, doc),
        "final_beam_missing_info": lambda: fig_beam_missing_info(rows, doc, effort_doc, controls_doc),
        "final_cost_vs_score": lambda: fig_cost_vs_score(rows, doc),
    }
    for name in FIGURES:
        fig, data = makers[name]()
        save(fig, out_dir, name, data)
        print(f"wrote {Path(out_dir) / name}.{{png,svg,json}}")
    return list(FIGURES)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    build_all(ap.parse_args(argv).out_dir)


if __name__ == "__main__":
    main()
