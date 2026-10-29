"""Beam baseline solver: task dir (goal.md + the one visible session file) -> client.py.

Only `goal.md` and the single `session.<ext>` file are ever read (`session.har` for the HAR sources),
plus `client_v1.py` and `failure.txt` when a repair task (case 3) has them.
The hidden grading files (`task.json`, `op_table.json`, `tests.json`, `engine.json`, `ref_client.py`)
are never opened.
"""

from __future__ import annotations

import copy
import json
import os
import random
import re
import time
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from har2client.env.sources import OBSERVATION_NAMES
from har2client.solve.envfile import API_KEY_ENV
from har2client.solve.prompt import build_system_prompt, build_user_prompt

BASE_URL = "https://api.reflection.ai/openai/v1"
MODEL = "Beam-1.0-501B-A23B"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
HAR_MODES = ("raw", "compact")
MAX_COMPLETION_TOKENS = 65536
MAX_TRIES = 5
MAX_CONNECTION_RETRIES = 3  # extra attempts after a connection error or timeout
BACKOFF_BASE_S = 2.0
JITTER_FRACTION = 0.25  # each wait is stretched by up to this fraction
RETRYABLE_HINT_STATUSES = (429, 503)  # statuses where Retry-After / x-should-retry apply
RATE_LIMIT_HEADERS = (
    "x-ratelimit-remaining-tokens-day",
    "x-ratelimit-remaining-output-tokens-day",
    "x-ratelimit-remaining-uncached-input-tokens-day",
    "x-ratelimit-remaining-requests-day",
)
BODY_LIMIT = 2000
CHARS_PER_TOKEN = 4  # rough prompt-size estimate
DAILY_ALLOWANCE_CODE = "daily_allowance_exhausted"

VISIBLE_FILES = ("goal.md", *dict.fromkeys(OBSERVATION_NAMES))
EXTRA_VISIBLE_FILES = ("client_v1.py", "failure.txt", "CHANGELOG.md")  # repair tasks (case 3); read only when present
HIDDEN_FILES = ("task.json", "op_table.json", "tests.json", "engine.json", "ref_client.py")

STATIC_EXTENSIONS = frozenset(
    ".js .mjs .css .map .png .jpg .jpeg .gif .svg .ico .webp .avif .woff .woff2 "
    ".ttf .otf .eot .html .htm".split()
)
STATIC_MIME_PREFIXES = ("text/html", "text/css", "image/", "font/", "audio/", "video/")
STATIC_MIME_SUBSTRINGS = ("javascript", "ecmascript", "font-woff", "x-font")

_CODE_BLOCK = re.compile(r"```python[ \t]*\r?\n(.*?)```", re.DOTALL)


class BeamError(Exception):
    """Base class for solver errors."""


class MissingApiKeyError(BeamError):
    """REFLECTION_API_KEY is not set."""


class DailyAllowanceExhausted(BeamError):
    """The daily limit is spent (`daily_allowance_exhausted` or `x-should-retry: false`)."""

    def __init__(self, message: str, rate_limit_headers: dict | None = None):
        super().__init__(message)
        self.rate_limit_headers = rate_limit_headers or {}


# ---- reading visible files ----------------------------------------------


def _read_visible(task_dir: Path, name: str) -> str:
    assert name in VISIBLE_FILES or name in EXTRA_VISIBLE_FILES, f"solver may not read {name!r}"
    assert name not in HIDDEN_FILES
    return (Path(task_dir) / name).read_text(encoding="utf-8")


def find_session_file(task_dir: Path) -> str:
    """Name of the single visible `session.*` file of a task dir (its format is the task's traffic source)."""
    found = sorted(name for name in VISIBLE_FILES[1:] if (Path(task_dir) / name).is_file())
    if len(found) != 1:
        raise BeamError(f"expected exactly one session file in {task_dir}, found {found or 'none'}")
    return found[0]


def find_optional_session_file(task_dir: Path) -> str | None:
    """Like `find_session_file`, but None when the task has no session file (a repair task with `failure_only` evidence)."""
    if not any((Path(task_dir) / name).is_file() for name in VISIBLE_FILES[1:]):
        return None
    return find_session_file(task_dir)


def read_extra_files(task_dir: Path) -> dict[str, str]:
    """The extra visible files (`client_v1.py`, `failure.txt`, `CHANGELOG.md`) that a repair task has; {} for a case 1 task."""
    return {n: _read_visible(task_dir, n) for n in EXTRA_VISIBLE_FILES if (Path(task_dir) / n).is_file()}


# ---- HAR compaction (pure) ----------------------------------------------


def _host(entry: dict) -> str:
    return urlsplit(entry.get("request", {}).get("url", "")).netloc.lower()


def _is_static(entry: dict) -> bool:
    path = urlsplit(entry.get("request", {}).get("url", "")).path.lower()
    if os.path.splitext(path)[1] in STATIC_EXTENSIONS:
        return True
    mime = (
        (entry.get("response", {}).get("content") or {}).get("mimeType") or ""
    ).lower()
    return mime.startswith(STATIC_MIME_PREFIXES) or any(
        s in mime for s in STATIC_MIME_SUBSTRINGS
    )


def most_common_api_host(entries: list[dict]) -> str | None:
    """Most frequent host among non-static entries (all entries if none)."""
    pool = [e for e in entries if not _is_static(e)] or entries
    counts = Counter(h for h in map(_host, pool) if h)
    if not counts:
        return None
    top = max(counts.values())
    return min(h for h, n in counts.items() if n == top)  # deterministic tie-break


def compact_har(har: dict) -> dict:
    """Drop other hosts and static assets; truncate response bodies to 2,000 chars.

    Pure: the input is not modified.
    """
    entries = (har.get("log") or {}).get("entries") or []
    host = most_common_api_host(entries)
    kept = []
    for entry in entries:
        if host is not None and _host(entry) != host:
            continue
        if _is_static(entry):
            continue
        entry = copy.deepcopy(entry)
        content = (entry.get("response") or {}).get("content")
        text = content.get("text") if isinstance(content, dict) else None
        if isinstance(text, str) and len(text) > BODY_LIMIT:
            content["text"] = text[:BODY_LIMIT]
            content["textTruncated"] = True
        kept.append(entry)
    out = copy.deepcopy(har)
    out.setdefault("log", {})["entries"] = kept
    return out


def prepare_har_text(raw_har_text: str, har_mode: str) -> str:
    if har_mode == "raw":
        return raw_har_text
    if har_mode == "compact":
        return json.dumps(compact_har(json.loads(raw_har_text)), separators=(",", ":"))
    raise ValueError(f"har_mode must be one of {HAR_MODES}, got {har_mode!r}")


# ---- code extraction -----------------------------------------------------


def extract_code(text: str) -> str | None:
    """Return the last ```python block, or None."""
    blocks = _CODE_BLOCK.findall(text or "")
    return blocks[-1] if blocks else None


# ---- API -----------------------------------------------------------------


def make_client(api_key: str | None = None) -> Any:
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
    if not key:
        raise MissingApiKeyError(
            f"{API_KEY_ENV} is not set. Export your Reflection AI key first."
        )
    from openai import OpenAI

    # max_retries=0: this module owns the retry policy.
    return OpenAI(base_url=BASE_URL, api_key=key, max_retries=0)


def _error_code(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("code"):
            return str(err["code"])
        if isinstance(err, str):
            return err
    response = getattr(exc, "response", None)
    text = getattr(response, "text", "") or ""
    if DAILY_ALLOWANCE_CODE in text or DAILY_ALLOWANCE_CODE in str(exc):
        return DAILY_ALLOWANCE_CODE
    return None


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status == 429 or (isinstance(status, int) and status >= 500)


def _header(headers: Any, name: str) -> str | None:
    """Case-insensitive header lookup that also works for plain dicts."""
    if headers is None:
        return None
    value = headers.get(name)
    if value is None and hasattr(headers, "items"):
        value = next((v for k, v in headers.items() if str(k).lower() == name), None)
    return None if value is None else str(value)


def _retry_after_s(headers: Any) -> float | None:
    """Seconds from a Retry-After header (number or HTTP date); None if absent or unreadable."""
    raw = _header(headers, "retry-after")
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        return max(parsedate_to_datetime(raw).timestamp() - time.time(), 0.0)
    except (TypeError, ValueError):
        return None


def rate_limit_headers(headers: Any) -> dict:
    """The daily rate-limit headers present in `headers`, as ints where they parse."""
    out: dict[str, Any] = {}
    for name in RATE_LIMIT_HEADERS:
        raw = _header(headers, name)
        if raw is None:
            continue
        try:
            out[name] = int(raw)
        except ValueError:
            out[name] = raw
    return out


def _error_headers(exc: Exception) -> Any:
    return getattr(getattr(exc, "response", None), "headers", None)


def call_with_retry(
    client: Any,
    request: dict,
    sleep: Callable[[float], None] = time.sleep,
    max_tries: int = MAX_TRIES,
    jitter: Callable[[], float] = random.random,
) -> tuple[Any, dict]:
    """Returns (parsed response, rate-limit headers).

    Reflection rules: on 429/503 wait at least `Retry-After` seconds; `x-should-retry: false`
    means the daily limit is spent, so stop. Without the header, back off exponentially.
    Connection errors and timeouts back off the same way, with at most `MAX_CONNECTION_RETRIES` extra attempts.
    """
    from openai import APIConnectionError, APIStatusError

    connection_retries = 0
    for attempt in range(1, max_tries + 1):
        try:
            raw = client.chat.completions.with_raw_response.create(**request)
            return raw.parse(), rate_limit_headers(raw.headers)
        except APIConnectionError:  # includes timeouts; no response, so no Retry-After or x-should-retry
            connection_retries += 1
            if attempt == max_tries or connection_retries > MAX_CONNECTION_RETRIES:
                raise
            wait = BACKOFF_BASE_S * 2 ** (attempt - 1)
        except APIStatusError as exc:
            headers = _error_headers(exc)
            spent = exc.status_code == 429 and _error_code(exc) == DAILY_ALLOWANCE_CODE
            if exc.status_code in RETRYABLE_HINT_STATUSES and (
                (_header(headers, "x-should-retry") or "").lower() == "false"
            ):
                spent = True
            if spent:
                raise DailyAllowanceExhausted(str(exc), rate_limit_headers(headers)) from exc
            if not _is_retryable(exc) or attempt == max_tries:
                raise
            wait = _retry_after_s(headers) if exc.status_code in RETRYABLE_HINT_STATUSES else None
            if wait is None:
                wait = BACKOFF_BASE_S * 2 ** (attempt - 1)
        sleep(wait * (1 + JITTER_FRACTION * jitter()))
    raise AssertionError("unreachable")


def _reasoning_content(message: Any) -> str | None:
    value = getattr(message, "reasoning_content", None)
    if value is None:
        value = (getattr(message, "model_extra", None) or {}).get("reasoning_content")
    return value


def _usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    details = getattr(usage, "completion_tokens_details", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None),
    }


# ---- solve ---------------------------------------------------------------


def _write_log(out_path: Path, log: dict) -> Path:
    log_path = out_path.with_name("solve_log.json")
    log_path.write_text(json.dumps(log, indent=2))
    return log_path


def solve(
    task_dir: Path,
    out_path: Path,
    effort: str = "high",
    har_mode: str = "raw",
    *,
    client: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Ask Beam for `client.py`. Returns the log dict (also written to disk)."""
    if effort not in EFFORTS:
        raise ValueError(f"effort must be one of {EFFORTS}, got {effort!r}")
    if har_mode not in HAR_MODES:
        raise ValueError(f"har_mode must be one of {HAR_MODES}, got {har_mode!r}")
    task_dir, out_path = Path(task_dir), Path(out_path)
    client = client if client is not None else make_client()

    goal = _read_visible(task_dir, "goal.md")
    extra_files = read_extra_files(task_dir)
    session_name = find_optional_session_file(task_dir) if extra_files else find_session_file(task_dir)
    system = build_system_prompt(has_recording=session_name is not None, has_changelog="CHANGELOG.md" in extra_files)
    if session_name is None:  # a repair task that shows only the failure: no recorded session section
        user = build_user_prompt(goal, None, None, extra_files)
    else:
        session_text = _read_visible(task_dir, session_name)
        # compaction understands HAR only; other formats are sent as they are
        har_text = prepare_har_text(session_text, har_mode) if session_name.endswith(".har") else session_text
        user = build_user_prompt(goal, har_text, session_name, extra_files) if extra_files else build_user_prompt(goal, har_text, session_name)
    request = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "reasoning_effort": effort,
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
    }
    log: dict[str, Any] = {
        "model": MODEL,
        "effort": effort,
        "har_mode": har_mode,
        "prompt_tokens_estimate": (len(system) + len(user)) // CHARS_PER_TOKEN,
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
        response, limits = call_with_retry(client, request, sleep=sleep)
    except DailyAllowanceExhausted as exc:
        log.update(status="daily_allowance_exhausted",
                   rate_limit_headers=exc.rate_limit_headers,
                   wall_time_s=round(time.monotonic() - started, 3))
        _write_log(out_path, log)
        raise
    except Exception as exc:
        log.update(status="api_error", error=f"{type(exc).__name__}: {exc}",
                   rate_limit_headers=rate_limit_headers(_error_headers(exc)),
                   wall_time_s=round(time.monotonic() - started, 3))
        _write_log(out_path, log)
        raise

    message = response.choices[0].message
    text = message.content or ""
    code = extract_code(text)
    if code is not None:
        out_path.write_text(code, encoding='utf-8')
    log.update(
        usage=_usage(response),
        rate_limit_headers=limits,
        wall_time_s=round(time.monotonic() - started, 3),
        status="ok" if code is not None else "no_code",
        reasoning_content=_reasoning_content(message),
        response_text=text,
    )
    _write_log(out_path, log)
    return log
