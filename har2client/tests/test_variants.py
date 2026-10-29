"""U2 (lexical-overlap names) and U3 (twin, closed-book): checks, golden exact output, self-consistency, reference client = 1.0."""

import json
import re
from pathlib import Path

import pytest

from har2client import build_task, cli, grade, namegen, ops, refclient, variants as v

FIX = Path(__file__).parent / "fixtures"
GOLDEN = FIX / "golden" / "exact_bookshelf_apikey_seed4"
SPECS = ["bookshelf_apikey.yaml", "bookshelf_basic.yaml", "bookshelf_bearer.yaml"]
SAFE_WORDS = ["quokka", "plinth", "marmot", "tundra", "cobalt", "saffron", "bramble", "walrus", "ferret", "gantry"]


def spec(name="bookshelf_apikey.yaml"):
    return ops.load_spec(FIX / name)


def fake_ask(system, user, schema):
    """Stands in for Beam: safe, unrelated words, deterministic."""
    request = json.loads(user[user.index("{"):])
    if "ops" in schema["properties"]:
        out = []
        for i, job in enumerate(request["operations"]):
            w = SAFE_WORDS[i % len(SAFE_WORDS)]
            out.append({
                "key": job["key"], "name": f"{w}_run{i}", "summary": f"The {w} job.",
                "description": f"Does the {w} thing.", "body_description": f"The {w} payload." if job["has_body"] else "",
                "params": [{"key": p["key"], "name": f"{SAFE_WORDS[(i + j + 3) % len(SAFE_WORDS)]}_v{j}",
                            "description": f"The {SAFE_WORDS[(i + j + 3) % len(SAFE_WORDS)]} value."} for j, p in enumerate(job["params"])],
            })
        return {"ops": out}
    words = [{"old": w, "new": f"{SAFE_WORDS[i % len(SAFE_WORDS)]}{'x' * (i // len(SAFE_WORDS))}"} for i, w in enumerate(request["words"])]
    return {"host": "api.quokkanet.io", "title": "Quokka Hub", "words": words}


def no_ask(*_):
    raise AssertionError("Beam must not be called when the cache is complete")


def build(tmp_path, name="bookshelf_apikey.yaml", seed=4, ask=fake_ask, **kw):
    kw.setdefault("rewordings_dir", tmp_path / "rw")
    return build_task.build_task(spec(name), "acme:bookshelf", tmp_path / "out", noise=0.7, seed=seed, ask=ask, **kw)


def load(task_dir, name):
    return json.loads((task_dir / name).read_text())


def score(task_dir):
    client = refclient.write_ref_client(task_dir)
    return grade.grade(task_dir, client)["pass_rate"]


# ---------------------------------------------------------------- collision checker

def test_tokens_split_camel_snake_brackets_and_drop_short_words():
    assert v.tokens("filter[accountType]") == ["filter", "account", "type"]
    assert v.tokens("get_ID_of_HTTPServer2") == ["get", "http", "server"]
    assert v.tokens("an_id") == []


@pytest.mark.parametrize(
    "candidate,wire,expected",
    [
        ("account_kind", "filter[accountType]", ["account"]),
        ("holding_category", "filter[accountType]", []),
        ("accounts", "account_id", ["accounts"]),  # plural of a wire token
        ("ledger_id", "account_id", []),  # `id` is shorter than 3 letters
        ("TypeCode", "filter_type", ["type"]),  # case-insensitive, camelCase
        ("xy", "xy", []),
    ],
)
def test_collision_checker(candidate, wire, expected):
    assert v.colliding_tokens(candidate, v.stems([wire])) == expected


def test_check_name_rules():
    forbidden = v.stems(["books"])
    assert v.check_name("shelf_item", forbidden, set()) is None
    assert "shares" in v.check_name("book_list", forbidden, set())
    assert v.check_name("class", set(), set()) and v.check_name("body", set(), set())
    assert v.check_name("Bad", set(), set()) and v.check_name("x", set(), {"x"})


# ---------------------------------------------------------------- exact mode is unchanged

def test_exact_is_byte_identical_to_golden(tmp_path):
    task = build(tmp_path, ask=no_ask)
    for name in ("goal.md", "session.har", "op_table.json", "tests.json"):
        assert (task / name).read_bytes() == (GOLDEN / name).read_bytes(), name
    gold, new = load(GOLDEN, "task.json"), load(task, "task.json")
    assert new.pop("variant") == {"names": "exact", "twin": False, "closed_book": False}
    assert new == gold
    assert not (tmp_path / "rw").exists()


# ---------------------------------------------------------------- reworded and opaque

def wire_text(table, op):
    return [seg for seg in re.split(r"[/{}]", op["path"]) if seg] + [p["name"] for p in op["params"]]


def test_reworded_names_share_no_token_with_wire_and_har_is_unchanged(tmp_path):
    exact, task = build(tmp_path / "e", ask=no_ask), build(tmp_path, names="reworded")
    assert task.name == "acme_bookshelf__reworded"
    t, table, orig = load(task, "task.json"), load(task, "op_table.json"), load(exact, "op_table.json")
    assert t["variant"] == {"names": "reworded", "twin": False, "closed_book": False}
    assert (task / "session.har").read_bytes() == (exact / "session.har").read_bytes()
    by, by_orig = {o["op_id"]: o for o in table["operations"]}, {o["op_id"]: o for o in orig["operations"]}
    assert len(t["goal_ops"]) == len(load(exact, "task.json")["goal_ops"])
    for new, wire_op in t["name_map"]["methods"].items():
        op, forbidden = by[new], v.stems(wire_text(orig, by_orig[wire_op]))
        assert v.colliding_tokens(new, forbidden) == []
        assert v.colliding_tokens(op["summary"], forbidden) == []
        for arg, wire_name in t["name_map"]["params"][new].items():
            assert v.colliding_tokens(arg, forbidden) == []
            assert any(p["py_name"] == arg and p["name"] == wire_name for p in op["params"])
        assert f"`{new}(self" in (task / "goal.md").read_text()
    assert {x["method"] for x in load(task, "tests.json")} == set(t["goal_ops"])
    assert score(task) == 1.0


def test_opaque_uses_numbered_names_and_descriptions(tmp_path):
    task = build(tmp_path, names="opaque")
    t, goal = load(task, "task.json"), (task / "goal.md").read_text()
    assert t["goal_ops"] == [f"op_{i}" for i in range(1, len(t["goal_ops"]) + 1)]
    for method, args in t["name_map"]["params"].items():
        assert list(args) == [f"arg_{j}" for j in range(1, len(args) + 1)]
        assert f"`{method}(self" in goal
        for arg in args:
            assert f"  - `{arg}`: " in goal
    assert "- `body`: " not in goal or "  - `body`: " in goal
    for orig in ("list_books", "get_book", "author_id", "include_draft"):
        assert orig not in goal
    assert score(task) == 1.0


def test_cache_makes_rebuilds_offline_and_shared_between_modes(tmp_path):
    first = build(tmp_path, names="reworded")
    cache = json.loads((tmp_path / "rw" / "acme_bookshelf.json").read_text())
    assert cache["names"]["original"]
    again = build(tmp_path / "again", names="reworded", rewordings_dir=tmp_path / "rw", ask=no_ask)
    assert (again / "goal.md").read_text() == (first / "goal.md").read_text()
    opaque = build(tmp_path / "o", names="opaque", rewordings_dir=tmp_path / "rw", ask=no_ask)
    assert load(opaque, "task.json")["variant"]["names"] == "opaque"


def test_bad_answers_are_reasked_then_fall_back_and_are_recorded(tmp_path):
    calls = []

    def stubborn(system, user, schema):
        calls.append(1)
        reply = fake_ask(system, user, schema)
        for item in reply.get("ops", []):
            item["name"] = "list_books"  # collides with the wire of the books ops, whatever the op
            item["description"] = "Lists books."
        return reply

    task = build(tmp_path, names="reworded", ask=stubborn)
    assert len(calls) == 1 + namegen.MAX_REASKS
    entries = json.loads((tmp_path / "rw" / "acme_bookshelf.json").read_text())["names"]["original"]
    assert any("name" in e["fallbacks"] for e in entries.values())
    assert score(task) == 1.0


def test_reworded_desc_has_reworded_names_plus_opaque_style_descriptions(tmp_path):
    exact, reworded = build(tmp_path / "e", ask=no_ask), build(tmp_path, names="reworded")
    task = build(tmp_path, names="reworded_desc", ask=no_ask)  # the cache is shared: no new Beam call
    assert task.name == "acme_bookshelf__reworded_desc"
    t, goal, orig = load(task, "task.json"), (task / "goal.md").read_text(), load(exact, "op_table.json")
    assert t["variant"] == {"names": "reworded_desc", "twin": False, "closed_book": False}
    assert t["goal_ops"] == load(reworded, "task.json")["goal_ops"] and t["name_map"] == load(reworded, "task.json")["name_map"]
    assert (task / "session.har").read_bytes() == (exact / "session.har").read_bytes()
    by_orig = {o["op_id"]: o for o in orig["operations"]}
    for method, wire_op in t["name_map"]["methods"].items():
        forbidden = v.stems(wire_text(orig, by_orig[wire_op]))
        assert f"`{method}(self" in goal
        line = next(l for l in goal.splitlines() if l.startswith(f"- `{method}(self"))
        assert v.colliding_tokens(line.split("`: ", 1)[1], forbidden) == []
        for arg in t["name_map"]["params"][method]:
            row = next(l for l in goal.splitlines() if l.startswith(f"  - `{arg}`: "))
            assert v.colliding_tokens(row.split("`: ", 1)[1], forbidden) == []
            assert v.colliding_tokens(arg, forbidden) == []
    assert goal.count("\n  - `") >= len(t["goal_ops"])
    assert "  - `body`: " in goal or not any(o["body"] for o in orig["operations"])
    assert "list_books" not in goal and "author_id" not in goal
    assert score(task) == 1.0


def test_reworded_desc_differs_from_reworded_only_in_descriptions(tmp_path):
    reworded, desc = build(tmp_path, names="reworded"), build(tmp_path, names="reworded_desc", ask=no_ask)
    assert load(reworded, "tests.json") == load(desc, "tests.json")
    assert "\n  - `" not in (reworded / "goal.md").read_text() and "\n  - `" in (desc / "goal.md").read_text()


# ---------------------------------------------------------------- twin

def test_twin_leaves_nothing_of_the_original_and_ref_scores_one(tmp_path):
    exact = build(tmp_path / "e", ask=no_ask)
    task = build(tmp_path, twin=True)
    assert task.name == "acme_bookshelf__twin"
    plan = json.loads((tmp_path / "rw" / "acme_bookshelf.json").read_text())["twin"]
    renamed = {w for w, new in plan["words"].items() if w != new}
    assert {"book", "author", "genre", "category"} <= renamed
    goal, har_text = (task / "goal.md").read_text(), (task / "session.har").read_text()
    table = load(task, "op_table.json")
    gone = lambda text: not (set(map(v.stem, v.tokens(text))) & renamed)  # noqa: E731
    assert "bookshelf" not in goal.lower() and "api.bookshelf.example" not in har_text + json.dumps(table)
    api_text = "\n".join(l for l in goal.splitlines() if l.startswith("# ") or "(self" in l)  # title + method list
    assert gone(api_text)
    har = json.loads(har_text)
    for entry in har["log"]["entries"]:
        url = entry["request"]["url"]
        assert "bookshelf" not in url and gone(url.split("?")[0].split("//", 1)[1].split("/", 1)[-1] if "/" in url.split("//", 1)[1] else "")
        for q in entry["request"]["queryString"]:
            assert gone(q["name"])
    for op in table["operations"]:
        assert all(gone(x) for x in [op["op_id"], op["path"], op["summary"], *[p["name"] for p in op["params"]], *[p["py_name"] for p in op["params"]]])
    keys = []

    def collect(node):
        if isinstance(node, dict):
            for k, val in node.items():
                if k == "properties":
                    keys.extend(val)
                collect(val)
        elif isinstance(node, list):
            for x in node:
                collect(x)

    collect(table)
    assert keys and all(gone(k) for k in keys)
    assert gone(json.dumps(table["dropped"])) and table["title"] == plan["title"]
    assert load(task, "task.json")["variant"] == {"names": "exact", "twin": True, "closed_book": False}
    assert len(load(task, "task.json")["goal_ops"]) == len(load(exact, "task.json")["goal_ops"])
    assert score(task) == 1.0


def test_twin_is_deterministic_and_cached(tmp_path):
    a = build(tmp_path / "a", twin=True, rewordings_dir=tmp_path / "rw")
    b = build(tmp_path / "b", twin=True, rewordings_dir=tmp_path / "rw", ask=no_ask)
    for name in ("goal.md", "session.har", "op_table.json", "tests.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_twin_word_checks():
    assert v.check_twin_word("account", "ledger", set(), set()) is None
    assert v.check_twin_word("account", "accounting", set(), set())  # near copy
    assert v.check_twin_word("account", "ledgers", set(), set())  # plural
    assert v.check_twin_word("account", "ledger", {"ledger"}, set())  # reused
    assert v.check_twin_host("api.bookshelf.example", "api.bookshelf.example")
    assert v.check_twin_host("api.quokkanet.io", "api.bookshelf.example") is None


def test_twin_with_reworded_names_ref_scores_one(tmp_path):
    task = build(tmp_path, twin=True, names="reworded")
    assert task.name == "acme_bookshelf__reworded__twin"
    assert load(task, "task.json")["name_map"]["methods"]
    assert score(task) == 1.0


# ---------------------------------------------------------------- closed book

def test_closed_book_har_is_empty_and_goal_keeps_title_and_base_url(tmp_path):
    exact, task = build(tmp_path / "e", ask=no_ask), build(tmp_path, closed_book=True, ask=no_ask)
    assert task.name == "acme_bookshelf__closed"
    har = load(task, "session.har")
    assert har["log"]["entries"] == [] and har["log"]["version"] == "1.2" and "creator" in har["log"]
    goal = (task / "goal.md").read_text()
    assert goal.startswith("# Bookshelf API") and "In the recording it looks like `https://api.bookshelf.example/v1`." in goal
    assert load(task, "tests.json") == load(exact, "tests.json") and load(task, "op_table.json") == load(exact, "op_table.json")
    assert load(task, "task.json")["har_entries"] == 0
    assert load(task, "task.json")["variant"] == {"names": "exact", "twin": False, "closed_book": True}


def test_twin_closed_has_empty_har_and_no_original_host_or_names(tmp_path):
    task = build(tmp_path, twin=True, closed_book=True)
    assert task.name == "acme_bookshelf__twin_closed"
    t = load(task, "task.json")
    assert t["variant"] == {"names": "exact", "twin": True, "closed_book": True} and t["har_entries"] == 0
    har = load(task, "session.har")
    assert har["log"]["entries"] == []
    plan = json.loads((tmp_path / "rw" / "acme_bookshelf.json").read_text())["twin"]
    renamed = {w for w, new in plan["words"].items() if w != new}
    goal, table = (task / "goal.md").read_text(), load(task, "op_table.json")
    assert "bookshelf" not in goal.lower() and "api.bookshelf.example" not in goal + json.dumps(table)
    assert plan["host"] in goal
    api_text = "\n".join(l for l in goal.splitlines() if l.startswith("# ") or "(self" in l)
    assert not (set(map(v.stem, v.tokens(api_text))) & renamed)
    twin = build(tmp_path / "t", twin=True, rewordings_dir=tmp_path / "rw", ask=no_ask)
    assert t["goal_ops"] == load(twin, "task.json")["goal_ops"] and load(task, "tests.json") == load(twin, "tests.json")
    assert load(task, "op_table.json") == load(twin, "op_table.json")


# ---------------------------------------------------------------- reference client on every variant and auth style

@pytest.mark.parametrize("name", SPECS)
@pytest.mark.parametrize(
    "kw", [{}, {"names": "reworded"}, {"names": "reworded_desc"}, {"names": "opaque"}, {"twin": True}, {"closed_book": True}],
    ids=lambda k: "-".join(k) or "exact",
)
def test_reference_client_scores_one_on_every_variant(tmp_path, name, kw):
    assert score(build(tmp_path, name=name, **kw)) == 1.0


# ---------------------------------------------------------------- CLI

def test_cli_builds_variants_and_reports_missing_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(namegen.API_KEY_ENV, raising=False)
    monkeypatch.setattr(namegen, "ENV_FILE", tmp_path / "no.env")
    argv = ["build", str(FIX / "bookshelf_apikey.yaml"), "--api-id", "acme:bookshelf", "--out", str(tmp_path / "o"), "--seed", "4"]
    assert cli.main(argv + ["--closed-book"]) == 0
    assert (tmp_path / "o" / "acme_bookshelf__closed" / "goal.md").is_file()
    assert cli.main(argv + ["--names", "reworded", "--rewordings-dir", str(tmp_path / "rw")]) == cli.EXIT_ERROR
    assert "REFLECTION_API_KEY" in capsys.readouterr().err


def test_twin_renames_a_distinctive_api_key_header_name(tmp_path):
    sp = spec()
    sp["components"]["securitySchemes"]["ApiKeyAuth"]["name"] = "X-Hapi-Subscription-Key"
    task = build_task.build_task(
        sp, "acme:bookshelf", tmp_path / "out", noise=0.7, seed=4, ask=fake_ask, twin=True, rewordings_dir=tmp_path / "rw"
    )
    name = load(task, "op_table.json")["security"]["name"]
    assert name != "X-Hapi-Subscription-Key" and name.startswith("X-") and name.endswith("-Key")
    assert not ({"hapi", "subscription"} & set(v.tokens(name)))
    assert "hapi" not in (task / "session.har").read_text().lower()
    assert score(task) == 1.0
    again = build_task.build_task(
        sp, "acme:bookshelf", tmp_path / "out2", noise=0.7, seed=4, ask=no_ask, twin=True, rewordings_dir=tmp_path / "rw"
    )
    assert (task / "session.har").read_bytes() == (again / "session.har").read_bytes()
