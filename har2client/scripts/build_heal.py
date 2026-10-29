#!/usr/bin/env python3
"""Build the case 3 task set (DESIGN-CASES.md, "Case 3: Self-healing client").

Usage:
  uv run python scripts/build_heal.py --out runs/heal48 --seed 1
  uv run python scripts/build_heal.py --suite --out runs/heal_suite --seed 1     # case 3 v2: 144 tasks (DESIGN-CASE3.md)

Writes `data/heal_configs.jsonl` (8 mutations x 6 base configs, where the mutation applies), builds one repair task per
row into `--out` and, unless `--no-scratch`, the matching case 1 task of the same v2 API into `--out`_scratch (or
`--scratch-out`). Every repair task is verified: client_v1.py scores 1.0 on v1 and < 1.0 on v2, the v2 reference client
scores 1.0, and no hidden data shows in the visible files. With `--suite` the 4 sets of `heal.suite` (core, terse, evidence,
compound; `--sets` picks some) are built into `--out` and the manifest is written to `--manifest` (data/heal_suite.jsonl);
there are no from-scratch tasks then. Exit code: 0 all built, 1 some failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client.heal import select, suite
from har2client.heal.build import HEAL_NOISE, build_heal_task, build_scratch_task

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGS = ROOT / "data" / "heal_configs.jsonl"
DEFAULT_MANIFEST = ROOT / "data" / "heal_suite.jsonl"
DEFAULT_JOBS = 4
SWITCHES = ("auth", "pagination", "token_expiry", "etag_update", "hidden_header", "envelope")


def short_config(config: dict) -> str:
    return "/".join(config[k] for k in SWITCHES)


def _build(args: tuple) -> tuple[dict, Path | None, str | None]:
    kind, row, out, seed, noise = args
    try:
        if kind == "heal":
            return row, build_heal_task(row, out, build_seed=seed, noise=noise), None
        if kind == "suite":
            return row, build_heal_task(
                suite.task_row(row), out, build_seed=row["build_seed"], noise=noise, error_detail=row["error_detail"],
                evidence=row["evidence"], task_id=row["task_id"],
            ), None
        return row, build_scratch_task(row, out, build_seed=seed, noise=noise), None
    except Exception as exc:  # noqa: BLE001 - report every failed task, keep building the rest
        return row, None, f"{type(exc).__name__}: {exc}"


def summary_line(row: dict, task_dir: Path) -> str:
    task = json.loads((task_dir / "task.json").read_text())
    har = task_dir / "session.har"
    kb = har.stat().st_size / 1024 if har.is_file() else 0.0
    gate, failure = task["heal"]["gate"], task["heal"]["failure"]
    label = "+".join(row["mutations"]) if row.get("mutations") else row["mutation"]
    return (
        f"{row['index']:2d} {label:<23} {row['domain']:<9} base={short_config(row['base_config'])}  "
        f"api_calls={task['session_calls']:2d} har_entries={task['har_entries']:2d} har_kb={kb:5.1f}  "
        f"v1_on_v1={gate['v1_on_v1']:.2f} v1_on_v2={gate['v1_on_v2']:.2f} ref_on_v2={gate['ref_on_v2']:.2f}  first_failure={failure['reason']}"
    )


def _main_suite(args: argparse.Namespace, rows: list[dict]) -> int:
    entries = suite.build_manifest(rows, args.seed)
    suite.write_manifest(entries, args.manifest)
    todo = [e for e in entries if not args.sets or e["set"] in args.sets]
    todo = todo[: args.limit] if args.limit else todo
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(_build, [("suite", e, args.out, args.seed, args.noise) for e in todo]))
    failed = [(e["task_id"], err) for e, (_, _, err) in zip(todo, results) if err]
    for tid, err in failed:
        print(f"FAILED {tid}: {err}", file=sys.stderr)
    ok = [(e, d) for e, (_, d, _) in zip(todo, results) if d]
    print(f"built {len(ok)}/{len(todo)} suite tasks into {args.out}; manifest in {args.manifest}")
    for e, task_dir in ok:
        print(f"{e['set']:<8} {e['evidence']:<14} {e['error_detail']:<7} identifiable={'yes' if e['identifiable'] else 'no ':<3} {summary_line(e, task_dir)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, type=Path, help="directory that receives the repair task dirs")
    p.add_argument("--scratch-out", type=Path, help="directory for the from-scratch tasks (default: <out>_scratch)")
    p.add_argument("--seed", type=int, default=1, help="seed for the row selection and the task builds")
    p.add_argument("--configs", type=Path, default=DEFAULT_CONFIGS, help="where the row list is written")
    p.add_argument("--per-mutation", type=int, default=select.PER_MUTATION)
    p.add_argument("--limit", type=int, help="build only the first N rows (debugging)")
    p.add_argument("--noise", type=float, default=HEAL_NOISE)
    p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    p.add_argument("--no-scratch", action="store_true", help="skip the from-scratch comparison tasks")
    p.add_argument("--suite", action="store_true", help="build the 144-task case 3 v2 suite (core, terse, evidence, compound)")
    p.add_argument("--sets", nargs="+", choices=suite.SETS, help="with --suite: build only these sets")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="with --suite: where the manifest is written")
    args = p.parse_args(argv)

    rows = select.select_rows(args.seed, args.per_mutation)
    select.write_rows(rows, args.configs, args.seed)
    if args.suite:
        return _main_suite(args, rows)
    todo = rows[: args.limit] if args.limit else rows
    scratch_out = args.scratch_out or args.out.with_name(args.out.name + "_scratch")
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = [("heal", r, args.out, args.seed, args.noise) for r in todo]
    if not args.no_scratch:
        scratch_out.mkdir(parents=True, exist_ok=True)
        jobs += [("scratch", r, scratch_out, args.seed, args.noise) for r in todo]
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(_build, jobs))
    failed = [(select.task_id_of(r) if j[0] == "heal" else select.scratch_id_of(r), err) for j, (r, _, err) in zip(jobs, results) if err]
    for tid, err in failed:
        print(f"FAILED {tid}: {err}", file=sys.stderr)
    heal_ok = [(r, d) for j, (r, d, _) in zip(jobs, results) if j[0] == "heal" and d]
    scratch_ok = [d for j, (_, d, _) in zip(jobs, results) if j[0] == "scratch" and d]
    print(f"built {len(heal_ok)}/{len(todo)} repair tasks into {args.out}; configs in {args.configs}")
    if not args.no_scratch:
        print(f"built {len(scratch_ok)}/{len(todo)} from-scratch tasks into {scratch_out}")
    for row, task_dir in heal_ok:
        print(summary_line(row, task_dir))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
