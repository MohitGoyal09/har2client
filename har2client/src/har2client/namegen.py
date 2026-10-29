"""Ask Beam for rewordings (U2) and twin renames (U3); validate; re-ask; fall back. Results are cached by `variants`."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from har2client import ops as ops_mod
from har2client import variants as v

BASE_URL = "https://api.reflection.ai/openai/v1"
MODEL = "Beam-1.0-501B-A23B"
EFFORT = "low"
API_KEY_ENV = "REFLECTION_API_KEY"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
MAX_REASKS = 2
MAX_COMPLETION_TOKENS = 32000
MAX_TRIES = 4
MAX_SAMPLE_ROUTES = 12


class NameGenError(RuntimeError):
    """One Beam answer could not be used (bad JSON, empty). The caller may ask again."""


# ------------------------------------------------------------------ Beam client

def _read_key() -> str | None:
    key = os.environ.get(API_KEY_ENV)
    if key:
        return key
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == API_KEY_ENV and value.strip():
                return value.strip().strip("\"'")
    return None


def _parse_json(text: str | None) -> dict:
    if not text:
        raise NameGenError("empty answer")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    try:
        out = json.loads(text[start : end + 1])
    except ValueError as why:
        raise NameGenError(f"answer is not JSON: {why}") from why
    if not isinstance(out, dict):
        raise NameGenError("answer is not a JSON object")
    return out


def make_ask(effort: str = EFFORT, client=None) -> v.Ask:
    """Beam through the OpenAI SDK. The key comes from REFLECTION_API_KEY or har2client/.env (never printed)."""
    if client is None:
        key = _read_key()
        if not key:
            raise v.VariantError(f"{API_KEY_ENV} is not set and {ENV_FILE} has no key; cannot generate rewordings")
        from openai import OpenAI

        client = OpenAI(base_url=BASE_URL, api_key=key, max_retries=0)

    def ask(system: str, user: str, schema: dict) -> dict:
        from openai import APIStatusError, BadRequestError

        request = {
            "model": MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "reasoning_effort": effort,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": True}},
        }
        for attempt in range(1, MAX_TRIES + 1):
            try:
                reply = client.chat.completions.create(**request)
                return _parse_json(reply.choices[0].message.content)
            except BadRequestError:
                if "response_format" not in request:
                    raise
                del request["response_format"]  # the endpoint rejected structured output: ask in plain text
            except APIStatusError as exc:
                if "daily_allowance_exhausted" in str(exc) or exc.status_code not in (429,) and exc.status_code < 500:
                    raise v.VariantError(f"Beam refused the request: {exc.status_code}") from exc
                if attempt == MAX_TRIES:
                    raise v.VariantError("Beam is unavailable after retries") from exc
                time.sleep(min(2.0**attempt, 30.0))
        raise v.VariantError("Beam gave no usable answer")

    return ask


# ------------------------------------------------------------------ names, summaries, descriptions

NAMES_SYSTEM = """You write interface text for a benchmark that tests whether a model reads recorded HTTP traffic instead of matching strings.
You get API operations with their real wire details (route and parameters). For each operation write:
- name: a new Python method name (snake_case, 1 to 4 words) that a developer could plausibly choose for what the operation does.
- params[].name: a new snake_case argument name for every parameter, distinct within the operation.
- summary: only when `summary_rewrite` is true, a rewrite of `summary` with the same meaning. Otherwise copy `summary`.
- description: one plain sentence (at most 25 words) saying what the operation does.
- body_description: one plain sentence on the JSON body argument; empty string when `has_body` is false.
- params[].description: one plain sentence on what that argument means to the caller.
HARD RULE: no word you write, in any name or sentence of an operation, may share a word of 3 or more letters with that operation's `forbidden` list.
Compare after splitting camelCase, snake_case and brackets, ignoring case and a plural `s`.
Example: forbidden `account` and `type` bans `account`, `accounts`, `account_kind` and `type_code`; `holding_category` is allowed.
So use synonyms or a different angle (for example `ledger` for `account`). Names must be natural English, not random strings.
Method names must differ across the API. Do not mention HTTP verbs, URLs, headers or the word `endpoint`. Answer with JSON only."""

NAMES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ops"],
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "name", "summary", "description", "body_description", "params"],
                "properties": {
                    "key": {"type": "string"},
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                    "body_description": {"type": "string"},
                    "params": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["key", "name", "description"],
                            "properties": {"key": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}},
                        },
                    },
                },
            },
        }
    },
}


def _job(op: dict, feedback: dict[str, str] | None, entry: dict | None) -> dict:
    forbidden = sorted({t for text in [*re.split(r"[/{}]", op["path"]), *(p["name"] for p in op["params"])] for t in v.tokens(text)})
    job = {
        "key": op["op_id"],
        "method": op["method"],
        "path": op["path"],
        "summary": op["summary"],
        "summary_rewrite": bool(v.colliding_tokens(op["summary"], v.op_forbidden(op))),
        "has_body": bool(op["body"]),
        "forbidden": forbidden,
        "params": [
            {"key": v.param_key(p), "wire_name": p["name"], "in": p["in"], "type": ops_mod.py_type(p["schema"]), "required": p["required"]}
            for p in op["params"]
        ],
    }
    if feedback:
        job["fix"] = {f: {"your_answer": v._field_path(entry or {}, f), "problem": why} for f, why in feedback.items()}
    return job


def _merge(entry: dict, op: dict, reply: dict | None, wanted: set[str] | None) -> None:
    """Copy the asked-for fields of one reply item into `entry` (wanted None = all)."""
    if not reply:
        return

    def take(field: str, value: object) -> None:
        if (wanted is None or field in wanted) and isinstance(value, str):
            v.set_field(entry, field, value.strip())

    for field in ("name", "summary", "description", "body_description"):
        take(field, reply.get(field))
    by_key = {p.get("key"): p for p in reply.get("params") or [] if isinstance(p, dict)}
    for param in op["params"]:
        key = v.param_key(param)
        for attr in ("name", "description"):
            take(f"param:{key}:{attr}", (by_key.get(key) or {}).get(attr))


def generate_entries(by_id: dict[str, dict], keys: list[str], ask: v.Ask, taken: set[str]) -> dict[str, dict]:
    """Entry per op in `keys`: valid, or deterministic fallback for fields still invalid after MAX_REASKS re-asks."""
    entries: dict[str, dict] = {}
    for k in keys:
        op = by_id[k]
        entries[k] = {} if v.colliding_tokens(op["summary"], v.op_forbidden(op)) else {"summary": op["summary"]}
    feedback: dict[str, dict[str, str] | None] = {k: None for k in keys}  # None = ask for everything
    for attempt in range(MAX_REASKS + 1):
        asked = [k for k in keys if feedback[k] is None or feedback[k]]
        if not asked:
            break
        jobs = [_job(by_id[k], feedback[k], entries[k]) for k in asked]
        note = "" if attempt == 0 else "Some earlier answers broke the rule. Answer again only for the fields listed in each `fix`; use empty strings elsewhere.\n"
        user = note + json.dumps({"operations": jobs}, indent=1)
        try:
            reply = ask(NAMES_SYSTEM, user, NAMES_SCHEMA)
        except NameGenError:
            reply = {}
        items = {i.get("key"): i for i in reply.get("ops") or [] if isinstance(i, dict)}
        taken_now = set(taken)
        for k in keys:
            if k in asked:
                wanted = None if feedback[k] is None else set(feedback[k])
                _merge(entries[k], by_id[k], items.get(k), wanted)
            problems = [(f, why) for f, why in v.validate_entry(by_id[k], entries[k], taken_now) if f != "*"]
            feedback[k] = dict(problems)
            if not problems:
                taken_now.add(entries[k]["name"])
    final: dict[str, dict] = {}
    taken_now = set(taken)
    for k in keys:
        entry = entries[k]
        entry["fallbacks"] = sorted(v.fill_fallbacks(by_id[k], entry, taken_now))
        taken_now.add(entry["name"])
        final[k] = entry
    return final


# ------------------------------------------------------------------ twin

TWIN_SYSTEM = """You build a counterfactual twin of a public API for a memorisation test: it must behave like the original but must not be recognisable from its words.
Invent:
- host: a plausible new API host name (for example `api.<brand>.<tld>`) that shares no word with the original host.
- title: a new API title in the same style that shares no word with the original title.
- words: for every listed word a different, natural English word. Keep the grammatical role and a sensible domain, so the renamed API still reads like a real product
  (for example `account` -> `ledger`, `holding` -> `parcel`). Each new word is one lower-case singular word of 3 to 14 letters, not a near-copy of the old word,
  and no two old words get the same new word. Do not use random strings or invented words.
Answer with JSON only."""

TWIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["host", "title", "words"],
    "properties": {
        "host": {"type": "string"},
        "title": {"type": "string"},
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["old", "new"],
                "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
            },
        },
    },
}


def generate_twin(op_table: dict, vocab: list[str], taken: set[str], host: str, ask: v.Ask, previous: dict | None = None) -> dict:
    """Twin plan. Valid entries of `previous` (an older cache) are kept; the rest is asked for."""
    title = op_table["title"]
    plan = {"host": None, "title": None, "words": {}, "fallbacks": []}
    if previous:
        if not v.check_twin_host(previous.get("host"), host):
            plan["host"] = previous["host"]
        if not v.check_twin_title(previous.get("title"), title):
            plan["title"] = previous["title"]
        used: set[str] = set()
        for old, new in (previous.get("words") or {}).items():
            if old in vocab and not v.check_twin_word(old, new, used, taken - {old}):
                plan["words"][old] = new
                used.add(new)
    routes = sorted({o["path"] for o in op_table["operations"]})[:MAX_SAMPLE_ROUTES]
    problems: dict[str, str] = {}
    for attempt in range(MAX_REASKS + 1):
        missing = [w for w in vocab if w not in plan["words"]]
        need_host, need_title = plan["host"] is None, plan["title"] is None
        if not (missing or need_host or need_title):
            break
        request = {"original_host": host, "original_title": title, "sample_routes": routes, "words": missing,
                   "need_host": need_host, "need_title": need_title}
        if problems:
            request["earlier_problems"] = problems
        try:
            reply = ask(TWIN_SYSTEM, json.dumps(request, indent=1), TWIN_SCHEMA)
        except NameGenError:
            reply = {}
        problems = {}
        used = set(plan["words"].values())
        if need_host:
            reason = v.check_twin_host(reply.get("host"), host)
            plan["host"], problems["host"] = (reply["host"], None) if not reason else (None, reason)
        if need_title:
            reason = v.check_twin_title(reply.get("title"), title)
            plan["title"], problems["title"] = (reply["title"], None) if not reason else (None, reason)
        answers = {i.get("old"): i.get("new") for i in reply.get("words") or [] if isinstance(i, dict)}
        for old in missing:
            reason = v.check_twin_word(old, answers.get(old), used, taken - {old})
            if reason:
                problems[f"word:{old}"] = f"{answers.get(old)!r}: {reason}"
            else:
                plan["words"][old] = answers[old]
                used.add(answers[old])
        problems = {k: r for k, r in problems.items() if r}
    used = set(plan["words"].values())
    for old in vocab:
        if old not in plan["words"]:
            plan["words"][old] = v.fallback_twin_word(old, used, taken - {old})
            used.add(plan["words"][old])
            plan["fallbacks"].append(f"word:{old}")
    if plan["host"] is None:
        plan["host"] = v.fallback_twin_host(op_table["api_id"], host)
        plan["fallbacks"].append("host")
    if plan["title"] is None:
        plan["title"] = v.fallback_twin_title(plan["host"], title)
        plan["fallbacks"].append("title")
    plan["fallbacks"].sort()
    plan["words"] = dict(sorted(plan["words"].items()))
    return plan
