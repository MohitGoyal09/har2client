"""Case 3 v2 suite (DESIGN-CASE3.md): identifiable label, manifest, built variants (terse, evidence, compound), validity gates,
leak checks, and the solver on tasks that lack one evidence file."""

import json
import re
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from har2client.heal import build as heal_build
from har2client.heal import identify, mutations, select, suite
from har2client.heal.build import HealBuildError, build_heal_task, find_fix_wordings, find_leaks, verify_heal_task, visible_files
from har2client.solve import beam
from test_beam_solver import FakeClient, response

pytestmark = pytest.mark.slow  # the tests in this file take 5-40 s each

ROOT = Path(__file__).resolve().parents[1]
BUILD_SEED = 1
CORE_ROWS = select.select_rows(1)
MANIFEST = suite.build_manifest(CORE_ROWS, BUILD_SEED)
NAMES = [m.name for m in mutations.MUTATIONS]
FIRST = {n: next(e for e in MANIFEST if e["set"] == "core" and e["mutations"] == [n]) for n in NAMES}
TERSE = {}
for _e in MANIFEST:
    if _e["set"] == "terse":
        TERSE.setdefault(_e["mutations"][0], _e)  # the first terse row of each mutation
COMPOUND = [e for e in MANIFEST if e["set"] == "compound"]
FIX_WORDS = ("log in again", "If-Match header is required", "GET the item for its ETag", "is required")


def build_entry(entry: dict, out: Path) -> Path:
    return build_heal_task(
        suite.task_row(entry), out, build_seed=entry["build_seed"], error_detail=entry["error_detail"], evidence=entry["evidence"],
        task_id=entry["task_id"],
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Tasks built once and never modified: core, terse and failure_only of every mutation's first row, plus a few others."""
    out = tmp_path_factory.mktemp("suite")
    wanted = {
        **{("core", n): FIRST[n] for n in NAMES},
        **{("terse", n): TERSE[n] for n in NAMES},
        **{(ev, n): next(e for e in MANIFEST if e["set"] == "evidence" and e["evidence"] == ev and e["mutations"] == [n]) for ev in ("failure_only", "recording_only") for n in NAMES},
        **{("compound", i): COMPOUND[i] for i in (0, 4, 9)},
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        dirs = list(pool.map(lambda e: build_entry(e, out), wanted.values()))
    return dict(zip(wanted, dirs))


def read_json(path):
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------ identifiable label


def test_identifiable_table():
    both_ok = {n: (identify.identifiable(n, "both"), identify.identifiable(n, "recording_only"), identify.identifiable(n, "failure_only")) for n in NAMES}
    assert both_ok == {
        "add_hidden_header": (True, True, False), "rename_hidden_header": (True, True, False), "apikey_to_bearer_login": (True, True, False),
        "offset_to_cursor": (True, True, False), "none_to_link_header": (True, True, True), "add_envelope": (True, True, True),
        "add_etag": (True, True, True), "add_token_expiry": (True, True, True),
    }


def test_identifiable_table_of_the_v3_changes_and_the_generalize_label():
    hard = [m.name for m in mutations.HARD_MUTATIONS]
    table = {n: (identify.label(n, "both"), identify.label(n, "recording_only"), identify.label(n, "failure_only")) for n in hard}
    assert table == {n: ("yes", "yes", "yes" if n == "add_rate_limit" else "no") for n in hard}
    assert {identify.label([n], "both", "partial") for n in hard} == {"generalize"}
    assert identify.label(["add_etag", "add_envelope"], "failure_only") == "yes" and identify.label("add_etag", "both", "partial") == "generalize"


def test_a_compound_task_is_identifiable_only_when_both_changes_are():
    assert identify.identifiable(["add_etag", "add_envelope"], "failure_only")
    assert not identify.identifiable(["add_etag", "add_hidden_header"], "failure_only")
    assert not identify.identifiable(("apikey_to_bearer_login", "offset_to_cursor"), "failure_only")
    assert identify.identifiable(["add_hidden_header", "offset_to_cursor"], "both")


def test_identifiable_refuses_unknown_inputs():
    with pytest.raises(ValueError):
        identify.identifiable("add_etag", "neither")
    with pytest.raises(ValueError):
        identify.identifiable("flip_a_coin", "both")


def test_every_mutation_has_an_identifiability_rule():
    assert set(identify.NEEDS_RECORDING) | set(identify.FAILURE_ONLY_OK) == set(NAMES)
    assert not set(identify.NEEDS_RECORDING) & set(identify.FAILURE_ONLY_OK)


def check_failure_output_against_the_label(d: Path, mutation: str) -> None:
    """The rule, checked against the real files: an arbitrary fact (header value, login path, cursor parameter) is never in
    failure.txt; the facts of the other mutations are."""
    task, spec = read_json(d / "task.json"), read_json(d / "engine.json")
    text = (d / "failure.txt").read_text()
    assert task["heal"]["identifiable"] == identify.identifiable(mutation, "failure_only")
    facts = {
        "add_hidden_header": spec["hidden_header"] and spec["hidden_header"]["value"],
        "rename_hidden_header": spec["hidden_header"] and spec["hidden_header"]["value"],
        "apikey_to_bearer_login": spec["login"]["path"],
        "offset_to_cursor": f"{spec['page']['param']}=",
    }
    shown = {
        "none_to_link_header": 'rel="next"', "add_envelope": '"data"', "add_etag": "HTTP 428", "add_token_expiry": "HTTP 401",
    }
    if mutation in facts:
        assert facts[mutation] not in text and not task["heal"]["identifiable"]
    else:
        assert shown[mutation] in text and task["heal"]["identifiable"]


@pytest.mark.parametrize("mutation", NAMES)
def test_the_label_matches_what_the_failure_output_shows(built, mutation):
    check_failure_output_against_the_label(built[("failure_only", mutation)], mutation)


@pytest.mark.parametrize("mutation", NAMES)
def test_the_recording_shows_the_facts_the_failure_does_not(built, mutation):
    d = built[("recording_only", mutation)]
    spec, text = read_json(d / "engine.json"), (d / "session.har").read_text()
    if spec["hidden_header"]:
        assert spec["hidden_header"]["value"] in text
    if mutation == "apikey_to_bearer_login":
        assert spec["login"]["path"] in text
    if mutation == "offset_to_cursor":
        assert re.search(rf"[?&]{re.escape(spec['page']['param'])}=", text)
    assert read_json(d / "task.json")["heal"]["identifiable"]


# ------------------------------------------------------------ manifest


def test_manifest_has_144_tasks_in_four_sets():
    assert Counter(e["set"] for e in MANIFEST) == suite.SET_SIZES and len(MANIFEST) == 144
    assert len({e["task_id"] for e in MANIFEST}) == 144


def test_core_is_heal48_with_its_names_and_rows():
    core = [e for e in MANIFEST if e["set"] == "core"]
    assert [e["task_id"] for e in core] == [select.task_id_of(r) for r in CORE_ROWS]
    assert all(e["error_detail"] == "verbose" and e["evidence"] == "both" and e["changes"] == 1 for e in core)
    assert [e["seed"] for e in core] == [r["seed"] for r in CORE_ROWS]


def test_terse_and_evidence_use_the_first_three_bases_of_each_mutation():
    first3 = [r for n in NAMES for r in [r for r in CORE_ROWS if r["mutation"] == n][:3]]
    assert len(first3) == 24
    terse = [e for e in MANIFEST if e["set"] == "terse"]
    assert [e["base_index"] for e in terse] == [r["index"] for r in first3]
    assert all(e["error_detail"] == "terse" and e["evidence"] == "both" for e in terse)
    evidence = [e for e in MANIFEST if e["set"] == "evidence"]
    assert Counter(e["evidence"] for e in evidence) == {"failure_only": 24, "recording_only": 24}
    assert {(e["base_index"], e["evidence"]) for e in evidence} == {(r["index"], ev) for r in first3 for ev in ("failure_only", "recording_only")}
    assert all(e["error_detail"] == "verbose" for e in evidence)
    by_index = {r["index"]: r for r in CORE_ROWS}
    for e in [*terse, *evidence]:  # the same API and seeds as the core task
        r = by_index[e["base_index"]]
        assert (e["seed"], e["domain"], e["config"]) == (r["seed"], r["domain"], r["config"])


def test_compound_rows_are_12_compatible_pairs_with_two_bases_each():
    pairs = [tuple(e["mutations"]) for e in COMPOUND]
    assert len(pairs) == 24 and Counter(pairs) == {p: 2 for p in suite.COMPOUND_PAIRS} and len(suite.COMPOUND_PAIRS) == 12
    assert Counter(n for p in suite.COMPOUND_PAIRS for n in p) == {n: 3 for n in NAMES}
    for e in COMPOUND:
        a, b = e["mutations"]
        assert mutations.SWITCH_GROUP[a] != mutations.SWITCH_GROUP[b]
        spec = make_spec_of(e)
        assert mutations.derive_v1_spec(spec, e["mutations"], e["seed"])["config"] == e["base_config"]
        assert e["changes"] == 2 and e["error_detail"] == "verbose" and e["evidence"] == "both"
    assert len({(e["mutations"][0], e["mutations"][1], e["domain"], json.dumps(e["config"])) for e in COMPOUND}) == 24


def make_spec_of(entry):
    from har2client.tierb.spec import make_spec

    return make_spec(entry["domain"], entry["config"], entry["seed"])


def test_two_mutations_on_the_same_switch_are_refused():
    row = next(r for r in CORE_ROWS if r["mutation"] == "add_hidden_header")
    spec = make_spec_of(row)
    with pytest.raises(ValueError, match="same switch"):
        mutations.derive_v1_spec(spec, ["add_hidden_header", "rename_hidden_header"], 0)
    with pytest.raises(ValueError, match="same switch"):
        mutations.derive_v1_spec(spec, ["offset_to_cursor", "none_to_link_header"], 0)


def test_a_mutation_pair_that_cannot_hold_in_one_config_is_refused():
    row = next(r for r in CORE_ROWS if r["mutation"] == "add_token_expiry")
    with pytest.raises(ValueError, match="does not apply"):
        mutations.derive_v1_spec(make_spec_of(row), ["apikey_to_bearer_login", "add_token_expiry"], 0)


def test_one_mutation_in_a_list_gives_the_same_v1_as_the_name():
    for row in (FIRST[n] for n in NAMES):
        spec = make_spec_of(row)
        assert mutations.derive_v1_spec(spec, row["mutations"], row["seed"]) == mutations.derive_v1_spec(spec, row["mutations"][0], row["seed"])


def test_manifest_is_deterministic_and_the_committed_file_matches(tmp_path):
    assert suite.build_manifest(CORE_ROWS, BUILD_SEED) == MANIFEST
    path = ROOT / "data" / "heal_suite.jsonl"
    assert suite.read_manifest(path) == [{k: e[k] for k in suite.MANIFEST_KEYS} for e in MANIFEST]
    suite.write_manifest(MANIFEST, tmp_path / "m.jsonl")
    assert (tmp_path / "m.jsonl").read_text() == path.read_text()


def test_twelve_failure_only_tasks_are_not_identifiable():
    assert Counter((e["set"], e["evidence"], e["identifiable"]) for e in MANIFEST if not e["identifiable"]) == {("evidence", "failure_only", False): 12}


# ------------------------------------------------------------ built tasks: layout, gates, pairing


def test_terse_tasks_have_the_terse_engine_and_pass_every_gate(built):
    for n in NAMES:
        d = built[("terse", n)]
        task = read_json(d / "task.json")
        assert read_json(d / "engine.json")["error_detail"] == "terse" and task["heal"]["error_detail"] == "terse"
        assert task["heal"]["set"] == "terse" and task["heal"]["mutations"] == [n] and task["heal"]["evidence"] == "both"
        scores = verify_heal_task(d)
        assert scores["v1_on_v1"] == 1.0 and scores["v1_on_v2"] < 1.0 and scores["ref_on_v2"] == 1.0
        assert find_fix_wordings(d, task) == [] and find_leaks(d, task) == []


@pytest.mark.parametrize("mutation", NAMES)
def test_terse_tasks_do_not_contain_the_fix_wordings(built, mutation):
    d = built[("terse", mutation)]
    spec = read_json(d / "engine.json")
    for name in visible_files(d):
        text = (d / name).read_text()
        assert not any(w in text for w in FIX_WORDS), name
        assert '"message"' not in text or name == "goal.md", name
    har = (d / "session.har").read_text() if (d / "session.har").is_file() else ""
    for entry in read_json(d / "session.har")["log"]["entries"]:
        if entry["response"]["status"] >= 400:
            body = entry["response"]["content"].get("text", "")
            assert set(json.loads(body)) == {"error"}
            if spec["hidden_header"]:
                assert spec["hidden_header"]["name"].lower() not in body.lower()
    if mutation in ("add_hidden_header", "rename_hidden_header"):  # the header may show in the recording: that is evidence
        assert spec["hidden_header"]["name"].lower() in har.lower()
        failure = (d / "failure.txt").read_text().split("Its response:")[1]
        assert spec["hidden_header"]["name"].lower() not in failure.lower()
        assert '{"error": "bad_request"}' in failure


def test_terse_token_expiry_recording_shows_a_generic_401_and_a_new_login(built):
    d = built[("terse", "add_token_expiry")]
    entries = read_json(d / "session.har")["log"]["entries"]
    spec = read_json(d / "engine.json")
    expired = [e for e in entries if e["response"]["status"] == 401]
    assert len(expired) == 1 and json.loads(expired[0]["response"]["content"]["text"]) == {"error": "token_invalid"}
    assert len([e for e in entries if e["request"]["url"].endswith(spec["login"]["path"])]) == 2


def test_the_gate_refuses_fix_wording_in_a_terse_task(built, tmp_path):
    work = tmp_path / "t"
    shutil.copytree(built[("terse", "add_etag")], work)
    (work / "failure.txt").write_text((work / "failure.txt").read_text() + '\n{"message": "If-Match header is required"}\n')
    with pytest.raises(HealBuildError, match="fix wording"):
        verify_heal_task(work)
    # the hidden header name in an error response of the recording
    work2 = tmp_path / "t2"
    shutil.copytree(built[("terse", "add_hidden_header")], work2)
    name = read_json(work2 / "engine.json")["hidden_header"]["name"]
    har = read_json(work2 / "session.har")
    har["log"]["entries"][0]["response"].update(status=400)
    har["log"]["entries"][0]["response"]["content"]["text"] = json.dumps({"error": f"need {name}"})
    (work2 / "session.har").write_text(json.dumps(har))
    assert any("error response" in w for w in find_fix_wordings(work2, read_json(work2 / "task.json")))


@pytest.mark.parametrize("evidence,present,absent", [("failure_only", "failure.txt", "session.har"), ("recording_only", "session.har", "failure.txt")])
def test_evidence_tasks_show_one_channel_and_pass_the_gates(built, evidence, present, absent):
    for n in NAMES:
        d = built[(evidence, n)]
        assert {p.name for p in d.iterdir()} == {"goal.md", "client_v1.py", present, "task.json", "tests.json", "engine.json", "ref_client.py"}
        task = read_json(d / "task.json")
        assert task["heal"]["set"] == "evidence" and task["heal"]["evidence"] == evidence and task["heal"]["error_detail"] == "verbose"
        goal = (d / "goal.md").read_text()
        assert present in goal and absent not in goal and "client_v1.py" in goal
        scores = verify_heal_task(d)
        assert scores["v1_on_v2"] < 1.0 and scores["ref_on_v2"] == 1.0
        assert find_leaks(d, task) == []


def test_verify_refuses_a_task_whose_files_do_not_match_its_evidence(built, tmp_path):
    work = tmp_path / "t"
    shutil.copytree(built[("recording_only", "add_etag")], work)
    shutil.copy(built[("core", "add_etag")] / "failure.txt", work / "failure.txt")
    with pytest.raises(HealBuildError, match="visible files"):
        verify_heal_task(work)


@pytest.mark.parametrize("mutation", NAMES)
def test_variants_of_a_row_are_paired(built, mutation):
    """A terse or single-channel task differs from its core task in the studied factor only: same hidden tests, credentials,
    seeds, client_v1 and reference client; the shown evidence files of the verbose variants are the core files."""
    core = built[("core", mutation)]
    ct = read_json(core / "task.json")
    for key in (("terse", mutation), ("failure_only", mutation), ("recording_only", mutation)):
        d = built[key]
        t = read_json(d / "task.json")
        for name in ("tests.json", "client_v1.py"):
            assert (d / name).read_text() == (core / name).read_text(), (key, name)
        assert (t["credentials"], t["seeds"], t["heal"]["practice"], t["heal"]["v1_credentials"]) == (ct["credentials"], ct["seeds"], ct["heal"]["practice"], ct["heal"]["v1_credentials"])
    for ev, name in (("failure_only", "failure.txt"), ("recording_only", "session.har")):
        assert (built[(ev, mutation)] / name).read_text() == (core / name).read_text()
        assert (built[(ev, mutation)] / "engine.json").read_text() == (core / "engine.json").read_text()


def test_compound_tasks_carry_two_changes_and_pass_every_gate(built):
    for i in (0, 4, 9):
        d = built[("compound", i)]
        task = read_json(d / "task.json")
        a, b = COMPOUND[i]["mutations"]
        assert task["heal"]["set"] == "compound" and task["heal"]["mutations"] == [a, b] and task["heal"]["mutation"] == f"{a}+{b}"
        assert {f"mutation:{a}", f"mutation:{b}"} <= set(task["features"]) and task["heal"]["identifiable"] is True
        scores = verify_heal_task(d)
        assert scores["v1_on_v2"] < 1.0 and scores["ref_on_v2"] == 1.0
        base, v2 = task["heal"]["base_config"], task["config"]
        assert sum(base[k] != v2[k] for k in v2) >= 1  # the v1 config differs (rename does not change the config)
        assert d.name == COMPOUND[i]["task_id"] and {p.name for p in d.iterdir()} >= {"goal.md", "client_v1.py", "failure.txt", "session.har"}


def test_the_old_single_mutation_row_still_builds(tmp_path):
    row = {k: v for k, v in FIRST["add_etag"].items() if k in ("index", "domain", "config", "seed")} | {"mutation": "add_etag"}
    d = build_heal_task(row, tmp_path, build_seed=BUILD_SEED)
    heal = read_json(d / "task.json")["heal"]
    assert heal["mutation"] == "add_etag" and heal["mutations"] == ["add_etag"] and heal["set"] == "core" and heal["identifiable"] is True
    assert d.name == select.task_id_of(row)


def test_bad_factor_values_are_refused(tmp_path):
    with pytest.raises(ValueError, match="error_detail"):
        build_heal_task(suite.task_row(FIRST["add_etag"]), tmp_path, error_detail="chatty")
    with pytest.raises(ValueError, match="evidence"):
        build_heal_task(suite.task_row(FIRST["add_etag"]), tmp_path, evidence="neither")


def test_both_evidence_goal_is_the_heal48_goal():
    row = FIRST["add_envelope"]
    text = heal_build.render_heal_goal(make_spec_of(row))
    assert text == heal_build.render_heal_goal(make_spec_of(row), "both")
    assert "- `failure.txt`:" in text and "- `session.har`:" in text and text.index("client_v1.py`: the client") < text.index("failure.txt`:") < text.index("session.har`:")


# ------------------------------------------------------------ solver on tasks with one evidence file


def user_message(fake):
    return next(m["content"] for m in fake.requests[0]["messages"] if m["role"] == "user")


def test_the_solver_sends_only_the_files_that_exist(built, tmp_path):
    secret = lambda d: read_json(d / "task.json")["credentials"]["test"]["password"]  # noqa: E731
    d = built[("failure_only", "add_etag")]
    fake = FakeClient([response()])
    log = beam.solve(d, tmp_path / "a" / "c.py", client=fake)
    text = user_message(fake)
    assert (d / "failure.txt").read_text().strip() in text and (d / "client_v1.py").read_text().strip() in text
    assert "Recorded session" not in text and log["extra_files"] == ["client_v1.py", "failure.txt"] and log["status"] == "ok"
    assert secret(d) not in json.dumps(fake.requests)
    d = built[("recording_only", "add_etag")]
    fake = FakeClient([response()])
    log = beam.solve(d, tmp_path / "b" / "c.py", client=fake)
    text = user_message(fake)
    assert "# Recorded session (HAR)" in text and "Failure of the previous client" not in text and log["extra_files"] == ["client_v1.py"]
    assert secret(d) not in json.dumps(fake.requests)


def test_a_case_1_task_without_a_session_file_is_still_an_error(tmp_path):
    d = tmp_path / "t"
    d.mkdir()
    (d / "goal.md").write_text("GOAL")
    with pytest.raises(beam.BeamError, match="session file"):
        beam.solve(d, tmp_path / "c.py", client=FakeClient([response()]))


def test_the_cost_estimate_handles_a_task_without_a_recording(built):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("estimate_cost", ROOT / "scripts" / "estimate_cost.py")
    ec = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("estimate_cost", ec)
    spec.loader.exec_module(ec)
    assert ec.task_prompt_tokens(built[("failure_only", "add_etag")]) < ec.task_prompt_tokens(built[("core", "add_etag")])


# ------------------------------------------------------------ the built suite on disk (skipped when runs/heal_suite is absent)

SUITE_DIR = ROOT / "runs" / "heal_suite"


@pytest.mark.skipif(not SUITE_DIR.is_dir(), reason="runs/heal_suite is not built")
def test_the_built_suite_matches_the_manifest_and_leaks_nothing():
    assert sorted(p.name for p in SUITE_DIR.iterdir()) == sorted(e["task_id"] for e in MANIFEST)
    for e in MANIFEST:
        d = SUITE_DIR / e["task_id"]
        task = read_json(d / "task.json")
        heal = task["heal"]
        assert (heal["set"], heal["mutations"], heal["error_detail"], heal["evidence"], heal["identifiable"]) == (
            e["set"], e["mutations"], e["error_detail"], e["evidence"], e["identifiable"])
        assert set(visible_files(d)) == {"goal.md", "client_v1.py", *heal_build.EVIDENCE_FILES[e["evidence"]]}
        assert find_leaks(d, task) == []
        if e["error_detail"] == "terse":
            assert find_fix_wordings(d, task) == []
        if e["evidence"] == "failure_only":
            check_failure_output_against_the_label(d, e["mutations"][0])
        gate = heal["gate"]
        assert gate["v1_on_v1"] == 1.0 and gate["v1_on_v2"] < 1.0 and gate["ref_on_v2"] == 1.0


@pytest.mark.skipif(not (ROOT / "runs" / "heal48").is_dir() or not SUITE_DIR.is_dir(), reason="runs/heal48 or runs/heal_suite is not built")
def test_core_set_files_are_byte_identical_to_heal48():
    for d in sorted((ROOT / "runs" / "heal48").iterdir()):
        for name in ("goal.md", "client_v1.py", "failure.txt", "session.har", "engine.json", "tests.json", "ref_client.py"):
            assert (d / name).read_bytes() == (SUITE_DIR / d.name / name).read_bytes(), (d.name, name)
        new, old = read_json(SUITE_DIR / d.name / "task.json"), read_json(d / "task.json")
        for key in ("set", "mutations", "error_detail", "evidence", "identifiable"):
            new["heal"].pop(key)
        for t in (new, old):
            t["heal"]["failure"].pop("detail")  # holds the random port of the practice server
        assert new == old


# ------------------------------------------------------------ build script, --suite

def test_script_builds_a_suite_subset_and_writes_the_manifest(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_heal", ROOT / "scripts" / "build_heal.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    manifest = tmp_path / "m.jsonl"
    code = script.main([
        "--suite", "--out", str(tmp_path / "s"), "--seed", "1", "--configs", str(tmp_path / "c.jsonl"), "--manifest", str(manifest),
        "--sets", "evidence", "--limit", "2", "--jobs", "2",
    ])
    assert code == 0
    assert len(suite.read_manifest(manifest)) == 144
    names = sorted(p.name for p in (tmp_path / "s").iterdir())
    assert names == sorted(e["task_id"] for e in [e for e in MANIFEST if e["set"] == "evidence"][:2])
    out = capsys.readouterr().out
    assert "built 2/2 suite tasks" in out and "identifiable=" in out and not (tmp_path / "s_scratch").exists()
