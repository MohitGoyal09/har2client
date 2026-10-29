"""The scientist agent loop and its prompts, with a fake OpenAI client (no network)."""

import json
import re
from types import SimpleNamespace

import httpx2
import openai
import pytest

from har2client.env import tasks
from har2client.grade import grade
from har2client.solve import agent_prompt, beam, beam_agent
from test_tierb_support import read_json

CONFIG = {"auth": "bearer_login", "pagination": "link_header", "token_expiry": "off", "etag_update": "off", "hidden_header": "on", "envelope": "off"}
CODE = "class Client:\n    def __init__(self, base_url, username, password):\n        pass\n"


@pytest.mark.parametrize('budget', [0, 5, 20])
def test_hf_protocol_keeps_submission_equal_and_omits_unsupported_effort(task_dir, tmp_path, budget):
    client = FakeClient([reply(calls=[tool_call('submit_client', {'code': CODE + '# Unicode: →\n'}, 's')])])
    log = beam_agent.solve_agent(task_dir, tmp_path / 'client.py', budget=budget, client=client,
                                 submission_tool_all_arms=True,
                                 request_options={'model': 'org/model:provider', 'reasoning_effort': None,
                                                  'max_completion_tokens': None, 'max_tokens': 4096})
    request = client.requests[0]
    tools = {t['function']['name'] for t in request['tools']}
    assert tools == ({'submit_client'} if budget == 0 else {'submit_client', 'http_request'})
    assert 'reasoning_effort' not in request and 'max_completion_tokens' not in request
    assert request['model'] == 'org/model:provider' and request['max_tokens'] == 4096
    assert log['status'] == 'ok' and '→' in (tmp_path / 'client.py').read_text(encoding='utf-8')


@pytest.fixture(scope="module")
def task_dir(tmp_path_factory):
    return tasks.ensure_task(tasks.make_spec("bookings", CONFIG, 23, "browser_har", noise=0.0), tmp_path_factory.mktemp("agent_cache"))


def tool_call(name, args, call_id):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args if isinstance(args, str) else json.dumps(args)))


def reply(content=None, calls=(), reasoning="thinking", usage=(100, 20, 5, None)):
    message = SimpleNamespace(content=content, tool_calls=list(calls) or None, reasoning_content=reasoning)
    prompt, completion, reasoning_tokens, cached = usage
    details = SimpleNamespace(cached_tokens=cached) if cached is not None else None
    use = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        prompt_tokens_details=details,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=use)


class FakeRaw:
    def __init__(self, parsed):
        self._parsed, self.headers = parsed, httpx2.Headers({})

    def parse(self):
        return self._parsed


class FakeClient:
    """Plays back replies; `outcomes` may also be a callable(request) -> reply."""

    def __init__(self, outcomes):
        self.outcomes, self.requests = outcomes, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create)))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes(kwargs) if callable(self.outcomes) else self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeRaw(outcome)


def get_list(call_id="c1"):
    return tool_call("http_request", {"method": "GET", "path": "/nothing"}, call_id)


def run(task_dir, tmp_path, client, budget):
    return beam_agent.solve_agent(task_dir, tmp_path / "out" / "client.py", budget=budget, client=client, sleep=lambda _: None)


# ---------------------------------------------------------------- prompts


def numbers_only_diff(a: str, b: str, budget: int) -> bool:
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb):
        return False
    diffs = [(x, y) for x, y in zip(ta, tb) if x != y]
    return bool(diffs) and all(x == "0" and y == str(budget) for x, y in diffs)


@pytest.mark.parametrize("budget", [5, 20])
def test_placebo_and_probe_prompts_differ_only_in_the_budget_number(task_dir, budget):
    goal = (task_dir / "goal.md").read_text()
    plan = __import__("har2client.env.probe", fromlist=["x"]).plan_practice(task_dir)
    kw = dict(base_url=plan.public_url, username=plan.credentials["username"], password=plan.credentials["password"])
    placebo = agent_prompt.render_prompts(goal, "SESSION", "session.har", budget=0, **kw)
    probing = agent_prompt.render_prompts(goal, "SESSION", "session.har", budget=budget, **kw)
    assert placebo[0] == probing[0]  # system prompt
    assert placebo[1] != probing[1] and numbers_only_diff(placebo[1], probing[1], budget)


def test_requests_of_the_arms_differ_in_tools_and_budget_number_only(task_dir, tmp_path):
    placebo, p5 = FakeClient([reply("```python\n" + CODE + "```")]), FakeClient([reply(calls=[tool_call("submit_client", {"code": CODE}, "s")])])
    run(task_dir, tmp_path / "a", placebo, 0)
    run(task_dir, tmp_path / "b", p5, 5)
    a, b = placebo.requests[0], p5.requests[0]
    assert "tools" not in a and [t["function"]["name"] for t in b["tools"]] == ["http_request", "submit_client"]
    assert numbers_only_diff(a["messages"][1]["content"], b["messages"][1]["content"], 5)
    assert a["messages"][0] == b["messages"][0]


# ---------------------------------------------------------------- loop


def test_reasoning_content_of_every_assistant_turn_is_sent_back(task_dir, tmp_path):
    client = FakeClient([
        reply(calls=[get_list("c1")], reasoning="r1"),
        reply(calls=[get_list("c2")], reasoning="r2"),
        reply(calls=[tool_call("submit_client", {"code": CODE}, "c3")], reasoning="r3"),
    ])
    log = run(task_dir, tmp_path, client, 5)
    assert log["status"] == "ok" and len(client.requests) == 3
    for request, expected in ((client.requests[1], ["r1"]), (client.requests[2], ["r1", "r2"])):
        sent = [m["reasoning_content"] for m in request["messages"] if m["role"] == "assistant"]
        assert sent == expected
    # each assistant turn is followed by a tool message for every call it made
    roles = [m["role"] for m in client.requests[2]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert client.requests[0]["messages"] == client.requests[0]["messages"][:2]  # earlier requests are not mutated later


def test_submit_ends_the_episode_and_writes_the_client(task_dir, tmp_path):
    client = FakeClient([reply(calls=[tool_call("submit_client", {"code": CODE}, "s"), get_list("late")])])
    log = run(task_dir, tmp_path, client, 5)
    assert (tmp_path / "out" / "client.py").read_text() == CODE
    assert len(client.requests) == 1 and log["status"] == "ok" and log["submitted_via"] == "tool"
    assert log["probes_used"] == 0  # the call after submit was not run


def test_probes_beyond_budget_are_refused_in_the_loop(task_dir, tmp_path):
    client = FakeClient([
        reply(calls=[get_list("a"), get_list("b"), get_list("c")]),
        reply(calls=[tool_call("submit_client", {"code": CODE}, "s")]),
    ])
    log = run(task_dir, tmp_path, client, 2)
    results = [json.loads(m["content"]) for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert [("status" in r) for r in results] == [True, True, False] and results[2]["error"] == "probe_budget_exhausted"
    assert log["probes_used"] == 2 and log["probes_refused"] == 1
    lines = (tmp_path / "out" / "probes.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[2])["refused"] == "budget"


def test_max_turns_is_budget_plus_three_and_missing_submit_is_reported(task_dir, tmp_path):
    client = FakeClient(lambda request: reply("still thinking"))
    log = run(task_dir, tmp_path, client, 2)
    assert beam_agent.max_turns(2) == 5 and len(client.requests) == 5
    assert log["status"] == "no_submit" and not (tmp_path / "out" / "client.py").exists()
    assert client.requests[1]["messages"][-1] == {"role": "user", "content": beam_agent.NUDGE}


def test_placebo_is_a_single_turn_that_reads_the_code_block(task_dir, tmp_path):
    client = FakeClient([reply(f"ok\n```python\n{CODE}```")])
    log = run(task_dir, tmp_path, client, 0)
    assert log["status"] == "ok" and log["submitted_via"] == "text" and len(client.requests) == 1
    assert log["probes_used"] == 0 and log["max_turns"] == 1
    none = run(task_dir, tmp_path / "n", FakeClient([reply("no code")]), 0)
    assert none["status"] == "no_code"


def test_a_code_block_without_a_tool_call_is_accepted_in_a_probe_arm(task_dir, tmp_path):
    log = run(task_dir, tmp_path, FakeClient([reply(f"```python\n{CODE}```")]), 5)
    assert log["status"] == "ok" and log["submitted_via"] == "text"


def test_bad_tool_calls_get_error_results_and_do_not_use_budget(task_dir, tmp_path):
    client = FakeClient([
        reply(calls=[tool_call("http_request", "{not json", "a"), tool_call("dance", {}, "b"), tool_call("submit_client", {"code": ""}, "c")]),
        reply(calls=[tool_call("submit_client", {"code": CODE}, "s")]),
    ])
    log = run(task_dir, tmp_path, client, 1)
    errors = [json.loads(m["content"])["error"] for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert errors == ["invalid_arguments", "unknown_tool", "invalid_arguments"] and log["probes_used"] == 0 and log["status"] == "ok"


# ---------------------------------------------------------------- logging and limits


def test_usage_is_logged_per_turn_with_cached_tokens(task_dir, tmp_path):
    client = FakeClient([
        reply(calls=[get_list()], usage=(1000, 30, 10, 0)),
        reply(calls=[tool_call("submit_client", {"code": CODE}, "s")], usage=(1500, 40, 20, 1000)),
    ])
    log = run(task_dir, tmp_path, client, 5)
    assert [t["usage"]["cached_tokens"] for t in log["turns"]] == [0, 1000]
    assert log["usage"]["prompt_tokens"] == 2500 and log["usage"]["cached_tokens"] == 1000 and log["usage"]["cached_share"] == 0.4
    assert log["turns"][0]["tool_calls"][0]["name"] == "http_request" and "status" in log["turns"][0]["tool_calls"][0]["result"]
    assert json.loads((tmp_path / "out" / "solve_log.json").read_text())["probe_labels"]["auth_test"] == 1


def test_cached_tokens_may_be_missing(task_dir, tmp_path):
    log = run(task_dir, tmp_path, FakeClient([reply(calls=[tool_call("submit_client", {"code": CODE}, "s")], usage=(10, 1, 0, None))]), 5)
    assert log["usage"]["cached_tokens"] is None and log["usage"]["cached_share"] is None


def status_error(cls, status, body=None, headers=None):
    req = httpx2.Request("POST", "https://api.reflection.ai/openai/v1/chat/completions")
    return cls("error", response=httpx2.Response(status, request=req, json=body or {}, headers=headers or {}), body=body)


def test_daily_limit_stops_the_episode_and_is_logged(task_dir, tmp_path):
    err = status_error(openai.RateLimitError, 429, {"error": {"code": "daily_allowance_exhausted"}})
    client = FakeClient([reply(calls=[get_list()]), err])
    with pytest.raises(beam.DailyAllowanceExhausted):
        run(task_dir, tmp_path, client, 5)
    assert json.loads((tmp_path / "out" / "solve_log.json").read_text())["status"] == "daily_allowance_exhausted"


def test_retryable_errors_use_the_beam_retry_rules(task_dir, tmp_path):
    err = status_error(openai.RateLimitError, 429, {}, {"retry-after": "1"})
    waits = []
    client = FakeClient([err, reply(calls=[tool_call("submit_client", {"code": CODE}, "s")])])
    log = beam_agent.solve_agent(task_dir, tmp_path / "o" / "client.py", budget=5, client=client, sleep=waits.append)
    assert log["status"] == "ok" and len(waits) == 1 and waits[0] >= 1


def test_the_agent_never_reads_hidden_files(task_dir, tmp_path):
    hidden = [read_json(task_dir / "task.json")["credentials"]["test"]["password"]]
    client = FakeClient([reply(calls=[tool_call("submit_client", {"code": CODE}, "s")])])
    run(task_dir, tmp_path, client, 5)
    text = json.dumps(client.requests[0]["messages"])
    assert not any(h in text for h in hidden) and "ref_client" not in text


# ---------------------------------------------------------------- end to end with a real practice instance and the grader


def test_scripted_agent_probes_once_then_submits_the_reference_client(task_dir, tmp_path):
    ref = (task_dir / "ref_client.py").read_text()
    client = FakeClient([reply(calls=[get_list()]), reply(calls=[tool_call("submit_client", {"code": ref}, "s")])])
    log = run(task_dir, tmp_path, client, 1)
    result = grade(task_dir, tmp_path / "out" / "client.py")
    assert log["probes_used"] == 1 and result["pass_rate"] == 1.0
    # the practice credentials in the prompt are not the grading ones
    user = client.requests[0]["messages"][1]["content"]
    assert re.search(r"Practice password: `pw_", user) and read_json(task_dir / "task.json")["credentials"]["test"]["password"] not in user


def test_no_practice_server_is_left_running_when_setup_fails(task_dir, tmp_path, monkeypatch):
    started, stopped = [], []
    real_start, real_stop = beam_agent.PracticeInstance.start, beam_agent.PracticeInstance.stop

    def counting_start(self):
        started.append(self)
        return real_start(self)

    def counting_stop(self):
        stopped.append(self)
        real_stop(self)

    monkeypatch.setattr(beam_agent.PracticeInstance, "start", counting_start)
    monkeypatch.setattr(beam_agent.PracticeInstance, "stop", counting_stop)
    blocker = tmp_path / "file"
    blocker.write_text("x")  # `file/out` cannot be created
    with pytest.raises(OSError):
        beam_agent.solve_agent(task_dir, blocker / "out" / "client.py", budget=2, client=FakeClient([]), sleep=lambda _: None)
    assert started == stopped  # every server that was started was stopped


def test_a_placebo_model_that_calls_a_tool_anyway_gets_no_server_and_no_probe(task_dir, tmp_path):
    client = FakeClient([reply(calls=[get_list("a")])])
    log = run(task_dir, tmp_path, client, 0)
    assert log["status"] == "no_code" and log["probes_used"] == 0 and log["turns"][0]["tool_calls"][0]["result"]["error"] == "unknown_tool"
