"""The 8 API mutations of case 3 and the v1/v2 spec pairing.

The v2 spec is the canonical Tier B spec of the changed API, `make_spec(domain, v2_config, seed)`, so the
from-scratch comparison task (case 1 on the same row) is the very same API. The v1 spec is derived from it by
undoing the one change; host, brand, paths, login fields and every other detail stay the same.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Callable, Sequence

from har2client.tierb import config as cfg
from har2client.tierb import spec as tspec
from har2client.tierb.config import DRIFT_KINDS, switch

Revert = Callable[[dict, random.Random], None]  # edits a copy of the v2 spec in place into the v1 spec


@dataclass(frozen=True)
class Mutation:
    name: str
    applies: Callable[[dict], bool]  # to the v2 config: is this change the one that was made to reach it
    revert: Revert
    description: str


def _set(spec: dict, **over: str) -> None:
    spec["config"] = {**spec["config"], **over}


def _revert_add_hidden_header(spec: dict, rng: random.Random) -> None:
    _set(spec, hidden_header="off")
    spec["hidden_header"] = None


def _revert_rename_hidden_header(spec: dict, rng: random.Random) -> None:
    """v1 used another header (name and value) for the same job."""
    options = [(n, make) for n, make in tspec._HIDDEN_HEADERS if n != spec["hidden_header"]["name"]]
    name, make_value = rng.choice(options)
    spec["hidden_header"] = {"name": name, "value": make_value(rng)}


def _revert_apikey_to_bearer_login(spec: dict, rng: random.Random) -> None:
    _set(spec, auth="apikey_header")


def _revert_offset_to_cursor(spec: dict, rng: random.Random) -> None:
    _set(spec, pagination="offset")
    spec["page"] = {**spec["page"], "param": rng.choice(tspec._OFFSET_PARAMS)}


def _revert_none_to_link_header(spec: dict, rng: random.Random) -> None:
    _set(spec, pagination="none")
    spec["page"] = {**spec["page"], "param": None}


def _revert_add_envelope(spec: dict, rng: random.Random) -> None:
    _set(spec, envelope="off")


def _revert_add_etag(spec: dict, rng: random.Random) -> None:
    _set(spec, etag_update="off")


def _revert_add_token_expiry(spec: dict, rng: random.Random) -> None:
    _set(spec, token_expiry="off")


MUTATIONS: tuple[Mutation, ...] = (
    Mutation("add_hidden_header", lambda c: c["hidden_header"] == "on", _revert_add_hidden_header, "every call now needs a constant header"),
    Mutation("apikey_to_bearer_login", lambda c: c["auth"] == "bearer_login" and c["token_expiry"] == "off", _revert_apikey_to_bearer_login,
             "API key header replaced by a login call and a bearer token"),
    Mutation("offset_to_cursor", lambda c: c["pagination"] == "cursor", _revert_offset_to_cursor, "offset paging replaced by cursor paging"),
    Mutation("none_to_link_header", lambda c: c["pagination"] == "link_header", _revert_none_to_link_header, "lists are now paged with a Link header"),
    Mutation("add_envelope", lambda c: c["envelope"] == "on", _revert_add_envelope, "responses are now wrapped in {data, meta}"),
    Mutation("add_etag", lambda c: c["etag_update"] == "on", _revert_add_etag, "updates now need If-Match from a GET"),
    Mutation("add_token_expiry", lambda c: c["token_expiry"] == "on", _revert_add_token_expiry, "sessions now expire after 4 calls"),
    Mutation("rename_hidden_header", lambda c: c["hidden_header"] == "on", _revert_rename_hidden_header, "the required constant header was renamed"),
)


def _drift_mutation(kind: str, description: str) -> Mutation:
    def revert(spec: dict, rng: random.Random) -> None:
        _set(spec, drift=[k for k in switch(spec["config"], "drift") if k != kind])

    return Mutation(f"add_drift_{kind}", lambda c: kind in switch(c, "drift"), revert, description)


def _switch_mutation(name: str, description: str) -> Mutation:
    """A protocol change: the switch `name` (of the hard-repair switches) is on in v2 and off in v1."""

    def revert(spec: dict, rng: random.Random) -> None:
        _set(spec, **{name: "off"})

    return Mutation(f"add_{name}", lambda c: switch(c, name) == "on", revert, description)


# The ten changes of the v3 hard repair tasks (DESIGN-CASE3-HARD.md). v1 is the v2 spec with the switch reverted. They are
# not part of `MUTATIONS` (the v2 suite and its row selection stay as they were); releases combine them (`heal.release`).
HARD_MUTATIONS: tuple[Mutation, ...] = (
    _drift_mutation("rename", "a field was renamed on the wire (responses and request bodies)"),
    _drift_mutation("cents", "a decimal amount became integer cents under a new field name"),
    _drift_mutation("epoch", "created_at is a Unix timestamp instead of an ISO string"),
    _drift_mutation("nest", "two flat fields moved into one nested object"),
    _drift_mutation("enum", "the values of an enum field changed their spelling"),
    _switch_mutation("rate_limit", "requests are rate limited: 429 with Retry-After"),
    _switch_mutation("async_create", "create answers 202 and a job to poll"),
    _switch_mutation("refresh_rotation", "login gives a refresh token, access tokens are short, refresh tokens rotate"),
    _switch_mutation("idempotency_key", "every write needs a unique Idempotency-Key header"),
    _switch_mutation("moved", "item paths moved to a new prefix; the old ones answer 308"),
)
DRIFT_MUTATIONS = tuple(m.name for m in HARD_MUTATIONS[:len(DRIFT_KINDS)])
PROTOCOL_MUTATIONS = tuple(m.name for m in HARD_MUTATIONS[len(DRIFT_KINDS):])
BY_NAME = {m.name: m for m in (*MUTATIONS, *HARD_MUTATIONS)}


# mutations that edit the same switch of the config (or the same hidden header) never go together in one task
SWITCH_GROUP = {
    "add_hidden_header": "hidden_header", "rename_hidden_header": "hidden_header", "apikey_to_bearer_login": "auth",
    "offset_to_cursor": "pagination", "none_to_link_header": "pagination", "add_envelope": "envelope",
    "add_etag": "etag_update", "add_token_expiry": "token_expiry",
    **{m.name: m.name.removeprefix("add_") for m in HARD_MUTATIONS},  # one switch each (a drift kind counts as its own switch)
}


def names_of(mutation: str | Sequence[str]) -> tuple[str, ...]:
    """A mutation name or a sequence of names as a tuple of names (the "+"-joined form is not accepted)."""
    return (mutation,) if isinstance(mutation, str) else tuple(mutation)


def derive_v1_spec(v2_spec: dict, mutation: str | Sequence[str], seed: int | str) -> dict:
    """The v1 spec: `v2_spec` with the mutation (or mutations, undone one after the other) undone. Raises ValueError when
    a mutation does not apply to the config, two mutations edit the same switch, or the v1 config is not valid.
    One name gives the same spec as before the compound tasks existed."""
    names = names_of(mutation)
    groups = [SWITCH_GROUP[n] for n in names]
    if len(set(groups)) != len(groups):
        raise ValueError(f"mutations {names} edit the same switch")
    v1 = copy.deepcopy(v2_spec)
    for name in names:
        m = BY_NAME[name]
        if not m.applies(v2_spec["config"]):
            raise ValueError(f"mutation {name} does not apply to config {v2_spec['config']}")
        m.revert(v1, random.Random(f"heal-v1:{name}:{seed}"))
    if not cfg.is_valid(v1["config"]):
        raise ValueError(f"v1 config of {'+'.join(names)} is not valid: {v1['config']}")
    return v1


def v1_flaws(v1_spec: dict) -> tuple[str, ...]:
    """Flaws of the generated reference client that make it the minimal correct client for v1.
    The generator always adds a re-login branch for login auth; v1 without token expiry never needs it, and keeping it
    would make `add_token_expiry` a no-op."""
    c = v1_spec["config"]
    return ("no_relogin",) if c["auth"] in cfg.LOGIN_MODES and c["token_expiry"] == "off" else ()


def v1_config(v2_config: dict, mutation: str | Sequence[str]) -> dict:
    """The v1 config of a v2 config. Raises ValueError when the mutation does not apply."""
    stub = {"config": v2_config, "hidden_header": {"name": "", "value": ""}, "page": {}}
    return derive_v1_spec(stub, mutation, 0)["config"]
