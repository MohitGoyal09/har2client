#!/usr/bin/env python3
"""Minimal offline check of a Croissant 1.0 file: required keys, RAI fields, and internal references.

Usage:  python3 release/validate_croissant.py [FILE] [--strict]
        FILE defaults to release/build/dataset/croissant.json; --strict also fails on unfilled placeholders.

This is NOT the official validator. The official one is the `mlcroissant` package
(`pip install mlcroissant; mlcroissant validate --jsonld FILE`) and the online validator on the Croissant site; run
one of them before submitting anywhere that requires Croissant. If `mlcroissant` is importable, this script also runs it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REQUIRED_TOP = ["@context", "@type", "name", "description", "url", "license", "conformsTo", "distribution", "recordSet"]
RAI_FIELDS = [
    "rai:dataLimitations", "rai:dataBiases", "rai:personalSensitiveInformation", "rai:dataUseCases",
    "rai:dataSocialImpact", "rai:hasSyntheticData", "prov:wasDerivedFrom", "prov:wasGeneratedBy",
]
REQUIRED_FILE = ["@type", "@id", "name", "contentUrl", "encodingFormat", "sha256"]
REQUIRED_FIELD = ["@type", "@id", "name"]
CONTEXT_KEYS = ["cr", "rai", "sc", "dct", "prov", "recordSet", "field", "dataType", "source", "extract", "fileObject", "jsonPath"]
PLACEHOLDERS = ("FILLED_BY_EXPORT_DATASET", "ORG/har2client")


def field_names(fields: list[dict]) -> set[str]:
    return {f.get("name") for f in fields}


def check_against_files(doc: dict, base: Path) -> list[str]:
    """Compare the description with the exported files: sha256, and every column of the first row has a field."""
    errors: list[str] = []
    by_id = {f.get("@id"): f for f in doc.get("distribution", [])}
    for f in by_id.values():
        path = base / str(f.get("name", ""))
        if not path.is_file():
            errors.append(f"distribution {f.get('@id')!r}: file {f.get('name')!r} not found in {base}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != f.get("sha256"):
            errors.append(f"distribution {f.get('@id')!r}: sha256 does not match {f.get('name')!r}")
    for rs in doc.get("recordSet", []):
        src = ((rs.get("field") or [{}])[0].get("source") or {}).get("fileObject", {}).get("@id")
        path = base / str((by_id.get(src) or {}).get("name", ""))
        if not path.is_file():
            continue
        first = next((ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()), None)
        missing = sorted(set(json.loads(first)) - field_names(rs.get("field", []))) if first else []
        if missing:
            errors.append(f"recordSet {rs.get('@id')!r}: columns in {path.name} with no field: {missing}")
    return errors


def check(doc: dict, strict: bool, base: Path | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for key in REQUIRED_TOP:
        if key not in doc:
            errors.append(f"missing top-level key {key!r}")
    if doc.get("@type") != "sc:Dataset":
        errors.append("@type must be 'sc:Dataset'")
    if doc.get("conformsTo") != "http://mlcommons.org/croissant/1.0":
        errors.append("conformsTo must be 'http://mlcommons.org/croissant/1.0'")
    for key in CONTEXT_KEYS:
        if key not in (doc.get("@context") or {}):
            errors.append(f"@context lacks {key!r}")
    for key in RAI_FIELDS:
        value = doc.get(key)
        if value in (None, "", []):
            errors.append(f"missing RAI/provenance field {key!r}")
    file_ids = set()
    for f in doc.get("distribution", []):
        file_ids.add(f.get("@id"))
        errors += [f"distribution {f.get('@id')!r}: missing {k!r}" for k in REQUIRED_FILE if k not in f]
    if not file_ids:
        errors.append("distribution is empty")
    for rs in doc.get("recordSet", []):
        if rs.get("@type") != "cr:RecordSet" or not rs.get("field"):
            errors.append(f"recordSet {rs.get('@id')!r}: needs @type cr:RecordSet and fields")
        seen: set[str] = set()
        stack = list(rs.get("field", []))
        while stack:
            fld = stack.pop()
            errors += [f"field {fld.get('@id')!r}: missing {k!r}" for k in REQUIRED_FIELD if k not in fld]
            if fld.get("@id") in seen:
                errors.append(f"duplicate field id {fld.get('@id')!r}")
            seen.add(fld.get("@id"))
            if "subField" in fld:
                stack += fld["subField"]
                continue
            if "dataType" not in fld:
                errors.append(f"field {fld.get('@id')!r}: no dataType")
            src = fld.get("source", {})
            ref = (src.get("fileObject") or src.get("fileSet") or {}).get("@id")
            if ref not in file_ids:
                errors.append(f"field {fld.get('@id')!r}: source points to unknown file {ref!r}")
            if "extract" not in src:
                errors.append(f"field {fld.get('@id')!r}: source has no extract")
    if base is not None:
        errors += check_against_files(doc, base)
    text = json.dumps(doc)
    for placeholder in PLACEHOLDERS:
        if placeholder in text:
            (errors if strict else warnings).append(f"placeholder {placeholder!r} still present")
    return errors, warnings


def run_official(path: Path) -> str:
    try:
        import mlcroissant as mlc  # noqa: PLC0415
    except ImportError:
        return "mlcroissant not installed: official validator not run"
    try:
        mlc.Dataset(jsonld=str(path))
    except Exception as exc:  # noqa: BLE001 - report whatever the official validator raises
        return f"mlcroissant FAILED: {str(exc)[:500]}"
    return "mlcroissant: OK"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", type=Path, default=Path(__file__).resolve().parent / "build" / "dataset" / "croissant.json")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    errors, warnings = check(json.loads(args.file.read_text(encoding="utf-8")), args.strict,
                             args.file.parent if (args.file.parent / "MANIFEST.json").is_file() else None)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    official = run_official(args.file)
    print(official)
    ok = not errors and "FAILED" not in official
    print("PASS" if ok else f"FAIL: {len(errors)} error(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
