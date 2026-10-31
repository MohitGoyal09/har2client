"""The static Space builds from the committed results, shows the right numbers, and leaks nothing.

Run:  cd har2client && uv run pytest -q ../release/tests/test_static_space.py
Needs release/build/dataset (run release/export_dataset.py first); the tests skip without it.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(RELEASE / "static_space"))
sys.path.insert(0, str(RELEASE))

import build as static_build  # noqa: E402
import h2c_release  # noqa: E402

P20 = re.compile(r"probe20|\bP20\b", re.I)
HAS_DATASET = any(h2c_release.DATASET_DIR.glob("*/data.jsonl"))


@unittest.skipUnless(HAS_DATASET, "run release/export_dataset.py first")
class StaticSpace(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "static_space"
        static_build.build(cls.out)
        cls.site = json.loads((cls.out / "data" / "site.json").read_text(encoding="utf-8"))
        cls.comparison = json.loads((static_build.RESULTS / "analysis" / "model_comparison" / "summary.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_build_writes_the_expected_files(self) -> None:
        for rel in ("index.html", "README.md", "assets/style.css", "assets/app.js", "assets/og.png", "data/site.json", "data/tasks.json"):
            self.assertTrue((self.out / rel).is_file(), rel)

    def test_readme_frontmatter_is_a_static_space(self) -> None:
        text = (self.out / "README.md").read_text(encoding="utf-8")
        front = text.split("---")[1].splitlines()
        for line in ("title: HAR2Client", "sdk: static", "app_file: index.html", "pinned: false", "license: apache-2.0"):
            self.assertIn(line, front)
        self.assertTrue(any(line.startswith("short_description:") for line in front))
        self.assertNotIn("sdk: gradio", text)

    def test_no_p20_text_anywhere(self) -> None:
        for path in self.out.rglob("*"):
            if path.is_file() and path.suffix in {".html", ".js", ".css", ".json", ".md"}:
                self.assertIsNone(P20.search(path.read_text(encoding="utf-8")), str(path))

    def test_no_hidden_task_data_in_the_site(self) -> None:
        tasks = json.loads((self.out / "data" / "tasks.json").read_text(encoding="utf-8"))["tasks"]
        self.assertGreater(len(tasks), 150)
        # results / fails: per-setting pass rates and failure-kind indexes, numbers only (no task content)
        allowed = {"id", "suite", "tier", "title", "goal", "features", "n_requests", "requests", "excerpt", "results", "fails"}
        for task in tasks:
            self.assertLessEqual(set(task), allowed)
            for key in ("results", "fails"):
                for values in task.get(key, {}).values():
                    self.assertTrue(all(v is None or isinstance(v, (int, float)) for v in values), (task["id"], key))
        blob = (self.out / "data" / "tasks.json").read_text(encoding="utf-8") + (self.out / "index.html").read_text(encoding="utf-8")
        for name in h2c_release.HIDDEN_FILES:
            self.assertNotIn(name, blob)

    def test_numbers_match_the_model_comparison_summary(self) -> None:
        by = {(r["key"], r["suite"]): r for r in self.comparison["rows"]}
        picks = [("beam-high", "v3hint", "c3b"), ("beam-high", "probe5", "c2b"), ("gpt-oss-120b-low", "tierb50", "c1"),
                 ("qwen3.5-397b-thinking", "v3base", "c3a"), ("beam-low", "probe0", "c2a")]
        for key, suite, bid in picks:
            row, cell = by[(key, suite)], self.site["cells"][key][bid]
            self.assertAlmostEqual(cell["pass"], row["pass_rate"], places=9)
            self.assertAlmostEqual(cell["lo"], row["ci_low"], places=9)
            self.assertAlmostEqual(cell["hi"], row["ci_high"], places=9)
            self.assertAlmostEqual(cell["tokens"], row["mean_output_tokens"], places=6)

    def test_every_setting_has_every_benchmark(self) -> None:
        self.assertEqual(len(self.site["settings"]), 7)
        self.assertEqual(len(self.site["benchmarks"]), 5)
        for setting in self.site["settings"]:
            self.assertEqual(set(self.site["cells"][setting["key"]]), {b["id"] for b in self.site["benchmarks"]})

    def test_signature_numbers_come_from_the_analysis(self) -> None:
        controls = json.loads((static_build.RESULTS / "analysis" / "heal_controls" / "summary.json").read_text(encoding="utf-8"))
        sig = {s["id"]: s for s in self.site["signature"]}
        self.assertAlmostEqual(sig["case1"]["withheld"], controls["h10"]["steps"]["a"]["pass_rate"] * 100, places=9)
        self.assertAlmostEqual(sig["case1"]["supplied"][1]["value"], controls["h10"]["steps"]["c"]["pass_rate"] * 100, places=9)
        self.assertAlmostEqual(sig["case2"]["supplied"][0]["value"], self.site["cells"]["beam-high"]["c2b"]["pass"], places=9)
        self.assertAlmostEqual(sig["case3"]["withheld"], self.site["cells"]["beam-high"]["c3a"]["pass"], places=9)

    def test_qwen_is_never_given_an_effort_level(self) -> None:
        qwen = [s for s in self.site["settings"] if s["model"] == "qwen"]
        self.assertEqual(len(qwen), 1)
        self.assertIsNone(qwen[0]["effort"])

    def test_verdicts_are_read_from_the_summaries(self) -> None:
        status = {v["id"]: v["status"] for v in self.site["verdicts"]}
        self.assertEqual(status["H10"], "supported")
        self.assertEqual(status["H17"], "rejected")
        self.assertEqual(status["H22"], "supported")

    def test_javascript_stays_small_and_uses_no_inner_html(self) -> None:
        js = (self.out / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertLess(len(js.encode()), 60 * 1024)
        self.assertNotIn("innerHTML", js)

    def test_page_has_one_h1_and_the_required_links(self) -> None:
        page = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertEqual(page.count("<h1"), 1)
        for url in (static_build.DATASET_URL, static_build.CODE_URL, static_build.PREREG_URL):
            self.assertIn(url, page)
        self.assertIn('property="og:image"', page)


if __name__ == "__main__":
    unittest.main()
