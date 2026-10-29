"""One Chrome-on-macOS header profile, shared by real API calls and HAR noise entries.

Real calls and noise must not be separable by header names, so both come from `request_headers`
and `response_headers` and are put in one canonical order.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from urllib.parse import urlparse

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "en-US,en;q=0.9"
ACCEPT_ENCODING = "gzip, deflate, br, zstd"
DEFAULT_SERVER_URL = "https://api.example.com"
FALLBACK_APP_HOST = "app.example.com"
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac", "nic"}  # as in co.uk, com.au, gov.in
_IPV4 = re.compile(r"\d+(\.\d+){3}")

# kind -> (accept, sec-fetch-dest, sec-fetch-mode, sends origin)
_KINDS = {
    "fetch": ("application/json", "empty", "cors", True),  # XHR / fetch from the app (real API calls)
    "fetch_any": ("*/*", "empty", "cors", True),
    "beacon": ("*/*", "empty", "cors", True),
    "script": ("*/*", "script", "no-cors", False),
    "stylesheet": ("text/css,*/*;q=0.1", "style", "no-cors", False),
    "font": ("*/*", "font", "cors", True),
    "image": ("image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8", "image", "no-cors", False),
    "manifest": ("*/*", "manifest", "cors", False),
    "document": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "document", "navigate", False,
    ),
}
_REQUEST_ORDER = [
    "host", "connection", "authorization", "origin", "content-type", "upgrade-insecure-requests", "user-agent",
    "accept", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-user", "sec-fetch-dest", "referer",
    "accept-encoding", "accept-language",
]
_SERVERS = ["nginx", "cloudflare", "envoy", "gws", "Apache", "AmazonS3"]


def registered_domain(host: str) -> str:
    labels = host.lower().split(".")
    if len(labels) <= 2:
        return host.lower()
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@dataclass(frozen=True)
class Site:
    """The API host and the web app origin (`https://app.<registered domain>`) that calls it."""

    api_host: str
    app_origin: str

    @classmethod
    def from_url(cls, url: str) -> "Site":
        parsed = urlparse(url)
        scheme, host = parsed.scheme or "https", parsed.hostname or urlparse(DEFAULT_SERVER_URL).hostname
        if "." in host and not _IPV4.fullmatch(host):
            app_host = f"app.{registered_domain(host)}"
            if app_host == host:
                app_host = f"www.{registered_domain(host)}"
        else:
            app_host = FALLBACK_APP_HOST
        return cls(api_host=parsed.netloc or host, app_origin=f"{scheme}://{app_host}")

    @property
    def referer(self) -> str:
        return self.app_origin + "/"

    def fetch_site(self, host: str) -> str:
        """`sec-fetch-site` for a request to `host` made by the app page."""
        app_host = urlparse(self.app_origin).hostname or ""
        return "same-site" if registered_domain(host.split(":")[0]) == registered_domain(app_host) else "cross-site"


def _position(name: str) -> int:
    lowered = name.lower()
    # custom headers (an API key) sit with authorization
    return _REQUEST_ORDER.index(lowered if lowered in _REQUEST_ORDER else "authorization")


def order_request_headers(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(headers, key=lambda item: _position(item[0]))


def request_headers(kind: str, host: str, site: Site, *, include_host: bool = True) -> list[tuple[str, str]]:
    """Browser request headers for one resource `kind` sent to `host` (from the page at `site.app_origin`).
    `authorization`, `content-type` and API-key headers are not here: callers add them where needed."""
    accept, dest, mode, sends_origin = _KINDS[kind]
    out = [
        ("connection", "keep-alive"),
        ("user-agent", CHROME_UA),
        ("accept", accept),
        ("sec-fetch-site", site.fetch_site(host)),
        ("sec-fetch-mode", mode),
        ("sec-fetch-dest", dest),
        ("referer", site.referer),
        ("accept-encoding", ACCEPT_ENCODING),
        ("accept-language", ACCEPT_LANGUAGE),
    ]
    if include_host:
        out.append(("host", host))
    if sends_origin:
        out.append(("origin", site.app_origin))
    if kind == "document":
        out += [("upgrade-insecure-requests", "1"), ("sec-fetch-user", "?1")]
    return order_request_headers(out)


def response_headers(
    host: str, site: Site, *, started: datetime, content_type: str, body: str, cache_control: str, wildcard: bool = False
) -> list[tuple[str, str]]:
    """Common response header set: date, content-type, content-length, server, cache-control and CORS
    (`access-control-allow-origin`, since the app page is on another origin)."""
    server = _SERVERS[zlib.crc32(host.encode()) % len(_SERVERS)]
    return [
        ("date", format_datetime(started, usegmt=True)),
        ("content-type", content_type),
        ("content-length", str(len(body.encode()))),
        ("server", server),
        ("cache-control", cache_control),
        ("access-control-allow-origin", "*" if wildcard else site.app_origin),
    ]
