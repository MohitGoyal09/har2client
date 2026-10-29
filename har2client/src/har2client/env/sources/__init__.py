"""Traffic sources: one canonical session, rendered through a vantage point into the one visible `session.*` file.

To add a source, write a module with `render(session, rng) -> str` and register it in `SOURCES`.
Future work (DESIGN-RL.md): `raw_http`, `curl_transcript`, `otel_spans`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from har2client.env.sources import browser, mobile
from har2client.env.sources.canon import Session


@dataclass(frozen=True)
class Source:
    name: str
    extension: str  # of the visible file: `session<extension>`
    render: Callable[[Session, random.Random], str]
    description: str  # one sentence for goal.md; must not name any API mechanism


SOURCES: dict[str, Source] = {
    "browser_har": Source(
        "browser_har", ".har", browser.render,
        "The file `session.har` is a browser recording of a user session. It holds real\nrequests and responses for this API, mixed with unrelated traffic.",
    ),
    "mobile_har": Source(
        "mobile_har", ".har", mobile.render,
        "The file `session.har` is a HAR export of a phone app's traffic, captured through a proxy. It holds real\nrequests and responses for this API, mixed with the app's unrelated SDK traffic.",
    ),
}
DEFAULT_SOURCE = "browser_har"
OBSERVATION_NAMES = tuple(f"session{s.extension}" for s in SOURCES.values())


def get(name: str) -> Source:
    if name not in SOURCES:
        raise ValueError(f"unknown source {name!r}; known: {sorted(SOURCES)}")
    return SOURCES[name]
