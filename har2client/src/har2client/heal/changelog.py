"""`CHANGELOG.md` of a v3 repair task (DESIGN-CASE3-HARD.md): the release notes of the new version of the API.

Levels: `none` (no file), `complete` (every change of the release), `incomplete` (one change is left out) and `misleading`
(one change is stated wrongly: a wrong header name, field name, unit or path; the recording shows the truth). The notes read like
a real release note: version, date, "Breaking changes", a few harmless other items. They name the API only by what a client sees
(paths, headers, fields), never a credential or test data."""

from __future__ import annotations

import random
from typing import Callable

from har2client.tierb import spec as tspec

LEVELS = ("none", "complete", "incomplete", "misleading")
MISLEADING_KINDS = ("header", "field", "unit", "path")
_OTHER_ITEMS = ["Documentation updates.", "Improved uptime reporting on the status page.", "Internal dependency updates.", "Performance work on the dashboard."]
Statement = tuple[str, list[str], "tuple[str, str, str] | None"]  # (bullet, true facts shown, (kind, stated, truth) of a wrong fact)
Handler = Callable[[dict, bool], Statement]


def _q(text: str) -> str:
    return f"`{text}`"


def _other(options: list[str], truth: str, rng_key: str) -> str:
    """A plausible value that differs from the truth (the first of a seeded shuffle)."""
    choices = [o for o in options if o != truth]
    return random.Random(rng_key).choice(choices)


def _rename(spec: dict, wrong: bool) -> Statement:
    d = spec["drift"]["rename"]
    new = f"{d['field']}_code" if wrong else d["to"]
    text = f"- The `{d['field']}` field of every {spec['singular']} is now called {_q(new)}, in responses and in request bodies. The old name is gone."
    return (text, [], ("field", _q(new), _q(d["to"]))) if wrong else (text, [_q(d["to"])], None)


def _cents(spec: dict, wrong: bool) -> Statement:
    d = spec["drift"]["cents"]
    unit = "thousandths" if wrong else "hundredths"
    text = (f"- The decimal `{d['field']}` field is replaced by {_q(d['to'])}: an integer number of {unit} of the currency unit, "
            "in responses and in request bodies.")
    facts = [_q(d["to"])]
    return (text, facts, ("unit", unit, "hundredths")) if wrong else (text, [*facts, "hundredths"], None)


def _epoch(spec: dict, wrong: bool) -> Statement:
    unit = "in milliseconds" if wrong else "in seconds"
    text = f"- `created_at` is now a Unix timestamp {unit} (an integer) instead of an ISO 8601 string."
    return (text, ["Unix timestamp"], ("unit", unit, "in seconds")) if wrong else (text, ["Unix timestamp", "in seconds"], None)


def _nest(spec: dict, wrong: bool) -> Statement:
    d = spec["drift"]["nest"]
    key = _other(tspec._NEST_KEYS, d["key"], f"nest:{d['key']}") if wrong else d["key"]
    text = (f"- `{d['name_field']}` and `{d['email_field']}` are now one nested object, {_q(key)}, with the keys `name` and `email`. "
            "This holds for responses and for request bodies.")
    return (text, ["`name`", "`email`"], ("field", _q(key), _q(d["key"]))) if wrong else (text, [_q(d["key"]), "`name`", "`email`"], None)


def _enum(spec: dict, wrong: bool) -> Statement:
    d = spec["drift"]["enum"]
    example = d["values"][0]
    if d["mode"] == "upper":
        text = f"- The values of `{d['field']}` are now upper case, for example `{example.upper()}`. This holds for filters and request bodies too."
        return text, ["upper case"], None
    text = f"- The values of `{d['field']}` now carry the prefix `{d['prefix']}`, for example `{d['prefix']}{example}`. This holds for filters and request bodies too."
    return text, [f"`{d['prefix']}`"], None


def _rate_limit(spec: dict, wrong: bool) -> Statement:
    r = spec["rate_limit"]
    header = "X-RateLimit-Reset" if wrong else "Retry-After"
    text = (f"- Requests are now rate limited to {r['limit']} per second per account. A request over the limit gets `429 Too Many Requests` "
            f"and the number of seconds to wait in the {_q(header)} header. Wait and send the request again.")
    return (text, ["`429 Too Many Requests`"], ("header", _q(header), _q("Retry-After"))) if wrong else (text, ["`429 Too Many Requests`", _q("Retry-After")], None)


def _async_create(spec: dict, wrong: bool) -> Statement:
    word = _other(tspec._JOB_WORDS, spec["async_create"]["job_word"], f"job:{spec['async_create']['job_word']}") if wrong else spec["async_create"]["job_word"]
    path = f"{spec['base_path']}/{word}/{{id}}"
    text = (f"- `POST` to the collection now answers `202 Accepted` with a `Location` header that points to a job ({_q(path)}). "
            f"`GET` the job until its `status` is `done`; the created {spec['singular']} is in `result`.")
    true_path = f"{spec['base_path']}/{spec['async_create']['job_word']}/{{id}}"
    return (text, ["`202 Accepted`"], ("path", _q(path), _q(true_path))) if wrong else (text, ["`202 Accepted`", _q(path)], None)


def _refresh(spec: dict, wrong: bool) -> Statement:
    r, token = spec["refresh"], spec["login"]["token_field"]
    path = _other(tspec._REFRESH_PATHS, r["path"], f"refresh:{r['path']}") if wrong else r["path"]
    text = (f"- Login now returns a {_q(r['field'])} next to the {_q(token)}. An access token works for {r['limit']} calls. "
            f"Send a `POST` to {_q(path)} with the {_q(r['field'])} to get a new pair; a refresh token works once. "
            "A second login with the password is refused (`429`). The old token and the used refresh token stop working.")
    return (text, [_q(r["field"])], ("path", _q(path), _q(r["path"]))) if wrong else (text, [_q(r["field"]), _q(path)], None)


def _idempotency(spec: dict, wrong: bool) -> Statement:
    header = "X-Request-Id" if wrong else spec["idempotency"]["header"]
    text = (f"- Every write (`POST`, `PATCH`, `DELETE`) now needs a unique {_q(header)} header. A write without it gets `400`. "
            "Using the same value for another request gets `422`.")
    return (text, [], ("header", _q(header), _q(spec["idempotency"]["header"]))) if wrong else (text, [_q(header)], None)


def _moved(spec: dict, wrong: bool) -> Statement:
    prefix = _other(tspec._MOVED_PREFIXES, spec["moved"]["prefix"], f"moved:{spec['moved']['prefix']}") if wrong else spec["moved"]["prefix"]
    word = "/" + spec["path_word"].strip("/")
    text = (f"- Single-item endpoints moved from `{spec['base_path']}{word}/{{id}}` to {_q(spec['base_path'] + prefix + word + '/{id}')}. "
            "The old paths answer `308 Permanent Redirect`. The collection path does not change.")
    true = _q(spec["base_path"] + spec["moved"]["prefix"] + word + "/{id}")
    return (text, [], ("path", _q(spec["base_path"] + prefix + word + "/{id}"), true)) if wrong else (text, [true], None)


HANDLERS: dict[str, Handler] = {
    "add_drift_rename": _rename, "add_drift_cents": _cents, "add_drift_epoch": _epoch, "add_drift_nest": _nest, "add_drift_enum": _enum,
    "add_rate_limit": _rate_limit, "add_async_create": _async_create, "add_refresh_rotation": _refresh,
    "add_idempotency_key": _idempotency, "add_moved": _moved,
}
# `add_drift_enum` cannot be misleading: it has no header, field name, unit or path to get wrong
MISLEADING_ELIGIBLE = tuple(n for n in HANDLERS if n != "add_drift_enum")


def truth(name: str, spec: dict) -> list[str]:
    """The true facts (code-quoted, as they stand in the text) that the statement of one change shows."""
    return HANDLERS[name](spec, False)[1]


def render(spec: dict, names: list[str], level: str, seed: int | str) -> tuple[str, dict] | None:
    """(text, info) of the changelog of a release, or None for `none`. `info` = {level, omitted, misleading}: `omitted` is the
    change left out (`incomplete`), `misleading` is {change, kind, stated, truth} for the change that is stated wrongly."""
    if level not in LEVELS:
        raise ValueError(f"changelog must be one of {LEVELS}, got {level!r}")
    if level == "none":
        return None
    rng = random.Random(f"changelog:{seed}")
    omitted = misleading = None
    if level == "incomplete":
        if len(names) < 2:
            raise ValueError("an incomplete changelog needs a release of two changes or more")
        omitted = rng.choice(list(names))
    if level == "misleading":
        eligible = [n for n in names if n in MISLEADING_ELIGIBLE]
        if not eligible:
            raise ValueError(f"no change of {list(names)} can be stated wrongly")
        misleading = rng.choice(eligible)
    bullets, info_mis = [], None
    for name in names:
        if name == omitted:
            continue
        text, _, wrong = HANDLERS[name](spec, name == misleading)
        bullets.append(text)
        if wrong:
            info_mis = {"change": name, "kind": wrong[0], "stated": wrong[1], "truth": wrong[2]}
    rng.shuffle(bullets)
    minor, patch = rng.randint(3, 9), rng.randint(0, 3)
    date = f"2026-{rng.randint(2, 5):02d}-{rng.randint(1, 28):02d}"
    others = rng.sample(_OTHER_ITEMS, 2)
    text = (
        f"# {spec['title']} Changelog\n\n"
        f"## 2.{minor}.0 ({date})\n\n"
        "### Breaking changes\n\n" + "\n".join(bullets) + "\n\n"
        "### Other changes\n\n" + "\n".join(f"- {o}" for o in others) + "\n\n"
        f"## 2.{minor - 1}.{patch} (2026-01-{rng.randint(10, 28):02d})\n\n"
        "- Bug fixes.\n"
    )
    return text, {"level": level, "omitted": omitted, "misleading": info_mis}
