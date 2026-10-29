"""The solver sends `CHANGELOG.md` of a v3 task and says so in the repair system prompt; every older prompt is byte-identical."""

import json

import pytest

from har2client.solve import beam, openai_compat, prompt
from test_beam_solver import FakeClient, entry, make_har, response

NOTES = "# Changelog\n\n## 2.4.0 (2026-03-18)\n\n### Breaking changes\n\n- Writes need a header.\n"


@pytest.fixture
def v3_dir(tmp_path):
    d = tmp_path / "v3"
    d.mkdir()
    (d / "goal.md").write_text("# Goal\nRepair the client.")
    (d / "session.har").write_text(json.dumps(make_har([entry("https://api.example.com/v1/orders")])))
    (d / "client_v1.py").write_text("class Client:\n    pass\n")
    (d / "failure.txt").write_text("Call 1 failed\n")
    (d / "CHANGELOG.md").write_text(NOTES)
    for name in beam.HIDDEN_FILES:
        (d / name).write_text("HIDDEN_SECRET_TOKEN")
    return d


def messages(fake):
    request = fake.requests[0]["messages"]
    return next(m["content"] for m in request if m["role"] == "system"), next(m["content"] for m in request if m["role"] == "user")


def test_system_prompt_is_unchanged_without_a_changelog_and_names_it_with_one():
    assert prompt.build_system_prompt() is prompt.SYSTEM_PROMPT
    assert prompt.build_system_prompt(has_recording=False) is prompt.NO_RECORDING_SYSTEM_PROMPT
    assert "CHANGELOG" not in prompt.SYSTEM_PROMPT and "CHANGELOG" not in prompt.NO_RECORDING_SYSTEM_PROMPT
    for has_recording in (True, False):
        base = prompt.build_system_prompt(has_recording)
        with_notes = prompt.build_system_prompt(has_recording, has_changelog=True)
        assert with_notes.startswith(base) and with_notes != base and "`CHANGELOG.md`" in with_notes


def test_changelog_section_comes_after_the_failure_and_before_the_recording():
    text = prompt.build_user_prompt("GOAL", "HAR", "session.har", {"client_v1.py": "V", "failure.txt": "F", "CHANGELOG.md": "C"})
    order = [text.index(s) for s in ("# Goal", "# Previous client", "# Failure of the previous client", "# Release notes", "\n\nC\n\n", "# Recorded session", "# Task")]
    assert order == sorted(order)


def test_user_prompt_without_a_changelog_is_unchanged():
    old = prompt.build_user_prompt("GOAL", "HAR", "session.har", {"client_v1.py": "V", "failure.txt": "F"})
    assert "Release notes" not in old and "CHANGELOG" not in old


def test_beam_solve_sends_the_changelog_and_the_longer_system_prompt(v3_dir, tmp_path):
    fake = FakeClient([response()])
    log = beam.solve(v3_dir, tmp_path / "c.py", client=fake)
    system, user = messages(fake)
    assert NOTES.strip() in user and "`CHANGELOG.md`" in system
    assert log["extra_files"] == ["CHANGELOG.md", "client_v1.py", "failure.txt"]
    assert "HIDDEN_SECRET_TOKEN" not in json.dumps(fake.requests)


def test_beam_solve_without_a_changelog_keeps_the_old_prompts(v3_dir, tmp_path):
    (v3_dir / "CHANGELOG.md").unlink()
    fake = FakeClient([response()])
    beam.solve(v3_dir, tmp_path / "c.py", client=fake)
    system, user = messages(fake)
    assert system == prompt.SYSTEM_PROMPT and "CHANGELOG" not in user


def test_openai_compat_sends_the_same(v3_dir, tmp_path):
    fake = FakeClient([response()])
    log = openai_compat.solve(v3_dir, tmp_path / "c.py", client=fake, model="org/model")
    system, user = messages(fake)
    assert log["status"] == "ok" and NOTES.strip() in user and "`CHANGELOG.md`" in system


def test_a_failure_only_task_with_a_changelog_has_no_har_wording(v3_dir, tmp_path):
    (v3_dir / "session.har").unlink()
    fake = FakeClient([response()])
    beam.solve(v3_dir, tmp_path / "c.py", client=fake)
    system, user = messages(fake)
    assert "HAR" not in system and "HAR" not in user and "`CHANGELOG.md`" in system


def test_the_guard_still_blocks_hidden_files_and_allows_the_changelog(v3_dir):
    for name in (*beam.HIDDEN_FILES, "heal.json"):
        with pytest.raises(AssertionError):
            beam._read_visible(v3_dir, name)
    assert beam._read_visible(v3_dir, "CHANGELOG.md") == NOTES
