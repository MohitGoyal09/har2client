#!/usr/bin/env python3
"""Write a `datasets`-compatible JSONL of repair prompts (DESIGN-CASE3.md, "RL environment").

Usage:
  uv run python scripts/make_repair_dataset.py --split train --n 2000 --out data/rl/repair_train_2000.jsonl

Each line: {"prompt": [system and user chat messages], "task_spec": {hidden build params}}.
The prompt is the repair prompt of the solver (goal, `client_v1.py`, and `failure.txt` / the recording when the task shows
them) and nothing hidden; `task_spec` is what `har2client.env.repair_env.repair_reward_fn` needs to rebuild the task from
the cache and grade a completion. Each task is built with `heal.build.build_heal_task`, whose gates require the v2
reference client to score 1.0. Load with `datasets.load_dataset("json", data_files=...)`; set `remove_unused_columns=False`.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client.env import repair_splits, repair_tasks
from har2client.solve.prompt import build_system_prompt, build_user_prompt

CHARS_PER_TOKEN = 4
DEFAULT_JOBS = 4


def make_row(spec: dict, cache_dir: Path | None) -> dict:
    task_dir = repair_tasks.ensure_task(spec, cache_dir)
    seen = repair_tasks.read_visible(task_dir)
    extra = {"client_v1.py": seen["client_v1"], **({"failure.txt": seen["failure"]} if seen["failure"] is not None else {})}
    user = build_user_prompt(seen["goal"], seen["observation"], "session.har", extra)
    return {
        "prompt": [{"role": "system", "content": build_system_prompt(has_recording=seen["observation"] is not None)}, {"role": "user", "content": user}],
        "task_spec": repair_tasks.normalize(spec),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--split", choices=repair_splits.SPLIT_NAMES, default="train")
    p.add_argument("--n", type=int, default=2000)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--level", type=int, help="only this level R<k> (default: all levels of the split in turn)")
    p.add_argument("--cache-dir", type=Path)
    p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    args = p.parse_args(argv)

    specs = repair_splits.sample_specs(args.split, args.n, level=args.level, seed=args.seed)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        rows = list(pool.map(lambda s: make_row(s, args.cache_dir), specs))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    sizes = sorted(sum(len(m["content"]) for m in r["prompt"]) // CHARS_PER_TOKEN for r in rows)
    print(f"wrote {len(rows)} rows to {args.out}; prompt tokens (chars/4) median {sizes[len(sizes) // 2]}, max {sizes[-1]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
