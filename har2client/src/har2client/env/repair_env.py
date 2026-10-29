"""`RepairEnv`: a correct client for the old version of an API, an API that changed, write the repaired client
(DESIGN-CASE3.md, "RL environment"). Does not touch `env/env.py` (case 2 is changing it).

    env = RepairEnv(split="train", curriculum="adaptive", seed=0)
    obs = env.reset()   # {task_id, goal, client_v1, failure (or None), observation (or None), observation_format}
    out = env.step({"type": "submit", "code": client_py_text})   # {"reward", "done": True, "info": {...}}

An episode is: observe, then one submit. Reward = `pass_rate` of the grader on a fresh v2 grading instance with the case 1
penalties (`env/reward.py`: hard-coded secret or host x 0.5, invented endpoint -0.1, no parse or no `class Client` -> 0).
`info` adds `regression_rate`, `patch_lines`, `patch_ast_nodes`; they are reported, never rewarded.

- `regression_rate`: share of the hidden tests that `client_v1.py` passes on the v2 grading instance and the submitted client
  fails (it broke what worked). The denominator is all hidden tests of the task.
- `patch_lines`, `patch_ast_nodes`: changed lines / AST nodes against `client_v1.py` (`heal.patch.patch_size`).

Levels R0-R3 and the splits are in `env/repair_splits.py`. TRL: `repair_reward_fn` (same contract as `env/trl.py`).
"""

from __future__ import annotations

import json
import random
import threading
from pathlib import Path

from har2client.env import repair_splits, repair_tasks
from har2client.env.curriculum import Curriculum
from har2client.env.reward import GradePool, RewardConfig, RewardResult, _combine, code_problem, default_pool
from har2client.env.trl import completion_text
from har2client.heal.patch import patch_size
from har2client.solve.beam import extract_code

_BASELINES: dict[str, list[bool]] = {}  # task dir -> which hidden tests client_v1.py passes on v2
_LOCK = threading.Lock()
_EMPTY_INFO = {"pass_rate": 0.0, "min_pass_rate_k": 0.0, "failure_counts": {}, "flags": [], "invented_endpoint_calls": 0, "milestones": None,
               "reliability_k": 0, "grade_time_s": 0.0}


def _job(task_dir: Path, code: str, config: RewardConfig) -> dict:
    return {"task_dir": str(task_dir), "code": code, "timeout_s": config.timeout_s, "want_log": config.milestones}


def _passed(grade: dict, n_tests: int | None = None) -> list[bool]:
    flags = [bool(r["passed"]) for r in grade["results"]]
    return flags + [False] * ((n_tests or 0) - len(flags))


def _patch_info(client_v1: str, code: str) -> dict:
    size = patch_size(client_v1, code)
    return {"patch_lines": size["changed_lines"], "patch_ast_nodes": size["changed_ast_nodes"]}


def grade_repairs(items: list[tuple[str | Path, str]], config: RewardConfig | None = None, pool: GradePool | None = None) -> list[RewardResult]:
    """Rewards of many (task_dir, code) pairs, with the repair metrics in `info`. All grades run in the pool at once;
    the v1-on-v2 baseline of a task is graded once and kept."""
    config, pool = config or RewardConfig(), pool or default_pool()
    if config.reliability_k != 1:
        raise ValueError("RepairEnv grades one fresh instance per submit (reliability_k must be 1)")
    dirs = [Path(task_dir) for task_dir, _ in items]
    v1_code = {d: (d / "client_v1.py").read_text(encoding="utf-8") for d in dirs}
    need = [d for d in dict.fromkeys(dirs) if str(d) not in _BASELINES]
    graded_items = [i for i, (_, code) in enumerate(items) if code_problem(code) is None]
    grades = pool.run([_job(d, v1_code[d], config) for d in need] + [_job(dirs[i], items[i][1], config) for i in graded_items])
    with _LOCK:
        failed: dict[str, str] = {}  # baseline grades that crashed: not cached (a crash is not "client_v1 passes nothing")
        for d, grade in zip(need, grades):
            if grade.get("error") or not grade.get("results"):
                failed[str(d)] = str(grade.get("error") or "no per-test results")
            else:
                _BASELINES[str(d)] = _passed(grade)
        by_item = dict(zip(graded_items, grades[len(need):]))
        out: list[RewardResult] = []
        for i, d in enumerate(dirs):
            code = items[i][1]
            baseline = _BASELINES.get(str(d))
            if i in by_item:
                spec = json.loads((d / "engine.json").read_text(encoding="utf-8"))
                result = _combine(spec, [by_item[i]], config)
                now = _passed(by_item[i], len(baseline or []))
            else:
                result = RewardResult(0.0, {**_EMPTY_INFO, "code_problem": code_problem(code)})
                now = [False] * len(baseline or [])
            if baseline is None:
                result.info.update(regression_rate=None, baseline_error=failed[str(d)], **_patch_info(v1_code[d], code))
                out.append(result)
                continue
            broke = sum(1 for before, after in zip(baseline, now) if before and not after)
            result.info.update(regression_rate=round(broke / len(baseline), 6) if baseline else 0.0, **_patch_info(v1_code[d], code))
            out.append(result)
    return out


def grade_repair(task_dir: str | Path, code: str, config: RewardConfig | None = None, pool: GradePool | None = None) -> RewardResult:
    return grade_repairs([(task_dir, code)], config, pool)[0]


def _curriculum_mode(mode: str | int) -> str | int:
    """`R2` and `fixed:2` like the case 1 `L2`."""
    return "L" + mode[1:] if isinstance(mode, str) and mode[:1] == "R" and mode[1:].isdigit() else mode


class RepairEnv:
    def __init__(
        self, split: str = "train", curriculum: str | int = "adaptive", seed: int = 0, *, reward_config: RewardConfig | None = None,
        cache_dir: str | Path | None = None, pool: GradePool | None = None,
    ):
        if split not in repair_splits.SPLIT_NAMES:
            raise ValueError(f"unknown split {split!r}; known: {repair_splits.SPLIT_NAMES}")
        self.split = split
        self.reward_config = reward_config or RewardConfig()
        if self.reward_config.reliability_k != 1:
            raise ValueError("RepairEnv grades one fresh instance per submit (reliability_k must be 1)")
        self.cache_dir, self.pool = cache_dir, pool
        self.curriculum = Curriculum(_curriculum_mode(curriculum), levels=repair_splits.available_levels(split))
        self._rng = random.Random(f"repair-env:{seed}")
        self._task_dir: Path | None = None
        self._spec: dict | None = None
        self._done = True

    def reset(self, spec: dict | None = None) -> dict:
        """Start an episode: a task of the split at the level the curriculum asks for (or the given spec)."""
        if spec is None:
            spec = repair_splits.sample_spec(self.split, self._rng, level=self.curriculum.next_level(self._rng))
        self._spec = repair_tasks.normalize(spec)
        self._task_dir = repair_tasks.ensure_task(self._spec, self.cache_dir)
        self._done = False
        seen = repair_tasks.read_visible(self._task_dir)
        return {"task_id": self._task_dir.name, **seen}

    def step(self, action: dict) -> dict:
        if self._done:
            raise RuntimeError("the episode is over; call reset()")
        if action.get("type") != "submit":
            raise ValueError(f"unknown action type {action.get('type')!r}; only 'submit' exists")
        result = grade_repair(self._task_dir, action["code"], self.reward_config, self.pool)
        self._done = True
        self.curriculum.record(result.reward)
        row = self._spec["row"]
        info = {
            **result.info, "task_id": self._task_dir.name, "level": self._spec["level"], "mutations": row["mutations"],
            "error_detail": self._spec["error_detail"], "evidence": self._spec["evidence"], "identifiable": self._spec["identifiable"],
        }
        return {"reward": result.reward, "done": True, "info": info}

    def reference_code(self) -> str:
        """The v2 reference client of the current task (for tests and demos; an agent never sees it)."""
        return (self._task_dir / "ref_client.py").read_text(encoding="utf-8")

    def close(self) -> None:
        self._done = True


# ---------------------------------------------------------------- TRL


def make_repair_reward_fn(config: RewardConfig | None = None, cache_dir: str | Path | None = None, pool: GradePool | None = None):
    def repair_reward_fn(prompts=None, completions=None, task_spec=None, **kwargs) -> list[float]:
        """One reward per completion: the v2 pass rate (with the case 1 penalties) of the client in the completion
        (0 when it has no code block). Same contract as `env.trl.reward_fn`; `task_spec` is a repair spec."""
        specs = task_spec if task_spec is not None else kwargs.get("task_specs")
        if specs is None or completions is None or len(specs) != len(completions):
            raise ValueError("repair_reward_fn needs `completions` and a `task_spec` column with one spec per completion")
        rewards: list[float] = [0.0] * len(completions)
        items, slots = [], []
        for slot, (completion, spec) in enumerate(zip(completions, specs)):
            code = extract_code(completion_text(completion))
            if code is None:
                continue
            items.append((repair_tasks.ensure_task(spec, cache_dir), code))
            slots.append(slot)
        for slot, result in zip(slots, grade_repairs(items, config, pool)):
            rewards[slot] = float(result.reward)
        return rewards

    repair_reward_fn.__name__ = "har2client_repair_reward"  # TRL logs reward functions by name
    return repair_reward_fn


repair_reward_fn = make_repair_reward_fn()
