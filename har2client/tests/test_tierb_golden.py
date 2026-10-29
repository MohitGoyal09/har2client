"""Existing Tier B configs (all new switches off) must stay byte-identical: spec, reference client and its flaw variants,
tests, start data, an engine log of one reference-client session, and the full built task directory (HAR included).
The golden digests in fixtures/golden_tierb/existing_configs.json were taken from the code before the new switches existed."""

import hashlib
import json
import random
import sys
from pathlib import Path

import pytest

from har2client.tierb.build import build_tierb_task
from har2client.tierb.engine import TierBEngine
from har2client.tierb.player import load_reference_client
from har2client.tierb.refclient import generate_client_source
from har2client.tierb.spec import make_credentials, make_spec, method_name
from har2client.tierb.testgen import make_tests
from har2client.tierb import domains

GOLDEN = Path(__file__).parent / "fixtures" / "golden_tierb" / "existing_configs.json"
OLD_FLAWS = ("ignore_pagination", "no_relogin", "no_if_match", "drop_hidden_header", "return_envelope", "no_csrf")
ALL_ON = {"auth": "bearer_login", "pagination": "cursor", "token_expiry": "on", "etag_update": "on", "hidden_header": "on", "envelope": "on"}
ROWS = {
    "baseline_tickets": {
        "index": 1, "domain": "tickets", "seed": 1234,
        "config": {"auth": "apikey_header", "pagination": "none", "token_expiry": "off", "etag_update": "off", "hidden_header": "off", "envelope": "off"},
    },
    "bearer_cursor_all_invoices": {"index": 2, "domain": "invoices", "seed": 99, "config": ALL_ON},
    "cookie_offset_etag_samples": {
        "index": 4, "domain": "samples", "seed": 7001,
        "config": {"auth": "cookie_csrf", "pagination": "offset", "token_expiry": "off", "etag_update": "on", "hidden_header": "on", "envelope": "off"},
    },
}


def _sha(text) -> str:
    return hashlib.sha256((text if isinstance(text, str) else json.dumps(text)).encode()).hexdigest()


def _engine_log_digest(spec: dict) -> str:
    creds = make_credentials(spec, random.Random(5))
    engine = TierBEngine(spec, 31, creds)
    base = engine.start()
    try:
        client = load_reference_client(spec)(base, creds["username"], creds["password"])
        rng = random.Random(17)
        listed = getattr(client, method_name(spec, "list"))()
        getattr(client, method_name(spec, "list"))(**{spec["filter"]: listed[0][spec["filter"]]})
        getattr(client, method_name(spec, "get"))(id=listed[1]["id"])
        created = getattr(client, method_name(spec, "create"))(body=domains.make_body(spec["domain"], rng))
        getattr(client, method_name(spec, "update"))(id=created["id"], changes=domains.make_changes(spec["domain"], rng, created))
        getattr(client, method_name(spec, "delete"))(id=listed[2]["id"])
        client.close()
    finally:
        engine.stop()
    rows = [[e["method"], e["path"], e["query"], e["body"], e["op_id"], e["path_params"], e["status"], e["response"]] for e in engine.log]
    return _sha(rows)


def fingerprint(row: dict, tmp_path: Path) -> dict:
    spec = make_spec(row["domain"], row["config"], row["seed"])
    out = {
        "spec": _sha(spec),
        "ref_client": _sha(generate_client_source(spec)),
        "flaws": {f: _sha(generate_client_source(spec, (f,))) for f in OLD_FLAWS},
        "tests": _sha(make_tests(spec, 4242)),
        "items": _sha(domains.initial_items(spec["domain"], 4242)),
        "engine_log": _engine_log_digest(spec),
    }
    task_dir = build_tierb_task(row, tmp_path, negatives=False)
    out["build"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(task_dir.iterdir())}
    return out


@pytest.mark.parametrize("name", list(ROWS))
def test_existing_configs_are_byte_identical_to_the_golden_digests(name, tmp_path):
    golden = json.loads(GOLDEN.read_text())
    assert fingerprint(ROWS[name], tmp_path) == golden[name]


if __name__ == "__main__":  # regenerate only from code that predates a change you want to prove harmless
    import tempfile

    result = {name: fingerprint(row, Path(tempfile.mkdtemp())) for name, row in ROWS.items()}
    GOLDEN.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print("wrote", GOLDEN, file=sys.stderr)
