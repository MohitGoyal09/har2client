# har2client: developer guide

This folder holds all the code of HAR2Client, a benchmark and an RL environment for "recorded HTTP traffic -> working API client". For the project front page and the headline results, read [../README.md](../README.md). For the design, read the files in [../docs/design/](../docs/design/) (section 16).

All commands run from this folder (`har2client/`) unless the text says otherwise.

## Contents

1. [Install](#1-install)
2. [Environment variables](#2-environment-variables)
3. [Words you need](#3-words-you-need)
4. [Quickstart](#4-quickstart)
5. [The command-line tool](#5-the-command-line-tool)
6. [Scripts](#6-scripts)
7. [Task directory layout](#7-task-directory-layout)
8. [Grading and metrics](#8-grading-and-metrics)
9. [RL environments](#9-rl-environments)
10. [Beam API notes](#10-beam-api-notes)
11. [Run models on many tasks](#11-run-models-on-many-tasks)
    - [11a. Case 3 v3: the hard repair suite](#11a-case-3-v3-the-hard-repair-suite)
12. [Run the tests](#12-run-the-tests)
    - [12a. Security: model code runs on your machine](#12a-security-model-code-runs-on-your-machine)
13. [Add a mutation or a switch](#13-add-a-mutation-or-a-switch)
14. [Troubleshooting](#14-troubleshooting)
15. [Source map](#15-source-map)
16. [Further reading](#16-further-reading)

## 1. Install

You need Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/).

```bash
cd har2client
uv sync                    # package + pytest
uv sync --extra plots      # also matplotlib, for the chart scripts
uv run har2client --help
```

`uv sync` creates `.venv/` and installs the console command `har2client`. The `plots` extra (matplotlib) is needed only by `plot_frontier.py`, `plot_story.py`, `plot_heal_families.py` and the analysis scripts that draw a chart. Run those with `uv run --extra plots python scripts/<name>.py`.

## 2. Environment variables

| Variable | Needed for | Where to put it |
|---|---|---|
| `REFLECTION_API_KEY` | Every Beam call (`har2client solve`, `run_batch.py`, `run_probe_batch.py`, `build_variants.py` when it needs new rewordings) | `har2client/.env` or your shell |
| `HF_TOKEN` (optional) | Open models through the Hugging Face router (`run_models.py`, `run_batch.py --solver openai_compat`) | `har2client/.env` or your shell |
| `HAR2CLIENT_RL_CACHE` (optional) | Where the RL environments cache built tasks. Default: a folder in the system temp directory | your shell |

Create `har2client/.env` like this (fake values):

```bash
REFLECTION_API_KEY=your-reflection-key-goes-here
HF_TOKEN=your-hugging-face-token-goes-here
```

- `.env` is git-ignored. Never commit it. Never paste a key into a file, a log or a chat.
- `run_batch.py` and `run_models.py` read `.env` by themselves. `uv run har2client solve` does **not**. For `solve`, load the file into your shell first: `set -a; source .env; set +a`.

## 3. Words you need

| Word | Meaning |
|---|---|
| HAR | HTTP Archive. A JSON file that records HTTP requests and responses. |
| Task | One exam: visible files for the model, hidden files for the grader. |
| Tier A | Real public APIs (165 selectable). A fake server is built from the OpenAPI spec. |
| Tier B | Invented stateful APIs. One engine, six switches. |
| Switch | A feature of a Tier B API: `auth` (apikey_header, bearer_login, cookie_csrf), `pagination` (none, offset, cursor, link_header), `token_expiry`, `etag_update`, `hidden_header`, `envelope`. |
| Mutation | Case 3 v2. A change from API v1 to API v2, for example `add_hidden_header`. v3 uses "changes" (section 11a). |
| Mock / engine | The fake server. Tier A uses the mock. Tier B and case 3 use the engine. |
| Reference client | The correct client that we build from the hidden spec. It must score 1.0. |
| Effort | Beam's `reasoning_effort`. This project reports three levels: low, medium and high (xhigh dropped 2026-10-27; see `PREREG.md`, deviations). |

## 4. Quickstart

Everything goes into `runs/quickstart/` (git-ignored). The build and grade steps use no model.

### 4.1 Tier A

`data/specs/` is git-ignored, so a fresh clone has no specs. Fetch one (needs the network):

```bash
uv run python scripts/fetch_spec.py up.com.au
uv run har2client build data/specs/up.com.au.json --api-id up.com.au --out runs/quickstart/tierA --noise 0.7 --seed 1
uv run har2client ref runs/quickstart/tierA/up.com.au
uv run har2client grade runs/quickstart/tierA/up.com.au runs/quickstart/tierA/up.com.au/ref_client.py   # pass_rate=1.000
```

`grade` writes `grade.json` next to the client file. `har2client build` also accepts an `https://` spec URL.

### 4.2 Tier B

`build_tierb.py` writes a config list. Its default path is a committed file, so pass `--configs`.

```bash
uv run python scripts/build_tierb.py --out runs/quickstart/tierB --seed 1 --limit 1 --configs runs/quickstart/tierb_configs.jsonl
T=runs/quickstart/tierB/tierb_00_invoices
uv run har2client grade $T $T/ref_client.py      # pass_rate=1.000
```

### 4.3 Case 3 (v3)

The build writes a manifest to a committed file by default, so pass `--manifest`.

```bash
uv run python scripts/build_heal_v3.py --suite eval --out runs/quickstart/v3 --seed 2 --families single --limit 1 --manifest runs/quickstart/v3_manifest.jsonl
H=runs/quickstart/v3/v3_single_00_cents_tickets
uv run har2client grade $H $H/client_v1.py       # old client on the new API: below 1.0
uv run har2client grade $H $H/ref_client.py      # reference repair: pass_rate=1.000
```

### 4.4 Run a solver on a task (calls a model)

```bash
set -a; source .env; set +a
uv run har2client solve $T --effort high --out runs/quickstart/solve/client.py
uv run har2client grade $T runs/quickstart/solve/client.py
```

- `solve` writes `client.py` and `solve_log.json` (usage, rate-limit headers, reasoning text) next to `--out`.
- The solver reads only the visible files: `goal.md`, the `session.*` file and, for a repair task, `client_v1.py`, `failure.txt` and `CHANGELOG.md`. It never reads a hidden file.

To test the batch runner with no model and no key, point `run_batch.py --solver openai_compat --base-url http://127.0.0.1:8099/v1` at `scripts/fake_openai_server.py --tasks <dir> --port 8099`. The fake server answers with the reference client.

## 5. The command-line tool

`uv run har2client <command>`. Source: `src/har2client/cli.py`.

| Command | What it does | Flags |
|---|---|---|
| `build SPEC --api-id ID` | Build a **Tier A** task from an OpenAPI 3.0 spec (path or URL). | `--out` (default `tasks`), `--noise` (0.7), `--seed` (0), `--names {exact,reworded,reworded_desc,opaque}`, `--twin`, `--closed-book`, `--rewordings-dir` |
| `ref TASK_DIR` | Write `ref_client.py` into the task directory. | none |
| `grade TASK_DIR CLIENT` | Grade a client. Works for every task kind. Writes `grade.json` next to the client. | `--timeout` (60 s) |
| `solve TASK_DIR --out FILE` | Run the Beam baseline once. | `--effort` (default `high`; always set it on purpose) |

Exit codes of `build`: 0 done, 1 error, 2 API skipped (the spec is not usable, for example OpenAPI 3.1 or too few operations).

Name and memory controls on `build`: `--names reworded` or `opaque` change the goal argument names (tests string matching). `--twin` renames host, title, paths and fields (tests memorisation). `--closed-book` writes an empty `session.har` (the model answers from memory).

## 6. Scripts

All scripts are in `scripts/`. Run them as `uv run python scripts/<name>.py`; use `--help` for every flag. Scripts marked **historical** still run but are not part of the current evaluation.

### Build tasks

| Script | Purpose |
|---|---|
| `select_specs.py` | Pick usable OpenAPI 3.0 specs from APIs.guru for Tier A. Writes `data/selected_specs.jsonl`. Needs the network. |
| `fetch_spec.py` | Download only the named specs into `data/specs/` (`fetch_spec.py up.com.au`). Checks the sha256 when the row has one. |
| `build_tierb.py` | Build the Tier B set (50 tasks by default). Checks the reference client first. |
| `build_variants.py` | Build name and memory variants (exact, reworded, opaque, twin, closed-book) of a Tier A set. Calls Beam only for rewordings that are not cached in `data/rewordings/`. |
| `build_heal_v3.py` | Build the case 3 v3 sets: `--suite eval` (64 tasks, seeds 2001+) or `--suite pilot` (38 tasks). Runs every validity gate. See 11a. |
| `add_contract_hint.py` | Make the hint arm of a v3 set (one added paragraph in `goal.md`). `--variant notice` and `--variant placebo` make the two control arms. |
| `make_header_controls.py` | Build the H10 header-ablation copies of the Tier B tasks that have the hidden header on (`__hint`, `__evidence`). |
| `build_heal.py` | **Historical (v2, saturated).** Build the 144-task v2 repair suite (`--suite`) or single tasks. |

`build_tierb.py` and `build_heal.py` write their row list to a committed file in `data/` by default; `build_heal_v3.py` writes a manifest there. Run `git status` after a build. Use `--configs` (or `--manifest`) to write elsewhere when you only test.

### Run models

| Script | Purpose |
|---|---|
| `run_batch.py` | Run a solver on many tasks, grade each client, write `results.csv`. Resumable. Beam by default; `--solver openai_compat` for any OpenAI-compatible endpoint. |
| `run_models.py` | Run the models in `data/models.json` through the Hugging Face router on Tier B and case 3. `--dry-run` plans and estimates cost. One `run_batch.py` call per model and suite. |
| `run_probe_batch.py` | Case 2: run the probe arms (`placebo`, `P5`, `P20`). `--dry-run` uses a scripted client. |
| `run_rae.py` | Run the external tool reverse-api-engineer on every task and grade its client. Notes: `results/rae_feasibility.md`. |
| `fake_openai_server.py` | A local fake OpenAI-compatible server that replies with the reference client, for dry runs. |
| `estimate_cost.py` | **Historical.** Cost estimate for open models. `run_models.py --dry-run` replaces it. |

### Score and analyse

| Script | Purpose |
|---|---|
| `grader_audit.py` | Audit the Tier A grader: reference client, trivial agents and 25 injected fault types. No model calls. |
| `score_heal.py` | Case 3: join a batch's `results.csv` with task factors, regression rate and patch size. |
| `analyze_tierb.py` | Tier B regression over the six switches (task-cluster bootstrap CIs) and the header ablation. Exploratory. |
| `analyze_tierb_effort.py` | Case 1 Tier B effort curve: per effort pass rate, strict rate, tokens, paired contrasts, per-switch rates. Writes `results/analysis/tierb50_effort/`. |
| `analyze_heal_v3.py` | Case 3 v3: per arm and effort, paired contrasts, tests of H16-H21. Writes `results/analysis/heal_v3_eval/`. |
| `heal_v3_verdicts.py` | Library, not a command: the statistics and the pre-registered verdict rules used by the v3 analysis. |
| `analyze_heal_controls.py` | Case 3 controls (placebo, warning-only), repeat runs and the H10 header run (H22, H23, H10). Writes `results/analysis/heal_controls/`. |
| `heal_v1_floor.py` | Score the unchanged `client_v1.py` and an empty client on the v3 tasks (the floor). No model calls. |
| `analyze_models.py` | Open models next to Beam: report, summary, leaderboard and score-versus-tokens charts. Writes `results/analysis/models/`. `--suites` limits the report, summary and charts to the listed suites. The cross-model comparison (GPT-OSS-120B low/medium/high, Qwen3.5-397B-A17B thinking; Case 2 on the placebo and P5 arms) is in [MODEL-COMPARISON.md](MODEL-COMPARISON.md). |
| `analyze_heal.py` | **Historical (v2).** Case 3 v2 analysis: effort curve, repair versus scratch, terse versus verbose. |

### Plot

| Script | Purpose |
|---|---|
| `plot_frontier.py` | Pass rate against output tokens, one line per series across efforts. Also holds the shared chart style. |
| `plot_heal_families.py` | Case 3 v3 pass rate by task family, base against hint. Called by `analyze_heal_v3.py`. |
| `plot_story.py` | The story figure set (`results/figures/story_*`). Helpers in `scripts/story_figs/`. |
| `plot_final.py` | The final figure set (`results/figures/final_{scoreboard,leaderboard,effort_curves,information_gain,gap_vs_beam,beam_missing_info,cost_vs_score,effort_matched,all_scores,beam_by_effort}.{png,svg,json}`): Beam against GPT-OSS-120B and Qwen3.5-397B-A17B. Reads the saved results; no model calls. |

### RL helpers

| Script | Purpose |
|---|---|
| `rl_demo.py` | Health check of the case 1 environment: reference client reward 1.0, empty client 0.0. |
| `make_rl_dataset.py` | Write a JSONL of case 1 prompts with a `task_spec` column, for TRL GRPO. |
| `make_repair_dataset.py` | The same for case 3 (`RepairEnv`). Splits: `train`, `test_mutation`, `test_pair`. |

## 7. Task directory layout

One task is one directory. The model sees the **visible** files. The grader uses the **hidden** files. Never show a hidden file to a model and never put one in a public export. (`../release/check_no_leak.py` checks this for the release.)

| File | Visible | In which tasks | What it holds |
|---|---|---|---|
| `goal.md` | yes | all | The task text: constructor, methods to write, rules. |
| `session.har` | yes | all (empty for `--closed-book`; absent for case 3 `failure_only`) | The recording, mixed with noise. |
| `client_v1.py` | yes | case 3 | The correct client for the old API. |
| `failure.txt` | yes | case 3 (absent for `recording_only`) | The first failing call of the old client on the new API, with the exception. |
| `CHANGELOG.md` | yes | case 3 v3, 8 `misleading` tasks | Release notes of the new API. One statement is wrong. |
| `task.json` | **hidden** | all | Task metadata, seeds, features, test credentials. |
| `op_table.json` | **hidden** | Tier A | The operation table from the spec. |
| `engine.json` | **hidden** | Tier B, case 3 | The spec and options of the stateful engine. |
| `tests.json` | **hidden** | all | The tests the model does not see. They can be rebuilt from public seeds. |
| `ref_client.py` | **hidden** | all (Tier A after `har2client ref`) | The reference client. |

Task names: Tier A is the API id (`up.com.au`). Tier B is `tierb_<index>_<domain>`. Case 3 v2 is `heal_<i>_<mutation>_<domain>` (and `terse_`, `evfail_`, `evrec_`, `compound_` prefixes). Case 3 v3 is `v3_<family>_<i>_<change>+..._<domain>`.

## 8. Grading and metrics

The grader starts a **new** fake server with new data and new credentials. It runs `client.py` in a separate child process (`python -I -B`, a temporary copy outside the task directory, secrets removed from the environment, imports of `har2client` blocked, time and output limits). This is not a full sandbox: see 12a. Tier A checks the request and the returned value. Tier B and case 3 check return values and the saved server state.

| Metric | Meaning |
|---|---|
| `pass_rate` | Share of hidden tests passed. The primary metric. |
| `strict_pass_rate` | Like `pass_rate`, but a test also fails if the client sends an extra, unrequested call inside it. |
| `failure_counts` | Why tests failed: `timeout`, `exception`, `no_request`, `auth_401`, `bad_request_400`, `not_found_404`, `wrong_op`, `args_mismatch`, `return_mismatch`, and for the stateful tasks `state_mismatch`, `incomplete_list`, `rate_limited_429`. |
| `invented_endpoint_calls` | Calls to endpoints that are not in the API. |
| `hardcoded_secret`, `hardcoded_host` | The client wrote the recorded secret or the spec host into its code. |
| `regression_rate` (case 3) | Tests that `client_v1.py` passes on the new API but the patched client fails, divided by all tests. |
| `patch_lines`, `patch_ast_nodes` (case 3) | Size of the patch against `client_v1.py`. Reported only; never rewarded in evaluation. |
| `identifiable` (case 3) | Whether the repair can be worked out from the visible files. See [DESIGN-CASE3.md](../docs/design/DESIGN-CASE3.md). |
| tokens, wall time | `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `wall_time_s` in `results.csv`. `completion_tokens` already includes reasoning tokens. |

Validity rules:

- The reference client must score 1.0 on every task. An empty client and a recording-replay client must score about 0.
- For case 3, `client_v1.py` must score 1.0 on v1 and less than 1.0 on v2. A no-op patch must fail.

## 9. RL environments

Case 1 and case 3 each have an environment. An episode is: observe, then submit one client. The reward is the `pass_rate` on a fresh grading instance, with penalties (see [DESIGN-RL.md](../docs/design/DESIGN-RL.md)): a hard-coded secret or host halves it, each invented endpoint call costs 0.1, and code that does not parse or has no top-level `class Client` gets 0.

The grader uses a pool of **spawned** worker processes. Put your code in a script file with an `if __name__ == "__main__":` guard. Do not run it from `python -` or a notebook cell: the pool breaks with `BrokenProcessPool`.

Save this as `demo_env.py` here and run `uv run python demo_env.py`. It uses reference clients, so it needs no model:

```python
from har2client.env.env import TrafficToClientEnv
from har2client.env.repair_env import RepairEnv


def main() -> None:
    # Case 1: observed traffic -> client
    env = TrafficToClientEnv(source="browser_har", split="train", curriculum="L0", compact=True, seed=0)
    obs = env.reset()  # keys: task_id, goal, observation, observation_format, probe_budget, source
    out = env.step({"type": "submit", "code": env.reference_code()})  # replace with your model's code
    print("case 1 reward:", out["reward"], "done:", out["done"])
    env.close()

    # Case 3: repair the old client
    repair = RepairEnv(split="train", curriculum="R0", seed=0)
    obs = repair.reset()  # keys: task_id, goal, client_v1, failure, observation, observation_format
    out = repair.step({"type": "submit", "code": obs["client_v1"]})  # a no-op patch: fails some tests
    print("case 3 reward:", out["reward"], out["info"]["regression_rate"], out["info"]["patch_lines"])
    repair.close()


if __name__ == "__main__":
    main()
```

- Case 1 `source` is `"browser_har"`, `"mobile_har"` or `"auto"`. Splits: `train`, `test_combo`, `test_domain`. Levels L0-L3 add switches and noise. `curriculum` is `"adaptive"`, `"uniform"` or a fixed level such as `"L2"`.
- Case 3 splits: `train`, `test_mutation`, `test_pair`. Levels R0-R3. Split files: `data/rl_splits.json`, `data/heal_splits.json`.
- `probe_budget > 0` (case 2) gives the agent a practice server: send `{"type": "probe", "request": {...}}`. Each probe costs 0.01 reward.
- TRL GRPO reward functions: `har2client.env.trl.make_reward_fn` (case 1) and `har2client.env.repair_env.make_repair_reward_fn` (case 3). Each returns `reward_fn(prompts, completions, task_spec, **kwargs)`. TRL is not a dependency. Use `remove_unused_columns=False`.
- The environment is built and tested. It is **not trained**.

## 10. Beam API notes

- Base URL: `https://api.reflection.ai/openai/v1` (OpenAI-compatible). Model id: `Beam-1.0-501B-A23B`.
- Beam always reasons. **Always set `reasoning_effort`** (`low`, `medium` or `high`; xhigh dropped 2026-10-27, see `PREREG.md`, deviations). The API default is `max`, which is slow and costly. The solver code checks the value.
- Send `reasoning_content` of earlier assistant turns back in the next request. The case 2 agent loop (`src/har2client/solve/beam_agent.py`) does this.
- Your Reflection account may have daily token limits (ours reset at 00:00 UTC). Uncached input tokens are the binding limit. One solve on a 30-50K-token recording uses about 40-55K input tokens; large recordings cost 2-16 times more. The batch runners stop cleanly when the allowance is spent (exit code 3). Run the same command later: it resumes.
- The request rate is limited (60 per minute). Use a lower `--concurrency` on HTTP 429.

## 11. Run models on many tasks

```bash
uv run python scripts/run_batch.py --tasks runs/tierb50 --out runs/batch/my_run --effort low high --runs 1 --concurrency 4
```

- One cell is task x effort x har_mode x run. Each cell writes `<out>/<task>/<effort>-<har_mode>-r<i>/{client.py,solve_log.json,grade.json}`.
- A cell with a `grade.json` is skipped, so the same command resumes.
- `results.csv` in `--out` has one row per cell: `pass_rate`, `strict_pass_rate`, failure counts, token counts.
- Exit codes: 0 done, 2 bad setup (for example no key), 3 stopped by the daily limit.
- An open model through the Hugging Face router (key in `HF_TOKEN`):

```bash
uv run python scripts/run_batch.py --tasks runs/tierb50 --out runs/batch/oss120b \
    --solver openai_compat --model openai/gpt-oss-120b:cheapest --effort high
```

- Several models at once: `scripts/run_models.py` (list in `data/models.json`; `--dry-run` first; `--limit 1` for a smoke test), then `uv run --extra plots python scripts/analyze_models.py`.

Pre-register first, then run. Check `PREREG.md` before you start a new experiment. Shared result tables go to `results/` and are committed. Built tasks and raw runs stay in `runs/`.

### 11a. Case 3 v3: the hard repair suite

**Why v3.** The v2 repair sets are saturated: Beam at low effort repaired all 48 core v2 tasks (pass rate 1.0). Every v2 change raised an HTTP error that showed the fix, and the client interface changed together with the wire format. A set that every effort solves cannot compare efforts or models ([DESIGN-CASE3-HARD.md](../docs/design/DESIGN-CASE3-HARD.md)).

**What v3 adds.** The client keeps its interface. The wire format changes under it, often with no error. Ten changes: five silent wire changes (`rename`, `cents`, `epoch`, `nest`, `enum`) and five protocol changes (`rate_limit`, `async_create`, `refresh_rotation`, `idempotency_key`, `moved`). The eval suite has 64 tasks in 7 families:

| Family | Tasks | What is special |
|---|---|---|
| `single` | 14 | One change (7 kinds, 2 tasks each). |
| `release3` | 10 | Three changes in one release. |
| `release4` | 8 | Four changes in one release. |
| `partial` | 8 | The recording shows only some operations. The fix must be generalised. |
| `sdk` | 8 | The old client is a layered SDK of 400 to 700 lines. |
| `misleading` | 8 | A `CHANGELOG.md` states one change wrongly. The recording shows the truth. |
| `terse` | 8 | Short error messages that do not name the fix. |

Each task has 17 hidden tests. Full repair means `pass_rate` = 1.0. A test passes when the return value, the saved server state and the write count are right. The setting is single-shot repair: one model call, no tools, no retries. The suite and hypotheses H16-H21 were frozen in `PREREG.md` before the first run; the controls H22, H23 were added on 2026-10-26, before they ran.

**Build.** Seeds 2001-2064. The build runs all validity gates and writes the manifest `data/heal_v3_eval.jsonl` (sha256 in `PREREG.md`). Run `git status` after: the manifest is a committed file.

```bash
uv run python scripts/build_heal_v3.py --suite eval --out runs/heal_v3_eval --seed 2
uv run python scripts/add_contract_hint.py --tasks runs/heal_v3_eval --out runs/heal_v3_eval_hint   # hint arm
```

The hint arm has the same 64 tasks; one paragraph is added to each `goal.md`. It warns that the wire format may have changed, lists the kinds of change, and asks for translation in both directions. It adds information as well as a warning, so do not read the gap as a clean measure of noticing. Report both arms.

**Run and score.**

```bash
uv run python scripts/run_batch.py --tasks runs/heal_v3_eval --out runs/batch/v3eval_base_low --effort low --runs 1 --concurrency 8
uv run python scripts/score_heal.py --tasks runs/heal_v3_eval --runs runs/batch/v3eval_base_low --out runs/batch/v3eval_base_low_scored.csv
uv run python scripts/analyze_heal_v3.py --batch-root runs/batch
```

Results for Beam at low, medium and high effort are in `results/analysis/heal_v3_eval/report.md` and the front page. The controls (placebo and notice-only arms, H22 and H23), the repeat runs at low and high effort and the Tier B header test (H10) are in `results/analysis/heal_controls/report.md`. At low effort: placebo minus base +0.5 [-6.7, +7.4]; notice minus base +18.7 [+10.0, +27.6]; hint minus placebo +39.5 [+30.0, +49.2]. H22 is supported. H23 is inconclusive: +18.7 is just under the 20-point bar and the CI excludes 0. H18 is not tested (xhigh dropped 2026-10-27; see `PREREG.md`, deviations). H10 is supported: on the 25 hidden-header Tier B tasks at high effort, as built 49.1% (mean of 3 runs), hint 96.0%, evidence 98.9% (1 run each). Repeat runs at low effort move the means by at most 1.1 points, but per-task scores are noisy (full-repair flip rate 9.4% base, 26.6% hint), so claims are about means. At high effort every case 3 and Tier B value is the mean of 3 runs per task (mean of 3 runs: base 46.0%, hint 87.7%, Tier B 74.5%). The run means are 42.7 / 47.1 / 48.2% (base), 90.6 / 85.0 / 87.5% (hint) and 78.0 / 70.0 / 75.4% (Tier B); the per-task SD over the 3 runs is 15.9, 9.7 and 9.6 points. H16 is supported (hint minus base +41.7 [+33.5, +49.8] at high, mean of 3 runs). H17 is rejected (base, high minus low +14.4 [+8.3, +20.8], 4.41x output). H19 is supported: the 1-run result was inconclusive, and under the pre-registered 3-run mean it is supported.

**Runs per task.** High effort in case 3 (base and hint arms) and Tier B is the mean of 3 runs per task. Case 3 at low effort (base and hint arms) has 2 runs. All other results are 1 run per task. `PREREG.md` set a default of 3 runs; the change is a recorded deviation. Case 1 intervals are over tasks only. The story figure `results/figures/story_hint_decomposition.*` (F7) shows base, placebo, notice-only and hint at low effort.

**Case 2 status.** Complete: all 50 Tier B tasks, 3 arms (placebo, P5, P20), 3 effort levels, 1 run per cell (450 cells). The report is `results/case2_probe/main_full/REPORT.md` (protocol `submission-tool-all-arms-v1`, see `results/case2_probe/PROTOCOL.md`). At high effort the pass rate is 49.7% for the placebo, 95.7% for P5 and 98.0% for P20; P20 minus placebo is +48.3 points [32.9, 62.6]. With the hidden header on, the gain is +64.0 [40.0, 84.0] (placebo 32.0%, P20 96.0%); with it off, +32.6 [16.0, 50.3] (placebo 67.4%, P20 100.0%). H11 is not supported: the header-off control fails (the gain should be under 5 points). One placebo cell produced no code (it hit the output-token cap) and counts as zero. A post-hoc check, not pre-registered and only for tasks 20-49 (`results/case2_probe/main_full/SENSITIVITY.md`), found a stray closing `"""` at the end of submitted code: placebo 15 of 89 clients, P5 3 of 90, P20 3 of 90. Removing it halves the header-off gain, which stays above 5 points. The placebo at high effort (49.7%) is far below case 1 Tier B as built (74.5%, mean of 3 runs) on the same tasks; the submission-tool interface is one known reason. Case 2 is 1 run per cell and repetition is deferred (`PROTOCOL.md`), so its intervals cover variation over tasks only. Tasks 00-19 ran on one machine and tasks 20-49 on a second, from the same frozen runtime archive (hashes checked). `results/case2_probe/main/` is the earlier partial record (21 of 50 tasks). The release kit exports both arms as the configs `heal_v3` and `heal_v3_hint` (64 rows each): see `../release/README.md`.

### 11b. Reproduce the headline results

The numbers on the front page come from saved files, so you can rebuild the model comparison and its figures without a model call (the other analysis scripts are in the table in section 6):

```bash
uv run --extra plots python scripts/analyze_models.py --models-file data/comparison_models.json --models-root results/model_comparison \
  --out results/analysis/model_comparison --fig-dir results/figures/model_comparison --suites tierb50 v3base v3hint probe0 probe5
uv run --extra plots python scripts/plot_final.py              # results/figures/final_*
```

To redo the runs themselves, build the task sets (section 11), then run Beam with `run_batch.py` (`REFLECTION_API_KEY` in `.env`) and the other models with `run_models.py` (`HF_TOKEN` in `.env`). The settings, providers and run counts of the model comparison are in [MODEL-COMPARISON.md](MODEL-COMPARISON.md). Beam's case 2 results are in `results/case2_probe/main_full/` (see `results/case2_probe/REPRODUCE.md`).

## 12. Run the tests

```bash
uv run pytest -q                    # about 1,570 tests, about 10.5 minutes
uv run pytest -q -m "not slow"      # faster: skips the slow tests
uv run pytest -q tests/test_heal.py # one file while you work
```

- Tests build real tasks and start real local servers. They use no network and no model.
- The release kit has its own tests. From the repo root: `python3 -m unittest discover -s release/tests`.

### 12a. Security: model code runs on your machine

The grader runs code that a model wrote. The child process has limits, but it is **not** a full sandbox.

- Since commit `715072a`, the runner executes the client from a temporary copy outside the task directory and blocks imports of `har2client`. This stops the simple route to `tests.json` and `ref_client.py`.
- The process still runs as your user. It can read any file that your user can read (for example `har2client/.env`, by an absolute path) and it can open network connections.

This is acceptable for evaluation runs of a trusted model. For RL training, run the grader in a container or VM with no network, no `.env` and no task files except `client.py`. A policy that learns to read the answer key gets reward without skill.

## 13. Add a mutation or a switch

### A new case 3 v2 mutation

A mutation changes one thing between API v1 and API v2. The code derives v1 by undoing the change on the v2 spec.

1. In `src/har2client/heal/mutations.py`, write `_revert_<name>(spec, rng)` and add a `Mutation(name, applies, revert, description)` entry to `MUTATIONS`.
2. Add the name to `SWITCH_GROUP`. Two mutations of one switch never go together in a compound task.
3. In `src/har2client/heal/identify.py`, add the name to `NEEDS_RECORDING` or `FAILURE_ONLY_OK`.
4. Optionally add compound pairs to `COMPOUND_PAIRS` in `heal/suite.py`.
5. Add a test in `tests/test_heal.py`. The validity gates must hold.
6. Rebuild with a scratch path and read the validity line:

```bash
uv run python scripts/build_heal.py --out runs/scratch/heal --limit 8 --no-scratch --configs runs/scratch/heal_configs.jsonl
```

Do not overwrite `data/heal_configs.jsonl`, `data/heal_suite.jsonl` or `data/heal_splits.json` unless you mean to change the committed suite. Log a change in `PREREG.md` under "Deviations". The ten v3 changes are `HARD_MUTATIONS` in the same file (see [DESIGN-CASE3-HARD.md](../docs/design/DESIGN-CASE3-HARD.md)).

### A new Tier B switch

A switch touches the config, the engine, the reference client and the builder. The files that mention an existing switch (`etag_update`) show the places:

- `src/har2client/tierb/config.py`: add the switch and its levels to `FACTORS`.
- `src/har2client/tierb/engine.py`: the server behaviour.
- `src/har2client/tierb/refclient.py`: the matching reference code (and a flawed variant in `FLAWS`).
- `src/har2client/tierb/build.py`: the goal text and the recording player where needed.
- `src/har2client/env/milestones.py` and `env/splits.py`: a milestone and the split rules.
- `scripts/analyze_tierb.py`: the term in the regression.
- Tests: `tests/test_tierb_*.py`, `tests/test_env_*.py` and the golden digests in `tests/fixtures/golden_tierb/`.

A new switch changes how the 50 configs are drawn, so treat it as a new benchmark version and log it in `PREREG.md`.

## 14. Troubleshooting

| Problem | Cause and fix |
|---|---|
| `REFLECTION_API_KEY is not set` | `har2client solve` does not read `.env`. Run `set -a; source .env; set +a`. |
| `BrokenProcessPool` or `FileNotFoundError: ...<stdin>` in an RL environment | Run your code from a `.py` file with `if __name__ == "__main__":`. |
| `run_batch.py` exits with code 3 | The daily token allowance is spent. Run the same command after the limit resets. It resumes. |
| HTTP 429 | Lower `--concurrency`. |
| Cell has `status` `no_code` | The reply had no ```python block. It counts as pass_rate 0. Read `solve_log.json` in the cell. |
| Cell has `status` `api_error` | The call failed. It is not a model result. `run_models.py` retries it; the analysis scripts leave it out and count it. |
| `har2client build` exits with code 2 | The spec is not usable (OpenAPI 3.1, or too few operations). Not a bug. |
| `data/specs/...json` does not exist | `data/specs/` is git-ignored. Run `scripts/fetch_spec.py <api_id>`, or `select_specs.py`, or pass an `https://` URL. |
| Reference client scores below 1.0 on a task | The task is invalid. It is our bug, not a model failure. Do not run models on it. |
| Rebuilt Tier A task has different hidden tests than an old run | The Tier A test generator changed in commit `5e68571`, after the first Tier A run. Goal and recording stay equal. Tier B rebuilds byte for byte. See `../release/README.md`, item 2. |
| `git status` shows changed files in `data/` after a build | The build scripts write config lists there by default. `git checkout -- data/<file>` or build with `--configs` / `--manifest`. |
| A test is slow or hangs | Run one file at a time. |
| You see a key in a file or log | Stop. Remove it. Rotate the key. Never commit it. |

## 15. Source map

`src/har2client/`:

| Path | What it holds |
|---|---|
| `cli.py` | The `har2client` command. |
| `ops.py`, `build_task.py`, `datagen.py`, `testgen.py`, `refclient.py` | Tier A: read the spec, build the task, make test data and hidden tests, write the reference client. |
| `mock.py`, `player.py`, `har.py`, `browser.py` | Tier A fake server, scripted user session, HAR writing, browser noise profile. |
| `variants.py`, `namegen.py` | Name and memory variants, and rewordings through Beam. |
| `mutants.py` | Tier A fault operators for the grader audit. |
| `grade.py`, `runner.py` | The grader and the child-process client runner (temporary copy, import block). |
| `tierb/` | Tier B: `config.py`, `spec.py`, `engine.py`, `player.py`, `build.py`, `grader.py`, `refclient.py`, `testgen.py`, `domains.py`. |
| `heal/` | Case 3 v2: `mutations.py`, `select.py`, `failure.py`, `session.py`, `build.py`, `suite.py`, `identify.py`, `patch.py`. |
| `heal/` (v3) | `hard.py` (the v3 task builder: a release of 1-4 changes to a task directory, with the validity gates), `hardtests.py` (17 hidden tests per task), `changelog.py` (`CHANGELOG.md` for `misleading` tasks), `release.py` (release selection and the pilot manifest; the ten changes are `mutations.HARD_MUTATIONS`), `sdk_client/` (layered SDK-style old clients: `auth.py`, `models.py`, `plan.py`, `render.py`, `resource.py`, `transport.py`). |
| `env/` | RL: `env.py` (`TrafficToClientEnv`), `repair_env.py` (`RepairEnv`), `reward.py`, `curriculum.py`, `splits.py`, `repair_splits.py`, `tasks.py`, `repair_tasks.py`, `milestones.py`, `probe.py`, `trl.py`, and `sources/` (`browser.py`, `mobile.py`, `canon.py`). |
| `solve/` | Solvers: `beam.py` (Beam), `openai_compat.py` (any OpenAI-compatible endpoint), `beam_agent.py` (case 2 agent loop), `prompt.py`, `agent_prompt.py`, `envfile.py` (reads `.env`). |

## 16. Further reading

| File | Topic |
|---|---|
| [../README.md](../README.md) | Project front page, results, quickstart. |
| [../docs/architecture.md](../docs/architecture.md) | The whole system, plain language first. |
| [../docs/related-work/](../docs/related-work/) | Literature reviews. |
| [DESIGN.md](../docs/design/DESIGN.md) | Tier A contracts and grading rules. |
| [DESIGN-TIERB.md](../docs/design/DESIGN-TIERB.md) | Tier B engine, switches, task set. |
| [DESIGN-RL.md](../docs/design/DESIGN-RL.md) | RL environment design. |
| [DESIGN-CASE3-HARD.md](../docs/design/DESIGN-CASE3-HARD.md) | Case 3 v3 (current): silent wire changes, protocol changes, the evaluation suite. |
| [DESIGN-CASES.md](../docs/design/DESIGN-CASES.md), [DESIGN-CASE3.md](../docs/design/DESIGN-CASE3.md) | Earlier designs of cases 2 and 3 (v2). Kept for the record. |
| [PREREG.md](PREREG.md) | Pre-registered hypotheses and deviations. |
| [results/README.md](results/README.md) | Index of the result files. |
| [../release/README.md](../release/README.md) | Hugging Face release kit. |
