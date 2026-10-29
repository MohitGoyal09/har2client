#!/usr/bin/env python3
"""Copy the committed results into the Space folder and build the task excerpts for the "Explore a task" tab.

Usage:  python3 release/sync_space_data.py          (run `export_dataset.py` first)

Writes release/space/data/ (generated; it is git-ignored, rebuild it with this script):
  results/*.csv, results/heal_v3_eval_v1_floor.json, analysis/tierb50_v1/*, analysis/heal_v3_eval/summary.json, analysis/heal_controls/summary.json, grader_audit_set30.md
                                                                exact copies of har2client/results/
  results/model_comparison/<setting>/<suite>/results.csv        the model comparison CSVs (GPT-OSS-120B low, medium, high; Qwen3.5-397B-A17B thinking;
                                                                suites tierb50, v3base, v3hint, probe0, probe5; no probe20)
  results/case2_probe/main_full/results.csv                     Beam case 2 (the Beam rows of the comparison on probe0 and probe5)
  analysis/model_comparison/summary.json                        confidence intervals and paired differences for the comparison table
  figures/{final,story}_*.png                                   the Findings figures, copies of har2client/results/figures/
  tierb_configs.jsonl                                           har2client/data/tierb_configs.jsonl without the seeds
  tasks.json                                                    goal, switches, request table and one excerpt per task
                                                                (Tier A, Tier B and the case 3 v3 base and hint arms)
Nothing here is typed by hand: the Space reads only these files.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from h2c_release import CASE2_CSV, COMPARISON_MODELS, COMPARISON_SUITES, COMPARISON_SUMMARY, DATASET_DIR, H2C_DIR, RELEASE_DIR, SPACE_FIGURES

SPACE_DATA = RELEASE_DIR / "space" / "data"
RESULTS = H2C_DIR / "results"
EXCERPT_CHARS = 3000
BODY_CHARS = 700
# Configs left out of the Space task listing. Empty: the v3 task listing passes the leak check (it shows only the visible files,
# like the dataset rows). The two v3 arms share task ids, so the hint arm is keyed "<task id>#hint" (HINT_SUFFIX).
NOT_IN_SPACE: tuple[str, ...] = ()
HINT_SUFFIX = "#hint"
STATIC_MIME = ("text/html", "text/css", "image/", "font/", "javascript")


def copy_results(dest: Path, src: Path = RESULTS) -> None:
    """Exact copies of the results the Space reads: every CSV, the v3 floor file, the Tier B regression, the grader audit, the figures."""
    (dest / "results").mkdir(parents=True)
    for csv_path in sorted(src.glob("*.csv")):
        shutil.copy(csv_path, dest / "results" / csv_path.name)
    shutil.copy(src / "heal_v3_eval_v1_floor.json", dest / "results" / "heal_v3_eval_v1_floor.json")
    shutil.copytree(src / "analysis" / "tierb50_v1", dest / "analysis" / "tierb50_v1")
    (dest / "analysis" / "heal_v3_eval").mkdir(parents=True)
    shutil.copy(src / "analysis" / "heal_v3_eval" / "summary.json", dest / "analysis" / "heal_v3_eval" / "summary.json")
    (dest / "analysis" / "heal_controls").mkdir(parents=True)
    shutil.copy(src / "analysis" / "heal_controls" / "summary.json", dest / "analysis" / "heal_controls" / "summary.json")
    shutil.copy(src / "grader_audit_set30.md", dest / "grader_audit_set30.md")
    copy_comparison(dest, src)
    (dest / "figures").mkdir()
    for name in SPACE_FIGURES:
        shutil.copy(src / "figures" / name, dest / "figures" / name)


def copy_comparison(dest: Path, src: Path = RESULTS) -> None:
    """The model comparison: one results.csv per setting and suite (probe20 is left out), Beam's case 2 CSV and the comparison summary."""
    for model in COMPARISON_MODELS:
        for suite in COMPARISON_SUITES:
            rel = Path("model_comparison") / model / suite / "results.csv"
            (dest / "results" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src / rel, dest / "results" / rel)
    (dest / "results" / "case2_probe" / "main_full").mkdir(parents=True)
    shutil.copy(src / CASE2_CSV, dest / "results" / CASE2_CSV)
    (dest / "analysis" / "model_comparison").mkdir(parents=True)
    shutil.copy(src / COMPARISON_SUMMARY, dest / COMPARISON_SUMMARY)


def entry_row(entry: dict) -> list:
    req, res = entry.get("request", {}), entry.get("response", {})
    mime = (res.get("content") or {}).get("mimeType", "")
    return [req.get("method", ""), req.get("url", ""), res.get("status", ""), mime]


def pick_excerpt(entries: list[dict]) -> str:
    """The first JSON API exchange, cut to a readable size (request line, key headers, response body start)."""
    for e in entries:
        res = e.get("response", {})
        mime = (res.get("content") or {}).get("mimeType", "")
        if "json" not in mime or e.get("request", {}).get("method") not in ("GET", "POST"):
            continue
        req = e["request"]
        view = {
            "request": {"method": req.get("method"), "url": req.get("url"), "headers": {h["name"]: h["value"] for h in req.get("headers", [])}},
            "response": {"status": res.get("status"), "content": {"mimeType": mime,
                                                                  "text": ((res.get("content") or {}).get("text") or "")[:BODY_CHARS]}},
        }
        return json.dumps(view, indent=2)[:EXCERPT_CHARS]
    return ""


def task_record(row: dict) -> dict:
    har = json.loads(row["observation"])
    entries = har["log"]["entries"]
    return {
        "tier": row["tier"],
        "title": row["title"],
        "goal_md": row["goal_md"],
        "config": row["config"],
        "features": row["features"],
        "observation_file": row["observation_file"],
        "n_entries": len(entries),
        "requests": [entry_row(e) for e in entries],
        "excerpt": pick_excerpt(entries),
    }


def main() -> int:
    if not DATASET_DIR.is_dir():
        print("error: run release/export_dataset.py first", file=sys.stderr)
        return 1
    if not any(DATASET_DIR.glob("*/data.jsonl")):
        print(f"error: no data.jsonl under {DATASET_DIR}; the Space would have no tasks", file=sys.stderr)
        return 1
    if SPACE_DATA.exists():
        shutil.rmtree(SPACE_DATA)
    copy_results(SPACE_DATA)
    # Seeds stay out of the Space: keep only the task id, the domain and the switches.
    configs = [json.loads(line) for line in (H2C_DIR / "data" / "tierb_configs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    slim = [{"task_id": c["task_id"], "domain": c["domain"], "config": c["config"]} for c in configs]
    (SPACE_DATA / "tierb_configs.jsonl").write_text("".join(json.dumps(c) + "\n" for c in slim), encoding="utf-8")

    tasks: dict[str, dict] = {}
    for data in sorted(p for p in DATASET_DIR.glob("*/data.jsonl") if p.parent.name not in NOT_IN_SPACE):
        for line in data.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = row["task_id"] + (HINT_SUFFIX if data.parent.name == "heal_v3_hint" else "")
            tasks[key] = {**task_record(row), "suite": data.parent.name}
    (SPACE_DATA / "tasks.json").write_text(json.dumps(tasks, indent=1), encoding="utf-8")
    print(f"synced {len(list((SPACE_DATA / 'results').glob('*.csv')))} CSVs and {len(tasks)} task records into {SPACE_DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
