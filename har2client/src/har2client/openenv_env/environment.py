"""HAR2Client wrapped as an OpenEnv `Environment`. The reward is the share of hidden tests passed on a fresh server.

Configure with reset kwargs or env vars: case ('build' | 'repair'), split, curriculum, probe_budget
(HAR2CLIENT_CASE, HAR2CLIENT_SPLIT, HAR2CLIENT_CURRICULUM, HAR2CLIENT_PROBE_BUDGET).
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from openenv.core.env_server.interfaces import Environment
from openenv.core.env_server.types import State

from har2client.env.env import TrafficToClientEnv
from har2client.env.repair_env import RepairEnv
from har2client.openenv_env.models import H2CAction, H2CObservation

REPAIR_FILES = ("client_v1", "failure", "changelog")


class Har2ClientEnvironment(Environment[H2CAction, H2CObservation, State]):
    SUPPORTS_CONCURRENT_SESSIONS = True

    def __init__(self) -> None:
        super().__init__()
        self._env: TrafficToClientEnv | RepairEnv | None = None
        self._config: tuple | None = None
        self._state = State(episode_id=str(uuid4()), step_count=0)
        self._case = "build"

    def _make(self, case: str, split: str, curriculum: str, probe_budget: int, seed: int) -> None:
        config = (case, split, curriculum, probe_budget, seed)
        if self._env is not None and config == self._config:
            return
        if self._env is not None:
            self._env.close()
        if case == "repair":
            self._env = RepairEnv(split=split, curriculum=curriculum, seed=seed)
        elif case == "build":
            self._env = TrafficToClientEnv(source="browser_har", split=split, curriculum=curriculum, probe_budget=probe_budget, seed=seed)
        else:
            raise ValueError(f"case must be 'build' or 'repair', got {case!r}")
        self._config, self._case = config, case

    def reset(self, seed: int | None = None, episode_id: str | None = None, **kwargs: Any) -> H2CObservation:
        case = kwargs.get("case") or os.environ.get("HAR2CLIENT_CASE", "build")
        split = kwargs.get("split") or os.environ.get("HAR2CLIENT_SPLIT", "train")
        curriculum = kwargs.get("curriculum") or os.environ.get("HAR2CLIENT_CURRICULUM", "adaptive")
        probe_budget = int(kwargs.get("probe_budget", os.environ.get("HAR2CLIENT_PROBE_BUDGET", 0)))
        self._make(case, split, curriculum, probe_budget if case == "build" else 0, seed if seed is not None else 0)
        obs = self._env.reset()
        self._state = State(episode_id=episode_id or str(uuid4()), step_count=0, task_id=obs["task_id"])
        return H2CObservation(
            task_id=obs["task_id"], case=case, goal=obs.get("goal", ""), observation=obs.get("observation") or "",
            observation_format=obs.get("observation_format") or "har",
            files={k: obs[k] for k in REPAIR_FILES if isinstance(obs.get(k), str)},
            probe_budget=int(obs.get("probe_budget") or 0), done=False, reward=None,
        )

    def step(self, action: H2CAction, timeout_s: float | None = None, **kwargs: Any) -> H2CObservation:
        if self._env is None:
            raise RuntimeError("call reset() first")
        self._state.step_count += 1
        payload = {"type": action.type}
        if action.type == "submit":
            payload["code"] = action.code or ""
        else:
            payload["request"] = action.request or {}
        out = self._env.step(payload)
        return H2CObservation(
            task_id=self._state.task_id or "", case=self._case, response=out.get("response"), info=out.get("info") or {},
            done=bool(out["done"]), reward=float(out["reward"]),
        )

    @property
    def state(self) -> State:
        return self._state

    def reference_code(self) -> str:
        """The reference client of the current task (tests and demos only; an agent never sees it)."""
        return self._env.reference_code()

    def close(self) -> None:
        if self._env is not None:
            self._env.close()
            self._env = None
