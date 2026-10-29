"""Build the Tier A control variants (exact, reworded, reworded_desc, opaque, twin, closed-book, twin_closed) for every task of a set.

    uv run python scripts/build_variants.py runs/set30 runs/set30v [--only-added]

Reads each `<set>/*/task.json` (api_id, build seed, noise) and the spec path from data/selected_specs.jsonl.
Beam is called only when `data/rewordings/<api_id>.json` lacks a valid entry. Grades the reference client
on every variant except closed-book (the grader does not read the HAR, so it scores the same).
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client import grade, ops, refclient
from har2client.build_task import build_task

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {
    "exact": {},
    "reworded": {"names": "reworded"},
    "opaque": {"names": "opaque"},
    "twin": {"twin": True},
    "closed": {"closed_book": True},
    "reworded_desc": {"names": "reworded_desc"},
    "twin_closed": {"twin": True, "closed_book": True},
}
ADDED = ("reworded_desc", "twin_closed")  # 2026-10-24: the other five already exist; `--only-added` builds just these
WORKERS = 5


def build_api(task_json: Path, out: Path, specs: dict[str, str], only_added: bool = False) -> dict:
    task = json.loads(task_json.read_text())
    api_id = task["api_id"]
    spec = ops.load_spec(ROOT / specs[api_id])
    row = {"api_id": api_id, "variants": {}}
    for label, kw in VARIANTS.items():
        if only_added and label not in ADDED:
            continue
        dir_ = build_task(spec, api_id, out, noise=task["noise_ratio"], seed=task["build_seed"], **kw)
        built = json.loads((dir_ / "task.json").read_text())
        info = {"dir": dir_.name, "goal_ops": len(built["goal_ops"]), "har_entries": built["har_entries"]}
        if label == "exact":
            info["same_goal_as_set"] = [g for g in built["goal_ops"]] == task["goal_ops"]
        if label not in ("closed", "twin_closed"):
            info["ref_pass_rate"] = grade.grade(dir_, refclient.write_ref_client(dir_))["pass_rate"]
        row["variants"][label] = info
    return row


def main(argv: list[str]) -> int:
    only_added = "--only-added" in argv
    src, out = (Path(a) for a in [x for x in argv[1:] if not x.startswith("--")][:2])
    specs = {}
    for line in (ROOT / "data" / "selected_specs.jsonl").read_text().splitlines():
        rec = json.loads(line)
        specs[rec["api_id"]] = rec["spec_path"]
    jobs = sorted(src.glob("*/task.json"))
    with ThreadPoolExecutor(WORKERS) as pool:
        rows = list(pool.map(lambda j: build_api(j, out, specs, only_added), jobs))
    name = "variants_added_summary.json" if only_added else "variants_summary.json"
    (out / name).write_text(json.dumps(rows, indent=1) + "\n")
    bad = [(r["api_id"], k) for r in rows for k, v in r["variants"].items() if v.get("ref_pass_rate", 1.0) != 1.0]
    bad += [(r["api_id"], "goal differs from set") for r in rows if r["variants"].get("exact", {}).get("same_goal_as_set") is False]
    n_variants = len(rows[0]["variants"]) if rows else 0
    print(f"built {len(rows)} APIs x {n_variants} variants; reference below 1.0: {bad or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
