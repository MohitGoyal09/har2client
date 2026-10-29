"""Tests for scripts/fetch_spec.py (download is monkeypatched; no network)."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_spec.py"
_spec = importlib.util.spec_from_file_location("fetch_spec", SCRIPT)
fs = importlib.util.module_from_spec(_spec)
sys.modules["fetch_spec"] = fs
_spec.loader.exec_module(fs)

BODY = b'{"openapi": "3.0.3", "paths": {}}'
DIGEST = hashlib.sha256(BODY).hexdigest()
LISTING = {
    "up.com.au": {"preferred": "1", "versions": {"1": {"openapiVer": "3.0.3", "swaggerUrl": "https://example.test/up.json"}}},
    "old.com": {"preferred": "1", "versions": {"1": {"openapiVer": "2.0", "swaggerUrl": "https://example.test/old.json"}}},
}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point the script at a temp specs dir and selected file, and fake the downloader."""
    specs = tmp_path / "specs"
    selected = tmp_path / "selected_specs.jsonl"
    calls = []

    def fake_download(url, dest, client, backoff=1.0):
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(BODY)
        return True

    monkeypatch.setattr(fs.ss, "download", fake_download)
    monkeypatch.setattr(fs.ss, "fetch_listing", lambda: LISTING)
    monkeypatch.setattr(fs.ss, "SPECS_DIR", specs)
    monkeypatch.setattr(fs.ss, "OUT_PATH", selected)
    return specs, selected, calls


def test_fetches_only_named_spec_and_prints_digest_when_none_recorded(paths, capsys):
    specs, _, calls = paths
    assert fs.main(["up.com.au"]) == 0
    assert calls == ["https://example.test/up.json"]
    assert (specs / "up.com.au.json").read_bytes() == BODY
    out = capsys.readouterr().out
    assert DIGEST in out and "no sha256 recorded" in out


def test_accepts_matching_recorded_digest(paths, capsys):
    _, selected, _ = paths
    selected.write_text(json.dumps({"api_id": "up.com.au", "sha256": DIGEST}) + "\n", encoding="utf-8")
    assert fs.main(["up.com.au"]) == 0
    assert "matches selected_specs.jsonl" in capsys.readouterr().out


def test_rejects_digest_mismatch(paths, capsys):
    _, selected, _ = paths
    selected.write_text(json.dumps({"api_id": "up.com.au", "sha256": "0" * 64}) + "\n", encoding="utf-8")
    assert fs.main(["up.com.au"]) == 1
    assert "differs" in capsys.readouterr().err


def test_unknown_or_non_30_api_fails_without_downloading(paths, capsys):
    _, _, calls = paths
    assert fs.main(["old.com", "nope.example"]) == 1
    assert calls == []
    assert capsys.readouterr().err.count("not an openapi 3.0.x API") == 2


def test_failed_download_is_reported(paths, monkeypatch, capsys):
    monkeypatch.setattr(fs.ss, "download", lambda url, dest, client, backoff=1.0: False)
    assert fs.main(["up.com.au"]) == 1
    assert "download failed" in capsys.readouterr().err


def test_never_rewrites_selected_specs(paths):
    _, selected, _ = paths
    selected.write_text('{"api_id": "x"}\n', encoding="utf-8")
    fs.main(["up.com.au"])
    assert selected.read_text(encoding="utf-8") == '{"api_id": "x"}\n'
