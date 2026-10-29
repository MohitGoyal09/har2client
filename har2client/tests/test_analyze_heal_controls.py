"""scripts/analyze_heal_controls.py on synthetic data (tmp dirs and CSVs; no model runs, no network, no grading)."""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import analyze_heal_controls as ac  # noqa: E402

N = 20
TASKS = [f"t{i:02d}" for i in range(N)]
BASE = [0.0, 0.2] * (N // 2)  # base pass rate per task; arms below add a constant, so paired CIs are tight


def write_step(root: Path, dirname: str, rates: dict, grades: dict | None = None, errors: tuple = ()) -> None:
    d = root / dirname
    d.mkdir(parents=True)
    rows = [{"task": t, "set": "single", "effort": "low", "har_mode": "raw", "run": 1, "status": "ok", "pass_rate": p} for t, p in rates.items()]
    rows += [{"task": t, "set": "single", "effort": "low", "har_mode": "raw", "run": 1, "status": "api_error", "pass_rate": ""} for t in errors]
    with open(d / "results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for task, tests in (grades or {}).items():
        cell = d / task / "low-raw-r1"
        cell.mkdir(parents=True)
        results = [{"index": i, "method": m, "passed": ok} for i, (m, ok) in enumerate(tests)]
        (cell / "grade.json").write_text(json.dumps({"results": results}))


def arm(delta: float, tasks=TASKS) -> dict:
    return {t: round(min(1.0, BASE[TASKS.index(t)] + delta), 6) for t in tasks}


@pytest.fixture
def env(tmp_path):
    tasks3, tierb = tmp_path / "case3", tmp_path / "tierb"
    for t in TASKS:
        (tasks3 / t).mkdir(parents=True)
        (tasks3 / t / "task.json").write_text("{}")
    for i, on in enumerate(["on", "on", "off", "off"]):
        (tierb / f"b{i}").mkdir(parents=True)
        (tierb / f"b{i}" / "task.json").write_text(json.dumps({"config": {"hidden_header": on}}))
    return {"root": tmp_path / "batch", "tasks3": tasks3, "tierb": tierb, "tmp": tmp_path}


def run(env, v1=None, medium=None) -> dict:
    def csv_of(name, rows):
        p = env["tmp"] / name
        if rows:
            with open(p, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=["task", "effort", "run", "status", "pass_rate"])
                w.writeheader()
                w.writerows(rows)
        return p

    out = env["tmp"] / "out"
    argv = ["--batch-root", str(env["root"]), "--tasks-case3", str(env["tasks3"]), "--tasks-tierb", str(env["tierb"]),
            "--tierb-v1", str(csv_of("v1.csv", v1)), "--tierb-medium", str(csv_of("med.csv", medium)), "--results-dir", str(env["tmp"] / "results"), "--out", str(out), "--n-boot", "200"]
    assert ac.main(argv) == 0
    summary = json.loads((out / "summary.json").read_text())
    summary["report"] = (out / "report.md").read_text()
    return summary


def verdict(summary: dict, hid: str) -> dict:
    return next(v for v in summary["verdicts"] if v["id"] == hid)


def tb(task, effort, p, status="ok"):
    return {"task": task, "effort": effort, "run": 1, "status": status, "pass_rate": p}


# ------------------------------------------------------------ case 3 verdicts


@pytest.mark.parametrize("placebo,hint,expected", [
    (0.0, 0.6, "supported"),   # placebo - base = 0, hint - placebo = 60
    (0.3, 0.9, "rejected"),    # placebo - base = 30
    (0.1, 0.8, "inconclusive"),  # placebo - base = 10: neither < 5 nor >= 20
    (0.0, 0.1, "inconclusive"),  # hint - placebo = 10 < 20
])
def test_h22_rules(env, placebo, hint, expected):
    write_step(env["root"], "v3eval_base_low", arm(0.0))
    write_step(env["root"], "v3eval_placebo_low", arm(placebo))
    write_step(env["root"], "v3eval_hint_low", arm(hint))
    assert verdict(run(env), "H22")["status"] == expected


@pytest.mark.parametrize("notice,expected", [(0.5, "supported"), (0.0, "rejected"), (0.1, "inconclusive")])
def test_h23_rules(env, notice, expected):
    write_step(env["root"], "v3eval_base_low", arm(0.0))
    write_step(env["root"], "v3eval_notice_low", arm(notice))
    assert verdict(run(env), "H23")["status"] == expected


def test_h23_not_rejected_when_the_ci_upper_end_is_10_or_more(env):
    # mean diff 0 but noisy: half the tasks +40, half -40 -> CI upper end >= 10, so not rejected
    base = {t: 0.5 for t in TASKS}
    write_step(env["root"], "v3eval_base_low", base)
    write_step(env["root"], "v3eval_notice_low", {t: 0.9 if i % 2 else 0.1 for i, t in enumerate(TASKS)})
    assert verdict(run(env), "H23")["status"] == "inconclusive"


def test_contrasts_are_paired_by_task_in_points(env):
    write_step(env["root"], "v3eval_base_low", arm(0.0))
    write_step(env["root"], "v3eval_notice_low", arm(0.5))
    c = next(c for c in run(env)["contrasts"] if c["contrast"] == "notice - base")
    assert c["pass"]["n"] == N
    assert c["pass"]["mean"] == pytest.approx(50.0 - 0.0, abs=6)  # a +0.5 step, capped at 1.0 for the 0.6 base tasks
    assert c["pass"]["ci_low"] <= c["pass"]["mean"] <= c["pass"]["ci_high"]


def test_missing_steps_are_not_run_yet_and_verdicts_not_testable(env):
    write_step(env["root"], "v3eval_base_low", arm(0.0))
    s = run(env)
    assert s["steps"]["placebo"]["state"] == "not run yet"
    assert verdict(s, "H22")["status"] == "not yet testable"
    assert verdict(s, "H23")["status"] == "not yet testable"
    assert verdict(s, "H10")["status"] == "not yet testable"
    assert "not run yet" in s["report"]


def test_a_partial_step_is_shown_but_not_used(env):
    write_step(env["root"], "v3eval_base_low", arm(0.0))
    write_step(env["root"], "v3eval_notice_low", arm(0.5, TASKS[:10]))
    s = run(env)
    assert s["steps"]["notice"]["state"] == "partial (not used)"
    assert verdict(s, "H23")["status"] == "not yet testable"


def test_api_error_rows_are_left_out_and_counted(env):
    write_step(env["root"], "v3eval_base_low", arm(0.0, TASKS[:-1]), errors=(TASKS[-1],))
    s = run(env)
    assert s["steps"]["base"]["api_error_rows"] == 1
    assert s["api_error_rows_total"] == 1
    assert s["steps"]["base"]["pass_rate"] == pytest.approx(sum(arm(0.0, TASKS[:-1]).values()) / (N - 1))


# ------------------------------------------------------------ repeat runs


def test_repeat_run_statistics(env):
    r1 = {t: 1.0 if i < 10 else 0.0 for i, t in enumerate(TASKS)}   # 10 fully repaired
    r2 = {t: 1.0 if 5 <= i < 15 else 0.0 for i, t in enumerate(TASKS)}  # tasks 5..14 fully repaired
    write_step(env["root"], "v3eval_base_low", r1)
    write_step(env["root"], "v3eval_base_low_r2", r2)
    runs = run(env)["repeat_runs"]
    r = runs["base"]
    assert runs["hint"] is None  # hint r1/r2 absent
    assert r["n"] == N
    assert r["mean_r1"] == r["mean_r2"] == 0.5
    assert r["r2_minus_r1"]["mean"] == 0.0
    assert r["flip_rate"] == pytest.approx(10 / N)          # tasks 0..4 and 10..14 differ
    assert r["mean_abs_diff_points"] == pytest.approx(50.0)
    assert r["pass_at_2"] == pytest.approx(5 / N)           # tasks 5..9 in both
    assert r["pass_at_1"] == pytest.approx(0.5)


# ------------------------------------------------------------ per operation


def test_per_operation_shares_and_reads_contrast(env):
    def tests(read_ok, write_ok, delete_ok):
        return [("list_x", read_ok), ("get_x", read_ok), ("create_x", write_ok), ("update_x", write_ok), ("delete_x", delete_ok)]

    base_g = {t: tests(False, False, True) for t in TASKS}
    notice_g = {t: tests(True, False, True) for t in TASKS}
    write_step(env["root"], "v3eval_base_low", arm(0.0), base_g)
    write_step(env["root"], "v3eval_notice_low", arm(0.5), notice_g)
    ops = run(env)["per_operation"]
    assert ops["arms"]["base"]["reads"]["pass_share"] == 0.0
    assert ops["arms"]["base"]["delete"]["pass_share"] == 1.0
    assert ops["arms"]["notice"]["reads"] == {"n_tasks": N, "pass_share": 1.0}
    assert ops["arms"]["notice"]["writes"]["pass_share"] == 0.0
    assert ops["reads_contrasts"]["notice - base"]["mean"] == pytest.approx(100.0)
    assert "placebo - base" not in ops["reads_contrasts"]  # placebo step absent


def test_per_operation_skips_cells_without_grade_and_tasks_without_a_group(env):
    grades = {t: [("get_x", True)] for t in TASKS[:-1]}  # last task has no grade.json; no task has create/delete tests
    write_step(env["root"], "v3eval_base_low", arm(0.0), grades)
    ops = run(env)["per_operation"]["arms"]["base"]
    assert ops == {"reads": {"n_tasks": N - 1, "pass_share": 1.0}}


# ------------------------------------------------------------ H10 and header on/off


def test_h10_supported_with_prompting_note(env):
    on = ["b0", "b1"]
    v1 = [tb(t, "high", 0.0) for t in on] + [tb(t, "high", 1.0) for t in ("b2", "b3")]
    write_step(env["root"], "tierb50_header_high",
               {**{f"{t}__hint": 1.0 for t in on}, **{f"{t}__evidence": 0.5 for t in on}})
    h10 = run(env, v1)["h10"]
    assert h10["state"] == "complete"
    assert h10["b_minus_a"]["mean"] == 100.0 and h10["c_minus_a"]["mean"] == 50.0 and h10["larger"] == "hint"
    assert h10["verdict"]["status"] == "supported"
    assert ac.PROMPT_NOTE in h10["verdict"]["evidence"]
    assert h10["steps"]["a"]["expected"] == 2  # only the hidden-header tasks


def test_h10_not_supported_when_one_arm_is_below_20_points(env):
    on = ["b0", "b1"]
    v1 = [tb(t, "high", 0.5) for t in on]
    write_step(env["root"], "tierb50_header_high",
               {**{f"{t}__hint": 0.6 for t in on}, **{f"{t}__evidence": 1.0 for t in on}})
    h10 = run(env, v1)["h10"]
    assert h10["verdict"]["status"] == "not supported"
    assert h10["larger"] == "evidence"
    assert ac.PROMPT_NOTE not in h10["verdict"]["evidence"]


def test_h10_incomplete_header_step_is_not_yet_testable(env):
    v1 = [tb(t, "high", 0.0) for t in ("b0", "b1")]
    write_step(env["root"], "tierb50_header_high", {"b0__hint": 1.0, "b0__evidence": 1.0, "b1__hint": 1.0})
    assert run(env, v1)["h10"]["verdict"]["status"] == "not yet testable"


def test_header_on_off_table_for_each_effort_present(env):
    v1 = [tb("b0", "low", 0.0), tb("b1", "low", 0.2), tb("b2", "low", 1.0), tb("b3", "low", 0.8),
          tb("b0", "high", 0.5), tb("b1", "high", 0.5), tb("b2", "high", 1.0), tb("b3", "high", 1.0, "api_error")]
    med = [tb(t, "medium", 1.0) for t in ("b0", "b1", "b2", "b3")]
    table = run(env, v1, med)["header_on_off"]
    assert list(table) == ["low", "medium", "high"]
    assert (table["low"]["n_on"], table["low"]["n_off"]) == (2, 2)
    assert table["low"]["pass_on"] == pytest.approx(0.1) and table["low"]["pass_off"] == pytest.approx(0.9)
    assert table["low"]["on_minus_off"]["mean"] == pytest.approx(-80.0)
    assert table["medium"]["on_minus_off"]["mean"] == 0.0
    assert table["high"]["api_error_rows"] == 1 and table["high"]["n_off"] == 1


def test_main_without_any_data_exits_0_and_marks_steps_not_run(env):
    s = run(env)
    assert s["header_on_off"] == {}
    assert all(v["state"] == "not run yet" for v in s["steps"].values())
    assert "no Tier B data" in s["report"]


# ------------------------------------------------------------ high effort: repeat runs and the mean of 3 runs


def write_results_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["task", "set", "effort", "har_mode", "run", "status", "pass_rate"])
        w.writeheader()
        w.writerows(rows)


def high_rows(rates: dict, status: str = "ok") -> list[dict]:
    return [{"task": t, "set": "single", "effort": "high", "har_mode": "raw", "run": 1, "status": status, "pass_rate": p} for t, p in rates.items()]


def test_repeat_runs_at_high_report_per_run_means_differences_and_per_task_sd(env):
    res = env["tmp"] / "results"
    r1 = {t: 1.0 if i < 10 else 0.0 for i, t in enumerate(TASKS)}
    r2 = {t: 1.0 if i < 8 else 0.0 for i, t in enumerate(TASKS)}   # 2 tasks lost: mean 0.4
    r3 = {t: 1.0 if i < 10 else 0.5 for i, t in enumerate(TASKS)}  # mean 0.75
    write_results_csv(res / "heal_v3_eval_base_high.csv", high_rows(r1))
    write_results_csv(res / "heal_v3_eval_base_high_r2.csv", high_rows(r2))
    write_results_csv(res / "heal_v3_eval_base_high_r3.csv", high_rows(r3))
    r = run(env)["repeat_runs"]["base_high"]
    assert (r["n"], r["runs"]) == (N, 3)
    assert (r["mean_r1"], r["mean_r2"], r["mean_r3"]) == pytest.approx((0.5, 0.4, 0.75))
    assert r["r2_minus_r1"]["mean"] == pytest.approx(-10.0) and r["r3_minus_r1"]["mean"] == pytest.approx(25.0)
    assert r["mean_of_runs"] == pytest.approx((0.5 + 0.4 + 0.75) / 3)
    # per-task sample SD over [r1, r2, r3]: tasks 0-7 (1,1,1): 0; 8-9 (1,0,1): 0.577; 10-19 (0,0,0.5): 0.289
    assert r["per_task_sd_points"] == pytest.approx(100 * (2 * 0.5773502691896257 + 10 * 0.2886751345948129) / N)
    assert r["flip_rate"] == pytest.approx((2 / N + 0 + 2 / N) / 3)  # full-repair flips: r1-r2 two tasks, r1-r3 none, r2-r3 two
    report = run(env)["report"]
    assert "Repeat runs at high" in report and "case 3 base" in report


def test_repeat_runs_at_high_skip_a_series_without_repeats_and_raise_on_a_missing_task(env):
    res = env["tmp"] / "results"
    write_results_csv(res / "heal_v3_eval_hint_high.csv", high_rows({t: 0.5 for t in TASKS}))
    assert run(env)["repeat_runs"]["hint_high"] is None  # one run only: not a repeat
    write_results_csv(res / "heal_v3_eval_hint_high_r2.csv", high_rows({t: 0.5 for t in TASKS[:-1]}))
    with pytest.raises(ac.high_runs.RunMismatchError, match=r"run 2: missing 1 task"):
        run(env)


def test_tierb_high_is_the_per_task_mean_of_its_runs_in_h10_and_the_header_table(env):
    res = env["tmp"] / "results"
    v1 = [tb(t, "high", p) for t, p in (("b0", 0.0), ("b1", 0.0), ("b2", 1.0), ("b3", 1.0))]
    write_results_csv(res / "tierb50_high_r2.csv", [{**r, "set": "", "har_mode": "raw"} for r in
                      [tb(t, "high", p) for t, p in (("b0", 1.0), ("b1", 0.0), ("b2", 1.0), ("b3", 1.0))]])
    write_results_csv(res / "tierb50_high_r3.csv", [{**r, "set": "", "har_mode": "raw"} for r in
                      [tb(t, "high", p) for t, p in (("b0", 0.5), ("b1", 0.5), ("b2", 1.0), ("b3", 0.0))]])
    write_step(env["root"], "tierb50_header_high", {**{f"{t}__hint": 1.0 for t in ("b0", "b1")}, **{f"{t}__evidence": 1.0 for t in ("b0", "b1")}})
    s = run(env, v1)
    on = s["header_on_off"]["high"]
    assert on["pass_on"] == pytest.approx((0 + 1 + 0.5 + 0 + 0.5 + 0) / 6) and on["pass_off"] == pytest.approx((1 + 1 + 1 + 1 + 1 + 0) / 6)
    assert s["h10"]["steps"]["a"]["runs_per_task"] == 3 and s["h10"]["steps"]["a"]["pass_rate"] == pytest.approx(1 / 3)
    assert s["h10"]["b_minus_a"]["mean"] == pytest.approx(100 * 2 / 3)
    assert s["high_runs_per_task"]["tierb"] == 3 and "mean of 3 runs" in s["report"]
