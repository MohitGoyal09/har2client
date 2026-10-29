"""`browser_har`: Chrome DevTools HAR export, with browser noise (CDN, analytics, fonts). Same bytes as the Tier B builder wrote before sources existed."""

from __future__ import annotations

import random

from har2client.browser import Site, request_headers
from har2client.env.sources.canon import Exchange, Session, base_response_headers, dumps_har
from har2client.har import make_entry, mix_noise, wrap


def _entry(exchange: Exchange, site: Site) -> dict:
    return make_entry(
        exchange.method,
        exchange.url,
        exchange.started,
        req_headers=[*request_headers("fetch", site.api_host, site, include_host=True), *exchange.req_headers],
        post_text=exchange.req_body,
        post_mime=exchange.req_mime,
        status=exchange.status,
        resp_headers=[*base_response_headers(exchange, cors=True), *exchange.resp_headers],
        mime=exchange.resp_mime,
        text=exchange.resp_body,
        wait_ms=exchange.wait_ms,
    )


def render_har(session: Session, rng: random.Random) -> dict:
    site = Site.from_url(f"https://{session.api_host}")
    har = wrap([_entry(e, site) for e in session.exchanges])
    return mix_noise(har, session.noise, rng, session.base_path)


def render(session: Session, rng: random.Random) -> str:
    return dumps_har(render_har(session, rng), session.compact)
