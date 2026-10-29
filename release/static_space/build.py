#!/usr/bin/env python3
"""Build the static Hugging Face Space (sdk: static) from the committed results. Standard library only.

Usage:  python3 release/static_space/build.py [--out DIR]

Reads (never hard-codes a result):
  har2client/results/analysis/model_comparison/summary.json   the 7 settings x 5 benchmarks (pass rate, 95% CI, output tokens)
  har2client/results/analysis/{tierb50_effort,heal_v3_eval,heal_controls}/summary.json   case 1 header on/off, case 3 arms, verdicts
  har2client/results/case2_probe/main_full/summary.json        case 2 sanity check (counts)
  release/build/dataset/*/data.jsonl                            visible task files only, through release/sync_space_data.py
  README.md, CITATION.cff                                       install / RL snippets and the BibTeX entry
Writes release/build/static_space/: index.html, README.md (Space frontmatter), assets/{style.css,app.js,og.png}, data/{site.json,tasks.json}.
Run release/export_dataset.py first (the task explorer reads its output).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RELEASE = HERE.parent
sys.path.insert(0, str(RELEASE))

import sync_space_data  # noqa: E402  (reused: task_record builds the visible-only task view)
from h2c_release import DATASET_DIR, H2C_DIR, REPO_ROOT  # noqa: E402

RESULTS = H2C_DIR / "results"
OUT_DEFAULT = RELEASE / "build" / "static_space"
DATASET_URL = "https://huggingface.co/datasets/Rockerleo/har2client"
CODE_URL = "https://github.com/MohitGoyal09/har2client"
PREREG_URL = CODE_URL + "/blob/main/har2client/PREREG.md"
SPACE_URL = "https://huggingface.co/spaces/Rockerleo/har2client-leaderboard"
OG_FIGURE = RESULTS / "figures" / "final_beam_missing_info.png"
MAX_REQUEST_ROWS = 60
MAX_EXCERPT_CHARS = 1600
EFFORTS = ("low", "medium", "high")
FORBIDDEN = re.compile(r"probe20|\bP20\b|P-?20 ", re.I)

MODELS = {
    "beam": {"label": "Beam", "full": "Beam-1.0-501B-A23B", "color": "#2a78d6"},
    "gptoss": {"label": "GPT-OSS", "full": "GPT-OSS-120B", "color": "#eb6834"},
    "qwen": {"label": "Qwen", "full": "Qwen3.5-397B-A17B (thinking on)", "color": "#1baf7a"},
}
# Display order of the 7 settings: (comparison key, model, effort).
SETTINGS = [
    ("beam-low", "beam", "low"), ("beam-medium", "beam", "medium"), ("beam-high", "beam", "high"),
    ("gpt-oss-120b-low", "gptoss", "low"), ("gpt-oss-120b-medium", "gptoss", "medium"), ("gpt-oss-120b-high", "gptoss", "high"),
    ("qwen3.5-397b-thinking", "qwen", None),
]
BENCHMARKS = [
    {"id": "c1", "suite": "tierb50", "short": "Case 1", "label": "Case 1 build", "n": 50},
    {"id": "c2a", "suite": "probe0", "short": "Case 2", "label": "Case 2 no probes", "n": 50},
    {"id": "c2b", "suite": "probe5", "short": "Case 2 + 5 probes", "label": "Case 2 up to 5 probes", "n": 50},
    {"id": "c3a", "suite": "v3base", "short": "Case 3", "label": "Case 3 repair, no hint", "n": 64},
    {"id": "c3b", "suite": "v3hint", "short": "Case 3 + hint", "label": "Case 3 repair, with hint", "n": 64},
]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cell(row: dict) -> dict:
    return {"pass": row["pass_rate"], "lo": row["ci_low"], "hi": row["ci_high"], "tokens": row["mean_output_tokens"],
            "n": row["n"], "runs": row["runs"], "full": row.get("full_solve_pct")}


def build_cells(comparison: dict) -> dict:
    by = {(r["key"], r["suite"]): r for r in comparison["rows"]}
    cells: dict[str, dict] = {}
    for key, _model, _effort in SETTINGS:
        cells[key] = {}
        for b in BENCHMARKS:
            row = by.get((key, b["suite"]))
            if row is None:
                raise SystemExit(f"error: model_comparison summary has no row for {key} / {b['suite']}")
            if row["n"] != b["n"]:
                raise SystemExit(f"error: {key}/{b['suite']} has n={row['n']}, expected {b['n']}")
            cells[key][b["id"]] = cell(row)
    return cells


def build_beam_detail(controls: dict, v3: dict, cells: dict) -> dict:
    case1 = {}
    for effort in EFFORTS:
        h = controls["header_on_off"][effort]
        case1[effort] = {"hidden": h["pass_on"] * 100, "visible": h["pass_off"] * 100, "n_hidden": h["n_on"], "n_visible": h["n_off"],
                         "diff": h["on_minus_off"]["mean"], "diff_lo": h["on_minus_off"]["ci_low"], "diff_hi": h["on_minus_off"]["ci_high"],
                         "runs": 3 if effort == "high" else 1}
    case3 = {}
    for effort in EFFORTS:
        base, hint = v3["steps"][f"base-{effort}"], v3["steps"][f"hint-{effort}"]
        case3[effort] = {"base": base["pass_rate"] * 100, "hint": hint["pass_rate"] * 100,
                         "base_full": base["full_repair_share"] * 100, "hint_full": hint["full_repair_share"] * 100,
                         "base_tokens": base["mean_output_tokens"], "hint_tokens": hint["mean_output_tokens"],
                         "runs": base["runs_per_task"], "n": base["n_scored"]}
    case2 = {}
    for effort in EFFORTS:
        a, b = cells[f"beam-{effort}"]["c2a"], cells[f"beam-{effort}"]["c2b"]
        case2[effort] = {"placebo": a["pass"], "p5": b["pass"], "placebo_full": a["full"], "p5_full": b["full"], "n": a["n"], "runs": a["runs"]}
    return {"case1": case1, "case2": case2, "case3": case3}


def build_signature(controls: dict, cells: dict) -> list[dict]:
    h10 = controls["h10"]
    steps = h10["steps"]
    b2 = cells["beam-high"]
    return [
        {"id": "case1", "case": "Case 1 · build", "n": steps["a"]["finished"],
         "withheld": steps["a"]["pass_rate"] * 100,
         "supplied": [{"label": "named in the goal text (hint)", "value": steps["b"]["pass_rate"] * 100},
                      {"label": "shown in the recording (evidence)", "value": steps["c"]["pass_rate"] * 100}],
         "withheld_text": "One request header is hidden from the recording",
         "supplied_text": "The missing header is named, or shown in a failed call",
         "scope": f"{steps['a']['finished']} Tier B tasks with the header hidden; withheld is the mean of {steps['a']['runs_per_task']} runs"},
        {"id": "case2", "case": "Case 2 · investigate", "n": b2["c2a"]["n"],
         "withheld": b2["c2a"]["pass"], "supplied": [{"label": "up to 5 test requests to a practice server", "value": b2["c2b"]["pass"]}],
         "withheld_text": "The model cannot test the API before it writes",
         "supplied_text": "The model may send up to 5 probes first",
         "scope": f"{b2['c2a']['n']} Tier B tasks, 1 run each"},
        {"id": "case3", "case": "Case 3 · repair", "n": b2["c3a"]["n"],
         "withheld": b2["c3a"]["pass"], "supplied": [{"label": "a contract hint in the goal text", "value": b2["c3b"]["pass"]}],
         "withheld_text": "The API changed silently and nothing says so",
         "supplied_text": "The goal warns that the wire format may have changed",
         "scope": f"{b2['c3a']['n']} repair tasks, mean of {b2['c3a']['runs']} runs"},
    ]


def clean_verdict(v: dict) -> dict:
    return {"id": v["id"], "title": v.get("title"), "status": v["status"], "evidence": v.get("evidence") or ""}


PREREG_CLAIMS = {  # H10 and H22 / H23 carry no title in their summary files; the wording is the PREREG.md row
    "H10": "The hidden-header failure is about evidence, not prompting",
    "H22": "The hint effect is not a generic prompt effect",
    "H23": "A warning alone gives a large part of the hint effect",
}


def build_verdicts(controls: dict, v3: dict) -> list[dict]:
    found = {v["id"]: v for v in v3["verdicts"]}
    found.update({v["id"]: v for v in controls["verdicts"]})
    order = ["H10", "H16", "H17", "H18 (base)", "H19", "H20", "H21", "H22", "H23"]
    out = []
    for vid in order:
        v = found.get(vid)
        if v is None:
            continue
        item = clean_verdict(v)
        item["title"] = item["title"] or PREREG_CLAIMS.get(vid, "")
        out.append(item)
    return out


def build_effort_facts(effort_summary: dict, v3: dict) -> dict:
    c1 = effort_summary["contrasts"]["high-low"]
    c3 = v3["effort_contrasts"]["base: high - low"]
    h = v3["hint_minus_base"]["high"]["pass"]
    return {
        "case1_gain": c1["pass_diff"]["mean"], "case1_lo": c1["pass_diff"]["ci_low"], "case1_hi": c1["pass_diff"]["ci_high"], "case1_tokens": c1["output_token_ratio"],
        "case3_gain": c3["pass"]["mean"], "case3_lo": c3["pass"]["ci_low"], "case3_hi": c3["pass"]["ci_high"], "case3_tokens": c3["output_token_ratio"],
        "hint_gain_high": h["mean"], "hint_gain_high_lo": h["ci_low"], "hint_gain_high_hi": h["ci_high"],
    }


def build_tasks() -> dict:
    """The task explorer data: the same visible-only records as release/sync_space_data.py (goal, request table, short excerpt)."""
    if not any(DATASET_DIR.glob("*/data.jsonl")):
        raise SystemExit("error: run release/export_dataset.py first (no data.jsonl under release/build/dataset)")
    tasks = []
    for data in sorted(p for p in DATASET_DIR.glob("*/data.jsonl") if p.parent.name not in sync_space_data.NOT_IN_SPACE):
        for line in data.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            rec = sync_space_data.task_record(row)
            suite = data.parent.name
            tasks.append({
                "id": row["task_id"], "suite": suite, "tier": rec["tier"], "title": rec["title"], "goal": rec["goal_md"],
                "features": rec["features"], "n_requests": rec["n_entries"], "requests": rec["requests"][:MAX_REQUEST_ROWS],
                "excerpt": rec["excerpt"][:MAX_EXCERPT_CHARS],
            })
    return {"suites": {s: sum(1 for t in tasks if t["suite"] == s) for s in ("tier_a", "tier_b", "heal_v3", "heal_v3_hint")}, "tasks": tasks}


def readme_blocks() -> dict:
    """The install, grade and RL snippets, copied from the repo README (so they cannot drift from it)."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    def section(title: str) -> str:
        m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## )", text, re.S | re.M)
        if not m:
            raise SystemExit(f"error: README.md has no '## {title}' section")
        return m.group(1)

    quick = re.findall(r"```bash\n(.*?)```", section("Quickstart"), re.S)
    rl = re.findall(r"```python\n(.*?)```", section("Use as an RL environment"), re.S)
    if len(quick) < 2 or not rl:
        raise SystemExit("error: README.md snippets not found (Quickstart bash blocks, RL python block)")
    # The README grades the shipped reference client; the hidden file name must not appear on the site (leak check), so the
    # command grades your own client.py instead.
    grade = quick[1].strip().replace("$T/ref_client.py", "client.py")
    return {"install": quick[0].strip(), "grade": grade, "rl": rl[0].strip()}


def bibtex() -> str:
    cff = (REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8")

    def field(name: str) -> str:
        m = re.search(rf'^{name}:\s*"?([^"\n]+)"?\s*$', cff, re.M)
        if not m:
            raise SystemExit(f"error: CITATION.cff has no {name}")
        return m.group(1).strip()

    authors = re.findall(r'family-names:\s*"([^"]+)"\n\s*given-names:\s*"([^"]+)"', cff)
    names = " and ".join(f"{fam}, {giv}" for fam, giv in authors)
    return (
        "@software{har2client2026,\n"
        f"  title   = {{{field('title')}}},\n"
        f"  author  = {{{names}}},\n"
        f"  year    = {{{field('date-released')[:4]}}},\n"
        f"  version = {{{field('version')}}},\n"
        f"  url     = {{{field('repository-code')}}},\n"
        f"  license = {{{field('license')}}}\n"
        "}"
    )


def space_readme() -> str:
    return f"""---
title: HAR2Client
emoji: null
colorFrom: gray
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: apache-2.0
short_description: Traffic-to-client benchmark with hidden execution tests
---

# HAR2Client

A static companion site for the HAR2Client benchmark: give a model recorded HTTP traffic, get a working `client.py`, and grade it
with hidden tests on a fresh fake server. It shows the leaderboard for Beam-1.0-501B-A23B, GPT-OSS-120B and Qwen3.5-397B-A17B,
the effort and extra-information results, a task explorer and the method. Every number is read from the analysis files of the
dataset repo at build time.

- Dataset (CC-BY-4.0): {DATASET_URL}
- Code (Apache-2.0): {CODE_URL}
- Pre-registration: {PREREG_URL}

Built for the Reflection x Hugging Face "Build with Beam" challenge.
"""


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def verdict_rows(verdicts: list[dict]) -> str:
    rows = []
    for v in verdicts:
        status = v["status"]
        rows.append(
            f'<tr><th scope="row">{esc(v["id"].replace(" (base)", ""))}</th><td>{esc(v["title"])}</td>'
            f'<td><span class="verdict verdict-{esc(status.replace(" ", "-"))}">{esc(status)}</span></td><td class="evidence">{esc(v["evidence"])}</td></tr>')
    return "\n".join(rows)


def fill(template: str, values: dict) -> str:
    for key, val in values.items():
        template = template.replace("{{" + key + "}}", val)
    left = re.findall(r"\{\{[A-Z0-9_]+\}\}", template)
    if left:
        raise SystemExit(f"error: unfilled template markers {sorted(set(left))}")
    return template


def build(out: Path) -> None:
    comparison = load(RESULTS / "analysis" / "model_comparison" / "summary.json")
    effort_summary = load(RESULTS / "analysis" / "tierb50_effort" / "summary.json")
    v3 = load(RESULTS / "analysis" / "heal_v3_eval" / "summary.json")
    controls = load(RESULTS / "analysis" / "heal_controls" / "summary.json")
    case2 = load(RESULTS / "case2_probe" / "main_full" / "summary.json")

    cells = build_cells(comparison)
    detail = build_beam_detail(controls, v3, cells)
    signature = build_signature(controls, cells)
    verdicts = build_verdicts(controls, v3)
    facts = build_effort_facts(effort_summary, v3)
    tasks = build_tasks()
    snippets = readme_blocks()
    cite = bibtex()

    site = {
        "models": MODELS,
        "settings": [{"key": k, "model": m, "effort": e} for k, m, e in SETTINGS],
        "benchmarks": BENCHMARKS,
        "cells": cells,
        "signature": signature,
        "detail": detail,
        "effort_facts": facts,
        "verdicts": verdicts,
        "n_boot": comparison["n_boot"],
        "case2_rows": case2["observed_rows"],
        "suites": tasks["suites"],
        "links": {"dataset": DATASET_URL, "code": CODE_URL, "prereg": PREREG_URL},
    }
    site_json = json.dumps(site, separators=(",", ":"))
    tasks_json = json.dumps(tasks, separators=(",", ":"))

    sig = {s["id"]: s for s in signature}
    h10 = controls["h10"]["steps"]
    vis = detail["case1"]["high"]["visible"]
    template = (HERE / "template" / "index.html").read_text(encoding="utf-8")
    page = fill(template, {
        "SITE_JSON": site_json.replace("</", "<\\/"),
        "H1_VISIBLE": f"{vis:.1f}",
        "H1_HIDDEN": f"{sig['case1']['withheld']:.1f}",
        "H1_SUPPLIED_LOW": f"{min(h10['b']['pass_rate'], h10['c']['pass_rate']) * 100:.1f}",
        "H1_SUPPLIED_HIGH": f"{max(h10['b']['pass_rate'], h10['c']['pass_rate']) * 100:.1f}",
        "N_TIER_A": str(tasks["suites"]["tier_a"]),
        "N_TIER_B": str(tasks["suites"]["tier_b"]),
        "N_CASE3": str(tasks["suites"]["heal_v3"]),
        "VERDICT_ROWS": verdict_rows(verdicts),
        "CODE_INSTALL": esc(snippets["install"]),
        "CODE_GRADE": esc(snippets["grade"]),
        "CODE_RL": esc(snippets["rl"]),
        "BIBTEX": esc(cite),
        "DATASET_URL": DATASET_URL,
        "CODE_URL": CODE_URL,
        "PREREG_URL": PREREG_URL,
        "OG_IMAGE": SPACE_URL + "/resolve/main/assets/og.png",
        "SPACE_URL": SPACE_URL,
    })
    if FORBIDDEN.search(page) or FORBIDDEN.search(site_json) or FORBIDDEN.search(tasks_json) or FORBIDDEN.search(space_readme()):
        raise SystemExit("error: the P20 arm must not appear anywhere on the site")

    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / "data").mkdir()
    (out / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(HERE / "assets" / "style.css", out / "assets" / "style.css")
    shutil.copy(HERE / "assets" / "app.js", out / "assets" / "app.js")
    shutil.copy(OG_FIGURE, out / "assets" / "og.png")
    (out / "data" / "site.json").write_text(json.dumps(site, indent=1), encoding="utf-8")
    (out / "data" / "tasks.json").write_text(tasks_json, encoding="utf-8")
    (out / "README.md").write_text(space_readme(), encoding="utf-8")
    js_kb = (out / "assets" / "app.js").stat().st_size / 1024
    print(f"built {out} ({len(tasks['tasks'])} tasks, app.js {js_kb:.1f} KB)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = ap.parse_args()
    build(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
