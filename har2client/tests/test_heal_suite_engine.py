"""Tier B engine option `error_detail`: `verbose` (default, unchanged) and `terse` (same status codes, generic bodies)."""

import httpx
import pytest

from har2client.tierb.engine import ERROR_DETAILS, TERSE_CODES, TierBEngine
from test_tierb_support import CREDS, Session, spec_for


def run(error_detail, script, **over):
    """Start an engine, call `script(session)`, return the (status, body) of every request in the engine log."""
    spec = spec_for(**over)
    engine = TierBEngine(spec, 21, CREDS) if error_detail is None else TierBEngine(spec, 21, CREDS, error_detail=error_detail)
    base = engine.start()
    session = Session(engine, base, spec)
    try:
        script(session)
        return [(e["status"], e["response"]) for e in engine.log]
    finally:
        session.close()
        engine.stop()


def first_item_path(s):
    return s.path + "/" + next(iter(s.engine.store))


def unauthenticated(s):
    httpx.get(s.path, trust_env=False)
    httpx.get(s.path, headers={s.spec["api_key_header"]: "wrong"}, trust_env=False)


def missing_header(s):
    s.http.get(s.path, headers={s.spec["api_key_header"]: s.creds["password"]})


def etag_errors(s):
    s.req("PATCH", first_item_path(s), json={})
    s.req("PATCH", first_item_path(s), json={}, headers={"If-Match": '"nope"'})


def not_found_and_bad_input(s):
    s.req("GET", s.path + "/NO-SUCH-ID")
    s.req("POST", s.path, json={})
    s.req("POST", s.path, content=b"not json", headers={"content-type": "application/json"})
    s.http.get(s.base + "/no/such/route")


def bad_login_and_expiry(s):
    s.login({"username": "x", "password": "y"})
    s.do_login()
    for _ in range(s.spec["expiry_limit"] + 1):
        s.req("GET", s.path)


def csrf_missing(s):
    s.do_login()
    s.http.post(s.path, json={}, headers={"cookie": s.http.cookies.jar and "; ".join(f"{c.name}={c.value}" for c in s.http.cookies.jar) or ""})


def bad_page(s):
    s.req("GET", s.path, params={s.spec["page"]["param"]: "zzz"})


CASES = {
    "api_key": (unauthenticated, {}),
    "hidden_header": (missing_header, {"hidden_header": "on"}),
    "etag": (etag_errors, {"etag_update": "on"}),
    "not_found_and_bad_input": (not_found_and_bad_input, {}),
    "login_and_expiry": (bad_login_and_expiry, {"auth": "bearer_login", "token_expiry": "on"}),
    "csrf": (csrf_missing, {"auth": "cookie_csrf"}),
    "bad_page": (bad_page, {"pagination": "offset"}),
}


@pytest.mark.parametrize("case", CASES)
def test_terse_errors_keep_the_status_codes_and_drop_the_message(case):
    script, over = CASES[case]
    verbose, terse = run("verbose", script, **over), run("terse", script, **over)
    assert [s for s, _ in verbose] == [s for s, _ in terse]
    errors = [(v, t) for (status, v), (_, t) in zip(verbose, terse) if status >= 400]
    assert errors
    for v, t in errors:
        assert set(t) == {"error"} and t["error"] == TERSE_CODES[v["error"]]
    assert [t for (s, _), (_, t) in zip(verbose, terse) if s < 400] == [v for s, v in verbose if s < 400]  # successes unchanged


def test_the_terse_codes_are_the_generic_ones_of_the_design():
    _, over = CASES["etag"]
    bodies = {s: b["error"] for s, b in run("terse", etag_errors, **over)}
    assert bodies == {428: "precondition_required", 412: "precondition_failed"}
    bodies = run("terse", bad_login_and_expiry, **CASES["login_and_expiry"][1])
    assert {"unauthorized", "token_invalid"} <= {b["error"] for s, b in bodies if s >= 400}
    assert [b for s, b in run("terse", missing_header, hidden_header="on") if s >= 400] == [{"error": "bad_request"}]


def test_verbose_is_the_default_and_its_bodies_are_unchanged():
    _, over = CASES["hidden_header"]
    default = run(None, missing_header, **over)
    assert default == run("verbose", missing_header, **over)
    name = spec_for(**over)["hidden_header"]["name"]
    assert default[0] == (400, {"error": "missing_header", "message": f"header {name} is required"})
    etag = run(None, etag_errors, etag_update="on")
    assert etag[0][1] == {"error": "precondition_required", "message": "If-Match header is required; GET the item for its ETag"}


def test_the_spec_can_ask_for_terse_and_the_argument_wins():
    spec = {**spec_for(), "error_detail": "terse"}
    assert TierBEngine(spec, 1, CREDS).error_detail == "terse"
    assert TierBEngine(spec, 1, CREDS, error_detail="verbose").error_detail == "verbose"
    assert TierBEngine(spec_for(), 1, CREDS).error_detail == "verbose"


def test_an_unknown_error_detail_is_refused():
    with pytest.raises(ValueError, match="error_detail"):
        TierBEngine(spec_for(), 1, CREDS, error_detail="chatty")


def test_every_error_code_of_the_engine_has_a_terse_code():
    import inspect
    import re

    from har2client.tierb import engine

    codes = set(re.findall(r'_error\(\d+, "(\w+)"', inspect.getsource(engine)))
    assert codes and codes <= set(TERSE_CODES) and set(ERROR_DETAILS) == {"verbose", "terse"}
