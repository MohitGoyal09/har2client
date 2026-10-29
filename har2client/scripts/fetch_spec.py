#!/usr/bin/env python3
"""Download only the named OpenAPI specs from APIs.guru into data/specs/.

Usage: uv run python scripts/fetch_spec.py <api_id> [<api_id> ...]

`data/specs/` is git-ignored, so a fresh clone has no specs. This fetches just the ones you need
(for example `up.com.au` for the README quickstart) instead of running the whole of select_specs.py.
It uses the same URL logic and download helper as select_specs.py.

Each file's sha256 is checked against the `sha256` field of its row in data/selected_specs.jsonl when
that field exists; otherwise the digest is printed. Exit code: 0 all fetched, 1 a spec is unknown,
failed to download, or does not match its recorded digest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx

_SELECT_SPECS = Path(__file__).resolve().with_name("select_specs.py")
_module_spec = importlib.util.spec_from_file_location("select_specs", _SELECT_SPECS)
ss = importlib.util.module_from_spec(_module_spec)
sys.modules.setdefault("select_specs", ss)
_module_spec.loader.exec_module(ss)

EXIT_OK, EXIT_FAILED = 0, 1


def recorded_digests(selected_path: Path) -> dict[str, str]:
    """api_id -> sha256 for the rows of selected_specs.jsonl that carry one."""
    digests: dict[str, str] = {}
    if not selected_path.is_file():
        return digests
    for line in selected_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("sha256"):
            digests[row["api_id"]] = row["sha256"]
    return digests


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_specs(
    api_ids: list[str],
    listing: dict,
    client: httpx.Client,
    specs_dir: Path,
    digests: dict[str, str],
) -> list[dict]:
    """One result per api_id: {api_id, status, path, sha256}. status: ok|unknown|download|mismatch."""
    urls = {c["api_id"]: c["swagger_url"] for c in ss.candidates(listing)}
    results = []
    for api_id in api_ids:
        dest = specs_dir / f"{ss.safe_api_id(api_id)}.json"
        result = {"api_id": api_id, "path": dest, "sha256": None}
        if not urls.get(api_id):
            results.append(dict(result, status="unknown"))
            continue
        if not ss.download(urls[api_id], dest, client):
            results.append(dict(result, status="download"))
            continue
        digest = sha256_of(dest)
        expected = digests.get(api_id)
        status = "mismatch" if expected and expected != digest else "ok"
        results.append(dict(result, status=status, sha256=digest, expected=expected))
    return results


def report(result: dict) -> str:
    api_id, status = result["api_id"], result["status"]
    if status == "unknown":
        return f"error: {api_id}: not an openapi 3.0.x API in the APIs.guru list"
    if status == "download":
        return f"error: {api_id}: download failed"
    path = Path(result["path"])
    if status == "mismatch":
        return f"error: {api_id}: sha256 {result['sha256']} differs from selected_specs.jsonl ({result['expected']})"
    note = "matches selected_specs.jsonl" if result.get("expected") else "no sha256 recorded in selected_specs.jsonl"
    return f"{api_id}: {path} sha256={result['sha256']} ({note})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("api_ids", nargs="+", metavar="api_id", help="APIs.guru id, for example up.com.au")
    args = ap.parse_args(argv)

    listing = ss.fetch_listing()
    digests = recorded_digests(ss.OUT_PATH)
    with httpx.Client() as client:
        results = fetch_specs(args.api_ids, listing, client, ss.SPECS_DIR, digests)
    for result in results:
        print(report(result), file=sys.stderr if result["status"] != "ok" else sys.stdout)
    return EXIT_OK if all(r["status"] == "ok" for r in results) else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
