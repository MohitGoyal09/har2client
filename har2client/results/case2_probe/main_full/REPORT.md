# Case 2: Beam probing evaluation

Observed rows: 450. Observed task/run blocks: 50.
Complete blocks by effort: low 50/50, medium 50/50, high 50/50.
Blocks complete at every plotted effort (low, medium, high): 50/50 (frontier).
All observed efforts have at least one complete block.
Statuses: {'ok': 449, 'no_code': 1}

Paired comparisons use all three arms for the same task/run/effort; a missing cell removes only that effort's block.
The frontier plot uses only blocks complete at every plotted effort.
No-code/no-submit model outcomes count as zero. Intervals resample tasks, not individual tests.
Completion tokens already include reasoning; do not add reasoning tokens again.

| Effort | Arm | Tasks | Pass (%) | Strict (%) | Probes | Output tokens | Time (s) |
|---|---|---:|---:|---:|---:|---:|---:|
| low | placebo | 50 | 53.14 | 53.14 | 0.00 | 1046 | 7.34 |
| low | P5 | 50 | 64.86 | 64.86 | 1.30 | 1046 | 7.78 |
| low | P20 | 50 | 78.57 | 78.57 | 1.98 | 1054 | 8.78 |
| medium | placebo | 50 | 46.29 | 46.29 | 0.00 | 2283 | 12.60 |
| medium | P5 | 50 | 87.71 | 87.71 | 2.58 | 2110 | 14.39 |
| medium | P20 | 50 | 87.14 | 87.14 | 3.84 | 2214 | 16.26 |
| high | placebo | 50 | 49.71 | 49.71 | 0.00 | 5634 | 25.33 |
| high | P5 | 50 | 95.71 | 95.71 | 3.76 | 4968 | 31.74 |
| high | P20 | 50 | 98.00 | 98.00 | 7.78 | 5262 | 40.05 |

| Effort | Hidden header | Comparison | Tasks | Gain (pp) | 95% CI (pp) |
|---|---|---|---:|---:|---|
| low | all | P5 − placebo | 50 | 11.71 | [-4.29, 27.43] |
| low | all | P20 − placebo | 50 | 25.43 | [11.43, 39.71] |
| low | on | P5 − placebo | 25 | 26.29 | [6.86, 46.29] |
| low | on | P20 − placebo | 25 | 38.86 | [16.00, 60.00] |
| low | off | P5 − placebo | 25 | -2.86 | [-27.43, 21.14] |
| low | off | P20 − placebo | 25 | 12.00 | [-4.57, 29.14] |
| medium | all | P5 − placebo | 50 | 41.43 | [28.29, 55.14] |
| medium | all | P20 − placebo | 50 | 40.86 | [24.00, 56.86] |
| medium | on | P5 − placebo | 25 | 62.29 | [43.43, 80.00] |
| medium | on | P20 − placebo | 25 | 70.86 | [52.00, 87.43] |
| medium | off | P5 − placebo | 25 | 20.57 | [5.71, 36.57] |
| medium | off | P20 − placebo | 25 | 10.86 | [-12.00, 33.14] |
| high | all | P5 − placebo | 50 | 46.00 | [29.71, 60.86] |
| high | all | P20 − placebo | 50 | 48.29 | [32.86, 62.57] |
| high | on | P5 − placebo | 25 | 67.43 | [47.43, 84.00] |
| high | on | P20 − placebo | 25 | 64.00 | [40.00, 84.00] |
| high | off | P5 − placebo | 25 | 24.57 | [0.57, 48.00] |
| high | off | P20 − placebo | 25 | 32.57 | [16.00, 50.29] |

Plots: `frontier_output.png` and `frontier_total.png` (also SVG/JSON).
Both plots use the task/run blocks complete at every plotted effort.
