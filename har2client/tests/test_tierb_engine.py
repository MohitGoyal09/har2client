import re

import httpx
import pytest

from test_tierb_support import CREDS, spec_for, start


@pytest.fixture
def boot():
    started = []

    def make(**kw):
        engine, base, sess = start(spec_for(**kw.pop("spec_kw", {}), **kw), **{})
        started.append((engine, sess))
        return engine, base, sess

    yield make
    for engine, sess in started:
        sess.close()
        engine.stop()


# ------------------------------------------------------------ baseline (apikey)


def test_start_returns_base_url_under_base_path(boot):
    engine, base, sess = boot()
    assert base.startswith("http://127.0.0.1:") and base.endswith(sess.spec["base_path"])


def test_apikey_needs_the_password_in_the_named_header(boot):
    engine, base, sess = boot()
    assert httpx.get(sess.path, trust_env=False).status_code == 401
    bad = {sess.spec["api_key_header"]: "wrong"}
    assert httpx.get(sess.path, headers=bad, trust_env=False).status_code == 401
    assert sess.req("GET", sess.path).status_code == 200
    assert [e["status"] for e in engine.log] == [401, 401, 200]


def test_apikey_mode_has_no_login_route(boot):
    engine, base, sess = boot()
    assert sess.login().status_code == 404
    assert engine.log[-1]["op_id"] is None


def test_baseline_list_returns_everything_as_a_bare_array(boot):
    engine, base, sess = boot()
    resp = sess.req("GET", sess.path)
    items = resp.json()
    assert isinstance(items, list) and 25 <= len(items) <= 45
    assert items == list(engine.store.values())
    assert set(items[0]) >= {"id", "created_at", sess.spec["filter"]}


def test_filter_param_filters_by_field(boot):
    engine, base, sess = boot()
    field = sess.spec["filter"]
    value = list(engine.store.values())[0][field]
    got = sess.req("GET", sess.path, params={field: value}).json()
    assert got == [i for i in engine.store.values() if i[field] == value]
    assert sess.req("GET", sess.path, params={field: "no-such-value"}).json() == []


def test_crud_round_trip(boot):
    engine, base, sess = boot()
    first = next(iter(engine.store.values()))
    assert sess.req("GET", f"{sess.path}/{first['id']}").json() == first
    assert sess.req("GET", f"{sess.path}/NOPE-1").status_code == 404

    body = {k: v for k, v in first.items() if k not in ("id", "created_at")}
    created = sess.req("POST", sess.path, json=body)
    assert created.status_code == 201
    item = created.json()
    assert item["id"] not in [first["id"]] and engine.store[item["id"]] == item
    assert list(engine.store)[-1] == item["id"]  # server order: appended
    assert sess.req("POST", sess.path, json={"only": 1}).status_code == 400

    field = sess.spec["filter"]
    patched = sess.req("PATCH", f"{sess.path}/{first['id']}", json={field: "zzz"})
    assert patched.status_code == 200 and patched.json()[field] == "zzz"
    assert engine.store[first["id"]][field] == "zzz" and engine.store[first["id"]]["id"] == first["id"]

    gone = sess.req("DELETE", f"{sess.path}/{first['id']}")
    assert gone.status_code == 204 and gone.content == b""
    assert first["id"] not in engine.store
    assert sess.req("DELETE", f"{sess.path}/{first['id']}").status_code == 404


def test_log_entries_have_tier_a_fields_plus_ts(boot):
    engine, base, sess = boot()
    first = next(iter(engine.store.values()))
    sess.req("GET", f"{sess.path}/{first['id']}")
    e = engine.log[-1]
    assert set(e) >= {"method", "path", "query", "headers", "body", "op_id", "path_params", "status", "response", "ts"}
    assert e["op_id"] == "get" and e["path"] == f"/{sess.spec['path_word']}/{first['id']}"
    assert e["path_params"] == {"id": first["id"]} and e["status"] == 200 and e["response"] == first


def test_same_seed_same_data_new_seed_new_data():
    a, b, c = (start(spec_for(), seed=s)[0] for s in (1, 1, 2))
    try:
        assert a.store == b.store and a.store != c.store
        assert not set(a.store) & set(c.store)
    finally:
        for e in (a, b, c):
            e.stop()


# ------------------------------------------------------------ hidden header


def test_hidden_header_missing_is_400_on_every_call_even_login(boot):
    engine, base, sess = boot(hidden_header="on")
    h = sess.spec["hidden_header"]
    assert httpx.get(sess.path, headers={sess.spec["api_key_header"]: CREDS["password"]}, trust_env=False).status_code == 400
    wrong = {sess.spec["api_key_header"]: CREDS["password"], h["name"]: "0.0.0"}
    assert httpx.get(sess.path, headers=wrong, trust_env=False).status_code == 400
    assert sess.req("GET", sess.path).status_code == 200


def test_hidden_header_is_checked_on_login(boot):
    engine, base, sess = boot(auth="bearer_login", hidden_header="on")
    assert httpx.post(base + sess.spec["login"]["path"], json={}, trust_env=False).status_code == 400
    assert sess.login().status_code == 200


# ------------------------------------------------------------ bearer login


@pytest.mark.parametrize("fmt", ["json", "form"])
def test_bearer_login_formats_and_token_use(boot, fmt):
    engine, base, sess = boot(auth="bearer_login", spec_kw={"edit": lambda s: s["login"].update(format=fmt)})
    assert sess.req("GET", sess.path).status_code == 401  # no token yet
    assert sess.login({"username": "x", "password": "y"}).status_code == 401
    wrong = httpx.post(base + sess.spec["login"]["path"], content=b"{}", trust_env=False)
    assert wrong.status_code in (400, 401)
    sess.do_login()
    assert sess.token
    assert sess.req("GET", sess.path).status_code == 200
    assert sess.req("GET", sess.path, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert sess.req("GET", sess.path, headers={"Authorization": "Basic " + sess.token}).status_code == 401


def test_login_rejects_the_wrong_body_format(boot):
    engine, base, sess = boot(auth="bearer_login", spec_kw={"edit": lambda s: s["login"].update(format="form")})
    lg = sess.spec["login"]
    resp = httpx.post(base + lg["path"], json={lg["user_field"]: CREDS["username"], lg["pass_field"]: CREDS["password"]}, trust_env=False)
    assert resp.status_code == 400


def test_token_never_expires_when_expiry_off(boot):
    engine, base, sess = boot(auth="bearer_login")
    sess.do_login()
    assert all(sess.req("GET", sess.path).status_code == 200 for _ in range(12))


def test_token_expires_after_four_authenticated_calls(boot):
    engine, base, sess = boot(auth="bearer_login", token_expiry="on")
    sess.do_login()
    codes = [sess.req("GET", sess.path).status_code for _ in range(6)]
    assert codes == [200, 200, 200, 200, 401, 401]
    err = sess.req("GET", sess.path).json()
    assert isinstance(err, dict) and "error" in err
    old = sess.token
    sess.do_login()
    assert sess.token != old
    assert [sess.req("GET", sess.path).status_code for _ in range(5)] == [200, 200, 200, 200, 401]


def test_unauthenticated_calls_do_not_use_up_the_token(boot):
    engine, base, sess = boot(auth="bearer_login", token_expiry="on")
    sess.do_login()
    for _ in range(3):
        httpx.get(sess.path, trust_env=False)
    assert [sess.req("GET", sess.path).status_code for _ in range(4)] == [200] * 4


# ------------------------------------------------------------ cookie + csrf


@pytest.mark.parametrize("csrf_in", ["header", "json"])
def test_cookie_login_and_csrf_on_writes(boot, csrf_in):
    engine, base, sess = boot(auth="cookie_csrf", spec_kw={"edit": lambda s: s["csrf"].update({"in": csrf_in})})
    assert sess.req("GET", sess.path).status_code == 401
    resp = sess.do_login()
    assert sess.spec["csrf"]["cookie"] in resp.headers["set-cookie"]
    assert sess.http.cookies.get(sess.spec["csrf"]["cookie"])
    if csrf_in == "header":
        assert sess.spec["csrf"]["header"] in resp.headers
    else:
        assert sess.spec["csrf"]["header"] not in resp.headers
    assert sess.req("GET", sess.path).status_code == 200  # reads need no csrf
    first = next(iter(engine.store.values()))
    body = {k: v for k, v in first.items() if k not in ("id", "created_at")}
    cookie_only = sess.http.post(sess.path, json=body, headers=sess.hidden())
    assert cookie_only.status_code == 403
    wrong = sess.http.post(sess.path, json=body, headers={sess.spec["csrf"]["header"]: "bad"})
    assert wrong.status_code == 403
    assert sess.req("POST", sess.path, json=body).status_code == 201
    assert sess.req("DELETE", f"{sess.path}/{first['id']}").status_code == 204


def test_cookie_needed_for_reads(boot):
    engine, base, sess = boot(auth="cookie_csrf")
    sess.do_login()
    assert httpx.get(sess.path, trust_env=False).status_code == 401


def test_cookie_session_expires_and_relogin_issues_new_csrf(boot):
    engine, base, sess = boot(auth="cookie_csrf", token_expiry="on")
    sess.do_login()
    old_csrf = sess.csrf
    assert [sess.req("GET", sess.path).status_code for _ in range(5)] == [200, 200, 200, 200, 401]
    sess.do_login()
    assert sess.csrf != old_csrf
    assert sess.req("GET", sess.path).status_code == 200


# ------------------------------------------------------------ etag


def test_patch_needs_if_match_from_a_get(boot):
    engine, base, sess = boot(etag_update="on")
    first = next(iter(engine.store.values()))
    url = f"{sess.path}/{first['id']}"
    field = sess.spec["filter"]
    assert sess.req("PATCH", url, json={field: "a"}).status_code == 428
    got = sess.req("GET", url)
    etag = got.headers["etag"]
    assert sess.req("PATCH", url, headers={"If-Match": '"stale"'}, json={field: "a"}).status_code == 412
    ok = sess.req("PATCH", url, headers={"If-Match": etag}, json={field: "a"})
    assert ok.status_code == 200 and ok.headers["etag"] != etag
    assert sess.req("PATCH", url, headers={"If-Match": etag}, json={field: "b"}).status_code == 412  # old etag now stale
    assert engine.store[first["id"]][field] == "a"


def test_no_etag_header_when_switch_off(boot):
    engine, base, sess = boot()
    first = next(iter(engine.store.values()))
    assert "etag" not in sess.req("GET", f"{sess.path}/{first['id']}").headers


# ------------------------------------------------------------ envelope


def test_envelope_wraps_success_bodies_not_errors(boot):
    engine, base, sess = boot(envelope="on")
    first = next(iter(engine.store.values()))
    one = sess.req("GET", f"{sess.path}/{first['id']}").json()
    assert set(one) == {"data", "meta"} and one["data"] == first and isinstance(one["meta"], dict)
    lst = sess.req("GET", sess.path).json()
    assert lst["data"] == list(engine.store.values())
    err = sess.req("GET", f"{sess.path}/NOPE").json()
    assert "data" not in err and "error" in err
    assert httpx.get(sess.path, trust_env=False).json().get("data") is None


def test_envelope_wraps_login_and_create_and_update(boot):
    engine, base, sess = boot(auth="bearer_login", envelope="on")
    login = sess.login().json()
    assert set(login) == {"data", "meta"} and sess.spec["login"]["token_field"] in login["data"]
    sess.do_login()
    first = next(iter(engine.store.values()))
    body = {k: v for k, v in first.items() if k not in ("id", "created_at")}
    made = sess.req("POST", sess.path, json=body).json()
    assert made["data"]["id"] in engine.store
    upd = sess.req("PATCH", f"{sess.path}/{first['id']}", json={sess.spec["filter"]: "q"}).json()
    assert upd["data"][sess.spec["filter"]] == "q"


# ------------------------------------------------------------ pagination


def collect_offset(sess, params=None):
    p = sess.spec["page"]["param"]
    items, offset = [], 0
    while True:
        meta = sess.req("GET", sess.path, params={**(params or {}), p: offset}).json()
        page = meta["data"] if sess.spec["config"]["envelope"] == "on" else meta[sess.spec["items_key"]]
        total = (meta["meta"] if sess.spec["config"]["envelope"] == "on" else meta)["total"]
        items += page
        offset += len(page)
        if not page or offset >= total:
            return items


@pytest.mark.parametrize("envelope", ["off", "on"])
def test_offset_pagination_pages_of_ten(boot, envelope):
    engine, base, sess = boot(pagination="offset", envelope=envelope)
    p = sess.spec["page"]["param"]
    first = sess.req("GET", sess.path).json()
    page = first["data"] if envelope == "on" else first[sess.spec["items_key"]]
    meta = first["meta"] if envelope == "on" else first
    assert len(page) == 10 and meta["total"] == len(engine.store)
    assert page == list(engine.store.values())[:10]
    second = sess.req("GET", sess.path, params={p: 10}).json()
    assert (second["data"] if envelope == "on" else second[sess.spec["items_key"]]) == list(engine.store.values())[10:20]
    assert collect_offset(sess) == list(engine.store.values())
    past = sess.req("GET", sess.path, params={p: 500}).json()
    assert (past["data"] if envelope == "on" else past[sess.spec["items_key"]]) == []
    assert sess.req("GET", sess.path, params={p: "abc"}).status_code == 400


def test_offset_pagination_keeps_the_filter(boot):
    engine, base, sess = boot(pagination="offset")
    field = sess.spec["filter"]
    value = max({i[field] for i in engine.store.values()}, key=lambda v: sum(i[field] == v for i in engine.store.values()))
    want = [i for i in engine.store.values() if i[field] == value]
    assert collect_offset(sess, {field: value}) == want


@pytest.mark.parametrize("envelope", ["off", "on"])
def test_cursor_pagination_follows_next_cursor(boot, envelope):
    engine, base, sess = boot(pagination="cursor", envelope=envelope)
    page = sess.spec["page"]
    items, cursor, pages = [], None, 0
    while True:
        params = {page["param"]: cursor} if cursor else {}
        body = sess.req("GET", sess.path, params=params).json()
        chunk = body["data"] if envelope == "on" else body[sess.spec["items_key"]]
        meta = body["meta"] if envelope == "on" else body
        assert len(chunk) <= 10
        items += chunk
        pages += 1
        cursor = meta[page["next_key"]]
        if not cursor:
            break
    assert items == list(engine.store.values()) and pages == -(-len(items) // 10)
    assert sess.req("GET", sess.path, params={page["param"]: "garbage"}).status_code == 400


def parse_next(link):
    m = re.search(r'<([^>]+)>\s*;\s*rel="?next"?', link or "")
    return m.group(1) if m else None


@pytest.mark.parametrize("envelope", ["off", "on"])
def test_link_header_pagination_with_rfc8288_next(boot, envelope):
    engine, base, sess = boot(pagination="link_header", envelope=envelope)
    field = sess.spec["filter"]
    value = list(engine.store.values())[0][field]
    for params in ({}, {field: value}):
        items, url, kw, hops = [], sess.path, {"params": params}, 0
        while url:
            resp = sess.req("GET", url, **kw)
            body = resp.json()
            items += body["data"] if envelope == "on" else body
            url, kw, hops = parse_next(resp.headers.get("link")), {}, hops + 1
        want = [i for i in engine.store.values() if not params or i[field] == value]
        assert items == want and hops == -(-len(want) // 10)
    last = sess.req("GET", sess.path)
    assert 'rel="next"' in last.headers["link"] and parse_next(last.headers["link"]).startswith("http://127.0.0.1:")


def test_pagination_none_returns_all_in_one_call(boot):
    engine, base, sess = boot(pagination="none")
    resp = sess.req("GET", sess.path)
    assert "link" not in resp.headers and len(resp.json()) == len(engine.store) > 10


def test_log_keeps_a_snapshot_of_each_response(boot):
    engine, base, sess = boot()
    first = next(iter(engine.store.values()))
    url = f"{sess.path}/{first['id']}"
    before = sess.req("GET", url).json()
    sess.req("PATCH", url, json={sess.spec["filter"]: "changed"})
    assert engine.log[0]["response"] == before and engine.log[0]["response"] != engine.store[first["id"]]


@pytest.mark.parametrize("style", ["offset", "link_header"])
@pytest.mark.parametrize("junk", ["²", "٣", "9" * 5000, "-1", " 1"])
def test_odd_numeric_page_values_are_a_logged_400_not_a_crash(boot, style, junk):
    # str.isdigit() is True for "²" but int() rejects it: that used to raise inside the handler (500, nothing logged)
    engine, base, sess = boot(pagination=style)
    param = sess.spec["page"]["param"]
    n_before = len(engine.log)
    resp = sess.req("GET", sess.path, params={param: junk})
    assert len(engine.log) == n_before + 1, "a rejected request must still be logged"
    assert resp.status_code in (200, 400) and engine.log[-1]["status"] == resp.status_code


def test_engine_bug_is_logged_as_a_500_with_its_op(boot):
    engine, base, sess = boot()

    def boom(*args, **kwargs):
        raise RuntimeError("engine bug")

    engine._op_list = boom
    resp = sess.req("GET", sess.path)
    assert resp.status_code == 500
    assert engine.log[-1]["status"] == 500 and engine.log[-1]["op_id"] == "list"
