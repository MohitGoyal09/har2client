# Case 2 extension: protocol case2-ext-v1

This file extends `PROTOCOL.md` (`submission-tool-all-arms-v1`, frozen in
`case2-runtime`). That runtime is not edited. This directory is a copy.

## What differs from v1

1. New arms `P1` (1 probe) and `P2` (2 probes). Placebo and P5 are unchanged.
   Prompt text, tools, turn limit (`budget + 3`), grading and tasks are the same as v1.
2. Optional endpoint override through environment variables (`src/har2client/solve/beam.py`).
   With no variables set, the runtime behaves exactly as v1.

| Variable | Default (v1 value) | Meaning |
|---|---|---|
| `BEAM_BASE_URL` | `https://api.reflection.ai/openai/v1` | OpenAI-compatible base URL |
| `BEAM_MODEL` | `Beam-1.0-501B-A23B` | model id sent in each request |
| `BEAM_API_KEY` | unset | API key for the endpoint. If unset and `BEAM_BASE_URL` is default, `REFLECTION_API_KEY` is used. If `BEAM_BASE_URL` is custom, there is no fallback: the Reflection key is never sent to another host. |

`solve_log.json` still shows `"protocol": "submission-tool-all-arms-v1"` (the per-episode
protocol is unchanged). Each output's `run_manifest.json` records `model` and `base_url`.

### Hugging Face router

```bash
export BEAM_BASE_URL=https://router.huggingface.co/v1
export BEAM_MODEL='reflection/Beam-1.0-501B-A23B:baseten'
export BEAM_API_KEY=<HF token>
```

Rate-limit header: `x-ratelimit-remaining-output-tokens-day` is only read with a safe lookup
(`beam.rate_limit_headers`, `run_batch.read_row`). A missing header gives an empty
`remaining_output_tokens_day` cell; it does not crash or stop the run. The batch stops only on an
explicit `daily_allowance_exhausted` 429 or `x-should-retry: false`. On the HF router, other
failures (for example 402 or 429 without those signals) are retried 5 times, then saved as
`api_error` cells. Such cells are excluded from means and retried on resume. The batch does not
stop by itself, so watch the log for a run of `api_error` rows.

Results from a different endpoint are a different provider condition. Do not pool them with
Reflection-endpoint results without stating it.

## Planned runs (do not run until approved)

Task coverage: the v1 main run covered 50 tasks as `tierb_00..19` (first 20 of `tasks/tierb50`)
plus `tierb_20..49` (`tasks/tierb50_rest`). `tasks/tierb50` holds all 50, and its tasks 20-49
are byte-identical to `tasks/tierb50_rest` (checked with `diff -rq`). So `--tasks tasks/tierb50`
(no `--limit`) gives the same 50 tasks in one command. The first-20 v1 output is not in this
tree; the `--limit 20` origin is inferred from `CASE2-RUNBOOK.md` and the `_rest` driver.

`--runs N` always numbers runs `r1..rN`. Cell dirs for R1 are therefore `r1`, `r2`; they are
protocol runs 2 and 3 (v1 holds run 1). Map r1 -> 2 and r2 -> 3 when merging.

### R1: P5, high, runs 2 and 3, 50 tasks (100 episodes)

```bash
cd case2-runtime-ext
uv run python scripts/run_probe_batch.py --tasks tasks/tierb50 --out runs/ext/R1_P5_high_r2r3 \
  --arms P5 --effort high --runs 2 --concurrency 4
```

### R2: P1 and P2, high, 1 run, 50 tasks (100 episodes)

```bash
uv run python scripts/run_probe_batch.py --tasks tasks/tierb50 --out runs/ext/R2_P1P2_high \
  --arms P1 P2 --effort high --runs 1 --concurrency 4
```

### R3 (optional): P5, effort max, 1 run, 50 tasks (50 episodes)

`beam.EFFORTS` = low, medium, high, xhigh, max, so `max` is allowed.

```bash
uv run python scripts/run_probe_batch.py --tasks tasks/tierb50 --out runs/ext/R3_P5_max \
  --arms P5 --effort max --runs 1 --concurrency 4
```

For the HF router, export the three variables above first. Re-running the same command resumes
(cells with a readable grade file are skipped). Use a new `--out` for each run, and never mix
`--dry-run` with real results.

## Offline check

```bash
uv run python scripts/run_probe_batch.py --tasks tasks/tierb50 --limit 2 --out /tmp/ext_dry \
  --arms P1 P2 P5 --effort high --dry-run
```

Uses a scripted fake client: no Beam calls.
