"""Build v3 hard repair tasks and check every gate and factor (DESIGN-CASE3-HARD.md)."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from har2client.heal import changelog, hard, hardtests, release
from har2client.heal.build import HealBuildError, find_leaks, visible_files
from har2client.heal.sdk_client import generate_sdk_client_source  # noqa: F401  (the module must import)
from har2client.tierb import config as cfg
from har2client.tierb.spec import OPS, make_spec

pytestmark = pytest.mark.slow  # the tests in this file take 5-40 s each

BASE = {**cfg.baseline_config()}


def row(task_id, names, domain="tickets", seed=1001, **factors):
    base = {**BASE, **factors.pop("base", {})}
    return {
        "task_id": task_id, "set": "test", "index": 0, "mutations": names, "domain": domain, "config": release.v2_config(base, names), "seed": seed,
        "error_detail": "verbose", "evidence": "both", "evidence_scope": "full", "changelog": "none", "client_style": "compact", **factors,
    }


ROWS = {
    "single": row("t_single", ["add_idempotency_key"], "invoices", 1101, base={"pagination": "offset"}),
    "drift": row("t_drift", ["add_drift_cents"], "tickets", 1107, base={"pagination": "offset"}),
    "release": row("t_release", ["add_drift_cents", "add_rate_limit", "add_idempotency_key"], "tickets", 1102, base={"pagination": "cursor", "auth": "bearer_login"}, changelog="complete"),
    "partial": row(
        "t_partial", ["add_drift_nest", "add_async_create", "add_moved"], "bookings", 1103, base={"pagination": "link_header", "auth": "cookie_csrf"},
        evidence_scope="partial", changelog="misleading",
    ),
    "sdk": row("t_sdk", ["add_drift_rename", "add_rate_limit", "add_idempotency_key"], "shipments", 1106, base={"pagination": "offset"}, client_style="sdk"),
    "terse": row("t_terse", ["add_drift_epoch", "add_refresh_rotation", "add_drift_enum"], "samples", 1104, base={"auth": "bearer_login", "pagination": "offset"}, error_detail="terse", changelog="incomplete"),
}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("v3")
    with ThreadPoolExecutor(max_workers=4) as pool:
        dirs = list(pool.map(lambda r: hard.build_hard_task(r, out, build_seed=3), ROWS.values()))
    return dict(zip(ROWS, dirs))


def read_json(path):
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------ fix size (no server)


def test_fix_size_counts_changed_lines_and_touched_classes():
    old = "class A:\n    x = 1\n\nclass B:\n    y = 1\n"
    new = "class A:\n    x = 2\n\nclass B:\n    y = 1\n\nclass C:\n    z = 1\n"
    lines, classes = hard.fix_size(old, new)
    assert lines == 5 and classes == 2  # A changed, C added, B not touched


def test_fix_size_gate_by_changes_and_style():
    names3, names2 = ("a", "b", "c"), ("a", "b")
    assert hard.check_fix_size(names3, "compact", 29, 1) is not None and hard.check_fix_size(names3, "compact", 30, 1) is None
    assert hard.check_fix_size(names2, "compact", 5, 1) is None  # the line gate is for 3 changes or more
    assert hard.check_fix_size(names2, "sdk", 50, 1) is not None and hard.check_fix_size(names2, "sdk", 50, 2) is None


def test_every_pilot_release_of_three_or_more_changes_has_a_big_enough_compact_fix():
    for e in release.select_pilot(1):
        if e["changes"] >= 3 and e["client_style"] == "compact":
            spec = make_spec(e["domain"], e["config"], e["seed"])
            from har2client.heal import mutations

            v1 = mutations.derive_v1_spec(spec, e["mutations"], e["seed"])
            lines, _ = hard.fix_size(hard.generate_source("compact", v1, mutations.v1_flaws(v1)), hard.generate_source("compact", spec))
            assert lines >= hard.MIN_FIX_LINES, e["task_id"]


def test_every_change_has_a_flaw_that_the_reference_client_knows():
    from har2client.heal.mutations import HARD_MUTATIONS
    from har2client.tierb.refclient import FLAWS

    assert {m.name for m in HARD_MUTATIONS} == set(hard.CHANGE_FLAW) and set(hard.CHANGE_FLAW.values()) <= set(FLAWS)


# ------------------------------------------------------------ built tasks


def test_gates_pass_and_the_factors_are_recorded(built):
    for key, d in built.items():
        task, r = read_json(d / "task.json"), ROWS[key]
        heal = task["heal"]
        assert heal["gate"] == {"v1_on_v1": 1.0, "v1_on_v2": heal["gate"]["v1_on_v2"], "ref_on_v2": 1.0} and heal["gate"]["v1_on_v2"] < 1.0
        assert heal["mutations"] == r["mutations"] and heal["changes"] == len(r["mutations"])
        for k in ("error_detail", "evidence", "evidence_scope", "changelog", "client_style"):
            assert heal[k] == r[k], (key, k)
        assert heal["identifiable"] == release.identify.label(r["mutations"], r["evidence"], r["evidence_scope"])
        assert set(heal["seeds"]) == {"spec", "build", "recording", "practice", "test"} and heal["seeds"]["spec"] == r["seed"]
        assert isinstance(heal["fix_lines"], int) and heal["fix_lines"] > 0 and heal["fix_classes"] >= 1
        assert set(heal["necessity"]) == set(r["mutations"]) and max(heal["necessity"].values()) < 1.0


def test_the_sdk_task_has_an_sdk_reference_and_a_fix_in_two_classes_or_more(built):
    d = built["sdk"]
    heal = read_json(d / "task.json")["heal"]
    assert heal["client_style"] == "sdk" and heal["fix_classes"] >= 2 and heal["fix_lines"] >= 30
    assert len((d / "client_v1.py").read_text().splitlines()) >= 400 and "class Transport" in (d / "ref_client.py").read_text()


def test_three_changes_or_more_have_a_fix_of_at_least_30_lines(built):
    for key in ("release", "partial", "terse", "sdk"):
        assert read_json(built[key] / "task.json")["heal"]["fix_lines"] >= 30


def test_tests_are_many_every_op_twice_and_say_which_changes_they_need(built):
    for key, d in built.items():
        tests = read_json(d / "tests.json")
        names = ROWS[key]["mutations"]
        assert len(tests) >= 15
        assert all(sum(t["op"] == op for t in tests) >= 2 for op in OPS)
        assert all(isinstance(t["needs"], list) and set(t["needs"]) <= set(names) for t in tests)
        for n in names:  # at least one test fails without each change
            assert any(n in t["needs"] for t in tests), (key, n)


def test_a_drift_change_is_needed_by_every_test_that_returns_the_field(built):
    d = built["release"]
    tests = read_json(d / "tests.json")
    for t in tests:
        if t["op"] in ("get", "create", "update"):
            assert "add_drift_cents" in t["needs"], t["method"]


def test_idempotency_is_needed_by_every_write_test_and_by_no_read(built):
    tests = read_json(built["single"] / "tests.json")
    for t in tests:
        assert (t["needs"] == ["add_idempotency_key"]) == (t["op"] in ("create", "update", "delete")), t["method"]


def test_the_hidden_paged_lists_need_two_pages(built):
    for d in built.values():
        task = read_json(d / "task.json")
        spec = read_json(d / "engine.json")
        assert hardtests.multi_page_lists(spec, task["seeds"]["test"]) >= 2


def test_visible_files_by_changelog_level(built):
    for key, d in built.items():
        has = ROWS[key]["changelog"] != "none"
        assert ("CHANGELOG.md" in visible_files(d)) == has
        assert (d / "CHANGELOG.md").is_file() == has
        assert not (d / "CHANGELOG.md").is_file() or "CHANGELOG.md" in (d / "goal.md").read_text()
        assert find_leaks(d, read_json(d / "task.json")) == []


def test_goal_states_the_stable_interface(built):
    for d in built.values():
        goal = (d / "goal.md").read_text()
        assert hard.RULE_RETURNS in goal and "Keep the constructor, the method names and the arguments" in goal


def test_failure_shows_a_wrong_value_for_a_silent_drift_and_the_exchange_otherwise(built):
    silent = (built["drift"] / "failure.txt").read_text()
    assert "returned without an exception" in silent and "web app shows" in silent and "Last request" not in silent
    assert re.search(r"list_tickets\(\) returned \d+ items; item \d+ is \{", silent)
    assert read_json(built["drift"] / "task.json")["heal"]["failure"]["index"] == 0
    for key, d in built.items():  # every failure text is one of the two forms
        text = (d / "failure.txt").read_text()
        value_form = "web app shows" in text
        assert value_form != ("Last request of this call" in text), key
        assert value_form or "Exception:" in text or "the result is wrong" in text, key


def test_a_partial_recording_plays_only_some_operations_and_still_shows_every_change(built):
    d = built["partial"]
    har = read_json(d / "session.har")
    spec = read_json(d / "engine.json")
    names = ROWS["partial"]["mutations"]
    assert hard.recording_misses(spec, tuple(names), har) == []
    api = [e["request"]["method"] for e in har["log"]["entries"] if spec["host"] in e["request"]["url"]]
    assert "DELETE" not in api and "PATCH" not in api  # update and delete are not played
    assert "POST" in api and "GET" in api  # create and get are


def test_a_full_recording_plays_every_operation(built):
    har = read_json(built["release"] / "session.har")
    spec = read_json(built["release"] / "engine.json")
    methods = {e["request"]["method"] for e in har["log"]["entries"] if spec["host"] in e["request"]["url"]}
    assert {"GET", "POST", "PATCH", "DELETE"} <= methods


def test_complete_and_incomplete_and_misleading_changelogs(built):
    spec = read_json(built["release"] / "engine.json")
    text = (built["release"] / "CHANGELOG.md").read_text()
    assert all(f in text for n in ROWS["release"]["mutations"] for f in changelog.truth(n, spec))
    info = read_json(built["terse"] / "task.json")["heal"]["changelog_info"]
    assert info["level"] == "incomplete" and info["omitted"] in ROWS["terse"]["mutations"]
    mis = read_json(built["partial"] / "task.json")["heal"]["changelog_info"]["misleading"]
    text = (built["partial"] / "CHANGELOG.md").read_text()
    assert mis["stated"] in text and mis["truth"] not in text
    har_spec = read_json(built["partial"] / "engine.json")
    assert hard._shows(mis["change"], har_spec, read_json(built["partial"] / "session.har"))  # the recording shows the truth


def test_terse_task_has_the_terse_engine_and_no_fix_wording(built):
    d = built["terse"]
    assert read_json(d / "engine.json")["error_detail"] == "terse"
    from har2client.heal.build import find_fix_wordings

    assert find_fix_wordings(d, read_json(d / "task.json")) == []


def test_verify_returns_the_scores_and_needs_again(built):
    scores = hard.verify_hard_task(built["single"])
    assert scores["v1_on_v1"] == 1.0 and scores["ref_on_v2"] == 1.0 and scores["necessity"]["add_idempotency_key"] < 1.0
    assert scores["needs"] == [t["needs"] for t in read_json(built["single"] / "tests.json")]


def test_verify_refuses_too_few_tests(built, tmp_path):
    import shutil

    d = tmp_path / "few"
    shutil.copytree(built["single"], d)
    tests = read_json(d / "tests.json")
    (d / "tests.json").write_text(json.dumps(tests[:10]))
    with pytest.raises(HealBuildError, match="hidden tests"):
        hard.verify_hard_task(d)


def test_verify_refuses_a_release_that_does_not_break_the_v1_client(built, tmp_path):
    import shutil

    d = tmp_path / "unbroken"
    shutil.copytree(built["single"], d)
    (d / "client_v1.py").write_text((d / "ref_client.py").read_text())
    task = read_json(d / "task.json")
    task["heal"]["v1_spec"] = read_json(d / "engine.json")
    (d / "task.json").write_text(json.dumps(task))
    with pytest.raises(HealBuildError, match="(still scores 1.0|fix size)"):
        hard.verify_hard_task(d)


def test_verify_refuses_hidden_data_in_the_changelog(built, tmp_path):
    import shutil

    d = tmp_path / "leak"
    shutil.copytree(built["release"], d)
    task = read_json(d / "task.json")
    (d / "CHANGELOG.md").write_text((d / "CHANGELOG.md").read_text() + f"\nToken {task['credentials']['test']['password']}\n")
    with pytest.raises(HealBuildError, match="hidden data"):
        hard.verify_hard_task(d)


def test_the_build_is_deterministic_per_seed(built, tmp_path):
    again = hard.build_hard_task(ROWS["single"], tmp_path, build_seed=3)
    for name in ("goal.md", "client_v1.py", "failure.txt", "session.har", "engine.json", "tests.json", "ref_client.py"):
        assert (again / name).read_text() == (built["single"] / name).read_text(), name
    a, b = read_json(again / "task.json"), read_json(built["single"] / "task.json")
    for key in ("seeds", "credentials", "features"):
        assert a[key] == b[key]
    assert a["heal"]["necessity"] == b["heal"]["necessity"] and a["heal"]["fix_lines"] == b["heal"]["fix_lines"]


def test_bad_factor_values_are_refused(tmp_path):
    for bad in ({"error_detail": "loud"}, {"evidence": "none"}, {"changelog": "wrong"}, {"client_style": "java"}):
        with pytest.raises(ValueError):
            hard.build_hard_task({**ROWS["single"], **bad}, tmp_path)


def test_a_release_with_too_small_a_fix_is_refused_before_any_server_starts(tmp_path):
    small = row("t_small", ["add_drift_epoch", "add_moved", "add_drift_enum"], "tickets", 1105, base={"pagination": "none"})
    spec = make_spec(small["domain"], small["config"], small["seed"])
    from har2client.heal import mutations

    v1 = mutations.derive_v1_spec(spec, small["mutations"], small["seed"])
    lines, _ = hard.fix_size(hard.generate_source("compact", v1, mutations.v1_flaws(v1)), hard.generate_source("compact", spec))
    if lines >= hard.MIN_FIX_LINES:
        pytest.skip("this release has a big enough fix")
    with pytest.raises(HealBuildError, match="changed lines"):
        hard.build_hard_task(small, tmp_path)


# ------------------------------------------------------------ pilot manifest


ROOT = Path(__file__).resolve().parents[1]


def test_pilot_has_38_tasks_in_the_designed_families_and_the_committed_manifest_matches():
    from collections import Counter

    entries = release.select_pilot(1)
    assert len(entries) == 38 and [e["index"] for e in entries] == list(range(38))
    assert dict(Counter(e["set"] for e in entries)) == {"single": 14, "release3": 4, "release4": 4, "partial": 4, "sdk": 4, "misleading": 4, "terse": 4}
    assert [e["seed"] for e in entries] == list(range(1001, 1039)) and len({e["task_id"] for e in entries}) == 38
    assert release.read_manifest(ROOT / "data" / "heal_v3_pilot.jsonl") == [{k: e[k] for k in release.MANIFEST_KEYS} for e in entries]
    assert release.select_pilot(1) == entries and release.select_pilot(2) != entries


def test_pilot_rows_follow_the_release_rules():
    from har2client.heal import mutations

    for e in release.select_pilot(1):
        names = e["mutations"]
        assert len(set(names)) == len(names) <= 4 and cfg.is_valid(e["config"]) and cfg.is_valid(e["base_config"])
        drift = [n for n in names if n in mutations.DRIFT_MUTATIONS]
        if len(names) >= 3:
            assert drift and len(drift) < len(names), e["task_id"]
        if len(names) == 1:
            assert not set(names) & set(release.ONLY_IN_RELEASES)
        assert e["error_detail"] == ("terse" if e["set"] == "terse" else "verbose")
        assert e["client_style"] == ("sdk" if e["set"] == "sdk" else "compact")
        assert e["evidence_scope"] == ("partial" if e["set"] == "partial" else "full")
        assert e["changelog"] == ("misleading" if e["set"] == "misleading" else "none") and e["evidence"] == "both"


def test_pilot_is_balanced_over_domains_and_auth_modes():
    from collections import Counter

    entries = release.select_pilot(1)
    for counts in (Counter(e["domain"] for e in entries), Counter(e["config"]["auth"] for e in entries)):
        assert max(counts.values()) - min(counts.values()) <= 3 and len(counts) >= 3


def test_pilot_singles_are_two_bases_of_each_of_seven_kinds():
    from collections import Counter

    singles = [e["mutations"][0] for e in release.select_pilot(1) if e["set"] == "single"]
    assert Counter(singles) == {n: 2 for n in release.SINGLE_KINDS}


# ------------------------------------------------------------ recording scope


def test_partial_ops_are_a_proper_subset_of_the_operations_that_covers_each_change():
    from har2client.heal import session
    from har2client.heal.mutations import HARD_MUTATIONS

    assert set(session.PARTIAL_OP) == {m.name for m in HARD_MUTATIONS}
    assert session.partial_ops(["add_drift_cents", "add_idempotency_key", "add_moved"]) == ("list", "get", "create")
    assert session.partial_ops(["add_drift_nest", "add_rate_limit"]) == ("list",)
    for n in session.PARTIAL_OP:
        assert set(session.partial_ops([n])) < set(OPS)


def test_the_recording_check_names_the_changes_it_does_not_see(built):
    d = built["single"]
    har, spec = read_json(d / "session.har"), read_json(d / "engine.json")
    assert hard.recording_misses(spec, ("add_idempotency_key",), har) == []
    for e in har["log"]["entries"]:  # a recording without the idempotency header
        e["request"]["headers"] = [h for h in e["request"]["headers"] if h["name"].lower() != spec["idempotency"]["header"].lower()]
    assert hard.recording_misses(spec, ("add_idempotency_key",), har) == ["add_idempotency_key"]
