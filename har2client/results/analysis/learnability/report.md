# Is the reward learnable?

This report checks, from evaluation data that already exists, whether the HAR2Client reward can drive RL. No model was called and nothing was trained.

The reward of one rollout is the share of hidden tests it passes, from 0 to 1. The data are 7 settings (Beam low, medium, high; GPT-OSS-120B low, medium, high; Qwen3.5-397B thinking on) on 5 benchmarks (n = 50, 50, 50, 64, 64 tasks, in the order below). Beam high has 3 runs per task on Case 1, Case 2 with 5 probes and both Case 3 arms. Every other cell has 1 run. Intervals in square brackets are 95% bootstrap CIs over tasks.

Rebuilt pass rates match the leaderboard (`results/analysis/model_comparison/summary.json`) in all 35 checked cells.

## 1. Dense reward

Share of rollouts with a reward strictly between 0 and 1. A reward that is mostly 0 or 1 is sparse. Partial credit gives RL a gradient. Each run is one rollout, so Beam high counts 3 times per task.

| Benchmark | Tasks | Rollouts | Partial credit (0 < r < 1) |
|---|---|---|---|
| Case 1: build | 50 | 450 | 5.3% [3.1, 7.6] |
| Case 2: no probes | 50 | 350 | 10.0% [6.0, 14.6] |
| Case 2: 5 probes | 50 | 450 | 9.6% [6.2, 13.3] |
| Case 3: repair, no hint | 64 | 576 | 62.2% [54.0, 70.1] |
| Case 3: repair, with hint | 64 | 576 | 31.2% [25.2, 37.7] |

Reward histogram, share of rollouts per bin (%):

| Benchmark | 0 | (0, 0.25] | (0.25, 0.5] | (0.5, 0.75] | (0.75, 1) | 1 |
|---|---|---|---|---|---|---|
| Case 1: build | 37.1 | 0.2 | 0.2 | 0.9 | 4.0 | 57.6 |
| Case 2: no probes | 45.1 | 0.9 | 0.6 | 1.7 | 6.9 | 44.9 |
| Case 2: 5 probes | 21.6 | 0.2 | 0.0 | 1.3 | 8.0 | 68.9 |
| Case 3: repair, no hint | 14.6 | 46.7 | 4.2 | 9.2 | 2.1 | 23.3 |
| Case 3: repair, with hint | 11.6 | 16.5 | 6.4 | 2.6 | 5.7 | 57.1 |

## 2. The reward separates policies

Pass rate per setting, best to worst. A setting that thinks harder should score higher.

**Case 1: build** (n = 50): Beam high 74.5 > GPT-OSS high 69.7 > Beam medium 63.7 > Beam low 57.1 > GPT-OSS medium 53.7 > GPT-OSS low 51.1 > Qwen3.5 thinking 36.0

**Case 2: no probes** (n = 50): GPT-OSS high 69.4 > GPT-OSS medium 56.6 > Beam low 53.1 > GPT-OSS low 50.0 > Beam high 49.7 > Beam medium 46.3 > Qwen3.5 thinking 40.0

**Case 2: 5 probes** (n = 50): Beam high 95.7 > Beam medium 87.7 > GPT-OSS high 82.3 > Beam low 64.9 > GPT-OSS low 62.6 > GPT-OSS medium 57.4 > Qwen3.5 thinking 48.0

**Case 3: repair, no hint** (n = 64): GPT-OSS high 58.5 > Beam high 46.0 > Beam medium 37.0 > GPT-OSS medium 34.4 > GPT-OSS low 32.7 > Beam low 31.6 > Qwen3.5 thinking 29.6

**Case 3: repair, with hint** (n = 64): Beam high 87.7 > Beam medium 76.9 > Beam low 71.6 > GPT-OSS high 57.8 > Qwen3.5 thinking 52.3 > GPT-OSS medium 50.4 > GPT-OSS low 47.7

Pass rate is monotone in effort (low <= medium <= high) in 8 of 10 model-benchmark curves. The exceptions are listed in the orderings above.

Spearman rank correlation between effort (low < medium < high) and task reward, within one model:

| Benchmark | Beam | GPT-OSS-120B |
|---|---|---|
| Case 1: build | 0.13 [0.06, 0.21] | 0.16 [0.05, 0.28] |
| Case 2: no probes | -0.02 [-0.14, 0.10] | 0.18 [0.07, 0.30] |
| Case 2: 5 probes | 0.29 [0.16, 0.42] | 0.18 [0.07, 0.29] |
| Case 3: repair, no hint | 0.25 [0.16, 0.35] | 0.28 [0.15, 0.40] |
| Case 3: repair, with hint | 0.14 [0.03, 0.25] | 0.11 [0.01, 0.22] |
| All 5 pooled | 0.13 [0.09, 0.17] | 0.18 [0.13, 0.22] |

The pooled CI resamples tasks inside each benchmark. Case 1 and Case 2 share tasks, and so do the two Case 3 arms, so the pooled interval is a little too narrow.

Pairwise win rate: share of tasks where setting A scores strictly higher than B. The rest are ties.

| Pair (A vs B) | Benchmark | A wins | B wins | Ties | A share of decided |
|---|---|---|---|---|---|
| Beam high vs Beam low | Case 1: build | 30.0% [18.0, 42.0] | 2.0% [0.0, 6.0] | 68.0% | 94% |
| Beam high vs Beam low | Case 2: no probes | 14.0% [6.0, 24.0] | 18.0% [8.0, 30.0] | 68.0% | 44% |
| Beam high vs Beam low | Case 2: 5 probes | 42.0% [28.0, 56.0] | 4.0% [0.0, 10.0] | 54.0% | 91% |
| Beam high vs Beam low | Case 3: repair, no hint | 46.9% [34.4, 59.4] | 10.9% [4.7, 18.8] | 42.2% | 81% |
| Beam high vs Beam low | Case 3: repair, with hint | 34.4% [23.4, 46.9] | 14.1% [6.2, 23.4] | 51.6% | 71% |
| GPT-OSS high vs GPT-OSS low | Case 1: build | 26.0% [14.0, 38.0] | 4.0% [0.0, 10.0] | 70.0% | 87% |
| GPT-OSS high vs GPT-OSS low | Case 2: no probes | 34.0% [22.0, 48.0] | 6.0% [0.0, 14.0] | 60.0% | 85% |
| GPT-OSS high vs GPT-OSS low | Case 2: 5 probes | 32.0% [20.0, 46.0] | 4.0% [0.0, 10.0] | 64.0% | 89% |
| GPT-OSS high vs GPT-OSS low | Case 3: repair, no hint | 53.1% [40.6, 65.6] | 10.9% [3.1, 18.8] | 35.9% | 83% |
| GPT-OSS high vs GPT-OSS low | Case 3: repair, with hint | 31.2% [20.3, 42.2] | 18.8% [9.4, 28.1] | 50.0% | 62% |
| Beam high vs Qwen3.5 | Case 1: build | 58.0% [44.0, 72.0] | 0.0% [0.0, 0.0] | 42.0% | 100% |
| Beam high vs Qwen3.5 | Case 2: no probes | 32.0% [20.0, 46.0] | 14.0% [6.0, 24.0] | 54.0% | 70% |
| Beam high vs Qwen3.5 | Case 2: 5 probes | 66.0% [52.0, 78.0] | 6.0% [0.0, 14.0] | 28.0% | 92% |
| Beam high vs Qwen3.5 | Case 3: repair, no hint | 50.0% [37.5, 62.5] | 15.6% [7.8, 25.0] | 34.4% | 76% |
| Beam high vs Qwen3.5 | Case 3: repair, with hint | 59.4% [46.9, 71.9] | 4.7% [0.0, 10.9] | 35.9% | 93% |

## 3. Not saturated, not impossible

Share of tasks that no setting solves (all 7 score 0), that every setting solves (all 7 score 1), and the middle. The middle is where a policy can still learn: some settings pass and some do not.

| Benchmark | Tasks | Solved by none | Solved by all | Middle (useful for learning) |
|---|---|---|---|---|
| Case 1: build | 50 | 18.0% (9) | 12.0% (6) | 70.0% [56.0, 82.0] (35) |
| Case 2: no probes | 50 | 20.0% (10) | 4.0% (2) | 76.0% [64.0, 88.0] (38) |
| Case 2: 5 probes | 50 | 0.0% (0) | 14.0% (7) | 86.0% [76.0, 94.0] (43) |
| Case 3: repair, no hint | 64 | 1.6% (1) | 3.1% (2) | 95.3% [89.1, 100.0] (61) |
| Case 3: repair, with hint | 64 | 3.1% (2) | 12.5% (8) | 84.4% [75.0, 92.2] (54) |

## 4. Within-policy variance (what GRPO needs)

GRPO compares samples drawn for the same task. If all samples get the same reward, the advantage is zero and that group teaches nothing. Beam high has 3 runs per task, so the share of tasks where the 3 runs differ is a proxy for groups with signal. Std is the sample standard deviation over the 3 runs. Case 2 with no probes has 1 run, so it is left out.

| Benchmark | Tasks | Runs differ (groups with signal) | Mean within-task std | No signal: all 0 / all 1 / same partial |
|---|---|---|---|---|
| Case 1: build | 50 | 20.0% [10.0, 32.0] | 0.096 | 9 / 31 / 0 |
| Case 2: 5 probes | 50 | 14.0% [6.0, 24.0] | 0.061 | 0 / 43 / 0 |
| Case 3: repair, no hint | 64 | 48.4% [35.9, 60.9] | 0.159 | 1 / 13 / 19 |
| Case 3: repair, with hint | 64 | 25.0% [14.1, 35.9] | 0.097 | 2 / 43 / 3 |
| All 4 pooled | 228 | 28.1% [22.4, 34.2] | 0.106 |  |

## 5. Headroom

Pass rate of the best setting on each benchmark. The gap to 100% is room left to learn.

| Benchmark | Best setting | Pass rate | Gap to 100 | Tasks it fully solves |
|---|---|---|---|---|
| Case 1: build | Beam high | 74.5% [63.2, 85.0] | 25.5 | 62.0% |
| Case 2: no probes | GPT-OSS high | 69.4% [56.9, 81.1] | 30.6 | 62.0% |
| Case 2: 5 probes | Beam high | 95.7% [91.9, 98.9] | 4.3 | 86.0% |
| Case 3: repair, no hint | GPT-OSS high | 58.5% [49.5, 68.0] | 41.5 | 35.9% |
| Case 3: repair, with hint | Beam high | 87.7% [81.6, 93.0] | 12.3 | 67.2% |

## What this means for RL

- Graded reward: partial credit is 5% to 62% of rollouts. It is high on Case 3 repair and low (5%, 10%, 10%) on Case 1 and Case 2, which are mostly 0 or 1.
- Separation: harder thinking helps, but the rank correlation is modest (pooled Spearman 0.13 for Beam, 0.18 for GPT-OSS) and effort is monotone in 8 of 10 model-benchmark curves.
- Useful middle: 70% to 95% of tasks are solved by some settings and not others, and the best setting is 4 to 41 points below 100%.
- GRPO groups: Beam high's 3 runs differ on 28% of tasks (22% to 34%). The other tasks would give a zero advantage with 3 samples.

Scope: this is evidence of a usable signal from evaluation data. It is not a training run, and it does not show that a policy improves when trained on this reward.
