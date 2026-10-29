#!/usr/bin/env python3
"""Create or update the Hugging Face dataset repo and Space from the staged folders. Called by publish.sh.

Needs `huggingface_hub` (publish.sh runs it through `uv run --with huggingface_hub`) and the HF_TOKEN environment variable.
With --dry-run it only prints what it would do and needs no token and no network.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path

import publish_guard

COMMIT_MESSAGE = "Release har2client"
# Remote files with these extensions that are not in the staged folder are deleted, so a re-run cannot leave an old file
# (for example a removed config) online. `.gitattributes` has no extension in this list and stays.
DELETE_PATTERNS = ["*.jsonl", "*.parquet", "*.json", "*.md", "*.csv", "*.txt", "*.py"]
IGNORE_PATTERNS = ["__pycache__/*", "*/__pycache__/*", "*.pyc", ".DS_Store"]


def files_to_upload(folder: Path) -> list[tuple[str, int]]:
    """(relative path, size in bytes) of every file `upload_folder` would send: the folder minus IGNORE_PATTERNS."""
    found = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if not any(fnmatch.fnmatch(rel, pat) for pat in IGNORE_PATTERNS):
            found.append((rel, path.stat().st_size))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--namespace", required=True, help="Hub user or organisation name")
    ap.add_argument("--dataset-name", default="har2client")
    ap.add_argument("--space-name", default="har2client-leaderboard")
    ap.add_argument("--dataset-dir", required=True, type=Path)
    ap.add_argument("--space-dir", required=True, type=Path)
    ap.add_argument("--private", action="store_true", help="create the repos as private (default: public)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    dataset_id = f"{args.namespace}/{args.dataset_name}"
    space_id = f"{args.namespace}/{args.space_name}"
    for folder in (args.dataset_dir, args.space_dir):
        if not folder.is_dir():
            print(f"error: {folder} does not exist", file=sys.stderr)
            return 1
    plan = [("dataset", dataset_id, args.dataset_dir), ("space", space_id, args.space_dir)]
    if args.dry_run:
        publish_guard.check([args.dataset_dir / "README.md", args.dataset_dir / "croissant.json", args.space_dir / "README.md"], dry_run=True)
        for kind, repo_id, folder in plan:
            files = files_to_upload(folder)
            print(f"[dry run] would create/update {kind} {repo_id} (private={args.private}) from {folder} ({len(files)} files)")
            for rel, size in files:
                print(f"[dry run]   {rel:<48} {size:>12} bytes")
            print(f"[dry run]   total {sum(size for _, size in files)} bytes in {len(files)} files")
        return 0

    # Real publish: stop on placeholder markers before reading the token or touching the network.
    staged = [args.dataset_dir / "README.md", args.dataset_dir / "croissant.json", args.space_dir / "README.md"]
    if publish_guard.check(staged, dry_run=False):
        return 1
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("error: set HF_TOKEN (a Hub token with write access)", file=sys.stderr)
        return 1
    from huggingface_hub import HfApi  # noqa: PLC0415 - imported late so --dry-run needs no install

    api = HfApi(token=token)
    for kind, repo_id, folder in plan:
        extra = {"space_sdk": "gradio"} if kind == "space" else {}
        api.create_repo(repo_id=repo_id, repo_type=kind, private=args.private, exist_ok=True, **extra)
        api.upload_folder(folder_path=str(folder), repo_id=repo_id, repo_type=kind, commit_message=COMMIT_MESSAGE,
                          ignore_patterns=IGNORE_PATTERNS, delete_patterns=DELETE_PATTERNS)
        print(f"uploaded {kind}: https://huggingface.co/{'datasets/' if kind == 'dataset' else 'spaces/'}{repo_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
