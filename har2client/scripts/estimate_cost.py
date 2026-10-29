#!/usr/bin/env python3
"""Estimate the $ cost of running N tasks on open models, before spending anything.

Prompt size is measured on real task dirs (the exact prompt `openai_compat.solve` would send, at
chars/4 like the solver; on 262 Beam logs this was within 4% of the true count, a different tokenizer
will differ a little, see --token-factor). Output size is an assumption (reasoning tokens are billed as
output): 2K / 4K / 16K tokens per solve for low / expected / high, set with --output-tokens.
Prices come from `data/baseline_models.json` (USD per 1M tokens, the `:cheapest` provider).

  uv run python scripts/estimate_cost.py --tasks runs/tierb50 --n 50
  uv run python scripts/estimate_cost.py --tasks runs/tierb50 --models openai/gpt-oss-120b --runs 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from har2client.solve import beam, openai_compat
from har2client.solve.prompt import build_system_prompt, build_user_prompt

DEFAULT_OUTPUT_TOKENS = (2_000, 4_000, 16_000)  # low, expected, high per solve
PER_MILLION = 1_000_000


@dataclass(frozen=True)
class ModelCost:
    model: str
    prompt_tokens_per_task: float
    cost_low: float
    cost_expected: float
    cost_high: float
    context_length: int
    fits_context: bool


def task_prompt_tokens(task_dir: Path, har_mode: str = "raw", token_factor: float = 1.0) -> int:
    """Estimated prompt tokens of one task (system + user prompt), the same text the solvers send."""
    goal = beam._read_visible(task_dir, "goal.md")
    extra = getattr(beam, "read_extra_files", lambda _dir: {})(task_dir)
    session = beam.find_optional_session_file(task_dir) if extra else beam.find_session_file(task_dir)
    if session is None:  # a repair task that shows only the failure
        user = build_user_prompt(goal, None, None, extra)
    else:
        text = beam._read_visible(task_dir, session)
        har_text = beam.prepare_har_text(text, har_mode) if session.endswith(".har") else text
        user = build_user_prompt(goal, har_text, session, extra) if extra else build_user_prompt(goal, har_text, session)
    return round((len(build_system_prompt()) + len(user)) / beam.CHARS_PER_TOKEN * token_factor)


def find_task_dirs(tasks_dir: Path) -> list[Path]:
    return sorted(d for d in Path(tasks_dir).iterdir() if (d / "goal.md").is_file())


def estimate(
    model: dict, prompt_tokens: list[int], n_tasks: int, runs: int, output_tokens: tuple[int, int, int]
) -> ModelCost:
    """Cost of `n_tasks` x `runs` solves at the mean prompt size; low/expected/high output assumptions."""
    mean_prompt = statistics.fmean(prompt_tokens)
    solves = n_tasks * runs

    def cost(out_tokens: int) -> float:
        return solves * (mean_prompt * model["price_in_per_mtok"] + out_tokens * model["price_out_per_mtok"]) / PER_MILLION

    low, expected, high = (cost(t) for t in output_tokens)
    fits = max(prompt_tokens) + model.get("max_output_tokens", openai_compat.DEFAULT_MAX_OUTPUT_TOKENS) <= model["context_length"]
    return ModelCost(model["id"], mean_prompt, low, expected, high, model["context_length"], fits)


def load_models(path: Path, wanted: list[str] | None) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id = {m["id"]: m for m in data["models"]}
    ids = [openai_compat.split_model_id(m)[0] for m in (wanted or data["candidates_for_leaderboard"])]
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise SystemExit(f"error: not in {path}: {', '.join(missing)}")
    return [by_id[i] for i in ids]


def render(results: list[ModelCost], n_tasks: int, runs: int, sizes: list[int]) -> str:
    lines = [
        f"{n_tasks} tasks x {runs} run(s); prompt tokens per task: min {min(sizes):,} / mean {statistics.fmean(sizes):,.0f} / max {max(sizes):,}",
        f"{'model':<34} {'prompt/task':>11} {'low $':>8} {'expected $':>10} {'high $':>8}  fits context",
    ]
    for r in results:
        lines.append(
            f"{r.model:<34} {r.prompt_tokens_per_task:>11,.0f} {r.cost_low:>8.2f} {r.cost_expected:>10.2f} "
            f"{r.cost_high:>8.2f}  {'yes' if r.fits_context else 'NO'} ({r.context_length:,})"
        )
    lines.append(f"{'TOTAL':<34} {'':>11} {sum(r.cost_low for r in results):>8.2f} "
                 f"{sum(r.cost_expected for r in results):>10.2f} {sum(r.cost_high for r in results):>8.2f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Estimate the $ cost of N tasks x models.")
    p.add_argument("--tasks", required=True, type=Path, help="directory of task dirs (goal.md + session file)")
    p.add_argument("--n", type=int, default=50, help="number of tasks to price (default 50)")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--models", nargs="+", help="model ids (default: candidates_for_leaderboard in the JSON)")
    p.add_argument("--models-file", type=Path, default=openai_compat.MODELS_PATH)
    p.add_argument("--har-mode", choices=beam.HAR_MODES, default="raw")
    p.add_argument("--output-tokens", type=int, nargs=3, metavar=("LOW", "EXPECTED", "HIGH"), default=DEFAULT_OUTPUT_TOKENS)
    p.add_argument("--token-factor", type=float, default=1.0, help="multiply the chars/4 prompt estimate (a denser tokenizer: 1.2)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    dirs = find_task_dirs(args.tasks)
    if not dirs:
        print(f"error: no task dirs (with goal.md) in {args.tasks}", file=sys.stderr)
        return 2
    sizes = [task_prompt_tokens(d, args.har_mode, args.token_factor) for d in dirs]
    models = load_models(args.models_file, args.models)
    results = [estimate(m, sizes, args.n, args.runs, tuple(args.output_tokens)) for m in models]
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        print(render(results, args.n, args.runs, sizes))
        if len(dirs) != args.n:
            print(f"(priced {args.n} tasks at the mean of the {len(dirs)} task dirs found)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
