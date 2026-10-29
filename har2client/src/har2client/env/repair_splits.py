"""Splits, curriculum levels and the task sampler of the repair environment (DESIGN-CASE3.md, "RL environment").

- `train`: 6 mutations (all but the two held out), levels R0-R3; R2/R3 use pairs of train mutations that are not held out.
- `test_mutation`: only the held-out mutations `rename_hidden_header` and `none_to_link_header` (one change), levels R0-R1.
- `test_pair`: only held-out pairs of train mutations (each part also occurs in train, alone and in other pairs), levels R2-R3.

`train` never draws a task that is not identifiable (`heal/identify.py`: `failure_only` evidence on a mutation that needs a fact only
the recording shows): its R1/R3 factor choices drop those combinations. The test splits keep them; every spec carries an
`identifiable` tag, so they can be reported apart as a "guessing" probe.

Levels: R0 1 change, verbose, both channels; R1 1 change, terse errors or one channel; R2 2 changes, verbose, both;
R3 2 changes, terse errors, one channel. Membership, the base seed and 24 fixed evaluation tasks per test split are
committed in `data/heal_splits.json`; `python -m har2client.env.repair_splits` writes the file again, a test checks it.
The core 48 tasks (`data/heal_configs.jsonl`) are the Beam evaluation set; their seeds are never used here.
"""

from __future__ import annotations

import functools
import itertools
import json
import random
import sys
from pathlib import Path

from har2client.env import repair_tasks as rt
from har2client.heal.identify import identifiable
from har2client.heal.mutations import MUTATIONS
from har2client.tierb import config as cfg
from har2client.tierb.domains import DOMAINS

ROOT = Path(__file__).resolve().parents[3]
SPLITS_PATH = ROOT / "data" / "heal_splits.json"
CORE_CONFIGS_PATH = ROOT / "data" / "heal_configs.jsonl"
SPLIT_NAMES = ("train", "test_mutation", "test_pair")
SEED_SPACE = 10**9
BASE_SEED = 20261024
EVAL_TASKS = 24  # fixed tasks per test split
HELD_OUT_MUTATIONS = ("rename_hidden_header", "none_to_link_header")
HELD_OUT_PAIRS = (
    ("add_hidden_header", "add_etag"), ("offset_to_cursor", "add_token_expiry"),
    ("add_envelope", "apikey_to_bearer_login"), ("add_etag", "add_token_expiry"),
)
TRAIN_MUTATIONS = tuple(m.name for m in MUTATIONS if m.name not in HELD_OUT_MUTATIONS)
SPLIT_LEVELS = {"train": (0, 1, 2, 3), "test_mutation": (0, 1), "test_pair": (2, 3)}
# (error_detail, evidence) choices per level; R1 and R3 draw one at random
LEVEL_FACTORS = {
    0: (("verbose", "both"),),
    1: (("terse", "both"), ("verbose", "failure_only"), ("verbose", "recording_only")),
    2: (("verbose", "both"),),
    3: (("terse", "failure_only"), ("terse", "recording_only")),
}


def _pair_key(pair: tuple[str, ...]) -> frozenset[str]:
    return frozenset(pair)


def base_configs(names: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
    """v2 configs on which all the mutations apply and can be undone together."""
    out = []
    for config in cfg.all_configs():
        try:
            rt.base_config(config, list(names))
        except ValueError:
            continue
        out.append(config)
    return out


@functools.lru_cache(maxsize=1)
def _train_pairs() -> tuple[tuple[str, ...], ...]:
    held = {_pair_key(p) for p in HELD_OUT_PAIRS}
    return tuple(p for p in itertools.combinations(TRAIN_MUTATIONS, 2) if _pair_key(p) not in held and base_configs(p))


def mutation_sets(split: str, level: int, splits: dict | None = None) -> list[tuple[str, ...]]:
    """The mutation tuples a split offers at a level (1 mutation at R0/R1, 2 at R2/R3)."""
    if split not in SPLIT_NAMES or level not in SPLIT_LEVELS[split]:
        raise ValueError(f"split {split!r} has no tasks at level {level}; levels: {SPLIT_LEVELS.get(split)}")
    members = (splits or load_splits())["mutations"][split]
    return [tuple(m) for m in members if len(m) == (1 if level <= 1 else 2)]


def default_membership() -> dict:
    return {
        "seed": BASE_SEED,
        "held_out_mutations": list(HELD_OUT_MUTATIONS),
        "held_out_pairs": [list(p) for p in HELD_OUT_PAIRS],
        "levels": {k: list(v) for k, v in SPLIT_LEVELS.items()},
        "domains": list(DOMAINS),
        "mutations": {
            "train": [[m] for m in TRAIN_MUTATIONS] + [list(p) for p in _train_pairs()],
            "test_mutation": [[m] for m in HELD_OUT_MUTATIONS],
            "test_pair": [list(p) for p in HELD_OUT_PAIRS],
        },
    }


def default_splits() -> dict:
    """Membership and the fixed evaluation tasks, computed from the constants above."""
    out = default_membership()
    out["eval"] = {name: _sample(out, name, EVAL_TASKS, seed=BASE_SEED, balanced=True) for name in ("test_mutation", "test_pair")}
    return out


@functools.lru_cache(maxsize=1)
def load_splits(path: str | None = None) -> dict:
    return json.loads(Path(path or SPLITS_PATH).read_text(encoding="utf-8"))


def available_levels(split: str) -> tuple[int, ...]:
    if split not in SPLIT_NAMES:
        raise ValueError(f"unknown split {split!r}; known: {SPLIT_NAMES}")
    return SPLIT_LEVELS[split]


def reserved_seeds(splits: dict | None = None) -> set[int]:
    """Seeds no sampled task may use: the fixed evaluation tasks and the core 48 tasks."""
    out: set[int] = set()
    for specs in (splits or {}).get("eval", {}).values():
        out |= {s["row"]["seed"] for s in specs}
    if CORE_CONFIGS_PATH.is_file():
        out |= {json.loads(line)["seed"] for line in CORE_CONFIGS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()}
    return out


def sample_spec(
    split: str, rng: random.Random, *, level: int | None = None, splits: dict | None = None, avoid: set[int] | None = None,
    mutations: tuple[str, ...] | None = None, index: int = 0,
) -> dict:
    """One task spec of a split. `level=None` draws one of the split's levels. The task seed is never a reserved seed."""
    splits = splits or load_splits()
    levels = available_levels(split)
    level = rng.choice(levels) if level is None else level
    options = mutation_sets(split, level, splits)
    names = mutations or rng.choice(options)
    config = rng.choice(base_configs(names))
    factors = LEVEL_FACTORS[level]
    if split == "train":  # an unsolvable task teaches guessing: leave it out of training
        factors = tuple(f for f in factors if identifiable(names, f[1]))
    error_detail, evidence = rng.choice(factors)
    blocked = reserved_seeds(splits) | (avoid or set())
    seed = rng.randrange(SEED_SPACE)
    while seed in blocked:
        seed = rng.randrange(SEED_SPACE)
    row = {"index": index, "mutations": list(names), "domain": rng.choice(splits["domains"]), "config": config, "seed": seed}
    return rt.make_spec(row, error_detail=error_detail, evidence=evidence, level=level)


def _sample(splits: dict, split: str, n: int, *, seed: int, balanced: bool = False, level: int | None = None) -> list[dict]:
    """`n` distinct specs. `balanced`: levels in turn, and within a level the mutation sets in turn."""
    rng = random.Random(f"repair-splits:{split}:{seed}")
    levels = available_levels(split)
    out: list[dict] = []
    for i in range(n):
        lvl = level if level is not None else levels[i % len(levels)]
        names = None
        if balanced:
            options = mutation_sets(split, lvl, splits)
            names = options[(i // len(levels)) % len(options)]
        out.append(sample_spec(split, rng, level=lvl, splits=splits, avoid={s["row"]["seed"] for s in out}, mutations=names, index=i))
    return out


def sample_specs(split: str, n: int, *, level: int | None = None, seed: int = 0) -> list[dict]:
    """`n` distinct task specs of a split, deterministic for the arguments. For a test split with the default seed and no
    level, the first `n` fixed evaluation tasks are returned."""
    splits = load_splits()
    if split != "train" and level is None and seed == 0 and n <= len(splits["eval"].get(split, [])):
        return [json.loads(json.dumps(s)) for s in splits["eval"][split][:n]]
    return _sample(splits, split, n, seed=seed, level=level)


def write_splits(path: Path = SPLITS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_splits(), indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_splits()
    print(f"wrote {SPLITS_PATH}", file=sys.stderr)
