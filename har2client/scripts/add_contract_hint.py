#!/usr/bin/env python3
"""Make the "contract hint" arm of a case 3 v3 task set (PREREG.md, v3 pilot deviation).

Usage:
  uv run python scripts/add_contract_hint.py --tasks runs/heal_v3_eval --out runs/heal_v3_eval_hint [--variant {contract,notice,placebo}]

Copies every task dir of `--tasks` into `--out` and adds one paragraph to `goal.md`, after the line that ends with `ANCHOR`.
`--variant` picks the paragraph: `contract` (`HINT`, the default, the hint arm), `notice` (`NOTICE`: the warning only) or
`placebo` (`PLACEBO`: same length, no information about the API change; PREREG.md H22-H23, 2026-10-26). All other files stay byte-identical. Exit code: 0 all done, 1 a goal.md has no anchor line.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ANCHOR = "works with the new version.\n"
HINT = (
    "\n**Important:** the new API may also have changed its wire format (field names, units, value formats, nesting). "
    "Your methods must still return exactly what `client_v1.py` returns (the same field names, units and formats), and accept the same "
    "arguments. Translate between the new wire format and the old one in both directions: in responses and in request bodies and query parameters.\n"
)
NOTICE = (
    "\n**Important:** the new API may also have changed its wire format, without raising errors. "
    "Your methods must still return exactly what `client_v1.py` returns.\n"
)
PLACEBO = (
    "\n**Important:** work carefully. Read every file in this folder before you change any code, and keep your changes small "
    "and easy to review. Your methods must keep the same names and arguments as in `client_v1.py`, and `client.py` must "
    "run with no extra packages.\n"
)
VARIANTS = {"contract": HINT, "notice": NOTICE, "placebo": PLACEBO}


def add_hint(goal: str, text: str = HINT) -> str:
    """`goal` with `text` after the first line that ends with `ANCHOR`. Raises ValueError when there is no such line."""
    at = goal.find(ANCHOR)
    if at < 0:
        raise ValueError(f"no line ends with {ANCHOR!r}")
    cut = at + len(ANCHOR)
    return goal[:cut] + text + goal[cut:]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tasks", required=True, type=Path, help="directory of task dirs (each has task.json and goal.md)")
    p.add_argument("--out", required=True, type=Path, help="directory that receives the hint copies (must not exist)")
    p.add_argument("--variant", choices=sorted(VARIANTS), default="contract", help="which paragraph to add (default: contract)")
    args = p.parse_args(argv)
    if args.out.exists():
        print(f"{args.out} exists; remove it or pick another --out", file=sys.stderr)
        return 1
    dirs = sorted(d for d in args.tasks.iterdir() if (d / "task.json").is_file())
    bad = []
    for d in dirs:
        try:
            goal = add_hint((d / "goal.md").read_text(), VARIANTS[args.variant])
        except ValueError as exc:
            bad.append(f"{d.name}: {exc}")
            continue
        shutil.copytree(d, args.out / d.name)
        (args.out / d.name / "goal.md").write_text(goal)
    for line in bad:
        print(f"FAILED {line}", file=sys.stderr)
    print(f"{'hint' if args.variant == 'contract' else args.variant} arm:{len(dirs) - len(bad)}/{len(dirs)} tasks into {args.out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
