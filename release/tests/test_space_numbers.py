"""The Space's numbers must match the committed analysis. Needs pandas (installed with gradio).

Run:  /path/to/venv/bin/python -m unittest discover -s release/tests -v
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

SPACE = Path(__file__).resolve().parents[1] / "space"
sys.path.insert(0, str(SPACE))
sys.path.insert(0, str(SPACE.parent))

import h2c_release  # noqa: E402

try:
    import tables  # noqa: E402
except ImportError:  # pandas missing
    tables = None


@unittest.skipIf(tables is None, "pandas not installed")
class SpaceMatchesAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = tables.load_bundle()
        cls.summary = tables.data_path("analysis", "tierb50_v1", "summary.md").read_text(encoding="utf-8")

    def test_beam_effort_table_matches_the_effort_summary_and_the_comparison_summary(self) -> None:
        table = tables.beam_effort_table(self.bundle).set_index("Beam, arm")
        effort = json.loads(tables.data_path("analysis", "tierb50_effort", "summary.json").read_text(encoding="utf-8")) if tables.data_path("analysis", "tierb50_effort", "summary.json").is_file() else json.loads((h2c_release.RESULTS_DIR / "analysis" / "tierb50_effort" / "summary.json").read_text(encoding="utf-8"))
        on = next(x for x in effort["switches"] if x["switch"] == "hidden_header" and x["level"] == "on")["by_effort"]
        for e in ("low", "medium", "high"):
            self.assertAlmostEqual(table.loc["Case 1: header hidden (25 tasks)", e], tables.r1(on[e]["pass_rate"] * 100), places=1)
        rows = {(r["key"], r["suite"]): r for r in tables.comparison_summary()["rows"]}
        for e in ("low", "medium", "high"):
            self.assertAlmostEqual(table.loc["Case 2: up to 5 probes (50 tasks)", e], tables.r1(rows[(f"beam-{e}", "probe5")]["pass_rate"]), places=1)
            self.assertAlmostEqual(table.loc["Case 3: with hint (64 tasks)", e], tables.r1(rows[(f"beam-{e}", "v3hint")]["pass_rate"]), places=1)

    def test_tier_b_effort_means_and_intervals_match_the_analysis(self) -> None:
        # low is one run: it equals the run-1 summary.md. High is the mean of 3 runs: it equals analysis/tierb50_effort/summary.json
        # (the Space bootstrap uses 5,000 resamples against 10,000 there, so the interval ends agree to about a point).
        row = re.search(r"\| low \| ([\d.]+) \[([\d.]+), ([\d.]+)\] \|", self.summary)
        mean, lo, hi = (float(x) for x in row.groups())
        got = self.bundle.summaries["B"]["low"]
        self.assertAlmostEqual(tables.r1(got["mean_pct"]), mean, places=1)
        self.assertAlmostEqual(got["ci_pct"][0], lo, delta=1.0)
        self.assertAlmostEqual(got["ci_pct"][1], hi, delta=1.0)
        effort = json.loads(tables.data_path("analysis", "tierb50_effort", "summary.json").read_text(encoding="utf-8")) if tables.data_path("analysis", "tierb50_effort", "summary.json").is_file() else None
        if effort is None:  # the Space bundle holds only some analysis files; the repo has all of them
            effort = json.loads((h2c_release.RESULTS_DIR / "analysis" / "tierb50_effort" / "summary.json").read_text(encoding="utf-8"))
        for name in ("low", "medium", "high"):
            step = effort["steps"][name]["pass_rate"]
            got = self.bundle.summaries["B"][name]
            self.assertAlmostEqual(got["mean_pct"], step["mean"] * 100, places=1, msg=name)
            self.assertAlmostEqual(got["ci_pct"][0], step["ci_low"] * 100, delta=1.0, msg=name)
            self.assertAlmostEqual(got["ci_pct"][1], step["ci_high"] * 100, delta=1.0, msg=name)

    def test_effort_contrast_matches_the_analysis(self) -> None:
        # High minus low with high = mean of 3 runs: +17.3 [+9.1, +26.7] in analysis/tierb50_effort/report.md.
        # (summary.md is run 1 only and gives +20.9.)
        report = (h2c_release.RESULTS_DIR / "analysis" / "tierb50_effort" / "report.md").read_text(encoding="utf-8")
        row = re.search(r"high \(mean of 3 runs\)-low \| 50 \| \+([\d.]+) \[\+([\d.]+), \+([\d.]+)\]", report)
        gain, lo, hi = (float(x) for x in row.groups())
        got = self.bundle.contrasts["B"]
        self.assertAlmostEqual(tables.r1(got["gain_pp"]), gain, places=1)
        self.assertAlmostEqual(got["gain_ci"][0], lo, delta=1.0)
        self.assertAlmostEqual(got["gain_ci"][1], hi, delta=1.0)

    def test_leaderboard_has_one_row_per_tier_and_effort(self) -> None:
        board = tables.leaderboard(self.bundle)
        # A: low, high (30 tasks run) + the same for the published tasks; B: 3 efforts; case 3: 3 efforts in each of 2 arms
        self.assertEqual(len(board), 2 + (2 if self.bundle.published_a else 0) + 3 + 3 + 3)
        self.assertEqual(set(board["Effort"]), {"low", "medium", "high"})
        tier_b = board[board["Tier"].str.startswith("Tier B")]
        self.assertEqual(sorted(tier_b["Effort"]), ["high", "low", "medium"])
        self.assertAlmostEqual(float(tier_b[tier_b["Effort"] == "medium"]["Mean pass rate (%)"].iloc[0]), 63.7, places=1)  # results/README.md

    def test_case3_table_matches_the_committed_analysis(self) -> None:
        summary = self.bundle.heal_summary
        table = tables.case3_table(self.bundle)
        self.assertEqual(len(table), 6)
        for (_, row), (arm, effort) in zip(table.iterrows(), [(a, e) for a in ("base", "hint") for e in ("low", "medium", "high")]):
            step = summary["steps"][f"{arm}-{effort}"]
            self.assertAlmostEqual(row["Mean pass rate (%)"], tables.r1(step["pass_rate"] * 100), places=1)
            if effort != "high":  # at high, "full repair" counts a task only when all 3 runs pass all 17 tests; full_repair_share is the share over runs
                self.assertEqual(row["Full repair"], f"{round(step['full_repair_share'] * step['n_scored'])}/{step['n_scored']}")
            self.assertEqual(row["Output tokens / task"], round(step["mean_output_tokens"]))

    def test_case3_family_table_matches_the_committed_analysis(self) -> None:
        summary = self.bundle.heal_summary
        fam = tables.case3_family_table(self.bundle).set_index("Family")
        for arm in ("base", "hint"):
            for effort in ("low", "medium", "high"):
                for family, row in summary["families"][f"{arm}-{effort}"].items():
                    self.assertAlmostEqual(fam.loc[family, f"{arm} {effort}"], tables.r1(row["pass_rate"] * 100), places=1)

    def test_case3_floor_and_gaps_come_from_the_files(self) -> None:
        self.assertIn("6.5%", tables.case3_floor_sentence(self.bundle))
        gaps = tables.case3_gap_table(self.bundle)
        self.assertEqual(gaps.iloc[0]["Points"], "+40.0")
        self.assertEqual(gaps.iloc[0]["95% CI"], "[29.4, 50.1]")

    def test_published_tier_a_summary_leaves_out_the_dropped_tasks(self) -> None:
        if not self.bundle.published_a:
            self.skipTest("no tasks.json (run release/sync_space_data.py)")
        self.assertEqual(self.bundle.published_a["low"]["n_tasks"], 27)
        self.assertAlmostEqual(self.bundle.published_a["low"]["mean_pct"], 95.83, places=2)
        self.assertAlmostEqual(self.bundle.published_a["high"]["mean_pct"], 99.54, places=2)

    def test_findings_figures_are_the_synced_ones(self) -> None:
        source = (SPACE / "app.py").read_text(encoding="utf-8")  # app.py needs gradio, so read its source instead of importing it
        shown = re.findall(r'^\s+\("((?:story|final)_[a-z_]+\.png)", "[^"]+"\),$', source, re.M)
        self.assertEqual(shown, list(h2c_release.SPACE_FIGURES))
        for name in shown:
            self.assertTrue((h2c_release.RESULTS_DIR / "figures" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
