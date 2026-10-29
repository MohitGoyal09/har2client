"""TRL `GRPOTrainer` integration: a plain custom reward function (TRL is not imported and not a dependency).

    trainer = GRPOTrainer(model=..., reward_funcs=reward_fn, train_dataset=dataset, ...)

TRL calls `reward_fn(prompts=..., completions=..., <dataset columns>..., **trainer kwargs)` and wants one float per
completion. The dataset from `scripts/make_rl_dataset.py` has the columns `prompt` (chat messages) and `task_spec`,
so `task_spec` arrives as a list with one spec per completion. Completions are strings (standard format) or
lists of messages (conversational format); the last ```python block of the text is the client.
Keep `remove_unused_columns=False` in `GRPOConfig`, as TRL asks when a reward function uses extra columns.
"""

from __future__ import annotations

from pathlib import Path

from har2client.env import tasks
from har2client.env.reward import GradePool, RewardConfig, compute_rewards
from har2client.solve.beam import extract_code


def completion_text(completion: object) -> str:
    """The text of a completion: a string, or the content of the last assistant message of a message list."""
    if isinstance(completion, str):
        return completion
    if isinstance(completion, dict):
        return str(completion.get("content") or "")
    if isinstance(completion, (list, tuple)):
        for message in reversed(completion):
            if isinstance(message, dict) and message.get("role", "assistant") == "assistant":
                return str(message.get("content") or "")
        return completion_text(completion[-1]) if completion else ""
    return ""


def make_reward_fn(config: RewardConfig | None = None, cache_dir: str | Path | None = None, pool: GradePool | None = None):
    def reward_fn(prompts=None, completions=None, task_spec=None, **kwargs) -> list[float]:
        """One reward per completion: the Tier B pass rate of the client in the completion (0 when it has no code block)."""
        specs = task_spec if task_spec is not None else kwargs.get("task_specs")
        if specs is None or completions is None or len(specs) != len(completions):
            raise ValueError("reward_fn needs `completions` and a `task_spec` column with one spec per completion")
        rewards: list[float] = [0.0] * len(completions)
        items, slots = [], []
        for slot, (completion, spec) in enumerate(zip(completions, specs)):
            code = extract_code(completion_text(completion))
            if code is None:
                continue
            items.append((tasks.ensure_task(spec, cache_dir), code))
            slots.append(slot)
        for slot, result in zip(slots, compute_rewards(items, config, pool)):
            rewards[slot] = float(result.reward)
        return rewards

    reward_fn.__name__ = "har2client_reward"  # TRL logs reward functions by name
    return reward_fn


reward_fn = make_reward_fn()
