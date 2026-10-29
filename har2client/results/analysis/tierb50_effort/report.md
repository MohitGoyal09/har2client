# Case 1 Tier B effort curve: analysis

Steps present: 3 of 3. Resamples: 10,000. Seed: 20261024. `api_error` rows excluded from every number: 0.

Note: high effort: each task is the mean of 3 runs (tierb50_high_r2.csv, tierb50_high_r3.csv are runs 2 onwards); low and medium: 1 run.

## Verdict (pre-registered, PREREG.md)

| test | verdict | evidence |
|---|---|---|
| Tier B effort curve (PREREG: test as H18) | **not tested** | not tested: xhigh dropped 2026-10-27, see PREREG.md deviations |

Test: not tested: xhigh dropped 2026-10-27, see PREREG.md deviations. Supported if: n/a. Rejected if: n/a.

## Per effort

Shares in 0-1; 95% task-bootstrap CI in brackets.

| effort | state | tasks scored | pass rate [95% CI] | strict pass rate | full-correct share | mean output tokens | api_error rows (excluded) |
|---|---|---|---|---|---|---|---|
| low | complete | 50/50 | 0.571 [0.434, 0.709] | 0.571 [0.431, 0.706] | 0.520 [0.380, 0.660] | 1,154 [1,072, 1,238] | 0 |
| medium | complete | 50/50 | 0.637 [0.500, 0.760] | 0.637 [0.500, 0.760] | 0.620 [0.480, 0.760] | 2,259 [2,078, 2,444] | 0 |
| high (mean of 3 runs) | complete | 50/50 | 0.745 [0.630, 0.849] | 0.745 [0.632, 0.847] | 0.733 [0.620, 0.833] | 4,818 [4,293, 5,386] | 0 |

## Paired contrasts (by task, points)

95% CI by paired bootstrap over tasks (10,000 resamples, fixed seed 20261024).

| contrast | paired tasks | pass diff [CI] | full-correct diff [CI] | output tokens ratio |
|---|---|---|---|---|
| medium-low | 50 | +6.6 [-1.4, +15.4] | +10.0 [+0.0, +20.0] | 1.96x |
| high (mean of 3 runs)-medium | 50 | +10.8 [+3.9, +18.9] | +11.3 [+4.0, +19.3] | 2.13x |
| high (mean of 3 runs)-low | 50 | +17.3 [+9.1, +26.7] | +21.3 [+11.3, +32.0] | 4.17x |

## Pass rate by switch level and effort

Mean pass rate (0-1) of the scored tasks at that level; descriptive, no CIs.

| switch | level | tasks | low | medium | high (mean of 3 runs) |
|---|---|---|---|---|---|
| auth | apikey_header | 16 | 0.679 (n=16) | 0.812 (n=16) | 0.833 (n=16) |
| auth | bearer_login | 17 | 0.521 (n=17) | 0.529 (n=17) | 0.686 (n=17) |
| auth | cookie_csrf | 17 | 0.521 (n=17) | 0.580 (n=17) | 0.720 (n=17) |
| pagination | cursor | 12 | 0.583 (n=12) | 0.750 (n=12) | 0.802 (n=12) |
| pagination | link_header | 12 | 0.500 (n=12) | 0.417 (n=12) | 0.583 (n=12) |
| pagination | none | 13 | 0.516 (n=13) | 0.681 (n=13) | 0.842 (n=13) |
| pagination | offset | 13 | 0.681 (n=13) | 0.692 (n=13) | 0.744 (n=13) |
| token_expiry | off | 33 | 0.623 (n=33) | 0.667 (n=33) | 0.808 (n=33) |
| token_expiry | on | 17 | 0.471 (n=17) | 0.580 (n=17) | 0.622 (n=17) |
| etag_update | off | 25 | 0.600 (n=25) | 0.680 (n=25) | 0.813 (n=25) |
| etag_update | on | 25 | 0.543 (n=25) | 0.594 (n=25) | 0.676 (n=25) |
| hidden_header | off | 25 | 0.943 (n=25) | 0.960 (n=25) | 0.998 (n=25) |
| hidden_header | on | 25 | 0.200 (n=25) | 0.314 (n=25) | 0.491 (n=25) |
| envelope | off | 25 | 0.680 (n=25) | 0.794 (n=25) | 0.823 (n=25) |
| envelope | on | 25 | 0.463 (n=25) | 0.480 (n=25) | 0.667 (n=25) |

## Tier A baseline (saturated, table only)

Tier A: 30 public-API tasks, original CSV; the saturated baseline for this curve (PREREG Deviations: Tier A alone does not separate systems).

| effort | state | tasks scored | pass rate [95% CI] | strict pass rate | full-correct share | mean output tokens | api_error rows (excluded) |
|---|---|---|---|---|---|---|---|
| Tier A low | complete | 30/30 | 0.963 [0.892, 1.000] | 0.963 [0.892, 1.000] | 0.933 [0.833, 1.000] | 1,013 [839, 1,215] | 0 |
| Tier A high | complete | 30/30 | 0.996 [0.988, 1.000] | 0.996 [0.988, 1.000] | 0.967 [0.900, 1.000] | 2,984 [2,363, 3,657] | 0 |

## Reading rules

- Unit = task. A task's score at an effort is the mean over its runs: high = 3 runs ("mean of 3 runs"; PREREG.md deviation 2026-10-27), low and medium = 1 run. Contrasts that involve high use these per-task means. Pass rate, strict pass rate and full-correct share are shares in 0-1; contrasts are in percentage points of `pass_rate` (the primary metric).
- `api_error` rows (failed model calls) are left out of every number and counted per effort. Any other failed status (`no_code`, `grade_error`, ...) counts as pass rate 0.
- Full-correct share = share of tasks with pass_rate = 1.0 (all runs). Strict pass rate comes from the CSV column when it has one, else from the saved strict regrade JSON (`strict_pass_rate` per cell); a task without a strict value is left out of that mean.
- 95% CIs are percentile intervals from a bootstrap over tasks (paired differences for contrasts), fixed seed; the resample count is printed in the header.
- Output tokens = `completion_tokens` (includes reasoning). A token ratio is the mean output of the first effort over the mean output of the second, on the tasks both have.
- The pre-registered verdict (test as H18, xhigh - high) is not tested: xhigh dropped 2026-10-27, see PREREG.md deviations.
- A step is complete when every task in `runs/tierb50` has a row (`api_error` rows count as finished but give no score). A partial step is shown in the step table but is not used in contrasts, switch table or chart, so a result never rests on a few early tasks.
- Medium is read from `<batch-root>/tierb50_<effort>/results.csv`; an effort that the --results files already hold (low, high) is not read again from the batch root.
- H4 (simple vs hard tasks) and H9 (pass^3) are not tested here: H9 needs 3 runs per task and this curve has 1; H4 needs the "simple" task definition applied to Tier B switches, which PREREG does not map. The switch table is descriptive (no CIs; switches overlap across tasks).
- Tier A (`set30_v1_tierA.csv`, low and high) is a table only: the saturated baseline, not part of the verdict or the chart.
