#!/usr/bin/env python3
"""Export har2client task sets to a Hugging Face dataset layout (standard library; parquet only if pyarrow is installed).

Usage:
  python3 release/export_dataset.py                       # tier_a from runs/set30, tier_b from runs/tierb50
  python3 release/export_dataset.py --probe DIR --heal DIR   # later, when cases 2 and 3 exist
  python3 release/export_dataset.py --heal-v3 har2client/runs/heal_v3_eval --heal-v3-hint har2client/runs/heal_v3_eval_hint
  python3 release/export_dataset.py --drop-spec-class restricted   # leave out Tier A tasks whose API spec licence is restrictive

Output (release/build/dataset/):
  README.md                 the dataset card (release/DATASET_CARD.md with the configs block regenerated)
  croissant.json            copy of release/croissant.json
  CANARY.txt, MANIFEST.json
  results/                  the results CSVs, JSON, analysis reports and figures the card cites (copied from har2client/results/)
  <config>/data.jsonl       one row per task  (+ data.parquet when pyarrow is importable)

Only the visible files are read: goal.md, the one session.* file, and for case 3 client_v1.py and failure.txt
(v3 also CHANGELOG.md when the task has one).
Metadata comes from an allow-list of task.json keys. Hidden files (tests, engine spec, op table, reference client,
credentials, seeds) are never opened here. `check_no_leak.py` verifies the result.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from h2c_release import (
    CONFIGS,
    DATASET_DIR,
    EXTRA_VISIBLE,
    GOAL_FILE,
    H2C_DIR,
    HEAL_V3_ARMS,
    RELEASE_DIR,
    RESULTS_DIR,
    RESULTS_FILES,
    RESULTS_REWRITES,
    add_dir_args,
    find_observation,
    load_json,
    read_canary,
    results_figure_names,
    selected_dirs,
    task_dirs,
)

# task.json keys that are safe metadata. Anything not listed here is dropped.
SCALAR_META_KEYS = ("domain", "noise_ratio", "har_entries", "session_calls", "mutation", "probe_budget", "arm", "source")
CONFIG_DICT_KEYS = ("config",)
# Case 3 v3 design labels, read from the `heal` block of task.json (allow-list; the rest of that block is hidden:
# practice seed and credentials, gate scores, necessity, changelog truth, seeds).
HEAL_V3_META_KEYS = ("set", "changes", "error_detail", "evidence", "evidence_scope", "changelog", "client_style", "identifiable")
SPEC_DIR = H2C_DIR / "data" / "specs"

PERMISSIVE = re.compile(r"\b(mit|apache|bsd|creative commons|cc0|cc-by|isc|mpl)\b", re.I)
RESTRICTIVE = re.compile(r"proprie|commercial|agreement|all rights reserved", re.I)
CONFIGS_BEGIN, CONFIGS_END = "# BEGIN-CONFIGS", "# END-CONFIGS"


def spec_licence(api_id: str) -> dict:
    """Licence and origin metadata declared in the APIs.guru spec's `info` block (not the spec itself)."""
    path = SPEC_DIR / f"{api_id.replace(':', '__')}.json"
    out = {"spec_license_name": None, "spec_license_url": None, "spec_origin_url": None, "spec_license_class": "undeclared"}
    if not path.is_file():
        return out
    info = load_json(path).get("info", {})
    lic = info.get("license") or {}
    origin = (info.get("x-origin") or [{}])[0]
    name = lic.get("name")
    out.update(spec_license_name=name, spec_license_url=lic.get("url"), spec_origin_url=origin.get("url"))
    if name:
        out["spec_license_class"] = "restricted" if RESTRICTIVE.search(name) else "permissive" if PERMISSIVE.search(name) else "other"
    return out


def tier_a_config(info: dict) -> dict:
    auth = next((f.split(":", 1)[1] for f in info.get("features", []) if f.startswith("auth:")), None)
    return {
        "auth_type": auth,
        "n_goal_ops": len(info.get("goal_ops", [])),
        "n_spec_ops": info.get("spec_ops"),
    }


def scalar_metadata(info: dict) -> dict:
    """Allow-listed scalar keys of task.json (case 3 keeps its mutation name, found under `heal`)."""
    meta = {k: info[k] for k in SCALAR_META_KEYS if isinstance(info.get(k), (str, int, float, bool))}
    mutation = (info.get("heal") or {}).get("mutation")
    if isinstance(mutation, str):
        meta["mutation"] = mutation
    return meta


def heal_v3_metadata(config: str, info: dict) -> dict:
    """Scalar design labels of a v3 task (family, number of changes, evidence, changelog, client style) and the arm."""
    heal = info.get("heal") or {}
    meta = {k: heal[k] for k in HEAL_V3_META_KEYS if isinstance(heal.get(k), (str, int, float, bool))}
    meta["arm"] = HEAL_V3_ARMS[config]
    return meta


def build_row(config: str, task_dir: Path, canary: str) -> dict:
    case, tier = CONFIGS[config]
    info = load_json(task_dir / "task.json")  # hidden file: only allow-listed keys are copied below
    obs_path = find_observation(task_dir)
    observation = obs_path.read_text(encoding="utf-8")
    cfg = {k: v for key in CONFIG_DICT_KEYS for k, v in (info.get(key) or {}).items() if isinstance(v, (str, int, float, bool))}
    if config == "tier_a":
        cfg = tier_a_config(info)
    row = {
        "task_id": info.get("task_id", task_dir.name),
        "case": case,
        "tier": tier,
        "title": info.get("title"),
        "config": cfg,
        "features": [f for f in info.get("features", []) if isinstance(f, str)],
        "metadata": {**scalar_metadata(info), **(heal_v3_metadata(config, info) if config in HEAL_V3_ARMS else {})},
        "goal_md": (task_dir / GOAL_FILE).read_text(encoding="utf-8"),
        "observation": observation,
        "observation_format": obs_path.suffix.lstrip("."),
        "observation_file": obs_path.name,
        "canary": canary,
    }
    if config == "tier_a":
        row["api_id"] = info.get("api_id")
        row.update(spec_licence(info.get("api_id", "")))
    for fname, column in EXTRA_VISIBLE.get(config, {}).items():
        path = task_dir / fname
        row[column] = path.read_text(encoding="utf-8") if path.is_file() else None
    return row


def unify_keys(rows: list[dict]) -> list[dict]:
    """Same keys in every row's `config` and `metadata` (None when absent), so parquet gets one schema."""
    for field in ("config", "metadata"):
        keys = sorted({k for r in rows for k in r[field]})
        for r in rows:
            r[field] = {k: r[field].get(k) for k in keys}
    return rows


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")


def write_parquet(rows: list[dict], path: Path) -> bool:
    """Write parquet when pyarrow is already installed. Returns False when it is not (nothing is installed)."""
    try:
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415
    except ImportError:
        return False
    pq.write_table(pa.Table.from_pylist(rows), path)
    return True


def render_card(configs_built: list[str], repo_id: str = "ORG/har2client") -> str:
    """DATASET_CARD.md with the YAML `configs:` block rewritten to list only the configs that have rows,
    and `<ns>` (the Hub namespace) filled in, so the "How to use" snippet and the Space link are real."""
    text = (RELEASE_DIR / "DATASET_CARD.md").read_text(encoding="utf-8").replace("<ns>", repo_id.split("/", 1)[0])
    block = ["configs:"]
    for name in configs_built:
        block += [f"  - config_name: {name}", "    data_files:", "      - split: test", f"        path: {name}/data.jsonl"]
    pattern = re.compile(rf"{re.escape(CONFIGS_BEGIN)}.*?{re.escape(CONFIGS_END)}", re.S)
    if not pattern.search(text):
        raise SystemExit("DATASET_CARD.md has no BEGIN-CONFIGS / END-CONFIGS markers")
    return pattern.sub(lambda _m: "\n".join([CONFIGS_BEGIN, *block, CONFIGS_END]), text)


def copy_results(out_dir: Path, src: Path = RESULTS_DIR) -> list[dict]:
    """Copy the results the card cites into `<out>/results/` (same relative paths; exact copies except RESULTS_REWRITES). A missing file stops the export:
    the card's Source columns would point at nothing. Returns [{path, sha256}] for MANIFEST.json."""
    wanted = [*RESULTS_FILES, *results_figure_names(src)]
    missing = [name for name in wanted if not (src / name).is_file()]
    if missing:
        raise SystemExit(f"error: results files missing in {src}: {missing}")
    entries = []
    for name in wanted:
        dest = out_dir / "results" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name in RESULTS_REWRITES:
            text = (src / name).read_text(encoding="utf-8")
            for old, new in RESULTS_REWRITES[name]:
                text = text.replace(old, new)
            dest.write_text(text, encoding="utf-8")
        else:
            shutil.copyfile(src / name, dest)
        entries.append({"path": f"results/{name}", "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()})
    return entries


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_croissant(built: list[str], out_dir: Path, repo_id: str) -> dict:
    """release/croissant.json for the configs that have rows: real sha256 per data file, the real repo id in the URLs."""
    doc = json.loads((RELEASE_DIR / "croissant.json").read_text(encoding="utf-8"))
    doc["distribution"] = [d for d in doc["distribution"] if d["@id"].removesuffix("-jsonl") in built]
    doc["recordSet"] = [r for r in doc["recordSet"] if r["@id"] in built]
    for d in doc["distribution"]:
        d["sha256"] = hashlib.sha256((out_dir / d["name"]).read_bytes()).hexdigest()
    doc["datePublished"] = datetime.date.today().isoformat()  # the day the export (and so the publish) was built
    return json.loads(json.dumps(doc).replace("ORG/har2client", repo_id))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_dir_args(ap)
    ap.add_argument("--out", type=Path, default=DATASET_DIR)
    ap.add_argument("--repo-id", default="ORG/har2client", help="Hub dataset id used in the Croissant URLs (publish.sh sets it)")
    ap.add_argument("--results", type=Path, default=RESULTS_DIR, help="results folder copied to <out>/results (default: har2client/results)")
    ap.add_argument("--drop-spec-class", action="append", default=[], choices=["restricted", "other", "undeclared", "permissive"],
                    help="leave out Tier A tasks whose API spec licence is in this class (repeatable)")
    args = ap.parse_args(argv)

    if args.drop_spec_class and not SPEC_DIR.is_dir():
        raise SystemExit(f"error: {SPEC_DIR} is missing, so spec licences cannot be read and --drop-spec-class would drop nothing")
    canary = read_canary()
    dirs = selected_dirs(args)
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    manifest: dict = {"canary": canary, "configs": {}}
    built: list[str] = []
    for name in CONFIGS:
        if name not in dirs:
            manifest["configs"][name] = {"rows": 0, "status": "placeholder (no task dir given)"}
            print(f"{name}: 0 rows (placeholder)")
            continue
        rows = [build_row(name, d, canary) for d in task_dirs(dirs[name])]
        rows = [r for r in rows if r.get("spec_license_class") not in args.drop_spec_class]
        if not rows:
            manifest["configs"][name] = {"rows": 0, "status": "placeholder (task dir empty)"}
            print(f"{name}: 0 rows (placeholder)")
            continue
        rows = unify_keys(sorted(rows, key=lambda r: r["task_id"]))
        write_jsonl(rows, args.out / name / "data.jsonl")
        parquet = write_parquet(rows, args.out / name / "data.parquet")
        built.append(name)
        manifest["configs"][name] = {
            "rows": len(rows),
            "parquet": parquet,
            "tasks": [{"task_id": r["task_id"], "goal_sha256": sha(r["goal_md"]), "observation_sha256": sha(r["observation"])} for r in rows],
        }
        print(f"{name}: {len(rows)} rows from {dirs[name]}" + (" (+parquet)" if parquet else " (jsonl only; pyarrow not installed)"))

    manifest["dropped_spec_classes"] = sorted(args.drop_spec_class)
    manifest["results"] = copy_results(args.out, args.results)
    (args.out / "README.md").write_text(render_card(built, args.repo_id), encoding="utf-8")
    (args.out / "CANARY.txt").write_text(canary + "\n", encoding="utf-8")
    (args.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    croissant = render_croissant(built, args.out, args.repo_id)
    (args.out / "croissant.json").write_text(json.dumps(croissant, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
