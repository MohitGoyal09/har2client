import random
from collections import Counter

import pytest

from har2client.tierb import domains


def test_five_domains_with_a_filter_field_among_5_to_8_typed_fields():
    assert len(domains.DOMAINS) == 5
    for name, info in domains.DOMAINS.items():
        names = [f[0] for f in info["fields"]]
        assert info["filter"] in names and 5 <= len(names) + 2 <= 8 + 2
        assert info["singular"] and info["id_prefix"]


@pytest.mark.parametrize("domain", list(domains.DOMAINS))
def test_initial_items_are_seeded_unique_and_sized(domain):
    a, b, c = (domains.initial_items(domain, s) for s in (1, 1, 2))
    assert a == b and a != c
    assert domains.MIN_ITEMS <= len(a) <= domains.MAX_ITEMS
    assert len({i["id"] for i in a}) == len(a)
    assert all(set(i) == {"id", "created_at", *domains.writable_fields(domain)} for i in a)
    assert [i["created_at"] for i in a] == sorted(i["created_at"] for i in a)


@pytest.mark.parametrize("domain", list(domains.DOMAINS))
def test_the_biggest_filter_group_usually_spans_two_pages(domain):
    field = domains.DOMAINS[domain]["filter"]
    wide = sum(Counter(i[field] for i in domains.initial_items(domain, s)).most_common(1)[0][1] > 10 for s in range(40))
    assert wide >= 28


@pytest.mark.parametrize("domain", list(domains.DOMAINS))
def test_changes_differ_from_current_values_and_body_has_every_field(domain):
    rng = random.Random(3)
    item = domains.initial_items(domain, 9)[0]
    changes = domains.make_changes(domain, rng, item)
    assert len(changes) == 2 and all(item[k] != v for k, v in changes.items())
    assert set(domains.make_body(domain, rng)) == set(domains.writable_fields(domain))
