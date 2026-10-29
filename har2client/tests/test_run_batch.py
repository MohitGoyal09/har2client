"""Tests for scripts/run_batch.py with a fake solver (no network)."""

import csv
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from har2client import build_task, ops
from har2client.refclient import write_ref_client
from har2client.solve import beam

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_batch.py"
_spec = importlib.util.spec_from_file_location("run_batch", SCRIPT)
rb = importlib.util.module_from_spec(_spec)
sys.modules["run_batch"] = rb  # dataclasses look the module up here
_spec.loader.exec_module(rb)

FIX = Path(__file__).parent / "fixtures"
SECRET = "sk-test-DO-NOT-PRINT-123456"
LIMIT_HEADER = "x-ratelimit-remaining-output-tokens-day"


@pytest.fixture(scope="module")
def tasks_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("pilot")
    build_task.build_task(ops.load_spec(FIX / "bookshelf_apikey.yaml"), "acme:alpha", root, seed=1)
    build_task.build_task(ops.load_spec(FIX / "bookshelf_basic.yaml"), "acme:beta", root, seed=2)
    for task in root.iterdir():
        write_ref_client(task)
    return root


class FakeSolver:
    """Same call shape as beam.solve. `mode` picks the behaviour per call."""

    def __init__(self, mode="good", daily_after=None):
        self.mode, self.daily_after, self.calls = mode, daily_after, []

    def __call__(self, task_dir, out_path, effort="high", har_mode="raw", *, client=None, **_):
        self.calls.append((Path(task_dir).name, effort, har_mode))
        if self.daily_after is not None and len(self.calls) > self.daily_after:
            raise beam.DailyAllowanceExhausted("spent")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log = {
            "status": "ok",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "reasoning_tokens": 30},
            "wall_time_s": 1.5,
            "rate_limit_headers": {LIMIT_HEADER: 1000 - 10 * len(self.calls)},
        }
        if self.mode == "api_error":
            raise RuntimeError("boom")
        if self.mode == "no_code":
            log["status"] = "no_code"
        elif self.mode == "broken":
            out_path.write_text("class Client:\n    def __init__(self, base_url, api_key):\n        raise RuntimeError('x')\n")
        else:
            shutil.copy(Path(task_dir) / "ref_client.py", out_path)
        out_path.with_name("solve_log.json").write_text(json.dumps(log))
        return log


def run(tasks, out, solver, **kw):
    args = dict(efforts=["low", "high"], har_modes=["raw"], runs=1, concurrency=1, solve_fn=solver, log=lambda *_: None)
    args.update(kw)
    return rb.run_batch(tasks, out, **args)


def read_csv(out):
    with open(Path(out) / "results.csv", newline="") as fh:
        return list(csv.DictReader(fh))


# ---- .env ---------------------------------------------------------------


def test_load_env_key_reads_dotenv_when_env_lacks_it(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"# comment\nOTHER=1\nexport REFLECTION_API_KEY = \"{SECRET}\"\n")
    environ = {}
    assert rb.load_env_key(env_file, environ) is True
    assert environ["REFLECTION_API_KEY"] == SECRET


def test_load_env_key_keeps_existing_env_value_and_tolerates_missing_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("REFLECTION_API_KEY=from-file\n")
    environ = {"REFLECTION_API_KEY": "from-env"}
    rb.load_env_key(env_file, environ)
    assert environ["REFLECTION_API_KEY"] == "from-env"
    other = {}
    assert rb.load_env_key(tmp_path / "nope", other) is False and other == {}


def test_load_env_key_handles_single_quotes_and_blank_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("REFLECTION_API_KEY='abc'\n")
    environ = {}
    rb.load_env_key(env_file, environ)
    assert environ["REFLECTION_API_KEY"] == "abc"
    env_file.write_text("REFLECTION_API_KEY=\n")
    other = {}
    assert rb.load_env_key(env_file, other) is False and other == {}


# ---- planning -----------------------------------------------------------


def test_plan_cells_layout(tasks_dir, tmp_path):
    cells = rb.plan_cells(rb.find_tasks(tasks_dir), ["low", "high"], ["raw", "compact"], 2, tmp_path)
    assert len(cells) == 2 * 2 * 2 * 2
    first = cells[0]
    assert first.task_dir.name == "acme_alpha"
    assert first.dir == tmp_path / "acme_alpha" / "low-raw-r1"
    assert first.client_path == first.dir / "client.py"
    assert cells[1].dir.name == "low-raw-r2"


def test_find_tasks_ignores_dirs_without_task_json(tasks_dir, tmp_path):
    (tasks_dir / "not_a_task").mkdir(exist_ok=True)
    try:
        assert [t.name for t in rb.find_tasks(tasks_dir)] == ["acme_alpha", "acme_beta"]
    finally:
        (tasks_dir / "not_a_task").rmdir()


# ---- run ----------------------------------------------------------------


def test_full_run_writes_cells_grades_and_csv(tasks_dir, tmp_path):
    solver = FakeSolver()
    result = run(tasks_dir, tmp_path, solver, concurrency=4)
    assert len(solver.calls) == 4 and result.remaining == [] and not result.stopped
    cell = tmp_path / "acme_alpha" / "high-raw-r1"
    assert (cell / "client.py").is_file() and (cell / "solve_log.json").is_file()
    grade = json.loads((cell / "grade.json").read_text())
    assert grade["pass_rate"] == 1.0 and grade["status"] == "ok"

    rows = read_csv(tmp_path)
    assert len(rows) == 4
    row = next(r for r in rows if r["task"] == "acme_alpha" and r["effort"] == "high")
    assert row["api_id"] == "acme:alpha" and row["auth_type"] == "apiKey"
    assert row["har_mode"] == "raw" and row["run"] == "1" and row["status"] == "ok"
    assert float(row["pass_rate"]) == 1.0 and float(row["strict_pass_rate"]) == 1.0 and int(row["n_tests"]) > 0
    assert row["invented_endpoint_calls"] == "0"
    assert row["hardcoded_secret"] == "False" and row["hardcoded_host"] == "False"
    assert (row["prompt_tokens"], row["completion_tokens"], row["reasoning_tokens"]) == ("100", "50", "30")
    assert float(row["wall_time_s"]) == 1.5
    assert row["remaining_output_tokens_day"] != ""
    beta = next(r for r in rows if r["task"] == "acme_beta")
    assert beta["auth_type"] == "basic"


def test_csv_header_order(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, FakeSolver(), efforts=["low"])
    header = (tmp_path / "results.csv").read_text().splitlines()[0].split(",")
    head = ["task", "api_id", "auth_type", "effort", "har_mode", "run", "status", "pass_rate", "strict_pass_rate", "n_tests"]
    tail = ["invented_endpoint_calls", "hardcoded_secret", "hardcoded_host", "prompt_tokens",
            "completion_tokens", "reasoning_tokens", "wall_time_s", "remaining_output_tokens_day"]
    assert header[:10] == head and header[-8:] == tail
    assert all(c.startswith("fail_") for c in header[10:-8]) and len(header) > 18


def test_resume_skips_cells_with_grade_json(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, FakeSolver())
    again = FakeSolver()
    result = run(tasks_dir, tmp_path, again)
    assert again.calls == [] and result.remaining == []
    assert len(read_csv(tmp_path)) == 4  # resumed cells still appear in the CSV


def test_resume_runs_only_missing_cells(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, FakeSolver())
    (tmp_path / "acme_beta" / "low-raw-r1" / "grade.json").unlink()
    again = FakeSolver()
    run(tasks_dir, tmp_path, again)
    assert again.calls == [("acme_beta", "low", "raw")]


def test_no_code_is_a_recorded_result(tasks_dir, tmp_path):
    solver = FakeSolver("no_code")
    result = run(tasks_dir, tmp_path, solver)
    assert len(solver.calls) == 4 and result.remaining == []  # batch did not stop
    for r in read_csv(tmp_path):
        assert r["status"] == "no_code" and float(r["pass_rate"]) == 0.0 and int(r["n_tests"]) > 0
    grade = json.loads((tmp_path / "acme_alpha" / "low-raw-r1" / "grade.json").read_text())
    assert grade["status"] == "no_code" and grade["pass_rate"] == 0.0


def test_api_error_is_written_but_the_cell_stays_open_for_resume(tasks_dir, tmp_path):
    solver = FakeSolver("api_error")
    result = run(tasks_dir, tmp_path, solver)
    assert len(solver.calls) == 4 and not result.stopped  # batch did not stop
    assert len(result.remaining) == 4  # not done: the next run retries them
    for r in read_csv(tmp_path):
        assert r["status"] == "api_error" and int(r["n_tests"]) > 0
    grade = json.loads((tmp_path / "acme_alpha" / "low-raw-r1" / "grade.json").read_text())
    assert grade["status"] == "api_error"


def test_resume_retries_api_error_cells_and_keeps_the_rest(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, FakeSolver())
    for cell in ("acme_alpha/low-raw-r1", "acme_beta/high-raw-r1"):
        (tmp_path / cell / "grade.json").write_text(json.dumps({"status": "api_error", "pass_rate": 0.0}))
    again = FakeSolver()
    result = run(tasks_dir, tmp_path, again)
    assert sorted(again.calls) == [("acme_alpha", "low", "raw"), ("acme_beta", "high", "raw")]
    assert result.remaining == []
    assert {r["status"] for r in read_csv(tmp_path)} == {"ok"}


def test_failure_reasons_become_columns(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, FakeSolver("broken"), efforts=["low"])
    rows = read_csv(tmp_path)
    assert all(float(r["pass_rate"]) == 0.0 for r in rows)
    assert all(int(r["fail_exception"]) > 0 for r in rows)
    assert all(r["fail_timeout"] == "0" for r in rows)


def test_daily_limit_stops_batch_and_reports_remaining(tasks_dir, tmp_path):
    solver = FakeSolver(daily_after=2)
    result = run(tasks_dir, tmp_path, solver)
    assert result.stopped is True
    assert len(solver.calls) == 3  # the third call hit the limit; no more were tried
    assert len(result.remaining) == 2 and len(read_csv(tmp_path)) == 2
    hit = result.remaining[0]
    assert not (hit.dir / "grade.json").exists()  # not recorded: it must re-run later
    resumed = FakeSolver()
    final = run(tasks_dir, tmp_path, resumed)
    assert len(resumed.calls) == 2 and not final.stopped and len(read_csv(tmp_path)) == 4


def test_grade_crash_is_recorded_not_fatal(tasks_dir, tmp_path):
    def bad_grade(*_a, **_k):
        raise ValueError("grader broke")

    result = run(tasks_dir, tmp_path, FakeSolver(), grade_fn=bad_grade, efforts=["low"])
    assert result.remaining == []
    assert {r["status"] for r in read_csv(tmp_path)} == {"grade_error"}


def test_interrupt_stops_the_cells_still_queued(tasks_dir, tmp_path):
    class Interrupted(FakeSolver):
        def __call__(self, *args, **kw):
            self.calls.append(args[0])
            raise KeyboardInterrupt

    solver = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        run(tasks_dir, tmp_path, solver)
    assert len(solver.calls) == 1  # the other cells were skipped, not run (each is a paid call)


def test_a_corrupt_grade_json_is_not_a_finished_cell(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, FakeSolver(), efforts=["low"])
    (tmp_path / "acme_beta" / "low-raw-r1" / "grade.json").write_text('{"pass_rate": 0.')  # killed mid-write
    again = FakeSolver()
    result = run(tasks_dir, tmp_path, again, efforts=["low"])
    assert again.calls == [("acme_beta", "low", "raw")] and result.remaining == []
    assert len(read_csv(tmp_path)) == 2


# ---- summary ------------------------------------------------------------


def test_summary_means_by_effort_and_mode_and_total_tokens(tasks_dir, tmp_path):
    result = run(tasks_dir, tmp_path, FakeSolver(), har_modes=["raw", "compact"])
    text = rb.summarize(result.rows)
    assert "low" in text and "high" in text and "raw" in text and "compact" in text
    assert "1.000" in text and "strict" in text
    assert "prompt=800" in text and "completion=400" in text and "reasoning=240" in text


def test_summary_mixes_failures_into_the_mean():
    rows = [
        {"effort": "low", "har_mode": "raw", "pass_rate": 1.0, "prompt_tokens": 1, "completion_tokens": 2, "reasoning_tokens": 3},
        {"effort": "low", "har_mode": "raw", "pass_rate": 0.0, "prompt_tokens": None, "completion_tokens": None, "reasoning_tokens": None},
    ]
    text = rb.summarize(rows)
    assert "0.500" in text and "prompt=1" in text


def test_summary_reports_strict_mean_next_to_pass_mean():
    base = {"effort": "low", "har_mode": "raw", "prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 1}
    rows = [
        {**base, "pass_rate": 1.0, "strict_pass_rate": 0.5},
        {**base, "pass_rate": 1.0, "strict_pass_rate": 0.0},
        {**base, "pass_rate": 0.0, "strict_pass_rate": 0.0},
    ]
    line = next(l for l in rb.summarize(rows).splitlines() if l.startswith("low"))
    assert line.split() == ["low", "raw", "3", "0.667", "0.167"]


def test_summary_strict_is_dash_when_no_row_has_it():
    rows = [{"effort": "low", "har_mode": "raw", "pass_rate": 1.0, "prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 1}]
    line = next(l for l in rb.summarize(rows).splitlines() if l.startswith("low"))
    assert line.split() == ["low", "raw", "1", "1.000", "-"]


def test_summary_excludes_api_error_rows_and_reports_the_count():
    base = {"effort": "low", "har_mode": "raw", "prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 1}
    rows = [
        {**base, "status": "ok", "pass_rate": 1.0},
        {**base, "status": "no_code", "pass_rate": 0.0},
        {**base, "status": "api_error", "pass_rate": 0.0},
    ]
    text = rb.summarize(rows)
    line = next(l for l in text.splitlines() if l.startswith("low"))
    assert line.split()[:4] == ["low", "raw", "2", "0.500"]  # no_code still counts as 0
    assert "api_error rows excluded: 1" in text


def test_api_error_stub_has_zero_strict_pass_rate(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, FakeSolver("api_error"))
    assert all(float(r["strict_pass_rate"]) == 0.0 for r in read_csv(tmp_path))


# ---- main ---------------------------------------------------------------


def test_main_without_key_exits_before_any_work(tasks_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REFLECTION_API_KEY", raising=False)
    monkeypatch.setattr(rb, "ENV_PATH", tmp_path / "missing.env")
    code = rb.main(["--tasks", str(tasks_dir), "--out", str(tmp_path / "o")])
    assert code == 2 and "REFLECTION_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_main_runs_prints_summary_and_never_prints_key(tasks_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REFLECTION_API_KEY", SECRET)
    solver = FakeSolver()
    code = rb.main(
        ["--tasks", str(tasks_dir), "--out", str(tmp_path / "o"), "--effort", "low", "high",
         "--har-mode", "raw", "--runs", "1", "--concurrency", "2"],
        solve_fn=solver,
        client=object(),
    )
    captured = capsys.readouterr()
    assert code == 0 and len(solver.calls) == 4
    assert SECRET not in captured.out + captured.err
    assert "mean pass rate" in captured.out.lower()


def test_main_returns_nonzero_when_stopped_by_daily_limit(tasks_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REFLECTION_API_KEY", SECRET)
    code = rb.main(
        ["--tasks", str(tasks_dir), "--out", str(tmp_path / "o"), "--effort", "low", "high"],
        solve_fn=FakeSolver(daily_after=1),
        client=object(),
    )
    out = capsys.readouterr().out
    assert code == 3 and "remaining" in out.lower()
