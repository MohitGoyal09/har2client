#!/usr/bin/env python3
"""Refuse to publish while placeholder markers are still in the public text.

Usage:  python3 release/publish_guard.py [--dry-run] [FILE ...]

Scans the dataset card, `croissant.json`, the Space README and the citation (the BibTeX block is in the card) for
the markers below. With no FILE it scans the sources in release/; --staged adds the built copies in release/build/.
Without --dry-run it exits 1 if any marker is found. With --dry-run it prints a warning that lists them and exits 0.
Standard library only; it makes no network call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKERS = ("TBD", "TODO", "<code repository>", "Open decision")  # "AUTHOR_2_TBD" and "CONTACT_TBD" contain "TBD"
SOURCES = (HERE / "DATASET_CARD.md", HERE / "croissant.json", HERE / "space" / "README.md", HERE / "CITATION.cff")
STAGED = (HERE / "build" / "dataset" / "README.md", HERE / "build" / "dataset" / "croissant.json", HERE / "build" / "space" / "README.md")


def find_markers(paths) -> dict[str, list[tuple[int, str]]]:
    """{file: [(line number, marker)]} for every marker found. Missing files are skipped."""
    found: dict[str, list[tuple[int, str]]] = {}
    for path in map(Path, paths):
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for marker in MARKERS:
                if marker in line:
                    found.setdefault(str(path), []).append((number, marker))
    return found


def report(found: dict[str, list[tuple[int, str]]]) -> str:
    lines = [f"  {path}:{number}  {marker}" for path, hits in found.items() for number, marker in hits]
    markers = sorted({m for hits in found.values() for _, m in hits})
    return f"placeholder markers still present ({', '.join(markers)}):\n" + "\n".join(lines)


def check(paths, dry_run: bool) -> int:
    """0 when clean, or when dry_run (the warning is printed). 1 when a real publish must be refused."""
    found = find_markers(paths)
    if not found:
        print("publish guard: no placeholder markers found")
        return 0
    if dry_run:
        print("WARNING (dry run only; a real publish would be refused): " + report(found))
        return 0
    print("error: refusing to publish, " + report(found), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--staged", action="store_true", help="also scan release/build/")
    args = ap.parse_args(argv)
    return check(args.files or [*SOURCES, *(STAGED if args.staged else ())], args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
