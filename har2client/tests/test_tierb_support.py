"""Shared helpers for the Tier B tests (imported by the other test_tierb_* files)."""

import copy

import httpx

from har2client.tierb.engine import TierBEngine
from har2client.tierb.spec import make_spec

BASELINE = {
    "auth": "apikey_header",
    "pagination": "none",
    "token_expiry": "off",
    "etag_update": "off",
    "hidden_header": "off",
    "envelope": "off",
}
CREDS = {"username": "test.user", "password": "pw_test_0123456789abcdef"}
HEADERS_OFF = {"accept": "application/json"}


def config(**over):
    return {**BASELINE, **over}


def spec_for(domain="shipments", task_seed=5, edit=None, **over):
    spec = make_spec(domain, config(**over), task_seed)
    spec = copy.deepcopy(spec)
    if edit:
        edit(spec)
    return spec


class Session:
    """A tiny hand-written client: knows the spec, speaks raw HTTP, so engine tests do not depend on the ref client."""

    def __init__(self, engine, base, spec, creds=CREDS):
        self.engine, self.base, self.spec, self.creds = engine, base, spec, creds
        self.http = httpx.Client(trust_env=False, timeout=10)
        self.token = None
        self.csrf = None

    @property
    def path(self):
        return self.base + "/" + self.spec["path_word"].strip("/")

    def hidden(self):
        h = self.spec["hidden_header"]
        return {h["name"]: h["value"]} if h else {}

    def login(self, creds=None, headers=None):
        creds = creds or self.creds
        lg = self.spec["login"]
        payload = {lg["user_field"]: creds["username"], lg["pass_field"]: creds["password"]}
        kw = {"json": payload} if lg["format"] == "json" else {"data": payload}
        resp = self.http.post(self.base + lg["path"], headers={**self.hidden(), **(headers or {})}, **kw)
        return resp

    def auth_headers(self, write=False):
        mode = self.spec["config"]["auth"]
        h = dict(self.hidden())
        if mode == "apikey_header":
            h[self.spec["api_key_header"]] = self.creds["password"]
        elif mode == "bearer_login" and self.token:
            h["Authorization"] = "Bearer " + self.token
        elif mode == "cookie_csrf" and write and self.csrf:
            h[self.spec["csrf"]["header"]] = self.csrf
        return h

    def do_login(self):
        resp = self.login()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if self.spec["config"]["envelope"] == "on":
            body = body["data"]
        if self.spec["config"]["auth"] == "bearer_login":
            self.token = body[self.spec["login"]["token_field"]]
        else:
            csrf = self.spec["csrf"]
            self.csrf = resp.headers[csrf["header"]] if csrf["in"] == "header" else body[csrf["field"]]
        return resp

    def req(self, method, url, write=None, **kw):
        write = method in ("POST", "PATCH", "DELETE") if write is None else write
        headers = {**self.auth_headers(write), **kw.pop("headers", {})}
        return self.http.request(method, url, headers=headers, **kw)

    def close(self):
        self.http.close()


def start(spec, seed=21, creds=CREDS, n_items=None):
    engine = TierBEngine(spec, seed, creds, n_items=n_items)
    base = engine.start()
    return engine, base, Session(engine, base, spec, creds)


# ------------------------------------------------------------ built tasks (cached for the whole test run)
import atexit  # noqa: E402
import functools  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from har2client.tierb.build import build_tierb_task  # noqa: E402

_ROOT = Path(tempfile.mkdtemp(prefix="h2c_tierb_tests_"))
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)


@functools.lru_cache(maxsize=None)
def build_cached(domain="shipments", seed=7, index=0, **over) -> Path:
    row = {"index": index, "domain": domain, "config": config(**over), "seed": seed}
    out = _ROOT / f"{len(list(_ROOT.iterdir()))}"
    return build_tierb_task(row, out, negatives=False)


def read_json(path):
    return json.loads(Path(path).read_text())


def write_client(tmp_path, source, name="client.py"):
    path = Path(tmp_path) / name
    path.write_text(source)
    return path


def api_entries(task_dir):
    """HAR entries that are real API calls (host + base path), in time order."""
    spec = read_json(Path(task_dir) / "engine.json")
    har = read_json(Path(task_dir) / "session.har")
    prefix = f"https://{spec['host']}{spec['base_path']}/"
    return [e for e in har["log"]["entries"] if e["request"]["url"].startswith(prefix)]


def hdr(entry_part, name):
    return next((h["value"] for h in entry_part["headers"] if h["name"].lower() == name.lower()), None)


# ------------------------------------------------------------ light tasks (no recording: spec, tests, credentials only)
import random  # noqa: E402

from har2client.tierb.grader import grade_tierb  # noqa: E402
from har2client.tierb.refclient import generate_client_source  # noqa: E402
from har2client.tierb.spec import make_credentials  # noqa: E402
from har2client.tierb.testgen import make_tests  # noqa: E402


def light_task(tmp_path, domain="shipments", seed=7, test_seed=4242, **over) -> Path:
    """A task directory with what `grade_tierb` reads (engine.json, tests.json, task.json): no HAR, so it builds in
    milliseconds. Same hidden-test generator and engine as a built task."""
    spec = make_spec(domain, config(**over), seed)
    rng = random.Random(f"light:{seed}")
    recording, test = make_credentials(spec, rng), make_credentials(spec, rng)
    d = Path(tmp_path) / f"light_{domain}_{seed}_{abs(hash(json.dumps(over, sort_keys=True))) % 10**8}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "engine.json").write_text(json.dumps(spec))
    (d / "tests.json").write_text(json.dumps(make_tests(spec, test_seed)))
    (d / "task.json").write_text(
        json.dumps(
            {"task_id": d.name, "tier": "B", "config": spec["config"], "seeds": {"test": test_seed}, "credentials": {"recording": recording, "test": test}}
        )
    )
    return d


def grade_flaws(tmp_path, task_dir, flaws=()):
    """Grade the generated client with these flaws on the task."""
    spec = read_json(Path(task_dir) / "engine.json")
    client = write_client(tmp_path, generate_client_source(spec, flaws), name=f"client_{'_'.join(flaws) or 'ref'}.py")
    return grade_tierb(task_dir, client)
