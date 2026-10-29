"""Silent wire drift (`drift`: rename, cents, epoch, nest, enum): the engine keeps canonical items and speaks another wire form."""

import random

import pytest

from har2client.tierb import domains
from har2client.tierb.config import DRIFT_KINDS, is_valid
from har2client.tierb.refclient import generate_client_source
from har2client.tierb.spec import Wire, drift_fields, extra_fields, make_spec
from har2client.tierb.testgen import make_tests
from test_tierb_support import config, grade_flaws, light_task, read_json, spec_for, start

ALL_KINDS = list(DRIFT_KINDS)
SEEDS = (3, 11, 29)


def drifted_spec(kinds, domain="shipments", seed=5, **over):
    return spec_for(domain=domain, task_seed=seed, drift=list(kinds), **over)


# ------------------------------------------------------------ spec: deterministic, seed-chosen, old specs untouched


def test_old_configs_get_no_new_spec_keys():
    spec = spec_for()
    assert not {"drift", "extra_fields", "rate_limit", "async_create", "refresh", "idempotency", "moved"} & set(spec)


def test_spec_is_deterministic_and_each_switch_draws_its_own_stream():
    a = make_spec("tickets", config(drift=ALL_KINDS, rate_limit="on"), 77)
    assert a == make_spec("tickets", config(drift=ALL_KINDS, rate_limit="on"), 77)
    alone = make_spec("tickets", config(drift=ALL_KINDS), 77)
    assert alone["drift"] == a["drift"] and alone["extra_fields"] == a["extra_fields"]
    assert {k: v for k, v in alone.items() if k not in ("drift", "extra_fields", "config")} == {
        k: v for k, v in a.items() if k not in ("drift", "extra_fields", "config", "rate_limit")
    }


def test_the_seed_picks_the_drifted_fields():
    renamed = {make_spec("tickets", config(drift=["rename"]), s)["drift"]["rename"]["field"] for s in range(40)}
    enums = {make_spec("tickets", config(drift=["enum"]), s)["drift"]["enum"]["field"] for s in range(40)}
    modes = {make_spec("tickets", config(drift=["enum"]), s)["drift"]["enum"]["mode"] for s in range(40)}
    assert len(renamed) > 3 and enums == {"priority", "queue"} and modes == {"upper", "prefix"}


@pytest.mark.parametrize("domain", list(domains.DOMAINS))
def test_drift_parameters_never_clash_with_item_fields(domain):
    for seed in range(25):
        spec = make_spec(domain, config(drift=ALL_KINDS), seed)
        names = set(domains.writable_fields(domain, extra_fields(spec)))
        d = spec["drift"]
        wire_keys = {d["rename"]["to"], d["cents"]["to"], d["nest"]["key"]}
        assert len(wire_keys) == 3 and not wire_keys & (names - {d["rename"]["field"]})  # a wire key is not another field's name
        assert d["enum"]["field"] != d["rename"]["field"]
        assert d["enum"]["mode"] == "prefix" or all(v == v.lower() for v in d["enum"]["values"])


def test_config_validity_of_drift_lists():
    assert is_valid(config(drift=["rename", "epoch"]))
    assert not is_valid(config(drift=["rename", "rename"])) and not is_valid(config(drift=["bogus"])) and not is_valid(config(drift="rename"))


# ------------------------------------------------------------ the translation is a bijection


@pytest.mark.parametrize("domain", list(domains.DOMAINS))
@pytest.mark.parametrize("seed", SEEDS)
def test_wire_round_trip_is_the_identity_on_items(domain, seed):
    spec = make_spec(domain, config(drift=ALL_KINDS), seed)
    wire = Wire(spec)
    extra = extra_fields(spec)
    for item in domains.initial_items(domain, seed, extra=extra):
        shown = wire.item(item)
        assert shown != item and "created_at" in shown and isinstance(shown["created_at"], int)
        body = {k: v for k, v in shown.items() if k not in ("id", "created_at")}
        back, problem = wire.body(body)
        assert problem is None and back == {k: v for k, v in item.items() if k not in ("id", "created_at")}


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_each_kind_changes_the_wire_form_alone(kind):
    spec = drifted_spec([kind], domain="invoices")
    wire = Wire(spec)
    item = domains.initial_items("invoices", 9, extra=extra_fields(spec))[0]
    shown = wire.item(item)
    changed = {k for k in set(item) | set(shown) if item.get(k) != shown.get(k)}
    assert changed, kind
    d = spec["drift"][kind]
    expected = {
        "rename": {d.get("field"), d.get("to")}, "cents": {d.get("field"), d.get("to")}, "epoch": {"created_at"},
        "nest": {d.get("name_field"), d.get("email_field"), d.get("key")}, "enum": {d.get("field")},
    }[kind]
    assert changed == expected


def test_wire_forms_follow_the_design_table():
    spec = drifted_spec(ALL_KINDS, domain="shipments", seed=2)
    d, wire = spec["drift"], Wire(spec)
    item = {"id": "X-1", "created_at": "2026-01-02T03:04:05Z", d["rename"]["field"]: "r", d["cents"]["field"]: 12.5,
            d["nest"]["name_field"]: "A", d["nest"]["email_field"]: "b", d["enum"]["field"]: d["enum"]["values"][0]}
    shown = wire.item(item)
    assert shown["created_at"] == 1767323045 and shown[d["cents"]["to"]] == 1250 and shown[d["rename"]["to"]] == "r"
    assert shown[d["nest"]["key"]] == {"name": "A", "email": "b"}
    value = d["enum"]["values"][0]
    assert shown[d["enum"]["field"]] == (value.upper() if d["enum"]["mode"] == "upper" else d["enum"]["prefix"] + value)


def test_wire_refuses_values_that_are_not_wire_values():
    spec = drifted_spec(["cents", "enum", "nest"], domain="invoices")
    d, wire = spec["drift"], Wire(spec)
    assert wire.body({d["cents"]["to"]: 12.5})[1] and wire.body({d["cents"]["to"]: True})[1]
    assert wire.body({d["enum"]["field"]: d["enum"]["values"][0]})[1] or d["enum"]["mode"] == "prefix"  # canonical form is no wire form
    assert wire.body({d["enum"]["field"]: "nope"})[1] and wire.body({d["nest"]["key"]: "text"})[1]


def test_old_field_names_are_not_part_of_the_wire_api():
    spec = drifted_spec(["rename", "cents", "nest"], domain="invoices")
    d = spec["drift"]
    body, problem = Wire(spec).body({d["rename"]["field"]: "x", d["cents"]["field"]: 3.5, d["nest"]["name_field"]: "n", "memo": "m"})
    assert problem is None and body == {"memo": "m"}


# ------------------------------------------------------------ engine: wire form out and in, canonical store


@pytest.fixture
def boot():
    started = []

    def make(spec):
        engine, base, sess = start(spec)
        started.append((engine, sess))
        return engine, sess

    yield make
    for engine, sess in started:
        sess.close()
        engine.stop()


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_engine_answers_in_wire_form_and_keeps_canonical_items(boot, kind):
    spec = drifted_spec([kind], domain="invoices", pagination="none")
    engine, sess = boot(spec)
    wire = Wire(spec)
    listed = sess.req("GET", sess.path).json()
    assert listed == [wire.item(i) for i in engine.store.values()] and listed != list(engine.store.values())
    first = next(iter(engine.store.values()))
    assert sess.req("GET", f"{sess.path}/{first['id']}").json() == wire.item(first)


def test_engine_translates_create_and_update_bodies(boot):
    spec = drifted_spec(ALL_KINDS, domain="invoices", pagination="none")
    engine, sess = boot(spec)
    wire = Wire(spec)
    extra = extra_fields(spec)
    canonical = domains.make_body("invoices", random.Random(1), extra)
    on_the_wire = {k: v for k, v in wire.item({"id": "", "created_at": "2026-01-01T00:00:00Z", **canonical}).items() if k not in ("id", "created_at")}
    created = sess.req("POST", sess.path, json=on_the_wire)
    assert created.status_code == 201
    stored = engine.store[created.json()["id"]]
    assert {k: v for k, v in stored.items() if k not in ("id", "created_at")} == canonical
    assert created.json() == wire.item(stored)
    d = spec["drift"]
    patch = {d["cents"]["to"]: 99900, d["rename"]["to"]: "renamed"}
    changed = sess.req("PATCH", f"{sess.path}/{stored['id']}", json=patch)
    assert changed.status_code == 200 and engine.store[stored["id"]][d["cents"]["field"]] == 999.0
    assert engine.store[stored["id"]][d["rename"]["field"]] == "renamed" and changed.json() == wire.item(engine.store[stored["id"]])


def test_canonical_bodies_get_no_help_on_the_wire(boot):
    spec = drifted_spec(["rename", "cents"], domain="invoices", pagination="none")
    engine, sess = boot(spec)
    d = spec["drift"]
    canonical = domains.make_body("invoices", random.Random(2), extra_fields(spec))
    assert sess.req("POST", sess.path, json=canonical).status_code == 400  # the wire names are missing
    target = next(iter(engine.store))
    before = dict(engine.store[target])
    resp = sess.req("PATCH", f"{sess.path}/{target}", json={d["rename"]["field"]: "ignored", d["cents"]["field"]: 1.0})
    assert resp.status_code == 200 and engine.store[target] == before  # a v1 update is silently a no-op


def test_filter_uses_the_wire_name_and_wire_value(boot):
    spec = drifted_spec(["enum"], domain="invoices", pagination="none")  # the filter field of invoices is `state`
    spec["drift"]["enum"] = {"field": "state", "mode": "prefix", "prefix": "st_", "values": ["draft", "sent", "paid", "void"]}
    engine, sess = boot(spec)
    wanted = [i for i in engine.store.values() if i["state"] == "paid"]
    assert wanted
    got = sess.req("GET", sess.path, params={"state": "st_paid"}).json()
    assert [i["id"] for i in got] == [i["id"] for i in wanted] and all(i["state"] == "st_paid" for i in got)
    assert sess.req("GET", sess.path, params={"state": "paid"}).json() == []  # the canonical value matches nothing


def test_filter_field_rename_renames_the_query_parameter(boot):
    spec = drifted_spec(["rename"], domain="shipments", pagination="link_header")
    spec["drift"]["rename"] = {"field": "status", "to": "state"}  # the filter field of shipments is `status`
    engine, sess = boot(spec)
    value = next(iter(engine.store.values()))["status"]
    wanted = [i["id"] for i in engine.store.values() if i["status"] == value]
    resp = sess.req("GET", sess.path, params={"state": value})
    assert resp.status_code == 200 and [i["id"] for i in resp.json()] == wanted[:10]
    if len(wanted) > 10:  # the next page link keeps the wire parameter
        assert f"state={value}" in resp.headers["link"]
    assert sess.req("GET", sess.path, params={"status": value}).json() == [wire_item for wire_item in sess.req("GET", sess.path).json()][:10]  # old name: ignored, no filter


def test_drift_has_no_http_error_for_a_drifted_client_read(boot):
    spec = drifted_spec(ALL_KINDS)
    engine, sess = boot(spec)
    resp = sess.req("GET", sess.path)
    assert resp.status_code == 200 and all(e["status"] < 400 for e in engine.log)


# ------------------------------------------------------------ tests and grading


def test_update_tests_change_a_drifted_field_and_old_specs_keep_their_tests():
    spec = make_spec("invoices", config(drift=ALL_KINDS), 5)
    for seed in range(5):
        update = make_tests(spec, seed)[5]["kwargs"]["changes"]
        assert set(update) <= set(domains.writable_fields("invoices", extra_fields(spec))) and set(update) & set(drift_fields(spec))
    assert make_tests(spec_for(domain="invoices"), 3) == make_tests(spec_for(domain="invoices"), 3)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_reference_client_scores_one_with_each_drift_kind(tmp_path, kind):
    task = light_task(tmp_path, drift=[kind])
    g = grade_flaws(tmp_path, task)
    assert g["pass_rate"] == 1.0 and g["strict_pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0, g["results"]


@pytest.mark.parametrize("domain", list(domains.DOMAINS))
def test_reference_client_scores_one_with_all_drift_kinds_in_every_domain(tmp_path, domain):
    for seed in (4, 8):
        g = grade_flaws(tmp_path, light_task(tmp_path, domain=domain, seed=seed, drift=ALL_KINDS))
        assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0, (domain, seed, g["results"])


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_a_client_that_returns_wire_shaped_values_fails(tmp_path, kind):
    task = light_task(tmp_path, domain="invoices", drift=[kind])
    g = grade_flaws(tmp_path, task, (f"no_drift_{kind}",))
    assert g["pass_rate"] < 1.0 and set(g["failure_counts"]) <= {"return_mismatch", "bad_request_400", "state_mismatch"}, g["failure_counts"]
    first = next(r for r in g["results"] if not r["passed"])
    assert first["index"] == 0 and first["method"].startswith("list_")  # the first failing call is a plain read: wrong values, no error


def test_the_first_failure_of_a_drifted_client_is_a_wrong_value_not_an_error(tmp_path):
    g = grade_flaws(tmp_path, light_task(tmp_path, drift=ALL_KINDS), tuple(f"no_drift_{k}" for k in ALL_KINDS))
    first = g["results"][0]
    assert not first["passed"] and first["reason"] == "return_mismatch" and first["invented_calls"] == 0


def test_built_task_with_drift_has_wire_forms_in_the_recording_and_a_perfect_reference_client(tmp_path):
    from har2client.tierb.build import build_tierb_task
    from har2client.tierb.grader import grade_tierb

    d = build_tierb_task({"index": 0, "domain": "invoices", "config": config(drift=ALL_KINDS), "seed": 3}, tmp_path, negatives=False)
    spec = read_json(d / "engine.json")
    har = (d / "session.har").read_text()
    assert spec["drift"]["cents"]["to"] in har and spec["drift"]["rename"]["to"] in har
    g = grade_tierb(d, d / "ref_client.py")
    assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0
    assert "_from_wire" in generate_client_source(spec) and "_from_wire" not in generate_client_source(spec, tuple(f"no_drift_{k}" for k in ALL_KINDS))
