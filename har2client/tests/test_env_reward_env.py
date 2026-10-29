"""Reward rules, the grading pool, the environment contract and the TRL function."""

import json

import pytest

from har2client.env import splits, tasks
from har2client.env.env import TrafficToClientEnv
from har2client.env.reward import GradePool, RewardConfig, code_problem, compute_reward, compute_rewards
from har2client.env.trl import completion_text, make_reward_fn, reward_fn
from har2client.tierb.refclient import generate_client_source
from test_tierb_support import read_json

ALL_ON = {"auth": "cookie_csrf", "pagination": "link_header", "token_expiry": "on", "etag_update": "on", "hidden_header": "on", "envelope": "on"}
EMPTY = "class Client:\n    def __init__(self, base_url, username, password):\n        pass\n"
EXTRA_CALL = """
_init = Client.__init__
def _patched(self, *a, **k):
    _init(self, *a, **k)
    self._http.get(self._base + "/zzz_not_listed")
Client.__init__ = _patched
"""


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("rl_cache2")


@pytest.fixture(scope="module")
def hard(cache):
    return tasks.ensure_task(tasks.make_spec("shipments", ALL_ON, 31, "browser_har", noise=0.0), cache)


@pytest.fixture(scope="module")
def easy(cache):
    return tasks.ensure_task(tasks.make_spec("tickets", {**ALL_ON, "auth": "apikey_header", "pagination": "none", "token_expiry": "off", "etag_update": "off", "hidden_header": "off", "envelope": "off"}, 8, "mobile_har"), cache)


def ref(task_dir):
    return (task_dir / "ref_client.py").read_text()


# ---------------------------------------------------------------- reward rules


def test_code_problem_reports_syntax_errors_and_a_missing_client():
    assert code_problem(EMPTY) is None
    assert "does not parse" in code_problem("def x(:\n")
    assert code_problem("class Other:\n    pass\n") == "no class Client"
    assert code_problem("def f():\n    class Client:\n        pass\n") == "no class Client"  # must be top level


def test_invented_endpoint_call_costs_a_tenth(easy):
    result = compute_reward(easy, ref(easy) + EXTRA_CALL)
    assert result.info["invented_endpoint_calls"] >= 1 and result.reward == pytest.approx(0.9)


def test_hardcoded_host_halves_the_reward(easy):
    host = read_json(easy / "engine.json")["host"]
    result = compute_reward(easy, ref(easy) + f"\nHOST = {host!r}\n")
    assert result.info["flags"] == ["hardcoded_host"] and result.reward == 0.5


def test_gates_can_be_turned_off(easy):
    host = read_json(easy / "engine.json")["host"]
    result = compute_reward(easy, ref(easy) + f"\nHOST = {host!r}\n", RewardConfig(gates=False))
    assert result.reward == 1.0 and result.info["flags"] == ["hardcoded_host"]


def test_reliability_k_grades_on_fresh_instances_and_aggregates(hard):
    flawed = generate_client_source(read_json(hard / "engine.json"), ("ignore_pagination",))
    one = compute_reward(hard, flawed)
    three = compute_reward(hard, flawed, RewardConfig(reliability_k=3))
    worst = compute_reward(hard, flawed, RewardConfig(reliability_k=3, reliability_agg="min"))
    assert three.info["reliability_k"] == 3 and 0 < three.reward < 1
    assert worst.reward <= three.reward and worst.info["min_pass_rate_k"] == worst.reward
    assert one.info["min_pass_rate_k"] == one.info["pass_rate"]
    assert compute_reward(hard, ref(hard), RewardConfig(reliability_k=3)).reward == 1.0


def test_reliability_variants_have_new_seeds_credentials_and_tests_but_the_same_task(hard):
    dirs = tasks.grading_variants(hard, 3)
    assert dirs[0] == hard and len(dirs) == 3
    base = read_json(hard / "task.json")
    obs = tasks.observation_path(hard).read_text()
    seen_seeds = {base["seeds"]["test"]}
    for d in dirs[1:]:
        t = read_json(d / "task.json")
        assert t["seeds"]["test"] not in seen_seeds and t["credentials"]["recording"] == base["credentials"]["recording"]
        assert t["credentials"]["test"] != base["credentials"]["test"]
        seen_seeds.add(t["seeds"]["test"])
        assert t["credentials"]["test"]["password"] not in obs
        assert read_json(d / "engine.json") == read_json(hard / "engine.json")
        assert read_json(d / "tests.json") != read_json(hard / "tests.json")
    assert tasks.grading_variants(hard, 3) == dirs  # same variants again


def test_milestones_shape_the_reward_without_reaching_one(hard):
    no_retry = generate_client_source(read_json(hard / "engine.json"), ("no_relogin",))
    plain = compute_reward(hard, no_retry)
    shaped = compute_reward(hard, no_retry, RewardConfig(milestones=True))
    assert shaped.reward > plain.reward and shaped.reward < 1
    reached = shaped.info["milestones"]
    assert reached["logged_in"] and reached["sent_hidden_header"] and not reached["relogin_after_401"]
    full = compute_reward(hard, ref(hard), RewardConfig(milestones=True))
    assert full.reward == 1.0 and all(full.info["milestones"].values())
    assert compute_reward(hard, EMPTY, RewardConfig(milestones=True)).reward == 0.0


def test_milestones_of_a_task_without_switches_are_empty_and_add_nothing(cache):
    base = tasks.ensure_task(tasks.make_spec("tickets", splits.configs_for("train", 0)[0], 3, "browser_har"), cache)
    result = compute_reward(base, EMPTY, RewardConfig(milestones=True))
    assert result.reward == 0.0 and result.info["milestones"] == {}


def test_pool_and_in_process_grading_agree_and_the_median_time_is_reported(hard):
    flawed = generate_client_source(read_json(hard / "engine.json"), ("no_csrf",))
    pooled, serial = GradePool(2), GradePool(0)
    try:
        a = compute_reward(hard, flawed, pool=pooled)
        b = compute_reward(hard, flawed, pool=serial)
    finally:
        pooled.close()
    assert a.reward == b.reward and a.info["failure_counts"] == b.info["failure_counts"]
    assert pooled.median_grade_time() and 0 < serial.median_grade_time() < 10


def test_batch_rewards_keep_their_order(hard, easy):
    results = compute_rewards([(hard, EMPTY), (easy, ref(easy)), (hard, "x = (")], pool=GradePool(0))
    assert [r.reward for r in results] == [0.0, 1.0, 0.0] and "code_problem" in results[2].info


def test_a_grader_crash_is_a_zero_not_an_exception(hard, monkeypatch):
    import har2client.grade as grading

    monkeypatch.setattr(grading, "grade", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    result = compute_reward(hard, EMPTY, pool=GradePool(0))
    assert result.reward == 0.0 and "boom" in result.info["grader_error"]


# ---------------------------------------------------------------- environment


def make_env(cache, **kw):
    return TrafficToClientEnv(cache_dir=cache, pool=GradePool(0), **{"source": "auto", "curriculum": "uniform", **kw})


def test_reset_gives_the_observation_dict(cache):
    env = make_env(cache, seed=1)
    obs = env.reset()
    assert {"task_id", "goal", "observation", "observation_format", "probe_budget"} <= set(obs)
    assert obs["probe_budget"] == 0 and obs["observation_format"] == "har" and "class Client" in obs["goal"]
    assert json.loads(obs["observation"])["log"]["entries"]


def test_submit_returns_reward_done_and_info_and_ends_the_episode(cache):
    env = make_env(cache, seed=2)
    env.reset()
    out = env.step({"type": "submit", "code": env.reference_code()})
    assert out["reward"] == 1.0 and out["done"] is True
    assert {"pass_rate", "failure_counts", "min_pass_rate_k", "flags", "milestones"} <= set(out["info"])
    with pytest.raises(RuntimeError):
        env.step({"type": "submit", "code": EMPTY})
    env.reset()
    assert env.step({"type": "submit", "code": EMPTY})["reward"] == 0.0


def test_step_before_reset_and_bad_actions_fail_clearly(cache):
    env = make_env(cache)
    with pytest.raises(RuntimeError):
        env.step({"type": "submit", "code": EMPTY})
    env.reset()
    with pytest.raises(ValueError):
        env.step({"type": "dance"})
    out = env.step({"type": "probe", "request": {}})  # probe_budget=0: no practice server
    assert out["done"] is False and out["reward"] == 0.0 and out["response"]["error"] == "no_practice_server"


def test_constructor_rejects_unsupported_options(cache):
    with pytest.raises(ValueError):
        TrafficToClientEnv(probe_budget=-1)
    with pytest.raises(ValueError):
        TrafficToClientEnv(tier="A")
    with pytest.raises(ValueError):
        TrafficToClientEnv(source="otel_spans")
    with pytest.raises(ValueError):
        TrafficToClientEnv(split="test_source")


def test_single_source_and_split_choice_are_respected(cache):
    env = make_env(cache, source="mobile_har", split="test_domain", curriculum="L1", seed=3)
    for _ in range(2):
        env.reset()
        info = env.step({"type": "submit", "code": EMPTY})["info"]
        assert info["source"] == "mobile_har" and info["level"] == 1
        assert read_json(tasks.ensure_task(env._spec, cache) / "engine.json")["domain"] == splits.HELD_OUT_DOMAIN


def test_adaptive_curriculum_moves_with_the_rewards(cache):
    env = make_env(cache, curriculum="adaptive", seed=4)
    env.curriculum.window = 2
    levels = []
    for _ in range(4):
        env.reset()
        out = env.step({"type": "submit", "code": env.reference_code()})
        levels.append(out["info"]["level"])
    assert levels[:2] == [0, 0] and levels[2:] == [1, 1]


def test_explicit_spec_episodes_do_not_feed_the_adaptive_window(cache):
    env = make_env(cache, curriculum="adaptive", seed=6)
    env.curriculum.window = 1
    spec = tasks.make_spec("tickets", {**ALL_ON, "auth": "apikey_header", "pagination": "none", "token_expiry": "off", "etag_update": "off", "hidden_header": "off", "envelope": "off"}, 9, "browser_har", level=3)
    env.reset(spec)
    assert env.step({"type": "submit", "code": env.reference_code()})["reward"] == 1.0
    assert env.curriculum.level == 0 and not env.curriculum._rewards  # an eval spec at level 3 must not promote the sampler
    env.reset()  # a sampled episode counts again
    env.step({"type": "submit", "code": env.reference_code()})
    assert env.curriculum.level == 1


def test_submit_without_a_code_key_is_a_code_problem_not_a_crash(cache):
    env = make_env(cache, seed=7)
    env.reset()
    out = env.step({"type": "submit"})
    assert out["reward"] == 0.0 and out["done"] is True and "code" in out["info"]["code_problem"]
    with pytest.raises(RuntimeError):
        env.step({"type": "submit", "code": EMPTY})


def test_same_seed_gives_the_same_episodes_and_cached_tasks_are_reused(cache):
    a, b = make_env(cache, seed=5), make_env(cache, seed=5)
    oa, ob = a.reset(), b.reset()
    assert oa == ob
    path = tasks.ensure_task(a._spec, cache)
    stamp = (path / "task.json").stat().st_mtime_ns
    assert tasks.ensure_task(a._spec, cache) == path and (path / "task.json").stat().st_mtime_ns == stamp


def test_spec_key_depends_on_every_spec_field():
    base = tasks.make_spec("tickets", ALL_ON, 1, "browser_har")
    keys = {tasks.spec_key(base)}
    for change in ({"seed": 2}, {"source": "mobile_har"}, {"noise": 0.1}, {"compact": True}, {"build_seed": 1}, {"domain": "invoices"}):
        keys.add(tasks.spec_key({**base, **change}))
    assert len(keys) == 7


# ---------------------------------------------------------------- TRL


def test_completion_text_handles_both_formats():
    assert completion_text("hi") == "hi"
    assert completion_text([{"role": "assistant", "content": "a"}]) == "a"
    assert completion_text([{"role": "user", "content": "u"}, {"role": "assistant", "content": "b"}]) == "b"
    assert completion_text([]) == "" and completion_text(None) == ""


def test_reward_fn_scores_standard_and_conversational_completions(cache, easy):
    spec = read_json(easy / "task.json")
    row = tasks.make_spec("tickets", {**ALL_ON, "auth": "apikey_header", "pagination": "none", "token_expiry": "off", "etag_update": "off", "hidden_header": "off", "envelope": "off"}, 8, "mobile_har")
    fn = make_reward_fn(pool=GradePool(0), cache_dir=cache)
    good = "Here:\n```python\n" + ref(easy) + "```\n"
    completions = [good, [{"role": "assistant", "content": good}], "no code here", f"```python\n{EMPTY}```"]
    prompts = ["p"] * 4
    out = fn(prompts, completions, task_spec=[row] * 4, completion_ids=[[1]] * 4, trainer_state=None)
    assert out == [1.0, 1.0, 0.0, 0.0] and spec["task_id"]
    assert fn.__name__ == "har2client_reward"
    assert fn(prompts[:1], completions[:1], task_spec=[json.dumps(row)]) == [1.0]  # specs as JSON strings


def test_reward_fn_needs_one_spec_per_completion():
    with pytest.raises(ValueError):
        reward_fn(["p"], ["x"], task_spec=[])
    with pytest.raises(ValueError):
        reward_fn(["p"], ["x"])


# ---------------------------------------------------------------- review fixes: static check and milestones


def test_code_problem_survives_non_text_and_pathological_code():
    assert "not text" in code_problem(None)
    assert "not text" in code_problem(b"class Client: pass")
    assert "does not parse" in code_problem("x = " + "+".join(["1"] * 200000))  # RecursionError in ast.parse


def _spec(**switches):
    config = {"auth": "bearer_login", "pagination": "offset", "token_expiry": "on", "etag_update": "on", "hidden_header": "off", "envelope": "off", **switches}
    return {"config": config, "hidden_header": None, "page": {"param": "offset"}}


def _entry(op, status=200, query=None, headers=None, error=None):
    return {"op_id": op, "status": status, "query": query or {}, "headers": headers or {}, "method": "GET", "response": {"error": error} if error else {}}


def test_second_page_milestone_needs_a_non_first_page_request():
    from har2client.env import milestones as ms

    def reached(query):
        return ms.reached(_spec(), [], [_entry("list", query=query)])["fetched_second_page"]

    assert not reached({}) and not reached({"offset": ["0"]})  # a first page, even with the parameter set
    assert reached({"offset": ["3"]})
    cursor = _spec(pagination="cursor")
    cursor["page"] = {"param": "cursor"}
    assert ms.reached(cursor, [], [_entry("list", query={"cursor": ["abc"]})])["fetched_second_page"]
    link = _spec(pagination="link_header")
    link["page"] = {"param": "page"}
    assert not ms.reached(link, [], [_entry("list", query={"page": ["1"]})])["fetched_second_page"]
    assert ms.reached(link, [], [_entry("list", query={"page": ["2"]})])["fetched_second_page"]


def test_if_match_milestone_needs_an_accepted_update():
    from har2client.env import milestones as ms

    rejected = [_entry("update", status=412, headers={"if-match": "*"})]
    accepted = [_entry("update", status=200, headers={"if-match": '"x"'})]
    assert not ms.reached(_spec(), [], rejected)["sent_if_match"]
    assert ms.reached(_spec(), [], accepted)["sent_if_match"]


def test_relogin_milestone_needs_an_expired_token_not_a_missing_login():
    from har2client.env import milestones as ms

    forgot_login = [_entry("list", 401, error="unauthorized"), _entry("login"), _entry("list")]
    expired = [_entry("login"), _entry("list", 401, error="token_expired"), _entry("login"), _entry("list")]
    assert not ms.reached(_spec(), [], forgot_login)["relogin_after_401"]
    assert ms.reached(_spec(), [], expired)["relogin_after_401"]
