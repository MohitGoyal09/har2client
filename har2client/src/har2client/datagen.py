"""Generate a valid value for a JSON-schema subset. Deterministic for a given rng."""

from __future__ import annotations

import math
import random
import string
import uuid
from datetime import date, datetime, timedelta
from typing import Any

MAX_DEPTH = 4
MAX_ITEMS = 3
OPTIONAL_PROP_P = 0.5
NULL_P = 0.2
_SHORT_MIN, _SHORT_MAX = 3, 10
_NUM_SPAN = 1000


def _short_str(rng: random.Random, lo: int = _SHORT_MIN, hi: int = _SHORT_MAX) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(lo, hi)))


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        if key == "properties":
            out["properties"] = {**base.get("properties", {}), **value}
        elif key == "required":
            out["required"] = list(dict.fromkeys([*base.get("required", []), *value]))
        else:
            out[key] = value
    return out


def _flatten(schema: dict, rng: random.Random | None) -> dict:
    """Merge allOf and pick one of oneOf/anyOf (first option when rng is None)."""
    s = dict(schema)
    for sub in s.pop("allOf", None) or []:
        if isinstance(sub, dict):
            s = _merge(s, _flatten(sub, rng))
    for key in ("oneOf", "anyOf"):
        options = [o for o in (s.pop(key, None) or []) if isinstance(o, dict)]
        if options:
            pick = rng.choice(options) if rng is not None else options[0]
            s = _merge(s, _flatten(pick, rng))
    return s


def pin_first_option(schema: Any) -> Any:
    """`schema` with allOf merged and oneOf/anyOf fixed to the first option, the same choice
    `schema_kind` makes. Use it for arguments whose Python type goal.md declares."""
    return _flatten(schema, None) if isinstance(schema, dict) else schema


def _kind_of(s: dict) -> str | None:
    t = s.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if isinstance(t, str):
        return t
    if "properties" in s or "additionalProperties" in s:
        return "object"
    if "items" in s:
        return "array"
    enum = s.get("enum")
    if enum:
        first = enum[0]
        if isinstance(first, bool):
            return "boolean"
        if isinstance(first, int):
            return "integer"
        if isinstance(first, float):
            return "number"
    return None


def schema_kind(schema: Any) -> str | None:
    """Best-effort JSON type of a schema without randomness; None when unknown."""
    if not isinstance(schema, dict):
        return None
    return _kind_of(_flatten(schema, None))


def _bounds(s: dict) -> tuple[float | None, float | None]:
    lo, hi = s.get("minimum"), s.get("maximum")
    ex_lo, ex_hi = s.get("exclusiveMinimum"), s.get("exclusiveMaximum")
    if ex_lo is True and lo is not None:
        lo = lo + 1 if s.get("type") == "integer" else lo + 0.01
    elif isinstance(ex_lo, (int, float)) and not isinstance(ex_lo, bool):
        lo = ex_lo + 1 if s.get("type") == "integer" else ex_lo + 0.01
    if ex_hi is True and hi is not None:
        hi = hi - 1 if s.get("type") == "integer" else hi - 0.01
    elif isinstance(ex_hi, (int, float)) and not isinstance(ex_hi, bool):
        hi = ex_hi - 1 if s.get("type") == "integer" else ex_hi - 0.01
    return lo, hi


def _range(s: dict) -> tuple[float, float]:
    lo, hi = _bounds(s)
    if lo is None and hi is None:
        return 0, _NUM_SPAN
    if lo is None:
        return hi - _NUM_SPAN, hi
    if hi is None:
        return lo, lo + _NUM_SPAN
    return lo, hi


def _gen_integer(s: dict, rng: random.Random) -> int:
    lo, hi = _range(s)
    lo_i, hi_i = math.ceil(lo), math.floor(hi)
    if lo_i > hi_i:
        return lo_i
    value = rng.randint(lo_i, hi_i)
    step = s.get("multipleOf")
    if isinstance(step, int) and step > 1:
        snapped = (value // step) * step
        value = snapped if snapped >= lo_i else snapped + step
    return value


def _gen_number(s: dict, rng: random.Random) -> float:
    lo, hi = _range(s)
    if lo > hi:
        return float(lo)
    return min(max(round(rng.uniform(lo, hi), 2), lo), hi)


def _gen_string(s: dict, rng: random.Random) -> str:
    fmt = s.get("format")
    if fmt == "date":
        return (date(2020, 1, 1) + timedelta(days=rng.randrange(2000))).isoformat()
    if fmt == "date-time":
        stamp = datetime(2020, 1, 1) + timedelta(seconds=rng.randrange(2000 * 86400))
        return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    if fmt == "email":
        return f"{_short_str(rng)}@example.com"
    if fmt == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if fmt in ("uri", "url"):
        return f"https://example.com/{_short_str(rng)}"
    min_len = s.get("minLength") or 0
    max_len = s.get("maxLength")
    lo = max(min_len, min(_SHORT_MIN, max_len) if max_len is not None else _SHORT_MIN)
    hi = max_len if max_len is not None else max(_SHORT_MAX, lo)
    hi = max(hi, lo)
    return _short_str(rng, lo, hi)


def _gen_array(s: dict, rng: random.Random, depth: int, allow_null: bool) -> list:
    if depth >= MAX_DEPTH:
        return []
    items = s.get("items")
    if isinstance(items, list):
        items = items[0] if items else {}
    if not isinstance(items, dict):
        items = {}
    hi = min(s.get("maxItems", MAX_ITEMS), MAX_ITEMS)
    lo = min(s["minItems"] if "minItems" in s else 1, MAX_ITEMS, hi)
    count = rng.randint(max(lo, 0), max(hi, 0))
    return [generate(items, rng, depth + 1, allow_null=allow_null) for _ in range(count)]


def _gen_object(s: dict, rng: random.Random, depth: int, allow_null: bool) -> dict:
    if depth >= MAX_DEPTH:
        return {}
    props = s.get("properties") or {}
    required = list(s.get("required") or [])
    out: dict[str, Any] = {}
    for name, sub in props.items():
        if name in required or rng.random() < OPTIONAL_PROP_P:
            out[name] = generate(sub, rng, depth + 1, allow_null=allow_null)
    for name in required:
        if name not in props:
            out[name] = _short_str(rng)
    if props and not out:  # never `{}` for a schema that has properties
        name = rng.choice(list(props))
        out[name] = generate(props[name], rng, depth + 1, allow_null=allow_null)
    extra = s.get("additionalProperties")
    if not props and isinstance(extra, dict) and extra:
        for _ in range(rng.randint(1, 2)):
            out[_short_str(rng)] = generate(extra, rng, depth + 1, allow_null=allow_null)
    return out


def _array_enum_to_items(s: dict) -> dict:
    """Specs sometimes put `enum` on an array instead of its items. Read it as the item enum,
    so the value is still a list (unless every enum value is already a list)."""
    enum = s.get("enum")
    if not enum or _kind_of(s) != "array" or all(isinstance(v, list) for v in enum):
        return s
    items = s.get("items")
    items = items[0] if isinstance(items, list) and items else items
    out = {k: v for k, v in s.items() if k != "enum"}
    out["items"] = {**(items if isinstance(items, dict) else {}), "enum": enum}
    return out


def generate(schema: Any, rng: random.Random, depth: int = 0, *, allow_null: bool = True) -> Any:
    """Return a value valid for `schema`. Unknown shapes give a short string."""
    if not isinstance(schema, dict):
        return _short_str(rng)
    s = _flatten(schema, rng)
    if "const" in s:
        return s["const"]
    s = _array_enum_to_items(s)
    if s.get("enum"):
        return rng.choice(s["enum"])
    if allow_null and s.get("nullable") and rng.random() < NULL_P:
        return None
    kind = _kind_of(s)
    if kind == "object":
        return _gen_object(s, rng, depth, allow_null)
    if kind == "array":
        return _gen_array(s, rng, depth, allow_null)
    if kind == "integer":
        return _gen_integer(s, rng)
    if kind == "number":
        return _gen_number(s, rng)
    if kind == "boolean":
        return rng.random() < 0.5
    if kind == "null":
        return None
    if kind == "string" or kind is None:
        return _gen_string(s, rng)
    return _short_str(rng)
