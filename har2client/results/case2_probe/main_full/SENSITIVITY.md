# Case 2: stray closing `"""` in submitted code (post hoc, exploratory)

Found after the main run, so this is not a pre-registered analysis. The primary results in `REPORT.md` are unchanged.

In tasks 20-49 (the cells run on the second machine, whose client files are kept), 21 submitted clients do not
parse only because Beam ended the `submit_client` code with a stray `"""`. Placebo: 15 of 89 clients; P5: 3 of 90; P20: 3 of 90.
`sensitivity_trailing_quotes.py` removes that last `"""` when that alone makes the file parse, then grades it again
with the frozen grader. Per-cell results: `sensitivity_trailing_quotes.json`. Client files for tasks 00-19 are not in
this repository, so this check covers 30 of 50 tasks.

P20 minus placebo, pass rate in points, tasks 20-49:

| Effort | Hidden header | As run | Stray `"""` removed | Placebo as run → removed |
|---|---|---:|---:|---|
| low | on | +29.5 | +29.5 | 18.8 → 18.8 |
| low | off | +14.3 | +14.3 | 76.5 → 82.7 |
| medium | on | +73.2 | +73.2 | 12.5 → 12.5 |
| medium | off | +19.4 | +12.2 | 65.3 → 86.7 |
| high | on | +68.8 | +62.5 | 25.0 → 31.2 |
| high | off | +29.6 | +15.3 | 70.4 → 84.7 |

Reading: the large header-on gain does not depend on this error. About half of the header-off gain at medium and high
comes from it: the placebo arm writes this broken ending about five times as often as the probing arms. The header-off
gain that is left (+12 to +15) is still above the 5-point bound of H11.
