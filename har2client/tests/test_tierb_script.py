import importlib.util
import json
from pathlib import Path

import pytest

from har2client.tierb import config as cfg

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_tierb.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("build_tierb", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_writes_the_configs_file_and_builds_tasks(script, tmp_path, capsys):
    configs = tmp_path / "cfg.jsonl"
    code = script.main(["--out", str(tmp_path / "out"), "--seed", "1", "--configs", str(configs), "--limit", "2", "--jobs", "2"])
    assert code == 0
    rows = [json.loads(line) for line in configs.read_text().splitlines()]
    assert len(rows) == 50 and rows[0]["task_id"] == "tierb_00_" + rows[0]["domain"] and rows[0]["config_seed"] == 1
    assert [r["index"] for r in rows] == list(range(50)) and all(cfg.is_valid(r["config"]) for r in rows)
    built = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert built == [rows[0]["task_id"], rows[1]["task_id"]]
    out = capsys.readouterr().out
    assert "built 2/2" in out and "config distribution" in out and "har size" in out


def test_configs_file_is_reproducible_and_matches_the_sampler(script, tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    script.write_configs(cfg.sample_configs(1), a, 1)
    script.write_configs(cfg.sample_configs(1), b, 1)
    assert a.read_text() == b.read_text()
    assert script.read_configs(a) == cfg.sample_configs(1)


def test_distribution_counts_each_switch_value(script):
    dist = script.distribution(cfg.sample_configs(1))
    assert sum(dist["pagination"].values()) == 50 and set(dist["auth"]) == set(cfg.FACTORS["auth"])
    assert dist["domain"] == {d: 10 for d in dist["domain"]}


def test_a_failing_task_gives_exit_code_1(script, tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(script, "build_tierb_task", boom)
    code = script.main(["--out", str(tmp_path / "o"), "--configs", str(tmp_path / "c.jsonl"), "--limit", "1"])
    assert code == 1 and "FAILED" in capsys.readouterr().err
