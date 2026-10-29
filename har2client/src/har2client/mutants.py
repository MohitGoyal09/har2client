"""Wrong clients for a grader-validity audit (mutation testing of the grader).

A mutant is the reference client with ONE plausible fault: valid Python, same public interface.
`generate_mutants(ctx)` returns the mutants that apply to a task plus a written reason for every
operator that does not. Two trivial agents (empty client, HAR replay) are built the same way.

A mutant is "killed" when the grader gives it pass_rate < 1.0. A survivor is then either an
equivalent mutant (an inspection rule says the grader cannot see the fault by design), a flag-only
case (the grader reports it in `invented_endpoint_calls`, `hardcoded_secret` or `hardcoded_host`
but does not score it), or a GRADER_GAP (see `classify_survivor`).
"""

from __future__ import annotations

import ast
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from har2client.refclient import _auth_lines, _path_expr, generate_client_source

CLASS_EQUIVALENT = "EQUIVALENT"
CLASS_FLAG_ONLY = "FLAG_ONLY"
CLASS_GRADER_GAP = "GRADER_GAP"

_ALT_METHOD = {"GET": "POST", "POST": "PUT", "PUT": "POST", "PATCH": "PUT", "DELETE": "POST"}
_PATH_PARAM = re.compile(r"(\{[^}]+\})")
_HEADER_REF = re.compile(r"headers\[(['\"])(.*?)\1\]")

_NO_EGRESS = '''class _NoEgress(httpx.BaseTransport):
    """Audit guard: the hard-coded real host must never be contacted."""

    def handle_request(self, request):
        raise httpx.ConnectError("egress blocked by the grader audit", request=request)
'''


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    op_table: dict
    tests: list
    recording_secret: str
    source: str  # the reference client the mutants are made from
    recorded_bodies: dict = field(default_factory=dict)  # op_id -> recorded JSON response body


@dataclass(frozen=True)
class Mutant:
    operator: str
    source: str
    op_id: str | None
    detail: str
    equivalent_rule: str | None = None  # set when the grader is blind to this fault by design


@dataclass(frozen=True)
class Skip:
    operator: str
    reason: str


class _NotApplicable(Exception):
    """Raised by an operator that has no site in this task; the message is the recorded reason."""


# ----------------------------------------------------------------------------- context


def _route_regex(op: dict) -> re.Pattern:
    escaped = "".join(
        "[^/]+" if part.startswith("{") else re.escape(part) for part in _PATH_PARAM.split(op["path"]) if part
    )
    return re.compile(f"^{escaped}$")


def route_matches(op_table: dict, method: str, path: str) -> bool:
    """True when some operation of the spec would serve `method path` (path relative to the base path)."""
    return any(o["method"] == method and _route_regex(o).match(path) for o in op_table["operations"])


def recorded_bodies(har: dict, op_table: dict) -> dict:
    """op_id -> first recorded 2xx JSON response body, matched by method and path (most literal route wins)."""
    base = op_table["base_path"]
    routes = []
    for op in op_table["operations"]:
        literal = len(_PATH_PARAM.sub("", op["path"]))
        routes.append((-literal, op["op_id"], op["method"], _route_regex(op)))
    routes.sort()
    out: dict = {}
    for entry in har["log"]["entries"]:
        path = urlparse(entry["request"]["url"]).path
        if base and not path.startswith(base + "/"):
            continue
        rel = path[len(base):]
        response = entry["response"]
        text = response["content"].get("text")
        if not (200 <= response["status"] < 300) or not text:
            continue
        try:
            body = json.loads(text)
        except ValueError:
            continue
        for _, op_id, method, rx in routes:
            if method == entry["request"]["method"] and rx.match(rel):
                out.setdefault(op_id, body)
                break
    return out


def load_context(task_dir: str | Path) -> TaskContext:
    """Read a built Tier A task directory (task.json, op_table.json, tests.json, session.har, ref_client.py)."""
    task_dir = Path(task_dir)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    table = json.loads((task_dir / "op_table.json").read_text(encoding="utf-8"))
    tests = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
    har = json.loads((task_dir / "session.har").read_text(encoding="utf-8"))
    ref = task_dir / "ref_client.py"
    source = ref.read_text(encoding="utf-8") if ref.exists() else generate_client_source(table)
    return TaskContext(task["task_id"], table, tests, task["recording_secret"], source, recorded_bodies(har, table))


# ----------------------------------------------------------------------------- helpers


def _edit(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise _NotApplicable(f"reference source has no unique anchor {old!r}")
    return source.replace(old, new)


def _by_id(ctx: TaskContext) -> dict:
    return {o["op_id"]: o for o in ctx.op_table["operations"]}


def _exercised_ops(ctx: TaskContext) -> list[dict]:
    """Operations the hidden tests call, in test order."""
    by_id = _by_id(ctx)
    seen: list[str] = []
    for t in ctx.tests:
        if t["method"] in by_id and t["method"] not in seen:
            seen.append(t["method"])
    return [by_id[i] for i in seen]


def _supplied_params(ctx: TaskContext, where: str) -> list[tuple[dict, dict]]:
    """(op, param) pairs where `param` lives in `where` and some hidden test passes a value for it."""
    sites = []
    for op in _exercised_ops(ctx):
        calls = [t["kwargs"] for t in ctx.tests if t["method"] == op["op_id"]]
        for p in op["params"]:
            if p["in"] == where and any(c.get(p["py_name"]) is not None for c in calls):
                sites.append((op, p))
    return sites


def _pick(ctx: TaskContext, operator: str, items: list):
    return random.Random(f"{ctx.task_id}:{operator}").choice(items)


def _block(source: str, op_id: str) -> str:
    start = source.find(f"\n    def {op_id}(")
    if start < 0:
        raise _NotApplicable(f"reference source has no method {op_id}")
    end = source.find("\n\n    def ", start + 1)
    return source[start + 1 : end if end >= 0 else len(source)]


def _edit_block(ctx: TaskContext, op_id: str, edit: Callable[[str], str]) -> str:
    block = _block(ctx.source, op_id)
    return _edit(ctx.source, block, edit(block))


def _security(ctx: TaskContext) -> dict:
    return ctx.op_table["security"]


def _uses_header_auth(sec: dict) -> bool:
    return sec["type"] in ("bearer", "basic") or (sec["type"] == "apiKey" and sec["in"] == "header")


def _require_exercised_auth(ctx: TaskContext) -> None:
    if _security(ctx)["type"] == "none":
        raise _NotApplicable("API has no auth (security type none)")
    if not any(o["auth_required"] for o in _exercised_ops(ctx)):
        raise _NotApplicable("no tested operation requires auth")


def _require_header_auth(ctx: TaskContext) -> str:
    _require_exercised_auth(ctx)
    if not _uses_header_auth(_security(ctx)):
        raise _NotApplicable("auth is sent in the query, not a header")
    return _auth_lines(_security(ctx))


def _tested_scalars(ctx: TaskContext) -> list:
    """Values the hidden tests pass for query and path params (list items flattened)."""
    values = []
    for op in _exercised_ops(ctx):
        for t in (t for t in ctx.tests if t["method"] == op["op_id"]):
            for p in op["params"]:
                v = t["kwargs"].get(p["py_name"])
                values += v if isinstance(v, list) else [v]
    return values


def _tested_with_body(ctx: TaskContext) -> bool:
    return any("body" in t["kwargs"] for t in ctx.tests)


# ----------------------------------------------------------------------------- operators

OPERATORS: dict[str, Callable[[TaskContext], Mutant]] = {}
_EQUIVALENT_RULES: dict[str, str] = {}


def operator(name: str, equivalent_rule: str | None = None):
    def register(fn):
        OPERATORS[name] = fn
        if equivalent_rule:
            _EQUIVALENT_RULES[name] = equivalent_rule
        return fn

    return register


def _mutant(name: str, source: str, op_id: str | None = None, detail: str = "") -> Mutant:
    return Mutant(name, source, op_id, detail, _EQUIVALENT_RULES.get(name))


@operator("base_path_doubled")
def _base_path_doubled(ctx):
    bp = ctx.op_table["base_path"]
    if not bp:
        raise _NotApplicable("spec has no base path")
    src = _edit(ctx.source, 'self._base = base_url.rstrip("/")', f'self._base = base_url.rstrip("/") + {bp!r}')
    return _mutant("base_path_doubled", src, detail=f"appends {bp} to a base_url that already has it")


@operator("base_path_dropped")
def _base_path_dropped(ctx):
    bp = ctx.op_table["base_path"]
    if not bp:
        raise _NotApplicable("spec has no base path")
    src = _edit(ctx.source, 'self._base = base_url.rstrip("/")', f'self._base = base_url.rstrip("/").removesuffix({bp!r})')
    return _mutant("base_path_dropped", src, detail=f"strips {bp} from base_url")


@operator("wrong_http_method")
def _wrong_http_method(ctx):
    ops_ = _exercised_ops(ctx)
    if not ops_:
        raise _NotApplicable("no tested operation")
    op = _pick(ctx, "wrong_http_method", ops_)
    new = _ALT_METHOD.get(op["method"], "GET")
    src = _edit_block(ctx, op["op_id"], lambda b: _edit(b, f"self._request({op['method']!r},", f"self._request({new!r},"))
    return _mutant("wrong_http_method", src, op["op_id"], f"{op['method']} -> {new}")


def _alter_path(path: str) -> str | None:
    """Change one literal word of a path template (singular <-> plural), or None when there is none."""
    parts = _PATH_PARAM.split(path)
    for i, part in enumerate(parts):
        m = None if part.startswith("{") else re.search(r"[A-Za-z][A-Za-z0-9_-]*", part)
        if m:
            word = m.group(0)
            new = word[:-1] if word.endswith("s") and len(word) > 3 else word + "s"
            parts[i] = part[: m.start()] + new + part[m.end() :]
            return "".join(parts)
    return None


@operator("path_segment_changed")
def _path_segment_changed(ctx):
    sites = [(o, _alter_path(o["path"])) for o in _exercised_ops(ctx)]
    sites = [(o, p) for o, p in sites if p]
    if not sites:
        raise _NotApplicable("no tested operation has a literal path segment")
    op, new_path = _pick(ctx, "path_segment_changed", sites)
    old_expr, new_expr = _path_expr(op), _path_expr({**op, "path": new_path})
    src = _edit_block(ctx, op["op_id"], lambda b: _edit(b, f", {old_expr}, _params", f", {new_expr}, _params"))
    return _mutant("path_segment_changed", src, op["op_id"], f"{op['path']} -> {new_path}")


@operator("path_param_in_query")
def _path_param_in_query(ctx):
    sites = [(o, p) for o in _exercised_ops(ctx) for p in o["params"] if p["in"] == "path"]
    if not sites:
        raise _NotApplicable("no tested operation has a path param")
    op, param = _pick(ctx, "path_param_in_query", sites)
    new_path = re.sub(r"/?" + re.escape("{" + param["name"] + "}"), "", op["path"], count=1)
    old_expr, new_expr = _path_expr(op), _path_expr({**op, "path": new_path})
    line = f"        _params.extend(_pairs({param['name']!r}, {param['py_name']}))\n"

    def edit(block: str) -> str:
        block = _edit(block, f", {old_expr}, _params", f", {new_expr}, _params")
        return _edit(block, "        return self._request(", line + "        return self._request(")

    src = _edit_block(ctx, op["op_id"], edit)
    return _mutant("path_param_in_query", src, op["op_id"], f"{{{param['name']}}} moved from {op['path']} to the query")


def _renamed(param: dict) -> str:
    if param["py_name"] != param["name"]:
        return param["py_name"]  # the classic slip: the Python name instead of the wire name
    return param["name"].swapcase() if param["name"].swapcase() != param["name"] else param["name"] + "_"


@operator("query_param_renamed")
def _query_param_renamed(ctx):
    sites = _supplied_params(ctx, "query")
    if not sites:
        raise _NotApplicable("no hidden test passes a query param")
    op, p = _pick(ctx, "query_param_renamed", sites)
    new = _renamed(p)
    old_line = f"_params.extend(_pairs({p['name']!r}, {p['py_name']}))"
    src = _edit_block(ctx, op["op_id"], lambda b: _edit(b, old_line, f"_params.extend(_pairs({new!r}, {p['py_name']}))"))
    return _mutant("query_param_renamed", src, op["op_id"], f"query {p['name']!r} -> {new!r}")


@operator("query_param_dropped")
def _query_param_dropped(ctx):
    sites = _supplied_params(ctx, "query")
    if not sites:
        raise _NotApplicable("no hidden test passes a query param")
    op, p = _pick(ctx, "query_param_dropped", sites)
    old_line = f"_params.extend(_pairs({p['name']!r}, {p['py_name']}))"
    src = _edit_block(ctx, op["op_id"], lambda b: _edit(b, old_line, "pass"))
    return _mutant("query_param_dropped", src, op["op_id"], f"query {p['name']!r} accepted but never sent")


def _bool_encoding(ctx: TaskContext, name: str, expr: str, detail: str) -> Mutant:
    if not any(isinstance(v, bool) for v in _tested_scalars(ctx)):
        raise _NotApplicable("no hidden test passes a boolean query or path param")
    src = _edit(ctx.source, 'return "true" if value else "false"', f"return {expr}")
    return _mutant(name, src, detail=detail)


@operator("bool_encoding_python_repr")
def _bool_python_repr(ctx):
    return _bool_encoding(ctx, "bool_encoding_python_repr", "str(value)", "True/False instead of true/false")


@operator("bool_encoding_numeric")
def _bool_numeric(ctx):
    return _bool_encoding(ctx, "bool_encoding_numeric", '"1" if value else "0"', "1/0 instead of true/false")


@operator("auth_header_name_wrong")
def _auth_header_name_wrong(ctx):
    line = _require_header_auth(ctx)
    sec = _security(ctx)
    if sec["type"] == "apiKey":
        old = sec["name"]
        new = old + "-Key" if old.lower().startswith("x-") else "X-" + old
    else:
        old, new = "Authorization", "X-Authorization"
    src = _edit(ctx.source, line, _HEADER_REF.sub(f"headers[{new!r}]", line))
    return _mutant("auth_header_name_wrong", src, detail=f"header {old} -> {new}")


@operator("auth_in_query")
def _auth_in_query(ctx):
    line = _require_header_auth(ctx)
    sec = _security(ctx)
    name = sec["name"] if sec["type"] == "apiKey" else ("access_token" if sec["type"] == "bearer" else "api_key")
    src = _edit(ctx.source, line, f"        query.append(({name!r}, self._api_key))")
    return _mutant("auth_in_query", src, detail=f"credential sent as ?{name}= instead of a header")


@operator("auth_in_header")
def _auth_in_header(ctx):
    _require_exercised_auth(ctx)
    sec = _security(ctx)
    if not (sec["type"] == "apiKey" and sec["in"] == "query"):
        raise _NotApplicable("auth is not an API key in the query")
    src = _edit(ctx.source, _auth_lines(sec), f"        headers[{sec['name']!r}] = self._api_key")
    return _mutant("auth_in_header", src, detail=f"credential sent as header {sec['name']} instead of ?{sec['name']}=")


def _prefix_missing(ctx: TaskContext, kind: str, prefix: str) -> Mutant:
    _require_exercised_auth(ctx)
    if _security(ctx)["type"] != kind:
        raise _NotApplicable(f"auth is {_security(ctx)['type']}, not {kind}")
    line = _auth_lines(_security(ctx))
    src = _edit(ctx.source, line, line.replace(f'"{prefix} " + ', ""))
    return _mutant(f"{kind}_prefix_missing", src, detail=f'"{prefix} " prefix removed')


@operator("bearer_prefix_missing")
def _bearer_prefix_missing(ctx):
    return _prefix_missing(ctx, "bearer", "Bearer")


@operator("basic_prefix_missing")
def _basic_prefix_missing(ctx):
    return _prefix_missing(ctx, "basic", "Basic")


@operator("credential_hardcoded")
def _credential_hardcoded(ctx):
    _require_exercised_auth(ctx)
    src = _edit(ctx.source, "self._api_key = api_key", f"self._api_key = {ctx.recording_secret!r}")
    return _mutant("credential_hardcoded", src, detail="api_key argument ignored; recording secret used")


@operator("host_hardcoded")
def _host_hardcoded(ctx):
    server = ctx.op_table["server_url"]
    src = _edit(ctx.source, 'self._base = base_url.rstrip("/")', f'self._base = {server!r}.rstrip("/")')
    src = _edit(src, "httpx.Client(timeout=30.0, trust_env=False)", "httpx.Client(timeout=30.0, trust_env=False, transport=_NoEgress())")
    src = _edit(src, "\n\ndef _ser(", "\n\n" + _NO_EGRESS + "\n\ndef _ser(")
    return _mutant("host_hardcoded", src, detail=f"base_url ignored; {server} used (network egress blocked)")


@operator("response_not_parsed")
def _response_not_parsed(ctx):
    if not any((o["response"] or {}).get("schema") for o in _exercised_ops(ctx)):
        raise _NotApplicable("no tested operation returns a JSON body")
    src = _edit(ctx.source, "        return response.json()", "        return response.text")
    return _mutant("response_not_parsed", src, detail="returns the response text, not parsed JSON")


@operator("returns_field_not_body")
def _returns_field(ctx):
    def has_fields(op):
        schema = (op["response"] or {}).get("schema") or {}
        return bool(schema.get("properties"))

    if not any(has_fields(o) for o in _exercised_ops(ctx)):
        raise _NotApplicable("no tested operation returns a JSON object with fields")
    new = (
        "        data = response.json()\n"
        "        if isinstance(data, dict) and data:\n"
        "            return next(iter(data.values()))\n"
        "        return data"
    )
    src = _edit(ctx.source, "        return response.json()", new)
    return _mutant("returns_field_not_body", src, detail="returns the first field of an object body")


@operator("extra_unrequested_call")
def _extra_call(ctx):
    path = "/__invented__"
    while route_matches(ctx.op_table, "GET", path):  # a catch-all like GET /{id} would make it a real call
        path += "/probe"
    line = f"        self._http.get(self._base + {path!r}, headers=headers)\n"
    src = _edit(ctx.source, "        response = self._http.request(", line + "        response = self._http.request(")
    return _mutant("extra_unrequested_call", src, detail=f"GET {path} before every real call")


@operator("body_as_form")
def _body_as_form(ctx):
    if not _tested_with_body(ctx):
        raise _NotApplicable("no hidden test passes a body")
    src = _edit(ctx.source, "json=body, headers=headers", "data=body, headers=headers")
    return _mutant("body_as_form", src, detail="body form-encoded instead of JSON")


@operator("body_dropped")
def _body_dropped(ctx):
    if not _tested_with_body(ctx):
        raise _NotApplicable("no hidden test passes a body")
    src = _edit(ctx.source, "json=body, headers=headers", "json=None, headers=headers")
    return _mutant("body_dropped", src, detail="body accepted but never sent")


@operator("error_swallowed", equivalent_rule="error_path_not_exercised")
def _error_swallowed(ctx):
    src = _edit(ctx.source, "        response.raise_for_status()", "        if response.is_error:\n            return {}")
    return _mutant("error_swallowed", src, detail="HTTP errors return {} instead of raising")


@operator("extra_query_param")
def _extra_query_param(ctx):
    src = _edit(ctx.source, "        query = list(params)", '        query = list(params)\n        query.append(("debug", "1"))')
    return _mutant("extra_query_param", src, detail="stray ?debug=1 on every call")


@operator("credential_also_in_query", equivalent_rule="design_leniency_1")
def _credential_also_in_query(ctx):
    line = _require_header_auth(ctx)
    src = _edit(ctx.source, line, line + '\n        query.append(("api_key", self._api_key))')
    return _mutant("credential_also_in_query", src, detail="credential sent in a header and again as ?api_key=")


@operator("empty_body_object_when_none", equivalent_rule="design_leniency_2")
def _empty_body_object(ctx):
    src = _edit(ctx.source, "json=body, headers=headers", "json=body if body is not None else {}, headers=headers")
    return _mutant("empty_body_object_when_none", src, detail="sends {} when there is no body")


def generate_mutants(ctx: TaskContext) -> tuple[list[Mutant], list[Skip]]:
    found, skipped = [], []
    for name, fn in OPERATORS.items():
        try:
            found.append(fn(ctx))
        except _NotApplicable as why:
            skipped.append(Skip(name, str(why)))
    return found, skipped


# ----------------------------------------------------------------------------- survivors


def classify_survivor(mutant: Mutant, grade: dict) -> tuple[str, str]:
    """(class, reason) for a mutant that scored pass_rate 1.0.

    EQUIVALENT: an inspection rule from DESIGN.md says the grader cannot see this fault by design.
    FLAG_ONLY: not scored, but a grade.json flag reports it (a policy decision, not a blind spot).
    GRADER_GAP: nothing in grade.json shows the fault."""
    if mutant.equivalent_rule:
        return CLASS_EQUIVALENT, mutant.equivalent_rule
    if grade.get("invented_endpoint_calls", 0) > 0:
        return CLASS_FLAG_ONLY, f"invented_endpoint_calls={grade['invented_endpoint_calls']} reported, not scored"
    for flag in ("hardcoded_secret", "hardcoded_host"):
        if grade.get(flag):
            return CLASS_FLAG_ONLY, f"{flag} flag set, not scored"
    return CLASS_GRADER_GAP, f"no flag raised; fault was: {mutant.detail}"


# ----------------------------------------------------------------------------- trivial agents


def _public_signatures(source: str) -> list[tuple[str, str]]:
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "Client")
    return [
        (f.name, ast.unparse(f.args))
        for f in cls.body
        if isinstance(f, ast.FunctionDef) and not f.name.startswith("_") and f.name != "close"
    ]


def _stub_client(ctx: TaskContext, doc: str, preamble: str, ret: Callable[[str], str]) -> str:
    lines = [
        f'"""{doc}"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        preamble,
        "",
        "class Client:",
        "    def __init__(self, base_url: str, api_key: str):",
        "        self._init(base_url, api_key)",
        "",
        "    def close(self) -> None:",
        "        return None",
    ]
    for name, args in _public_signatures(ctx.source):
        lines += ["", f"    def {name}({args}) -> Any:", f"        return {ret(name)}"]
    return "\n".join(lines) + "\n"


def empty_client(ctx: TaskContext) -> str:
    """Trivial agent 1: every method returns None and sends nothing."""
    src = _stub_client(ctx, "Trivial agent: empty client.", "", lambda name: "None")
    return src.replace("        self._init(base_url, api_key)", "        pass")


def har_replay_client(ctx: TaskContext) -> str:
    """Trivial agent 2: answers from the recorded HAR bodies, with the recorded host and key baked in. Sends nothing."""
    preamble = f"_CANNED = {ctx.recorded_bodies!r}"
    src = _stub_client(ctx, "Trivial agent: replays recorded HAR responses.", preamble, lambda name: f"_CANNED.get({name!r})")
    init = (
        f"        self._base = {ctx.op_table['server_url']!r}\n"
        f"        self._api_key = {ctx.recording_secret!r}"
    )
    return src.replace("        self._init(base_url, api_key)", init)
