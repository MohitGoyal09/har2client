"""scripts/plot_story.py on synthetic data (tmp dirs and CSVs; no model runs, no Beam API)."""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import heal_v3_verdicts as hv  # noqa: E402
from story_figs import data as D  # noqa: E402

FAMILIES = ("single", "release3", "release4", "partial", "sdk", "misleading", "terse")
EFFORTS = D.EFFORTS
N_BOOT = 60


def score(pass_rate: float, tokens: float = 100.0, family: str = "single", credit: dict | None = None) -> hv.TaskScore:
    return hv.TaskScore(family, pass_rate, pass_rate, 1.0 if pass_rate >= 1 else 0.0, tokens, credit or {})


def tasks(*rates: float, tokens: float = 100.0) -> dict:
    return {f"t{i}": score(r, tokens) for i, r in enumerate(rates)}


# ------------------------------------------------------------ data functions


def test_cost_per_solve_is_total_tokens_over_solved_tasks():
    # Arrange: total tokens 1000, two tasks at pass rate 1.0
    t = {f"t{i}": score(p, tok) for i, (p, tok) in enumerate([(1.0, 100), (0.0, 200), (1.0, 300), (0.5, 400)])}
    # Act
    out = D.cost_per_solve(t, N_BOOT, 1, "k")
    # Assert
    assert (out["n"], out["solved"], out["mean_tokens"], out["value"]) == (4, 2, 250.0, 500.0)
    assert out["ci_low"] <= out["ci_high"]


def test_cost_per_solve_with_nothing_solved_has_no_value():
    out = D.cost_per_solve(tasks(0.0, 0.5), N_BOOT, 1, "k")
    assert out["solved"] == 0 and out["value"] is None and out["ci_low"] is None


def test_cost_per_solve_matches_issue_sanity_numbers():
    # 64 tasks x 1,758 mean tokens, 11 solved -> about 10.2K
    t = {f"t{i}": score(1.0 if i < 11 else 0.0, 1758.0) for i in range(64)}
    assert D.cost_per_solve(t, N_BOOT, 1, "k")["value"] == pytest.approx(1758 * 64 / 11)


@pytest.mark.parametrize("with_floor", [False, True])
def test_outcome_shares_sum_to_one(with_floor):
    t = tasks(1.0, 1.0, 0.0, 0.3, 0.6, 0.9)
    floor = {"t0": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.5, "t4": 0.2, "t5": 0.9} if with_floor else None
    out = D.outcome_counts(t, floor)
    assert sum(out["shares"].values()) == pytest.approx(1.0)
    assert sum(out["counts"].values()) == out["n"] == 6
    if with_floor:  # t3 is at or below its v1 score; t4 is above; t5 ties (not above)
        assert out["counts"] == {D.SEG_SOLVED: 2, D.SEG_ABOVE: 1, D.SEG_BELOW: 2, D.SEG_ZERO: 1}
    else:
        assert out["counts"] == {D.SEG_SOLVED: 2, D.SEG_PARTIAL: 3, D.SEG_ZERO: 1}


def test_family_matrix_shape_and_order():
    steps = {}
    for arm in (D.BASE, D.HINT):
        for e in EFFORTS:
            steps[arm, e] = {f"{f}_{k}": score(0.5 if arm == D.BASE else 1.0, family=f) for f in reversed(FAMILIES) for k in range(2)}
    m = D.family_matrix(D.Dataset(steps))
    assert [r["family"] for r in m["rows"]] == list(FAMILIES)
    assert all(r["n"] == 2 for r in m["rows"])
    assert [(c["arm"], c["effort"]) for c in m["cols"]] == [(a, e) for a in (D.BASE, D.HINT) for e in EFFORTS]
    assert len(m["matrix"]) == 7 and all(len(row) == 6 for row in m["matrix"])
    assert m["matrix"][0][:3] == [50.0] * 3 and m["matrix"][0][3:] == [100.0] * 3


def test_flip_counts_gains_and_losses():
    a, b = tasks(1.0, 0.0, 1.0, 0.5), tasks(0.0, 1.0, 1.0, 1.0)
    assert D.flip_counts(a, b) == {"n": 4, "gains": 2, "losses": 1, "net": 1, "solved_before": 2, "solved_after": 3}


def test_token_split_reports_missing_reasoning_rows():
    rows = [{"task": "a", "status": "ok", "completion_tokens": "100", "reasoning_tokens": "60"},
            {"task": "b", "status": "ok", "completion_tokens": "50", "reasoning_tokens": ""},
            {"task": "c", "status": "ok", "completion_tokens": "10", "reasoning_tokens": "20"},
            {"task": "d", "status": "api_error", "completion_tokens": "", "reasoning_tokens": ""}]
    out = D.token_split(rows)
    assert out["n_tasks"] == 3 and out["n_with_split"] == 2 and out["tasks_without_split"] == 1
    assert out["rows_missing_reasoning"] == 1 and out["rows_reasoning_over_completion"] == 1


def test_load_floor_absent_or_incomplete_means_no_split(tmp_path):
    assert D.load_floor(tmp_path / "none.json", {"a"})[0] is None
    path = tmp_path / "floor.json"
    path.write_text(json.dumps({"per_task": [{"task": "a", "v1_pass_rate": 0.1}]}))
    assert D.load_floor(path, {"a"})[0] == {"a": 0.1}
    assert D.load_floor(path, {"a", "b"})[0] is None


# ------------------------------------------------------------ synthetic project on disk

HEADER = ["task", "effort", "run", "status", "pass_rate", "strict_pass_rate", "completion_tokens", "reasoning_tokens", "set", "change_credit"]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def case3_rows(effort: str, lift: float, with_credit: bool = True) -> list[dict]:
    rows = []
    for i, fam in enumerate(f for f in FAMILIES for _ in range(2)):
        rate = min(1.0, (i % 5) / 4 * 0.6 + lift + {"low": 0, "medium": 0.1, "high": 0.2}[effort] * (i % 2))
        credit = {"add_drift_enum": rate, "add_rate_limit": max(0.0, rate - 0.2)} if with_credit else {}
        rows.append({"task": f"v3_{fam}_{i:02d}", "effort": effort, "run": "1", "status": "ok", "pass_rate": rate, "strict_pass_rate": rate,
                     "completion_tokens": 1000 + 50 * i, "reasoning_tokens": 600 + 20 * i, "set": fam, "change_credit": json.dumps(credit)})
    return rows


@pytest.fixture
def project(tmp_path):
    results, batch, tierb = tmp_path / "results", tmp_path / "batch", tmp_path / "tierb"
    for arm, lift in (("base", 0.0), ("hint", 0.3)):
        for e in EFFORTS:
            write_csv(results / f"heal_v3_eval_{arm}_{e}.csv", case3_rows(e, lift))
    c1 = lambda e, base: [{"task": f"tierb_{i:02d}", "effort": e, "run": "1", "status": "ok", "pass_rate": 1.0 if (i % 2 == 0 or base > i) else 0.0,  # noqa: E731
                           "strict_pass_rate": 0, "completion_tokens": 800 + 10 * i, "reasoning_tokens": 300} for i in range(8)]
    write_csv(results / "tierb50_v1.csv", c1("low", 2) + c1("high", 6))
    write_csv(results / "tierb50_medium.csv", c1("medium", 4))
    for i in range(8):
        d = tierb / f"tierb_{i:02d}"
        d.mkdir(parents=True)
        (d / "task.json").write_text(json.dumps({"config": {"hidden_header": "on" if i % 2 else "off"}}))
    names = [r["task"] for r in case3_rows("low", 0)]
    (results / "heal_v3_eval_v1_floor.json").write_text(json.dumps({"per_task": [{"task": n, "v1_pass_rate": 0.1} for n in names]}))
    return {"results": results, "batch": batch, "tierb": tierb, "n3": len(names)}


def write_batch(project, arms=("base", "placebo", "notice", "hint"), lifts=None) -> None:
    lifts = lifts or {"base": 0.0, "placebo": 0.05, "notice": 0.2, "hint": 0.4}
    for arm in arms:
        write_csv(project["batch"] / f"v3eval_{arm}_low" / "results.csv", case3_rows("low", lifts[arm], with_credit=False))


def test_f7_skips_until_placebo_and_notice_exist(project):
    D.load_dataset(project["results"], project["tierb"])
    write_batch(project, arms=("base", "hint"))
    with pytest.raises(D.Skip, match="waiting for placebo/notice runs"):
        D.load_decomposition(project["batch"], project["n3"])


def test_f7_skips_on_incomplete_step_and_renders_when_complete(project, tmp_path):
    pytest.importorskip("matplotlib")
    import plot_story

    ds = D.load_dataset(project["results"], project["tierb"])
    write_batch(project)
    notice = project["batch"] / "v3eval_notice_low" / "results.csv"
    rows = notice.read_text().splitlines()
    notice.write_text("\n".join(rows[:-3]) + "\n")  # three tasks short
    out = tmp_path / "figs"
    status = plot_story.render(ds, out, ["F7"], project["batch"], n_boot=N_BOOT)
    assert status["F7"] == "F7 skipped: waiting for notice runs"
    assert not out.exists() or not list(out.glob("story_hint_decomposition.*"))
    write_batch(project)
    status = plot_story.render(ds, out, ["F7"], project["batch"], n_boot=N_BOOT)
    assert status["F7"].startswith("F7 story_hint_decomposition")
    assert (out / "story_hint_decomposition.png").is_file()
    steps = D.load_decomposition(project["batch"], project["n3"])
    dec = D.decomposition(steps, N_BOOT, 1)
    assert list(dec["bars"]) == list(D.DECOMP_ARMS) and len(dec["gains"]) == 3
    assert dec["bars"]["hint"]["pass"] > dec["bars"]["base"]["pass"]


def test_header_gap_is_on_minus_off(project):
    ds = D.load_dataset(project["results"], project["tierb"])
    rows = D.header_gap(ds, N_BOOT, 1)
    assert [r["effort"] for r in rows] == list(EFFORTS)
    assert all(r["gap_points"] == pytest.approx(r["on"]["pass"] - r["off"]["pass"]) for r in rows)
    assert rows[0]["on"]["n"] == rows[0]["off"]["n"] == 4 and rows[0]["gap_points"] < 0


def test_header_gap_skips_without_task_json(project):
    ds = D.load_dataset(project["results"], None)
    with pytest.raises(D.Skip):
        D.header_gap(ds, N_BOOT, 1)


# ------------------------------------------------------------ smoke


def test_smoke_renders_all_figures(project, tmp_path):
    pytest.importorskip("matplotlib")
    import plot_story

    write_batch(project)
    ds = D.load_dataset(project["results"], project["tierb"])
    out = tmp_path / "figs"
    status = plot_story.render(ds, out, None, project["batch"], n_boot=N_BOOT)
    assert set(status) == set(plot_story.FIGURES)
    assert not any("skipped" in line for line in status.values()), status
    for stem, _, _ in plot_story.FIGURES.values():
        for ext in ("png", "svg", "json"):
            assert (out / f"{stem}.{ext}").stat().st_size > 0, f"{stem}.{ext}"
    meta = json.loads((out / "story_outcomes.json").read_text())
    assert meta["partial_split_by_v1"] is True and "Beam-1.0-501B-A23B" in meta["footer"]


def test_cli_only_flag_and_unknown_figure(project, tmp_path, capsys):
    pytest.importorskip("matplotlib")
    import plot_story

    base = ["--results-dir", str(project["results"]), "--tasks-tierb", str(project["tierb"]), "--out-dir", str(tmp_path / "o"),
            "--batch-root", str(project["batch"]), "--n-boot", str(N_BOOT)]
    assert plot_story.main([*base, "--only", "F1,F3"]) == 0
    assert capsys.readouterr().out.count("\n") == 2
    assert sorted(p.stem for p in (tmp_path / "o").glob("*.png")) == ["story_effort_curves", "story_outcomes"]
    assert plot_story.main([*base, "--only", "F99"]) == 2


def test_outcomes_without_floor_file_use_three_segments(project):
    from story_figs import figs_a

    (project["results"] / "heal_v3_eval_v1_floor.json").unlink()
    ds = D.load_dataset(project["results"], project["tierb"])
    out = figs_a.f3_data(ds, N_BOOT, 1)
    assert out["partial_split_by_v1"] is False and "not found" in out["note"]
    assert all(D.SEG_PARTIAL in b["shares"] for b in out["bars"])


# ------------------------------------------------------------ high effort: mean of 3 runs


def write_repeat(project, stem: str, rows: list[dict], n: int) -> None:
    write_csv(project["results"] / f"{stem}_r{n}.csv", rows)


def test_high_steps_are_the_per_task_mean_over_run_1_and_the_repeats(project):
    base_high = case3_rows("high", 0.0)
    write_repeat(project, "heal_v3_eval_base_high", [{**r, "pass_rate": 0.0} for r in base_high], 2)
    write_repeat(project, "heal_v3_eval_base_high", [{**r, "pass_rate": 1.0} for r in base_high], 3)
    ds = D.load_dataset(project["results"], project["tierb"])
    run1 = {r["task"]: float(r["pass_rate"]) for r in base_high}
    got = ds.tasks(D.BASE, "high")
    assert all(got[t].pass_rate == pytest.approx((p + 0.0 + 1.0) / 3) for t, p in run1.items())
    assert ds.tasks(D.BASE, "low")["v3_single_00"].pass_rate == pytest.approx(case3_rows("low", 0.0)[0]["pass_rate"])  # low stays 1 run


def test_dataset_reports_the_fewest_runs_behind_any_high_step(project):
    for arm in ("base", "hint"):
        for n in (2, 3):
            write_repeat(project, f"heal_v3_eval_{arm}_high", case3_rows("high", 0.0 if arm == "base" else 0.3), n)
    assert D.load_dataset(project["results"], project["tierb"]).high_runs == 1  # Tier B has no repeats yet
    for n in (2, 3):
        write_repeat(project, "tierb50_high", [{"task": f"tierb_{i:02d}", "effort": "high", "run": "1", "status": "ok", "pass_rate": 1.0,
                                                "completion_tokens": 900} for i in range(8)], n)
    assert D.load_dataset(project["results"], project["tierb"]).high_runs == 3


def test_a_repeat_that_misses_a_task_raises_for_the_story_figures(project):
    write_repeat(project, "heal_v3_eval_base_high", case3_rows("high", 0.0)[:-1], 2)
    with pytest.raises(D.high_runs.RunMismatchError, match="run 2: missing 1 task"):
        D.load_dataset(project["results"], project["tierb"])


def test_figure_captions_name_the_runs_behind_high(project, tmp_path):
    for n in (2, 3):
        write_repeat(project, "tierb50_high", [{"task": f"tierb_{i:02d}", "effort": "high", "run": "1", "status": "ok", "pass_rate": 1.0,
                                                "completion_tokens": 900} for i in range(8)], n)
        for arm in ("base", "hint"):
            write_repeat(project, f"heal_v3_eval_{arm}_high", case3_rows("high", 0.0 if arm == "base" else 0.3), n)
    ds = D.load_dataset(project["results"], project["tierb"])
    out = tmp_path / "figs"
    pytest.importorskip("matplotlib")
    import plot_story as ps
    ps.render(ds, out, only=["F1"], n_boot=N_BOOT)
    data = json.loads((out / "story_effort_curves.json").read_text())
    assert data["high_runs"] == 3
