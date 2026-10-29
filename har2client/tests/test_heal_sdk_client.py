"""The SDK-style client (`heal/sdk_client.py`) behaves like the compact reference client of `tierb/refclient.py`."""

import ast
import random
import statistics
import subprocess
import sys

import pytest

from har2client.heal import mutations
from har2client.tierb import config as tcfg
from har2client.tierb.config import DRIFT_KINDS
from har2client.tierb.grader import grade_tierb
from har2client.tierb.refclient import FLAWS, generate_client_source
from har2client.tierb.spec import make_spec
from har2client.heal.sdk_client import KNOWN_FLAWS, generate_sdk_client_source
from test_tierb_protocols import ATOMS, _combos
from test_tierb_support import build_cached, light_task, read_json, write_client

pytestmark = pytest.mark.slow  # the tests in this file take 5-40 s each

SAMPLE_SEED = 3
SAMPLE = tcfg.sample_configs(SAMPLE_SEED, 32)
LINES_MIN, LINES_MAX = 400, 800
# flaw -> config predicate under which the flaw changes behaviour (otherwise it is a no-op)
FLAW_MATTERS = {
    "ignore_pagination": lambda c: c["pagination"] != "none",
    "no_relogin": lambda c: c["token_expiry"] == "on",
    "no_if_match": lambda c: c["etag_update"] == "on",
    "drop_hidden_header": lambda c: c["hidden_header"] == "on",
    "return_envelope": lambda c: c["envelope"] == "on",
    "no_csrf": lambda c: c["auth"] == "cookie_csrf",
}


def _task(row):
    return build_cached(row["domain"], row["seed"], row["index"], **row["config"])


def _shape(grade):
    """What must be equal between two clients: per test, pass flag and failure reason."""
    return [(r["index"], r["passed"], r["reason"]) for r in grade["results"]]


def _grade(task_dir, tmp_path, source, name):
    return grade_tierb(task_dir, write_client(tmp_path, source, name))


def _ids(rows):
    return [f"{r['index']}-{r['domain']}-{r['config']['auth']}-{r['config']['pagination']}" for r in rows]


def test_every_refclient_flaw_is_known_here_and_unknown_ones_raise():
    assert set(KNOWN_FLAWS) == set(FLAWS)
    spec = make_spec("tickets", tcfg.baseline_config(), 1)
    with pytest.raises(NotImplementedError, match="made_up_flaw"):
        generate_sdk_client_source(spec, ("made_up_flaw",))


@pytest.mark.parametrize("row", SAMPLE, ids=_ids(SAMPLE))
def test_sdk_client_scores_like_the_reference_client(row, tmp_path):
    d = _task(row)
    spec = read_json(d / "engine.json")
    sdk = _grade(d, tmp_path, generate_sdk_client_source(spec), "sdk.py")
    ref = _grade(d, tmp_path, generate_client_source(spec), "ref.py")
    assert sdk["pass_rate"] == 1.0, sdk["results"]
    assert _shape(sdk) == _shape(ref)
    assert sdk["invented_endpoint_calls"] == 0 and sdk["hardcoded_secret"] is False and sdk["hardcoded_host"] is False
    assert sdk["requests_logged"] == ref["requests_logged"]  # the same number of requests on the wire


@pytest.mark.parametrize("row", SAMPLE, ids=_ids(SAMPLE))
def test_each_flaw_fails_the_same_tests_as_in_the_reference_client(row, tmp_path):
    d = _task(row)
    spec = read_json(d / "engine.json")
    for flaw in FLAW_MATTERS:
        if not FLAW_MATTERS[flaw](row["config"]):
            continue
        sdk = _grade(d, tmp_path, generate_sdk_client_source(spec, (flaw,)), f"sdk_{flaw}.py")
        ref = _grade(d, tmp_path, generate_client_source(spec, (flaw,)), f"ref_{flaw}.py")
        assert sdk["pass_rate"] < 1.0, flaw
        assert _shape(sdk) == _shape(ref), flaw
        assert sdk["failure_counts"] == ref["failure_counts"], flaw


def _v1_clients(spec):
    """(mutation, v1 spec, v1 flaws) for every mutation that applies to the v2 spec."""
    for m in mutations.MUTATIONS:
        if m.applies(spec["config"]):
            try:
                v1 = mutations.derive_v1_spec(spec, m.name, 11)
            except ValueError:
                continue
            yield m.name, v1, mutations.v1_flaws(v1)


@pytest.mark.parametrize("row", SAMPLE[:12], ids=_ids(SAMPLE[:12]))
def test_v1_clients_fail_on_v2_like_the_compact_v1_clients(row, tmp_path):
    """The repair task shows `client_v1.py` on the new API: the SDK v1 client must break exactly where the compact one does."""
    d = _task(row)
    v2 = read_json(d / "engine.json")
    for name, v1, flaws in _v1_clients(v2):
        sdk = _grade(d, tmp_path, generate_sdk_client_source(v1, flaws), f"sdk_{name}.py")
        ref = _grade(d, tmp_path, generate_client_source(v1, flaws), f"ref_{name}.py")
        assert sdk["pass_rate"] < 1.0, name
        assert _shape(sdk) == _shape(ref), name


def _sources():
    return [generate_sdk_client_source(make_spec(r["domain"], r["config"], r["seed"])) for r in SAMPLE]


def test_line_count_is_sdk_sized():
    counts = [src.count("\n") for src in _sources()]
    assert LINES_MIN <= min(counts) and max(counts) <= LINES_MAX, (min(counts), statistics.median(counts), max(counts))


def test_line_count_of_the_largest_config_is_in_range():
    full = dict(auth="cookie_csrf", pagination="link_header", token_expiry="on", etag_update="on", hidden_header="on", envelope="on")
    for domain in ("shipments", "invoices"):
        n = generate_sdk_client_source(make_spec(domain, full, 5)).count("\n")
        assert LINES_MIN <= n <= LINES_MAX, n


def test_output_is_deterministic_and_has_no_timestamps():
    row = SAMPLE[0]
    spec = make_spec(row["domain"], row["config"], row["seed"])
    assert generate_sdk_client_source(spec, ("no_csrf",)) == generate_sdk_client_source(spec, ("no_csrf",))
    assert generate_sdk_client_source(spec) == generate_sdk_client_source(make_spec(row["domain"], row["config"], row["seed"]))


def test_imports_only_httpx_and_the_standard_library():
    for src in _sources():
        roots = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots.add((node.module or "").split(".")[0])
        assert roots <= set(sys.stdlib_module_names) | {"httpx"}, roots


def test_isolated_python_can_import_the_client(tmp_path):
    row = SAMPLE[1]
    path = write_client(tmp_path, generate_sdk_client_source(make_spec(row["domain"], row["config"], row["seed"])), "sdk_client_mod.py")
    code = "import importlib.util,sys;s=importlib.util.spec_from_file_location('m',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.Client.__name__)"
    out = subprocess.run([sys.executable, "-I", "-c", code, str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "Client"


def test_public_surface_matches_the_reference_client(tmp_path):
    row = SAMPLE[2]
    spec = make_spec(row["domain"], row["config"], row["seed"])
    namespaces = []
    for gen in (generate_sdk_client_source, generate_client_source):
        ns: dict = {}
        exec(compile(gen(spec), "client", "exec"), ns)
        namespaces.append(ns["Client"])
    sdk, ref = namespaces
    import inspect

    public = lambda cls: {n: str(inspect.signature(f, eval_str=True)) for n, f in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}
    ref_public = public(ref)
    sdk_public = public(sdk)
    assert {k: v for k, v in sdk_public.items() if k in ref_public} == ref_public
    assert str(inspect.signature(sdk.__init__, eval_str=True)) == str(inspect.signature(ref.__init__, eval_str=True))


def test_models_round_trip_items_exactly_and_keep_unknown_keys():
    spec = make_spec("shipments", tcfg.baseline_config(), 4)
    ns: dict = {}
    exec(compile(generate_sdk_client_source(spec), "client", "exec"), ns)
    model = ns["Shipment"]
    item = {"zz_unknown": {"a": [1, 2.0, True]}, "id": "SHP-1", "weight_kg": 12.0, "fragile": False, "status": "pending", "created_at": "2026-01-01T00:00:00Z"}
    assert model.from_dict(item).to_dict() == item
    assert list(model.from_dict(item).to_dict()) == list(item)  # key order survives
    partial = {"status": "delivered"}
    assert model.from_dict(partial).to_dict() == partial  # absent keys stay absent
    assert str(model.from_dict({"weight_kg": 1.0}).to_dict()) == "{'weight_kg': 1.0}"  # 1.0 is not turned into 1
    obj = model.from_dict(item)
    obj.status = "delivered"
    assert obj.to_dict()["status"] == "delivered"


def test_exceptions_are_typed():
    spec = make_spec("shipments", tcfg.baseline_config(), 4)
    ns: dict = {}
    exec(compile(generate_sdk_client_source(spec), "client", "exec"), ns)
    assert issubclass(ns["AuthError"], ns["ApiError"]) and issubclass(ns["NotFoundError"], ns["ApiError"])


def test_retry_helper_retries_connection_errors_only():
    spec = make_spec("shipments", tcfg.baseline_config(), 4)
    ns: dict = {}
    exec(compile(generate_sdk_client_source(spec), "client", "exec"), ns)
    httpx, retries, policy = ns["httpx"], ns["with_retries"], ns["RetryPolicy"](attempts=3, backoff=0.0)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("down")
        return "ok"

    assert retries(flaky, policy) == "ok" and len(calls) == 3
    calls.clear()
    with pytest.raises(httpx.ReadTimeout):
        retries(lambda: (calls.append(1), (_ for _ in ()).throw(httpx.ReadTimeout("slow")))[1], policy)
    assert len(calls) == 1


@pytest.mark.parametrize("over", [{"brand_new_switch": "on"}, {"drift": ["bogus"]}, {"rate_limit": "sometimes"}])
def test_unknown_config_switches_raise_not_implemented(over):
    spec = make_spec("shipments", tcfg.baseline_config(), 4)
    spec["config"] = {**spec["config"], **over}
    with pytest.raises(NotImplementedError, match=next(iter(over))):
        generate_sdk_client_source(spec)


def test_unknown_level_of_a_known_switch_raises_not_implemented():
    spec = make_spec("shipments", tcfg.baseline_config(), 4)
    spec["config"] = {**spec["config"], "pagination": "keyset"}
    with pytest.raises(NotImplementedError, match="pagination=keyset"):
        generate_sdk_client_source(spec)


def test_switches_set_off_do_not_raise():
    spec = make_spec("shipments", tcfg.baseline_config(), 4)
    spec["config"] = {**spec["config"], "rate_limit": "off", "drift": []}
    assert "class Client" in generate_sdk_client_source(spec)


# ------------------------------------------------------------------------------ hard-repair switches (drift, protocols)

PARITY_DOMAINS = ("tickets", "bookings")
BEARER = {"auth": "bearer_login"}
# (flaw, config overrides under which the flaw changes behaviour)
NEW_FLAWS = [
    *((f"no_drift_{k}", {"drift": [k]}) for k in DRIFT_KINDS),
    ("no_rate_limit_retry", {"rate_limit": "on"}),
    ("hammer_rate_limit", {"rate_limit": "on"}),
    ("no_job_poll", {"async_create": "on"}),
    ("no_refresh", {**BEARER, "refresh_rotation": "on"}),
    ("no_relogin", {**BEARER, "refresh_rotation": "on"}),
    ("no_idempotency_key", {"idempotency_key": "on"}),
    ("no_moved_path", {"moved": "on"}),
]


def _atom_config(atom):
    name, value = atom
    return {"drift": [value]} if name == "drift" else {name: "on", **(BEARER if name == "refresh_rotation" else {})}


def _both(tmp_path, task, flaws=()):
    """(sdk grade, compact grade) of the same flaws on the same task."""
    spec = read_json(task / "engine.json")
    tag = "_".join(flaws) or "ok"
    return (
        _grade(task, tmp_path, generate_sdk_client_source(spec, flaws), f"sdk_{tag}.py"),
        _grade(task, tmp_path, generate_client_source(spec, flaws), f"ref_{tag}.py"),
    )


def _assert_same(sdk, ref, flaws=()):
    assert _shape(sdk) == _shape(ref), flaws
    assert sdk["failure_counts"] == ref["failure_counts"], flaws
    assert sdk["invented_endpoint_calls"] == ref["invented_endpoint_calls"] == 0, flaws
    assert sdk["hardcoded_secret"] is False and sdk["hardcoded_host"] is False


@pytest.mark.parametrize("domain", PARITY_DOMAINS)
@pytest.mark.parametrize("atom", ATOMS, ids=[f"{n}:{v}" for n, v in ATOMS])
def test_every_new_switch_alone_scores_like_the_reference_client(tmp_path, atom, domain):
    sdk, ref = _both(tmp_path, light_task(tmp_path, domain=domain, seed=6, **_atom_config(atom)))
    assert sdk["pass_rate"] == 1.0 and sdk["strict_pass_rate"] == 1.0, sdk["results"]
    _assert_same(sdk, ref)


@pytest.mark.parametrize("atoms,conf,domain", _combos())
def test_combinations_of_new_switches_score_like_the_reference_client(tmp_path, atoms, conf, domain):
    task = light_task(tmp_path, domain=domain, seed=17, **conf)
    sdk, ref = _both(tmp_path, task)
    assert sdk["pass_rate"] == 1.0 and sdk["strict_pass_rate"] == 1.0, (atoms, sdk["results"])
    _assert_same(sdk, ref)
    if "rate_limit" not in conf:  # with a rate limit the count of 429s depends on timing
        assert sdk["requests_logged"] == ref["requests_logged"]


@pytest.mark.parametrize("domain", PARITY_DOMAINS)
@pytest.mark.parametrize("flaw,over", NEW_FLAWS, ids=[f for f, _ in NEW_FLAWS])
def test_each_new_flaw_fails_the_same_tests_for_the_same_reasons(tmp_path, flaw, over, domain):
    task = light_task(tmp_path, domain=domain, seed=6, **over)
    sdk, ref = _both(tmp_path, task, (flaw,))
    assert sdk["pass_rate"] < 1.0, flaw
    _assert_same(sdk, ref, (flaw,))


def _flaws_for(atoms):
    by_atom = {"rate_limit": ["no_rate_limit_retry", "hammer_rate_limit"], "async_create": ["no_job_poll"], "refresh_rotation": ["no_refresh"],
               "idempotency_key": ["no_idempotency_key"], "moved": ["no_moved_path"]}
    return [f"no_drift_{v}" if n == "drift" else f for n, v in atoms for f in ([None] if n == "drift" else by_atom[n])]


@pytest.mark.parametrize("atoms,conf,domain", _combos())
def test_a_flaw_in_a_combination_fails_like_the_reference_client(tmp_path, atoms, conf, domain):
    flaw = random.Random(str(atoms)).choice(_flaws_for(atoms))
    task = light_task(tmp_path, domain=domain, seed=17, **conf)
    sdk, ref = _both(tmp_path, task, (flaw,))
    assert sdk["pass_rate"] < 1.0, (atoms, flaw)
    _assert_same(sdk, ref, (flaw,))


def _load(spec, flaws=()):
    ns: dict = {}
    exec(compile(generate_sdk_client_source(spec, flaws), "client", "exec"), ns)
    return ns


def _spec(domain="invoices", seed=5, **over):
    return make_spec(domain, {**tcfg.baseline_config(), **over}, seed)


def test_drift_is_translated_in_the_model_and_round_trips():
    spec = _spec(drift=list(DRIFT_KINDS), pagination="none")
    model = _load(spec)["Invoice"]
    d = spec["drift"]
    canonical = {"id": "INV-1", "created_at": "2026-01-02T03:04:05Z", d["rename"]["field"]: "r", d["cents"]["field"]: 12.5,
                 d["nest"]["name_field"]: "A", d["nest"]["email_field"]: "b@x.io", d["enum"]["field"]: d["enum"]["values"][0]}
    wire = model.from_dict(canonical).to_wire()
    wire["created_at"] = 1767323045  # `epoch` only changes responses: the model does not send a timestamp as a number
    assert wire[d["cents"]["to"]] == 1250 and wire[d["nest"]["key"]] == {"name": "A", "email": "b@x.io"}
    assert d["rename"]["field"] not in wire and wire[d["rename"]["to"]] == "r" and wire[d["enum"]["field"]] != canonical[d["enum"]["field"]]
    assert model.parse(wire) == canonical
    assert model.from_wire(wire).to_dict() == canonical
    assert model.query_to_wire({d["rename"]["field"]: "x"}) == {d["rename"]["to"]: "x"}


def test_no_new_switch_means_no_new_code():
    src = generate_sdk_client_source(_spec())
    assert "to_wire" in src and "Identity" in src
    assert "datetime" not in src and "uuid" not in src and "retry-after" not in src.lower() and "ITEM_PREFIX" not in src


def test_protocol_behaviour_lives_in_the_layer_that_owns_it():
    ns = _load(_spec("tickets", 3, **BEARER, rate_limit="on", async_create="on", refresh_rotation="on", idempotency_key="on", moved="on"))
    transport, bearer = ns["Transport"], ns["BearerAuth"]
    assert {"await_job", "item_path"} <= set(vars(transport)) and "refresh" in vars(bearer)
    assert ns["RATE_LIMIT_RETRIES"] > 1 and ns["ITEM_PREFIX"].startswith("/") and ns["IDEMPOTENCY_HEADER"]
    assert not {"await_job", "item_path", "refresh"} & set(vars(ns["Tickets"]))  # the resource only calls them


def test_new_switch_flaws_remove_only_their_layer():
    full = dict(**BEARER, rate_limit="on", async_create="on", refresh_rotation="on", idempotency_key="on", moved="on", drift=list(DRIFT_KINDS))
    spec = _spec("tickets", 3, **full)
    whole = generate_sdk_client_source(spec)
    for flaw, _ in NEW_FLAWS:
        assert generate_sdk_client_source(spec, (flaw,)) != whole, flaw
    assert "await_job" not in generate_sdk_client_source(spec, ("no_job_poll",))
    assert "ITEM_PREFIX" not in generate_sdk_client_source(spec, ("no_moved_path",))
    assert "IDEMPOTENCY_HEADER" not in generate_sdk_client_source(spec, ("no_idempotency_key",))


def test_line_count_with_new_switches_is_in_range():
    counts = []
    for param in _combos():
        _, conf, domain = param.values
        counts.append(generate_sdk_client_source(make_spec(domain, conf, 17)).count("\n"))
    full = dict(auth="bearer_login", pagination="link_header", etag_update="on", hidden_header="on", envelope="on", rate_limit="on",
                async_create="on", refresh_rotation="on", idempotency_key="on", moved="on", drift=list(DRIFT_KINDS))
    counts.append(generate_sdk_client_source(make_spec("shipments", {**tcfg.baseline_config(), **full}, 5)).count("\n"))
    assert LINES_MIN <= min(counts) and max(counts) <= LINES_MAX, (min(counts), statistics.median(counts), max(counts))
