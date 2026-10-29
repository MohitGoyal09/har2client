"""Reward of a client for a task (DESIGN-RL.md, "Reward").

reward = pass_rate of the Tier B semantic grader on a fresh grading instance (`grade.grade`; the grading rules
are not changed here), then, with `gates`:
- `hardcoded_secret` or `hardcoded_host` -> x 0.5;
- any invented endpoint call -> - 0.1;
- code that does not parse, or has no top-level `class Client` -> 0.
With `milestones`, + 0.2 x (share of applicable milestones reached) x (1 - reward), so shaping never lifts an
imperfect client to 1.0. With `reliability_k > 1` the
client is graded on k fresh grading instances and the mean (or, `reliability_agg="min"`, the minimum: pass^k) is used.
The reward is deterministic for a fixed (task, code). Grading runs in a pool of worker processes.
"""

from __future__ import annotations

import ast
import atexit
import json
import multiprocessing
import os
import statistics
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from har2client.env import milestones as ms
from har2client.env.tasks import grading_variants

PENALTY_FLAGS = 0.5  # multiplier for hardcoded secrets and hosts
PENALTY_INVENTED = 0.1
MILESTONE_WEIGHT = 0.2
GRADE_TIMEOUT_S = 20.0
DEFAULT_WORKERS = 4
_LOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class RewardConfig:
    reliability_k: int = 1
    reliability_agg: str = "mean"  # "mean" or "min"
    milestones: bool = False
    gates: bool = True
    timeout_s: float = GRADE_TIMEOUT_S

    def __post_init__(self):
        if self.reliability_k < 1 or self.reliability_agg not in ("mean", "min"):
            raise ValueError("reliability_k must be >= 1 and reliability_agg 'mean' or 'min'")


@dataclass
class RewardResult:
    reward: float
    info: dict = field(default_factory=dict)


# ---------------------------------------------------------------- static check


def code_problem(code: str) -> str | None:
    """Why the code cannot be graded (None when it parses and defines a top-level `class Client`)."""
    if not isinstance(code, str):
        return f"code is not text: {type(code).__name__}"
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return f"does not parse: {type(exc).__name__}: {exc}"
    if not any(isinstance(node, ast.ClassDef) and node.name == "Client" for node in tree.body):
        return "no class Client"
    return None


# ---------------------------------------------------------------- one grade (runs in a worker process)


def _grade_job(job: dict) -> dict:
    """Grade `job["code"]` on `job["task_dir"]`; returns a small picklable summary. Never raises."""
    from har2client import grade as grading
    from har2client.tierb import grader

    started = time.perf_counter()
    log: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="h2c_rl_") as tmp:
            client = Path(tmp) / "client.py"
            client.write_text(job["code"], encoding="utf-8")
            if job["want_log"] and hasattr(grader, "TierBEngine"):
                with _LOG_LOCK, mock.patch.object(grader, "TierBEngine", _logging_engine(grader.TierBEngine, log)):
                    result = grading.grade(job["task_dir"], client, job["timeout_s"])
            else:
                result = grading.grade(job["task_dir"], client, job["timeout_s"])
    except Exception as exc:  # noqa: BLE001 - a crashing grade is a zero, not a crashed training run
        return {"error": f"{type(exc).__name__}: {exc}", "pass_rate": 0.0, "failure_counts": {"grader_error": 1}, "invented_endpoint_calls": 0,
                "hardcoded_secret": False, "hardcoded_host": False, "results": [], "log": [], "grade_time_s": time.perf_counter() - started}
    return {
        "pass_rate": result["pass_rate"], "failure_counts": result["failure_counts"],
        "invented_endpoint_calls": result["invented_endpoint_calls"], "hardcoded_secret": result["hardcoded_secret"],
        "hardcoded_host": result["hardcoded_host"], "results": result["results"], "log": log, "grade_time_s": time.perf_counter() - started,
    }


def _logging_engine(base: type, sink: list[dict]) -> type:
    """The grader's engine class, but it copies its request log into `sink` when it stops."""

    class LoggingEngine(base):
        def stop(self):
            sink.extend(self.log)
            return super().stop()

    return LoggingEngine


# ---------------------------------------------------------------- the pool


class GradePool:
    """Grades jobs in worker processes (`workers=0`: in this process, one after the other). Keeps the wall time of every job."""

    def __init__(self, workers: int | None = None):
        self.workers = min(DEFAULT_WORKERS, os.cpu_count() or 1) if workers is None else workers
        self.durations: list[float] = []
        self._executor: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()

    def _pool(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(self.workers, mp_context=multiprocessing.get_context("spawn"))
            return self._executor

    def run(self, jobs: list[dict]) -> list[dict]:
        if self.workers == 0:
            out = [_grade_job(job) for job in jobs]
        else:
            try:
                out = list(self._pool().map(_grade_job, jobs))
            except BrokenProcessPool:  # a worker died: start a new pool once
                self.close()
                out = list(self._pool().map(_grade_job, jobs))
        self.durations.extend(r["grade_time_s"] for r in out)
        return out

    def median_grade_time(self) -> float | None:
        return statistics.median(self.durations) if self.durations else None

    def close(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None


_DEFAULT_POOL: GradePool | None = None


def default_pool() -> GradePool:
    global _DEFAULT_POOL
    if _DEFAULT_POOL is None:
        _DEFAULT_POOL = GradePool()
        atexit.register(_DEFAULT_POOL.close)
    return _DEFAULT_POOL


# ---------------------------------------------------------------- reward


def _combine(spec: dict, grades: list[dict], config: RewardConfig) -> RewardResult:
    rates = [g["pass_rate"] for g in grades]
    pass_rate = min(rates) if config.reliability_agg == "min" else sum(rates) / len(rates)
    flags = [name for name in ("hardcoded_secret", "hardcoded_host") if any(g[name] for g in grades)]
    invented = max(g["invented_endpoint_calls"] for g in grades)
    reward = pass_rate
    if config.gates:
        if flags:
            reward *= PENALTY_FLAGS
        if invented:
            reward -= PENALTY_INVENTED
    reached = None
    if config.milestones:
        reached = ms.reached(spec, grades[0]["results"], grades[0]["log"])
        if reached:
            reward += MILESTONE_WEIGHT * sum(reached.values()) / len(reached) * max(0.0, 1.0 - reward)
    failures: Counter = Counter()
    for g in grades:
        failures.update(g["failure_counts"])
    info = {
        "pass_rate": round(pass_rate, 6), "min_pass_rate_k": round(min(rates), 6), "failure_counts": dict(failures), "flags": flags,
        "invented_endpoint_calls": invented, "milestones": reached, "reliability_k": len(grades),
        "grade_time_s": round(statistics.median(g["grade_time_s"] for g in grades), 3),
    }
    errors = [g["error"] for g in grades if g.get("error")]
    if errors:
        info["grader_error"] = errors[0]
    return RewardResult(round(reward, 6), info)


def compute_rewards(items: list[tuple[str | Path, str]], config: RewardConfig | None = None, pool: GradePool | None = None) -> list[RewardResult]:
    """Rewards of many (task_dir, code) pairs; all grades of all pairs run in the pool at once."""
    config, pool = config or RewardConfig(), pool or default_pool()
    results: list[RewardResult | None] = [None] * len(items)
    jobs, owners = [], []
    for index, (task_dir, code) in enumerate(items):
        problem = code_problem(code)
        if problem:
            results[index] = RewardResult(0.0, {"pass_rate": 0.0, "min_pass_rate_k": 0.0, "failure_counts": {}, "flags": [], "invented_endpoint_calls": 0,
                                                "milestones": None, "reliability_k": 0, "grade_time_s": 0.0, "code_problem": problem})
            continue
        for variant in grading_variants(task_dir, config.reliability_k):
            jobs.append({"task_dir": str(variant), "code": code, "timeout_s": config.timeout_s, "want_log": config.milestones})
            owners.append(index)
    graded = pool.run(jobs)
    for index, (task_dir, _) in enumerate(items):
        if results[index] is None:
            mine = [g for g, owner in zip(graded, owners) if owner == index]
            spec = json.loads((Path(task_dir) / "engine.json").read_text(encoding="utf-8"))
            results[index] = _combine(spec, mine, config)
    return results  # type: ignore[return-value]


def compute_reward(task_dir: str | Path, code: str, config: RewardConfig | None = None, pool: GradePool | None = None) -> RewardResult:
    return compute_rewards([(task_dir, code)], config, pool)[0]
