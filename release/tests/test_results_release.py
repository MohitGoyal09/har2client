"""Tests for the `results/` folder in the dataset, the dropped Tier A specs and the Space data sync.

Run:  python3 -m unittest discover -s release/tests -v   (standard library only; no network)
"""

from __future__ import annotations

import contextlib
import filecmp
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

import check_no_leak  # noqa: E402
import export_dataset  # noqa: E402
import sync_space_data  # noqa: E402
from h2c_release import H2C_DIR, RESULTS_DIR, RESULTS_FILES, SPACE_FIGURES, results_figure_names  # noqa: E402

SET30 = H2C_DIR / "runs" / "set30"
TIER_B = H2C_DIR / "runs" / "tierb50"
DROPPED = ("apiz.ebay.com_sell-finances", "nfusionsolutions.biz", "surevoip.co.uk")  # restricted spec licence


class Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


@unittest.skipUnless(RESULTS_DIR.is_dir() and TIER_B.is_dir(), "needs har2client/results and har2client/runs/tierb50")
class ResultsFolder(Tmp):
    def export(self) -> Path:
        out = self.tmp / "out"
        task = self.tmp / "tasks" / "tierb_00_invoices"
        shutil.copytree(TIER_B / "tierb_00_invoices", task)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(export_dataset.main(["--out", str(out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(task.parent)]), 0)
        return out

    def test_every_listed_result_is_copied_and_recorded_in_the_manifest(self) -> None:
        out = self.export()
        manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
        listed = {e["path"] for e in manifest["results"]}
        for name in (*RESULTS_FILES, *results_figure_names()):
            self.assertIn(f"results/{name}", listed)
            self.assertTrue((out / "results" / name).is_file(), name)

    def test_the_six_v3_csvs_and_the_requested_figures_are_in(self) -> None:
        out = self.export()
        for arm in ("base", "hint"):
            for effort in ("low", "medium", "high"):
                self.assertTrue((out / "results" / f"heal_v3_eval_{arm}_{effort}.csv").is_file())
        for name in ("heal_v3_eval_frontier.png", "tierb50_frontier.png", "heal_v3_eval_families.png", "models_leaderboard.png", "story_effort_curves.png"):
            self.assertTrue((out / "results" / "figures" / name).is_file(), name)
        for name in ("heal_v3_eval_v1_floor.json", "tierb50_validity.json", "analysis/heal_controls/report.md", "analysis/tierb50_effort/summary.json"):
            self.assertTrue((out / "results" / name).is_file(), name)

    def test_copies_are_exact_except_for_the_listed_rewrites(self) -> None:
        out = self.export()
        for name in RESULTS_FILES:
            if name in export_dataset.RESULTS_REWRITES:
                continue
            self.assertTrue(filecmp.cmp(RESULTS_DIR / name, out / "results" / name, shallow=False), name)

    def test_leak_check_scans_the_results_folder_and_a_planted_secret_fails(self) -> None:
        out = self.export()
        args = ["--dataset", str(out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(self.tmp / "tasks"), "--extra"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_no_leak.main(args), 0)
        planted = out / "results" / "analysis" / "heal_v3_eval" / "report.md"
        planted.write_text(planted.read_text(encoding="utf-8") + "\nhf_" + "A1b2C3d4" * 4 + "\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_no_leak.main(args), 1)

    def test_a_missing_result_file_stops_the_export(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(SystemExit):
            export_dataset.copy_results(self.tmp / "o", empty)


@unittest.skipUnless(SET30.is_dir(), "needs har2client/runs/set30")
class RestrictedSpecsAreDropped(Tmp):
    def test_drop_restricted_leaves_27_tier_a_rows_and_keeps_the_undeclared_ones(self) -> None:
        out = self.tmp / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = export_dataset.main(["--out", str(out), "--tier-a", str(SET30), "--tier-b", str(self.tmp / "none"), "--drop-spec-class", "restricted"])
        self.assertEqual(rc, 0)
        rows = [json.loads(x) for x in (out / "tier_a" / "data.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 27)
        classes = sorted({r["spec_license_class"] for r in rows})
        self.assertEqual(classes, ["permissive", "undeclared"])
        self.assertEqual(sum(r["spec_license_class"] == "undeclared" for r in rows), 20)
        self.assertFalse({r["task_id"] for r in rows} & set(DROPPED))
        manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["configs"]["tier_a"]["rows"], 27)
        self.assertEqual(manifest["dropped_spec_classes"], ["restricted"])

    def test_publish_sh_passes_the_drop_flag(self) -> None:
        self.assertIn("--drop-spec-class restricted", (RELEASE / "publish.sh").read_text(encoding="utf-8"))


@unittest.skipUnless(RESULTS_DIR.is_dir() and TIER_B.is_dir(), "needs har2client/results and har2client/runs/tierb50")
class SpaceDataSync(Tmp):
    def test_space_csvs_equal_the_results_csvs_after_a_sync(self) -> None:
        dest = self.tmp / "space_data"
        sync_space_data.copy_results(dest)
        source = sorted(RESULTS_DIR.glob("*.csv"))
        self.assertGreaterEqual(len(source), 8)
        self.assertEqual(sorted(p.name for p in (dest / "results").glob("*.csv")), [p.name for p in source])
        for path in source:
            self.assertTrue(filecmp.cmp(path, dest / "results" / path.name, shallow=False), path.name)
        for name in SPACE_FIGURES:
            self.assertTrue(filecmp.cmp(RESULTS_DIR / "figures" / name, dest / "figures" / name, shallow=False), name)
        self.assertTrue(filecmp.cmp(RESULTS_DIR / "analysis" / "heal_v3_eval" / "summary.json", dest / "analysis" / "heal_v3_eval" / "summary.json", shallow=False))

    def test_generated_space_data_is_git_ignored(self) -> None:
        self.assertIn("space/data/", (RELEASE / ".gitignore").read_text(encoding="utf-8").splitlines())

    def test_space_readme_licence_is_apache(self) -> None:
        head = (RELEASE / "space" / "README.md").read_text(encoding="utf-8").split("---")[1]
        self.assertIn("license: apache-2.0", head)


if __name__ == "__main__":
    unittest.main()
