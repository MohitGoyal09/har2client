"""The canonical session: the recorded exchanges of one Tier B task plus a noise profile.

Every traffic source (today `browser_har` and `mobile_har`; see `sources/__init__.py`) is a pure
rendering of a `Session`. An `Exchange` keeps only what belongs to the API call itself: the
vantage-point headers (browser, mobile, ...) are not stored, each renderer adds its own.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from har2client.browser import registered_domain

BODY_LIMIT = 2000  # `compact` truncates a response body that is not JSON to this many characters
LIST_LIMIT = 3  # `compact` keeps this many elements of every JSON list in a response body
NOISE_WINDOW_S = 4.0  # noise may start this long before the first / after the last real call
MAX_RATIO = 0.99
_BROWSER_REQUEST_HEADERS = frozenset(
    "host connection user-agent accept accept-encoding accept-language origin referer upgrade-insecure-requests "
    "sec-fetch-site sec-fetch-mode sec-fetch-dest sec-fetch-user".split()
)
_BROWSER_RESPONSE_HEADERS = frozenset("date content-type content-length server cache-control access-control-allow-origin".split())
_HEX = "0123456789abcdef"


@dataclass(frozen=True)
class Exchange:
    """One request and its response. `req_headers` and `resp_headers` hold only the API-specific headers
    (auth, cookie, csrf, if-match, etag, link, ...); `api` is False for noise."""

    method: str
    url: str
    started: datetime
    wait_ms: float
    req_headers: tuple[tuple[str, str], ...]
    req_body: str | None
    req_mime: str
    status: int
    resp_headers: tuple[tuple[str, str], ...]
    resp_content_type: str | None  # the content-type header; None when the response has none
    resp_mime: str  # the recorded content mimeType ("x-unknown" when unknown)
    resp_body: str
    cache_control: str = "no-store"
    app: bool = True  # sent by the app itself (carries app headers on mobile); False for third-party SDK traffic
    api: bool = True

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    @property
    def target(self) -> str:
        """Path and query, as on the request line."""
        parts = urlsplit(self.url)
        return parts.path + (f"?{parts.query}" if parts.query else "")

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.req_headers if k.lower() == name.lower()), None)


@dataclass(frozen=True)
class Session:
    """`exchanges` in time order; `noise` is the share of the observation that is unrelated traffic."""

    exchanges: tuple[Exchange, ...]
    api_host: str
    base_path: str
    noise: float = 0.0
    seed: str = "0"
    compact: bool = False  # renderers write minified JSON

    @property
    def start(self) -> datetime:
        return min(e.started for e in self.exchanges)

    @property
    def end(self) -> datetime:
        return max(e.started for e in self.exchanges)


def parse_stamp(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def from_har(har: dict, *, base_path: str, noise: float, seed: str) -> Session:
    """Canonical session of a recorded HAR that holds API calls only (no noise yet)."""
    exchanges = []
    for entry in har["log"]["entries"]:
        req, resp = entry["request"], entry["response"]
        post = req.get("postData")
        content = resp["content"]
        resp_headers = {h["name"].lower(): h["value"] for h in resp["headers"]}
        exchanges.append(
            Exchange(
                method=req["method"],
                url=req["url"],
                started=parse_stamp(entry["startedDateTime"]),
                wait_ms=entry["time"],
                req_headers=tuple((h["name"], h["value"]) for h in req["headers"] if h["name"].lower() not in _BROWSER_REQUEST_HEADERS),
                req_body=post["text"] if post else None,
                req_mime=post["mimeType"] if post else "application/json",
                status=resp["status"],
                resp_headers=tuple((h["name"], h["value"]) for h in resp["headers"] if h["name"].lower() not in _BROWSER_RESPONSE_HEADERS),
                resp_content_type=resp_headers.get("content-type"),
                resp_mime=content["mimeType"],
                resp_body=content.get("text", ""),
            )
        )
    host = urlsplit(exchanges[0].url).netloc
    return Session(tuple(exchanges), host, base_path, noise, seed)


def _shorten(value: object) -> object:
    if isinstance(value, list):
        return [_shorten(v) for v in value[:LIST_LIMIT]]
    if isinstance(value, dict):
        return {k: _shorten(v) for k, v in value.items()}
    return value


def shorten_body(text: str) -> str:
    """A JSON body with every list cut to LIST_LIMIT elements (keys such as a next cursor stay); other text cut to BODY_LIMIT."""
    try:
        return json.dumps(_shorten(json.loads(text)), separators=(",", ":"), ensure_ascii=False)
    except ValueError:
        return text[:BODY_LIMIT]


def dumps_har(har: dict, compact: bool) -> str:
    """HAR text: indented like a DevTools export, or minified for compact sessions."""
    if compact:
        return json.dumps(har, separators=(",", ":"), ensure_ascii=False) + "\n"
    return json.dumps(har, indent=2, ensure_ascii=False) + "\n"


def compacted(session: Session) -> Session:
    """No noise, short response bodies and minified JSON (prompt-length control)."""
    short = tuple(replace(e, resp_body=shorten_body(e.resp_body) if e.resp_body else "") for e in session.exchanges)
    return replace(session, exchanges=short, noise=0.0, compact=True)


# ---------------------------------------------------------------- noise


@dataclass(frozen=True)
class NoiseCall:
    """A template of an unrelated call. `{h}` is 24 random hex characters, `{h8}` 8, `{n}` a random number,
    `{reg}` the registered domain of the API host."""

    method: str
    url: str
    req_body: str | None = None
    resp_body: str = ""
    mime: str = "application/json"
    status: int = 200
    req_headers: tuple[tuple[str, str], ...] = ()
    app: bool = True
    req_mime: str = "application/json"
    cache_control: str = "no-store"


def fill(rng: random.Random, template: str, reg: str = "") -> str:
    return (
        template.replace("{reg}", reg)
        .replace("{h8}", "".join(rng.choice(_HEX) for _ in range(8)))
        .replace("{h}", "".join(rng.choice(_HEX) for _ in range(24)))
        .replace("{n}", str(rng.randint(10**7, 10**9)))
    )


def noise_count(real: int, ratio: float) -> int:
    if not 0 <= ratio <= MAX_RATIO:
        raise ValueError(f"noise must be in [0, {MAX_RATIO}], got {ratio}")
    return round(ratio * real / (1 - ratio)) if real else 0


def noise_exchanges(session: Session, catalog: tuple[NoiseCall, ...], rng: random.Random, count: int) -> list[Exchange]:
    """`count` unrelated exchanges drawn from `catalog`, spread over the session window."""
    reg = registered_domain(session.api_host.split(":")[0])
    span = (session.end - session.start).total_seconds() + 2 * NOISE_WINDOW_S
    out = []
    for _ in range(count):
        call = rng.choice(catalog)
        moment = session.start - timedelta(seconds=NOISE_WINDOW_S) + timedelta(seconds=rng.uniform(0, span))
        body = None if call.req_body is None else fill(rng, call.req_body, reg)
        out.append(
            Exchange(
                method=call.method,
                url=fill(rng, call.url, reg),
                started=moment,
                wait_ms=round(rng.uniform(15, 400), 3),
                req_headers=tuple((k, fill(rng, v, reg)) for k, v in call.req_headers),
                req_body=body,
                req_mime=call.req_mime,
                status=call.status,
                resp_headers=(),
                resp_content_type=call.mime,
                resp_mime=call.mime,
                resp_body=fill(rng, call.resp_body, reg),
                cache_control=call.cache_control,
                app=call.app,
                api=False,
            )
        )
    return out


def merged(session: Session, catalog: tuple[NoiseCall, ...], rng: random.Random, ratio: float | None = None) -> list[Exchange]:
    """Real exchanges and noise in time order."""
    ratio = session.noise if ratio is None else ratio
    noise = noise_exchanges(session, catalog, rng, noise_count(len(session.exchanges), ratio))
    return sorted([*session.exchanges, *noise], key=lambda e: e.started)


# ---------------------------------------------------------------- shared helpers

_ID_SEGMENT = re.compile(r"^[A-Z]{3}-\d{4,6}$")


def relative_path(session: Session, exchange: Exchange) -> str:
    path = urlsplit(exchange.url).path
    return path[len(session.base_path):] if session.base_path and path.startswith(session.base_path) else path


def path_key(session: Session, exchange: Exchange) -> str:
    """The path with an item id replaced by `{id}`."""
    return "/".join("{id}" if _ID_SEGMENT.match(p) else p for p in relative_path(session, exchange).split("/"))


def base_response_headers(exchange: Exchange, *, cors: bool) -> list[tuple[str, str]]:
    """date, content-type, content-length, server and cache-control of a response (CORS only when `cors`)."""
    from har2client.browser import Site, response_headers

    site = Site.from_url(exchange.url)
    headers = response_headers(
        site.api_host, site, started=exchange.started, content_type=exchange.resp_content_type or "application/json",
        body=exchange.resp_body, cache_control=exchange.cache_control,
    )
    return [(k, v) for k, v in headers if cors or k != "access-control-allow-origin"]


def response_header_list(exchange: Exchange, *, cors: bool) -> list[tuple[str, str]]:
    """All response headers: the base set (without content-type when the response has none) plus the API-specific ones."""
    base = base_response_headers(exchange, cors=cors)
    if exchange.resp_content_type is None:
        base = [(k, v) for k, v in base if k != "content-type"]
    return [*base, *exchange.resp_headers]
