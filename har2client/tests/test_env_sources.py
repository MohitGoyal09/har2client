"""Traffic sources: canonical session, mobile HAR, compact rendering and the builder hooks."""

import json
import random
import re

import pytest

from har2client import browser as browser_profile
from har2client.env import sources, tasks
from har2client.env.sources import canon, mobile
from har2client.tierb import config as cfg
from har2client.tierb.build import build_tierb_task, render_goal
from test_tierb_support import api_entries, build_cached, read_json

BROWSER_ONLY = {"origin", "referer", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user", "upgrade-insecure-requests"}
LOGIN = {**cfg.baseline_config(), "auth": "bearer_login", "pagination": "cursor", "etag_update": "on", "hidden_header": "on"}


@pytest.fixture(scope="module")
def mobile_dir(tmp_path_factory):
    row = {"index": 1, "domain": "tickets", "config": LOGIN, "seed": 4242}
    return build_tierb_task(row, tmp_path_factory.mktemp("mobile"), source="mobile_har", noise=0.7, negatives=False)


def entries_of(task_dir):
    return read_json(task_dir / "session.har")["log"]["entries"]


def names(entry):
    return {h["name"].lower() for h in entry["request"]["headers"]}


def host_of(entry):
    return entry["request"]["url"].split("/")[2]


def test_registry_has_the_two_sources_and_rejects_unknown_ones():
    assert set(sources.SOURCES) == {"browser_har", "mobile_har"}
    assert all(s.extension == ".har" for s in sources.SOURCES.values())
    with pytest.raises(ValueError, match="unknown source"):
        sources.get("otel_spans")


def test_canonical_session_keeps_api_headers_and_drops_the_browser_profile():
    d = build_cached(auth="bearer_login", hidden_header="on", etag_update="on")
    har = read_json(d / "session.har")
    real = api_entries(d)
    spec = read_json(d / "engine.json")
    session = canon.from_har({"log": {"entries": real}}, base_path=spec["base_path"], noise=0.0, seed="x")
    assert len(session.exchanges) == len(real)
    names_seen = {k.lower() for e in session.exchanges for k, _ in e.req_headers}
    assert {"authorization", "x-client-version"} <= names_seen or "authorization" in names_seen
    assert not names_seen & (BROWSER_ONLY | {"user-agent", "accept", "host"})
    assert har["log"]["entries"]


def test_mobile_calls_have_app_headers_and_no_browser_headers(mobile_dir):
    api = [e for e in entries_of(mobile_dir) if host_of(e) == read_json(mobile_dir / "engine.json")["host"]]
    assert len(api) > 10
    for e in entries_of(mobile_dir):
        assert not names(e) & BROWSER_ONLY, names(e)
        assert "access-control-allow-origin" not in {h["name"] for h in e["response"]["headers"]}
        assert "_resourceType" not in e
    for e in api:
        assert {"x-device-id", "x-app-version", "user-agent"} <= names(e)
    agents = {h["value"] for e in api for h in e["request"]["headers"] if h["name"] == "user-agent"}
    assert len(agents) == 1 and not agents & {browser_profile.CHROME_UA}
    assert re.search(r"okhttp|CFNetwork", agents.pop())


def test_mobile_noise_is_sdk_traffic_with_the_same_header_profile(mobile_dir):
    host = read_json(mobile_dir / "engine.json")["host"]
    noise = [e for e in entries_of(mobile_dir) if host_of(e) != host]
    api = [e for e in entries_of(mobile_dir) if host_of(e) == host]
    assert len(noise) > len(api)  # noise 0.7
    hosts = " ".join(host_of(e) for e in noise)
    assert any(word in hosts for word in ("crashlytics", "sentry", "adjust", "branch", "fcm", "firebase", "amplitude", "onesignal"))
    base = {"user-agent", "accept", "accept-encoding", "host", "connection"}
    assert all(base <= names(e) for e in noise + api)  # one shared profile
    assert any("x-device-id" in names(e) for e in noise) and any("x-device-id" not in names(e) for e in noise)
    assert any(e["request"]["method"] == "POST" and e["request"]["postData"]["mimeType"] == "application/json" for e in noise)
    assert {"authorization", "x-goog-api-key"} & set().union(*(names(e) for e in noise))  # auth headers are not an API-only signal


def test_mobile_observation_is_deterministic_and_the_same_task_as_browser(tmp_path):
    row = {"index": 2, "domain": "invoices", "config": LOGIN, "seed": 77}
    a = build_tierb_task(row, tmp_path / "a", source="mobile_har", negatives=False)
    b = build_tierb_task(row, tmp_path / "b", source="mobile_har", negatives=False)
    c = build_tierb_task(row, tmp_path / "c", source="browser_har", negatives=False)
    assert (a / "session.har").read_text() == (b / "session.har").read_text()
    assert (a / "session.har").read_text() != (c / "session.har").read_text()
    for name in ("tests.json", "engine.json", "ref_client.py"):
        assert (a / name).read_text() == (c / name).read_text()
    ta, tc = read_json(a / "task.json"), read_json(c / "task.json")
    assert ta["credentials"] == tc["credentials"] and ta["seeds"] == tc["seeds"]
    assert ta["source"] == "mobile_har" and ta["observation_file"] == "session.har" and "source" not in tc


def test_browser_task_json_has_no_new_keys_and_goal_names_the_file(mobile_dir):
    d = build_cached()
    assert not {"source", "observation_file", "compact"} & set(read_json(d / "task.json"))
    goal = (mobile_dir / "goal.md").read_text()
    assert "`session.har`" in goal and "phone app" in goal
    spec = read_json(mobile_dir / "engine.json")
    for word in ("cursor", "bearer", "etag", "if-match", "csrf", "cookie", "x-client", "x-device"):
        assert word not in goal.lower(), word
    assert render_goal(spec) != render_goal(spec, "mobile_har")


def test_only_one_session_file_is_visible(mobile_dir):
    assert tasks.observation_path(mobile_dir).name == "session.har"
    seen = tasks.read_visible(mobile_dir)
    assert set(seen) == {"goal", "observation", "observation_file", "source"} and seen["source"] == "mobile_har"


def test_compact_session_has_no_noise_short_lists_and_keeps_the_cursor(tmp_path):
    row = {"index": 3, "domain": "shipments", "config": LOGIN, "seed": 5}
    full = build_tierb_task(row, tmp_path / "f", source="browser_har", negatives=False)
    short = build_tierb_task(row, tmp_path / "s", source="browser_har", negatives=False, compact=True)
    assert short.joinpath("session.har").stat().st_size < full.joinpath("session.har").stat().st_size / 4
    assert read_json(short / "task.json")["compact"] is True
    entries = entries_of(short)
    assert all(host_of(e) == read_json(short / "engine.json")["host"] for e in entries)  # no noise hosts
    assert "\n" not in (short / "session.har").read_text().strip()  # minified
    lists = [json.loads(e["response"]["content"]["text"]) for e in entries if '"next_' in e["response"]["content"].get("text", "") or "cursor" in e["response"]["content"].get("text", "")]
    key = read_json(short / "engine.json")["items_key"]
    assert lists and all(len(body[key]) <= canon.LIST_LIMIT for body in lists)
    assert any(v for body in lists for k, v in body.items() if k != key)  # a next cursor survived


def test_noise_ratio_arithmetic_and_limits():
    assert canon.noise_count(10, 0.0) == 0
    assert canon.noise_count(10, 0.5) == 10
    with pytest.raises(ValueError):
        canon.noise_count(10, 1.0)


def test_noise_exchanges_fill_templates_and_stay_in_the_window():
    session = canon.from_har({"log": {"entries": api_entries(build_cached())}}, base_path="/v1", noise=0.5, seed="s")
    noise = canon.noise_exchanges(session, mobile.CATALOG, random.Random(1), 20)
    assert len(noise) == 20 and not any("{" in e.url and "}" in e.url for e in noise)
    assert all(not e.api for e in noise)
    assert all(session.start.timestamp() - 5 <= e.started.timestamp() <= session.end.timestamp() + 5 for e in noise)
