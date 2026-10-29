import json
import re
from pathlib import Path

import pytest

from har2client import build_task, ops

FIX = Path(__file__).parent / "fixtures"


def spec(name="bookshelf_apikey.yaml"):
    return ops.load_spec(FIX / name)


@pytest.fixture(scope="module")
def task_dir(tmp_path_factory):
    return build_task.build_task(spec(), "acme:bookshelf", tmp_path_factory.mktemp("tasks"), noise=0.7, seed=4)


def load(task_dir, name):
    return json.loads((task_dir / name).read_text())


def test_task_layout_and_ids(task_dir):
    assert task_dir.name == "acme_bookshelf"
    for f in ("goal.md", "session.har", "task.json", "op_table.json", "tests.json"):
        assert (task_dir / f).is_file()


def test_task_json_fields(task_dir):
    t = load(task_dir, "task.json")
    assert t["task_id"] == "acme_bookshelf" and t["api_id"] == "acme:bookshelf"
    assert 1 <= len(t["goal_ops"]) <= 8
    assert t["seeds"]["recording"] != t["seeds"]["test"]
    assert re.fullmatch(r"sk_[0-9a-f]{24}", t["recording_secret"])
    assert re.fullmatch(r"sk_[0-9a-f]{24}", t["test_secret"])
    assert t["recording_secret"] != t["test_secret"]
    assert t["noise_ratio"] == 0.7
    assert "partial_coverage" in t["features"] and "noise" in t["features"]
    assert "auth:apiKey" in t["features"]


def test_tests_cover_goal_ops_two_each(task_dir):
    t, tests = load(task_dir, "task.json"), load(task_dir, "tests.json")
    assert [x["method"] for x in tests] == [g for g in t["goal_ops"] for _ in range(2)]


def test_har_is_clean_and_real_looking(task_dir):
    t = load(task_dir, "task.json")
    text = (task_dir / "session.har").read_text()
    assert "127.0.0.1" not in text
    assert "api.bookshelf.example" in text
    assert t["recording_secret"] in text  # the key is visible where it is used
    assert t["test_secret"] not in text
    har = json.loads(text)
    assert len(har["log"]["entries"]) == t["har_entries"]
    noise = t["har_entries"] - t["session_calls"]
    assert abs(noise - 0.7 * t["har_entries"]) <= 1


def test_goal_md_lists_signatures_but_not_secrets_or_auth_location(task_dir):
    goal = (task_dir / "goal.md").read_text()
    t = load(task_dir, "task.json")
    assert "Bookshelf API" in goal and "class Client" in goal
    assert "Client(base_url: str, api_key: str)" in goal
    assert "https://api.bookshelf.example/v1" in goal
    for op_id in t["goal_ops"]:
        assert f"{op_id}(self" in goal
    assert "X-API-Key" not in goal and t["recording_secret"] not in goal and t["test_secret"] not in goal
    assert "httpx" in goal
    assert "`client.py` must import everything it uses (for example `from typing import Any` for the return annotations)." in goal


def test_goal_only_has_ops_with_json_schema_and_is_partial(task_dir):
    t = load(task_dir, "task.json")
    table = load(task_dir, "op_table.json")
    by = {o["op_id"]: o for o in table["operations"]}
    assert all(by[g]["response"]["schema"] is not None for g in t["goal_ops"])
    assert "delete_book" in by and "delete_book" not in t["goal_ops"]


def test_build_is_deterministic(tmp_path):
    a = build_task.build_task(spec(), "acme:bookshelf", tmp_path / "a", noise=0.5, seed=9)
    b = build_task.build_task(spec(), "acme:bookshelf", tmp_path / "b", noise=0.5, seed=9)
    for f in ("goal.md", "session.har", "task.json", "op_table.json", "tests.json"):
        assert (a / f).read_text() == (b / f).read_text(), f
    c = build_task.build_task(spec(), "acme:bookshelf", tmp_path / "c", noise=0.5, seed=10)
    assert (a / "task.json").read_text() != (c / "task.json").read_text()


def test_basic_auth_secret_shape(tmp_path):
    d = build_task.build_task(spec("bookshelf_basic.yaml"), "acme:bookshelf", tmp_path, seed=1)
    t = load(d, "task.json")
    assert re.fullmatch(r"user_[0-9a-f]{8}:pw_[0-9a-f]{16}", t["recording_secret"])
    assert re.fullmatch(r"user_[0-9a-f]{8}:pw_[0-9a-f]{16}", t["test_secret"])
    assert t["recording_secret"] != t["test_secret"]
    assert "auth:basic" in t["features"]


def test_test_responses_differ_from_recorded_ones(task_dir):
    """No goal op may have a test response equal to a recorded one (copying the HAR must fail)."""
    from har2client.mock import generate_response

    t, table = load(task_dir, "task.json"), load(task_dir, "op_table.json")
    by = {o["op_id"]: o for o in table["operations"]}
    har = json.loads((task_dir / "session.har").read_text())
    texts = [e["response"]["content"]["text"] for e in har["log"]["entries"]]
    recorded = [json.loads(x) for x in texts if x and x[0] in "[{"]
    for op_id in t["goal_ops"]:
        for n in range(2):
            assert generate_response(by[op_id], t["seeds"]["test"], n) not in recorded


def test_unusable_api_is_refused(tmp_path):
    s = spec()
    s["paths"] = dict(list(s["paths"].items())[:1])
    with pytest.raises(ops.UnusableApi) as e:
        build_task.build_task(s, "acme:bookshelf", tmp_path)
    assert "operations" in str(e.value)
    s2 = spec()
    s2["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "apiKey", "in": "cookie", "name": "sid"}}
    with pytest.raises(ops.UnusableApi):
        build_task.build_task(s2, "acme:bookshelf", tmp_path)
    assert not (tmp_path / "acme_bookshelf").exists()


def test_explicit_goal_ops_and_cap_of_eight(tmp_path):
    d = build_task.build_task(spec(), "acme:bookshelf", tmp_path, seed=2, goal_ops=["get_book", "health"])
    assert load(d, "task.json")["goal_ops"] == ["get_book", "health"]
    s = spec()
    template = s["paths"]["/health"]
    for i in range(14):
        s["paths"][f"/extra{i}"] = json.loads(json.dumps(template))
        s["paths"][f"/extra{i}"]["get"]["operationId"] = f"extra{i}"
    d = build_task.build_task(s, "acme:big", tmp_path, seed=2)
    assert len(load(d, "task.json")["goal_ops"]) == 8


def _bbci_spec():
    s = spec()
    params = s["paths"]["/books"]["get"]["parameters"]
    params.append(
        {"name": "mixin", "in": "query", "required": True,
         "schema": {"type": "array", "items": {"type": "string"}, "enum": ["live", "promotions"]}}
    )
    return s


def test_bbci_style_array_enum_builds_with_list_args_everywhere(tmp_path):
    d = build_task.build_task(_bbci_spec(), "acme:bbci", tmp_path, seed=3, goal_ops=["list_books", "get_book"])
    assert "mixin: list" in (d / "goal.md").read_text()
    tests = load(d, "tests.json")
    mixins = [t["kwargs"]["mixin"] for t in tests if t["method"] == "list_books"]
    assert mixins and all(isinstance(m, list) for m in mixins)


def test_build_fails_loudly_on_arg_type_mismatch(tmp_path, monkeypatch):
    from har2client import testgen

    real = testgen.make_tests

    def bad(*a, **k):
        tests = real(*a, **k)
        for t in tests:
            if "limit" in t["kwargs"]:
                t["kwargs"]["limit"] = "3"
        return tests

    monkeypatch.setattr(build_task, "make_tests", bad)
    with pytest.raises(testgen.ArgTypeMismatch):
        build_task.build_task(spec(), "acme:bookshelf", tmp_path, seed=3, goal_ops=["list_books"])
    assert not issubclass(testgen.ArgTypeMismatch, ops.UnusableApi)  # never a quiet skip


def test_build_fails_loudly_on_recorded_arg_type_mismatch(tmp_path, monkeypatch):
    real = build_task.play_session

    def bad(*a, **k):
        calls = real(*a, **k)
        for c in calls:
            if "limit" in c["kwargs"]:
                c["kwargs"]["limit"] = "3"
        return calls

    monkeypatch.setattr(build_task, "play_session", bad)
    from har2client import testgen

    with pytest.raises(testgen.ArgTypeMismatch):
        build_task.build_task(spec(), "acme:bookshelf", tmp_path, seed=3, goal_ops=["list_books"])
