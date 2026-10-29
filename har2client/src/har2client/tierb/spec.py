"""The seed-chosen look of one Tier B API: host, paths, header names, formats. Same spec for recording and tests."""

from __future__ import annotations

import calendar
import random
import time
from typing import Any

from har2client.tierb import domains
from har2client.tierb.config import DRIFT_KINDS, switch

PAGE_SIZE = 10
EXPIRY_LIMIT = 4
_BASE_PATHS = ["/v1", "/v2", "/v3", "/api"]
_API_KEY_HEADERS = ["X-API-Key", "X-Api-Token", "Api-Key", "X-Auth-Key"]
_LOGIN_PATHS = ["/auth/login", "/sessions", "/login", "/auth/token"]
_LOGIN_FIELDS = [("username", "password"), ("login", "password"), ("email", "password"), ("user", "secret")]
_TOKEN_FIELDS = ["access_token", "token", "jwt"]
_CSRF_HEADERS = ["X-CSRF-Token", "X-XSRF-Token", "X-Csrf-Header"]
_COOKIE_NAMES = ["session", "sid", "_session_id"]
_ITEMS_KEYS = ["items", "results", "records"]
_OFFSET_PARAMS = ["offset", "skip", "start"]
_CURSOR_PARAMS = ["cursor", "after", "page_token"]
_NEXT_KEYS = ["next_cursor", "next_page_token", "cursor_next"]
_LINK_PARAMS = ["page", "p"]
_HIDDEN_HEADERS = [
    ("X-Client-Version", lambda r: f"{r.randint(2, 6)}.{r.randint(0, 20)}.{r.randint(0, 9)}"),
    ("X-App-Build", lambda r: str(r.randint(1000, 9999))),
    ("X-Api-Version", lambda r: f"2024-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"),
    ("X-Platform", lambda r: f"web-{r.randint(1, 5)}.{r.randint(0, 9)}"),
]


TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
RATE_WINDOW_S = 1.0
RETRY_AFTER_S = 1
# wire name of a renamed field (a field with no entry gets `<field>_v2`)
_RENAMES = {
    "status": "state", "state": "status", "priority": "severity", "carrier": "vendor", "origin": "from_city", "destination": "to_city",
    "weight_kg": "mass_kg", "eta_days": "lead_days", "fragile": "is_fragile", "title": "subject", "assignee": "owner", "queue": "team",
    "estimate_hours": "effort_hours", "customer_visible": "public", "customer": "client", "currency": "ccy", "due_date": "due_on",
    "memo": "note", "amount_cents": "amount_minor", "guest": "guest_name", "room": "room_name", "starts_on": "check_in",
    "nights": "stay_nights", "party_size": "guests_count", "confirmed": "is_confirmed", "assay": "assay_type", "lab": "lab_site",
    "volume_ul": "volume_microlitres", "collected_on": "sampled_on", "batch": "batch_code", "hazardous": "is_hazardous",
}
_MONEY_NAMES = ["amount", "total", "price", "fee", "deposit"]
_NEST_KEYS = ["customer", "contact", "owner", "requester"]
_ENUM_PREFIXES = ["st_", "v2_", "x_"]
_JOB_WORDS = ["jobs", "tasks", "operations"]
_REFRESH_PATHS = ["/auth/refresh", "/token/refresh", "/sessions/refresh", "/refresh"]
_REFRESH_FIELDS = ["refresh_token", "refresh"]
_MOVED_PREFIXES = ["/rest", "/data", "/entities"]
_IDEMPOTENCY_HEADERS = ["Idempotency-Key", "X-Idempotency-Key"]
RATE_LIMITS = (3, 4, 5)  # requests per window; a plain 7-call test run always crosses it
REFRESH_LIMITS = (3, 4)  # calls an access token lives


def _ext_rng(seed: int | str, name: str) -> random.Random:
    """One random stream per switch: a switch draws the same values whatever other switches are on."""
    return random.Random(f"spec:{seed}:ext:{name}")


def _drift_spec(domain: str, kinds: list[str], seed: int | str) -> tuple[dict, list[list]]:
    """({kind: parameters}, extra item fields) of the wire drift. Parameters name the canonical (v1) field and the wire form."""
    names = domains.writable_fields(domain)
    drift: dict[str, dict] = {}
    extra: list[list] = []
    if "cents" in kinds:
        rng = _ext_rng(seed, "cents")
        name = rng.choice([n for n in _MONEY_NAMES if n not in names and f"{n}_cents" not in names])
        drift["cents"] = {"field": name, "to": f"{name}_cents"}
        extra.append([name, "money"])
    if "nest" in kinds:
        rng = _ext_rng(seed, "nest")
        key = rng.choice([k for k in _NEST_KEYS if k not in names])
        drift["nest"] = {"key": key, "name_field": f"{key}_name", "email_field": f"{key}_email"}
        extra += [[f"{key}_name", "person"], [f"{key}_email", "email"]]
    if "enum" in kinds:
        rng = _ext_rng(seed, "enum")
        enums = domains.enum_fields(domain)
        field = rng.choice(sorted(enums))
        lower = all(v == v.lower() for v in enums[field])  # `upper` must be undoable by lower()
        mode = rng.choice(["upper", "prefix"]) if lower else "prefix"
        drift["enum"] = {"field": field, "mode": mode, "prefix": rng.choice(_ENUM_PREFIXES), "values": enums[field]}
    if "epoch" in kinds:
        drift["epoch"] = {"field": "created_at"}
    if "rename" in kinds:
        rng = _ext_rng(seed, "rename")
        taken = {*names, *(f[0] for f in extra), *([drift["nest"]["key"]] if "nest" in drift else [])}
        field = rng.choice([n for n in names if n != drift.get("enum", {}).get("field")])
        to = _RENAMES.get(field, f"{field}_v2")
        drift["rename"] = {"field": field, "to": to if to not in taken else f"{field}_v2"}
    return {k: drift[k] for k in DRIFT_KINDS if k in drift}, extra


def _extension(domain: str, config: dict, seed: int | str) -> dict:
    """Spec keys of the hard-repair switches that are on (none when all are off: the spec of an old config is unchanged)."""
    ext: dict[str, Any] = {}
    kinds = switch(config, "drift")
    if kinds:
        ext["drift"], extra = _drift_spec(domain, kinds, seed)
        if extra:
            ext["extra_fields"] = extra
    if switch(config, "rate_limit") == "on":
        ext["rate_limit"] = {"limit": _ext_rng(seed, "rate_limit").choice(RATE_LIMITS), "window_s": RATE_WINDOW_S, "retry_after": RETRY_AFTER_S}
    if switch(config, "async_create") == "on":
        rng = _ext_rng(seed, "async_create")
        ext["async_create"] = {"job_word": rng.choice(_JOB_WORDS), "pending_polls": rng.choice([1, 2])}
    if switch(config, "refresh_rotation") == "on":
        rng = _ext_rng(seed, "refresh_rotation")
        ext["refresh"] = {"path": rng.choice(_REFRESH_PATHS), "field": rng.choice(_REFRESH_FIELDS), "limit": rng.choice(REFRESH_LIMITS)}
    if switch(config, "idempotency_key") == "on":
        ext["idempotency"] = {"header": _ext_rng(seed, "idempotency_key").choice(_IDEMPOTENCY_HEADERS)}
    if switch(config, "moved") == "on":
        ext["moved"] = {"prefix": _ext_rng(seed, "moved").choice(_MOVED_PREFIXES)}
    return ext


def make_spec(domain: str, config: dict[str, str], seed: int | str) -> dict:
    """Everything the engine and the reference client need besides the data and the credentials."""
    rng = random.Random(f"spec:{seed}")
    info = domains.DOMAINS[domain]
    brand = domains.make_brand(rng)
    host = f"api.{brand}.{rng.choice(domains.TLDS)}"
    base_path = rng.choice(_BASE_PATHS)
    user_field, pass_field = rng.choice(_LOGIN_FIELDS)
    hidden = None
    if config["hidden_header"] == "on":
        name, make_value = rng.choice(_HIDDEN_HEADERS)
        hidden = {"name": name, "value": make_value(rng)}
    page_param = {"offset": _OFFSET_PARAMS, "cursor": _CURSOR_PARAMS, "link_header": _LINK_PARAMS}.get(config["pagination"])
    spec = {
        "domain": domain,
        "singular": info["singular"],
        "filter": info["filter"],
        "config": dict(config),
        "title": f"{brand.capitalize()} {domain.capitalize()} API",
        "host": host,
        "base_path": base_path,
        "server_url": f"https://{host}{base_path}",
        "path_word": rng.choice([domain, "items", "records"]),
        "items_key": rng.choice(_ITEMS_KEYS),
        "api_key_header": rng.choice(_API_KEY_HEADERS),
        "login": {
            "path": rng.choice(_LOGIN_PATHS),
            "format": rng.choice(["json", "form"]),
            "user_field": user_field,
            "pass_field": pass_field,
            "token_field": rng.choice(_TOKEN_FIELDS),
        },
        "csrf": {
            "cookie": rng.choice(_COOKIE_NAMES),
            "header": rng.choice(_CSRF_HEADERS),
            "in": rng.choice(["header", "json"]),
            "field": "csrf_token",
        },
        "page": {
            "size": PAGE_SIZE,
            "param": rng.choice(page_param) if page_param else None,
            "next_key": rng.choice(_NEXT_KEYS),
        },
        "expiry_limit": EXPIRY_LIMIT,
        "hidden_header": hidden,
    }
    return {**spec, **_extension(domain, config, seed)}


def make_credentials(spec: dict, rng: random.Random) -> dict:
    """A username and password; for `apikey_header` the password is the API key."""
    if spec["config"]["auth"] == "apikey_header":
        password = f"sk_{rng.getrandbits(96):024x}"
    else:
        password = f"pw_{rng.getrandbits(64):016x}"
    return {"username": f"user_{rng.getrandbits(32):08x}", "password": password}


OPS = ("list", "get", "create", "update", "delete")


def method_name(spec: dict, op: str) -> str:
    """Goal method for an op: `list_<plural>`, `get_<singular>`, `create_<singular>`, ..."""
    return f"{op}_{spec['domain'] if op == 'list' else spec['singular']}"


def extra_fields(spec: dict) -> list[list]:
    """Item fields a spec adds to the domain's own (money and nested pair of the drift switches); [] for old specs."""
    return spec.get("extra_fields", [])


def drift_fields(spec: dict) -> list[str]:
    """Canonical names of the fields a wire drift changes (tests prefer them in update bodies)."""
    d = {k: v for k, v in spec.get("drift", {}).items() if k in switch(spec["config"], "drift")}
    names = [d[k]["field"] for k in ("rename", "cents", "enum") if k in d]
    return names + ([d["nest"]["name_field"], d["nest"]["email_field"]] if "nest" in d else [])


NO_MATCH = object()  # a filter value that is no wire value: it matches no item


class Wire:
    """Canonical (v1) <-> wire (v2) translation of one spec: the engine keeps canonical items and speaks the wire form.
    Each drift kind is a bijection. Identity (`active` False) when `drift` is off, so old specs behave as before."""

    def __init__(self, spec: dict):
        kinds = switch(spec["config"], "drift")
        self.d = {k: v for k, v in spec.get("drift", {}).items() if k in kinds}
        self.active = bool(self.d)
        enum = self.d.get("enum")
        self._to_wire: dict[str, str] = {}
        if enum:
            self._to_wire = {v: (v.upper() if enum["mode"] == "upper" else enum["prefix"] + v) for v in enum["values"]}
        self._from_wire = {w: v for v, w in self._to_wire.items()}
        # canonical names whose wire key is another one: the old name is not part of the wire API
        self._moved_keys = {self.d[k]["field"] for k in ("rename", "cents") if k in self.d}
        if "nest" in self.d:
            self._moved_keys |= {self.d["nest"]["name_field"], self.d["nest"]["email_field"]}
        rename = self.d.get("rename")
        self.filter_param = rename["to"] if rename and rename["field"] == spec["filter"] else spec["filter"]
        self._filter = spec["filter"]

    def item(self, item: dict) -> dict:
        if not self.active:
            return item
        d, out = self.d, {}
        for key, value in item.items():
            if "nest" in d and key in (d["nest"]["name_field"], d["nest"]["email_field"]):
                nested = out.setdefault(d["nest"]["key"], {})
                nested["name" if key == d["nest"]["name_field"] else "email"] = value
            elif "cents" in d and key == d["cents"]["field"]:
                out[d["cents"]["to"]] = round(value * 100)
            elif "epoch" in d and key == d["epoch"]["field"]:
                out[key] = calendar.timegm(time.strptime(value, TIMESTAMP_FORMAT))
            elif "enum" in d and key == d["enum"]["field"]:
                out[key] = self._to_wire.get(value, value)
            elif "rename" in d and key == d["rename"]["field"]:
                out[d["rename"]["to"]] = value
            else:
                out[key] = value
        return out

    def body(self, wire_body: dict) -> tuple[dict, str | None]:
        """(canonical fields, None) of a request body, or ({}, message) when a value is not valid on the wire."""
        if not self.active:
            return dict(wire_body), None
        d, out = self.d, {}
        for key, value in wire_body.items():
            if key in self._moved_keys:
                continue
            if "nest" in d and key == d["nest"]["key"]:
                if not isinstance(value, dict):
                    return {}, f"{key} must be an object"
                for sub, field in (("name", d["nest"]["name_field"]), ("email", d["nest"]["email_field"])):
                    if sub in value:
                        out[field] = value[sub]
            elif "cents" in d and key == d["cents"]["to"]:
                if not isinstance(value, int) or isinstance(value, bool):
                    return {}, f"{key} must be an integer"
                out[d["cents"]["field"]] = round(value / 100, 2)
            elif "enum" in d and key == d["enum"]["field"]:
                if value not in self._from_wire:
                    return {}, f"invalid value for {key}"
                out[key] = self._from_wire[value]
            elif "rename" in d and key == d["rename"]["to"]:
                out[d["rename"]["field"]] = value
            else:
                out[key] = value
        return out, None

    def wire_keys(self, fields: list[str]) -> list[str]:
        """Wire names of canonical fields, each once, in order."""
        d, keys = self.d, []
        for field in fields:
            if "nest" in d and field in (d["nest"]["name_field"], d["nest"]["email_field"]):
                key = d["nest"]["key"]
            elif "cents" in d and field == d["cents"]["field"]:
                key = d["cents"]["to"]
            elif "rename" in d and field == d["rename"]["field"]:
                key = d["rename"]["to"]
            else:
                key = field
            if key not in keys:
                keys.append(key)
        return keys

    def filter_value(self, raw: str | None) -> Any:
        """Canonical value of the filter query value (`NO_MATCH` when the wire form does not know it)."""
        if raw is None or "enum" not in self.d or self.d["enum"]["field"] != self._filter:
            return raw
        return self._from_wire.get(raw, NO_MATCH)
