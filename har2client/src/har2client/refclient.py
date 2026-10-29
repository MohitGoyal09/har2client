"""Generate a gold `client.py` from an OpTable. It proves every task is solvable."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from har2client.ops import ordered_args

_PATH_VAR = re.compile(r"\{([^}]+)\}")

_HEADER = '''"""Reference client generated from the operation table. Uses only httpx and the standard library."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

import httpx


def _ser(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _q(value: Any) -> str:
    return quote(_ser(value), safe="")


def _pairs(name: str, value: Any) -> list:
    items = value if isinstance(value, (list, tuple)) else [value]
    return [(name, _ser(item)) for item in items]


class Client:
    def __init__(self, base_url: str, api_key: str):
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.Client(timeout=30.0, trust_env=False)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, params: list, body: Any = None) -> Any:
        headers: dict = {}
        query = list(params)
{auth}
        response = self._http.request(
            method, self._base + path, params=query or None, json=body, headers=headers
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()
'''


def _auth_lines(security: dict) -> str:
    kind = security["type"]
    if kind == "apiKey" and security["in"] == "query":
        line = f"query.append(({security['name']!r}, self._api_key))"
    elif kind == "apiKey":
        line = f"headers[{security['name']!r}] = self._api_key"
    elif kind == "bearer":
        line = 'headers["Authorization"] = "Bearer " + self._api_key'
    elif kind == "basic":
        line = 'headers["Authorization"] = "Basic " + base64.b64encode(self._api_key.encode()).decode()'
    else:
        line = "pass  # this API needs no credentials"
    return "        " + line


def _path_expr(op: dict) -> str:
    by_name = {p["name"]: p["py_name"] for p in op["params"] if p["in"] == "path"}
    pieces = []
    for part in re.split(r"(\{[^}]+\})", op["path"]):
        if not part:
            continue
        var = _PATH_VAR.fullmatch(part)
        pieces.append(f"_q({by_name[var.group(1)]})" if var else repr(part))
    return " + ".join(pieces) or "''"


def _method_source(op: dict, host: str) -> str:
    args = ["self"]
    for arg in ordered_args(op):
        args.append(
            f"{arg['py_name']}: {arg['type']}" if arg["required"] else f"{arg['py_name']}: {arg['type']} | None = None"
        )
    summary = " ".join(op["summary"].replace(host, "<host>").split()) if host else op["summary"]
    lines = [f"    def {op['op_id']}({', '.join(args)}) -> Any:", f"        # {summary}", "        _params: list = []"]
    for p in op["params"]:
        if p["in"] != "query":
            continue
        add = f"_params.extend(_pairs({p['name']!r}, {p['py_name']}))"
        if p["required"]:
            lines.append("        " + add)
        else:
            lines += [f"        if {p['py_name']} is not None:", "            " + add]
    body = "body" if op["body"] else "None"
    lines.append(f"        return self._request({op['method']!r}, {_path_expr(op)}, _params, {body})")
    return "\n".join(lines)


def generate_client_source(op_table: dict) -> str:
    host = urlparse(op_table["server_url"]).hostname or ""
    parts = [_HEADER.replace("{auth}", _auth_lines(op_table["security"]))]
    parts += [_method_source(op, host) for op in op_table["operations"]]
    return "\n\n".join(parts).rstrip() + "\n"


def write_ref_client(task_dir: str | Path) -> Path:
    task_dir = Path(task_dir)
    op_table = json.loads((task_dir / "op_table.json").read_text(encoding="utf-8"))
    out = task_dir / "ref_client.py"
    out.write_text(generate_client_source(op_table), encoding="utf-8")
    return out
