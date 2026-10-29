"""The Tier B engine: a stateful, configurable fake API (Starlette + uvicorn in a thread, like MockServer)."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import random
import time
import traceback
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from har2client.mock import MockServer
from har2client.tierb import domains
from har2client.tierb.config import switch
from har2client.tierb.spec import Wire, extra_fields

WRITE_METHODS = {"POST", "PATCH", "DELETE"}
CREATE_GAP = timedelta(minutes=13)
Reply = tuple[int, Any, dict[str, str]]
ERROR_DETAILS = ("verbose", "terse")
# `terse` error bodies: the generic code of each verbose error code. Same status codes, no message, no hint about the fix.
TERSE_CODES = {
    "not_found": "not_found", "missing_header": "bad_request", "invalid_body": "bad_request", "validation_failed": "bad_request",
    "invalid_page": "bad_request", "unauthorized": "unauthorized", "invalid_credentials": "unauthorized", "token_expired": "token_invalid",
    "csrf_failed": "forbidden", "precondition_required": "precondition_required", "precondition_failed": "precondition_failed",
    "internal_error": "internal_error", "rate_limited": "too_many_requests", "idempotency_conflict": "unprocessable",
}


def _natural(raw: str) -> int | None:
    """The int of a string of ASCII digits, else None. `str.isdigit()` alone is not enough: it accepts "\u00b2" and
    int() then raises, and a very long digit string also raises, which would turn a bad page value into an unlogged 500."""
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 18 else None


def item_etag(item: dict) -> str:
    return '"' + hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest()[:16] + '"'


class TierBEngine(MockServer):
    """`TierBEngine(spec, seed, credentials)`; `start() -> base_url`, `stop()`, `log` (MockServer entries),
    `store` (id -> item, in server order) and `initial` (a copy of the starting items).
    `error_detail`: `"verbose"` (default) answers errors with a code and a message, `"terse"` with the same status code and a
    generic body `{"error": <generic code>}`; when not given, `spec["error_detail"]` decides (absent: verbose)."""

    def __init__(self, spec: dict, seed: int | str, credentials: dict, n_items: int | None = None, error_detail: str | None = None):
        super().__init__(
            {"operations": [], "base_path": spec["base_path"], "security": {"type": "none"}}, seed, credentials["password"]
        )
        self.spec = spec
        self.error_detail = error_detail or spec.get("error_detail", "verbose")
        if self.error_detail not in ERROR_DETAILS:
            raise ValueError(f"error_detail must be one of {ERROR_DETAILS}, got {self.error_detail!r}")
        self.cfg = spec["config"]
        self.credentials = dict(credentials)
        self.wire = Wire(spec)
        self._extra = extra_fields(spec)
        self.initial = domains.initial_items(spec["domain"], seed, n_items, self._extra)
        self.store: dict[str, dict] = {item["id"]: copy.deepcopy(item) for item in self.initial}
        self._ids = random.Random(f"{seed}:ids")
        self._secrets = random.Random(f"{seed}:secrets")
        self._salt = f"{self._secrets.getrandbits(32):08x}"
        self._clock = datetime.strptime(self.initial[-1]["created_at"], "%Y-%m-%dT%H:%M:%SZ") + CREATE_GAP
        self._sessions: dict[str, dict] = {}
        self._word = "/" + spec["path_word"].strip("/")
        self._item_word = spec["moved"]["prefix"] + self._word if switch(self.cfg, "moved") == "on" else self._word
        self._job_word = "/" + spec["async_create"]["job_word"] if switch(self.cfg, "async_create") == "on" else None
        self._jobs: dict[str, dict] = {}
        self._job_ids = random.Random(f"{seed}:jobs")
        self._refresh_tokens: dict[str, dict] = {}
        self._password_logins = 0
        self._window_start: float | None = None
        self._window_count = 0
        self._idempotent: dict[str, dict] = {}
        self._entry: dict = {}

    # ------------------------------------------------------------ HTTP glue
    async def _handle(self, request: Request) -> Response:
        raw_path = request.scope.get("raw_path")
        raw_path = (raw_path.decode("latin-1") if raw_path else request.url.path).split("?", 1)[0]
        query: dict[str, list[str]] = {}
        for key, value in request.query_params.multi_items():
            query.setdefault(key, []).append(value)
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            headers[key] = f"{headers[key]}, {value}" if key in headers else value
        raw = await request.body()
        body, is_json = self._parse_body(raw)
        entry: dict[str, Any] = {
            "method": request.method, "path": self._relative(raw_path), "query": query, "headers": headers,
            "body": body, "op_id": None, "path_params": {}, "status": 404, "response": None, "ts": time.perf_counter(),
        }
        self._entry = entry
        try:
            status, payload, extra = self._decide(request, entry, raw_path, query, raw, body, is_json)
        except Exception:  # noqa: BLE001 - an engine bug must show up in the log (and the grade) as a 500, not vanish
            traceback.print_exc()
            status, payload, extra = self._error(500, "internal_error", "engine error")
        if self.error_detail == "terse" and status >= 400:
            payload = {"error": TERSE_CODES.get(payload["error"], payload["error"])}
        entry["status"], entry["response"] = status, copy.deepcopy(payload)  # later updates must not rewrite the log
        self.log.append(entry)  # logged before the client sees the response
        if payload is None:
            return Response(status_code=status, headers=extra)
        return JSONResponse(payload, status_code=status, headers=extra)

    # ------------------------------------------------------------ routing
    def _match(self, method: str, raw_path: str) -> tuple[str, dict] | None:
        base = self.base_path
        if base and raw_path != base and not raw_path.startswith(base + "/"):
            return None
        rel = self._relative(raw_path)
        if self.cfg["auth"] != "apikey_header" and rel == self.spec["login"]["path"]:
            return ("login", {}) if method == "POST" else None
        if switch(self.cfg, "refresh_rotation") == "on" and rel == self.spec["refresh"]["path"]:
            return ("refresh", {}) if method == "POST" else None
        if rel == self._word:
            return {"GET": ("list", {}), "POST": ("create", {})}.get(method)
        if self._job_word and rel.startswith(self._job_word + "/"):
            ident = unquote(rel[len(self._job_word) + 1:])
            return ("job", {"id": ident}) if method == "GET" and ident and "/" not in ident else None
        for word, old in ((self._item_word, False), (self._word, True)):
            prefix = word + "/"
            if rel.startswith(prefix) and (not old or self._item_word != self._word):  # the old item path of a moved API
                ident = unquote(rel[len(prefix):])
                if ident and "/" not in ident:
                    op = {"GET": "get", "PATCH": "update", "DELETE": "delete"}.get(method)
                    return (op, {"id": ident, **({"old_path": True} if old else {})}) if op else None
        return None

    # ------------------------------------------------------------ envelope and errors
    @staticmethod
    def _error(status: int, code: str, message: str, extra: dict | None = None) -> Reply:
        return status, {"error": code, "message": message}, extra or {}

    def _ok(self, status: int, data: Any, meta: dict | None = None, extra: dict | None = None) -> Reply:
        if self.cfg["envelope"] == "on":
            data = {"data": data, "meta": {"request_id": f"req_{len(self.log) + 1:06d}", **(meta or {})}}
        return status, data, extra or {}

    # ------------------------------------------------------------ decision
    def _decide(self, request: Request, entry: dict, raw_path: str, query: dict, raw: bytes, body: Any, is_json: bool) -> Reply:
        hit = self._match(request.method, raw_path)
        if hit is None:
            return self._error(404, "not_found", "no such route")
        op, params = hit
        old_path = params.pop("old_path", False)
        entry["op_id"], entry["path_params"] = op, params
        if switch(self.cfg, "rate_limit") == "on":
            limited = self._rate_limited()
            if limited:
                return limited
        hidden = self.spec["hidden_header"]
        if hidden and request.headers.get(hidden["name"].lower()) != hidden["value"]:
            return self._error(400, "missing_header", f"header {hidden['name']} is required")
        if old_path:  # an item path of the API before it moved
            return 308, None, {"Location": f"{self.base_path}{self._item_word}/{quote(params['id'], safe='')}"}
        if op == "login":
            return self._login(request, raw, body, is_json)
        if op == "refresh":
            return self._refresh(body, is_json)
        session = {}
        if self.cfg["auth"] != "apikey_header":
            session, failure = self._session(request)
            if failure:
                return failure
        elif request.headers.get(self.spec["api_key_header"].lower()) != self.credentials["password"]:
            return self._error(401, "unauthorized", "missing or invalid API key")
        if self.cfg["auth"] == "cookie_csrf" and request.method in WRITE_METHODS:
            if request.headers.get(self.spec["csrf"]["header"].lower()) != session["csrf"]:
                return self._error(403, "csrf_failed", "missing or invalid CSRF token")
        if switch(self.cfg, "idempotency_key") == "on" and request.method in WRITE_METHODS:
            return self._idempotent_write(request, entry, raw_path, query, params, body, is_json)
        return getattr(self, f"_op_{op}")(request, query, params, body, is_json)

    # ------------------------------------------------------------ protocol switches
    def _rate_limited(self) -> Reply | None:
        """429 when more than `limit` requests came in one fixed window; rejected requests are not counted."""
        rule, now = self.spec["rate_limit"], time.monotonic()
        if self._window_start is None or now - self._window_start >= rule["window_s"]:
            self._window_start, self._window_count = now, 0
        if self._window_count >= rule["limit"]:
            return self._error(429, "rate_limited", "too many requests, retry later", {"Retry-After": str(rule["retry_after"])})
        self._window_count += 1
        return None

    def _idempotent_write(self, request: Request, entry: dict, raw_path: str, query: dict, params: dict, body: Any, is_json: bool) -> Reply:
        """A write needs a unique key; the same key with the same request replays the first answer (no second write)."""
        name = self.spec["idempotency"]["header"]
        key = request.headers.get(name.lower())
        if not key:
            return self._error(400, "missing_header", f"header {name} is required on writes")
        fingerprint = [request.method, self._relative(raw_path), body]
        known = self._idempotent.get(key)
        if known is not None:
            if known["fingerprint"] != fingerprint:
                return self._error(422, "idempotency_conflict", f"{name} was already used for a different request")
            entry["replay"] = True
            status, payload, extra = known["reply"]
            return status, copy.deepcopy(payload), {**extra, "Idempotent-Replay": "true"}
        reply = getattr(self, f"_op_{entry['op_id']}")(request, query, params, body, is_json)
        if reply[0] < 300:
            self._idempotent[key] = {"fingerprint": fingerprint, "reply": (reply[0], copy.deepcopy(reply[1]), dict(reply[2]))}
        return reply

    def _w(self, data: Any) -> Any:
        """Item or list of items in wire form."""
        return [self.wire.item(i) for i in data] if isinstance(data, list) else self.wire.item(data)

    # ------------------------------------------------------------ auth
    def _login(self, request: Request, raw: bytes, body: Any, is_json: bool) -> Reply:
        login = self.spec["login"]
        if login["format"] == "json":
            if not is_json or not isinstance(body, dict):
                return self._error(400, "invalid_body", "JSON body required")
            fields = body
        else:
            if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
                return self._error(400, "invalid_body", "form-encoded body required")
            fields = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True).items()}
        if fields.get(login["user_field"]) != self.credentials["username"] or fields.get(login["pass_field"]) != self.credentials["password"]:
            return self._error(401, "invalid_credentials", "wrong username or password")
        if switch(self.cfg, "refresh_rotation") == "on":
            if self._password_logins:  # one password login per account: the next token comes from the refresh call
                return self._error(429, "rate_limited", "login limit reached, use the refresh token")
            self._password_logins += 1
            return self._ok(200, self._issue_pair())
        token = f"{'tok' if self.cfg['auth'] == 'bearer_login' else 'sess'}_{self._secrets.getrandbits(96):024x}"
        record = {"uses": 0, "csrf": f"{self._secrets.getrandbits(64):016x}"}
        self._sessions[token] = record
        if self.cfg["auth"] == "bearer_login":
            return self._ok(200, {login["token_field"]: token, "token_type": "Bearer", "expires_in": 3600})
        csrf = self.spec["csrf"]
        cookie = {"Set-Cookie": f"{csrf['cookie']}={token}; HttpOnly; Path=/; SameSite=Lax"}
        data: dict[str, Any] = {"user": self.credentials["username"]}
        if csrf["in"] == "header":
            cookie[csrf["header"]] = record["csrf"]
        else:
            data[csrf["field"]] = record["csrf"]
        return self._ok(200, data, extra=cookie)

    def _issue_pair(self) -> dict:
        """A new access token and refresh token (`refresh_rotation`); the access token lives `refresh.limit` calls."""
        access = f"tok_{self._secrets.getrandbits(96):024x}"
        refresh = f"rtk_{self._secrets.getrandbits(96):024x}"
        self._sessions[access] = {"uses": 0, "csrf": ""}
        self._refresh_tokens[refresh] = {"access": access}
        return {self.spec["login"]["token_field"]: access, self.spec["refresh"]["field"]: refresh, "token_type": "Bearer", "expires_in": 3600}

    def _refresh(self, body: Any, is_json: bool) -> Reply:
        """Rotation: a refresh token works once; it and the old access token stop working."""
        if not is_json or not isinstance(body, dict):
            return self._error(400, "invalid_body", "JSON body required")
        token = body.get(self.spec["refresh"]["field"])
        record = self._refresh_tokens.pop(token, None) if isinstance(token, str) else None
        if record is None:
            return self._error(401, "invalid_credentials", "unknown or already used refresh token")
        self._sessions.pop(record["access"], None)
        return self._ok(200, self._issue_pair())

    def _session(self, request: Request) -> tuple[dict, Reply | None]:
        """(session record, None) when authenticated, else ({}, the 401 reply)."""
        challenge = {"WWW-Authenticate": "Bearer"} if self.cfg["auth"] == "bearer_login" else {}
        if self.cfg["auth"] == "bearer_login":
            header = request.headers.get("authorization", "")
            token = header[len("Bearer "):] if header.startswith("Bearer ") else None
        else:
            token = request.cookies.get(self.spec["csrf"]["cookie"])
        record = self._sessions.get(token) if token else None
        if record is None:
            return {}, self._error(401, "unauthorized", "not logged in", challenge)
        limit = self.spec["refresh"]["limit"] if switch(self.cfg, "refresh_rotation") == "on" else self.spec["expiry_limit"]
        if self.cfg["token_expiry"] == "on" or switch(self.cfg, "refresh_rotation") == "on":
            record["uses"] += 1
            if record["uses"] > limit:
                return {}, self._error(401, "token_expired", "the session expired, log in again", challenge)
        return record, None

    # ------------------------------------------------------------ list and pagination
    def _op_list(self, request: Request, query: dict, params: dict, body: Any, is_json: bool) -> Reply:
        field = self.spec["filter"]
        filter_param = self.wire.filter_param
        raw_filter = query.get(filter_param, [None])[0]
        value = self.wire.filter_value(raw_filter)
        matching = [item for item in self.store.values() if value is None or item[field] == value]
        style = self.cfg["pagination"]
        if style == "none":
            return self._ok(200, self._w(matching))
        size = self.spec["page"]["size"]
        param = self.spec["page"]["param"]
        raw_value = query.get(param, [None])[0]
        if style == "link_header":
            return self._link_page(request, matching, filter_param, raw_filter, param, raw_value, size)
        offset = self._parse_offset(raw_value) if style == "offset" else self._parse_cursor(raw_value)
        if offset is None:
            return self._error(400, "invalid_page", f"bad {param} value")
        page = self._w(matching[offset:offset + size])
        if style == "offset":
            extra = {"total": len(matching), "offset": offset, "limit": size}
        else:
            more = offset + size < len(matching)
            extra = {self.spec["page"]["next_key"]: self._cursor(offset + size) if more else None}
        if self.cfg["envelope"] == "on":
            return self._ok(200, page, extra)
        return 200, {self.spec["items_key"]: page, **extra}, {}

    @staticmethod
    def _parse_offset(raw: str | None) -> int | None:
        if raw is None:
            return 0
        return _natural(raw)

    def _cursor(self, offset: int) -> str:
        return base64.urlsafe_b64encode(f"{self._salt}:{offset}".encode()).decode().rstrip("=")

    def _parse_cursor(self, raw: str | None) -> int | None:
        if raw is None:
            return 0
        try:
            salt, offset = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode().split(":")
            return int(offset) if salt == self._salt and offset.isdigit() else None
        except ValueError:
            return None

    def _link_page(self, request: Request, matching: list, field: str, value: str | None, param: str, raw: str | None, size: int) -> Reply:
        number = 1 if raw is None else _natural(raw)
        if number is None or number < 1:
            return self._error(400, "invalid_page", f"bad {param} value")
        pages = max(1, -(-len(matching) // size))
        origin = f"{request.url.scheme}://{request.headers['host']}{self.base_path}{self._word}"

        def url(n: int) -> str:
            pairs = ([(field, value)] if value is not None else []) + [(param, str(n))]
            return f"{origin}?{urlencode(pairs)}"

        links = []
        if number > 1:
            links += [(1, "first"), (number - 1, "prev")]
        if number < pages:
            links += [(number + 1, "next"), (pages, "last")]
        header = ", ".join(f'<{url(n)}>; rel="{rel}"' for n, rel in links)
        page = self._w(matching[(number - 1) * size:number * size])
        return self._ok(200, page, extra={"Link": header} if header else {})

    # ------------------------------------------------------------ item operations
    def _etag_headers(self, item: dict) -> dict[str, str]:
        return {"ETag": item_etag(item)} if self.cfg["etag_update"] == "on" else {}

    def _op_get(self, request: Request, query: dict, params: dict, body: Any, is_json: bool) -> Reply:
        item = self.store.get(params["id"])
        if item is None:
            return self._error(404, "not_found", "no such item")
        return self._ok(200, self._w(item), extra=self._etag_headers(item))

    def _op_create(self, request: Request, query: dict, params: dict, body: Any, is_json: bool) -> Reply:
        fields = domains.writable_fields(self.spec["domain"], self._extra)
        if not is_json or not isinstance(body, dict):
            return self._error(400, "invalid_body", "JSON object required")
        canonical, problem = self.wire.body(body)
        if problem:
            return self._error(400, "validation_failed", problem)
        missing = [f for f in fields if f not in canonical]
        if missing:
            return self._error(400, "validation_failed", "missing fields: " + ", ".join(self.wire.wire_keys(missing)))
        if self._job_word:
            job_id = f"job_{self._job_ids.getrandbits(48):012x}"
            self._jobs[job_id] = {"fields": {f: canonical[f] for f in fields}, "polls": 0, "item": None}
            location = f"{self.base_path}{self._job_word}/{job_id}"
            return self._ok(202, {"job_id": job_id, "status": "pending"}, extra={"Location": location})
        item = self._new_item({f: canonical[f] for f in fields})
        return self._ok(201, self._w(item), extra={"Location": self._item_location(item), **self._etag_headers(item)})

    def _new_item(self, fields: dict) -> dict:
        item_id = domains.make_id(self.spec["domain"], self._ids)
        while item_id in self.store:
            item_id = domains.make_id(self.spec["domain"], self._ids)
        self._clock += CREATE_GAP
        item = {"id": item_id, "created_at": self._clock.strftime("%Y-%m-%dT%H:%M:%SZ"), **fields}
        self.store[item_id] = item
        self._entry["created_id"] = item_id  # for the grader: which item this request created
        return item

    def _item_location(self, item: dict) -> str:
        return f"{self.base_path}{self._item_word}/{quote(item['id'], safe='')}"

    def _op_job(self, request: Request, query: dict, params: dict, body: Any, is_json: bool) -> Reply:
        """`async_create`: pending for `pending_polls` GETs, then done; the item is created by the poll that finds it done."""
        job = self._jobs.get(params["id"])
        if job is None:
            return self._error(404, "not_found", "no such job")
        job["polls"] += 1
        if job["polls"] <= self.spec["async_create"]["pending_polls"]:
            return self._ok(200, {"job_id": params["id"], "status": "pending"})
        if job["item"] is None:
            job["item"] = self._new_item(job["fields"])
        return self._ok(200, {"job_id": params["id"], "status": "done", "result": self._w(job["item"])}, extra={"Location": self._item_location(job["item"])})

    def _op_update(self, request: Request, query: dict, params: dict, body: Any, is_json: bool) -> Reply:
        item = self.store.get(params["id"])
        if item is None:
            return self._error(404, "not_found", "no such item")
        if self.cfg["etag_update"] == "on":
            match = request.headers.get("if-match")
            if match is None:
                return self._error(428, "precondition_required", "If-Match header is required; GET the item for its ETag")
            if match != item_etag(item):
                return self._error(412, "precondition_failed", "the item changed; GET it again")
        if not is_json or not isinstance(body, dict):
            return self._error(400, "invalid_body", "JSON object required")
        canonical, problem = self.wire.body(body)
        if problem:
            return self._error(400, "validation_failed", problem)
        item.update({k: v for k, v in canonical.items() if k in domains.writable_fields(self.spec["domain"], self._extra)})
        return self._ok(200, self._w(item), extra=self._etag_headers(item))

    def _op_delete(self, request: Request, query: dict, params: dict, body: Any, is_json: bool) -> Reply:
        if self.store.pop(params["id"], None) is None:
            return self._error(404, "not_found", "no such item")
        return 204, None, {}
