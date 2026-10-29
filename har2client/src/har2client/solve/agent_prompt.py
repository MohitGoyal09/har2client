"""Prompts of the scientist agent (DESIGN-CASES.md, "Case 2").

The placebo arm (budget 0, no tool) and the probe arms (P5, P20) get the **same text**: the only thing that
changes is the budget number. `render_prompts(..., budget)` is the one function that builds both messages.
"""

from __future__ import annotations

HTTP_REQUEST_TOOL = {
    "type": "function",
    "function": {
        "name": "http_request",
        "description": (
            "Send one HTTP request to the practice server and get back {status, headers, body}. "
            "The body is cut to 4,000 characters. Every request that is sent counts as one probe."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]},
                "path": {"type": "string", "description": "Path relative to the practice base URL, for example `/things`."},
                "query": {"type": "object", "description": "Query parameters (string values, or arrays of strings)."},
                "headers": {"type": "object", "description": "Request headers, name to string value."},
                "body": {"description": "A JSON object or array (sent as JSON), or a string (sent as it is)."},
            },
            "required": ["method", "path"],
        },
    },
}
SUBMIT_CLIENT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_client",
        "description": "Submit the finished `client.py` (the complete file). This ends the task.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "The complete text of client.py."}},
            "required": ["code"],
        },
    },
}
TOOLS = [HTTP_REQUEST_TOOL, SUBMIT_CLIENT_TOOL]

SYSTEM_PROMPT = """\
You are an expert Python engineer who reverse-engineers HTTP APIs from recorded traffic.

You receive a goal (a list of client methods to implement) and a HAR file recorded \
from a real session against the API. The HAR may also contain unrelated traffic \
such as analytics, fonts, static assets and HTML pages; ignore it. \
You also get a practice server for this API (described in the goal) that you may \
send a limited number of requests to.

Write one Python file, `client.py`, that defines `class Client` and implements \
every method listed in the goal. Your client will be run against a fresh server \
that has the same API shape but different data and different credentials, so it \
must work from the API's structure and not from the recorded or practice values.

Rules:
- Define `class Client` with exactly the constructor the goal states (its signature and what each argument means).
- Use only `httpx`, `requests` or the Python standard library.
- Work out from the HAR how requests are authenticated (header, query parameter, \
bearer token, basic auth, a login call, a session cookie) and apply the credentials \
the constructor receives the same way.
- Never hard-code a host, a base URL or any credential taken from the HAR or from the practice server. Build \
every URL from `base_url`, which already includes any base path the HAR shows.
- Each method returns what the goal says it returns. When the goal says nothing \
else, it returns the parsed JSON response body (`None` when there is no body).
- Send the request body, when a method has one, as the HAR shows (normally JSON).
- Do not call endpoints that are not needed for the goal's methods.
- Deliver the complete file by calling `submit_client(code)` when that tool is available; \
otherwise reply with the complete file in a single ```python fenced block. Keep any explanation brief.
"""

PRACTICE_TEMPLATE = """\
## Practice server

A practice server runs this API. It has the same API shape as the real one, with its own data and its own account.

- Practice base URL: `{base_url}` (paths are relative to it)
- Practice username: `{username}`
- Practice password: `{password}` (for an API that uses a single key, this is the key)
- Probe budget: you may send at most {budget} requests to it with the `http_request` tool. Every request that is sent counts.
- The server that tests your client has other data and other credentials. Never put the practice URL, \
username, password or any practice id in `client.py`.
- Use the requests, if any, to check what you are unsure about after reading the recording.
"""


def practice_section(base_url: str, username: str, password: str, budget: int) -> str:
    return PRACTICE_TEMPLATE.format(base_url=base_url, username=username, password=password, budget=budget)


def build_agent_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_agent_user_prompt(goal: str, session_text: str, session_name: str, practice: str) -> str:
    heading = "Recorded session (HAR)" if session_name.endswith(".har") else f"Recorded session (`{session_name}`)"
    return (
        "# Goal\n\n"
        f"{goal.strip()}\n\n"
        f"{practice.strip()}\n\n"
        f"# {heading}\n\n"
        f"{session_text}\n\n"
        "# Task\n\n"
        "Write `client.py` now. It must define `class Client` with the constructor "
        "stated in the goal and the methods named in the goal, using only `httpx`, "
        "`requests` or the standard library, returning what the goal says each method "
        "returns, and never hard-coding hosts or credentials from the HAR or the practice server."
    )


def render_prompts(goal: str, session_text: str, session_name: str, *, base_url: str, username: str, password: str, budget: int) -> tuple[str, str]:
    """(system, user) prompt of one arm. Same text for every budget except the budget number."""
    section = practice_section(base_url, username, password, budget)
    return build_agent_system_prompt(), build_agent_user_prompt(goal, session_text, session_name, section)
