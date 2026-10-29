"""The hidden tests of a v3 hard repair task (DESIGN-CASE3-HARD.md): 17 calls in one order, 3 lists, 4 gets, 3 creates, 4 updates,
3 deletes.

Reads come first and the write targets are different items, so the grader can compare each result with the start data of the
server (`tierb.grader` judges lists and gets against it). Every call returns or sends the changed fields, so each wire drift is
checked on every operation: a list, a get, a create and an update return the whole item, and a create and an update send
drifted fields. The updates take turns in changing each drifted field. With paging, two of the three lists need two pages or
more (`pick_seed` picks a test seed where the most common filter value has more items than one page).
Which change each test needs is not decided here: the builder derives it from the necessity runs (`heal.hard`)."""

from __future__ import annotations

import random
from collections import Counter
from typing import Iterable

from har2client.tierb import domains
from har2client.tierb.config import switch
from har2client.tierb.spec import extra_fields, method_name

MIN_TESTS = 15
N_LISTS, N_GETS, N_CREATES, N_UPDATES, N_DELETES = 3, 4, 3, 4, 3
MIN_PAGED_LISTS = 2


def _filter_values(spec: dict, items: list[dict]) -> list[str]:
    """Filter values by number of items, most common first (ties in alphabetical order, so the list is stable)."""
    counts = Counter(item[spec["filter"]] for item in items)
    return sorted(counts, key=lambda v: (-counts[v], v))


def _drift_groups(spec: dict) -> list[list[str]]:
    """Canonical field names that each writable drift kind changes (`epoch` changes a response only), one group per kind."""
    d = {k: v for k, v in spec.get("drift", {}).items() if k in switch(spec["config"], "drift")}
    groups = [[d[k]["field"]] for k in ("rename", "cents", "enum") if k in d]
    return groups + ([[d["nest"]["name_field"], d["nest"]["email_field"]]] if "nest" in d else [])


def make_hard_tests(spec: dict, seed: int | str) -> list[dict]:
    """[{op, method, kwargs}] in run order. Ids come from the seeded start data of the engine instance with this `seed`."""
    domain, field = spec["domain"], spec["filter"]
    rng = random.Random(f"{seed}:hard-tests")
    extra = extra_fields(spec)
    items = domains.initial_items(domain, seed, extra=extra)
    by_id = {item["id"]: item for item in items}
    values = _filter_values(spec, items)
    ids = rng.sample(sorted(by_id), N_GETS + N_UPDATES + N_DELETES)
    get_ids, update_ids, delete_ids = ids[:N_GETS], ids[N_GETS:N_GETS + N_UPDATES], ids[N_GETS + N_UPDATES:]
    groups = _drift_groups(spec)
    calls: list[tuple[str, dict]] = [("list", {})]
    calls += [("list", {field: v}) for v in values[:N_LISTS - 1]]
    calls += [("get", {"id": i}) for i in get_ids]
    calls += [("create", {"body": domains.make_body(domain, rng, extra)}) for _ in range(N_CREATES)]
    for n, target in enumerate(update_ids):
        prefer = groups[n % len(groups)] if groups else []
        calls.append(("update", {"id": target, "changes": domains.make_changes(domain, rng, by_id[target], extra=extra, prefer=prefer)}))
    calls += [("delete", {"id": i}) for i in delete_ids]
    return [{"op": op, "method": method_name(spec, op), "kwargs": kwargs} for op, kwargs in calls]


def multi_page_lists(spec: dict, seed: int | str) -> int:
    """How many of the list tests of this test seed need more than one page (all of them when the API does not page)."""
    items = domains.initial_items(spec["domain"], seed, extra=extra_fields(spec))
    tests = [t for t in make_hard_tests(spec, seed) if t["op"] == "list"]
    if spec["config"]["pagination"] == "none":
        return len(tests)
    field, size = spec["filter"], spec["page"]["size"]
    counts = [len([i for i in items if t["kwargs"].get(field) in (None, i[field])]) for t in tests]
    return sum(n > size for n in counts)


def pick_seed(spec: dict, seeds: Iterable[int]) -> int:
    """The first seed whose tests have `MIN_PAGED_LISTS` list tests that need two pages."""
    for seed in seeds:
        if multi_page_lists(spec, seed) >= MIN_PAGED_LISTS:
            return seed
    raise ValueError("no test seed with two multi-page list tests")
