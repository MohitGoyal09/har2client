#!/usr/bin/env python3
"""Grader-validity audit: how trustworthy is `grade.grade`?  (research plan item U4)

Usage:
  uv run python scripts/grader_audit.py --tasks runs/set30 --out runs/audit/set30 [--workers 4]

Per task: (1) oracle = the reference client (must score 1.0); (2) trivial agents (empty client,
HAR replay; should score about 0); (3) every applicable mutant from `har2client.mutants`
(killed = pass_rate < 1.0). Mutants of a task whose oracle is below 1.0 are not graded.
Writes `<out>/audit.csv`, `summary.md`, `summary.json` and every graded client under `<out>/clients/`.
No model calls and no outbound network: the hard-coded-host mutant runs behind a blocked transport.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from har2client import mutants
from har2client.grade import grade

COLUMNS = [
    "task", "api_id", "auth_type", "kind", "name", "op_id", "detail", "status", "skip_reason",
    "pass_rate", "passed", "total", "outcome", "survivor_class", "survivor_reason",
    "failure_counts", "invented_endpoint_calls", "hardcoded_secret", "hardcoded_host", "duration_s",
]
KIND_ORACLE, KIND_TRIVIAL, KIND_MUTANT = "oracle", "trivial", "mutant"
TRIVIAL = {"empty_client": mutants.empty_client, "har_replay_client": mutants.har_replay_client}
TRIVIAL_MAX_OK = 0.1
EXIT_OK, EXIT_SETUP, EXIT_ORACLE, EXIT_TRIVIAL = 0, 2, 3, 4


def _base_row(ctx: mutants.TaskContext, kind: str, name: str) -> dict:
    row = dict.fromkeys(COLUMNS, "")
    row.update(
        task=ctx.task_id, api_id=ctx.op_table.get("api_id", ""), auth_type=ctx.op_table["security"]["type"],
        kind=kind, name=name,
    )
    return row


def _check_plausible(ctx: mutants.TaskContext, m: mutants.Mutant) -> None:
    compile(m.source, f"<{m.operator}>", "exec")
    if m.source == ctx.source:
        raise AssertionError(f"{ctx.task_id}/{m.operator}: mutant equals the reference")


def _grade_row(task_dir: Path, ctx: mutants.TaskContext, kind: str, name: str, source: str, out: Path,
               m: mutants.Mutant | None = None) -> dict:
    row = _base_row(ctx, kind, name)
    path = out / "clients" / ctx.task_id / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    if m:
        row.update(op_id=m.op_id or "", detail=m.detail)
    try:
        g = grade(task_dir, path)
    except Exception as exc:  # noqa: BLE001 - one broken grade must not lose the rest of the audit
        row.update(status="error", skip_reason=f"{type(exc).__name__}: {exc}")
        return row
    row.update(
        status="graded", pass_rate=g["pass_rate"], passed=g["passed"], total=g["total"],
        failure_counts=json.dumps(g["failure_counts"], sort_keys=True),
        invented_endpoint_calls=g["invented_endpoint_calls"], hardcoded_secret=g["hardcoded_secret"],
        hardcoded_host=g["hardcoded_host"], duration_s=g["duration_s"],
    )
    if m:
        row["outcome"] = "killed" if g["pass_rate"] < 1.0 else "survived"
        if row["outcome"] == "survived":
            row["survivor_class"], row["survivor_reason"] = mutants.classify_survivor(m, g)
    return row


def _task_dirs(tasks: Path) -> list[Path]:
    return sorted(p for p in tasks.iterdir() if (p / "task.json").exists() and (p / "op_table.json").exists())


def run_audit(tasks: Path, out: Path, workers: int = 4, only: list[str] | None = None) -> list[dict]:
    tasks, out = Path(tasks), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    loaded: list[tuple[Path, mutants.TaskContext]] = []
    for d in _task_dirs(tasks):
        if only and d.name not in only:
            continue
        if json.loads((d / "task.json").read_text(encoding="utf-8")).get("tier") == "B":
            print(f"skip {d.name}: Tier B (the reference-client audit covers Tier A)", file=sys.stderr)
            continue
        loaded.append((d, mutants.load_context(d)))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        jobs = []
        for d, ctx in loaded:
            jobs.append(pool.submit(_grade_row, d, ctx, KIND_ORACLE, "oracle", ctx.source, out))
            for name, make in TRIVIAL.items():
                jobs.append(pool.submit(_grade_row, d, ctx, KIND_TRIVIAL, name, make(ctx), out))
        phase1 = [j.result() for j in jobs]
        rows += phase1
        sound = {r["task"] for r in phase1 if r["kind"] == KIND_ORACLE and r["pass_rate"] == 1.0}

        jobs = []
        for d, ctx in loaded:
            if ctx.task_id not in sound:
                continue
            found, skipped = mutants.generate_mutants(ctx)
            for s in skipped:
                row = _base_row(ctx, KIND_MUTANT, s.operator)
                row.update(status="skipped", skip_reason=s.reason)
                rows.append(row)
            for m in found:
                _check_plausible(ctx, m)
                jobs.append(pool.submit(_grade_row, d, ctx, KIND_MUTANT, m.operator, m.source, out, m))
        rows += [j.result() for j in jobs]
    return rows


# ----------------------------------------------------------------------------- summary


def _graded_mutants(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["kind"] == KIND_MUTANT and r["status"] == "graded"]


def summarize(rows: list[dict]) -> dict:
    oracle = [r for r in rows if r["kind"] == KIND_ORACLE]
    graded = _graded_mutants(rows)
    killed = [r for r in graded if r["outcome"] == "killed"]
    survivors = [r for r in graded if r["outcome"] == "survived"]
    equivalent = [r for r in survivors if r["survivor_class"] == mutants.CLASS_EQUIVALENT]

    per_operator: dict[str, dict] = {}
    for name in sorted({r["name"] for r in rows if r["kind"] == KIND_MUTANT}):
        mine = [r for r in graded if r["name"] == name]
        k = sum(r["outcome"] == "killed" for r in mine)
        per_operator[name] = {
            "graded": len(mine), "killed": k, "survived": len(mine) - k,
            "kill_rate": k / len(mine) if mine else None,
            "skipped": sum(r["kind"] == KIND_MUTANT and r["name"] == name and r["status"] == "skipped" for r in rows),
            "survivor_classes": dict(Counter(r["survivor_class"] for r in mine if r["outcome"] == "survived")),
        }
    trivial: dict[str, dict] = {}
    for name in TRIVIAL:
        mine = [r for r in rows if r["kind"] == KIND_TRIVIAL and r["name"] == name and r["status"] == "graded"]
        rates = [r["pass_rate"] for r in mine]
        trivial[name] = {
            "tasks": len(mine), "mean_pass_rate": sum(rates) / len(rates) if rates else None,
            "max_pass_rate": max(rates, default=None), "nonzero": sorted(r["task"] for r in mine if r["pass_rate"] > 0),
            "hardcoded_secret_flagged": sum(bool(r["hardcoded_secret"]) for r in mine),
            "hardcoded_host_flagged": sum(bool(r["hardcoded_host"]) for r in mine),
        }
    # a trivial agent above the limit (or one that could not be graded) is a grader false pass the audit must not hide
    trivial_failures = sorted(
        f"{r['task']}/{r['name']}" for r in rows
        if r["kind"] == KIND_TRIVIAL and (r["status"] != "graded" or r["pass_rate"] > TRIVIAL_MAX_OK)
    )
    skip_reasons: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["kind"] == KIND_MUTANT and r["status"] == "skipped":
            skip_reasons[r["name"]][r["skip_reason"]] += 1
    return {
        "tasks": len(oracle),
        "oracle_failures": sorted(r["task"] for r in oracle if r["pass_rate"] != 1.0),
        "oracle_scores": {r["task"]: r["pass_rate"] for r in oracle},
        "trivial": trivial,
        "trivial_failures": trivial_failures,
        "mutants_graded": len(graded),
        "mutants_killed": len(killed),
        "mutants_survived": len(survivors),
        "mutants_errored": sum(r["kind"] == KIND_MUTANT and r["status"] == "error" for r in rows),
        "mutants_skipped": sum(r["kind"] == KIND_MUTANT and r["status"] == "skipped" for r in rows),
        "kill_rate": len(killed) / len(graded) if graded else None,
        "kill_rate_excluding_equivalent": (len(killed) / (len(graded) - len(equivalent))) if len(graded) > len(equivalent) else None,
        "survivor_classes": dict(Counter(r["survivor_class"] for r in survivors)),
        "per_operator": per_operator,
        "survivors": [
            {k: r[k] for k in ("task", "name", "op_id", "detail", "survivor_class", "survivor_reason")} for r in survivors
        ],
        "skip_reasons": {k: dict(v) for k, v in skip_reasons.items()},
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render_summary(s: dict) -> str:
    out = ["# Grader-validity audit", ""]
    out += ["## Oracle", "", f"{s['tasks']} tasks; reference client scores 1.0 on {s['tasks'] - len(s['oracle_failures'])}."]
    if s["oracle_failures"]:
        out.append(f"ORACLE FAILURES (mutants not graded): {', '.join(s['oracle_failures'])}")
    if s["trivial_failures"]:
        out += ["", f"TRIVIAL AGENTS ABOVE {TRIVIAL_MAX_OK} OR UNGRADED: {', '.join(s['trivial_failures'])}"]
    out += ["", "## Trivial agents", "", "| agent | tasks | mean pass_rate | max | tasks > 0 | hardcoded_secret flagged | hardcoded_host flagged |", "|---|---|---|---|---|---|---|"]
    for name, t in s["trivial"].items():
        mean = "n/a" if t["mean_pass_rate"] is None else f"{t['mean_pass_rate']:.3f}"
        out.append(f"| {name} | {t['tasks']} | {mean} | {t['max_pass_rate']} | {', '.join(t['nonzero']) or '-'} | {t['hardcoded_secret_flagged']} | {t['hardcoded_host_flagged']} |")
    out += [
        "", "## Kill rate", "",
        f"Overall: {s['mutants_killed']}/{s['mutants_graded']} = {_pct(s['kill_rate'])} "
        f"(excluding EQUIVALENT survivors: {_pct(s['kill_rate_excluding_equivalent'])}). "
        f"Skipped (operator not applicable): {s['mutants_skipped']}. Errors: {s['mutants_errored']}.",
        f"Survivor classes: {s['survivor_classes'] or 'none'}", "",
        "| operator | graded | killed | survived | kill rate | skipped | survivor classes |", "|---|---|---|---|---|---|---|",
    ]
    for name, p in s["per_operator"].items():
        out.append(f"| {name} | {p['graded']} | {p['killed']} | {p['survived']} | {_pct(p['kill_rate'])} | {p['skipped']} | {p['survivor_classes'] or '-'} |")
    out += ["", "## Survivors", ""]
    if not s["survivors"]:
        out.append("None.")
    else:
        out += ["| task | operator | op | class | reason |", "|---|---|---|---|---|"]
        order = {mutants.CLASS_GRADER_GAP: 0, mutants.CLASS_FLAG_ONLY: 1, mutants.CLASS_EQUIVALENT: 2}
        for v in sorted(s["survivors"], key=lambda v: (order.get(v["survivor_class"], 9), v["name"], v["task"])):
            out.append(f"| {v['task']} | {v['name']} | {v['op_id']} | {v['survivor_class']} | {v['survivor_reason']} |")
    out += ["", "## Skipped operators", ""]
    for name, reasons in sorted(s["skip_reasons"].items()):
        out.append(f"- {name}: " + "; ".join(f"{n}x {why}" for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])))
    return "\n".join(out) + "\n"


def write_outputs(rows: list[dict], out: Path) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "audit.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    s = summarize(rows)
    (out / "summary.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(render_summary(s), encoding="utf-8")
    return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True, type=Path, help="directory of built Tier A task dirs")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", nargs="*", help="task ids to audit (default: all)")
    args = ap.parse_args(argv)
    if not args.tasks.is_dir():
        print(f"no such tasks directory: {args.tasks}", file=sys.stderr)
        return EXIT_SETUP
    rows = run_audit(args.tasks, args.out, args.workers, args.only)
    if not rows:
        print("no tasks found", file=sys.stderr)
        return EXIT_SETUP
    s = write_outputs(rows, args.out)
    print(render_summary(s))
    if s["oracle_failures"]:
        return EXIT_ORACLE
    return EXIT_TRIVIAL if s["trivial_failures"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
