"""Shared look for the story figures: palette, axes style, captions, saving. Constants come from plot_frontier."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plot_frontier as pf  # noqa: E402

from .data import BASE, CAPTION, CAPTION_ONE_RUN, CASE1, HINT, NOTICE, PLACEBO  # noqa: E402

SURFACE, INK, INK_2, GRID, DPI = pf.SURFACE, pf.INK, pf.INK_2, pf.GRID, pf.DPI
SERIES_COLOR = {CASE1: pf.SERIES_COLORS[2], BASE: pf.SERIES_COLORS[0], HINT: pf.SERIES_COLORS[1],
                PLACEBO: pf.SERIES_COLORS[3], NOTICE: pf.SERIES_COLORS[6]}
SERIES_MARKER = {CASE1: "^", BASE: "o", HINT: "s", PLACEBO: "D", NOTICE: "X"}
LABEL = {CASE1: "case 1 Tier B", BASE: "case 3 base", HINT: "case 3 + hint", PLACEBO: "placebo", NOTICE: "notice only"}
# One-hue ordered ramp (slate), light to dark. Not a series color: used for ordered categories and the heatmap.
SLATE = ("#f1f2f4", "#c9ced6", "#8f98a8", "#566277", "#2b3649", "#121a2b")
BAR_GAP_PT = 2.0  # surface-coloured gap between adjacent bars and segments


def setup():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "sans-serif", "svg.fonttype": "none"})
    return plt


def halo():
    import matplotlib.patheffects as pe

    return [pe.withStroke(linewidth=3, foreground=SURFACE)]


def slate(t: float) -> str:
    """Colour on the slate ramp, t in 0 (light) .. 1 (dark)."""
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("slate", SLATE)(min(max(t, 0.0), 1.0))


def text_on(color) -> str:
    """INK or white, whichever has the higher WCAG contrast on `color`."""
    from matplotlib.colors import to_rgb

    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in to_rgb(color))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    ink = (lum + 0.05) / 0.0537  # INK #0b0b0b has relative luminance about 0.0033
    return INK if ink >= 1.05 / (lum + 0.05) else "#ffffff"


def style_axes(ax, grid_axis: str = "y") -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(True, axis=grid_axis, color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_2, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def headline(fig, title: str, subtitle: str) -> None:
    fig.text(0.012, 0.985, title, fontsize=14, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.012, 0.985 - 0.058, subtitle, fontsize=9.5, color=INK_2, ha="left", va="top")


MODEL_NAME = "Beam-1.0-501B-A23B"


def git_sha() -> str:
    import subprocess

    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def runs_text(d: dict) -> str:
    """Runs per task behind the figure: high is the mean of 3 runs when the repeats were loaded."""
    return "low, medium 1 run per task; high mean of {} runs".format(d["high_runs"]) if d.get("high_runs", 1) > 1 else "1 run per task"


def footer_text(d: dict) -> str:
    return f"{MODEL_NAME} \u00b7 n={d.get('footer_n', '?')} \u00b7 {runs_text(d)} \u00b7 95% CI over tasks \u00b7 HAR2Client {git_sha()}"


def caption(fig, d: dict, extra: str, n_boot: int, seed: int, ci: bool = True) -> None:
    """Two footer lines: the standard provenance footer, then the figure's own note."""
    tail = f" Error bars: 95% bootstrap over tasks ({n_boot:,} resamples, seed {seed})." if ci else ""
    fig.text(0.012, 0.010, footer_text(d), fontsize=7.5, color=INK_2, ha="left", va="bottom")
    base = CAPTION.replace("3 runs", f"{d['high_runs']} runs") if d.get("high_runs", 1) > 1 else CAPTION_ONE_RUN
    fig.text(0.012, 0.032, f"{base} {extra}{tail}".strip(), fontsize=7.5, color=INK_2, ha="left", va="bottom", wrap=True)


def legend(ax, handles, **kw):
    kw.setdefault("fontsize", 9)
    leg = ax.legend(handles=handles, frameon=True, facecolor=SURFACE, edgecolor=GRID, framealpha=0.95, **kw)
    for t in leg.get_texts():
        t.set_color(INK)
    return leg


def save(fig, out_dir: Path, name: str, data: dict) -> None:
    data = {**data, "footer": footer_text(data)}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{name}.png", dpi=DPI, facecolor=SURFACE)
    fig.savefig(out_dir / f"{name}.svg", facecolor=SURFACE)
    (out_dir / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    import matplotlib.pyplot as plt

    plt.close(fig)


def fmt_k(v: float) -> str:
    return f"{v / 1000:.1f}K" if v >= 1000 else f"{v:.0f}"
