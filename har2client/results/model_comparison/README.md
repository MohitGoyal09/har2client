# Model comparison snapshot

Final snapshot of the cross-model comparison. The provider is Novita via the
Hugging Face router for both models. Settings and pinned token-rate estimates
are in `data/comparison_models.json`.

- GPT-OSS-120B: explicit `reasoning_effort` low, medium and high (three settings).
- Qwen3.5-397B-A17B: thinking explicitly enabled; there is no matched effort
  level, so it is not equivalent to any Beam effort. The earlier Qwen
  provider-default experiment is excluded.
- 1 run per cell for these models. Beam (high) is the per-task mean of 3 runs on
  Cases 1 and 3; Beam Case 2 is 1 run from `results/case2_probe/main_full`.
- Outputs are capped at 32,768 tokens per call; the Beam runs used 65,536.

Case 2 is compared on the placebo and P5 arms. These CSVs contain no API keys,
request logs or account billing details.

| Setting | Suite | Scored cells | API-error cells |
|---|---|---:|---:|
| GPT-OSS-120B (low) | tierb50 | 50 | 0 |
| GPT-OSS-120B (low) | v3base | 64 | 0 |
| GPT-OSS-120B (low) | v3hint | 64 | 0 |
| GPT-OSS-120B (low) | probe0 | 50 | 0 |
| GPT-OSS-120B (low) | probe5 | 50 | 0 |
| GPT-OSS-120B (medium) | tierb50 | 50 | 0 |
| GPT-OSS-120B (medium) | v3base | 64 | 0 |
| GPT-OSS-120B (medium) | v3hint | 64 | 0 |
| GPT-OSS-120B (medium) | probe0 | 50 | 0 |
| GPT-OSS-120B (medium) | probe5 | 50 | 0 |
| GPT-OSS-120B (high) | tierb50 | 50 | 0 |
| GPT-OSS-120B (high) | v3base | 64 | 0 |
| GPT-OSS-120B (high) | v3hint | 64 | 0 |
| GPT-OSS-120B (high) | probe0 | 50 | 0 |
| GPT-OSS-120B (high) | probe5 | 50 | 0 |
| Qwen3.5-397B-A17B (thinking enabled) | tierb50 | 50 | 0 |
| Qwen3.5-397B-A17B (thinking enabled) | v3base | 64 | 0 |
| Qwen3.5-397B-A17B (thinking enabled) | v3hint | 64 | 0 |
| Qwen3.5-397B-A17B (thinking enabled) | probe0 | 50 | 0 |
| Qwen3.5-397B-A17B (thinking enabled) | probe5 | 50 | 0 |

Case 1 is `tierb50` (50 tasks). Case 2 is `probe0` (placebo) and `probe5` (P5),
50 tasks each. Case 3 is `v3base` and `v3hint` (64 tasks each). Missing
submissions are scored model failures; API errors and budget stops are excluded
from accuracy and must remain visible as missing results.

Reproduce the report and figures from `har2client/`:

```text
uv run --extra plots python scripts/analyze_models.py --models-file data/comparison_models.json --models-root results/model_comparison --out results/analysis/model_comparison --fig-dir results/figures/model_comparison --suites tierb50 v3base v3hint probe0 probe5
```
