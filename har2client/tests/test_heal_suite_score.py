"""scripts/score_heal.py: regression rate, patch size and the factor columns, with synthetic patched clients."""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from har2client import grade as grading
from har2client.heal import select
from har2client.heal.build import build_heal_task

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("score_heal", ROOT / "scripts" / "score_heal.py")
sh = importlib.util.module_from_spec(_spec)
sys.modules["score_heal"] = sh
_spec.loader.exec_module(sh)

ROW = next(r for r in select.select_rows(1) if r["mutation"] == "add_etag")


@pytest.fixture(scope="module")
def task_dir(tmp_path_factory):
    return build_heal_task(ROW, tmp_path_factory.mktemp("score"), build_seed=1)


def write_client(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def broken_get(source: str) -> str:
    """The same client, with `get_*` returning None: breaks a test that client_v1 passes on v2."""
    name = next(line.split("def ")[1].split("(")[0] for line in source.splitlines() if line.strip().startswith("def get_"))
    return source + f"\n\ndef _broken(self, *a, **k):\n    return None\n\nClient.{name} = _broken\n"


# ------------------------------------------------------------ pure functions


def test_regression_counts_tests_that_v1_passed_and_the_patch_fails():
    out = sh.regression([True, True, False, True], [True, False, True, False])
    assert out == {"regression_count": 2, "v1_pass_count": 3, "n_tests": 4, "regression_rate": 0.5}


def test_regression_is_zero_for_a_patch_that_keeps_every_passing_test():
    assert sh.regression([True, False], [True, True])["regression_rate"] == 0.0
    assert sh.regression([False, False], [False, False])["regression_rate"] == 0.0


def test_regression_refuses_results_of_different_length():
    with pytest.raises(ValueError):
        sh.regression([True], [True, True])


def test_factors_of_a_task_without_the_new_keys_are_core():
    task = {"heal": {"mutation": "add_etag"}}
    assert sh.task_factors(task) == {"set": "core", "mutations": "add_etag", "changes": 1, "error_detail": "verbose", "evidence": "both", "identifiable": True}
    task = {"heal": {"mutation": "a+b", "mutations": ["a", "b"], "set": "compound", "error_detail": "terse", "evidence": "failure_only", "identifiable": False}}
    assert sh.task_factors(task) == {"set": "compound", "mutations": "a+b", "changes": 2, "error_detail": "terse", "evidence": "failure_only", "identifiable": False}


def _grade(passed: list[bool]) -> dict:
    return {"results": [{"index": i, "passed": ok} for i, ok in enumerate(passed)]}


def test_change_credit_is_the_pass_share_over_the_tests_that_need_the_change():
    tests = [{"needs": ["a"]}, {"needs": ["a", "b"]}, {"needs": ["b"]}, {"needs": ["a"]}]
    credit = sh.change_credit(tests, _grade([True, False, True, True]))
    assert credit == {"a": 2 / 3, "b": 0.5}


def test_change_credit_reads_results_by_index_not_by_order():
    tests = [{"needs": ["a"]}, {"needs": ["b"]}]
    grade = {"results": [{"index": 1, "passed": True}, {"index": 0, "passed": False}]}
    assert sh.change_credit(tests, grade) == {"a": 0.0, "b": 1.0}


def test_change_credit_is_empty_for_tests_without_needs_and_refuses_a_length_mismatch():
    assert sh.change_credit([{"op": "list"}, {"op": "get", "needs": []}], _grade([True, True])) == {}
    with pytest.raises(ValueError):
        sh.change_credit([{"needs": ["a"]}], _grade([True, True]))


# ------------------------------------------------------------ real grading of synthetic clients


def test_no_op_patch_has_no_regression_and_no_patch(task_dir, tmp_path):
    scorer = sh.Scorer(task_dir.parent)
    out = scorer.score(task_dir.name, write_client(tmp_path / "c.py", (task_dir / "client_v1.py").read_text()))
    assert out["regression_rate"] == 0.0 and out["regression_count"] == 0 and out["patch_lines"] == 0 and out["patch_ast_nodes"] == 0
    assert 0 < out["v1_pass_count"] < out["n_tests"] and out["v1_pass_rate"] == out["v1_pass_count"] / out["n_tests"]
    assert out["set"] == "core" and out["mutations"] == "add_etag" and out["changes"] == 1 and out["identifiable"] is True


def test_reference_client_fixes_without_regression_and_is_a_real_patch(task_dir, tmp_path):
    scorer = sh.Scorer(task_dir.parent)
    out = scorer.score(task_dir.name, write_client(tmp_path / "c.py", (task_dir / "ref_client.py").read_text()))
    assert out["regression_rate"] == 0.0 and out["patch_lines"] > 0 and out["patch_ast_nodes"] > 0


def test_a_patch_that_breaks_a_working_method_is_a_regression(task_dir, tmp_path):
    scorer = sh.Scorer(task_dir.parent)
    patched = broken_get((task_dir / "ref_client.py").read_text())
    out = scorer.score(task_dir.name, write_client(tmp_path / "c.py", patched))
    assert out["regression_count"] >= 1 and out["regression_rate"] == out["regression_count"] / out["n_tests"] > 0
    assert grading.grade(task_dir, tmp_path / "c.py")["pass_rate"] < 1.0


def test_the_grade_json_of_the_batch_is_reused_and_v1_is_graded_once(task_dir, tmp_path):
    calls = []

    def counting(d, client):
        calls.append(Path(client).name)
        return grading.grade(d, client)

    scorer = sh.Scorer(task_dir.parent, grade_fn=counting)
    client = write_client(tmp_path / "c.py", (task_dir / "ref_client.py").read_text())
    grade = grading.grade(task_dir, client)
    calls.clear()
    scorer.score(task_dir.name, client, grade)
    scorer.score(task_dir.name, client, grade)
    assert calls == ["client_v1.py"]  # the cell's own grade is used; v1 once per task
    scorer.score(task_dir.name, client, None)  # no grade.json: the client is graded again
    assert calls == ["client_v1.py", "c.py"]


def test_an_attempt_without_a_client_keeps_empty_scores(task_dir, tmp_path):
    out = sh.Scorer(task_dir.parent).score(task_dir.name, tmp_path / "missing.py")
    assert out["set"] == "core" and "regression_rate" not in out and "patch_lines" not in out


# ------------------------------------------------------------ the script


def make_batch(root: Path, task: str, cells: dict) -> Path:
    runs = root / "batch"
    rows = []
    for (effort, run), source in cells.items():
        cell = runs / task / f"{effort}-raw-r{run}"
        if source is not None:
            write_client(cell / "client.py", source)
        rows.append({"task": task, "api_id": "x", "effort": effort, "har_mode": "raw", "run": run, "status": "ok" if source else "no_code", "pass_rate": 0.5})
    runs.mkdir(parents=True, exist_ok=True)
    with open(runs / "results.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["task", "api_id", "effort", "har_mode", "run", "status", "pass_rate"])
        writer.writeheader()
        writer.writerows(rows)
    return runs


def test_script_joins_results_with_factors_regression_and_patch_size(task_dir, tmp_path, capsys):
    ref = (task_dir / "ref_client.py").read_text()
    runs = make_batch(tmp_path, task_dir.name, {("low", 1): (task_dir / "client_v1.py").read_text(), ("high", 1): ref, ("high", 2): broken_get(ref), ("high", 3): None})
    out = tmp_path / "scored.csv"
    assert sh.main(["--tasks", str(task_dir.parent), "--runs", str(runs), "--out", str(out)]) == 0
    rows = list(csv.DictReader(open(out)))
    assert [r["pass_rate"] for r in rows] == ["0.5"] * 4  # the batch columns stay
    by = {(r["effort"], r["run"]): r for r in rows}
    assert float(by["low", "1"]["regression_rate"]) == 0.0 and by["low", "1"]["patch_lines"] == "0"
    assert float(by["high", "1"]["regression_rate"]) == 0.0 and int(by["high", "1"]["patch_lines"]) > 0 and int(by["high", "1"]["patch_ast_nodes"]) > 0
    assert float(by["high", "2"]["regression_rate"]) > 0
    assert by["high", "3"]["regression_rate"] == "" and by["high", "3"]["patch_lines"] == "" and by["high", "3"]["v1_pass_rate"] != ""
    for r in rows:
        assert (r["set"], r["mutations"], r["changes"], r["error_detail"], r["evidence"], r["identifiable"]) == ("core", "add_etag", "1", "verbose", "both", "True")
    assert "scored 3/4 attempts" in capsys.readouterr().out


def test_script_reads_the_per_test_grade_of_the_cell_when_present(task_dir, tmp_path):
    ref = (task_dir / "ref_client.py").read_text()
    runs = make_batch(tmp_path, task_dir.name, {("high", 1): ref})
    grade = grading.grade(task_dir, runs / task_dir.name / "high-raw-r1" / "client.py")
    grade["results"][0]["passed"] = False  # a doctored cell grade: the script trusts what the batch recorded
    (runs / task_dir.name / "high-raw-r1" / "grade.json").write_text(json.dumps(grade))
    columns, rows = sh.score_runs(task_dir.parent, runs, runs / "results.csv")
    v1_first = sh.Scorer(task_dir.parent).v1_passed(task_dir.name)[0]
    assert rows[0]["regression_count"] == int(v1_first) and "regression_rate" in columns


def test_script_fails_clearly_without_a_results_csv(tmp_path, capsys):
    assert sh.main(["--tasks", str(tmp_path), "--runs", str(tmp_path), "--out", str(tmp_path / "o.csv")]) == 2
    assert "no results CSV" in capsys.readouterr().err


def test_score_adds_change_credit_as_json_and_leaves_other_columns(tmp_path):
    task = tmp_path / "t"
    task.mkdir()
    (task / "task.json").write_text(json.dumps({"heal": {"mutations": ["a", "b"], "set": "release3"}}))
    (task / "tests.json").write_text(json.dumps([{"needs": ["a"]}, {"needs": ["a", "b"]}, {"needs": ["b"]}]))
    (task / "client_v1.py").write_text("x = 1\n")
    client = write_client(tmp_path / "c.py", "x = 2\n")
    scorer = sh.Scorer(tmp_path, grade_fn=lambda d, c: _grade([True, True, False]))
    out = scorer.score("t", client, _grade([True, False, False]))
    assert json.loads(out["change_credit"]) == {"a": 0.5, "b": 0.0}
    assert out["regression_rate"] == 1 / 3 and out["n_tests"] == 3  # existing columns as before
    assert "change_credit" not in scorer.score("t", tmp_path / "missing.py")
    assert "change_credit" in sh.SCORE_COLUMNS
