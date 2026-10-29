#!/usr/bin/env python3
"""Build the Tier B task set (DESIGN-TIERB.md, "Task set").

Usage:
  uv run python scripts/build_tierb.py --out runs/tierb50 --seed 1

Writes `data/tierb_configs.jsonl` (the seeded, balanced list of 50 configs), then builds one task per
config into `--out`. Each build runs the reference client (must score 1.0) and, unless `--no-negatives`,
the broken clients of the validity gate (each must fail with its reason).

The config list is committed, so the script never overwrites it silently. If the file (the default, or the path of
`--configs`) exists and the new list differs, nothing is built and the script exits with code 2; pass `--force` to
replace the file. A new list that is identical to the file leaves the file untouched.

Exit code: 0 all built, 1 some failed, 2 the config list exists and differs (no `--force`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client.tierb import config as cfg
from har2client.tierb.build import DEFAULT_NOISE, build_tierb_task

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGS = ROOT / "data" / "tierb_configs.jsonl"
DEFAULT_JOBS = 4
EXIT_CONFIGS_DIFFER = 2


class ConfigsDifferError(Exception):
    """The config list on disk differs from the new one and `--force` is not set."""


def task_id_of(row: dict) -> str:
    return f"tierb_{row['index']:02d}_{row['domain']}"


def write_configs(rows: list[dict], path: Path, seed: int, *, force: bool = False) -> None:
    """Write the config list. An existing file with the same content stays as it is; one with different content
    raises ConfigsDifferError unless `force`."""
    lines = [json.dumps({"task_id": task_id_of(r), **r, "config_seed": seed}, sort_keys=False) for r in rows]
    text = "\n".join(lines) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == text:
            return
        if not force:
            raise ConfigsDifferError(
                f"{path} exists and differs from the config list for seed {seed} ({len(rows)} configs); "
                "nothing was written or built. Pass --force to overwrite it, or --configs <other path>."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_configs(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{k: r[k] for k in ("index", "domain", "config", "seed")} for r in rows]


def distribution(rows: list[dict]) -> dict[str, dict[str, int]]:
    out = {name: dict(Counter(r["config"][name] for r in rows)) for name in cfg.FACTORS}
    out["domain"] = dict(Counter(r["domain"] for r in rows))
    return out


def har_sizes(task_dirs: list[Path]) -> dict:
    entries = [json.loads((d / "task.json").read_text())["har_entries"] for d in task_dirs]
    kb = [(d / "session.har").stat().st_size / 1024 for d in task_dirs]
    return {"entries": [min(entries), max(entries)], "kb": [round(min(kb), 1), round(max(kb), 1)]}


def _build(args: tuple) -> tuple[str, Path | None, str | None]:
    row, out, seed, noise, negatives = args
    try:
        return task_id_of(row), build_tierb_task(row, out, noise=noise, build_seed=seed, negatives=negatives, task_id=task_id_of(row)), None
    except Exception as exc:  # noqa: BLE001 - report every failed task, keep building the rest
        return task_id_of(row), None, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, type=Path, help="directory that receives the task dirs")
    p.add_argument("--seed", type=int, default=1, help="seed for the config list and the task builds")
    p.add_argument("--configs", type=Path, default=DEFAULT_CONFIGS, help="where the config list is written; an existing file that differs is kept unless --force")
    p.add_argument("--force", action="store_true", help="overwrite the config list when its content differs")
    p.add_argument("--count", type=int, default=cfg.DEFAULT_COUNT, help="number of configs (default 50)")
    p.add_argument("--limit", type=int, help="build only the first N configs (debugging)")
    p.add_argument("--noise", type=float, default=DEFAULT_NOISE)
    p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    p.add_argument("--no-negatives", action="store_true", help="skip the broken-client checks (the reference check always runs)")
    args = p.parse_args(argv)

    rows = cfg.sample_configs(args.seed, args.count)
    try:
        write_configs(rows, args.configs, args.seed, force=args.force)
    except ConfigsDifferError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGS_DIFFER
    todo = rows[: args.limit] if args.limit else rows
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = [(r, args.out, args.seed, args.noise, not args.no_negatives) for r in todo]
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(_build, jobs))
    failed = [(tid, err) for tid, _, err in results if err]
    built = [d for _, d, _ in results if d]
    for tid, err in failed:
        print(f"FAILED {tid}: {err}", file=sys.stderr)
    print(f"built {len(built)}/{len(todo)} tasks into {args.out}; configs in {args.configs}")
    print("config distribution:", json.dumps(distribution(todo), sort_keys=True))
    if built:
        print("har size:", json.dumps(har_sizes(built)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
