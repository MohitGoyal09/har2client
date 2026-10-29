"""Tier B task builder: config row -> task directory (goal.md, session.<ext>, engine.json, tests.json, task.json).

The recording is rendered through a traffic source (`browser_har` by default, see `har2client.env.sources`)."""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from har2client.browser import Site
from har2client.env import sources
from har2client.env.sources import canon
from har2client.env.sources.browser import render_har
from har2client.har import rewrite_host
from har2client.tierb import domains
from har2client.tierb.engine import TierBEngine
from har2client.tierb.grader import grade_tierb
from har2client.tierb.player import TierBRecorder, play_session
from har2client.tierb.refclient import generate_client_source
from har2client.tierb.spec import OPS, make_credentials, make_spec, method_name
from har2client.tierb.testgen import make_tests

SEED_SPACE = 10**9
TEST_SEED_TRIES = 30
DEFAULT_NOISE = 0.7
# flaw -> (config switch that makes it matter, failure reason the grader must report)
NEGATIVE_CLIENTS = {
    "ignore_pagination": (lambda c: c["pagination"] != "none", "incomplete_list"),
    "no_relogin": (lambda c: c["token_expiry"] == "on", "auth_401"),
    "no_if_match": (lambda c: c["etag_update"] == "on", "precondition_428"),
    "drop_hidden_header": (lambda c: c["hidden_header"] == "on", "bad_request_400"),
    "return_envelope": (lambda c: c["envelope"] == "on", "return_mismatch"),
    "no_csrf": (lambda c: c["auth"] == "cookie_csrf", "auth_401"),
}


class TierBBuildError(RuntimeError):
    """The built task failed its own validity gate."""


def render_goal(spec: dict, source: str = sources.DEFAULT_SOURCE) -> str:
    plural, one, field = spec["domain"], spec["singular"], spec["filter"]
    a_one = f"{'an' if one[0] in 'aeiou' else 'a'} {one}"
    methods = "\n".join(
        [
            f"- `{method_name(spec, 'list')}({field}: str | None = None) -> list[dict]`: Return every {one}, across all pages, in the order "
            f"the server gives them. When `{field}` is given, return only the {plural} whose `{field}` equals it.",
            f"- `{method_name(spec, 'get')}(id: str) -> dict`: Return the {one} with this id.",
            f"- `{method_name(spec, 'create')}(body: dict) -> dict`: Create {a_one} from `body` (a JSON object with the {one} fields). "
            f"Return the created {one}.",
            f"- `{method_name(spec, 'update')}(id: str, changes: dict) -> dict`: Change the given fields of {a_one}. Return the updated {one}.",
            f"- `{method_name(spec, 'delete')}(id: str) -> None`: Delete the {one}. Return `None`.",
        ]
    )
    return f"""# {spec['title']}

Write one Python file, `client.py`, that defines `class Client` for this API.
{sources.get(source).description}

## Constructor

`Client(base_url: str, username: str, password: str)`

- `base_url` is the API root and already includes any base path, with no trailing slash.
  In the recording it looks like `{spec['server_url']}`. Tests use another host and port.
- `username` and `password` are the account credentials. Some APIs use a single API key
  instead of a login; for those, `password` is the API key and `username` is not used.

## Methods

Tests call these methods in order, all on one `Client` instance, so anything the client
keeps (such as a login session) carries over from call to call. Each argument is a keyword
argument. A method that returns {a_one} returns the {one} object itself, not a wrapper around it.

{methods}

## Rules

- Use only `httpx`, `requests` and the standard library.
- Do not hard-code the host or any credential. Use `base_url`, `username` and `password`.
- Add no methods for endpoints that are not listed.
- Tests run against a fresh server with new data and new credentials.
- `client.py` must import everything it uses (for example `from typing import Any` for the return annotations).
"""


def features(spec: dict) -> list[str]:
    cfg = spec["config"]
    feats = ["tier:B", f"auth:{cfg['auth']}", f"pagination:{cfg['pagination']}", f"domain:{spec['domain']}", "base_path", "noise"]
    feats += [name for name in ("token_expiry", "etag_update", "hidden_header", "envelope") if cfg[name] == "on"]
    if cfg["auth"] != "apikey_header":
        feats.append(f"login_format:{spec['login']['format']}")
    if cfg["auth"] == "cookie_csrf":
        feats.append(f"csrf_in:{spec['csrf']['in']}")
    return feats


def _rewrite_response_origins(har: dict, loopback_origin: str, public_origin: str) -> dict:
    """Link headers hold absolute URLs of the recording server; show the public host instead."""
    for entry in har["log"]["entries"]:
        for header in entry["response"]["headers"]:
            header["value"] = header["value"].replace(loopback_origin, public_origin)
    return har


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _record(spec: dict, rng: random.Random, creds: dict, rec_seed: int) -> tuple[dict, dict]:
    """The recorded API calls (no noise yet) and a summary of the played session."""
    engine = TierBEngine(spec, rec_seed, creds)
    base_url = engine.start()
    recorder = TierBRecorder(latency_rng=random.Random(rng.random()), site=Site.from_url(spec["server_url"]))
    try:
        summary = play_session(spec, base_url, creds, rng, recorder)
    finally:
        engine.stop()
    raw = recorder.har()
    summary["requests"] = len(raw["log"]["entries"])
    har = rewrite_host(raw, spec["server_url"])
    port = urlparse(base_url).port
    har = _rewrite_response_origins(har, f"http://127.0.0.1:{port}", f"https://{spec['host']}")
    return har, summary


def _pick_test_seed(spec: dict, rng: random.Random, har_text: str, avoid: int) -> int:
    """A seed whose start data shares no id with the HAR (no response can be copied from the recording)."""
    for _ in range(TEST_SEED_TRIES):
        seed = rng.randrange(SEED_SPACE)
        if seed != avoid and not any(i["id"] in har_text for i in domains.initial_items(spec["domain"], seed)):
            return seed
    raise TierBBuildError("no test seed without ids from the recording")


def check_validity(task_dir: Path, spec: dict, tmp: Path) -> None:
    """The reference client must score 1.0; every applicable broken client must fail with its reason."""
    ref = tmp / "ref_client.py"  # outside the task dir: running a client there would leave a __pycache__ behind
    ref.write_text(generate_client_source(spec), encoding="utf-8")
    grade = grade_tierb(task_dir, ref)
    if grade["pass_rate"] != 1.0 or grade["invented_endpoint_calls"]:
        raise TierBBuildError(f"{task_dir.name}: reference client scored {grade['pass_rate']} {grade['failure_counts']}")
    for flaw, (applies, reason) in NEGATIVE_CLIENTS.items():
        if not applies(spec["config"]):
            continue
        bad = tmp / f"{flaw}.py"
        bad.write_text(generate_client_source(spec, (flaw,)), encoding="utf-8")
        result = grade_tierb(task_dir, bad)
        if result["pass_rate"] == 1.0 or reason not in result["failure_counts"]:
            raise TierBBuildError(f"{task_dir.name}: client with flaw {flaw} gave {result['failure_counts']}, expected {reason}")


def _observation(session: canon.Session, source: str, har: dict, build_seed: int, row_seed: int) -> str:
    """The text of the visible session file. The browser HAR is the one the recording produced; other sources
    render the same canonical session with their own random stream."""
    if source == sources.DEFAULT_SOURCE:
        return canon.dumps_har(har, session.compact)
    return sources.get(source).render(session, random.Random(f"obs:{source}:{build_seed}:{row_seed}"))


def build_tierb_task(
    row: dict, out_dir: str | Path, *, noise: float = DEFAULT_NOISE, build_seed: int = 0, negatives: bool = True,
    task_id: str | None = None, source: str = sources.DEFAULT_SOURCE, compact: bool = False,
) -> Path:
    """Build `out_dir/<task_id>/` from one config row {index, domain, config, seed}. Raises TierBBuildError
    when the reference client does not score 1.0 (or, with `negatives`, a broken client is not caught).

    `source` picks the vantage point of the visible file (`session.har` for the HAR sources) and `noise` the
    share of unrelated traffic in it. `compact` drops the noise and cuts response bodies. Credentials, seeds
    and hidden tests do not depend on `source`: the same task can be shown through every source."""
    source_info = sources.get(source)
    spec = make_spec(row["domain"], row["config"], row["seed"])
    rng = random.Random(f"tierb:{build_seed}:{row['seed']}")
    recording, test = make_credentials(spec, rng), make_credentials(spec, rng)
    while test["password"] == recording["password"] or test["username"] == recording["username"]:
        test = make_credentials(spec, rng)
    rec_seed = rng.randrange(SEED_SPACE)
    recorded, summary = _record(spec, rng, recording, rec_seed)
    session = canon.from_har(recorded, base_path=spec["base_path"], noise=noise, seed=f"{build_seed}:{row['seed']}")
    if compact:
        session = canon.compacted(session)
    # The browser rendering always runs: it draws from `rng` as it always did, so the test seed and the
    # credentials stay the same for every source, and it holds every id the other sources may show.
    har = render_har(session, rng)
    test_seed = _pick_test_seed(spec, rng, json.dumps(har), rec_seed)
    tests = make_tests(spec, test_seed)

    task_id = task_id or f"tierb_{row['index']:02d}_{spec['domain']}"
    task_dir = Path(out_dir) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    observation = _observation(session, source, har, build_seed, row["seed"])
    observation_file = f"session{source_info.extension}"
    (task_dir / "goal.md").write_text(render_goal(spec, source), encoding="utf-8")
    (task_dir / observation_file).write_text(observation, encoding="utf-8")
    _write_json(task_dir / "engine.json", spec)
    _write_json(task_dir / "tests.json", tests)
    extras = {} if source == sources.DEFAULT_SOURCE else {"source": source, "observation_file": observation_file}
    if compact:
        extras["compact"] = True
    _write_json(
        task_dir / "task.json",
        {
            "task_id": task_id,
            "tier": "B",
            "api_id": f"tierb:{spec['domain']}:{row['index']:02d}",
            "title": spec["title"],
            "server_url": spec["server_url"],
            "domain": spec["domain"],
            "config": spec["config"],
            "goal_ops": [method_name(spec, op) for op in OPS],
            "seeds": {"recording": rec_seed, "test": test_seed, "spec": row["seed"]},
            "build_seed": build_seed,
            "credentials": {"recording": recording, "test": test},
            "noise_ratio": noise,
            "session_calls": summary["requests"],
            "har_entries": len(har["log"]["entries"]) if source == sources.DEFAULT_SOURCE else len(json.loads(observation)["log"]["entries"]),
            "recorded_items": len(summary["listed"]),
            "features": features(spec),
            **extras,
        },
    )
    (task_dir / "ref_client.py").write_text(generate_client_source(spec), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="h2c_tierb_") as tmp:
        if negatives:
            check_validity(task_dir, spec, Path(tmp))
        else:
            ref = Path(tmp) / "ref_client.py"
            ref.write_text(generate_client_source(spec), encoding="utf-8")
            grade = grade_tierb(task_dir, ref)
            if grade["pass_rate"] != 1.0:
                raise TierBBuildError(f"{task_id}: reference client scored {grade['pass_rate']} {grade['failure_counts']}")
    return task_dir
