# Case 2 live evaluation: isolated execution protocol

This protocol records the submission interface, evaluation settings, and
interpretation rules fixed before the main evaluation.

## Original pilot (diagnostic only)

`runs/probe/case2_initial_pilot`: two tasks, placebo/P5/P20, low/high, one run.
All four placebo responses had null final content and no tool calls despite
nonzero reasoning and output usage. One extra raw-response diagnostic confirmed
`finish_reason: stop` with no content, tool calls, or legacy function_call.
The cause of the provider/model behavior is not established. These outcomes
are retained; they are not the baseline for the revised evaluation.

## Revised protocol: submission-tool-all-arms-v1

Every arm gets `submit_client(code)` as its output channel. Only P5/P20 receive
`http_request`; placebo has no HTTP tool and no running practice instance.
The prompt text is unchanged; only the probe budget differs between arms.
This controls the submission interface while varying HTTP interaction access.
No instruction forces the model to use probes. Actual probe use is reported.

Runtime and task files were frozen in an archive (`runtime_source.zip`, see
`REPRODUCE.md`) for reproducible execution. The runtime/task SHA256
is recorded in each output's `protocol.json`; a changed snapshot cannot resume
into the same output. Credentials are read from a local `.env` and
are not copied into the snapshot or outputs.

The isolated patch also logs raw response messages and finish reasons for each
turn. Two local protocol tests verified identical submission access across all
arms and no HTTP tool/request execution in placebo.

## Stages and interpretation

- Revised pilot: two tasks x three arms x low/medium/high x one run = 18 cells.
  Its scores are pilot data and excluded from main summaries.
- Main comparison: 50 existing Tier B tasks x three arms x low/medium/high x
  one run = 450 cells. Use the core effort levels first; repetition
  and xhigh/max are deferred. Stop cleanly at the provider's daily limit and retain resumable
  cells. Do not claim a completed benchmark from partial coverage.
- Hidden tests and fresh-server grading are unchanged. Client code runs locally;
  Beam inference uses Reflection's API.
- Output tokens include reasoning; do not add reasoning tokens a second time.
- Analysis is paired across complete task/run blocks. With one run per cell,
  uncertainty estimates describe variation over tasks, not repeated-run variance.
- No results from the old submission protocol are pooled with the revised one.
- Original Case 1 results are a historical reference, not a perfectly matched
  control for the revised Case 2 prompt/submission interface.

## Locations

- Original pilot: `runs/probe/case2_initial_pilot/`.
- Revised pilot: `runs/probe/case2_probe_pilot/`.
- Main evaluation: `runs/probe/case2_probe_main/`.
- Analysis: `results/case2_probe/pilot/` and `results/case2_probe/main/`.
- Frozen runtime: `runtime_source.zip` (see `REPRODUCE.md`).

This is model evaluation; no RL training was performed.
