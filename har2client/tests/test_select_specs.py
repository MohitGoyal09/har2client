"""Tests for scripts/select_specs.py (pure helpers, no network)."""

import importlib.util
import json
import random
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select_specs.py"
_spec = importlib.util.spec_from_file_location("select_specs", SCRIPT)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)

JSON_RESP = {
    "200": {
        "description": "ok",
        "content": {"application/json": {"schema": {"type": "object"}}},
    }
}


def make_spec(n_ops=6, security=None, schemes=None, extra_paths=None):
    paths = {}
    for i in range(n_ops):
        paths[f"/r{i}"] = {"get": {"operationId": f"get{i}", "responses": JSON_RESP}}
    paths.update(extra_paths or {})
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": paths,
    }
    if schemes is not None:
        spec["components"] = {"securitySchemes": schemes}
    if security is not None:
        spec["security"] = security
    return spec


# ---- helpers -------------------------------------------------------------


def test_provider_of_splits_on_colon():
    assert ss.provider_of("azure.com:compute") == "azure.com"
    assert ss.provider_of("stripe.com") == "stripe.com"


def test_safe_api_id_has_no_path_chars():
    assert ss.safe_api_id("azure.com:compute") == "azure.com__compute"
    assert "/" not in ss.safe_api_id("a/b:c d")


def test_is_openapi_30():
    assert ss.is_openapi_30("3.0.1")
    assert ss.is_openapi_30("3.0.0")
    assert not ss.is_openapi_30("3.1.0")
    assert not ss.is_openapi_30("2.0")
    assert not ss.is_openapi_30(None)


# ---- op counting ---------------------------------------------------------


def test_count_ops_counts_get_and_json_post():
    spec = make_spec(
        n_ops=2,
        extra_paths={
            "/x": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {}}}},
                    "responses": JSON_RESP,
                }
            }
        },
    )
    n_ops, n_json = ss.count_ops(spec)
    assert (n_ops, n_json) == (3, 3)


def test_count_ops_skips_non_json_request_body():
    spec = make_spec(
        n_ops=1,
        extra_paths={
            "/up": {
                "post": {
                    "requestBody": {
                        "content": {"multipart/form-data": {"schema": {}}}
                    },
                    "responses": JSON_RESP,
                }
            }
        },
    )
    assert ss.count_ops(spec) == (1, 1)


def test_count_ops_ignores_non_crud_methods_and_path_level_keys():
    spec = make_spec(
        n_ops=1,
        extra_paths={
            "/h": {
                "parameters": [],
                "head": {"responses": JSON_RESP},
                "options": {"responses": JSON_RESP},
            }
        },
    )
    assert ss.count_ops(spec) == (1, 1)


def test_count_ops_json_response_needs_schema_and_2xx():
    spec = make_spec(
        n_ops=0,
        extra_paths={
            "/a": {"get": {"responses": {"200": {"content": {"application/json": {}}}}}},
            "/b": {"get": {"responses": {"404": JSON_RESP["200"]}}},
            "/c": {"get": {"responses": {"201": JSON_RESP["200"]}}},
            "/d": {"get": {"responses": {"204": {"description": "none"}}}},
        },
    )
    assert ss.count_ops(spec) == (4, 1)


def test_count_ops_resolves_local_refs():
    spec = make_spec(n_ops=0)
    spec["components"] = {
        "responses": {"Ok": JSON_RESP["200"]},
        "requestBodies": {"B": {"content": {"application/json": {"schema": {}}}}},
    }
    spec["paths"]["/a"] = {
        "post": {
            "requestBody": {"$ref": "#/components/requestBodies/B"},
            "responses": {"200": {"$ref": "#/components/responses/Ok"}},
        }
    }
    assert ss.count_ops(spec) == (1, 1)


def test_count_ops_charset_media_type():
    spec = make_spec(n_ops=0)
    spec["paths"]["/a"] = {
        "get": {
            "responses": {
                "200": {
                    "content": {"application/json; charset=utf-8": {"schema": {"type": "object"}}}
                }
            }
        }
    }
    assert ss.count_ops(spec) == (1, 1)


# ---- security ------------------------------------------------------------


@pytest.mark.parametrize(
    "schemes,expected",
    [
        ({"k": {"type": "apiKey", "in": "header", "name": "X"}}, "apiKey"),
        ({"k": {"type": "apiKey", "in": "query", "name": "key"}}, "apiKey"),
        ({"k": {"type": "http", "scheme": "bearer"}}, "bearer"),
        ({"k": {"type": "http", "scheme": "Bearer"}}, "bearer"),
        ({"k": {"type": "oauth2", "flows": {}}}, "bearer"),
        ({"k": {"type": "openIdConnect", "openIdConnectUrl": "x"}}, "bearer"),
        ({"k": {"type": "http", "scheme": "basic"}}, "basic"),
    ],
)
def test_classify_security_supported(schemes, expected):
    spec = make_spec(security=[{"k": []}], schemes=schemes)
    assert ss.classify_security(spec) == expected


@pytest.mark.parametrize(
    "scheme",
    [
        {"type": "apiKey", "in": "cookie", "name": "sid"},
        {"type": "mutualTLS"},
        {"type": "http", "scheme": "digest"},
    ],
)
def test_classify_security_unsupported_returns_none_value(scheme):
    spec = make_spec(security=[{"k": []}], schemes={"k": scheme})
    assert ss.classify_security(spec) is None


def test_classify_security_no_requirements_is_none_string():
    assert ss.classify_security(make_spec()) == "none"
    spec = make_spec(schemes={"k": {"type": "http", "scheme": "bearer"}})
    assert ss.classify_security(spec) == "none"  # defined but never required


def test_classify_security_picks_supported_among_alternatives():
    spec = make_spec(
        security=[{"c": []}, {"k": []}],
        schemes={
            "c": {"type": "apiKey", "in": "cookie", "name": "s"},
            "k": {"type": "apiKey", "in": "header", "name": "X"},
        },
    )
    assert ss.classify_security(spec) == "apiKey"


def test_classify_security_reads_op_level_security():
    spec = make_spec(
        n_ops=0,
        schemes={"b": {"type": "http", "scheme": "basic"}},
    )
    spec["paths"]["/a"] = {
        "get": {"security": [{"b": []}], "responses": JSON_RESP}
    }
    assert ss.classify_security(spec) == "basic"


# ---- analyze -------------------------------------------------------------


def test_analyze_spec_accepts_good_spec():
    rec, reason = ss.analyze_spec(make_spec(n_ops=6))
    assert reason is None
    assert rec["n_ops"] == 6 and rec["n_json_responses"] == 6
    assert rec["security_type"] == "none"


def test_analyze_spec_rejects_too_few_and_too_many():
    assert ss.analyze_spec(make_spec(n_ops=4))[1] == "ops_range"
    assert ss.analyze_spec(make_spec(n_ops=41))[1] == "ops_range"
    assert ss.analyze_spec(make_spec(n_ops=40))[1] is None
    assert ss.analyze_spec(make_spec(n_ops=5))[1] is None


def test_analyze_spec_rejects_few_json_responses():
    spec = make_spec(n_ops=2)
    for i in range(4):
        spec["paths"][f"/n{i}"] = {"get": {"responses": {"204": {"description": ""}}}}
    assert ss.analyze_spec(spec)[1] == "json_responses"


def test_analyze_spec_rejects_unsupported_security():
    spec = make_spec(
        security=[{"k": []}],
        schemes={"k": {"type": "apiKey", "in": "cookie", "name": "s"}},
    )
    assert ss.analyze_spec(spec)[1] == "security"


# ---- cap -----------------------------------------------------------------


def rec(api_id):
    return {"api_id": api_id, "provider": ss.provider_of(api_id)}


def test_cap_per_provider_limits_and_is_deterministic():
    recs = [rec(f"azure.com:s{i}") for i in range(10)] + [rec("stripe.com")]
    a = ss.cap_per_provider(recs, 2, random.Random(1))
    b = ss.cap_per_provider(list(reversed(recs)), 2, random.Random(1))
    assert [r["api_id"] for r in a] == [r["api_id"] for r in b]
    assert sum(r["provider"] == "azure.com" for r in a) == 2
    assert any(r["api_id"] == "stripe.com" for r in a)
    assert [r["api_id"] for r in a] == sorted(r["api_id"] for r in a)


def test_cap_per_provider_seed_changes_choice():
    recs = [rec(f"p.com:s{i}") for i in range(30)]
    picks = {
        tuple(r["api_id"] for r in ss.cap_per_provider(recs, 2, random.Random(s)))
        for s in range(5)
    }
    assert len(picks) > 1


# ---- download ------------------------------------------------------------


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_writes_and_caches(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"openapi": "3.0.0"})

    dest = tmp_path / "a.json"
    with make_client(handler) as c:
        assert ss.download("http://x/a.json", dest, c) is True
        assert ss.download("http://x/a.json", dest, c) is True
    assert len(calls) == 1
    assert json.loads(dest.read_text())["openapi"] == "3.0.0"


def test_download_retries_once_then_fails(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500)

    with make_client(handler) as c:
        assert ss.download("http://x/a.json", tmp_path / "a.json", c, backoff=0) is False
    assert len(calls) == 2
    assert not (tmp_path / "a.json").exists()


def test_download_retry_succeeds(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"a": 1})

    with make_client(handler) as c:
        assert ss.download("http://x/a.json", tmp_path / "a.json", c, backoff=0) is True


def test_download_rejects_non_json_body(tmp_path):
    def handler(req):
        return httpx.Response(200, text="<html>nope</html>")

    with make_client(handler) as c:
        assert ss.download("http://x/a.json", tmp_path / "a.json", c, backoff=0) is False
    assert not (tmp_path / "a.json").exists()


# ---- candidates ----------------------------------------------------------


def test_candidates_uses_preferred_version_and_30_only():
    listing = {
        "a.com": {
            "preferred": "2",
            "versions": {
                "1": {"openapiVer": "3.0.0", "swaggerUrl": "u1", "info": {}},
                "2": {"openapiVer": "3.0.1", "swaggerUrl": "u2", "info": {}},
            },
        },
        "b.com": {
            "preferred": "1",
            "versions": {"1": {"openapiVer": "3.1.0", "swaggerUrl": "u3", "info": {}}},
        },
        "c.com": {
            "preferred": "1",
            "versions": {"1": {"openapiVer": "2.0", "swaggerUrl": "u4", "info": {}}},
        },
    }
    out = ss.candidates(listing)
    assert [c["api_id"] for c in out] == ["a.com"]
    assert out[0]["swagger_url"] == "u2"


# ---- --no-write ----------------------------------------------------------


def _run_main(monkeypatch, tmp_path, argv):
    listing = {
        f"p{i}.com": {"preferred": "1", "versions": {"1": {"openapiVer": "3.0.0", "swaggerUrl": f"https://x.test/{i}.json"}}}
        for i in range(3)
    }
    out = tmp_path / "data" / "selected_specs.jsonl"
    out.parent.mkdir(parents=True)
    out.write_text("SENTINEL\n", encoding="utf-8")
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    monkeypatch.setattr(ss, "OUT_PATH", out)
    monkeypatch.setattr(ss, "fetch_listing", lambda: listing)
    monkeypatch.setattr(ss, "ProcessPoolExecutor", lambda: __import__("concurrent.futures").futures.ThreadPoolExecutor(1))
    monkeypatch.setattr(ss, "_validate_path", lambda path: True)

    def fake_analyze(args):
        cand, _client = args
        return dict(cand, spec_path=f"data/specs/{cand['api_id']}.json", title="T", n_ops=6, n_json_responses=4,
                    security_type="none", categories=[], openapi_version="3.0.0")

    monkeypatch.setattr(ss, "_download_and_analyze", fake_analyze)
    assert ss.main(argv) == 0
    return out


def test_no_write_leaves_selected_file_untouched(monkeypatch, tmp_path):
    out = _run_main(monkeypatch, tmp_path, ["--no-write"])
    assert out.read_text(encoding="utf-8") == "SENTINEL\n"


def test_default_run_still_writes_selected_file(monkeypatch, tmp_path):
    out = _run_main(monkeypatch, tmp_path, [])
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["api_id"] for r in rows] == ["p0.com", "p1.com", "p2.com"]
