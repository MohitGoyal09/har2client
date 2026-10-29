"""Prompt builders for the Beam baseline solver."""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are an expert Python engineer who reverse-engineers HTTP APIs from recorded traffic.

You receive a goal (a list of client methods to implement) and a HAR file recorded \
from a real session against the API. The HAR may also contain unrelated traffic \
such as analytics, fonts, static assets and HTML pages; ignore it.

Write one Python file, `client.py`, that defines `class Client` and implements \
every method listed in the goal. Your client will be run against a fresh server \
that has the same API shape but different data and different credentials, so it \
must work from the API's structure and not from the recorded values.

Rules:
- Define `class Client` with exactly the constructor the goal states (its signature and what each argument means).
- Use only `httpx`, `requests` or the Python standard library.
- Work out from the HAR how requests are authenticated (header, query parameter, \
bearer token, basic auth, a login call, a session cookie) and apply the credentials \
the constructor receives the same way.
- Never hard-code a host, a base URL or any credential taken from the HAR. Build \
every URL from `base_url`, which already includes any base path the HAR shows.
- Each method returns what the goal says it returns. When the goal says nothing \
else, it returns the parsed JSON response body (`None` when there is no body).
- Send the request body, when a method has one, as the HAR shows (normally JSON).
- Do not call endpoints that are not needed for the goal's methods.
- Reply with the complete file in a single ```python fenced block. Put any \
explanation outside the block and keep it brief.
"""


# System prompt of a repair task that shows no recording (`evidence=failure_only`): the files it names are the ones the task has.
NO_RECORDING_SYSTEM_PROMPT = """\
You are an expert Python engineer who repairs HTTP API clients after the API changed.

You receive a goal (a list of client methods to implement), the previous client (`client_v1.py`, written for the old \
version of the API) and the output of that client failing against the changed API (`failure.txt`). \
There is no recording of the new API.

Write one Python file, `client.py`, that defines `class Client` and implements \
every method listed in the goal. Your client will be run against a fresh server \
that has the same API shape but different data and different credentials, so it \
must work from the API's structure and not from the values in the failure output.

Rules:
- Define `class Client` with exactly the constructor the goal states (its signature and what each argument means).
- Use only `httpx`, `requests` or the Python standard library.
- Work out from the previous client and the failure output how requests are authenticated (header, query parameter, \
bearer token, basic auth, a login call, a session cookie) and apply the credentials \
the constructor receives the same way.
- Never hard-code a host, a base URL or any credential taken from the failure output. Build \
every URL from `base_url`, which already includes any base path the goal shows.
- Each method returns what the goal says it returns. When the goal says nothing \
else, it returns the parsed JSON response body (`None` when there is no body).
- Send the request body, when a method has one, as the previous client does (normally JSON).
- Do not call endpoints that are not needed for the goal's methods.
- Reply with the complete file in a single ```python fenced block. Put any \
explanation outside the block and keep it brief.
"""


# Added to the system prompt of a repair task that has release notes (v3); neutral: it does not say how far to trust them.
CHANGELOG_NOTE = "\nThe task also gives you the release notes of the new version of the API (`CHANGELOG.md`).\n"


def build_system_prompt(has_recording: bool = True, has_changelog: bool = False) -> str:
    """`has_recording=False`: a repair task without `session.har`; the default is the prompt of case 1 and of repair tasks
    that show a recording (unchanged). `has_changelog=True`: the task has a `CHANGELOG.md`; the prompt names it, and only then."""
    base = SYSTEM_PROMPT if has_recording else NO_RECORDING_SYSTEM_PROMPT
    return base + CHANGELOG_NOTE if has_changelog else base


EXTRA_HEADINGS = {
    "client_v1.py": "Previous client (`client_v1.py`, written for the old version of the API)",
    "failure.txt": "Failure of the previous client against the changed API (`failure.txt`)",
    "CHANGELOG.md": "Release notes of the changed API (`CHANGELOG.md`)",
}


def _extra_sections(extra_files: dict[str, str] | None) -> str:
    """Sections for the extra visible files of a repair task (case 3), in a fixed order; empty for case 1."""
    out = ""
    for name, heading in EXTRA_HEADINGS.items():
        if extra_files and name in extra_files:
            out += f"# {heading}\n\n{extra_files[name].strip()}\n\n"
    return out


def build_user_prompt(
    goal: str, har_text: str | None, session_name: str | None = "session.har", extra_files: dict[str, str] | None = None
) -> str:
    """`session_name` is the visible session file; HAR files keep the heading earlier runs used.
    `extra_files` (name -> text) adds the repair-task files after the goal; without it the prompt is the case 1 prompt.
    `har_text=None` (a repair task without a recording) leaves the session section out."""
    session = ""
    if har_text is not None:
        heading = "Recorded session (HAR)" if session_name.endswith(".har") else f"Recorded session (`{session_name}`)"
        session = f"# {heading}\n\n{har_text}\n\n"
    source = "the HAR" if har_text is not None else "the failure output"
    return (
        "# Goal\n\n"
        f"{goal.strip()}\n\n"
        f"{_extra_sections(extra_files)}"
        f"{session}"
        "# Task\n\n"
        "Write `client.py` now. It must define `class Client` with the constructor "
        "stated in the goal and the methods named in the goal, using only `httpx`, "
        "`requests` or the standard library, returning what the goal says each method "
        f"returns, and never hard-coding hosts or credentials from {source}. Reply with "
        "the file in a single ```python fenced block."
    )
