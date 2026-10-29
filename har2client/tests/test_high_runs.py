"""scripts/high_runs.py: the per-task mean over the high-effort runs (PREREG.md deviation 2026-10-27), on synthetic rows."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import high_runs as hr  # noqa: E402


def rows(passes: dict, status: str = "ok") -> list[dict]:
    return [{"task": t, "run": "1", "status": status, "pass_rate": p} for t, p in passes.items()]


def test_merge_numbers_the_runs_and_keeps_every_row():
    merged = hr.merge_runs([rows({"a": 1.0, "b": 0.0}), rows({"b": 0.5, "a": 0.5}), rows({"a": 0.0, "b": 1.0})])
    assert len(merged) == 6
    assert sorted({r["run"] for r in merged}) == ["1", "2", "3"]
    assert {(r["task"], r["run"]): r["pass_rate"] for r in merged}[("a", "2")] == 0.5  # task alignment is by id, not row order


def test_merge_does_not_change_its_input():
    first = rows({"a": 1.0})
    hr.merge_runs([first, rows({"a": 0.0})])
    assert first[0]["run"] == "1" and len(first) == 1


def test_a_task_missing_from_a_later_run_raises_and_names_it():
    with pytest.raises(hr.RunMismatchError, match=r"run 2.*missing.*\['b'\]"):
        hr.merge_runs([rows({"a": 1, "b": 1}), rows({"a": 1})])


def test_a_task_missing_from_the_first_run_raises():
    with pytest.raises(hr.RunMismatchError, match=r"run 1.*missing.*\['b'\]"):
        hr.merge_runs([rows({"a": 1}), rows({"a": 1, "b": 1})])


def test_an_api_error_row_raises_because_the_task_would_not_have_all_runs():
    bad = rows({"a": 1}) + rows({"b": 0}, status="api_error")
    with pytest.raises(hr.RunMismatchError, match="api_error"):
        hr.merge_runs([bad, rows({"a": 1, "b": 1})])


def test_a_duplicate_task_in_one_run_raises():
    with pytest.raises(hr.RunMismatchError, match="twice"):
        hr.merge_runs([rows({"a": 1}) + rows({"a": 0}), rows({"a": 1})])


def test_the_expected_task_count_is_checked_when_given():
    with pytest.raises(hr.RunMismatchError, match="expected 3"):
        hr.merge_runs([rows({"a": 1}), rows({"a": 1})], expected=3)


def test_one_run_is_returned_as_is_but_still_checked():
    assert len(hr.merge_runs([rows({"a": 1})])) == 1
    with pytest.raises(hr.RunMismatchError):
        hr.merge_runs([rows({"a": 1}, status="api_error")])


def test_no_runs_raise():
    with pytest.raises(hr.RunMismatchError, match="no runs"):
        hr.merge_runs([])


def test_extra_run_paths_are_the_existing_r2_and_r3_files(tmp_path):
    (tmp_path / "x_high_r2.csv").write_text("a\n")
    assert hr.extra_run_paths(tmp_path, "x_high") == [tmp_path / "x_high_r2.csv"]
    (tmp_path / "x_high_r3.csv").write_text("a\n")
    assert [p.name for p in hr.extra_run_paths(tmp_path, "x_high")] == ["x_high_r2.csv", "x_high_r3.csv"]


def test_extra_run_paths_refuse_r3_without_r2(tmp_path):
    (tmp_path / "x_high_r3.csv").write_text("a\n")
    with pytest.raises(hr.RunMismatchError, match="r3 without r2"):
        hr.extra_run_paths(tmp_path, "x_high")


def test_label_names_the_run_count_only_above_one():
    assert hr.label("high", 3) == "high (mean of 3 runs)"
    assert hr.label("low", 1) == "low"
