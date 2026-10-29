#!/usr/bin/env python3
"""Static story figures (PNG + SVG + JSON) for the blog post and the dataset card. No Beam API calls.

    uv run --extra plots python scripts/plot_story.py [--only F1,F3] [--batch-root runs/batch]

Reads the scored CSVs in results/ (case 1 Tier B, case 3 v3 eval), the per-task unchanged-v1 floor
(results/heal_v3_eval_v1_floor.json, optional) and the hidden-header flag in runs/tierb50. F7 needs the placebo and
notice batch steps; without them it prints "F7 skipped" and writes nothing. Shared style: scripts/plot_frontier.py.
Low and medium use 1 run per task; high is the per-task mean of 3 runs when `results/*_high_r2.csv`, `_r3.csv` exist ("solved" at
high = pass rate 1.0 in every run). Error bars are 95% bootstrap CIs over tasks only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from story_figs import data as D  # noqa: E402
from story_figs import figs_a, figs_b, figs_c  # noqa: E402
from story_figs.data import ROOT  # noqa: E402

import plot_frontier as pf  # noqa: E402
import analyze_heal_v3 as ah  # noqa: E402

DEFAULT_OUT = ROOT / "results" / "figures"
# id -> (file stem, build(ds, n_boot, seed, **ctx) -> dict, draw(data, out_dir, n_boot, seed))
FIGURES = {
    "F1": ("story_effort_curves", figs_a.f1_data, figs_a.draw_f1),
    "F2": ("story_cost_per_solve", figs_a.f2_data, figs_a.draw_f2),
    "F3": ("story_outcomes", figs_a.f3_data, figs_a.draw_f3),
    "F4": ("story_family_heatmap", figs_b.f4_data, figs_b.draw_f4),
    "F5": ("story_header_gap", figs_b.f5_data, figs_b.draw_f5),
    "F6": ("story_change_kinds", figs_b.f6_data, figs_b.draw_f6),
    "F7": ("story_hint_decomposition", figs_b.f7_data, figs_b.draw_f7),
    "F8": ("story_hint_vs_effort", figs_c.f8_data, figs_c.draw_f8),
    "F9": ("story_tokens_split", figs_c.f9_data, figs_c.draw_f9),
    "F10": ("story_task_flips", figs_c.f10_data, figs_c.draw_f10),
}
CASE1_ONLY, BOTH_CASES = {"F5"}, {"F1", "F2", "F3", "F9", "F10"}  # footer n: the other figures are case 3 only


def footer_n(ds: D.Dataset, fid: str) -> str:
    n1 = len(ds.steps[D.CASE1, "low"]) if (D.CASE1, "low") in ds.steps else 0
    n3 = len(ds.steps[D.BASE, "low"]) if (D.BASE, "low") in ds.steps else 0
    if fid in CASE1_ONLY:
        return str(n1)
    return f"{n1} case 1, {n3} case 3" if fid in BOTH_CASES else str(n3)


def render(ds: D.Dataset, out_dir: Path, only: list[str] | None = None, batch_root: Path | None = None,
           n_boot: int = pf.DEFAULT_RESAMPLES, seed: int = pf.DEFAULT_SEED) -> dict[str, str]:
    """Build the chosen figures; returns {id: status line}. A figure that waits for data is skipped, never half-written."""
    status = {}
    for fid, (stem, build, draw) in FIGURES.items():
        if only and fid not in only:
            continue
        try:
            kwargs = {"batch_root": batch_root} if fid == "F7" else {}
            data = build(ds, n_boot, seed, **kwargs)
        except D.Skip as exc:
            status[fid] = f"{fid} skipped: {exc}"
            continue
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        data["footer_n"] = footer_n(ds, fid)
        data["high_runs"] = ds.high_runs
        draw(data, out_dir, n_boot, seed)
        status[fid] = f"{fid} {stem}: {data['title']}"
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma list, e.g. F1,F3 (default: all)")
    ap.add_argument("--batch-root", type=Path, default=ah.DEFAULT_BATCH_ROOT, help="batch steps for F7 (read-only)")
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results")
    ap.add_argument("--tasks-tierb", type=Path, default=ROOT / "runs" / "tierb50", help="Tier B tasks (hidden_header flag)")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--n-boot", type=int, default=pf.DEFAULT_RESAMPLES)
    ap.add_argument("--seed", type=int, default=pf.DEFAULT_SEED)
    args = ap.parse_args(argv)
    only = [f.strip().upper() for f in args.only.split(",")] if args.only else None
    if only and (bad := [f for f in only if f not in FIGURES]):
        print(f"error: unknown figure(s) {', '.join(bad)}; choose from {', '.join(FIGURES)}", file=sys.stderr)
        return 2
    ds = D.load_dataset(args.results_dir, args.tasks_tierb)
    for line in render(ds, args.out_dir, only, args.batch_root, args.n_boot, args.seed).values():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
