"""scripts/heal_v1_floor.py with a fake grader (no mock server, no network)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import heal_v1_floor as floor  # noqa: E402


def make_task(root, name, methods):
    d = root / name
    d.mkdir()
    (d / "client_v1.py").write_text("# v1")
    (d / "tests.json").write_text(json.dumps([{"method": m, "kwargs": {}} for m in methods]))
    return d


def test_empty_client_source_has_every_method_returning_none():
    ns = {}
    exec(floor.empty_client_source(["a", "b", "a"]), ns)
    client = ns["Client"]("http://x", "u", "p")
    assert client.a(x=1) is None and client.b() is None


def test_summarize_counts_tasks_above_zero_and_the_max():
    rows = [{"task": "t1", "v1_pass_rate": 0.0, "empty_pass_rate": 0.0}, {"task": "t2", "v1_pass_rate": 0.5, "empty_pass_rate": 0.0},
            {"task": "t3", "v1_pass_rate": 0.25, "empty_pass_rate": 0.0}]
    s = floor.summarize(rows)
    assert s["v1_mean"] == 0.25 and s["v1_max"] == 0.5 and s["v1_max_task"] == "t2" and s["v1_tasks_above_zero"] == 2 and s["empty_mean"] == 0.0


def test_run_floor_grades_v1_unchanged_and_an_empty_client(tmp_path):
    make_task(tmp_path, "a", ["m"])
    make_task(tmp_path, "b", ["m", "n"])
    seen = []

    def fake_grade(task_dir, client_path):
        text = Path(client_path).read_text()
        seen.append((Path(task_dir).name, Path(client_path).name))
        return {"total": 4, "pass_rate": 0.5 if text == "# v1" else 0.0}

    out = floor.run_floor(tmp_path, workers=1, grade_fn=fake_grade)
    assert out["summary"]["n_tasks"] == 2 and out["summary"]["v1_mean"] == 0.5 and out["summary"]["empty_mean"] == 0.0
    assert ("a", "client_v1.py") in seen and ("b", "client.py") in seen
