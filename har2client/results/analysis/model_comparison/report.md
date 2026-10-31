# Open models vs Beam

2 other model(s), 4 setting(s) scored: gpt-oss-120b-high, gpt-oss-120b-low, gpt-oss-120b-medium, qwen3.5-397b-thinking.

Pass rate = mean over tasks of the share of hidden tests passed. Runs per task: Beam (high) on Case 1, Case 3 and Case 2 with 5 probes is the mean of 3 runs (PREREG.md deviations 2026-10-27 and 2026-10-31); Beam low and medium, the other Case 2 arms and every other model are 1 run. Full solve = share of tasks at pass rate 1.0. CI = 95% percentile bootstrap over tasks (10,000 resamples, seed 20261024). Output tokens include reasoning tokens. `api_error` cells are not model results: they are left out and counted. Difference = model minus Beam (high), in points, paired over the tasks both have; a partial suite is paired on its shared tasks only and marked partial. A missing code submission (`no_code`, `no_submit`) scores 0.

## Case 1 Tier B (`tierb50`)

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| Beam (high, mean of 3 runs) | 50 | 74.5% [63.2, 84.8] | 62.0% | 4,818 | 0 | - |
| GPT-OSS-120B (high) | 50 | 69.7% [56.0, 81.7] | 68.0% | 5,854 | 0 | -4.8 [-13.5, +3.0] |
| Beam (medium) | 50 | 63.7% [50.0, 76.0] | 62.0% | 2,259 | 0 | -10.8 [-18.7, -3.9] |
| Beam (low) | 50 | 57.1% [43.4, 70.6] | 52.0% | 1,154 | 0 | -17.3 [-26.6, -9.0] |
| GPT-OSS-120B (medium) | 50 | 53.7% [40.6, 66.9] | 44.0% | 2,183 | 0 | -20.8 [-31.6, -10.1] |
| GPT-OSS-120B (low) | 50 | 51.1% [37.7, 65.1] | 48.0% | 2,133 | 0 | -23.3 [-33.9, -13.7] |
| Qwen3.5-397B-A17B (thinking enabled) | 50 | 36.0% [23.7, 48.9] | 24.0% | 3,159 | 0 | -38.5 [-50.3, -27.0] |

Pareto line drawn: 3 frontier point(s).

Matched-effort comparisons (model minus Beam at the same setting):

- GPT-OSS-120B (high): -4.8 [-13.5, +3.0] points.
- GPT-OSS-120B (medium): -10.0 [-21.7, +2.0] points.
- GPT-OSS-120B (low): -6.0 [-16.6, +4.0] points.

## Case 3 base (`v3base`)

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| GPT-OSS-120B (high) | 64 | 58.5% [49.4, 67.8] | 35.9% | 10,734 | 0 | +12.6 [+3.0, +21.5] |
| Beam (high, mean of 3 runs) | 64 | 46.0% [38.2, 54.0] | 20.3% | 7,746 | 0 | - |
| Beam (medium) | 64 | 37.0% [28.4, 46.0] | 21.9% | 2,922 | 0 | -8.9 [-14.2, -4.1] |
| GPT-OSS-120B (medium) | 64 | 34.4% [26.0, 43.2] | 14.1% | 2,445 | 0 | -11.6 [-21.5, -1.5] |
| GPT-OSS-120B (low) | 64 | 32.7% [24.5, 41.6] | 15.6% | 2,497 | 0 | -13.3 [-23.3, -2.9] |
| Beam (low) | 64 | 31.6% [23.7, 40.2] | 17.2% | 1,758 | 0 | -14.4 [-21.0, -8.3] |
| Qwen3.5-397B-A17B (thinking enabled) | 64 | 29.6% [21.9, 37.8] | 14.1% | 5,050 | 0 | -16.4 [-24.4, -8.7] |

Pareto line drawn: 5 frontier point(s).

Matched-effort comparisons (model minus Beam at the same setting):

- GPT-OSS-120B (high): +12.6 [+3.0, +21.5] points.
- GPT-OSS-120B (medium): -2.7 [-13.1, +7.6] points.
- GPT-OSS-120B (low): +1.1 [-9.3, +11.6] points.

## Case 3 + hint (`v3hint`)

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| Beam (high, mean of 3 runs) | 64 | 87.7% [81.6, 93.0] | 67.2% | 6,824 | 0 | - |
| Beam (medium) | 64 | 76.9% [68.0, 85.0] | 62.5% | 2,846 | 0 | -10.8 [-17.8, -4.1] |
| Beam (low) | 64 | 71.6% [62.0, 80.5] | 57.8% | 1,800 | 0 | -16.1 [-24.8, -7.8] |
| GPT-OSS-120B (high) | 64 | 57.8% [47.5, 68.0] | 45.3% | 10,163 | 0 | -29.9 [-39.3, -20.8] |
| Qwen3.5-397B-A17B (thinking enabled) | 64 | 52.3% [42.0, 62.7] | 37.5% | 5,309 | 0 | -35.4 [-45.6, -24.8] |
| GPT-OSS-120B (medium) | 64 | 50.4% [40.2, 60.8] | 35.9% | 2,490 | 0 | -37.3 [-47.0, -27.8] |
| GPT-OSS-120B (low) | 64 | 47.7% [37.4, 58.5] | 32.8% | 2,508 | 0 | -40.0 [-50.2, -30.1] |

Matched-effort comparisons (model minus Beam at the same setting):

- GPT-OSS-120B (high): -29.9 [-39.3, -20.8] points.
- GPT-OSS-120B (medium): -26.6 [-37.9, -15.1] points.
- GPT-OSS-120B (low): -23.9 [-35.3, -13.1] points.

## Case 2 no probes (`probe0`)

Beam: 1 run per task (`results/case2_probe/main_full/results.csv`, arm placebo).

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| GPT-OSS-120B (high) | 50 | 69.4% [56.9, 81.4] | 62.0% | 5,402 | 0 | +19.7 [+5.7, +34.3] |
| GPT-OSS-120B (medium) | 50 | 56.6% [43.7, 69.4] | 42.0% | 2,120 | 0 | +6.9 [-10.0, +24.3] |
| Beam (low) | 50 | 53.1% [39.7, 66.9] | 50.0% | 1,046 | 0 | +3.4 [-11.1, +17.7] |
| GPT-OSS-120B (low) | 50 | 50.0% [36.6, 63.1] | 40.0% | 2,201 | 0 | +0.3 [-16.9, +17.4] |
| Beam (high) | 50 | 49.7% [36.0, 63.7] | 48.0% | 5,634 | 0 | - |
| Beam (medium) | 50 | 46.3% [32.6, 60.0] | 44.0% | 2,283 | 0 | -3.4 [-19.1, +11.7] |
| Qwen3.5-397B-A17B (thinking enabled) | 50 | 40.0% [27.1, 52.9] | 28.0% | 4,065 | 0 | -9.7 [-25.4, +6.3] |

Matched-effort comparisons (model minus Beam at the same setting):

- GPT-OSS-120B (high): +19.7 [+5.7, +34.3] points.
- GPT-OSS-120B (medium): +10.3 [-5.4, +25.7] points.
- GPT-OSS-120B (low): -3.1 [-16.9, +10.6] points.

## Case 2 up to 5 probes (`probe5`)

Beam: 1 run per task, except Beam (high): mean of 3 runs (`results/case2_probe/main_full/results.csv`, arm P5).

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| Beam (high, mean of 3 runs) | 50 | 95.7% [91.9, 98.9] | 86.0% | 4,835 | 0 | - |
| Beam (medium) | 50 | 87.7% [78.6, 95.4] | 78.0% | 2,110 | 0 | -8.0 [-17.1, +0.3] |
| GPT-OSS-120B (high) | 50 | 82.3% [71.7, 91.7] | 74.0% | 5,903 | 0 | -13.4 [-24.4, -3.4] |
| Beam (low) | 50 | 64.9% [51.4, 77.4] | 58.0% | 1,046 | 0 | -30.9 [-43.8, -18.4] |
| GPT-OSS-120B (low) | 50 | 62.6% [49.1, 75.1] | 54.0% | 2,297 | 0 | -33.1 [-47.3, -19.4] |
| GPT-OSS-120B (medium) | 50 | 57.4% [44.3, 70.3] | 44.0% | 2,229 | 0 | -38.3 [-51.5, -25.4] |
| Qwen3.5-397B-A17B (thinking enabled) | 50 | 48.0% [35.7, 60.6] | 30.0% | 3,727 | 0 | -47.7 [-61.0, -34.1] |

Matched-effort comparisons (model minus Beam at the same setting):

- GPT-OSS-120B (high): -13.4 [-24.4, -3.4] points.
- GPT-OSS-120B (low): -2.3 [-18.0, +13.1] points.
- GPT-OSS-120B (medium): -30.3 [-42.9, -18.3] points.

## Sources

- Beam rows: `results/tierb50_v1.csv` (low, high), `results/tierb50_medium.csv`, `results/heal_v3_eval_{base,hint}_{low,medium,high}.csv`; Beam high also `results/tierb50_high_r{2,3}.csv`, `results/heal_v3_eval_{base,hint}_high_r{2,3}.csv` (mean of 3 runs).
- Beam Case 2: `results/case2_probe/main_full/results.csv` (1 run); Beam high on probe5 also `results/case2_probe/ext/R1_P5_high_r2r3/results.csv` (runs 2 and 3, exploratory protocol case2-ext-v1; mean of 3 runs).
- Other models: `runs/batch/models/<slug>/<suite>/results.csv`, written by `scripts/run_models.py`.
