#!/usr/bin/env python3
"""Build the case 3 v3 hard repair pilot (DESIGN-CASE3-HARD.md, "Pilot").

Usage:
  uv run python scripts/build_heal_v3.py --out runs/heal_v3_pilot --seed 1      # 38 tasks, manifest data/heal_v3_pilot.jsonl
  uv run python scripts/build_heal_v3.py --suite eval --out runs/heal_v3_eval --seed 2   # 64 tasks, seeds 2001+, data/heal_v3_eval.jsonl

Writes the manifest (`--manifest`), builds each task into `--out` and runs its gates (`heal.hard.verify_hard_task`): client_v1 is 1.0 on
v1 and < 1.0 on v2, the v2 reference client is 1.0, each change is needed, the fix is big enough, the hidden tests are
many enough, no hidden data is visible, the recording shows each change. A task that fails a gate is reported on stderr and not
kept. Prints a table of the built tasks. Exit code: 0 all built, 1 some failed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client.heal import release
from har2client.heal.build import HEAL_NOISE
from har2client.heal.hard import build_hard_task

ROOT = Path(__file__).resolve().parents[1]
SUITES = {  # suite -> (families, first spec seed, manifest)
    "pilot": (release.FAMILIES, release.FIRST_SEED, ROOT / "data" / "heal_v3_pilot.jsonl"),
    "eval": (release.EVAL_FAMILIES, release.EVAL_FIRST_SEED, ROOT / "data" / "heal_v3_eval.jsonl"),
}
DEFAULT_JOBS = 4


def _build(args: tuple) -> tuple[dict, Path | None, str | None]:
    entry, out, noise = args
    try:
        return entry, build_hard_task(entry, out, build_seed=entry["build_seed"], noise=noise), None
    except Exception as exc:  # noqa: BLE001 - report every failed task, keep building the rest
        shutil.rmtree(Path(out) / entry["task_id"], ignore_errors=True)  # a task that failed a gate is not kept
        return entry, None, f"{type(exc).__name__}: {exc}"


def table_row(entry: dict, task_dir: Path) -> str:
    task = json.loads((task_dir / "task.json").read_text())
    tests = json.loads((task_dir / "tests.json").read_text())
    heal, gate = task["heal"], task["heal"]["gate"]
    kb = (task_dir / "session.har").stat().st_size / 1024 if (task_dir / "session.har").is_file() else 0.0
    return (
        f"{entry['set']:<10} {entry['index']:2d} {'+'.join(release.short_name(n) for n in entry['mutations']):<40} {entry['domain']:<9} "
        f"{task['config']['auth']:<13} k={heal['changes']} {heal['client_style']:<7} {heal['evidence_scope']:<7} {heal['changelog']:<10} {heal['error_detail']:<7} "
        f"fix_lines={heal['fix_lines']:3d} fix_classes={heal['fix_classes']} n_tests={len(tests)} har_kb={kb:5.1f} "
        f"v1/v1={gate['v1_on_v1']:.2f} v1/v2={gate['v1_on_v2']:.2f} ref/v2={gate['ref_on_v2']:.2f} "
        f"min_without={min(heal['necessity'].values()):.2f} identifiable={heal['identifiable']}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, type=Path, help="directory that receives the task dirs")
    p.add_argument("--seed", type=int, default=1, help="seed for the release selection and the task builds")
    p.add_argument("--suite", choices=list(SUITES), default="pilot", help="pilot (38 tasks, dev seeds 1001+) or eval (64 tasks, seeds 2001+)")
    p.add_argument("--first-seed", type=int, help="spec seed of the first task (default: the suite's)")
    p.add_argument("--manifest", type=Path, help="where the manifest is written (default: the suite's)")
    p.add_argument("--families", nargs="+", choices=[f[0] for f in release.FAMILIES], help="build only these families")
    p.add_argument("--limit", type=int, help="build only the first N tasks (debugging)")
    p.add_argument("--noise", type=float, default=HEAL_NOISE)
    p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    args = p.parse_args(argv)

    families, first_seed, manifest = SUITES[args.suite]
    args.manifest = args.manifest or manifest
    entries = release.select_pilot(args.seed, args.first_seed or first_seed, families)
    release.write_manifest(entries, args.manifest)
    todo = [e for e in entries if not args.families or e["set"] in args.families]
    todo = todo[: args.limit] if args.limit else todo
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(_build, [(e, args.out, args.noise) for e in todo]))
    failed = [(e["task_id"], err) for e, _, err in results if err]
    for tid, err in failed:
        print(f"FAILED {tid}: {err}", file=sys.stderr)
    ok = [(e, d) for e, d, _ in results if d]
    print(f"built {len(ok)}/{len(todo)} {args.suite} tasks into {args.out}; manifest in {args.manifest}")
    for e, task_dir in ok:
        print(table_row(e, task_dir))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
