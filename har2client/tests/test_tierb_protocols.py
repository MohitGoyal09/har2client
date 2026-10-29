"""Protocol switches (control flow): rate_limit, async_create, refresh_rotation, idempotency_key, moved; and combinations."""

import random
import re
import time

import pytest

from har2client import runner
from har2client.tierb import config as cfg
from har2client.tierb.config import DRIFT_KINDS, is_valid
from har2client.tierb.domains import DOMAINS
from har2client.tierb.engine import ERROR_DETAILS, TERSE_CODES, TierBEngine
from har2client.tierb.grader import _stray_writes
from har2client.tierb.refclient import FLAWS, generate_client_source
from test_tierb_support import config, grade_flaws, light_task, read_json, spec_for, start, write_client

pytestmark = pytest.mark.slow  # the tests in this file take 5-40 s each

PROTOCOLS = ("rate_limit", "async_create", "refresh_rotation", "idempotency_key", "moved")


@pytest.fixture
def boot():
    started = []

    def make(**kw):
        engine, base, sess = start(spec_for(**kw))
        started.append((engine, sess))
        return engine, sess

    yield make
    for engine, sess in started:
        sess.close()
        engine.stop()


def run_with_log(tmp_path, task_dir, source):
    """Run `source` as the client against the task's hidden tests; (run result, engine log)."""
    task, spec, tests = (read_json(task_dir / n) for n in ("task.json", "engine.json", "tests.json"))
    creds = task["credentials"]["test"]
    engine = TierBEngine(spec, task["seeds"]["test"], creds)
    base = engine.start()
    try:
        run = runner.run_client(write_client(tmp_path, source), base, creds["password"], tests, ctor_args=[creds["username"], creds["password"]])
    finally:
        engine.stop()
    return run, list(engine.log)


# ------------------------------------------------------------ config rules and spec


def test_switches_default_to_off_and_old_configs_stay_valid():
    assert all(is_valid(c) for c in cfg.all_configs()[:50]) and cfg.switch(config(), "drift") == []
    assert all(cfg.switch(config(), name) == "off" for name in PROTOCOLS)
    assert set(DRIFT_KINDS) == {"rename", "cents", "epoch", "nest", "enum"}


def test_refresh_rotation_is_a_mode_of_bearer_login_without_token_expiry():
    assert is_valid(config(auth="bearer_login", refresh_rotation="on"))
    assert not is_valid(config(refresh_rotation="on"))  # apikey
    assert not is_valid(config(auth="cookie_csrf", refresh_rotation="on"))
    assert not is_valid(config(auth="bearer_login", token_expiry="on", refresh_rotation="on"))
    assert not is_valid(config(rate_limit="sometimes")) and not is_valid({**config(), "nonsense": "on"})


def test_protocol_spec_fields_are_seed_chosen_and_deterministic():
    over = dict(auth="bearer_login", **{name: "on" for name in PROTOCOLS})
    spec = spec_for(task_seed=3, **over)
    assert spec == spec_for(task_seed=3, **over)
    assert spec["rate_limit"]["limit"] in (3, 4, 5) and spec["rate_limit"]["retry_after"] == 1
    assert spec["async_create"]["pending_polls"] in (1, 2) and spec["refresh"]["limit"] in (3, 4)
    seen = [spec_for(task_seed=s, **over) for s in range(30)]
    assert len({s["rate_limit"]["limit"] for s in seen}) == 3 and len({s["moved"]["prefix"] for s in seen}) == 3
    assert len({s["async_create"]["job_word"] for s in seen}) == 3 and len({s["refresh"]["path"] for s in seen}) > 1


# ------------------------------------------------------------ rate_limit


def test_rate_limit_answers_429_with_retry_after_and_recovers(boot):
    engine, sess = boot(rate_limit="on")
    limit = sess.spec["rate_limit"]["limit"]
    codes = [sess.req("GET", sess.path).status_code for _ in range(limit + 1)]
    assert codes == [200] * limit + [429]
    last = engine.log[-1]
    assert last["status"] == 429 and last["op_id"] == "list" and last["response"]["error"] == "rate_limited"  # logged like any error
    again = sess.req("GET", sess.path)  # a retry at once is still refused
    assert again.status_code == 429 and again.headers["retry-after"] == "1"
    time.sleep(1.05)
    assert sess.req("GET", sess.path).status_code == 200


def test_rate_limit_error_is_terse_in_terse_mode():
    spec = {**spec_for(rate_limit="on"), "error_detail": "terse"}
    engine, base, sess = start(spec)
    try:
        bodies = [sess.req("GET", sess.path) for _ in range(spec["rate_limit"]["limit"] + 1)]
        assert bodies[-1].status_code == 429 and bodies[-1].json() == {"error": "too_many_requests"}
    finally:
        sess.close()
        engine.stop()


def test_every_new_error_code_has_a_terse_code():
    from har2client.tierb import engine

    import inspect

    codes = set(re.findall(r'_error\(\d+, "(\w+)"', inspect.getsource(engine)))
    assert {"rate_limited", "idempotency_conflict"} <= codes <= set(TERSE_CODES) and set(ERROR_DETAILS) == {"verbose", "terse"}


@pytest.mark.parametrize("seed", range(1, 5))
def test_the_test_run_hits_the_limit_and_the_reference_client_waits_it_out(tmp_path, seed):
    task = light_task(tmp_path, seed=seed, rate_limit="on")
    spec = read_json(task / "engine.json")
    run, log = run_with_log(tmp_path, task, generate_client_source(spec))
    assert sum(e["status"] == 429 for e in log) >= 1 and all(r["ok"] for r in run["results"])
    g = grade_flaws(tmp_path, task)
    assert g["pass_rate"] == 1.0 and g["strict_pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0  # retries are no invented calls


@pytest.mark.parametrize("flaw", ["hammer_rate_limit", "no_rate_limit_retry"])
def test_a_client_that_ignores_retry_after_does_not_pass_by_luck(tmp_path, flaw):
    for seed in range(1, 4):
        g = grade_flaws(tmp_path, light_task(tmp_path, seed=seed, rate_limit="on"), (flaw,))
        assert g["pass_rate"] < 1.0 and g["failure_counts"].get("rate_limited_429", 0) >= 1, (seed, g["failure_counts"])
        assert g["invented_endpoint_calls"] == 0


# ------------------------------------------------------------ async_create


def test_async_create_answers_202_then_the_job_finishes_after_its_polls(boot):
    engine, sess = boot(async_create="on")
    polls = sess.spec["async_create"]["pending_polls"]
    from har2client.tierb import domains

    body = domains.make_body("shipments", random.Random(4))
    before = len(engine.store)
    resp = sess.req("POST", sess.path, json=body)
    assert resp.status_code == 202 and resp.json()["status"] == "pending" and len(engine.store) == before  # nothing stored yet
    location = resp.headers["location"]
    assert location.startswith(sess.spec["base_path"] + "/" + sess.spec["async_create"]["job_word"] + "/")
    url = sess.base.removesuffix(sess.spec["base_path"]) + location
    for _ in range(polls):
        assert sess.req("GET", url).json()["status"] == "pending" and len(engine.store) == before
    done = sess.req("GET", url).json()
    assert done["status"] == "done" and len(engine.store) == before + 1
    assert {k: v for k, v in done["result"].items() if k not in ("id", "created_at")} == body
    assert engine.store[done["result"]["id"]] == done["result"]
    assert sess.req("GET", url).json()["result"] == done["result"] and len(engine.store) == before + 1  # polling again creates nothing
    created = [e for e in engine.log if e.get("created_id")]
    assert len(created) == 1 and created[0]["op_id"] == "job"
    assert sess.req("GET", url.replace(location.rsplit("/", 1)[1], "job_nope")).status_code == 404


def test_async_create_with_envelope_wraps_the_job_answer(boot):
    engine, sess = boot(async_create="on", envelope="on")
    from har2client.tierb import domains

    resp = sess.req("POST", sess.path, json=domains.make_body("shipments", random.Random(4)))
    assert resp.status_code == 202 and resp.json()["data"]["status"] == "pending"


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_reference_client_polls_and_returns_the_item(tmp_path, seed):
    task = light_task(tmp_path, seed=seed, async_create="on")
    g = grade_flaws(tmp_path, task)
    assert g["pass_rate"] == 1.0 and g["strict_pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0  # job polls are legitimate


def test_a_client_that_does_not_poll_fails_the_create_test(tmp_path):
    g = grade_flaws(tmp_path, light_task(tmp_path, async_create="on"), ("no_job_poll",))
    create = next(r for r in g["results"] if r["method"].startswith("create_"))
    assert not create["passed"] and create["reason"] == "state_mismatch"


# ------------------------------------------------------------ refresh_rotation


def test_login_returns_a_token_pair_and_a_second_password_login_is_refused(boot):
    engine, sess = boot(auth="bearer_login", refresh_rotation="on")
    first = sess.login()
    body = first.json()
    assert first.status_code == 200 and body[sess.spec["login"]["token_field"]] and body[sess.spec["refresh"]["field"]]
    second = sess.login()
    assert second.status_code == 429 and "retry-after" not in second.headers and engine.log[-1]["response"]["error"] == "rate_limited"
    assert engine.log[-1]["op_id"] == "login"


def test_access_token_expires_and_refresh_rotates_the_pair(boot):
    engine, sess = boot(auth="bearer_login", refresh_rotation="on")
    spec = sess.spec
    pair = sess.login().json()
    sess.token = pair[spec["login"]["token_field"]]
    for _ in range(spec["refresh"]["limit"]):
        assert sess.req("GET", sess.path).status_code == 200
    expired = sess.req("GET", sess.path)
    assert expired.status_code == 401 and engine.log[-1]["response"]["error"] == "token_expired"
    refresh_url = sess.base + spec["refresh"]["path"]
    new = sess.http.post(refresh_url, json={spec["refresh"]["field"]: pair[spec["refresh"]["field"]]}, headers=sess.hidden())
    assert new.status_code == 200 and engine.log[-1]["op_id"] == "refresh"
    new_pair = new.json()
    assert new_pair[spec["login"]["token_field"]] != pair[spec["login"]["token_field"]] and new_pair[spec["refresh"]["field"]] != pair[spec["refresh"]["field"]]
    again = sess.http.post(refresh_url, json={spec["refresh"]["field"]: pair[spec["refresh"]["field"]]}, headers=sess.hidden())
    assert again.status_code == 401  # the old refresh token stopped working
    assert sess.req("GET", sess.path).status_code == 401  # and so did the old access token
    sess.token = new_pair[spec["login"]["token_field"]]
    assert sess.req("GET", sess.path).status_code == 200


def test_refresh_needs_a_json_body_and_a_known_token(boot):
    engine, sess = boot(auth="bearer_login", refresh_rotation="on")
    url = sess.base + sess.spec["refresh"]["path"]
    assert sess.http.post(url, content=b"x=1", headers={"content-type": "text/plain"}).status_code == 400
    assert sess.http.post(url, json={sess.spec["refresh"]["field"]: "nope"}).status_code == 401
    assert sess.http.post(url, json={sess.spec["refresh"]["field"]: 5}).status_code == 401


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_reference_client_refreshes_instead_of_logging_in_again(tmp_path, seed):
    task = light_task(tmp_path, seed=seed, auth="bearer_login", refresh_rotation="on")
    spec = read_json(task / "engine.json")
    run, log = run_with_log(tmp_path, task, generate_client_source(spec))
    assert all(r["ok"] for r in run["results"])
    ops = [e["op_id"] for e in log]
    assert ops.count("login") == 1 and ops.count("refresh") >= 1 and all(e["status"] != 429 for e in log)
    g = grade_flaws(tmp_path, task)
    assert g["pass_rate"] == 1.0 and g["strict_pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0  # the refresh call is legitimate


@pytest.mark.parametrize("flaw", ["no_refresh", "no_relogin"])
def test_a_client_without_the_refresh_flow_fails(tmp_path, flaw):
    g = grade_flaws(tmp_path, light_task(tmp_path, auth="bearer_login", refresh_rotation="on"), (flaw,))
    assert g["pass_rate"] < 1.0 and set(g["failure_counts"]) <= {"auth_401", "rate_limited_429"}


# ------------------------------------------------------------ idempotency_key


def test_writes_need_a_key_and_reads_do_not(boot):
    engine, sess = boot(idempotency_key="on")
    name = sess.spec["idempotency"]["header"]
    first = next(iter(engine.store))
    assert sess.req("GET", sess.path).status_code == 200
    for method, url, kw in (("POST", sess.path, {"json": {}}), ("PATCH", f"{sess.path}/{first}", {"json": {}}), ("DELETE", f"{sess.path}/{first}", {})):
        resp = sess.req(method, url, **kw)
        assert resp.status_code == 400 and resp.json()["error"] == "missing_header" and name in resp.json()["message"], method
    assert first in engine.store and sess.req("DELETE", f"{sess.path}/{first}", headers={name: "k1"}).status_code == 204


def test_same_key_replays_the_first_answer_and_a_different_body_is_refused(boot):
    from har2client.tierb import domains

    engine, sess = boot(idempotency_key="on")
    name = sess.spec["idempotency"]["header"]
    body = domains.make_body("shipments", random.Random(8))
    before = len(engine.store)
    one = sess.req("POST", sess.path, json=body, headers={name: "key-a"})
    two = sess.req("POST", sess.path, json=body, headers={name: "key-a"})
    assert one.status_code == two.status_code == 201 and one.json() == two.json() and two.headers["idempotent-replay"] == "true"
    assert len(engine.store) == before + 1
    assert [bool(e.get("replay")) for e in engine.log] == [False, True] and engine.log[-1]["op_id"] == "create"
    other = sess.req("POST", sess.path, json={**body, "carrier": "Orbitrail" if body["carrier"] != "Orbitrail" else "Kestrel Lines"}, headers={name: "key-a"})
    assert other.status_code == 422 and other.json()["error"] == "idempotency_conflict" and len(engine.store) == before + 1


def test_a_failed_write_does_not_use_up_its_key(boot):
    from har2client.tierb import domains

    engine, sess = boot(idempotency_key="on")
    name = sess.spec["idempotency"]["header"]
    assert sess.req("POST", sess.path, json={"x": 1}, headers={name: "key-b"}).status_code == 400
    ok = sess.req("POST", sess.path, json=domains.make_body("shipments", random.Random(8)), headers={name: "key-b"})
    assert ok.status_code == 201


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_reference_client_sends_a_fresh_key_per_write(tmp_path, seed):
    task = light_task(tmp_path, seed=seed, idempotency_key="on")
    spec = read_json(task / "engine.json")
    name = spec["idempotency"]["header"].lower()
    run, log = run_with_log(tmp_path, task, generate_client_source(spec))
    keys = [e["headers"][name] for e in log if e["method"] != "GET"]
    assert len(keys) == 3 and len(set(keys)) == 3 and all(r["ok"] for r in run["results"])
    assert grade_flaws(tmp_path, task)["pass_rate"] == 1.0


def test_a_client_without_the_key_fails_on_writes(tmp_path):
    g = grade_flaws(tmp_path, light_task(tmp_path, idempotency_key="on"), ("no_idempotency_key",))
    assert g["pass_rate"] < 1.0 and set(g["failure_counts"]) == {"bad_request_400"} and g["passed"] == 4  # list x2, get x2 pass


def test_replays_are_neither_stray_writes_nor_invented_calls(tmp_path):
    task = light_task(tmp_path, idempotency_key="on")
    spec = read_json(task / "engine.json")
    replaying = generate_client_source(spec) + '''

_Base = Client


class Client(_Base):
    """Sends every write a second time with the same key (what a client does after a timeout)."""

    def _send(self, method, url, **kw):
        resp = super()._send(method, url, **kw)
        if method != "GET":
            req = resp.request
            again = self._http.request(method, str(req.url), content=req.content, headers=dict(req.headers))
            again.raise_for_status()
        return resp
'''
    run, log = run_with_log(tmp_path, task, replaying)
    assert all(r["ok"] for r in run["results"]) and sum(bool(e.get("replay")) for e in log) == 3
    from har2client.tierb import grader

    client = write_client(tmp_path, replaying, name="replaying.py")
    g = grader.grade_tierb(task, client)
    assert g["pass_rate"] == 1.0 and g["strict_pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0, g["results"]
    entry = {"op_id": "update", "status": 200, "replay": True, "path_params": {"id": "X"}}
    assert _stray_writes({"op": "update", "kwargs": {"id": "Y"}}, [entry]) == []
    assert _stray_writes({"op": "update", "kwargs": {"id": "Y"}}, [{**entry, "replay": False}]) != []


# ------------------------------------------------------------ moved


def test_item_paths_move_and_the_old_path_redirects(boot):
    engine, sess = boot(moved="on")
    prefix, word = sess.spec["moved"]["prefix"], "/" + sess.spec["path_word"].strip("/")
    first = next(iter(engine.store))
    new_url = f"{sess.base}{prefix}{word}/{first}"
    assert sess.req("GET", new_url).status_code == 200 and sess.req("GET", sess.path).status_code == 200  # the collection stays
    old = sess.req("GET", f"{sess.path}/{first}")
    assert old.status_code == 308 and old.headers["location"] == f"{sess.spec['base_path']}{prefix}{word}/{first}"
    assert engine.log[-1]["op_id"] == "get" and engine.log[-1]["status"] == 308
    for method in ("PATCH", "DELETE"):
        assert sess.req(method, f"{sess.path}/{first}", json={}).status_code == 308
    assert first in engine.store
    assert sess.req("GET", f"{sess.path}/{first}", follow_redirects=True).status_code == 200


def test_create_points_to_the_new_item_path(boot):
    from har2client.tierb import domains

    engine, sess = boot(moved="on")
    resp = sess.req("POST", sess.path, json=domains.make_body("shipments", random.Random(3)))
    prefix = sess.spec["moved"]["prefix"]
    assert resp.status_code == 201 and resp.headers["location"].startswith(f"{sess.spec['base_path']}{prefix}/")


def test_reference_client_uses_the_new_paths_and_the_old_path_client_fails(tmp_path):
    task = light_task(tmp_path, moved="on")
    g = grade_flaws(tmp_path, task)
    assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0
    bad = grade_flaws(tmp_path, task, ("no_moved_path",))
    assert bad["pass_rate"] < 1.0 and set(bad["failure_counts"]) == {"moved_308"} and bad["invented_endpoint_calls"] == 0


# ------------------------------------------------------------ flaws, combinations


def test_every_new_switch_has_flaws_the_heal_code_can_use():
    assert {f"no_drift_{k}" for k in DRIFT_KINDS} | {"no_rate_limit_retry", "hammer_rate_limit", "no_job_poll", "no_refresh", "no_idempotency_key", "no_moved_path"} <= set(FLAWS)
    spec = spec_for()  # a flaw whose switch is off changes nothing
    base = generate_client_source(spec)
    assert all(generate_client_source(spec, (f,)) == base for f in FLAWS if f.startswith(("no_drift", "no_rate", "hammer", "no_job", "no_idem", "no_moved")))
    assert generate_client_source(spec_for(auth="bearer_login", refresh_rotation="on"), ("no_refresh",)) != generate_client_source(spec_for(auth="bearer_login", refresh_rotation="on"))


ATOMS = [("drift", k) for k in DRIFT_KINDS] + [(p, "on") for p in PROTOCOLS]


def combo_config(atoms, rng):
    """A valid config with these switches on and the other Tier B factors drawn at random."""
    over = {name: rng.choice(levels) for name, levels in cfg.FACTORS.items()}
    over["drift"] = [value for name, value in atoms if name == "drift"]
    over.update({name: "on" for name, _ in atoms if name != "drift"})
    if "refresh_rotation" in over:
        over.update(auth="bearer_login", token_expiry="off")
    elif over["auth"] == "apikey_header":
        over["token_expiry"] = "off"
    return {**config(), **{k: v for k, v in over.items() if k != "drift" or v}}


def _combos():
    rng = random.Random("tierb-protocol-combos")
    out = []
    for i in range(20):
        atoms = rng.sample(ATOMS, rng.choice([2, 3, 4]))
        out.append(pytest.param(atoms, combo_config(atoms, rng), rng.choice(list(DOMAINS)), id=f"combo{i}"))
    return out


@pytest.mark.parametrize("atom", ATOMS, ids=[f"{n}:{v}" for n, v in ATOMS])
@pytest.mark.parametrize("domain", ["tickets", "bookings"])
def test_reference_client_scores_one_on_every_single_switch(tmp_path, atom, domain):
    name, value = atom
    over = {"drift": [value]} if name == "drift" else {name: "on", **({"auth": "bearer_login"} if name == "refresh_rotation" else {})}
    task = light_task(tmp_path, domain=domain, seed=6, **over)
    assert is_valid(read_json(task / "task.json")["config"])
    g = grade_flaws(tmp_path, task)
    assert g["pass_rate"] == 1.0 and g["strict_pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0, g["results"]


@pytest.mark.parametrize("atoms,conf,domain", _combos())
def test_reference_client_scores_one_on_combinations_of_two_to_four_switches(tmp_path, atoms, conf, domain):
    assert is_valid(conf), conf
    task = light_task(tmp_path, domain=domain, seed=17, **conf)
    g = grade_flaws(tmp_path, task)
    assert g["pass_rate"] == 1.0 and g["strict_pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0, (atoms, g["results"])


# ------------------------------------------------------------ whole task build (recording included)


@pytest.mark.parametrize("name", PROTOCOLS)
def test_built_task_records_the_protocol_and_the_reference_client_scores_one(tmp_path, name):
    from har2client.tierb.build import build_tierb_task
    from har2client.tierb.grader import grade_tierb

    over = {name: "on", **({"auth": "bearer_login"} if name == "refresh_rotation" else {})}
    task = build_tierb_task({"index": 0, "domain": "tickets", "config": config(**over), "seed": 5}, tmp_path, negatives=False)
    har = (task / "session.har").read_text()
    spec = read_json(task / "engine.json")
    marker = {
        "rate_limit": lambda: "Retry-After", "async_create": lambda: spec["async_create"]["job_word"], "refresh_rotation": lambda: spec["refresh"]["path"],
        "idempotency_key": lambda: spec["idempotency"]["header"], "moved": lambda: spec["moved"]["prefix"],
    }[name]()
    assert marker.lower() in har.lower()
    g = grade_tierb(task, task / "ref_client.py")
    assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 0


def test_a_task_with_every_new_switch_builds_and_passes_its_own_validity_gate(tmp_path):
    from har2client.tierb.build import build_tierb_task

    over = dict(auth="bearer_login", pagination="offset", etag_update="on", envelope="on", drift=list(DRIFT_KINDS), **{p: "on" for p in PROTOCOLS})
    task = build_tierb_task({"index": 1, "domain": "samples", "config": config(**over), "seed": 9}, tmp_path)  # negatives on
    assert read_json(task / "task.json")["config"]["drift"] == list(DRIFT_KINDS)
