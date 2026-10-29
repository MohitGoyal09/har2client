#!/usr/bin/env python3
"""Pass rate vs tokens per task, one line per series across reasoning-effort levels.

Like the Artificial Analysis "intelligence vs cost" chart, but the X axis is
tokens, not dollars. Input is one or more batch CSVs (``scripts/run_batch.py``)
or case 3 scored CSVs (``scripts/score_heal.py``); the CSVs carry no model
column, so each CSV is one series and the caller names it::

    uv run --extra plots python scripts/plot_frontier.py \\
        --series "Beam=results/tierb50_v1.csv" \\
        --title "Tier B: pass rate vs tokens" \\
        --out results/figures/tierb50_frontier

Writes ``<out>.png`` (200 dpi), ``<out>.svg`` and ``<out>.json``.

Token accounting (checked in src/har2client/solve/beam.py::_usage and
solve/openai_compat.py): ``completion_tokens`` is copied verbatim from
``response.usage.completion_tokens``. Under the OpenAI convention that figure
INCLUDES reasoning tokens; ``reasoning_tokens`` comes from
``usage.completion_tokens_details.reasoning_tokens`` and is a SUBSET of it.
So output tokens = ``completion_tokens`` alone. ``reasoning_tokens`` is never
added on top (that would double count). ``--x total`` adds ``prompt_tokens``.

Scoring rules:
* y = mean over tasks of (mean over that task's runs of ``pass_rate``), in points.
  A row with status ``api_error`` (the model call failed) is not a model
  result: it is left out of every mean and CI, and its count per effort is
  reported (a warning and ``api_error_rows_excluded`` in the JSON). Other
  failures (``no_code``, ``grade_error``, ...) are written with ``pass_rate`` 0.0
  by run_batch.py and count as 0; an empty or unparseable ``pass_rate`` is also
  counted as 0.
* x = mean over tasks of (mean over that task's runs of the token count). Rows
  with no token data (e.g. an API error) are left out of the task's token mean;
  a task with no token data at all contributes 0 tokens.
* 95% CI on y: percentile bootstrap, 5,000 resamples by default. Each resample
  redraws whole tasks with replacement, then redraws the runs inside each drawn
  task with replacement.
* Only tasks present at every effort of a series are used for that series
  (paired comparison); the dropped count is printed as a warning.
* Pareto set is taken over all points of all series: a point is on it unless
  another point has x <= and y >= with at least one strict.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
DEFAULT_SEED = 20261024
DEFAULT_RESAMPLES = 5000
CI_LEVEL = 0.95
DPI = 200

# Palette: dataviz skill reference palette, categorical slots in fixed order
# (validated with validate_palette.js; adjacent CVD dE >= 9.1). Light surface.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e6e5e1"
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
SERIES_MARKERS = ("o", "s", "^", "D", "v", "P", "X", "*")  # secondary encoding besides colour
REGION_COLOR = "#a8dfb8"


API_ERROR = "api_error"  # run_batch.py status for a failed model call: not a model result


@dataclass(frozen=True)
class Row:
    task: str
    effort: str
    run: str
    status: str
    pass_rate: float
    prompt_tokens: float | None
    completion_tokens: float | None


@dataclass(frozen=True)
class Point:
    series: str
    effort: str
    x: float
    y: float
    ci_low: float
    ci_high: float
    n_tasks: int
    on_pareto: bool = False


def _num(value: object) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except ValueError:
        return None


def parse_row(raw: dict) -> Row:
    pass_rate = _num(raw.get("pass_rate"))
    return Row(
        task=(raw.get("task") or "").strip(),
        effort=(raw.get("effort") or "").strip(),
        run=(raw.get("run") or "").strip(),
        status=(raw.get("status") or "").strip(),
        pass_rate=0.0 if pass_rate is None else pass_rate,
        prompt_tokens=_num(raw.get("prompt_tokens")),
        completion_tokens=_num(raw.get("completion_tokens")),
    )


def load_rows(path: Path) -> list[Row]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [parse_row(r) for r in csv.DictReader(fh)]


def order_efforts(efforts) -> list[str]:
    """Known efforts in low..max order (absent ones skipped), unknown ones last."""
    known = [e for e in EFFORT_ORDER if e in efforts]
    return known + sorted(e for e in efforts if e not in EFFORT_ORDER)


def parse_series(arg: str) -> tuple[str, Path]:
    label, sep, path = arg.partition("=")
    if not sep or not label.strip() or not path.strip():
        raise ValueError(f"--series must look like LABEL=path.csv, got {arg!r}")
    return label.strip(), Path(path.strip())


def _token_value(row: Row, x_mode: str) -> float | None:
    if row.completion_tokens is None:
        return None
    if x_mode == "total":
        return None if row.prompt_tokens is None else row.prompt_tokens + row.completion_tokens
    return row.completion_tokens


def bootstrap_ci(groups: list[list[float]], n_boot: int, seed: int, key: str) -> tuple[float, float]:
    """95% percentile CI (in points) of the mean of per-task mean pass rates.

    ``groups`` holds one list of per-run pass rates (0-1) per task. Each
    resample redraws tasks, then redraws the runs inside each drawn task.
    """
    rng = random.Random(f"{seed}:{key}")  # str seeds hash deterministically
    n = len(groups)
    means = []
    for _ in range(n_boot):
        total = 0.0
        for _ in range(n):
            runs = groups[rng.randrange(n)]
            total += sum(runs[rng.randrange(len(runs))] for _ in runs) / len(runs)
        means.append(100.0 * total / n)
    means.sort()
    tail = (1.0 - CI_LEVEL) / 2.0
    return means[int(tail * n_boot)], means[min(n_boot - 1, int((1.0 - tail) * n_boot))]


def compute_series_points(
    label: str, rows: list[Row], x_mode: str, n_boot: int, seed: int
) -> tuple[list[Point], int]:
    """Points for one series (effort order) and the count of unpaired tasks dropped. `api_error` rows are left out."""
    rows = [r for r in rows if r.status != API_ERROR]
    cells: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in rows:
        cells[(r.task, r.effort)].append(r)
    efforts = order_efforts({e for _, e in cells})
    tasks_by_effort = {e: {t for t, ee in cells if ee == e} for e in efforts}
    all_tasks = set().union(*tasks_by_effort.values()) if efforts else set()
    paired = sorted(set.intersection(*tasks_by_effort.values())) if efforts else []
    dropped = len(all_tasks) - len(paired)

    points = []
    for effort in efforts:
        groups, task_tokens = [], []
        for task in paired:
            cell = cells[(task, effort)]
            groups.append([r.pass_rate for r in cell])
            toks = [v for v in (_token_value(r, x_mode) for r in cell) if v is not None]
            task_tokens.append(statistics.fmean(toks) if toks else 0.0)
        y = 100.0 * statistics.fmean(statistics.fmean(g) for g in groups) if groups else 0.0
        lo, hi = bootstrap_ci(groups, n_boot, seed, f"{label}:{effort}") if groups else (0.0, 0.0)
        x = statistics.fmean(task_tokens) if task_tokens else 0.0
        points.append(Point(label, effort, x, y, lo, hi, len(paired)))
    return points, dropped


def pareto_flags(points: list[Point]) -> list[bool]:
    """True where no other point has x <= and y >= with one strict."""
    flags = []
    for p in points:
        dominated = any(
            q.x <= p.x and q.y >= p.y and (q.x < p.x or q.y > p.y) for q in points if q is not p
        )
        flags.append(not dominated)
    return flags


def medians(points: list[Point]) -> tuple[float, float]:
    return statistics.median(p.x for p in points), statistics.median(p.y for p in points)


def _display_path(path: Path) -> str:
    """Repo-relative path when the file is inside the repo, so figure JSON holds no machine-specific path."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def build_report(
    series: list[tuple[str, Path]], x_mode: str, n_boot: int, seed: int
) -> tuple[list[Point], dict]:
    points: list[Point] = []
    meta_series, warnings = {}, []
    for label, path in series:
        all_rows = load_rows(path)
        rows = [r for r in all_rows if r.status != API_ERROR]
        errors = Counter(r.effort for r in all_rows if r.status == API_ERROR)
        pts, dropped = compute_series_points(label, rows, x_mode, n_boot, seed)
        points.extend(pts)
        run_counts = [len(v) for v in _cell_runs(rows).values()]
        meta_series[label] = {
            "csv": _display_path(path),
            "n_tasks": pts[0].n_tasks if pts else 0,
            "dropped_unpaired_tasks": dropped,
            "runs_per_task_min": min(run_counts, default=0),
            "runs_per_task_max": max(run_counts, default=0),
        }
        if errors:
            meta_series[label]["api_error_rows_excluded"] = {e: errors[e] for e in order_efforts(errors)}
            counts = ", ".join(f"{e}={errors[e]}" for e in order_efforts(errors))
            warnings.append(f"{label}: api_error rows excluded: {counts}")
        if dropped:
            warnings.append(f"{label}: dropped {dropped} task(s) not present at every effort")
    flags = pareto_flags(points)
    points = [dataclasses.replace(p, on_pareto=f) for p, f in zip(points, flags)]
    meta = {"x_mode": x_mode, "resamples": n_boot, "seed": seed, "ci_level": CI_LEVEL,
            "series": meta_series, "warnings": warnings}
    return points, meta


def _cell_runs(rows: list[Row]) -> dict[tuple[str, str], list[Row]]:
    cells: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in rows:
        cells[(r.task, r.effort)].append(r)
    return cells


def _out(out: Path, ext: str) -> Path:
    return Path(f"{out}.{ext}")  # not with_suffix: --out names may contain dots


def write_json(out: Path, points: list[Point], meta: dict) -> Path:
    path = _out(out, "json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "points": [dataclasses.asdict(p) for p in points]}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _caption(meta: dict) -> str:
    parts = []
    for label, s in meta["series"].items():
        runs = s["runs_per_task_min"]
        runs_text = str(runs) if runs == s["runs_per_task_max"] else f"{runs}-{s['runs_per_task_max']}"
        parts.append(f"{label}: {s['n_tasks']} tasks x {runs_text} run(s)")
    return (
        "; ".join(parts)
        + f". Error bars: 95% bootstrap CI ({meta['resamples']:,} resamples of tasks and runs, "
        f"seed {meta['seed']}). Pareto line spans all series."
    )


def plot(points: list[Point], meta: dict, title: str, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    plt.rcParams.update({"font.family": "sans-serif", "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(9, 6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)

    labels = list(dict.fromkeys(p.series for p in points))
    if len(labels) > len(SERIES_COLORS):
        raise ValueError(f"at most {len(SERIES_COLORS)} series are supported, got {len(labels)}")

    xs = [p.x for p in points if p.x > 0]
    x_lo, x_hi = min(xs) / 1.35, max(xs) * 1.35
    y_lo = max(0.0, min(p.ci_low for p in points) - 6.0)
    y_hi = min(103.0, max(p.ci_high for p in points) + 6.0)
    ax.set_xscale("log")
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.set_yticks([tick for tick in ax.get_yticks() if y_lo <= tick <= min(100, y_hi)])

    x_med, y_med = medians(points)
    ax.fill_between([x_lo, max(x_lo, x_med)], [y_med, y_med], [y_hi, y_hi],
                    color=REGION_COLOR, alpha=0.45, linewidth=0, zorder=1)

    halo = [pe.withStroke(linewidth=3, foreground=SURFACE)]
    for i, label in enumerate(labels):
        color, marker = SERIES_COLORS[i], SERIES_MARKERS[i]
        pts = [p for p in points if p.series == label]
        px, py = [p.x for p in pts], [p.y for p in pts]
        ax.plot(px, py, color=color, linewidth=2, solid_capstyle="round", solid_joinstyle="round", zorder=3)
        ax.errorbar(px, py, yerr=[[p.y - p.ci_low for p in pts], [p.ci_high - p.y for p in pts]],
                    fmt="none", ecolor=color, elinewidth=1.25, capsize=3, zorder=3)
        ax.plot(px, py, linestyle="none", marker=marker, markersize=9, markerfacecolor=color,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=4)
    # Coincident effort points occur in saturated tasks and offline smoke runs.
    # Label them together rather than drawing several labels on top of each other.
    import textwrap
    annotations = defaultdict(list)
    for p in points:
        if p.effort not in annotations[(p.x, p.y)]:
            annotations[(p.x, p.y)].append(p.effort)
    for (x, y), efforts in annotations.items():
        ax.annotate(textwrap.fill(", ".join(efforts), width=20), (x, y), xytext=(9, -12),
                    textcoords="offset points", va="top", fontsize=9, color=INK,
                    path_effects=halo, zorder=5)

    front = sorted((p for p in points if p.on_pareto), key=lambda p: (p.x, -p.y))
    ax.plot([p.x for p in front], [p.y for p in front], color="black", linestyle=":", linewidth=1.8, zorder=3.5)

    x_label = ("Total tokens per task (prompt + output), log scale" if meta["x_mode"] == "total"
               else "Output tokens per task (incl. reasoning), log scale")
    ax.set_xlabel(x_label, color=INK_2, fontsize=10)
    ax.set_ylabel("Pass rate (%)", color=INK_2, fontsize=10)
    ax.set_title(title, color=INK, fontsize=13, fontweight="bold", loc="left")
    ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.grid(True, which="major", color=GRID, linewidth=1, linestyle="-", zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_2, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)

    handles = [Line2D([0], [0], color=SERIES_COLORS[i], marker=SERIES_MARKERS[i], markersize=8,
                      markeredgecolor=SURFACE, linewidth=2, label=label) for i, label in enumerate(labels)]
    handles.append(Line2D([0], [0], color="black", linestyle=":", linewidth=1.8, label="Pareto line"))
    handles.append(Patch(facecolor=REGION_COLOR, alpha=0.45, linewidth=0,
                         label="Most attractive (fewer tokens, higher pass rate)"))
    legend = ax.legend(handles=handles, loc="best", fontsize=9, frameon=True, facecolor=SURFACE,
                       edgecolor=GRID, framealpha=0.95)
    for text in legend.get_texts():
        text.set_color(INK)

    fig.text(0.012, 0.012, textwrap.fill(_caption(meta), width=150), fontsize=7.5, color=INK_2, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.09, 1, 1))

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(_out(out, "png"), dpi=DPI, facecolor=SURFACE)
    fig.savefig(_out(out, "svg"), facecolor=SURFACE)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", action="append", required=True, metavar="LABEL=path.csv",
                    help="one series per CSV; repeatable")
    ap.add_argument("--title", default="Pass rate vs tokens per task")
    ap.add_argument("--out", required=True, help="output path prefix (no extension)")
    ap.add_argument("--x", dest="x_mode", choices=("output", "total"), default="output")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    args = ap.parse_args(argv)

    try:
        series = [parse_series(s) for s in args.series]
        points, meta = build_report(series, args.x_mode, args.resamples, args.seed)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not points:
        print("error: no points to plot", file=sys.stderr)
        return 2
    for warning in meta["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)

    out = Path(args.out)
    write_json(out, points, meta)
    plot(points, meta, args.title, out)
    for p in points:
        flag = " *pareto" if p.on_pareto else ""
        print(f"{p.series:12s} {p.effort:7s} x={p.x:10.1f} y={p.y:6.2f} "
              f"CI[{p.ci_low:.1f}, {p.ci_high:.1f}] n={p.n_tasks}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
