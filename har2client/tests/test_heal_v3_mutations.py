"""v3 hard-repair mutations: the ten new changes, v1/v2 pairing, release rules, identifiable labels (DESIGN-CASE3-HARD.md)."""

import itertools

import pytest

from har2client.heal import identify, mutations
from har2client.tierb import config as cfg
from har2client.tierb.spec import make_spec

HARD = [m.name for m in mutations.HARD_MUTATIONS]
DRIFTS = ["add_drift_rename", "add_drift_cents", "add_drift_epoch", "add_drift_nest", "add_drift_enum"]
PROTOCOLS = ["add_rate_limit", "add_async_create", "add_refresh_rotation", "add_idempotency_key", "add_moved"]


def v2_config(names, **over):
    """A valid v2 config that has every named change on."""
    config = {**cfg.baseline_config(), **over}
    drift = [n.removeprefix("add_drift_") for n in names if n in DRIFTS]
    config["drift"] = drift
    for n in names:
        if n in PROTOCOLS:
            config[n.removeprefix("add_")] = "on"
    if "add_refresh_rotation" in names:
        config.update(auth="bearer_login", token_expiry="off")
    return config


def test_the_ten_new_mutations_are_the_ones_in_the_design():
    assert HARD == [*DRIFTS, *PROTOCOLS]
    assert len(mutations.MUTATIONS) == 8  # the v2 set is unchanged
    assert set(HARD) <= set(mutations.BY_NAME) and set(HARD) <= set(mutations.SWITCH_GROUP)
    assert len({mutations.SWITCH_GROUP[n] for n in HARD}) == 10  # one switch each: at most one change per switch


@pytest.mark.parametrize("name", HARD)
def test_v1_is_the_v2_spec_with_that_one_switch_reverted(name):
    config = v2_config([name])
    v2 = make_spec("tickets", config, 7)
    v1 = mutations.derive_v1_spec(v2, name, 7)
    assert cfg.is_valid(v1["config"])
    changed = {k for k in cfg.EXTRA_SWITCHES if cfg.switch(v1["config"], k) != cfg.switch(v2["config"], k)}
    assert len(changed) == 1
    assert {k: v for k, v in v1.items() if k != "config"} == {k: v for k, v in v2.items() if k != "config"}  # the rest of the spec is the same


@pytest.mark.parametrize("name", HARD)
def test_a_hard_mutation_that_is_not_on_is_refused(name):
    v2 = make_spec("tickets", cfg.baseline_config(), 3)
    with pytest.raises(ValueError):
        mutations.derive_v1_spec(v2, name, 3)


def test_a_release_reverts_every_change_and_keeps_the_rest():
    names = ["add_drift_cents", "add_drift_epoch", "add_rate_limit", "add_idempotency_key"]
    config = v2_config(names, pagination="cursor", etag_update="on")
    v1 = mutations.derive_v1_spec(make_spec("invoices", config, 11), names, 11)
    assert v1["config"]["drift"] == [] and v1["config"]["rate_limit"] == "off" and v1["config"]["idempotency_key"] == "off"
    assert v1["config"]["pagination"] == "cursor" and v1["config"]["etag_update"] == "on"


def test_two_changes_of_one_drift_list_revert_independently():
    names = ["add_drift_cents", "add_drift_nest"]
    v2 = make_spec("tickets", v2_config(names), 2)
    assert mutations.derive_v1_spec(v2, ["add_drift_cents"], 2)["config"]["drift"] == ["nest"]
    assert mutations.derive_v1_spec(v2, ["add_drift_nest"], 2)["config"]["drift"] == ["cents"]


def test_refresh_rotation_v1_is_bearer_login_without_expiry_and_the_v1_client_has_no_relogin():
    v2 = make_spec("tickets", v2_config(["add_refresh_rotation"]), 4)
    v1 = mutations.derive_v1_spec(v2, "add_refresh_rotation", 4)
    assert v1["config"]["auth"] == "bearer_login" and v1["config"]["token_expiry"] == "off"
    assert mutations.v1_flaws(v1) == ("no_relogin",)


def test_a_mutation_name_is_refused_when_two_changes_share_a_switch():
    v2 = make_spec("tickets", v2_config(["add_drift_cents"]), 3)
    with pytest.raises(ValueError):
        mutations.derive_v1_spec(v2, ["add_drift_cents", "add_drift_cents"], 3)


# ------------------------------------------------------------ identifiable label


def test_label_of_a_full_scope_recording_task_is_yes():
    for names in ([n] for n in HARD):
        assert identify.label(names, "both") == "yes" and identify.label(names, "recording_only") == "yes"


def test_partial_scope_gives_generalize():
    assert identify.label(["add_drift_cents", "add_idempotency_key", "add_moved"], "both", "partial") == "generalize"
    assert identify.label(["add_rate_limit"], "recording_only", "partial") == "generalize"


def test_failure_only_shows_one_change_so_only_a_single_standard_one_is_identifiable():
    assert identify.label(["add_rate_limit"], "failure_only") == "yes"
    assert all(identify.label([n], "failure_only") == "no" for n in HARD if n != "add_rate_limit")
    assert identify.label(["add_rate_limit", "add_drift_cents"], "failure_only") == "no"


def test_partial_scope_needs_a_recording():
    with pytest.raises(ValueError):
        identify.label(["add_moved"], "failure_only", "partial")
    with pytest.raises(ValueError):
        identify.label(["add_moved"], "both", "some")


def test_identifiable_bool_covers_the_new_names_and_the_old_ones_are_unchanged():
    assert identify.identifiable("add_drift_cents", "both") is True
    assert identify.identifiable("add_idempotency_key", "failure_only") is False
    assert identify.identifiable(["add_etag", "add_envelope"], "failure_only") is True
    assert set(identify.NEEDS_RECORDING) | set(identify.FAILURE_ONLY_OK) == {m.name for m in mutations.MUTATIONS}  # v2 table unchanged
    assert set(identify.HARD_NEEDS_RECORDING) | set(identify.HARD_FAILURE_ONLY_OK) == set(HARD)


def test_every_pair_of_new_mutations_with_distinct_switches_can_be_reverted_when_valid():
    ok = 0
    for a, b in itertools.combinations(HARD, 2):
        names = [a, b]
        config = v2_config(names)
        if not cfg.is_valid(config):
            continue
        v1 = mutations.derive_v1_spec(make_spec("bookings", config, 5), names, 5)
        assert cfg.is_valid(v1["config"])
        ok += 1
    assert ok == 45
