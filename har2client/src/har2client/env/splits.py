"""The three splits and the task sampler (DESIGN-RL.md, "Splits").

- `train`: 4 domains, every switch combination except the held-out ones, sources `browser_har` and `mobile_har`.
- `test_combo`: only the held-out switch combinations (compositional generalisation).
- `test_domain`: the held-out 5th domain, with train-style combinations.
(`test_source` waits for more sources; see DESIGN-RL.md.)

Membership, the base seed and the fixed evaluation tasks of the two test splits are committed in
`data/rl_splits.json`. Run `python -m har2client.env.splits` to write that file again.
"""

from __future__ import annotations

import functools
import json
import random
import sys
from pathlib import Path

from har2client.env import tasks
from har2client.env.curriculum import LEVELS, level_of
from har2client.tierb import config as cfg
from har2client.tierb.domains import DOMAINS

SPLITS_PATH = Path(__file__).resolve().parents[3] / "data" / "rl_splits.json"
SPLIT_NAMES = ("train", "test_combo", "test_domain")
SEED_SPACE = 10**9
BASE_SEED = 20261024
EVAL_TASKS = 60  # fixed tasks per test split
HELD_OUT_DOMAIN = "samples"
# Switch values that never meet in train: each entry is a partial config (all its values must match).
HELD_OUT_COMBOS = (
    {"auth": "cookie_csrf", "pagination": "link_header", "etag_update": "on"},
    {"auth": "bearer_login", "pagination": "cursor", "envelope": "on"},
)
TRAIN_SOURCES = ("browser_har", "mobile_har")


def default_splits() -> dict:
    """Membership and fixed evaluation tasks, computed from the constants above."""
    domains = list(DOMAINS)
    membership = {
        "seed": BASE_SEED,
        "held_out_combos": [dict(c) for c in HELD_OUT_COMBOS],
        "domains": {"train": [d for d in domains if d != HELD_OUT_DOMAIN], "test_combo": [d for d in domains if d != HELD_OUT_DOMAIN],
                    "test_domain": [HELD_OUT_DOMAIN]},
        "sources": {name: list(TRAIN_SOURCES) for name in SPLIT_NAMES},
    }
    out = {**membership, "eval": {}}
    for name in ("test_combo", "test_domain"):
        out["eval"][name] = _sample(membership, name, EVAL_TASKS, level=None, source=None, seed=BASE_SEED, avoid=set())
    return out


@functools.lru_cache(maxsize=1)
def load_splits(path: str | None = None) -> dict:
    return json.loads(Path(path or SPLITS_PATH).read_text(encoding="utf-8"))


def is_held_out(config: dict[str, str], held_out: list[dict] | tuple = HELD_OUT_COMBOS) -> bool:
    return any(all(config[k] == v for k, v in combo.items()) for combo in held_out)


def configs_for(split: str, level: int, splits: dict | None = None) -> list[dict[str, str]]:
    """Valid switch combinations of a split at a level."""
    held = (splits or load_splits())["held_out_combos"]
    want_held = split == "test_combo"
    return [c for c in cfg.all_configs() if level_of(c) == level and is_held_out(c, held) == want_held]


def available_levels(split: str, splits: dict | None = None) -> tuple[int, ...]:
    return tuple(level.index for level in LEVELS if configs_for(split, level.index, splits))


def _seeds_of(splits: dict) -> set[int]:
    return {spec["seed"] for specs in splits.get("eval", {}).values() for spec in specs}


def sample_spec(
    split: str, rng: random.Random, *, level: int | None = None, source: str | None = None, splits: dict | None = None,
    avoid: set[int] | None = None,
) -> dict:
    """One task spec of a split. `level=None` draws a level among those the split has; `source=None` draws one of the
    split's sources. The task seed is never one of the fixed evaluation seeds."""
    splits = splits or load_splits()
    if split not in SPLIT_NAMES:
        raise ValueError(f"unknown split {split!r}; known: {SPLIT_NAMES}")
    levels = available_levels(split, splits)
    if level is None:
        level = rng.choice(levels)
    elif level not in levels:
        raise ValueError(f"split {split!r} has no tasks at level {level}; levels: {levels}")
    config = rng.choice(configs_for(split, level, splits))
    domain = rng.choice(splits["domains"][split])
    chosen = source or rng.choice(splits["sources"][split])
    blocked = _seeds_of(splits) if avoid is None else avoid | _seeds_of(splits)
    seed = rng.randrange(SEED_SPACE)
    while seed in blocked:
        seed = rng.randrange(SEED_SPACE)
    return tasks.make_spec(domain, config, seed, chosen)


def _sample(splits: dict, split: str, n: int, *, level: int | None, source: str | None, seed: int, avoid: set[int]) -> list[dict]:
    rng = random.Random(f"rl-splits:{split}:{seed}")
    levels = available_levels(split, splits)
    out: list[dict] = []
    for i in range(n):
        lvl = level if level is not None else levels[i % len(levels)]  # equal share per level
        spec = sample_spec(split, rng, level=lvl, source=source, splits=splits, avoid=avoid | {s["seed"] for s in out})
        out.append(spec)
    return out


def sample_specs(split: str, n: int, *, level: int | None = None, source: str | None = None, seed: int = 0) -> list[dict]:
    """`n` distinct task specs of a split, deterministic for the arguments. Levels are used in turn
    when `level` is None. For a test split with the default seed and no filters, the first `n` fixed
    evaluation tasks are returned."""
    splits = load_splits()
    if split != "train" and level is None and source is None and seed == 0 and n <= len(splits["eval"].get(split, [])):
        return [dict(s) for s in splits["eval"][split][:n]]
    return _sample(splits, split, n, level=level, source=source, seed=seed, avoid=set())


def write_splits(path: Path = SPLITS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_splits(), indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_splits()
    print(f"wrote {SPLITS_PATH}", file=sys.stderr)
