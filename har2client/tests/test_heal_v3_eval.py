"""The v3 evaluation suite selection and the contract-hint arm."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from har2client.heal import release

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_eval_suite_has_64_tasks_on_fresh_seeds_and_every_pilot_family():
    entries = release.select_pilot(2, release.EVAL_FIRST_SEED, release.EVAL_FAMILIES)
    assert len(entries) == 64
    assert [e["seed"] for e in entries] == list(range(2001, 2065))
    counts = {f: sum(e["set"] == f for e in entries) for f, *_ in release.FAMILIES}
    assert counts == {"single": 14, "release3": 10, "release4": 8, "partial": 8, "sdk": 8, "misleading": 8, "terse": 8}
    assert len({e["task_id"] for e in entries}) == 64


def test_eval_seeds_do_not_overlap_the_pilot():
    pilot = {e["seed"] for e in release.select_pilot(1)}
    evals = {e["seed"] for e in release.select_pilot(2, release.EVAL_FIRST_SEED, release.EVAL_FAMILIES)}
    assert not pilot & evals


def test_add_hint_inserts_the_paragraph_after_the_anchor_line_only():
    hint = _script("add_contract_hint")
    goal = "# Repair\nMake it so the client works with the new version.\nKeep the rest.\n"
    out = hint.add_hint(goal)
    assert out == "# Repair\nMake it so the client works with the new version.\n" + hint.HINT + "Keep the rest.\n"


def test_add_hint_rejects_a_goal_without_the_anchor():
    hint = _script("add_contract_hint")
    with pytest.raises(ValueError):
        hint.add_hint("# Repair\nnothing here\n")


def _task_dir(root: Path, name: str, goal: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "task.json").write_text("{}")
    (d / "goal.md").write_text(goal)
    (d / "client_v1.py").write_text("# v1\n")
    return d


GOAL = "# Repair\nMake it so the client works with the new version.\nKeep the rest.\n"


def test_notice_and_placebo_texts_are_exact():
    hint = _script("add_contract_hint")
    assert hint.NOTICE == (
        "\n**Important:** the new API may also have changed its wire format, without raising errors. "
        "Your methods must still return exactly what `client_v1.py` returns.\n"
    )
    assert hint.PLACEBO == (
        "\n**Important:** work carefully. Read every file in this folder before you change any code, and keep your changes small "
        "and easy to review. Your methods must keep the same names and arguments as in `client_v1.py`, and `client.py` must "
        "run with no extra packages.\n"
    )


@pytest.mark.parametrize("variant,attr", [("contract", "HINT"), ("notice", "NOTICE"), ("placebo", "PLACEBO")])
def test_add_hint_text_argument_uses_the_same_anchor(variant, attr):
    hint = _script("add_contract_hint")
    text = getattr(hint, attr)
    assert hint.add_hint(GOAL, text) == "# Repair\nMake it so the client works with the new version.\n" + text + "Keep the rest.\n"
    assert hint.VARIANTS[variant] == text


@pytest.mark.parametrize("variant", ["contract", "notice", "placebo"])
def test_main_variant_writes_only_goal_md_changes(tmp_path, variant):
    hint = _script("add_contract_hint")
    src = tmp_path / "src"
    _task_dir(src, "t1", GOAL)
    out = tmp_path / "out"
    assert hint.main(["--tasks", str(src), "--out", str(out), "--variant", variant]) == 0
    assert (out / "t1" / "goal.md").read_text() == hint.add_hint(GOAL, hint.VARIANTS[variant])
    assert (out / "t1" / "client_v1.py").read_text() == "# v1\n"


def test_main_default_variant_is_contract_and_matches_the_old_output(tmp_path):
    hint = _script("add_contract_hint")
    src = tmp_path / "src"
    _task_dir(src, "t1", GOAL)
    assert hint.main(["--tasks", str(src), "--out", str(tmp_path / "out")]) == 0
    cut = GOAL.index(hint.ANCHOR) + len(hint.ANCHOR)  # the pre-change algorithm
    assert (tmp_path / "out" / "t1" / "goal.md").read_text() == GOAL[:cut] + hint.HINT + GOAL[cut:]


@pytest.mark.skipif(not (ROOT / "runs" / "heal_v3_eval_hint").is_dir(), reason="built hint arm not in this checkout")
def test_contract_variant_is_byte_identical_to_the_built_hint_arm(tmp_path):
    hint = _script("add_contract_hint")
    built = ROOT / "runs" / "heal_v3_eval_hint"
    base = ROOT / "runs" / "heal_v3_eval"
    for d in sorted(p for p in built.iterdir() if (p / "task.json").is_file()):
        assert hint.add_hint((base / d.name / "goal.md").read_text()) == (d / "goal.md").read_text(), d.name
