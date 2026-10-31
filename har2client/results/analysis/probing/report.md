# Case 2 probing: does Beam test its hypotheses?

Beam Case 2, arms placebo (no probes) and P5 (up to 5 probes), 1 run per task, 50 Tier B tasks of which 25 hide a required header (`hidden_header` on). Pass rate = mean over tasks of the share of hidden tests passed, in percent (the CSV scale is 0 to 1). CI = 95% percentile bootstrap over tasks (10,000 resamples, seed 20261024). Probe columns are mean counts per task. `tested header` = the model sent a header-ablation probe (`ablated_hidden_header`), counted on hidden-header tasks only. A missing code submission counts as 0.

## Beam by effort

| effort | arm | tasks | n | pass rate [95% CI] | probes used | header ablation | auth test | paging test | etag test | other | refused | tested header | invented endpoint >0 | hardcoded secret | mean completion tokens |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| low | placebo | hidden header on | 25 | 24.0% [8.0, 40.0] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0/25 (0%) | 0 | 0 | 1,021 |
| low | placebo | hidden header off | 25 | 82.3% [66.9, 94.9] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | - | 0 | 0 | 1,070 |
| low | placebo | hidden header all | 50 | 53.1% [39.4, 66.6] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | - | 0 | 0 | 1,046 |
| low | P5 | hidden header on | 25 | 50.3% [31.4, 69.1] | 1.44 | 0.32 | 0.76 | 0.08 | 0.00 | 0.28 | 0.04 | 4/25 (16%) | 0 | 0 | 1,147 |
| low | P5 | hidden header off | 25 | 79.4% [63.4, 94.3] | 1.16 | 0.24 | 0.32 | 0.40 | 0.00 | 0.20 | 0.00 | - | 0 | 0 | 944 |
| low | P5 | hidden header all | 50 | 64.9% [51.1, 77.1] | 1.30 | 0.28 | 0.54 | 0.24 | 0.00 | 0.24 | 0.02 | - | 0 | 0 | 1,046 |
| medium | placebo | hidden header on | 25 | 16.0% [4.0, 32.0] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0/25 (0%) | 0 | 0 | 2,234 |
| medium | placebo | hidden header off | 25 | 76.6% [60.0, 91.4] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | - | 0 | 0 | 2,331 |
| medium | placebo | hidden header all | 50 | 46.3% [32.9, 59.7] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | - | 0 | 0 | 2,283 |
| medium | P5 | hidden header on | 25 | 78.3% [61.7, 92.6] | 2.68 | 0.84 | 0.64 | 0.36 | 0.00 | 0.84 | 0.12 | 10/25 (40%) | 0 | 0 | 2,120 |
| medium | P5 | hidden header off | 25 | 97.1% [94.3, 99.4] | 2.48 | 0.28 | 0.68 | 0.76 | 0.00 | 0.76 | 0.08 | - | 1 | 0 | 2,099 |
| medium | P5 | hidden header all | 50 | 87.7% [78.9, 95.4] | 2.58 | 0.56 | 0.66 | 0.56 | 0.00 | 0.80 | 0.10 | - | 1 | 0 | 2,110 |
| high | placebo | hidden header on | 25 | 32.0% [16.0, 52.0] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0/25 (0%) | 0 | 0 | 6,776 |
| high | placebo | hidden header off | 25 | 67.4% [48.0, 84.0] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | - | 0 | 0 | 4,491 |
| high | placebo | hidden header all | 50 | 49.7% [35.7, 63.7] | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | - | 0 | 0 | 5,634 |
| high | P5 | hidden header on | 25 | 99.4% [98.3, 100.0] | 3.88 | 1.48 | 0.68 | 0.20 | 0.12 | 1.40 | 0.32 | 17/25 (68%) | 0 | 0 | 5,037 |
| high | P5 | hidden header off | 25 | 92.0% [80.0, 100.0] | 3.64 | 0.44 | 0.76 | 0.96 | 0.08 | 1.40 | 0.24 | - | 0 | 0 | 4,899 |
| high | P5 | hidden header all | 50 | 95.7% [89.7, 100.0] | 3.76 | 0.96 | 0.72 | 0.58 | 0.10 | 1.40 | 0.28 | - | 0 | 0 | 4,968 |

## Other models

These files lack: `hidden_header`, `probes_used`, `probes_header_ablation`, `probes_auth_test`, `probes_paging_test`, `probes_etag_test`, `probes_other`, `probes_refused`, `ablated_hidden_header`. The hidden-header flag is joined by task name from the Beam file (same 50 tasks). Probe counts and the header-test flag are not recorded, so only pass rate, tokens and the two flag counts are compared, and no tested-vs-not-tested split is possible for these models.

| model | effort | arm | tasks | n | pass rate [95% CI] | invented endpoint >0 | hardcoded secret | mean completion tokens |
|---|---|---|---|---:|---:|---:|---:|---:|
| GPT-OSS-120B | high | placebo | hidden header on | 25 | 45.1% [26.3, 64.0] | 0 | 0 | 5,164 |
| GPT-OSS-120B | high | placebo | hidden header off | 25 | 93.7% [84.6, 98.9] | 0 | 0 | 5,640 |
| GPT-OSS-120B | high | placebo | hidden header all | 50 | 69.4% [56.6, 81.4] | 0 | 0 | 5,402 |
| GPT-OSS-120B | high | P5 | hidden header on | 25 | 66.3% [47.4, 83.4] | 0 | 0 | 6,113 |
| GPT-OSS-120B | high | P5 | hidden header off | 25 | 98.3% [95.4, 100.0] | 0 | 0 | 5,693 |
| GPT-OSS-120B | high | P5 | hidden header all | 50 | 82.3% [71.7, 91.4] | 0 | 0 | 5,903 |
| GPT-OSS-120B | low | placebo | hidden header on | 25 | 30.9% [14.3, 49.1] | 3 | 0 | 2,202 |
| GPT-OSS-120B | low | placebo | hidden header off | 25 | 69.1% [51.4, 85.1] | 4 | 0 | 2,200 |
| GPT-OSS-120B | low | placebo | hidden header all | 50 | 50.0% [36.9, 63.4] | 7 | 0 | 2,201 |
| GPT-OSS-120B | low | P5 | hidden header on | 25 | 31.4% [15.4, 50.3] | 6 | 0 | 2,269 |
| GPT-OSS-120B | low | P5 | hidden header off | 25 | 93.7% [84.6, 98.9] | 0 | 0 | 2,324 |
| GPT-OSS-120B | low | P5 | hidden header all | 50 | 62.6% [49.4, 75.4] | 6 | 0 | 2,297 |
| GPT-OSS-120B | medium | placebo | hidden header on | 25 | 32.6% [16.0, 50.9] | 4 | 0 | 2,060 |
| GPT-OSS-120B | medium | placebo | hidden header off | 25 | 80.6% [65.7, 92.6] | 4 | 0 | 2,181 |
| GPT-OSS-120B | medium | placebo | hidden header all | 50 | 56.6% [43.4, 69.4] | 8 | 0 | 2,120 |
| GPT-OSS-120B | medium | P5 | hidden header on | 25 | 37.7% [19.4, 56.6] | 2 | 0 | 2,321 |
| GPT-OSS-120B | medium | P5 | hidden header off | 25 | 77.1% [60.6, 91.4] | 4 | 0 | 2,137 |
| GPT-OSS-120B | medium | P5 | hidden header all | 50 | 57.4% [44.3, 70.3] | 6 | 0 | 2,229 |
| Qwen3.5-397B-A17B | thinking | placebo | hidden header on | 25 | 8.0% [0.0, 20.0] | 0 | 0 | 4,175 |
| Qwen3.5-397B-A17B | thinking | placebo | hidden header off | 25 | 72.0% [55.4, 86.3] | 1 | 0 | 3,955 |
| Qwen3.5-397B-A17B | thinking | placebo | hidden header all | 50 | 40.0% [27.4, 53.1] | 1 | 0 | 4,065 |
| Qwen3.5-397B-A17B | thinking | P5 | hidden header on | 25 | 29.1% [13.1, 46.9] | 2 | 0 | 4,142 |
| Qwen3.5-397B-A17B | thinking | P5 | hidden header off | 25 | 66.9% [49.7, 82.3] | 2 | 0 | 3,312 |
| Qwen3.5-397B-A17B | thinking | P5 | hidden header all | 50 | 48.0% [34.9, 60.6] | 4 | 0 | 3,727 |

## Pass rate when Beam tested the header, and when it did not

Beam, arm P5, hidden-header tasks. The placebo column of each group is the no-probe pass rate on the same tasks: tasks where Beam chose to test are not a random sample, so the placebo column shows how hard those tasks are without probing. Groups are small at low and medium effort: read the CIs.

| model | effort | tested the header: n | pass rate [95% CI] | placebo, same tasks | did not test: n | pass rate [95% CI] | placebo, same tasks |
|---|---|---:|---:|---:|---:|---:|---:|
| Beam | low | 4 | 96.4% [89.3, 100.0] | 0.0% | 21 | 41.5% [22.4, 61.9] | 28.6% |
| Beam | medium | 10 | 100.0% [100.0, 100.0] | 0.0% | 15 | 63.8% [39.0, 85.7] | 26.7% |
| Beam | high | 17 | 100.0% [100.0, 100.0] | 5.9% | 8 | 98.2% [94.6, 100.0] | 87.5% |

## Failure kinds (Beam, Case 1 and Case 3)

Share of failing hidden tests by kind: the `fail_*` columns summed over tasks (over the 3 runs at high effort on these cases), divided by the total.

### case1

| effort | runs | failed tests | 400 bad request | code raised an exception | timeout | 428 precondition | 401 auth |
|---|---:|---:|---:|---:|---:|---:|---:|
| low | 1 | 150 | 65.3% | 28.0% | 4.7% | 2.0% | 0.0% |
| medium | 1 | 127 | 82.7% | 11.0% | 5.5% | 0.8% | 0.0% |
| high | 3 | 268 | 96.6% | 2.6% | 0.0% | 0.4% | 0.4% |

### case3_base

| effort | runs | failed tests | wrong return value | 400 bad request | wrong server state | 429 rate limited | code raised an exception | incomplete list | timeout | 404 not found | 428 precondition | 401 auth |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 1 | 744 | 40.5% | 25.1% | 14.2% | 10.9% | 2.4% | 1.5% | 2.2% | 2.4% | 0.5% | 0.3% |
| medium | 1 | 685 | 46.0% | 25.4% | 16.4% | 5.1% | 0.3% | 1.3% | 2.3% | 2.9% | - | 0.3% |
| high | 3 | 1746 | 49.0% | 25.2% | 17.5% | 3.2% | 1.9% | 1.7% | 0.9% | 0.2% | 0.2% | 0.1% |

### case3_hint

| effort | runs | failed tests | 400 bad request | 429 rate limited | wrong return value | 404 not found | wrong server state | code raised an exception | 401 auth | incomplete list |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 1 | 309 | 42.4% | 25.2% | 22.3% | 5.8% | 1.3% | 0.0% | 1.6% | 1.3% |
| medium | 1 | 251 | 41.0% | 40.6% | 6.8% | 9.2% | 1.6% | 0.4% | 0.4% | - |
| high | 3 | 350 | 28.0% | 31.4% | 30.3% | 0.9% | 3.4% | 4.9% | 0.6% | 0.6% |
