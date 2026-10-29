# Case 3 v3 evaluation suite: analysis

Steps present: 6 of 6. `api_error` rows excluded from every number: 0.

Runs per task: steps marked "mean of 3 runs" score each task as the mean over its runs (PREREG.md deviation 2026-10-27); every other step is 1 run per task. Their CIs are over tasks only.

## Steps

| arm | effort | state | tasks finished | api_error rows (excluded) |
|---|---|---|---|---|
| base | low | complete | 64/64 | 0 |
| base | medium | complete | 64/64 | 0 |
| base | high (mean of 3 runs) | complete | 64/64 | 0 |
| hint | low | complete | 64/64 | 0 |
| hint | medium | complete | 64/64 | 0 |
| hint | high (mean of 3 runs) | complete | 64/64 | 0 |

## Verdicts (pre-registered tests, PREREG.md)

| test | verdict | evidence |
|---|---|---|
| H16 | **supported** | low: hint - base +40.0 pts [95% CI +29.4, +50.1] (n=64); high (mean of 3 runs): hint - base +41.7 pts [95% CI +33.5, +49.8] (n=64) |
| H17 | **rejected** | base, high (mean of 3 runs) - low +14.4 pts [95% CI +8.3, +20.8] (n=64); output high/low = 4.41x |
| H18 (base) | **not tested** | not tested: xhigh dropped 2026-10-27, see PREREG.md deviations |
| H18 (hint) | **not tested** | not tested: xhigh dropped 2026-10-27, see PREREG.md deviations |
| H19 | **supported** | full-repair share, hint, high (mean of 3 runs): single 97.6% (n=14), release3 76.7% (n=10), release4 75.0% (n=8) |
| H20 | **inconclusive** | pass rate, hint, high (mean of 3 runs): partial - release3 -27.7 pts [95% CI -56.6, -1.1] (n=18) (n=8 vs 10); full-repair -30.8 pts; misleading - release3 +6.9 pts [95% CI -3.3, +16.6] (n=18) (n=8 vs 10); full-repair +6.7 pts; terse - release3 +8.1 pts [95% CI -2.8, +18.2] (n=18) (n=8 vs 10); full-repair +15.0 pts (terse is shown, not used) |
| H21 | **inconclusive** | pass rate, high (mean of 3 runs): base: sdk - release3 +58.6 pts [95% CI +36.7, +76.6] (n=18) (n=8 vs 10); full-repair +68.3 pts; hint: sdk - release3 -3.4 pts [95% CI -28.0, +15.0] (n=18) (n=8 vs 10); full-repair +6.7 pts |
| saturation | **not triggered** | base-high (mean of 3 runs) 0.460 (threshold 0.85, n=64) [xhigh dropped 2026-10-27, see PREREG.md deviations: the xhigh clauses are not evaluated] |

- **H16** Beam fails to notice a silent interface break more than it fails to fix it. Test: hint - base, paired by task, at each effort. Supported if: >= 20 points at low and at high, CI excludes 0. Rejected if: < 5 points at low and at high.
- **H17** Effort does not fix noticing. Test: (high - low) in the base arm. Supported if: < 5 points, while high output >= 3x low. Rejected if: >= 10 points.
- **H18 (base)** Effort has diminishing returns on repair (H14 on v3; per arm). Test: not tested: xhigh dropped 2026-10-27, see PREREG.md deviations. Supported if: n/a. Rejected if: n/a.
- **H18 (hint)** Effort has diminishing returns on repair (H14 on v3; per arm). Test: not tested: xhigh dropped 2026-10-27, see PREREG.md deviations. Supported if: n/a. Rejected if: n/a.
- **H19** More changes per release are harder (H15 on v3). Test: full-repair share by changes: single (1) vs release3 (3) vs release4 (4), hint arm, high. Supported if: strictly falls with the number of changes, release4 >= 15 points below single. Rejected if: release4 >= single.
- **H20** Missing or wrong side information costs more than more code (k = 3 families, hint arm, high). Test: partial, misleading, terse vs release3. Supported if: each of partial and misleading >= 10 points below release3. Rejected if: both within 3 points of release3.
- **H21** A layered SDK client is harder to repair than a compact one (k = 3). Test: sdk vs release3, both arms, high. Supported if: sdk >= 10 points below release3 in both arms. Rejected if: sdk >= release3 in both arms.
- **saturation** Saturation rule (added 2026-10-24 after the low-effort steps). Test: mean pass rate of the base arm at high (the xhigh clauses were dropped; xhigh dropped 2026-10-27, see PREREG.md deviations). Supported if: base arm >= 0.85 at high: the suite is reported as near saturation for Beam. Rejected if: base arm < 0.85 at high.

## Per arm and effort

| arm | effort | tasks scored | pass rate | strict pass rate | full repair | mean output tokens |
|---|---|---|---|---|---|---|
| base | low | 64 | 31.6% | 31.6% | 17.2% | 1,758 |
| base | medium | 64 | 37.0% | 37.0% | 21.9% | 2,922 |
| base | high (mean of 3 runs) | 64 | 46.0% | 45.9% | 30.2% | 7,746 |
| hint | low | 64 | 71.6% | 71.6% | 57.8% | 1,800 |
| hint | medium | 64 | 76.9% | 76.9% | 62.5% | 2,846 |
| hint | high (mean of 3 runs) | 64 | 87.7% | 87.7% | 80.7% | 6,824 |

## Hint - base (paired by task)

Paired by task; 95% CI by paired bootstrap over tasks (10,000 resamples, fixed seed 20261024); points.

| effort | paired tasks | base pass | hint pass | pass diff [CI] | strict diff [CI] | full-repair diff [CI] |
|---|---|---|---|---|---|---|
| low | 64 | 31.6% | 71.6% | +40.0 [+29.4, +50.1] | +40.0 [+29.5, +50.3] | +40.6 [+26.6, +54.7] |
| medium | 64 | 37.0% | 76.9% | +39.9 [+29.7, +50.1] | +39.9 [+29.8, +49.8] | +40.6 [+28.1, +53.1] |
| high (mean of 3 runs) | 64 | 46.0% | 87.7% | +41.7 [+33.5, +49.8] | +41.8 [+33.4, +50.2] | +50.5 [+40.1, +60.9] |

## Effort contrasts (paired by task, per arm)

| contrast | paired tasks | pass diff [CI] | full-repair diff [CI] | output tokens ratio |
|---|---|---|---|---|
| base: high (mean of 3 runs) - low | 64 | +14.4 [+8.3, +20.8] | +13.0 [+7.3, +19.3] | 4.41x |
| hint: high (mean of 3 runs) - low | 64 | +16.1 [+8.1, +24.8] | +22.9 [+12.5, +33.9] | 3.79x |

## Per family

| arm | effort | family | n | pass rate | strict | full repair | mean output tokens |
|---|---|---|---|---|---|---|---|
| base | low | single | 14 | 60.9% | 60.9% | 50.0% | 1,491 |
| base | low | release3 | 10 | 13.5% | 13.5% | 0.0% | 1,587 |
| base | low | release4 | 8 | 13.2% | 13.2% | 0.0% | 1,837 |
| base | low | partial | 8 | 24.3% | 24.3% | 0.0% | 1,632 |
| base | low | sdk | 8 | 55.1% | 55.1% | 50.0% | 2,708 |
| base | low | misleading | 8 | 18.4% | 18.4% | 0.0% | 1,773 |
| base | low | terse | 8 | 18.4% | 18.4% | 0.0% | 1,517 |
| base | medium | single | 14 | 73.5% | 73.5% | 64.3% | 2,538 |
| base | medium | release3 | 10 | 22.4% | 22.4% | 0.0% | 2,940 |
| base | medium | release4 | 8 | 19.9% | 19.9% | 0.0% | 3,064 |
| base | medium | partial | 8 | 18.4% | 18.4% | 0.0% | 2,312 |
| base | medium | sdk | 8 | 60.3% | 60.3% | 50.0% | 4,474 |
| base | medium | misleading | 8 | 23.5% | 23.5% | 12.5% | 2,741 |
| base | medium | terse | 8 | 17.6% | 17.6% | 0.0% | 2,666 |
| base | high (mean of 3 runs) | single | 14 | 77.5% | 77.5% | 71.4% | 6,966 |
| base | high (mean of 3 runs) | release3 | 10 | 22.7% | 22.7% | 6.7% | 8,063 |
| base | high (mean of 3 runs) | release4 | 8 | 32.8% | 32.8% | 12.5% | 8,136 |
| base | high (mean of 3 runs) | partial | 8 | 27.9% | 27.9% | 0.0% | 7,912 |
| base | high (mean of 3 runs) | sdk | 8 | 81.4% | 81.4% | 75.0% | 7,799 |
| base | high (mean of 3 runs) | misleading | 8 | 32.4% | 31.6% | 16.7% | 8,488 |
| base | high (mean of 3 runs) | terse | 8 | 29.4% | 29.4% | 4.2% | 7,363 |
| hint | low | single | 14 | 100.0% | 100.0% | 100.0% | 1,426 |
| hint | low | release3 | 10 | 54.7% | 54.7% | 40.0% | 1,552 |
| hint | low | release4 | 8 | 72.1% | 72.1% | 50.0% | 1,601 |
| hint | low | partial | 8 | 52.2% | 52.2% | 25.0% | 1,633 |
| hint | low | sdk | 8 | 77.9% | 77.9% | 62.5% | 3,110 |
| hint | low | misleading | 8 | 78.7% | 78.7% | 62.5% | 1,915 |
| hint | low | terse | 8 | 48.5% | 48.5% | 37.5% | 1,704 |
| hint | medium | single | 14 | 100.0% | 100.0% | 100.0% | 2,333 |
| hint | medium | release3 | 10 | 73.5% | 73.5% | 60.0% | 2,412 |
| hint | medium | release4 | 8 | 61.8% | 61.8% | 50.0% | 2,984 |
| hint | medium | partial | 8 | 51.5% | 51.5% | 25.0% | 2,474 |
| hint | medium | sdk | 8 | 68.4% | 68.4% | 62.5% | 4,574 |
| hint | medium | misleading | 8 | 82.4% | 82.4% | 50.0% | 3,087 |
| hint | medium | terse | 8 | 84.6% | 84.6% | 62.5% | 2,552 |
| hint | high (mean of 3 runs) | single | 14 | 98.0% | 98.0% | 97.6% | 6,239 |
| hint | high (mean of 3 runs) | release3 | 10 | 88.2% | 88.2% | 76.7% | 6,596 |
| hint | high (mean of 3 runs) | release4 | 8 | 83.1% | 83.1% | 75.0% | 6,972 |
| hint | high (mean of 3 runs) | partial | 8 | 60.5% | 60.5% | 45.8% | 6,875 |
| hint | high (mean of 3 runs) | sdk | 8 | 84.8% | 84.8% | 83.3% | 7,369 |
| hint | high (mean of 3 runs) | misleading | 8 | 95.1% | 95.1% | 83.3% | 7,721 |
| hint | high (mean of 3 runs) | terse | 8 | 96.3% | 96.3% | 91.7% | 6,490 |

## Per-change credit, by change kind

Per-change credit = pass share over the hidden tests whose `needs` list holds the change; mean over the tasks whose release holds it.

| change | base-low | base-medium | base-high (mean of 3 runs) | hint-low | hint-medium | hint-high (mean of 3 runs) |
|---|---|---|---|---|---|---|
| add_async_create | 22.2% (n=18) | 27.8% (n=18) | 33.3% (n=18) | 72.2% (n=18) | 66.7% (n=18) | 94.4% (n=18) |
| add_drift_cents | 22.2% (n=18) | 16.7% (n=18) | 28.6% (n=18) | 69.4% (n=18) | 84.5% (n=18) | 92.6% (n=18) |
| add_drift_enum | 10.0% (n=15) | 13.3% (n=15) | 30.3% (n=15) | 60.0% (n=15) | 77.6% (n=15) | 89.2% (n=15) |
| add_drift_epoch | 3.2% (n=18) | 14.3% (n=18) | 25.3% (n=18) | 71.4% (n=18) | 71.0% (n=18) | 86.5% (n=18) |
| add_drift_nest | 13.9% (n=18) | 19.4% (n=18) | 27.9% (n=18) | 74.6% (n=18) | 71.0% (n=18) | 80.7% (n=18) |
| add_drift_rename | 16.7% (n=15) | 18.6% (n=15) | 30.5% (n=15) | 61.9% (n=15) | 81.9% (n=15) | 90.8% (n=15) |
| add_idempotency_key | 28.3% (n=18) | 35.0% (n=18) | 40.9% (n=18) | 20.0% (n=18) | 33.3% (n=18) | 75.4% (n=18) |
| add_moved | 26.7% (n=16) | 28.4% (n=16) | 38.8% (n=16) | 83.5% (n=16) | 89.8% (n=16) | 92.2% (n=16) |
| add_rate_limit | 25.6% (n=18) | 36.5% (n=18) | 46.5% (n=18) | 57.1% (n=18) | 62.7% (n=18) | 79.8% (n=18) |
| add_refresh_rotation | 25.5% (n=18) | 35.6% (n=18) | 38.2% (n=18) | 62.1% (n=18) | 56.9% (n=18) | 81.4% (n=18) |

## Reading rules

- Unit = task; the per-task score is the mean over that task's runs. `api_error` rows are not model results and are left out.
- Runs per task: high = 3 runs (every number at high is a "mean of 3 runs" per task; PREREG.md deviation 2026-10-27), low = 1 run (the repeat runs are in `heal_controls`), medium = 1 run. H16, H17, H19-H21 and the saturation rule use these per-task means; a full-repair share at high is the mean over runs of the 0/1 full-repair flag.
- Points are percentage points of `pass_rate` (the primary metric) unless a row says full-repair share.
- H19 names the full-repair share. H20 and H21 name no metric, so they use the primary metric `pass_rate`; their full-repair contrasts are shown beside them and not used for the verdict. Their CIs (independent groups of tasks) are information only.
- "CI excludes 0" (H16) is a 95% paired bootstrap CI whose lower end is above 0, required at both low and high.
- "Both within 3 points" (H20) means |difference| <= 3 for partial and for misleading (terse is shown, not used).
- A threshold on a mean is tested on the point estimate; "not yet testable" means a required step is missing.
- A step that has not finished every task counts as not yet run.
- Saturation uses the mean pass rate (0-1) of base-high on its scored tasks; the xhigh clauses of the rule were dropped with xhigh (2026-10-27, see PREREG.md deviations).
- H18 (xhigh - high) is not tested: xhigh dropped 2026-10-27, see PREREG.md deviations.
