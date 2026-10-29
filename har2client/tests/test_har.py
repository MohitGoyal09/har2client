import base64
import copy
import json
import random
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

from har2client import har, ops
from har2client.mock import MockServer
from har2client.player import gen_args, play_session, wire

FIX = Path(__file__).parent / "fixtures"
SECRET = "sk_" + "cd34" * 6
GOAL = ["list_books", "get_book", "create_book", "books_by_one_author", "category_tree"]


def table(name="bookshelf_apikey.yaml"):
    return ops.normalize(ops.load_spec(FIX / name), "acme:bookshelf")


def record(t=None, secret=SECRET, goal=GOAL, seed=5):
    t = t or table()
    m = MockServer(t, seed=seed, secret=secret)
    base = m.start()
    rec = har.HarRecorder()
    try:
        calls = play_session(t, goal, base, secret, random.Random(seed), recorder=rec)
    finally:
        m.stop()
    return rec.har(), calls, t


def test_wire_matches_http_serialization():
    assert wire(True) == "true" and wire(False) == "false"
    assert wire(3) == "3" and wire(1.5) == "1.5" and wire("a b") == "a b"


def test_gen_args_required_always_optional_sometimes():
    t = table()
    lb = next(o for o in t["operations"] if o["op_id"] == "list_books")
    seen = [set(gen_args(lb, random.Random(i))) for i in range(60)]
    assert any("limit" in s for s in seen) and any("limit" not in s for s in seen)
    ab = next(o for o in t["operations"] if o["op_id"] == "books_by_one_author")
    assert all({"author_id", "page"} <= set(gen_args(ab, random.Random(i))) for i in range(30))
    cb = next(o for o in t["operations"] if o["op_id"] == "create_book")
    assert all(isinstance(gen_args(cb, random.Random(i))["body"], dict) for i in range(30))


def test_gen_args_path_values_are_url_safe_single_segments():
    t = table()
    gb = next(o for o in t["operations"] if o["op_id"] == "get_book")
    gb["params"][0]["schema"] = {"enum": ["a/b", "", "..", "x y"]}
    for i in range(40):
        v = gen_args(gb, random.Random(i))["book_id"]
        assert v and "/" not in v and v not in (".", "..")


def test_session_calls_are_valid_and_cover_every_param():
    har_doc, calls, t = record()
    ops_by_id = {o["op_id"]: o for o in t["operations"]}
    assert {c["op_id"] for c in calls} == set(GOAL)
    for op_id in GOAL:
        mine = [c for c in calls if c["op_id"] == op_id]
        assert 1 <= len(mine) <= 3
        assert all(200 <= c["status"] < 300 for c in mine)
        used = set().union(*(set(c["kwargs"]) for c in mine))
        want = {p["py_name"] for p in ops_by_id[op_id]["params"]} | ({"body"} if ops_by_id[op_id]["body"] else set())
        assert used == want, op_id
    assert len(har_doc["log"]["entries"]) == len(calls)


def test_har_structure_is_valid_12():
    doc, calls, _ = record()
    log = doc["log"]
    assert log["version"] == "1.2" and log["creator"]["name"]
    for e in log["entries"]:
        assert e["startedDateTime"].endswith("Z") and isinstance(e["time"], (int, float))
        r, s = e["request"], e["response"]
        assert r["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE")
        assert r["url"].startswith("https://api.bookshelf.example/v1") or r["url"].startswith("http://127.0.0.1")
        assert all({"name", "value"} <= set(h) for h in r["headers"] + s["headers"])
        qs = [(q["name"], q["value"]) for q in r["queryString"]]
        assert qs == parse_qsl(urlparse(r["url"]).query, keep_blank_values=True)
        assert isinstance(s["status"], int) and {"mimeType", "text"} <= set(s["content"])
        if r["method"] == "POST":
            assert r["postData"]["mimeType"] == "application/json"
            json.loads(r["postData"]["text"])
        if s["content"]["text"]:
            json.loads(s["content"]["text"])
    times = [e["startedDateTime"] for e in log["entries"]]
    assert times == sorted(times) and len(set(times)) > 1


def test_auth_is_in_recorded_headers_per_type():
    doc, _, _ = record()
    for e in doc["log"]["entries"]:
        assert {h["name"].lower(): h["value"] for h in e["request"]["headers"]}["x-api-key"] == SECRET
    doc, _, _ = record(table("bookshelf_bearer.yaml"))
    for e in doc["log"]["entries"]:
        assert {h["name"].lower(): h["value"] for h in e["request"]["headers"]}["authorization"] == f"Bearer {SECRET}"
    basic = "user_1a2b3c4d:pw_0123456789abcdef"
    doc, _, _ = record(table("bookshelf_basic.yaml"), secret=basic)
    want = "Basic " + base64.b64encode(basic.encode()).decode()
    for e in doc["log"]["entries"]:
        assert {h["name"].lower(): h["value"] for h in e["request"]["headers"]}["authorization"] == want
    t = table()
    t["security"] = {"type": "apiKey", "in": "query", "name": "key"}
    doc, _, _ = record(t)
    for e in doc["log"]["entries"]:
        assert ("key", SECRET) in [(q["name"], q["value"]) for q in e["request"]["queryString"]]


def test_rewrite_host_replaces_loopback_everywhere():
    doc, _, t = record()
    assert "127.0.0.1" in json.dumps(doc)
    snapshot = copy.deepcopy(doc)
    out = har.rewrite_host(doc, t["server_url"])
    assert doc == snapshot  # input untouched
    text = json.dumps(out)
    assert "127.0.0.1" not in text
    for e in out["log"]["entries"]:
        assert e["request"]["url"].startswith("https://api.bookshelf.example/v1/")
        hosts = [h["value"] for h in e["request"]["headers"] if h["name"].lower() == "host"]
        assert hosts in ([], ["api.bookshelf.example"])


def noise_split(out, real):
    keys = {(e["request"]["method"], e["request"]["url"]) for e in real["log"]["entries"]}
    entries = out["log"]["entries"]
    is_real = [(e["request"]["method"], e["request"]["url"]) in keys for e in entries]
    return entries, is_real


@pytest.fixture(scope="module")
def recorded():
    doc, _, t = record()
    return har.rewrite_host(doc, t["server_url"])


@pytest.mark.parametrize("ratio", [0.0, 0.3, 0.5, 0.7, 0.9])
def test_noise_ratio_within_one_entry(recorded, ratio):
    out = har.mix_noise(recorded, ratio, random.Random(1))
    entries, is_real = noise_split(out, recorded)
    assert sum(is_real) == len(recorded["log"]["entries"])
    noise = len(entries) - sum(is_real)
    assert abs(noise - ratio * len(entries)) <= 1


def test_noise_keeps_real_entries_in_order_and_interleaves(recorded):
    out = har.mix_noise(recorded, 0.7, random.Random(2))
    entries, is_real = noise_split(out, recorded)
    real_urls = [e["request"]["url"] for e, r in zip(entries, is_real) if r]
    assert real_urls == [e["request"]["url"] for e in recorded["log"]["entries"]]
    times = [e["startedDateTime"] for e in entries]
    assert times == sorted(times)
    first, last = is_real.index(True), len(is_real) - 1 - is_real[::-1].index(True)
    assert any(not r for r in is_real[first:last])  # noise sits between real calls


def test_noise_looks_like_a_browser_session(recorded):
    out = har.mix_noise(recorded, 0.9, random.Random(3))
    entries, is_real = noise_split(out, recorded)
    noise = [e for e, r in zip(entries, is_real) if not r]
    hosts = {urlparse(e["request"]["url"]).hostname for e in noise}
    assert "api.bookshelf.example" in hosts and len(hosts) >= 4  # same-origin and third-party
    mimes = {e["response"]["content"]["mimeType"].split(";")[0] for e in noise}
    assert {"text/html", "text/css"} & mimes and any("javascript" in m for m in mimes)
    assert "127.0.0.1" not in json.dumps(out)
    assert all(e["request"]["method"] in ("GET", "POST") for e in noise)


def test_noise_is_deterministic_and_input_untouched(recorded):
    snapshot = copy.deepcopy(recorded)
    a = har.mix_noise(recorded, 0.6, random.Random(9))
    b = har.mix_noise(recorded, 0.6, random.Random(9))
    c = har.mix_noise(recorded, 0.6, random.Random(10))
    assert a == b and a != c and recorded == snapshot


def test_noise_ratio_validation(recorded):
    with pytest.raises(ValueError):
        har.mix_noise(recorded, 1.0, random.Random(1))
    with pytest.raises(ValueError):
        har.mix_noise(recorded, -0.1, random.Random(1))


def test_gen_args_union_param_matches_declared_type():
    # goal.md shows `ops.py_type` (first union option); generated args must agree with it
    t = table()
    lb = next(o for o in t["operations"] if o["op_id"] == "list_books")
    lb["params"][0]["schema"] = {"anyOf": [{"type": "integer"}, {"type": "string"}]}
    declared = {a["py_name"]: a["type"] for a in ops.ordered_args(lb)}
    name = lb["params"][0]["py_name"]
    assert declared[name] == "int"
    for i in range(60):
        value = gen_args(lb, random.Random(i), include={name}).get(name)
        assert isinstance(value, int) and not isinstance(value, bool)


def test_noise_json_xhr_avoids_real_api_routes(recorded):
    # a spec route like /ping must not also appear as same-origin noise with another body
    out = har.mix_noise(recorded, 0.9, random.Random(4), "", routes=["/{id}"])  # matches one-segment paths
    paths = {urlparse(e["request"]["url"]).path for e in out["log"]["entries"]}
    assert not paths & {"/__flags", "/config.json", "/ping"}
    out = har.mix_noise(recorded, 0.9, random.Random(4), "", routes=["/ping"])
    paths = {urlparse(e["request"]["url"]).path for e in out["log"]["entries"]}
    assert "/ping" not in paths and paths & {"/__flags", "/config.json", "/sockjs-node/info"}
