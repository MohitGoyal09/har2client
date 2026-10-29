from pathlib import Path

from har2client import ops
from har2client.testgen import make_tests

FIX = Path(__file__).parent / "fixtures"


def table():
    return ops.normalize(ops.load_spec(FIX / "bookshelf_apikey.yaml"), "acme:bookshelf")


GOAL = ["list_books", "get_book", "create_book", "books_by_one_author"]


def test_two_calls_per_goal_op_in_order():
    tests = make_tests(table(), GOAL, seed=11)
    assert [t["method"] for t in tests] == [g for g in GOAL for _ in range(2)]


def test_required_args_always_present_optional_sometimes():
    t = table()
    tests = make_tests(t, GOAL * 15, seed=3)
    by = {}
    for x in tests:
        by.setdefault(x["method"], []).append(x["kwargs"])
    assert all({"author_id", "page"} <= set(k) for k in by["books_by_one_author"])
    assert all("book_id" in k for k in by["get_book"])
    assert all(isinstance(k["body"], dict) for k in by["create_book"])
    assert any("limit" in k for k in by["list_books"]) and any("limit" not in k for k in by["list_books"])


def test_deterministic_and_seed_sensitive():
    a, b, c = (make_tests(table(), GOAL, seed=s) for s in (1, 1, 2))
    assert a == b and a != c


def test_avoids_recorded_args_and_duplicates():
    t = table()
    avoid = [{"book_id": "abcd"}]
    for seed in range(20):
        tests = make_tests(t, ["get_book"], seed=seed, avoid=avoid)
        assert all(x["kwargs"] not in avoid for x in tests)
        assert tests[0]["kwargs"] != tests[1]["kwargs"]


def test_kwargs_are_json_serializable():
    import json

    json.dumps(make_tests(table(), GOAL, seed=4))


# ---- argument type check ---------------------------------------------------


def _bbci_table():
    t = table()
    op = next(o for o in t["operations"] if o["op_id"] == "list_books")
    op["params"] = [
        {"name": "mixin", "py_name": "mixin", "in": "query", "required": True,
         "schema": {"type": "array", "items": {"type": "string"}, "enum": ["live", "promotions"]}},
    ]
    return t


def test_bbci_style_array_enum_param_gets_lists_in_tests():
    t = _bbci_table()
    for seed in range(10):
        for test in make_tests(t, ["list_books"], seed=seed):
            assert isinstance(test["kwargs"]["mixin"], list)


def test_check_arg_types_accepts_matching_values():
    from har2client.testgen import check_arg_types

    t = table()
    check_arg_types(t, "list_books", {"limit": 3, "genre": "fiction", "tags": ["a"]})
    check_arg_types(t, "create_book", {"body": {"title": "x"}})


def test_check_arg_types_rejects_each_mismatch():
    import pytest

    from har2client.testgen import ArgTypeMismatch, check_arg_types

    t = table()
    for kwargs in ({"tags": "a"}, {"limit": "3"}, {"limit": True}, {"genre": 1}, {"limit": 2.5}):
        with pytest.raises(ArgTypeMismatch):
            check_arg_types(t, "list_books", kwargs)
    with pytest.raises(ArgTypeMismatch):
        check_arg_types(t, "create_book", {"body": "x"})


def test_check_arg_types_accepts_int_for_float_not_bool():
    import pytest

    from har2client.testgen import ArgTypeMismatch, check_arg_types

    t = table()
    op = next(o for o in t["operations"] if o["op_id"] == "list_books")
    op["params"].append({"name": "w", "py_name": "w", "in": "query", "required": False, "schema": {"type": "number"}})
    check_arg_types(t, "list_books", {"w": 2})
    check_arg_types(t, "list_books", {"w": 2.5})
    with pytest.raises(ArgTypeMismatch):
        check_arg_types(t, "list_books", {"w": False})


# ---- coverage rule ----------------------------------------------------------


def _optional_names(op):
    return {p["py_name"] for p in op["params"] if not p["required"] and p["in"] in ("query", "path")}


def _typed_names(op, kind):
    return {p["py_name"] for p in op["params"] if p["schema"].get("type") == kind and p["in"] in ("query", "path")}


def test_every_optional_param_is_covered_per_op_with_two_calls_each():
    t = table()
    by_id = {o["op_id"]: o for o in t["operations"]}
    for seed in range(40):
        tests = make_tests(t, GOAL, seed=seed)
        assert [x["method"] for x in tests] == [g for g in GOAL for _ in range(2)]
        for op_id in GOAL:
            used = set().union(*(set(x["kwargs"]) for x in tests if x["method"] == op_id))
            assert _optional_names(by_id[op_id]) <= used, (seed, op_id)


def test_ops_with_bool_and_int_params_get_a_bool_and_an_int_value():
    t = table()
    by_id = {o["op_id"]: o for o in t["operations"]}
    seen = {"boolean": 0, "integer": 0}
    for seed in range(40):
        tests = make_tests(t, GOAL, seed=seed)
        for op_id in GOAL:
            kwargs = [x["kwargs"] for x in tests if x["method"] == op_id]
            for kind, pytype in (("boolean", bool), ("integer", int)):
                names = _typed_names(by_id[op_id], kind)
                if not names:
                    continue
                seen[kind] += 1
                assert any(isinstance(k[n], pytype) and (kind == "boolean" or not isinstance(k[n], bool)) for k in kwargs for n in names if n in k)
    assert seen["boolean"] and seen["integer"]  # the fixture really has both kinds


def test_coverage_calls_are_still_deterministic_and_type_checked():
    from har2client.testgen import check_arg_types

    t = table()
    a, b = make_tests(t, GOAL, seed=9), make_tests(t, GOAL, seed=9)
    assert a == b
    for x in a:
        check_arg_types(t, x["method"], x["kwargs"])


def test_coverage_still_avoids_recorded_args():
    t = table()
    avoid = [{"author_id": "abcd", "page": 1}]
    for seed in range(20):
        tests = make_tests(t, ["books_by_one_author"], seed=seed, avoid=avoid)
        assert all(x["kwargs"] not in avoid for x in tests)
