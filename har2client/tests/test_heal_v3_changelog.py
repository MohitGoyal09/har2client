"""The visible CHANGELOG.md of a v3 task: none, complete, incomplete (omits one change), misleading (one change stated wrongly)."""

import pytest

from har2client.heal import changelog
from har2client.heal.mutations import HARD_MUTATIONS
from har2client.tierb import config as cfg
from har2client.tierb.spec import make_spec

ALL = [m.name for m in HARD_MUTATIONS]


def config_of(names):
    c = {**cfg.baseline_config(), "drift": [n.removeprefix("add_drift_") for n in names if n.startswith("add_drift_")]}
    for n in names:
        if not n.startswith("add_drift_"):
            c[n.removeprefix("add_")] = "on"
    if "add_refresh_rotation" in names:
        c.update(auth="bearer_login", token_expiry="off")
    return c


def spec_of(names, domain="invoices", seed=9):
    return make_spec(domain, config_of(names), seed)


def facts_of(names, spec):
    return {n: changelog.truth(n, spec) for n in names}


def test_none_gives_no_file():
    assert changelog.render(spec_of(["add_moved"]), ["add_moved"], "none", 1) is None


def test_levels_are_validated():
    with pytest.raises(ValueError):
        changelog.render(spec_of(["add_moved"]), ["add_moved"], "bogus", 1)


@pytest.mark.parametrize("name", ALL)
def test_complete_changelog_states_each_change_with_its_true_facts(name):
    spec = spec_of([name])
    text, info = changelog.render(spec, [name], "complete", 3)
    assert info == {"level": "complete", "omitted": None, "misleading": None}
    for fact in changelog.truth(name, spec):
        assert fact in text, (name, fact)


def test_it_reads_like_a_release_note():
    names = ["add_drift_cents", "add_rate_limit", "add_idempotency_key"]
    text, _ = changelog.render(spec_of(names), names, "complete", 5)
    assert text.startswith("# ") and "Changelog" in text
    assert "### Breaking changes" in text and "## " in text
    import re
    assert re.search(r"## \d+\.\d+\.\d+ \(2026-\d\d-\d\d\)", text)


def test_deterministic_and_seed_dependent():
    names = ["add_drift_cents", "add_rate_limit", "add_idempotency_key"]
    spec = spec_of(names)
    assert changelog.render(spec, names, "complete", 5) == changelog.render(spec, names, "complete", 5)
    assert changelog.render(spec, names, "complete", 5)[0] != changelog.render(spec, names, "complete", 6)[0]


def test_incomplete_omits_exactly_one_change():
    names = ["add_drift_cents", "add_drift_epoch", "add_rate_limit", "add_moved"]
    spec = spec_of(names)
    text, info = changelog.render(spec, names, "incomplete", 4)
    assert info["level"] == "incomplete" and info["omitted"] in names and info["misleading"] is None
    for n in names:
        shown = all(f in text for f in changelog.truth(n, spec))
        assert shown == (n != info["omitted"]), n


@pytest.mark.parametrize("name", [n for n in ALL if n != "add_drift_enum"])
def test_misleading_states_one_change_wrongly_and_the_wrong_fact_differs_from_the_truth(name):
    spec = spec_of([name])
    text, info = changelog.render(spec, [name], "misleading", 2)
    mis = info["misleading"]
    assert mis["change"] == name and mis["kind"] in changelog.MISLEADING_KINDS
    assert mis["stated"] != mis["truth"] and mis["stated"] in text
    assert mis["truth"] not in text


def test_misleading_changes_only_the_target_change():
    names = ["add_drift_cents", "add_rate_limit", "add_idempotency_key"]
    spec = spec_of(names)
    text, info = changelog.render(spec, names, "misleading", 7)
    target = info["misleading"]["change"]
    for n in names:
        if n != target:
            assert all(f in text for f in changelog.truth(n, spec))


def test_misleading_needs_an_eligible_change():
    names = ["add_drift_enum"]
    with pytest.raises(ValueError):
        changelog.render(spec_of(names), names, "misleading", 1)


def test_misleading_kinds_are_header_field_unit_path():
    assert set(changelog.MISLEADING_KINDS) == {"header", "field", "unit", "path"}
    kinds = {}
    for n in ALL:
        if n != "add_drift_enum":
            kinds[n] = changelog.render(spec_of([n]), [n], "misleading", 1)[1]["misleading"]["kind"]
    assert set(kinds.values()) == set(changelog.MISLEADING_KINDS)


def test_the_text_holds_no_credential_or_test_data():
    names = ["add_refresh_rotation", "add_drift_nest"]
    text, _ = changelog.render(spec_of(names), names, "complete", 3)
    assert "pw_" not in text and "sk_" not in text
