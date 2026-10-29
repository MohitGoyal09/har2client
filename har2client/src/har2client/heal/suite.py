"""The case 3 v2 task suite (DESIGN-CASE3.md): 144 tasks in 4 sets, and its manifest `data/heal_suite.jsonl`.

| set      | tasks | rows                                                    | error_detail | evidence                    | changes |
| core     | 48    | the 48 rows of `data/heal_configs.jsonl` (heal48)        | verbose      | both                        | 1       |
| terse    | 24    | the first 3 rows of each mutation                        | terse        | both                        | 1       |
| evidence | 48    | the same 24 rows, once per evidence level               | verbose      | failure_only, recording_only | 1       |
| compound | 24    | 12 compatible mutation pairs x 2 base configs            | verbose      | both                        | 2       |

The variants of one row share its spec seed, credentials, data and hidden tests (`build_seed` is the same), so a terse or
single-channel task differs from its core task in the studied factor only (compound rows are new APIs)."""

from __future__ import annotations

import json
import random
from pathlib import Path

from har2client.heal import identify, mutations
from har2client.heal.select import SEED_SPACE, _levels
from har2client.tierb import config as cfg
from har2client.tierb.domains import DOMAINS
from har2client.tierb.spec import make_spec

SETS = ("core", "terse", "evidence", "compound")
SET_SIZES = {"core": 48, "terse": 24, "evidence": 48, "compound": 24}
TERSE_BASES_PER_MUTATION = 3
BASES_PER_PAIR = 2
EVIDENCE_PREFIX = {"both": "", "failure_only": "evfail", "recording_only": "evrec"}
MANIFEST_KEYS = (
    "task_id", "set", "index", "mutations", "changes", "domain", "base_config", "config", "seed", "error_detail", "evidence",
    "identifiable", "base_index", "build_seed",
)

# 12 compatible mutation pairs (different switches, both apply to one v2 config); every mutation is in exactly 3 pairs.
COMPOUND_PAIRS: tuple[tuple[str, str], ...] = (
    ("add_hidden_header", "offset_to_cursor"),
    ("add_hidden_header", "add_etag"),
    ("add_hidden_header", "add_token_expiry"),
    ("rename_hidden_header", "none_to_link_header"),
    ("rename_hidden_header", "add_envelope"),
    ("rename_hidden_header", "apikey_to_bearer_login"),
    ("apikey_to_bearer_login", "add_etag"),
    ("apikey_to_bearer_login", "none_to_link_header"),
    ("offset_to_cursor", "add_envelope"),
    ("offset_to_cursor", "add_token_expiry"),
    ("none_to_link_header", "add_etag"),
    ("add_envelope", "add_token_expiry"),
)


def task_id(set_name: str, row: dict, evidence: str = "both") -> str:
    """core: `heal_<i>_<mutation>_<domain>` (the heal48 names); terse: `terse_...`; evidence: `evfail_...` / `evrec_...`;
    compound: `compound_<i>_<a>+<b>_<domain>`. The index of terse and evidence tasks is the core row index."""
    label = "+".join(row["mutations"])
    prefix = {"core": "heal", "terse": "terse", "compound": "compound"}.get(set_name) or EVIDENCE_PREFIX[evidence]
    return f"{prefix}_{row['index']:02d}_{label}_{row['domain']}"


def _entry(set_name: str, row: dict, *, error_detail: str, evidence: str, build_seed: int, base_index: int | None = None) -> dict:
    names = list(row["mutations"])
    return {
        "task_id": task_id(set_name, row, evidence), "set": set_name, "index": row["index"], "mutations": names, "changes": len(names),
        "domain": row["domain"], "base_config": row["base_config"], "config": row["config"], "seed": row["seed"],
        "error_detail": error_detail, "evidence": evidence, "identifiable": identify.identifiable(names, evidence),
        "base_index": row["index"] if base_index is None else base_index, "build_seed": build_seed,
    }


def select_compound_rows(seed: int, pairs: tuple[tuple[str, str], ...] = COMPOUND_PAIRS, per_pair: int = BASES_PER_PAIR) -> list[dict]:
    """Rows {index, mutations, domain, base_config, config, seed} (index 0..): per pair, base configs where both mutations
    apply, as different in the other switches and the domain as possible (greedy on level counts, as `select_rows`)."""
    rng = random.Random(f"heal-compound-rows:{seed}")
    domains = list(DOMAINS)
    overall: dict[tuple, int] = {}
    out = []
    for pair in pairs:
        local: dict[tuple, int] = {}
        pool = [(c, d) for c in cfg.all_configs() if _applies(c, pair) for d in domains]
        rng.shuffle(pool)
        for _ in range(per_pair):
            def cells(item: tuple[dict, str]) -> list[tuple[str, str]]:
                return _levels(mutations.v1_config(item[0], pair), item[1])

            best = min(range(len(pool)), key=lambda i: (sum(local.get(k, 0) for k in cells(pool[i])), sum(overall.get(k, 0) for k in cells(pool[i]))))
            config, domain = pool.pop(best)
            for k in cells((config, domain)):
                local[k] = local.get(k, 0) + 1
                overall[k] = overall.get(k, 0) + 1
            row_seed = rng.randrange(SEED_SPACE)
            base = mutations.derive_v1_spec(make_spec(domain, config, row_seed), pair, row_seed)["config"]
            out.append({"index": len(out), "mutations": list(pair), "domain": domain, "base_config": base, "config": config, "seed": row_seed})
    return out


def _applies(config: dict, pair: tuple[str, ...]) -> bool:
    try:
        mutations.v1_config(config, pair)
    except ValueError:
        return False
    return True


def build_manifest(core_rows: list[dict], seed: int) -> list[dict]:
    """The 144 manifest entries (in set order). `core_rows`: the rows of `data/heal_configs.jsonl` (or `select_rows`)."""
    core_rows = [{**r, "mutations": list(mutations.names_of(r.get("mutations") or r["mutation"]))} for r in core_rows]
    per_mutation: dict[str, int] = {}
    terse_rows = []
    for r in core_rows:
        n = per_mutation.get(r["mutations"][0], 0)
        per_mutation[r["mutations"][0]] = n + 1
        if n < TERSE_BASES_PER_MUTATION:
            terse_rows.append(r)
    entries = [_entry("core", r, error_detail="verbose", evidence="both", build_seed=seed) for r in core_rows]
    entries += [_entry("terse", r, error_detail="terse", evidence="both", build_seed=seed) for r in terse_rows]
    entries += [
        _entry("evidence", r, error_detail="verbose", evidence=ev, build_seed=seed)
        for r in terse_rows for ev in ("failure_only", "recording_only")
    ]
    entries += [_entry("compound", r, error_detail="verbose", evidence="both", build_seed=seed) for r in select_compound_rows(seed)]
    return entries


def task_row(entry: dict) -> dict:
    """The `build_heal_task` row of a manifest entry."""
    return {k: entry[k] for k in ("index", "mutations", "domain", "base_config", "config", "seed")}


def write_manifest(entries: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({k: e[k] for k in MANIFEST_KEYS}) + "\n" for e in entries), encoding="utf-8")


def read_manifest(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
