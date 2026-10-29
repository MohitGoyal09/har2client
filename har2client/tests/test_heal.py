"""Case 3 (self-healing client): mutations, row selection, patch size, failure capture, built tasks and their validity gates."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from har2client import grade as grading
from har2client.heal import build as heal_build
from har2client.heal import failure, mutations, patch, select
from har2client.heal.build import HealBuildError, build_heal_task, build_scratch_task, find_leaks, verify_heal_task
from har2client.tierb import config as cfg
from har2client.tierb.build import render_goal
from har2client.tierb.refclient import generate_client_source
from har2client.tierb.spec import make_spec

ROOT = Path(__file__).resolve().parents[1]
ROWS = select.select_rows(1)
FIRST_ROW = {m.name: next(r for r in ROWS if r["mutation"] == m.name) for m in mutations.MUTATIONS}
SWITCH = {
    "add_hidden_header": "hidden_header", "apikey_to_bearer_login": "auth", "offset_to_cursor": "pagination", "none_to_link_header": "pagination",
    "add_envelope": "envelope", "add_etag": "etag_update", "add_token_expiry": "token_expiry", "rename_hidden_header": None,
}
MECHANISM_WORDS = ("cursor", "offset", "etag", "if-match", "csrf", "cookie", "bearer", "expire", "envelope", "link header", "x-client")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One repair task per mutation (built once, never modified by the tests)."""
    out = tmp_path_factory.mktemp("heal")
    return {name: build_heal_task(row, out) for name, row in FIRST_ROW.items()}


def read_json(path):
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------ mutations and v1/v2 pairing


@pytest.mark.parametrize("row", [FIRST_ROW[m.name] for m in mutations.MUTATIONS], ids=lambda r: r["mutation"])
def test_v1_differs_from_v2_in_the_mutated_feature_only(row):
    v2 = make_spec(row["domain"], row["config"], row["seed"])
    v1 = mutations.derive_v1_spec(v2, row["mutation"], row["seed"])
    same = ("domain", "host", "base_path", "server_url", "path_word", "items_key", "title", "login", "csrf", "api_key_header", "filter")
    assert {k: v1[k] for k in same} == {k: v2[k] for k in same}
    assert cfg.is_valid(v1["config"])
    changed = {k for k in v2["config"] if v1["config"][k] != v2["config"][k]}
    switch = SWITCH[row["mutation"]]
    if switch is None:  # renamed header: same config, another header
        assert not changed and v1["hidden_header"]["name"] != v2["hidden_header"]["name"]
    else:
        assert changed == {switch}
        assert (v1["hidden_header"] == v2["hidden_header"]) == (switch != "hidden_header")
    assert row["base_config"] == v1["config"]


def test_a_mutation_that_does_not_apply_is_refused():
    v2 = make_spec("tickets", {**cfg.baseline_config()}, 3)
    for name in mutations.BY_NAME:
        with pytest.raises(ValueError):
            mutations.derive_v1_spec(v2, name, 3)


def test_the_eight_mutations_are_the_ones_in_the_design():
    assert [m.name for m in mutations.MUTATIONS] == [
        "add_hidden_header", "apikey_to_bearer_login", "offset_to_cursor", "none_to_link_header",
        "add_envelope", "add_etag", "add_token_expiry", "rename_hidden_header",
    ]


def test_v1_client_drops_relogin_only_for_login_auth_without_expiry():
    spec = lambda **over: make_spec("tickets", {**cfg.baseline_config(), **over}, 3)  # noqa: E731
    assert mutations.v1_flaws(spec(auth="bearer_login")) == ("no_relogin",)
    assert mutations.v1_flaws(spec(auth="cookie_csrf")) == ("no_relogin",)
    assert mutations.v1_flaws(spec(auth="bearer_login", token_expiry="on")) == ()
    assert mutations.v1_flaws(spec()) == ()


# ------------------------------------------------------------ row selection


def test_rows_are_8_mutations_times_6_valid_bases():
    assert len(ROWS) == 48 and [r["index"] for r in ROWS] == list(range(48))
    for m in mutations.MUTATIONS:
        rows = [r for r in ROWS if r["mutation"] == m.name]
        assert len(rows) == 6 and all(m.applies(r["config"]) and cfg.is_valid(r["base_config"]) and cfg.is_valid(r["config"]) for r in rows)
        assert len({r["domain"] for r in rows}) >= 5 and len({json.dumps(r["base_config"]) for r in rows}) == 6
    assert len({r["seed"] for r in ROWS}) == 48


def test_rows_are_deterministic_and_the_committed_file_matches():
    assert select.select_rows(1) == ROWS and select.select_rows(2) != ROWS
    assert select.read_rows(ROOT / "data" / "heal_configs.jsonl") == ROWS


def test_rows_file_round_trip(tmp_path):
    path = tmp_path / "rows.jsonl"
    select.write_rows(ROWS, path, 1)
    assert select.read_rows(path) == ROWS
    first = json.loads(path.read_text().splitlines()[0])
    assert first["task_id"] == select.task_id_of(ROWS[0]) and first["scratch_id"] == select.scratch_id_of(ROWS[0]) and first["config_seed"] == 1


# ------------------------------------------------------------ patch size


def test_patch_size_of_no_change_is_zero():
    src = "def f(x):\n    return x + 1\n"
    assert patch.patch_size(src, src) == {
        "lines_removed": 0, "lines_added": 0, "changed_lines": 0, "nodes_removed": 0, "nodes_added": 0, "changed_ast_nodes": 0,
    }


def test_patch_size_counts_a_replaced_line_as_one_removed_and_one_added():
    old = "def f(x):\n    y = x + 1\n    return y\n"
    new = "def f(x):\n    y = x + 2\n    return y\n"
    size = patch.patch_size(old, new)
    assert (size["lines_removed"], size["lines_added"], size["changed_lines"]) == (1, 1, 2)
    assert (size["nodes_removed"], size["nodes_added"]) == (1, 1)  # only the constant


def test_patch_size_counts_added_lines_and_nodes():
    old = "def f(x):\n    return x\n"
    new = "def f(x):\n    if x is None:\n        return 0\n    return x\n"
    size = patch.patch_size(old, new)
    assert size["lines_removed"] == 0 and size["lines_added"] == 2
    assert size["nodes_removed"] == 0 and size["nodes_added"] >= 6


def test_formatting_and_comments_change_lines_but_no_ast_nodes():
    old = "def f(x):\n    return x+1\n"
    new = "def f(x):\n    # add one\n    return x + 1\n"
    size = patch.patch_size(old, new)
    assert size["changed_lines"] > 0 and size["changed_ast_nodes"] == 0


def test_patch_size_of_a_file_that_does_not_parse():
    size = patch.patch_size("x = 1\n", "x = (\n")
    assert size["changed_lines"] == 2 and size["changed_ast_nodes"] is None and size["nodes_added"] is None


@pytest.mark.slow
def test_repair_patch_of_a_built_task_is_small_but_not_empty(built):
    task_dir = built["add_etag"]
    size = patch.patch_size((task_dir / "client_v1.py").read_text(), (task_dir / "ref_client.py").read_text())
    total = len((task_dir / "client_v1.py").read_text().splitlines())
    assert 0 < size["changed_lines"] < total and size["changed_ast_nodes"] > 0


# ------------------------------------------------------------ built tasks: layout and gates


@pytest.mark.slow
@pytest.mark.parametrize("mutation", [m.name for m in mutations.MUTATIONS])
def test_built_task_layout_and_visible_files(built, mutation):
    d = built[mutation]
    assert {p.name for p in d.iterdir()} == {"goal.md", "client_v1.py", "failure.txt", "session.har", "task.json", "tests.json", "engine.json", "ref_client.py"}
    task = read_json(d / "task.json")
    assert task["tier"] == "B" and task["heal"]["mutation"] == mutation and task["noise_ratio"] == 0.3
    assert task["config"] == read_json(d / "engine.json")["config"] and task["heal"]["base_config"] == task["heal"]["v1_spec"]["config"]
    assert {"credentials", "seeds", "goal_ops"} <= set(task) and set(task["credentials"]) == {"recording", "test"}
    assert "case:heal" in task["features"] and f"mutation:{mutation}" in task["features"]


@pytest.mark.slow
@pytest.mark.parametrize("mutation", [m.name for m in mutations.MUTATIONS])
def test_client_v1_scores_one_on_v1_and_less_on_v2_and_reference_scores_one(built, mutation):
    d = built[mutation]
    scores = verify_heal_task(d)
    assert scores == {"v1_on_v1": 1.0, "v1_on_v2": scores["v1_on_v2"], "ref_on_v2": 1.0} and scores["v1_on_v2"] < 1.0
    assert read_json(d / "task.json")["heal"]["gate"] == scores
    assert grading.grade(d, d / "ref_client.py")["pass_rate"] == 1.0  # the unchanged grader, tier B dispatch


@pytest.mark.slow
@pytest.mark.parametrize("mutation", [m.name for m in mutations.MUTATIONS])
def test_a_no_op_patch_fails(built, mutation):
    d = built[mutation]
    result = grading.grade(d, d / "client_v1.py")
    assert result["tier"] == "B" and result["pass_rate"] < 1.0 and result["failure_counts"]


@pytest.mark.slow
@pytest.mark.parametrize("mutation", [m.name for m in mutations.MUTATIONS])
def test_no_hidden_data_in_visible_files(built, mutation):
    d = built[mutation]
    task = read_json(d / "task.json")
    assert find_leaks(d, task) == []
    test, practice = task["credentials"]["test"], task["heal"]["practice"]["credentials"]
    assert practice["password"] != test["password"] and practice["username"] != test["username"]
    for name in heal_build.VISIBLE_FILES:
        text = (d / name).read_text()
        assert test["password"] not in text and test["username"] not in text, name
        assert practice["password"] not in text and practice["username"] not in text, name
    test_ids = {i["id"] for i in heal_build.domains.initial_items(task["domain"], task["seeds"]["test"])}
    practice_ids = {i["id"] for i in heal_build.domains.initial_items(task["domain"], task["heal"]["practice"]["seed"])}
    assert not test_ids & practice_ids
    assert not any(i in (d / n).read_text() for i in test_ids for n in heal_build.VISIBLE_FILES)
    assert task["seeds"]["test"] not in (task["seeds"]["recording"], task["heal"]["practice"]["seed"])


@pytest.mark.slow
@pytest.mark.parametrize("mutation", [m.name for m in mutations.MUTATIONS])
def test_recording_is_short_and_goal_names_no_mechanism(built, mutation):
    d = built[mutation]
    task = read_json(d / "task.json")
    spec = read_json(d / "engine.json")
    assert 4 <= task["session_calls"] <= 10
    assert task["har_entries"] >= task["session_calls"]  # plus noise
    prefix = f"https://{spec['host']}{spec['base_path']}/"
    api = [e for e in read_json(d / "session.har")["log"]["entries"] if e["request"]["url"].startswith(prefix)]
    assert len(api) == task["session_calls"]
    goal = (d / "goal.md").read_text().lower()
    assert all(w not in goal for w in MECHANISM_WORDS)
    assert "client_v1.py" in goal and "failure.txt" in goal and "session.har" in goal
    assert spec["hidden_header"] is None or spec["hidden_header"]["name"] not in goal


@pytest.mark.slow
def test_goal_has_the_case_1_constructor_and_method_sections(built):
    d = built["add_envelope"]
    spec = read_json(d / "engine.json")
    case1 = render_goal(spec)
    section = "## Constructor" + case1.split("## Constructor", 1)[1].split("## Rules", 1)[0]
    assert section in (d / "goal.md").read_text()


@pytest.mark.slow
def test_token_expiry_recording_shows_one_expiry_and_a_new_login(built):
    d = built["add_token_expiry"]
    entries = read_json(d / "session.har")["log"]["entries"]
    spec = read_json(d / "engine.json")
    expired = [e for e in entries if e["response"]["status"] == 401 and "token_expired" in e["response"]["content"].get("text", "")]
    logins = [e for e in entries if e["request"]["url"].endswith(spec["login"]["path"])]
    assert len(expired) == 1 and len(logins) == 2


@pytest.mark.slow
def test_add_hidden_header_recording_and_failure_show_the_header_effect(built):
    d = built["add_hidden_header"]
    spec = read_json(d / "engine.json")
    name = spec["hidden_header"]["name"]
    assert name.lower() in (d / "session.har").read_text().lower()
    assert name not in (d / "client_v1.py").read_text() and "400" in (d / "failure.txt").read_text()


@pytest.mark.slow
def test_verify_refuses_a_v1_client_that_still_works_on_v2(built, tmp_path):
    work = tmp_path / "task"
    shutil.copytree(built["add_etag"], work)
    shutil.copy(work / "ref_client.py", work / "client_v1.py")
    task = read_json(work / "task.json")  # no change at all: v1 is the v2 API
    task["heal"]["v1_spec"], task["heal"]["v1_credentials"] = read_json(work / "engine.json"), task["credentials"]["test"]
    (work / "task.json").write_text(json.dumps(task))
    with pytest.raises(HealBuildError, match="still scores 1.0 on v2"):
        verify_heal_task(work)


@pytest.mark.slow
def test_verify_refuses_hidden_data_in_a_visible_file(built, tmp_path):
    work = tmp_path / "task"
    shutil.copytree(built["add_etag"], work)
    leaked = read_json(work / "task.json")["credentials"]["test"]["password"]
    (work / "failure.txt").write_text((work / "failure.txt").read_text() + f"\npassword={leaked}\n")
    with pytest.raises(HealBuildError, match="hidden data"):
        verify_heal_task(work)


@pytest.mark.slow
def test_verify_refuses_a_v1_client_that_is_wrong_on_v1(built, tmp_path):
    work = tmp_path / "task"
    shutil.copytree(built["add_hidden_header"], work)
    (work / "client_v1.py").write_text("class Client:\n    def __init__(self, base_url, username, password):\n        pass\n")
    with pytest.raises(HealBuildError, match="on v1"):
        verify_heal_task(work)


# ------------------------------------------------------------ failure capture


@pytest.mark.slow
def test_failure_text_shows_the_call_the_exception_and_the_last_request_and_response(built):
    d = built["offset_to_cursor"]
    text = (d / "failure.txt").read_text()
    spec = read_json(d / "engine.json")
    assert text.startswith("Ran client_v1.py against a practice server") and spec["server_url"] in text
    assert "127.0.0.1" not in text and "client.list_" in text
    assert "Traceback" in text and 'File "client_v1.py"' in text and "KeyError" in text
    assert "Last request of this call:" in text and "Its response:" in text and "HTTP 200" in text


@pytest.mark.slow
def test_failure_text_of_a_call_that_returns_a_wrong_result(built):
    text = (built["none_to_link_header"] / "failure.txt").read_text()
    assert "without an exception" in text and "got 10 of" in text and "Link:" in text and "Traceback" not in text


@pytest.mark.slow
def test_failure_text_redacts_the_practice_credentials(built):
    d = built["apikey_to_bearer_login"]
    task = read_json(d / "task.json")
    creds = task["heal"]["practice"]["credentials"]
    text = (d / "failure.txt").read_text()
    assert creds["password"] not in text and "401" in text


def test_failure_capture_raises_when_the_client_passes_every_call(tmp_path):
    spec = make_spec("tickets", cfg.baseline_config(), 3)
    path = tmp_path / "client_v1.py"
    path.write_text(generate_client_source(spec))
    creds = {"username": "user_practice", "password": "sk_practice_key"}
    tests = heal_build.make_tests(spec, 55)
    with pytest.raises(failure.NoFailure):
        failure.capture_failure(spec, path, 55, creds, tests)


# ------------------------------------------------------------ scratch (case 1) comparison task


@pytest.mark.slow
def test_scratch_task_is_a_case_1_tier_b_task_of_the_same_api(built, tmp_path):
    row = FIRST_ROW["add_envelope"]
    d = build_scratch_task(row, tmp_path, negatives=False)
    assert d.name == select.scratch_id_of(row)
    assert {p.name for p in d.iterdir()} == {"goal.md", "session.har", "task.json", "tests.json", "engine.json", "ref_client.py"}
    assert read_json(d / "engine.json") == read_json(built["add_envelope"] / "engine.json")
    assert read_json(d / "task.json")["tier"] == "B" and read_json(d / "task.json")["config"] == row["config"]
    assert grading.grade(d, d / "ref_client.py")["pass_rate"] == 1.0
    heal = read_json(built["add_envelope"] / "task.json")
    scratch = read_json(d / "task.json")
    assert scratch["credentials"]["test"] != heal["credentials"]["test"]


# ------------------------------------------------------------ script


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("build_heal", ROOT / "scripts" / "build_heal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_builds_heal_and_scratch_tasks(script, tmp_path, capsys):
    configs = tmp_path / "heal.jsonl"
    code = script.main(["--out", str(tmp_path / "heal"), "--seed", "1", "--configs", str(configs), "--limit", "2", "--jobs", "2"])
    assert code == 0
    assert len(configs.read_text().splitlines()) == 48 and select.read_rows(configs) == ROWS
    assert sorted(p.name for p in (tmp_path / "heal").iterdir()) == [select.task_id_of(r) for r in ROWS[:2]]
    assert sorted(p.name for p in (tmp_path / "heal_scratch").iterdir()) == [select.scratch_id_of(r) for r in ROWS[:2]]
    out = capsys.readouterr().out
    assert "built 2/2 repair tasks" in out and "built 2/2 from-scratch tasks" in out and "v1_on_v2=" in out and "har_kb=" in out
