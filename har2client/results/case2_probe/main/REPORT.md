# Case 2: Beam probing evaluation

Observed rows: 187. Observed task/run blocks: 21.
Complete blocks by effort: low 21/21, medium 21/21, high 20/21.
Blocks complete at every plotted effort (low, medium, high): 20/21 (frontier).
All observed efforts have at least one complete block.
Statuses: {'ok': 187}

Paired comparisons use all three arms for the same task/run/effort; a missing cell removes only that effort's block.
The frontier plot uses only blocks complete at every plotted effort.
No-code/no-submit model outcomes count as zero. Intervals resample tasks, not individual tests.
Completion tokens already include reasoning; do not add reasoning tokens again.

| Effort | Arm | Tasks | Pass (%) | Strict (%) | Probes | Output tokens | Time (s) |
|---|---|---:|---:|---:|---:|---:|---:|
| low | placebo | 21 | 61.22 | 61.22 | 0.00 | 964 | 5.23 |
| low | P5 | 21 | 70.75 | 70.75 | 0.71 | 946 | 5.68 |
| low | P20 | 21 | 94.56 | 94.56 | 1.57 | 1035 | 7.47 |
| medium | placebo | 21 | 57.14 | 57.14 | 0.00 | 2138 | 10.96 |
| medium | P5 | 21 | 83.67 | 83.67 | 1.86 | 2028 | 12.67 |
| medium | P20 | 21 | 90.48 | 90.48 | 2.81 | 2086 | 13.37 |
| high | placebo | 20 | 55.00 | 55.00 | 0.00 | 3759 | 17.98 |
| high | P5 | 20 | 95.00 | 95.00 | 3.30 | 4213 | 25.64 |
| high | P20 | 20 | 100.00 | 100.00 | 5.95 | 4374 | 29.28 |

| Effort | Hidden header | Comparison | Tasks | Gain (pp) | 95% CI (pp) |
|---|---|---|---:|---:|---|
| low | all | P5 − placebo | 21 | 9.52 | [-15.65, 36.73] |
| low | all | P20 − placebo | 21 | 33.33 | [14.29, 52.38] |
| low | on | P5 − placebo | 9 | 22.22 | [-22.22, 66.67] |
| low | on | P20 − placebo | 9 | 55.56 | [22.22, 88.89] |
| low | off | P5 − placebo | 12 | 0.00 | [-33.33, 33.33] |
| low | off | P20 − placebo | 12 | 16.67 | [0.00, 41.67] |
| medium | all | P5 − placebo | 21 | 26.53 | [8.16, 46.26] |
| medium | all | P20 − placebo | 21 | 33.33 | [9.52, 57.14] |
| medium | on | P5 − placebo | 9 | 44.44 | [11.11, 77.78] |
| medium | on | P20 − placebo | 9 | 66.67 | [33.33, 100.00] |
| medium | off | P5 − placebo | 12 | 13.10 | [-4.76, 36.90] |
| medium | off | P20 − placebo | 12 | 8.33 | [-16.67, 33.33] |
| high | all | P5 − placebo | 20 | 40.00 | [15.00, 65.00] |
| high | all | P20 − placebo | 20 | 45.00 | [25.00, 65.00] |
| high | on | P5 − placebo | 9 | 55.56 | [22.22, 88.89] |
| high | on | P20 − placebo | 9 | 55.56 | [22.22, 88.89] |
| high | off | P5 − placebo | 11 | 27.27 | [-9.09, 63.64] |
| high | off | P20 − placebo | 11 | 36.36 | [9.09, 63.64] |

Plots: `frontier_output.png` and `frontier_total.png` (also SVG/JSON).
Both plots use the task/run blocks complete at every plotted effort.
