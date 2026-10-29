"""Tests for scripts/estimate_cost.py."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "estimate_cost.py"
_spec = importlib.util.spec_from_file_location("estimate_cost", SCRIPT)
ec = importlib.util.module_from_spec(_spec)
sys.modules["estimate_cost"] = ec
_spec.loader.exec_module(ec)

MODEL = {"id": "m/x", "price_in_per_mtok": 1.0, "price_out_per_mtok": 4.0, "context_length": 131072, "max_output_tokens": 32768}


def make_task(root, name, har_chars):
    d = root / name
    d.mkdir(parents=True)
    (d / "goal.md").write_text("# Goal\nDo it.")
    (d / "session.har").write_text('{"log": {"entries": []}, "pad": "' + "x" * har_chars + '"}')
    (d / "ref_client.py").write_text("HIDDEN")
    return d


def test_estimate_is_linear_in_tasks_runs_and_prices():
    one = ec.estimate(MODEL, [10_000], 1, 1, (1000, 2000, 3000))
    assert one.cost_expected == pytest.approx((10_000 * 1.0 + 2000 * 4.0) / 1e6)
    many = ec.estimate(MODEL, [10_000], 50, 3, (1000, 2000, 3000))
    assert many.cost_expected == pytest.approx(one.cost_expected * 150)
    assert one.cost_low < one.cost_expected < one.cost_high


def test_context_fit_uses_the_largest_prompt_plus_max_output():
    assert ec.estimate(MODEL, [90_000], 1, 1, (1, 1, 1)).fits_context
    assert not ec.estimate(MODEL, [100_000], 1, 1, (1, 1, 1)).fits_context


def test_prompt_tokens_follow_the_solver_prompt(tmp_path):
    small, big = make_task(tmp_path, "a", 4_000), make_task(tmp_path, "b", 40_000)
    assert ec.task_prompt_tokens(big) - ec.task_prompt_tokens(small) == pytest.approx(9_000, abs=5)
    assert ec.task_prompt_tokens(small, token_factor=2.0) == pytest.approx(2 * ec.task_prompt_tokens(small), abs=1)
    assert [d.name for d in ec.find_task_dirs(tmp_path)] == ["a", "b"]


def test_models_file_lists_the_leaderboard_candidates():
    models = ec.load_models(ec.openai_compat.MODELS_PATH, None)
    assert len(models) >= 3 and all(m["context_length"] >= 128_000 for m in models)
    assert ec.load_models(ec.openai_compat.MODELS_PATH, ["openai/gpt-oss-120b:cheapest"])[0]["id"] == "openai/gpt-oss-120b"
    with pytest.raises(SystemExit):
        ec.load_models(ec.openai_compat.MODELS_PATH, ["nope/none"])


def test_cli_prints_a_total_and_json(tmp_path, capsys):
    make_task(tmp_path, "a", 4_000)
    assert ec.main(["--tasks", str(tmp_path), "--n", "50"]) == 0
    assert "TOTAL" in capsys.readouterr().out
    assert ec.main(["--tasks", str(tmp_path), "--json", "--models", "openai/gpt-oss-120b"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["model"] == "openai/gpt-oss-120b"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ec.main(["--tasks", str(empty)]) == 2
