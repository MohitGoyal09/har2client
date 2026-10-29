"""F4 family heatmap, F5 header gap, F6 change kinds, F7 hint decomposition."""

from __future__ import annotations

from pathlib import Path

from . import data as D
from . import style as S

MINUS = "−"
CHANGE_LABEL = {}  # change kinds are shown as stored, underscores to spaces


def _signed(v: float, unit: str = " pts") -> str:
    return f"{'+' if v >= 0 else MINUS}{abs(v):.0f}{unit}"


# ------------------------------------------------------------ F4


def f4_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    m = D.family_matrix(ds)
    col = {(c["arm"], c["effort"]): j for j, c in enumerate(m["cols"])}
    gains = [m["matrix"][i][col[D.HINT, "low"]] - m["matrix"][i][col[D.BASE, "low"]] for i in range(len(m["rows"]))]
    best = max(range(len(gains)), key=gains.__getitem__)
    m["title"] = f"The hint lifts every family; {m['rows'][best]['family']} gains most at low effort ({_signed(gains[best])})" \
        if all(g > 0 for g in gains) else "Hint vs base by task family: the gain is not the same in every family"
    return m


def draw_f4(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    plt = S.setup()
    rows, cols, mat = d["rows"], d["cols"], d["matrix"]
    fig, ax = plt.subplots(figsize=(10.5, 6.2), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.17, right=0.985, top=0.78, bottom=0.23)
    gap = 0.35  # visible gap between the arm groups
    xs = [j + (gap if c["arm"] == D.HINT else 0.0) for j, c in enumerate(cols)]
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            v = mat[i][j]
            color = S.slate(v / 100.0)
            ax.add_patch(plt.Rectangle((xs[j] - 0.5, i - 0.5), 1, 1, facecolor=color, edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3))
            ax.text(xs[j], i, f"{v:.0f}", ha="center", va="center", fontsize=11, fontweight="bold", color=S.text_on(color), zorder=4)
    ax.set_xlim(-0.5, xs[-1] + 0.5)
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.set_yticks(range(len(rows)), [f"{r['family']}  (n={r['n']})" for r in rows])
    ax.xaxis.tick_top()
    ax.set_xticks(xs, [c["effort"] for c in cols])
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(colors=S.INK, labelsize=9.5, length=0)
    for arm, label in ((D.BASE, "case 3 base"), (D.HINT, "case 3 + hint")):
        js = [j for j, c in enumerate(cols) if c["arm"] == arm]
        mid = (xs[js[0]] + xs[js[-1]]) / 2
        ax.annotate(label, (mid, 1.0), xycoords=("data", "axes fraction"), xytext=(0, 38), textcoords="offset points", ha="center", va="bottom",
                    fontsize=10.5, fontweight="bold", color=S.INK)
        ax.plot([xs[js[0]] - 0.45, xs[js[-1]] + 0.45], [1.075, 1.075], transform=ax.get_xaxis_transform(), color=S.SERIES_COLOR[arm],
                linewidth=3, solid_capstyle="butt", clip_on=False)
    # colour key for the ramp
    cax = fig.add_axes([0.62, 0.125, 0.25, 0.02])
    import numpy as np

    cax.imshow(np.linspace(0, 1, 256)[None, :], aspect="auto", cmap=__import__("matplotlib.colors", fromlist=["x"]).LinearSegmentedColormap.from_list("s", S.SLATE))
    cax.set_yticks([])
    cax.set_xticks([0, 255], ["0%", "100%"])
    cax.tick_params(labelsize=8, colors=S.INK_2, length=0)
    for side in cax.spines.values():
        side.set_visible(False)
    fig.text(0.60, 0.135, "mean pass rate", ha="right", va="center", fontsize=8.5, color=S.INK_2)
    S.headline(fig, d["title"], f"Case 3 v3 eval ({sum(r['n'] for r in rows)} tasks): higher is better. Mean pass rate (%) per task family, by arm and effort; darker = higher.")
    S.caption(fig, d, f"Cells are means over the family's tasks; no CI drawn (families hold {min(r['n'] for r in rows)} to {max(r['n'] for r in rows)} tasks).", n_boot, seed, ci=False)
    S.save(fig, out_dir, "story_family_heatmap", d)


# ------------------------------------------------------------ F5


def f5_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    rows = D.header_gap(ds, n_boot, seed)
    return {"title": "Case 1 is hard because of one hidden header",
            "rows": rows, "gap_by_effort": {r["effort"]: r["gap_points"] for r in rows}}


def draw_f5(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    from matplotlib.lines import Line2D

    plt = S.setup()
    fig, ax = plt.subplots(figsize=(10.5, 5.4), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.10, right=0.94, top=0.84, bottom=0.23)
    green = S.SERIES_COLOR[D.CASE1]
    for i, r in enumerate(d["rows"]):
        on, off = r["on"], r["off"]
        ax.plot([on["pass"], off["pass"]], [i, i], color=green, linewidth=3, alpha=0.5, zorder=2, solid_capstyle="round")
        for g, key, filled in ((r["off"], "off", True), (r["on"], "on", False)):
            ax.errorbar([g["pass"]], [i], xerr=[[g["pass"] - g["ci_low"]], [g["ci_high"] - g["pass"]]], fmt="none", ecolor=S.INK_2,
                        elinewidth=1, capsize=3, zorder=3)
            ax.plot([g["pass"]], [i], marker="o", markersize=11, markerfacecolor=green if filled else S.SURFACE, markeredgecolor=green,
                    markeredgewidth=2.2, zorder=4, linestyle="none", clip_on=False)
            ax.annotate(f"{g['pass']:.0f}%", (g["pass"], i), xytext=(0, 13), textcoords="offset points", ha="center", va="bottom",
                        fontsize=10, fontweight="bold", color=S.INK, zorder=5)
        mid = (on["pass"] + off["pass"]) / 2
        ax.annotate(f"{MINUS}{abs(r['gap_points']):.0f} pts" if r["gap_points"] < 0 else f"+{r['gap_points']:.0f} pts", (mid, i), xytext=(0, -12),
                    textcoords="offset points", ha="center", va="top", fontsize=11, fontweight="bold", color=S.INK, path_effects=S.halo(), zorder=5)
    ax.set_yticks(range(len(d["rows"])), [r["effort"] for r in d["rows"]])
    ax.set_ylim(len(d["rows"]) - 0.4, -0.7)
    ax.set_xlim(0, 100)
    ax.set_xticks(range(0, 101, 20))
    S.style_axes(ax, "x")
    ax.tick_params(axis="y", length=0, labelsize=10, colors=S.INK)
    ax.set_xlabel("Pass rate (%)", color=S.INK_2, fontsize=10)
    ax.set_ylabel("Reasoning effort", color=S.INK_2, fontsize=10)
    n_on, n_off = d["rows"][0]["on"]["n"], d["rows"][0]["off"]["n"]
    S.legend(ax, [Line2D([], [], marker="o", linestyle="none", markersize=10, markerfacecolor=green, markeredgecolor=green, markeredgewidth=2, label=f"header off ({n_off} tasks)"),
                  Line2D([], [], marker="o", linestyle="none", markersize=10, markerfacecolor=S.SURFACE, markeredgecolor=green, markeredgewidth=2, label=f"header on ({n_on} tasks)")],
             loc="lower left")
    S.headline(fig, d["title"], "Case 1 Tier B, mean pass rate for tasks with the hidden header on vs off. Higher pass rate is better. The label is the gap (on minus off); whiskers are 95% CIs.")
    S.caption(fig, d, "Two groups of different tasks (independent groups).", n_boot, seed)
    S.save(fig, out_dir, "story_header_gap", d)


# ------------------------------------------------------------ F6


def f6_data(ds: D.Dataset, n_boot: int, seed: int) -> dict:
    rows = D.change_kind_credit(ds, "high")
    gaps = [(r[D.HINT] - r[D.BASE], r["change"]) for r in rows if r[D.HINT] is not None and r[D.BASE] is not None]
    top = max(gaps)
    lead = "lifts every change kind" if all(g > 0 for g, _ in gaps) else "lifts most change kinds"
    return {"title": f"At high effort the hint {lead}; {top[1].replace('_', ' ')} gains most ({_signed(top[0])})",
            "effort": "high", "rows": rows}


def draw_f6(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    plt = S.setup()
    rows = d["rows"]
    fig, ax = plt.subplots(figsize=(10.5, 1.9 + 0.5 * len(rows)), facecolor=S.SURFACE)
    top = 0.82 if len(rows) > 6 else 0.74
    fig.subplots_adjust(left=0.26, right=0.93, top=top, bottom=0.18)
    for i, r in enumerate(rows):
        b, h = r[D.BASE], r[D.HINT]
        if b is not None and h is not None:
            ax.plot([b, h], [i, i], color=S.GRID, linewidth=2, zorder=2, solid_capstyle="round")
        for arm, v, other in ((D.BASE, b, h), (D.HINT, h, b)):
            if v is None:
                continue
            ax.plot([v], [i], marker=S.SERIES_MARKER[arm], markersize=10, markerfacecolor=S.SERIES_COLOR[arm], markeredgecolor=S.SURFACE,
                    markeredgewidth=1.5, linestyle="none", zorder=4)
            right = other is None or v >= other
            ax.annotate(f"{v:.0f}%", (v, i), xytext=(10 if right else -10, 0), textcoords="offset points", ha="left" if right else "right",
                        va="center", fontsize=9, color=S.INK, path_effects=S.halo(), zorder=5)
    labels = [f"{r['change'].replace('_', ' ')}  (n={r['n_base'] if r['n_base'] == r['n_hint'] else str(r['n_base']) + '/' + str(r['n_hint'])})" for r in rows]
    ax.set_yticks(range(len(rows)), labels)
    ax.set_ylim(len(rows) - 0.5, -0.6)
    ax.set_xlim(0, 100)
    ax.set_xticks(range(0, 101, 20))
    S.style_axes(ax, "x")
    ax.tick_params(axis="y", length=0, labelsize=9.5, colors=S.INK)
    ax.set_xlabel("Mean credit per task (% of hidden tests that need this change kind)", color=S.INK_2, fontsize=10)
    from matplotlib.lines import Line2D

    S.legend(ax, [Line2D([], [], marker=S.SERIES_MARKER[a], linestyle="none", markersize=9, markerfacecolor=S.SERIES_COLOR[a], markeredgecolor=S.SURFACE, label=S.LABEL[a])
                  for a in (D.BASE, D.HINT)], loc="lower right", bbox_to_anchor=(1.0, 1.01), ncol=2, borderaxespad=0)
    S.headline(fig, d["title"], "Higher is better. Case 3 at high effort: per-change-kind credit, base vs hint. Rows sorted by base credit; n = tasks whose release holds the change.")
    S.caption(fig, d, "Credit = pass share of the hidden tests that need the change, averaged over tasks; no CI drawn.", n_boot, seed, ci=False)
    S.save(fig, out_dir, "story_change_kinds", d)


# ------------------------------------------------------------ F7


def f7_data(ds: D.Dataset, n_boot: int, seed: int, batch_root: Path | None = None) -> dict:
    if batch_root is None:
        raise D.Skip("no --batch-root given")
    steps = D.load_decomposition(batch_root, len(ds.tasks(D.BASE, D.DECOMP_EFFORT)))
    d = D.decomposition(steps, n_boot, seed)
    g = {x["to"]: x for x in d["gains"]}
    d["title"] = f"Where the hint's gain comes from, case 3 at low effort: full hint {_signed(d['bars'][D.HINT]['pass'] - d['bars'][D.BASE]['pass'])} over base"
    d["_gain_by_to"] = {k: v["gain_points"] for k, v in g.items()}
    return d


def draw_f7(d: dict, out_dir: Path, n_boot: int, seed: int) -> None:
    plt = S.setup()
    fig, ax = plt.subplots(figsize=(10.5, 6.4), facecolor=S.SURFACE)
    fig.subplots_adjust(left=0.085, right=0.985, top=0.82, bottom=0.22)
    arms = D.DECOMP_ARMS
    for i, a in enumerate(arms):
        b = d["bars"][a]
        ax.bar(i, b["pass"], width=0.6, color=S.SERIES_COLOR[a], edgecolor=S.SURFACE, linewidth=S.BAR_GAP_PT, zorder=3)
        ax.errorbar([i], [b["pass"]], yerr=[[b["pass"] - b["ci_low"]], [b["ci_high"] - b["pass"]]], fmt="none", ecolor=S.INK, elinewidth=1.1, capsize=3, zorder=4)
        ax.annotate(f"{b['pass']:.0f}%", (i, b["ci_high"]), xytext=(0, 5), textcoords="offset points", ha="center", va="bottom", fontsize=11, fontweight="bold", color=S.INK)
    for i, g in enumerate(d["gains"]):
        y = min(max(d["bars"][g["from"]]["ci_high"], d["bars"][g["to"]]["ci_high"]) + 8, 92)
        ax.plot([i + 0.1, i + 0.9], [y, y], color=S.INK_2, linewidth=1, zorder=5)
        ax.annotate(f"{_signed(g['gain_points'])}\n[{g['ci_low']:+.0f}, {g['ci_high']:+.0f}]".replace("-", MINUS), ((i + 0.5), y), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=9, color=S.INK, zorder=5)
    S.style_axes(ax, "y")
    ax.set_xticks(range(len(arms)), [S.LABEL[a] for a in arms], fontsize=10)
    ax.set_ylim(0, 100)
    ax.set_xlim(-0.6, len(arms) - 0.4)
    ax.set_ylabel("Pass rate (%)", color=S.INK_2, fontsize=10)
    S.headline(fig, d["title"], f"Higher is better. Case 3 low effort, {d['bars'][D.BASE]['n']} tasks. Brackets: paired gain between neighbouring bars, points [95% CI over tasks].")
    S.caption(fig, d, "Placebo = same-length neutral text; notice = hint that only says the API changed; hint = full contract hint.", n_boot, seed)
    S.save(fig, out_dir, "story_hint_decomposition", d)
