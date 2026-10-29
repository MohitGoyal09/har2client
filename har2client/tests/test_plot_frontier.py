"""Tests for scripts/plot_frontier.py (score vs tokens frontier chart)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "plot_frontier.py"
_spec = importlib.util.spec_from_file_location("plot_frontier", SCRIPT)
pf = importlib.util.module_from_spec(_spec)
sys.modules["plot_frontier"] = pf
_spec.loader.exec_module(pf)

FIELDS = ["task", "effort", "run", "status", "pass_rate", "prompt_tokens", "completion_tokens", "reasoning_tokens"]


def row(task, effort, pass_rate, comp, prompt=1000, run=1, status="ok"):
    return {"task": task, "effort": effort, "run": run, "status": status,
            "pass_rate": pass_rate, "prompt_tokens": prompt,
            "completion_tokens": comp, "reasoning_tokens": ""}


def write_csv(path: Path, rows) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if v is None else v for k, v in r.items()})
    return path


def parsed(rows):
    return [pf.parse_row({k: str(v) if v is not None else "" for k, v in r.items()}) for r in rows]


# ---- parsing ----------------------------------------------------------

def test_parse_row_empty_pass_rate_counts_as_zero():
    r = pf.parse_row({"task": "t", "effort": "low", "run": "1", "status": "api_error",
                      "pass_rate": "", "prompt_tokens": "", "completion_tokens": ""})
    assert r.pass_rate == 0.0
    assert r.completion_tokens is None


def test_parse_row_reads_numbers():
    r = pf.parse_row({"task": "t", "effort": "high", "run": "2", "status": "ok",
                      "pass_rate": "0.5", "prompt_tokens": "10", "completion_tokens": "5"})
    assert (r.pass_rate, r.prompt_tokens, r.completion_tokens, r.run) == (0.5, 10.0, 5.0, "2")


# ---- effort ordering --------------------------------------------------

def test_effort_order_low_to_max_and_skips_absent():
    assert pf.order_efforts({"high", "low", "max", "medium", "xhigh"}) == ["low", "medium", "high", "xhigh", "max"]
    assert pf.order_efforts({"high", "low"}) == ["low", "high"]


def test_effort_order_unknown_efforts_go_last_sorted():
    assert pf.order_efforts({"high", "zzz", "aaa"}) == ["high", "aaa", "zzz"]


# ---- points -----------------------------------------------------------

def test_point_means_over_tasks_of_task_means():
    rows = parsed([
        row("a", "low", 1.0, 100, run=1), row("a", "low", 0.0, 300, run=2),  # task mean 0.5 / 200
        row("b", "low", 1.0, 400, run=1),                                      # 1.0 / 400
    ])
    pts, dropped = pf.compute_series_points("S", rows, x_mode="output", n_boot=50, seed=1)
    assert dropped == 0
    (p,) = pts
    assert p.y == pytest.approx(75.0)
    assert p.x == pytest.approx(300.0)
    assert p.n_tasks == 2


def test_total_mode_adds_prompt_tokens():
    rows = parsed([row("a", "low", 1.0, 100, prompt=900)])
    (p,), _ = pf.compute_series_points("S", rows, x_mode="total", n_boot=10, seed=1)
    assert p.x == pytest.approx(1000.0)


def test_no_code_with_empty_pass_rate_counts_zero_and_missing_tokens_ignored():
    rows = parsed([
        row("a", "low", 1.0, 100),
        row("b", "low", None, None, prompt=None, status="no_code"),
    ])
    (p,), _ = pf.compute_series_points("S", rows, x_mode="output", n_boot=10, seed=1)
    assert p.y == pytest.approx(50.0)
    assert p.x == pytest.approx(50.0)  # task b has no token data -> 0 for that task


def test_api_error_rows_are_left_out_of_the_means():
    rows = parsed([
        row("a", "low", 1.0, 100),
        row("b", "low", None, None, prompt=None, status="api_error"),
    ])
    (p,), _ = pf.compute_series_points("S", rows, x_mode="output", n_boot=10, seed=1)
    assert p.y == pytest.approx(100.0) and p.n_tasks == 1


def test_report_counts_api_error_rows_per_effort(tmp_path):
    path = write_csv(tmp_path / "s.csv", [
        row("a", "low", 1.0, 100), row("a", "high", 1.0, 200),
        row("a", "low", 0.0, None, run=2, status="api_error"),
        row("a", "high", 0.0, None, run=2, status="api_error"),
        row("a", "high", 0.0, None, run=3, status="api_error"),
    ])
    points, meta = pf.build_report([("S", path)], "output", 10, 1)
    assert [p.y for p in points] == [100.0, 100.0]
    assert meta["series"]["S"]["api_error_rows_excluded"] == {"low": 1, "high": 2}
    assert any("api_error rows excluded" in w and "high=2" in w for w in meta["warnings"])


def test_report_has_no_api_error_key_when_there_are_none(tmp_path):
    path = write_csv(tmp_path / "s.csv", [row("a", "low", 1.0, 100)])
    _, meta = pf.build_report([("S", path)], "output", 10, 1)
    assert "api_error_rows_excluded" not in meta["series"]["S"]


def test_completion_tokens_not_double_counted_with_reasoning():
    rows = parsed([{**row("a", "low", 1.0, 1000), "reasoning_tokens": 600}])
    (p,), _ = pf.compute_series_points("S", rows, x_mode="output", n_boot=10, seed=1)
    assert p.x == pytest.approx(1000.0)


# ---- paired filter ----------------------------------------------------

def test_only_tasks_in_every_effort_are_used():
    rows = parsed([
        row("a", "low", 1.0, 100), row("a", "high", 1.0, 200),
        row("b", "low", 0.0, 100), row("b", "high", 1.0, 200),
        row("c", "low", 1.0, 100),  # missing at high
    ])
    pts, dropped = pf.compute_series_points("S", rows, x_mode="output", n_boot=10, seed=1)
    assert dropped == 1
    assert [p.n_tasks for p in pts] == [2, 2]
    assert [p.effort for p in pts] == ["low", "high"]
    assert pts[0].y == pytest.approx(50.0)


# ---- bootstrap --------------------------------------------------------

def test_bootstrap_is_deterministic_for_seed_and_differs_across_seeds():
    groups = [[(i * 7 % 10) / 10] for i in range(25)]
    a = pf.bootstrap_ci(groups, n_boot=500, seed=7, key="k")
    b = pf.bootstrap_ci(groups, n_boot=500, seed=7, key="k")
    c = pf.bootstrap_ci(groups, n_boot=500, seed=8, key="k")
    assert a == b
    assert a != c


def test_bootstrap_ci_brackets_mean_and_is_in_points():
    groups = [[1.0], [0.0], [1.0], [0.0], [1.0], [1.0]]
    lo, hi = pf.bootstrap_ci(groups, n_boot=1000, seed=3, key="k")
    assert 0.0 <= lo < 4 / 6 * 100 < hi <= 100.0


def test_bootstrap_constant_data_has_zero_width():
    lo, hi = pf.bootstrap_ci([[1.0], [1.0], [1.0]], n_boot=200, seed=1, key="k")
    assert lo == hi == 100.0


def test_bootstrap_redraws_runs_within_task():
    # One task with runs [1, 0]: the resampled mean must be able to hit 0 and 100.
    lo, hi = pf.bootstrap_ci([[1.0, 0.0]], n_boot=500, seed=1, key="k")
    assert (lo, hi) == (0.0, 100.0)


# ---- pareto -----------------------------------------------------------

def P(x, y, label="p"):
    return pf.Point(series="S", effort=label, x=x, y=y, ci_low=y, ci_high=y, n_tasks=1)


def test_pareto_marks_non_dominated():
    pts = [P(100, 50, "a"), P(200, 80, "b"), P(300, 70, "c"), P(50, 10, "d")]
    flags = pf.pareto_flags(pts)
    assert flags == [True, True, False, True]


def test_pareto_ties_both_kept_when_identical():
    pts = [P(100, 50, "a"), P(100, 50, "b")]
    assert pf.pareto_flags(pts) == [True, True]


def test_pareto_same_x_lower_y_dominated_and_same_y_higher_x_dominated():
    pts = [P(100, 60, "a"), P(100, 50, "b"), P(200, 60, "c")]
    assert pf.pareto_flags(pts) == [True, False, False]


def test_pareto_spans_all_series():
    a = pf.Point("A", "low", 100, 50, 50, 50, 1)
    b = pf.Point("B", "low", 90, 60, 60, 60, 1)
    assert pf.pareto_flags([a, b]) == [False, True]


# ---- medians / region -------------------------------------------------

def test_medians():
    pts = [P(1, 10), P(3, 30), P(2, 20), P(4, 40)]
    assert pf.medians(pts) == (2.5, 25.0)


# ---- end to end -------------------------------------------------------

def test_build_report_and_json(tmp_path):
    rows_a = [row(t, e, 1.0 if (t != "t2" or e == "high") else 0.0, 100 if e == "low" else 400)
              for t in ("t1", "t2", "t3") for e in ("low", "high")]
    rows_b = [row(t, e, 0.0, 900) for t in ("t1", "t2") for e in ("low", "medium")]
    pa = write_csv(tmp_path / "a.csv", rows_a)
    pb = write_csv(tmp_path / "b.csv", rows_b)
    out = tmp_path / "fig" / "f"
    points, meta = pf.build_report([("A", pa), ("B", pb)], x_mode="output", n_boot=100, seed=5)
    pf.write_json(out, points, meta)
    data = json.loads(out.with_suffix(".json").read_text())
    keys = {"series", "effort", "x", "y", "ci_low", "ci_high", "n_tasks", "on_pareto"}
    assert len(data["points"]) == 4
    assert all(keys <= set(p) for p in data["points"])
    assert [p["effort"] for p in data["points"] if p["series"] == "B"] == ["low", "medium"]
    assert any(p["on_pareto"] for p in data["points"])
    assert data["meta"]["seed"] == 5


def test_parse_series_arg():
    assert pf.parse_series("Beam=results/x.csv") == ("Beam", Path("results/x.csv"))
    with pytest.raises(ValueError):
        pf.parse_series("nolabel.csv")


def test_plot_smoke_writes_png_svg(tmp_path):
    pytest.importorskip("matplotlib")
    rows = [row(t, e, 1.0 if i % 2 else 0.0, 100 * (j + 1))
            for i, t in enumerate(("t1", "t2", "t3", "t4")) for j, e in enumerate(("low", "medium", "high"))]
    p = write_csv(tmp_path / "a.csv", rows)
    out = tmp_path / "chart"
    rc = pf.main(["--series", f"A={p}", "--title", "T", "--out", str(out), "--resamples", "100"])
    assert rc == 0
    assert out.with_suffix(".png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert out.with_suffix(".svg").stat().st_size > 0
    assert out.with_suffix(".json").exists()


def test_display_path_is_repo_relative_inside_repo_and_unchanged_outside(tmp_path):
    inside = pf.ROOT / "results" / "x.csv"
    assert pf._display_path(inside) == "results/x.csv"
    assert pf._display_path(tmp_path / "x.csv") == str(tmp_path / "x.csv")
