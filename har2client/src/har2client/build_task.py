"""Spec -> task directory (goal.md, session.har, task.json, op_table.json, tests.json)."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from har2client import ops as ops_mod
from har2client import variants
from har2client.browser import Site
from har2client.har import HarRecorder, mix_noise, rewrite_host
from har2client.mock import MockServer, generate_response
from har2client.player import play_session
from har2client.testgen import CALLS_PER_OP, check_arg_types, make_tests

MAX_GOAL_OPS = 8
TEST_SEED_TRIES = 20
SEED_SPACE = 10**9


class TaskBuildError(ops_mod.UnusableApi):
    """The API is usable in general but no solvable task could be made from it."""


def make_secret(security_type: str, rng: random.Random) -> str:
    if security_type == "basic":
        return f"user_{rng.getrandbits(32):08x}:pw_{rng.getrandbits(64):016x}"
    return f"sk_{rng.getrandbits(96):024x}"


def task_id_for(api_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", api_id).strip("_") or "task"


def _json_bodies(har: dict) -> list:
    bodies = []
    for entry in har["log"]["entries"]:
        text = entry["response"]["content"]["text"]
        if text and text[0] in "[{":
            try:
                bodies.append(json.loads(text))
            except ValueError:
                continue
    return bodies


def _colliding_ops(op_table: dict, goal: list[str], seed: int, recorded: list) -> list[str]:
    """Goal ops whose test responses could equal something in the HAR (copying would pass)."""
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    return [
        op_id
        for op_id in goal
        if any(generate_response(by_id[op_id], seed, n) in recorded for n in range(CALLS_PER_OP))
    ]


def _pick_test_seed(op_table: dict, goal: list[str], rng: random.Random, recorded: list, avoid_seed: int):
    """(seed, goal): a test seed where no goal op can be answered from the HAR; drops ops that always can."""
    seed, colliding = avoid_seed, goal
    for _ in range(TEST_SEED_TRIES):
        seed = rng.randrange(SEED_SPACE)
        if seed == avoid_seed:
            continue
        colliding = _colliding_ops(op_table, goal, seed, recorded)
        if not colliding:
            return seed, goal
    kept = [g for g in goal if g not in colliding]
    if not kept:
        raise TaskBuildError("every goal response can be copied from the recording")
    return seed, kept


CLOSED_BOOK_NOTE = """The file `session.har` is empty: it holds no recorded requests in this variant.
Use what you know about this API."""
RECORDING_NOTE = """The file `session.har` is a browser recording of a user session. It holds real
requests and responses for this API, mixed with unrelated traffic."""


def render_goal(op_table: dict, goal_ops: list[str], methods: str | None = None, closed_book: bool = False) -> str:
    """`methods` replaces the method list (`opaque` and `reworded_desc` variants); `closed_book` says the recording is empty."""
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    if methods is None:
        methods = "\n".join(f"- `{ops_mod.signature(by_id[g])}`: {by_id[g]['summary']}" for g in goal_ops)
    return f"""# {op_table['title']}

Write one Python file, `client.py`, that defines `class Client` for this API.
{CLOSED_BOOK_NOTE if closed_book else RECORDING_NOTE}

## Constructor

`Client(base_url: str, api_key: str)`

- `base_url` is the API root and already includes any base path, with no trailing slash.
  In the recording it looks like `{op_table['server_url']}`. Tests use another host and port.
- `api_key` is the credential as one string. (If the API uses HTTP basic auth, it is `"user:pass"`.)

## Methods

Implement exactly these methods. Each argument is a keyword argument; `body` is the JSON
request body as a `dict`. A method returns the parsed JSON response body, or `None` if the
response has no body.

{methods}

## Rules

- Use only `httpx`, `requests` and the standard library.
- Do not hard-code the host or any credential. Use `base_url` and `api_key`.
- Add no methods for endpoints that are not listed.
- Tests run against a fresh server with new data and a new key.
- `client.py` must import everything it uses (for example `from typing import Any` for the return annotations).
"""


def _features(op_table: dict, goal_ops: list[str], noise: float) -> list[str]:
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    chosen = [by_id[g] for g in goal_ops]
    sec = op_table["security"]
    feats = [f"auth:{sec['type']}"]
    if sec["in"]:
        feats.append(f"apikey_in:{sec['in']}")
    if op_table["base_path"]:
        feats.append("base_path")
    if any(p["in"] == "path" for o in chosen for p in o["params"]):
        feats.append("path_params")
    if any(p["in"] == "query" for o in chosen for p in o["params"]):
        feats.append("query_params")
    if any(o["body"] for o in chosen):
        feats.append("json_body")
    if noise > 0:
        feats.append("noise")
    if len(op_table["operations"]) > len(goal_ops):
        feats.append("partial_coverage")
    return feats


def _write_json(path: Path, data: object, indent: int = 2) -> None:
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")


def build_task(
    spec: dict,
    api_id: str,
    out_dir: str | Path,
    noise: float = 0.7,
    seed: int = 0,
    goal_ops: list[str] | None = None,
    names: str = "exact",
    twin: bool = False,
    closed_book: bool = False,
    rewordings_dir: str | Path | None = None,
    ask: variants.Ask | None = None,
) -> Path:
    """Build `out_dir/{task_id}/`. Raises UnusableApi before writing anything when the API is skipped.

    `names` (exact | reworded | reworded_desc | opaque), `twin` and `closed_book` build the Tier A controls (see `variants`)."""
    if names not in variants.NAMES_MODES:
        raise ValueError(f"names must be one of {variants.NAMES_MODES}, got {names!r}")
    op_table = ops_mod.normalize(spec, api_id)
    reason = ops_mod.usability_reason(op_table)
    if reason:
        raise ops_mod.UnusableApi(reason)
    if twin:
        op_table, id_map = variants.apply_twin(op_table, variants.resolve_twin(op_table, rewordings_dir, ask))
        goal_ops = None if goal_ops is None else [id_map.get(g, g) for g in goal_ops]
    rng = random.Random(f"{seed}:{api_id}")
    security = op_table["security"]["type"]
    recording_secret = make_secret(security, rng)
    test_secret = make_secret(security, rng)
    while test_secret == recording_secret:
        test_secret = make_secret(security, rng)

    spec_order = [o["op_id"] for o in op_table["operations"]]
    if goal_ops is None:
        eligible = [o["op_id"] for o in op_table["operations"] if o["response"]["schema"] is not None]
        picked = set(rng.sample(eligible, min(MAX_GOAL_OPS, len(eligible))))
        goal_ops = [i for i in spec_order if i in picked]
    unknown = [g for g in goal_ops if g not in spec_order]
    if unknown or not goal_ops or len(goal_ops) > MAX_GOAL_OPS:
        raise TaskBuildError(f"bad goal ops: {goal_ops} (unknown: {unknown}, max {MAX_GOAL_OPS})")

    recording_seed = rng.randrange(SEED_SPACE)
    mock = MockServer(op_table, seed=recording_seed, secret=recording_secret)
    base_url = mock.start()
    recorder = HarRecorder(
        latency_rng=random.Random(f"{seed}:{api_id}:latency"), site=Site.from_url(op_table["server_url"])
    )
    try:
        calls = play_session(op_table, goal_ops, base_url, recording_secret, rng, recorder=recorder)
    finally:
        mock.stop()
    for call in calls:
        check_arg_types(op_table, call["op_id"], call["kwargs"])
    routes = [op_table["base_path"] + o["path"] for o in op_table["operations"]]
    har = mix_noise(rewrite_host(recorder.har(), op_table["server_url"]), noise, rng, op_table["base_path"], routes)

    test_seed, goal_ops = _pick_test_seed(op_table, goal_ops, rng, _json_bodies(har), recording_seed)
    tests = make_tests(op_table, goal_ops, test_seed, avoid=[c["kwargs"] for c in calls])
    for test in tests:
        check_arg_types(op_table, test["method"], test["kwargs"])

    out_table, out_goal, out_tests, name_map, docs = op_table, goal_ops, tests, None, None
    if names != "exact":
        entries = variants.resolve_name_entries(op_table, goal_ops, rewordings_dir, ask, twin)
        out_table, out_goal, out_tests, name_map, docs = variants.apply_names(op_table, goal_ops, tests, names, entries)
    methods = variants.render_opaque_methods(out_table, out_goal, docs) if names in variants.DESCRIBED_MODES else None
    if closed_book:
        har = variants.blank_har(har)

    task_id = task_id_for(api_id) + variants.variant_suffix(names, twin, closed_book)
    task_dir = Path(out_dir) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "goal.md").write_text(render_goal(out_table, out_goal, methods, closed_book), encoding="utf-8")
    _write_json(task_dir / "session.har", har)
    _write_json(task_dir / "op_table.json", out_table)
    _write_json(task_dir / "tests.json", out_tests)
    _write_json(
        task_dir / "task.json",
        {
            "task_id": task_id,
            "api_id": api_id,
            "title": op_table["title"],
            "server_url": op_table["server_url"],
            "goal_ops": out_goal,
            "seeds": {"recording": recording_seed, "test": test_seed},
            "build_seed": seed,
            "recording_secret": recording_secret,
            "test_secret": test_secret,
            "noise_ratio": noise,
            "session_calls": len(calls),
            "har_entries": len(har["log"]["entries"]),
            "spec_ops": len(op_table["operations"]),
            "features": _features(op_table, goal_ops, noise),
            "variant": {"names": names, "twin": twin, "closed_book": closed_book},
            **({"name_map": name_map} if name_map else {}),
        },
    )
    return task_dir
