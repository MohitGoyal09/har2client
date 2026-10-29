"""HAR 1.2 recording (httpx event hooks), host rewriting and noise mixing."""

from __future__ import annotations

import copy
import random
import re
import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse

import httpx

from har2client.browser import (
    DEFAULT_SERVER_URL,
    Site,
    order_request_headers,
    request_headers,
    response_headers,
)

LOOPBACK = "127.0.0.1"
DEFAULT_START = datetime(2026, 3, 14, 10, 22, 5, tzinfo=timezone.utc)
NOISE_WINDOW_S = 4.0  # noise may start this long before the first / after the last real call
MAX_RATIO = 0.99
API_CACHE_CONTROL = "no-store"
SYNTHETIC_LATENCY_MS = (18.0, 260.0)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _pairs(items) -> list[dict]:
    return [{"name": k, "value": v} for k, v in items]


def make_entry(
    method: str,
    url: str,
    started: datetime,
    *,
    req_headers: list[tuple[str, str]],
    post_text: str | None = None,
    post_mime: str = "application/json",
    status: int = 200,
    resp_headers: list[tuple[str, str]] = (),
    mime: str = "",
    text: str = "",
    wait_ms: float = 30.0,
    resource_type: str = "fetch",
) -> dict:
    request: dict = {
        "method": method,
        "url": url,
        "httpVersion": "HTTP/1.1",
        "cookies": [],
        "headers": _pairs(order_request_headers(list(req_headers))),
        "queryString": _pairs(httpx.URL(url).params.multi_items()),
        "headersSize": -1,
        "bodySize": len(post_text.encode()) if post_text else 0,
    }
    if post_text is not None:
        request["postData"] = {"mimeType": post_mime, "text": post_text}
    size = len(text.encode())
    return {
        "_resourceType": resource_type,
        "startedDateTime": _iso(started),
        "time": round(wait_ms, 3),
        "request": request,
        "response": {
            "status": status,
            "statusText": httpx.codes.get_reason_phrase(status),
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": _pairs(resp_headers),
            "content": {"size": size, "mimeType": mime or "x-unknown", "text": text},
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": size,
        },
        "cache": {},
        "timings": {"blocked": -1, "dns": -1, "connect": -1, "ssl": -1, "send": 0, "wait": round(wait_ms, 3), "receive": 0},
    }


def wrap(entries: list[dict]) -> dict:
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "WebInspector", "version": "537.36"},
            "pages": [],
            "entries": entries,
        }
    }


class HarRecorder:
    """Collects one HAR entry per httpx request via event hooks. Time is virtual:
    call `tick(seconds)` between requests to space a session out without sleeping."""

    def __init__(
        self, start: datetime | None = None, latency_rng: random.Random | None = None, site: Site | None = None
    ):
        """With `latency_rng`, timings are synthetic (reproducible HAR); else they are measured.
        `site` says which API host and app origin the recorded browser belongs to."""
        self.site = site or Site.from_url(DEFAULT_SERVER_URL)
        self._now = start or DEFAULT_START
        self._latency_rng = latency_rng
        self._entries: list[dict] = []
        self._pending: dict[int, tuple[datetime, float]] = {}

    def tick(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    @property
    def hooks(self) -> dict:
        return {"request": [self._on_request], "response": [self._on_response]}

    def _on_request(self, request: httpx.Request) -> None:
        self._pending[id(request)] = (self._now, time.perf_counter())

    def _on_response(self, response: httpx.Response) -> None:
        request = response.request
        started, t0 = self._pending.pop(id(request))
        response.read()
        if self._latency_rng is not None:
            elapsed_ms = self._latency_rng.uniform(*SYNTHETIC_LATENCY_MS)
        else:
            elapsed_ms = max((time.perf_counter() - t0) * 1000, 1.0)
        body = request.content.decode("utf-8", errors="replace") if request.content else None
        req_headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() != "content-length"]
        content_type = request.headers.get("content-type", "application/json")
        resp_headers = response_headers(
            self.site.api_host,
            self.site,
            started=started,
            content_type=response.headers.get("content-type", "application/json"),
            body=response.text,
            cache_control=API_CACHE_CONTROL,
        )
        self._entries.append(
            make_entry(
                request.method,
                str(request.url),
                started,
                req_headers=req_headers,
                post_text=body,
                post_mime=content_type,
                status=response.status_code,
                resp_headers=resp_headers,
                mime=response.headers.get("content-type", ""),
                text=response.text,
                wait_ms=elapsed_ms,
            )
        )

    def har(self) -> dict:
        return wrap(copy.deepcopy(self._entries))


def rewrite_host(har: dict, base_url: str) -> dict:
    """Copy of `har` where loopback origins become the scheme and host of `base_url`."""
    target = urlparse(base_url)
    out = copy.deepcopy(har)
    for entry in out["log"]["entries"]:
        req = entry["request"]
        parsed = urlparse(req["url"])
        if parsed.hostname != LOOPBACK:
            continue
        req["url"] = urlunparse(parsed._replace(scheme=target.scheme, netloc=target.netloc))
        for header in req["headers"]:
            if header["name"].lower() == "host":
                header["value"] = target.netloc
    return out


# ---------------------------------------------------------------- noise

_HEX = "0123456789abcdef"
_JS_BITS = ["function(e,t){return e+t}", "window.dataLayer=window.dataLayer||[]", "var n=document.createElement(\"script\")",
            "e.exports=function(){}", "!function(){\"use strict\"}()", "navigator.sendBeacon", "Object.defineProperty(t,\"__esModule\",{value:!0})"]
_CSS_BITS = [".btn{display:inline-block;padding:.375rem .75rem}", "body{margin:0;font-family:system-ui}",
             ".container{max-width:1140px;margin:0 auto}", "a{color:#0d6efd;text-decoration:underline}",
             "@font-face{font-family:Inter;font-display:swap}"]
_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{title}</title>"
    "<link rel=\"stylesheet\" href=\"/static/css/main.css\"></head><body><div id=\"root\"></div>"
    "<script src=\"/static/js/main.js\"></script></body></html>"
)

# (url, mime, kind) for third-party static files; {h} = random hex, {n} = random digits
_THIRD_PARTY_STATIC = [
    ("https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap", "text/css", "css", "stylesheet"),
    ("https://fonts.gstatic.com/s/inter/v13/{h}.woff2", "font/woff2", "binary", "font"),
    ("https://www.googletagmanager.com/gtag/js?id=G-{n}", "application/javascript", "js", "script"),
    ("https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css", "text/css", "css", "stylesheet"),
    ("https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js", "application/javascript", "js", "script"),
    ("https://connect.facebook.net/en_US/fbevents.js", "application/javascript", "js", "script"),
    ("https://cdn.segment.com/analytics.js/v1/{h}/analytics.min.js", "application/javascript", "js", "script"),
    ("https://static.hotjar.com/c/hotjar-{n}.js?sv=6", "application/javascript", "js", "script"),
    ("https://browser.sentry-cdn.com/7.100.0/bundle.tracing.min.js", "application/javascript", "js", "script"),
    ("https://js-agent.newrelic.com/nr-loader-spa-1.250.0.min.js", "application/javascript", "js", "script"),
]
# (method, url, request body template or None, response body, mime)
_BEACONS = [
    ("POST", "https://www.google-analytics.com/g/collect?v=2&tid=G-{n}&cid={n}.{n}&en=page_view", "", "", "text/plain"),
    ("POST", "https://api.segment.io/v1/t", '{"event":"Clicked","anonymousId":"{h}","writeKey":"{h}"}',
     '{"success":true}', "application/json"),
    ("POST", "https://o{n}.ingest.sentry.io/api/{n}/envelope/?sentry_key={h}", '{"event_id":"{h}","sent_at":"2026-03-14T10:22:07Z"}',
     '{"id":"{h}"}', "application/json"),
    ("GET", "https://px.ads.linkedin.com/collect?pid={n}&fmt=gif", None, "", "image/gif"),
    ("POST", "https://api.mixpanel.com/track?ip=1", '[{"event":"page view","properties":{"token":"{h}"}}]', "1", "text/plain"),
]
_SAME_ORIGIN = [
    ("/", "text/html", "html", "document"),
    ("/static/js/main.{h8}.js", "application/javascript", "js", "script"),
    ("/static/css/main.{h8}.css", "text/css", "css", "stylesheet"),
    ("/favicon.ico", "image/x-icon", "binary", "image"),
    ("/static/media/logo.{h8}.svg", "image/svg+xml", "svg", "image"),
    ("/manifest.json", "application/json", "manifest", "manifest"),
    ("/robots.txt", "text/plain", "robots", "other"),
]
_JSON_XHR = {  # same-origin JSON a web app polls; path -> body template
    "/__flags": '{"flags":{"newNav":true,"betaBanner":false,"exportV2":true},"etag":"{h8}"}',
    "/config.json": '{"env":"production","release":"{h8}","features":["search","export"],"telemetry":true}',
    "/sockjs-node/info": '{"websocket":true,"origins":["*:*"],"cookie_needed":false,"entropy":{n}}',
    "/ping": '{"status":"ok","t":{n}}',
}
THIRD_PARTY_SHARE = 0.7
JSON_XHR_SHARE = 0.15
_FILLER = 6  # code fragments per generated file


def _fill(rng: random.Random, template: str) -> str:
    return (
        template.replace("{h8}", "".join(rng.choice(_HEX) for _ in range(8)))
        .replace("{h}", "".join(rng.choice(_HEX) for _ in range(24)))
        .replace("{n}", str(rng.randint(10**7, 10**9)))
    )


def _body(rng: random.Random, kind: str, origin: str) -> str:
    if kind == "js":
        return "/*! bundle */" + ";".join(rng.choice(_JS_BITS) for _ in range(_FILLER)) + ";"
    if kind == "css":
        return "".join(rng.choice(_CSS_BITS) for _ in range(_FILLER))
    if kind == "html":
        return _HTML.format(title=urlparse(origin).hostname)
    if kind == "svg":
        return '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><circle cx="12" cy="12" r="10"/></svg>'
    if kind == "manifest":
        return '{"short_name":"App","start_url":".","display":"standalone","icons":[]}'
    if kind == "robots":
        return "User-agent: *\nDisallow:\n"
    return ""  # binary payloads are not stored as text


def _request_kind(resource_type: str) -> str:
    return {"other": "fetch_any"}.get(resource_type, resource_type)


def _noise_entry(
    rng: random.Random, site: Site, moment: datetime, url: str, kind: str, resource_type: str, mime: str, text: str,
    cache_control: str, *, method: str = "GET", post: str | None = None, status: int = 200,
) -> dict:
    """One browser entry; headers come from the shared profile, like real API calls."""
    host = urlparse(url).netloc
    req_headers = request_headers(kind, host, site)
    if post is not None:
        req_headers.append(("content-type", "application/json" if post.startswith(("{", "[")) else "text/plain"))
    resp_headers = response_headers(
        host, site, started=moment, content_type=mime, body=text, cache_control=cache_control,
        wildcard=kind not in ("fetch", "fetch_any"),
    )
    return make_entry(
        method, url, moment, req_headers=req_headers, post_text=post,
        post_mime=dict(req_headers).get("content-type", "text/plain"), status=status,
        resp_headers=resp_headers, mime=mime, text=text, wait_ms=rng.uniform(15, 400), resource_type=resource_type,
    )


def _json_xhr(rng: random.Random, site: Site, moment: datetime, origin: str, paths: list[str]) -> dict:
    path = rng.choice(paths)
    body = _fill(rng, _JSON_XHR[path])
    return _noise_entry(
        rng, site, moment, origin + path, "fetch", "fetch", "application/json", body, "no-cache",
    )


def make_noise(
    rng: random.Random, site: Site, origin: str, moment: datetime, json_paths: list[str] | None = None
) -> dict:
    """One browser-style entry that is not an API call: analytics, fonts, CDN files, same-origin assets.
    With `json_paths`, the entry is instead a same-origin JSON XHR to one of those paths on `origin`."""
    if json_paths:
        return _json_xhr(rng, site, moment, origin, json_paths)
    if rng.random() >= THIRD_PARTY_SHARE:
        path, mime, kind, rtype = rng.choice(_SAME_ORIGIN)
        return _noise_entry(
            rng, site, moment, origin + _fill(rng, path), _request_kind(rtype), rtype, mime,
            _body(rng, kind, origin), "public, max-age=3600",
        )
    if rng.random() < 0.5:
        url, mime, kind, rtype = rng.choice(_THIRD_PARTY_STATIC)
        return _noise_entry(
            rng, site, moment, _fill(rng, url), _request_kind(rtype), rtype, mime,
            _body(rng, kind, origin), "public, max-age=31536000",
        )
    method, url, req_body, resp_body, mime = rng.choice(_BEACONS)
    post = None if req_body is None else _fill(rng, req_body)
    is_post = method == "POST"
    return _noise_entry(
        rng, site, moment, _fill(rng, url), "beacon" if is_post else "image", "ping" if is_post else "image",
        mime, _fill(rng, resp_body), "no-store", method=method, post=post, status=200 if resp_body else 204,
    )


def _route_pattern(template: str) -> re.Pattern:
    """Regex for an OpenAPI path template: each `{name}` matches one path segment."""
    parts = re.split(r"(\{[^}]+\})", template)
    return re.compile("".join("[^/]+" if p.startswith("{") and p.endswith("}") else re.escape(p) for p in parts))


def _json_noise_paths(real: list[dict], base_path: str, routes: Iterable[str] = ()) -> list[str]:
    """Same-origin JSON XHR paths that are outside the API base path and not a real API route
    (`routes`: full path templates of the spec, base path included)."""
    used = {urlparse(e["request"]["url"]).path for e in real}
    base = base_path.rstrip("/")
    patterns = [_route_pattern(r) for r in routes]
    return [
        p for p in _JSON_XHR
        if p not in used and not (base and (p == base or p.startswith(base + "/")))
        and not any(rx.fullmatch(p) for rx in patterns)
    ]


def mix_noise(har: dict, ratio: float, rng: random.Random, base_path: str = "", routes: Iterable[str] = ()) -> dict:
    """Copy of `har` with browser noise so that noise is `ratio` of all entries (+-1).
    About JSON_XHR_SHARE of the noise is same-origin JSON XHR outside `base_path` and `routes`."""
    if not 0 <= ratio <= MAX_RATIO:
        raise ValueError(f"ratio must be in [0, {MAX_RATIO}], got {ratio}")
    out = copy.deepcopy(har)
    real = out["log"]["entries"]
    if not real or ratio == 0:
        return out
    count = round(ratio * len(real) / (1 - ratio))
    stamps = [_parse(e["startedDateTime"]) for e in real]
    lo, hi = min(stamps), max(stamps)
    span = (hi - lo).total_seconds() + 2 * NOISE_WINDOW_S
    parsed = urlparse(real[0]["request"]["url"])
    origin = f"{parsed.scheme}://{parsed.netloc}"
    site = Site.from_url(origin)
    json_paths = _json_noise_paths(real, base_path, routes)
    json_slots = set(rng.sample(range(count), round(JSON_XHR_SHARE * count))) if json_paths else set()
    noise = []
    for i in range(count):
        moment = lo - timedelta(seconds=NOISE_WINDOW_S) + timedelta(seconds=rng.uniform(0, span))
        noise.append(make_noise(rng, site, origin, moment, json_paths if i in json_slots else None))
    out["log"]["entries"] = sorted([*real, *noise], key=lambda e: e["startedDateTime"])
    return out
