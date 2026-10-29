"""Splits (committed file, held-out combinations, domains, seeds) and the curriculum."""

import random

import pytest

from har2client.env import splits
from har2client.env.curriculum import LEVELS, Curriculum, count_switches, level_of
from har2client.tierb import config as cfg


def test_committed_splits_file_matches_the_code():
    assert splits.load_splits() == splits.default_splits()


def test_levels_follow_the_switch_count():
    base = cfg.baseline_config()
    assert level_of(base) == 0 and count_switches(base) == 0
    assert level_of({**base, "envelope": "on"}) == 1
    assert level_of({**base, "envelope": "on", "etag_update": "on"}) == 2
    assert level_of({**base, "envelope": "on", "etag_update": "on", "pagination": "offset"}) == 3
    assert [lv.noise for lv in LEVELS][:3] == [0.0, 0.15, 0.3] and LEVELS[3].noise == 0.7 and len(LEVELS) == 4


def test_train_never_contains_a_held_out_combination_and_test_combo_only_does():
    for level in range(4):
        assert not any(splits.is_held_out(c) for c in splits.configs_for("train", level))
        assert all(splits.is_held_out(c) for c in splits.configs_for("test_combo", level))
    assert splits.available_levels("test_combo") == (3,)
    assert splits.available_levels("train") == (0, 1, 2, 3)
    for spec in splits.sample_specs("train", 200, seed=1):
        assert not splits.is_held_out(spec["config"])
    for spec in splits.sample_specs("test_combo", 40, seed=1):
        assert splits.is_held_out(spec["config"])


def test_the_held_out_triples_have_their_parts_in_train():
    train = [c for lv in range(4) for c in splits.configs_for("train", lv)]
    for combo in splits.HELD_OUT_COMBOS:
        for key, value in combo.items():
            assert any(c[key] == value for c in train), (key, value)


def test_test_domain_holds_out_one_domain_and_train_never_uses_it():
    assert splits.sample_specs("test_domain", 30, seed=2) and {s["domain"] for s in splits.sample_specs("test_domain", 30, seed=2)} == {splits.HELD_OUT_DOMAIN}
    assert splits.HELD_OUT_DOMAIN not in {s["domain"] for s in splits.sample_specs("train", 300, seed=2)}
    assert splits.HELD_OUT_DOMAIN not in {s["domain"] for s in splits.sample_specs("test_combo", 100, seed=2)}


def test_sources_of_the_splits_and_the_explicit_source_override():
    assert {s["source"] for s in splits.sample_specs("train", 60, seed=3)} == {"browser_har", "mobile_har"}
    assert {s["source"] for s in splits.sample_specs("train", 20, seed=3, source="mobile_har")} == {"mobile_har"}


def test_sampling_is_deterministic_stratified_by_level_and_seeds_are_distinct():
    a, b = splits.sample_specs("train", 40, seed=9), splits.sample_specs("train", 40, seed=9)
    assert a == b and a != splits.sample_specs("train", 40, seed=10)
    assert [s["level"] for s in a[:8]] == [0, 1, 2, 3, 0, 1, 2, 3]
    assert len({s["seed"] for s in a}) == 40
    assert all(s["noise"] == LEVELS[s["level"]].noise for s in a)


def test_train_seeds_never_hit_the_fixed_evaluation_tasks():
    eval_seeds = {s["seed"] for name in ("test_combo", "test_domain") for s in splits.load_splits()["eval"][name]}
    assert len(eval_seeds) == 2 * splits.EVAL_TASKS
    assert not eval_seeds & {s["seed"] for s in splits.sample_specs("train", 500, seed=0)}


def test_fixed_evaluation_tasks_are_returned_for_the_test_splits():
    committed = splits.load_splits()["eval"]["test_combo"]
    assert splits.sample_specs("test_combo", 10) == committed[:10]


def test_unknown_split_or_unavailable_level_is_an_error():
    with pytest.raises(ValueError):
        splits.sample_specs("test_source", 1)
    with pytest.raises(ValueError):
        splits.sample_specs("test_combo", 1, level=0)


# ---------------------------------------------------------------- curriculum


def feed(c, reward, n):
    for _ in range(n):
        c.record(reward)


def test_adaptive_promotes_after_a_full_window_with_mean_at_least_point_seven():
    c = Curriculum("adaptive", window=10)
    feed(c, 1.0, 9)
    assert c.level == 0
    feed(c, 1.0, 1)
    assert c.level == 1
    feed(c, 0.7, 10)
    assert c.level == 2  # a mean of exactly 0.7 counts


def test_adaptive_demotes_at_point_two_and_holds_in_between():
    c = Curriculum("adaptive", start=2, window=10)
    feed(c, 0.5, 10)
    assert c.level == 2
    feed(c, 0.2, 10)
    assert c.level == 1
    feed(c, 0.0, 10)
    assert c.level == 0
    feed(c, 0.0, 10)
    assert c.level == 0  # no level below 0


def test_adaptive_stops_at_the_top_and_uses_the_last_64_episodes_by_default():
    c = Curriculum("adaptive", start=3)
    feed(c, 1.0, 64)
    assert c.level == 3
    c = Curriculum("adaptive")
    feed(c, 1.0, 63)
    assert c.level == 0
    feed(c, 1.0, 1)
    assert c.level == 1


def test_window_restarts_after_a_move():
    c = Curriculum("adaptive", window=4)
    feed(c, 1.0, 4)
    assert c.level == 1
    feed(c, 0.0, 3)
    assert c.level == 1  # old rewards were dropped, the new window is not full yet
    feed(c, 0.0, 1)
    assert c.level == 0


def test_fixed_and_uniform_modes():
    for mode in ("L2", "fixed:2", 2):
        c = Curriculum(mode)
        feed(c, 1.0, 100)
        assert c.level == 2 and c.next_level(random.Random(0)) == 2
    u = Curriculum("uniform")
    assert {u.next_level(random.Random(i)) for i in range(60)} == {0, 1, 2, 3}
    with pytest.raises(ValueError):
        Curriculum("sideways")
    with pytest.raises(ValueError):
        Curriculum("L0", levels=(3,))


def test_levels_limited_to_those_of_a_split():
    c = Curriculum("adaptive", levels=(3,))
    assert c.level == 3
