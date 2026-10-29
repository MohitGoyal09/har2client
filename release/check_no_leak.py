#!/usr/bin/env python3
"""Fail if the exported dataset (or card, Croissant file, Space data) contains hidden grading data.

Usage:
  python3 release/check_no_leak.py [--dataset DIR] [--tier-a DIR] [--tier-b DIR] [--probe DIR] [--heal DIR]
                                  [--heal-v3 DIR] [--heal-v3-hint DIR] [--extra FILE ...]

It reads the hidden files of every task dir given (task.json, tests.json, ref_client.py) and looks for:
  1. test credentials and the test secret, and every seed (as a whole number of 6+ digits);
  2. every string value of 8+ characters in tests.json (hidden test arguments and ids);
  3. any line of 40+ characters of the reference client;
  4. names of hidden files (task.json, tests.json, ...), which a row must not mention;
  5. row hygiene: only allow-listed columns, and the canary GUID in every row.
Exit code 0 = clean, 1 = a leak or hygiene error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from h2c_release import (
    DATASET_DIR,
    HIDDEN_FILES,
    RELEASE_DIR,
    VISIBLE_FILES,
    add_dir_args,
    canary_guid,
    read_any,
    load_json,
    selected_dirs,
    task_dirs,
)

MIN_VALUE_LEN = 8  # shorter test values (for example "byw") collide with ordinary words by chance
MIN_REF_LINE_LEN = 40
MIN_SEED_DIGITS = 6
ALLOWED_COLUMNS = {
    "task_id", "case", "tier", "title", "config", "features", "metadata", "goal_md", "observation",
    "observation_format", "observation_file", "canary", "api_id", "spec_license_name", "spec_license_url",
    "spec_origin_url", "spec_license_class", "client_v1", "failure_txt", "changelog_md",
}
# Real-looking secrets. The synthetic mock credentials (sk_<24 hex>, user_..., pw_...) do not match these.
SECRET_PATTERNS = {
    "jwt_or_reflection_key": re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),
    "hub_token": re.compile(r"\bhf_[A-Za-z0-9]{20,}"),
    "api_key_sk-": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
ENV_SECRET_NAME = re.compile(r"(TOKEN|SECRET|API_?KEY|PASSWORD)", re.I)
MIN_ENV_SECRET_LEN = 16
CANARY_FILES = ("README.md", "CANARY.txt", "MANIFEST.json")  # exported files that must carry the canary GUID
DEFAULT_EXTRA = [RELEASE_DIR / "DATASET_CARD.md", RELEASE_DIR / "croissant.json", RELEASE_DIR / "space" / "data"]

Needle = tuple[str, str, str]  # (kind, task, value)


def leaves(node, kind: type) -> list:
    """All leaf values of the given type in a nested JSON value (bool is not an int here)."""
    if isinstance(node, bool):
        return []
    if isinstance(node, kind):
        return [node]
    if isinstance(node, dict):
        return [x for v in node.values() for x in leaves(v, kind)]
    if isinstance(node, list):
        return [x for v in node for x in leaves(v, kind)]
    return []


def visible_text(task_dir: Path) -> str:
    """Everything the solver may see in this task dir (goal, recording, and for case 3 the old client, failure log and release notes)."""
    paths = [task_dir / n for n in VISIBLE_FILES] + sorted(task_dir.glob("session.*"))
    return "".join(p.read_text(encoding="utf-8") for p in paths if p.is_file())


def hidden_needles(task_dir: Path) -> list[Needle]:
    """Hidden values from one task dir. A value that also occurs in the task's visible files is visible by design
    (a shared vocabulary such as the status "delivered", or boilerplate in client_v1.py) and is skipped."""
    task = task_dir.name
    visible = visible_text(task_dir)
    out: list[Needle] = []
    info_path = task_dir / "task.json"
    if info_path.is_file():
        info = load_json(info_path)
        secret = info.get("test_secret")
        if isinstance(secret, str) and secret:
            out.append(("test_secret", task, secret))
        out += [("test_credential", task, v) for v in leaves((info.get("credentials") or {}).get("test", {}), str)]
        out += [("seed", task, str(s)) for s in leaves(info.get("seeds", {}), int) if len(str(abs(s))) >= MIN_SEED_DIGITS]
    tests_path = task_dir / "tests.json"
    if tests_path.is_file():
        # Only the call arguments are hidden; the method names ("method", "op") are in the visible goal.md.
        kwargs = [t.get("kwargs", {}) for t in load_json(tests_path)]
        out += [("hidden_test_value", task, v) for v in leaves(kwargs, str) if len(v) >= MIN_VALUE_LEN and v not in visible]
    ref_path = task_dir / "ref_client.py"
    if ref_path.is_file():
        lines = (ln.strip() for ln in ref_path.read_text(encoding="utf-8").splitlines())
        out += [("ref_client_line", task, ln) for ln in lines if len(ln) >= MIN_REF_LINE_LEN and ln not in visible]
    return out


SAME_TASK_KINDS = {"hidden_test_value", "ref_client_line"}  # words, dates and boilerplate repeat across tasks: count only in their own row


def env_secrets() -> list[tuple[str, str]]:
    """(variable name, value) of secret-looking environment variables, so a token that sits in this shell cannot be uploaded."""
    return [(k, v) for k, v in os.environ.items() if ENV_SECRET_NAME.search(k) and len(v) >= MIN_ENV_SECRET_LEN]


def hidden_name_pattern(name: str) -> re.Pattern:
    """A hidden file name as a whole token: `set30_regrade_newtests.json` does not count as `tests.json`."""
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?!\w)")


def scan_text(label: str, text: str, needles: list[Needle], task: str | None = None) -> list[str]:
    """Look for needles in text. `task` is the row's task id (None for files that are not rows)."""
    errors = []
    for kind, owner, needle in needles:
        if kind in SAME_TASK_KINDS and owner != task:
            continue
        hit = needle in text and (kind != "seed" or re.search(rf"(?<!\d){re.escape(needle)}(?!\d)", text))  # cheap test first
        if hit:
            errors.append(f"LEAK [{kind}] value of hidden task {owner!r} found in {label}: {needle[:60]!r}")
    for name in HIDDEN_FILES:
        if hidden_name_pattern(name).search(text):
            errors.append(f"LEAK [hidden_file_name] {name!r} mentioned in {label}")
    for kind, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"LEAK [secret:{kind}] secret-looking string found in {label} (value not shown)")
    for var, value in env_secrets():
        if value in text:
            errors.append(f"LEAK [secret:env] the value of environment variable {var} found in {label} (value not shown)")
    return errors


def row_text(line: str, row: dict) -> str:
    """The raw JSONL line plus every decoded string in it, so values with quotes, newlines or unicode escapes still match."""
    return line + "\n" + "\n".join(leaves(row, str))


def check_rows(path: Path, canary: str, needles: list[Needle], known_tasks: set[str] | None = None) -> list[str]:
    """Row hygiene, plus a scan of each row against the global needles and its own task's test values.
    A row whose task was not scanned (no task dir given, or no hidden files in it) is an error: it could not be checked."""
    errors = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        row = json.loads(line)
        label = f"{path.parent.name}/{path.name}:{i}"
        extra = set(row) - ALLOWED_COLUMNS
        if extra:
            errors.append(f"HYGIENE {label}: columns not on the allow-list: {sorted(extra)}")
        if canary not in str(row.get("canary", "")):
            errors.append(f"HYGIENE {label}: canary GUID missing")
        if known_tasks is not None and row.get("task_id") not in known_tasks:
            errors.append(f"HYGIENE {label}: task {row.get('task_id')!r} was not scanned (no task dir with hidden files was given for it)")
        errors += scan_text(label, row_text(line, row), needles, task=row.get("task_id"))
    return errors


def scan_tasks_json(label: str, text: str, needles: list[Needle]) -> list[str]:
    """Space `tasks.json` is {task id: record}: scan each record against its own task's test values as well."""
    errors = scan_text(label, text, needles)
    try:
        records = json.loads(text)
    except ValueError:
        return errors + [f"HYGIENE {label}: not valid JSON"]
    for task, rec in (records.items() if isinstance(records, dict) else []):
        errors += scan_text(f"{label}[{task}]", row_text(json.dumps(rec), rec), needles, task=task)
    return errors


def scan_parquet(path: Path, needles: list[Needle]) -> list[str]:
    """Parquet is uploaded as is, so a file that cannot be read here is an error, not a note."""
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415
    except ImportError:
        return [f"UNSCANNED {path}: pyarrow is missing, so this file cannot be checked. Install pyarrow or delete the file."]
    errors = []
    for i, row in enumerate(pq.read_table(path).to_pylist(), start=1):
        text = json.dumps(row, default=str, ensure_ascii=False)
        errors += scan_text(f"{path}:{i}", row_text(text, row), needles, task=row.get("task_id"))
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_dir_args(ap)
    ap.add_argument("--dataset", type=Path, default=DATASET_DIR)
    ap.add_argument("--extra", type=Path, nargs="*", default=DEFAULT_EXTRA, help="more files to scan (card, Croissant, Space data)")
    args = ap.parse_args(argv)

    needles: list[Needle] = []
    known_tasks: set[str] = set()
    n_tasks = 0
    for root in selected_dirs(args).values():
        for d in task_dirs(root):
            found = hidden_needles(d)
            needles += found
            n_tasks += 1
            if found:
                known_tasks |= {d.name, (load_json(d / "task.json").get("task_id") if (d / "task.json").is_file() else None) or d.name}
    if not needles:
        print("error: no hidden values found; check the task dir arguments", file=sys.stderr)
        return 1
    if not args.dataset.is_dir():
        print(f"error: {args.dataset} does not exist; run export_dataset.py first", file=sys.stderr)
        return 1

    canary = canary_guid()
    errors: list[str] = []
    notes: list[str] = []
    n_files = 0
    for path in sorted(args.dataset.rglob("*")):
        if not path.is_file():
            continue
        n_files += 1
        rel = str(path.relative_to(args.dataset))
        if path.suffix == ".parquet":
            errors += scan_parquet(path, needles)
            continue
        if path.name == "data.jsonl":
            errors += check_rows(path, canary, needles, known_tasks)
            continue
        text = read_any(path)  # PNG figures under results/ are read as latin-1
        errors += scan_text(rel, text, needles)
        if rel in CANARY_FILES and canary not in text:
            errors.append(f"HYGIENE {rel}: canary GUID missing")
    if not any(args.dataset.rglob("data.jsonl")):
        errors.append(f"HYGIENE {args.dataset}: no data.jsonl found, so no rows were checked")
    for extra in args.extra or []:
        files = [p for p in sorted(Path(extra).rglob("*")) if p.is_file()] if Path(extra).is_dir() else [Path(extra)]
        for path in files:
            if path.is_file():
                n_files += 1
                text = read_any(path)
                errors += scan_tasks_json(str(path), text, needles) if path.name == "tasks.json" else scan_text(str(path), text, needles)

    print(f"scanned {n_files} files against {len(needles)} hidden values from {n_tasks} task dirs")
    for line in notes:
        print(line)
    unique = list(dict.fromkeys(errors))
    for e in unique:
        print(e)
    if unique:
        print(f"FAIL: {len(unique)} problem(s)")
        return 1
    print("PASS: no hidden data found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
