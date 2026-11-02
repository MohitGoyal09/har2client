"""Real-API demo: a model writes a client from each real recording (the unchanged Case 1 prompt), then live tests grade it.

    REFLECTION_API_KEY=... uv run python realapi/run.py --model beam --effort high --runs 3
    HF_TOKEN=... uv run python realapi/run.py --model gpt-oss-120b-high --runs 3     (slugs from data/comparison_models.json)
Writes realapi/runs/<api>/<setting>-r<n>/{client.py, solve_log.json, live.json} and realapi/results.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import functools

from har2client.solve import beam, openai_compat

HERE = Path(__file__).resolve().parent
APIS = ("restful_booker", "dummyjson", "petstore")
LIVE_TOTALS = {"restful_booker": range(6), "dummyjson": range(6), "petstore": range(5)}  # tests per suite (live_tests.py)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="beam", help="'beam' or a slug from data/comparison_models.json")
    p.add_argument("--effort", default="high", help="Beam's reasoning_effort (comparison models use their pinned setting)")
    p.add_argument("--runs", type=int, default=3)
    args = p.parse_args()
    if args.model == "beam":
        setting, solve = f"beam-{args.effort}", functools.partial(beam.solve, effort=args.effort)
    else:
        cfg = next(m for m in json.loads((HERE.parent / "data" / "comparison_models.json").read_text())["models"] if m["slug"] == args.model)
        setting = args.model
        solve = functools.partial(openai_compat.solve, effort=cfg.get("reasoning_effort") or "high", model=cfg["id"],
                                  supports_reasoning_effort=cfg.get("reasoning_effort") is not None, enable_thinking=cfg.get("enable_thinking"))
    rows = []
    for api in APIS:
        for r in range(1, args.runs + 1):
            out = HERE / "runs" / api / f"{setting}-r{r}" / "client.py"
            status = "no_code"
            if not out.is_file():
                try:
                    log = solve(HERE / "tasks" / api, out)
                    print(f"{api} r{r}: solve {log['status']}", flush=True)
                except Exception as exc:  # e.g. the recording does not fit the model's context window
                    status = "context_overflow" if "context length" in str(exc) else f"error: {type(exc).__name__}"
                    print(f"{api} r{r}: {status}", flush=True)
            if not out.is_file():
                total = len(LIVE_TOTALS[api])
                rows.append({"api": api, "setting": setting, "run": r, "passed": 0, "total": total, "status": status, "failed": "all"})
                continue
            res = json.loads(subprocess.run([sys.executable, str(HERE / "live_tests.py"), api, str(out)], capture_output=True, text=True, timeout=600).stdout)
            (out.parent / "live.json").write_text(json.dumps(res, indent=1))
            log = json.loads((out.parent / "solve_log.json").read_text()) if (out.parent / "solve_log.json").is_file() else {}
            usage = log.get("usage") or {}
            rows.append({"api": api, "setting": setting, "run": r, "passed": res["passed"], "total": res["total"], "status": "ok",
                         "completion_tokens": usage.get("completion_tokens"), "failed": ";".join(t["test"] for t in res["tests"] if not t["ok"])})
            print(f"{api} r{r}: {res['passed']}/{res['total']} failed={rows[-1]['failed']}", flush=True)
    path = HERE / "results.csv"
    old = [row for row in csv.DictReader(path.open())] if path.is_file() else []
    keep = [row for row in old if row.get("setting") != setting]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["api", "setting", "run", "passed", "total", "status", "completion_tokens", "failed"])
        w.writeheader()
        w.writerows(keep + rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
