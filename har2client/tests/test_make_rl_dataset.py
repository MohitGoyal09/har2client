import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import make_rl_dataset  # noqa: E402


def test_dataset_rows_have_chat_prompt_and_task_spec_only(tmp_path):
    out = tmp_path / "ds.jsonl"
    assert make_rl_dataset.main(["--split", "test_combo", "--n", "3", "--out", str(out), "--cache-dir", str(tmp_path / "c"), "--jobs", "2"]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 3
    for row in rows:
        assert set(row) == {"prompt", "task_spec"}
        assert [m["role"] for m in row["prompt"]] == ["system", "user"]
        assert "class Client" in row["prompt"][1]["content"] and "# Recorded session" in row["prompt"][1]["content"]
        assert row["task_spec"]["compact"] is True and row["task_spec"]["level"] == 3
        user = row["prompt"][1]["content"]
        assert "tests.json" not in user and "engine.json" not in user
    assert len({json.dumps(r["task_spec"], sort_keys=True) for r in rows}) == 3


def test_dataset_is_deterministic_and_specs_rebuild_the_prompt(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    for path, cache in ((a, "c1"), (b, "c2")):
        make_rl_dataset.main(["--split", "train", "--n", "4", "--out", str(path), "--cache-dir", str(tmp_path / cache), "--no-compact", "--level", "1"])
    assert a.read_text() == b.read_text()
