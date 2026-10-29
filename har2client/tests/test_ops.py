import copy
import keyword
from pathlib import Path

import pytest

from har2client import ops

FIX = Path(__file__).parent / "fixtures"


def table(name="bookshelf_apikey.yaml", api_id="acme:bookshelf"):
    return ops.normalize(ops.load_spec(FIX / name), api_id)


def by_id(t):
    return {o["op_id"]: o for o in t["operations"]}


def test_load_spec_resolves_refs_and_caps_recursion():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    resp = spec["paths"]["/categories"]["get"]["responses"]["200"]
    items = resp["content"]["application/json"]["schema"]["items"]
    assert "$ref" not in str(spec["paths"])
    assert items["properties"]["children"]["items"]["type"] == "object"
    # recursion is cut after a bounded depth, so the structure is finite and JSON-safe
    import json

    assert len(json.dumps(spec["paths"])) < 200_000


def test_load_spec_reads_json_and_stringifies_yaml_ints_and_dates(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "openapi: 3.0.0\ninfo: {title: t, version: 2020-01-01}\npaths:\n  /a:\n    get:\n"
        "      responses:\n        200: {description: ok}\n"
    )
    spec = ops.load_spec(p)
    assert "200" in spec["paths"]["/a"]["get"]["responses"]
    assert spec["info"]["version"] == "2020-01-01"
    j = tmp_path / "s.json"
    j.write_text('{\n\t"openapi": "3.0.0", "paths": {}}')
    assert ops.load_spec(j)["paths"] == {}


def test_op_table_top_level_fields():
    t = table()
    assert t["api_id"] == "acme:bookshelf"
    assert t["title"] == "Bookshelf API"
    assert t["server_url"] == "https://api.bookshelf.example/v1"
    assert t["base_path"] == "/v1"
    assert t["security"] == {"type": "apiKey", "in": "header", "name": "X-API-Key"}


def test_op_ids_unique_valid_identifiers():
    t = table()
    ids = [o["op_id"] for o in t["operations"]]
    assert len(ids) == len(set(ids))
    for i in ids:
        assert i.isidentifier() and not keyword.iskeyword(i)
    assert {"list_books", "create_book", "get_book", "delete_book", "health", "get_stats"} <= set(ids)
    # no operationId -> snake_case summary
    assert "books_by_one_author" in ids
    assert "category_tree" in ids


def test_op_id_clash_gets_suffix_and_keywords_are_safe():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "servers": [{"url": "https://x.example/api"}],
        "paths": {
            "/a": {"get": {"operationId": "class", "responses": {"200": {"description": "ok"}}}},
            "/b": {"get": {"operationId": "Class", "responses": {"200": {"description": "ok"}}}},
        },
    }
    t = ops.normalize(spec, "x:y")
    ids = [o["op_id"] for o in t["operations"]]
    assert ids[0] != ids[1]
    assert all(i.isidentifier() and not keyword.iskeyword(i) for i in ids)
    assert ids[1].endswith("_2")


def test_params_py_names_and_locations():
    o = by_id(table())
    lb = o["list_books"]
    assert [(p["name"], p["py_name"], p["in"], p["required"]) for p in lb["params"]] == [
        ("limit", "limit", "query", False),
        ("genre", "genre", "query", False),
        ("tags", "tags", "query", False),
    ]  # header + cookie params dropped
    assert lb["body"] is None
    gb = o["get_book"]
    assert gb["params"][0]["in"] == "path" and gb["params"][0]["required"] is True
    ab = o["books_by_one_author"]
    assert [p["name"] for p in ab["params"]] == ["authorId", "page", "include_draft"]
    assert [p["py_name"] for p in ab["params"]] == ["author_id", "page", "include_draft"]


def test_py_name_reserved_and_unique():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "servers": [{"url": "https://x.example"}],
        "paths": {
            "/a/{id}": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "id", "in": "query", "schema": {"type": "string"}},
                        {"name": "body", "in": "query", "schema": {"type": "string"}},
                        {"name": "self", "in": "query", "schema": {"type": "string"}},
                        {"name": "from", "in": "query", "schema": {"type": "string"}},
                        {"name": "filter[name]", "in": "query", "schema": {"type": "string"}},
                        {"name": "2fa", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    op = ops.normalize(spec, "x:y")["operations"][0]
    names = [p["py_name"] for p in op["params"]]
    assert len(names) == len(set(names))
    for n in names:
        assert n.isidentifier() and not keyword.iskeyword(n) and n not in ("body", "self")
    assert names[0] == "id" and names[1] == "id_2"


def test_body_and_response_mapping():
    o = by_id(table())
    cb = o["create_book"]
    assert cb["method"] == "POST" and cb["path"] == "/books"
    assert cb["body"]["required"] is True
    props = cb["body"]["schema"]
    assert "allOf" in props or "properties" in props
    assert cb["response"]["status"] == 201 and cb["response"]["schema"] is not None
    assert o["delete_book"]["response"] == {"status": 204, "schema": None}
    assert o["get_book"]["response"]["status"] == 200


def test_dropped_ops_are_listed_with_reason():
    t = table()
    ids = {o["op_id"] for o in t["operations"]}
    assert "upload_cover" not in ids  # multipart only
    assert "get_filter" not in ids  # object path param
    reasons = {(d["method"], d["path"]): d["reason"] for d in t["dropped"]}
    assert "json" in reasons[("POST", "/books/{bookId}/cover")].lower()
    assert "path" in reasons[("GET", "/filters/{filter}")].lower()


def test_security_mapping_variants():
    assert table("bookshelf_bearer.yaml")["security"] == {"type": "bearer", "in": None, "name": None}
    assert table("bookshelf_basic.yaml")["security"] == {"type": "basic", "in": None, "name": None}


def test_security_oauth2_and_openid_map_to_bearer_and_query_apikey():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    s1 = copy.deepcopy(spec)
    s1["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "oauth2", "flows": {}}}
    assert ops.normalize(s1, "a:b")["security"]["type"] == "bearer"
    s2 = copy.deepcopy(spec)
    s2["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "openIdConnect", "openIdConnectUrl": "x"}}
    assert ops.normalize(s2, "a:b")["security"]["type"] == "bearer"
    s3 = copy.deepcopy(spec)
    s3["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "apiKey", "in": "query", "name": "key"}}
    assert ops.normalize(s3, "a:b")["security"] == {"type": "apiKey", "in": "query", "name": "key"}


def test_auth_required_flags():
    o = by_id(table())
    assert o["health"]["auth_required"] is False
    assert o["get_book"]["auth_required"] is True


def test_unsupported_security_skips_api():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    spec["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "apiKey", "in": "cookie", "name": "sid"}}
    with pytest.raises(ops.UnusableApi) as e:
        ops.normalize(spec, "a:b")
    assert "security" in str(e.value).lower()


def test_no_security_is_none():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    del spec["security"]
    t = ops.normalize(spec, "a:b")
    assert t["security"] == {"type": "none", "in": None, "name": None}
    assert all(o["auth_required"] is False for o in t["operations"])


def test_query_apikey_param_is_not_a_regular_param():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    spec["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "apiKey", "in": "query", "name": "limit"}}
    t = ops.normalize(spec, "a:b")
    lb = by_id(t)["list_books"]
    assert "limit" not in [p["name"] for p in lb["params"]]


def test_relative_server_url_falls_back_to_provider_host():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    spec["servers"] = [{"url": "/api/v3"}]
    t = ops.normalize(spec, "bookshelf.example:core")
    assert t["server_url"] == "https://bookshelf.example/api/v3"
    assert t["base_path"] == "/api/v3"


def test_server_variables_use_defaults():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    spec["servers"] = [
        {"url": "https://{region}.x.example/{ver}", "variables": {"region": {"default": "eu"}, "ver": {"default": "v9"}}}
    ]
    t = ops.normalize(spec, "a:b")
    assert t["server_url"] == "https://eu.x.example/v9"


def test_swagger2_is_rejected():
    with pytest.raises(ops.UnusableApi):
        ops.normalize({"swagger": "2.0", "paths": {}}, "a:b")


def test_usability_rule():
    t = table()
    assert ops.usability_reason(t) is None
    few = copy.deepcopy(t)
    few["operations"] = few["operations"][:4]
    assert "5" in ops.usability_reason(few)
    many = copy.deepcopy(t)
    many["operations"] = [dict(many["operations"][0], op_id=f"o{i}") for i in range(41)]
    assert "40" in ops.usability_reason(many)
    noschema = copy.deepcopy(t)
    for o in noschema["operations"]:
        o["response"]["schema"] = None
    assert "schema" in ops.usability_reason(noschema)


def test_signature_orders_required_first():
    o = by_id(table())
    sig = ops.signature(o["books_by_one_author"])
    assert sig.startswith("books_by_one_author(self, author_id: int, page: int")
    assert "include_draft: bool | None = None" in sig
    assert ops.signature(o["create_book"]).endswith("body: dict) -> Any")


def test_and_requirement_needs_one_credential_only():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    spec["components"]["securitySchemes"] = {
        "A": {"type": "oauth2", "flows": {}},
        "B": {"type": "oauth2", "flows": {}},
        "K": {"type": "apiKey", "in": "header", "name": "X-K"},
    }
    spec["security"] = [{"A": [], "B": []}]
    assert ops.normalize(spec, "a:b")["security"]["type"] == "bearer"
    spec["security"] = [{"A": [], "K": []}]  # two different credentials: cannot be supplied
    with pytest.raises(ops.UnusableApi):
        ops.normalize(spec, "a:b")


def test_op_with_other_scheme_than_the_api_one_is_dropped():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    spec["components"]["securitySchemes"]["Other"] = {"type": "apiKey", "in": "header", "name": "X-Other"}
    spec["paths"]["/health"]["get"]["security"] = [{"Other": []}]
    t = ops.normalize(spec, "a:b")
    assert "health" not in {o["op_id"] for o in t["operations"]}
    assert any(d["path"] == "/health" and "security" in d["reason"] for d in t["dropped"])


def test_paths_with_fragment_or_query_are_dropped():
    """Regression: AWS specs list `/tags/{arn}#tagKeys` and `/#X-Amz-Target=...`; they are not real paths."""
    ok = {"responses": {"200": {"description": "ok"}}}
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "servers": [{"url": "https://x.example"}],
        "paths": {"/a": {"get": ok}, "/tags/{arn}#keys": {"get": ok}, "/?Action=Go": {"post": ok}},
    }
    t = ops.normalize(spec, "x:y")
    assert [o["path"] for o in t["operations"]] == ["/a"]
    assert {d["path"] for d in t["dropped"]} == {"/tags/{arn}#keys", "/?Action=Go"}
    assert all("path" in d["reason"] for d in t["dropped"])


def _dense_spec(n=14):
    """Regression (presalytics.io:ooxml): every schema refers to every other one, so a naive
    resolver expands n! paths and never finishes."""
    schemas = {
        f"S{i}": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}, **{f"p{j}": {"$ref": f"#/components/schemas/S{j}"} for j in range(n)}},
        }
        for i in range(n)
    }
    ok = {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S0"}}}}}
    return {
        "openapi": "3.0.0",
        "info": {"title": "dense"},
        "servers": [{"url": "https://dense.example"}],
        "paths": {f"/r{i}": {"get": {"responses": ok}} for i in range(6)},
        "components": {"schemas": schemas},
    }


def test_dense_cyclic_refs_resolve_fast_and_bounded(tmp_path):
    import json
    import time

    p = tmp_path / "dense.json"
    p.write_text(json.dumps(_dense_spec()))
    t0 = time.time()
    spec = ops.load_spec(p)
    table = ops.normalize(spec, "dense:api")
    assert time.time() - t0 < 10
    assert len(json.dumps(table)) < 3_000_000
    assert len(table["operations"]) == 6
    props = table["operations"][0]["response"]["schema"]["properties"]
    assert "id" in props and "p3" in props  # still a real schema near the top


def test_small_specs_keep_full_expansion():
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    book = spec["paths"]["/books/{bookId}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert book["allOf"][1]["properties"]["author_email"]["format"] == "email"


def test_duplicate_route_shapes_are_dropped():
    """Regression (googleapis.com:iam): GET /v2/{name} and GET /v2/{parent} cannot be told apart."""
    ok = {"responses": {"200": {"description": "ok"}}}
    p = lambda n: {"parameters": [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}}], **ok}  # noqa: E731
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "servers": [{"url": "https://x.example"}],
        "paths": {"/v2/{name}": {"get": p("name")}, "/v2/{parent}": {"get": p("parent"), "delete": p("parent")}},
    }
    t = ops.normalize(spec, "x:y")
    assert [(o["method"], o["path"]) for o in t["operations"]] == [("GET", "/v2/{name}"), ("DELETE", "/v2/{parent}")]
    assert [d["path"] for d in t["dropped"]] == ["/v2/{parent}"]
    assert "duplicate" in t["dropped"][0]["reason"]


def test_204_response_never_has_a_schema():
    """Regression (amazonaws.com:iotevents): a 204 that declares a schema made the mock send a body."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "servers": [{"url": "https://x.example"}],
        "paths": {
            "/a": {
                "delete": {
                    "responses": {
                        "204": {"description": "gone", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            }
        },
    }
    assert ops.normalize(spec, "x:y")["operations"][0]["response"] == {"status": 204, "schema": None}


# ---------------------------------------------------------------- op names without operationId


def _ids_for(summaries):
    """op ids for one `GET /p{i}/{x}` per entry; a summary of None means no summary key."""
    paths = {}
    for i, summary in enumerate(summaries):
        op = {"responses": {"200": {"description": "ok"}}}
        if summary is not None:
            op["summary"] = summary
        paths[f"/p{i}/{{x}}"] = {
            "parameters": [{"name": "x", "in": "path", "required": True, "schema": {"type": "string"}}],
            "get": op,
        }
    spec = {"openapi": "3.0.0", "info": {"title": "x"}, "servers": [{"url": "https://x.example"}], "paths": paths}
    return [o["op_id"] for o in ops.normalize(spec, "x:x")["operations"]]


def test_missing_operation_id_uses_snake_case_summary():
    assert _ids_for(["Category tree", "List All Orders.", "Get user's profile", "Fetch one"]) == [
        "category_tree", "list_all_orders", "get_users_profile", "fetch_one",
    ]


def test_summary_name_does_not_leak_the_path():
    (name,) = _ids_for(["Books by one author"])
    assert name == "books_by_one_author" and "p0" not in name


def test_summary_with_zero_or_more_than_six_words_falls_back_to_path_rule():
    assert _ids_for(["", "  ", "One two three four five six seven", None]) == [
        "get_p0_x", "get_p1_x", "get_p2_x", "get_p3_x",
    ]
    assert _ids_for(["One two three four five six"]) == ["one_two_three_four_five_six"]


def test_unusable_summary_falls_back_to_path_rule():
    # keyword, leading digit, no identifier characters
    got = _ids_for(["class", "2 fast", "???", "Lambda"])
    assert got == ["get_p0_x", "get_p1_x", "get_p2_x", "get_p3_x"]
    assert all(i.isidentifier() and not keyword.iskeyword(i) for i in got)


def test_duplicate_summary_falls_back_for_the_later_op_and_ids_stay_unique():
    got = _ids_for(["List things", "List things", "List things"])
    assert got == ["list_things", "get_p1_x", "get_p2_x"]


def test_operation_id_still_wins_over_summary_and_clash_suffix_is_kept():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "servers": [{"url": "https://x.example"}],
        "paths": {
            "/a": {"get": {"summary": "Do thing", "responses": {"200": {"description": "ok"}}}},
            "/b": {"get": {"operationId": "doThing", "summary": "Other", "responses": {"200": {"description": "ok"}}}},
        },
    }
    ids = [o["op_id"] for o in ops.normalize(spec, "x:x")["operations"]]
    assert ids == ["do_thing", "do_thing_2"]
