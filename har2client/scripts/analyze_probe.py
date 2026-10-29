#!/usr/bin/env python3
"""Case 2 coverage, paired arm comparisons, and pass-rate/token plots.

Only complete task/run/effort blocks (all three arms) enter paired comparisons.
A missing cell removes that effort's block, not other efforts. The frontier
plot uses blocks complete at every plotted effort; an observed effort with no
complete block is omitted from the frontier. API errors and grading
infrastructure errors are excluded, not scored as model failures. Bootstrap
resamples tasks, preserving pairing.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import plot_frontier as frontier

ARMS = ("placebo", "P5", "P20")
INFRA_ERRORS = {"api_error", "grade_error"}


def read_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(row, key):
    return float(row.get(key) or 0)


@dataclass(frozen=True)
class Coverage:
    rows: list[dict]                  # rows in at least one complete effort block
    efforts: list[str]
    observed_blocks: int
    complete_by_effort: dict[str, int]
    frontier_efforts: list[str]       # observed efforts with at least one complete block
    common_rows: list[dict]           # blocks complete at every frontier effort
    common_blocks: int


def complete_blocks(rows):
    """Per-effort complete blocks, plus the all-effort set used by the frontier.

    A block is complete for one effort when all three arms for the same
    task/run/effort are present and none is an infrastructure error. A missing
    cell in an exploratory effort therefore does not discard the same task/run
    from the primary high-effort comparison. The frontier still needs the same
    tasks at every effort, so it uses `common_rows`: blocks complete at every
    frontier effort (an observed effort with at least one complete block).
    """
    efforts = frontier.order_efforts({r["effort"] for r in rows})
    grouped = defaultdict(dict)
    for r in rows:
        key = (r["task"], r.get("har_mode", "raw"), r["run"])
        cell = (r["arm"], r["effort"])
        if cell in grouped[key]:
            raise ValueError(f"duplicate cell: {key}, {cell}")
        grouped[key][cell] = r
    complete_by_effort = {effort: 0 for effort in efforts}
    block_efforts = []
    for block in grouped.values():
        complete_efforts = [
            effort for effort in efforts
            if all((arm, effort) in block for arm in ARMS)
            and not any(block[(arm, effort)].get("status") in INFRA_ERRORS for arm in ARMS)
        ]
        block_efforts.append((block, complete_efforts))
        for effort in complete_efforts:
            complete_by_effort[effort] += 1
    frontier_efforts = [effort for effort in efforts if complete_by_effort[effort]]
    kept, common = [], []
    common_blocks = 0
    for block, complete_efforts in block_efforts:
        for effort in complete_efforts:
            kept.extend([block[(arm, effort)] for arm in ARMS])
        if frontier_efforts and all(effort in complete_efforts for effort in frontier_efforts):
            common_blocks += 1
            common.extend([block[(arm, effort)] for effort in frontier_efforts for arm in ARMS])
    return Coverage(rows=kept, efforts=efforts, observed_blocks=len(grouped),
                    complete_by_effort=complete_by_effort, frontier_efforts=frontier_efforts,
                    common_rows=common, common_blocks=common_blocks)


def interval(values, rng, resamples):
    if not values:
        return None
    samples = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(resamples))
    return [samples[int((resamples - 1) * .025)], samples[int((resamples - 1) * .975)]]


def compare(rows, efforts, resamples=5000, seed=20261026):
    rng, comparisons = random.Random(seed), []
    for effort in efforts:
        for header in ("all", "on", "off"):
            chosen = [r for r in rows if r["effort"] == effort and (header == "all" or r["hidden_header"] == header)]
            per_task = defaultdict(lambda: defaultdict(list))
            for r in chosen:
                per_task[r["task"]][r["arm"]].append(number(r, "pass_rate"))
            for arm in ARMS[1:]:
                diffs = [statistics.mean(v[arm]) - statistics.mean(v["placebo"]) for v in per_task.values()]
                if diffs:
                    comparisons.append(dict(effort=effort, hidden_header=header, arm=arm,
                                            tasks=len(diffs), delta_pp=100 * statistics.mean(diffs),
                                            ci95_pp=[100 * x for x in interval(diffs, rng, resamples)]))
    return comparisons


def analyze(input_csv, out, *, dry_run=False, resamples=5000):
    rows = read_rows(input_csv)
    if not rows:
        raise ValueError("no results yet")
    if len({r.get("har_mode", "raw") for r in rows}) > 1:
        raise ValueError("analyze one HAR mode at a time")
    manifest_path = Path(input_csv).parent / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if manifest.get("dry_run") and not dry_run:
        raise ValueError("synthetic dry-run data: pass --dry-run to label every output")
    coverage = complete_blocks(rows)
    kept, efforts = coverage.rows, coverage.efforts
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    comparisons = compare(kept, efforts, resamples)
    report = dict(dry_run=dry_run, source=str(input_csv), observed_rows=len(rows),
                  statuses=dict(Counter(r.get("status", "unknown") for r in rows)),
                  observed_blocks=coverage.observed_blocks,
                  complete_blocks_by_effort=coverage.complete_by_effort,
                  frontier_efforts=coverage.frontier_efforts,
                  complete_blocks_all_efforts=coverage.common_blocks,
                  complete_blocks=coverage.common_blocks, paired_rows=len(kept),
                  efforts=efforts, comparisons=comparisons,
                  method="Complete task/run/effort blocks (all three arms); task-mean differences; "
                         "percentile bootstrap over tasks. The frontier uses blocks complete at every plotted effort.")
    metrics = ("pass_rate", "strict_pass_rate", "probes_used", "completion_tokens", "prompt_tokens", "wall_time_s")
    report["scores"] = []
    for effort in efforts:
        for arm in ARMS:
            by_task = defaultdict(list)
            for row in kept:
                if row["effort"] == effort and row["arm"] == arm:
                    by_task[row["task"]].append(row)
            if by_task:
                score = {metric: statistics.mean(statistics.mean(number(r, metric) for r in group)
                                                  for group in by_task.values()) for metric in metrics}
                report["scores"].append(dict(arm=arm, effort=effort, tasks=len(by_task), **score))
    report["total_tokens"] = {k: sum(number(r, k) for r in rows) for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens")}
    (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    title = "SYNTHETIC DRY RUN — not Beam results" if dry_run else "Case 2: Beam probing evaluation"
    blocks_by_effort = ", ".join(
        f"{effort} {coverage.complete_by_effort[effort]}/{coverage.observed_blocks}" for effort in efforts
    )
    omitted = [effort for effort in efforts if effort not in coverage.frontier_efforts]
    lines = [f"# {title}", "",
             f"Observed rows: {len(rows)}. Observed task/run blocks: {coverage.observed_blocks}.",
             f"Complete blocks by effort: {blocks_by_effort or 'none'}.",
             f"Blocks complete at every plotted effort ({', '.join(coverage.frontier_efforts) or 'none'}): "
             f"{coverage.common_blocks}/{coverage.observed_blocks} (frontier).",
             (f"Efforts with no complete block (omitted from the frontier): {', '.join(omitted)}."
              if omitted else "All observed efforts have at least one complete block."),
             f"Statuses: {report['statuses']}", "",
             "Paired comparisons use all three arms for the same task/run/effort; a missing cell removes only that effort's block.",
             "The frontier plot uses only blocks complete at every plotted effort.",
             "No-code/no-submit model outcomes count as zero. Intervals resample tasks, not individual tests.",
             "Completion tokens already include reasoning; do not add reasoning tokens again.", "",
             "| Effort | Arm | Tasks | Pass (%) | Strict (%) | Probes | Output tokens | Time (s) |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for s in report["scores"]:
        lines.append(f"| {s['effort']} | {s['arm']} | {s['tasks']} | {100*s['pass_rate']:.2f} | {100*s['strict_pass_rate']:.2f} | {s['probes_used']:.2f} | {s['completion_tokens']:.0f} | {s['wall_time_s']:.2f} |")
    lines += ["",
             "| Effort | Hidden header | Comparison | Tasks | Gain (pp) | 95% CI (pp) |",
             "|---|---|---|---:|---:|---|"]
    for c in comparisons:
        lo, hi = c["ci95_pp"]
        lines.append(f"| {c['effort']} | {c['hidden_header']} | {c['arm']} − placebo | {c['tasks']} | {c['delta_pp']:.2f} | [{lo:.2f}, {hi:.2f}] |")
    if coverage.common_rows:
        series = []
        for arm in ARMS:
            path = out / f"{arm}.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(r for r in coverage.common_rows if r["arm"] == arm)
            series.append((arm, path))
        for x_mode in ("output", "total"):
            points, meta = frontier.build_report(series, x_mode, resamples, 20261026)
            prefix = out / f"frontier_{x_mode}"
            frontier.write_json(prefix, points, meta)
            frontier.plot(points, meta, title, prefix)
        lines += ["", "Plots: `frontier_output.png` and `frontier_total.png` (also SVG/JSON).",
                  "Both plots use the task/run blocks complete at every plotted effort."]
    else:
        lines += ["", "No task/run blocks are complete at every plotted effort; no comparison plot generated."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resamples", type=int, default=5000)
    args = parser.parse_args()
    if args.resamples < 1:
        parser.error("--resamples must be positive")
    try:
        report = analyze(args.input, args.out, dry_run=args.dry_run, resamples=args.resamples)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Paired blocks by effort: {report['complete_blocks_by_effort']}; "
          f"common for frontier: {report['complete_blocks_all_efforts']}/{report['observed_blocks']}; "
          f"report: {args.out / 'REPORT.md'}")


if __name__ == "__main__":
    main()
