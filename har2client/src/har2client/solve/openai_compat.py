"""Baseline solver for any OpenAI-compatible chat endpoint (default: Hugging Face Inference Providers).

Same task input, same prompt, same code extraction and same `solve_log.json` as `solve/beam.py`;
only the endpoint, the model and the key differ. That keeps a leaderboard row comparable with Beam.

Only `goal.md` and the single `session.<ext>` file are read (see `beam._read_visible`).

Hugging Face router (docs checked 2026-10-24): base URL `https://router.huggingface.co/v1`, key = a
fine-grained HF token with "Make calls to Inference Providers" (env `HF_TOKEN`). The model id is
`org/name`, optionally with a suffix: `:fastest` (default), `:cheapest`, `:preferred` or a provider name.
`data/baseline_models.json` lists candidate models with context length and price.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any, Callable

from har2client.solve import beam
from har2client.solve.prompt import build_system_prompt, build_user_prompt

DEFAULT_BASE_URL = "https://router.huggingface.co/v1"
DEFAULT_API_KEY_ENV = "HF_TOKEN"
MODELS_PATH = Path(__file__).resolve().parents[3] / "data" / "baseline_models.json"
DEFAULT_MAX_OUTPUT_TOKENS = 32768  # includes reasoning tokens; prompt (<= ~60K) + this stays inside 128K
REQUEST_TIMEOUT_S = 600.0
MAX_TRIES = beam.MAX_TRIES
BACKOFF_BASE_S = beam.BACKOFF_BASE_S
JITTER_FRACTION = beam.JITTER_FRACTION
MAX_RETRY_WAIT_S = 300.0  # a longer Retry-After is treated as "stop", not "sleep"
CREDITS_EXHAUSTED_STATUS = 402  # HF: pre-paid credits are spent; retrying cannot help
EFFORT_ALIASES = {"xhigh": "high", "max": "high"}  # Beam has 5 levels; OpenAI-style APIs have 3

HAR_MODES = beam.HAR_MODES
EFFORTS = beam.EFFORTS
MissingApiKeyError = beam.MissingApiKeyError
DailyAllowanceExhausted = beam.DailyAllowanceExhausted


def split_model_id(model: str) -> tuple[str, str | None]:
    """`org/name:cheapest` -> (`org/name`, `cheapest`)."""
    base, _, suffix = model.partition(":")
    return base, suffix or None


def model_config(model: str, path: Path = MODELS_PATH) -> dict:
    """Entry of `data/baseline_models.json` for `model` (policy suffix ignored); {} when unknown."""
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8")).get("models", [])
    except (OSError, ValueError):
        return {}
    base = split_model_id(model)[0]
    return next((m for m in entries if m.get("id") == base), {})


def reasoning_effort_for(effort: str, config: dict) -> str | None:
    """Value to send as `reasoning_effort`, or None when the model does not take it."""
    if not config.get("supports_reasoning_effort"):
        return None
    allowed = config.get("reasoning_effort_values") or ["low", "medium", "high"]
    value = EFFORT_ALIASES.get(effort, effort)
    return value if value in allowed else allowed[-1]


# ---- client and retry ------------------------------------------------------


def make_client(
    base_url: str = DEFAULT_BASE_URL,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    api_key: str | None = None,
) -> Any:
    key = api_key if api_key is not None else os.environ.get(api_key_env)
    if not key:
        raise MissingApiKeyError(f"{api_key_env} is not set. Export the key for {base_url} first.")
    from openai import OpenAI

    # max_retries=0: this module owns the retry policy.
    return OpenAI(base_url=base_url, api_key=key, max_retries=0, timeout=REQUEST_TIMEOUT_S)


def call_with_retry(
    client: Any,
    request: dict,
    sleep: Callable[[float], None] = time.sleep,
    max_tries: int = MAX_TRIES,
    jitter: Callable[[], float] = random.random,
) -> tuple[Any, Any]:
    """Returns (parsed response, response headers).

    Retries 429, 5xx and connection errors. On any retryable status the wait is at least
    `Retry-After` (seconds or HTTP date); without it, exponential backoff. A 402 (credits spent)
    raises `DailyAllowanceExhausted` so a batch stops; a Retry-After above `MAX_RETRY_WAIT_S` ends retrying.
    """
    from openai import APIConnectionError, APIStatusError

    connection_retries = 0
    for attempt in range(1, max_tries + 1):
        try:
            raw = client.chat.completions.with_raw_response.create(**request)
            return raw.parse(), raw.headers
        except APIStatusError as exc:
            headers = beam._error_headers(exc)
            if exc.status_code == CREDITS_EXHAUSTED_STATUS:
                raise DailyAllowanceExhausted(str(exc)) from exc
            if not beam._is_retryable(exc) or attempt == max_tries:
                raise
            wait = beam._retry_after_s(headers)
            if wait is not None and wait > MAX_RETRY_WAIT_S:
                raise
            if wait is None:
                wait = BACKOFF_BASE_S * 2 ** (attempt - 1)
        except APIConnectionError:  # includes timeouts
            connection_retries += 1
            if attempt == max_tries or connection_retries > beam.MAX_CONNECTION_RETRIES:
                raise
            wait = BACKOFF_BASE_S * 2 ** (attempt - 1)
        sleep(wait * (1 + JITTER_FRACTION * jitter()))
    raise AssertionError("unreachable")


def _reasoning_text(message: Any) -> str | None:
    """`reasoning_content` (DeepSeek/Qwen style) or `reasoning` (gpt-oss style), whichever is present."""
    text = beam._reasoning_content(message)
    if text is None:
        text = getattr(message, "reasoning", None) or (getattr(message, "model_extra", None) or {}).get("reasoning")
    return text


# ---- solve -----------------------------------------------------------------


def solve(
    task_dir: Path,
    out_path: Path,
    effort: str = "high",
    har_mode: str = "raw",
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    client: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    supports_reasoning_effort: bool | None = None,
    max_output_tokens: int | None = None,
    enable_thinking: bool | None = None,
) -> dict:
    """Ask an OpenAI-compatible model for `client.py`. Returns the log dict (also written to disk).

    `effort` becomes `reasoning_effort` only when the model's entry in `data/baseline_models.json`
    says it supports it (or `supports_reasoning_effort=True` is passed); otherwise it is not sent and
    the log records that.
    """
    if effort not in EFFORTS:
        raise ValueError(f"effort must be one of {EFFORTS}, got {effort!r}")
    if har_mode not in HAR_MODES:
        raise ValueError(f"har_mode must be one of {HAR_MODES}, got {har_mode!r}")
    task_dir, out_path = Path(task_dir), Path(out_path)
    client = client if client is not None else make_client(base_url, api_key_env)

    config = model_config(model)
    if supports_reasoning_effort is not None:
        config = {**config, "supports_reasoning_effort": supports_reasoning_effort}
    sent_effort = reasoning_effort_for(effort, config)
    max_tokens = max_output_tokens or config.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS

    goal = beam._read_visible(task_dir, "goal.md")
    # repair tasks (case 3) carry extra visible files; Beam gets the same prompt, so this solver does too
    extra_files = getattr(beam, "read_extra_files", lambda _dir: {})(task_dir)
    session_name = beam.find_optional_session_file(task_dir) if extra_files else beam.find_session_file(task_dir)
    if session_name is None:  # a repair task that shows only the failure: no recorded session section
        har_text = None
    else:
        session_text = beam._read_visible(task_dir, session_name)
        har_text = beam.prepare_har_text(session_text, har_mode) if session_name.endswith(".har") else session_text
    system = build_system_prompt(has_recording=session_name is not None, has_changelog="CHANGELOG.md" in extra_files)
    user = (
        build_user_prompt(goal, har_text, session_name, extra_files)
        if extra_files
        else build_user_prompt(goal, har_text, session_name)
    )
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    if sent_effort is not None:
        request["reasoning_effort"] = sent_effort
    if enable_thinking is not None:
        request['extra_body'] = {'enable_thinking': enable_thinking}
    log: dict[str, Any] = {
        "provider": "openai_compat",
        "base_url": base_url,
        "model": model,
        "effort": effort,
        "reasoning_effort_sent": sent_effort,
        "enable_thinking_sent": enable_thinking,
        "effort_note": None if sent_effort is not None else "model does not take reasoning_effort; effort ignored",
        "har_mode": har_mode,
        "prompt_tokens_estimate": (len(system) + len(user)) // beam.CHARS_PER_TOKEN,
        "usage": {},
        "rate_limit_headers": {},
        "wall_time_s": None,
        "status": None,
        "reasoning_content": None,
        "response_text": None,
    }
    if extra_files:
        log["extra_files"] = sorted(extra_files)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        response, headers = call_with_retry(client, request, sleep=sleep)
    except DailyAllowanceExhausted as exc:
        log.update(status="daily_allowance_exhausted", error=str(exc)[: beam.BODY_LIMIT],
                   wall_time_s=round(time.monotonic() - started, 3))
        beam._write_log(out_path, log)
        raise
    except Exception as exc:
        log.update(status="api_error", error=f"{type(exc).__name__}: {exc}"[: beam.BODY_LIMIT],
                   rate_limit_headers=beam.rate_limit_headers(beam._error_headers(exc)),
                   wall_time_s=round(time.monotonic() - started, 3))
        beam._write_log(out_path, log)
        raise

    message = response.choices[0].message
    text = message.content or ""
    code = beam.extract_code(text)
    if code is not None:
        out_path.write_text(code, encoding='utf-8')
    log.update(
        usage=beam._usage(response),
        rate_limit_headers=beam.rate_limit_headers(headers),
        wall_time_s=round(time.monotonic() - started, 3),
        status="ok" if code is not None else "no_code",
        response_model=getattr(response, "model", None),  # what the provider reports it served (router policy may differ from `model`)
        max_output_tokens=max_tokens,
        finish_reason=getattr(response.choices[0], 'finish_reason', None),
        reasoning_content=_reasoning_text(message),
        response_text=text,
    )
    beam._write_log(out_path, log)
    return log
