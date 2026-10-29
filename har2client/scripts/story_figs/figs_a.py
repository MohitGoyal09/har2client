"""F1 effort curves, F2 cost per solve, F3 outcome shares."""

from __future__ import annotations

from pathlib import Path

from . import data as D
from . import style as S

EFFORT_NAME = {"low": "low", "medium": "medium", "high": "high"}
OUTCOME_COLOR_T = {D.SEG_SOLVED: 0.92, D.SEG_ABOVE: 0.68, D.SEG_PARTIAL: 0.52, D.SEG_BELOW: 0.36, D.SEG_ZERO: 0.14}
OUTCOME_LABEL = {D.SEG_SOLVED: "fully solved (pass rate 1.0)", D.SEG_PARTIAL: "partial (no v1 baseline for case 1)",
                 D.SEG_ABOVE: "partial, above the unchanged-v1 score", D.SEG_BELOW: "partial, at or below the unchanged-v1 score",
                 D.SEG_ZERO: "zero"}
MIN_LABEL_SHARE = 0.06


# ------------------------------------------------------------ F1


def f1_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    by = {s: {e: ds.tasks(s, e) for e in D.EFFORTS} for s in (D.CASE1, D.BASE, D.HINT)}
    out = {"title": "Effort helps both cases; the hint helps case 3 far more",
           "case1": D.effort_curve(by[D.CASE1], n_boot, seed, "f1:case1"),
           "case3_base": D.effort_curve(by[D.BASE], n_boot, seed, "f1:base"),
           "case3_hint": D.effort_curve(by[D.HINT], n_boot, seed, "f1:hint"),
           "header_subsets": None}
    if ds.header_known:
        out["header_subsets"] = {
            name: D.effort_curve({e: D.subset(by[D.CASE1][e], ds.header_on, on) for e in D.EFFORTS}, n_boot, seed, f"f1:hdr:{name}", ci=False)
            for name, on in (("header_on", True), ("header_off", False))}
    return out


def _curve(ax, pts, series, above):
    """Line with CI bars; each point is labeled with effort and mean pass rate just outside its CI bar."""
    color = S.SERIES_COLOR[series]
    xs, ys = [p["tokens"] for p in pts], [p["pass"] for p in pts]
    ax.plot(xs, ys, color=color, linewidth=2, solid_capstyle="round", zorder=3)
    ax.errorbar(xs, ys, yerr=[[p["pass"] - p["ci_low"] for p in pts], [p["ci_high"] - p["pass"] for p in pts]],
                fmt="none", ecolor=color, elinewidth=1.25, capsize=3, zorder=3)
    ax.plot(xs, ys, linestyle="none", marker=S.SERIES_MARKER[series], markersize=9, markerfacecolor=color,
            markeredgecolor=S.SURFACE, markeredgewidth=2, zorder=4, clip_on=False)
    for p in pts:
        ax.annotate(f"{p['effort']}  {p['pass']:.0f}%", (p["tokens"], p["ci_high"] if above else p["ci_low"]), xytext=(0, 7 if above else -8),
                    textcoords="offset points", fontsize=8.5, color=S.INK, path_effects=S.halo(), zorder=5,
                    va="bottom" if above else "top", ha="center")


def _subset_line(ax, pts, style, label):
    color = S.SERIES_COLOR[D.CASE1]
    xs, ys = [p["tokens"] for p in pts], [p["pass"] for p in pts]
    ax.plot(xs, ys, color=color, linewidth=1.4, linestyle=style, alpha=0.75, marker="o", markersize=5, markerfacecolor=color,
            markeredgecolor=S.SURFACE, markeredgewidth=1, zorder=2)
    ax.annotate(label, (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points", fontsize=8.5,
                color=S.INK, va="center", path_effects=S.halo())


def draw_f1(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    plt = S.setup()
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 6.6), facecolor=S.SURFACE, sharey=True, sharex=True)
    fig.subplots_adjust(left=0.065, right=0.985, top=0.84, bottom=0.20, wspace=0.06)
    n1, n3 = d["case1"][0]["n"], d["case3_base"][0]["n"]
    _curve(a1, d["case1"], D.CASE1, True)
    a1.annotate(f"all {n1} tasks", (d["case1"][-1]["tokens"], d["case1"][-1]["pass"]), xytext=(9, 7), textcoords="offset points",
                fontsize=8.5, color=S.INK, fontweight="bold", va="center", path_effects=S.halo())
    hs = d["header_subsets"]
    if hs:
        _subset_line(a1, hs["header_off"], ":", f"header off ({hs['header_off'][0]['n']})")
        _subset_line(a1, hs["header_on"], "--", f"header on ({hs['header_on'][0]['n']})")
    _curve(a2, d["case3_base"], D.BASE, False)
    _curve(a2, d["case3_hint"], D.HINT, False)
    for pts, name, dy in ((d["case3_hint"], "case 3 + hint", 0), (d["case3_base"], "case 3 base", 0)):
        a2.annotate(name, (pts[-1]["tokens"], pts[-1]["pass"]), xytext=(9, dy), textcoords="offset points", fontsize=8.5,
                    color=S.INK, fontweight="bold", va="center", path_effects=S.halo())
    for ax, title in ((a1, f"Case 1 · Tier B · {n1} tasks"), (a2, f"Case 3 · v3 eval · {n3} tasks")):
        S.style_axes(ax, "both")
        ax.set_xscale("log")
        ax.set_xlim(850, 17000)
        ax.set_ylim(0, 100)
        ax.set_yticks(range(0, 101, 20))
        ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_title(title, loc="left", fontsize=11, fontweight="bold", color=S.INK)
        ax.set_xlabel("Output tokens per task (incl. reasoning), log scale", color=S.INK_2, fontsize=9.5)
    a1.set_ylabel("Pass rate (%)", color=S.INK_2, fontsize=10)
    a2.tick_params(axis="y", left=False)
    S.headline(fig, d["title"], "Higher is better. Mean pass rate vs mean output tokens at low, medium and high effort. Dashed and dotted: case 1 tasks with and without the hidden header.")
    S.caption(fig, d, "Points are labeled with effort and mean pass rate; the header-subset lines have no CI.", n_boot, seed)
    S.save(fig, out_dir, "story_effort_curves", d)


# ------------------------------------------------------------ F2


def f2_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    cells = {s: {e: D.cost_per_solve(ds.tasks(s, e), n_boot, seed, f"f2:{s}:{e}") for e in D.EFFORTS} for s in (D.CASE1, D.BASE, D.HINT)}
    ratios = [cells[D.BASE][e]["value"] / cells[D.HINT][e]["value"] for e in D.EFFORTS if cells[D.BASE][e]["value"] and cells[D.HINT][e]["value"]]
    title = "Effort raises the cost of a solved task; the hint cuts it about 3x"
    if ratios:
        title = f"Effort makes a solved task costlier; the hint cuts case 3 cost {min(ratios):.1f}x to {max(ratios):.1f}x"
    return {"title": title, "cells": cells, "hint_vs_base_cost_ratio": dict(zip(D.EFFORTS, ratios)) if len(ratios) == 3 else ratios}


def draw_f2(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter

    plt = S.setup()
    fig, ax = plt.subplots(figsize=(10.5, 6.4), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.085, right=0.985, top=0.86, bottom=0.18)
    series, w = (D.CASE1, D.BASE, D.HINT), 0.27
    top = 0.0
    for j, s in enumerate(series):
        for i, e in enumerate(D.EFFORTS):
            c = d["cells"][s][e]
            x = i + (j - 1) * (w + 0.01)
            if c["value"] is None:
                ax.annotate(f"none solved\n0/{c['n']}", (x, 0), xytext=(0, 4), textcoords="offset points", ha="center", va="bottom", fontsize=8, color=S.INK_2)
                continue
            ax.bar(x, c["value"], width=w, color=S.SERIES_COLOR[s], edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
            ax.errorbar([x], [c["value"]], yerr=[[c["value"] - c["ci_low"]], [c["ci_high"] - c["value"]]], fmt="none", ecolor=S.INK,
                        elinewidth=1.1, capsize=3, zorder=4)
            top = max(top, c["ci_high"])
            ax.annotate(f"{c['solved']}/{c['n']} solved", (x, c["ci_high"]), xytext=(0, 4), textcoords="offset points", ha="center", va="bottom",
                        fontsize=7.5, color=S.INK_2, zorder=5)
            ax.annotate(S.fmt_k(c["value"]), (x, c["ci_high"]), xytext=(0, 16), textcoords="offset points", ha="center", va="bottom",
                        fontsize=10, fontweight="bold", color=S.INK, zorder=5)
    S.style_axes(ax, "y")
    ax.set_xticks(range(3), [e for e in D.EFFORTS])
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, top * 1.16)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:g}K" if v else "0"))
    ax.set_xlabel("Reasoning effort", color=S.INK_2, fontsize=10)
    ax.set_ylabel("Output tokens per fully solved task", color=S.INK_2, fontsize=10)
    S.legend(ax, [Patch(facecolor=S.SERIES_COLOR[s], label=S.LABEL[s]) for s in series], loc="upper left")
    S.headline(fig, d["title"], "Lower is better. Output tokens incl. reasoning; input tokens not included.")
    S.caption(fig, d, "Cost per solve = mean output tokens x tasks / tasks with pass rate 1.0 (at high: 1.0 in every run); the CI redraws tasks and recomputes the ratio.", n_boot, seed)
    S.save(fig, out_dir, "story_cost_per_solve", d)


# ------------------------------------------------------------ F3


def f3_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    bars = []
    for s in (D.CASE1, D.BASE, D.HINT):
        floor = ds.floor if s != D.CASE1 else None
        for e in D.EFFORTS:
            bars.append({"series": s, "effort": e, **D.outcome_counts(ds.tasks(s, e), floor)})
    split = ds.floor is not None
    p = {b["series"]: b for b in bars if b["effort"] == "high"}
    title = (f"At high effort the hint fully solves {100 * p[D.HINT]['shares'][D.SEG_SOLVED]:.0f}% of case 3 tasks, "
             f"base {100 * p[D.BASE]['shares'][D.SEG_SOLVED]:.0f}%")
    return {"title": title, "bars": bars, "partial_split_by_v1": split,
            "note": (ds.floor_note[:1].upper() + ds.floor_note[1:]) if split else "3 segments for every bar: " + ds.floor_note}


def draw_f3(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    from matplotlib.patches import Patch

    plt = S.setup()
    fig, ax = plt.subplots(figsize=(11, 6.8), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.10, right=0.975, top=0.86, bottom=0.23)
    series_order = [D.CASE1, D.BASE, D.HINT]
    split = d["partial_split_by_v1"]
    y, ticks, used = 0.0, [], set()
    for s in series_order:
        y += 1.15
        ax.text(0, y - 0.62, S.LABEL[s].capitalize() if s != D.CASE1 else "Case 1 Tier B", fontsize=10, fontweight="bold", color=S.INK, va="bottom")
        for b in (b for b in d["bars"] if b["series"] == s):
            left = 0.0
            for seg, share in b["shares"].items():
                col = S.slate(OUTCOME_COLOR_T[seg])
                ax.barh(y, 100 * share, left=left, height=0.72, color=col, edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
                used.add(seg)
                if share >= MIN_LABEL_SHARE:
                    ax.text(left + 50 * share, y, f"{100 * share:.0f}%", ha="center", va="center", fontsize=8.5, color=S.text_on(col), zorder=4)
                left += 100 * share
            ticks.append((y, b["effort"]))
            y += 1
    ax.set_yticks([t for t, _ in ticks], [e for _, e in ticks])
    ax.set_ylim(y, 0.35)
    ax.set_xlim(0, 100)
    S.style_axes(ax, "x")
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Share of tasks (%)", color=S.INK_2, fontsize=10)
    order = [D.SEG_SOLVED] + ([D.SEG_ABOVE, D.SEG_BELOW] if split else []) + [D.SEG_PARTIAL, D.SEG_ZERO]
    if split:  # case 1 bars still use the unsplit partial segment
        order = [D.SEG_SOLVED, D.SEG_ABOVE, D.SEG_BELOW, D.SEG_PARTIAL, D.SEG_ZERO]
    handles = [Patch(facecolor=S.slate(OUTCOME_COLOR_T[k]), edgecolor=S.GRID, label=OUTCOME_LABEL[k]) for k in order if k in used]
    leg = fig.legend(handles=handles, loc="lower center", ncol=3, bbox_to_anchor=(0.5, 0.075), frameon=False, fontsize=8.5)
    for t in leg.get_texts():
        t.set_color(S.INK)
    S.headline(fig, d["title"], "Higher is better (more fully solved). Share of tasks by outcome, darker = more solved. Case 3 partials are split by whether they beat the unchanged-v1 client."
               if split else "Higher is better (more fully solved). Share of tasks by outcome, darker = more solved.")
    S.caption(fig, d, "No CI drawn: bars are counts of tasks. " + d["note"], n_boot, seed, ci=False)
    S.save(fig, out_dir, "story_outcomes", d)
