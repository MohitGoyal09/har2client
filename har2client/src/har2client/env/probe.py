"""Practice instance for probe actions (DESIGN-CASES.md, "Case 2"; DESIGN-RL.md, "Environment API").

A practice instance is a second Tier B engine with the **same spec** of the task (same host, paths, header names:
that is what the agent can learn about) but its **own data seed and its own credentials**. It never shares
credentials, item ids or data with the recording or with a grading instance. The agent reaches it only through
`PracticeInstance.request`, which is a proxy: it takes a path relative to the practice base URL, counts the probe,
labels it, and returns `{status, headers, body}` (body cut to 4,000 characters).

The base URL shown to the agent is a fixed virtual URL (`https://practice.<host><base path>`), the same in every
arm and every run of a task; the port of the loopback server never shows (response origins are rewritten).

Probe labels are automatic and use the hidden spec (the harness may; the agent may not):
`header_ablation` (same request as a recorded one minus one API header), `etag_test`, `paging_test`, `auth_test`,
`other`. The first rule that matches wins, in that order.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from har2client.env import tasks
from har2client.tierb.build import SEED_SPACE, _pick_test_seed
from har2client.tierb.domains import initial_items
from har2client.tierb.engine import TierBEngine
from har2client.tierb.spec import make_credentials

BODY_LIMIT = 4000
REQUEST_TIMEOUT_S = 10.0
SEED_TRIES = 200
MAX_GRADING_VARIANTS = 8  # reliability_k up to this value: the practice instance is disjoint from all of its grading instances
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
LABELS = ("header_ablation", "auth_test", "paging_test", "etag_test", "other")
DROPPED_RESPONSE_HEADERS = frozenset({"date", "server", "connection", "keep-alive"})
GENERIC_PAGING_KEYS = frozenset({"page", "p", "cursor", "after", "page_token", "offset", "skip", "start", "limit", "per_page"})
PRECONDITION_HEADERS = frozenset({"if-match", "if-none-match"})
FORM_TYPE = "application/x-www-form-urlencoded"


class ProbeError(ValueError):
    """A probe request the proxy cannot send (bad method, bad path, other host, ...)."""


# ---------------------------------------------------------------- the plan: seed, credentials, public URL


@dataclass(frozen=True)
class PracticePlan:
    """Everything about the practice instance of a task that can be known without starting it."""

    seed: int
    credentials: dict
    public_url: str  # fixed virtual base URL, the same in every arm


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _predicted_variants(task: dict, spec: dict, observation: str, count: int) -> list[tuple[int, dict]]:
    """(test seed, test credentials) of the grading variants `<name>.rel1 .. <name>.rel<count>`, computed the way
    `tasks.grading_variants` computes them, without reading them from disk (they are written only when a grader asks
    for reliability_k > 1, which may be after the practice instance is planned)."""
    used = {task["credentials"]["recording"]["password"], task["credentials"]["test"]["password"]}
    out = []
    for i in range(1, count + 1):
        rng = random.Random(f"reliability:{task['seeds']['spec']}:{task['seeds']['test']}:{i}")
        seed = _pick_test_seed(spec, rng, observation, task["seeds"]["test"])
        creds = make_credentials(spec, rng)
        while creds["password"] in used:
            creds = make_credentials(spec, rng)
        out.append((seed, creds))
    return out


def _grading_seeds_and_creds(task_dir: Path, task: dict) -> tuple[set[int], list[dict]]:
    """Seeds and credentials of every instance that can grade this task: the recording, the test instance and the
    reliability variants (the first `MAX_GRADING_VARIANTS - 1`; those on disk are included as well)."""
    seeds = {task["seeds"]["recording"], task["seeds"]["test"]}
    creds = [task["credentials"]["recording"], task["credentials"]["test"]]
    spec = _read_json(task_dir / "engine.json")
    observation = tasks.observation_path(task_dir).read_text(encoding="utf-8")
    for seed, cred in _predicted_variants(task, spec, observation, MAX_GRADING_VARIANTS - 1):
        seeds.add(seed)
        creds.append(cred)
    for path in sorted(task_dir.parent.glob(f"{task_dir.name}.rel*")):
        if (path / "task.json").is_file():
            variant = _read_json(path / "task.json")
            seeds.add(variant["seeds"]["test"])
            creds.append(variant["credentials"]["test"])
    return seeds, creds


def plan_practice(task_dir: str | Path) -> PracticePlan:
    """The practice instance of a task, deterministic in the task: a data seed whose start items share no id with
    the recording or with any grading instance, and credentials that match none of theirs."""
    task_dir = Path(task_dir)
    task = _read_json(task_dir / "task.json")
    spec = _read_json(task_dir / "engine.json")
    avoid_seeds, avoid_creds = _grading_seeds_and_creds(task_dir, task)
    avoid_ids = {item["id"] for seed in avoid_seeds for item in initial_items(spec["domain"], seed)}
    recording_text = tasks.observation_path(task_dir).read_text(encoding="utf-8")
    rng = random.Random(f"practice:{task['seeds']['spec']}:{task['seeds']['recording']}:{task['seeds']['test']}:{task.get('build_seed', 0)}")
    for _ in range(SEED_TRIES):
        seed = rng.randrange(SEED_SPACE)
        ids = {item["id"] for item in initial_items(spec["domain"], seed)}
        if seed not in avoid_seeds and not ids & avoid_ids and not any(i in recording_text for i in ids):
            break
    else:
        raise RuntimeError(f"no practice seed without shared ids for {task_dir.name}")
    names = {c["username"] for c in avoid_creds} | {c["password"] for c in avoid_creds}
    credentials = make_credentials(spec, rng)
    while credentials["username"] in names or credentials["password"] in names:
        credentials = make_credentials(spec, rng)
    return PracticePlan(seed, credentials, f"https://practice.{spec['host']}{spec['base_path']}")


# ---------------------------------------------------------------- labels


def _path_shape(rel_path: str, spec: dict) -> str:
    """Path with the item id replaced, so a recorded `/items/SHP-1` and a probe `/items/SHP-2` compare equal."""
    word = "/" + spec["path_word"].strip("/")
    rel = rel_path.rstrip("/") or "/"
    return f"{word}/{{id}}" if rel.startswith(word + "/") else rel


def _relative(path: str, spec: dict) -> str:
    base = spec["base_path"]
    return path[len(base):] or "/" if base and (path == base or path.startswith(base + "/")) else path


def api_headers(spec: dict) -> frozenset[str]:
    """Lower-case names of the headers that carry API meaning here (the vantage-point headers do not)."""
    names = {"authorization", "cookie", "if-match", spec["api_key_header"].lower()}
    if spec["hidden_header"]:
        names.add(spec["hidden_header"]["name"].lower())
    if spec["config"]["auth"] == "cookie_csrf":
        names.add(spec["csrf"]["header"].lower())
    return frozenset(names)


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    shape: str
    query_keys: frozenset[str]
    headers: frozenset[str]  # API headers only


def recorded_requests(task_dir: str | Path, spec: dict) -> list[RecordedRequest]:
    """The API calls of the visible recording (HAR sources); an empty list for other formats."""
    path = tasks.observation_path(task_dir)
    if path.suffix != ".har":
        return []
    keep, out = api_headers(spec), []
    for entry in json.loads(path.read_text(encoding="utf-8"))["log"]["entries"]:
        req = entry["request"]
        url = urlsplit(req["url"])
        if url.netloc != spec["host"]:
            continue
        names = frozenset(h["name"].lower() for h in req["headers"]) & keep
        out.append(RecordedRequest(req["method"].upper(), _path_shape(_relative(url.path, spec), spec), frozenset(k for k, _ in parse_qsl(url.query)), names))
    return out


class Labeler:
    """Labels one probe from the hidden spec and the recorded requests."""

    def __init__(self, spec: dict, recorded: list[RecordedRequest]):
        self.spec, self.recorded = spec, recorded
        self.keep = api_headers(spec)
        page_param = spec["page"]["param"]
        self.paging_keys = GENERIC_PAGING_KEYS | ({page_param} if page_param else set())

    def ablated_header(self, method: str, rel_path: str, query: dict, header_names: set[str]) -> str | None:
        """The header a probe leaves out when it equals a recorded request minus exactly that one header."""
        shape, keys, mine = _path_shape(rel_path, self.spec), frozenset(query), frozenset(header_names) & self.keep
        for rec in self.recorded:
            if rec.method == method and rec.shape == shape and rec.query_keys == keys and mine < rec.headers and len(rec.headers - mine) == 1:
                return next(iter(rec.headers - mine))
        return None

    def label(self, method: str, rel_path: str, query: dict, header_names: set[str]) -> dict:
        names = {h.lower() for h in header_names}
        ablated = self.ablated_header(method, rel_path, query, names)
        if ablated:
            return {"label": "header_ablation", "ablated_header": ablated}
        if names & PRECONDITION_HEADERS:
            return {"label": "etag_test", "ablated_header": None}
        if self.paging_keys & set(query):
            return {"label": "paging_test", "ablated_header": None}
        if self._is_auth_test(method, rel_path, names):
            return {"label": "auth_test", "ablated_header": None}
        return {"label": "other", "ablated_header": None}

    def _is_auth_test(self, method: str, rel_path: str, names: set[str]) -> bool:
        if method == "POST" and rel_path.rstrip("/") == self.spec["login"]["path"] and self.spec["config"]["auth"] != "apikey_header":
            return True
        auth = self.spec["config"]["auth"]
        needed = {"apikey_header": self.spec["api_key_header"].lower(), "bearer_login": "authorization", "cookie_csrf": "cookie"}[auth]
        return needed not in names


# ---------------------------------------------------------------- the instance


class PracticeInstance:
    """`start()` the engine, send probes with `request(...)`, `stop()`. At most `budget` requests are sent;
    later ones are refused (not sent, not counted as used). `log` holds one entry per `request` call."""

    def __init__(self, task_dir: str | Path, budget: int, *, plan: PracticePlan | None = None):
        if budget < 0:
            raise ValueError("budget must be >= 0")
        self.task_dir, self.budget = Path(task_dir), budget
        self.plan = plan or plan_practice(self.task_dir)
        self.spec = _read_json(self.task_dir / "engine.json")
        self.labeler = Labeler(self.spec, recorded_requests(self.task_dir, self.spec))
        self.log: list[dict] = []
        self._engine: TierBEngine | None = None
        self._http: httpx.Client | None = None
        self._origin = ""  # loopback origin, e.g. http://127.0.0.1:5000

    # -- facts
    @property
    def base_url(self) -> str:
        return self.plan.public_url

    @property
    def credentials(self) -> dict:
        return dict(self.plan.credentials)

    @property
    def engine(self) -> TierBEngine | None:
        return self._engine

    @property
    def probes_used(self) -> int:
        return sum(1 for e in self.log if e["sent"])

    @property
    def probes_refused(self) -> int:
        return sum(1 for e in self.log if e["refused"])

    @property
    def probes_left(self) -> int:
        return self.budget - self.probes_used

    def label_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(LABELS, 0)
        for entry in self.log:
            if entry["sent"]:
                counts[entry["label"]] += 1
        return counts

    def ablated_hidden_header(self) -> bool:
        hidden = self.spec["hidden_header"]
        return bool(hidden) and any(e.get("ablated_header") == hidden["name"].lower() for e in self.log if e["sent"])

    # -- lifecycle
    def start(self) -> str:
        if self._engine is None:
            self._engine = TierBEngine(self.spec, self.plan.seed, self.plan.credentials)
            loopback = self._engine.start()
            self._origin = loopback[: len(loopback) - len(self.spec["base_path"])] if self.spec["base_path"] else loopback
            self._http = httpx.Client(trust_env=False, timeout=REQUEST_TIMEOUT_S)
        return self.base_url

    def stop(self) -> None:
        if self._http is not None:
            self._http.close()
        if self._engine is not None:
            self._engine.stop()
        self._http = self._engine = None

    def __enter__(self) -> PracticeInstance:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- requests
    def request(self, req: dict) -> dict:
        """One probe. Returns `{status, headers, body}` (plus `body_truncated` when cut), or `{error, message}`
        when the probe is refused (budget spent) or invalid (not sent, not counted)."""
        entry: dict[str, Any] = {"seq": len(self.log), "request": req, "sent": False, "refused": None, "label": None, "ablated_header": None}
        self.log.append(entry)
        try:
            parsed = self._parse(req)
        except ProbeError as exc:
            entry["refused"] = "invalid_request"
            entry["response"] = {"error": "invalid_request", "message": str(exc)}
            return entry["response"]
        if self.probes_left <= 0:
            entry["refused"] = "budget"
            entry["response"] = {"error": "probe_budget_exhausted", "message": f"all {self.budget} probes are used; submit the client now"}
            return entry["response"]
        method, rel_path, query, headers, content = parsed
        entry.update(sent=True, **self.labeler.label(method, rel_path, query, set(headers)))
        entry["probe_no"] = self.probes_used
        entry["response"] = self._send(method, rel_path, query, headers, content)
        return entry["response"]

    def _parse(self, req: Any) -> tuple[str, str, dict[str, list[str]], dict[str, str], bytes | None]:
        if not isinstance(req, dict):
            raise ProbeError("the request must be an object")
        method = str(req.get("method", "")).upper()
        if method not in METHODS:
            raise ProbeError(f"method must be one of {', '.join(METHODS)}")
        path = req.get("path")
        if not isinstance(path, str) or not path:
            raise ProbeError("path must be a non-empty string, relative to the practice base URL")
        url = urlsplit(path)
        if url.scheme or url.netloc:
            if f"{url.scheme}://{url.netloc}" != urlsplit(self.base_url).scheme + "://" + urlsplit(self.base_url).netloc:
                raise ProbeError("only the practice server can be reached")
            path = url.path + (f"?{url.query}" if url.query else "")
        elif not path.startswith("/"):
            raise ProbeError("path must start with '/'")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
            raise ProbeError("path must not contain control characters")
        route, _, inline_query = path.partition("?")
        route = self._under_base(route)
        if ".." in route.split("/"):
            raise ProbeError("path must not contain '..'")
        query: dict[str, list[str]] = {}
        for key, value in [*parse_qsl(inline_query, keep_blank_values=True), *self._query_pairs(req.get("query"))]:
            query.setdefault(key, []).append(value)
        headers = self._headers(req.get("headers"))
        return method, route, query, headers, self._content(req.get("body"), headers)

    def _under_base(self, route: str) -> str:
        """The route relative to the base URL; a route that already starts with the base path is accepted as is."""
        return _relative(route, self.spec)

    @staticmethod
    def _query_pairs(query: Any) -> list[tuple[str, str]]:
        if query is None:
            return []
        if not isinstance(query, dict):
            raise ProbeError("query must be an object")
        pairs = []
        for key, value in query.items():
            for item in value if isinstance(value, list) else [value]:
                if item is not None:
                    pairs.append((str(key), str(item).lower() if isinstance(item, bool) else str(item)))
        return pairs

    @staticmethod
    def _headers(headers: Any) -> dict[str, str]:
        if headers is None:
            return {}
        if not isinstance(headers, dict) or not all(isinstance(v, (str, int, float)) for v in headers.values()):
            raise ProbeError("headers must be an object of strings")
        out = {str(k): str(v) for k, v in headers.items() if str(k).lower() not in ("host", "content-length")}
        if not all(k.isascii() and v.isascii() for k, v in out.items()):
            raise ProbeError("header names and values must be ASCII")
        return out

    @staticmethod
    def _content(body: Any, headers: dict[str, str]) -> bytes | None:
        if body is None:
            return None
        content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        if isinstance(body, str):
            return body.encode("utf-8")
        if not isinstance(body, (dict, list)):
            raise ProbeError("body must be an object, an array or a string")
        if isinstance(body, dict) and content_type.startswith(FORM_TYPE):
            return urlencode({k: str(v) for k, v in body.items()}).encode("utf-8")
        if not content_type:
            headers["content-type"] = "application/json"
        try:
            return json.dumps(body).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProbeError(f"body is not JSON: {exc}") from exc

    def _send(self, method: str, route: str, query: dict[str, list[str]], headers: dict[str, str], content: bytes | None) -> dict:
        assert self._http is not None, "start() the practice instance first"
        url = f"{self._origin}{self.spec['base_path']}{route}"
        try:
            resp = self._http.request(method, url, params=[(k, v) for k, vs in query.items() for v in vs], headers=headers, content=content)
        except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as exc:  # a malformed probe is a result, not a crash
            return {"error": "request_failed", "message": f"{type(exc).__name__}: {exc}"}
        public = urlsplit(self.base_url)
        public_origin = f"{public.scheme}://{public.netloc}"
        merged: dict[str, str] = {}
        for key, value in resp.headers.multi_items():
            if key.lower() not in DROPPED_RESPONSE_HEADERS:
                merged[key.lower()] = f"{merged[key.lower()]}, {value}" if key.lower() in merged else value
        merged = {k: v.replace(self._origin, public_origin) for k, v in merged.items()}
        text = resp.text.replace(self._origin, public_origin)
        out: dict[str, Any] = {"status": resp.status_code, "headers": merged, "body": text[:BODY_LIMIT]}
        if len(text) > BODY_LIMIT:
            out["body_truncated"] = True
        return out
