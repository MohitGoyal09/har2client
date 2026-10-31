#!/usr/bin/env python3
"""Build the static Hugging Face Space (sdk: static) from the committed results. Standard library only.

Usage:  python3 release/static_space/build.py [--out DIR]

Reads (never hard-codes a result):
  har2client/results/analysis/model_comparison/summary.json   the 7 settings x 5 benchmarks (pass rate, 95% CI, output tokens)
  har2client/results/analysis/{tierb50_effort,heal_v3_eval,heal_controls}/summary.json   case 1 header on/off, case 3 arms, verdicts
  har2client/results/case2_probe/main_full/summary.json        case 2 sanity check (counts)
  har2client/results/analysis/probing/summary.json              case 2 header tests per effort (analyze_probing.py)
  release/build/dataset/*/data.jsonl                            visible task files only, through release/sync_space_data.py
  README.md, CITATION.cff                                       install / RL snippets and the BibTeX entry
  (per-task scores for the explorer come from analyze_models.load_beam / load_models, through static_space/per_task.py)
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

import per_task  # noqa: E402  (per-task scores for the task explorer; reuses har2client/scripts/analyze_models.py)
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


NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
CASE3_CONTROL_EFFORT = "low"  # the heal_controls runs (base, placebo, hint) are Beam low; checked against the leaderboard below


def f1(x: float) -> str:
    return f"{x:.1f}".replace("-", "−")


def sgn(x: float) -> str:
    return ("+" if x >= 0 else "−") + f"{abs(x):.1f}"


def interval(m: dict) -> str:
    return f"[{sgn(m['ci_low'])}, {sgn(m['ci_high'])}]"


def join_and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def build_findings(cells: dict, v3: dict, controls: dict, facts: dict, verdicts: list[dict], probing: dict) -> list[dict]:
    """The six finding cards. Prose is written here; every number is read from the analysis files (cells, effort_facts, controls, v3)."""
    hi, lo = cells["beam-high"], cells["beam-low"]
    verdict = {v["id"].replace(" (base)", ""): v for v in verdicts}
    steps = controls["steps"]
    contrast = {c["contrast"]: c for c in controls["contrasts"]}
    if abs(steps["base"]["pass_rate"] * 100 - lo["c3a"]["pass"]) > 0.05:
        raise SystemExit("error: heal_controls base arm no longer matches Beam low / Case 3 no hint; the H22 card says low effort")
    h22_base, h22_hint = contrast["placebo - base"]["pass"], contrast["hint - placebo"]["pass"]
    h10 = controls["h10"]
    h10_hint, h10_ev = h10["b_minus_a"], h10["c_minus_a"]
    fam = v3["families"]["hint-high"]
    fam_pct = {k: fam[k]["full_repair_share"] * 100 for k in ("single", "release3", "release4")}
    ratio = round(facts["hint_gain_high"] / facts["case3_gain"])
    ratio_word = NUMBER_WORDS.get(ratio, str(ratio))
    p0, p5 = hi["c2a"]["pass"], hi["c2b"]["pass"]
    n3, n_h10 = hi["c3a"]["n"], h10["steps"]["a"]["finished"]
    n2 = hi["c2a"]["n"]
    t = {e: probing["beam"][e]["P5"]["on"] for e in EFFORTS}  # Case 2, hidden-header tasks, 5 probes: tested share and pass rate

    def pill(vid: str) -> tuple[str, str]:
        return verdict[vid]["status"].capitalize(), verdict[vid]["status"].replace(" ", "-")

    cards = [
        {"id": "H16", "pill": pill("H16"), "claim": "Beam fails to notice a silent API change more than it fails to fix it.",
         "bars": [("No hint", None, hi["c3a"]["pass"], 0), ("With hint", None, hi["c3b"]["pass"], 2)],
         "caption": f"Pass rate, %. Beam, high effort, mean of 3 runs, {n3} tasks.",
         "big": sgn(facts["hint_gain_high"]) + " pts", "big_cap": "with the hint, high effort",
         "tested": "Case 3: the same 64 broken clients, with and without a one-line hint that names what changed.",
         "result": f"{sgn(facts['hint_gain_high'])} pts [{f1(facts['hint_gain_high_lo'])}, {f1(facts['hint_gain_high_hi'])}] at high effort",
         "why": "Told what changed, Beam repairs most clients. Not told, it keeps trusting the old client and does not check it against the new traffic."},
        {"id": "H22", "pill": pill("H22"), "claim": "The hint works because of what it says, not because it is extra text.",
         "bars": [("No hint", None, steps["base"]["pass_rate"] * 100, 0), ("Placebo hint", None, steps["placebo"]["pass_rate"] * 100, 0),
                  ("Real hint", None, steps["hint"]["pass_rate"] * 100, 2)],
         "caption": f"Pass rate, %. Beam, {CASE3_CONTROL_EFFORT} effort, 1 run, {n3} tasks.",
         "big": sgn(h22_hint["mean"]) + " pts", "big_cap": f"real hint over placebo, {CASE3_CONTROL_EFFORT} effort",
         "tested": "A placebo hint of the same length that says nothing about the change.",
         "result": f"Placebo: {sgn(h22_base['mean'])} pts {interval(h22_base)}. Real hint over placebo: {sgn(h22_hint['mean'])} pts {interval(h22_hint)}.",
         "why": "The gain comes from the information about the change. Extra words alone do nothing."},
        {"id": "H17", "pill": pill("H17"), "prereg": verdict["H17"]["title"] + ".", "claim": "Thinking longer helps Beam notice, but slowly.",
         "bars": [("Low effort", None, lo["c3a"]["pass"], 0), ("High effort", None, hi["c3a"]["pass"], 1)],
         "caption": f"Pass rate, %. Beam, no hint, {n3} tasks. Low is 1 run, high is the mean of 3.",
         "big": sgn(facts["case3_gain"]) + " pts", "big_cap": "high over low effort, no hint",
         "tested": "Case 3 with no hint, low effort against high effort.",
         "result": f"{sgn(facts['case3_gain'])} pts [{f1(facts['case3_lo'])}, {f1(facts['case3_hi'])}] for {facts['case3_tokens']:.1f}× the output tokens",
         "why": f"At high effort Beam checks more of the recording against the client and catches some changes. A one-line hint gives about {ratio_word} times the gain at no extra cost."},
        {"id": "H10", "pill": pill("H10"), "claim": "When the recording hides a header, the model lacks evidence, not instructions.",
         "bars": [("As built", None, h10["steps"]["a"]["pass_rate"] * 100, 0), ("With hint", None, h10["steps"]["b"]["pass_rate"] * 100, 2),
                  ("With evidence", None, h10["steps"]["c"]["pass_rate"] * 100, 2)],
         "caption": f"Pass rate, %. Beam, high effort, {n_h10} tasks. As built is the mean of 3 runs, the others 1 run.",
         "big": sgn(h10_ev["mean"]) + " pts", "big_cap": "evidence over as built, high effort",
         "tested": f"Case 1 tasks where the recording hides a required header (n = {n_h10}): add a hint, or add the missing evidence.",
         "result": f"Hint {sgn(h10_hint['mean'])} pts, evidence {sgn(h10_ev['mean'])} pts.",
         "why": "Beam cannot guess a header it never saw. Give it the evidence and the gap closes."},
        {"id": "Case 2", "pill": ("Result", "result"), "claim": "At high effort, Beam runs its own experiments.",
         "bars": [(e.capitalize() + " effort", f"{t[e]['tested_header_n']}/{t[e]['n']} tasks", t[e]["tested_header_share"], 2) for e in EFFORTS],
         "caption": f"Share of the {t['high']['n']} hidden-header tasks where Beam tested the header, %. Beam, up to 5 probes, 1 run.",
         "big": f"{t['high']['tested_header_n']}/{t['high']['n']}", "big_cap": "tasks where Beam tested the header, high effort",
         "tested": f"Case 2, {t['high']['n']} tasks whose recording hides a required header. Beam may send 5 requests to a practice server.",
         "result": (f"Tested the header in {t['low']['tested_header_n']}/{t['low']['n']}, {t['medium']['tested_header_n']}/{t['medium']['n']}, "
                    f"{t['high']['tested_header_n']}/{t['high']['n']} tasks; pass rate on these tasks {f1(t['low']['pass_rate'])}% → "
                    f"{f1(t['medium']['pass_rate'])}% → {f1(t['high']['pass_rate'])}%. "
                    f"On all {n2} Case 2 tasks at high effort: {f1(p0)}% with no probes, {f1(p5)}% with five."),
         "why": "With more effort Beam forms a guess, designs a request to test it, and writes the client from the answer."},
        {"id": "H19", "pill": pill("H19"), "claim": "More changes per release are harder to repair.",
         "bars": [("Single change", f"n = {fam['single']['n']}", fam_pct["single"], 2), ("3 changes", f"n = {fam['release3']['n']}", fam_pct["release3"], 1),
                  ("4 changes", f"n = {fam['release4']['n']}", fam_pct["release4"], 0)],
         "caption": "Full-repair share, %. Beam, hint arm, high effort, mean of 3 runs.",
         "big": sgn(fam_pct["release4"] - fam_pct["single"]) + " pts", "big_cap": "full repair, four changes against one",
         "tested": "Case 3 with the hint, high effort, grouped by how many changes a release has.",
         "result": f"Full repair falls from {f1(fam_pct['single'])}% with one change to {f1(fam_pct['release3'])}% with three and {f1(fam_pct['release4'])}% with four.",
         "why": "Each change is one more thing to find. One miss fails the client."},
    ]
    return cards


def finding_html(c: dict) -> str:
    bars = "".join(
        f'<li><span class="fb-l">{esc(label)}' + (f'<small>{esc(sub)}</small>' if sub else "") + '</span>'
        f'<svg class="fb-t" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
        f'<rect class="fb-track" width="100" height="10"/><rect class="fb-bar fb-s{shade}" width="{value:.2f}" height="10"/></svg>'
        f'<b class="fb-v">{value:.1f}%</b></li>'
        for label, sub, value, shade in c["bars"])
    rows = "".join(f"<div><dt>{k}</dt><dd>{esc(c[key])}</dd></div>" for k, key in (("We tested", "tested"), ("Result", "result"), ("Why", "why")))
    slug = "f-" + re.sub(r"[^a-z0-9]+", "-", c["id"].lower())
    prereg = f'<p class="find-prereg"><span>Pre-registered</span> <s>{esc(c["prereg"])}</s></p>' if c.get("prereg") else ""
    return (
        f'<article class="find" aria-labelledby="{slug}">\n'
        f'  <p class="find-meta"><span class="verdict verdict-{esc(c["pill"][1])}">{esc(c["pill"][0])}</span><span class="find-id">{esc(c["id"])}</span></p>\n'
        f'  {prereg}<h3 id="{slug}">{esc(c["claim"])}</h3>\n'
        f'  <figure class="find-bars"><ul>{bars}</ul><figcaption>{esc(c["caption"])}</figcaption></figure>\n'
        f'  <p class="find-big"><b>{esc(c["big"])}</b><span>{esc(c["big_cap"])}</span></p>\n'
        f'  <dl class="find-rows">{rows}</dl>\n'
        f'</article>')


def not_settled(verdicts: list[dict]) -> str:
    """The muted line under the findings, built from verdict statuses (no hard-coded hypothesis ids)."""
    def ids(status: str) -> list[str]:
        return [v["id"].replace(" (base)", "") for v in verdicts if v["status"] == status]
    open_, untested = ids("inconclusive"), ids("not tested")
    parts = []
    if open_:
        parts.append(f"{join_and(open_)} {'is' if len(open_) == 1 else 'are'} inconclusive at this sample size")
    if untested:
        parts.append(f"{join_and(untested)} {'was' if len(untested) == 1 else 'were'} not tested")
    return esc("Not settled: " + "; ".join(parts) + ". Full table in ") + '<a href="#method">Method</a>.' if parts else ""


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
emoji: 🧪
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
    probing = load(RESULTS / "analysis" / "probing" / "summary.json")

    cells = build_cells(comparison)
    detail = build_beam_detail(controls, v3, cells)
    signature = build_signature(controls, cells)
    verdicts = build_verdicts(controls, v3)
    facts = build_effort_facts(effort_summary, v3)
    tasks = build_tasks()
    scores = per_task.build_per_task([k for k, _m, _e in SETTINGS], BENCHMARKS)
    per_task.attach(tasks["tasks"], scores)
    n_checked = per_task.check_means(tasks["tasks"], scores, comparison, BENCHMARKS)  # raises unless every cell matches the leaderboard
    tasks["result_settings"], tasks["fail_kinds"] = scores["settings"], scores["fail_kinds"]
    findings = build_findings(cells, v3, controls, facts, verdicts, probing)
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
        "FINDINGS": "\n".join(finding_html(c) for c in findings),
        "NOT_SETTLED": not_settled(verdicts),
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
    tasks_kb = (out / "data" / "tasks.json").stat().st_size / 1024
    print(f"built {out} ({len(tasks['tasks'])} tasks, app.js {js_kb:.1f} KB, tasks.json {tasks_kb:.0f} KB)")
    print(f"per-task check: {n_checked} of {len(SETTINGS) * len(BENCHMARKS)} (setting, benchmark) means match the leaderboard within {per_task.MEAN_TOLERANCE}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = ap.parse_args()
    build(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
