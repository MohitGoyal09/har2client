#!/usr/bin/env python3
"""Case 3 v3 evaluation suite: pass rate by task family, base vs hint, as grouped bars. No Beam API calls.

Called from `scripts/analyze_heal_v3.py` (it holds the scored steps). Writes `<out>.json`, `<out>.png` (same dpi as the
frontier chart) and `<out>.svg`. One effort is shown: `high` when both arms finished it, else the highest complete one
below it. Bars are the mean over tasks of `pass_rate`, in percent. Error bars are a
95% percentile bootstrap over tasks (`plot_frontier.bootstrap_ci`, fixed seed). The last group, "all", is every task.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_heal_v3 as ah  # noqa: E402
import heal_v3_verdicts as hv  # noqa: E402
import high_runs  # noqa: E402
import plot_frontier as pf  # noqa: E402

PREFERRED_EFFORT = "high"
FALLBACK_EFFORTS = ("high", "medium", "low")
ALL = "all"
BAR_COLORS = dict(zip(hv.ARMS, pf.SERIES_COLORS))  # same arm -> colour slots as the frontier chart (base, hint)


def pick_effort(a: hv.Analysis, preferred: str = PREFERRED_EFFORT) -> str | None:
    """`preferred` if both arms finished it, else the highest of high, medium, low that both finished."""
    def both_complete(effort: str) -> bool:
        return all((s := a.steps.get((arm, effort))) is not None and s.complete and bool(s.scores) for arm in hv.ARMS)

    return next((e for e in (preferred, *FALLBACK_EFFORTS) if both_complete(e)), None)


def _row(arm: str, effort: str, name: str, scores: list[hv.TaskScore], n_boot: int, seed: int) -> dict:
    groups = [[s.pass_rate] for s in scores]
    lo, hi = pf.bootstrap_ci(groups, n_boot, seed, f"families:{arm}:{effort}:{name}")
    return {"n": len(scores), "pass_rate": 100.0 * hv.mean(s.pass_rate for s in scores), "ci_low": lo, "ci_high": hi}


def family_points(a: hv.Analysis, effort: str, n_boot: int, seed: int) -> dict:
    """Per arm, per family (FAMILY_ORDER first) and "all": n, mean pass rate and 95% bootstrap CI, in percent."""
    data: dict = {"effort": effort, "n_boot": n_boot, "seed": seed, "ci_level": pf.CI_LEVEL,
                  "runs_per_task": min(a.steps[arm, effort].runs for arm in hv.ARMS)}
    for arm in hv.ARMS:
        scores = list(a.steps[arm, effort].scores.values())
        families = sorted({s.family for s in scores}, key=ah._family_key)
        data[arm] = {f: _row(arm, effort, f, [s for s in scores if s.family == f], n_boot, seed) for f in families}
        data[arm][ALL] = _row(arm, effort, ALL, scores, n_boot, seed)
    return data


def plot_families(data: dict, title: str, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    plt.rcParams.update({"font.family": "sans-serif", "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(10, 6), facecolor=pf.SURFACE)
    ax.set_facecolor(pf.SURFACE)

    groups = list(data[hv.ARMS[0]])
    width = 0.38
    for i, arm in enumerate(hv.ARMS):
        rows = [data[arm][g] for g in groups]
        xs = [k + (i - 0.5) * width for k in range(len(groups))]
        ys = [r["pass_rate"] for r in rows]
        ax.bar(xs, ys, width=width * 0.94, color=BAR_COLORS[arm], zorder=3)
        ax.errorbar(xs, ys, yerr=[[r["pass_rate"] - r["ci_low"] for r in rows], [r["ci_high"] - r["pass_rate"] for r in rows]],
                    fmt="none", ecolor=pf.INK, elinewidth=1.1, capsize=3, zorder=4)
        for x, r in zip(xs, rows):
            ax.annotate(f"{r['pass_rate']:.0f}", (x, r["ci_high"]), xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=9, color=pf.INK, zorder=5)

    ax.set_xticks(range(len(groups)), [f"{g}\n(n={data[hv.ARMS[0]][g]['n']})" for g in groups])
    ax.set_ylim(0, 118)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("Pass rate (%)", color=pf.INK_2, fontsize=10)
    ax.set_title(title, color=pf.INK, fontsize=13, fontweight="bold", loc="left")
    ax.grid(True, axis="y", color=pf.GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=pf.INK_2, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(pf.GRID)
    handles = [Patch(facecolor=BAR_COLORS[arm], label=ah.SERIES_LABEL[arm]) for arm in hv.ARMS]
    legend = ax.legend(handles=handles, loc="upper left", ncol=2, fontsize=9, frameon=True, facecolor=pf.SURFACE, edgecolor=pf.GRID, framealpha=0.95)
    for text in legend.get_texts():
        text.set_color(pf.INK)

    caption = (f"Error bars: {data['ci_level']:.0%} bootstrap CI over tasks ({data['n_boot']:,} resamples, seed {data['seed']}). "
               f"Beam {high_runs.label(data['effort'], data.get('runs_per_task', 1))}; \"all\" is every task; bar labels are the mean pass rate.")
    fig.text(0.012, 0.012, caption, fontsize=7.5, color=pf.INK_2, ha="left", va="bottom", wrap=True)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pf._out(out, "png"), dpi=pf.DPI, facecolor=pf.SURFACE)
    fig.savefig(pf._out(out, "svg"), facecolor=pf.SURFACE)
    plt.close(fig)


def write_family_chart(a: hv.Analysis, out: Path, effort: str | None = None) -> dict | None:
    """Write `<out>.json`, `.png` and `.svg`; None (nothing written) when no effort has both arms complete."""
    effort = effort or pick_effort(a)
    if effort is None:
        return None
    data = family_points(a, effort, pf.DEFAULT_RESAMPLES, pf.DEFAULT_SEED)
    path = pf._out(out, "json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    plot_families(data, f"Case 3 v3 eval: pass rate by task family, Beam {high_runs.label(effort, data['runs_per_task'])}, base vs hint", out)
    return data
