"""Task specs, the build cache and the reliability grading variants.

A task spec is a plain dict: {domain, config, seed, source, level, noise, build_seed, compact}. It is the
only thing the dataset stores about a task, and the cache key is a hash of it.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import tempfile
from pathlib import Path

from har2client.env import sources
from har2client.env.curriculum import LEVELS, level_of
from har2client.tierb import config as cfg
from har2client.tierb import domains
from har2client.tierb.build import _pick_test_seed, build_tierb_task
from har2client.tierb.spec import make_credentials
from har2client.tierb.testgen import make_tests

CACHE_ENV = "HAR2CLIENT_RL_CACHE"
SPEC_FIELDS = ("domain", "config", "seed", "source", "level", "noise", "build_seed", "compact")


def make_spec(
    domain: str, config: dict[str, str], seed: int, source: str = sources.DEFAULT_SOURCE, *,
    level: int | None = None, noise: float | None = None, build_seed: int = 0, compact: bool = False,
) -> dict:
    """A validated spec. `level` defaults to the level of the config, `noise` to the noise of the level."""
    if domain not in domains.DOMAINS:
        raise ValueError(f"unknown domain {domain!r}")
    if not cfg.is_valid(config):
        raise ValueError(f"invalid config {config}")
    sources.get(source)
    level = level_of(config) if level is None else level
    noise = LEVELS[level].noise if noise is None else noise
    return {
        "domain": domain, "config": dict(config), "seed": int(seed), "source": source, "level": int(level),
        "noise": float(noise), "build_seed": int(build_seed), "compact": bool(compact),
    }


def normalize(spec: dict | str) -> dict:
    """The spec as stored in a dataset row (a dict, or a JSON string) -> a validated spec."""
    data = json.loads(spec) if isinstance(spec, str) else spec
    return make_spec(
        data["domain"], data["config"], data["seed"], data.get("source", sources.DEFAULT_SOURCE), level=data.get("level"),
        noise=data.get("noise"), build_seed=data.get("build_seed", 0), compact=data.get("compact", False),
    )


def spec_key(spec: dict) -> str:
    text = json.dumps({k: spec[k] for k in SPEC_FIELDS}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def default_cache_dir() -> Path:
    return Path(os.environ.get(CACHE_ENV) or Path(tempfile.gettempdir()) / "har2client_rl_cache")


def ensure_task(spec: dict | str, cache_dir: str | Path | None = None) -> Path:
    """The task dir of a spec, built on first use with the Tier B builder (its reference client must score 1.0,
    so every cached task is solvable). Safe to call from several processes: the build goes to a temp dir
    and is renamed into place."""
    spec = normalize(spec)
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    target = cache / f"rl_{spec_key(spec)}"
    if (target / "task.json").exists():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".build_", dir=cache))
    try:
        row = {"index": 0, "domain": spec["domain"], "config": spec["config"], "seed": spec["seed"]}
        built = build_tierb_task(
            row, scratch, noise=spec["noise"], build_seed=spec["build_seed"], negatives=False, task_id=target.name,
            source=spec["source"], compact=spec["compact"],
        )
        try:
            built.rename(target)
        except OSError:
            if not (target / "task.json").exists():
                raise  # lost a race only if the other build finished
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return target


def observation_path(task_dir: str | Path) -> Path:
    """The single visible `session.*` file of a task dir."""
    found = [Path(task_dir) / name for name in dict.fromkeys(sources.OBSERVATION_NAMES) if (Path(task_dir) / name).is_file()]
    if len(found) != 1:
        raise FileNotFoundError(f"expected one session file in {task_dir}, found {[p.name for p in found]}")
    return found[0]


def read_visible(task_dir: str | Path) -> dict:
    """What the agent may see: {goal, observation, observation_file, source}."""
    task_dir = Path(task_dir)
    path = observation_path(task_dir)
    source = next(name for name, s in sources.SOURCES.items() if f"session{s.extension}" == path.name)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    return {
        "goal": (task_dir / "goal.md").read_text(encoding="utf-8"),
        "observation": path.read_text(encoding="utf-8"),
        "observation_file": path.name,
        "source": task.get("source", source),
    }


def grading_variants(task_dir: str | Path, k: int) -> list[Path]:
    """`k` task dirs that differ only in the hidden grading instance (test seed, test credentials, tests):
    the task dir itself and k-1 siblings `<name>.rel<i>`. Same spec -> same variants."""
    task_dir = Path(task_dir)
    if k < 1:
        raise ValueError("k must be at least 1")
    dirs = [task_dir]
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    for i in range(1, k):
        target = task_dir.with_name(f"{task_dir.name}.rel{i}")
        if not (target / "task.json").exists():
            _write_variant(task_dir, task, i, target)
        dirs.append(target)
    return dirs


def _write_variant(task_dir: Path, task: dict, i: int, target: Path) -> None:
    engine = json.loads((task_dir / "engine.json").read_text(encoding="utf-8"))
    spec = engine
    rng = random.Random(f"reliability:{task['seeds']['spec']}:{task['seeds']['test']}:{i}")
    text = observation_path(task_dir).read_text(encoding="utf-8")
    seed = _pick_test_seed(spec, rng, text, task["seeds"]["test"])
    used = {task["credentials"]["recording"]["password"], task["credentials"]["test"]["password"]}
    creds = make_credentials(spec, rng)
    while creds["password"] in used:
        creds = make_credentials(spec, rng)
    variant = {**task, "seeds": {**task["seeds"], "test": seed}, "credentials": {**task["credentials"], "test": creds}}
    scratch = Path(tempfile.mkdtemp(prefix=".variant_", dir=task_dir.parent))
    try:
        (scratch / "task.json").write_text(json.dumps(variant, indent=2) + "\n", encoding="utf-8")
        (scratch / "engine.json").write_text(json.dumps(engine, indent=2) + "\n", encoding="utf-8")
        (scratch / "tests.json").write_text(json.dumps(make_tests(spec, seed), indent=2) + "\n", encoding="utf-8")
        try:
            scratch.rename(target)
        except OSError:
            if not (target / "task.json").exists():
                raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
