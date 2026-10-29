"""Pick the base configs of the case 3 task set: 8 mutations x 6 rows, where the mutation applies.

A row is {index, mutation, domain, base_config (v1), config (v2), seed}. `seed` is the spec seed of the v2 API
(`make_spec(domain, config, seed)`); the from-scratch comparison task uses the same row."""

from __future__ import annotations

import json
import random
from pathlib import Path

from har2client.heal.mutations import MUTATIONS, derive_v1_spec, v1_config
from har2client.tierb import config as cfg
from har2client.tierb.domains import DOMAINS
from har2client.tierb.spec import make_spec

PER_MUTATION = 6
SEED_SPACE = 10**9


def _label(row: dict) -> str:
    return "+".join(row["mutations"]) if row.get("mutations") else row["mutation"]


def task_id_of(row: dict) -> str:
    return f"heal_{row['index']:02d}_{_label(row)}_{row['domain']}"


def scratch_id_of(row: dict) -> str:
    return f"scratch_{row['index']:02d}_{_label(row)}_{row['domain']}"


def _levels(config: dict, domain: str) -> list[tuple[str, str]]:
    cells = [(k, "na" if k == "token_expiry" and config["auth"] not in cfg.LOGIN_MODES else v) for k, v in config.items()]
    return [*cells, ("domain", domain)]


def select_rows(seed: int, per_mutation: int = PER_MUTATION) -> list[dict]:
    """Deterministic for a seed. Per mutation: base configs that differ in the other switches and cover the domains
    as evenly as possible (greedy: least-used level combination first); the counts also run across mutations."""
    rng = random.Random(f"heal-rows:{seed}")
    domains = list(DOMAINS)
    overall: dict[tuple, int] = {}
    rows: list[dict] = []
    for mutation in MUTATIONS:
        local: dict[tuple, int] = {}
        pool = [(c, d) for c in cfg.all_configs() if mutation.applies(c) and _valid_base(c, mutation.name) for d in domains]
        rng.shuffle(pool)
        for _ in range(per_mutation):
            def cost(item: tuple[dict, str]) -> tuple[int, int]:
                cells = _levels(v1_config(item[0], mutation.name), item[1])
                return sum(local.get(k, 0) for k in cells), sum(overall.get(k, 0) for k in cells)

            best = min(range(len(pool)), key=lambda i: cost(pool[i]))
            config, domain = pool.pop(best)
            for k in _levels(v1_config(config, mutation.name), domain):
                local[k] = local.get(k, 0) + 1
                overall[k] = overall.get(k, 0) + 1
            rows.append({"mutation": mutation.name, "domain": domain, "config": config, "seed": rng.randrange(SEED_SPACE)})
    out = []
    for i, row in enumerate(rows):
        spec_v2 = make_spec(row["domain"], row["config"], row["seed"])
        base = derive_v1_spec(spec_v2, row["mutation"], row["seed"])["config"]
        out.append({"index": i, "mutation": row["mutation"], "domain": row["domain"], "base_config": base, "config": row["config"], "seed": row["seed"]})
    return out


def _valid_base(config: dict, mutation: str) -> bool:
    try:
        v1_config(config, mutation)
    except ValueError:
        return False
    return True


def write_rows(rows: list[dict], path: Path, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"task_id": task_id_of(r), "scratch_id": scratch_id_of(r), **r, "config_seed": seed}) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_rows(path: Path) -> list[dict]:
    keys = ("index", "mutation", "domain", "base_config", "config", "seed")
    return [{k: r[k] for k in keys} for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if r]
