# Reproduce the Case 2 submission protocol

`runtime_source.zip` preserves the exact source, scripts, and 50 synthetic task
directories used by the evaluation. It also includes the project dependency
lock, licence, and protocol note. No `.env` file or Reflection API key is included.

`reproducibility.json` records the archive SHA256, individual runtime/task file
hashes, the aggregate runtime hash, and installed dependency versions. Its
`runtime_sha256` must equal the value in the evaluation's `protocol.json`.

## Offline validation

Extract the archive into a new directory; do not overwrite an active checkout.
From that directory:

```text
uv sync --extra plots
uv run har2client grade tasks/tierb50/tierb_00_invoices tasks/tierb50/tierb_00_invoices/ref_client.py
```

The reference client should score 1.0. These are the frozen task files; no task
rebuilding is required to reproduce the evaluation inputs.

## Model evaluation

Configure `REFLECTION_API_KEY` locally, then run:

```text
uv run python scripts/run_probe_batch.py --tasks tasks/tierb50 --out runs/replication --arms placebo P5 P20 --effort low medium high --runs 1 --concurrency 2
uv run python scripts/analyze_probe.py --input runs/replication/results.csv --out results/replication
```

Every arm has the same `submit_client` tool. Only P5/P20 have `http_request`.
Placebo must use zero probes; P5/P20 must stay within their budgets. Re-running
the same command resumes saved cells. Use a new output for any protocol change.
Stochastic model outputs need not reproduce the exact measured scores.

`submission_protocol.patch` documents the small solver difference from the
shared checkout at the start of this run. The archive is the authoritative
source for exact execution, including logging changes. It does not depend on
the ignored local runtime directory remaining available.

The separate integrity audit checks frozen hashes, coverage, probe budgets,
score consistency, and token accounting. It does not substitute for reviewing
the statistical analysis or checking repeated-run reliability.
