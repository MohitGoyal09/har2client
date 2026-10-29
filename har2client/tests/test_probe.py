"""Practice instance, probe proxy, labels and the env probe path (no network beyond loopback)."""

import json

import pytest

from har2client.env import probe, tasks
from har2client.env.env import TrafficToClientEnv
from har2client.env.reward import GradePool
from har2client.tierb.domains import initial_items
from test_tierb_support import read_json

ALL_ON = {"auth": "bearer_login", "pagination": "cursor", "token_expiry": "off", "etag_update": "on", "hidden_header": "on", "envelope": "off"}
KEY_ONLY = {"auth": "apikey_header", "pagination": "none", "token_expiry": "off", "etag_update": "off", "hidden_header": "on", "envelope": "off"}


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("probe_cache")


@pytest.fixture(scope="module")
def hard(cache):
    return tasks.ensure_task(tasks.make_spec("shipments", ALL_ON, 41, "browser_har", noise=0.0), cache)


@pytest.fixture(scope="module")
def keyed(cache):
    return tasks.ensure_task(tasks.make_spec("tickets", KEY_ONLY, 17, "mobile_har"), cache)


def spec_of(task_dir):
    return read_json(task_dir / "engine.json")


def login_body(spec, creds):
    return {spec["login"]["user_field"]: creds["username"], spec["login"]["pass_field"]: creds["password"]}


def login_request(spec, creds):
    headers = hidden_headers(spec)
    if spec["login"]["format"] == "form":
        headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    return {"method": "POST", "path": spec["login"]["path"], "body": login_body(spec, creds), "headers": headers}


def hidden_headers(spec):
    h = spec["hidden_header"]
    return {h["name"]: h["value"]} if h else {}


# ---------------------------------------------------------------- independence from the grading instance


@pytest.mark.parametrize("fixture", ["hard", "keyed"])
def test_practice_never_shares_credentials_ids_or_data_with_grading(fixture, request):
    task_dir = request.getfixturevalue(fixture)
    task, spec = read_json(task_dir / "task.json"), spec_of(task_dir)
    plan = probe.plan_practice(task_dir)
    rec, test = task["credentials"]["recording"], task["credentials"]["test"]
    assert plan.credentials["password"] not in (rec["password"], test["password"])
    assert plan.credentials["username"] not in (rec["username"], test["username"])
    assert plan.seed not in (task["seeds"]["recording"], task["seeds"]["test"])
    mine = {i["id"] for i in initial_items(spec["domain"], plan.seed)}
    for seed in (task["seeds"]["recording"], task["seeds"]["test"]):
        assert not mine & {i["id"] for i in initial_items(spec["domain"], seed)}
    assert not any(i in (task_dir / "session.har").read_text() for i in mine)


def test_practice_serves_its_own_data_and_credentials(hard):
    spec, task = spec_of(hard), read_json(hard / "task.json")
    with probe.PracticeInstance(hard, 5) as practice:
        creds = practice.credentials
        resp = practice.request(login_request(spec, creds))
        assert resp["status"] == 200
        # the grading credentials do not work on the practice server
        bad = practice.request(login_request(spec, task["credentials"]["test"]))
        assert bad["status"] == 401
        served = {i["id"] for i in practice.engine.store.values()}
    assert not served & {i["id"] for i in initial_items(spec["domain"], task["seeds"]["test"])}


def test_plan_is_deterministic_and_url_is_virtual(hard):
    a, b = probe.plan_practice(hard), probe.plan_practice(hard)
    assert a == b and a.public_url.startswith("https://practice.") and "127.0.0.1" not in a.public_url


def test_response_origins_are_rewritten_to_the_virtual_url(keyed):
    spec = spec_of(keyed)
    with probe.PracticeInstance(keyed, 3) as practice:
        resp = practice.request({"method": "GET", "path": f"/{spec['path_word'].strip('/')}", "headers": {**hidden_headers(spec), spec["api_key_header"]: practice.credentials["password"]}})
    assert resp["status"] == 200 and "127.0.0.1" not in json.dumps(resp)


# ---------------------------------------------------------------- budget


def test_budget_is_enforced_and_refusals_are_not_counted(keyed):
    spec = spec_of(keyed)
    req = {"method": "GET", "path": f"/{spec['path_word'].strip('/')}"}
    with probe.PracticeInstance(keyed, 2) as practice:
        sent = [practice.request(req) for _ in range(2)]
        refused = practice.request(req)
        assert all("status" in r for r in sent)
        assert refused["error"] == "probe_budget_exhausted"
        assert practice.probes_used == 2 and practice.probes_refused == 1 and practice.probes_left == 0
        assert len(practice.engine.log) == 2  # the refused probe never reached the server


def test_invalid_requests_are_not_sent_and_not_counted(keyed):
    with probe.PracticeInstance(keyed, 3) as practice:
        for bad in ({"method": "TRACE", "path": "/x"}, {"method": "GET", "path": "x"}, {"method": "GET", "path": "https://evil.example/x"},
                    {"method": "GET", "path": "/a/../b"}, "nope", {"method": "GET", "path": "/x", "headers": ["a"]}):
            assert practice.request(bad)["error"] == "invalid_request"
        assert practice.probes_used == 0 and practice.engine.log == []


@pytest.mark.parametrize("bad", [
    {"method": "GET", "path": "/items\nX"},
    {"method": "GET", "path": "/x", "headers": {"\u00e9": "1"}},
    {"method": "GET", "path": "/x", "headers": {"X-A": "\u2603"}},
    {"method": "POST", "path": "/x", "body": {"a": object()}},
])
def test_malformed_model_input_is_an_error_result_not_an_exception(keyed, bad):
    """A tool call with a control character, a non-ASCII header or a body that is not JSON must not kill the episode."""
    with probe.PracticeInstance(keyed, 3) as practice:
        response = practice.request(bad)
        assert response["error"] == "invalid_request"  # it never reaches the wire, so it is not sent and not counted
        assert practice.probes_used == 0 and practice.engine.log == []
        ok = practice.request({"method": "GET", "path": f"/{spec_of(keyed)['path_word'].strip('/')}"})
        assert "status" in ok  # the instance is still usable


def test_body_is_cut_to_4000_chars(keyed):
    spec = spec_of(keyed)
    with probe.PracticeInstance(keyed, 1) as practice:
        resp = practice.request({"method": "GET", "path": f"/{spec['path_word'].strip('/')}", "headers": {spec["api_key_header"]: practice.credentials["password"], **hidden_headers(spec)}})
    assert len(resp["body"]) <= probe.BODY_LIMIT and resp.get("body_truncated") is True


# ---------------------------------------------------------------- labels


def recorded_get_list(task_dir, spec):
    return next(r for r in probe.recorded_requests(task_dir, spec) if r.method == "GET" and r.shape == "/" + spec["path_word"].strip("/") and not r.query_keys)


def test_request_equal_to_a_recorded_one_minus_one_header_is_header_ablation(hard):
    spec = spec_of(hard)
    rec = recorded_get_list(hard, spec)
    hidden = spec["hidden_header"]["name"].lower()
    assert hidden in rec.headers
    labeler = probe.Labeler(spec, probe.recorded_requests(hard, spec))
    path = "/" + spec["path_word"].strip("/")
    without = labeler.label("GET", path, {}, set(rec.headers) - {hidden})
    assert without == {"label": "header_ablation", "ablated_header": hidden}
    # the full recorded request is not an ablation
    assert labeler.label("GET", path, {}, set(rec.headers))["label"] != "header_ablation"
    # two headers missing is not "minus one"
    assert labeler.label("GET", path, {}, set())["label"] != "header_ablation"
    # an item path with another id has the same shape
    assert labeler.ablated_header("GET", f"{path}/SHP-1", {}, set()) is None


def test_other_labels(hard):
    spec = spec_of(hard)
    labeler = probe.Labeler(spec, probe.recorded_requests(hard, spec))
    path = "/" + spec["path_word"].strip("/")
    full = {"authorization", spec["hidden_header"]["name"].lower()}
    assert labeler.label("POST", spec["login"]["path"], {}, {spec["hidden_header"]["name"]})["label"] == "auth_test"
    assert labeler.label("PUT", path + "/SHP-1", {}, {spec["hidden_header"]["name"]})["label"] == "auth_test"  # no credential sent
    assert labeler.label("GET", path, {spec["page"]["param"]: ["x"]}, full)["label"] == "paging_test"
    assert labeler.label("PATCH", path + "/SHP-1", {}, full | {"If-Match"})["label"] == "etag_test"
    assert labeler.label("GET", path, {"status": ["pending"]}, full)["label"] == "other"
    assert labeler.label("GET", path + "/SHP-1", {}, full)["label"] == "other"


def test_labels_and_counts_are_logged_per_probe(hard):
    spec = spec_of(hard)
    rec = recorded_get_list(hard, spec)
    hidden = spec["hidden_header"]
    names = {h: "x" for h in rec.headers - {hidden["name"].lower()}}
    with probe.PracticeInstance(hard, 4) as practice:
        practice.request({"method": "GET", "path": "/" + spec["path_word"].strip("/"), "headers": names})
        practice.request(login_request(spec, practice.credentials))
        counts = practice.label_counts()
        assert counts["header_ablation"] == 1 and counts["auth_test"] == 1 and sum(counts.values()) == 2
        assert practice.ablated_hidden_header() is True
        assert [e["label"] for e in practice.log] == ["header_ablation", "auth_test"]
        assert practice.log[0]["response"]["status"] == 400  # the missing hidden header is what the server complains about


# ---------------------------------------------------------------- env, end to end


def make_env(cache, budget, **kw):
    return TrafficToClientEnv(cache_dir=cache, pool=GradePool(0), source="browser_har", curriculum="uniform", probe_budget=budget, **kw)


def test_env_probe_path_end_to_end_with_a_scripted_agent(cache):
    env = make_env(cache, 3)
    obs = env.reset()
    practice = obs["practice"]
    assert obs["probe_budget"] == 3 and practice["budget"] == 3
    assert practice["base_url"] in obs["goal"] and practice["password"] in obs["goal"]
    spec = spec_of(tasks.ensure_task(env._spec, cache))
    task = read_json(tasks.ensure_task(env._spec, cache) / "task.json")
    assert practice["password"] not in (task["credentials"]["recording"]["password"], task["credentials"]["test"]["password"])
    # scripted agent: one probe (a request without credentials), then the reference client
    out = env.step({"type": "probe", "request": {"method": "GET", "path": "/" + spec["path_word"].strip("/"), "headers": hidden_headers(spec)}})
    assert out["done"] is False and out["reward"] == pytest.approx(-0.01) and out["info"]["probes_left"] == 2
    assert out["response"]["status"] in (400, 401)
    done = env.step({"type": "submit", "code": env.reference_code()})
    assert done["done"] is True and done["reward"] == 1.0
    assert done["info"]["probes_used"] == 1 and sum(done["info"]["probe_labels"].values()) == 1
    with pytest.raises(RuntimeError):
        env.step({"type": "probe", "request": {"method": "GET", "path": "/"}})


def test_env_refuses_probes_past_the_budget_and_stops_the_server(cache):
    env = make_env(cache, 1)
    env.reset()
    req = {"type": "probe", "request": {"method": "GET", "path": "/x"}}
    assert env.step(req)["reward"] == pytest.approx(-0.01)
    refused = env.step(req)
    assert refused["reward"] == 0.0 and refused["response"]["error"] == "probe_budget_exhausted" and refused["info"]["probes_used"] == 1
    env.step({"type": "submit", "code": "class Client:\n    pass\n"})
    assert env._practice is None


def test_env_without_probes_is_unchanged(cache):
    obs = make_env(cache, 0).reset()
    assert "practice" not in obs and "Practice server" not in obs["goal"]


def test_env_stops_the_practice_server_when_grading_raises(cache, monkeypatch):
    env = make_env(cache, 2)
    env.reset()
    practice = env._practice
    assert practice.engine is not None

    def boom(*_a, **_k):
        raise RuntimeError("grader pool died")

    monkeypatch.setattr("har2client.env.env.compute_reward", boom)
    with pytest.raises(RuntimeError, match="grader pool died"):
        env.step({"type": "submit", "code": env.reference_code()})
    assert env._practice is None and practice.engine is None  # the loopback server is not left running


# ---------------------------------------------------------------- the practice seed is disjoint from every grading variant


GRADING_K = 8  # reliability_k values up to this one must stay disjoint from the practice instance


def test_the_avoided_set_holds_the_seeds_and_credentials_of_variants_not_yet_on_disk(cache):
    task_dir = tasks.ensure_task(tasks.make_spec("tickets", KEY_ONLY, 31, "browser_har", noise=0.0), cache)
    task = read_json(task_dir / "task.json")
    avoid_seeds, avoid_creds = probe._grading_seeds_and_creds(task_dir, task)  # no `.rel<i>` dir exists yet
    for variant in tasks.grading_variants(task_dir, GRADING_K)[1:]:
        vt = read_json(variant / "task.json")
        assert vt["seeds"]["test"] in avoid_seeds and vt["credentials"]["test"] in avoid_creds


def test_practice_avoids_the_seeds_ids_and_credentials_of_the_rel_variants_even_before_they_are_written(cache):
    task_dir = tasks.ensure_task(tasks.make_spec("tickets", KEY_ONLY, 29, "browser_har", noise=0.0), cache)
    plan = probe.plan_practice(task_dir)  # no `.rel<i>` dir exists yet
    assert not list(task_dir.parent.glob(f"{task_dir.name}.rel*"))
    spec = spec_of(task_dir)
    mine = {i["id"] for i in initial_items(spec["domain"], plan.seed)}
    variants = tasks.grading_variants(task_dir, GRADING_K)  # what the grader may write later
    assert len(variants) == GRADING_K and probe.MAX_GRADING_VARIANTS >= GRADING_K
    for variant in variants + [task_dir]:
        task = read_json(variant / "task.json")
        assert plan.seed != task["seeds"]["test"] and plan.seed != task["seeds"]["recording"]
        assert not mine & {i["id"] for i in initial_items(spec["domain"], task["seeds"]["test"])}
        assert plan.credentials["password"] != task["credentials"]["test"]["password"]
        assert plan.credentials["username"] != task["credentials"]["test"]["username"]


def test_practice_plan_is_the_same_before_and_after_the_variants_exist(cache):
    task_dir = tasks.ensure_task(tasks.make_spec("tickets", KEY_ONLY, 30, "browser_har", noise=0.0), cache)
    before = probe.plan_practice(task_dir)
    tasks.grading_variants(task_dir, GRADING_K)
    assert probe.plan_practice(task_dir) == before  # the plan does not depend on what is on disk
