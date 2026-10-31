"""Tests for the submission additions: case 2 results and the model comparison in the export and the Space, the final figures,
and the card wording the challenge rules ask for (project description, functionality, how Beam was used).

Run:  python3 -m unittest discover -s release/tests -v   (the Space-number tests also need pandas, installed with gradio)
"""

from __future__ import annotations

import filecmp
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
SPACE = RELEASE / "space"
ROOT = RELEASE.parent
sys.path.insert(0, str(RELEASE))
sys.path.insert(0, str(SPACE))

import h2c_release  # noqa: E402
import sync_space_data  # noqa: E402
from h2c_release import COMPARISON_MODELS, COMPARISON_SUITES, RESULTS_DIR, RESULTS_FILES  # noqa: E402

try:
    import tables  # noqa: E402
except ImportError:  # pandas missing
    tables = None

CARD = (RELEASE / "DATASET_CARD.md").read_text(encoding="utf-8")


class ExportList(unittest.TestCase):
    def test_case2_main_full_files_are_exported(self) -> None:
        for name in ("REPORT.md", "summary.json", "results.csv", "SENSITIVITY.md"):
            self.assertIn(f"case2_probe/main_full/{name}", RESULTS_FILES)

    def test_comparison_files_are_exported_and_probe20_is_not(self) -> None:
        for model in COMPARISON_MODELS:
            for suite in COMPARISON_SUITES:
                self.assertIn(f"model_comparison/{model}/{suite}/results.csv", RESULTS_FILES)
        self.assertEqual(len([n for n in RESULTS_FILES if n.startswith("model_comparison/") and n.endswith("results.csv")]), 20)
        self.assertFalse([n for n in RESULTS_FILES if "probe20" in n])
        self.assertIn("analysis/model_comparison/summary.json", RESULTS_FILES)
        self.assertIn("analysis/model_comparison/report.md", RESULTS_FILES)

    def test_the_final_figures_are_exported_and_shown_in_the_space(self) -> None:
        names = [f"final_{n}.png" for n in ("scoreboard", "leaderboard", "effort_curves", "information_gain", "gap_vs_beam", "beam_missing_info", "cost_vs_score", "effort_matched", "all_scores", "beam_by_effort", "probing", "failure_kinds")]
        self.assertEqual(list(h2c_release.FINAL_FIGURES), names)
        for name in names:
            self.assertIn(f"figures/{name}", RESULTS_FILES)
            self.assertIn(name, h2c_release.SPACE_FIGURES)

    def test_every_exported_file_exists_in_the_results_folder(self) -> None:
        if not RESULTS_DIR.is_dir():
            self.skipTest("no har2client/results")
        missing = [n for n in RESULTS_FILES if not (RESULTS_DIR / n).is_file()]
        self.assertEqual(missing, [])


class SpaceSync(unittest.TestCase):
    def test_sync_copies_the_comparison_csvs_the_case2_csv_and_the_summary(self) -> None:
        if not (RESULTS_DIR / "analysis" / "model_comparison" / "summary.json").is_file():
            self.skipTest("no model comparison results")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "results").mkdir()
        sync_space_data.copy_comparison(tmp)
        for model in COMPARISON_MODELS:
            for suite in COMPARISON_SUITES:
                rel = Path("model_comparison") / model / suite / "results.csv"
                self.assertTrue(filecmp.cmp(RESULTS_DIR / rel, tmp / "results" / rel, shallow=False), rel)
        self.assertEqual(list((tmp / "results").rglob("probe20")), [])
        self.assertTrue(filecmp.cmp(RESULTS_DIR / h2c_release.CASE2_CSV, tmp / "results" / h2c_release.CASE2_CSV, shallow=False))
        self.assertTrue(filecmp.cmp(RESULTS_DIR / h2c_release.COMPARISON_SUMMARY, tmp / h2c_release.COMPARISON_SUMMARY, shallow=False))


@unittest.skipIf(tables is None, "pandas not installed")
class ComparisonNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = tables.load_bundle()
        cls.cells = tables.comparison_cells(cls.bundle)
        cls.summary = tables.comparison_summary()

    def test_pass_rates_and_tokens_computed_from_the_csvs_equal_the_committed_summary(self) -> None:
        self.assertEqual(len(self.summary["rows"]), 35)
        for row in self.summary["rows"]:
            cell = self.cells[(row["key"], row["suite"])]
            self.assertAlmostEqual(cell["pass_pct"], row["pass_rate"], places=1, msg=(row["key"], row["suite"]))
            self.assertAlmostEqual(cell["out_tokens"], row["mean_output_tokens"], delta=1.0, msg=(row["key"], row["suite"]))

    def test_the_table_has_the_three_models_and_five_suites_and_no_p20(self) -> None:
        table = tables.comparison_table(self.bundle)
        self.assertEqual(len(table), 7)
        self.assertEqual(list(table.columns), ["Model (setting)", *tables.COMPARISON_SUITES.values()])
        self.assertFalse([c for c in table.columns if "P20" in c])
        beam_high = table[table["Model (setting)"] == "Beam (high)"].iloc[0]
        self.assertEqual([beam_high[c] for c in table.columns[1:]], [74.5, 46.0, 87.7, 49.7, 95.7])
        self.assertFalse({r["suite"] for r in self.summary["rows"]} - set(tables.COMPARISON_SUITES))

    def test_gap_table_shows_the_paired_difference_and_a_dash_for_beam_high(self) -> None:
        gaps = tables.comparison_gap_table().set_index("Model (setting)")
        self.assertEqual(gaps.loc["Beam (high)"].tolist(), ["-"] * 5)
        self.assertEqual(gaps.loc["GPT-OSS-120B (high)", "Case 3 base"], "+12.6 [+3.0, +21.5]")
        self.assertEqual(gaps.loc["Qwen3.5-397B-A17B (thinking on)", "Case 2 P5"], "-47.7 [-61.4, -33.7]")

    def test_sentences_cover_every_suite(self) -> None:
        sentences = tables.comparison_sentences(self.bundle)
        self.assertEqual(len(sentences), 5)
        self.assertTrue(all("GPT-OSS-120B (high)" in s and "Qwen3.5-397B-A17B" in s for s in sentences))


class CardWording(unittest.TestCase):
    def test_the_card_has_the_sections_the_challenge_rules_ask_for(self) -> None:
        for heading in ("## How Beam was used", "## Results at a glance", "## Model comparison", "# har2client:"):
            self.assertIn(heading, CARD)
        for fact in ("Beam-1.0-501B-A23B", "https://api.reflection.ai/openai/v1", "reasoning_effort", "reasoning_content", "no LLM judge", "mean of 3 runs"):
            self.assertIn(fact, CARD)

    def test_the_card_shows_the_two_figures_with_the_export_path(self) -> None:
        self.assertIn("](results/figures/final_scoreboard.png)", CARD)
        self.assertIn("](results/figures/final_leaderboard.png)", CARD)

    def test_case2_wording_points_at_the_released_results(self) -> None:
        self.assertNotIn("probe config not in this release", CARD)
        self.assertNotIn("Not in this release as a config", CARD)
        self.assertIn("same 50 Tier B task rows", CARD)
        self.assertIn("results/case2_probe/", CARD)

    def test_the_comparison_section_does_not_use_p20(self) -> None:
        section = CARD.split("## Model comparison", 1)[1].split("\n## ", 1)[0]
        table_and_notes = re.sub(r"\*Case 2\.\*[^\n]*", "", section)  # the one note that says where Beam's own P20 results are described
        self.assertNotIn("P20", table_and_notes)
        for note in ("32,768", "65,536", "Novita", "no effort level", "1 run"):
            self.assertIn(note, section)

    def test_the_card_comparison_table_matches_the_summary_file(self) -> None:
        summary = json.loads((RESULTS_DIR / "analysis" / "model_comparison" / "summary.json").read_text(encoding="utf-8")) if RESULTS_DIR.is_dir() else None
        if summary is None:
            self.skipTest("no har2client/results")
        section = CARD.split("## Model comparison", 1)[1].split("\n## ", 1)[0]
        for row in summary["rows"]:
            self.assertIn(f"{row['pass_rate']:.1f}%", section, (row["key"], row["suite"]))

    def test_the_space_about_tab_and_readme_say_how_beam_was_used(self) -> None:
        app = (SPACE / "app.py").read_text(encoding="utf-8")
        readme = (SPACE / "README.md").read_text(encoding="utf-8")
        for text in (app, readme):
            self.assertIn("How Beam was used", text)
            self.assertIn("https://api.reflection.ai/openai/v1", text)
        self.assertIn('gr.Tab("Model comparison")', app)


if __name__ == "__main__":
    unittest.main()
