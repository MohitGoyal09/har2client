# Contributing

Issues and pull requests are welcome at https://github.com/MohitGoyal09/har2client.

## Run the tests

```bash
cd har2client
uv sync
uv run pytest -q tests            # benchmark code
uv run pytest -q ../release/tests # release kit
uvx ruff check src scripts tests
```

## Add a task

- Tier A (real public API): put an OpenAPI 3.0 spec in `har2client/data/specs/` and run `uv run har2client build SPEC --api-id ID --out runs/mytasks`.
- Tier B (invented stateful API): add a config to the Tier B builder (`har2client/scripts/build_tierb.py`, `--configs`).
- Case 3 (repair): see `har2client/scripts/build_heal_v3.py`.

Every task must pass its reference client with `uv run har2client ref TASK_DIR` and `uv run har2client grade TASK_DIR TASK_DIR/ref_client.py` (pass_rate 1.000). The developer guide is `har2client/README.md`.

## Rules

- Never put a hidden test file, credential or seed into a visible task file. `release/check_no_leak.py` checks this.
- Do not commit secrets or `.env` files.
- Contributions are under Apache-2.0 (code) and CC-BY-4.0 (data).
