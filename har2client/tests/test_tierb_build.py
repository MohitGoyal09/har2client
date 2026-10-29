import json
import re

import pytest

from har2client.tierb import config as cfg
from har2client.tierb.build import TierBBuildError, build_tierb_task, render_goal
from test_tierb_support import api_entries, build_cached, hdr, read_json

LOGIN_MODES = dict(auth="bearer_login")


# ------------------------------------------------------------ layout and contract


def test_task_directory_layout_and_task_json():
    d = build_cached(auth="cookie_csrf", pagination="cursor", token_expiry="on", etag_update="on", hidden_header="on", envelope="on")
    assert {p.name for p in d.iterdir()} == {"goal.md", "session.har", "task.json", "tests.json", "engine.json", "ref_client.py"}
    task = read_json(d / "task.json")
    assert task["tier"] == "B" and task["config"]["pagination"] == "cursor" and task["domain"] == "shipments"
    assert set(task["credentials"]) == {"recording", "test"}
    rec, test = task["credentials"]["recording"], task["credentials"]["test"]
    assert set(rec) == set(test) == {"username", "password"} and rec["password"] != test["password"] and rec["username"] != test["username"]
    assert task["seeds"]["recording"] != task["seeds"]["test"]
    assert {"tier:B", "auth:cookie_csrf", "pagination:cursor", "token_expiry", "etag_update", "hidden_header", "envelope"} <= set(task["features"])
    assert {"task_id", "api_id", "goal_ops", "seeds", "build_seed", "noise_ratio", "har_entries"} <= set(task)
    assert task["goal_ops"] == ["list_shipments", "get_shipment", "create_shipment", "update_shipment", "delete_shipment"]


def test_tests_follow_the_design_order_and_use_fresh_ids():
    d = build_cached()
    tests = read_json(d / "tests.json")
    assert [t["op"] for t in tests] == ["list", "list", "get", "get", "create", "update", "delete"]
    assert tests[1]["kwargs"] and list(tests[1]["kwargs"]) == [read_json(d / "engine.json")["filter"]]
    ids = [t["kwargs"]["id"] for t in tests if "id" in t["kwargs"]]
    assert len(set(ids)) == 4
    assert not any(i in (d / "session.har").read_text() for i in ids)


def test_goal_md_states_the_constructor_and_methods_but_no_mechanism():
    d = build_cached(auth="cookie_csrf", pagination="link_header", token_expiry="on", etag_update="on", hidden_header="on", envelope="on")
    goal = (d / "goal.md").read_text()
    spec = read_json(d / "engine.json")
    assert "`Client(base_url: str, username: str, password: str)`" in goal
    for op in ("list", "get", "create", "update", "delete"):
        assert f"`{op}_" in goal
    assert "all pages" in goal and "not a wrapper" in goal and spec["server_url"] in goal
    for word in ("cursor", "offset", "etag", "if-match", "csrf", "cookie", "bearer", "x-client", "expire", "envelope", spec["api_key_header"].lower()):
        assert word not in goal.lower(), word
    hidden = spec["hidden_header"]
    assert hidden["name"] not in goal and hidden["value"] not in goal


def test_goal_md_is_the_same_text_apart_from_names_for_every_config():
    a = render_goal(read_json(build_cached() / "engine.json"))
    b = render_goal(read_json(build_cached(auth="bearer_login", pagination="offset", envelope="on") / "engine.json"))
    strip = lambda t: re.sub(r"`[^`]*`|# .*", "", t)  # noqa: E731
    assert strip(a) == strip(b)


def test_session_har_has_no_loopback_and_only_expected_files_are_visible():
    d = build_cached(auth="cookie_csrf", pagination="link_header")
    text = (d / "session.har").read_text()
    assert "127.0.0.1" not in text and "localhost" not in text
    task = read_json(d / "task.json")
    assert task["credentials"]["test"]["password"] not in text and task["credentials"]["test"]["username"] not in text


def test_har_is_a_mix_of_api_calls_and_noise_with_the_browser_header_profile():
    d = build_cached(auth="bearer_login")
    har = read_json(d / "session.har")
    entries = har["log"]["entries"]
    assert har["log"]["version"] == "1.2" and len(api_entries(d)) < len(entries)
    assert any("google" in e["request"]["url"] or "static" in e["request"]["url"] for e in entries)
    first = api_entries(d)[0]
    names = [h["name"] for h in first["request"]["headers"]]
    assert {"user-agent", "sec-fetch-mode", "referer", "origin"} <= set(names)


def test_build_is_deterministic_for_a_row():
    row = {"index": 3, "domain": "tickets", "config": cfg.baseline_config(), "seed": 42}
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        da = build_tierb_task(row, a, negatives=False)
        db = build_tierb_task(row, b, negatives=False)
        for name in ("goal.md", "engine.json", "tests.json", "session.har"):
            assert (da / name).read_text() == (db / name).read_text(), name
        ta, tb = read_json(da / "task.json"), read_json(db / "task.json")
        assert ta == tb and Path(da).name == "tierb_03_tickets"


# ------------------------------------------------------------ the recording shows each switch that is on


def test_login_appears_in_the_recording_for_login_modes_only():
    for auth, expect in (("apikey_header", False), ("bearer_login", True), ("cookie_csrf", True)):
        d = build_cached(auth=auth)
        spec = read_json(d / "engine.json")
        logins = [e for e in api_entries(d) if e["request"]["url"].endswith(spec["login"]["path"])]
        assert bool(logins) is expect, auth
        if logins:
            assert logins[0] == api_entries(d)[0] and logins[0]["request"]["method"] == "POST"
            sent = logins[0]["request"]["postData"]["text"]
            creds = read_json(d / "task.json")["credentials"]["recording"]
            assert creds["username"] in sent and creds["password"] in sent
            assert ("json" in logins[0]["request"]["postData"]["mimeType"]) == (spec["login"]["format"] == "json")


@pytest.mark.parametrize("style", ["offset", "cursor", "link_header"])
def test_recording_follows_two_or_more_pages_with_and_without_the_filter(style):
    d = build_cached(pagination=style)
    spec = read_json(d / "engine.json")
    lists = [e for e in api_entries(d) if e["request"]["method"] == "GET" and e["request"]["url"].split("?")[0].endswith("/" + spec["path_word"])]
    plain = [e for e in lists if spec["filter"] not in e["request"]["url"]]
    filtered = [e for e in lists if spec["filter"] in e["request"]["url"]]
    assert len(plain) >= 3 and len(filtered) >= 1
    assert sum(1 for e in plain if "?" in e["request"]["url"]) >= 2  # page 2, 3 carry a page parameter
    if style == "link_header":
        assert any(hdr(e["response"], "link") and 'rel="next"' in hdr(e["response"], "link") for e in plain)
        assert "127.0.0.1" not in json.dumps([e["response"]["headers"] for e in plain])


def test_pagination_none_records_single_lists():
    d = build_cached(pagination="none")
    spec = read_json(d / "engine.json")
    lists = [e for e in api_entries(d) if e["request"]["url"].split("?")[0].endswith("/" + spec["path_word"]) and e["request"]["method"] == "GET"]
    assert len(lists) == 2


@pytest.mark.parametrize("auth", ["bearer_login", "cookie_csrf"])
def test_expiry_shows_a_401_followed_by_a_new_login(auth):
    d = build_cached(auth=auth, token_expiry="on")
    spec = read_json(d / "engine.json")
    calls = api_entries(d)
    status401 = [i for i, e in enumerate(calls) if e["response"]["status"] == 401]
    assert status401
    login_idx = [i for i, e in enumerate(calls) if e["request"]["url"].endswith(spec["login"]["path"])]
    assert len(login_idx) >= 2 and any(i > status401[0] for i in login_idx)
    assert "error" in json.loads(calls[status401[0]]["response"]["content"]["text"])


def test_no_401_without_expiry():
    d = build_cached(auth="bearer_login")
    assert all(e["response"]["status"] != 401 for e in api_entries(d))


def test_etag_flow_get_then_patch_with_if_match():
    d = build_cached(etag_update="on")
    calls = api_entries(d)
    patch_i = next(i for i, e in enumerate(calls) if e["request"]["method"] == "PATCH")
    patch = calls[patch_i]
    previous = calls[patch_i - 1]
    assert previous["request"]["method"] == "GET" and previous["request"]["url"] == patch["request"]["url"]
    etag = hdr(previous["response"], "etag")
    assert etag and hdr(patch["request"], "if-match") == etag
    assert patch["response"]["status"] == 200


def test_without_etag_switch_patch_has_no_if_match():
    d = build_cached()
    patch = next(e for e in api_entries(d) if e["request"]["method"] == "PATCH")
    assert hdr(patch["request"], "if-match") is None and hdr(patch["response"], "etag") is None


def test_hidden_header_is_on_every_api_call_when_enabled():
    d = build_cached(auth="bearer_login", hidden_header="on")
    hidden = read_json(d / "engine.json")["hidden_header"]
    assert all(hdr(e["request"], hidden["name"]) == hidden["value"] for e in api_entries(d))


def test_cookie_csrf_flow_is_visible():
    d = build_cached(auth="cookie_csrf")
    spec = read_json(d / "engine.json")
    calls = api_entries(d)
    login = calls[0]
    assert "set-cookie" in {h["name"].lower() for h in login["response"]["headers"]}
    writes = [e for e in calls if e["request"]["method"] in ("POST", "PATCH", "DELETE") and not e["request"]["url"].endswith(spec["login"]["path"])]
    assert writes and all(hdr(e["request"], spec["csrf"]["header"]) and hdr(e["request"], "cookie") for e in writes)
    reads = [e for e in calls if e["request"]["method"] == "GET"]
    assert all(hdr(e["request"], "cookie") for e in reads)


def test_envelope_shows_in_recorded_bodies():
    d = build_cached(envelope="on")
    ok = [e for e in api_entries(d) if e["response"]["status"] == 200]
    assert all(set(json.loads(e["response"]["content"]["text"])) == {"data", "meta"} for e in ok)


def test_apikey_header_is_sent_on_every_call():
    d = build_cached()
    spec = read_json(d / "engine.json")
    key = read_json(d / "task.json")["credentials"]["recording"]["password"]
    assert all(hdr(e["request"], spec["api_key_header"]) == key for e in api_entries(d))


# ------------------------------------------------------------ the validity gate


def test_build_runs_negative_clients_when_asked(tmp_path):
    row = {"index": 0, "domain": "samples", "config": {**cfg.baseline_config(), "auth": "bearer_login", "pagination": "offset", "token_expiry": "on", "etag_update": "on", "hidden_header": "on", "envelope": "on"}, "seed": 5}
    d = build_tierb_task(row, tmp_path, negatives=True)
    assert (d / "task.json").exists()


def test_build_fails_when_the_reference_client_does_not_score_one(tmp_path, monkeypatch):
    from har2client.tierb import build

    monkeypatch.setattr(build, "make_tests", lambda spec, seed: [{"op": "get", "method": "get_" + spec["singular"], "kwargs": {"id": "NOPE-0"}}])
    row = {"index": 0, "domain": "samples", "config": cfg.baseline_config(), "seed": 5}
    with pytest.raises(TierBBuildError):
        build_tierb_task(row, tmp_path, negatives=False)
