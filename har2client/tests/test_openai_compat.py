"""Tests for har2client.solve.openai_compat and `run_batch.py --solver openai_compat`.

The network is a local fake OpenAI-compatible server (scripts/fake_openai_server.py) or a scripted client.
"""

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest

from har2client import build_task, ops
from har2client.refclient import write_ref_client
from har2client.solve import beam, openai_compat

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIX = Path(__file__).parent / "fixtures"
SECRET = "hf_test_DO_NOT_PRINT_123456"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rb = load_script("run_batch")
fake = load_script("fake_openai_server")

GOAL = "# Goal\nImplement `list_orders(self)`."
CODE = "import httpx\n\nclass Client:\n    def __init__(self, base_url, api_key):\n        pass\n"
GOOD_TEXT = f"Here you go.\n```python\n{CODE}```\nDone."
GPT_OSS = "openai/gpt-oss-120b"


@pytest.fixture
def task_dir(tmp_path):
    d = tmp_path / "task"
    d.mkdir()
    (d / "goal.md").write_text(GOAL)
    har = {"log": {"entries": [{
        "request": {"method": "GET", "url": "https://api.example.com/v1/orders", "headers": []},
        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
    }]}}
    (d / "session.har").write_text(json.dumps(har))
    for name in beam.HIDDEN_FILES:
        (d / name).write_text("HIDDEN_SECRET_TOKEN")
    return d


def response(text=GOOD_TEXT, reasoning="because"):
    message = SimpleNamespace(content=text, reasoning_content=reasoning)
    out = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    out.usage = SimpleNamespace(
        prompt_tokens=10, completion_tokens=20, completion_tokens_details=SimpleNamespace(reasoning_tokens=7)
    )
    return out


def status_error(status, headers=None):
    req = httpx2.Request("POST", "https://router.huggingface.co/v1/chat/completions")
    resp = httpx2.Response(status, request=req, json={}, headers=headers or {})
    cls = openai.RateLimitError if status == 429 else openai.APIStatusError
    return cls("error", response=resp, body={})


class FakeRaw:
    def __init__(self, parsed, headers=None):
        self._parsed, self.headers = parsed, httpx2.Headers(headers or {})

    def parse(self):
        return self._parsed


class FakeClient:
    """Plays back responses or exceptions through `with_raw_response`."""

    def __init__(self, outcomes):
        self.outcomes, self.requests = list(outcomes), []
        raw = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=raw))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeRaw(outcome)


def solve(task_dir, tmp_path, outcomes, model=GPT_OSS, **kw):
    client = FakeClient(outcomes)
    waits = []
    out = tmp_path / "out" / "client.py"
    log = openai_compat.solve(task_dir, out, client=client, sleep=waits.append, model=model, **kw)
    return log, client, waits, out


# ---- model config ----------------------------------------------------------


def test_model_config_ignores_router_policy_suffix():
    assert openai_compat.model_config("openai/gpt-oss-120b:cheapest")["id"] == GPT_OSS
    assert openai_compat.model_config("no/such-model") == {}


def test_qwen_explicit_thinking_reaches_api_and_log(task_dir, tmp_path):
    log, client, _, _ = solve(task_dir, tmp_path, [response()],
                             model='Qwen/Qwen3.5-397B-A17B:novita',
                             supports_reasoning_effort=False, enable_thinking=True)
    assert client.requests[0]['extra_body'] == {'enable_thinking': True}
    assert 'reasoning_effort' not in client.requests[0]
    assert log['enable_thinking_sent'] is True


def test_baseline_models_file_is_complete():
    data = json.loads(openai_compat.MODELS_PATH.read_text())
    assert data["base_url"] == openai_compat.DEFAULT_BASE_URL
    assert len(data["candidates_for_leaderboard"]) >= 3
    for model in data["models"]:
        assert model["context_length"] >= 128_000
        assert model["price_in_per_mtok"] > 0 and model["price_out_per_mtok"] > 0
    ids = {m["id"] for m in data["models"]}
    assert set(data["candidates_for_leaderboard"]) <= ids


@pytest.mark.parametrize("effort,expected", [("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "high"), ("max", "high")])
def test_reasoning_effort_maps_five_levels_to_three(effort, expected):
    assert openai_compat.reasoning_effort_for(effort, {"supports_reasoning_effort": True}) == expected


def test_reasoning_effort_is_none_without_support():
    assert openai_compat.reasoning_effort_for("high", {}) is None


# ---- solve -----------------------------------------------------------------


def test_prompt_is_identical_to_beam(task_dir, tmp_path):
    beam_client = FakeClient([response()])
    beam.solve(task_dir, tmp_path / "b" / "client.py", client=beam_client)
    _, client, _, _ = solve(task_dir, tmp_path, [response()])
    assert client.requests[0]["messages"] == beam_client.requests[0]["messages"]


def test_log_has_every_beam_key_and_writes_code(task_dir, tmp_path):
    beam_log = beam.solve(task_dir, tmp_path / "b" / "client.py", client=FakeClient([response()]))
    log, _, _, out = solve(task_dir, tmp_path, [response()])
    assert set(beam_log) <= set(log)
    assert log["status"] == "ok" and out.read_text() == CODE
    assert log["usage"] == {"prompt_tokens": 10, "completion_tokens": 20, "reasoning_tokens": 7}
    assert json.loads(out.with_name("solve_log.json").read_text())["model"] == GPT_OSS


def test_effort_goes_to_reasoning_effort_only_for_supporting_models(task_dir, tmp_path):
    log, client, _, _ = solve(task_dir, tmp_path, [response()], effort="xhigh")
    assert client.requests[0]["reasoning_effort"] == "high" and log["reasoning_effort_sent"] == "high"
    log, client, _, _ = solve(task_dir, tmp_path / "x", [response()], model="deepseek-ai/DeepSeek-V4-Flash", effort="high")
    assert "reasoning_effort" not in client.requests[0]
    assert log["reasoning_effort_sent"] is None and "ignored" in log["effort_note"]


def test_supports_reasoning_effort_override(task_dir, tmp_path):
    _, client, _, _ = solve(task_dir, tmp_path, [response()], model="some/unknown", supports_reasoning_effort=True)
    assert client.requests[0]["reasoning_effort"] == "high"


def test_no_code_is_logged_not_raised(task_dir, tmp_path):
    log, _, _, out = solve(task_dir, tmp_path, [response(text="no fences")])
    assert log["status"] == "no_code" and not out.exists()


def test_reasoning_field_of_gpt_oss_is_kept(task_dir, tmp_path):
    message = SimpleNamespace(content=GOOD_TEXT, reasoning="step by step")
    resp = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
    log, _, _, _ = solve(task_dir, tmp_path, [resp])
    assert log["reasoning_content"] == "step by step"


def test_hidden_files_are_never_read(task_dir, tmp_path):
    _, client, _, _ = solve(task_dir, tmp_path, [response()])
    assert "HIDDEN_SECRET_TOKEN" not in json.dumps(client.requests)


def test_rejects_bad_effort_and_har_mode(task_dir, tmp_path):
    with pytest.raises(ValueError):
        solve(task_dir, tmp_path, [], effort="turbo")
    with pytest.raises(ValueError):
        solve(task_dir, tmp_path, [], har_mode="zip")


# ---- retries ---------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_retries_retryable_statuses_and_honours_retry_after(task_dir, tmp_path, status):
    log, client, waits, _ = solve(
        task_dir, tmp_path, [status_error(status, {"retry-after": "7"}), response()]
    )
    assert log["status"] == "ok" and len(client.requests) == 2
    assert 7 <= waits[0] <= 7 * (1 + openai_compat.JITTER_FRACTION)


def test_backoff_doubles_without_retry_after(task_dir, tmp_path):
    _, _, waits, _ = solve(task_dir, tmp_path, [status_error(500), status_error(500), response()])
    assert waits[0] < waits[1] and waits[0] >= openai_compat.BACKOFF_BASE_S


def test_does_not_retry_client_errors(task_dir, tmp_path):
    with pytest.raises(openai.APIStatusError):
        solve(task_dir, tmp_path, [status_error(400)])
    log = json.loads((tmp_path / "out" / "solve_log.json").read_text())
    assert log["status"] == "api_error"


def test_gives_up_after_max_tries(task_dir, tmp_path):
    errors = [status_error(500)] * openai_compat.MAX_TRIES
    with pytest.raises(openai.APIStatusError):
        solve(task_dir, tmp_path, errors)


def test_huge_retry_after_stops_retrying(task_dir, tmp_path):
    with pytest.raises(openai.RateLimitError):
        solve(task_dir, tmp_path, [status_error(429, {"retry-after": "86400"}), response()])


def test_retries_connection_errors(task_dir, tmp_path):
    err = openai.APIConnectionError(request=httpx2.Request("POST", "https://x"))
    log, client, _, _ = solve(task_dir, tmp_path, [err, response()])
    assert log["status"] == "ok" and len(client.requests) == 2


def test_connection_errors_get_at_most_three_extra_attempts(task_dir, tmp_path):
    err = openai.APIConnectionError(request=httpx2.Request("POST", "https://x"))
    with pytest.raises(openai.APIConnectionError):
        solve(task_dir, tmp_path, [err] * 5)


def test_402_means_credits_spent_and_stops_the_batch(task_dir, tmp_path):
    with pytest.raises(beam.DailyAllowanceExhausted):
        solve(task_dir, tmp_path, [status_error(402)])
    log = json.loads((tmp_path / "out" / "solve_log.json").read_text())
    assert log["status"] == "daily_allowance_exhausted"


# ---- API key ---------------------------------------------------------------


def test_missing_key_names_the_variable(monkeypatch, task_dir, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(beam.MissingApiKeyError, match="HF_TOKEN"):
        openai_compat.solve(task_dir, tmp_path / "o" / "client.py", model=GPT_OSS)


def test_make_client_uses_base_url_and_no_sdk_retries():
    client = openai_compat.make_client("http://x.test/v1", api_key=SECRET)
    assert str(client.base_url).startswith("http://x.test/v1") and client.max_retries == 0


# ---- against the fake HTTP server (real SDK, real HTTP) --------------------


@pytest.fixture(scope="module")
def tasks_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("compat_tasks")
    build_task.build_task(ops.load_spec(FIX / "bookshelf_apikey.yaml"), "acme:alpha", root, seed=1)
    build_task.build_task(ops.load_spec(FIX / "bookshelf_basic.yaml"), "acme:beta", root, seed=2)
    for task in root.iterdir():
        write_ref_client(task)
    return root


def test_solve_over_http_with_injected_429s(task_dir, tmp_path, monkeypatch):
    (task_dir / "ref_client.py").write_text(CODE)  # the fake picks its reply by goal text
    state = fake.FakeState(task_dir.parent, None, [429, 503])
    monkeypatch.setenv("FAKE_KEY", SECRET)
    with fake.serve(state) as base_url:
        waits = []
        log = openai_compat.solve(
            task_dir, tmp_path / "o" / "client.py", base_url=base_url, model="fake-model",
            api_key_env="FAKE_KEY", sleep=waits.append,
        )
    assert log["status"] == "ok" and (tmp_path / "o" / "client.py").read_text() == CODE
    assert len(state.requests) == 3 and len(waits) == 2
    assert SECRET not in (tmp_path / "o" / "solve_log.json").read_text()


def test_run_batch_openai_compat_end_to_end(tasks_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_KEY", SECRET)
    monkeypatch.setattr(rb, "ENV_PATH", tmp_path / "none.env")
    state = fake.FakeState(tasks_dir, None, [])
    out = tmp_path / "batch"
    with fake.serve(state) as base_url:
        code = rb.main(["--tasks", str(tasks_dir), "--out", str(out), "--solver", "openai_compat",
                        "--model", "fake-model", "--base-url", base_url, "--api-key-env", "FAKE_KEY",
                        "--effort", "high", "--concurrency", "2"])
    captured = capsys.readouterr()
    assert code == 0 and len(state.requests) == 2
    assert SECRET not in captured.out + captured.err
    rows = list(csv.DictReader(open(out / "results.csv", newline="")))
    assert [float(r["pass_rate"]) for r in rows] == [1.0, 1.0]  # the canned reference client passes
    cell = next(out.glob("*/high-raw-r1"))
    assert json.loads((cell / "solve_log.json").read_text())["provider"] == "openai_compat"


def test_run_batch_openai_compat_requires_model_and_key(tasks_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(rb, "ENV_PATH", tmp_path / "none.env")
    base = ["--tasks", str(tasks_dir), "--out", str(tmp_path / "o"), "--solver", "openai_compat"]
    assert rb.main(base) == 2 and "--model is required" in capsys.readouterr().err
    assert rb.main([*base, "--model", GPT_OSS]) == 2 and "HF_TOKEN" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_run_batch_beam_rejects_openai_compat_flags(tasks_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REFLECTION_API_KEY", SECRET)
    code = rb.main(["--tasks", str(tasks_dir), "--out", str(tmp_path / "o"), "--model", GPT_OSS])
    assert code == 2 and "only apply with --solver openai_compat" in capsys.readouterr().err


def test_run_batch_defaults_stay_on_beam():
    args = rb._parser().parse_args(["--tasks", "t", "--out", "o"])
    assert (args.solver, args.model, args.base_url, args.api_key_env) == ("beam", None, None, None)


def test_load_env_key_reads_other_names(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"REFLECTION_API_KEY=other\nHF_TOKEN={SECRET}\n")
    environ = {}
    assert rb.load_env_key(env, environ, "HF_TOKEN") is True and environ == {"HF_TOKEN": SECRET}
