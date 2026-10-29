import pytest

from har2client import grade as grade_mod
from har2client.tierb.refclient import FLAWS, generate_client_source
from har2client.tierb.grader import grade_tierb
from test_tierb_support import build_cached, read_json, write_client

ALL_ON = dict(auth="bearer_login", pagination="cursor", token_expiry="on", etag_update="on", hidden_header="on", envelope="on")
CONFIGS = {
    "baseline": {},
    "bearer_offset_expiry": dict(auth="bearer_login", pagination="offset", token_expiry="on"),
    "cookie_link_header_envelope": dict(auth="cookie_csrf", pagination="link_header", envelope="on"),
    "cookie_expiry_etag": dict(auth="cookie_csrf", token_expiry="on", etag_update="on"),
    "apikey_cursor_hidden": dict(pagination="cursor", hidden_header="on"),
    "all_on_bearer": ALL_ON,
    "all_on_cookie": {**ALL_ON, "auth": "cookie_csrf", "pagination": "link_header"},
}


def spec_of(task_dir):
    return read_json(task_dir / "engine.json")


@pytest.mark.parametrize("name", list(CONFIGS))
def test_reference_client_scores_one(name):
    d = build_cached(**CONFIGS[name])
    g = grade_tierb(d, d / "ref_client.py")
    assert g["pass_rate"] == 1.0 and g["failure_counts"] == {}, g["results"]
    assert g["total"] == 7 and g["passed"] == 7
    assert g["invented_endpoint_calls"] == 0 and g["hardcoded_secret"] is False and g["hardcoded_host"] is False


def test_call_attribution_does_not_depend_on_wall_clock_resolution(monkeypatch):
    import time

    d = build_cached()
    # The server's wall clock may tick more slowly than successive client calls
    # (Windows Python 3.12). Attribution must use the shared performance clock.
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    g = grade_tierb(d, d / "ref_client.py")
    assert g["pass_rate"] == 1.0, g["results"]


def test_grade_dispatches_on_tier_and_keeps_tier_a_keys(tmp_path):
    d = build_cached(**CONFIGS["bearer_offset_expiry"])
    g = grade_mod.grade(d, d / "ref_client.py")
    assert g["tier"] == "B" and g["config"] == read_json(d / "task.json")["config"]
    tier_a_keys = {
        "task_id", "client", "pass_rate", "passed", "total", "failure_counts", "results", "invented_endpoint_calls",
        "invented_endpoints", "hardcoded_secret", "hardcoded_host", "runner", "requests_logged", "duration_s",
    }
    assert tier_a_keys <= set(g)
    assert set(g["results"][0]) == {"index", "method", "kwargs", "passed", "reason", "detail", "invented_calls", "strict_passed"}


NEGATIVE = {
    "ignore_pagination": ("bearer_offset_expiry", "incomplete_list"),
    "no_relogin": ("bearer_offset_expiry", "auth_401"),
    "no_if_match": ("cookie_expiry_etag", "precondition_428"),
    "drop_hidden_header": ("apikey_cursor_hidden", "bad_request_400"),
    "return_envelope": ("cookie_link_header_envelope", "return_mismatch"),
    "no_csrf": ("cookie_link_header_envelope", "auth_401"),
}


# flaws of the hard-repair switches: their negative tests are in test_tierb_drift.py and test_tierb_protocols.py
HARD_FLAWS = {
    "no_drift_rename", "no_drift_cents", "no_drift_epoch", "no_drift_nest", "no_drift_enum", "no_rate_limit_retry", "hammer_rate_limit",
    "no_job_poll", "no_refresh", "no_idempotency_key", "no_moved_path",
}


def test_every_flaw_has_a_negative_test():
    assert set(NEGATIVE) | HARD_FLAWS == set(FLAWS) and not set(NEGATIVE) & HARD_FLAWS


@pytest.mark.parametrize("flaw", list(NEGATIVE))
def test_broken_client_fails_with_its_reason(tmp_path, flaw):
    name, reason = NEGATIVE[flaw]
    d = build_cached(**CONFIGS[name])
    bad = write_client(tmp_path, generate_client_source(spec_of(d), (flaw,)))
    g = grade_tierb(d, bad)
    assert g["pass_rate"] < 1.0
    assert reason in g["failure_counts"], g["failure_counts"]


def test_ignoring_pagination_only_fails_the_list_tests(tmp_path):
    d = build_cached(**CONFIGS["bearer_offset_expiry"])
    g = grade_tierb(d, write_client(tmp_path, generate_client_source(spec_of(d), ("ignore_pagination",))))
    failed = {r["index"]: r["reason"] for r in g["results"] if not r["passed"]}
    assert failed[0] == "incomplete_list"
    assert set(failed.values()) <= {"incomplete_list"} and set(failed) <= {0, 1}


def test_no_relogin_client_fails_once_the_token_expires(tmp_path):
    d = build_cached(**CONFIGS["bearer_offset_expiry"])
    g = grade_tierb(d, write_client(tmp_path, generate_client_source(spec_of(d), ("no_relogin",))))
    reasons = [r["reason"] for r in g["results"]]
    assert set(reasons) <= {None, "auth_401"} and reasons.count("auth_401") >= 3
    assert reasons[-1] == "auth_401" and g["failure_counts"] == {"auth_401": reasons.count("auth_401")}


def test_no_if_match_client_fails_update_only(tmp_path):
    d = build_cached(**CONFIGS["cookie_expiry_etag"])
    g = grade_tierb(d, write_client(tmp_path, generate_client_source(spec_of(d), ("no_if_match",))))
    update = next(r for r in g["results"] if r["method"].startswith("update_"))
    assert update["reason"] == "precondition_428" and g["passed"] == g["total"] - 1


def test_envelope_returning_client_fails_item_methods_only(tmp_path):
    d = build_cached(**CONFIGS["cookie_link_header_envelope"])
    g = grade_tierb(d, write_client(tmp_path, generate_client_source(spec_of(d), ("return_envelope",))))
    bad = {r["method"].split("_")[0] for r in g["results"] if not r["passed"]}
    assert bad == {"get", "create", "update"} and g["failure_counts"] == {"return_mismatch": 4}


# ------------------------------------------------------------ semantic checks with hand-written clients

HEAD = "import httpx\n\nclass Client:\n    def __init__(self, base_url, username, password):\n        self.b, self.u, self.p = base_url, username, password\n"


def test_client_that_sends_nothing_is_no_request(tmp_path):
    d = build_cached()
    spec = spec_of(d)
    code = HEAD + f"    def {spec['domain'] and 'list_' + spec['domain']}(self, {spec['filter']}=None):\n        return []\n"
    g = grade_tierb(d, write_client(tmp_path, code))
    assert g["results"][0]["reason"] == "no_request"


def test_create_that_never_posts_is_state_mismatch(tmp_path):
    d = build_cached()
    spec = spec_of(d)
    base = generate_client_source(spec)
    fake = base.replace("resp = self._send('POST', ", "resp = self._send('GET', ", 1).replace("body=body)", "params=None)", 1)
    assert fake != base
    g = grade_tierb(d, write_client(tmp_path, fake))
    create = next(r for r in g["results"] if r["method"].startswith("create_"))
    assert create["reason"] == "state_mismatch"


def test_delete_that_returns_something_is_return_mismatch(tmp_path):
    d = build_cached()
    src = generate_client_source(spec_of(d))
    assert src.count("        return None\n") == 1
    g = grade_tierb(d, write_client(tmp_path, src.replace("        return None\n", "        return {}\n")))
    delete = next(r for r in g["results"] if r["method"].startswith("delete_"))
    assert delete["reason"] == "return_mismatch"


def test_unknown_routes_count_as_invented_and_secrets_are_flagged(tmp_path):
    d = build_cached(**CONFIGS["bearer_offset_expiry"])
    task = read_json(d / "task.json")
    spec = spec_of(d)
    src = generate_client_source(spec) + f"\n_OLD = {task['credentials']['recording']['password']!r}\n_H = {spec['host']!r}\n"
    src = src.replace("    def close(self)", "    def poke(self):\n        self._http.get(self._base + '/__debug')\n\n    def close(self)")
    src = src.replace("        write = method != \"GET\"", "        write = method != \"GET\"\n        if method == 'DELETE':\n            self._http.get(self._base + '/__debug')")
    g = grade_tierb(d, write_client(tmp_path, src))
    assert g["invented_endpoint_calls"] >= 1 and any("__debug" in e for e in g["invented_endpoints"])
    assert g["hardcoded_secret"] is True and g["hardcoded_host"] is True
    assert g["pass_rate"] == 1.0  # an extra call does not fail a test; it is only counted


def test_failures_are_attributed_to_the_window_of_each_call(tmp_path):
    d = build_cached(**CONFIGS["apikey_cursor_hidden"])
    g = grade_tierb(d, write_client(tmp_path, generate_client_source(spec_of(d), ("drop_hidden_header",))))
    assert [r["reason"] for r in g["results"]] == ["bad_request_400"] * 7
    assert g["requests_logged"] >= 7


def test_timeout_when_client_hangs(tmp_path):
    d = build_cached()
    spec = spec_of(d)
    code = HEAD + f"    def list_{spec['domain']}(self, {spec['filter']}=None):\n        import time\n        time.sleep(30)\n"
    g = grade_tierb(d, write_client(tmp_path, code), timeout=3)
    assert g["runner"]["timed_out"] and g["results"][0]["reason"] == "timeout"


def test_strict_pass_rate_penalizes_invented_routes_inside_a_test_window(tmp_path):
    d = build_cached(**CONFIGS["bearer_offset_expiry"])
    clean = grade_tierb(d, d / "ref_client.py")
    assert clean["strict_pass_rate"] == clean["pass_rate"] == 1.0
    src = generate_client_source(spec_of(d))
    src = src.replace("        write = method != \"GET\"", "        write = method != \"GET\"\n        if method == 'DELETE':\n            self._http.get(self._base + '/__debug')")
    g = grade_tierb(d, write_client(tmp_path, src))
    assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] >= 1
    assert g["strict_pass_rate"] < 1.0
    bad = [r for r in g["results"] if not r["strict_passed"]]
    assert bad and all(r["passed"] for r in bad)
    assert all(r["method"].startswith("delete_") for r in bad)


def test_strict_counts_an_invented_call_made_in_the_constructor(tmp_path):
    d = build_cached()
    src = generate_client_source(spec_of(d))
    anchor = "        self._http = httpx.Client(timeout=30.0, trust_env=False)\n"
    assert anchor in src
    src = src.replace(anchor, anchor + "        self._http.get(self._base + '/__ctor')  # before any test window opens\n", 1)
    g = grade_tierb(d, write_client(tmp_path, src))
    assert g["pass_rate"] == 1.0 and g["invented_endpoint_calls"] >= 1
    assert g["strict_pass_rate"] < 1.0


def test_update_that_also_creates_another_item_is_state_mismatch(tmp_path):
    d = build_cached()
    spec = spec_of(d)
    src = generate_client_source(spec)
    patch = "        resp = self._send('PATCH', self._item_path(id), body=changes, headers=headers)\n"
    assert src.count(patch) == 1
    extra = (
        "        copy = dict(self._data(resp)); copy.pop('id'); copy.pop('created_at')\n"
        f"        self._send('POST', {'/' + spec['path_word'].strip('/')!r}, body=copy)\n"
    )
    g = grade_tierb(d, write_client(tmp_path, src.replace(patch, patch + extra)))
    update = next(r for r in g["results"] if r["method"].startswith("update_"))
    assert update["reason"] == "state_mismatch" and "unrequested write" in update["detail"]
    assert g["failure_counts"] == {"state_mismatch": 1}


def test_delete_that_also_deletes_another_item_is_state_mismatch(tmp_path):
    d = build_cached()
    spec = spec_of(d)
    src = generate_client_source(spec)
    anchor = "        self._send('DELETE', self._item_path(id))\n"
    assert src.count(anchor) == 1
    other = "        first = self._send('GET', '/' + " + repr(spec["path_word"].strip("/")) + ").json()[0]['id']\n"
    other += "        self._send('DELETE', self._item_path(first if first != id else self._send('GET', '/" + spec["path_word"].strip("/") + "').json()[1]['id']))\n"
    g = grade_tierb(d, write_client(tmp_path, src.replace(anchor, other + anchor)))
    delete = next(r for r in g["results"] if r["method"].startswith("delete_"))
    assert delete["reason"] == "state_mismatch"
