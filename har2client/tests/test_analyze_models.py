"""Tests for scripts/analyze_models.py on synthetic CSVs (no model runs, no network)."""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("analyze_models", SCRIPTS / "analyze_models.py")
am = importlib.util.module_from_spec(spec)
sys.modules["analyze_models"] = am
spec.loader.exec_module(am)

N_BOOT = 200
COLUMNS = ["task", "arm", "effort", "run", "status", "pass_rate", "prompt_tokens", "completion_tokens"]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, restval="")
        writer.writeheader()
        writer.writerows(rows)


def cells(effort, rates, tokens, n_tasks=None, status="ok"):
    return [{"task": f"t{i}", "effort": effort, "run": 1, "status": status, "pass_rate": r, "prompt_tokens": 1000, "completion_tokens": tokens}
            for i, r in enumerate(rates)]


@pytest.fixture
def results_dir(tmp_path):
    d = tmp_path / "results"
    # case 1 (4 tasks): low .25, high .75; case 3 base (4 tasks): low 0, medium .25, high .5
    write_csv(d / "tierb50_v1.csv", cells("low", [0, 0, 0.5, 0.5], 1000) + cells("high", [1, 1, 0.5, 0.5], 4000))
    write_csv(d / "tierb50_medium.csv", cells("medium", [1, 0, 0.5, 0.5], 2000))
    for effort, rates, tok in (("low", [0, 0, 0, 0], 1500), ("medium", [1, 0, 0, 0], 3000), ("high", [1, 1, 0, 0], 8000)):
        write_csv(d / f"heal_v3_eval_base_{effort}.csv", cells(effort, rates, tok))
    return d


def add_model(root: Path, slug: str, suite: str, rates, tokens, errors=0, n_tasks=4):
    rows = cells("high", rates, tokens) + [
        {"task": f"e{i}", "effort": "high", "run": 1, "status": "api_error", "pass_rate": 0.0} for i in range(errors)]
    write_csv(root / slug / suite / "results.csv", rows)


def make_models_file(tmp_path, *slugs):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": [{"id": f"o/{s}", "slug": s, "label": f"Label {s}", "reasoning_effort": None} for s in slugs]}))
    return path


def run_main(tmp_path, results_dir, models_root, models_file, *extra):
    out = tmp_path / "out"
    figs = tmp_path / "figs"
    code = am.main(["--results-dir", str(results_dir), "--models-root", str(models_root), "--models-file", str(models_file),
                    "--out", str(out), "--fig-dir", str(figs), "--n-boot", str(N_BOOT), *extra])
    return code, out, figs


# ---- loading and numbers -------------------------------------------------------


def test_read_cells_leaves_out_api_errors_and_counts_them(tmp_path):
    root = tmp_path / "m"
    add_model(root, "a", "tierb50", [1, 0.5], 700, errors=2)
    scores, tokens, errors = am.read_cells(root / "a" / "tierb50" / "results.csv")
    assert scores == {"t0": 1.0, "t1": 0.5} and tokens == {"t0": 700.0, "t1": 700.0} and errors == 2


def test_beam_rows_are_one_per_effort(results_dir):
    beam = am.load_beam(results_dir)
    labels = {(e.suite, e.label) for e in beam}
    assert ("tierb50", "Beam (low)") in labels and ("tierb50", "Beam (medium)") in labels and ("tierb50", "Beam (high)") in labels
    assert ("v3base", "Beam (high)") in labels and not any(e.suite == "v3hint" for e in beam)
    low = next(e for e in beam if e.suite == "tierb50" and e.effort == "low")
    assert dict(low.scores) == {"t0": 0, "t1": 0, "t2": 0.5, "t3": 0.5}  # the high rows of the shared CSV are not mixed in


def test_stats_pass_rate_full_solve_tokens_and_errors(tmp_path):
    entry = am.Entry("m", "M", "model", "tierb50", None, {"a": 1.0, "b": 0.5, "c": 0.0, "d": 1.0}, {"a": 100.0, "b": 300.0}, 3)
    stats = am.entry_stats(entry, N_BOOT, 1)
    assert stats["n"] == 4 and stats["pass_rate"] == pytest.approx(62.5) and stats["full_solve_pct"] == pytest.approx(50.0)
    assert stats["mean_output_tokens"] == pytest.approx(200.0) and stats["api_errors"] == 3
    assert stats["ci_low"] <= 62.5 <= stats["ci_high"]


def test_paired_difference_pairs_only_shared_tasks_and_marks_partial(results_dir, tmp_path):
    root = tmp_path / "m"
    add_model(root, "same", "tierb50", [1, 1, 1, 1], 900)
    add_model(root, "fewer", "tierb50", [1, 1, 1], 900)
    entries = am.load_beam(results_dir) + am.load_models(root, tmp_path / "none.json")
    stats = {(r["key"], r["suite"]): r for r in am.build_stats(entries, N_BOOT, 1)}
    same = stats["same", "tierb50"]["paired_vs_beam_high"]
    assert same["n"] == 4 and same["diff"] == pytest.approx(100 * (1 - 0.75)) and same["ci_low"] <= same["diff"] <= same["ci_high"]
    assert not same["partial"]
    fewer = stats["fewer", "tierb50"]["paired_vs_beam_high"]  # t0..t2 only: Beam high .75 -> model 1 on t0, t1 (1 vs 1), t2 (1 vs .5)
    assert fewer["n"] == 3 and fewer["partial"] and fewer["n_model"] == 3 and fewer["n_ref"] == 4
    assert fewer["diff"] == pytest.approx(100 * (0 + 0 + 0.5) / 3)
    assert stats["beam-high", "tierb50"]["paired_vs_beam_high"] is None
    report = am.render_report(list(stats.values()), am.pareto_info([], list(stats.values())), N_BOOT, 1)
    assert "partial: n=3 shared of 4 Beam" in report and "+25.0" in report


def test_no_shared_tasks_gives_no_pairing(results_dir, tmp_path):
    root = tmp_path / "m"
    write_csv(root / "other" / "tierb50" / "results.csv", [{**c, "task": f"x{i}"} for i, c in enumerate(cells("high", [1, 1], 900))])
    entries = am.load_beam(results_dir) + am.load_models(root, tmp_path / "none.json")
    stats = am.build_stats(entries, N_BOOT, 1)
    row = next(r for r in stats if r["key"] == "other")
    assert row["paired_vs_beam_high"] is None
    assert "no shared tasks" in am.render_report(stats, {}, N_BOOT, 1)


def add_beam_high_repeats(d: Path) -> None:
    """tierb50 high run 1 is [1, 1, .5, .5] (fixture); runs 2 and 3 make the per-task means [1, 2/3, 1/3, 1/3]."""
    write_csv(d / "tierb50_high_r2.csv", cells("high", [1, 0.5, 0.5, 0], 4100))
    write_csv(d / "tierb50_high_r3.csv", cells("high", [1, 0.5, 0, 0.5], 4200))


def test_beam_high_is_the_mean_of_three_runs_and_low_stays_one_run(results_dir):
    add_beam_high_repeats(results_dir)
    beam = {(e.suite, e.effort): e for e in am.load_beam(results_dir)}
    high = beam["tierb50", "high"]
    assert high.runs == 3 and dict(high.scores) == pytest.approx({"t0": 1.0, "t1": 2 / 3, "t2": 1 / 3, "t3": 1 / 3})
    assert high.tokens["t0"] == pytest.approx((4000 + 4100 + 4200) / 3)
    assert beam["tierb50", "low"].runs == 1 and beam["tierb50", "medium"].runs == 1 and beam["v3base", "high"].runs == 1
    stats = {(r["suite"], r["effort"]): r for r in am.build_stats(list(beam.values()), N_BOOT, 1)}
    assert stats["tierb50", "high"]["display_label"] == "Beam (high, mean of 3 runs)"
    assert stats["tierb50", "low"]["display_label"] == "Beam (low)" and stats["tierb50", "high"]["label"] == am.REFERENCE
    assert "Beam (high, mean of 3 runs)" in am.render_report(list(stats.values()), {}, N_BOOT, 1)


def test_model_paired_against_the_three_run_beam_mean(results_dir, tmp_path):
    add_beam_high_repeats(results_dir)
    root = tmp_path / "m"
    add_model(root, "m", "tierb50", [1, 1, 1, 1], 900)
    entries = am.load_beam(results_dir) + am.load_models(root, tmp_path / "none.json")
    paired = next(r for r in am.build_stats(entries, N_BOOT, 1) if r["key"] == "m")["paired_vs_beam_high"]
    assert paired["diff"] == pytest.approx(100 * (1 - (1 + 2 / 3 + 1 / 3 + 1 / 3) / 4))


def test_beam_high_repeat_that_misses_a_task_raises(results_dir):
    write_csv(results_dir / "tierb50_high_r2.csv", cells("high", [1, 1, 1], 4000))
    with pytest.raises(am.high_runs.RunMismatchError):
        am.load_beam(results_dir)


def test_beam_case2_rows_come_from_main_full_by_arm_and_effort(results_dir):
    def row(task, arm, effort, status, rate, tokens):
        return {"task": task, "arm": arm, "effort": effort, "run": 1, "status": status, "pass_rate": rate, "completion_tokens": tokens}

    rows = [row(f"t{i}", arm, effort, "ok", rate, tok)
            for arm, rate, tok in (("placebo", 0.0, 100), ("P5", 0.5, 200), ("P20", 1.0, 300))
            for effort in am.EFFORTS for i in range(2)]
    rows += [row("t2", "placebo", "high", "no_code", "", 50), row("t3", "placebo", "high", "api_error", "", 0)]
    write_csv(results_dir / "case2_probe" / "main_full" / "results.csv", rows)
    # the old partial run must not be read
    write_csv(results_dir / "case2_probe" / "main" / "placebo.csv", cells("high", [1, 1, 1], 1))
    beam = {(e.suite, e.effort): e for e in am.load_beam(results_dir)}
    placebo, p5, p20 = beam["probe0", "high"], beam["probe5", "high"], beam["probe20", "high"]
    assert dict(placebo.scores) == {"t0": 0.0, "t1": 0.0, "t2": 0.0}  # no_code is a scored 0
    assert placebo.api_errors == 1 and placebo.runs == 1
    assert dict(p5.scores) == {"t0": 0.5, "t1": 0.5} and dict(p20.scores) == {"t0": 1.0, "t1": 1.0}
    assert {e for (suite, e) in beam if suite == "probe5"} == set(am.EFFORTS)
    assert "Beam: 1 run per task" in am.render_report(am.build_stats(list(beam.values()), N_BOOT, 1), {}, N_BOOT, 1)


def test_pareto_frontier_keeps_only_undominated_points():
    points = [(1000, 50), (2000, 40), (3000, 70), (5000, 70), (800, 10)]
    assert am.pareto_frontier(points) == [(800, 10), (1000, 50), (3000, 70)]


# ---- Beam only and 1, 2, 3+ models -----------------------------------------------


def test_beam_only_runs_and_says_no_other_models(results_dir, tmp_path, capsys):
    code, out, _ = run_main(tmp_path, results_dir, tmp_path / "no_models", tmp_path / "none.json", "--no-figures")
    assert code == 0 and "no other models run yet" in capsys.readouterr().out
    summary = json.loads((out / "summary.json").read_text())
    assert summary["other_models"] == [] and summary["note"] == "no other models run yet"
    assert not any(p["drawn"] for p in summary["pareto"].values())
    assert {r["label"] for r in summary["rows"]} == {"Beam (low)", "Beam (medium)", "Beam (high)"}
    assert "No other models run yet" in (out / "report.md").read_text()


def test_empty_inputs_exit_2(tmp_path, capsys):
    code, _, _ = run_main(tmp_path, tmp_path / "empty", tmp_path / "none", tmp_path / "none.json", "--no-figures")
    assert code == 2 and "no Beam CSVs" in capsys.readouterr().err


@pytest.mark.parametrize("n_other, drawn", [(1, False), (2, True), (3, True)])
def test_pareto_only_with_three_or_more_models(results_dir, tmp_path, n_other, drawn):
    root = tmp_path / "m"
    slugs = [f"m{i}" for i in range(n_other)]
    for i, slug in enumerate(slugs):
        for suite in ("tierb50", "v3base"):
            add_model(root, slug, suite, [1, 0.5, 0, 0], 700 * (i + 1))
    code, out, _ = run_main(tmp_path, results_dir, root, make_models_file(tmp_path, *slugs), "--no-figures")
    summary = json.loads((out / "summary.json").read_text())
    assert code == 0 and summary["other_models"] == slugs
    for suite in ("tierb50", "v3base"):
        info = summary["pareto"][suite]
        assert info["distinct_models"] == n_other + 1 and info["drawn"] is drawn and bool(info["frontier"]) is drawn
    assert ("Pareto line drawn" in (out / "report.md").read_text()) is drawn
    assert "Label m0" in (out / "report.md").read_text()


def test_model_with_only_api_errors_is_listed_with_no_score(results_dir, tmp_path):
    root = tmp_path / "m"
    add_model(root, "broken", "tierb50", [], 0, errors=3)
    code, out, _ = run_main(tmp_path, results_dir, root, make_models_file(tmp_path, "broken"), "--no-figures")
    row = next(r for r in json.loads((out / "summary.json").read_text())["rows"] if r["key"] == "broken")
    assert code == 0 and row["n"] == 0 and row["pass_rate"] is None and row["api_errors"] == 3


# ---- figures ----------------------------------------------------------------------


def test_figures_render_beam_only(results_dir, tmp_path):
    pytest.importorskip("matplotlib")
    code, _, figs = run_main(tmp_path, results_dir, tmp_path / "none", tmp_path / "none.json")
    assert code == 0
    for stem in ("models_leaderboard", "models_score_vs_tokens"):
        for ext in ("png", "svg", "json"):
            assert (figs / f"{stem}.{ext}").stat().st_size > 0
    assert (figs / "models_leaderboard.png").read_bytes()[:4] == b"\x89PNG"


@pytest.mark.parametrize("n_other, drawn", [(1, False), (2, True)])
def test_figures_with_other_models_draw_pareto_only_from_three(results_dir, tmp_path, n_other, drawn):
    pytest.importorskip("matplotlib")
    root = tmp_path / "m"
    slugs = [f"m{i}" for i in range(n_other)]
    for i, slug in enumerate(slugs):
        for suite in ("tierb50", "v3base"):
            add_model(root, slug, suite, [1, 0.5, 0.5, 0], 600 * (i + 1))
    code, _, figs = run_main(tmp_path, results_dir, root, make_models_file(tmp_path, *slugs))
    assert code == 0
    board = json.loads((figs / "models_leaderboard.json").read_text())
    scatter = json.loads((figs / "models_score_vs_tokens.json").read_text())
    labels = {r["label"] for r in board["panels"]["tierb50"]}
    assert {"Beam (high)", "Label m0"} <= labels
    assert scatter["panels"]["tierb50"]["pareto"]["drawn"] is drawn
    assert "Beam-1.0-501B-A23B" in scatter["footer"]


def test_suites_option_limits_report_summary_and_figures(results_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(am, "FIGURE_SUITES", am.FIGURE_SUITES)  # main() rebinds it; restore after the test
    root = tmp_path / "m"
    for suite in ("tierb50", "v3base", "probe5", "probe20"):
        add_model(root, "m0", suite, [1, 0.5, 0.5, 0], 600)
    code, out, figs = run_main(tmp_path, results_dir, root, make_models_file(tmp_path, "m0"), "--suites", "tierb50", "probe5", "--no-figures")
    assert code == 0
    summary = json.loads((out / "summary.json").read_text())
    assert {r["suite"] for r in summary["rows"]} == {"tierb50", "probe5"}
    assert set(summary["pareto"]) <= {"tierb50"}
    report = (out / "report.md").read_text()
    assert "`tierb50`" in report and "`probe5`" in report
    assert "`v3base`" not in report and "`probe20`" not in report
    assert am.FIGURE_SUITES == ("tierb50",)
