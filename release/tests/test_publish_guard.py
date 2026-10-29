"""The publish guard: a real publish stops while placeholder markers are in the card, croissant.json or the Space README.

Run:  python3 -m unittest discover -s release/tests -v   (standard library only; no network)
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

import publish_guard  # noqa: E402
import publish_hf  # noqa: E402

MARKERS = ("AUTHOR_2_TBD", "CONTACT_TBD", "TODO", "<code repository>", "Open decision")


class Guard(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def write(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_each_marker_is_found_with_its_line_number(self) -> None:
        path = self.write("card.md", "\n".join(["clean", *(f"line with {m}" for m in MARKERS)]))
        found = publish_guard.find_markers([path])[str(path)]
        self.assertEqual({marker for _, marker in found}, {"TBD", "TODO", "<code repository>", "Open decision"})
        self.assertIn((2, "TBD"), found)

    def test_clean_files_pass_a_real_publish(self) -> None:
        path = self.write("card.md", "Authors: Jane Doe. Contact: jane@example.org\n")
        self.assertEqual(publish_guard.check([path, self.tmp / "missing.json"], dry_run=False), 0)

    def test_real_publish_is_refused_and_lists_the_markers(self) -> None:
        path = self.write("card.md", "Contact: CONTACT_TBD\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(publish_guard.check([path], dry_run=False), 1)
        self.assertIn("TBD", err.getvalue())
        self.assertIn(f"{path}:1", err.getvalue())

    def test_dry_run_only_warns(self) -> None:
        path = self.write("card.md", "Contact: CONTACT_TBD\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(publish_guard.check([path], dry_run=True), 0)
        self.assertIn("WARNING", out.getvalue())
        self.assertIn("TBD", out.getvalue())

    def test_publish_hf_refuses_before_reading_the_token_or_importing_the_hub(self) -> None:
        for name in ("d", "s"):
            (self.tmp / name).mkdir()
        (self.tmp / "d" / "README.md").write_text("Open decision for the maintainers\n", encoding="utf-8")
        (self.tmp / "s" / "README.md").write_text("fine\n", encoding="utf-8")
        argv = ["--namespace", "acme", "--dataset-dir", str(self.tmp / "d"), "--space-dir", str(self.tmp / "s")]
        err = io.StringIO()
        # a module that raises on import proves that no network client was loaded
        with mock.patch.dict(os.environ, {"HF_TOKEN": "hf_" + "x" * 30}), mock.patch.dict(sys.modules, {"huggingface_hub": None}), \
                contextlib.redirect_stderr(err):
            self.assertEqual(publish_hf.main(argv), 1)
        self.assertIn("Open decision", err.getvalue())

    def test_publish_hf_dry_run_still_works_and_warns(self) -> None:
        for name in ("d", "s"):
            (self.tmp / name).mkdir()
            (self.tmp / name / "README.md").write_text("TODO\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = publish_hf.main(["--namespace", "acme", "--dataset-dir", str(self.tmp / "d"), "--space-dir", str(self.tmp / "s"), "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("WARNING", out.getvalue())

    def test_card_has_no_open_todos_or_code_repository_placeholder(self) -> None:
        # only the owner markers (AUTHOR_2_TBD, CONTACT_TBD) may remain; they are filled in by hand before the real publish
        card = (RELEASE / "DATASET_CARD.md").read_text(encoding="utf-8")
        for banned in ("TODO", "<code repository>", "Open decision"):
            self.assertNotIn(banned, card)

    def test_publish_sh_refuses_a_real_publish_before_any_network_call(self) -> None:
        if not publish_guard.find_markers(publish_guard.SOURCES):
            self.skipTest("no markers left in the release sources (the markers were filled in)")
        env = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
        r = subprocess.run(["bash", str(RELEASE / "publish.sh"), "no-such-namespace-xyz"], capture_output=True, text=True, env=env, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to publish", r.stderr)
        self.assertIn("TBD", r.stderr)
        self.assertNotIn("1/8", r.stdout)  # stopped before the build, so before any upload step


if __name__ == "__main__":
    unittest.main()
