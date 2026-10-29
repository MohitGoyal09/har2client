"""Mutation operators and trivial agents (grader-validity audit, U4)."""

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from har2client import build_task, mutants, ops, refclient

FIX = Path(__file__).parent / "fixtures"

REQUIRED_OPERATORS = {
    "base_path_doubled", "base_path_dropped", "wrong_http_method", "path_segment_changed",
    "path_param_in_query", "query_param_renamed", "query_param_dropped",
    "bool_encoding_python_repr", "bool_encoding_numeric", "auth_header_name_wrong", "auth_in_query",
    "auth_in_header", "bearer_prefix_missing", "credential_hardcoded", "host_hardcoded",
    "response_not_parsed", "returns_field_not_body", "extra_unrequested_call", "body_as_form",
    "error_swallowed",
}


def query_key(spec):
    spec["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "apiKey", "in": "query", "name": "api_key"}}


def no_auth(spec):
    spec.pop("security")
    for item in spec["paths"].values():
        for op in item.values():
            if isinstance(op, dict):
                op.pop("security", None)


VARIANTS = {
    "apikey_header": ("bookshelf_apikey.yaml", None),
    "apikey_query": ("bookshelf_apikey.yaml", query_key),
    "bearer": ("bookshelf_bearer.yaml", None),
    "basic": ("bookshelf_basic.yaml", None),
    "none": ("bookshelf_apikey.yaml", no_auth),
}


@pytest.fixture(scope="module")
def task_dirs(tmp_path_factory):
    out = {}
    for key, (name, mutate) in VARIANTS.items():
        spec = ops.load_spec(FIX / name)
        if mutate:
            mutate(spec)
        d = build_task.build_task(spec, "acme:bookshelf", tmp_path_factory.mktemp(key), noise=0.7, seed=3)
        refclient.write_ref_client(d)
        out[key] = d
    return out


@pytest.fixture(scope="module")
def ctxs(task_dirs):
    return {k: mutants.load_context(d) for k, d in task_dirs.items()}


def methods_of(source: str) -> dict[str, list[str]]:
    """Public method name -> argument names of `Client` (the interface a caller sees)."""
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Client")
    return {
        f.name: [a.arg for a in f.args.args]
        for f in cls.body
        if isinstance(f, ast.FunctionDef) and not f.name.startswith("_") and f.name != "close"
    }


def test_operator_catalogue_covers_the_required_list():
    assert REQUIRED_OPERATORS <= set(mutants.OPERATORS)


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_every_mutant_is_valid_python_differs_and_keeps_the_interface(ctxs, variant):
    ctx = ctxs[variant]
    found, skipped = mutants.generate_mutants(ctx)
    assert found
    ref_interface = methods_of(ctx.source)
    for m in found:
        compile(m.source, f"<{m.operator}>", "exec")
        assert m.source != ctx.source, m.operator
        assert methods_of(m.source) == ref_interface, m.operator
        assert m.operator in mutants.OPERATORS
    # every operator is either produced or skipped with a written reason, never silently dropped
    accounted = {m.operator for m in found} | {s.operator for s in skipped}
    assert accounted == set(mutants.OPERATORS)
    assert all(s.reason.strip() for s in skipped)


def test_header_apikey_task_applies_the_core_operators(ctxs):
    found, skipped = mutants.generate_mutants(ctxs["apikey_header"])
    produced = {m.operator for m in found}
    must = REQUIRED_OPERATORS - {"auth_in_header", "bearer_prefix_missing"}  # those two need another auth type
    assert must <= produced, must - produced
    why = {s.operator: s.reason for s in skipped}
    assert "query" in why["auth_in_header"]
    assert "bearer" in why["bearer_prefix_missing"]


def test_per_operation_mutants_name_the_operation_they_touch(ctxs):
    found, _ = mutants.generate_mutants(ctxs["apikey_header"])
    by_op = {m.operator: m for m in found}
    for name in ("wrong_http_method", "path_segment_changed", "path_param_in_query",
                 "query_param_renamed", "query_param_dropped"):
        assert by_op[name].op_id, name
        assert by_op[name].op_id in {t["method"] for t in ctxs["apikey_header"].tests}


def test_wrong_method_changes_only_the_method_of_one_op(ctxs):
    ctx = ctxs["apikey_header"]
    m = next(x for x in mutants.generate_mutants(ctx)[0] if x.operator == "wrong_http_method")
    old, new = ctx.source.splitlines(), m.source.splitlines()
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    assert len(old) == len(new) and len(changed) == 1
    assert "self._request(" in changed[0][0]


def test_bearer_and_basic_prefix_operators(ctxs):
    bearer, _ = mutants.generate_mutants(ctxs["bearer"])
    assert "bearer_prefix_missing" in {m.operator for m in bearer}
    basic, skipped = mutants.generate_mutants(ctxs["basic"])
    assert "basic_prefix_missing" in {m.operator for m in basic}
    assert "bearer_prefix_missing" in {s.operator for s in skipped}


def test_query_key_task_gets_the_reverse_auth_mutant(ctxs):
    found, skipped = mutants.generate_mutants(ctxs["apikey_query"])
    produced = {m.operator for m in found}
    assert "auth_in_header" in produced
    assert "auth_in_query" in {s.operator for s in skipped}


def test_no_auth_task_skips_every_credential_operator_with_a_reason(ctxs):
    found, skipped = mutants.generate_mutants(ctxs["none"])
    why = {s.operator: s.reason for s in skipped}
    for name in ("auth_header_name_wrong", "auth_in_query", "credential_hardcoded", "bearer_prefix_missing"):
        assert name in why and "auth" in why[name].lower(), name


def test_base_path_operators_skip_when_there_is_no_base_path(ctxs):
    ctx = ctxs["apikey_header"]
    flat = dataclasses.replace(ctx, op_table={**ctx.op_table, "base_path": ""})
    _, skipped = mutants.generate_mutants(flat)
    why = {s.operator: s.reason for s in skipped}
    assert "base path" in why["base_path_doubled"] and "base path" in why["base_path_dropped"]


def test_param_operators_skip_when_no_test_supplies_the_param(ctxs):
    ctx = ctxs["apikey_header"]
    bare = dataclasses.replace(ctx, tests=[t for t in ctx.tests if not t["kwargs"]])
    _, skipped = mutants.generate_mutants(bare)
    why = {s.operator: s.reason for s in skipped}
    for name in ("query_param_renamed", "query_param_dropped", "path_param_in_query",
                 "bool_encoding_python_repr", "body_as_form"):
        assert name in why, name


def test_generation_is_deterministic(ctxs):
    a, _ = mutants.generate_mutants(ctxs["apikey_header"])
    b, _ = mutants.generate_mutants(ctxs["apikey_header"])
    assert [(m.operator, m.op_id, m.source) for m in a] == [(m.operator, m.op_id, m.source) for m in b]


def test_hardcoded_host_mutant_never_leaves_the_machine(ctxs):
    ctx = ctxs["apikey_header"]
    m = next(x for x in mutants.generate_mutants(ctx)[0] if x.operator == "host_hardcoded")
    assert "_NoEgress" in m.source and ctx.op_table["server_url"] in m.source


def test_credential_hardcoded_uses_the_recording_secret(ctxs):
    ctx = ctxs["apikey_header"]
    m = next(x for x in mutants.generate_mutants(ctx)[0] if x.operator == "credential_hardcoded")
    assert ctx.recording_secret in m.source


@pytest.mark.parametrize("make", [mutants.empty_client, mutants.har_replay_client])
def test_trivial_clients_are_valid_and_keep_the_interface(ctxs, make):
    ctx = ctxs["apikey_header"]
    src = make(ctx)
    compile(src, "<trivial>", "exec")
    assert methods_of(src) == methods_of(ctx.source)


def test_har_replay_client_hardcodes_host_and_key_and_returns_recorded_bodies(ctxs):
    ctx = ctxs["apikey_header"]
    src = mutants.har_replay_client(ctx)
    assert ctx.recording_secret in src and "api.bookshelf.example" in src
    assert ctx.recorded_bodies  # the HAR has at least one JSON response
    for op_id, body in ctx.recorded_bodies.items():
        assert repr(op_id) in src
        assert json.dumps(body) in src or repr(body) in src


def test_recorded_bodies_prefers_the_most_specific_route():
    table = {
        "base_path": "/v1",
        "operations": [
            {"op_id": "get_item", "method": "GET", "path": "/items/{id}"},
            {"op_id": "get_latest", "method": "GET", "path": "/items/latest"},
        ],
    }
    har = {"log": {"entries": [{
        "request": {"method": "GET", "url": "https://h.example/v1/items/latest"},
        "response": {"status": 200, "content": {"text": '{"x": 1}'}},
    }]}}
    assert mutants.recorded_bodies(har, table) == {"get_latest": {"x": 1}}


def test_classify_survivor_rules():
    base = {"invented_endpoint_calls": 0, "hardcoded_secret": False, "hardcoded_host": False}
    eq = mutants.Mutant("credential_also_in_query", "x", None, "", equivalent_rule="design_leniency_1")
    assert mutants.classify_survivor(eq, base) == ("EQUIVALENT", "design_leniency_1")
    plain = mutants.Mutant("wrong_http_method", "x", "op", "")
    assert mutants.classify_survivor(plain, base)[0] == "GRADER_GAP"
    flagged = mutants.Mutant("extra_unrequested_call", "x", None, "")
    cls, why = mutants.classify_survivor(flagged, {**base, "invented_endpoint_calls": 4})
    assert cls == "FLAG_ONLY" and "invented_endpoint_calls" in why


def test_invented_call_never_lands_on_a_real_route(ctxs):
    ctx = ctxs["apikey_header"]
    catch_all = {"op_id": "catch", "method": "GET", "path": "/{x}", "params": [], "body": None,
                 "response": None, "auth_required": True, "summary": ""}
    table = {**ctx.op_table, "operations": [*ctx.op_table["operations"], catch_all]}
    m = next(x for x in mutants.generate_mutants(dataclasses.replace(ctx, op_table=table))[0]
             if x.operator == "extra_unrequested_call")
    path = m.detail.split()[1]
    assert not mutants.route_matches(table, "GET", path)
    assert mutants.route_matches(table, "GET", "/anything")
