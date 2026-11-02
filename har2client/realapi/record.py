"""Record real browser sessions (HAR) against three public practice APIs.

Chromium (Playwright) opens each API's own web page, so the HAR carries the page's real noise (HTML, CSS,
scripts, fonts), then the page runs the API calls with fetch(). The recorded data differs from the data the
live tests use. Credentials are the public demo credentials each service documents.

    uv run --with playwright python realapi/record.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent

SESSIONS = {
    "restful_booker": ("https://restful-booker.herokuapp.com/apidoc/index.html", """
async () => {
  const B = 'https://restful-booker.herokuapp.com';
  const j = {'Content-Type': 'application/json', 'Accept': 'application/json'};
  const auth = await (await fetch(B + '/auth', {method: 'POST', headers: j, body: JSON.stringify({username: 'admin', password: 'password123'})})).json();
  await (await fetch(B + '/booking?firstname=Sally', {headers: {'Accept': 'application/json'}})).text();
  const created = await (await fetch(B + '/booking', {method: 'POST', headers: j, body: JSON.stringify({firstname: 'Ada', lastname: 'Rowe', totalprice: 150, depositpaid: true, bookingdates: {checkin: '2026-03-01', checkout: '2026-03-04'}, additionalneeds: 'Late checkout'})})).json();
  const id = created.bookingid;
  await (await fetch(B + '/booking/' + id, {headers: {'Accept': 'application/json'}})).text();
  await (await fetch(B + '/booking?firstname=Ada&lastname=Rowe', {headers: {'Accept': 'application/json'}})).text();
  document.cookie = 'token=' + auth.token + '; path=/';  // the web app keeps the login token as a cookie
  await (await fetch(B + '/booking/' + id, {method: 'PUT', headers: j, credentials: 'include', body: JSON.stringify({firstname: 'Ada', lastname: 'Rowe', totalprice: 175, depositpaid: true, bookingdates: {checkin: '2026-03-01', checkout: '2026-03-05'}, additionalneeds: 'Breakfast'})})).text();
  await (await fetch(B + '/booking/' + id, {method: 'DELETE', headers: {'Content-Type': 'application/json'}, credentials: 'include'})).text();
}"""),
    "dummyjson": ("https://dummyjson.com/docs/products", """
async () => {
  const B = 'https://dummyjson.com';
  const login = await (await fetch(B + '/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: 'emilys', password: 'emilyspass', expiresInMins: 30})})).json();
  const bearer = {'Authorization': 'Bearer ' + login.accessToken};
  await (await fetch(B + '/auth/me', {headers: bearer})).text();
  await (await fetch(B + '/products?limit=30&skip=0', {headers: bearer})).text();
  await (await fetch(B + '/products?limit=30&skip=30', {headers: bearer})).text();
  await (await fetch(B + '/products/category/laptops?limit=30&skip=0', {headers: bearer})).text();
  await (await fetch(B + '/products/search?q=watch&limit=30&skip=0', {headers: bearer})).text();
  await (await fetch(B + '/products/7', {headers: bearer})).text();
  await (await fetch(B + '/products/add', {method: 'POST', headers: {...bearer, 'Content-Type': 'application/json'}, body: JSON.stringify({title: 'Desk lamp', price: 24})})).text();
}"""),
    "petstore": ("https://petstore3.swagger.io/", """
async () => {
  const B = 'https://petstore3.swagger.io/api/v3';
  const j = {'Content-Type': 'application/json', 'Accept': 'application/json'};
  const id = 7300000 + Math.floor(Math.random() * 99999);
  await (await fetch(B + '/pet', {method: 'POST', headers: j, body: JSON.stringify({id: id, name: 'Biscuit', category: {id: 1, name: 'Dogs'}, photoUrls: ['https://example.com/biscuit.jpg'], tags: [{id: 1, name: 'friendly'}], status: 'available'})})).text();
  await (await fetch(B + '/pet/' + id, {headers: {'Accept': 'application/json'}})).text();
  await (await fetch(B + '/pet/findByStatus?status=pending', {headers: {'Accept': 'application/json'}})).text();
  await (await fetch(B + '/pet', {method: 'PUT', headers: j, body: JSON.stringify({id: id, name: 'Biscuit', category: {id: 1, name: 'Dogs'}, photoUrls: ['https://example.com/biscuit.jpg'], tags: [{id: 1, name: 'friendly'}], status: 'sold'})})).text();
  await (await fetch(B + '/pet/' + id, {method: 'DELETE', headers: {'api_key': 'special-key', 'Accept': 'application/json'}})).text();
}"""),
}


API_PREFIXES = ("https://restful-booker.herokuapp.com/booking", "https://restful-booker.herokuapp.com/auth",
                "https://dummyjson.com/auth", "https://dummyjson.com/products", "https://petstore3.swagger.io/api")
NOISE_BODY_CHARS = 300  # the generated tasks keep short bodies on noise entries; real JS, CSS and fonts are cut to match


def shorten_noise(har_path: Path) -> list[dict]:
    """Cut the response bodies of non-API entries (scripts, styles, fonts, images) to NOISE_BODY_CHARS. API entries stay whole."""
    har = json.loads(har_path.read_text())
    for e in har["log"]["entries"]:
        if e["request"]["url"].startswith(API_PREFIXES):
            continue
        content = e["response"].get("content", {})
        text = content.get("text")
        if text and len(text) > NOISE_BODY_CHARS:
            content["text"] = text[:NOISE_BODY_CHARS] + "...[cut]"
            content.pop("encoding", None) if content.get("encoding") == "base64" else None
    har_path.write_text(json.dumps(har, indent=1))
    return har["log"]["entries"]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (page_url, script) in SESSIONS.items():
            har = HERE / "tasks" / name / "session.har"
            ctx = browser.new_context(record_har_path=str(har), record_har_content="embed", bypass_csp=True)
            page = ctx.new_page()
            page.goto(page_url, wait_until="load", timeout=60000)
            page.wait_for_timeout(1500)
            page.evaluate(script)
            page.wait_for_timeout(1000)
            ctx.close()
            entries = shorten_noise(har)
            api = [e for e in entries if e["request"]["url"].startswith(API_PREFIXES)]
            print(f"{name}: {len(entries)} entries, {len(api)} API calls, {har.stat().st_size // 1024} KB")
            for e in api:
                print("   ", e["request"]["method"], e["request"]["url"][:90], e["response"]["status"])
        browser.close()


if __name__ == "__main__":
    main()
