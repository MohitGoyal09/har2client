#!/usr/bin/env python3
"""A tiny fake OpenAI-compatible server (Starlette) that answers with a canned reference client.

For dry runs of the baseline plumbing without a real model:
  - `solve/openai_compat.py` and `scripts/run_batch.py --solver openai_compat`
  - `scripts/run_rae.py` (reverse-api-engineer through OpenCode), which needs tool calls.

Reply logic, for each POST /v1/chat/completions:
  1. Pick the reference client: the `ref_client.py` of the task whose `goal.md` text occurs in the
     request messages (`--tasks DIR`), or `--client FILE` for every request.
  2. No `tools` in the request: reply with that code in one ```python block (what openai_compat expects).
  3. `tools` present (an agent such as OpenCode): first turn, call the `write` tool to save the code to
     `<output_dir>/api_client.py` (the directory RAE names in its prompt); after the tool result, reply "done".
`stream: true` gets Server-Sent Events. `--fail 429,429` makes the first requests fail with those statuses
(with `Retry-After: 0`) to exercise retries. The key is never checked and never logged.

  uv run python scripts/fake_openai_server.py --tasks runs/tierb50 --port 8099
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

MODEL_ID = "fake-model"
OUTPUT_DIR_RE = re.compile(r"<output_dir>\s*(.*?)\s*</output_dir>", re.DOTALL)
WRITE_TOOL_NAMES = ("write", "Write", "write_file")


def message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def all_text(body: dict) -> str:
    return "\n".join(message_text(m) for m in body.get("messages", []))


def _har_urls(task: Path) -> set[str]:
    try:
        har = json.loads((task / "session.har").read_text(encoding="utf-8"))
        return {e["request"]["url"] for e in har["log"]["entries"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def pick_reference(body: dict, tasks_dir: Path | None, client_file: Path | None) -> str:
    """Code of the reference client for this request.

    Goals can be identical across tasks, so: a task whose RAE run id (`h2c_<task>`) is in the request wins;
    otherwise, among the tasks whose goal is in the request, the one whose recorded URLs show up most often
    wins (works for raw and compact HAR text).
    """
    if client_file is not None:
        return client_file.read_text(encoding="utf-8")
    text = all_text(body)
    tasks = sorted(Path(tasks_dir).iterdir()) if tasks_dir is not None else []
    for task in tasks:  # RAE names the run `h2c_<task>` and puts that in the HAR path of its prompt
        if (task / "ref_client.py").is_file() and re.search(rf"/har/h2c_{re.escape(task.name)}/", text):
            return (task / "ref_client.py").read_text(encoding="utf-8")
    best: tuple[int, Path] | None = None
    for task in tasks:
        goal, ref = task / "goal.md", task / "ref_client.py"
        if not (goal.is_file() and ref.is_file() and goal.read_text(encoding="utf-8").strip() in text):
            continue
        score = sum(1 for url in _har_urls(task) if url in text)
        if best is None or score > best[0]:
            best = (score, ref)
    return best[1].read_text(encoding="utf-8") if best else "class Client:\n    pass\n"


def write_tool(body: dict) -> dict | None:
    for tool in body.get("tools") or []:
        fn = tool.get("function", {})
        if fn.get("name") in WRITE_TOOL_NAMES:
            return fn
    return None


def write_args(fn: dict, path: str, content: str) -> dict:
    """Arguments for the `write` tool, using whichever parameter names its schema declares."""
    props = (fn.get("parameters") or {}).get("properties") or {}
    path_key = next((k for k in ("filePath", "file_path", "path") if k in props), "filePath")
    content_key = next((k for k in ("content", "contents", "text") if k in props), "content")
    return {path_key: path, content_key: content}


def plan_reply(body: dict, tasks_dir: Path | None, client_file: Path | None) -> dict:
    """{"content": str} or {"tool_call": {"name", "arguments"}} for this request."""
    code = pick_reference(body, tasks_dir, client_file)
    fn = write_tool(body)
    if fn is None:
        return {"content": f"Here is the client.\n\n```python\n{code}```\n"}
    if any(m.get("role") == "tool" for m in body.get("messages", [])):
        return {"content": "Wrote api_client.py. It follows the goal's Client interface."}
    match = OUTPUT_DIR_RE.search(all_text(body))
    out_dir = match.group(1) if match else "."
    return {"tool_call": {"name": fn["name"], "arguments": write_args(fn, f"{out_dir}/api_client.py", code)}}


def _usage(body: dict, reply: dict) -> dict:
    prompt = len(all_text(body)) // 4
    completion = len(json.dumps(reply)) // 4
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def completion_json(body: dict, reply: dict) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": reply.get("content")}
    finish = "stop"
    if "tool_call" in reply:
        call = reply["tool_call"]
        message["tool_calls"] = [{
            "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
        }]
        finish = "tool_calls"
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
        "model": body.get("model", MODEL_ID),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _usage(body, reply),
    }


def completion_sse(body: dict, reply: dict) -> Iterator[str]:
    base = {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": body.get("model", MODEL_ID)}

    def chunk(delta: dict, finish: str | None = None, **extra: Any) -> str:
        data = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
        return f"data: {json.dumps(data)}\n\n"

    yield chunk({"role": "assistant", "content": ""})
    if "tool_call" in reply:
        call = reply["tool_call"]
        yield chunk({"tool_calls": [{
            "index": 0, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
        }]})
        finish = "tool_calls"
    else:
        yield chunk({"content": reply["content"]})
        finish = "stop"
    yield chunk({}, finish)
    if (body.get("stream_options") or {}).get("include_usage"):
        yield f"data: {json.dumps({**base, 'choices': [], 'usage': _usage(body, reply)})}\n\n"
    yield "data: [DONE]\n\n"


def request_summary(body: dict) -> dict:
    """The parts of a request worth checking in a log (no message text, so no HAR content)."""
    return {
        "model": body.get("model"),
        "reasoning_effort": body.get("reasoning_effort"),
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
        "stream": bool(body.get("stream")),
        "n_messages": len(body.get("messages", [])),
        "tools": [t.get("function", {}).get("name") for t in body.get("tools") or []],
        "prompt_chars": len(all_text(body)),
    }


class FakeState:
    """What the app remembers: every request body (key and headers excluded) and the failures still to inject."""

    def __init__(self, tasks_dir: Path | None, client_file: Path | None, fail: list[int], log_path: Path | None = None):
        self.tasks_dir, self.client_file, self.log_path = tasks_dir, client_file, log_path
        self.fail = list(fail)
        self.requests: list[dict] = []
        self.lock = threading.Lock()


def make_app(state: FakeState) -> Starlette:
    async def chat(request: Request) -> Response:
        body = await request.json()
        with state.lock:
            state.requests.append(body)
            if state.log_path is not None:
                with open(state.log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(request_summary(body)) + "\n")
            status = state.fail.pop(0) if state.fail else None
        if status:
            return JSONResponse({"error": {"message": f"injected {status}"}}, status_code=status,
                                headers={"Retry-After": "0"})
        reply = plan_reply(body, state.tasks_dir, state.client_file)
        if body.get("stream"):
            return StreamingResponse(completion_sse(body, reply), media_type="text/event-stream")
        return JSONResponse(completion_json(body, reply))

    async def models(_request: Request) -> Response:
        return JSONResponse({"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "fake"}]})

    return Starlette(routes=[
        Route("/v1/chat/completions", chat, methods=["POST"]),
        Route("/v1/models", models, methods=["GET"]),
    ])


@contextmanager
def serve(state: FakeState, port: int = 0) -> Iterator[str]:
    """Run the app in a background thread; yields its base URL (`http://127.0.0.1:PORT/v1`)."""
    config = uvicorn.Config(make_app(state), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("fake server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tasks", type=Path, help="directory of task dirs; replies with the matching ref_client.py")
    p.add_argument("--client", type=Path, help="one client file used for every request")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--log", type=Path, help="append one JSON line per request (model, reasoning_effort, tools; no message text)")
    p.add_argument("--fail", default="", help="comma-separated statuses to return for the first requests, e.g. 429,503")
    args = p.parse_args(argv)
    if not args.tasks and not args.client:
        p.error("give --tasks or --client")
    state = FakeState(args.tasks, args.client, [int(s) for s in args.fail.split(",") if s], args.log)
    print(f"fake OpenAI-compatible server on http://127.0.0.1:{args.port}/v1 (Ctrl-C to stop)")
    uvicorn.run(make_app(state), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
