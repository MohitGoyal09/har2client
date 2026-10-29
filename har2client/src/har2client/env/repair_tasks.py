"""Repair task specs, the build cache and the visible view of a repair task (DESIGN-CASE3.md, "RL environment").

A repair task spec is a plain dict
    {row: {index, mutations, domain, config, seed}, build_seed, noise, error_detail, evidence, level, identifiable}
and the cache key is a hash of it (`identifiable` is derived from the mutations and the evidence, so it is not in the key). `row` is a case 3 row (see `heal/select.py`) with a list of 1-2 mutations.
The builder is `heal.build.build_heal_task`; `build_task` below is a thin adapter so that this module works with
the single-mutation, verbose, both-channels builder as well as with the extended one (`error_detail`, `evidence`,
`row["mutations"]`).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import tempfile
from pathlib import Path

from har2client.heal import build as heal_build
from har2client.heal import identify as heal_identify
from har2client.heal import mutations as heal_mutations
from har2client.tierb import config as cfg

CACHE_ENV = "HAR2CLIENT_RL_CACHE"
ERROR_DETAILS = ("verbose", "terse")
EVIDENCE = ("both", "failure_only", "recording_only")
DEFAULT_NOISE = heal_build.HEAL_NOISE
SPEC_FIELDS = ("row", "build_seed", "noise", "error_detail", "evidence", "level")
ROW_FIELDS = ("index", "mutations", "domain", "config", "seed")
MAX_CHANGES = 2


class FactorsUnavailable(NotImplementedError):
    """The builder does not support the requested task factors (yet)."""


def row_mutations(row: dict) -> list[str]:
    """The mutation names of a row: `row["mutations"]` (1-2 names) or the older single `row["mutation"]`."""
    names = row.get("mutations") or ([row["mutation"]] if row.get("mutation") else [])
    return list(names)


def level_of(n_changes: int, error_detail: str, evidence: str) -> int:
    """Curriculum level of a task: R0 1 change, verbose, both; R1 1 change with terse errors or one channel;
    R2 2 changes, verbose, both; R3 2 changes with terse errors or one channel."""
    plain = error_detail == "verbose" and evidence == "both"
    return (0 if plain else 1) if n_changes == 1 else (2 if plain else 3)


def base_config(config: dict[str, str], names: list[str]) -> dict[str, str]:
    """The v1 config of a v2 config with all the mutations undone. Raises ValueError when they do not fit this config
    or each other (same switch)."""
    try:
        return heal_mutations.v1_config(config, list(names))
    except (TypeError, KeyError) as exc:  # a revert that reads what another one removed
        raise ValueError(f"mutations {names} do not combine on {config}: {exc}") from exc


def make_spec(
    row: dict, *, error_detail: str = "verbose", evidence: str = "both", build_seed: int = 0, noise: float = DEFAULT_NOISE,
    level: int | None = None,
) -> dict:
    """A validated spec. `level` defaults to the level the factors give."""
    names = row_mutations(row)
    if not 1 <= len(names) <= MAX_CHANGES or len(set(names)) != len(names):
        raise ValueError(f"a row needs 1 or {MAX_CHANGES} different mutations, got {names}")
    unknown = [n for n in names if n not in heal_mutations.BY_NAME]
    if unknown:
        raise ValueError(f"unknown mutation {unknown}")
    if error_detail not in ERROR_DETAILS or evidence not in EVIDENCE:
        raise ValueError(f"error_detail must be in {ERROR_DETAILS} and evidence in {EVIDENCE}")
    if not cfg.is_valid(row["config"]):
        raise ValueError(f"invalid config {row['config']}")
    base_config(row["config"], names)
    clean = {"index": int(row.get("index", 0)), "mutations": names, "domain": row["domain"], "config": dict(row["config"]), "seed": int(row["seed"])}
    return {
        "row": clean, "build_seed": int(build_seed), "noise": float(noise), "error_detail": error_detail, "evidence": evidence,
        "level": level_of(len(names), error_detail, evidence) if level is None else int(level),
        "identifiable": heal_identify.identifiable(names, evidence),  # False: the repair cannot be worked out from the visible files
    }


def normalize(spec: dict | str) -> dict:
    """A spec as stored in a dataset row (a dict or a JSON string) -> a validated spec."""
    data = json.loads(spec) if isinstance(spec, str) else spec
    return make_spec(
        data["row"], error_detail=data.get("error_detail", "verbose"), evidence=data.get("evidence", "both"),
        build_seed=data.get("build_seed", 0), noise=data.get("noise", DEFAULT_NOISE), level=data.get("level"),
    )


def spec_key(spec: dict) -> str:
    return hashlib.sha256(json.dumps({k: spec[k] for k in SPEC_FIELDS}, sort_keys=True).encode()).hexdigest()[:16]


def default_cache_dir() -> Path:
    return Path(os.environ.get(CACHE_ENV) or Path(tempfile.gettempdir()) / "har2client_rl_cache")


def build_task(spec: dict, out_dir: Path, task_id: str) -> Path:
    """Build the task of a normalized spec with `heal.build.build_heal_task` (adapter for old and new builder)."""
    names = spec["row"]["mutations"]
    row = {**spec["row"], "mutations": names}
    if len(names) == 1:
        row["mutation"] = names[0]  # the single-mutation builder reads this key; a pair must not fall back to it
    params = inspect.signature(heal_build.build_heal_task).parameters
    kwargs: dict = {"build_seed": spec["build_seed"], "noise": spec["noise"], "task_id": task_id}
    if "error_detail" in params and "evidence" in params:
        kwargs.update(error_detail=spec["error_detail"], evidence=spec["evidence"])
    elif spec["error_detail"] != "verbose" or spec["evidence"] != "both":
        raise FactorsUnavailable("the heal builder has no error_detail / evidence options yet")
    return heal_build.build_heal_task(row, out_dir, **kwargs)


def ensure_task(spec: dict | str, cache_dir: str | Path | None = None) -> Path:
    """The task dir of a spec, built on first use (the builder runs its validity gates: the v2 reference client scores 1.0,
    `client_v1.py` fails). Safe to call from several processes: the build goes to a temp dir and is renamed into place."""
    spec = normalize(spec)
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    target = cache / f"repair_{spec_key(spec)}"
    if (target / "task.json").exists():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".build_", dir=cache))
    try:
        built = build_task(spec, scratch, target.name)
        try:
            built.rename(target)
        except OSError:
            if not (target / "task.json").exists():
                raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return target


def _read_optional(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def read_visible(task_dir: str | Path) -> dict:
    """What the agent may see: {goal, client_v1, failure | None, observation | None, observation_format | None}."""
    d = Path(task_dir)
    observation = _read_optional(d / "session.har")
    return {
        "goal": (d / "goal.md").read_text(encoding="utf-8"), "client_v1": (d / "client_v1.py").read_text(encoding="utf-8"),
        "failure": _read_optional(d / "failure.txt"), "observation": observation, "observation_format": "har" if observation is not None else None,
    }
