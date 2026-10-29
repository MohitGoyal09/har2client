"""Regression tests from the release review: leak check strength, publish scripts, Croissant checks, Space sync.

Run:  python3 -m unittest discover -s release/tests -v   (standard library only; no network)
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
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

SOURCE = H2C_DIR / "runs" / "tierb50"
TASKS = ("tierb_00_invoices", "tierb_01_invoices")
HF_LIKE = "hf_" + "A1b2C3d4E5" * 3 + "wxyz"  # 37 chars, built at run time so no token-looking literal sits in the repo
JWT_LIKE = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcDEF123_-xyz"


def kinds(errors: list[str]) -> str:
    return "\n".join(errors)


class HiddenFileNames(unittest.TestCase):
    def test_longer_file_names_that_end_in_a_hidden_name_are_not_a_leak(self) -> None:
        text = "see `har2client/results/set30_v1_regrade_newtests.json` and my_task.json"
        self.assertEqual(check_no_leak.scan_text("card", text, []), [])

    def test_real_mentions_are_still_a_leak(self) -> None:
        for text in ("open tests.json", "`task.json`", "runs/set30/x/ref_client.py", "tests.json."):
            self.assertTrue(check_no_leak.scan_text("card", text, []), text)


class SecretPatterns(unittest.TestCase):
    def test_jwt_like_and_hub_tokens_are_caught_without_needles(self) -> None:
        for secret in (JWT_LIKE, HF_LIKE, "sk-" + "a1b2c3d4" * 4, "AKIA" + "ABCDEFGHIJKLMNOP",
                       "-----BEGIN RSA PRIVATE KEY-----", "ghp_" + "a" * 36):
            errors = check_no_leak.scan_text("f", f"value: {secret} end", [])
            self.assertTrue(errors, secret)
            self.assertNotIn(secret, kinds(errors))  # the message must not echo the secret

    def test_synthetic_mock_credentials_are_not_flagged(self) -> None:
        text = "Authorization: Bearer sk_7f9fdc502c0d4132047446b6 user_6632ee41 pw_54e78cf33ba735c3"
        self.assertEqual(check_no_leak.scan_text("f", text, []), [])

    def test_env_secret_values_are_caught_and_not_printed(self) -> None:
        value = "Zx9" * 8
        with mock.patch.dict(os.environ, {"REFLECTION_API_KEY": value, "HF_TOKEN": ""}):
            errors = check_no_leak.scan_text("f", f"data {value} data", [])
        self.assertTrue(errors)
        self.assertNotIn(value, kinds(errors))


class NeedleMatching(unittest.TestCase):
    def test_value_with_json_escapes_is_found_in_a_row(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        value = 'abc"def\nghi'
        row = {"task_id": "t1", "goal_md": f"x {value} y", "canary": check_no_leak.canary_guid()}
        path = tmp / "tier_b" / "data.jsonl"
        path.parent.mkdir()
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        errors = check_no_leak.check_rows(path, check_no_leak.canary_guid(), [("hidden_test_value", "t1", value)], {"t1"})
        self.assertTrue(any("hidden_test_value" in e for e in errors), errors)


@unittest.skipUnless(SOURCE.is_dir(), "needs har2client/runs/tierb50")
class CheckAgainstExport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tasks = self.tmp / "tasks"
        self.tasks.mkdir()
        for name in TASKS:
            shutil.copytree(SOURCE / name, self.tasks / name)
        self.out = self.tmp / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            export_dataset.main(["--out", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(self.tasks)])

    def check(self, tasks: Path | None = None, extra: list[Path] | None = None) -> tuple[int, str]:
        buf = io.StringIO()
        args = ["--dataset", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(tasks or self.tasks), "--extra", *map(str, extra or [])]
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = check_no_leak.main(args)
        return rc, buf.getvalue()

    def test_clean_export_passes(self) -> None:
        self.assertEqual(self.check()[0], 0)

    def test_secret_in_any_exported_file_fails(self) -> None:
        (self.out / "README.md").write_text((self.out / "README.md").read_text(encoding="utf-8") + f"\n{JWT_LIKE}\n", encoding="utf-8")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertNotIn(JWT_LIKE, out)

    def test_hidden_test_value_in_space_tasks_json_fails(self) -> None:
        tests = json.loads((self.tasks / TASKS[0] / "tests.json").read_text(encoding="utf-8"))
        hidden = next(v for t in tests for v in t["kwargs"].values() if isinstance(v, str) and len(v) >= 8)
        space = self.tmp / "spacedata"
        space.mkdir()
        (space / "tasks.json").write_text(json.dumps({TASKS[0]: {"goal_md": f"x {hidden} y"}}), encoding="utf-8")
        rc, out = self.check(extra=[space])
        visible = (self.tasks / TASKS[0] / "goal.md").read_text(encoding="utf-8") + (self.tasks / TASKS[0] / "session.har").read_text(encoding="utf-8")
        if hidden in visible:
            self.skipTest("value is visible by design")
        self.assertEqual(rc, 1, out)

    def test_rows_of_a_task_that_was_not_scanned_fail(self) -> None:
        only_one = self.tmp / "one"
        only_one.mkdir()
        shutil.copytree(self.tasks / TASKS[0], only_one / TASKS[0])
        rc, out = self.check(tasks=only_one)
        self.assertEqual(rc, 1)
        self.assertIn(TASKS[1], out)

    def test_missing_canary_in_card_or_canary_file_fails(self) -> None:
        card = self.out / "README.md"
        card.write_text(card.read_text(encoding="utf-8").replace(check_no_leak.canary_guid(), "REDACTED"), encoding="utf-8")
        self.assertEqual(self.check()[0], 1)

    def test_parquet_that_cannot_be_scanned_fails(self) -> None:
        try:
            import pyarrow  # noqa: F401, PLC0415
            self.skipTest("pyarrow installed")
        except ImportError:
            pass
        (self.out / "tier_b" / "data.parquet").write_bytes(b"PAR1")
        self.assertEqual(self.check()[0], 1)


class DatasetCard(unittest.TestCase):
    def test_card_says_credentials_are_synthetic(self) -> None:
        card = (RELEASE / "DATASET_CARD.md").read_text(encoding="utf-8")
        self.assertIn("All credentials in this data are synthetic", card)
        self.assertIn("sk_", card)


class PublishHf(unittest.TestCase):
    def run_main(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, mock.MagicMock | None]:
        buf = io.StringIO()
        fake_api = mock.MagicMock()
        module = types.ModuleType("huggingface_hub")
        module.HfApi = mock.MagicMock(return_value=fake_api)
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.dict(sys.modules, {"huggingface_hub": module}), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = publish_hf.main(argv)
        return rc, buf.getvalue(), fake_api

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("d", "s"):
            (self.tmp / name).mkdir()
            (self.tmp / name / "f.txt").write_text("x", encoding="utf-8")
        self.base = ["--namespace", "acme", "--dataset-dir", str(self.tmp / "d"), "--space-dir", str(self.tmp / "s")]

    def test_upload_uses_right_repo_types_and_removes_stale_files(self) -> None:
        rc, out, api = self.run_main(self.base, {"HF_TOKEN": HF_LIKE})
        self.assertEqual(rc, 0, out)
        types_ids = [(c.kwargs["repo_type"], c.kwargs["repo_id"]) for c in api.create_repo.call_args_list]
        self.assertEqual(types_ids, [("dataset", "acme/har2client"), ("space", "acme/har2client-leaderboard")])
        for call in api.upload_folder.call_args_list:
            self.assertTrue(call.kwargs.get("delete_patterns"), "stale remote files would stay after a re-run")
            self.assertFalse(any(p in ("*", "**") for p in call.kwargs["delete_patterns"]), ".gitattributes must survive")
        self.assertNotIn(HF_LIKE, out)

    def test_dry_run_touches_nothing_and_prints_no_token(self) -> None:
        rc, out, api = self.run_main([*self.base, "--dry-run"], {"HF_TOKEN": HF_LIKE})
        self.assertEqual(rc, 0)
        api.create_repo.assert_not_called()
        self.assertNotIn(HF_LIKE, out)

    def test_missing_token_fails(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.assertEqual(publish_hf.main(self.base), 1)


class PublishSh(unittest.TestCase):
    def sh(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        e = {**os.environ, **(env or {})}
        e.pop("HF_TOKEN", None) if not (env and "HF_TOKEN" in env) else None
        return subprocess.run(["bash", str(RELEASE / "publish.sh"), *args], capture_output=True, text=True, env=e, timeout=300)

    def test_flag_in_namespace_position_is_rejected(self) -> None:
        r = self.sh("--dry-run")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_unknown_flag_is_rejected_before_any_work(self) -> None:
        r = self.sh("acme", "--dry-runn")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("1/7", r.stdout)

    @unittest.skipUnless(SOURCE.is_dir() and (H2C_DIR / "runs" / "set30").is_dir(), "needs har2client/runs")
    def test_dry_run_needs_no_uv_and_creates_space_tasks_json(self) -> None:
        bindir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, bindir, ignore_errors=True)
        fake_uv = bindir / "uv"
        fake_uv.write_text("#!/bin/sh\necho uv-was-called >&2\nexit 99\n", encoding="utf-8")
        fake_uv.chmod(0o755)
        # CODE_URL is ignored: the GitHub link is fixed in the text; links.json holds the dataset URL only
        r = self.sh("acme", "--dry-run", env={"PATH": f"{bindir}:{os.environ['PATH']}", "CODE_URL": "https://example.org/code"})
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertNotIn("uv-was-called", r.stderr)
        staged = RELEASE / "build" / "space" / "data"
        self.assertTrue((staged / "tasks.json").is_file())
        links = json.loads((staged / "links.json").read_text(encoding="utf-8"))
        self.assertNotIn("code", links)
        self.assertEqual(links["dataset"], "https://huggingface.co/datasets/acme/har2client")


class CroissantChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        if not SOURCE.is_dir():
            self.skipTest("needs har2client/runs/tierb50")
        tasks = self.tmp / "tasks"
        tasks.mkdir()
        for name in TASKS:
            shutil.copytree(SOURCE / name, tasks / name)
        self.out = self.tmp / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            export_dataset.main(["--out", str(self.out), "--tier-a", str(self.tmp / "none"), "--tier-b", str(tasks), "--repo-id", "acme/har2client"])
        self.doc = json.loads((self.out / "croissant.json").read_text(encoding="utf-8"))

    def test_export_validates_strictly(self) -> None:
        errors, _ = validate_croissant.check(self.doc, True, self.out)
        self.assertEqual(errors, [])

    def test_wrong_sha256_is_an_error(self) -> None:
        doc = copy.deepcopy(self.doc)
        doc["distribution"][0]["sha256"] = "0" * 64
        errors, _ = validate_croissant.check(doc, True, self.out)
        self.assertTrue(any("sha256" in e for e in errors), errors)

    def test_column_without_a_field_is_an_error(self) -> None:
        doc = copy.deepcopy(self.doc)
        doc["recordSet"][0]["field"] = [f for f in doc["recordSet"][0]["field"] if f["name"] != "observation"]
        errors, _ = validate_croissant.check(doc, True, self.out)
        self.assertTrue(any("observation" in e for e in errors), errors)

    def test_exported_columns_are_all_described(self) -> None:
        for rs in self.doc["recordSet"]:
            row = json.loads((self.out / rs["@id"] / "data.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(set(row) - {f["name"] for f in rs["field"]}, set())


class SyncSpaceData(unittest.TestCase):
    def test_empty_dataset_dir_is_an_error_and_keeps_old_data(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "dataset").mkdir()
        space = tmp / "space_data"
        space.mkdir()
        (space / "keep.txt").write_text("x", encoding="utf-8")
        with mock.patch.object(sync_space_data, "DATASET_DIR", tmp / "dataset"), mock.patch.object(sync_space_data, "SPACE_DATA", space), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sync_space_data.main(), 1)
        self.assertTrue((space / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
