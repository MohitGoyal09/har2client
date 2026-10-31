"""Minimal GRPO run on HAR2Client with TRL: a few steps of a small model, to show the wiring (not a result).

Usage (needs `pip install trl datasets`; run the grader in a container, it is not an OS sandbox):
  uv run python examples/grpo_train.py --case build --steps 5

The prompts are 4-8K tokens (goal plus recording), so keep `max_prompt_length` and the batch small on a laptop GPU.
"""

from __future__ import annotations

import argparse

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--case", choices=("build", "repair"), default="build")
    p.add_argument("--steps", type=int, default=5)
    p.add_argument("--n", type=int, default=16, help="prompts in the training set")
    args = p.parse_args(argv)

    from trl import GRPOConfig, GRPOTrainer

    from har2client.rl.trl_reward import build_dataset, har2client_reward

    config = GRPOConfig(
        output_dir=f"grpo-har2client-{args.case}", max_steps=args.steps, per_device_train_batch_size=2, num_generations=2,
        max_prompt_length=8192, max_completion_length=1536, logging_steps=1, report_to="none",
        remove_unused_columns=False,  # keep the `task_id` and `task_spec` columns for the reward function
    )
    trainer = GRPOTrainer(
        model=MODEL, reward_funcs=har2client_reward, args=config, train_dataset=build_dataset(args.case, "train", n=args.n),
    )
    trainer.train()


if __name__ == "__main__":  # the grader uses spawned worker processes: keep this guard
    main()
