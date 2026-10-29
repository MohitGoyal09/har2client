"""Tests for har2client.solve.beam (no network: the OpenAI client is mocked)."""

import builtins
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest

from har2client.solve import beam

GOAL = "# Goal\nImplement `list_orders(self)`."
CODE = "import httpx\n\nclass Client:\n    def __init__(self, base_url, api_key):\n        pass\n"
GOOD_TEXT = f"Here you go.\n```python\n{CODE}```\nDone."


def entry(url, mime="application/json", text="{}", method="GET"):
    return {
        "request": {"method": method, "url": url, "headers": []},
        "response": {"status": 200, "content": {"mimeType": mime, "text": text}},
    }


def make_har(entries):
    return {"log": {"version": "1.2", "entries": entries}}


@pytest.fixture
def task_dir(tmp_path):
    d = tmp_path / "task"
    d.mkdir()
    (d / "goal.md").write_text(GOAL)
    har = make_har([entry("https://api.example.com/v1/orders")])
    (d / "session.har").write_text(json.dumps(har))
    for name in beam.HIDDEN_FILES:
        (d / name).write_text("HIDDEN_SECRET_TOKEN")
    return d


def response(text=GOOD_TEXT, reasoning="because", usage=True):
    message = SimpleNamespace(content=text, reasoning_content=reasoning)
    out = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    out.usage = (
        SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=20,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=7),
        )
        if usage
        else None
    )
    return out


def status_error(cls, status, body=None, headers=None):
    req = httpx2.Request("POST", "https://api.reflection.ai/openai/v1/chat/completions")
    resp = httpx2.Response(status, request=req, json=body or {}, headers=headers or {})
    return cls("error", response=resp, body=body)


class FakeRaw:
    """Mimics the SDK's `with_raw_response` result: `.headers` and `.parse()`."""

    def __init__(self, parsed, headers=None):
        self._parsed = parsed
        self.headers = httpx2.Headers(headers or {})

    def parse(self):
        return self._parsed


class FakeClient:
    """Plays back a list of responses, FakeRaw objects or exceptions via with_raw_response."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        raw = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=raw))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, FakeRaw) else FakeRaw(outcome)


def no_sleep(_):
    pass


# ---- code extraction -----------------------------------------------------


def test_extract_code_returns_last_python_block():
    text = "```python\nA = 1\n```\ntext\n```python\nB = 2\n```"
    assert beam.extract_code(text) == "B = 2\n"


def test_extract_code_ignores_other_languages_and_none():
    assert beam.extract_code("```bash\nls\n```") is None
    assert beam.extract_code("") is None
    assert beam.extract_code("no fences") is None


# ---- API key -------------------------------------------------------------


def test_missing_key_raises_clear_error(monkeypatch, task_dir, tmp_path):
    monkeypatch.delenv(beam.API_KEY_ENV, raising=False)
    with pytest.raises(beam.MissingApiKeyError, match="REFLECTION_API_KEY"):
        beam.solve(task_dir, tmp_path / "out" / "client.py")


def test_make_client_uses_base_url_and_no_sdk_retries(monkeypatch):
    monkeypatch.setenv(beam.API_KEY_ENV, "k")
    c = beam.make_client()
    assert str(c.base_url).startswith("https://api.reflection.ai/openai/v1")
    assert c.max_retries == 0


# ---- solve: happy path ---------------------------------------------------


def test_solve_writes_client_and_log(task_dir, tmp_path):
    fake = FakeClient([response()])
    out = tmp_path / "out" / "client.py"
    result = beam.solve(task_dir, out, effort="max", har_mode="raw", client=fake)

    assert out.read_text() == CODE
    log = json.loads((out.parent / "solve_log.json").read_text())
    assert log == result
    assert log["status"] == "ok"
    assert log["model"] == "Beam-1.0-501B-A23B"
    assert log["effort"] == "max" and log["har_mode"] == "raw"
    assert log["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "reasoning_tokens": 7,
    }
    assert log["reasoning_content"] == "because"
    assert log["response_text"] == GOOD_TEXT
    assert log["prompt_tokens_estimate"] > 0
    assert log["wall_time_s"] >= 0

    req = fake.requests[0]
    assert req["model"] == "Beam-1.0-501B-A23B"
    assert req["reasoning_effort"] == "max"
    assert req["max_completion_tokens"] == 65536
    assert "temperature" not in req  # server default
    user = req["messages"][1]["content"]
    assert GOAL in user and "api.example.com/v1/orders" in user


def test_solve_reads_reasoning_from_model_extra(task_dir, tmp_path):
    message = SimpleNamespace(content=GOOD_TEXT, model_extra={"reasoning_content": "x"})
    resp = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
    log = beam.solve(task_dir, tmp_path / "c.py", client=FakeClient([resp]))
    assert log["reasoning_content"] == "x"
    assert log["usage"] == {}


def test_solve_no_code_writes_no_client(task_dir, tmp_path):
    out = tmp_path / "out" / "client.py"
    result = beam.solve(task_dir, out, client=FakeClient([response(text="sorry")]))
    assert result["status"] == "no_code"
    assert not out.exists()
    assert (out.parent / "solve_log.json").exists()


def test_solve_rejects_bad_effort_and_mode(task_dir, tmp_path):
    with pytest.raises(ValueError):
        beam.solve(task_dir, tmp_path / "c.py", effort="ultra", client=FakeClient([]))
    with pytest.raises(ValueError):
        beam.solve(task_dir, tmp_path / "c.py", har_mode="zip", client=FakeClient([]))


# ---- retry ---------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,status", [(openai.RateLimitError, 429), (openai.InternalServerError, 503)]
)
def test_retries_on_429_and_5xx(task_dir, tmp_path, cls, status):
    fake = FakeClient(
        [status_error(cls, status, {"error": {"code": "busy"}}), response()]
    )
    sleeps = []
    result = beam.solve(task_dir, tmp_path / "c.py", client=fake, sleep=sleeps.append)
    assert result["status"] == "ok"
    assert len(fake.requests) == 2
    assert len(sleeps) == 1


def test_backoff_is_exponential_with_jitter_and_capped_at_five_tries(task_dir, tmp_path):
    errs = [status_error(openai.InternalServerError, 500) for _ in range(5)]
    fake = FakeClient(errs)
    sleeps = []
    with pytest.raises(openai.InternalServerError):
        beam.solve(task_dir, tmp_path / "c.py", client=fake, sleep=sleeps.append)
    assert len(fake.requests) == 5
    assert len(sleeps) == 4
    for slept, base in zip(sleeps, [2.0, 4.0, 8.0, 16.0]):
        assert base <= slept <= base * (1 + beam.JITTER_FRACTION)
    log = json.loads((tmp_path / "solve_log.json").read_text())
    assert log["status"] == "api_error"


def test_does_not_retry_other_4xx(task_dir, tmp_path):
    fake = FakeClient([status_error(openai.BadRequestError, 400)])
    with pytest.raises(openai.BadRequestError):
        beam.solve(task_dir, tmp_path / "c.py", client=fake, sleep=no_sleep)
    assert len(fake.requests) == 1


def _conn_error(cls=openai.APIConnectionError):
    request = httpx2.Request("POST", "https://api.reflection.ai/openai/v1/chat/completions")
    return cls(request=request)


@pytest.mark.parametrize("cls", [openai.APIConnectionError, openai.APITimeoutError])
def test_retries_connection_errors_and_timeouts_like_429(cls):
    fake = FakeClient([_conn_error(cls), response()])
    sleeps = []
    beam.call_with_retry(fake, {}, sleep=sleeps.append, jitter=lambda: 0.0)
    assert len(fake.requests) == 2
    assert sleeps == [2.0]  # same backoff as a 429 without Retry-After


def test_connection_errors_get_at_most_three_extra_attempts(task_dir, tmp_path):
    fake = FakeClient([_conn_error() for _ in range(5)])
    sleeps = []
    with pytest.raises(openai.APIConnectionError):
        beam.solve(task_dir, tmp_path / "c.py", client=fake, sleep=sleeps.append)
    assert len(fake.requests) == 4 and len(sleeps) == 3
    log = json.loads((tmp_path / "solve_log.json").read_text())
    assert log["status"] == "api_error"


def test_connection_error_retry_does_not_override_should_retry_false():
    err = status_error(openai.RateLimitError, 429, headers={"x-should-retry": "false"})
    fake = FakeClient([_conn_error(), err, response()])
    with pytest.raises(beam.DailyAllowanceExhausted):
        beam.call_with_retry(fake, {}, sleep=no_sleep)
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": "daily_allowance_exhausted", "message": "m"}},
        {"code": "daily_allowance_exhausted"},
    ],
)
def test_daily_allowance_stops_immediately(task_dir, tmp_path, body):
    fake = FakeClient([status_error(openai.RateLimitError, 429, body), response()])
    sleeps = []
    with pytest.raises(beam.DailyAllowanceExhausted):
        beam.solve(task_dir, tmp_path / "c.py", client=fake, sleep=sleeps.append)
    assert len(fake.requests) == 1
    assert sleeps == []
    log = json.loads((tmp_path / "solve_log.json").read_text())
    assert log["status"] == "daily_allowance_exhausted"


# ---- Reflection rate-limit rules -------------------------------------------


def test_backoff_without_header_uses_injected_jitter():
    errs = [status_error(openai.RateLimitError, 429), response()]
    fake = FakeClient(errs)
    sleeps = []
    beam.call_with_retry(fake, {}, sleep=sleeps.append, jitter=lambda: 0.0)
    assert sleeps == [2.0]


@pytest.mark.parametrize(
    "cls,status", [(openai.RateLimitError, 429), (openai.InternalServerError, 503)]
)
def test_waits_at_least_retry_after_seconds(task_dir, tmp_path, cls, status):
    err = status_error(cls, status, {"error": {"code": "busy"}}, headers={"Retry-After": "37"})
    fake = FakeClient([err, response()])
    sleeps = []
    result = beam.solve(task_dir, tmp_path / "c.py", client=fake, sleep=sleeps.append)
    assert result["status"] == "ok"
    assert len(sleeps) == 1 and sleeps[0] >= 37


def test_retry_after_overrides_a_shorter_backoff():
    fake = FakeClient([status_error(openai.RateLimitError, 429, headers={"retry-after": "1"}), response()])
    sleeps = []
    beam.call_with_retry(fake, {}, sleep=sleeps.append, jitter=lambda: 0.0)
    assert sleeps == [1.0]


def test_unparseable_retry_after_falls_back_to_backoff():
    err = status_error(openai.RateLimitError, 429, headers={"retry-after": "soon"})
    fake = FakeClient([err, response()])
    sleeps = []
    beam.call_with_retry(fake, {}, sleep=sleeps.append, jitter=lambda: 0.0)
    assert sleeps == [2.0]


@pytest.mark.parametrize(
    "cls,status", [(openai.RateLimitError, 429), (openai.InternalServerError, 503)]
)
def test_should_retry_false_stops_like_daily_allowance(task_dir, tmp_path, cls, status):
    err = status_error(cls, status, {"error": {"code": "limit"}}, headers={"x-should-retry": "false"})
    fake = FakeClient([err, response()])
    sleeps = []
    with pytest.raises(beam.DailyAllowanceExhausted):
        beam.solve(task_dir, tmp_path / "c.py", client=fake, sleep=sleeps.append)
    assert len(fake.requests) == 1 and sleeps == []
    log = json.loads((tmp_path / "solve_log.json").read_text())
    assert log["status"] == "daily_allowance_exhausted"


def test_should_retry_true_still_retries():
    err = status_error(openai.RateLimitError, 429, headers={"x-should-retry": "true"})
    fake = FakeClient([err, response()])
    beam.call_with_retry(fake, {}, sleep=no_sleep)
    assert len(fake.requests) == 2


RATE_HEADERS = {
    "x-ratelimit-remaining-tokens-day": "900000",
    "x-ratelimit-remaining-output-tokens-day": "123456",
    "x-ratelimit-remaining-uncached-input-tokens-day": "800000",
    "x-ratelimit-remaining-requests-day": "42",
    "x-unrelated": "ignored",
}


def test_log_records_rate_limit_headers(task_dir, tmp_path):
    fake = FakeClient([FakeRaw(response(), RATE_HEADERS)])
    log = beam.solve(task_dir, tmp_path / "c.py", client=fake)
    assert log["rate_limit_headers"] == {
        "x-ratelimit-remaining-tokens-day": 900000,
        "x-ratelimit-remaining-output-tokens-day": 123456,
        "x-ratelimit-remaining-uncached-input-tokens-day": 800000,
        "x-ratelimit-remaining-requests-day": 42,
    }
    on_disk = json.loads((tmp_path / "solve_log.json").read_text())
    assert on_disk["rate_limit_headers"] == log["rate_limit_headers"]


def test_log_rate_limit_headers_empty_when_absent(task_dir, tmp_path):
    log = beam.solve(task_dir, tmp_path / "c.py", client=FakeClient([response()]))
    assert log["rate_limit_headers"] == {}


def test_no_code_result_still_records_rate_limit_headers(task_dir, tmp_path):
    fake = FakeClient([FakeRaw(response(text="sorry"), RATE_HEADERS)])
    log = beam.solve(task_dir, tmp_path / "c.py", client=fake)
    assert log["status"] == "no_code"
    assert log["rate_limit_headers"]["x-ratelimit-remaining-output-tokens-day"] == 123456


def test_daily_allowance_log_records_headers_from_error_response(task_dir, tmp_path):
    body = {"error": {"code": "daily_allowance_exhausted"}}
    err = status_error(openai.RateLimitError, 429, body, headers=RATE_HEADERS)
    with pytest.raises(beam.DailyAllowanceExhausted):
        beam.solve(task_dir, tmp_path / "c.py", client=FakeClient([err]))
    log = json.loads((tmp_path / "solve_log.json").read_text())
    assert log["rate_limit_headers"]["x-ratelimit-remaining-requests-day"] == 42


# ---- hidden files --------------------------------------------------------


def test_assertion_blocks_hidden_file_reads(task_dir):
    for name in beam.HIDDEN_FILES:
        with pytest.raises(AssertionError):
            beam._read_visible(task_dir, name)


def test_solve_never_opens_hidden_files(task_dir, tmp_path, monkeypatch):
    opened = []

    def record(target):
        opened.append(Path(os.fspath(target)).name)

    real_open, real_io_open = builtins.open, io.open
    real_path_open = Path.open

    def spy_open(file, *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            record(file)
        return real_open(file, *a, **k)

    def spy_io_open(file, *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            record(file)
        return real_io_open(file, *a, **k)

    def spy_path_open(self, *a, **k):
        record(self)
        return real_path_open(self, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_io_open)
    monkeypatch.setattr(Path, "open", spy_path_open)

    fake = FakeClient([response()])
    for har_mode in beam.HAR_MODES:
        fake.outcomes.append(response())
        beam.solve(task_dir, tmp_path / har_mode / "c.py", har_mode=har_mode, client=fake)

    assert {"goal.md", "session.har"} <= set(opened)
    assert not set(opened) & set(beam.HIDDEN_FILES)
    for req in fake.requests:
        assert "HIDDEN_SECRET_TOKEN" not in json.dumps(req)


def test_solve_works_when_hidden_files_are_unreadable(task_dir, tmp_path):
    for name in beam.HIDDEN_FILES:
        (task_dir / name).chmod(0)
    try:
        result = beam.solve(task_dir, tmp_path / "c.py", client=FakeClient([response()]))
    finally:
        for name in beam.HIDDEN_FILES:
            (task_dir / name).chmod(0o644)
    assert result["status"] == "ok"


# ---- compact mode --------------------------------------------------------


def test_compact_keeps_most_common_host_only():
    har = make_har(
        [
            entry("https://api.example.com/a"),
            entry("https://api.example.com/b"),
            entry("https://www.google-analytics.com/collect"),
        ]
    )
    urls = [e["request"]["url"] for e in beam.compact_har(har)["log"]["entries"]]
    assert urls == ["https://api.example.com/a", "https://api.example.com/b"]


def test_compact_drops_static_assets_by_extension_and_mime():
    har = make_har(
        [
            entry("https://api.example.com/api/x"),
            entry("https://api.example.com/api/y"),
            entry("https://api.example.com/app.js", mime="application/json"),
            entry("https://api.example.com/logo", mime="image/png"),
            entry("https://api.example.com/", mime="text/html; charset=utf-8"),
            entry("https://api.example.com/f", mime="font/woff2"),
            entry("https://api.example.com/s", mime="text/javascript"),
        ]
    )
    urls = [e["request"]["url"] for e in beam.compact_har(har)["log"]["entries"]]
    assert urls == ["https://api.example.com/api/x", "https://api.example.com/api/y"]


def test_compact_host_chosen_among_non_static_entries():
    statics = [entry("https://cdn.example.net/a.js") for _ in range(5)]
    har = make_har(statics + [entry("https://api.example.com/x")])
    urls = [e["request"]["url"] for e in beam.compact_har(har)["log"]["entries"]]
    assert urls == ["https://api.example.com/x"]


def test_compact_truncates_bodies_and_does_not_mutate_input():
    big = "x" * 5000
    har = make_har([entry("https://api.example.com/a", text=big)])
    out = beam.compact_har(har)
    content = out["log"]["entries"][0]["response"]["content"]
    assert len(content["text"]) == 2000
    assert content["textTruncated"] is True
    assert len(har["log"]["entries"][0]["response"]["content"]["text"]) == 5000


def test_compact_leaves_short_bodies_alone():
    har = make_har([entry("https://api.example.com/a", text="short")])
    content = beam.compact_har(har)["log"]["entries"][0]["response"]["content"]
    assert content["text"] == "short" and "textTruncated" not in content


def test_compact_handles_empty_har():
    assert beam.compact_har({"log": {"entries": []}})["log"]["entries"] == []
    assert beam.compact_har({})["log"]["entries"] == []


def test_prepare_har_text_modes():
    raw = json.dumps(make_har([entry("https://api.example.com/a")]), indent=4)
    assert beam.prepare_har_text(raw, "raw") == raw
    compact = json.loads(beam.prepare_har_text(raw, "compact"))
    assert len(compact["log"]["entries"]) == 1
    with pytest.raises(ValueError):
        beam.prepare_har_text(raw, "other")


def test_solve_compact_mode_sends_smaller_prompt(task_dir, tmp_path):
    big = [entry("https://api.example.com/v1/orders", text="y" * 9000)]
    noise = [entry("https://cdn.example.net/a.js", mime="text/javascript")]
    (task_dir / "session.har").write_text(json.dumps(make_har(big + noise)))
    raw_fake, compact_fake = FakeClient([response()]), FakeClient([response()])
    raw = beam.solve(task_dir, tmp_path / "r" / "c.py", har_mode="raw", client=raw_fake)
    compact = beam.solve(
        task_dir, tmp_path / "k" / "c.py", har_mode="compact", client=compact_fake
    )
    assert compact["prompt_tokens_estimate"] < raw["prompt_tokens_estimate"]
    assert "cdn.example.net" not in compact_fake.requests[0]["messages"][1]["content"]


# ---- session file lookup (several traffic sources) ----------------------


def test_find_session_file_returns_the_single_visible_file(task_dir):
    assert beam.find_session_file(task_dir) == "session.har"


def test_find_session_file_rejects_none_or_two(task_dir, tmp_path, monkeypatch):
    (task_dir / "session.har").unlink()
    with pytest.raises(beam.BeamError):
        beam.find_session_file(task_dir)
    monkeypatch.setattr(beam, "VISIBLE_FILES", ("goal.md", "session.har", "session.other.txt"))
    (task_dir / "session.har").write_text("{}")
    (task_dir / "session.other.txt").write_text("x")
    with pytest.raises(beam.BeamError):
        beam.find_session_file(task_dir)


def test_ref_client_is_hidden_and_never_sent(task_dir, tmp_path):
    (task_dir / "ref_client.py").write_text("REFERENCE_SECRET")
    fake = FakeClient([response()])
    beam.solve(task_dir, tmp_path / "c.py", client=fake)
    assert "ref_client.py" in beam.HIDDEN_FILES and "REFERENCE_SECRET" not in json.dumps(fake.requests)


def test_solver_reads_the_other_session_file_of_a_task_and_keeps_non_har_text_raw(task_dir, tmp_path, monkeypatch):
    (task_dir / "session.har").unlink()
    (task_dir / "session.other.txt").write_text("GET /things HTTP/1.1\n")
    monkeypatch.setattr(beam, "VISIBLE_FILES", ("goal.md", "session.other.txt"))
    for mode in beam.HAR_MODES:
        fake = FakeClient([response()])
        beam.solve(task_dir, tmp_path / mode / "c.py", har_mode=mode, client=fake)
        user = fake.requests[0]["messages"][1]["content"]
        assert "GET /things HTTP/1.1" in user and "`session.other.txt`" in user


def test_mobile_task_dir_is_read_like_a_browser_one(tmp_path):
    from har2client.tierb.build import build_tierb_task
    from har2client.tierb import config as cfg

    d = build_tierb_task({"index": 0, "domain": "tickets", "config": cfg.baseline_config(), "seed": 3}, tmp_path, source="mobile_har", negatives=False)
    fake = FakeClient([response()])
    result = beam.solve(d, tmp_path / "c.py", client=fake)
    assert result["status"] == "ok" and "Recorded session (HAR)" in fake.requests[0]["messages"][1]["content"]
