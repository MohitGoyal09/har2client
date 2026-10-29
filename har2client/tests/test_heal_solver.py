"""The solver reads the extra visible files of a repair task (case 3); case 1 prompts stay byte-identical."""

import builtins
import io
import json
import os
from pathlib import Path

import pytest

from har2client.solve import beam, prompt
from test_beam_solver import FakeClient, entry, make_har, response

GOAL = "# Goal\nRepair the client."
V1 = "class Client:\n    pass\n"
FAILURE = "Ran client_v1.py ... KeyError: 'total'\n"
# the case 1 user prompt as it was before repair tasks existed (frozen text)
CASE_1_PROMPT = (
    "# Goal\n\nGOAL\n\n# Recorded session (HAR)\n\nHAR\n\n# Task\n\n"
    "Write `client.py` now. It must define `class Client` with the constructor "
    "stated in the goal and the methods named in the goal, using only `httpx`, "
    "`requests` or the standard library, returning what the goal says each method "
    "returns, and never hard-coding hosts or credentials from the HAR. Reply with "
    "the file in a single ```python fenced block."
)


@pytest.fixture
def heal_dir(tmp_path):
    d = tmp_path / "heal"
    d.mkdir()
    (d / "goal.md").write_text(GOAL)
    (d / "session.har").write_text(json.dumps(make_har([entry("https://api.example.com/v1/orders")])))
    (d / "client_v1.py").write_text(V1)
    (d / "failure.txt").write_text(FAILURE)
    for name in beam.HIDDEN_FILES:
        (d / name).write_text("HIDDEN_SECRET_TOKEN")
    return d


def user_message(fake):
    return next(m["content"] for m in fake.requests[0]["messages"] if m["role"] == "user")


def test_case_1_user_prompt_is_byte_identical():
    assert prompt.build_user_prompt("GOAL", "HAR") == CASE_1_PROMPT
    assert prompt.build_user_prompt("GOAL", "HAR", "session.har", None) == CASE_1_PROMPT
    assert prompt.build_user_prompt("GOAL", "HAR", "session.har", {}) == CASE_1_PROMPT


def test_case_1_task_sends_the_case_1_prompt_and_logs_no_extra_files(tmp_path):
    d = tmp_path / "t"
    d.mkdir()
    (d / "goal.md").write_text("GOAL")
    (d / "session.har").write_text("HAR")
    fake = FakeClient([response()])
    log = beam.solve(d, tmp_path / "c.py", client=fake)
    assert user_message(fake) == CASE_1_PROMPT and "extra_files" not in log


def test_repair_prompt_adds_client_v1_and_failure_between_goal_and_session():
    text = prompt.build_user_prompt("GOAL", "HAR", "session.har", {"failure.txt": "F", "client_v1.py": "V"})
    order = [text.index(s) for s in ("# Goal", "# Previous client", "\n\nV\n\n", "# Failure of the previous client", "\n\nF\n\n", "# Recorded session", "# Task")]
    assert order == sorted(order) and text.endswith(CASE_1_PROMPT.split("# Task")[1])


def test_solve_sends_the_extra_files_when_present(heal_dir, tmp_path):
    fake = FakeClient([response()])
    log = beam.solve(heal_dir, tmp_path / "c.py", client=fake)
    text = user_message(fake)
    assert V1.strip() in text and FAILURE.strip() in text and "Repair the client." in text
    assert log["extra_files"] == ["client_v1.py", "failure.txt"] and log["status"] == "ok"
    assert "HIDDEN_SECRET_TOKEN" not in json.dumps(fake.requests)


def test_solve_sends_only_the_extra_file_that_exists(heal_dir, tmp_path):
    (heal_dir / "failure.txt").unlink()
    fake = FakeClient([response()])
    beam.solve(heal_dir, tmp_path / "c.py", client=fake)
    text = user_message(fake)
    assert V1.strip() in text and "Failure of the previous client" not in text


def test_the_hidden_file_guard_still_blocks_everything_else(heal_dir):
    for name in (*beam.HIDDEN_FILES, "heal.json", "../task.json"):
        with pytest.raises(AssertionError):
            beam._read_visible(heal_dir, name)
    assert beam._read_visible(heal_dir, "client_v1.py") == V1 and beam._read_visible(heal_dir, "failure.txt") == FAILURE


def test_solve_on_a_repair_task_never_opens_hidden_files(heal_dir, tmp_path, monkeypatch):
    opened = []
    real_open, real_io_open, real_path_open = builtins.open, io.open, Path.open

    def spy(real):
        def inner(file, *a, **k):
            if isinstance(file, (str, bytes, os.PathLike)):
                opened.append(Path(os.fspath(file)).name)
            return real(file, *a, **k)
        return inner

    monkeypatch.setattr(builtins, "open", spy(real_open))
    monkeypatch.setattr(io, "open", spy(real_io_open))
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (opened.append(self.name), real_path_open(self, *a, **k))[1])
    beam.solve(heal_dir, tmp_path / "c.py", client=FakeClient([response()]))
    assert {"goal.md", "session.har", "client_v1.py", "failure.txt"} <= set(opened)
    assert not set(opened) & set(beam.HIDDEN_FILES)


def test_a_built_repair_task_is_solvable_by_the_solver(tmp_path):
    from har2client.heal.build import build_heal_task
    from har2client.heal.select import select_rows

    row = next(r for r in select_rows(1) if r["mutation"] == "add_etag")
    d = build_heal_task(row, tmp_path)
    fake = FakeClient([response()])
    beam.solve(d, tmp_path / "out" / "c.py", client=fake)
    text = user_message(fake)
    assert (d / "client_v1.py").read_text().strip() in text and (d / "failure.txt").read_text().strip() in text
    task = json.loads((d / "task.json").read_text())
    assert task["credentials"]["test"]["password"] not in json.dumps(fake.requests)


# ---- failure_only repair tasks (no recording): every text names only the files the task has ----------------


def test_system_prompt_default_is_unchanged_and_the_no_recording_one_names_no_har():
    assert prompt.build_system_prompt() is prompt.SYSTEM_PROMPT and prompt.build_system_prompt(has_recording=True) is prompt.SYSTEM_PROMPT
    assert "a HAR file" in prompt.SYSTEM_PROMPT
    no_har = prompt.build_system_prompt(has_recording=False)
    assert "HAR" not in no_har and "no recording" in no_har
    assert "class Client" in no_har and "```python" in no_har


def test_failure_only_task_gets_a_prompt_without_har_wording(heal_dir, tmp_path):
    (heal_dir / "session.har").unlink()
    fake = FakeClient([response()])
    beam.solve(heal_dir, tmp_path / "c.py", client=fake)
    system = next(m["content"] for m in fake.requests[0]["messages"] if m["role"] == "system")
    user = user_message(fake)
    assert "HAR" not in system and "HAR" not in user and "Recorded session" not in user
    assert FAILURE.strip() in user and V1.strip() in user


def test_repair_task_with_a_recording_keeps_the_case_1_system_prompt(heal_dir, tmp_path):
    fake = FakeClient([response()])
    beam.solve(heal_dir, tmp_path / "c.py", client=fake)
    assert next(m["content"] for m in fake.requests[0]["messages"] if m["role"] == "system") == prompt.SYSTEM_PROMPT


def test_openai_compat_solves_a_failure_only_task(heal_dir, tmp_path):
    from har2client.solve import openai_compat

    (heal_dir / "session.har").unlink()
    fake = FakeClient([response()])
    log = openai_compat.solve(heal_dir, tmp_path / "c.py", client=fake, model="org/model")
    messages = fake.requests[0]["messages"]
    assert log["status"] == "ok" and "HAR" not in json.dumps(messages) and FAILURE.strip() in messages[1]["content"]


def test_failure_only_goal_does_not_point_at_a_recording():
    from har2client.heal.build import render_heal_goal
    from har2client.tierb.spec import make_spec

    spec = make_spec("invoices", {"auth": "apikey_header", "pagination": "offset", "token_expiry": "off", "etag_update": "off", "hidden_header": "off", "envelope": "off"}, 5)
    assert "recording" not in render_heal_goal(spec, "failure_only").lower()
    assert "In the recording it looks like" in render_heal_goal(spec, "both")
