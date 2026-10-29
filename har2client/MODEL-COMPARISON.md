# Beam, GPT-OSS-120B and Qwen comparison

## Scope and settings

- Case 1: the existing 50 Tier B tasks.
- Case 2: the same 50 tasks, compared on the placebo (budget 0) and P5
  (budget 5) arms. Both arms have `submit_client`; only P5 has `http_request`.
- Case 3: the 64-task evaluation set, both base and contract-hint arms.
- GPT-OSS-120B: explicit `reasoning_effort` low, medium and high (Novita),
  recorded separately.
- Qwen3.5-397B-A17B: explicit `enable_thinking=true`, with no
  `reasoning_effort` sent. This is not equivalent to Beam high.
- Beam: reuse saved results. Beam high on Cases 1 and 3 is the per-task mean
  of 3 runs; low, medium and Case 2 (`results/case2_probe/main_full`, complete)
  are 1 run. Every other model is 1 run per cell. Paired differences use the
  tasks both sides have.
- Output cap: other models are capped at 32,768 output tokens per call; Beam
  used 65,536.

The scope is 278 task/condition pairs per model setting (50 + 50 + 50 + 64 + 64),
1,112 other-model cells over four settings. Reasoning labels are API settings, not calibrated
equal compute budgets across models. Compare measured token usage as well.

## Providers and smoke checks

`data/comparison_models.json` contains only the two requested baseline models.
Provider IDs and published rates are pinned per setting. The public HF router
catalogue was fetched on 2026-10-28. GPT-OSS uses Novita after DeepInfra returned
a 504 on a full-task smoke request. Qwen also uses Novita after repeated
DeepInfra 504 responses on Case 3 smoke requests. Diagnostics and smoke
outputs are separate from evaluation results.

A Windows UTF-8 output bug found during smoke testing was corrected before
the main run. Failed harness cells are retried and excluded from model accuracy;
their API usage is still counted in the cost ledger. A missing code submission
from a completed model response remains a model failure.

## Cost tracking

Set `--cost-limit-usd` to your spending limit. Use one append-only ledger per
billing checkpoint, keep a margin below available credit, and run only one
process against a ledger.
Before a call, the tracker reserves a conservative upper estimate from the pinned
provider's context limit, requested maximum output and tariff. In-flight calls,
retries, and interrupted requests retain their reservations. Unknown costs are
not assumed to be free. Provider tariff changes and billing reconciliation are
separate from this local guard; final charged amounts come from HF billing.

`completion_tokens` already includes reasoning; do not add reasoning again.
Cached input is logged but no discount is assumed without a verified tariff.
The ledger stores request IDs, model/provider, settings, usage, and task context;
it does not store API credentials, request prompts, or authorization headers.

Sources: [HF pricing and billing](https://huggingface.co/docs/inference-providers/pricing),
[public model catalogue](https://router.huggingface.co/v1/models).

## Commands

From `har2client/`, with `HF_TOKEN` in the local `.env`:

```text
uv run python scripts/run_models.py --models-file data/comparison_models.json --models gpt-oss-120b-high qwen3.5-397b-thinking gpt-oss-120b-low gpt-oss-120b-medium --suites tierb50 v3base v3hint probe0 probe5 --out-root runs/batch/comparison --cost-ledger runs/model_comparison/requests.jsonl --cost-limit-usd 24 --concurrency 2
```

If a run stops, keep the output directory and run the same command again.
Finished cells are skipped. Do not run two copies against one ledger at the
same time.

The JSONL ledger preserves every attempt, including retries. Its adjacent
`requests.summary.json` distinguishes token-rate estimates, unknown costs and
reserved budget charges. Reconcile its request IDs with HF usage/billing.

```text
uv run --extra plots python scripts/analyze_models.py --models-file data/comparison_models.json --models-root results/model_comparison --out results/analysis/model_comparison --fig-dir results/figures/model_comparison --suites tierb50 v3base v3hint probe0 probe5
```

`--suites` limits the report, `summary.json` and figures to the listed suites.
The report adds matched-effort contrasts for GPT-OSS. Qwen is labelled thinking
enabled; it has no matched-effort contrast. The primary token limit for the new
baselines is 32,768; Beam used 65,536.

## Explicit Qwen thinking follow-up

Qwen now sends `extra_body={"enable_thinking": true}` in every case. Its new
setting is `qwen3.5-397b-thinking`; the earlier provider-default outputs remain
separate under `qwen3.5-397b-default` and must not be pooled. The earlier
responses contained reasoning tokens; thinking was never explicitly disabled.
The follow-up uses a new frozen runtime. All paid calls keep the configured
spending guard.

## Results

All runs are complete. No cell has an `api_error`; no call came near the output
cap (largest completion: 20,316 tokens). Case 2 is compared on the placebo and P5
arms. Cells are pass rate, with the paired difference from Beam (high) in points
and its 95% bootstrap CI over tasks. Beam (high) is the mean of 3 runs on Cases 1
and 3 and 1 run on Case 2; all other rows are 1 run.

| Setting | tierb50 (n=50) | v3base (n=64) | v3hint (n=64) | probe0 (n=50) | probe5 (n=50) |
|---|---|---|---|---|---|
| Beam (high) | 74.5% | 46.0% | 87.7% | 49.7% | 95.7% |
| GPT-OSS-120B (low) | 51.1% (-23.3 [-33.9, -13.7]) | 32.7% (-13.3 [-23.3, -2.9]) | 47.7% (-40.0 [-50.2, -30.1]) | 50.0% (+0.3 [-16.9, +17.4]) | 62.6% (-33.1 [-48.3, -18.0]) |
| GPT-OSS-120B (medium) | 53.7% (-20.8 [-31.6, -10.1]) | 34.4% (-11.6 [-21.5, -1.5]) | 50.4% (-37.3 [-47.0, -27.8]) | 56.6% (+6.9 [-10.0, +24.3]) | 57.4% (-38.3 [-52.3, -24.3]) |
| GPT-OSS-120B (high) | 69.7% (-4.8 [-13.5, +3.0]) | 58.5% (+12.6 [+3.0, +21.5]) | 57.8% (-29.9 [-39.3, -20.8]) | 69.4% (+19.7 [+5.7, +34.3]) | 82.3% (-13.4 [-25.4, -1.4]) |
| Qwen3.5-397B-A17B (thinking) | 36.0% (-38.5 [-50.3, -27.0]) | 29.6% (-16.4 [-24.4, -8.7]) | 52.3% (-35.4 [-45.6, -24.8]) | 40.0% (-9.7 [-25.4, +6.3]) | 48.0% (-47.7 [-61.4, -33.7]) |

Full tables, token use and matched-effort contrasts are in
`results/analysis/model_comparison/report.md`; figures are in
`results/figures/model_comparison/`.

One observation, single run per cell: GPT-OSS gains about 15 points from the
contract hint on Case 3 at low (32.7% to 47.7%) and medium (34.4% to 50.4%),
but none at high (base 58.5%, hint 57.8%). There were no output-cap hits or
errors in those cells.

## Shared score snapshot

`results/model_comparison/` contains the saved evaluation CSVs for the compared
suites. The earlier Qwen provider-default run is excluded. See the snapshot
README for counts. Analyze these CSVs offline with the command above.
