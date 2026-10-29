# Case 2: Beam probing evaluation

Observed rows: 18. Observed task/run blocks: 2.
Complete blocks by effort: low 2/2, medium 2/2, high 2/2.
Blocks complete at every plotted effort (low, medium, high): 2/2 (frontier).
All observed efforts have at least one complete block.
Statuses: {'ok': 18}

Paired comparisons use all three arms for the same task/run/effort; a missing cell removes only that effort's block.
The frontier plot uses only blocks complete at every plotted effort.
No-code/no-submit model outcomes count as zero. Intervals resample tasks, not individual tests.
Completion tokens already include reasoning; do not add reasoning tokens again.

| Effort | Arm | Tasks | Pass (%) | Strict (%) | Probes | Output tokens | Time (s) |
|---|---|---:|---:|---:|---:|---:|---:|
| low | placebo | 2 | 0.00 | 0.00 | 0.00 | 1058 | 6.56 |
| low | P5 | 2 | 100.00 | 100.00 | 1.00 | 1330 | 7.85 |
| low | P20 | 2 | 50.00 | 50.00 | 0.00 | 1054 | 5.02 |
| medium | placebo | 2 | 50.00 | 50.00 | 0.00 | 1748 | 9.91 |
| medium | P5 | 2 | 100.00 | 100.00 | 2.00 | 2824 | 17.16 |
| medium | P20 | 2 | 100.00 | 100.00 | 3.50 | 2567 | 18.66 |
| high | placebo | 2 | 50.00 | 50.00 | 0.00 | 3088 | 12.73 |
| high | P5 | 2 | 100.00 | 100.00 | 3.50 | 2955 | 18.95 |
| high | P20 | 2 | 100.00 | 100.00 | 5.50 | 4579 | 31.15 |

| Effort | Hidden header | Comparison | Tasks | Gain (pp) | 95% CI (pp) |
|---|---|---|---:|---:|---|
| low | all | P5 − placebo | 2 | 100.00 | [100.00, 100.00] |
| low | all | P20 − placebo | 2 | 50.00 | [0.00, 100.00] |
| low | on | P5 − placebo | 1 | 100.00 | [100.00, 100.00] |
| low | on | P20 − placebo | 1 | 0.00 | [0.00, 0.00] |
| low | off | P5 − placebo | 1 | 100.00 | [100.00, 100.00] |
| low | off | P20 − placebo | 1 | 100.00 | [100.00, 100.00] |
| medium | all | P5 − placebo | 2 | 50.00 | [0.00, 100.00] |
| medium | all | P20 − placebo | 2 | 50.00 | [0.00, 100.00] |
| medium | on | P5 − placebo | 1 | 100.00 | [100.00, 100.00] |
| medium | on | P20 − placebo | 1 | 100.00 | [100.00, 100.00] |
| medium | off | P5 − placebo | 1 | 0.00 | [0.00, 0.00] |
| medium | off | P20 − placebo | 1 | 0.00 | [0.00, 0.00] |
| high | all | P5 − placebo | 2 | 50.00 | [0.00, 100.00] |
| high | all | P20 − placebo | 2 | 50.00 | [0.00, 100.00] |
| high | on | P5 − placebo | 1 | 100.00 | [100.00, 100.00] |
| high | on | P20 − placebo | 1 | 100.00 | [100.00, 100.00] |
| high | off | P5 − placebo | 1 | 0.00 | [0.00, 0.00] |
| high | off | P20 − placebo | 1 | 0.00 | [0.00, 0.00] |

Plots: `frontier_output.png` and `frontier_total.png` (also SVG/JSON).
Both plots use the task/run blocks complete at every plotted effort.
