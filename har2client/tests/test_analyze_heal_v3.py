"""scripts/analyze_heal_v3.py and scripts/heal_v3_verdicts.py on synthetic data (no model runs, no network, no grading)."""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import analyze_heal_v3 as ah  # noqa: E402
import heal_v3_verdicts as hv  # noqa: E402
import high_runs  # noqa: E402

N_BOOT = 400  # enough for the planted effects below; the real run uses 10,000
FAMILIES = ("single", "release3", "release4", "partial", "sdk", "misleading", "terse")


def spec(**families):
    """family -> list of pass rates (one task each); task ids are family + index, so arms and efforts pair up."""
    return families


def make_step(arm, effort, passes, tokens=1000.0, expected=None, api_errors=0, credit=None):
    rows = []
    for family, values in passes.items():
        for i, p in enumerate(values):
            rows.append({"task": f"{family}{i}", "set": family, "status": "ok", "pass_rate": p, "strict_pass_rate": p,
                         "completion_tokens": tokens, "change_credit": json.dumps(credit or {})})
    n = len(rows)
    for k in range(api_errors):
        rows.append({"task": f"err{k}", "set": "single", "status": "api_error", "pass_rate": "", "completion_tokens": ""})
    return ah.build_step(arm, effort, rows, expected if expected is not None else n + api_errors)


def analysis(*steps):
    return hv.Analysis({(s.arm, s.effort): s for s in steps}, n_boot=N_BOOT, seed=1)


def flat(n, p):
    return {"single": [p] * n}


def shifted(values, delta):
    return [min(1.0, max(0.0, v + delta)) for v in values]


BASE = [0.0, 0.2, 0.4, 0.1, 0.3, 0.0, 0.5, 0.2, 0.1, 0.3] * 2  # 20 tasks, mean 0.2 (+ noise-free ties)


# ------------------------------------------------------------ per-change credit


def test_credit_by_change_is_the_mean_per_task_credit_over_tasks_holding_the_change():
    rows = [
        {"task": "t1", "set": "release3", "status": "ok", "pass_rate": 0.5, "change_credit": json.dumps({"a": 1.0, "b": 0.0})},
        {"task": "t2", "set": "release3", "status": "ok", "pass_rate": 0.5, "change_credit": json.dumps({"a": 0.5})},
        {"task": "t3", "set": "single", "status": "ok", "pass_rate": 0.5, "change_credit": ""},  # a v2-style row: no credit
    ]
    step = ah.build_step("base", "low", rows, 3)
    assert ah.credit_by_change(step) == {"a": {"n_tasks": 2, "credit": 0.75}, "b": {"n_tasks": 1, "credit": 0.0}}


def test_credit_of_a_task_with_several_runs_is_the_mean_over_runs():
    rows = [{"task": "t", "set": "x", "status": "ok", "pass_rate": 1, "change_credit": json.dumps({"a": 1.0})},
            {"task": "t", "set": "x", "status": "ok", "pass_rate": 0, "change_credit": json.dumps({"a": 0.0})}]
    step = ah.build_step("base", "low", rows, 1)
    assert step.scores["t"].credit == {"a": 0.5} and step.scores["t"].pass_rate == 0.5 and step.scores["t"].full == 0.5


# ------------------------------------------------------------ bootstrap


def test_paired_bootstrap_of_constant_differences_has_a_point_interval():
    est = hv.bootstrap_mean([40.0] * 10, 200, 1, "k")
    assert (est.n, est.mean, est.lo, est.hi) == (10, 40.0, 40.0, 40.0)


def test_paired_bootstrap_is_deterministic_and_brackets_the_mean():
    diffs = [10.0, 30.0, 50.0, 20.0, 40.0, 60.0, 0.0, 35.0]
    a, b = hv.bootstrap_mean(diffs, 1000, 7, "k"), hv.bootstrap_mean(diffs, 1000, 7, "k")
    assert a == b and a.mean == sum(diffs) / len(diffs)
    assert a.lo < a.mean < a.hi and 0 <= a.lo and a.hi <= 60
    assert hv.bootstrap_mean(diffs, 1000, 8, "k") != a  # another seed, another draw
    assert hv.bootstrap_mean([], 10, 1, "k") is None


def test_unpaired_bootstrap_difference_of_two_groups():
    est = hv.bootstrap_diff([1.0, 1.0, 1.0], [0.0, 0.0], 200, 1, "k")
    assert est.mean == 1.0 and est.lo == 1.0 and est.hi == 1.0
    assert hv.bootstrap_diff([], [1.0], 10, 1, "k") is None


def test_contrast_is_paired_by_task_in_points():
    lo = make_step("base", "low", spec(single=[0.0, 0.5, 1.0, 0.5]))
    hi = make_step("base", "high", spec(single=[0.5, 0.5, 1.0, 1.0]))
    est = analysis(lo, hi).effort_contrast("base", "high", "low")
    assert est.n == 4 and est.mean == pytest.approx(25.0)


# ------------------------------------------------------------ api_error


def test_api_error_rows_are_counted_and_left_out_of_every_mean():
    step = make_step("base", "low", flat(4, 0.5), api_errors=2)
    assert step.api_errors == 2 and len(step.scores) == 4 and step.finished == 6 and step.complete
    s = ah.step_summary(step)
    assert s["pass_rate"] == 0.5 and s["api_error_rows"] == 2


# ------------------------------------------------------------ H16


def h16_steps(low_delta, high_delta, values=BASE):
    base = {e: make_step("base", e, spec(single=values)) for e in ("low", "high")}
    hint = {"low": make_step("hint", "low", spec(single=shifted(values, low_delta))),
            "high": make_step("hint", "high", spec(single=shifted(values, high_delta)))}
    return analysis(base["low"], base["high"], hint["low"], hint["high"])


def test_h16_supported_when_the_hint_gains_20_points_at_low_and_high_with_ci_above_zero():
    v = hv.verdict_h16(h16_steps(0.4, 0.3, [0.0, 0.2, 0.1, 0.3] * 5))
    assert v["status"] == hv.SUPPORTED and "+40.0" in v["evidence"] and ">= 20 points at low and at high" in v["supported_if"]


def test_h16_rejected_when_both_gains_are_under_5_points():
    assert hv.verdict_h16(h16_steps(0.02, 0.0))["status"] == hv.REJECTED


def test_h16_inconclusive_when_gain_is_between_the_thresholds_or_only_one_effort_clears():
    assert hv.verdict_h16(h16_steps(0.1, 0.1))["status"] == hv.INCONCLUSIVE
    assert hv.verdict_h16(h16_steps(0.4, 0.02, [0.0, 0.2, 0.1, 0.3] * 5))["status"] == hv.INCONCLUSIVE


def test_h16_needs_the_ci_to_exclude_zero_not_only_a_20_point_mean():
    diffs = [1.0, 0.0, -0.1, -0.1]  # mean +20 points, but the CI reaches below 0
    base = [0.0, 0.5, 0.5, 0.5]
    hint = [b + d for b, d in zip(base, diffs)]
    a = analysis(*(make_step(arm, e, spec(single=vals)) for e in ("low", "high") for arm, vals in (("base", base), ("hint", hint))))
    est = a.hint_minus_base("low")
    assert est.mean == pytest.approx(20.0) and est.lo <= 0
    assert hv.verdict_h16(a)["status"] == hv.INCONCLUSIVE


def test_h16_not_yet_testable_names_the_missing_steps():
    a = analysis(make_step("base", "low", flat(4, 0.3)), make_step("hint", "low", flat(4, 0.7)))
    v = hv.verdict_h16(a)
    assert v["status"] == hv.NOT_YET and v["missing"] == ["base-high", "hint-high"]
    assert hv.verdict_h16(analysis())["status"] == hv.NOT_YET


# ------------------------------------------------------------ H17, H18


def effort_pair(arm, lo_p, hi_p, lo_tok, hi_tok, lo="low", hi="high", n=10):
    return [make_step(arm, lo, flat(n, lo_p), tokens=lo_tok), make_step(arm, hi, flat(n, hi_p), tokens=hi_tok)]


def test_h17_supported_rejected_and_inconclusive():
    assert hv.verdict_h17(analysis(*effort_pair("base", 0.30, 0.32, 1000, 4000)))["status"] == hv.SUPPORTED
    assert hv.verdict_h17(analysis(*effort_pair("base", 0.30, 0.32, 1000, 2000)))["status"] == hv.INCONCLUSIVE  # output only 2x
    assert hv.verdict_h17(analysis(*effort_pair("base", 0.30, 0.50, 1000, 4000)))["status"] == hv.REJECTED  # +20 >= 10
    assert hv.verdict_h17(analysis(*effort_pair("base", 0.30, 0.37, 1000, 4000)))["status"] == hv.INCONCLUSIVE  # +7: between
    assert hv.verdict_h17(analysis(make_step("base", "low", flat(4, 0.3))))["status"] == hv.NOT_YET


def test_h18_is_not_tested_for_either_arm_whatever_data_exist():
    a = analysis(*effort_pair("base", 0.50, 0.51, 1000, 2000), *effort_pair("hint", 0.50, 0.70, 1000, 2000))
    for arm in ("base", "hint"):
        v = hv.verdict_h18(a, arm)
        assert v["status"] == hv.NOT_TESTED and v["id"] == f"H18 ({arm})"
        assert "xhigh dropped 2026-10-27" in v["evidence"] and "PREREG.md deviations" in v["evidence"]
    assert hv.verdict_h18(analysis(), "base")["status"] == hv.NOT_TESTED


def test_the_suite_has_three_effort_levels():
    assert hv.EFFORTS == ("low", "medium", "high")


def test_a_step_that_has_not_finished_every_task_is_never_used():
    rows = [{"task": "single0", "set": "single", "status": "ok", "pass_rate": 1.0, "completion_tokens": 10}]
    assert not ah.build_step("base", "high", rows, expected=4).usable
    assert ah.build_step("base", "high", rows, expected=1).usable
    a = analysis(ah.build_step("base", "high", rows, 4), make_step("base", "low", flat(4, 0.3)))
    assert hv.verdict_h17(a)["status"] == hv.NOT_YET and hv.verdict_h17(a)["missing"] == ["base-high"]


# ------------------------------------------------------------ H19, H20, H21


def hint_high(**families):
    return analysis(make_step("hint", "high", families))


def test_h19_supported_when_full_repair_falls_strictly_and_release4_is_15_points_below_single():
    a = hint_high(single=[1.0] * 8 + [0.5] * 2, release3=[1.0] * 5 + [0.5] * 5, release4=[1.0] * 2 + [0.5] * 8)
    v = hv.verdict_h19(a)
    assert v["status"] == hv.SUPPORTED and "single 80.0%" in v["evidence"] and "release4 20.0%" in v["evidence"]


def test_h19_rejected_when_release4_is_not_below_single():
    a = hint_high(single=[1.0] * 5 + [0.5] * 5, release3=[1.0] * 3 + [0.5] * 7, release4=[1.0] * 6 + [0.5] * 4)
    assert hv.verdict_h19(a)["status"] == hv.REJECTED


def test_h19_inconclusive_when_not_strictly_falling_or_gap_under_15():
    not_strict = hint_high(single=[1.0] * 8 + [0.0] * 2, release3=[1.0] * 3 + [0.0] * 7, release4=[1.0] * 5 + [0.0] * 5)
    assert hv.verdict_h19(not_strict)["status"] == hv.INCONCLUSIVE
    small_gap = hint_high(single=[1.0] * 8 + [0.0] * 2, release3=[1.0] * 7 + [0.0] * 3, release4=[1.0] * 7 + [0.0] * 3)
    assert hv.verdict_h19(small_gap)["status"] == hv.INCONCLUSIVE  # equal: not strictly falling and < 15 below single is 10
    assert hv.verdict_h19(analysis())["status"] == hv.NOT_YET
    assert hv.verdict_h19(hint_high(single=[1.0], release3=[1.0]))["status"] == hv.NOT_YET  # no release4 tasks


def test_h20_supported_when_partial_and_misleading_are_10_points_below_release3():
    a = hint_high(release3=[0.8] * 8, partial=[0.6] * 8, misleading=[0.5] * 8, terse=[0.8] * 8)
    v = hv.verdict_h20(a)
    assert v["status"] == hv.SUPPORTED and "terse" in v["evidence"]


def test_h20_rejected_when_both_are_within_3_points_and_inconclusive_otherwise():
    assert hv.verdict_h20(hint_high(release3=[0.8] * 8, partial=[0.81] * 8, misleading=[0.78] * 8, terse=[0.1] * 8))["status"] == hv.REJECTED
    assert hv.verdict_h20(hint_high(release3=[0.8] * 8, partial=[0.6] * 8, misleading=[0.79] * 8, terse=[0.8] * 8))["status"] == hv.INCONCLUSIVE
    assert hv.verdict_h20(hint_high(release3=[0.8] * 8, partial=[0.9] * 8, misleading=[0.5] * 8, terse=[0.8] * 8))["status"] == hv.INCONCLUSIVE


def two_arms_high(base_fams, hint_fams):
    return analysis(make_step("base", "high", base_fams), make_step("hint", "high", hint_fams))


def test_h21_needs_both_arms_and_decides_on_sdk_vs_release3():
    lower = dict(release3=[0.8] * 8, sdk=[0.6] * 8)
    assert hv.verdict_h21(two_arms_high(lower, lower))["status"] == hv.SUPPORTED
    same = dict(release3=[0.5] * 8, sdk=[0.5] * 8)
    assert hv.verdict_h21(two_arms_high(same, dict(release3=[0.5] * 8, sdk=[0.7] * 8)))["status"] == hv.REJECTED
    assert hv.verdict_h21(two_arms_high(lower, dict(release3=[0.5] * 8, sdk=[0.7] * 8)))["status"] == hv.INCONCLUSIVE
    only_hint = analysis(make_step("hint", "high", lower))
    v = hv.verdict_h21(only_hint)
    assert v["status"] == hv.NOT_YET and v["missing"] == ["base-high"]


# ------------------------------------------------------------ saturation


def test_saturation_is_evaluated_on_base_high_only():
    def a_with(bh):
        return analysis(make_step("base", "high", flat(4, bh)))

    assert hv.verdict_saturation(a_with(0.3))["status"] == hv.NOT_TRIGGERED
    assert hv.verdict_saturation(a_with(0.85))["status"] == hv.TRIGGERED
    assert hv.verdict_saturation(a_with(0.84))["status"] == hv.NOT_TRIGGERED
    assert "xhigh" in hv.verdict_saturation(a_with(0.3))["evidence"]  # says the xhigh clauses were dropped


def test_saturation_is_not_yet_testable_while_base_high_is_missing():
    v = hv.verdict_saturation(analysis(make_step("base", "low", flat(4, 0.3))))
    assert v["status"] == hv.NOT_YET and v["missing"] == ["base-high"]


def test_verdicts_quote_the_preregistered_thresholds():
    v = hv.verdict_saturation(analysis())
    assert ">= 0.85" in v["supported_if"] and "base arm" in v["supported_if"]
    assert len(hv.all_verdicts(analysis())) == 8
    statuses = {x["id"]: x["status"] for x in hv.all_verdicts(analysis())}
    assert statuses["H18 (base)"] == statuses["H18 (hint)"] == hv.NOT_TESTED
    assert {s for k, s in statuses.items() if not k.startswith("H18")} == {hv.NOT_YET}


# ------------------------------------------------------------ the script end to end


def write_results(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["task", "effort", "run", "status", "pass_rate", "strict_pass_rate", "completion_tokens", "set", "change_credit"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def fake_score(tasks_dir, runs_dir, results_csv, scorer):
    """Stands in for score_heal: the fixture results.csv already holds the scored columns."""
    with open(results_csv, newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames), list(reader)


def make_root(tmp_path, n=8):
    for arm in ("base", "hint"):
        for i in range(n):
            d = tmp_path / f"tasks_{arm}" / f"t{i}"
            d.mkdir(parents=True)
            (d / "task.json").write_text("{}")
    batch = tmp_path / "batch"
    for arm, p in (("base", 0.25), ("hint", 0.75)):
        rows = [{"task": f"t{i}", "effort": "low", "run": 1, "status": "ok", "pass_rate": p, "strict_pass_rate": p,
                 "completion_tokens": 1000 + i, "set": "single" if i < 4 else "release3", "change_credit": json.dumps({"add_x": p})} for i in range(n)]
        write_results(batch / f"v3eval_{arm}_low" / "results.csv", rows)
    return batch


def run_main(tmp_path, batch, extra=()):
    argv = ["--batch-root", str(batch), "--tasks-base", str(tmp_path / "tasks_base"), "--tasks-hint", str(tmp_path / "tasks_hint"),
            "--out", str(tmp_path / "out"), "--scored-dir", str(tmp_path / "scored"), "--figure", str(tmp_path / "fig" / "frontier"), "--family-figure", str(tmp_path / "fig" / "families"),
            "--n-boot", str(N_BOOT), *extra]
    return ah.main(argv, score_fn=fake_score)


def test_script_scores_present_steps_and_writes_report_summary_csv_and_chart(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    batch = make_root(tmp_path)
    assert run_main(tmp_path, batch) == 0
    report = (tmp_path / "out" / "report.md").read_text()
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert (tmp_path / "scored" / "heal_v3_eval_base_low.csv").is_file() and (tmp_path / "scored" / "heal_v3_eval_hint_low.csv").is_file()
    assert summary["steps"]["base-low"]["pass_rate"] == 0.25 and summary["steps"]["hint-low"]["pass_rate"] == 0.75
    assert summary["families"]["base-low"]["single"]["n"] == 4 and summary["families"]["base-low"]["release3"]["n"] == 4
    assert summary["hint_minus_base"]["low"]["pass"]["n"] == 8 and summary["hint_minus_base"]["low"]["pass"]["mean"] == pytest.approx(50.0)
    assert summary["change_credit"]["base-low"]["add_x"]["n_tasks"] == 8
    assert {v["status"] for v in summary["verdicts"] if not v["id"].startswith("H18")} == {hv.NOT_YET}
    assert "not run yet" in report and "Hint - base" in report and "H16" in report
    fig = tmp_path / "fig"
    assert (fig / "frontier.png").is_file() and (fig / "frontier.svg").is_file()
    points = json.loads((fig / "frontier.json").read_text())["points"]
    assert sorted(p["series"] for p in points) == ["+ contract hint", "base"] and all(p["effort"] == "low" for p in points)
    assert "2 step(s)" in capsys.readouterr().out


def test_script_works_with_a_single_step_and_fails_clearly_with_none(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    batch = make_root(tmp_path)
    (batch / "v3eval_hint_low" / "results.csv").unlink()
    assert run_main(tmp_path, batch) == 0
    report = (tmp_path / "out" / "report.md").read_text()
    assert "Hint - base" in report and "base-low" in report
    assert run_main(tmp_path, tmp_path / "nothing") == 2
    assert "no v3eval" in capsys.readouterr().err


def test_the_chart_series_have_one_point_per_present_effort():
    full = [make_step(arm, e, flat(6, 0.5), tokens=1000.0 * (i + 1)) for arm in hv.ARMS for i, e in enumerate(("low", "high"))]
    points, meta = ah.frontier_points(analysis(*full), 100, 1)
    by = {(p.series, p.effort): p for p in points}
    assert set(by) == {("base", "low"), ("base", "high"), ("+ contract hint", "low"), ("+ contract hint", "high")}
    assert by[("base", "low")].n_tasks == 6
    assert meta["series"]["base"]["n_tasks"] == 6


def test_an_xhigh_step_on_disk_is_ignored_and_never_written(tmp_path):
    batch = make_root(tmp_path)
    write_results(batch / "v3eval_base_xhigh" / "results.csv",
                  [{"task": "t0", "effort": "xhigh", "run": 1, "status": "ok", "pass_rate": 1.0, "strict_pass_rate": 1.0,
                    "completion_tokens": 9000, "set": "single", "change_credit": "{}"}])
    assert run_main(tmp_path, batch) == 0
    assert not list((tmp_path / "scored").glob("*xhigh*"))
    for name in ("report.md", "summary.json"):
        lines = (tmp_path / "out" / name).read_text().splitlines()
        assert [ln for ln in lines if "xhigh" in ln and "dropped" not in ln] == []  # only the explicit "xhigh dropped" notes


# ------------------------------------------------------------ high effort: per-task mean over 3 runs


def add_high(batch, arm, run_passes, n=8, suffix=""):
    """One high-effort results.csv per run: `run_passes` = pass rate of every task in that run; suffix '' (run 1), '_r2', '_r3'."""
    rows = [{"task": f"t{i}", "effort": "high", "run": 1, "status": "ok", "pass_rate": run_passes[i % len(run_passes)] if isinstance(run_passes, list) else run_passes,
             "strict_pass_rate": 0.0, "completion_tokens": 4000, "set": "single" if i < 4 else "release3", "change_credit": "{}"} for i in range(n)]
    write_results(batch / f"v3eval_{arm}_high{suffix}" / "results.csv", rows)


def test_the_high_step_is_the_per_task_mean_of_three_runs_and_the_repeats_are_written(tmp_path):
    pytest.importorskip("matplotlib")
    batch = make_root(tmp_path)
    for arm in ("base", "hint"):
        add_high(batch, arm, 0.0)
        add_high(batch, arm, 0.5, suffix="_r2")
        add_high(batch, arm, 1.0, suffix="_r3")
    assert run_main(tmp_path, batch) == 0
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    high = summary["steps"]["base-high"]
    assert high["pass_rate"] == pytest.approx(0.5) and high["runs_per_task"] == 3 and high["n_scored"] == 8
    assert high["full_repair_share"] == pytest.approx(1 / 3)  # one of three runs is a full repair on every task
    assert summary["steps"]["base-low"]["runs_per_task"] == 1
    assert (tmp_path / "scored" / "heal_v3_eval_base_high_r2.csv").is_file() and (tmp_path / "scored" / "heal_v3_eval_hint_high_r3.csv").is_file()
    assert summary["effort_contrasts"]["base: high - low"]["pass"]["n"] == 8
    report = (tmp_path / "out" / "report.md").read_text()
    assert "high (mean of 3 runs)" in report and "low (mean" not in report
    assert summary["families"]["base-high"]["single"]["n"] == 4


def test_a_repeat_run_that_misses_a_task_stops_the_analysis_with_a_clear_error(tmp_path):
    batch = make_root(tmp_path)
    for arm in ("base", "hint"):
        add_high(batch, arm, 0.5)
    add_high(batch, "base", 0.5, n=7, suffix="_r2")  # t7 is missing in r2
    with pytest.raises(high_runs.RunMismatchError, match=r"run 2: missing 1 task.*t7"):
        run_main(tmp_path, batch)


def test_without_repeat_files_high_stays_a_single_run(tmp_path):
    batch = make_root(tmp_path)
    for arm in ("base", "hint"):
        add_high(batch, arm, 0.5)
    run_main(tmp_path, batch)
    assert json.loads((tmp_path / "out" / "summary.json").read_text())["steps"]["base-high"]["runs_per_task"] == 1
    assert not list((tmp_path / "scored").glob("*_r2.csv"))


def three_run_step(arm, effort, passes):
    """A step whose tasks hold 3 runs each (per-run pass rates `passes[task_index]`)."""
    rows = [{"task": f"single{i}", "set": "single", "status": "ok", "run": str(k + 1), "pass_rate": p, "strict_pass_rate": p, "completion_tokens": 1000.0 * (k + 1)}
            for i, ps in enumerate(passes) for k, p in enumerate(ps)]
    return ah.build_step(arm, effort, rows, len(passes))


def test_a_task_with_three_runs_scores_the_mean_of_pass_rate_tokens_and_full_repair():
    step = three_run_step("base", "high", [[1.0, 0.5, 0.0]])
    score = step.scores["single0"]
    assert step.runs == 3 and score.pass_rate == pytest.approx(0.5) and score.full == pytest.approx(1 / 3) and score.tokens == pytest.approx(2000.0)


def test_verdict_text_names_the_runs_behind_high():
    a = analysis(make_step("base", "low", flat(6, 0.2)), make_step("hint", "low", flat(6, 0.8)),
                 three_run_step("base", "high", [[0.2, 0.3, 0.1]] * 6), three_run_step("hint", "high", [[0.8, 0.9, 0.7]] * 6))
    assert "high (mean of 3 runs): hint - base" in hv.verdict_h16(a)["evidence"] and "low: hint - base" in hv.verdict_h16(a)["evidence"]
    assert "high (mean of 3 runs) - low" in hv.verdict_h17(a)["evidence"]
    assert "base-high (mean of 3 runs)" in hv.verdict_saturation(a)["evidence"]


def test_h16_and_h17_use_the_per_task_mean_at_high_not_run_one():
    # run 1 alone would show a +60 point hint gain; the 3-run per-task means show +13.3.
    base_high = three_run_step("base", "high", [[0.0, 0.5, 0.5]] * 10)
    hint_high = three_run_step("hint", "high", [[0.6, 0.6, 0.2]] * 10)
    a = analysis(make_step("base", "low", flat(10, 0.2)), make_step("hint", "low", flat(10, 0.8)), base_high, hint_high)
    est = a.hint_minus_base("high")
    assert est.mean == pytest.approx(100 * (1.4 / 3 - 1.0 / 3))
    assert a.effort_contrast("base", "high", "low").mean == pytest.approx((1.0 / 3 - 0.2) * 100)
