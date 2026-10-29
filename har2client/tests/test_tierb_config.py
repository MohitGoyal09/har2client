from collections import Counter
from itertools import combinations

from har2client.tierb import config as cfg
from har2client.tierb.domains import DOMAINS

SWITCHES = ["auth", "pagination", "token_expiry", "etag_update", "hidden_header", "envelope"]


def test_factor_levels_match_the_design():
    assert cfg.FACTORS["auth"] == ["apikey_header", "bearer_login", "cookie_csrf"]
    assert cfg.FACTORS["pagination"] == ["none", "offset", "cursor", "link_header"]
    for name in ("token_expiry", "etag_update", "hidden_header", "envelope"):
        assert cfg.FACTORS[name] == ["off", "on"]


def test_token_expiry_is_only_valid_with_login_modes():
    base = cfg.baseline_config()
    assert cfg.is_valid(base)
    assert not cfg.is_valid({**base, "token_expiry": "on"})
    assert cfg.is_valid({**base, "auth": "bearer_login", "token_expiry": "on"})
    assert cfg.is_valid({**base, "auth": "cookie_csrf", "token_expiry": "on"})
    assert not cfg.is_valid({**base, "auth": "nope"})


def test_all_valid_configs_count():
    # apikey: 4*2*2*2 = 32 (expiry off); login: 2 auth * 4 * 2 * 2 * 2 * 2 = 128
    assert len(cfg.all_configs()) == 160


def test_sample_is_seeded_and_has_50_rows():
    a, b, c = cfg.sample_configs(1), cfg.sample_configs(1), cfg.sample_configs(2)
    assert a == b and a != c
    assert len(a) == 50 and [r["index"] for r in a] == list(range(50))
    assert all(cfg.is_valid(r["config"]) for r in a)
    assert all(r["domain"] in DOMAINS for r in a)
    assert len({(tuple(r["config"].values()), r["domain"]) for r in a}) == 50


def test_sample_includes_an_all_off_baseline_in_every_domain():
    rows = cfg.sample_configs(1)
    base = [r["domain"] for r in rows if r["config"] == cfg.baseline_config()]
    assert sorted(base) == sorted(DOMAINS)


def test_sample_is_balanced():
    rows = cfg.sample_configs(1)
    for name, levels in cfg.FACTORS.items():
        if name == "token_expiry":
            continue
        counts = Counter(r["config"][name] for r in rows)
        assert set(counts) == set(levels)
        assert max(counts.values()) - min(counts.values()) <= 3, (name, counts)
    assert max(Counter(r["domain"] for r in rows).values()) == min(Counter(r["domain"] for r in rows).values()) == 10
    login = [r for r in rows if r["config"]["auth"] != "apikey_header"]
    on = sum(r["config"]["token_expiry"] == "on" for r in login)
    assert abs(on - (len(login) - on)) <= 2


def test_pairs_of_switches_are_all_covered():
    rows = cfg.sample_configs(1)
    for a, b in combinations(["auth", "pagination", "etag_update", "hidden_header", "envelope"], 2):
        cells = {(r["config"][a], r["config"][b]) for r in rows}
        assert len(cells) == len(cfg.FACTORS[a]) * len(cfg.FACTORS[b]), (a, b)
