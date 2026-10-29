import base64
import copy
from pathlib import Path

import httpx
import pytest

from har2client import ops
from har2client.mock import MockServer

FIX = Path(__file__).parent / "fixtures"
SECRET = "sk_" + "ab12" * 6


def table(name="bookshelf_apikey.yaml"):
    return ops.normalize(ops.load_spec(FIX / name), "acme:bookshelf")


@pytest.fixture
def server():
    started = []

    def make(t=None, seed=1, secret=SECRET):
        m = MockServer(t or table(), seed=seed, secret=secret)
        started.append(m)
        return m, m.start()

    yield make
    for m in started:
        m.stop()


def get(url, **kw):
    return httpx.get(url, trust_env=False, **kw)


def test_start_returns_base_url_under_base_path(server):
    m, base = server()
    assert base.startswith("http://127.0.0.1:") and base.endswith("/v1")


def test_404_unknown_route_wrong_method_and_wrong_base(server):
    m, base = server()
    h = {"X-API-Key": SECRET}
    assert get(base + "/nope", headers=h).status_code == 404
    assert httpx.put(base + "/books", headers=h, trust_env=False).status_code == 404
    assert get(base.removesuffix("/v1") + "/books", headers=h).status_code == 404
    assert get(base + "/books/", headers=h).status_code == 404
    assert [e["op_id"] for e in m.log] == [None, None, None, None]
    assert [e["status"] for e in m.log] == [404] * 4


def test_404_comes_before_401(server):
    m, base = server()
    assert get(base + "/nope").status_code == 404


def test_401_api_key_header(server):
    m, base = server()
    assert get(base + "/books").status_code == 401
    assert get(base + "/books", headers={"X-API-Key": "wrong"}).status_code == 401
    assert get(base + "/books", headers={"X-API-Key": SECRET, "x-trace-id": "t"}).status_code == 200
    assert [e["status"] for e in m.log] == [401, 401, 200]
    assert m.log[0]["op_id"] == "list_books"


def test_401_api_key_query(server):
    t = table()
    t["security"] = {"type": "apiKey", "in": "query", "name": "key"}
    m, base = server(t)
    assert get(base + "/books").status_code == 401
    assert get(base + "/books", params={"key": "no"}).status_code == 401
    assert get(base + "/books", headers={"key": SECRET}).status_code == 401
    assert get(base + "/books", params={"key": SECRET}).status_code == 200


def test_401_bearer(server):
    m, base = server(table("bookshelf_bearer.yaml"))
    assert get(base + "/books").status_code == 401
    assert get(base + "/books", headers={"Authorization": SECRET}).status_code == 401
    assert get(base + "/books", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert get(base + "/books", headers={"Authorization": f"Bearer {SECRET}"}).status_code == 200


def test_401_basic(server):
    secret = "user_1a2b3c4d:pw_0123456789abcdef"
    m, base = server(table("bookshelf_basic.yaml"), secret=secret)
    good = base64.b64encode(secret.encode()).decode()
    assert get(base + "/books").status_code == 401
    assert get(base + "/books", headers={"Authorization": f"Basic {good}x"}).status_code == 401
    assert get(base + "/books", headers={"Authorization": f"Bearer {good}"}).status_code == 401
    assert get(base + "/books", headers={"Authorization": f"Basic {good}"}).status_code == 200


def test_auth_not_required_op_is_open(server):
    m, base = server()
    r = get(base + "/health")
    assert r.status_code == 200 and set(r.json()) >= {"status", "uptime"}


def test_security_none_needs_no_credentials(server):
    t = table()
    t["security"] = {"type": "none", "in": None, "name": None}
    for o in t["operations"]:
        o["auth_required"] = False
    m, base = server(t)
    assert get(base + "/books").status_code == 200


def test_400_missing_required_query_param(server):
    m, base = server()
    h = {"X-API-Key": SECRET}
    assert get(base + "/authors/7/books", headers=h).status_code == 400
    assert get(base + "/authors/7/books", headers=h, params={"page": "2"}).status_code == 200
    assert m.log[0]["op_id"] == "books_by_one_author"


def test_400_missing_or_non_json_required_body(server):
    m, base = server()
    h = {"X-API-Key": SECRET}
    assert httpx.post(base + "/books", headers=h, trust_env=False).status_code == 400
    assert httpx.post(base + "/books", headers=h, content=b"not json", trust_env=False).status_code == 400
    r = httpx.post(base + "/books", headers=h, json={"title": "abc", "isbn": "1234567890"}, trust_env=False)
    assert r.status_code == 201


def test_success_status_204_has_no_body(server):
    m, base = server()
    r = httpx.delete(base + "/books/abcd", headers={"X-API-Key": SECRET}, trust_env=False)
    assert r.status_code == 204 and r.content == b""
    assert m.log[-1]["response"] is None


def test_log_fields(server):
    m, base = server()
    httpx.post(
        base + "/books?x=1&x=2",
        headers={"X-API-Key": SECRET},
        json={"title": "abc", "isbn": "1234567890"},
        trust_env=False,
    )
    get(base + "/books/ab%20cd", headers={"X-API-Key": SECRET})
    e1, e2 = m.log
    assert set(e1) >= {"method", "path", "query", "headers", "body", "op_id", "path_params", "status", "response"}
    assert e1["method"] == "POST" and e1["path"] == "/books"
    assert e1["query"] == {"x": ["1", "2"]}
    assert e1["headers"]["x-api-key"] == SECRET
    assert e1["body"] == {"title": "abc", "isbn": "1234567890"}
    assert e1["op_id"] == "create_book" and e1["status"] == 201 and e1["path_params"] == {}
    assert e1["response"]["title"]
    assert e2["op_id"] == "get_book" and e2["path_params"] == {"bookId": "ab cd"}
    assert e2["body"] is None and e2["path"] == "/books/ab%20cd"
    assert e2["response"] is not None


def test_encoded_slash_stays_inside_one_path_param(server):
    m, base = server()
    r = get(base + "/books/a%2Fb", headers={"X-API-Key": SECRET})
    assert r.status_code == 200 and m.log[0]["path_params"] == {"bookId": "a/b"}


def test_more_specific_route_wins():
    t = table()
    t["operations"].append(
        {**copy_op(t, "get_book"), "op_id": "get_book_special", "path": "/books/special"}
    )
    m = MockServer(t, seed=1, secret=SECRET)
    base = m.start()
    try:
        get(base + "/books/special", headers={"X-API-Key": SECRET})
        get(base + "/books/other", headers={"X-API-Key": SECRET})
        assert [e["op_id"] for e in m.log] == ["get_book_special", "get_book"]
    finally:
        m.stop()


def copy_op(t, op_id):
    return copy.deepcopy(next(o for o in t["operations"] if o["op_id"] == op_id))


def fetch_all(base, paths):
    return [get(base + p, headers={"X-API-Key": SECRET}).json() for p in paths]


def test_same_seed_same_data_new_seed_different_data(server):
    paths = ["/books/aaaa", "/books/bbbb", "/health", "/categories"]
    _, b1 = server(seed=7)
    _, b2 = server(seed=7)
    _, b3 = server(seed=8)
    r1, r2, r3 = fetch_all(b1, paths), fetch_all(b2, paths), fetch_all(b3, paths)
    assert r1 == r2
    assert r1 != r3


def test_data_changes_between_calls_to_same_op_but_failures_do_not_advance(server):
    m, base = server(seed=3)
    h = {"X-API-Key": SECRET}
    a = get(base + "/books/aaaa", headers=h).json()
    b = get(base + "/books/aaaa", headers=h).json()
    assert a != b
    m2, base2 = server(seed=3)
    get(base2 + "/books/aaaa")  # 401: must not use up a data slot
    assert get(base2 + "/books/aaaa", headers=h).json() == a


def test_stop_releases_port(server):
    m, base = server()
    m.stop()
    with pytest.raises(httpx.TransportError):
        get(base + "/health")


def test_204_with_a_declared_schema_still_sends_no_body(server):
    t = table()
    delete = next(o for o in t["operations"] if o["op_id"] == "delete_book")
    delete["response"] = {"status": 204, "schema": {"type": "object", "properties": {}}}
    m, base = server(t)
    r = httpx.delete(base + "/books/abcd", headers={"X-API-Key": SECRET}, trust_env=False)
    assert r.status_code == 204 and r.content == b"" and m.log[-1]["response"] is None


def test_failed_start_releases_socket_and_thread(monkeypatch):
    import uvicorn

    def boom(self, sockets=None):
        return  # the server thread ends without ever starting

    monkeypatch.setattr(uvicorn.Server, "run", boom)
    m = MockServer({"operations": [], "security": {"type": "none"}}, seed=1, secret="s")
    with pytest.raises(RuntimeError):
        m.start()
    assert m._sock is None and m._thread is None
