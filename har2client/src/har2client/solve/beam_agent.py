"""Scientist agent on Beam: probe a practice server, then write `client.py` (DESIGN-CASES.md, "Case 2").

Multi-turn OpenAI-style tool calling. Tools: `http_request` (against the practice instance) and `submit_client`
(ends the episode). Every assistant turn's `reasoning_content` is sent back in the next request. Max turns =
budget + 3. A budget of 0 is the placebo arm: same prompt text, no tool offered, one turn.
Retry and daily-limit rules are those of `beam.py` (its functions are reused).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from har2client.env.probe import PracticeInstance, plan_practice
from har2client.solve import beam
from har2client.solve.agent_prompt import TOOLS, SUBMIT_CLIENT_TOOL, render_prompts

EXTRA_TURNS = 3
NUDGE = "Call `submit_client` with the complete `client.py`."


def max_turns(budget: int) -> int:
    return 1 if budget == 0 else budget + EXTRA_TURNS


def _turn_usage(response: Any) -> dict:
    usage = beam._usage(response)
    raw = getattr(response, "usage", None)
    details = getattr(raw, "prompt_tokens_details", None)
    usage["cached_tokens"] = getattr(details, "cached_tokens", None) if details is not None else None
    return usage


def _sum(turns: list[dict], key: str) -> int | None:
    values = [t["usage"].get(key) for t in turns if t["usage"].get(key) is not None]
    return sum(values) if values else None


def total_usage(turns: list[dict]) -> dict:
    usage = {k: _sum(turns, k) for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens")}
    prompt, cached = usage["prompt_tokens"], usage["cached_tokens"]
    usage["cached_share"] = round(cached / prompt, 4) if prompt and cached is not None else None
    return usage


def _assistant_message(message: Any) -> dict:
    """The assistant turn as a request message: content, tool calls and its `reasoning_content`."""
    out: dict[str, Any] = {"role": "assistant", "content": message.content}
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        out["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls
        ]
    reasoning = beam._reasoning_content(message)
    if reasoning is not None:
        out["reasoning_content"] = reasoning
    return out


def _tool_result(content: dict) -> str:
    return json.dumps(content)


def _run_tool(call: Any, practice: PracticeInstance | None) -> tuple[dict, str | None]:
    """(result for the model, submitted code or None) for one tool call."""
    name = call.function.name
    try:
        args = json.loads(call.function.arguments or "{}")
    except ValueError:
        return {"error": "invalid_arguments", "message": "arguments are not valid JSON"}, None
    if not isinstance(args, dict):
        return {"error": "invalid_arguments", "message": "arguments must be a JSON object"}, None
    if name == "submit_client":
        code = args.get("code")
        if isinstance(code, str) and code.strip():
            return {"status": "submitted"}, code
        return {"error": "invalid_arguments", "message": "code must be a non-empty string"}, None
    if name == "http_request" and practice is not None:
        return practice.request(args), None
    return {"error": "unknown_tool", "message": f"no tool named {name!r}"}, None


def _log_turn(index: int, response: Any, message: Any, results: list[dict], calls: list[Any]) -> dict:
    return {
        "turn": index,
        "usage": _turn_usage(response),
        "reasoning_content": beam._reasoning_content(message),
        "content": message.content,
        "tool_calls": [
            {"name": c.function.name, "arguments": c.function.arguments, "result": r} for c, r in zip(calls, results)
        ],
    }


def solve_agent(
    task_dir: Path,
    out_path: Path,
    effort: str = "high",
    har_mode: str = "raw",
    *,
    budget: int,
    client: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    practice: PracticeInstance | None = None,
    request_options: dict | None = None,
    call_fn: Callable | None = None,
    submission_tool_all_arms: bool = False,
) -> dict:
    """Run one episode. Writes `client.py` (when submitted), `solve_log.json` and `probes.jsonl` next to `out_path`."""
    if effort not in beam.EFFORTS:
        raise ValueError(f"effort must be one of {beam.EFFORTS}, got {effort!r}")
    if har_mode not in beam.HAR_MODES:
        raise ValueError(f"har_mode must be one of {beam.HAR_MODES}, got {har_mode!r}")
    if budget < 0:
        raise ValueError("budget must be >= 0")
    task_dir, out_path = Path(task_dir), Path(out_path)
    client = client if client is not None else beam.make_client()
    goal = beam._read_visible(task_dir, "goal.md")
    session_name = beam.find_session_file(task_dir)
    session_text = beam._read_visible(task_dir, session_name)
    if session_name.endswith(".har"):
        session_text = beam.prepare_har_text(session_text, har_mode)
    plan = practice.plan if practice is not None else plan_practice(task_dir)
    system, user = render_prompts(
        goal, session_text, session_name, base_url=plan.public_url, username=plan.credentials["username"],
        password=plan.credentials["password"], budget=budget,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)  # before the server starts: a failure here must not leak it
    own = practice is None and budget > 0
    if own:
        practice = PracticeInstance(task_dir, budget, plan=plan)
        practice.start()
    log: dict[str, Any] = {
        "model": (request_options or {}).get('model', beam.MODEL), "effort": effort, "har_mode": har_mode, "probe_budget": budget, "max_turns": max_turns(budget),
        "reasoning_effort_sent": (request_options or {}).get('reasoning_effort', effort),
        "enable_thinking_sent": (request_options or {}).get('extra_body', {}).get('enable_thinking'),
        "protocol": 'submission-tool-all-arms-v1' if submission_tool_all_arms else 'legacy',
        "prompt_tokens_estimate": (len(system) + len(user)) // beam.CHARS_PER_TOKEN, "usage": {}, "rate_limit_headers": {},
        "wall_time_s": None, "status": None, "turns": [], "submitted_via": None, "response_text": None,
    }
    started = time.monotonic()
    try:
        code = _episode(client, system, user, effort, budget, practice, log, sleep,
                        request_options=request_options, call_fn=call_fn,
                        submission_tool_all_arms=submission_tool_all_arms)
    except beam.DailyAllowanceExhausted as exc:
        log.update(status="daily_allowance_exhausted", rate_limit_headers=exc.rate_limit_headers)
        _finish(out_path, log, practice, started)
        raise
    except Exception as exc:
        log.update(status="api_error", error=f"{type(exc).__name__}: {exc}", rate_limit_headers=beam.rate_limit_headers(beam._error_headers(exc)))
        _finish(out_path, log, practice, started)
        raise
    finally:
        if own and practice is not None:
            practice.stop()
    if code is not None:
        out_path.write_text(code, encoding='utf-8')
    log["status"] = "ok" if code is not None else ("no_code" if budget == 0 else "no_submit")
    _finish(out_path, log, practice, started)
    return log


def _episode(client: Any, system: str, user: str, effort: str, budget: int, practice: PracticeInstance | None, log: dict, sleep: Callable,
             *, request_options: dict | None = None, call_fn: Callable | None = None,
             submission_tool_all_arms: bool = False) -> str | None:
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for index in range(1, max_turns(budget) + 1):
        request: dict[str, Any] = {
            "model": beam.MODEL, "messages": list(messages), "reasoning_effort": effort,
            "max_completion_tokens": beam.MAX_COMPLETION_TOKENS,
        }
        if budget > 0:
            request["tools"] = TOOLS
        elif submission_tool_all_arms:
            request['tools'] = [SUBMIT_CLIENT_TOOL]
        for key, value in (request_options or {}).items():
            if value is None:
                request.pop(key, None)
            else:
                request[key] = value
        response, limits = (call_fn or beam.call_with_retry)(client, request, sleep=sleep)
        log["rate_limit_headers"] = limits
        message = response.choices[0].message
        calls = list(getattr(message, "tool_calls", None) or [])
        messages.append(_assistant_message(message))
        results, code = [], None
        for call in calls:
            result, submitted = _run_tool(call, practice)
            results.append(result)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": _tool_result(result)})
            if submitted is not None:
                code = submitted
                break  # submit ends the episode; later calls of this turn are not run
        log["turns"].append(_log_turn(index, response, message, results, calls[: len(results)]))
        log["usage"] = total_usage(log["turns"])
        if code is not None:
            log["submitted_via"] = "tool"
            return code
        text_code = beam.extract_code(message.content or "")
        if not calls and text_code is not None:
            log.update(submitted_via="text", response_text=message.content)
            return text_code
        if not calls and budget > 0:
            messages.append({"role": "user", "content": NUDGE})
    return None


def _finish(out_path: Path, log: dict, practice: PracticeInstance | None, started: float) -> None:
    log["wall_time_s"] = round(time.monotonic() - started, 3)
    entries = practice.log if practice is not None else []
    log.update(
        probes_used=practice.probes_used if practice else 0, probes_refused=practice.probes_refused if practice else 0,
        probe_labels=practice.label_counts() if practice else {}, ablated_hidden_header=practice.ablated_hidden_header() if practice else False,
    )
    (out_path.parent / "probes.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    beam._write_log(out_path, log)
