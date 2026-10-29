"""Feature switches and the balanced selection of the 50 Tier B configs."""

from __future__ import annotations

import random
from itertools import combinations, product

from har2client.tierb.domains import DOMAINS

FACTORS: dict[str, list[str]] = {
    "auth": ["apikey_header", "bearer_login", "cookie_csrf"],
    "pagination": ["none", "offset", "cursor", "link_header"],
    "token_expiry": ["off", "on"],
    "etag_update": ["off", "on"],
    "hidden_header": ["off", "on"],
    "envelope": ["off", "on"],
}
LOGIN_MODES = {"bearer_login", "cookie_csrf"}
# Hard-repair switches (DESIGN-CASE3-HARD.md). Optional keys of a config, all off when absent, so the 6 factors above, the
# sampling of the 50 tasks and every existing config stay as they were. `drift` is a list of kinds, the rest are off/on.
DRIFT_KINDS = ("rename", "cents", "epoch", "nest", "enum")
EXTRA_SWITCHES: dict[str, object] = {
    "drift": [], "rate_limit": "off", "async_create": "off", "refresh_rotation": "off", "idempotency_key": "off", "moved": "off",
}
DEFAULT_COUNT = 50
SEED_SPACE = 10**9
_NA = "na"  # token_expiry level of apikey tasks, kept apart from "off" when balancing


def baseline_config() -> dict[str, str]:
    return {name: levels[0] for name, levels in FACTORS.items()}


def switch(config: dict, name: str):
    """Value of one of the optional hard-repair switches (its default when the config does not name it)."""
    return config.get(name, EXTRA_SWITCHES[name])


def _extras_valid(config: dict) -> bool:
    drift = switch(config, "drift")
    if not isinstance(drift, list) or len(set(drift)) != len(drift) or any(k not in DRIFT_KINDS for k in drift):
        return False
    if any(switch(config, n) not in ("off", "on") for n in EXTRA_SWITCHES if n != "drift"):
        return False
    # refresh_rotation is a mode of bearer_login with its own expiry: it replaces token_expiry (a re-login is refused there)
    return switch(config, "refresh_rotation") == "off" or (config["auth"] == "bearer_login" and config["token_expiry"] == "off")


def is_valid(config: dict[str, str]) -> bool:
    if set(config) - set(EXTRA_SWITCHES) != set(FACTORS) or any(config[k] not in FACTORS[k] for k in FACTORS):
        return False
    return not (config["token_expiry"] == "on" and config["auth"] not in LOGIN_MODES) and _extras_valid(config)


def all_configs() -> list[dict[str, str]]:
    combos = (dict(zip(FACTORS, values)) for values in product(*FACTORS.values()))
    return [c for c in combos if is_valid(c)]


def _levels(config: dict[str, str], domain: str) -> dict[str, str]:
    levels = {**config, "domain": domain}
    if config["auth"] not in LOGIN_MODES:
        levels["token_expiry"] = _NA
    return levels


def sample_configs(seed: int, n: int = DEFAULT_COUNT) -> list[dict]:
    """`n` rows {index, domain, config, seed}: the all-off baseline in every domain, then a greedy
    fractional factorial that keeps every switch, every pair of switches and the domains as evenly
    covered as possible. Deterministic for a given seed."""
    rng = random.Random(f"tierb-configs:{seed}")
    domains = list(DOMAINS)
    names = [*FACTORS, "domain"]
    pairs = list(combinations(names, 2))
    singles: dict[tuple, int] = {}
    doubles: dict[tuple, int] = {}
    chosen: list[tuple[dict, str]] = []

    def cells(config: dict, domain: str) -> tuple[list[tuple], list[tuple]]:
        lv = _levels(config, domain)
        return [(a, lv[a]) for a in names], [(a, lv[a], b, lv[b]) for a, b in pairs]

    def add(config: dict, domain: str) -> None:
        one, two = cells(config, domain)
        for key in one:
            singles[key] = singles.get(key, 0) + 1
        for key in two:
            doubles[key] = doubles.get(key, 0) + 1
        chosen.append((config, domain))

    def cost(config: dict, domain: str) -> int:
        one, two = cells(config, domain)  # adding to a cell with count c raises the sum of squares by 2c+1
        return sum(2 * singles.get(k, 0) + 1 for k in one) * len(pairs) + sum(2 * doubles.get(k, 0) + 1 for k in two)

    for domain in domains[:n]:
        add(baseline_config(), domain)
    pool = [(c, d) for c in all_configs() for d in domains if c != baseline_config()]
    rng.shuffle(pool)
    while len(chosen) < n:
        best = min(range(len(pool)), key=lambda i: cost(*pool[i]))
        add(*pool.pop(best))
    rng.shuffle(chosen)
    return [
        {"index": i, "domain": domain, "config": config, "seed": rng.randrange(SEED_SPACE)}
        for i, (config, domain) in enumerate(chosen)
    ]
