import json
import re
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import pytest

from har2client import build_task, grade, ops, refclient

FIX = Path(__file__).parent / "fixtures"


def build(tmp_path_factory, name="bookshelf_apikey.yaml", seed=3, mutate=None):
    spec = ops.load_spec(FIX / name)
    if mutate:
        mutate(spec)
    return build_task.build_task(spec, "acme:bookshelf", tmp_path_factory.mktemp("t"), noise=0.7, seed=seed)


def query_key(spec):
    spec["components"]["securitySchemes"] = {"ApiKeyAuth": {"type": "apiKey", "in": "query", "name": "api_key"}}


def no_auth(spec):
    spec.pop("security")
    for item in spec["paths"].values():
        for op in item.values():
            if isinstance(op, dict):
                op.pop("security", None)


VARIANTS = {
    "apikey_header": dict(name="bookshelf_apikey.yaml"),
    "apikey_query": dict(name="bookshelf_apikey.yaml", mutate=query_key),
    "bearer": dict(name="bookshelf_bearer.yaml"),
    "basic": dict(name="bookshelf_basic.yaml"),
    "none": dict(name="bookshelf_apikey.yaml", mutate=no_auth),
}


@pytest.fixture(scope="module", params=list(VARIANTS))
def built(request, tmp_path_factory):
    d = build(tmp_path_factory, **VARIANTS[request.param])
    ref = d / "ref_client.py"
    ref.write_text(refclient.generate_client_source(json.loads((d / "op_table.json").read_text())))
    return d, ref


@pytest.fixture(scope="module")
def main_task(tmp_path_factory):
    d = build(tmp_path_factory)
    ref = d / "ref_client.py"
    ref.write_text(refclient.generate_client_source(json.loads((d / "op_table.json").read_text())))
    return d, ref


def task_json(d):
    return json.loads((d / "task.json").read_text())


def test_reference_client_passes_everything_for_every_auth_type(built):
    d, ref = built
    g = grade.grade(d, ref)
    assert g["pass_rate"] == 1.0, g["failure_counts"]
    assert g["passed"] == g["total"] == len(task_json(d)["goal_ops"]) * 2
    assert g["invented_endpoint_calls"] == 0
    assert g["hardcoded_secret"] is False and g["hardcoded_host"] is False
    assert g["failure_counts"] == {} and all(r["passed"] and r["reason"] is None for r in g["results"])


def mutate_source(main_task, edit):
    d, ref = main_task
    return edit(ref.read_text(), task_json(d))


def write(tmp_path, text):
    p = tmp_path / "client.py"
    p.write_text(textwrap.dedent(text))
    return p


def recorded_bodies(d):
    """Cheat data: recorded response body per op id, read from the HAR."""
    table = json.loads((d / "op_table.json").read_text())
    har = json.loads((d / "session.har").read_text())
    base = table["base_path"]
    out = {}
    for e in har["log"]["entries"]:
        path = urlparse(e["request"]["url"]).path
        if base and not path.startswith(base + "/"):
            continue
        rel = path[len(base):]
        for op in table["operations"]:
            rx = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(op["path"])) + "$"
            if op["method"] == e["request"]["method"] and re.match(rx, rel) and e["response"]["content"]["text"]:
                out.setdefault(op["op_id"], json.loads(e["response"]["content"]["text"]))
    return out


def test_har_copying_client_fails_with_return_mismatch(main_task, tmp_path):
    d, ref = main_task
    canned = recorded_bodies(d)
    src = ref.read_text() + textwrap.dedent(
        f"""

        _CANNED = {canned!r}
        _Base = Client


        class Client(_Base):
            pass


        def _wrap(name):
            orig = getattr(_Base, name)

            def method(self, *a, **k):
                orig(self, *a, **k)  # real, correct request
                return _CANNED[name]  # but the answer comes from the recording

            return method


        for _n in _CANNED:
            setattr(Client, _n, _wrap(_n))
        """
    )
    g = grade.grade(d, write(tmp_path, src))
    schema_ops = {x["method"] for x in json.loads((d / "tests.json").read_text())}
    failed = [r for r in g["results"] if not r["passed"]]
    assert failed and {r["reason"] for r in failed} == {"return_mismatch"}
    assert {r["method"] for r in failed} == set(canned) & schema_ops
    assert g["pass_rate"] < 1.0
    assert g["invented_endpoint_calls"] == 0


def test_hardcoded_recording_secret_gets_auth_401(main_task, tmp_path):
    d, ref = main_task
    secret = task_json(d)["recording_secret"]
    src = ref.read_text()
    assert "self._api_key = api_key" in src
    cheat = src.replace("self._api_key = api_key", f'self._api_key = "{secret}"')
    g = grade.grade(d, write(tmp_path, cheat))
    assert g["hardcoded_secret"] is True
    reasons = {r["method"]: r["reason"] for r in g["results"]}
    assert reasons["get_book"] == "auth_401" and reasons["list_books"] == "auth_401"
    assert reasons["health"] is None  # open endpoint still works
    assert g["pass_rate"] < 1.0


def test_hardcoded_host_is_flagged(main_task, tmp_path):
    d, ref = main_task
    g = grade.grade(d, write(tmp_path, ref.read_text() + "\n# https://api.bookshelf.example/v1\n"))
    assert g["hardcoded_host"] is True and g["pass_rate"] == 1.0


def test_hardcoded_basic_secret_parts_and_base64_are_flagged(tmp_path_factory, tmp_path):
    d = build(tmp_path_factory, "bookshelf_basic.yaml")
    ref = refclient.generate_client_source(json.loads((d / "op_table.json").read_text()))
    secret = task_json(d)["recording_secret"]
    import base64

    for hidden in (secret, secret.split(":")[1], base64.b64encode(secret.encode()).decode()):
        g = grade.grade(d, write(tmp_path, ref + f"\n_X = '{hidden}'\n"))
        assert g["hardcoded_secret"] is True, hidden


MIXED = """
import httpx


class Client:
    def __init__(self, base_url, api_key):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.h = httpx.Client(trust_env=False)

    def _hdr(self, key=None):
        return {"X-API-Key": key or self.key}

    def create_book(self, body):
        self.h.post(self.base + "/books", content=b"nope", headers=self._hdr())

    def get_book(self, book_id, **kw):
        self.h.get(f"{self.base}/books/{book_id}", headers=self._hdr("wrong"))

    def list_books(self, **kw):
        self.h.get(self.base + "/nope", headers=self._hdr())

    def category_tree(self):
        self.h.get(self.base + "/health")

    def health(self):
        return {}

    def get_stats(self):
        raise RuntimeError("boom")

    def books_by_one_author(self, author_id, page, include_draft=None):
        r = self.h.get(f"{self.base}/authors/{author_id}/books", params={"page": page, "extra": "1"}, headers=self._hdr())
        return r.json()
"""


def test_each_failure_reason(main_task, tmp_path):
    d, _ = main_task
    g = grade.grade(d, write(tmp_path, MIXED))
    by = {}
    for r in g["results"]:
        by.setdefault(r["method"], set()).add(r["reason"])
    assert by == {
        "create_book": {"bad_request_400"},
        "get_book": {"auth_401"},
        "list_books": {"not_found_404"},
        "category_tree": {"wrong_op"},
        "health": {"no_request"},
        "get_stats": {"exception"},
        "books_by_one_author": {"args_mismatch"},
    }
    assert g["pass_rate"] == 0.0
    assert g["failure_counts"]["auth_401"] == 2
    # list_books -> /nope (op None), category_tree -> /health (op in goal): only /nope is invented
    assert g["invented_endpoint_calls"] >= 2


def test_return_mismatch_and_args_mismatch_variants(main_task, tmp_path):
    d, ref = main_task
    src = ref.read_text() + textwrap.dedent(
        """

        _Base = Client


        class Client(_Base):
            def get_book(self, book_id, **kw):
                _Base.get_book(self, book_id, **kw)
                return {}

            def list_books(self, **kw):
                return {"items": [], "total": 0}
        """
    )
    g = grade.grade(d, write(tmp_path, src))
    reasons = {r["method"]: r["reason"] for r in g["results"]}
    assert reasons["get_book"] == "return_mismatch" and reasons["list_books"] == "no_request"


def test_wrong_path_value_is_args_mismatch(main_task, tmp_path):
    d, ref = main_task
    src = ref.read_text() + textwrap.dedent(
        """

        _Base = Client


        class Client(_Base):
            def get_book(self, book_id, **kw):
                return _Base.get_book(self, book_id + "zz", **kw)
        """
    )
    g = grade.grade(d, write(tmp_path, src))
    assert {r["reason"] for r in g["results"] if r["method"] == "get_book"} == {"args_mismatch"}


def test_sleeping_client_is_timeout(main_task, tmp_path):
    d, _ = main_task
    src = "import time\nclass Client:\n    def __init__(self, a, b): pass\n    def __getattr__(self, n):\n        return lambda **k: time.sleep(3600)\n"
    g = grade.grade(d, write(tmp_path, src), timeout=3)
    assert g["pass_rate"] == 0.0 and g["runner"]["timed_out"] is True
    assert {r["reason"] for r in g["results"]} == {"timeout"}


def test_raising_client_is_exception(main_task, tmp_path):
    d, _ = main_task
    src = "class Client:\n    def __init__(self, a, b): pass\n    def __getattr__(self, n):\n        raise ValueError('nope')\n"
    g = grade.grade(d, write(tmp_path, src))
    assert {r["reason"] for r in g["results"]} == {"exception"} and g["pass_rate"] == 0.0


def test_retry_after_a_failed_attempt_still_passes_but_extra_404_is_invented(main_task, tmp_path):
    d, ref = main_task
    src = ref.read_text() + textwrap.dedent(
        """

        _Base = Client


        class Client(_Base):
            def health(self):
                import httpx
                httpx.get(self._base + "/guess", headers={"X-API-Key": "x"}, trust_env=False)
                return _Base.health(self)
        """
    )
    g = grade.grade(d, write(tmp_path, src))
    assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 2


# --- lenient rules: credential in an unlisted query param; empty-object body ---

SECRET = "s3cr3t-key"
NO_AUTH = {"type": "none"}
KEY_OP = {
    "params": [
        {"name": "q", "py_name": "q", "in": "query", "required": True},
        {"name": "apikey", "py_name": "apikey", "in": "query", "required": False},
    ],
    "body": None,
}


def entry(query=None, body=None):
    return {"path_params": {}, "query": query or {}, "body": body}


def args_ok(kwargs, e, op=KEY_OP, auth=NO_AUTH, secret=SECRET):
    return grade._args_ok(op, auth, kwargs, e, secret)


def test_extra_query_param_holding_the_test_secret_is_ignored():
    assert args_ok({"q": "x"}, entry({"q": ["x"], "apikey": [SECRET]})) is None


def test_extra_query_param_with_other_value_is_still_a_mismatch():
    assert "query" in args_ok({"q": "x"}, entry({"q": ["x"], "apikey": ["other"]}))


def test_passed_query_param_must_match_even_if_it_equals_the_secret():
    assert "query" in args_ok({"q": "x", "apikey": "mine"}, entry({"q": ["x"], "apikey": [SECRET]}))
    assert args_ok({"q": "x", "apikey": "mine"}, entry({"q": ["x"], "apikey": ["mine"]})) is None


def test_extra_query_param_still_fails_when_a_real_param_is_wrong():
    assert "query" in args_ok({"q": "x"}, entry({"q": ["y"], "apikey": [SECRET]}))


def test_basic_auth_either_part_is_accepted_in_extra_query_param():
    for value in ("user:pw", "user", "pw"):
        assert args_ok({"q": "x"}, entry({"q": ["x"], "key": [value]}), secret="user:pw") is None
    assert "query" in args_ok({"q": "x"}, entry({"q": ["x"], "key": ["pu"]}), secret="user:pw")


BODY_OP = {"params": [], "body": {"required": False}}


def test_missing_body_arg_accepts_no_body_or_empty_object():
    assert args_ok({}, entry(body=None), BODY_OP) is None
    assert args_ok({}, entry(body={}), BODY_OP) is None


def test_missing_body_arg_rejects_non_empty_body():
    assert args_ok({}, entry(body={"a": 1}), BODY_OP) == "body differs"
    assert args_ok({}, entry(body=[]), BODY_OP) == "body differs"


def test_passed_body_arg_stays_exact():
    assert args_ok({"body": {"a": 1}}, entry(body={"a": 1}), BODY_OP) is None
    assert args_ok({"body": {"a": 1}}, entry(body={}), BODY_OP) == "body differs"
    assert args_ok({"body": {}}, entry(body=None), BODY_OP) == "body differs"


# ---- strict score ---------------------------------------------------------------

EXTRA_CALL = """

_Base = Client


class Client(_Base):
    def get_book(self, book_id, **kw):
        self._http.get(self._base + "/__nope")  # invented route, logged inside this test's window
        return _Base.get_book(self, book_id, **kw)
"""


def test_strict_pass_rate_equals_pass_rate_for_a_clean_client(main_task):
    d, ref = main_task
    g = grade.grade(d, ref)
    assert g["strict_pass_rate"] == g["pass_rate"] == 1.0
    assert all(r["strict_passed"] for r in g["results"])


def test_invented_call_lowers_strict_but_not_pass_rate(main_task, tmp_path):
    d, ref = main_task
    g = grade.grade(d, write(tmp_path, ref.read_text() + EXTRA_CALL))
    assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] == 2
    hit = [r for r in g["results"] if r["method"] == "get_book"]
    assert len(hit) == 2 and all(r["passed"] and not r["strict_passed"] for r in hit)
    assert g["strict_pass_rate"] == pytest.approx(1 - 2 / g["total"])


def test_strict_ignores_a_failed_test_and_counts_it_once(main_task, tmp_path):
    d, _ = main_task
    g = grade.grade(d, write(tmp_path, MIXED))
    assert g["pass_rate"] == 0.0 and g["strict_pass_rate"] == 0.0


def test_goal_ops_do_not_count_as_invented(main_task, tmp_path):
    d, ref = main_task
    goal = set(task_json(d)["goal_ops"])
    assert "health" in goal  # a goal op called from another test's window stays legitimate
    src = ref.read_text() + textwrap.dedent(
        """

        _Base = Client


        class Client(_Base):
            def get_book(self, book_id, **kw):
                _Base.health(self)
                return _Base.get_book(self, book_id, **kw)
        """
    )
    g = grade.grade(d, write(tmp_path, src))
    assert g["strict_pass_rate"] == g["pass_rate"] == 1.0


def test_pass_rate_is_unchanged_by_the_strict_rule(main_task, tmp_path):
    d, ref = main_task
    plain = grade.grade(d, ref)
    extra = grade.grade(d, write(tmp_path, ref.read_text() + EXTRA_CALL))
    assert plain["pass_rate"] == extra["pass_rate"] and plain["passed"] == extra["passed"]


LIST_OP = {"params": [{"name": "tag", "py_name": "tag", "in": "query", "required": False}], "body": None}


def test_empty_list_query_arg_sends_nothing_and_is_not_a_mismatch():
    # an empty list puts no parameter on the wire (httpx drops it), so a correct client cannot be expected to send one
    assert args_ok({"tag": []}, entry({}), LIST_OP) is None
    assert args_ok({"tag": ["a", "b"]}, entry({"tag": ["a", "b"]}), LIST_OP) is None
    assert "query" in args_ok({"tag": ["a"]}, entry({}), LIST_OP)
