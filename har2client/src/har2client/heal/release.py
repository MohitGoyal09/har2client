"""Releases and the v3 pilot manifest (DESIGN-CASE3-HARD.md, "Pilot").

A release is 1-4 of the ten changes (`mutations.HARD_MUTATIONS`), at most one per switch, and its v2 config must be valid
(`tierb.config.is_valid`). A release of 3 or 4 changes has at least one drift and one protocol change. `moved`, `add_drift_rename`
and `add_drift_enum` appear only in releases of 2 changes or more (alone they are too easy), and a release has at least one of the
seven changes that stand alone in the pilot. The pilot has 38 tasks on dev seeds
1001 and up, balanced over the domains and the auth modes:

| family     | tasks | changes | factors beyond the default                       |
| single     | 14    | 1       | 7 kinds x 2 bases                                 |
| release3   | 4     | 3       |                                                   |
| release4   | 4     | 4       |                                                   |
| partial    | 4     | 3       | evidence_scope partial                            |
| sdk        | 4     | 3       | client_style sdk                                  |
| misleading | 4     | 3       | changelog misleading                              |
| terse      | 4     | 3       | error_detail terse                                |
The default is verbose errors, both evidence channels, full scope, no changelog and the compact client.

The evaluation suite (`EVAL_FAMILIES`, 64 tasks on fresh seeds 2001 and up) keeps every pilot family (the keep rule on the
pilot as built, PREREG.md) and has more releases per family."""

from __future__ import annotations

import json
import random
from pathlib import Path

from har2client.heal import hard, identify, mutations
from har2client.tierb import config as cfg
from har2client.tierb.domains import DOMAINS
from har2client.tierb.spec import make_spec

FIRST_SEED = 1001
SINGLE_KINDS = (
    "add_drift_cents", "add_drift_epoch", "add_drift_nest", "add_rate_limit", "add_async_create", "add_refresh_rotation", "add_idempotency_key",
)
ONLY_IN_RELEASES = ("add_moved", "add_drift_rename", "add_drift_enum")
SINGLES_PER_KIND = 2
SPLITS = {3: ((1, 2), (2, 1)), 4: ((2, 2), (1, 3), (3, 1))}  # (drift changes, protocol changes) of a release of k changes
FAMILIES: tuple[tuple[str, int, int, dict], ...] = (  # (family, tasks, changes, factors)
    ("single", 2 * len(SINGLE_KINDS), 1, {}),
    ("release3", 4, 3, {}),
    ("release4", 4, 4, {}),
    ("partial", 4, 3, {"evidence_scope": "partial"}),
    ("sdk", 4, 3, {"client_style": "sdk"}),
    ("misleading", 4, 3, {"changelog": "misleading"}),
    ("terse", 4, 3, {"error_detail": "terse"}),
)
EVAL_FIRST_SEED = 2001
EVAL_FAMILIES: tuple[tuple[str, int, int, dict], ...] = tuple(
    (family, {"single": count, "release3": 10}.get(family, 8), kind, factors) for family, count, kind, factors in FAMILIES
)
DEFAULT_FACTORS = {"error_detail": "verbose", "evidence": "both", "evidence_scope": "full", "changelog": "none", "client_style": "compact"}
CANDIDATE_TRIES = 60
MANIFEST_KEYS = (
    "task_id", "set", "index", "mutations", "changes", "domain", "base_config", "config", "seed", "error_detail", "evidence",
    "evidence_scope", "changelog", "client_style", "identifiable", "build_seed",
)


def short_name(name: str) -> str:
    return name.removeprefix("add_drift_").removeprefix("add_")


def task_id(family: str, index: int, names: list[str], domain: str) -> str:
    return f"v3_{family}_{index:02d}_{'+'.join(short_name(n) for n in names)}_{domain}"


def v2_config(base: dict, names: list[str]) -> dict:
    """`base` (the six old switches) with every named change on."""
    config = {**base, "drift": [n.removeprefix("add_drift_") for n in names if n in mutations.DRIFT_MUTATIONS]}
    for n in names:
        if n in mutations.PROTOCOL_MUTATIONS:
            config[n.removeprefix("add_")] = "on"
    return config


def _bases(names: list[str]) -> list[dict]:
    """Base configs (old switches) that can hold the release."""
    out = []
    for base in cfg.all_configs():
        if "add_refresh_rotation" in names and not (base["auth"] == "bearer_login" and base["token_expiry"] == "off"):
            continue
        if cfg.is_valid(v2_config(base, names)):
            out.append(base)
    return out


def _least_used(options: tuple[str, ...], count: int, used: dict[str, int], rng: random.Random, jitter: float) -> list[str]:
    ranked = sorted(options, key=lambda n: used.get(n, 0) + jitter * rng.random() + 1e-6 * rng.random())
    return ranked[:count]


def _pick_names(slot: int, kind: int, used: dict[str, int], rng: random.Random, single: str | None, attempt: int) -> list[str]:
    """The changes of a release: the least used ones in the releases so far; later attempts add noise to the ranking."""
    if single is not None:
        return [single]
    drifts, protocols = SPLITS[kind][slot % len(SPLITS[kind])]
    jitter = 0.0 if attempt == 0 else 3.0
    names = _least_used(mutations.DRIFT_MUTATIONS, drifts, used, rng, jitter) + _least_used(mutations.PROTOCOL_MUTATIONS, protocols, used, rng, jitter)
    return sorted(names, key=list(mutations.BY_NAME).index)


def select_pilot(seed: int = 1, first_seed: int = FIRST_SEED, families: tuple = FAMILIES) -> list[dict]:
    """The manifest entries of `families` (default: the 38 of the pilot), deterministic for `seed`. Spec seeds are `first_seed` + index. A release whose fix
    (compact style, or sdk when the renderer can do it) is below the size gate is replaced by another."""
    rng = random.Random(f"heal3-pilot:{seed}")
    domains = list(DOMAINS)
    used: dict[str, int] = {}
    seen: dict[tuple, int] = {}
    entries: list[dict] = []
    singles = [n for n in SINGLE_KINDS for _ in range(SINGLES_PER_KIND)]
    for family, count, kind, factors in families:
        for slot in range(count):
            index = len(entries)
            for attempt in range(CANDIDATE_TRIES):
                names = _pick_names(slot, kind, used, rng, singles[slot] if family == "single" else None, attempt)
                if not any(n in SINGLE_KINDS for n in names):  # a release of the easy changes only would be easy
                    continue
                bases = _bases(names)
                if not bases:
                    continue
                pool = [(b, d) for b in bases for d in domains]
                rng.shuffle(pool)
                base, domain = min(pool, key=lambda p: (seen.get(("domain", p[1]), 0) + seen.get(("auth", p[0]["auth"]), 0), seen.get(("pair", p[1], p[0]["auth"]), 0)))
                row = {**DEFAULT_FACTORS, **factors, "mutations": names, "domain": domain, "config": v2_config(base, names), "seed": first_seed + index}
                if _fix_ok(row):
                    break
            else:
                raise RuntimeError(f"no valid release for {family} #{slot}")
            for n in names if kind > 1 else ():  # the usage counts balance the releases; the singles are fixed
                used[n] = used.get(n, 0) + 1
            for key in (("domain", domain), ("auth", base["auth"]), ("pair", domain, base["auth"])):
                seen[key] = seen.get(key, 0) + 1
            v1 = mutations.derive_v1_spec(make_spec(domain, row["config"], row["seed"]), names, row["seed"])
            entries.append({
                **row, "task_id": task_id(family, index, names, domain), "set": family, "index": index, "changes": len(names),
                "base_config": v1["config"], "identifiable": identify.label(names, row["evidence"], row["evidence_scope"]), "build_seed": seed,
            })
    return entries


def _fix_ok(row: dict) -> bool:
    """The fix-size gate on the generated sources (cheap: no server). A style whose renderer fails is let through: the build reports it."""
    names = tuple(row["mutations"])
    spec = make_spec(row["domain"], row["config"], row["seed"])
    try:
        v1_spec = mutations.derive_v1_spec(spec, names, row["seed"])
        lines, classes = hard.fix_size(hard.generate_source(row["client_style"], v1_spec, mutations.v1_flaws(v1_spec)), hard.generate_source(row["client_style"], spec))
    except (NotImplementedError, TypeError, KeyError):
        return row["client_style"] == "sdk"
    return hard.check_fix_size(names, row["client_style"], lines, classes) is None


def write_manifest(entries: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({k: e[k] for k in MANIFEST_KEYS}) + "\n" for e in entries), encoding="utf-8")


def read_manifest(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
