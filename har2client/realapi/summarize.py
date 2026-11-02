"""Summarise realapi/results.csv into results/realapi/{summary.json, report.md, results.csv}."""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "results" / "realapi"
LABELS = {"beam-high": "Beam (high)", "beam-low": "Beam (low)", "gpt-oss-120b-high": "GPT-OSS-120B (high)",
          "qwen3.5-397b-thinking": "Qwen3.5-397B-A17B (thinking on)"}
APIS = {"restful_booker": "Restful-Booker", "dummyjson": "DummyJSON", "petstore": "Petstore v3"}


def main() -> None:
    rows = list(csv.DictReader((HERE / "results.csv").open()))
    by = defaultdict(list)
    for r in rows:
        by[(r["setting"], r["api"])].append(r)
    summary = {}
    for setting in [s for s in LABELS if any(k[0] == s for k in by)]:
        per_api, tp, tt, perfect, runs = {}, 0, 0, 0, 0
        for api in APIS:
            rs = by.get((setting, api), [])
            passed = sum(int(r["passed"]) for r in rs)
            total = sum(int(r["total"] or 0) for r in rs)
            per_api[api] = {"runs": len(rs), "tests_passed": passed, "tests_total": total,
                            "perfect_runs": sum(r["passed"] == r["total"] for r in rs),
                            "failed_tests": sorted({t for r in rs for t in (r["failed"] or "").split(";") if t and t != "all"}),
                            "statuses": sorted({r["status"] for r in rs if r["status"] != "ok"})}
            tp, tt, perfect, runs = tp + passed, tt + total, perfect + per_api[api]["perfect_runs"], runs + len(rs)
        summary[setting] = {"label": LABELS[setting], "tests_passed": tp, "tests_total": tt, "pass_rate": round(100 * tp / tt, 1) if tt else None,
                            "perfect_runs": perfect, "runs": runs, "per_api": per_api}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    shutil.copyfile(HERE / "results.csv", OUT / "results.csv")
    lines = ["# Real-API demo", "", "Clients written from real browser recordings of three public practice APIs, graded by live tests "
             "with fresh data (`har2client/realapi/`). 3 runs per setting and API; the unchanged Case 1 prompt.", "",
             "| Setting | " + " | ".join(APIS.values()) + " | All tests | Perfect runs |", "|---|" + "---|" * (len(APIS) + 2)]
    for s in summary.values():
        cells = [f"{a['tests_passed']}/{a['tests_total']}" for a in s["per_api"].values()]
        lines.append(f"| {s['label']} | " + " | ".join(cells) + f" | {s['tests_passed']}/{s['tests_total']} ({s['pass_rate']}%) | {s['perfect_runs']}/{s['runs']} |")
    lines += ["", "Failed tests per setting and API:", ""]
    for s in summary.values():
        for api, a in s["per_api"].items():
            if a["failed_tests"] or a["statuses"]:
                lines.append(f"- {s['label']}, {APIS[api]}: {', '.join(a['statuses'] + a['failed_tests'])}")
    lines += ["", "Scope: a small external check (3 APIs, 17 tests per run), not a benchmark split. Public practice APIs are shared "
              "and may appear in training data."]
    (OUT / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
