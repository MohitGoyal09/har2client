"""Validity checks of the RL environment (DESIGN-RL.md, "Validity checks"). Written before the implementation."""

import hashlib
import json
from pathlib import Path

import pytest

from har2client.env import splits, tasks
from har2client.env.reward import compute_reward
from har2client.env.sources import SOURCES
from har2client.env.tasks import ensure_task, observation_path
from har2client.tierb import domains
from har2client.tierb.refclient import FLAWS, generate_client_source
from test_tierb_support import read_json

GOLDEN = Path(__file__).parent / "fixtures" / "golden_tierb" / "browser_build_digests.json"
LEVELS = range(4)
EMPTY_CLIENT = "class Client:\n    def __init__(self, base_url, username, password):\n        pass\n"
ALL_ON = {"auth": "cookie_csrf", "pagination": "link_header", "token_expiry": "on", "etag_update": "on", "hidden_header": "on", "envelope": "on"}


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("rl_cache")


def task_for(cache, source, level, seed=11):
    spec = splits.sample_specs("train", 1, level=level, source=source, seed=seed)[0]
    return spec, ensure_task(spec, cache)


def reference_code(task_dir):
    return (task_dir / "ref_client.py").read_text()


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("level", LEVELS)
def test_reference_client_scores_one_on_every_source_and_level(cache, source, level):
    _, task_dir = task_for(cache, source, level)
    result = compute_reward(task_dir, reference_code(task_dir))
    assert result.reward == 1.0, result.info
    assert result.info["pass_rate"] == 1.0 and not result.info["flags"]


@pytest.mark.parametrize("source", SOURCES)
def test_empty_client_scores_zero(cache, source):
    _, task_dir = task_for(cache, source, 2)
    assert compute_reward(task_dir, EMPTY_CLIENT).reward == 0


def test_unparseable_or_clientless_code_scores_zero(cache):
    _, task_dir = task_for(cache, "browser_har", 1)
    assert compute_reward(task_dir, "def broken(:\n").reward == 0
    assert compute_reward(task_dir, "x = 1\n").reward == 0


def replay_client(task_dir):
    """A client that returns recorded answers and never calls the server."""
    spec = read_json(task_dir / "engine.json")
    har = read_json(task_dir / "session.har")
    prefix = f"https://{spec['host']}{spec['base_path']}/{spec['path_word'].strip('/')}"
    bodies = [json.loads(e["response"]["content"]["text"]) for e in har["log"]["entries"]
              if e["request"]["url"].startswith(prefix) and e["response"]["content"].get("text", "").startswith(("{", "["))]
    pool = json.dumps(bodies)
    names = {op: f"{op}_{spec['domain'] if op == 'list' else spec['singular']}" for op in ("list", "get", "create", "update", "delete")}
    return f'''import json
POOL = json.loads({pool!r})
class Client:
    def __init__(self, base_url, username, password):
        pass
    def {names["list"]}(self, **kw):
        return [x for b in POOL if isinstance(b, list) for x in b]
    def {names["get"]}(self, **kw):
        return POOL[0]
    def {names["create"]}(self, **kw):
        return POOL[0]
    def {names["update"]}(self, **kw):
        return POOL[0]
    def {names["delete"]}(self, **kw):
        return None
'''


@pytest.mark.parametrize("source", ["browser_har", "mobile_har"])
def test_observation_replay_client_scores_at_most_a_tenth(cache, source):
    spec = splits.sample_specs("train", 1, level=2, source="browser_har", seed=5)[0]
    har_dir = ensure_task(spec, cache)
    code = replay_client(har_dir)
    assert compute_reward(har_dir, code).reward <= 0.1
    if source != "browser_har":
        other = ensure_task({**spec, "source": source}, cache)
        assert compute_reward(other, code).reward <= 0.1


def test_at_least_five_mutant_kinds_score_below_one(cache):
    spec = tasks.make_spec("shipments", ALL_ON, seed=77, source="browser_har", level=3, noise=0.3)
    task_dir = ensure_task(spec, cache)
    engine = read_json(task_dir / "engine.json")
    below = {}
    for flaw in FLAWS:
        code = generate_client_source(engine, (flaw,))
        below[flaw] = compute_reward(task_dir, code).reward
    assert sum(r < 1 for r in below.values()) >= 5, below


def test_hardcoded_secret_and_host_halve_the_reward(cache):
    _, task_dir = task_for(cache, "browser_har", 1)
    task = read_json(task_dir / "task.json")
    code = reference_code(task_dir) + f"\nSECRET = {task['credentials']['recording']['password']!r}\n"
    result = compute_reward(task_dir, code)
    assert result.info["flags"] == ["hardcoded_secret"] and result.reward == 0.5


def test_same_task_and_code_give_the_same_reward_twice(cache):
    _, task_dir = task_for(cache, "mobile_har", 3)
    flawed = reference_code(task_dir).replace("resp.raise_for_status()", "pass")
    a, b = compute_reward(task_dir, flawed), compute_reward(task_dir, flawed)
    assert a.reward == b.reward and a.info["failure_counts"] == b.info["failure_counts"]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("level", [1, 3])
def test_observation_has_no_hidden_data(cache, source, level):
    spec, task_dir = task_for(cache, source, level, seed=23)
    text = observation_path(task_dir).read_text()
    task, engine = read_json(task_dir / "task.json"), read_json(task_dir / "engine.json")
    test = task["credentials"]["test"]
    assert test["username"] not in text and test["password"] not in text
    test_ids = [i["id"] for i in domains.initial_items(engine["domain"], task["seeds"]["test"])]
    assert not any(i in text for i in test_ids)
    # engine values the recording does not show must not appear in any observation
    browser = ensure_task({**spec, "source": "browser_har"}, cache)
    visible = (browser / "session.har").read_text()
    secret_values = [s for s in _strings(engine) if len(s) >= 6 and s not in visible]
    assert not [s for s in secret_values if s in text]
    for hidden_name in ("tests.json", "engine.json", "task.json", "ref_client.py"):
        assert hidden_name not in text


def test_browser_build_output_is_unchanged_for_existing_configs(tmp_path):
    from har2client.tierb.build import build_tierb_task

    golden = json.loads(GOLDEN.read_text())
    for task_id, entry in golden.items():
        d = build_tierb_task(entry["row"], tmp_path, negatives=False)
        assert d.name == task_id
        digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}
        assert digests == entry["files"], task_id


def test_same_task_through_different_sources_has_the_same_hidden_tests(cache):
    spec = tasks.make_spec("tickets", {**ALL_ON, "auth": "bearer_login", "pagination": "cursor"}, seed=91, source="browser_har", level=3, noise=0.3)
    hidden = {s: read_json(ensure_task({**spec, "source": s}, cache) / "tests.json") for s in SOURCES}
    first = next(iter(hidden.values()))
    assert all(t == first for t in hidden.values())
