"""Tests for the export and the leak check. Run:  python3 -m unittest discover -s release/tests -v

They use two real Tier B task dirs from har2client/runs/tierb50 (copied to a temp dir) and need no network.
"""

from __future__ import annotations

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
from h2c_release import H2C_DIR  # noqa: E402

SOURCE = H2C_DIR / "runs" / "tierb50"
TASKS = ("tierb_00_invoices", "tierb_01_invoices")


@unittest.skipUnless(SOURCE.is_dir(), "needs har2client/runs/tierb50")
class ExportAndLeakCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tasks = self.tmp / "tasks"
        self.tasks.mkdir()
        for name in TASKS:
            shutil.copytree(SOURCE / name, self.tasks / name)
        self.out = self.tmp / "out"

    def export(self, **dirs: Path) -> int:
        args = ["--out", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(self.tasks)]
        for key, path in dirs.items():
            args += [f"--{key}", str(path)]
        return export_dataset.main(args)

    def check(self, **dirs: Path) -> int:
        args = ["--dataset", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(self.tasks), "--extra"]
        for key, path in dirs.items():
            args += [f"--{key}", str(path)]
        return check_no_leak.main(args)

    def rows(self, config: str = "tier_b") -> list[dict]:
        return [json.loads(x) for x in (self.out / config / "data.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_rows_have_no_hidden_columns_and_carry_the_canary(self) -> None:
        self.assertEqual(self.export(), 0)
        rows = self.rows()
        self.assertEqual([r["task_id"] for r in rows], sorted(TASKS))
        for r in rows:
            self.assertLessEqual(set(r), check_no_leak.ALLOWED_COLUMNS)
            self.assertIn(check_no_leak.canary_guid(), r["canary"])
            self.assertEqual(r["observation_format"], "har")
        self.assertEqual(self.check(), 0)

    def test_placeholders_are_empty(self) -> None:
        self.export()
        self.assertFalse((self.out / "probe").exists())
        manifest = json.loads((self.out / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["configs"]["heal"]["rows"], 0)
        self.assertNotIn("probe:", (self.out / "README.md").read_text(encoding="utf-8").split("END-CONFIGS")[0].split("BEGIN-CONFIGS")[1])

    def test_leak_check_catches_test_credential(self) -> None:
        self.export()
        info = json.loads((self.tasks / TASKS[0] / "task.json").read_text(encoding="utf-8"))
        path = self.out / "tier_b" / "data.jsonl"
        rows = self.rows()
        rows[0]["goal_md"] += "\npassword: " + info["credentials"]["test"]["password"]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertEqual(self.check(), 1)

    def test_leak_check_catches_test_seed_and_hidden_test_value(self) -> None:
        self.export()
        info = json.loads((self.tasks / TASKS[0] / "task.json").read_text(encoding="utf-8"))
        tests = json.loads((self.tasks / TASKS[0] / "tests.json").read_text(encoding="utf-8"))
        hidden_id = next(t["kwargs"]["id"] for t in tests if "id" in t["kwargs"])
        path = self.out / "tier_b" / "data.jsonl"
        for needle in (str(info["seeds"]["test"]), hidden_id):
            rows = self.rows()
            rows[0]["features"].append(f"x:{needle}")
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            self.assertEqual(self.check(), 1, needle)
            self.export()

    def test_leak_check_catches_missing_canary_and_extra_column(self) -> None:
        self.export()
        path = self.out / "tier_b" / "data.jsonl"
        rows = self.rows()
        rows[0]["canary"] = ""
        rows[1]["tests"] = []
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertEqual(self.check(), 1)

    def test_heal_dir_is_accepted_with_extra_visible_files(self) -> None:
        heal = self.tmp / "heal"
        shutil.copytree(self.tasks / TASKS[0], heal / "heal_00")
        (heal / "heal_00" / "client_v1.py").write_text("class Client:\n    pass\n", encoding="utf-8")
        (heal / "heal_00" / "failure.txt").write_text("Call 1 failed\n", encoding="utf-8")
        self.assertEqual(self.export(heal=heal), 0)
        row = self.rows("heal")[0]
        self.assertEqual(row["case"], "heal")
        self.assertIn("class Client", row["client_v1"])
        self.assertEqual(self.check(heal=heal), 0)


if __name__ == "__main__":
    unittest.main()
