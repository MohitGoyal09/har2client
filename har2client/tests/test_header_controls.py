import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from test_tierb_support import build_cached, spec_for, start

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_header_controls.py"
HIDDEN_ON = dict(hidden_header="on", auth="bearer_login")


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("make_header_controls", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pair(script, tmp_path_factory):
    src = build_cached(**HIDDEN_ON)
    out = tmp_path_factory.mktemp("controls")
    hint, evidence = script.build_pair(src, out)
    return src, hint, evidence


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_pair_names_and_hidden_files_are_unchanged(pair):
    src, hint, evidence = pair
    assert (hint.name, evidence.name) == (f"{src.name}__hint", f"{src.name}__evidence")
    for copy in (hint, evidence):
        assert sorted(p.name for p in copy.iterdir()) == sorted(p.name for p in src.iterdir())
        for name in ("task.json", "engine.json", "tests.json", "ref_client.py"):
            assert (copy / name).read_bytes() == (src / name).read_bytes()


def test_hint_adds_exactly_one_rules_line_and_keeps_the_har(pair, script):
    src, hint, evidence = pair
    old, new = (src / "goal.md").read_text().splitlines(), (hint / "goal.md").read_text().splitlines()
    assert new == [*old, script.HINT_LINE] and old[-1].startswith("- ")
    assert (hint / "session.har").read_bytes() == (src / "session.har").read_bytes()


def test_evidence_keeps_goal_and_adds_exactly_one_entry(pair):
    src, hint, evidence = pair
    assert (evidence / "goal.md").read_bytes() == (src / "goal.md").read_bytes()
    old, new = _read(src / "session.har")["log"]["entries"], _read(evidence / "session.har")["log"]["entries"]
    assert len(new) == len(old) + 1
    extra = [e for e in new if e not in old]
    assert len(extra) == 1
    assert [e for e in new if e is not extra[0]] == old  # nothing else changed, same order
    assert _read(evidence / "session.har")["log"]["version"] == "1.2"


def test_inserted_entry_is_the_failed_first_api_call(pair):
    src, _, evidence = pair
    hidden = _read(src / "engine.json")["hidden_header"]
    entries = _read(evidence / "session.har")["log"]["entries"]
    api = [i for i, e in enumerate(entries) if any(h["name"].lower() == hidden["name"].lower() for h in e["request"]["headers"])]
    failed = next(i for i, e in enumerate(entries) if e["response"]["status"] == 400)
    assert failed + 1 == api[0]  # the original first API call follows right after (now a retry)
    bad, retry = entries[failed], entries[failed + 1]
    assert (bad["request"]["method"], bad["request"]["url"]) == (retry["request"]["method"], retry["request"]["url"])
    assert bad["request"].get("postData") == retry["request"].get("postData")
    names = [h["name"].lower() for h in bad["request"]["headers"]]
    assert hidden["name"].lower() not in names
    assert names == [h["name"].lower() for h in retry["request"]["headers"] if h["name"].lower() != hidden["name"].lower()]
    assert sum(e["response"]["status"] == 400 for e in entries) == 1


def test_inserted_entry_timing_is_consistent_with_neighbours(pair):
    _, _, evidence = pair
    entries = _read(evidence / "session.har")["log"]["entries"]
    stamps = [e["startedDateTime"] for e in entries]
    assert stamps == sorted(stamps)
    failed = next(i for i, e in enumerate(entries) if e["response"]["status"] == 400)
    bad, retry = entries[failed], entries[failed + 1]
    assert bad["startedDateTime"] < retry["startedDateTime"]
    assert bad["time"] == bad["timings"]["wait"] > 0 and bad["time"] < retry["time"] * 5 + 100
    date = next(h["value"] for h in bad["response"]["headers"] if h["name"] == "date")
    assert date[17:25] == bad["startedDateTime"][11:19]


def test_inserted_entry_has_every_har_1_2_field(pair):
    _, _, evidence = pair
    log = _read(evidence / "session.har")["log"]
    assert {"version", "creator", "entries"} <= set(log)
    for e in log["entries"]:
        assert {"startedDateTime", "time", "request", "response", "cache", "timings"} <= set(e)
        assert {"method", "url", "httpVersion", "cookies", "headers", "queryString", "headersSize", "bodySize"} <= set(e["request"])
        assert {"status", "statusText", "httpVersion", "cookies", "headers", "content", "redirectURL", "headersSize", "bodySize"} <= set(e["response"])
        assert {"size", "mimeType"} <= set(e["response"]["content"]) and {"send", "wait", "receive"} <= set(e["timings"])
        sizes = {h["name"]: h["value"] for h in e["response"]["headers"]}
        if "content-length" in sizes and e["response"]["content"].get("text") is not None:
            assert int(sizes["content-length"]) == len(e["response"]["content"]["text"].encode()) == e["response"]["content"]["size"]


def test_error_body_matches_what_the_live_engine_sends(script):
    engine, base, sess = start(spec_for(**HIDDEN_ON))
    try:
        hidden = sess.spec["hidden_header"]
        resp = httpx.get(sess.path, trust_env=False)
        status, text = script.engine_error_body(hidden)
        assert (resp.status_code, resp.text) == (status, text) == (400, resp.text)
        assert hidden["name"] in text
    finally:
        sess.close()
        engine.stop()


def test_reference_client_scores_one_on_both_copies(pair, script):
    _, hint, evidence = pair
    assert script.verify(hint) == 1.0
    assert script.verify(evidence) == 1.0


def test_main_builds_only_hidden_header_tasks(script, tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    import shutil

    on, off = build_cached(**HIDDEN_ON), build_cached(auth="bearer_login")
    shutil.copytree(on, src / "t_on")
    shutil.copytree(off, src / "t_off")
    assert script.main(["--src", str(src), "--out", str(tmp_path / "out"), "--no-verify"]) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["t_on__evidence", "t_on__hint"]
