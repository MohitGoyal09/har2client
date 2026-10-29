"""Load an OpenAPI 3.0 spec and normalize it to an OpTable (see DESIGN.md)."""

from __future__ import annotations

import json
import keyword
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

from har2client.datagen import schema_kind

MAX_REF_RECURSION = 2  # a $ref may appear at most this many times on one resolution path
REF_DEPTHS = (6, 5, 4, 3, 2)  # nested $ref expansions allowed; tried in order until the result fits
NODE_BUDGET = 120_000  # max JSON nodes produced by one resolution attempt
MIN_OPS, MAX_OPS, MIN_SCHEMA_OPS = 5, 40, 3
MAX_SUMMARY_WORDS = 6  # a longer summary is a sentence, not a method name
RESERVED_PARAM_NAMES = {"body", "self"}
NO_BODY_STATUSES = (204, 205)  # a response with these statuses cannot carry a body
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
FALLBACK_HOST = "api.example.com"
_RECURSION_CUT = {"type": "object"}
_PATH_VAR = re.compile(r"\{([^}]+)\}")


class UnusableApi(ValueError):
    """The spec cannot become a task; the message says why."""


# ---------------------------------------------------------------- loading

_BaseLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _Loader(_BaseLoader):  # type: ignore[misc, valid-type]
    """SafeLoader without implicit timestamps, so dates stay strings."""


_Loader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _pointer_get(root: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    node = root
    for raw in ref[2:].split("/"):
        key = unquote(raw).replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return None
    return node


class _TooBig(Exception):
    pass


class _Resolver:
    """Inline `$ref`. Bounded three ways: a ref may recur at most MAX_REF_RECURSION times on one
    path, refs nest at most `max_depth` deep, and the output has at most NODE_BUDGET nodes
    (`_TooBig` is raised beyond that)."""

    def __init__(self, root: dict, max_depth: int):
        self.root = root
        self.max_depth = max_depth
        self.stack: list[str] = []
        self.cache: dict[str, tuple[Any, int]] = {}  # ref -> (value, node count), uncut results only
        self.cuts = 0
        self.nodes = 0

    def _count(self, n: int = 1) -> None:
        self.nodes += n
        if self.nodes > NODE_BUDGET:
            raise _TooBig

    def walk(self, node: Any) -> Any:
        self._count()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                return self._follow(ref)
            return {str(k): self.walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v) for v in node]
        if isinstance(node, (str, int, float, bool)) or node is None:
            return node
        return str(node)  # dates and other YAML-only scalars

    def _follow(self, ref: str) -> Any:
        if ref in self.cache:
            value, size = self.cache[ref]
            self._count(size)
            return value
        target = _pointer_get(self.root, ref)
        if target is None:
            return {}
        if len(self.stack) >= self.max_depth or self.stack.count(ref) >= MAX_REF_RECURSION:
            self.cuts += 1
            return dict(_RECURSION_CUT)
        before_cuts, before_nodes = self.cuts, self.nodes
        self.stack.append(ref)
        try:
            out = self.walk(target)
        finally:
            self.stack.pop()
        if self.cuts == before_cuts:  # only fully expanded results are safe to reuse
            self.cache[ref] = (out, self.nodes - before_nodes)
        return out


def _resolve_sections(raw: dict) -> dict:
    last_error: Exception | None = None
    for depth in REF_DEPTHS:
        resolver = _Resolver(raw, depth)
        try:
            out: dict[str, Any] = {str(k): v for k, v in raw.items()}
            for key in ("info", "servers", "security", "paths"):
                if key in raw:
                    out[key] = resolver.walk(raw[key])
            components = dict(raw.get("components") or {})
            if "securitySchemes" in components:
                components["securitySchemes"] = resolver.walk(components["securitySchemes"])
            out["components"] = components
            return out
        except _TooBig as exc:
            last_error = exc
    raise UnusableApi("spec is too large to resolve $ref within the size budget") from last_error


def load_spec(path: str | Path) -> dict:
    """Read YAML or JSON. `$ref` is resolved (recursion capped) in `paths`,
    `servers`, `security` and `components.securitySchemes`; other `components`
    sections stay raw because nothing reads them after resolution."""
    text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(text) if text.lstrip().startswith("{") else yaml.load(text, Loader=_Loader)  # noqa: S506
    if not isinstance(raw, dict):
        raise UnusableApi("spec is not a mapping")
    out = _resolve_sections(raw)
    return json.loads(json.dumps(out, default=str))


# ---------------------------------------------------------------- names


def snake_case(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    return re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()


def _identifier(text: str, fallback: str) -> str:
    name = snake_case(text) or fallback
    if name[0].isdigit():
        name = f"{fallback}_{name}"
    if keyword.iskeyword(name):
        name += "_"
    return name


def _unique(name: str, taken: set[str]) -> str:
    out, n = name, 2
    while out in taken:
        out = f"{name}_{n}"
        n += 1
    taken.add(out)
    return out


# ---------------------------------------------------------------- security


def _map_scheme(scheme: Any) -> dict | None:
    if not isinstance(scheme, dict):
        return None
    kind = scheme.get("type")
    if kind == "apiKey" and scheme.get("in") in ("header", "query") and scheme.get("name"):
        return {"type": "apiKey", "in": scheme["in"], "name": scheme["name"]}
    if kind == "http" and str(scheme.get("scheme", "")).lower() in ("bearer", "basic"):
        return {"type": str(scheme["scheme"]).lower(), "in": None, "name": None}
    if kind in ("oauth2", "openIdConnect"):
        return {"type": "bearer", "in": None, "name": None}
    return None


def _alternatives(requirement: Any) -> list[dict]:
    return [alt for alt in requirement if isinstance(alt, dict)] if isinstance(requirement, list) else []


def _single_scheme(alt: dict, schemes: dict) -> dict | None:
    """The one credential that satisfies this requirement, or None. Several schemes in one
    requirement are all needed (AND); that is fine only when they map to the same credential
    (for example two oauth2 schemes), because the client has a single `api_key`."""
    mapped = [_map_scheme(schemes.get(name)) for name in alt]
    if not mapped or any(m is None for m in mapped) or any(m != mapped[0] for m in mapped):
        return None
    return mapped[0]


def _choose_security(spec: dict, op_requirements: list[list[dict]], schemes: dict) -> dict:
    all_requirements = [_alternatives(spec.get("security")), *op_requirements]
    saw_requirement = False
    for alternatives in all_requirements:
        for alt in alternatives:
            if not alt:
                continue
            saw_requirement = True
            mapped = _single_scheme(alt, schemes)
            if mapped:
                return mapped
    if saw_requirement:
        raise UnusableApi("security: no supported single scheme (apiKey header/query, bearer, basic, oauth2)")
    return {"type": "none", "in": None, "name": None}


def _op_auth(alternatives: list[dict], chosen: dict, schemes: dict) -> bool | None:
    """True/False for auth_required; None when the op needs a scheme we cannot supply."""
    if not alternatives or any(not alt for alt in alternatives):
        return False
    if any(_single_scheme(alt, schemes) == chosen for alt in alternatives):
        return True
    return None


# ---------------------------------------------------------------- server


def _server(spec: dict, api_id: str) -> tuple[str, str]:
    servers = spec.get("servers") or [{"url": "/"}]
    first = servers[0] if isinstance(servers[0], dict) else {"url": "/"}
    url = str(first.get("url") or "/")
    for name, var in (first.get("variables") or {}).items():
        default = var.get("default") or (var.get("enum") or ["example"])[0]
        url = url.replace("{" + name + "}", str(default))
    parsed = urlparse(url)
    if not parsed.netloc:
        provider = api_id.split(":", 1)[0]
        host = provider if "." in provider else FALLBACK_HOST
        path = parsed.path if parsed.path.startswith("/") else "/" + parsed.path
        url = f"https://{host}{path}"
        parsed = urlparse(url)
    elif not parsed.scheme:
        url = f"https:{url}"
        parsed = urlparse(url)
    url = url.rstrip("/")
    return url, parsed.path.rstrip("/")


# ---------------------------------------------------------------- operations


class _Drop(Exception):
    pass


def _merge_params(path_level: list, op_level: list) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for param in [*path_level, *op_level]:
        if isinstance(param, dict) and "name" in param and "in" in param:
            merged[(param["name"], param["in"])] = param
    return list(merged.values())


def _param_schema(param: dict) -> dict:
    schema = param.get("schema")
    if isinstance(schema, dict):
        return schema
    return {"type": "string"}


def _check_query_schema(param: dict, schema: dict) -> None:
    kind = schema_kind(schema)
    if kind == "object":
        raise _Drop(f"query param {param['name']} is an object")
    if kind == "array":
        items = schema.get("items")
        if schema_kind(items) in ("object", "array"):
            raise _Drop(f"query param {param['name']} is an array of objects")
        if param.get("explode") is False or param.get("style") in ("spaceDelimited", "pipeDelimited"):
            raise _Drop(f"query param {param['name']} is not exploded")


def _build_params(path: str, raw_params: list[dict], security: dict, taken_py: set[str]) -> list[dict]:
    declared = {p["name"] for p in raw_params if p.get("in") == "path"}
    wanted = [p for p in raw_params if p.get("in") in ("path", "query")]
    for name in re.findall(r"\{([^}]+)\}", path):
        if name not in declared:
            wanted.append({"name": name, "in": "path", "required": True, "schema": {"type": "string"}})
    wanted.sort(key=lambda p: p["in"] != "path")  # path params first, spec order otherwise
    out = []
    for param in wanted:
        schema = _param_schema(param)
        if param["in"] == "path":
            if schema_kind(schema) in ("array", "object"):
                raise _Drop(f"path param {param['name']} is array or object")
        else:
            if security["type"] == "apiKey" and security["in"] == "query" and param["name"] == security["name"]:
                continue
            _check_query_schema(param, schema)
        py = _unique(_param_py_name(param["name"]), taken_py)
        out.append(
            {
                "name": param["name"],
                "py_name": py,
                "in": param["in"],
                "required": True if param["in"] == "path" else bool(param.get("required", False)),
                "schema": schema,
            }
        )
    return out


def _param_py_name(name: str) -> str:
    py = _identifier(name, "p")
    return py + "_" if py in RESERVED_PARAM_NAMES else py


def _is_json(media_type: str) -> bool:
    return media_type.split(";")[0].strip().lower() == "application/json"


def _build_body(op: dict) -> dict | None:
    body = op.get("requestBody")
    if not isinstance(body, dict) or not body.get("content"):
        return None
    content = body["content"]
    media = next((m for m in content if _is_json(m)), None)
    if media is None:
        raise _Drop("request body is not application/json")
    schema = (content[media] or {}).get("schema")
    schema = schema if isinstance(schema, dict) else {}
    kind = schema_kind(schema)
    if kind is None:
        schema = {"type": "object"}
    elif kind != "object":
        raise _Drop(f"request body is a {kind}, not an object")
    return {"required": bool(body.get("required", False)), "schema": schema}


def _build_response(op: dict) -> dict:
    responses = op.get("responses") or {}
    for code, resp in responses.items():
        code = str(code)
        if not re.fullmatch(r"2\d\d|2XX", code, re.IGNORECASE):
            continue
        status = 200 if code.upper() == "2XX" else int(code)
        media = next((m for m in ((resp or {}).get("content") or {}) if _is_json(m)), None)
        schema = None
        if media and status not in NO_BODY_STATUSES:
            raw = resp["content"][media].get("schema") if resp["content"][media] else None
            schema = raw if isinstance(raw, dict) else {}
        return {"status": status, "schema": schema}
    raise _Drop("no 2xx response")


def _summary(op: dict, fallback: str) -> str:
    text = op.get("summary") or str(op.get("description") or "").strip().split("\n")[0] or fallback
    return " ".join(str(text).split())[:120]


def _iter_ops(spec: dict):
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict) or not str(path).startswith("/"):
            continue
        for method in HTTP_METHODS:
            if isinstance(item.get(method), dict):
                yield str(path), method, item, item[method]


def _summary_name(op: dict, taken: set[str]) -> str | None:
    """snake_case of a 1-6 word summary, or None when it is not a valid, free, non-keyword identifier."""
    summary = op.get("summary")
    if not isinstance(summary, str) or not 1 <= len(summary.split()) <= MAX_SUMMARY_WORDS:
        return None
    name = snake_case(re.sub(r"['\u2019]", "", summary))
    if not name.isascii() or not name.isidentifier() or keyword.iskeyword(name) or name in taken:
        return None
    return name


def _op_name(path: str, method: str, op: dict, taken: set[str]) -> str:
    if op.get("operationId"):
        return _identifier(str(op["operationId"]), "op")
    named = _summary_name(op, taken)  # a summary does not reveal the route; the path words do
    if named:
        return named
    words = re.sub(r"[{}]", "", path)
    return _identifier(f"{method}_{words}", "op")


def normalize(spec: dict, api_id: str) -> dict:
    """Spec (from `load_spec`) -> OpTable. Raises UnusableApi for a skipped API."""
    if not str(spec.get("openapi", "")).startswith("3.0"):
        raise UnusableApi(f"only OpenAPI 3.0.x is supported (got {spec.get('openapi') or spec.get('swagger')})")
    schemes = (spec.get("components") or {}).get("securitySchemes") or {}
    entries = list(_iter_ops(spec))
    op_requirements = [
        _alternatives(op["security"]) for _, _, _, op in entries if "security" in op
    ]
    security = _choose_security(spec, op_requirements, schemes)
    server_url, base_path = _server(spec, api_id)
    operations: list[dict] = []
    dropped: list[dict] = []
    taken_ids: set[str] = set()
    seen_shapes: set[tuple[str, str]] = set()
    for path, method, item, op in entries:
        try:
            if "?" in path or "#" in path:
                raise _Drop("path contains a query or fragment part")
            shape = (method, _PATH_VAR.sub("{}", path))
            if shape in seen_shapes:
                raise _Drop("duplicate route: same method and path shape as an earlier operation")
            alts = _alternatives(op["security"]) if "security" in op else _alternatives(spec.get("security"))
            auth = _op_auth(alts, security, schemes)
            if auth is None:
                raise _Drop("requires a security scheme that is not supported")
            raw_params = _merge_params(item.get("parameters") or [], op.get("parameters") or [])
            params = _build_params(path, raw_params, security, set())
            body = _build_body(op)
            response = _build_response(op)
            seen_shapes.add(shape)
        except _Drop as why:
            dropped.append({"method": method.upper(), "path": path, "reason": str(why)})
            continue
        operations.append(
            {
                "op_id": _unique(_op_name(path, method, op, taken_ids), taken_ids),
                "method": method.upper(),
                "path": path,
                "summary": _summary(op, f"{method.upper()} {path}"),
                "params": params,
                "body": body,
                "response": response,
                "auth_required": auth,
            }
        )
    return {
        "api_id": api_id,
        "title": str((spec.get("info") or {}).get("title") or api_id),
        "server_url": server_url,
        "base_path": base_path,
        "security": security,
        "operations": operations,
        "dropped": dropped,
    }


def usability_reason(op_table: dict) -> str | None:
    """None when the API is usable; else a one-line reason."""
    n = len(op_table["operations"])
    if n < MIN_OPS:
        return f"only {n} usable operations (need at least {MIN_OPS})"
    if n > MAX_OPS:
        return f"{n} usable operations (need at most {MAX_OPS})"
    with_schema = sum(1 for o in op_table["operations"] if o["response"]["schema"] is not None)
    if with_schema < MIN_SCHEMA_OPS:
        return f"only {with_schema} operations with a JSON response schema (need at least {MIN_SCHEMA_OPS})"
    return None


# ---------------------------------------------------------------- shared helpers


def py_type(schema: dict | None) -> str:
    return {
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "dict",
    }.get(schema_kind(schema) or "string", "str")


def ordered_args(op: dict) -> list[dict]:
    """Method arguments: required first (params, then body), then optional."""
    args = [
        {"py_name": p["py_name"], "required": p["required"], "type": py_type(p["schema"])}
        for p in op["params"]
    ]
    if op["body"]:
        args.append({"py_name": "body", "required": op["body"]["required"], "type": "dict"})
    return [a for a in args if a["required"]] + [a for a in args if not a["required"]]


def signature(op: dict) -> str:
    parts = ["self"]
    for arg in ordered_args(op):
        parts.append(
            f"{arg['py_name']}: {arg['type']}" if arg["required"] else f"{arg['py_name']}: {arg['type']} | None = None"
        )
    return f"{op['op_id']}({', '.join(parts)}) -> Any"
