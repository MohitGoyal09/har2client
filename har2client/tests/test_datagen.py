import json
import random
import re
import uuid

from har2client.datagen import generate, schema_kind


def gen(schema, seed=1, **kw):
    return generate(schema, random.Random(seed), **kw)


def test_deterministic_for_same_rng_seed():
    s = {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
            "c": {"type": "array", "items": {"type": "number"}},
            "d": {"type": "string", "format": "uuid"},
        },
    }
    assert [gen(s, i) for i in range(20)] == [gen(s, i) for i in range(20)]
    assert gen(s, 1) != gen(s, 2)


def test_string_formats():
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", gen({"type": "string", "format": "date"}))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", gen({"type": "string", "format": "date-time"}))
    assert re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]+", gen({"type": "string", "format": "email"}))
    assert str(uuid.UUID(gen({"type": "string", "format": "uuid"}))) == gen({"type": "string", "format": "uuid"})
    assert gen({"type": "string", "format": "uri"}).startswith("https://")


def test_string_length_bounds():
    for seed in range(50):
        v = gen({"type": "string", "minLength": 5, "maxLength": 7}, seed)
        assert 5 <= len(v) <= 7
        assert len(gen({"type": "string", "maxLength": 2}, seed)) <= 2
        assert len(gen({"type": "string", "minLength": 20}, seed)) >= 20


def test_enum_and_const():
    assert {gen({"enum": ["a", "b", "c"]}, i) for i in range(60)} == {"a", "b", "c"}
    assert gen({"type": "string", "const": "fixed"}) == "fixed"


def test_number_bounds():
    for seed in range(100):
        i = gen({"type": "integer", "minimum": 3, "maximum": 5}, seed)
        assert isinstance(i, int) and 3 <= i <= 5
        n = gen({"type": "number", "minimum": 0, "maximum": 1}, seed)
        assert isinstance(n, float) and 0 <= n <= 1
        assert gen({"type": "integer", "minimum": 10}, seed) >= 10
        assert gen({"type": "integer", "maximum": -10}, seed) <= -10
        assert 4 <= gen({"type": "integer", "minimum": 3, "exclusiveMinimum": True, "maximum": 5}, seed) <= 5


def test_boolean_and_null():
    assert {gen({"type": "boolean"}, i) for i in range(30)} == {True, False}
    assert gen({"type": "null"}) is None


def test_nullable_yields_null_sometimes_but_not_when_disallowed():
    s = {"type": "string", "nullable": True}
    vals = [gen(s, i) for i in range(200)]
    assert None in vals and any(v is not None for v in vals)
    assert all(gen(s, i, allow_null=False) is not None for i in range(200))


def test_arrays_respect_min_max_and_cap():
    for seed in range(50):
        v = gen({"type": "array", "items": {"type": "integer"}}, seed)
        assert 1 <= len(v) <= 3
        v = gen({"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, seed)
        assert len(v) == 2
        v = gen({"type": "array", "items": {"type": "integer"}, "maxItems": 100}, seed)
        assert len(v) <= 3


def test_objects_required_always_optional_sometimes():
    s = {
        "type": "object",
        "required": ["r"],
        "properties": {"r": {"type": "integer"}, "o": {"type": "integer"}},
    }
    vals = [gen(s, i) for i in range(100)]
    assert all("r" in v for v in vals)
    assert any("o" in v for v in vals) and any("o" not in v for v in vals)


def test_required_not_in_properties_gets_string():
    v = gen({"type": "object", "required": ["x"], "properties": {}})
    assert isinstance(v["x"], str)


def test_additional_properties_schema():
    v = gen({"type": "object", "additionalProperties": {"type": "integer"}})
    assert 1 <= len(v) <= 2 and all(isinstance(x, int) for x in v.values())


def test_depth_cap_on_recursive_structure():
    node = {"type": "object", "required": ["children"], "properties": {}}
    node["properties"]["children"] = {"type": "array", "minItems": 3, "items": node}
    v = gen(node)
    depth = 0
    cur = v
    while cur and cur.get("children"):
        cur = cur["children"][0]
        depth += 1
    assert depth <= 4
    assert len(json.dumps(v)) < 100_000


def test_allof_merges_properties_and_required():
    s = {
        "allOf": [
            {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
            {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}, "c": {"type": "boolean"}}},
        ]
    }
    for seed in range(20):
        v = gen(s, seed)
        assert isinstance(v["a"], int) and isinstance(v["b"], str)


def test_oneof_anyof_pick_one():
    s = {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 1}, {"type": "string", "enum": ["z"]}]}
    assert {gen(s, i) for i in range(50)} == {1, "z"}
    s = {"anyOf": [{"type": "boolean"}]}
    assert isinstance(gen(s), bool)


def test_missing_type_is_inferred():
    assert isinstance(gen({"properties": {"a": {"type": "string"}}, "required": ["a"]}), dict)
    assert isinstance(gen({"items": {"type": "integer"}}), list)
    assert isinstance(gen({}), str)
    assert isinstance(gen({"description": "x"}), str)
    assert schema_kind({"properties": {}}) == "object"
    assert schema_kind({"items": {}}) == "array"
    assert schema_kind({}) is None


def test_unknown_inputs_give_short_string():
    for s in ({"type": "weird"}, None, True, {"type": "string", "format": "made-up"}):
        v = gen(s)
        assert isinstance(v, str) and 1 <= len(v) <= 12
    assert isinstance(gen({"type": ["string", "null"]}, allow_null=False), str)


def test_object_with_only_optional_props_is_never_empty():
    """Regression (canada-holidays.ca): all-optional response objects came out as {} half the time,
    so a client could copy `{}` from the HAR and pass."""
    s = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}
    vals = [gen(s, i) for i in range(200)]
    assert all(len(v) >= 1 for v in vals)
    assert any(len(v) == 2 for v in vals) and any(len(v) == 1 for v in vals)
    assert gen({"type": "object", "properties": {}}) == {}


BBCI_MIXIN = {"type": "array", "items": {"type": "string"}, "enum": ["live", "promotions"]}


def test_array_with_array_level_enum_returns_list_of_enum_values():
    """Malformed spec (enum on the array, not on items): still a list; items come from the enum."""
    for seed in range(50):
        v = gen(BBCI_MIXIN, seed)
        assert isinstance(v, list) and v
        assert all(x in ("live", "promotions") for x in v)


def test_array_enum_without_type_but_with_items_returns_list():
    for seed in range(20):
        assert isinstance(gen({"items": {"type": "string"}, "enum": ["a", "b"]}, seed), list)


def test_array_enum_of_lists_is_a_whole_value_enum():
    s = {"type": "array", "items": {"type": "string"}, "enum": [["a"], ["b", "c"]]}
    assert {tuple(gen(s, seed)) for seed in range(30)} <= {("a",), ("b", "c")}


def test_array_enum_on_items_still_works():
    s = {"type": "array", "items": {"type": "string", "enum": ["x", "y"]}}
    assert all(v in ("x", "y") for seed in range(20) for v in gen(s, seed))


def test_pin_first_option_agrees_with_schema_kind():
    from har2client.datagen import pin_first_option

    s = {"anyOf": [{"type": "integer"}, {"type": "string"}]}
    assert schema_kind(s) == "integer"
    assert all(isinstance(gen(pin_first_option(s), i), int) for i in range(40))
    assert pin_first_option({"allOf": [{"type": "string"}]}) == {"type": "string"}
    assert pin_first_option(None) is None
