# Case 3 controls, repeat runs and H10: analysis

`api_error` rows excluded from every number: 0. 95% paired bootstrap CI, 10,000 resamples, seed 20261026; points.

## Steps

| step | state | tasks finished | api_error rows (excluded) | pass rate | full repair |
|---|---|---|---|---|---|
| base | complete | 64/64 | 0 | 31.6% | 17.2% |
| hint | complete | 64/64 | 0 | 71.6% | 57.8% |
| notice | complete | 64/64 | 0 | 50.3% | 29.7% |
| placebo | complete | 64/64 | 0 | 32.1% | 17.2% |
| base_r2 | complete | 64/64 | 0 | 32.1% | 17.2% |
| hint_r2 | complete | 64/64 | 0 | 70.5% | 59.4% |

## Verdicts

| test | verdict | evidence |
|---|---|---|
| H22 | **supported** | placebo - base +0.5 [-6.7, +7.4] pts (n=64); hint - placebo +39.5 [+30.0, +49.2] pts (n=64) |
| H23 | **inconclusive** | notice - base +18.7 [+10.0, +27.6] pts (n=64) |
| H10 | **supported** | hint - as built +46.9 [+30.1, +63.0] pts; evidence - as built +49.7 [+33.3, +66.1] pts (n=25); larger: evidence; hint arm 96.0%; headline is a prompting effect |

- **H22** rule: Supported if placebo - base < 5 points AND hint - placebo >= 20 points with a CI that excludes 0. Rejected if placebo - base >= 20 points. Otherwise inconclusive.
- **H23** rule: Supported if notice - base >= 20 points with a CI that excludes 0. Rejected if notice - base < 5 points (and the CI upper end < 10). Otherwise inconclusive.
- **H10** rule: Supported if (c) evidence - (a) as built >= 20 points and (b) hint - (a) >= 20 points (report which is larger); add the note "headline is a prompting effect" if (b) reaches 90%. Otherwise not supported.

## Case 3 contrasts at low (paired by task)

| contrast | paired tasks | x pass | y pass | pass diff [CI] | full-repair diff [CI] |
|---|---|---|---|---|---|
| placebo - base | 64 | 32.1% | 31.6% | +0.5 [-6.7, +7.4] | +0.0 [-6.2, +6.2] |
| notice - base | 64 | 50.3% | 31.6% | +18.7 [+10.0, +27.6] | +12.5 [+3.1, +21.9] |
| hint - notice | 64 | 71.6% | 50.3% | +21.3 [+10.6, +32.1] | +28.1 [+14.1, +40.6] |
| hint - placebo | 64 | 71.6% | 32.1% | +39.5 [+30.0, +49.2] | +40.6 [+28.1, +53.1] |

## Repeat runs (r1 = existing low step, r2 = repeat)

| arm | tasks | mean r1 | mean r2 | r2 - r1 [CI] | flip rate (full repair) | mean abs diff (pts) | pass^2 vs pass@1 |
|---|---|---|---|---|---|---|---|
| base | 64 | 31.6% | 32.1% | +0.5 [-8.0, +8.7] | 9.4% | 16.5 | 12.5% vs 17.2% |
| hint | 64 | 71.6% | 70.5% | -1.1 [-10.7, +8.1] | 26.6% | 21.3 | 45.3% vs 58.6% |

### Repeat runs at high (run-to-run stability; the per-task mean of these runs is the high score)

| series | tasks x runs | mean r1 | mean r2 | mean r3 | r2 - r1 [CI] | r3 - r1 [CI] | per-task SD (pts) | mean abs diff (pts) | flip rate (full repair) |
|---|---|---|---|---|---|---|---|---|---|
| case 3 base | 64 x 3 | 42.7% | 47.1% | 48.2% | +4.3 [-5.1, +14.2] | +5.4 [-4.0, +14.8] | 15.9 | 18.6 | 14.6% |
| case 3 hint | 64 x 3 | 90.6% | 85.0% | 87.5% | -5.6 [-12.9, +1.3] | -3.1 [-9.7, +3.1] | 9.7 | 11.2 | 14.6% |
| case 1 Tier B (as built) | 50 x 3 | 78.0% | 70.0% | 75.4% | -8.0 [-20.0, +2.0] | -2.6 [-11.1, +5.7] | 9.6 | 11.0 | 13.3% |

## Pass share by operation type, low (descriptive, no verdict)

Pass share of the hidden tests per group (reads = list/get, writes = create/update, delete), from each cell's saved grade record; mean over the tasks that have tests of the group. Reads depend only on return-shape translation.

| arm | reads (tasks) | writes (tasks) | delete (tasks) |
|---|---|---|---|
| base | 27.0% (64) | 17.2% (64) | 76.0% (64) |
| placebo | 28.1% (64) | 17.2% (64) | 76.0% (64) |
| notice | 59.6% (64) | 31.2% (64) | 72.9% (64) |
| hint | 80.1% (64) | 63.8% (64) | 69.8% (64) |

| contrast on reads only | paired tasks | diff [CI] |
|---|---|---|
| notice - base | 64 | +32.6 [+18.5, +46.2] |
| placebo - base | 64 | +1.1 [-8.5, +10.7] |

## H10: hidden-header Tier B tasks, high

| arm | runs per task | tasks finished | api_error rows (excluded) | pass rate |
|---|---|---|---|---|
| a | 3 | 25/25 | 0 | 49.1% |
| b | 1 | 25/25 | 0 | 96.0% |
| c | 1 | 25/25 | 0 | 98.9% |

a = as built (`results/tierb50_v1.csv` high and the repeats: mean of 3 runs per task), b = `__hint`, c = `__evidence` (1 run each).

b - a +46.9 [+30.1, +63.0]; c - a +49.7 [+33.3, +66.1]; larger: evidence.

## Hidden header on vs off, Tier B (descriptive)

Tasks with `hidden_header` on vs off in `tierb50`; the arm as built (no hint, no evidence). High = mean of 3 runs per task; low and medium = 1 run.

| effort | tasks on | tasks off | pass on | pass off | on - off [CI, independent groups] | api_error rows |
|---|---|---|---|---|---|---|
| low | 25 | 25 | 20.0% | 94.3% | -74.3 [-90.3, -54.9] | 0 |
| medium | 25 | 25 | 31.4% | 96.0% | -64.6 [-84.0, -44.0] | 0 |
| high | 25 | 25 | 49.1% | 99.8% | -50.7 [-67.6, -34.5] | 0 |

## Reading rules

- Unit = task; 1 run per cell, except high effort in the H10 as-built arm and the header table (mean of 3 runs per task). Points = percentage points of `pass_rate`. `api_error` rows are not model results: left out and counted.
- A step is used only when every task has a row (`finished >= expected`); a partial step is shown and not used.
- "CI excludes 0" means the 95% paired-bootstrap lower end is above 0. Thresholds on a mean are tested on the point estimate.
- Full repair = a task with pass_rate 1.0. Flip rate = share of tasks whose full-repair status differs between r1 and r2.
- If the mean absolute per-task difference between r1 and r2 is above 5 points, single-run differences below that size are within noise.
- Repeat runs at high: per-task SD = sample standard deviation of a task's pass rate over its runs, in points, averaged over tasks; mean abs diff and flip rate are averaged over the run pairs (r1-r2, r1-r3, r2-r3).
- Header on/off is descriptive context: independent groups of tasks (hidden_header on vs off in `runs/tierb50`), no verdict.

