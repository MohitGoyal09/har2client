"""Header, exceptions and model layers of the SDK-style client (templates and the code that fills them)."""

from __future__ import annotations

from har2client.heal.sdk_client.plan import Plan, fill
from har2client.tierb import domains
from har2client.tierb.spec import TIMESTAMP_FORMAT, extra_fields

# ---------------------------------------------------------------------------------------------------- header, exceptions

HEAD = '''"""Python SDK for the @title@.

Layers: exceptions, models, retry helper, auth strategy, transport, paginator, resource, client.
`Client` is the public entry point; everything else is an implementation detail of one layer.
Uses only httpx and the standard library.
"""

@imports@

import httpx

__all__ = ["Client", "ApiError", "AuthError", "NotFoundError"]
__version__ = "1.4.0"

T = TypeVar("T")

logger = logging.getLogger("@logger@")

DEFAULT_TIMEOUT = 30.0  # seconds, applied to connect, read, write and pool waits
RETRY_ATTEMPTS = 3  # total tries of one request when the connection fails
RETRY_BACKOFF = 0.2  # seconds; the wait grows with every failed try
RESOURCE_PATH = @resource_path@  # collection path, below the base URL
FILTER_FIELD = @filter_field@  # the one query filter of the list call (canonical name)
@protocol_constants@
@items_key@'''

EXCEPTIONS = '''

# ------------------------------------------------------------------------------------------------------ exceptions


class ApiError(Exception):
    """The API answered with a status code outside 2xx."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.status_code}: {self.message}"


class AuthError(ApiError):
    """The credentials, the session or the API key were not accepted (401 or 403)."""


class NotFoundError(ApiError):
    """The item or the path does not exist (404)."""


def _error_from(resp: httpx.Response) -> ApiError:
    """Map a failed response to the typed exception that describes it."""
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text
    message = f"{resp.request.method} {resp.request.url.path} failed"
    if isinstance(body, dict):
        message = str(body.get("message") or body.get("error") or message)
    if resp.status_code in (401, 403):
        return AuthError(message, resp.status_code, body)
    if resp.status_code == 404:
        return NotFoundError(message, resp.status_code, body)
    return ApiError(message, resp.status_code, body)
'''

# ----------------------------------------------------------------------------------------------------------- models

_MODELS = '''

# --------------------------------------------------------------------------------------------------------- models


class _Missing:
    """Marks a field the payload did not carry, so `to_dict` can leave it out again."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()
_INTERNAL = ("extra", "_order")


class Model:
    """Base of the resource models.

    `from_dict` reads a canonical payload, `to_dict` writes one. A payload survives the round trip exactly: unknown keys
    are kept in `extra`, absent keys stay absent, values are never converted. `from_wire` and `to_wire` do the same for the
    wire form. `_from_wire` and `_to_wire` are the only places that know the wire representation; when the API changes how
    a field looks on the wire, translate it there.
    """

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls) if f.name not in _INTERNAL)  # type: ignore[arg-type]

    @classmethod
    def _from_wire(cls, data: dict) -> dict:
        """Wire payload -> canonical payload. Identity while the wire format equals the canonical one."""
        return data

    @classmethod
    def _to_wire(cls, data: dict) -> dict:
        """Canonical payload -> wire payload. Identity while the wire format equals the canonical one."""
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Model":
        payload = dict(data)
        names = cls.field_names()
        known = {key: value for key, value in payload.items() if key in names}
        extra = {key: value for key, value in payload.items() if key not in names}
        return cls(**known, extra=extra, _order=tuple(payload))  # type: ignore[call-arg]

    def to_dict(self) -> dict:
        names = self.field_names()
        out: dict = {}
        for key in self._order:  # type: ignore[attr-defined]
            value = getattr(self, key) if key in names else self.extra.get(key, MISSING)  # type: ignore[attr-defined]
            if value is not MISSING:
                out[key] = value
        for key in names:  # attributes that were set after parsing
            value = getattr(self, key)
            if key not in out and value is not MISSING:
                out[key] = value
        for key, value in self.extra.items():  # type: ignore[attr-defined]
            out.setdefault(key, value)
        return out

    @classmethod
    def from_wire(cls, data: dict) -> "Model":
        """The model of a payload as the API sends it."""
        return cls.from_dict(cls._from_wire(dict(data)))

    def to_wire(self) -> dict:
        """The payload as the API wants it in a request body."""
        return self._to_wire(self.to_dict())

    @classmethod
    def query_to_wire(cls, params: dict) -> dict:
        """Query parameters (canonical names and values) -> the names and values the API expects."""
        return cls._to_wire(dict(params))

    @classmethod
    def parse(cls, value: Any) -> Any:
        """Canonical dict of a response item. Anything that is not an object is returned as it is."""
        return cls.from_wire(value).to_dict() if isinstance(value, dict) else value

    @classmethod
    def parse_all(cls, values: Any) -> Any:
        """`parse` for every item of a list payload; a payload that is not a list is returned as it is."""
        return [cls.parse(value) for value in values] if isinstance(values, list) else values


@dataclass
class @model@(Model):
    """One @singular@ of the @domain@ collection."""

@model_fields@
    extra: dict = field(default_factory=dict)  # keys of the payload that this model does not know
    _order: tuple = field(default=(), repr=False, compare=False)  # key order of the payload
@wire_methods@'''

_PY_TYPES = {"enum": "str", "city": "str", "person": "str", "company": "str", "phrase": "str", "int": "int", "float": "float",
             "bool": "bool", "date": "str", "code": "str", "money": "float", "email": "str"}


def _wire_methods(plan: Plan) -> str:
    """`_from_wire` (and `_to_wire` when requests change too) of the model, one block per drift kind."""
    d = plan.drift
    if not d:
        return ""
    back = ["        out = dict(data)"]
    if "rename" in d:
        r = d["rename"]
        back += [f"        if {r['to']!r} in out:  # rename: `{r['field']}` is called `{r['to']}` on the wire", f"            out[{r['field']!r}] = out.pop({r['to']!r})"]
    if "cents" in d:
        c = d["cents"]
        back += [f"        if {c['to']!r} in out:  # cents: money is a whole number of cents on the wire", f"            out[{c['field']!r}] = round(out.pop({c['to']!r}) / 100, 2)"]
    if "epoch" in d:
        f = d["epoch"]["field"]
        back += [
            f"        if {f!r} in out:  # epoch: timestamps are seconds since 1970 on the wire",
            f"            out[{f!r}] = datetime.fromtimestamp(out[{f!r}], timezone.utc).strftime({TIMESTAMP_FORMAT!r})",
        ]
    if "nest" in d:
        n = d["nest"]
        back += [
            f"        if {n['key']!r} in out:  # nest: name and email sit in one object on the wire",
            f"            nested = out.pop({n['key']!r})",
            f"            out[{n['name_field']!r}] = nested[\"name\"]",
            f"            out[{n['email_field']!r}] = nested[\"email\"]",
        ]
    if "enum" in d:
        e = d["enum"]
        undo = f"out[{e['field']!r}].lower()" if e["mode"] == "upper" else f"out[{e['field']!r}][{len(e['prefix'])}:]"
        how = "upper case" if e["mode"] == "upper" else f"prefix {e['prefix']!r}"
        back += [f"        if {e['field']!r} in out:  # enum: `{e['field']}` values are in {how} on the wire", f"            out[{e['field']!r}] = {undo}"]
    text = [
        "", "    @classmethod", "    def _from_wire(cls, data: dict) -> dict:",
        '        """Wire payload -> canonical payload: undo what the API changed on the wire."""', *back, "        return out",
    ]
    if plan.sends:
        go = ["        out = dict(data)"]
        if "rename" in d:
            r = d["rename"]
            go += [f"        if {r['field']!r} in out:", f"            out[{r['to']!r}] = out.pop({r['field']!r})"]
        if "cents" in d:
            c = d["cents"]
            go += [f"        if {c['field']!r} in out:", f"            out[{c['to']!r}] = round(out.pop({c['field']!r}) * 100)"]
        if "nest" in d:
            n = d["nest"]
            go += ["        nested = {}"]
            for sub, name in (("name", n["name_field"]), ("email", n["email_field"])):
                go += [f"        if {name!r} in out:", f"            nested[{sub!r}] = out.pop({name!r})"]
            go += ["        if nested:", f"            out[{n['key']!r}] = nested"]
        if "enum" in d:
            f = d["enum"]["field"]
            wired = f"out[{f!r}].upper()" if d["enum"]["mode"] == "upper" else f"{d['enum']['prefix']!r} + out[{f!r}]"
            go += [f"        if {f!r} in out:", f"            out[{f!r}] = {wired}"]
        text += [
            "", "    @classmethod", "    def _to_wire(cls, data: dict) -> dict:",
            '        """Canonical payload -> wire payload (request bodies and query parameters)."""', *go, "        return out",
        ]
    return "\n".join(text) + "\n"


def models_source(spec: dict, plan: Plan) -> str:
    info = domains.DOMAINS[spec["domain"]]
    lines = ["    id: str = MISSING", "    created_at: str = MISSING"]
    lines += [f"    {f[0]}: {_PY_TYPES[f[1]]} = MISSING" for f in (*info["fields"], *extra_fields(spec))]
    return fill(
        _MODELS, model=model_name(spec), singular=spec["singular"], domain=spec["domain"], model_fields="\n".join(lines),
        wire_methods=_wire_methods(plan),
    )


def model_name(spec: dict) -> str:
    return spec["singular"].capitalize()
