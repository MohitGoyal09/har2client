"""Hidden Tier B tests: one ordered sequence run against one client instance."""

from __future__ import annotations

import random
from collections import Counter

from har2client.tierb import domains
from har2client.tierb.spec import drift_fields, extra_fields, method_name


def make_tests(spec: dict, seed: int | str) -> list[dict]:
    """[{op, method, kwargs}] in run order: list, list(filter), get x2, create, update, delete.
    Ids come from the seeded start data of the engine instance with this `seed`, and the update, delete and
    get targets are different items, so every test can be judged on its own. With a wire drift the update changes
    the drifted fields, so a client that translates only the create body fails the update."""
    domain, field = spec["domain"], spec["filter"]
    rng = random.Random(f"{seed}:tierb-tests")
    extra = extra_fields(spec)
    items = domains.initial_items(domain, seed, extra=extra)
    by_id = {item["id"]: item for item in items}
    biggest = Counter(item[field] for item in items).most_common(1)[0][0]
    get_a, get_b, target, doomed = rng.sample(sorted(by_id), 4)
    tests = [
        ("list", {}),
        ("list", {field: biggest}),
        ("get", {"id": get_a}),
        ("get", {"id": get_b}),
        ("create", {"body": domains.make_body(domain, rng, extra)}),
        ("update", {"id": target, "changes": domains.make_changes(domain, rng, by_id[target], extra=extra, prefer=drift_fields(spec))}),
        ("delete", {"id": doomed}),
    ]
    return [{"op": op, "method": method_name(spec, op), "kwargs": kwargs} for op, kwargs in tests]
