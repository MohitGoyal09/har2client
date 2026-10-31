"""TRL reward function and dataset (`rl/trl_reward.py`): reference client 1.0, garbage 0.0, chat-format completions parsed."""

import json

import pytest

from har2client.env.env import TrafficToClientEnv
from har2client.env.repair_env import RepairEnv
from har2client.env.reward import GradePool
from har2client.rl.trl_reward import build_dataset, make_har2client_reward

EMPTY = "class Client:\n    def __init__(self, base_url, username, password):\n        pass\n"


def fenced(code: str) -> str:
    return f"Here is the client.\n```python\n{code}\n```\n"


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("trl_cache")


@pytest.fixture(scope="module")
def reward_fn(cache):
    pool = GradePool(0)  # in this process: no spawned workers inside pytest
    yield make_har2client_reward(cache_dir=cache, pool=pool)
    pool.close()


@pytest.fixture(scope="module")
def build_task(cache):
    env = TrafficToClientEnv(source="browser_har", split="train", curriculum="L0", compact=True, seed=0, cache_dir=cache)
    obs = env.reset()
    return obs["task_id"], env.reference_code()


@pytest.fixture(scope="module")
def repair_task(cache):
    env = RepairEnv(split="train", curriculum="R0", seed=0, cache_dir=cache)
    obs = env.reset()
    return obs["task_id"], env.reference_code()


def test_reference_client_scores_one_and_garbage_scores_zero(reward_fn, build_task, repair_task):
    (b_id, b_ref), (r_id, r_ref) = build_task, repair_task
    completions = [fenced(b_ref), "", "no code here", fenced("def broken(:"), fenced(r_ref), fenced(EMPTY)]
    ids = [b_id, b_id, b_id, b_id, r_id, r_id]
    rewards = reward_fn(prompts=[""] * 6, completions=completions, task_id=ids)
    assert rewards[0] == 1.0 and rewards[4] == 1.0
    assert rewards[1:4] == [0.0, 0.0, 0.0] and rewards[5] == 0.0


def test_chat_format_completion_is_parsed(reward_fn, build_task):
    task_id, ref = build_task
    chat = [{"role": "assistant", "content": fenced(ref)}]
    assert reward_fn(prompts=[[]], completions=[chat], task_id=[task_id]) == [1.0]


def test_dataset_rows_grade_through_task_spec(cache, build_task, repair_task):
    for case, (_, ref) in (("build", build_task), ("repair", repair_task)):
        row = build_dataset(case, "train", n=1, seed=0, cache_dir=cache)[0]
        assert [m["role"] for m in row["prompt"]] == ["system", "user"] and "class Client" in row["prompt"][1]["content"]
        assert row["task_id"].startswith("repair_" if case == "repair" else "rl_")
        assert json.loads(row["task_spec"])  # a JSON text: rows stay valid for `datasets`
        # a fresh cache: the reward rebuilds the task from the spec column
        pool = GradePool(0)
        try:
            fn = make_har2client_reward(cache_dir=cache / "rebuilt", pool=pool)
            assert fn(completions=[EMPTY], task_id=[row["task_id"]], task_spec=[row["task_spec"]]) == [0.0]
        finally:
            pool.close()


def test_unknown_task_id_without_spec_raises(reward_fn):
    with pytest.raises(ValueError, match="not in the cache"):
        reward_fn(completions=[fenced(EMPTY)], task_id=["rl_doesnotexist"])
