# har2client: Hugging Face release kit

This folder builds the dataset repo (`<ns>/har2client`) and the leaderboard Space (`<ns>/har2client-leaderboard`).
Nothing is uploaded until you run `publish.sh` without `--dry-run`. Nothing here is committed by the scripts.

Decisions the scripts enforce:
- The code repository is public under Apache-2.0: https://github.com/MohitGoyal09/har2client. The card and the Space link to it and say how to run the grader. The Space data links file holds the dataset URL only; the GitHub link is written into the card and the Space text.
- The 3 Tier A tasks whose API spec has a restrictive licence are dropped (`--drop-spec-class restricted`, set in `publish.sh`). The 20 with an undeclared licence stay, with a note in the card. Tier A is 27 rows.
- Licence: code Apache-2.0 (the Space), data CC-BY-4.0.

## Files

| File | What it does |
|---|---|
| `publish.sh` | One command: placeholder guard, export, leak check, Croissant check, Space data sync, leak check, stage the Space, leak check of the staged Space, upload. `--dry-run` stops before the upload. |
| `publish_hf.py` | The upload step (called by `publish.sh`). `--dry-run` lists every file with its size. A real run checks the placeholder guard first. |
| `publish_guard.py` | Refuses a real publish while a placeholder marker (the words TBD or TODO, an angle-bracket code-repository tag, or the words Open decision) is in the card, `croissant.json` or the Space README (the citation is in the card). A dry run prints a warning that lists them. |
| `export_dataset.py` | Exports task dirs to `build/dataset/`: one `data.jsonl` per config, `README.md` (the card, with `<ns>` and the configs block filled), `croissant.json` (real sha256, repo id, today's date), `MANIFEST.json`, `CANARY.txt`, and `results/` (see below). Reads only the visible task files. |
| `DATASET_CARD.md` | The Hub README for the dataset: summary, How Beam was used, results at a glance, How to use, cases, grading, validity, results with caveats, model comparison, Bias, Limitations, Licence, citation, datasheet. The challenge rules ask for a description, the functionality and how Beam was used: they are in this file. |
| `croissant.json` | Croissant 1.0 template with RAI fields. The export fills sha256, repo id and `datePublished`. |
| `validate_croissant.py` | Offline Croissant check (`--strict` also fails on unfilled placeholders). Runs the official `mlcroissant` too if it is installed. |
| `check_no_leak.py` | Fails if the dataset (rows, card, Croissant, `results/` files, Space data) holds hidden test values, test credentials, seeds, reference-client lines, hidden file names or secret-looking strings. Also checks columns and the canary. |
| `sync_space_data.py` | Writes `space/data/` (generated, git-ignored): exact copies of the `har2client/results` CSVs, the v3 floor file, the Tier B regression, the v3 analysis summary, the grader audit, the model comparison CSVs (without `probe20`), Beam's case 2 CSV, the comparison summary, the Findings figures (the 7 `final_*` and the 6 `story_*`), and `tasks.json` (goal, request table, excerpt for Tier A, Tier B and both v3 arms). |
| `space/` | Gradio app (Leaderboard, Explore a task, Case 3, Model comparison, Findings, About). `tables.py` and `stats.py` compute every number from the files in `space/data/`. `requirements.txt` pins `gradio==6.30.0` (tested on Python 3.12). |
| `h2c_release.py` | Shared paths, the list of `results/` files and figures, the Space figure list. |
| `CANARY.txt` | The canary GUID (in every row and in the card). |
| `tests/` | Unit tests for the export, results copy, dropped specs, leak check, guard, sync, Space numbers, the model comparison and the card wording (`test_submission_release.py`). |

Generated, not tracked: `build/`, `space/data/` (both in `.gitignore`; rebuild them with the scripts).

## What the dataset repo holds

Configs: `tier_a` (27 rows), `tier_b` (50), `heal_v3` (64), `heal_v3_hint` (64). `probe` and `heal` are placeholders with no rows and are not listed in the card header. Case 2 has no config of its own: it uses the same 50 rows as `tier_b`, and its results are in `results/case2_probe/`.
`results/` (copied from `har2client/results/`; exact copies except one wording change recorded in `RESULTS_REWRITES` in `h2c_release.py`):
- CSVs: `set30_v1_tierA.csv`, `tierb50_v1.csv`, `tierb50_medium.csv`, and the six `heal_v3_eval_{base,hint}_{low,medium,high}.csv`; plus `set30v_high_partial.csv`.
- JSON: `heal_v3_eval_v1_floor.json`, `tierb50_validity.json`, `set30_v1_regrade_newtests.json`; and `grader_audit_set30.md`.
- Analysis: `analysis/{heal_v3_eval,tierb50_effort,heal_controls}/{report.md,summary.json}` and `analysis/tierb50_v1/{summary.md,coefficients.csv}`.
- Case 2 (Beam, the same 50 Tier B tasks): `case2_probe/main_full/{REPORT.md,summary.json,results.csv,SENSITIVITY.md}`.
- Model comparison (Beam, GPT-OSS-120B low/medium/high, Qwen3.5-397B-A17B thinking on): `analysis/model_comparison/{report.md,summary.json}`, `model_comparison/README.md` and `model_comparison/<setting>/<suite>/results.csv` for suites `tierb50`, `v3base`, `v3hint`, `probe0` (placebo) and `probe5` (P5). The P20 arm (`probe20`) is not part of the comparison and is not exported.
- Figures: `figures/final_{scoreboard,leaderboard,effort_curves,information_gain,gap_vs_beam,beam_missing_info,cost_vs_score}.png` (made by `har2client/scripts/plot_final.py`), `figures/story_*.png`, `heal_v3_eval_frontier.png`, `tierb50_frontier.png`, `heal_v3_eval_families.png`, `models_*.png`.

The last four groups of "Source" names in the card point at these files. The reports were written while the repo was private and still mention script names and a pre-registration file (`har2client/PREREG.md`); the card tells readers so. `results/README.md` is not copied (it cites local script paths).

## Prerequisites

Python 3.10+ for the scripts (standard library only), `uv` for the real upload only. Run all commands from the repo root.
The export reads task dirs that must exist. They are generated, not tracked, so build them first (from `har2client/`, with `uv sync` done once):

```bash
cd har2client
uv run python scripts/build_tierb.py --out runs/tierb50 --seed 1                                   # Tier B: 50 tasks
uv run python scripts/select_specs.py                                                               # fetches the Tier A specs into data/specs
# Tier A: runs/set30 holds 30 task dirs. No single script builds them: each is one call of
#   uv run har2client build data/specs/<spec>.json --api-id <api_id> --out runs/set30 --noise 0.7 --seed 1
#   for the 30 specs chosen by the rule in har2client/results/README.md ("How the 30 Tier A tasks were chosen"). Reuse the existing folder.
uv run python scripts/build_heal_v3.py --suite eval --out runs/heal_v3_eval --seed 2                # case 3 v3 base arm: 64 tasks
uv run python scripts/add_contract_hint.py --tasks runs/heal_v3_eval --out runs/heal_v3_eval_hint   # case 3 v3 hint arm: 64 tasks
```

Also needed: `har2client/results/` with the files listed above (the export stops with a list if one is missing), and `har2client/data/specs/` (Tier A licence metadata is read from the spec `info` block; the export keeps only `license` and `x-origin`).
Check: `ls har2client/runs/set30 | wc -l` is 30, `tierb50` 50, `heal_v3_eval` and `heal_v3_eval_hint` 64 each.

## Build, dry run, publish

```bash
# 1. Dry run: guard (warning only), export, leak check, Croissant check, Space sync, leak checks, stage. No token, no network, no upload.
bash release/publish.sh <namespace> --dry-run
#    prints the row counts, then every file to upload with its size. Expected rows: tier_a=27, tier_b=50, heal_v3=64, heal_v3_hint=64.
#    It prints a WARNING only if placeholder markers are still in the card, croissant.json or the Space README (none now).

# 2. (Done) The authors and the contact are filled in. The guard stops a real publish if a placeholder marker comes back.

# 3. Real publish (needs a Hub write token; add --private to start private)
HF_TOKEN=hf_xxx bash release/publish.sh <namespace>
```

Steps inside `publish.sh`: guard, 1 export (with `--drop-spec-class restricted`), 2 leak check, 3 Croissant `--strict`, 4 Space sync, 5 leak check again, 6 stage the Space (writes `data/links.json` with the dataset URL only), 7 leak check of the staged Space, 8 upload.
Any failed check stops the script before the upload. A real publish with markers present exits non-zero and lists them before it reads the token or touches the network. `publish_hf.py` repeats the guard on the staged files, so calling it directly is also safe.
`PROBE_DIR` and `HEAL_DIR` add the case 2 and v2 case 3 sets when they exist. `HEAL_V3_DIR` and `HEAL_V3_HINT_DIR` override the v3 task dirs. `CODE_URL` is not used (the GitHub link is fixed in the card and the Space text).

Step by step, if you want to run the parts by hand:

```bash
V3="--heal-v3 har2client/runs/heal_v3_eval --heal-v3-hint har2client/runs/heal_v3_eval_hint"
python3 release/export_dataset.py --repo-id <ns>/har2client --drop-spec-class restricted $V3
python3 release/check_no_leak.py $V3
python3 release/validate_croissant.py --strict
python3 release/sync_space_data.py
```

## Run the Space locally

```bash
uv venv /tmp/h2c-space --python 3.12 && uv pip install --python /tmp/h2c-space/bin/python -r release/space/requirements.txt
python3 release/sync_space_data.py                  # needs build/dataset (run export_dataset.py first)
cd release/space && PORT=7861 GRADIO_SERVER_NAME=127.0.0.1 /tmp/h2c-space/bin/python app.py     # http://127.0.0.1:7861
```

## Tests

```bash
python3 -m unittest discover -s release/tests              # export, results, guard, leak check, sync
/tmp/h2c-space/bin/python -m unittest discover -s release/tests   # also the Space numbers (needs pandas, installed with gradio)
```

The "Space CSVs equal the results CSVs" test runs the sync into a temporary folder. Some tests print `FAIL:` lines from the leak check on purpose; read the final `OK`.

## Known caveats

1. The Tier A results (card and Space) were computed on 30 tasks and include the 3 dropped ones. The card gives the 27-task means next to them (low 95.83%, high 99.54%). The per-task CSV in `results/` still names the 3 dropped APIs.
2. The `grade.json` mention in `har2client/results/analysis/heal_controls/report.md` is reworded in the copy (the leak check bans hidden file names). The source fix would be in `har2client/scripts/analyze_heal_controls.py`, after which the entry in `RESULTS_REWRITES` can go.
3. Tier A hidden tests drifted after the `set30_v1` run (see the card, "Tier A tests changed once"); the means are unchanged on regrade. Tier B rebuilds byte for byte.
4. Official Croissant validation (`mlcroissant`) is not installed in the default environment. `pip install mlcroissant` in a venv and run `validate_croissant.py` to include it.
5. Parquet: `export_dataset.py` writes `data.parquet` next to the JSONL when `pyarrow` is installed, and the leak check then scans it. It is not installed by default, so no parquet is published.
