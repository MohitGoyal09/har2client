"""Tier A controls: lexical overlap (`--names`, U2) and memorisation (`--twin`, `--closed-book`, U3).

Pure logic lives here: token and collision checks, name validation and fallbacks, the twin
renamer, applying a naming plan to a built task, and the cache of Beam answers
(`data/rewordings/<api_id>.json`). Calling Beam lives in `namegen.py`.

Naming plans are applied to the *finished* op table, tests and goal, so the HAR and the hidden
tests of `reworded` and `opaque` builds are the same as in the `exact` build. In the op table the
goal names are `op_id` and `py_name`; the wire names stay in `path` and `params[].name`. The grader
and the reference client already read the wire from there, so neither needs the goal names.
"""

from __future__ import annotations

import copy
import hashlib
import json
import keyword
import re
from itertools import count
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, urlunparse

from har2client import ops as ops_mod
from har2client.browser import registered_domain
from har2client.har import wrap

NAMES_MODES = ("exact", "reworded", "reworded_desc", "opaque")
DESCRIBED_MODES = ("reworded_desc", "opaque")  # goal shows a description line per method and argument
MIN_TOKEN_LEN = 3  # tokens shorter than this never count as a collision
MAX_NAME_LEN = 40
MAX_TEXT_LEN = 200
RESERVED_NAMES = frozenset({"body", "self", "close", "cls", "client"})
REWORDINGS_DIR = Path(__file__).resolve().parents[2] / "data" / "rewordings"
OPAQUE_METHOD, OPAQUE_ARG = "op", "arg"
Ask = Callable[[str, str, dict], dict]  # (system prompt, user prompt, JSON schema) -> parsed JSON


class VariantError(RuntimeError):
    """A variant cannot be built (for example Beam is needed and unavailable)."""


# ------------------------------------------------------------------ tokens and collisions

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")  # camelCase, snake_case and acronym words


def words(text: str) -> list[str]:
    """Lower-case letter words of `text`, split on camelCase, separators, digits and brackets."""
    return [w.lower() for w in _WORD.findall(text)]


def tokens(text: str) -> list[str]:
    """Words of at least MIN_TOKEN_LEN letters."""
    return [w for w in words(text) if len(w) >= MIN_TOKEN_LEN]


def stem(word: str) -> str:
    """Plural-insensitive form: `accounts` and `account` share one stem."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "shes", "ches", "xes", "zes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def pluralize(word: str) -> str:
    if len(word) > 2 and word.endswith("y") and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def stems(texts) -> set[str]:
    return {stem(t) for text in texts for t in tokens(text)}


def colliding_tokens(text: str, forbidden: set[str]) -> list[str]:
    """Tokens of `text` that share a token of >= 3 letters (up to plural) with the forbidden stems."""
    return sorted({t for t in tokens(text) if stem(t) in forbidden})


def op_forbidden(op: dict) -> set[str]:
    """Stems of everything the wire shows for this op: path segments and parameter names."""
    texts = [seg for seg in re.split(r"[/{}]", op["path"]) if seg]
    texts += [p["name"] for p in op["params"]]
    return stems(texts)


def param_key(param: dict) -> str:
    return f"{param['in']}:{param['name']}"


# ------------------------------------------------------------------ name and text checks

_SNAKE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def check_name(name: object, forbidden: set[str], taken: set[str]) -> str | None:
    """None when `name` is a valid, free, collision-free identifier; else the reason."""
    if not isinstance(name, str) or not name:
        return "no name given"
    if not _SNAKE.fullmatch(name) or len(name) > MAX_NAME_LEN:
        return "not a lower-case snake_case identifier of at most 40 characters"
    if keyword.iskeyword(name) or name in RESERVED_NAMES:
        return "reserved word"
    if name in taken:
        return "already used by another name"
    shared = colliding_tokens(name, forbidden)
    return f"shares {shared} with the wire names" if shared else None


def check_text(text: object, forbidden: set[str]) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "no text given"
    if "\n" in text or len(text) > MAX_TEXT_LEN:
        return "must be one line of at most 200 characters"
    shared = colliding_tokens(text, forbidden)
    return f"shares {shared} with the wire names" if shared else None


# Neutral fallback names, tried in order. A stem is skipped when it collides with the op's wire.
FALLBACK_METHOD_STEMS = ("routine", "procedure", "action", "handler", "job", "step")
FALLBACK_PARAM_STEMS = ("input", "setting", "option", "detail", "entry", "slot")
GENERIC_SUMMARY = {
    "GET": "Reads information from the service.",
    "POST": "Sends new information to the service.",
    "PUT": "Replaces information held by the service.",
    "PATCH": "Changes part of the information held by the service.",
    "DELETE": "Removes information from the service.",
}
LAST_RESORT_TEXT = "Performs one operation."
LAST_RESORT_PARAM_TEXT = "An input value."


def fallback_name(stem_words: tuple[str, ...], forbidden: set[str], taken: set[str]) -> str:
    for n in count(1):
        for word in stem_words:
            candidate = word if n == 1 else f"{word}_{n}"
            if check_name(candidate, forbidden, taken) is None:
                return candidate
    raise AssertionError("unreachable")


def fallback_text(candidates: list[str], forbidden: set[str], last_resort: str) -> str:
    return next((c for c in candidates if check_text(c, forbidden) is None), last_resort)


def param_fallback_text(param: dict) -> list[str]:
    where = {"path": "in the address of the request", "query": "as a filter or option of the request"}[param["in"]]
    kind = ops_mod.py_type(param["schema"])
    need = "required" if param["required"] else "optional"
    return [f"A {need} {kind} value sent {where}.", f"A {need} {kind} value."]


# ------------------------------------------------------------------ entries (one per goal op)

def _split_field(field: str) -> tuple[str, str | None, str | None]:
    """'name' -> ('name', None, None); 'param:query:x:name' -> ('param', 'query:x', 'name')."""
    if not field.startswith("param:"):
        return field, None, None
    key, _, attr = field[len("param:"):].rpartition(":")
    return "param", key, attr


def _field_path(entry: dict, field: str):
    kind, key, attr = _split_field(field)
    if kind != "param":
        return entry.get(kind)
    return (entry.get("params") or {}).get(key, {}).get(attr)


def set_field(entry: dict, field: str, value: str) -> None:
    kind, key, attr = _split_field(field)
    if kind != "param":
        entry[kind] = value
        return
    entry.setdefault("params", {}).setdefault(key, {})[attr] = value


def validate_entry(op: dict, entry: object, taken: set[str]) -> list[tuple[str, str]]:
    """(field, reason) for every invalid part of a cached or generated entry; [] when usable."""
    if not isinstance(entry, dict):
        return [("*", "missing")]
    forbidden = op_forbidden(op)
    problems = []

    def judge(field: str, reason: str | None) -> None:
        if reason:
            problems.append((field, reason))

    judge("name", check_name(entry.get("name"), forbidden, taken))
    judge("summary", check_text(entry.get("summary"), forbidden))
    judge("description", check_text(entry.get("description"), forbidden))
    if op["body"]:
        judge("body_description", check_text(entry.get("body_description"), forbidden))
    used: set[str] = set()
    for param in op["params"]:
        key = param_key(param)
        pname = (entry.get("params") or {}).get(key, {}).get("name")
        reason = check_name(pname, forbidden, used)
        if reason is None and pname in ("body",):
            reason = "reserved word"
        judge(f"param:{key}:name", reason)
        if reason is None:
            used.add(pname)
        judge(f"param:{key}:description", check_text(_field_path(entry, f"param:{key}:description"), forbidden))
    return problems


def fill_fallbacks(op: dict, entry: dict, taken: set[str]) -> list[str]:
    """Replace every invalid field of `entry` by a deterministic safe value. Returns the fields replaced."""
    forbidden = op_forbidden(op)
    replaced = []
    for field, _ in validate_entry(op, entry, taken):
        kind, key, attr = _split_field(field)
        if field == "*":
            continue
        if field == "name":
            value = fallback_name(FALLBACK_METHOD_STEMS, forbidden, taken)
        elif field == "summary":
            value = fallback_text([GENERIC_SUMMARY[op["method"]]], forbidden, LAST_RESORT_TEXT)
        elif field == "description":
            value = fallback_text([GENERIC_SUMMARY[op["method"]]], forbidden, LAST_RESORT_TEXT)
        elif field == "body_description":
            value = fallback_text(["The JSON object sent with the request."], forbidden, LAST_RESORT_PARAM_TEXT)
        elif attr == "name":
            used = {p.get("name") for k, p in (entry.get("params") or {}).items() if k != key and p.get("name")}
            value = fallback_name(FALLBACK_PARAM_STEMS, forbidden, used | RESERVED_NAMES)
        else:
            param = next(p for p in op["params"] if param_key(p) == key)
            value = fallback_text(param_fallback_text(param), forbidden, LAST_RESORT_PARAM_TEXT)
        set_field(entry, field, value)
        replaced.append(field)
    return replaced


# ------------------------------------------------------------------ the cache file

def store_path(api_id: str, directory: str | Path | None = None) -> Path:
    stem_ = re.sub(r"[^A-Za-z0-9._-]+", "_", api_id).strip("_") or "api"
    return Path(directory or REWORDINGS_DIR) / f"{stem_}.json"


def load_store(path: Path, api_id: str) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"api_id": api_id}


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _ask_or_default(ask: Ask | None) -> Ask:
    if ask is not None:
        return ask
    from har2client.namegen import make_ask

    return make_ask()


def resolve_name_entries(
    op_table: dict, goal_ops: list[str], directory: str | Path | None, ask: Ask | None, twin: bool
) -> dict[str, dict]:
    """Entry per goal op, from the cache when valid, else generated (with Beam) and cached."""
    path = store_path(op_table["api_id"], directory)
    store = load_store(path, op_table["api_id"])
    section = store.setdefault("names", {}).setdefault("twin" if twin else "original", {})
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    taken = {i for i in by_id if i not in goal_ops}
    chosen: dict[str, dict] = {}
    need = []
    for op_id in goal_ops:
        entry = section.get(op_id)
        if entry and not validate_entry(by_id[op_id], entry, taken):
            chosen[op_id] = entry
            taken.add(entry["name"])
        else:
            need.append(op_id)
    if need:
        from har2client.namegen import generate_entries

        generated = generate_entries(by_id, need, _ask_or_default(ask), taken)
        section.update(generated)
        chosen.update(generated)
        save_store(path, store)
    return {g: chosen[g] for g in goal_ops}


# ------------------------------------------------------------------ applying names to a built task

def opaque_entries(op_table: dict, goal_ops: list[str], entries: dict[str, dict]) -> dict[str, dict]:
    """Entries with the generated names replaced by op_1..op_n and arg_1..arg_n (per method)."""
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    out = {}
    for i, op_id in enumerate(goal_ops, 1):
        entry = copy.deepcopy(entries[op_id])
        entry["name"] = f"{OPAQUE_METHOD}_{i}"
        for j, param in enumerate(by_id[op_id]["params"], 1):
            entry["params"][param_key(param)]["name"] = f"{OPAQUE_ARG}_{j}"
        out[op_id] = entry
    return out


def apply_names(
    op_table: dict, goal_ops: list[str], tests: list[dict], mode: str, entries: dict[str, dict]
) -> tuple[dict, list[str], list[dict], dict, dict]:
    """Rename goal methods and parameters. Returns (op table, goal ops, tests, name_map, docs).

    `name_map` = {"methods": {goal name: wire op}, "params": {goal method: {goal arg: wire name}}}.
    `docs` = {goal method: {"description": str, "args": {arg: str}}} (shown by `opaque` and `reworded_desc`).
    """
    if mode == "opaque":
        entries = opaque_entries(op_table, goal_ops, entries)
    table = copy.deepcopy(op_table)
    by_id = {o["op_id"]: o for o in table["operations"]}
    method_of = {g: entries[g]["name"] for g in goal_ops}
    arg_of: dict[str, dict[str, str]] = {}
    name_map: dict = {"methods": {}, "params": {}}
    docs: dict = {}
    for g in goal_ops:
        op, entry = by_id[g], entries[g]
        arg_of[g] = {p["py_name"]: entry["params"][param_key(p)]["name"] for p in op["params"]}
        new = method_of[g]
        name_map["methods"][new] = g
        name_map["params"][new] = {arg_of[g][p["py_name"]]: p["name"] for p in op["params"]}
        docs[new] = {"description": entry["description"], "args": {arg_of[g][p["py_name"]]: entry["params"][param_key(p)]["description"] for p in op["params"]}}
        if op["body"]:
            docs[new]["args"]["body"] = entry["body_description"]
        op["summary"] = entry["description"] if mode == "opaque" else entry["summary"]
        for p in op["params"]:
            p["py_name"] = arg_of[g][p["py_name"]]
        op["op_id"] = new
    new_tests = []
    for test in tests:
        g = test["method"]
        kwargs = {arg_of[g].get(k, k): v for k, v in test["kwargs"].items()}
        new_tests.append({**test, "method": method_of[g], "kwargs": kwargs})
    return table, [method_of[g] for g in goal_ops], new_tests, name_map, docs


def render_opaque_methods(op_table: dict, goal_ops: list[str], docs: dict) -> str:
    """The goal's method list for `opaque` and `reworded_desc`: signature, one-line description, one line per argument."""
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    lines = []
    for g in goal_ops:
        op = by_id[g]
        lines.append(f"- `{ops_mod.signature(op)}`: {docs[g]['description']}")
        for arg in ops_mod.ordered_args(op):
            lines.append(f"  - `{arg['py_name']}`: {docs[g]['args'][arg['py_name']]}")
    return "\n".join(lines)


def blank_har(har: dict) -> dict:
    """A valid HAR 1.2 with the same creator and no entries (closed-book variant)."""
    empty = wrap([])
    log = {**har["log"], "entries": []}
    log.setdefault("version", empty["log"].get("version", "1.2"))
    return {**har, "log": log}


def variant_suffix(names: str, twin: bool, closed_book: bool) -> str:
    memory = "__twin_closed" if twin and closed_book else ("__twin" if twin else "") + ("__closed" if closed_book else "")
    return ("" if names == "exact" else f"__{names}") + memory


# ------------------------------------------------------------------ twin: counterfactual rename

TWIN_STOP = frozenset(
    """api apis rest json xml http https www app com net org dev get put set add all new the and for
    not has can are was you our use any each key keys id ids uuid guid url uri token tokens auth oauth
    type types name names data value values status code error errors message messages info meta text
    page pages limit offset size count total sort order filter query search item items list result
    results version config date time true false null none other""".split()
)
TWIN_DROP_KEYS = frozenset({"description", "title", "example", "examples", "externalDocs", "xml", "discriminator"})
TWIN_KEEP_KEYS = frozenset({"enum", "default", "const"})
NATURAL_WORDS = tuple(
    """anchor basket bridge candle canyon cedar compass copper cotton crystal dolphin ember falcon
    fern forest garden glacier harbor hazel island jasmine kettle lantern lemon maple meadow mirror
    orchard otter paddle pebble pepper pillar quartz raven ribbon river saddle sparrow summit tulip
    valley velvet walnut willow window yarrow zephyr acorn birch breeze clover dune fjord grove
    heron lagoon marble nectar oasis plume quill reef spruce thistle umbra vine wren""".split()
)


def is_renameable(word: str) -> bool:
    return len(word) >= MIN_TOKEN_LEN and word not in TWIN_STOP and stem(word) not in TWIN_STOP


def _walk_properties(node: object, acc: set[str]) -> None:
    if isinstance(node, list):
        for v in node:
            _walk_properties(v, acc)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in TWIN_DROP_KEYS or k in TWIN_KEEP_KEYS:
                continue
            if k == "properties" and isinstance(v, dict):
                for name, sub in v.items():
                    acc.add(name)
                    _walk_properties(sub, acc)
            else:
                _walk_properties(v, acc)


def wire_identifiers(op_table: dict) -> list[str]:
    """Every string the twin renames: path text, parameter names, schema field names, title, base path."""
    acc: set[str] = {op_table["title"], op_table["base_path"]}
    for op in op_table["operations"]:
        acc.add(op["path"])
        acc.update(p["name"] for p in op["params"])
        for p in op["params"]:
            _walk_properties(p["schema"], acc)
        if op["body"]:
            _walk_properties(op["body"]["schema"], acc)
        _walk_properties(op["response"]["schema"], acc)
    return sorted(acc)


def twin_vocabulary(op_table: dict) -> list[str]:
    """Singular stems of the renameable words, most frequent first."""
    counts: dict[str, int] = {}
    for text in wire_identifiers(op_table):
        for w in tokens(text):
            if is_renameable(w):
                counts[stem(w)] = counts.get(stem(w), 0) + 1
    return sorted(counts, key=lambda w: (-counts[w], w))


def all_stems(op_table: dict) -> set[str]:
    """Stems of every word in the table's names and summaries (a twin word may not reuse one)."""
    texts = wire_identifiers(op_table) + [o["summary"] for o in op_table["operations"]]
    texts += [o["op_id"] for o in op_table["operations"]]
    return stems(texts)


def check_twin_word(old: str, new: object, used: set[str], taken: set[str]) -> str | None:
    if not isinstance(new, str) or not re.fullmatch(r"[a-z]{3,14}", new):
        return "must be one lower-case English word of 3 to 14 letters"
    if keyword.iskeyword(new) or stem(new) != new:
        return "must be a singular word that is not a Python keyword"
    if new in TWIN_STOP or stem(new) in taken or new in used:
        return "already used in this API or by another rename"
    if new.startswith(old[:4]) or old.startswith(new[:4]):
        return "too close to the original word"
    return None


_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")


def check_twin_host(host: object, original: str) -> str | None:
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        return "not a lower-case host name"
    shared = {t for t in tokens(host.replace("-", " ")) if is_renameable(t)} & {
        t for t in tokens(original.replace("-", " ")) if is_renameable(t)
    }
    if host == original or shared:
        return f"shares {sorted(shared) or 'everything'} with the original host"
    return None


def check_twin_title(title: object, original: str) -> str | None:
    if not isinstance(title, str) or not title.strip() or len(title) > 60 or "\n" in title:
        return "must be a one-line title of at most 60 characters"
    shared = {stem(t) for t in tokens(title) if is_renameable(t)} & {stem(t) for t in tokens(original) if is_renameable(t)}
    return f"shares {sorted(shared)} with the original title" if shared else None


def fallback_twin_word(old: str, used: set[str], taken: set[str]) -> str:
    start = int(hashlib.sha256(old.encode()).hexdigest(), 16)
    for i in range(len(NATURAL_WORDS)):
        candidate = NATURAL_WORDS[(start + i) % len(NATURAL_WORDS)]
        if check_twin_word(old, candidate, used, taken) is None:
            return candidate
    for n in count(2):  # the word list is spent: add a plain suffix
        candidate = f"{NATURAL_WORDS[start % len(NATURAL_WORDS)]}{'x' * n}"
        if check_twin_word(old, candidate, used, taken) is None:
            return candidate
    raise AssertionError("unreachable")


def fallback_twin_host(api_id: str, original: str) -> str:
    start = int(hashlib.sha256(api_id.encode()).hexdigest(), 16)
    for i in range(len(NATURAL_WORDS)):
        a, b = NATURAL_WORDS[(start + i) % len(NATURAL_WORDS)], NATURAL_WORDS[(start // 7 + i * 3) % len(NATURAL_WORDS)]
        host = f"api.{a}{b}.com"
        if check_twin_host(host, original) is None:
            return host
    raise AssertionError("unreachable")


def fallback_twin_title(host: str, original: str) -> str:
    brand = registered_domain(host).split(".")[0].capitalize()
    title = f"{brand} API"
    return title if check_twin_title(title, original) is None else f"{brand} Service"


def resolve_twin(op_table: dict, directory: str | Path | None, ask: Ask | None) -> dict:
    """{"host", "title", "words": {stem: new word}, "fallbacks": [...]} from the cache or Beam."""
    path = store_path(op_table["api_id"], directory)
    store = load_store(path, op_table["api_id"])
    original_host = urlparse(op_table["server_url"]).hostname or ""
    vocab, taken = twin_vocabulary(op_table), all_stems(op_table)
    plan = store.get("twin")
    if plan and not _twin_problems(plan, vocab, taken, op_table["title"], original_host):
        return plan
    from har2client.namegen import generate_twin

    plan = generate_twin(op_table, vocab, taken, original_host, _ask_or_default(ask), previous=plan)
    store["twin"] = plan
    save_store(path, store)
    return plan


def _twin_problems(plan: dict, vocab: list[str], taken: set[str], title: str, host: str) -> list[str]:
    problems = []
    if check_twin_host(plan.get("host"), host):
        problems.append("host")
    if check_twin_title(plan.get("title"), title):
        problems.append("title")
    mapped, used = plan.get("words") or {}, set()
    for old in vocab:
        new = mapped.get(old)
        if check_twin_word(old, new, used, taken - {old}):
            problems.append(f"word:{old}")
        else:
            used.add(new)
    return problems


class Renamer:
    """Replaces words inside identifiers, paths and sentences, keeping separators and letter case."""

    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    def _word(self, match: re.Match) -> str:
        word = match.group(0)
        low = word.lower()
        base = stem(low)
        if base not in self.mapping:
            return word
        new = self.mapping[base] if base == low else pluralize(self.mapping[base])
        if len(word) > 1 and word.isupper():
            return new.upper()
        return new.capitalize() if word[0].isupper() else new

    def text(self, text: str) -> str:
        return _WORD.sub(self._word, text)

    def schema(self, node: object) -> object:
        if isinstance(node, list):
            return [self.schema(v) for v in node]
        if not isinstance(node, dict):
            return node
        out: dict = {}
        for key, value in node.items():
            if key in TWIN_DROP_KEYS or key.startswith("x-"):
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {self.text(n): self.schema(s) for n, s in value.items()}
            elif key == "required" and isinstance(value, list):
                out[key] = [self.text(v) if isinstance(v, str) else v for v in value]
            elif key in TWIN_KEEP_KEYS:
                out[key] = copy.deepcopy(value)
            else:
                out[key] = self.schema(value)
        return out


def _twin_credential_name(op_table: dict, plan: dict) -> str:
    """The apiKey header or query name with its renameable words replaced: by the plan's word when it has
    one, else by a deterministic fallback word (such a name, e.g. `hapikey`, can identify the API)."""
    name = op_table["security"]["name"]
    taken, used = all_stems(op_table), set(plan["words"].values())
    extra: dict[str, str] = {}
    for word in tokens(name):
        base = stem(word)
        if is_renameable(word) and base not in plan["words"] and base not in extra:
            extra[base] = fallback_twin_word(base, used | set(extra.values()), taken - {base})
    return Renamer({**extra, **plan["words"]}).text(name)


def apply_twin(op_table: dict, plan: dict) -> tuple[dict, dict[str, str]]:
    """Counterfactual op table (host, title, paths, parameter and field names). Returns it and {old op_id: new op_id}."""
    ren = Renamer(plan["words"])
    table = copy.deepcopy(op_table)
    parsed = urlparse(op_table["server_url"])
    netloc = plan["host"] + (f":{parsed.port}" if parsed.port else "")
    table["base_path"] = ren.text(op_table["base_path"])
    table["server_url"] = urlunparse(parsed._replace(netloc=netloc, path=ren.text(parsed.path)))
    table["title"] = plan["title"]
    if table["security"].get("name"):
        table["security"] = {**table["security"], "name": _twin_credential_name(op_table, plan)}
    id_map = {}
    for op in table["operations"]:
        new_id = ren.text(op["op_id"])
        id_map[op["op_id"]] = new_id
        op["op_id"], op["path"], op["summary"] = new_id, ren.text(op["path"]), ren.text(op["summary"])
        for p in op["params"]:
            p["name"], p["py_name"], p["schema"] = ren.text(p["name"]), ren.text(p["py_name"]), ren.schema(p["schema"])
        if op["body"]:
            op["body"] = {**op["body"], "schema": ren.schema(op["body"]["schema"])}
        op["response"] = {**op["response"], "schema": ren.schema(op["response"]["schema"])}
    table["dropped"] = [{**d, "path": ren.text(d["path"]), "reason": ren.text(d["reason"])} for d in table["dropped"]]
    if len(set(id_map.values())) != len(id_map):
        raise VariantError("twin rename made two op ids equal")
    return table, id_map
