# Open models vs Beam

No other models run yet. Only the Beam rows are scored; run `scripts/run_models.py` and this script again.

Pass rate = mean over tasks of the share of hidden tests passed (1 run per task). Full solve = share of tasks at pass rate 1.0. CI = 95% percentile bootstrap over tasks (10,000 resamples, seed 20261024). Output tokens include reasoning tokens. `api_error` cells are not model results: they are left out and counted. Difference = model minus Beam (high), in points, paired over tasks, shown only when the task sets are equal.

## Case 1 Tier B (`tierb50`)

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| Beam (high) | 50 | 78.0% [66.0, 88.0] | 78.0% | 4,854 | 0 | - |
| Beam (medium) | 50 | 63.7% [50.0, 76.0] | 62.0% | 2,259 | 0 | -14.3 [-24.6, -6.0] |
| Beam (low) | 50 | 57.1% [43.4, 70.6] | 52.0% | 1,154 | 0 | -20.9 [-32.6, -10.6] |

No Pareto line: 1 distinct model(s), need 3.

## Case 3 base (`v3base`)

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| Beam (high) | 64 | 42.7% [33.6, 52.1] | 28.1% | 7,626 | 0 | - |
| Beam (medium) | 64 | 37.0% [28.4, 46.0] | 21.9% | 2,922 | 0 | -5.7 [-13.0, +1.7] |
| Beam (low) | 64 | 31.6% [23.7, 40.2] | 17.2% | 1,758 | 0 | -11.1 [-18.9, -3.9] |

No Pareto line: 1 distinct model(s), need 3.

## Case 3 + hint (`v3hint`)

| model | n | pass rate [95% CI] | full solve | mean output tokens | api_error | vs Beam (high) [95% CI] |
|---|---:|---:|---:|---:|---:|---:|
| Beam (high) | 64 | 90.6% [84.1, 96.1] | 84.4% | 6,855 | 0 | - |
| Beam (medium) | 64 | 76.9% [68.0, 85.0] | 62.5% | 2,846 | 0 | -13.7 [-21.3, -6.2] |
| Beam (low) | 64 | 71.6% [62.0, 80.5] | 57.8% | 1,800 | 0 | -19.0 [-28.1, -10.3] |

## Sources

- Beam rows: `results/tierb50_v1.csv` (low, high), `results/tierb50_medium.csv`, `results/heal_v3_eval_{base,hint}_{low,medium,high}.csv`.
- Other models: `runs/batch/models/<slug>/<suite>/results.csv`, written by `scripts/run_models.py`.
