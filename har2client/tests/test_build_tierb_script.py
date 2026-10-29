"""scripts/build_tierb.py never silently overwrites an existing config list (no task builds, no network)."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_tierb.py"
_spec = importlib.util.spec_from_file_location("build_tierb", SCRIPT)
bt = importlib.util.module_from_spec(_spec)
sys.modules["build_tierb"] = bt
_spec.loader.exec_module(bt)

COUNT = 6


@pytest.fixture
def no_build(monkeypatch):
    """Replace the task build with a stub that records the rows it gets."""
    built: list[str] = []

    def fake(args):
        built.append(bt.task_id_of(args[0]))
        return bt.task_id_of(args[0]), None, None

    monkeypatch.setattr(bt, "_build", fake)
    return built


def run(tmp_path, configs, *extra):
    return bt.main(["--out", str(tmp_path / "out"), "--configs", str(configs), "--count", str(COUNT), "--jobs", "1", *extra])


def test_writes_a_new_config_list(tmp_path, no_build):
    configs = tmp_path / "configs.jsonl"

    assert run(tmp_path, configs, "--seed", "1") == 0

    assert len(configs.read_text().splitlines()) == COUNT
    assert len(no_build) == COUNT


def test_identical_content_is_a_no_op(tmp_path, no_build):
    configs = tmp_path / "configs.jsonl"
    run(tmp_path, configs, "--seed", "1")
    before = configs.stat().st_mtime_ns

    assert run(tmp_path, configs, "--seed", "1") == 0

    assert configs.stat().st_mtime_ns == before


def test_different_content_exits_non_zero_and_keeps_the_file(tmp_path, no_build, capsys):
    configs = tmp_path / "configs.jsonl"
    configs.write_text("committed list\n", encoding="utf-8")

    code = run(tmp_path, configs, "--seed", "2")

    assert code != 0
    assert configs.read_text(encoding="utf-8") == "committed list\n"
    assert no_build == []  # fails before any task is built
    err = capsys.readouterr().err
    assert str(configs) in err and "--force" in err


def test_force_overwrites_a_different_list(tmp_path, no_build):
    configs = tmp_path / "configs.jsonl"
    configs.write_text("committed list\n", encoding="utf-8")

    assert run(tmp_path, configs, "--seed", "2", "--force") == 0

    assert configs.read_text(encoding="utf-8") != "committed list\n"
    assert len(configs.read_text().splitlines()) == COUNT


def test_changed_seed_is_refused_after_a_first_write(tmp_path, no_build):
    configs = tmp_path / "configs.jsonl"
    run(tmp_path, configs, "--seed", "1")
    first = configs.read_text(encoding="utf-8")

    assert run(tmp_path, configs, "--seed", "3") != 0

    assert configs.read_text(encoding="utf-8") == first


def test_default_path_is_the_committed_list():
    assert bt.DEFAULT_CONFIGS == bt.ROOT / "data" / "tierb_configs.jsonl"
