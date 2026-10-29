"""Tests for scripts/run_models.py: the plan, the token check, resume, the output layout. No network, no real model."""

import csv
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from har2client import build_task, ops
from har2client.refclient import write_ref_client
from har2client.solve import beam, openai_compat

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIX = Path(__file__).parent / "fixtures"
SECRET = "hf_test_DO_NOT_PRINT_123456"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rm = load_script("run_models")
fake = load_script("fake_openai_server")


@pytest.fixture(scope="module")
def tasks_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("models_tasks")
    for spec, api, seed in (("bookshelf_apikey", "acme:alpha", 1), ("bookshelf_basic", "acme:beta", 2), ("bookshelf_bearer", "acme:gamma", 3)):
        build_task.build_task(ops.load_spec(FIX / f"{spec}.yaml"), api, root, seed=seed)
    for task in root.iterdir():
        write_ref_client(task)
    return root


def entry(slug, effort="high", enabled=True, **extra):
    return {"id": f"org/{slug}", "slug": slug, "label": slug.upper(), "reasoning_effort": effort, "enabled": enabled,
            "notes": "n", "price_in_per_mtok": 1.0, "price_out_per_mtok": 2.0, **extra}


@pytest.fixture
def models_file(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": [entry("alpha"), entry("beta", effort=None), entry("off", enabled=False, notes="not confirmed")]}))
    return path


class FakeSolver:
    """Same call shape as openai_compat.solve; copies the task's reference client. `fail` = call numbers that raise."""

    def __init__(self, fail=(), stop_after=None):
        self.calls, self.fail, self.stop_after = [], set(fail), stop_after

    def __call__(self, task_dir, out_path, effort="high", har_mode="raw", *, client=None, **_):
        self.calls.append(Path(task_dir).name)
        if self.stop_after is not None and len(self.calls) > self.stop_after:
            raise beam.DailyAllowanceExhausted("credits spent")
        if len(self.calls) in self.fail:
            raise RuntimeError("boom")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(task_dir) / "ref_client.py", out_path)
        out_path.with_name("solve_log.json").write_text(json.dumps(
            {"status": "ok", "usage": {"prompt_tokens": 100, "completion_tokens": 50, "reasoning_tokens": 30}, "wall_time_s": 1.0}))
        return {"status": "ok"}


def run(tasks_dir, models_file, out_root, *extra, solver=None):
    solver = solver or FakeSolver()
    argv = ["--models-file", str(models_file), "--out-root", str(out_root), "--concurrency", "1",
            "--suite-path", f"tierb50={tasks_dir}", "--suite-path", f"v3base={tasks_dir}", *extra]
    return rm.main(argv, solve_factory=lambda model: solver, client=object()), solver


def read_results(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# ---- config ------------------------------------------------------------------


def test_shipped_models_file_is_valid_and_seeded():
    models = {m.slug: m for m in rm.load_models(rm.MODELS_FILE)}
    assert models["gpt-oss-120b"].id == "openai/gpt-oss-120b" and models["gpt-oss-120b"].reasoning_effort == "high"
    assert models["gpt-oss-20b"].id == "openai/gpt-oss-20b" and models["gpt-oss-20b"].reasoning_effort == "high"
    assert len(models) >= 4 and all(m.notes for m in models.values())
    assert all(m.notes for m in models.values() if not m.enabled)


@pytest.mark.parametrize("bad", [entry("x", effort="xhigh"), entry("Bad Slug"), {"id": "a/b", "slug": "ok"}])
def test_load_models_rejects_bad_entries(tmp_path, bad):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"models": [bad]}))
    with pytest.raises(rm.ConfigError):
        rm.load_models(path)


def test_load_models_rejects_duplicate_slugs(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"models": [entry("a"), entry("a")]}))
    with pytest.raises(rm.ConfigError, match="duplicate"):
        rm.load_models(path)


# ---- plan and dry run ----------------------------------------------------------


def test_plan_counts_cells_and_layout(tasks_dir, models_file, tmp_path):
    models = [m for m in rm.load_models(models_file) if m.enabled]
    jobs = rm.plan_jobs(models, ["tierb50", "v3base"], {"tierb50": tasks_dir, "v3base": tasks_dir}, tmp_path / "out", None)
    assert [(j.model.slug, j.suite, j.n_tasks, j.n_todo) for j in jobs] == [
        ("alpha", "tierb50", 3, 3), ("alpha", "v3base", 3, 3), ("beta", "tierb50", 3, 3), ("beta", "v3base", 3, 3)]
    assert jobs[0].out_dir == tmp_path / "out" / "alpha" / "tierb50"
    assert jobs[0].est_output_tokens > 0


def test_limit_shrinks_the_plan(tasks_dir, models_file, tmp_path):
    models = [m for m in rm.load_models(models_file) if m.enabled]
    jobs = rm.plan_jobs(models, ["tierb50"], {"tierb50": tasks_dir}, tmp_path / "out", 1)
    assert [j.n_tasks for j in jobs] == [1, 1]


def test_dry_run_prints_plan_and_calls_nothing(tasks_dir, models_file, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)  # a dry run needs no token
    out = tmp_path / "out"

    def boom(model):
        raise AssertionError("the solver must not be built in a dry run")

    code = rm.main(["--dry-run", "--models-file", str(models_file), "--out-root", str(out),
                    "--suite-path", f"tierb50={tasks_dir}", "--suite-path", f"v3base={tasks_dir}"], solve_factory=boom)
    text = capsys.readouterr().out
    assert code == 0 and not out.exists()
    assert "2 model(s) x 2 suite(s) = 4 job(s), 12 cell(s), 12 to run" in text
    assert "model alpha  id=org/alpha  reasoning_effort=high" in text
    assert "reasoning_effort=not sent" in text
    assert "est output" in text and "cost" in text
    assert "skipped (disabled): off" in text


@pytest.mark.skipif(not (SCRIPTS.parent / "runs" / "tierb50").is_dir(), reason="needs runs/tierb50 (git-ignored; build it first)")
def test_dry_run_default_plan_over_the_real_models_file(capsys):
    assert rm.main(["--dry-run"]) == 0
    assert "gpt-oss-120b" in capsys.readouterr().out


def test_unknown_model_and_missing_suite_dir_exit_2(tasks_dir, models_file, tmp_path, capsys):
    base = ["--models-file", str(models_file), "--out-root", str(tmp_path / "o"), "--suite-path", f"tierb50={tasks_dir}", "--suites", "tierb50"]
    assert rm.main([*base, "--models", "nope", "--dry-run"]) == 2 and "unknown model" in capsys.readouterr().err
    assert rm.main([*base[:-2], "--suite-path", f"v3base={tmp_path / 'missing'}", "--suites", "v3base", "--dry-run"]) == 2
    assert "no task dirs" in capsys.readouterr().err


# ---- token check ---------------------------------------------------------------


def test_missing_token_exits_2_before_any_work(tasks_dir, models_file, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    out = tmp_path / "out"
    code = rm.main(["--models-file", str(models_file), "--out-root", str(out), "--env-path", str(tmp_path / "none.env"),
                    "--suite-path", f"tierb50={tasks_dir}", "--suites", "tierb50"])
    err = capsys.readouterr().err
    assert code == 2 and "HF_TOKEN is not set" in err
    assert not out.exists()


def test_token_in_env_file_is_used_and_never_printed(tasks_dir, models_file, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"HF_TOKEN={SECRET}\n")
    state = fake.FakeState(tasks_dir, None, [])
    with fake.serve(state) as base_url:
        code = rm.main(["--models-file", str(models_file), "--out-root", str(tmp_path / "out"), "--env-path", str(env),
                        "--models", "alpha", "--base-url", base_url, "--suite-path", f"tierb50={tasks_dir}", "--suites", "tierb50",
                        "--concurrency", "2"])
    captured = capsys.readouterr()
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert code == 0 and len(state.requests) == 3
    assert SECRET not in captured.out + captured.err
    rows = read_results(tmp_path / "out" / "alpha" / "tierb50" / "results.csv")
    assert [float(r["pass_rate"]) for r in rows] == [1.0, 1.0, 1.0]
    log = json.loads(next((tmp_path / "out" / "alpha" / "tierb50").glob("*/high-raw-r1/solve_log.json")).read_text())
    assert log["provider"] == "openai_compat" and "response_model" in log and SECRET not in json.dumps(log)


def test_solver_factory_sends_effort_only_for_models_that_take_it(models_file):
    alpha, beta, _ = rm.load_models(models_file)
    factory = rm.make_solver_factory("http://x/v1", "HF_TOKEN")
    assert factory(alpha).keywords["supports_reasoning_effort"] is True
    assert factory(beta).keywords["supports_reasoning_effort"] is False
    assert factory(alpha).keywords["model"] == "org/alpha" and factory(alpha).func is openai_compat.solve
    assert (alpha.cell_effort, beta.cell_effort) == ("high", "high")


def test_qwen_thinking_setting_reaches_all_suites():
    models = rm.load_models(Path(__file__).resolve().parents[1] / 'data/comparison_models.json')
    qwen = next(m for m in models if m.slug == 'qwen3.5-397b-thinking')
    assert qwen.enable_thinking is True
    factory = rm.make_solver_factory('http://x/v1', 'HF_TOKEN')
    for suite in ('tierb50', 'v3base', 'v3hint'):
        assert factory(qwen, suite).keywords['enable_thinking'] is True
    for suite in ('probe0', 'probe5', 'probe20'):
        assert factory(qwen, suite).keywords['request_options']['extra_body'] == {'enable_thinking': True}


# ---- running, layout, resume ----------------------------------------------------


def test_run_writes_layout_results_and_done_marker(tasks_dir, models_file, tmp_path, capsys):
    out = tmp_path / "out"
    code, solver = run(tasks_dir, models_file, out)
    text = capsys.readouterr().out
    assert code == 0 and len(solver.calls) == 12
    for slug in ("alpha", "beta"):
        for suite in ("tierb50", "v3base"):
            suite_dir = out / slug / suite
            assert (suite_dir / ".done").is_file() and (suite_dir / "results.csv").is_file()
            assert (suite_dir / "acme_alpha" / "high-raw-r1" / "grade.json").is_file()
            assert (suite_dir / "acme_alpha" / "high-raw-r1" / "client.py").is_file()
    assert not (out / "off").exists()
    # one progress line per cell, prefixed with model and suite
    assert "[alpha/tierb50] [1/3] acme_alpha/high-raw-r1 ok pass_rate=1.000" in text


def test_finished_suite_is_not_run_again(tasks_dir, models_file, tmp_path):
    out = tmp_path / "out"
    run(tasks_dir, models_file, out)
    code, solver = run(tasks_dir, models_file, out)
    assert code == 0 and solver.calls == []


def test_limit_runs_first_n_tasks_without_marker_then_resume_skips_recorded(tasks_dir, models_file, tmp_path):
    out = tmp_path / "out"
    code, solver = run(tasks_dir, models_file, out, "--models", "alpha", "--suites", "tierb50", "--limit", "1")
    assert code == 0 and len(solver.calls) == 1
    suite_dir = out / "alpha" / "tierb50"
    assert not (suite_dir / ".done").exists()  # a smoke run is not a finished suite
    first = solver.calls[0]
    code, solver = run(tasks_dir, models_file, out, "--models", "alpha", "--suites", "tierb50")
    assert code == 0 and len(solver.calls) == 2 and first not in solver.calls  # recorded cell skipped
    assert (suite_dir / ".done").is_file() and len(read_results(suite_dir / "results.csv")) == 3


def test_api_error_cells_are_retried_in_the_same_run(tasks_dir, models_file, tmp_path):
    out = tmp_path / "out"
    code, solver = run(tasks_dir, models_file, out, "--models", "alpha", "--suites", "tierb50", solver=FakeSolver(fail={2}))
    assert code == 0 and len(solver.calls) == 4  # 3 cells + 1 retry
    rows = read_results(out / "alpha" / "tierb50" / "results.csv")
    assert {r["status"] for r in rows} == {"ok"} and (out / "alpha" / "tierb50" / ".done").is_file()


def test_api_error_without_retries_exits_1_and_next_run_fixes_it(tasks_dir, models_file, tmp_path, capsys):
    out = tmp_path / "out"
    code, _ = run(tasks_dir, models_file, out, "--models", "alpha", "--suites", "tierb50", "--retries", "0", solver=FakeSolver(fail={2}))
    assert code == 1 and "not recorded" in capsys.readouterr().out
    assert not (out / "alpha" / "tierb50" / ".done").exists()
    code, solver = run(tasks_dir, models_file, out, "--models", "alpha", "--suites", "tierb50")
    assert code == 0 and len(solver.calls) == 1  # only the failed cell


def test_spent_credits_stop_the_run_with_exit_3(tasks_dir, models_file, tmp_path, capsys):
    out = tmp_path / "out"
    code, solver = run(tasks_dir, models_file, out, solver=FakeSolver(stop_after=1))
    assert code == 3 and "stopped" in capsys.readouterr().out
    assert not (out / "alpha" / "tierb50" / ".done").exists() and not (out / "beta").exists()


def test_smoke_subset_works_without_windows_symlink_privileges(tasks_dir, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError('symlinks require privilege')
    monkeypatch.setattr(Path, 'symlink_to', denied)
    with rm.limited_tasks(tasks_dir, 1) as subset:
        selected = rm.rb.find_tasks(subset)
        assert len(selected) == 1
        assert (selected[0] / 'goal.md').read_bytes() == (rm.rb.find_tasks(tasks_dir)[0] / 'goal.md').read_bytes()


def test_invalid_cost_limits_are_rejected(capsys):
    for limit in ('-1', '0', 'nan', 'inf'):
        assert rm.main(['--cost-limit-usd', limit]) == 2
    assert 'positive and finite' in capsys.readouterr().err
