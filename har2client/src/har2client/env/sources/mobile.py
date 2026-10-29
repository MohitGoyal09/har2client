"""`mobile_har`: HAR export of a phone app's traffic through a proxy (mitmproxy / Charles).

App headers (device id, app version) and the app's user agent instead of browser headers; no CORS, no
sec-fetch, no origin or referer. The noise is the usual mobile SDK traffic: crash reporting, attribution,
push registration, remote config and analytics. API calls and noise share one header profile.
"""

from __future__ import annotations

import random

from har2client.browser import registered_domain
from har2client.env.sources.canon import Exchange, NoiseCall, Session, dumps_har, merged, response_header_list
from har2client.har import make_entry, wrap

_STYLES = ("okhttp", "app_okhttp", "cfnetwork")
_FIRST_PARTY = (
    NoiseCall("GET", "https://cfg.{reg}/mobile/config.json", None, '{"min_version":"3.1.0","maintenance":false,"flags":{"newHome":true}}'),
    NoiseCall("POST", "https://events.{reg}/v1/batch", '{"batch":[{"event":"screen_view","id":"{h}"}],"sent_at":"2026-03-14T10:22:07Z"}', '{"accepted":1}'),
)
_SDK = (
    NoiseCall("GET", "https://settings.crashlytics.com/spi/v2/platforms/android/gmp/1:{n}:android:{h8}/settings",
              None, '{"settings_version":3,"cache_duration":3600,"features":{"collect_reports":true}}', app=False),
    NoiseCall("POST", "https://o{n}.ingest.sentry.io/api/{n}/envelope/?sentry_key={h}",
              '{"event_id":"{h}","sent_at":"2026-03-14T10:22:07Z"}', '{"id":"{h}"}', app=False),
    NoiseCall("POST", "https://app.adjust.com/session", '{"app_token":"{h8}","environment":"production","os_name":"android"}',
              '{"message":"OK","timestamp":"2026-03-14T10:22:06.000Z"}', app=False),
    NoiseCall("POST", "https://api2.branch.io/v1/open", '{"branch_key":"key_live_{h}","hardware_id":"{h}"}',
              '{"session_id":"{n}","identity_id":"{n}"}', app=True),
    NoiseCall("POST", "https://fcmregistrations.googleapis.com/v1/projects/{n}/registrations",
              '{"fid":"{h}","appId":"1:{n}:android:{h8}"}', '{"name":"projects/{n}/registrations/{h}","token":"{h}"}',
              req_headers=(("x-goog-api-key", "AIza{h}"),), app=False),
    NoiseCall("POST", "https://firebaseremoteconfig.googleapis.com/v1/projects/{n}/namespaces/firebase:fetch",
              '{"appInstanceId":"{h}","appId":"1:{n}:android:{h8}"}',
              '{"entries":{"new_onboarding":"true","api_timeout_ms":"8000"},"state":"UPDATE"}',
              req_headers=(("x-goog-api-key", "AIza{h}"),), app=False),
    NoiseCall("POST", "https://api2.amplitude.com/2/httpapi", '{"api_key":"{h}","events":[{"event_type":"app_open","user_id":"{h8}"}]}',
              '{"code":200,"events_ingested":1,"payload_size_bytes":96}', app=True),
    NoiseCall("POST", "https://api.onesignal.com/players", '{"app_id":"{h}","device_type":1,"identifier":"{h}"}',
              '{"success":true,"id":"{h}"}', req_headers=(("authorization", "Basic {h}"),), app=False),
)
CATALOG = (*_FIRST_PARTY, *_SDK)


def _profile(session: Session) -> dict:
    rng = random.Random(f"mobile:{session.seed}")
    app = registered_domain(session.api_host.split(":")[0]).split(".")[0].capitalize()
    version = f"{rng.randint(2, 6)}.{rng.randint(0, 20)}.{rng.randint(0, 9)}"
    style = rng.choice(_STYLES)
    agent = {
        "okhttp": "okhttp/4.12.0",
        "app_okhttp": f"{app}/{version} (Android 14; Pixel 7) okhttp/4.12.0",
        "cfnetwork": f"{app}/{version} CFNetwork/1494.0.7 Darwin/23.4.0",
    }[style]
    return {"style": style, "agent": agent, "version": version, "device": f"{rng.getrandbits(128):032x}"}


def _headers(exchange: Exchange, profile: dict) -> list[tuple[str, str]]:
    app = [("x-device-id", profile["device"]), ("x-app-version", profile["version"])] if exchange.app else []
    content = [("content-type", exchange.req_mime)] if exchange.req_body is not None and not exchange.header("content-type") else []
    custom = [*exchange.req_headers, *app, *content]
    if profile["style"] == "cfnetwork":
        return [("host", exchange.host), *custom, ("accept", "application/json"), ("user-agent", profile["agent"]),
                ("accept-language", "en-US,en;q=0.9"), ("accept-encoding", "gzip, deflate, br"), ("connection", "keep-alive")]
    return [("accept", "application/json"), *custom, ("host", exchange.host), ("connection", "Keep-Alive"),
            ("accept-encoding", "gzip"), ("user-agent", profile["agent"])]


def _entry(exchange: Exchange, profile: dict) -> dict:
    entry = make_entry(
        exchange.method, exchange.url, exchange.started, req_headers=[], post_text=exchange.req_body, post_mime=exchange.req_mime,
        status=exchange.status, resp_headers=response_header_list(exchange, cors=False), mime=exchange.resp_mime,
        text=exchange.resp_body, wait_ms=exchange.wait_ms,
    )
    entry.pop("_resourceType")
    entry["request"]["headers"] = [{"name": k, "value": v} for k, v in _headers(exchange, profile)]
    return entry


def render(session: Session, rng: random.Random) -> str:
    profile = _profile(session)
    har = wrap([_entry(e, profile) for e in merged(session, CATALOG, rng)])
    har["log"]["creator"] = {"name": "mitmproxy", "version": "10.2.4"}
    return dumps_har(har, session.compact)
