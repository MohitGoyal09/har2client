"""The hidden test list of a v3 task: 17 calls, every operation at least twice, drift fields in the writes."""

from collections import Counter

import pytest

from har2client.heal import hardtests
from har2client.tierb import config as cfg
from har2client.tierb import domains
from har2client.tierb.spec import OPS, extra_fields, make_spec


def spec_of(domain="tickets", pagination="offset", drift=("cents", "enum", "nest"), seed=5, **over):
    return make_spec(domain, {**cfg.baseline_config(), "pagination": pagination, "drift": list(drift), **over}, seed)


def test_at_least_fifteen_tests_and_every_operation_twice():
    tests = hardtests.make_hard_tests(spec_of(), 42)
    assert len(tests) >= hardtests.MIN_TESTS == 15
    counts = Counter(t["op"] for t in tests)
    assert set(counts) == set(OPS) and min(counts.values()) >= 2


def test_order_is_reads_then_writes_so_the_grader_can_compare_with_the_start_data():
    ops = [t["op"] for t in hardtests.make_hard_tests(spec_of(), 42)]
    assert ops == sorted(ops, key=lambda o: ["list", "get", "create", "update", "delete"].index(o))


def test_targets_are_distinct_items_of_the_start_data():
    spec, seed = spec_of(), 42
    tests = hardtests.make_hard_tests(spec, seed)
    ids = [t["kwargs"]["id"] for t in tests if t["op"] in ("get", "update", "delete")]
    assert len(ids) == len(set(ids))
    initial = {i["id"] for i in domains.initial_items(spec["domain"], seed, extra=extra_fields(spec))}
    assert set(ids) <= initial


def test_updates_cover_every_drifted_field():
    spec = spec_of(drift=("cents", "enum", "nest", "rename"))
    changed = set()
    for t in hardtests.make_hard_tests(spec, 42):
        if t["op"] == "update":
            changed |= set(t["kwargs"]["changes"])
    d = spec["drift"]
    assert {d["rename"]["field"], d["cents"]["field"], d["enum"]["field"]} <= changed
    assert {d["nest"]["name_field"], d["nest"]["email_field"]} & changed


def test_creates_send_every_field():
    spec = spec_of()
    fields = set(domains.writable_fields(spec["domain"], extra_fields(spec)))
    for t in hardtests.make_hard_tests(spec, 42):
        if t["op"] == "create":
            assert set(t["kwargs"]["body"]) == fields


def test_deterministic_and_seed_dependent():
    spec = spec_of()
    assert hardtests.make_hard_tests(spec, 7) == hardtests.make_hard_tests(spec, 7)
    assert hardtests.make_hard_tests(spec, 7) != hardtests.make_hard_tests(spec, 8)


@pytest.mark.parametrize("pagination", ["offset", "cursor", "link_header"])
def test_a_paged_api_has_two_list_tests_that_need_two_pages(pagination):
    spec = spec_of(pagination=pagination)
    seed = hardtests.pick_seed(spec, range(100, 400))
    items = domains.initial_items(spec["domain"], seed, extra=extra_fields(spec))
    size = spec["page"]["size"]
    paged = 0
    for t in hardtests.make_hard_tests(spec, seed):
        if t["op"] != "list":
            continue
        flt = t["kwargs"].get(spec["filter"])
        n = len([i for i in items if flt is None or i[spec["filter"]] == flt])
        paged += n > size
    assert paged >= 2
    assert hardtests.multi_page_lists(spec, seed) >= 2


def test_pick_seed_returns_the_first_seed_that_qualifies_and_refuses_when_none_does():
    spec = spec_of()
    bad = [s for s in range(300) if hardtests.multi_page_lists(spec, s) < 2]
    good = next(s for s in range(300) if hardtests.multi_page_lists(spec, s) >= 2)
    assert bad, "some seeds must fail, or the rule is empty"
    assert hardtests.pick_seed(spec, [*bad[:3], good, 999]) == good
    with pytest.raises(ValueError):
        hardtests.pick_seed(spec, bad[:3])


def test_no_pagination_needs_no_second_page():
    assert hardtests.multi_page_lists(spec_of(pagination="none"), 3) >= 2
