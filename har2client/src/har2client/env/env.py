"""`TrafficToClientEnv`: observed traffic -> working API client, one episode per task (DESIGN-RL.md, "Environment API").

    env = TrafficToClientEnv(source="browser_har", split="train", curriculum="adaptive", seed=0)
    obs = env.reset()
    out = env.step({"type": "submit", "code": client_py_text})   # {"reward", "done": True, "info": {...}}

An episode is: observe, then one submit. With `probe_budget > 0` the agent may first send up to that many probes
to a practice instance of the API (`env/probe.py`: same spec, own data seed and own credentials, never the grading
instance); `reset` returns the practice base URL and credentials in `obs["practice"]`:

    out = env.step({"type": "probe", "request": {"method": "GET", "path": "/items", "headers": {...}}})
    # -> {"reward": -probe_cost, "done": False, "response": {status, headers, body}, "info": {...}}

A probe step costs `probe_cost` (default 0.01) of reward; a refused probe (budget spent) or an invalid one costs
nothing. The submit step's reward is the task reward alone, so the episode return is that reward minus the probes.
"""

from __future__ import annotations

import random
from pathlib import Path

from har2client.env import splits, tasks
from har2client.env.probe import PracticeInstance
from har2client.env.curriculum import Curriculum
from har2client.env.reward import GradePool, RewardConfig, compute_reward
from har2client.env.sources import SOURCES
from har2client.solve.agent_prompt import practice_section

AUTO = "auto"  # use the sources of the split
PROBE_COST = 0.01  # reward cost of one sent probe


class TrafficToClientEnv:
    def __init__(
        self, source: str = "browser_har", tier: str = "B", split: str = "train", curriculum: str | int = "adaptive",
        probe_budget: int = 0, seed: int = 0, *, compact: bool = False, reward_config: RewardConfig | None = None,
        cache_dir: str | Path | None = None, pool: GradePool | None = None, probe_cost: float = PROBE_COST,
    ):
        if tier != "B":
            raise ValueError("only tier 'B' is available")
        if probe_budget < 0:
            raise ValueError("probe_budget must be >= 0")
        if split not in splits.SPLIT_NAMES:
            raise ValueError(f"unknown split {split!r}; known: {splits.SPLIT_NAMES}")
        if source != AUTO and source not in SOURCES:
            raise ValueError(f"unknown source {source!r}; known: {sorted(SOURCES)} or {AUTO!r}")
        self.source, self.tier, self.split, self.probe_budget = None if source == AUTO else source, tier, split, probe_budget
        self.probe_cost = probe_cost
        self._practice: PracticeInstance | None = None
        self.compact = compact
        self.reward_config = reward_config or RewardConfig()
        self.cache_dir, self.pool = cache_dir, pool
        self.curriculum = Curriculum(curriculum, levels=splits.available_levels(split))
        self._rng = random.Random(f"env:{seed}")
        self._task_dir: Path | None = None
        self._spec: dict | None = None
        self._done = True
        self._sampled = False  # the current episode was drawn by the curriculum

    def reset(self, spec: dict | None = None) -> dict:
        """Start an episode: a task of the split at the level the curriculum asks for (or the given spec).
        Only sampled episodes feed the adaptive curriculum; an explicit spec (an eval task) does not."""
        self._sampled = spec is None
        if spec is None:
            level = self.curriculum.next_level(self._rng)
            spec = splits.sample_spec(self.split, self._rng, level=level, source=self.source)
            spec = {**spec, "compact": self.compact}
        self._stop_practice()
        self._spec = tasks.normalize(spec)
        self._task_dir = tasks.ensure_task(self._spec, self.cache_dir)
        self._done = False
        seen = tasks.read_visible(self._task_dir)
        obs = {
            "task_id": self._task_dir.name, "goal": seen["goal"], "observation": seen["observation"],
            "observation_format": Path(seen["observation_file"]).suffix.lstrip("."), "probe_budget": self.probe_budget,
            "source": seen["source"],
        }
        if self.probe_budget:
            self._practice = PracticeInstance(self._task_dir, self.probe_budget)
            self._practice.start()
            creds = self._practice.credentials
            obs["practice"] = {"base_url": self._practice.base_url, "budget": self.probe_budget, **creds}
            obs["goal"] = f"{seen['goal'].rstrip()}\n\n{practice_section(self._practice.base_url, creds['username'], creds['password'], self.probe_budget)}"
        return obs

    def _stop_practice(self) -> None:
        if self._practice is not None:
            self._practice.stop()
            self._practice = None

    def step(self, action: dict) -> dict:
        if self._done:
            raise RuntimeError("the episode is over; call reset()")
        kind = action.get("type")
        if kind == "probe":
            return self._probe(action.get("request"))
        if kind != "submit":
            raise ValueError(f"unknown action type {kind!r}")
        has_code = "code" in action
        try:
            result = compute_reward(self._task_dir, action.get("code"), self.reward_config, self.pool)
            self._done = True
            if self._sampled:
                self.curriculum.record(result.reward)
            info = {**result.info, "task_id": self._task_dir.name, "level": self._spec["level"], "source": self._spec["source"]}
            if not has_code:
                info["code_problem"] = "the submit action has no 'code' key"
            if self._practice is not None:
                info.update(self._probe_info())
        finally:  # also when grading raises: the loopback server must not stay up
            self._stop_practice()
        return {"reward": result.reward, "done": True, "info": info}

    def _probe(self, request: dict | None) -> dict:
        if self._practice is None:
            return {"reward": 0.0, "done": False, "response": {"error": "no_practice_server", "message": "probe_budget is 0"}, "info": {}}
        before = self._practice.probes_used
        response = self._practice.request(request)
        sent = self._practice.probes_used > before
        info = {**self._probe_info(), "label": self._practice.log[-1]["label"]}
        return {"reward": -self.probe_cost if sent else 0.0, "done": False, "response": response, "info": info}

    def _probe_info(self) -> dict:
        p = self._practice
        return {"probes_used": p.probes_used, "probes_left": p.probes_left, "probes_refused": p.probes_refused, "probe_labels": p.label_counts()}

    def reference_code(self) -> str:
        """The reference client of the current task (for tests and demos; an agent never sees it)."""
        return (self._task_dir / "ref_client.py").read_text(encoding="utf-8")

    def close(self) -> None:
        self._stop_practice()
        self._done = True
