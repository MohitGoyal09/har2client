#!/usr/bin/env python3
"""Write a `datasets`-compatible JSONL of RL prompts (DESIGN-RL.md, "Integrations").

Usage:
  uv run python scripts/make_rl_dataset.py --split train --n 2000 --out data/rl/train_2000.jsonl

Each line: {"prompt": [system and user chat messages], "task_spec": {hidden build params}}.
The prompt holds the goal and the observation only; `task_spec` is what `har2client.env.trl.reward_fn` needs to
rebuild (from the cache) the task and grade a completion. Tasks are built with the Tier B builder, whose reference
client must score 1.0, so every row is solvable. Load it with `datasets.load_dataset("json", data_files=...)`.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client.env import splits, tasks
from har2client.solve.prompt import build_system_prompt, build_user_prompt

CHARS_PER_TOKEN = 4
DEFAULT_JOBS = 4


def make_row(spec: dict, cache_dir: Path | None) -> dict:
    task_dir = tasks.ensure_task(spec, cache_dir)
    seen = tasks.read_visible(task_dir)
    user = build_user_prompt(seen["goal"], seen["observation"], seen["observation_file"])
    return {
        "prompt": [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": user}],
        "task_spec": tasks.normalize(spec),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--split", choices=splits.SPLIT_NAMES, default="train")
    p.add_argument("--n", type=int, default=2000)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--level", type=int, help="only this level (default: all levels of the split in turn)")
    p.add_argument("--source", help="only this source (default: the sources of the split)")
    p.add_argument("--compact", action=argparse.BooleanOptionalAction, default=True,
                   help="no noise, JSON lists cut to 3 elements, minified JSON: prompts of about 4-8K tokens (default on)")
    p.add_argument("--cache-dir", type=Path)
    p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    args = p.parse_args(argv)

    specs = splits.sample_specs(args.split, args.n, level=args.level, source=args.source, seed=args.seed)
    specs = [{**s, "compact": args.compact} for s in specs]
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        rows = list(pool.map(lambda s: make_row(s, args.cache_dir), specs))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    sizes = sorted(sum(len(m["content"]) for m in r["prompt"]) // CHARS_PER_TOKEN for r in rows)
    print(f"wrote {len(rows)} rows to {args.out}; prompt tokens (chars/4) median {sizes[len(sizes) // 2]}, max {sizes[-1]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
