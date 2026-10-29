"""The five invented resource domains: field types, value pools and seeded item generation."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

MIN_ITEMS, MAX_ITEMS = 25, 45
FILTER_WEIGHTS = (0.42, 0.25, 0.15, 0.10, 0.08)  # one value dominates, so a filtered list spans 2+ pages
_CITIES = ["Lisbon", "Osaka", "Tallinn", "Nairobi", "Quito", "Gdansk", "Perth", "Valparaiso", "Mumbai", "Reykjavik", "Hanoi", "Ghent"]
_PEOPLE = ["Ines Varga", "Tomas Ito", "Amara Okoye", "Lena Brandt", "Ravi Menon", "Sofia Duarte", "Kofi Mensah", "Elin Berg", "Marta Lindqvist", "Dev Patel"]
_COMPANIES = ["Halden Works", "Corvid Labs", "Pinegate Foods", "Mosaic Freight", "Lumen & Sons", "Brightwater Co", "Ostrava Tools", "Nimbus Dental"]
_PHRASES = [
    "Label printer jams on large batches", "Export fails for archived projects", "Dashboard shows stale totals",
    "Cannot reset two-factor device", "Webhook retries stop after day two", "Invoice PDF misses the tax line",
    "Search ignores accented names", "Bulk import times out", "Mobile app logs users out", "Role change not applied",
]
_MAIL_HOSTS = ["mail.test", "example.org", "corp.test"]
_BRAND_START = ["vel", "quo", "tra", "nim", "bro", "kes", "lum", "ori", "zan", "pel", "mir", "dax", "sol", "fen", "hav", "tor"]
_BRAND_END = ["rix", "lo", "vo", "ra", "na", "ly", "ton", "dex", "ium", "ora", "ik", "ant"]
TLDS = ["com", "io", "co", "net", "app", "dev"]

# field spec: (name, kind, *args). kinds: enum, city, person, company, phrase, int, float, bool, date, code
# Kinds only for extra fields (`spec.make_spec`, drift kinds `cents` and `nest`): money (a float in whole cents), email.
DOMAINS: dict[str, dict[str, Any]] = {
    "shipments": {
        "singular": "shipment", "id_prefix": "SHP", "filter": "status",
        "fields": [
            ("carrier", "enum", ["Kestrel Lines", "Orbitrail", "Tidewater Express", "Northwind Freight"]),
            ("status", "enum", ["pending", "in_transit", "delivered", "returned"]),
            ("origin", "city"), ("destination", "city"),
            ("weight_kg", "float", 0.5, 480.0), ("fragile", "bool"), ("eta_days", "int", 1, 21),
        ],
    },
    "tickets": {
        "singular": "ticket", "id_prefix": "TCK", "filter": "priority",
        "fields": [
            ("title", "phrase"), ("priority", "enum", ["low", "normal", "high", "urgent"]),
            ("assignee", "person"), ("queue", "enum", ["billing", "onboarding", "platform", "security"]),
            ("estimate_hours", "int", 1, 40), ("customer_visible", "bool"),
        ],
    },
    "invoices": {
        "singular": "invoice", "id_prefix": "INV", "filter": "state",
        "fields": [
            ("customer", "company"), ("state", "enum", ["draft", "sent", "paid", "void"]),
            ("amount_cents", "int", 1500, 950000), ("currency", "enum", ["USD", "EUR", "GBP", "INR"]),
            ("due_date", "date"), ("memo", "phrase"),
        ],
    },
    "bookings": {
        "singular": "booking", "id_prefix": "BKG", "filter": "room",
        "fields": [
            ("guest", "person"), ("room", "enum", ["Aurora", "Birch", "Cedar", "Dune", "Ember"]),
            ("starts_on", "date"), ("nights", "int", 1, 14), ("party_size", "int", 1, 8), ("confirmed", "bool"),
        ],
    },
    "samples": {
        "singular": "sample", "id_prefix": "SMP", "filter": "lab",
        "fields": [
            ("assay", "enum", ["pcr", "elisa", "mass_spec", "cytometry"]),
            ("lab", "enum", ["north", "harbor", "summit", "delta"]),
            ("volume_ul", "float", 5.0, 900.0), ("collected_on", "date"), ("batch", "code"), ("hazardous", "bool"),
        ],
    },
}
_EPOCH = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)


def make_brand(rng: random.Random) -> str:
    return rng.choice(_BRAND_START) + rng.choice(_BRAND_END)


def writable_fields(domain: str, extra: Sequence = ()) -> list[str]:
    """Field names of a domain's items; `extra` (field specs a spec adds for the wire-drift switches) come last."""
    return [f[0] for f in (*DOMAINS[domain]["fields"], *extra)]


def enum_fields(domain: str) -> dict[str, list[str]]:
    return {f[0]: list(f[2]) for f in DOMAINS[domain]["fields"] if f[1] == "enum"}


def _value(field: tuple, rng: random.Random) -> Any:
    kind, args = field[1], field[2:]
    if kind == "enum":
        return rng.choice(args[0])
    if kind == "city":
        return rng.choice(_CITIES)
    if kind == "person":
        return rng.choice(_PEOPLE)
    if kind == "company":
        return rng.choice(_COMPANIES)
    if kind == "phrase":
        return rng.choice(_PHRASES)
    if kind == "int":
        return rng.randint(args[0], args[1])
    if kind == "float":
        return round(rng.uniform(args[0], args[1]), 1)
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "date":
        return (datetime(2026, 1, 1) + timedelta(days=rng.randint(0, 364))).date().isoformat()
    if kind == "code":
        return f"B-{rng.randint(1000, 9999)}"
    if kind == "money":  # whole cents, so the value survives a round trip through integer cents
        return rng.randint(100, 99999) / 100
    if kind == "email":
        return f"{rng.choice(_PEOPLE).lower().replace(' ', '.')}@{rng.choice(_MAIL_HOSTS)}"
    raise ValueError(f"unknown field kind {kind}")


def make_body(domain: str, rng: random.Random, extra: Sequence = ()) -> dict:
    """A full create body: every writable field."""
    return {f[0]: _value(f, rng) for f in (*DOMAINS[domain]["fields"], *extra)}


def make_changes(domain: str, rng: random.Random, item: dict, count: int = 2, extra: Sequence = (), prefer: Sequence[str] = ()) -> dict:
    """`count` fields with new values (each differs from the item's current value). Fields named in `prefer` come first
    (a stable sort after the shuffle: no extra random draws, and no change at all when `prefer` is empty)."""
    fields = [*DOMAINS[domain]["fields"], *extra]
    rng.shuffle(fields)
    fields.sort(key=lambda f: f[0] not in prefer)
    changes: dict[str, Any] = {}
    for field in fields[:count]:
        for _ in range(50):
            value = _value(field, rng)
            if value != item[field[0]]:
                changes[field[0]] = value
                break
    return changes


def item_count(seed: int | str) -> int:
    return random.Random(f"{seed}:count").randint(MIN_ITEMS, MAX_ITEMS)


def make_id(domain: str, rng: random.Random) -> str:
    return f"{DOMAINS[domain]['id_prefix']}-{rng.randint(10000, 99999)}"


def initial_items(domain: str, seed: int | str, n_items: int | None = None, extra: Sequence = ()) -> list[dict]:
    """The seeded starting data of an engine instance: same (domain, seed) -> same items. The `extra` fields draw from
    their own random stream and come last, so the base fields and the ids are the same with or without them."""
    rng = random.Random(f"{seed}:items")
    n = n_items or item_count(seed)
    info = DOMAINS[domain]
    flt = next(f for f in info["fields"] if f[0] == info["filter"])
    weights = list(FILTER_WEIGHTS[: len(flt[2])])
    rng.shuffle(weights)
    items: list[dict] = []
    seen: set[str] = set()
    when = _EPOCH + timedelta(minutes=rng.randint(0, 600))
    for _ in range(n):
        item_id = make_id(domain, rng)
        while item_id in seen:
            item_id = make_id(domain, rng)
        seen.add(item_id)
        when += timedelta(minutes=rng.randint(7, 140))
        item: dict[str, Any] = {"id": item_id, "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}
        for field in info["fields"]:
            item[field[0]] = rng.choices(flt[2], weights)[0] if field is flt else _value(field, rng)
        items.append(item)
    extra_rng = random.Random(f"{seed}:items-extra")
    for item in items if extra else ():
        item.update({f[0]: _value(f, extra_rng) for f in extra})
    return items
