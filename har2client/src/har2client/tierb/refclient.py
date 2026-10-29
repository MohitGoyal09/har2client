"""Generate the gold `client.py` for a Tier B spec, and deliberately broken variants for the validity gate."""

from __future__ import annotations

from pathlib import Path

from har2client.tierb.config import DRIFT_KINDS, switch
from har2client.tierb.spec import TIMESTAMP_FORMAT, method_name

# Each flaw switches off one behaviour. The first six are the Tier B validity-gate flaws; the rest belong to the hard-repair
# switches (`heal` builds a v1 client with them: one that lacks a v2 feature). A flaw only matters when its switch is on.
FLAWS = (
    "ignore_pagination", "no_relogin", "no_if_match", "drop_hidden_header", "return_envelope", "no_csrf",
    *(f"no_drift_{kind}" for kind in DRIFT_KINDS),  # return the wire value as it is (and send the canonical one)
    "no_rate_limit_retry",  # raise on the first 429
    "hammer_rate_limit",  # retry a 429 at once, ignoring Retry-After
    "no_job_poll",  # return the 202 job answer as if it were the item
    "no_refresh",  # log in again on a 401 instead of using the refresh token
    "no_idempotency_key",  # send writes without the key
    "no_moved_path",  # keep using the item path of before the move
)
RETRIES = 10
JOB_POLLS = 30
JOB_POLL_DELAY_S = 0.1

_HEAD = '''"""Reference client generated from the Tier B spec. Uses only httpx and the standard library."""

from __future__ import annotations

import re
{std_imports}from typing import Any
from urllib.parse import quote{urljoin}

import httpx

_NEXT_LINK = re.compile(r'<([^>]+)>\\s*;\\s*rel="?next"?')
{module_helpers}

class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._http = httpx.Client(timeout=30.0, trust_env=False)
        self._token: str | None = None
        self._csrf: str | None = None
{init_extra}
    def close(self) -> None:
        self._http.close()

    def _headers(self, write: bool) -> dict:
        headers: dict = {}
{headers_body}
        return headers
{login}
    def _send(self, method: str, url: str, *, params: Any = None, body: Any = None, headers: dict | None = None) -> httpx.Response:
        write = method != "GET"
{send_body}
        resp.raise_for_status()
        return resp

    def _data(self, resp: httpx.Response) -> Any:
        body = resp.json()
        return {unwrap}

    def _item_path(self, item_id: str) -> str:
        return {path_word} + "/" + quote(item_id, safe="")
'''


def _headers_body(spec: dict, flaws: set[str]) -> str:
    lines = []
    hidden = spec["hidden_header"]
    if hidden and "drop_hidden_header" not in flaws:
        lines.append(f"        headers[{hidden['name']!r}] = {hidden['value']!r}")
    auth = spec["config"]["auth"]
    if auth == "apikey_header":
        lines.append(f"        headers[{spec['api_key_header']!r}] = self._password")
    elif auth == "bearer_login":
        lines += ["        if self._token:", '            headers["Authorization"] = "Bearer " + self._token']
    elif "no_csrf" not in flaws:
        lines += ["        if write and self._csrf:", f"            headers[{spec['csrf']['header']!r}] = self._csrf"]
    return "\n".join(lines) or "        pass"


class _Plan:
    """Which hard-repair behaviours the generated client has: the switches that are on, less the flaws."""

    def __init__(self, spec: dict, flaws: set[str]):
        cfg = spec["config"]
        self.spec, self.flaws = spec, flaws
        self.drift = {k: spec["drift"][k] for k in DRIFT_KINDS if k in switch(cfg, "drift") and f"no_drift_{k}" not in flaws}
        self.rate_limit = switch(cfg, "rate_limit") == "on" and "no_rate_limit_retry" not in flaws
        self.hammer = "hammer_rate_limit" in flaws
        self.async_create = switch(cfg, "async_create") == "on"
        self.poll = self.async_create and "no_job_poll" not in flaws
        self.refresh_mode = switch(cfg, "refresh_rotation") == "on"
        self.refresh = self.refresh_mode and "no_refresh" not in flaws
        self.idempotency = switch(cfg, "idempotency_key") == "on" and "no_idempotency_key" not in flaws
        self.moved = switch(cfg, "moved") == "on" and "no_moved_path" not in flaws
        self.relogin = cfg["auth"] != "apikey_header" and "no_relogin" not in flaws
        self.loop = self.rate_limit or self.refresh_mode  # the send loop is needed to retry more than once
        # request bodies change on the wire for these kinds; `epoch` only changes responses
        self.sends = [k for k in self.drift if k != "epoch"]

    def item(self, expr: str) -> str:
        return f"_from_wire({expr})" if self.drift else expr

    def items(self, expr: str) -> str:
        return f"[_from_wire(i) for i in {expr}]" if self.drift else expr

    def retry_429(self, indent: str) -> list[str]:
        """Lines that wait for Retry-After and go round again (the caller's loop)."""
        lines = [f'{indent}if resp.status_code == 429 and "retry-after" in resp.headers:']
        if not self.hammer:
            lines.append(f'{indent}    time.sleep(float(resp.headers["retry-after"]))')
        return lines + [f"{indent}    continue"]


def _std_imports(plan: _Plan) -> str:
    lines = []
    if plan.rate_limit or plan.poll:
        lines.append("import time")
    if plan.idempotency:
        lines.append("import uuid")
    if "epoch" in plan.drift:
        lines.append("from datetime import datetime, timezone")
    return "".join(line + "\n" for line in lines)


def _enum_to_wire(plan: _Plan, expr: str) -> str:
    enum = plan.drift["enum"]
    return f"{expr}.upper()" if enum["mode"] == "upper" else f"{enum['prefix']!r} + {expr}"


def _init_extra(plan: _Plan) -> str:
    lines = []
    if plan.refresh_mode:
        lines.append("        self._refresh_token: str | None = None")
    if plan.idempotency:
        lines.append("        self._writes = 0")
    return "".join(line + "\n" for line in lines)


def _drift_helpers(plan: _Plan) -> str:
    """Module-level `_from_wire` (response item -> canonical) and `_to_wire` (request body -> wire), one block per kind."""
    d = plan.drift
    if not d:
        return ""
    back = ["    out = dict(item)"]
    if "rename" in d:
        back.append(f"    out[{d['rename']['field']!r}] = out.pop({d['rename']['to']!r})")
    if "cents" in d:
        back.append(f"    out[{d['cents']['field']!r}] = round(out.pop({d['cents']['to']!r}) / 100, 2)")
    if "epoch" in d:
        field = d["epoch"]["field"]
        back.append(f"    out[{field!r}] = datetime.fromtimestamp(out[{field!r}], timezone.utc).strftime({TIMESTAMP_FORMAT!r})")
    if "nest" in d:
        n = d["nest"]
        back += [f"    nested = out.pop({n['key']!r})", f"    out[{n['name_field']!r}] = nested[\"name\"]", f"    out[{n['email_field']!r}] = nested[\"email\"]"]
    if "enum" in d:
        e = d["enum"]
        undo = f"out[{e['field']!r}].lower()" if e["mode"] == "upper" else f"out[{e['field']!r}][{len(e['prefix'])}:]"
        back.append(f"    out[{e['field']!r}] = {undo}")
    out = ["", "", "def _from_wire(item: dict) -> dict:", *back, "    return out"]
    if plan.sends:
        go = ["    out = dict(body)"]
        if "rename" in d:
            r = d["rename"]
            go += [f"    if {r['field']!r} in out:", f"        out[{r['to']!r}] = out.pop({r['field']!r})"]
        if "cents" in d:
            c = d["cents"]
            go += [f"    if {c['field']!r} in out:", f"        out[{c['to']!r}] = round(out.pop({c['field']!r}) * 100)"]
        if "nest" in d:
            n = d["nest"]
            go += ["    nested = {}"]
            for sub, field in (("name", n["name_field"]), ("email", n["email_field"])):
                go += [f"    if {field!r} in out:", f"        nested[{sub!r}] = out.pop({field!r})"]
            go += ["    if nested:", f"        out[{n['key']!r}] = nested"]
        if "enum" in d:
            f = d["enum"]["field"]
            go += [f"    if {f!r} in out:", f"        out[{f!r}] = {_enum_to_wire(plan, f'out[{f!r}]')}"]
        out += ["", "", "def _to_wire(body: dict) -> dict:", *go, "    return out"]
    return "\n".join(out) + "\n"


def _login(plan: _Plan) -> str:
    spec = plan.spec
    auth, login = spec["config"]["auth"], spec["login"]
    if auth == "apikey_header":
        return ""
    send = "json=payload" if login["format"] == "json" else "data=payload"
    post = f"self._http.post(self._base + {login['path']!r}, {send}, headers=self._headers(False))"
    lines = ["", "    def _login(self) -> None:", "        self._token = self._csrf = None"]
    if plan.refresh_mode:
        lines.append("        self._refresh_token = None")
    lines.append(f"        payload = {{{login['user_field']!r}: self._username, {login['pass_field']!r}: self._password}}")
    lines += _post_lines(plan, post)
    lines.append("        body = self._data(resp)")
    if auth == "bearer_login":
        lines.append(f"        self._token = body[{login['token_field']!r}]")
        if plan.refresh_mode:
            lines.append(f"        self._refresh_token = body[{spec['refresh']['field']!r}]")
    elif spec["csrf"]["in"] == "header":
        lines.append(f"        self._csrf = resp.headers[{spec['csrf']['header']!r}]")
    else:
        lines.append(f"        self._csrf = body[{spec['csrf']['field']!r}]")
    return "\n".join(lines) + "\n"


def _post_lines(plan: _Plan, post: str) -> list[str]:
    """`resp = <post>` plus the status check; with `rate_limit` the call is retried after Retry-After."""
    if not plan.rate_limit:
        return [f"        resp = {post}", "        resp.raise_for_status()"]
    return [f"        for _ in range({RETRIES}):", f"            resp = {post}", *plan.retry_429("            "), "            break", "        resp.raise_for_status()"]


def _refresh(plan: _Plan) -> str:
    """`_refresh`: one refresh call (the old pair stops working), a password login only when there is no refresh token yet."""
    if not plan.refresh:
        return ""
    spec = plan.spec
    refresh, token_field = spec["refresh"], spec["login"]["token_field"]
    post = f"self._http.post(self._base + {refresh['path']!r}, json={{{refresh['field']!r}: self._refresh_token}}, headers=self._headers(False))"
    lines = ["", "    def _refresh(self) -> None:", "        if self._refresh_token is None:", "            return self._login()", *_post_lines(plan, post)]
    lines += ["        body = self._data(resp)", f"        self._token = body[{token_field!r}]", f"        self._refresh_token = body[{refresh['field']!r}]"]
    return "\n".join(lines) + "\n"


def _request_lines(indent: str) -> list[str]:
    return [
        f"{indent}resp = self._http.request(",
        f'{indent}    method, url if url.startswith("http") else self._base + url,',
        f"{indent}    params=params or None, json=body, headers={{**self._headers(write), **(headers or {{}})}},",
        f"{indent})",
    ]


def _send_body(plan: _Plan) -> str:
    auth = plan.spec["config"]["auth"]
    lines = []
    if plan.idempotency:
        name = plan.spec["idempotency"]["header"]
        lines += [
            "        if write:",
            "            self._writes += 1  # a key per write, from the account and a counter: unique per account, the same in every run",
            f"            headers = {{**(headers or {{}}), {name!r}: str(uuid.uuid5(uuid.NAMESPACE_URL, f'{{self._username}}/{{self._writes}}'))}}",
        ]
    if auth != "apikey_header":
        guard = "self._token is None" if auth == "bearer_login" else "self._csrf is None"
        lines += [f"        if {guard}:", "            self._login()"]
    recover = "self._refresh()" if plan.refresh else "self._login()"
    if plan.loop:
        lines += ["        recovered = False", f"        for _ in range({RETRIES}):", *_request_lines("            ")]
        if plan.rate_limit:
            lines += plan.retry_429("            ")
        if plan.relogin:
            lines += ["            if resp.status_code == 401 and not recovered:", "                recovered = True", f"                {recover}", "                continue"]
        lines.append("            break")
    elif plan.relogin:
        lines += ["        for attempt in (0, 1):", *_request_lines("            ")]
        lines += ["            if resp.status_code == 401 and attempt == 0:", "                self._login()", "                continue", "            break"]
    else:
        lines += _request_lines("        ")
    return "\n".join(lines)


def _filter_param(plan: _Plan) -> tuple[str, str]:
    """(wire query name, expression of the wire value) of the list filter, given the goal argument named like the field."""
    spec = plan.spec
    field = spec["filter"]
    rename = plan.drift.get("rename")
    name = rename["to"] if rename and rename["field"] == field else field
    enum = plan.drift.get("enum")
    return name, _enum_to_wire(plan, field) if enum and enum["field"] == field else field


def _list_source(plan: _Plan) -> str:
    spec, flaws = plan.spec, plan.flaws
    style, page = spec["config"]["pagination"], spec["page"]
    env = spec["config"]["envelope"] == "on"
    one_page = "ignore_pagination" in flaws
    field = spec["filter"]
    path = "/" + spec["path_word"].strip("/")
    param_name, param_value = _filter_param(plan)
    head = [
        f"    def {method_name(spec, 'list')}(self, {field}: str | None = None) -> list[dict]:",
        "        items: list = []",
        f"        params: dict = {{{param_name!r}: {param_value}}} if {field} is not None else {{}}",
    ]
    items_of = 'body["data"]' if env else f'body[{spec["items_key"]!r}]'
    meta_of = 'body["meta"]' if env else "body"
    if style == "none":
        body = [f"        resp = self._send('GET', {path!r}, params=params)", f"        return {plan.items('self._data(resp)')}"]
    elif style == "link_header":
        body = [
            f"        url: str | None = {path!r}",
            "        while url:",
            "            resp = self._send('GET', url, params=params)",
            f"            items.extend({plan.items('self._data(resp)')})",
            "            params = None",
            '            match = _NEXT_LINK.search(resp.headers.get("link", ""))',
            "            url = match.group(1) if match else None",
        ]
        if one_page:
            body.insert(5, "            break")
        body.append("        return items")
    else:
        param = page["param"]
        first = "0" if style == "offset" else "None"
        body = [f"        position: Any = {first}", "        while True:"]
        body += [
            "            query = dict(params)",
            "            if position:",
            f"                query[{param!r}] = position",
            f"            body = self._send('GET', {path!r}, params=query).json()",
            f"            page = {items_of}",
            f"            items.extend({plan.items('page')})",
        ]
        if one_page:
            body.append("            break")
        elif style == "offset":
            body += [
                "            position += len(page)",
                f'            if not page or position >= {meta_of}["total"]:',
                "                break",
            ]
        else:
            body += [f"            position = {meta_of}[{page['next_key']!r}]", "            if not position:", "                break"]
        body.append("        return items")
    return "\n".join(head + body)


def _job_source(plan: _Plan) -> str:
    """`_await_job`: poll the job an accepted create points to until it is done; its result is the item (wire form)."""
    if not plan.poll:
        return ""
    return "\n".join(
        [
            "    def _await_job(self, resp: httpx.Response) -> dict:",
            '        location = resp.headers["location"]',
            f"        for _ in range({JOB_POLLS}):",
            '            job = self._data(self._send("GET", urljoin(self._base + "/", location)))',
            '            if job["status"] == "done":',
            '                return job["result"]',
            f"            time.sleep({JOB_POLL_DELAY_S})",
            '        raise TimeoutError("the job did not finish")',
        ]
    )


def _item_methods(plan: _Plan) -> str:
    spec, flaws = plan.spec, plan.flaws
    raw = "return resp.json()" if "return_envelope" in flaws else f"return {plan.item('self._data(resp)')}"
    update = [f"    def {method_name(spec, 'update')}(self, id: str, changes: dict) -> dict:", "        headers = None"]
    if spec["config"]["etag_update"] == "on" and "no_if_match" not in flaws:
        update += ["        current = self._send('GET', self._item_path(id))", '        headers = {"If-Match": current.headers["etag"]}']
    changes = "_to_wire(changes)" if plan.sends else "changes"
    update += [f"        resp = self._send('PATCH', self._item_path(id), body={changes}, headers=headers)", f"        {raw}"]
    path = "/" + spec["path_word"].strip("/")
    body = "_to_wire(body)" if plan.sends else "body"
    created = [f"    def {method_name(spec, 'create')}(self, body: dict) -> dict:", f"        resp = self._send('POST', {path!r}, body={body})"]
    if plan.poll:
        created.append(f"        return {plan.item('self._await_job(resp)')}")
    else:
        created.append(f"        {'return self._data(resp)' if plan.async_create else raw}")
    return "\n\n".join(
        [
            "\n".join([f"    def {method_name(spec, 'get')}(self, id: str) -> dict:", "        resp = self._send('GET', self._item_path(id))", f"        {raw}"]),
            "\n".join(created),
            "\n".join(update),
            "\n".join([f"    def {method_name(spec, 'delete')}(self, id: str) -> None:", "        self._send('DELETE', self._item_path(id))", "        return None"]),
        ]
    )


def generate_client_source(spec: dict, flaws: tuple[str, ...] | set[str] = ()) -> str:
    """Source of `client.py` for `spec`. `flaws` (names from FLAWS) switch off one behaviour each."""
    flaws = set(flaws)
    unknown = flaws - set(FLAWS)
    if unknown:
        raise ValueError(f"unknown flaws: {sorted(unknown)}")
    plan = _Plan(spec, flaws)
    item_word = spec["moved"]["prefix"] + "/" + spec["path_word"].strip("/") if plan.moved else "/" + spec["path_word"].strip("/")
    head = (
        _HEAD.replace("{headers_body}", _headers_body(spec, flaws))
        .replace("{login}", _login(plan) + _refresh(plan))
        .replace("{send_body}", _send_body(plan))
        .replace("{unwrap}", 'body["data"]' if spec["config"]["envelope"] == "on" else "body")
        .replace("{path_word}", repr(item_word))
        .replace("{std_imports}", _std_imports(plan))
        .replace("{urljoin}", ", urljoin" if plan.poll else "")
        .replace("{module_helpers}", _drift_helpers(plan))
        .replace("{init_extra}", _init_extra(plan))
    )
    parts = [head.rstrip(), _list_source(plan), _item_methods(plan), _job_source(plan)]
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


def write_ref_client(task_dir: str | Path, spec: dict) -> Path:
    out = Path(task_dir) / "ref_client.py"
    out.write_text(generate_client_source(spec), encoding="utf-8")
    return out
