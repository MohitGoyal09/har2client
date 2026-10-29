"""scripts/analyze_tierb_effort.py on synthetic data (no model runs, no network, no grading)."""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import analyze_tierb_effort as ate  # noqa: E402

N_BOOT = 300  # enough for the planted effects below; the real run uses 10,000
FIELDS = ["task", "effort", "har_mode", "run", "status", "pass_rate", "strict_pass_rate", "prompt_tokens", "completion_tokens"]


def row(task, effort, pass_rate, tokens=1000.0, status="ok", strict="", run="1"):
    return {"task": task, "effort": effort, "har_mode": "raw", "run": run, "status": status, "pass_rate": pass_rate,
            "strict_pass_rate": strict, "prompt_tokens": 40000, "completion_tokens": tokens}


def write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def make_tasks(root: Path, n: int, hidden_every: int = 2) -> Path:
    """n tasks t00..; every `hidden_every`-th task has hidden_header on; auth alternates."""
    for i in range(n):
        d = root / f"t{i:02d}"
        d.mkdir(parents=True)
        config = {"auth": "bearer_login" if i % 2 else "apikey_header", "pagination": "none", "token_expiry": "off",
                  "etag_update": "off", "hidden_header": "on" if i % hidden_every == 0 else "off", "envelope": "off"}
        (d / "task.json").write_text(json.dumps({"domain": "invoices", "config": config}), encoding="utf-8")
    return root


def effort_rows(effort, passes, tokens=1000.0, offset=0):
    return [row(f"t{i + offset:02d}", effort, p, tokens) for i, p in enumerate(passes)]


def step_of(effort, passes, tokens=1000.0, expected=None, offset=0, extra=()):
    rows = [*effort_rows(effort, passes, tokens, offset), *extra]
    return ate.build_steps(rows, expected if expected is not None else len(passes))[effort]


def analysis(*steps):
    return ate.Analysis({s.effort: s for s in steps}, n_boot=N_BOOT, seed=1)


# ------------------------------------------------------------ bootstrap CI


def test_ci_brackets_the_mean_and_is_deterministic():
    values = [0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.5, 0.0, 1.0, 1.0]
    first = ate.boot_ci(values, N_BOOT, 7, "k")
    again = ate.boot_ci(values, N_BOOT, 7, "k")
    assert first == again
    assert first.mean == pytest.approx(0.65)
    assert first.lo < first.mean < first.hi
    assert first.n == 10


def test_ci_collapses_when_every_task_scores_the_same():
    stat = ate.boot_ci([0.5] * 8, N_BOOT, 1, "k")
    assert (stat.lo, stat.mean, stat.hi) == (0.5, 0.5, 0.5)


def test_ci_of_nothing_is_none():
    assert ate.boot_ci([], N_BOOT, 1, "k") is None


def test_ci_is_wider_with_fewer_tasks():
    spread = [0.0, 1.0] * 20
    wide = ate.boot_ci(spread[:6], N_BOOT, 1, "k")
    narrow = ate.boot_ci(spread, N_BOOT, 1, "k")
    assert (wide.hi - wide.lo) > (narrow.hi - narrow.lo)


# ------------------------------------------------------------ steps


def test_step_metrics_per_task():
    rows = [row("a", "low", 1.0, 100, strict=1.0), row("b", "low", 0.5, 300, strict=0.25), row("c", "low", 0.0, 200, strict=0.0)]
    step = ate.build_steps(rows, expected=3)["low"]
    summary = ate.step_summary(step, N_BOOT, 1)
    assert summary["pass_rate"].mean == pytest.approx(0.5)
    assert summary["strict_pass_rate"].mean == pytest.approx(1.25 / 3)
    assert summary["full_correct_share"].mean == pytest.approx(1 / 3)
    assert summary["mean_output_tokens"].mean == pytest.approx(200.0)
    assert step.complete and step.api_errors == 0


def test_api_error_rows_are_excluded_and_counted():
    rows = [row("a", "low", 1.0), row("b", "low", "", status="api_error"), row("c", "low", 0.0)]
    step = ate.build_steps(rows, expected=3)["low"]
    assert sorted(step.scores) == ["a", "c"]
    assert step.api_errors == 1
    assert step.finished == 3 and step.complete  # the failed call was tried; it is not a model failure
    assert ate.step_summary(step, N_BOOT, 1)["pass_rate"].mean == pytest.approx(0.5)  # not counted as 0 for b


def test_other_failed_status_counts_as_zero():
    rows = [row("a", "low", 1.0), row("b", "low", 0.7, status="no_code")]
    step = ate.build_steps(rows, expected=2)["low"]
    assert step.scores["b"].pass_rate == 0.0


def test_runs_of_one_task_are_averaged():
    rows = [row("a", "low", 1.0, 100, run="1"), row("a", "low", 0.0, 300, run="2")]
    score = ate.build_steps(rows, expected=1)["low"].scores["a"]
    assert (score.pass_rate, score.full, score.tokens) == (0.5, 0.5, 200.0)


def test_strict_comes_from_the_regrade_json_when_the_csv_has_no_value(tmp_path):
    csv_path = write_csv(tmp_path / "r.csv", [row("a", "low", 1.0), row("a", "high", 0.5)])
    regrade = tmp_path / "regrade.json"
    regrade.write_text(json.dumps([{"task": "a", "cell": "low-raw-r1", "pass_rate": 1.0, "strict_pass_rate": 0.5},
                                   {"task": "a", "cell": "high-raw-r1", "pass_rate": 0.5, "strict_pass_rate": 0.25}]), encoding="utf-8")
    strict = ate.load_strict_map([regrade])
    steps = ate.build_steps(ate.load_rows([csv_path], "raw"), expected=1, strict_map=strict)
    assert steps["low"].scores["a"].strict == 0.5
    assert steps["high"].scores["a"].strict == 0.25


def test_strict_is_none_without_any_source():
    step = ate.build_steps([row("a", "low", 1.0)], expected=1)["low"]
    assert step.scores["a"].strict is None
    assert ate.step_summary(step, N_BOOT, 1)["strict_pass_rate"] is None


def test_load_rows_refuses_mixed_har_modes(tmp_path):
    rows = [row("a", "low", 1.0), {**row("b", "low", 1.0), "har_mode": "compact"}]
    with pytest.raises(SystemExit):
        ate.load_rows([write_csv(tmp_path / "r.csv", rows)], None)
    assert len(ate.load_rows([write_csv(tmp_path / "r.csv", rows)], "raw")) == 1


# ------------------------------------------------------------ paired contrasts


def test_contrast_pairs_by_task_id_not_by_row_order():
    low = ate.build_steps(list(reversed(effort_rows("low", [0.0, 0.0, 1.0, 0.0]))), 4)["low"]
    high = ate.build_steps(effort_rows("high", [1.0, 1.0, 1.0, 0.0]), 4)["high"]
    c = ate.paired_contrast(high, low, N_BOOT, 1, "high-low")
    assert c.pass_diff.n == 4
    assert c.pass_diff.mean == pytest.approx(50.0)  # (+1 +1 0 0) / 4, in points


def test_contrast_uses_only_tasks_both_steps_have():
    low = step_of("low", [0.0, 0.0, 0.0, 0.0])  # t00..t03
    high = step_of("high", [1.0, 1.0, 1.0, 1.0], offset=1)  # t01..t04
    c = ate.paired_contrast(high, low, N_BOOT, 1, "high-low")
    assert c.pass_diff.n == 3
    assert c.pass_diff.mean == pytest.approx(100.0)


def test_contrast_reports_output_token_ratio_on_paired_tasks():
    low = step_of("low", [0.5, 0.5], tokens=1000.0)
    high = step_of("high", [0.5, 0.5], tokens=2500.0)
    assert ate.paired_contrast(high, low, N_BOOT, 1, "k").token_ratio == pytest.approx(2.5)


def test_contrast_ci_excludes_zero_for_a_planted_gain():
    low = step_of("low", [0.0] * 20)
    high = step_of("high", [0.0, 1.0] * 10)
    c = ate.paired_contrast(high, low, N_BOOT, 1, "k")
    assert c.pass_diff.lo > 0


def test_contrast_of_disjoint_steps_is_none():
    assert ate.paired_contrast(step_of("high", [1.0]), step_of("low", [1.0], offset=5), N_BOOT, 1, "k") is None


def test_analysis_contrasts_follow_the_three_effort_pairs():
    a = analysis(step_of("low", [0.0] * 4), step_of("high", [1.0] * 4))
    got = a.contrasts()
    assert list(got) == ["medium-low", "high-medium", "high-low"]
    assert got["high-low"] is not None
    assert got["medium-low"] is None and got["high-medium"] is None


# ------------------------------------------------------------ verdict


def test_the_curve_has_three_effort_levels():
    assert ate.EFFORTS == ("low", "medium", "high")


def test_verdict_is_not_tested_whatever_data_exist():
    for steps in ([], [step_of("low", [1.0]), step_of("high", [1.0])], [step_of("high", [1.0] * 4), step_of("medium", [0.0] * 4)]):
        v = ate.effort_verdict(analysis(*steps))
        assert v["status"] == ate.NOT_TESTED
        assert "xhigh dropped 2026-10-27" in v["evidence"] and "PREREG.md deviations" in v["evidence"]
        assert v["missing"] == []


def test_a_partial_step_is_never_used():
    a = analysis(step_of("low", [1.0, 0.0], expected=4), step_of("high", [1.0] * 4))
    assert a.step("low") is None
    assert a.contrasts()["high-low"] is None
    assert a.state("low").startswith("partial") and "not used" in a.state("low")


# ------------------------------------------------------------ switches


def test_switch_table_groups_pass_rate_by_level_and_effort():
    feats = {f"t{i:02d}": {"domain": "invoices", "config": {"auth": "a" if i < 2 else "b", "hidden_header": "on"}} for i in range(4)}
    a = analysis(step_of("low", [1.0, 0.0, 0.0, 0.0]), step_of("high", [1.0, 1.0, 1.0, 0.0]))
    table = ate.switch_table(a, feats)
    cell = {(r["switch"], r["level"]): r["by_effort"] for r in table}
    assert cell[("auth", "a")]["low"] == {"n": 2, "pass_rate": 0.5}
    assert cell[("auth", "b")]["high"] == {"n": 2, "pass_rate": 0.5}
    assert cell[("hidden_header", "on")]["low"]["n"] == 4
    assert "medium" not in cell[("auth", "a")]  # step not run


def test_switch_order_is_fixed_and_unknown_switches_follow():
    feats = {"t00": {"domain": "d", "config": {"zeta": "1", "auth": "x", "error_detail": "terse", "envelope": "on"}}}
    a = analysis(step_of("low", [1.0]))
    assert [r["switch"] for r in ate.switch_table(a, feats)] == ["auth", "envelope", "error_detail", "zeta"]


# ------------------------------------------------------------ end to end


def run_main(tmp_path, results, batch=None, tier_a=None, tasks=None, extra=(), highs=()):
    out = tmp_path / "out"
    args = ["--results", *[str(r) for r in results], "--high-runs", *[str(h) for h in highs], "--tasks", str(tasks or make_tasks(tmp_path / "tasks", 6)),
            "--batch-root", str(batch or tmp_path / "nobatch"), "--strict-json", str(tmp_path / "none.json"),
            "--tier-a", str(tier_a or tmp_path / "no_tier_a.csv"), "--out", str(out),
            "--figure", str(tmp_path / "fig" / "frontier"), "--n-boot", str(N_BOOT), *extra]
    assert ate.main(args) == 0
    return out


def low_high_csv(tmp_path):
    return write_csv(tmp_path / "v1.csv", [*effort_rows("low", [1, 0, 0, 1, 0, 0], 1000.0), *effort_rows("high", [1, 1, 0, 1, 1, 0], 4000.0)])


def test_main_with_only_low_and_high_marks_the_rest_not_run_yet(tmp_path):
    out = run_main(tmp_path, [low_high_csv(tmp_path)])
    summary = json.loads((out / "summary.json").read_text())
    assert summary["steps"]["low"]["state"] == "complete"
    assert summary["steps"]["medium"]["state"] == "not run yet"
    assert "xhigh" not in summary["steps"]
    assert summary["steps"]["low"]["pass_rate"]["mean"] == pytest.approx(2 / 6)
    assert summary["steps"]["high"]["mean_output_tokens"]["mean"] == pytest.approx(4000.0)
    assert summary["contrasts"]["high-low"]["pass_diff"]["mean"] == pytest.approx(100 * 2 / 6)
    assert summary["contrasts"]["medium-low"] is None
    assert summary["verdict"]["status"] == ate.NOT_TESTED
    report = (out / "report.md").read_text()
    assert "not run yet" in report and "not tested" in report and "## Reading rules" in report


def test_main_adds_medium_from_the_batch_root_and_ignores_an_xhigh_step(tmp_path):
    batch = tmp_path / "batch"
    for effort, p, tok in (("medium", [1, 0, 0, 1, 1, 0], 2000.0), ("xhigh", [1, 1, 0, 1, 1, 0], 9000.0)):
        (batch / f"tierb50_{effort}").mkdir(parents=True)
        write_csv(batch / f"tierb50_{effort}" / "results.csv", effort_rows(effort, p, tok))
    out = run_main(tmp_path, [low_high_csv(tmp_path)], batch=batch)
    summary = json.loads((out / "summary.json").read_text())
    assert {e: s["state"] for e, s in summary["steps"].items()} == {e: "complete" for e in ("low", "medium", "high")}
    assert summary["verdict"]["status"] == ate.NOT_TESTED
    assert summary["contrasts"]["high-medium"]["pass_diff"]["n"] == 6 and "xhigh-high" not in summary["contrasts"]
    points = json.loads((tmp_path / "fig" / "frontier.json").read_text())["points"]
    assert [p["effort"] for p in points] == ["low", "medium", "high"]
    for name in ("report.md", "summary.json"):
        lines = (out / name).read_text().splitlines()
        assert [ln for ln in lines if "xhigh" in ln and "dropped" not in ln] == []


def test_main_prefers_the_results_file_over_the_batch_root_for_the_same_effort(tmp_path):
    batch = tmp_path / "batch"
    (batch / "tierb50_high").mkdir(parents=True)
    write_csv(batch / "tierb50_high" / "results.csv", effort_rows("high", [0.0] * 6))
    out = run_main(tmp_path, [low_high_csv(tmp_path)], batch=batch)
    assert json.loads((out / "summary.json").read_text())["steps"]["high"]["pass_rate"]["mean"] == pytest.approx(4 / 6)


def test_main_reports_api_errors_per_effort(tmp_path):
    rows = [*effort_rows("low", [1, 0, 0, 1, 0], 1000.0), row("t05", "low", "", status="api_error"), *effort_rows("high", [1] * 6)]
    out = run_main(tmp_path, [write_csv(tmp_path / "v1.csv", rows)])
    summary = json.loads((out / "summary.json").read_text())
    assert summary["steps"]["low"]["api_error_rows"] == 1 and summary["steps"]["low"]["n_scored"] == 5
    assert "api_error" in (out / "report.md").read_text()


def test_main_shows_tier_a_as_a_baseline_row_and_not_on_the_chart(tmp_path):
    tier_a = write_csv(tmp_path / "a.csv", [row("x", "low", 0.9, 800.0), row("y", "low", 1.0, 900.0), row("x", "high", 1.0, 2000.0), row("y", "high", 1.0, 3000.0)])
    out = run_main(tmp_path, [low_high_csv(tmp_path)], tier_a=tier_a)
    summary = json.loads((out / "summary.json").read_text())
    assert summary["tier_a"]["low"]["pass_rate"]["mean"] == pytest.approx(0.95)
    assert summary["tier_a"]["high"]["n_scored"] == 2
    report = (out / "report.md").read_text()
    assert "Tier A" in report and "saturated baseline" in report
    assert {p["series"] for p in json.loads((tmp_path / "fig" / "frontier.json").read_text())["points"]} == {"Beam"}


def test_main_writes_the_chart_files(tmp_path):
    pytest.importorskip("matplotlib")
    run_main(tmp_path, [low_high_csv(tmp_path)])
    for ext in ("png", "svg", "json"):
        assert (tmp_path / "fig" / f"frontier.{ext}").is_file()


def test_main_fails_cleanly_when_no_results_exist(tmp_path):
    args = ["--results", str(tmp_path / "missing.csv"), "--high-runs", "--tasks", str(make_tasks(tmp_path / "tasks", 2)), "--batch-root", str(tmp_path / "nb"),
            "--out", str(tmp_path / "out"), "--figure", str(tmp_path / "f")]
    with pytest.raises(SystemExit):
        ate.main(args)


# ------------------------------------------------------------ high effort: per-task mean over 3 runs


def repeat_csv(tmp_path, name, passes, tokens=4000.0):
    return write_csv(tmp_path / name, effort_rows("high", passes, tokens))


def test_high_is_the_per_task_mean_of_the_repeat_runs_and_low_stays_single_run(tmp_path):
    r2 = repeat_csv(tmp_path, "r2.csv", [0, 1, 0, 1, 0, 0], 6000.0)
    r3 = repeat_csv(tmp_path, "r3.csv", [0, 0, 0, 1, 1, 0], 5000.0)
    out = run_main(tmp_path, [low_high_csv(tmp_path)], highs=[r2, r3])
    summary = json.loads((out / "summary.json").read_text())
    high = summary["steps"]["high"]
    assert high["pass_rate"]["mean"] == pytest.approx((4 + 2 + 2) / 18)  # run 1 [1,1,0,1,1,0], r2 [0,1,0,1,0,0], r3 [0,0,0,1,1,0]
    assert high["mean_output_tokens"]["mean"] == pytest.approx(5000.0)
    assert high["full_correct_share"]["mean"] == pytest.approx(8 / 18)
    assert summary["steps"]["low"]["pass_rate"]["mean"] == pytest.approx(2 / 6)
    assert summary["contrasts"]["high-low"]["pass_diff"]["mean"] == pytest.approx(100 * (8 / 18 - 2 / 6))
    report = (out / "report.md").read_text()
    assert "high (mean of 3 runs)" in report and "low (mean" not in report and "r2.csv" in report


def test_a_repeat_that_misses_a_task_raises_a_clear_error(tmp_path):
    r2 = write_csv(tmp_path / "r2.csv", effort_rows("high", [0, 1, 0, 1, 0], 6000.0))  # t05 missing
    with pytest.raises(ate.high_runs.RunMismatchError, match=r"r2\.csv.*run 2: missing 1 task.*t05"):
        run_main(tmp_path, [low_high_csv(tmp_path)], highs=[r2])


def test_a_missing_repeat_file_is_noted_and_the_runs_that_exist_are_used(tmp_path):
    r2 = repeat_csv(tmp_path, "r2.csv", [0, 1, 0, 1, 0, 0])
    out = run_main(tmp_path, [low_high_csv(tmp_path)], highs=[r2, tmp_path / "r3.csv"])
    summary = json.loads((out / "summary.json").read_text())
    assert summary["steps"]["high"]["pass_rate"]["mean"] == pytest.approx((4 + 2) / 12)
    assert "mean of 2 runs" in (out / "report.md").read_text() and "r3.csv" in (out / "report.md").read_text()


def test_repeat_rows_pair_with_run_one_by_task_id_not_by_row_order(tmp_path):
    shuffled = effort_rows("high", [0, 0, 1, 1, 1, 1])[::-1]  # t05 .. t00
    r2 = write_csv(tmp_path / "r2.csv", shuffled)
    rows = ate.merge_high_runs(ate.load_rows([low_high_csv(tmp_path)], "raw"), [r2], "raw", [])
    steps = ate.build_steps(rows, 6)
    assert steps["high"].runs == 2 and steps["high"].scores["t02"].pass_rate == pytest.approx(0.5)  # run 1: 0, r2: 1
    assert steps["low"].runs == 1
