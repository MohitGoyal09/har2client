"""scripts/plot_final.py: the plotted numbers match the source CSVs, no probe20, every figure has png + svg + json."""

import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import analyze_models as am  # noqa: E402
import plot_final as pfin  # noqa: E402

RESULTS = ROOT / "results"
MODELS = RESULTS / "model_comparison"
pytestmark = pytest.mark.skipif(not (pfin.SUMMARY.is_file() and MODELS.is_dir()), reason="result files not present")


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("final_figs")
    pfin.build_all(out)
    return out


def load(out_dir: Path, name: str) -> dict:
    return json.loads((out_dir / f"{name}.json").read_text(encoding="utf-8"))


def raw_pass_rate(path: Path, effort: str | None = None, arm: str | None = None, repeats: tuple[Path, ...] = ()) -> float:
    scores, _tokens, _errors = am.read_cells(path, effort, arm, repeats)
    return 100.0 * sum(scores.values()) / len(scores)


def find(points: list[dict], model: str, effort: str, suite: str) -> dict:
    return next(p for p in points if (p["model"], p["effort"], p["suite"]) == (model, effort, suite))


def test_every_figure_has_png_svg_and_json(out_dir):
    for name in pfin.FIGURES:
        for ext in ("png", "svg", "json"):
            assert (out_dir / f"{name}.{ext}").stat().st_size > 0, f"{name}.{ext}"


def test_committed_figures_exist():
    figures = RESULTS / "figures"
    if not all((figures / f"{n}.json").is_file() for n in pfin.FIGURES):
        pytest.skip("figures not generated yet: run scripts/plot_final.py")
    for name in pfin.FIGURES:
        assert all((figures / f"{name}.{ext}").is_file() for ext in ("png", "svg", "json"))


def test_png_is_200_dpi_wide_enough(out_dir):
    from matplotlib.image import imread

    for name in pfin.FIGURES:
        assert imread(out_dir / f"{name}.png").shape[1] >= 1800, name


def test_no_probe20_in_any_output(out_dir):
    for name in pfin.FIGURES:
        for ext in ("json", "svg"):
            text = (out_dir / f"{name}.{ext}").read_text(encoding="utf-8").lower()
            # matplotlib names SVG clip paths "p" + 10 random hex digits; those can contain "p20" by chance
            text = re.sub(r"\bp[0-9a-f]{10}\b", "", text)
            assert "probe20" not in text and "p20" not in text and "20 probes" not in text, f"{name}.{ext}"


def test_scoreboard_has_five_benchmarks_in_fixed_order(out_dir):
    data = load(out_dir, "final_scoreboard")
    assert data["benchmarks"] == ["Case 1: build", "Case 2: no probes", "Case 2: 5 probes", "Case 3: repair, no hint",
                                  "Case 3: repair, with hint"]
    assert len(data["cells"]) == 7 * 5


def test_beam_high_case1_is_the_mean_of_three_runs(out_dir):
    cells = load(out_dir, "final_scoreboard")["cells"]
    got = find(cells, "beam", "high", "tierb50")
    want = raw_pass_rate(RESULTS / "tierb50_v1.csv", "high", None,
                         (RESULTS / "tierb50_high_r2.csv", RESULTS / "tierb50_high_r3.csv"))
    assert got["runs"] == 3
    assert got["pass_rate"] == pytest.approx(want, abs=1e-6)
    assert round(got["pass_rate"], 1) == 74.5
    assert (got["ci_high"] - got["ci_low"]) / 2 == pytest.approx(10.8, abs=0.1) or got["ci_low"] < 74.5 < got["ci_high"]


def test_gpt_oss_high_case3_base_and_qwen_hint_match_raw_csv(out_dir):
    cells = load(out_dir, "final_scoreboard")["cells"]
    gpt = find(cells, "gpt", "high", "v3base")["pass_rate"]
    qwen = find(cells, "qwen", "thinking", "v3hint")["pass_rate"]
    assert gpt == pytest.approx(raw_pass_rate(MODELS / "gpt-oss-120b-high" / "v3base" / "results.csv"), abs=1e-6)
    assert qwen == pytest.approx(raw_pass_rate(MODELS / "qwen3.5-397b-thinking" / "v3hint" / "results.csv"), abs=1e-6)
    assert round(gpt, 1) == 58.5 and round(qwen, 1) == 52.3


def test_beam_case2_and_case3_cells_match_raw_csv(out_dir):
    cells = load(out_dir, "final_scoreboard")["cells"]
    case2 = RESULTS / "case2_probe" / "main_full" / "results.csv"
    assert find(cells, "beam", "high", "probe5")["pass_rate"] == pytest.approx(raw_pass_rate(case2, "high", "P5"), abs=1e-6)
    assert find(cells, "beam", "low", "probe0")["pass_rate"] == pytest.approx(raw_pass_rate(case2, "low", "placebo"), abs=1e-6)
    hint = raw_pass_rate(RESULTS / "heal_v3_eval_hint_high.csv", "high", None,
                         (RESULTS / "heal_v3_eval_hint_high_r2.csv", RESULTS / "heal_v3_eval_hint_high_r3.csv"))
    assert find(cells, "beam", "high", "v3hint")["pass_rate"] == pytest.approx(hint, abs=1e-6)


def test_scoreboard_outlines_the_best_cell_of_each_column(out_dir):
    cells = load(out_dir, "final_scoreboard")["cells"]
    for suite in pfin.SUITES:
        col = [c for c in cells if c["suite"] == suite]
        top = max(c["pass_rate"] for c in col)
        assert {c["pass_rate"] for c in col if c["best_in_column"]} == {top}


def test_leaderboard_shows_each_model_at_its_best_setting_in_order(out_dir):
    bars = load(out_dir, "final_leaderboard")["bars"]
    first = [b for b in bars if b["suite"] == "tierb50"]
    assert [(b["model"], b["effort"]) for b in first] == [("beam", "high"), ("gpt", "high"), ("qwen", "thinking")]
    assert all(b["ci_low"] <= b["pass_rate"] <= b["ci_high"] for b in bars)


def test_effort_curves_never_call_qwen_low_medium_or_high(out_dir):
    points = load(out_dir, "final_effort_curves")["points"]
    assert {p["effort"] for p in points if p["model"] == "qwen"} == {"thinking"}
    assert {p["effort"] for p in points if p["model"] != "qwen"} == {"low", "medium", "high"}


def test_information_gain_is_to_minus_from_and_beam_gains_most(out_dir):
    data = load(out_dir, "final_information_gain")
    gains = data["gains"]
    assert len(gains) == 6
    assert all(g["gain_points"] == pytest.approx(g["to"] - g["from"]) for g in gains)
    for info in {g["information"] for g in gains}:
        rows = {g["model"]: g["gain_points"] for g in gains if g["information"] == info}
        assert rows["beam"] == max(rows.values())
    assert "most" in data["title"]


def test_gap_vs_beam_matches_paired_differences_and_verdicts(out_dir):
    rows, _ = pfin.load_rows()
    for d in load(out_dir, "final_gap_vs_beam")["differences"]:
        want = rows[(d["model"], pfin.BEST[d["model"]], d["suite"])]["paired_vs_beam_high"]
        assert d["diff"] == pytest.approx(want["diff"])
        assert d["ci_low"] <= d["diff"] <= d["ci_high"]
        side = "beam_better" if d["ci_high"] < 0 else "other_better" if d["ci_low"] > 0 else "no_clear_difference"
        assert d["verdict"] == side
    gpt_case3 = next(d for d in load(out_dir, "final_gap_vs_beam")["differences"] if (d["model"], d["suite"]) == ("gpt", "v3base"))
    assert round(gpt_case3["diff"], 1) == 12.6


def test_beam_missing_info_numbers(out_dir):
    bars = {b["label"]: b for b in load(out_dir, "final_beam_missing_info")["bars"]}
    assert round(bars["header visible"]["pass_rate"], 1) == 99.8
    assert round(bars["header hidden"]["pass_rate"], 1) == 49.1
    assert round(bars["header hidden + hint (H10)"]["pass_rate"], 1) == 96.0
    assert round(bars["header hidden + evidence (H10)"]["pass_rate"], 1) == 98.9
    assert round(bars["up to 5 probes (P5)"]["pass_rate"], 1) == 95.7
    assert round(bars["no hint"]["pass_rate"], 1) == 46.0 and round(bars["with hint"]["pass_rate"], 1) == 87.7
    assert not bars["header hidden"]["information_available"] and bars["header visible"]["information_available"]


def test_cost_vs_score_carries_mean_output_tokens(out_dir):
    points = load(out_dir, "final_cost_vs_score")["points"]
    assert len(points) == 7 * 5
    got = find(points, "gpt", "high", "v3base")["mean_output_tokens"]
    _scores, tokens, _err = am.read_cells(MODELS / "gpt-oss-120b-high" / "v3base" / "results.csv")
    assert got == pytest.approx(sum(tokens.values()) / len(tokens))


def test_classify_maps_summary_keys():
    assert pfin.classify("beam-high") == ("beam", "high")
    assert pfin.classify("gpt-oss-120b-medium") == ("gpt", "medium")
    assert pfin.classify("qwen3.5-397b-thinking") == ("qwen", "thinking")
    with pytest.raises(ValueError):
        pfin.classify("other-model")


def test_spread_keeps_order_and_minimum_gap():
    out = pfin.spread([50.0, 51.0, 80.0], 9.0, 0, 100)
    assert out[1] - out[0] >= 9.0 - 1e-9 and out[2] > out[1]


def test_effort_matched_has_every_beam_and_gpt_cell_and_one_qwen_line_per_benchmark(out_dir):
    data = load(out_dir, "final_effort_matched")
    assert len(data["benchmarks"]) == 5
    bars = data["bars"]
    assert len(bars) == 5 * (3 + 3 + 1)
    assert {b["effort"] for b in bars if b["model"] == "qwen"} == {"thinking"}
    assert find(bars, "beam", "medium", "probe5")["pass_rate"] == pytest.approx(
        raw_pass_rate(RESULTS / "case2_probe" / "main_full" / "results.csv", "medium", "P5"), abs=1e-6)
    assert round(find(bars, "gpt", "low", "v3hint")["pass_rate"], 1) == 47.7
    wins = sum(find(bars, "beam", e, s)["pass_rate"] > find(bars, "gpt", e, s)["pass_rate"] for e in pfin.EFFORTS for s in pfin.SUITES)
    assert data["beam_ahead_cells"] == wins and data["matched_cells"] == 15
    assert f"{wins} of 15" in data["title"]


def test_all_scores_has_seven_settings_by_five_benchmarks_with_intervals(out_dir):
    data = load(out_dir, "final_all_scores")
    pts = data["points"]
    assert len(pts) == 7 * 5
    assert all(p["ci_low"] <= p["pass_rate"] <= p["ci_high"] for p in pts)
    assert {(p["model"], p["effort"]) for p in pts} == set(pfin.ROW_ORDER)
    assert find(pts, "beam", "low", "tierb50")["pass_rate"] == pytest.approx(
        raw_pass_rate(RESULTS / "tierb50_v1.csv", "low"), abs=1e-6)
    assert round(find(pts, "qwen", "thinking", "probe5")["pass_rate"], 1) == 48.0


def test_beam_by_effort_matches_the_effort_and_comparison_summaries(out_dir):
    bars = load(out_dir, "final_beam_by_effort")["bars"]
    assert len(bars) == 3 * 2 * 3
    effort_doc = json.loads((RESULTS / "analysis" / "tierb50_effort" / "summary.json").read_text(encoding="utf-8"))
    for level, arm in (("on", "header hidden"), ("off", "header visible")):
        sw = next(s for s in effort_doc["switches"] if s["switch"] == "hidden_header" and s["level"] == level)
        for e in pfin.EFFORTS:
            got = next(b for b in bars if b["arm"] == arm and b["effort"] == e)
            assert got["pass_rate"] == pytest.approx(sw["by_effort"][e]["pass_rate"] * 100)
    got = next(b for b in bars if b["arm"] == "up to 5 probes" and b["effort"] == "low")
    assert got["pass_rate"] == pytest.approx(raw_pass_rate(RESULTS / "case2_probe" / "main_full" / "results.csv", "low", "P5"), abs=1e-6)
    got = next(b for b in bars if b["arm"] == "with hint" and b["effort"] == "medium")
    assert round(got["pass_rate"], 1) == 76.9
