# reverse-api-engineer (RAE) with Beam: feasibility (2026-10-24)

Verdict: **runnable offline.** No browser is needed. The plumbing was tested end to end with a fake
OpenAI-compatible server (3 Tier B tasks, graded 1.0 each: RAE -> OpenCode -> fake server -> `client.py` -> grader).
No real model call was made.

## Command

```bash
cd har2client   # from the repo root
# Beam (key REFLECTION_API_KEY from the environment or har2client/.env; never printed)
uv run python scripts/run_rae.py --tasks runs/tierb50 --out runs/rae_beam --effort high --concurrency 2
# plumbing check, no key and no cost
uv run python scripts/fake_openai_server.py --tasks runs/tierb50 --port 8099 --log /tmp/fake.jsonl &
FAKE_KEY=x uv run python scripts/run_rae.py --tasks <dir> --out /tmp/rae_dry \
    --base-url http://127.0.0.1:8099/v1 --model fake-model --api-key-env FAKE_KEY
# any open model: --base-url https://router.huggingface.co/v1 --model openai/gpt-oss-120b:cheapest --api-key-env HF_TOKEN
```

First run installs RAE into `~/.cache/har2client/rae-venv` (`uv sync --project <clone>` with
`UV_PROJECT_ENVIRONMENT`; your local reverse-api-engineer clone, set with `--rae-dir` or `RAE_DIR`, is not changed) and lets `npx` fetch
`opencode-ai@1.18.4` plus `@ai-sdk/openai-compatible` (needs network, about 1 minute). The script reuses
`run_batch.py` for the cell loop, resume, grading, `results.csv` and the summary. Results per cell:
`<out>/<task>/<effort>-raw-r1/{client.py,solve_log.json,grade.json,rae/}`; `rae/` holds RAE's log, JSON result and
message log. Opt-in real test: `H2C_RAE_E2E=1 uv run pytest -q tests/test_run_rae.py`.

## How RAE runs offline (from its source)

- `reverse-api-engineer engineer <run_id> --fresh -p <goal> -o <dir> --json` re-engineers an existing run. With no
  history entry it reads `<dir>/har/<run_id>/recording.har` (run_id: letters, digits, `-`, `_`). The script stages
  the task's `session.har` there (plus a `session.har` copy, because goal.md calls the file that).
- `--fresh -p` replaces RAE's original goal. We pass `goal.md` whole, so the agent is told our `Client` interface.
- Model: RAE's `sdk: opencode` mode talks to an OpenCode server. The script starts one server per batch with
  `OPENCODE_CONFIG_CONTENT` (provider `@ai-sdk/openai-compatible`, base URL, `apiKey: "{env:NAME}"`, model,
  `reasoningEffort`). Reflection: `https://api.reflection.ai/openai/v1`, `Beam-1.0-501B-A23B`, `REFLECTION_API_KEY`.
  Checked with the fake server: the model id and `reasoning_effort` reach the provider.
- RAE runs with its own `HOME` (`<cell>/rae/home`) and cwd, so `~/.reverse-api` is never touched.
- Output: `<dir>/scripts/<run_id>/api_client.py`.

## Adapter (how much it changes RAE's output)

RAE writes `api_client.py`, not `client.py`. The script copies it to `client.py`:

| RAE output | Result | Change |
|---|---|---|
| defines `class Client` | copied | none |
| exactly one class with `base_url` in `__init__` | copied | one added line `Client = <Name>` |
| other, or syntax error | no `client.py` | status `no_client_class` or `no_code`, pass rate 0 |

The adapter used is in `solve_log.json` (`adapter`). Report the share of `alias` and `no_client_class` cells next
to the score. Nothing else is rewritten; a hard-coded host or key in RAE's code is scored by the grader as usual.

## Blockers and caveats (exact)

1. **Real run not done.** Needs a model API key (for example `REFLECTION_API_KEY`). RAE is an agent, so one task costs several model
   turns, each resending the context (the HAR is about 20-56K tokens). Rough, not measured: 200-800K input tokens
   per task. At a limit of 10M uncached input tokens per day, that is about 12-50 tasks a day. Run a
   5-task trial first and read `usage` in `solve_log.json`.
2. **Prompt conflict (by design, not changed).** RAE's system prompt says "hardcode all credentials" and "test
   against the live API"; goal.md says the opposite. We do not edit RAE's prompts (it is the tool as shipped). Expect
   hard-coded hosts and keys (`hardcoded_host`, `hardcoded_secret` in the grade) and an `api_client.py` that has
   a `main()` demo.
3. **Live verification cannot work.** RAE tells the agent to run its script (up to 5 tries). Our HAR hosts are
   not reachable, so the agent sees connection errors and may rewrite working code. This is part of the measured
   behaviour; say so in the write-up.
4. **Version pin.** RAE 0.14.0 expects the OpenCode 1.x API. A Homebrew `opencode` 2.1.4 (other API,
   random server password) and does not work with RAE. The script pins `opencode-ai@1.18.4` (RAE's tested version).
5. **Isolation.** The agent has file tools and RAE auto-approves access outside the run dir. The run dir holds only
   the HAR; hidden files live elsewhere. The script scans RAE's message log for hidden file names and task paths
   and records them in `hidden_mentions` (`[]` in all dry runs). Check that field before publishing a score.
6. OpenCode caps output at 32,000 tokens per turn (seen in the request log); Beam in our own runs allows 65,536.
7. RAE's printed cost is its own estimate and is wrong for these models; use token counts from `usage`.
