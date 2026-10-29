#!/usr/bin/env python3
"""Run other models (Hugging Face router) on the benchmark with one command, then compare them with Beam.

Models come from data/models.json (`enabled: true` runs by default). Task sets ("suites"):
  tierb50 = runs/tierb50 (case 1, 50 tasks), v3base = runs/heal_v3_eval (case 3, 64 tasks),
  v3hint = runs/heal_v3_eval_hint (case 3 with the contract hint, 64 tasks; optional).
Each (model, suite) is one `run_batch.py --solver openai_compat ... --runs 1` call. Output goes to
`runs/batch/models/<slug>/<suite>/` (`<task>/<effort>-raw-r1/{client.py,solve_log.json,grade.json}`, `results.csv`).
Resumable: a recorded cell is skipped, an `api_error` cell is retried (up to --retries more passes), and a finished
suite gets a `.done` marker (delete it to run the suite again). The key is HF_TOKEN, read from the environment or
har2client/.env; it is never printed. A model with `reasoning_effort: null` gets no `reasoning_effort` parameter.

Usage:
  # plan only: cells, output-token and cost estimate per model; calls nothing and needs no token
  uv run python scripts/run_models.py --dry-run
  # smoke test: 1 task, 1 model, 1 suite (a few thousand output tokens, a fraction of a cent for gpt-oss)
  uv run python scripts/run_models.py --models gpt-oss-120b --suites tierb50 --limit 1
  # the full run: every enabled model on tierb50 and v3base (add v3hint to --suites for the hint arm)
  uv run python scripts/run_models.py
  # compare with Beam: report, summary and two figures
  uv run --extra plots python scripts/analyze_models.py

Cost: the dry run estimates it from Beam's measured mean tokens per task (case 1 high ~4.9K output, case 3 high ~7.6K) and the
router prices in data/models.json. For gpt-oss-120b that is under 1 USD for the full run; the larger models cost a few USD.
Real output tokens per task are unknown for the other models until the first smoke run.
Exit code: 0 done, 1 some cells still not recorded (api errors: run the same command again), 2 bad setup or missing HF_TOKEN,
3 stopped because credits are spent (HTTP 402).
"""

from __future__ import annotations

import argparse
import csv
import functools
import json
import os
import re
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_models as am  # noqa: E402
import run_batch as rb  # noqa: E402
from har2client.solve import openai_compat, beam_agent  # noqa: E402
from har2client.solve.cost_tracking import CostLimitReached, CostTracker  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODELS_FILE = ROOT / "data" / "models.json"
OUT_ROOT = ROOT / "runs" / "batch" / "models"
ENV_PATH = ROOT / ".env"
SUITE_DIRS = {"tierb50": "runs/tierb50", "v3base": "runs/heal_v3_eval", "v3hint": "runs/heal_v3_eval_hint",
              "probe0": "runs/tierb50", "probe5": "runs/tierb50", "probe20": "runs/tierb50"}
PROBE_BUDGETS = {'probe0': 0, 'probe5': 5, 'probe20': 20}
DEFAULT_SUITES = ("tierb50", "v3base")
EXIT_OK, EXIT_INCOMPLETE, EXIT_SETUP, EXIT_STOPPED = 0, 1, 2, 3
DONE_MARKER = ".done"
HAR_MODE = "raw"
EFFORT_IF_NONE = "high"  # cell label for a model that takes no reasoning_effort (the parameter is not sent)
ESTIMATE_EFFORT_IF_NONE = "medium"  # Beam effort whose token means stand in for such a model
FALLBACK_TOKENS = (30_000, 4_000)  # (prompt, output) per task when no Beam CSV is there
DEFAULT_RETRIES = 2
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MODEL_EFFORTS = ("low", "medium", "high")


class ConfigError(ValueError):
    """data/models.json is wrong."""


@dataclass(frozen=True)
class Model:
    id: str
    slug: str
    label: str
    reasoning_effort: str | None
    enabled: bool
    notes: str
    price_in_per_mtok: float | None
    price_out_per_mtok: float | None
    pricing_source: str | None = None
    pricing_as_of: str | None = None
    context_length: int | None = None
    enable_thinking: bool | None = None

    @property
    def cell_effort(self) -> str:
        return self.reasoning_effort or EFFORT_IF_NONE


@dataclass(frozen=True)
class Job:
    model: Model
    suite: str
    tasks_dir: Path
    out_dir: Path
    n_tasks: int
    n_todo: int
    est_prompt_tokens: float
    est_output_tokens: float


# ---- config and plan -------------------------------------------------------


def load_models(path: Path) -> list[Model]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError) as exc:
        raise ConfigError(f"cannot read models from {path}: {exc}") from exc
    models, seen = [], set()
    for entry in raw:
        missing = [k for k in ("id", "slug", "label", "reasoning_effort", "enabled") if k not in entry]
        if missing:
            raise ConfigError(f"model entry {entry.get('id', '?')} lacks {', '.join(missing)}")
        slug, effort = entry["slug"], entry["reasoning_effort"]
        if not SLUG_RE.match(slug):
            raise ConfigError(f"bad slug {slug!r}: use lower-case letters, digits, '.', '_' and '-'")
        if slug in seen:
            raise ConfigError(f"duplicate slug {slug!r}")
        if effort is not None and effort not in MODEL_EFFORTS:
            raise ConfigError(f"{slug}: reasoning_effort must be one of {MODEL_EFFORTS} or null, got {effort!r}")
        seen.add(slug)
        if entry.get('enable_thinking') is not None and not isinstance(entry['enable_thinking'], bool):
            raise ConfigError(f'{slug}: enable_thinking must be a boolean or null')
        models.append(Model(entry["id"], slug, entry["label"], effort, bool(entry["enabled"]), entry.get("notes", ""),
                            entry.get("price_in_per_mtok"), entry.get("price_out_per_mtok"),
                            entry.get("pricing_source"), entry.get("pricing_as_of"), entry.get('context_length'),
                            entry.get('enable_thinking')))
    return models


def parse_suite_paths(items: list[str]) -> dict[str, Path]:
    paths = {name: ROOT / rel for name, rel in SUITE_DIRS.items()}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or name not in SUITE_DIRS or not value:
            raise ConfigError(f"--suite-path must look like NAME=DIR with NAME in {sorted(SUITE_DIRS)}, got {item!r}")
        paths[name] = Path(value)
    return paths


def estimate_tokens(suite: str, effort: str, results_dir: Path) -> tuple[float, float]:
    """(prompt, output) tokens per task: Beam's measured means at `effort` for the suite, else FALLBACK_TOKENS."""
    path = am.beam_sources(results_dir).get(('tierb50' if suite in PROBE_BUDGETS else suite, effort))
    prompt, output = [], []
    if path:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("status") == am.API_ERROR or row.get("effort", effort) != effort:
                    continue
                p, c = am._num(row.get("prompt_tokens")), am._num(row.get("completion_tokens"))
                if p is not None and c is not None:
                    prompt.append(p)
                    output.append(c)
    estimate = (sum(prompt) / len(prompt), sum(output) / len(output)) if prompt else FALLBACK_TOKENS
    # No measured other-model probe usage yet: explicitly estimate a conservative
    # one-request-per-turn upper scenario rather than presenting one-call cost.
    multiplier = PROBE_BUDGETS[suite] + 1 if suite in PROBE_BUDGETS else 1
    return estimate[0] * multiplier, estimate[1] * multiplier


def plan_jobs(models: list[Model], suites: list[str], suite_dirs: dict[str, Path], out_root: Path, limit: int | None,
              results_dir: Path = am.DEFAULT_RESULTS) -> list[Job]:
    jobs = []
    for model in models:
        for suite in suites:
            tasks = rb.find_tasks(suite_dirs[suite])[:limit]
            out_dir = Path(out_root) / model.slug / suite
            cells = rb.plan_cells(tasks, [model.cell_effort], [HAR_MODE], 1, out_dir)
            todo = sum(not rb.is_recorded(c) for c in cells)
            p, o = estimate_tokens(suite, model.reasoning_effort or ESTIMATE_EFFORT_IF_NONE, results_dir)
            jobs.append(Job(model, suite, suite_dirs[suite], out_dir, len(tasks), todo, p * todo, o * todo))
    return jobs


def job_cost(job: Job) -> float | None:
    m = job.model
    if m.price_in_per_mtok is None or m.price_out_per_mtok is None:
        return None
    return (job.est_prompt_tokens * m.price_in_per_mtok + job.est_output_tokens * m.price_out_per_mtok) / 1e6


def render_plan(jobs: list[Job], skipped: list[Model], limit: int | None) -> str:
    cells = sum(j.n_tasks for j in jobs)
    todo = sum(j.n_todo for j in jobs)
    lines = [f"plan: {len({j.model.slug for j in jobs})} model(s) x {len({j.suite for j in jobs})} suite(s) = {len(jobs)} job(s), "
             f"{cells} cell(s), {todo} to run" + (f", first {limit} task(s) per suite" if limit else "")]
    for slug in dict.fromkeys(j.model.slug for j in jobs):
        mine = [j for j in jobs if j.model.slug == slug]
        m = mine[0].model
        effort = m.reasoning_effort or "not sent"
        lines.append(f"model {m.slug}  id={m.id}  reasoning_effort={effort}")
        for j in mine:
            cost = job_cost(j)
            lines.append(f"  {j.suite:<8} {j.n_tasks:>3} tasks, {j.n_todo:>3} to run  -> {j.out_dir}  "
                         f"est output ~{j.est_output_tokens / 1000:,.0f}K tok" + (f", cost ~${cost:.2f}" if cost is not None else ""))
    total_out = sum(j.est_output_tokens for j in jobs)
    costs = [job_cost(j) for j in jobs]
    lines.append(f"total est output ~{total_out / 1000:,.0f}K tok"
                 + (f", cost ~${sum(c for c in costs if c is not None):.2f}" if costs and all(c is not None for c in costs) else "")
                 + "  (Beam's mean tokens per task as the proxy; real use is unknown until a smoke run)")
    for m in skipped:
        lines.append(f"skipped (disabled): {m.slug}  {m.notes}".rstrip())
    return "\n".join(lines)


# ---- running ---------------------------------------------------------------


@contextmanager
def limited_tasks(tasks_dir: Path, limit: int | None) -> Iterator[Path]:
    """`tasks_dir` itself, or a temp dir of symlinks to its first `limit` tasks (cell paths keep the task names)."""
    if not limit:
        yield Path(tasks_dir)
        return
    with tempfile.TemporaryDirectory(prefix="run_models_") as tmp:
        for task in rb.find_tasks(tasks_dir)[:limit]:
            destination = Path(tmp) / task.name
            try:
                destination.symlink_to(task.resolve(), target_is_directory=True)
            except OSError:
                # Windows may not grant symlink privileges. Copy only the
                # selected task instead of failing before the smoke request.
                shutil.copytree(task, destination)
        yield Path(tmp)


def run_job(job: Job, solve_fn: Callable, client: Any, *, concurrency: int, retries: int, limit: int | None,
            timeout: float, log: Callable[[str], None]) -> rb.BatchResult | None:
    """One run_batch call (plus retry passes while cells are unrecorded). None when the suite is already done."""
    marker = job.out_dir / DONE_MARKER
    if marker.exists():
        log(f"[{job.model.slug}/{job.suite}] already done ({marker}); delete the marker to run again")
        return None
    prefix = f"[{job.model.slug}/{job.suite}]"
    result = None
    with limited_tasks(job.tasks_dir, limit) as tasks:
        for attempt in range(retries + 1):
            result = rb.run_batch(
                tasks, job.out_dir, efforts=[job.model.cell_effort], har_modes=[HAR_MODE], runs=1, concurrency=concurrency,
                solve_fn=solve_fn, client=client, timeout=timeout, log=lambda msg: log(f"{prefix} {msg}"),
            )
            if result.stopped or not result.remaining:
                break
            if attempt < retries:
                log(f"{prefix} retry pass {attempt + 1}/{retries}: {len(result.remaining)} cell(s) not recorded (api_error)")
    if result is not None and not result.remaining and not limit:
        marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8")
    return result


def make_solver_factory(base_url: str, key_env: str, tracker: CostTracker | None = None) -> Callable[[Model], Callable]:
    def agent_call(client, request, **kwargs):
        response, headers = openai_compat.call_with_retry(client, request, **kwargs)
        return response, openai_compat.beam.rate_limit_headers(headers)

    def factory(model: Model, suite: str | None = None) -> Callable:
        solve = functools.partial(openai_compat.solve, base_url=base_url, model=model.id, api_key_env=key_env,
                                  supports_reasoning_effort=model.reasoning_effort is not None,
                                  enable_thinking=model.enable_thinking)
        if suite in PROBE_BUDGETS:
            solve = functools.partial(beam_agent.solve_agent, budget=PROBE_BUDGETS[suite],
                                      request_options={'model': model.id, 'reasoning_effort': model.reasoning_effort,
                                                       'max_completion_tokens': None, 'max_tokens': 32768},
                                      call_fn=agent_call, submission_tool_all_arms=True)
            if model.enable_thinking is not None:
                solve.keywords['request_options']['extra_body'] = {'enable_thinking': model.enable_thinking}
        if tracker is None:
            return solve

        def metered(task_dir, out_path, effort="high", har_mode="raw", **kwargs):
            with tracker.context(task_id=Path(task_dir).name, model_slug=model.slug,
                                 suite=Path(out_path).parents[2].name):
                try:
                    return solve(task_dir, out_path, effort, har_mode, **kwargs)
                except CostLimitReached as exc:
                    raise openai_compat.DailyAllowanceExhausted(str(exc)) from exc
        return metered
    return factory


# ---- CLI -------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--models", nargs="+", metavar="SLUG", help="slugs to run (default: every enabled model in the models file)")
    p.add_argument("--suites", nargs="+", default=list(DEFAULT_SUITES), choices=list(SUITE_DIRS))
    p.add_argument("--suite-path", action="append", default=[], metavar="NAME=DIR", help="use DIR as the task set of suite NAME")
    p.add_argument("--models-file", type=Path, default=MODELS_FILE)
    p.add_argument("--out-root", type=Path, default=OUT_ROOT)
    p.add_argument("--limit", type=int, default=None, help="run only the first N tasks per suite (smoke test)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="extra passes over api_error cells per suite")
    p.add_argument("--timeout", type=float, default=rb.runner.DEFAULT_TIMEOUT_S, help="grading time limit per task, seconds")
    p.add_argument("--dry-run", action="store_true", help="print the plan and the estimates; call nothing")
    p.add_argument("--base-url", default=openai_compat.DEFAULT_BASE_URL)
    p.add_argument("--api-key-env", default=openai_compat.DEFAULT_API_KEY_ENV)
    p.add_argument("--env-path", type=Path, default=ENV_PATH, help=".env file that may hold the key")
    p.add_argument("--cost-ledger", type=Path, help="append-only request ledger (default: OUT_ROOT/cost_ledger.jsonl)")
    p.add_argument("--cost-limit-usd", type=float, help="reserve provider/context/output tariff bounds before calls; reconcile actual HF billing")
    return p


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return EXIT_SETUP


def main(argv: list[str] | None = None, *, solve_factory: Callable[[Model], Callable] | None = None, client: Any = None) -> int:
    args = _parser().parse_args(argv)
    if args.concurrency < 1 or args.retries < 0 or (args.limit is not None and args.limit < 1):
        return _fail("--concurrency and --limit must be at least 1, --retries at least 0")
    if args.cost_limit_usd is not None and (not 0 < args.cost_limit_usd < float('inf')):
        return _fail("--cost-limit-usd must be positive and finite")
    try:
        models = load_models(args.models_file)
        suite_dirs = parse_suite_paths(args.suite_path)
    except ConfigError as exc:
        return _fail(str(exc))
    by_slug = {m.slug: m for m in models}
    if args.models:
        if unknown := [s for s in args.models if s not in by_slug]:
            return _fail(f"unknown model slug(s) {', '.join(unknown)}; known: {', '.join(by_slug)}")
        chosen, skipped = [by_slug[s] for s in dict.fromkeys(args.models)], []
    else:
        chosen = [m for m in models if m.enabled]
        skipped = [m for m in models if not m.enabled]
    if not chosen:
        return _fail("no model selected (every model in the file is disabled)")
    for suite in args.suites:
        if not suite_dirs[suite].is_dir() or not rb.find_tasks(suite_dirs[suite]):
            return _fail(f"suite {suite}: no task dirs (with task.json) in {suite_dirs[suite]}")

    jobs = plan_jobs(chosen, args.suites, suite_dirs, args.out_root, args.limit)
    if args.dry_run:
        print(render_plan(jobs, skipped, args.limit))
        return EXIT_OK

    tracker = None
    real_calls = solve_factory is None
    if real_calls:  # real calls: the key must be there before any work
        rb.load_env_key(args.env_path, os.environ, args.api_key_env)
        if not os.environ.get(args.api_key_env):
            return _fail(f"{args.api_key_env} is not set. Add `{args.api_key_env}=<your Hugging Face token>` to {args.env_path} "
                         "or export it (token needs the \"Make calls to Inference Providers\" permission).")
        prices = {m.id: dict(input=m.price_in_per_mtok, output=m.price_out_per_mtok,
                            source=m.pricing_source, as_of=m.pricing_as_of,
                            provider=openai_compat.split_model_id(m.id)[1], context_length=m.context_length) for m in chosen}
        tracker = CostTracker(args.cost_ledger or args.out_root / 'cost_ledger.jsonl', prices,
                              limit_usd=args.cost_limit_usd)
        tracker.path.with_name('pricing_snapshot.json').write_text(json.dumps(prices, indent=2) + '\n', encoding='utf-8')
        solve_factory = make_solver_factory(args.base_url, args.api_key_env, tracker)
        client = tracker.wrap(openai_compat.make_client(args.base_url, args.api_key_env))

    print(render_plan(jobs, skipped, args.limit), flush=True)
    incomplete = 0
    for job in jobs:
        try:
            solve_fn = solve_factory(job.model, job.suite) if real_calls else solve_factory(job.model)
            result = run_job(job, solve_fn, client, concurrency=args.concurrency, retries=args.retries,
                             limit=args.limit, timeout=args.timeout, log=lambda msg: print(msg, flush=True))
        finally:
            if tracker is not None:
                spending = tracker.write_summary()
                print(f"Cost tracking: estimated ${spending['estimated_cost_usd']:.6f}; "
                      f"unknown-cost requests={spending['requests_with_unknown_cost']}; "
                      "final billed charges require HF reconciliation.", flush=True)
        if result is None:
            continue
        print(f"\n[{job.model.slug}/{job.suite}] {rb.summarize(result.rows)}\nresults: {job.out_dir / 'results.csv'}\n", flush=True)
        if result.stopped:
            print(f"stopped: credits or daily allowance spent at {job.model.slug}/{job.suite}. Run the same command to resume.")
            return EXIT_STOPPED
        incomplete += len(result.remaining)
    if incomplete:
        print(f"{incomplete} cell(s) not recorded (api errors). Run the same command to retry them.")
        return EXIT_INCOMPLETE
    print("done. Compare with Beam: uv run --extra plots python scripts/analyze_models.py")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
