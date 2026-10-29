#!/usr/bin/env python3
"""Run episodes of the RL environment with the reference client and with an empty client; print reward stats.

Usage:
  uv run python scripts/rl_demo.py [--episodes 20] [--split train] [--cache-dir DIR]

The reference client should get 1.0 on every task and the empty client 0.0; this is the quick health check of the
environment. Episodes use the sources of the split and all its levels (curriculum "uniform").
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from pathlib import Path

from har2client.env.env import TrafficToClientEnv
from har2client.env.reward import GradePool, RewardConfig

EMPTY_CLIENT = "class Client:\n    def __init__(self, base_url, username, password):\n        pass\n"


def run(name: str, episodes: int, split: str, cache_dir: Path | None, pool: GradePool, milestones: bool) -> list[dict]:
    env = TrafficToClientEnv(source="auto", split=split, curriculum="uniform", seed=7, cache_dir=cache_dir, pool=pool,
                             reward_config=RewardConfig(milestones=milestones))
    rows = []
    for _ in range(episodes):
        env.reset()
        code = env.reference_code() if name == "reference" else EMPTY_CLIENT
        out = env.step({"type": "submit", "code": code})
        rows.append({"reward": out["reward"], **{k: out["info"][k] for k in ("source", "level", "grade_time_s")}})
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--episodes", type=int, default=20)
    p.add_argument("--split", default="train")
    p.add_argument("--cache-dir", type=Path)
    p.add_argument("--milestones", action="store_true")
    args = p.parse_args(argv)
    pool = GradePool()
    try:
        for name in ("reference", "empty"):
            rows = run(name, args.episodes, args.split, args.cache_dir, pool, args.milestones)
            rewards = [r["reward"] for r in rows]
            print(f"{name:9} client: {len(rows)} episodes  reward mean {statistics.mean(rewards):.3f}  min {min(rewards):.3f}  max {max(rewards):.3f}")
            print(f"          sources {dict(Counter(r['source'] for r in rows))}  levels {dict(sorted(Counter(r['level'] for r in rows).items()))}")
        print(f"median grade time: {pool.median_grade_time():.2f} s over {len(pool.durations)} grades")
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
