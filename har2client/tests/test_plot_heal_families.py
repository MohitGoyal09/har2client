"""scripts/plot_heal_families.py on synthetic Steps (no model runs, no network)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import heal_v3_verdicts as hv  # noqa: E402
import plot_heal_families as phf  # noqa: E402

N_BOOT = 300


def make_step(arm, effort, passes, expected=None):
    """passes: family -> list of pass rates; one task per value."""
    scores = {f"{fam}{i}": hv.TaskScore(fam, p, p, 1.0 if p >= 1 else 0.0, 100.0) for fam, vals in passes.items() for i, p in enumerate(vals)}
    n = len(scores)
    return hv.Step(arm, effort, n if expected is None else expected, n, 0, scores)


def analysis(*steps):
    return hv.Analysis({(s.arm, s.effort): s for s in steps}, n_boot=50, seed=1)


BASE = {"terse": [0.0, 0.5], "single": [0.5, 1.0, 0.0], "sdk": [0.25]}  # deliberately not in FAMILY_ORDER order
HINT = {"terse": [1.0, 1.0], "single": [1.0, 1.0, 0.5], "sdk": [0.75]}


def pair(effort, **kw):
    return [make_step("base", effort, BASE, **kw), make_step("hint", effort, HINT, **kw)]


# ------------------------------------------------------------ pick_effort


def test_pick_effort_returns_preferred_when_both_arms_complete():
    assert phf.pick_effort(analysis(*pair("high"), *pair("low"))) == "high"


def test_pick_effort_falls_back_to_highest_complete_effort_below_preferred():
    assert phf.pick_effort(analysis(*pair("medium"), *pair("low"))) == "medium"


def test_pick_effort_skips_effort_where_one_arm_is_missing_or_partial():
    partial = make_step("hint", "high", HINT, expected=99)
    a = analysis(make_step("base", "high", BASE), partial, *pair("low"))
    assert phf.pick_effort(a) == "low"
    assert phf.pick_effort(analysis(make_step("base", "high", BASE), *pair("low"))) == "low"


def test_pick_effort_only_knows_high_medium_low():
    assert phf.PREFERRED_EFFORT == "high" and phf.FALLBACK_EFFORTS == ("high", "medium", "low")
    assert phf.pick_effort(analysis(*pair("xhigh"))) is None  # an xhigh step is never shown


def test_pick_effort_is_none_when_nothing_qualifies():
    assert phf.pick_effort(analysis()) is None


# ------------------------------------------------------------ family_points


def points():
    return phf.family_points(analysis(*pair("high")), "high", N_BOOT, 7)


def test_family_points_means_and_n_are_in_percent():
    d = points()
    assert d["base"]["single"]["n"] == 3 and d["base"]["single"]["pass_rate"] == pytest.approx(50.0)
    assert d["hint"]["terse"]["pass_rate"] == pytest.approx(100.0)
    assert d["base"]["sdk"]["n"] == 1 and d["base"]["sdk"]["pass_rate"] == pytest.approx(25.0)


def test_family_points_order_follows_family_order_then_all():
    d = points()
    assert list(d["base"]) == ["single", "sdk", "terse", "all"]
    assert list(d["base"]) == list(d["hint"])


def test_all_row_is_the_overall_task_mean():
    d = points()
    assert d["base"]["all"]["n"] == 6
    assert d["base"]["all"]["pass_rate"] == pytest.approx(100 * (0.0 + 0.5 + 0.5 + 1.0 + 0.0 + 0.25) / 6)


def test_ci_contains_the_mean_and_is_deterministic():
    d = points()
    for arm in hv.ARMS:
        for row in d[arm].values():
            assert row["ci_low"] <= row["pass_rate"] <= row["ci_high"]
    assert d == points()


def test_family_points_records_settings_and_is_json_able():
    d = points()
    assert (d["effort"], d["n_boot"], d["seed"], d["ci_level"]) == ("high", N_BOOT, 7, hv.CI_LEVEL)
    json.dumps(d)


# ------------------------------------------------------------ plot / write


def test_plot_writes_png_and_svg(tmp_path):
    pytest.importorskip("matplotlib")
    out = tmp_path / "fam"
    phf.plot_families(points(), "title", out)
    assert (tmp_path / "fam.png").stat().st_size > 0 and (tmp_path / "fam.svg").stat().st_size > 0


def test_write_family_chart_writes_json_and_returns_data(tmp_path):
    pytest.importorskip("matplotlib")
    out = tmp_path / "fam"
    data = phf.write_family_chart(analysis(*pair("high")), out)
    assert data["effort"] == "high"
    assert json.loads((tmp_path / "fam.json").read_text())["base"]["all"]["n"] == 6
    assert (tmp_path / "fam.png").is_file()


def test_write_family_chart_returns_none_without_qualifying_effort(tmp_path):
    assert phf.write_family_chart(analysis(), tmp_path / "fam") is None
    assert not list(tmp_path.iterdir())
