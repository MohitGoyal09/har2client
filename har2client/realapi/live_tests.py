"""Live tests for the real-API demo: run one client.py against the real public API and print a JSON result.

    python realapi/live_tests.py <api> <path/to/client.py>

Each test uses fresh data (random names and ids), so no recorded value helps. Reward = share of tests passed.
The demo credentials are the public ones each service documents.
"""

from __future__ import annotations

import importlib.util
import json
import random
import string
import sys
import traceback

import requests

CREDS = {
    "restful_booker": ("https://restful-booker.herokuapp.com", "admin", "password123"),
    "dummyjson": ("https://dummyjson.com", "emilys", "emilyspass"),
    "petstore": ("https://petstore3.swagger.io/api/v3", "", "special-key"),
}


def word(n: int = 8) -> str:
    return "H2c" + "".join(random.choices(string.ascii_lowercase, k=n))


def restful_booker(c, base: str) -> list:
    fn, ln = word(), word()
    body = {"firstname": fn, "lastname": ln, "totalprice": random.randint(100, 900), "depositpaid": False,
            "bookingdates": {"checkin": "2027-05-02", "checkout": "2027-05-09"}, "additionalneeds": "Quiet room"}
    state = {}

    def t_create():
        bid = c.create_booking(body=body)
        assert isinstance(bid, int), f"id is {type(bid).__name__}"
        state["id"] = bid

    def t_get():
        got = c.get_booking(id=state["id"])
        assert all(got[k] == v for k, v in body.items()), got

    def t_filter():
        ids = c.list_bookings(firstname=fn, lastname=ln)
        assert ids == [state["id"]], ids

    def t_list():
        ids = c.list_bookings()
        assert isinstance(ids, list) and all(isinstance(i, int) for i in ids) and state["id"] in ids

    def t_update():
        new = {**body, "totalprice": body["totalprice"] + 50, "additionalneeds": "Breakfast"}
        got = c.update_booking(id=state["id"], body=new)
        assert got["totalprice"] == new["totalprice"] and got["additionalneeds"] == "Breakfast", got
        assert c.get_booking(id=state["id"])["totalprice"] == new["totalprice"]

    def t_delete():
        assert c.delete_booking(id=state["id"]) is None
        r = requests.get(f"{base}/booking/{state['id']}", headers={"Accept": "application/json"}, timeout=30)
        assert r.status_code == 404, r.status_code

    return [t_create, t_get, t_filter, t_list, t_update, t_delete]


def dummyjson(c, base: str) -> list:
    def ref(path: str) -> list:
        sep = "&" if "?" in path else "?"
        return requests.get(f"{base}{path}{sep}limit=0", timeout=30).json()["products"]

    def t_me():
        assert c.get_me()["username"] == "emilys"

    def t_all():
        got, want = c.list_products(), ref("/products")
        assert [p["id"] for p in got] == [p["id"] for p in want], (len(got), len(want))

    def t_category():
        got, want = c.list_products(category="smartphones"), ref("/products/category/smartphones")
        assert [p["id"] for p in got] == [p["id"] for p in want], (len(got), len(want))

    def t_search():
        got, want = c.search_products(query="phone"), ref("/products/search?q=phone")
        assert [p["id"] for p in got] == [p["id"] for p in want], (len(got), len(want))

    def t_get():
        want = requests.get(f"{base}/products/12", timeout=30).json()
        got = c.get_product(id=12)
        assert got["id"] == 12 and got["title"] == want["title"], got.get("title")

    def t_add():
        title = word()
        got = c.add_product(body={"title": title, "price": 31})
        assert got["title"] == title and "id" in got, got

    return [t_me, t_all, t_category, t_search, t_get, t_add]


def petstore(c, base: str) -> list:
    pid = random.randint(9_100_000, 9_899_999)
    body = {"id": pid, "name": word(), "category": {"id": 2, "name": "Cats"}, "photoUrls": ["https://example.com/p.jpg"],
            "tags": [{"id": 3, "name": "calm"}], "status": "pending"}

    def t_create():
        got = c.create_pet(body=body)
        assert got["id"] == pid and got["name"] == body["name"], got

    def t_get():
        got = c.get_pet(id=pid)
        assert got["name"] == body["name"] and got["status"] == "pending", got

    def t_find():
        got = c.find_pets_by_status(status="pending")
        assert isinstance(got, list) and pid in [p.get("id") for p in got] and all(p.get("status") == "pending" for p in got)

    def t_update():
        new = {**body, "name": word(), "status": "sold"}
        got = c.update_pet(body=new)
        assert got["status"] == "sold" and got["name"] == new["name"], got
        assert c.get_pet(id=pid)["status"] == "sold"

    def t_delete():
        assert c.delete_pet(id=pid) is None
        r = requests.get(f"{base}/pet/{pid}", headers={"Accept": "application/json"}, timeout=30)
        assert r.status_code == 404, r.status_code

    return [t_create, t_get, t_find, t_update, t_delete]


SUITES = {"restful_booker": restful_booker, "dummyjson": dummyjson, "petstore": petstore}


def main() -> int:
    api, client_path = sys.argv[1], sys.argv[2]
    base, user, pw = CREDS[api]
    results = []
    try:
        spec = importlib.util.spec_from_file_location("client_under_test", client_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        client = mod.Client(base_url=base, username=user, password=pw)
        tests = SUITES[api](client, base)
    except Exception as exc:  # the client does not load or construct: every test fails
        print(json.dumps({"api": api, "passed": 0, "total": len(SUITES[api](None, base)), "error": f"load: {exc!r}"[:300], "tests": []}))
        return 0
    for t in tests:
        try:
            t()
            results.append({"test": t.__name__, "ok": True})
        except Exception as exc:
            results.append({"test": t.__name__, "ok": False, "error": (repr(exc) or traceback.format_exc())[:300]})
    print(json.dumps({"api": api, "passed": sum(r["ok"] for r in results), "total": len(results), "tests": results}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
