"""Tests for the case 3 v3 evaluation suite in the release (configs `heal_v3` and `heal_v3_hint`).

Run:  python3 -m unittest discover -s release/tests -v   (standard library only; no network)
They copy two real task dirs (one with a CHANGELOG.md) from har2client/runs/heal_v3_eval and its hint arm.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))

import check_no_leak  # noqa: E402
import export_dataset  # noqa: E402
import publish_hf  # noqa: E402
import sync_space_data  # noqa: E402
import validate_croissant  # noqa: E402
from h2c_release import H2C_DIR  # noqa: E402

BASE = H2C_DIR / "runs" / "heal_v3_eval"
HINT = H2C_DIR / "runs" / "heal_v3_eval_hint"
TIER_B = H2C_DIR / "runs" / "tierb50"
SINGLE = "v3_single_00_cents_tickets"
CHANGELOG = "v3_misleading_48_enum+refresh_rotation+idempotency_key_shipments"
TASKS = (SINGLE, CHANGELOG)
TIER_B_TASK = "tierb_00_invoices"


@unittest.skipUnless(BASE.is_dir() and HINT.is_dir() and TIER_B.is_dir(), "needs har2client/runs/heal_v3_eval, _hint and tierb50")
class HealV3Release(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.base, self.hint, self.tier_b = self.tmp / "base", self.tmp / "hint", self.tmp / "tier_b"
        for name in TASKS:
            shutil.copytree(BASE / name, self.base / name)
            shutil.copytree(HINT / name, self.hint / name)
        shutil.copytree(TIER_B / TIER_B_TASK, self.tier_b / TIER_B_TASK)
        self.out = self.tmp / "out"

    def export(self, v3: bool = True) -> int:
        args = ["--out", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(self.tier_b)]
        if v3:
            args += ["--heal-v3", str(self.base), "--heal-v3-hint", str(self.hint)]
        with contextlib.redirect_stdout(io.StringIO()):
            return export_dataset.main(args)

    def check(self, extra: list[Path] | None = None) -> tuple[int, str]:
        args = ["--dataset", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(self.tier_b),
                "--heal-v3", str(self.base), "--heal-v3-hint", str(self.hint), "--extra", *map(str, extra or [])]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = check_no_leak.main(args)
        return rc, buf.getvalue()

    def rows(self, config: str) -> list[dict]:
        return [json.loads(x) for x in (self.out / config / "data.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_both_arms_are_exported_with_the_visible_files_only(self) -> None:
        self.assertEqual(self.export(), 0)
        for config, arm in (("heal_v3", "base"), ("heal_v3_hint", "hint")):
            rows = self.rows(config)
            self.assertEqual([r["task_id"] for r in rows], sorted(TASKS))
            for r in rows:
                self.assertEqual(r["case"], "heal")
                self.assertEqual(r["metadata"]["arm"], arm)
                self.assertLessEqual(set(r), check_no_leak.ALLOWED_COLUMNS)
                self.assertIn("class", r["client_v1"])
                self.assertTrue(r["failure_txt"])
        rc, out = self.check()
        self.assertEqual(rc, 0, out)

    def test_changelog_is_a_column_and_none_when_the_task_has_no_changelog(self) -> None:
        self.export()
        by_id = {r["task_id"]: r for r in self.rows("heal_v3")}
        self.assertEqual(by_id[CHANGELOG]["changelog_md"], (BASE / CHANGELOG / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertIsNone(by_id[SINGLE]["changelog_md"])

    def test_hint_arm_goal_is_the_base_goal_plus_one_paragraph(self) -> None:
        self.export()
        base = {r["task_id"]: r for r in self.rows("heal_v3")}
        hint = {r["task_id"]: r for r in self.rows("heal_v3_hint")}
        for task in TASKS:
            self.assertNotEqual(base[task]["goal_md"], hint[task]["goal_md"])
            self.assertEqual(len(hint[task]["goal_md"].split("\n\n")), len(base[task]["goal_md"].split("\n\n")) + 1)
            for column in ("observation", "client_v1", "failure_txt", "changelog_md"):
                self.assertEqual(base[task][column], hint[task][column], column)

    def test_metadata_has_family_and_design_labels_but_no_seeds_or_changelog_truth(self) -> None:
        self.export()
        row = {r["task_id"]: r for r in self.rows("heal_v3")}[CHANGELOG]
        meta = row["metadata"]
        self.assertEqual((meta["set"], meta["changes"], meta["changelog"], meta["client_style"]), ("misleading", 3, "misleading", "compact"))
        self.assertEqual(meta["mutation"], "add_drift_enum+add_refresh_rotation+add_idempotency_key")
        info = json.loads((BASE / CHANGELOG / "task.json").read_text(encoding="utf-8"))
        text = json.dumps(row)
        for hidden in (str(info["seeds"]["test"]), str(info["seeds"]["recording"]), info["credentials"]["test"]["password"],
                       info["heal"]["changelog_info"]["misleading"]["truth"], "changelog_info", "necessity"):
            self.assertNotIn(hidden, text)

    def test_existing_configs_are_byte_identical_with_and_without_v3(self) -> None:
        self.export(v3=False)
        before = (self.out / "tier_b" / "data.jsonl").read_bytes()
        self.export(v3=True)
        self.assertEqual((self.out / "tier_b" / "data.jsonl").read_bytes(), before)

    def test_without_v3_dirs_the_configs_are_placeholders(self) -> None:
        self.export(v3=False)
        manifest = json.loads((self.out / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["configs"]["heal_v3"]["rows"], 0)
        self.assertFalse((self.out / "heal_v3_hint").exists())

    def test_card_and_croissant_list_the_v3_configs(self) -> None:
        self.export()
        head = (self.out / "README.md").read_text(encoding="utf-8").split("END-CONFIGS")[0]
        self.assertIn("config_name: heal_v3\n", head)
        self.assertIn("config_name: heal_v3_hint", head)
        doc = json.loads((self.out / "croissant.json").read_text(encoding="utf-8"))
        self.assertEqual({rs["@id"] for rs in doc["recordSet"]}, {"tier_b", "heal_v3", "heal_v3_hint"})
        errors, _ = validate_croissant.check(doc, False, self.out)
        self.assertEqual(errors, [])
        for rs in doc["recordSet"]:
            row = self.rows(rs["@id"])[0]
            self.assertEqual(set(row) - {f["name"] for f in rs["field"]}, set(), rs["@id"])
            for key in row["metadata"]:
                self.assertIn(key, {sub["name"] for f in rs["field"] if f["name"] == "metadata" for sub in f["subField"]})

    def test_space_tasks_json_lists_both_v3_arms_under_distinct_keys(self) -> None:
        # the hint arm reuses the base task ids, so it is keyed "<task id>#hint" and does not overwrite the base arm
        self.export()
        space = self.tmp / "space_data"
        with mock.patch.object(sync_space_data, "DATASET_DIR", self.out), mock.patch.object(sync_space_data, "SPACE_DATA", space), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync_space_data.main(), 0)
        tasks = json.loads((space / "tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(tasks), sorted([TIER_B_TASK, *TASKS, *(t + "#hint" for t in TASKS)]))
        self.assertEqual({k: v["suite"] for k, v in tasks.items() if k in TASKS}, {t: "heal_v3" for t in TASKS})
        self.assertEqual({v["suite"] for k, v in tasks.items() if k.endswith("#hint")}, {"heal_v3_hint"})
        self.assertNotEqual(tasks[SINGLE]["goal_md"], tasks[SINGLE + "#hint"]["goal_md"])
        self.assertEqual(self.check([space])[0], 0)  # the Space task listing passes the leak check

    def test_leak_check_reads_the_changelog_as_a_visible_file(self) -> None:
        # a hidden test value that also sits in the visible CHANGELOG.md is visible by design, not a leak
        visible = check_no_leak.visible_text(self.base / CHANGELOG)
        self.assertIn("Breaking changes", visible)

    def test_leak_check_catches_a_hidden_value_in_the_changelog_column(self) -> None:
        self.export()
        tests = json.loads((self.base / SINGLE / "tests.json").read_text(encoding="utf-8"))
        visible = check_no_leak.visible_text(self.base / SINGLE)
        hidden = next(v for t in tests for v in check_no_leak.leaves(t.get("kwargs", {}), str) if len(v) >= 8 and v not in visible)
        path = self.out / "heal_v3" / "data.jsonl"
        rows = self.rows("heal_v3")
        for r in rows:
            if r["task_id"] == SINGLE:
                r["changelog_md"] = f"## Notes\n{hidden}\n"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        rc, out = self.check()
        self.assertEqual(rc, 1, out)

    def test_leak_check_fails_for_v3_rows_whose_task_dir_was_not_given(self) -> None:
        self.export()
        args = ["--dataset", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(self.tier_b), "--extra"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            self.assertEqual(check_no_leak.main(args), 1)
        self.assertIn(SINGLE, buf.getvalue())


class PublishDryRunListsFiles(unittest.TestCase):
    def test_dry_run_prints_every_file_with_its_size_and_a_total(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name, files in (("d", {"README.md": "ab", "heal_v3/data.jsonl": "x" * 10}), ("s", {"app.py": "y" * 3})):
            for rel, text in files.items():
                (tmp / name / rel).parent.mkdir(parents=True, exist_ok=True)
                (tmp / name / rel).write_text(text, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = publish_hf.main(["--namespace", "acme", "--dataset-dir", str(tmp / "d"), "--space-dir", str(tmp / "s"), "--dry-run"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("heal_v3/data.jsonl", out)
        self.assertRegex(out, r"heal_v3/data\.jsonl\s+10 bytes")
        self.assertIn("total 12 bytes", out)
        self.assertIn("total 3 bytes", out)


if __name__ == "__main__":
    unittest.main()
