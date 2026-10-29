"""Tests for scripts/grader_audit.py on the fixture tasks (real grader, real mock, no network)."""

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

from har2client import build_task, ops, refclient

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "grader_audit.py"
_spec = importlib.util.spec_from_file_location("grader_audit", SCRIPT)
ga = importlib.util.module_from_spec(_spec)
sys.modules["grader_audit"] = ga
_spec.loader.exec_module(ga)

FIX = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.slow  # the module fixture builds and audits two tasks (about 8 s)

CORE_KILLED = {
    "base_path_doubled", "base_path_dropped", "wrong_http_method", "path_segment_changed",
    "path_param_in_query", "query_param_renamed", "query_param_dropped",
    "bool_encoding_python_repr", "bool_encoding_numeric", "auth_header_name_wrong", "auth_in_query",
    "credential_hardcoded", "host_hardcoded", "response_not_parsed", "returns_field_not_body",
    "body_as_form", "body_dropped", "extra_query_param",
}
EXPECTED_SURVIVORS = {
    "extra_unrequested_call": "FLAG_ONLY",
    "error_swallowed": "EQUIVALENT",
    "credential_also_in_query": "EQUIVALENT",
    "empty_body_object_when_none": "EQUIVALENT",
}


@pytest.fixture(scope="module")
def tasks_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("tasks")
    for name, api in (("bookshelf_apikey.yaml", "acme:alpha"), ("bookshelf_bearer.yaml", "acme:beta")):
        d = build_task.build_task(ops.load_spec(FIX / name), api, root, noise=0.7, seed=3)
        refclient.write_ref_client(d)
    return root


@pytest.fixture(scope="module")
def audited(tasks_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("audit")
    rows = ga.run_audit(tasks_dir, out, workers=2)
    return rows, out


def rows_where(rows, **kw):
    return [r for r in rows if all(r[k] == v for k, v in kw.items())]


def test_oracle_scores_one_on_every_task(audited):
    rows, _ = audited
    oracle = rows_where(rows, kind="oracle")
    assert len(oracle) == 2 and all(r["pass_rate"] == 1.0 for r in oracle)


def test_trivial_agents_score_about_zero(audited):
    rows, _ = audited
    trivial = rows_where(rows, kind="trivial")
    assert {r["name"] for r in trivial} == {"empty_client", "har_replay_client"}
    assert len(trivial) == 4 and all(r["pass_rate"] <= 0.1 for r in trivial)


def test_har_replay_client_trips_the_hardcode_flags(audited):
    rows, _ = audited
    for r in rows_where(rows, name="har_replay_client"):
        assert r["hardcoded_secret"] is True and r["hardcoded_host"] is True


def test_core_operators_are_killed_on_every_task(audited):
    rows, _ = audited
    for r in rows_where(rows, kind="mutant", status="graded"):
        if r["name"] in CORE_KILLED:
            assert r["outcome"] == "killed", (r["task"], r["name"], r["pass_rate"], r["failure_counts"])


def test_survivors_are_the_known_design_cases_and_nothing_is_a_grader_gap(audited):
    rows, _ = audited
    survivors = rows_where(rows, kind="mutant", outcome="survived")
    assert survivors
    for r in survivors:
        assert r["name"] in EXPECTED_SURVIVORS, (r["task"], r["name"])
        assert r["survivor_class"] == EXPECTED_SURVIVORS[r["name"]], (r["name"], r["survivor_class"])
        assert r["survivor_reason"]


def test_skipped_mutants_are_recorded_with_a_reason(audited):
    rows, _ = audited
    skipped = rows_where(rows, kind="mutant", status="skipped")
    assert skipped and all(r["skip_reason"] for r in skipped)


def test_outputs_are_written(audited):
    rows, out = audited
    ga.write_outputs(rows, out)
    with open(out / "audit.csv", newline="") as fh:
        read = list(csv.DictReader(fh))
    assert len(read) == len(rows)
    assert {"task", "kind", "name", "pass_rate", "outcome", "survivor_class", "skip_reason"} <= set(read[0])
    summary = (out / "summary.md").read_text()
    for heading in ("Oracle", "Trivial agents", "Kill rate", "Survivors"):
        assert heading in summary
    assert (out / "summary.json").exists()
    assert list((out / "clients").rglob("*.py"))


def test_summary_kill_rates_are_consistent(audited):
    rows, _ = audited
    s = ga.summarize(rows)
    graded = rows_where(rows, kind="mutant", status="graded")
    killed = [r for r in graded if r["outcome"] == "killed"]
    assert s["mutants_graded"] == len(graded) and s["mutants_killed"] == len(killed)
    assert s["kill_rate"] == pytest.approx(len(killed) / len(graded))
    for name, per in s["per_operator"].items():
        assert per["killed"] + per["survived"] == per["graded"]
    assert s["oracle_failures"] == []


def test_oracle_failure_skips_the_tasks_mutants(tasks_dir, tmp_path):
    broken = tmp_path / "tasks"
    broken.mkdir()
    import shutil

    src = next(p for p in tasks_dir.iterdir() if p.is_dir())
    shutil.copytree(src, broken / src.name)
    (broken / src.name / "ref_client.py").write_text("class Client:\n    def __init__(self, b, k): pass\n")
    rows = ga.run_audit(broken, tmp_path / "out", workers=1)
    assert rows_where(rows, kind="oracle")[0]["pass_rate"] < 1.0
    assert not rows_where(rows, kind="mutant", status="graded")
    assert ga.summarize(rows)["oracle_failures"] == [src.name]


def test_trivial_agent_above_the_limit_or_ungraded_is_reported(audited):
    rows, _ = audited
    assert ga.summarize(rows)["trivial_failures"] == []
    bad = [dict(r) for r in rows]
    victim = next(r for r in bad if r["kind"] == "trivial" and r["name"] == "empty_client")
    victim["pass_rate"] = 1.0
    other = next(r for r in bad if r["kind"] == "trivial" and r["name"] == "har_replay_client")
    other.update(status="error", pass_rate="")
    s = ga.summarize(bad)
    assert s["trivial_failures"] == sorted([f"{victim['task']}/empty_client", f"{other['task']}/har_replay_client"])
    assert "TRIVIAL AGENTS ABOVE" in ga.render_summary(s)
