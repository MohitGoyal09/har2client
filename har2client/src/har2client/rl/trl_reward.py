"""Plug the environments into TRL's `GRPOTrainer`: a prompt dataset and an execution-graded reward function.

    from har2client.rl.trl_reward import build_dataset, har2client_reward
    trainer = GRPOTrainer(model=..., reward_funcs=har2client_reward, train_dataset=build_dataset("build", n=64), args=...)

`build_dataset(case)` gives rows `{"prompt": <chat messages>, "task_id": ..., "task_spec": <JSON text>}`. The prompt is the
prompt of the solvers (goal and recording; case "repair" adds `client_v1.py` and the failure). `task_spec` holds the hidden
build parameters, so the reward can rebuild a task that is not in the cache of this machine. TRL passes the extra columns
to the reward function as keyword arguments, one entry per completion; keep `remove_unused_columns=False`.

`har2client_reward(prompts, completions, task_id, **kwargs)` returns one float in [0, 1] per completion: the share of hidden
tests the client in the completion passes on a fresh server (case "repair": the v2 server), with the penalties of
`env/reward.py`. No code block, code that does not parse, or no `class Client` gives 0.0. Completions are strings or chat
message lists; the last ```python block is the client. Grades run in a bounded pool of worker processes (`GradePool`).

The pool starts worker processes with `spawn`: the script that calls the trainer must have an `if __name__ == "__main__":`
guard, or each worker runs the script again. Run training with the grader in a container: the grader is not an OS sandbox.

`trl` and `datasets` are not imported here at module level; the reward function works without them. `build_dataset` returns
a `datasets.Dataset` when `datasets` is installed and a list of dicts when it is not.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client.env import repair_splits, repair_tasks, splits, tasks
from har2client.env.reward import GradePool, RewardConfig, compute_rewards
from har2client.env.repair_env import grade_repairs
from har2client.env.trl import completion_text
from har2client.solve.beam import extract_code
from har2client.solve.prompt import build_system_prompt, build_user_prompt

CASES = ("build", "repair")
REPAIR_PREFIX = "repair_"
DEFAULT_JOBS = 4  # threads that build the tasks of a dataset


def _row(case: str, spec: dict, cache_dir: Path | None) -> dict:
    """One dataset row; builds the task (cached) to read what the agent may see."""
    if case == "build":
        task_dir = tasks.ensure_task(spec, cache_dir)
        seen = tasks.read_visible(task_dir)
        user = build_user_prompt(seen["goal"], seen["observation"], seen["observation_file"])
        system = build_system_prompt()
        spec = tasks.normalize(spec)
    else:
        task_dir = repair_tasks.ensure_task(spec, cache_dir)
        seen = repair_tasks.read_visible(task_dir)
        extra = {"client_v1.py": seen["client_v1"], **({"failure.txt": seen["failure"]} if seen["failure"] is not None else {})}
        user = build_user_prompt(seen["goal"], seen["observation"], "session.har", extra)
        system = build_system_prompt(has_recording=seen["observation"] is not None)
        spec = repair_tasks.normalize(spec)
    return {
        "prompt": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "task_id": task_dir.name,
        "task_spec": json.dumps(spec, sort_keys=True),
    }


def build_dataset(
    case: str = "build", split: str = "train", n: int = 64, seed: int = 0, *, level: int | None = None, compact: bool = True,
    cache_dir: str | Path | None = None, jobs: int = DEFAULT_JOBS,
):
    """`n` prompt rows of a split: a `datasets.Dataset` if `datasets` is installed, else a list of dicts.
    `compact` (case "build"): no noise, short JSON lists, so prompts have about 4-8K tokens."""
    if case not in CASES:
        raise ValueError(f"unknown case {case!r}; known: {CASES}")
    cache = Path(cache_dir) if cache_dir else None
    if case == "build":
        specs = [{**s, "compact": compact} for s in splits.sample_specs(split, n, level=level, seed=seed)]
    else:
        specs = repair_splits.sample_specs(split, n, level=level, seed=seed)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        rows = list(pool.map(lambda spec: _row(case, spec, cache), specs))
    try:
        from datasets import Dataset
    except ImportError:
        return rows
    return Dataset.from_list(rows)


def _task_dir(task_id: str, task_spec: str | dict | None, cache_dir: Path | None) -> Path:
    """The task dir of a row: rebuilt from `task_spec` when there is one (cached), else looked up by `task_id`."""
    module = repair_tasks if task_id.startswith(REPAIR_PREFIX) else tasks
    if task_spec is not None:
        return module.ensure_task(task_spec, cache_dir)
    path = (cache_dir or module.default_cache_dir()) / task_id
    if not (path / "task.json").is_file():
        raise ValueError(f"task {task_id!r} is not in the cache {path.parent}; pass the `task_spec` column or the same cache_dir")
    return path


def make_har2client_reward(config: RewardConfig | None = None, cache_dir: str | Path | None = None, pool: GradePool | None = None):
    """A reward function with its own grading config, cache dir and pool (default: the shared bounded pool)."""
    cache = Path(cache_dir) if cache_dir else None

    def har2client_reward(prompts=None, completions=None, task_id=None, task_spec=None, **kwargs) -> list[float]:
        """One reward in [0, 1] per completion (see the module doc). `task_id` (and `task_spec`) hold one entry per completion."""
        if completions is None or task_id is None or len(task_id) != len(completions):
            raise ValueError("har2client_reward needs `completions` and a `task_id` column with one id per completion")
        specs = task_spec if task_spec is not None else [None] * len(completions)
        rewards = [0.0] * len(completions)
        build_items, repair_items = [], []  # (slot, (task_dir, code))
        for slot, (completion, tid, spec) in enumerate(zip(completions, task_id, specs)):
            code = extract_code(completion_text(completion))
            if code is None:
                continue
            target = repair_items if tid.startswith(REPAIR_PREFIX) else build_items
            target.append((slot, (_task_dir(tid, spec, cache), code)))
        for items, grade in ((build_items, compute_rewards), (repair_items, grade_repairs)):
            for (slot, _), result in zip(items, grade([item for _, item in items], config, pool) if items else []):
                rewards[slot] = float(result.reward)
        return rewards

    har2client_reward.__name__ = "har2client_reward"  # TRL logs reward functions by name
    return har2client_reward


har2client_reward = make_har2client_reward()
