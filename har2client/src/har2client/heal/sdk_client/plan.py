"""Shared pieces of the SDK-style renderer: supported flaws and levels, the `Plan` of behaviours, the template filler."""

from __future__ import annotations

from har2client.tierb.config import DRIFT_KINDS, switch

KNOWN_FLAWS = (
    "ignore_pagination", "no_relogin", "no_if_match", "drop_hidden_header", "return_envelope", "no_csrf",
    *(f"no_drift_{kind}" for kind in DRIFT_KINDS),
    "no_rate_limit_retry", "hammer_rate_limit", "no_job_poll", "no_refresh", "no_idempotency_key", "no_moved_path",
)
# config switch -> levels this renderer implements (`drift` is a list of kinds, checked apart)
SUPPORTED_LEVELS = {
    "auth": ("apikey_header", "bearer_login", "cookie_csrf"),
    "pagination": ("none", "offset", "cursor", "link_header"),
    "token_expiry": ("off", "on"),
    "etag_update": ("off", "on"),
    "hidden_header": ("off", "on"),
    "envelope": ("off", "on"),
    "rate_limit": ("off", "on"),
    "async_create": ("off", "on"),
    "refresh_rotation": ("off", "on"),
    "idempotency_key": ("off", "on"),
    "moved": ("off", "on"),
}
_OFF_VALUES = (None, False, "off", "", [], (), {})
RATE_LIMIT_RETRIES = 10  # tries of one request while the API answers 429 with Retry-After
JOB_POLLS = 30
JOB_POLL_DELAY_S = 0.1


def check_supported(spec: dict, flaws: set[str]) -> None:
    """Raise NotImplementedError(<switch name>) for a flaw, config switch or level that is not rendered."""
    for flaw in sorted(flaws - set(KNOWN_FLAWS)):
        raise NotImplementedError(flaw)
    for name, value in spec["config"].items():
        if name == "drift":
            for kind in value or ():
                if kind not in DRIFT_KINDS:
                    raise NotImplementedError(f"drift={kind}")
        elif name not in SUPPORTED_LEVELS:
            if value not in _OFF_VALUES:
                raise NotImplementedError(name)
        elif value not in SUPPORTED_LEVELS[name]:
            raise NotImplementedError(f"{name}={value}")


class Plan:
    """The behaviours the generated client has: the switches that are on, less the flaws that remove them."""

    def __init__(self, spec: dict, flaws: set[str]):
        cfg = spec["config"]
        self.spec, self.flaws = spec, flaws
        self.drift = {k: spec["drift"][k] for k in DRIFT_KINDS if k in switch(cfg, "drift") and f"no_drift_{k}" not in flaws}
        self.rate_limit = switch(cfg, "rate_limit") == "on" and "no_rate_limit_retry" not in flaws
        self.hammer = "hammer_rate_limit" in flaws  # retry at once, ignore Retry-After
        self.async_create = switch(cfg, "async_create") == "on"
        self.poll = self.async_create and "no_job_poll" not in flaws
        self.refresh = switch(cfg, "refresh_rotation") == "on" and "no_refresh" not in flaws and "no_relogin" not in flaws
        self.idempotency = switch(cfg, "idempotency_key") == "on" and "no_idempotency_key" not in flaws
        self.moved = switch(cfg, "moved") == "on" and "no_moved_path" not in flaws
        # request bodies change on the wire for these kinds; `epoch` only changes responses
        self.sends = [k for k in self.drift if k != "epoch"]


def fill(template: str, **values: str) -> str:
    """Replace `@key@` markers. Markers keep the templates readable: they are full of braces.

    A marker that stands alone on its line and has no value takes its line with it.
    """
    for key, value in values.items():
        marker = f"@{key}@"
        if value == "" and f"\n{marker}\n" in template:
            template = template.replace(f"\n{marker}\n", "\n")
        else:
            template = template.replace(marker, value)
    return template
