import json
import sys
import types
from importlib import metadata
from pathlib import Path

import pytest

from har2client import cli

FIX = Path(__file__).parent / "fixtures"
SECRET = "sk-test-not-a-real-key-123"


@pytest.fixture(autouse=True)
def _no_real_dotenv(tmp_path, monkeypatch):
    """Never read the developer's real har2client/.env during tests."""
    monkeypatch.setattr(cli, "ENV_PATH", tmp_path / "absent.env")


def test_build_ref_grade_roundtrip(tmp_path, capsys):
    out = tmp_path / "tasks"
    assert cli.main(["build", str(FIX / "bookshelf_apikey.yaml"), "--api-id", "acme:bookshelf", "--out", str(out), "--seed", "5"]) == 0
    task = out / "acme_bookshelf"
    assert (task / "task.json").is_file()
    assert cli.main(["ref", str(task)]) == 0
    assert (task / "ref_client.py").is_file()
    assert cli.main(["grade", str(task), str(task / "ref_client.py")]) == 0
    g = json.loads((task / "grade.json").read_text())
    assert g["pass_rate"] == 1.0
    assert "pass_rate" in capsys.readouterr().out


def test_build_skips_unusable_api_with_exit_code_2(tmp_path, capsys):
    spec = tmp_path / "tiny.yaml"
    spec.write_text(
        "openapi: 3.0.0\ninfo: {title: t}\nservers: [{url: 'https://t.example'}]\npaths:\n  /a:\n    get:\n"
        "      responses:\n        '200': {description: ok}\n"
    )
    assert cli.main(["build", str(spec), "--api-id", "t:t", "--out", str(tmp_path / "o")]) == 2
    assert "skipped" in capsys.readouterr().err.lower()
    assert not (tmp_path / "o" / "t_t").exists()


def test_build_skips_unsupported_security_with_exit_code_2(tmp_path, capsys):
    text = (FIX / "bookshelf_apikey.yaml").read_text().replace("in: header\n      name: X-API-Key", "in: cookie\n      name: sid")
    spec = tmp_path / "s.yaml"
    spec.write_text(text)
    assert cli.main(["build", str(spec), "--api-id", "t:t", "--out", str(tmp_path / "o")]) == 2
    assert "security" in capsys.readouterr().err.lower()


def test_solve_reports_missing_solver_clearly(tmp_path, capsys, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "har2client.solve.beam", None)  # makes the import fail
    assert cli.main(["solve", str(tmp_path), "--effort", "high", "--out", str(tmp_path / "c.py")]) == 1
    assert "har2client.solve.beam" in capsys.readouterr().err


def test_solve_calls_solver_with_path_objects(tmp_path, capsys, monkeypatch):
    import sys
    import types

    seen = {}
    fake = types.ModuleType("har2client.solve.beam")

    def solve(task_dir, out_path, effort):
        seen.update(task_dir=task_dir, out_path=out_path, effort=effort)
        return {"tokens": 1}

    fake.solve = solve
    monkeypatch.setitem(sys.modules, "har2client.solve.beam", fake)
    assert cli.main(["solve", str(tmp_path), "--effort", "low", "--out", str(tmp_path / "c.py")]) == 0
    assert seen == {"task_dir": tmp_path, "out_path": tmp_path / "c.py", "effort": "low"}
    assert '"tokens": 1' in capsys.readouterr().out


def test_version_flag_prints_package_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"har2client {metadata.version('har2client')}"


def _fake_solver(monkeypatch, seen):
    fake = types.ModuleType("har2client.solve.beam")

    def solve(task_dir, out_path, effort):
        import os

        seen["key"] = os.environ.get("REFLECTION_API_KEY")
        return {"ok": True}

    fake.solve = solve
    monkeypatch.setitem(sys.modules, "har2client.solve.beam", fake)


def test_solve_loads_key_from_dotenv_without_printing_it(tmp_path, capsys, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"REFLECTION_API_KEY={SECRET}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ENV_PATH", env_file)
    monkeypatch.delenv("REFLECTION_API_KEY", raising=False)
    seen = {}
    _fake_solver(monkeypatch, seen)
    try:
        assert cli.main(["solve", str(tmp_path), "--out", str(tmp_path / "c.py")]) == 0
        assert seen["key"] == SECRET
        captured = capsys.readouterr()
        assert SECRET not in captured.out + captured.err
    finally:
        import os

        os.environ.pop("REFLECTION_API_KEY", None)  # cli wrote it straight into os.environ


def test_solve_keeps_key_already_in_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("REFLECTION_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ENV_PATH", env_file)
    monkeypatch.setenv("REFLECTION_API_KEY", "from-env")
    seen = {}
    _fake_solver(monkeypatch, seen)
    assert cli.main(["solve", str(tmp_path), "--out", str(tmp_path / "c.py")]) == 0
    assert seen["key"] == "from-env"
