"""The `identifiable` label of a repair task: can the repair be worked out from the visible evidence at all?

A fix needs some facts. Standard ones (that a 428 means "send If-Match with the ETag of a GET", that a 401 after some calls
means "log in again", a `Link: rel="next"` header, a `{data, meta}` wrapper) follow from the HTTP status or a header shown
in the failure output. Arbitrary ones (the value of the hidden header, the login path and its field names, the name of the
cursor parameter) are shown only in the new recording: the failure output holds the failing call and its response, and
that response names at most the header (and only in the verbose error text), never its value, the login call or the
cursor parameter.

Rule (DESIGN-CASE3.md, "Identifiable label"): `both` and `recording_only` -> identifiable (the recording shows every new
behaviour: headers, login call, cursor parameter, wrapper, Link and ETag headers, the expiry and the new login).
`failure_only` -> identifiable unless a mutation needs an arbitrary fact. For two changes the task is identifiable when
both changes are. The label does not depend on `error_detail`: terse errors keep the status codes the standard facts rest on."""

from __future__ import annotations

from typing import Sequence

EVIDENCE_LEVELS = ("both", "failure_only", "recording_only")
EVIDENCE_SCOPES = ("full", "partial")

# mutation -> the arbitrary fact that only the recording shows
NEEDS_RECORDING: dict[str, str] = {
    "add_hidden_header": "value of the required header",
    "rename_hidden_header": "name and value of the required header",
    "apikey_to_bearer_login": "login path, login body fields and token field",
    "offset_to_cursor": "name of the cursor parameter",
}
FAILURE_ONLY_OK = ("none_to_link_header", "add_envelope", "add_etag", "add_token_expiry")
# The ten changes of the v3 hard repair tasks (`heal.mutations.HARD_MUTATIONS`). The failure output shows only the first failing
# check, so with `failure_only` a release is identifiable only when it is one change whose whole fix the failure shows: a 429
# with `Retry-After`. Every other new change needs a fact that only the recording shows (the wire name or format of a field, the
# format of a job answer, the refresh call, the header name, the new path prefix).
HARD_NEEDS_RECORDING: dict[str, str] = {
    "add_drift_rename": "new field name, in responses and in request bodies",
    "add_drift_cents": "new field name and unit, in responses and in request bodies",
    "add_drift_epoch": "unit and type of the timestamp",
    "add_drift_nest": "name of the nested object and its keys, in responses and in request bodies",
    "add_drift_enum": "new spelling of the values, in responses, filters and request bodies",
    "add_async_create": "job path, polling field and result field",
    "add_refresh_rotation": "refresh path, body field, rotation",
    "add_idempotency_key": "header name",
    "add_moved": "new path prefix",
}
HARD_FAILURE_ONLY_OK = ("add_rate_limit",)


def identifiable(mutations: str | Sequence[str], evidence: str) -> bool:
    names = (mutations,) if isinstance(mutations, str) else tuple(mutations)
    if evidence not in EVIDENCE_LEVELS:
        raise ValueError(f"evidence must be one of {EVIDENCE_LEVELS}, got {evidence!r}")
    known = (*NEEDS_RECORDING, *FAILURE_ONLY_OK, *HARD_NEEDS_RECORDING, *HARD_FAILURE_ONLY_OK)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"no identifiability rule for {unknown}")
    if evidence != "failure_only":
        return True
    if any(n in HARD_NEEDS_RECORDING or n in HARD_FAILURE_ONLY_OK for n in names):
        return len(names) == 1 and names[0] in HARD_FAILURE_ONLY_OK
    return not any(n in NEEDS_RECORDING for n in names)


def label(mutations: str | Sequence[str], evidence: str, scope: str = "full") -> str:
    """`yes`, `no` or `generalize`. `generalize`: the recording shows each change on some of the operations only (`scope`
    `partial`), so the rule must be applied to the others; it needs a recording (`both` or `recording_only`)."""
    if scope not in EVIDENCE_SCOPES:
        raise ValueError(f"evidence scope must be one of {EVIDENCE_SCOPES}, got {scope!r}")
    ok = identifiable(mutations, evidence)
    if scope == "partial":
        if evidence == "failure_only":
            raise ValueError("a partial evidence scope needs a recording")
        return "generalize" if ok else "no"
    return "yes" if ok else "no"
