#!/usr/bin/env python3
"""Select candidate OpenAPI 3.0.x specs from APIs.guru for Tier A.

Usage: uv run scripts/select_specs.py [--limit N] [--seed S] [--max-candidates N] [--no-write]

Funnel: preferred version, openapi 3.0.x -> download -> 5..40 usable ops ->
>=3 JSON responses with schema -> supported security -> valid spec ->
max 2 per provider. Output: data/selected_specs.jsonl (sorted by api_id);
`--no-write` leaves that tracked file alone (specs still download into data/specs/).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
LIST_URL = "https://api.apis.guru/v2/list.json"
LIST_PATH = ROOT / "data" / "raw" / "list.json"
SPECS_DIR = ROOT / "data" / "specs"
OUT_PATH = ROOT / "data" / "selected_specs.jsonl"

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MIN_OPS, MAX_OPS = 5, 40
MIN_JSON_RESPONSES = 3
PROVIDER_CAP = 2
CONCURRENCY = 8
TIMEOUT_S = 60.0
MAX_REF_DEPTH = 5
OPS_BUCKETS = ((5, 9), (10, 19), (20, 29), (30, 40))


# ---- small helpers -------------------------------------------------------


def provider_of(api_id: str) -> str:
    return api_id.split(":", 1)[0]


def safe_api_id(api_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", api_id.replace(":", "__"))


def is_openapi_30(version: Any) -> bool:
    return isinstance(version, str) and version.startswith("3.0")


def _deref(spec: dict, obj: Any) -> Any:
    """Follow local `$ref` chains (`#/...`); return {} when unresolvable."""
    for _ in range(MAX_REF_DEPTH):
        if not isinstance(obj, dict) or "$ref" not in obj:
            return obj
        ref = obj["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return {}
        cur: Any = spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(cur, dict) or part not in cur:
                return {}
            cur = cur[part]
        obj = cur
    return {}


def _json_media(content: Any) -> dict | None:
    if not isinstance(content, dict):
        return None
    for media_type, media in content.items():
        if media_type.split(";")[0].strip().lower() == "application/json":
            return media if isinstance(media, dict) else {}
    return None


def _operations(spec: dict):
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return
    for item in paths.values():
        item = _deref(spec, item)
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                yield op


def _has_json_or_no_body(spec: dict, op: dict) -> bool:
    if "requestBody" not in op:
        return True
    body = _deref(spec, op["requestBody"])
    return isinstance(body, dict) and _json_media(body.get("content")) is not None


def _has_json_2xx_schema(spec: dict, op: dict) -> bool:
    responses = op.get("responses")
    if not isinstance(responses, dict):
        return False
    for code, resp in responses.items():
        if not str(code).startswith("2"):
            continue
        resp = _deref(spec, resp)
        media = _json_media(resp.get("content")) if isinstance(resp, dict) else None
        if media and media.get("schema"):
            return True
    return False


def count_ops(spec: dict) -> tuple[int, int]:
    """Return (usable op count, usable ops with a 2xx JSON response schema)."""
    n_ops = n_json = 0
    for op in _operations(spec):
        if not _has_json_or_no_body(spec, op):
            continue
        n_ops += 1
        if _has_json_2xx_schema(spec, op):
            n_json += 1
    return n_ops, n_json


# ---- security ------------------------------------------------------------


def _scheme_kind(scheme: Any) -> str | None:
    if not isinstance(scheme, dict):
        return None
    stype = scheme.get("type")
    if stype == "apiKey":
        return "apiKey" if scheme.get("in") in ("header", "query") else None
    if stype == "http":
        s = str(scheme.get("scheme", "")).lower()
        return {"bearer": "bearer", "basic": "basic"}.get(s)
    if stype in ("oauth2", "openIdConnect"):
        return "bearer"
    return None


def _required_scheme_names(spec: dict) -> list[str]:
    """Scheme names in order: global requirements first, then per-op ones."""
    names: list[str] = []
    reqs = [spec.get("security")] + [op.get("security") for op in _operations(spec)]
    for req_list in reqs:
        if not isinstance(req_list, list):
            continue
        for req in req_list:
            if isinstance(req, dict):
                names.extend(req.keys())
    return list(dict.fromkeys(names))


def classify_security(spec: dict) -> str | None:
    """apiKey|bearer|basic|none, or None when only unsupported schemes are used."""
    names = _required_scheme_names(spec)
    if not names:
        return "none"
    schemes = (spec.get("components") or {}).get("securitySchemes") or {}
    for name in names:
        kind = _scheme_kind(_deref(spec, schemes.get(name)))
        if kind:
            return kind
    return None


# ---- analysis, cap, candidates ------------------------------------------


def analyze_spec(spec: dict) -> tuple[dict | None, str | None]:
    """Cheap filters. Returns (record, None) or (None, reject_reason)."""
    n_ops, n_json = count_ops(spec)
    if not MIN_OPS <= n_ops <= MAX_OPS:
        return None, "ops_range"
    if n_json < MIN_JSON_RESPONSES:
        return None, "json_responses"
    security = classify_security(spec)
    if security is None:
        return None, "security"
    info = spec.get("info") or {}
    categories = info.get("x-apisguru-categories")
    return {
        "title": info.get("title", ""),
        "n_ops": n_ops,
        "n_json_responses": n_json,
        "security_type": security,
        "categories": categories if isinstance(categories, list) else [],
        "openapi_version": spec.get("openapi"),
    }, None


def cap_per_provider(records: list[dict], cap: int, rng: random.Random) -> list[dict]:
    """Keep at most `cap` records per provider; deterministic for a given seed."""
    by_provider: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(records, key=lambda r: r["api_id"]):
        by_provider[r["provider"]].append(r)
    kept: list[dict] = []
    for provider in sorted(by_provider):
        group = by_provider[provider]
        kept.extend(rng.sample(group, min(cap, len(group))))
    return sorted(kept, key=lambda r: r["api_id"])


def candidates(listing: dict) -> list[dict]:
    """Preferred version of each API, openapi 3.0.x only, sorted by api_id."""
    out = []
    for api_id in sorted(listing):
        entry = listing[api_id]
        version = (entry.get("versions") or {}).get(entry.get("preferred"))
        if not version or not is_openapi_30(version.get("openapiVer")):
            continue
        out.append(
            {
                "api_id": api_id,
                "provider": provider_of(api_id),
                "swagger_url": version.get("swaggerUrl"),
            }
        )
    return out


# ---- network -------------------------------------------------------------


def download(
    url: str, dest: Path, client: httpx.Client, backoff: float = 1.0
) -> bool:
    """Download a JSON spec to dest (cached). Retry once. True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for attempt in range(2):
        try:
            resp = client.get(url, timeout=TIMEOUT_S, follow_redirects=True)
            resp.raise_for_status()
            json.loads(resp.content)  # must be JSON
        except (httpx.HTTPError, ValueError):
            if attempt == 0:
                time.sleep(backoff)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)
        return True
    return False


def fetch_listing(path: Path = LIST_PATH) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = httpx.get(LIST_URL, timeout=TIMEOUT_S, follow_redirects=True)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return json.loads(path.read_text())


def _download_and_analyze(args: tuple[dict, httpx.Client]) -> dict:
    cand, client = args
    dest = SPECS_DIR / f"{safe_api_id(cand['api_id'])}.json"
    result = dict(cand, spec_path=str(dest.relative_to(ROOT)))
    if not cand["swagger_url"] or not download(cand["swagger_url"], dest, client):
        return dict(result, reject="download")
    try:
        spec = json.loads(dest.read_text())
    except ValueError:
        return dict(result, reject="download")
    if not isinstance(spec, dict):
        return dict(result, reject="download")
    rec, reason = analyze_spec(spec)
    if reason:
        return dict(result, reject=reason)
    return dict(result, **rec)


def _validate_path(path: str) -> bool:
    from openapi_spec_validator import validate

    try:
        validate(json.loads(Path(path).read_text()))
    except Exception:  # any validation/parse failure drops the spec
        return False
    return True


# ---- main ----------------------------------------------------------------


def bucket_of(n_ops: int) -> str:
    for lo, hi in OPS_BUCKETS:
        if lo <= n_ops <= hi:
            return f"{lo}-{hi}"
    return "other"


def print_summary(funnel: list[tuple[str, int]], final: list[dict]) -> None:
    print("Selection funnel:")
    for label, n in funnel:
        print(f"  {label:<44}{n:>6}")
    print("Security types:", dict(Counter(r["security_type"] for r in final)))
    buckets = Counter(bucket_of(r["n_ops"]) for r in final)
    print("n_ops buckets:", {f"{lo}-{hi}": buckets.get(f"{lo}-{hi}", 0) for lo, hi in OPS_BUCKETS})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="max APIs in final output")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--max-candidates", type=int, default=None, help="dev: only process first N candidates"
    )
    ap.add_argument("--no-write", action="store_true", help="do not rewrite data/selected_specs.jsonl")
    args = ap.parse_args(argv)

    listing = fetch_listing()
    funnel: list[tuple[str, int]] = [("APIs in list", len(listing))]
    cands = candidates(listing)
    funnel.append(("preferred version is openapi 3.0.x", len(cands)))
    if args.max_candidates:
        cands = cands[: args.max_candidates]
        funnel.append((f"--max-candidates {args.max_candidates}", len(cands)))

    with httpx.Client(limits=httpx.Limits(max_connections=CONCURRENCY)) as client:
        with ThreadPoolExecutor(CONCURRENCY) as pool:
            results = list(pool.map(_download_and_analyze, [(c, client) for c in cands]))

    reasons = Counter(r["reject"] for r in results if "reject" in r)
    remaining = len(results)
    for key, label in (
        ("download", "downloaded and parsed"),
        ("ops_range", f"{MIN_OPS}..{MAX_OPS} usable ops"),
        ("json_responses", f">={MIN_JSON_RESPONSES} JSON 2xx responses with schema"),
        ("security", "supported security scheme"),
    ):
        remaining -= reasons[key]
        funnel.append((label, remaining))
    passed = [r for r in results if "reject" not in r]

    abs_paths = [str(ROOT / r["spec_path"]) for r in passed]
    with ProcessPoolExecutor() as pool:
        valid_flags = list(pool.map(_validate_path, abs_paths, chunksize=1))
    valid = [r for r, ok in zip(passed, valid_flags) if ok]
    funnel.append(("valid per openapi_spec_validator", len(valid)))

    capped = cap_per_provider(valid, PROVIDER_CAP, random.Random(args.seed))
    funnel.append((f"max {PROVIDER_CAP} per provider", len(capped)))

    if args.limit is not None and len(capped) > args.limit:
        capped = sorted(
            random.Random(args.seed).sample(capped, args.limit), key=lambda r: r["api_id"]
        )
        funnel.append((f"--limit {args.limit}", len(capped)))

    print_summary(funnel, capped)
    if args.no_write:
        print(f"Selected {len(capped)} APIs; --no-write, {OUT_PATH.relative_to(ROOT)} left unchanged")
        return 0
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    keys = (
        "api_id", "provider", "title", "spec_path", "n_ops",
        "n_json_responses", "security_type", "categories", "openapi_version",
    )
    with OUT_PATH.open("w") as fh:
        for r in capped:
            fh.write(json.dumps({k: r[k] for k in keys}) + "\n")
    print(f"Wrote {len(capped)} APIs to {OUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
