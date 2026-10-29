"""Real API entries and noise entries share one browser header profile (no header-name leak)."""

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from har2client import build_task, ops

FIX = Path(__file__).parent / "fixtures"
CASES = [
    ("bookshelf_apikey.yaml", 1),
    ("bookshelf_apikey.yaml", 2),
    ("bookshelf_bearer.yaml", 3),
    ("bookshelf_basic.yaml", 4),
    ("bookshelf_apikey.yaml", 5),
]
RESPONSE_BASE = {"date", "content-type", "content-length", "server", "cache-control", "access-control-allow-origin"}


@pytest.fixture(scope="module")
def tasks(tmp_path_factory):
    out = []
    for name, seed in CASES:
        d = build_task.build_task(
            ops.load_spec(FIX / name), "acme:bookshelf", tmp_path_factory.mktemp("t"), noise=0.7, seed=seed
        )
        table = json.loads((d / "op_table.json").read_text())
        entries = json.loads((d / "session.har").read_text())["log"]["entries"]
        api_prefix = table["server_url"]
        real = [e for e in entries if e["request"]["url"].startswith(api_prefix + "/")]
        noise = [e for e in entries if not e["request"]["url"].startswith(api_prefix + "/")]
        out.append({"table": table, "real": real, "noise": noise})
    return out


def req_names(entry):
    return {h["name"].lower() for h in entry["request"]["headers"]}


def req_header(entry, name):
    return next((h["value"] for h in entry["request"]["headers"] if h["name"].lower() == name), None)


def api_only(table):
    sec = table["security"]
    return {"authorization", "content-type"} | ({sec["name"].lower()} if sec["type"] == "apiKey" and sec["in"] == "header" else set())


def is_fetch(entry):
    return req_header(entry, "sec-fetch-dest") == "empty"


def test_api_and_fetch_noise_have_the_same_request_header_names(tasks):
    fetch_noise = 0
    for t in tasks:
        extra = api_only(t["table"])
        api_sets = {frozenset(req_names(e) - extra) for e in t["real"]}
        noise_sets = {frozenset(req_names(e) - extra) for e in t["noise"] if is_fetch(e)}
        fetch_noise += sum(1 for e in t["noise"] if is_fetch(e))
        assert len(api_sets) == 1 and api_sets == noise_sets
    assert fetch_noise > 0
    names = req_names(tasks[0]["real"][0]) - api_only(tasks[0]["table"])
    assert names == {
        "host", "accept", "accept-language", "accept-encoding", "connection", "user-agent",
        "referer", "origin", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-dest",
    }


def test_header_order_does_not_separate_api_from_noise(tasks):
    for t in tasks:
        extra = api_only(t["table"])
        orders = {
            tuple(h["name"].lower() for h in e["request"]["headers"] if h["name"].lower() not in extra)
            for e in [*t["real"], *[n for n in t["noise"] if is_fetch(n)]]
        }
        assert len(orders) == 1


def test_api_calls_look_like_browser_fetch(tasks):
    for t in tasks:
        for e in t["real"]:
            assert req_header(e, "sec-fetch-dest") == "empty" and req_header(e, "accept") == "application/json"
            assert "Chrome/" in req_header(e, "user-agent") and "Macintosh" in req_header(e, "user-agent")
            origin, referer = req_header(e, "origin"), req_header(e, "referer")
            host = urlparse(t["table"]["server_url"]).hostname
            assert referer == origin + "/" and urlparse(origin).hostname.endswith(host.split(".", 1)[1])
            assert req_header(e, "host") == urlparse(t["table"]["server_url"]).netloc


def test_noise_header_values_follow_resource_type(tasks):
    seen = {}
    for t in tasks:
        for e in t["noise"]:
            seen.setdefault(req_header(e, "sec-fetch-dest"), set()).add(req_header(e, "accept"))
    assert {"script", "style", "font", "image", "empty"} <= set(seen)
    assert "application/json" not in seen["script"] | seen["style"] | seen["image"]


def test_response_header_names_match_between_api_and_noise(tasks):
    for t in tasks:
        for e in [*t["real"], *t["noise"]]:
            names = {h["name"].lower() for h in e["response"]["headers"]}
            assert names == RESPONSE_BASE, (e["request"]["url"], names)
            length = next(h["value"] for h in e["response"]["headers"] if h["name"].lower() == "content-length")
            assert int(length) == len(e["response"]["content"]["text"].encode())


def test_same_origin_json_noise_is_about_15_percent_and_outside_base_path(tasks):
    total = json_noise = 0
    for t in tasks:
        api = urlparse(t["table"]["server_url"])
        for e in t["noise"]:
            total += 1
            url = urlparse(e["request"]["url"])
            if url.netloc == api.netloc and is_fetch(e) and e["response"]["content"]["mimeType"].startswith("application/json"):
                json_noise += 1
                assert not url.path.startswith(t["table"]["base_path"] + "/") and url.path != t["table"]["base_path"]
                assert req_header(e, "accept") == "application/json"
                json.loads(e["response"]["content"]["text"])
    assert 0.10 <= json_noise / total <= 0.20, (json_noise, total)
