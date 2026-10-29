"""F8 hint vs effort, F9 output-token split, F10 task flips between effort steps."""

from __future__ import annotations

from pathlib import Path

from . import data as D
from . import style as S

MINUS = "−"


def _signed(v: float, unit: str = " pts") -> str:
    return f"{'+' if v >= 0 else MINUS}{abs(v):.0f}{unit}"


def tint(color: str, keep: float = 0.45) -> str:
    """`color` mixed with the surface: the lighter shade used for losses and for the answer tokens."""
    from matplotlib.colors import to_hex, to_rgb

    c, bg = to_rgb(color), to_rgb(S.SURFACE)
    return to_hex(tuple(keep * a + (1 - keep) * b for a, b in zip(c, bg)))


# ------------------------------------------------------------ F8


def f8_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    d = D.hint_vs_effort(ds, n_boot, seed)
    ratio = d["token_ratio_base_high_over_hint_low"]
    verb = "beats" if d["hint_low_beats_base_high"] else "matches"
    d["title"] = (f"The hint at low effort {verb} base at high effort"
                  + (f", with {ratio:.1f}x fewer output tokens" if ratio and ratio > 1 else ""))
    return d


def draw_f8(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt = S.setup()
    fig, ax = plt.subplots(figsize=(10.5, 6.6), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.085, right=0.985, top=0.80, bottom=0.25)
    w = 0.34
    for i, e in enumerate(D.EFFORTS):
        row = d["efforts"][e]
        for arm, dx in ((D.BASE, -w / 2 - 0.01), (D.HINT, w / 2 + 0.01)):
            b = row[arm]
            ax.bar(i + dx, b["pass"], width=w, color=S.SERIES_COLOR[arm], edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
            ax.errorbar([i + dx], [b["pass"]], yerr=[[b["pass"] - b["ci_low"]], [b["ci_high"] - b["pass"]]], fmt="none", ecolor=S.INK,
                        elinewidth=1.1, capsize=3, zorder=4)
            ax.annotate(f"{b['pass']:.0f}%", (i + dx, 0), xytext=(0, 8), textcoords="offset points", ha="center", va="bottom",
                        fontsize=10, fontweight="bold", color=S.text_on(S.SERIES_COLOR[arm]), zorder=5)
        g = row["gain"]
        top = max(row[D.BASE]["ci_high"], row[D.HINT]["ci_high"]) + 3
        ax.plot([i - w / 2 - 0.01, i - w / 2 - 0.01, i + w / 2 + 0.01, i + w / 2 + 0.01], [top - 1.5, top, top, top - 1.5], color=S.INK_2, linewidth=1,
                zorder=5, clip_on=False)
        ax.annotate(f"{_signed(g['points'])}  [{g['ci_low']:+.0f}, {g['ci_high']:+.0f}]".replace("-", MINUS), (i, top), xytext=(0, 4),
                    textcoords="offset points", ha="center", va="bottom", fontsize=9.5, fontweight="bold", color=S.INK, zorder=5, annotation_clip=False)
    ref = d["base_high_pass"]
    ax.axhline(ref, color=S.INK_2, linewidth=1, linestyle=(0, (4, 3)), zorder=2)
    ax.annotate(f"base at high effort: {ref:.1f}%", (-0.55, ref), xytext=(2, 4), textcoords="offset points", ha="left", va="bottom", fontsize=8.5,
                color=S.INK, path_effects=S.halo(), zorder=6)
    S.style_axes(ax, "y")
    ax.set_xticks(range(3), D.EFFORTS)
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, 100)
    ax.set_yticks(range(0, 101, 20))
    ax.set_xlabel("Reasoning effort", color=S.INK_2, fontsize=10)
    ax.set_ylabel("Pass rate (%)", color=S.INK_2, fontsize=10)
    leg = fig.legend(handles=[Patch(facecolor=S.SERIES_COLOR[a], label=S.LABEL[a]) for a in (D.BASE, D.HINT)] +
                     [Line2D([], [], color=S.INK_2, linestyle=(0, (4, 3)), linewidth=1, label="base pass rate at high effort")],
                     loc="lower center", ncol=3, bbox_to_anchor=(0.5, 0.075), frameon=False, fontsize=9)
    for t in leg.get_texts():
        t.set_color(S.INK)
    S.headline(fig, d["title"], "Higher is better. Case 3, base vs hint at each effort. Brackets: paired hint minus base gain in points [95% CI over tasks].")
    S.caption(fig, d, "Dashed line: base pass rate at high effort.", n_boot, seed)
    S.save(fig, out_dir, "story_hint_vs_effort", d)


# ------------------------------------------------------------ F9


def f9_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    cells = D.tokens_split_all(ds)
    flat = [c for v in cells.values() for c in v.values()]
    missing = sum(c["rows_missing_reasoning"] for c in flat)
    over = sum(c["rows_reasoning_over_completion"] for c in flat)
    share = {s: {e: c["reasoning_share"] for e, c in v.items()} for s, v in cells.items()}
    note = "reasoning_tokens present on every row and never above completion_tokens"
    if missing or over:
        note = f"{missing} row(s) lack reasoning_tokens (left out of the split); {over} row(s) have reasoning above completion tokens"
    at = {e: [cells[s][e]["reasoning_share"] for s in cells] for e in D.EFFORTS}
    title = (f"Reasoning is {100 * min(at['low']):.0f}-{100 * max(at['low']):.0f}% of output tokens at low effort "
             f"and {100 * min(at['high']):.0f}-{100 * max(at['high']):.0f}% at high")
    return {"title": title, "cells": cells,
            "reasoning_share": share, "data_check": note, "rows_missing_reasoning": missing, "rows_reasoning_over_completion": over}


def draw_f9(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    from matplotlib.patches import Patch

    plt = S.setup()
    fig, ax = plt.subplots(figsize=(10.5, 6.6), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.085, right=0.985, top=0.82, bottom=0.20)
    series, w, top = (D.CASE1, D.BASE, D.HINT), 0.27, 0.0
    for j, s in enumerate(series):
        for i, e in enumerate(D.EFFORTS):
            c = d["cells"][s][e]
            x = i + (j - 1) * (w + 0.01)
            col = S.SERIES_COLOR[s]
            ax.bar(x, c["reasoning"], width=w, color=col, edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
            ax.bar(x, c["answer"], bottom=c["reasoning"], width=w, color=tint(col), edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
            top = max(top, c["completion"])
            ax.annotate(S.fmt_k(c["completion"]), (x, c["completion"]), xytext=(0, 16), textcoords="offset points", ha="center", va="bottom",
                        fontsize=10, fontweight="bold", color=S.INK, zorder=5)
            ax.annotate(f"{100 * c['reasoning_share']:.0f}% reasoning", (x, c["completion"]), xytext=(0, 4), textcoords="offset points", ha="center",
                        va="bottom", fontsize=7.5, color=S.INK_2, zorder=5)
    S.style_axes(ax, "y")
    ax.set_xticks(range(3), D.EFFORTS)
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, top * 1.16)
    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:g}K" if v else "0"))
    ax.set_xlabel("Reasoning effort", color=S.INK_2, fontsize=10)
    ax.set_ylabel("Mean output tokens per task", color=S.INK_2, fontsize=10)
    handles = [Patch(facecolor=S.SERIES_COLOR[s], label=f"{S.LABEL[s]}: reasoning") for s in series]
    handles += [Patch(facecolor=tint(S.SERIES_COLOR[s]), label=f"{S.LABEL[s]}: answer") for s in series]
    S.legend(ax, handles, loc="upper left", ncol=2, fontsize=8)
    S.headline(fig, d["title"], "Lower is better · output tokens include reasoning. Solid = reasoning tokens, light = answer (completion minus reasoning).")
    S.caption(fig, d, "Bar labels: total mean output tokens and the reasoning share. Data check: " + d["data_check"] + ".", n_boot, seed, ci=False)
    S.save(fig, out_dir, "story_tokens_split", d)


# ------------------------------------------------------------ F10


def f10_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    rows = D.flips_all(ds)
    gains, losses = sum(r["gains"] for r in rows), sum(r["losses"] for r in rows)
    return {"title": f"Across six effort steps: {gains} gains and {losses} losses; the net gain hides churn", "rows": rows,
            "total_gains": gains, "total_losses": losses}


def draw_f10(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    from matplotlib.patches import Patch

    plt = S.setup()
    fig, ax = plt.subplots(figsize=(10.5, 6.8), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.13, right=0.90, top=0.84, bottom=0.25)
    peak = max(max(r["gains"], r["losses"]) for r in d["rows"])
    y, ticks = 0.0, []
    for s in (D.CASE1, D.BASE, D.HINT):
        y += 1.15
        ax.text(-0.01, y - 0.62, "Case 1 Tier B" if s == D.CASE1 else S.LABEL[s].capitalize(), fontsize=10, fontweight="bold", color=S.INK, va="bottom",
                ha="right", transform=ax.get_yaxis_transform())
        for r in (r for r in d["rows"] if r["series"] == s):
            col = S.SERIES_COLOR[s]
            ax.barh(y, r["gains"], height=0.62, color=col, edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
            ax.barh(y, -r["losses"], height=0.62, color=tint(col), edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
            ax.annotate(f"+{r['gains']}", (r["gains"], y), xytext=(5, 0), textcoords="offset points", ha="left", va="center", fontsize=9.5, fontweight="bold", color=S.INK)
            ax.annotate((f"{MINUS}{r['losses']}" if r["losses"] else "0"), (-r["losses"], y), xytext=(-5, 0), textcoords="offset points", ha="right", va="center", fontsize=9.5, fontweight="bold", color=S.INK)
            ax.annotate(f"net {'+' if r['net'] >= 0 else MINUS}{abs(r['net'])}", (1.0, y), xycoords=("axes fraction", "data"), xytext=(8, 0),
                        textcoords="offset points", ha="left", va="center", fontsize=9.5, color=S.INK)
            ticks.append((y, r["step"]))
            y += 1
    ax.axvline(0, color=S.INK_2, linewidth=1, zorder=4)
    ax.set_yticks([t for t, _ in ticks], [t for _, t in ticks])
    ax.set_ylim(y, 0.35)
    ax.set_xlim(-peak * 1.25, peak * 1.25)
    S.style_axes(ax, "x")
    ax.tick_params(axis="y", length=0, labelsize=9.5, colors=S.INK)
    ax.xaxis.set_major_formatter(__import__("matplotlib.ticker", fromlist=["x"]).FuncFormatter(lambda v, _: f"{abs(v):g}"))
    ax.set_xlabel("Tasks (losses to the left, gains to the right)", color=S.INK_2, fontsize=10)
    leg = fig.legend(handles=[Patch(facecolor=S.INK_2, label="gained: not fully solved, then fully solved (solid)"),
                              Patch(facecolor=tint(S.INK_2), label="lost: fully solved, then not (lighter tint)")],
                     loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.075), frameon=False, fontsize=8.5)
    for t in leg.get_texts():
        t.set_color(S.INK)
    S.headline(fig, d["title"], ("Same tasks; low and medium are one run, high is the mean of {} runs: some movement is run-to-run noise. ".format(d["high_runs"]) if d.get("high_runs", 1) > 1
                                       else "Same tasks, one run each: some movement is run-to-run noise. ") + "Net above zero (more gains than losses) is better.")
    S.caption(fig, d, "Fully solved = pass rate 1.0 (at high: in every run). Colour = series (case 1 green, case 3 base blue, case 3 hint orange); the lighter tint marks losses. No CI drawn.", n_boot, seed, ci=False)
    S.save(fig, out_dir, "story_task_flips", d)
