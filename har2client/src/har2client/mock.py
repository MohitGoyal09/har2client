"""Spec-driven mock API server (Starlette + uvicorn in a thread). See DESIGN.md."""

from __future__ import annotations

import base64
import json
import random
import re
import socket
import threading
import time
from typing import Any
from urllib.parse import unquote

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from har2client.datagen import generate
from har2client.ops import NO_BODY_STATUSES

STARTUP_TIMEOUT_S = 10.0
SHUTDOWN_TIMEOUT_S = 10.0
_PARAM = re.compile(r"\{([^}]+)\}")


def generate_response(op: dict, seed: int | str, n: int) -> Any:
    """Body for the n-th 2xx call (0-based) to `op` on an instance with `seed`."""
    schema = op["response"]["schema"]
    if schema is None or op["response"]["status"] in NO_BODY_STATUSES:
        return None
    return generate(schema, random.Random(f"{seed}:{op['op_id']}:{n}"))


class _Route:
    """One op path template, matched segment by segment on the percent-decoded value."""

    def __init__(self, op: dict):
        self.op = op
        self.segments = op["path"].split("/")
        self.patterns: list[tuple[re.Pattern, list[str]]] = []
        for seg in self.segments:
            names = _PARAM.findall(seg)
            regex = ""
            for part in re.split(r"(\{[^}]+\})", seg):
                regex += "(.+)" if _PARAM.fullmatch(part) else re.escape(part)
            self.patterns.append((re.compile(f"^{regex}$"), names))
        self.params = sum(len(names) for _, names in self.patterns)
        self.literal_chars = sum(len(_PARAM.sub("", s)) for s in self.segments)

    def match(self, raw_segments: list[str]) -> dict[str, str] | None:
        if len(raw_segments) != len(self.segments):
            return None
        found: dict[str, str] = {}
        for (pattern, names), raw in zip(self.patterns, raw_segments):
            m = pattern.match(unquote(raw))
            if not m:
                return None
            found.update(zip(names, m.groups()))
        return found

    @property
    def specificity(self) -> tuple[int, int]:
        return (self.params, -self.literal_chars)


class MockServer:
    def __init__(self, op_table: dict, seed: int | str, secret: str):
        self.op_table = op_table
        self.seed = seed
        self.secret = secret
        self.log: list[dict] = []
        self.base_path = op_table.get("base_path", "").rstrip("/")
        self._routes = [_Route(op) for op in op_table["operations"]]
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._app = Starlette(
            routes=[Route("/{path:path}", self._handle, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])]
        )

    # ------------------------------------------------------------ lifecycle
    def start(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = uvicorn.Config(
            self._app, log_level="warning", access_log=False, lifespan="off", timeout_graceful_shutdown=2
        )
        self._server = uvicorn.Server(config)
        self._sock = sock
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [sock]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                self.stop()  # do not leak the socket or a half-started thread
                raise RuntimeError("mock server failed to start")
            time.sleep(0.01)
        return f"http://127.0.0.1:{port}{self.base_path}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=SHUTDOWN_TIMEOUT_S)
        if self._sock is not None:
            self._sock.close()
        self._server = self._thread = self._sock = None

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------ handling
    def _relative(self, raw_path: str) -> str:
        """Path without the base path; unchanged when it is not under it."""
        base = self.base_path
        if base and (raw_path == base or raw_path.startswith(base + "/")):
            return raw_path[len(base):] or "/"
        return raw_path

    def _route(self, method: str, raw_path: str) -> tuple[_Route, dict[str, str]] | None:
        base = self.base_path
        if base and raw_path != base and not raw_path.startswith(base + "/"):
            return None
        segments = self._relative(raw_path).split("/")
        hits = []
        for route in self._routes:
            if route.op["method"] != method:
                continue
            params = route.match(segments)
            if params is not None:
                hits.append((route, params))
        if not hits:
            return None
        return min(hits, key=lambda h: h[0].specificity)

    def _authorized(self, request: Request) -> bool:
        sec = self.op_table["security"]
        kind = sec["type"]
        if kind == "none":
            return True
        if kind == "apiKey":
            if sec["in"] == "query":
                return request.query_params.get(sec["name"]) == self.secret
            return request.headers.get(sec["name"]) == self.secret
        header = request.headers.get("authorization")
        if kind == "bearer":
            return header == f"Bearer {self.secret}"
        if kind == "basic":
            return header == "Basic " + base64.b64encode(self.secret.encode()).decode()
        return False

    @staticmethod
    def _parse_body(raw: bytes) -> tuple[Any, bool]:
        """(parsed value or raw text or None, is_valid_json)."""
        if not raw:
            return None, False
        try:
            return json.loads(raw), True
        except ValueError:
            return raw.decode("utf-8", errors="replace"), False

    async def _handle(self, request: Request) -> Response:
        raw_path = request.scope.get("raw_path")
        raw_path = raw_path.decode("latin-1") if raw_path else request.url.path
        raw_path = raw_path.split("?", 1)[0]
        query: dict[str, list[str]] = {}
        for key, value in request.query_params.multi_items():
            query.setdefault(key, []).append(value)
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            headers[key] = f"{headers[key]}, {value}" if key in headers else value
        body, is_json = self._parse_body(await request.body())

        entry: dict[str, Any] = {
            "method": request.method,
            "path": self._relative(raw_path),
            "query": query,
            "headers": headers,
            "body": body,
            "op_id": None,
            "path_params": {},
            "status": 404,
            "response": None,
            # Same high-resolution cross-process clock as runner's call windows.
            "ts": time.perf_counter(),
        }
        status, payload = self._decide(request, entry, raw_path, query, body, is_json)
        entry["status"], entry["response"] = status, payload
        self.log.append(entry)  # logged before the client sees the response
        if payload is None:
            return Response(status_code=status)
        resp = JSONResponse(payload, status_code=status)
        if status == 401:
            resp.headers["WWW-Authenticate"] = "Bearer"
        return resp

    def _decide(self, request: Request, entry: dict, raw_path: str, query: dict, body: Any, is_json: bool):
        hit = self._route(request.method, raw_path)
        if hit is None:
            return 404, {"error": "not found"}
        route, path_params = hit
        op = route.op
        entry["op_id"], entry["path_params"] = op["op_id"], path_params
        if op["auth_required"] and not self._authorized(request):
            return 401, {"error": "unauthorized"}
        for param in op["params"]:
            if param["required"] and param["in"] == "query" and param["name"] not in query:
                return 400, {"error": f"missing query parameter {param['name']}"}
        spec_body = op["body"]
        if spec_body:
            if body is None and spec_body["required"]:
                return 400, {"error": "request body required"}
            if body is not None and not is_json:
                return 400, {"error": "request body must be JSON"}
        with self._lock:
            n = self._counts.get(op["op_id"], 0)
            self._counts[op["op_id"]] = n + 1
        status = op["response"]["status"]
        return status, generate_response(op, self.seed, n)
