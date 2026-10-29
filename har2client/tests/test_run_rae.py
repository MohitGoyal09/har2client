"""Tests for scripts/run_rae.py (no RAE, no OpenCode, no network: the RAE process is replaced by a stub).

The one real end-to-end check (RAE + OpenCode + the fake OpenAI server) needs node, network and the RAE
clone; it runs only with H2C_RAE_E2E=1.
"""

import functools
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from har2client import build_task, ops
from har2client.refclient import write_ref_client
from har2client.solve import beam

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIX = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rb = load("run_batch")
rr = load("run_rae")
fake = load("fake_openai_server")

SECRET = "sk-test-DO-NOT-PRINT-123456"
GOOD = "class Client:\n    def __init__(self, base_url, api_key):\n        self.base_url = base_url\n"
ALIASED = "class InvoiceApi:\n    def __init__(self, base_url, username, password):\n        pass\n\ndef main():\n    pass\n"


@pytest.fixture(scope="module")
def tasks_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("rae_tasks")
    build_task.build_task(ops.load_spec(FIX / "bookshelf_apikey.yaml"), "acme:alpha", root, seed=1)
    build_task.build_task(ops.load_spec(FIX / "bookshelf_basic.yaml"), "acme:beta", root, seed=2)
    for task in root.iterdir():
        write_ref_client(task)
    return root


def settings(tasks_root, timeout_s=5.0):
    return rr.RaeSettings("rae", "http://127.0.0.1:1", "fake-model", "http://x/v1", Path(tasks_root), timeout_s)


def stub_process(code=None, returncode=0, payload=None, timed_out=False, stderr="", log_extra=""):
    """Plays RAE: writes `api_client.py` (when `code` is given) and a message log, returns the JSON result."""
    calls = []

    def process(cmd, env, cwd, timeout_s, log_path):
        calls.append({"cmd": cmd, "env": env, "cwd": cwd})
        run_id = cmd[2]
        rae_out = Path(cmd[cmd.index("-o") + 1])
        Path(log_path).write_text(stderr)
        (rae_out / "messages").mkdir(parents=True, exist_ok=True)
        (rae_out / "messages" / f"{run_id}.jsonl").write_text(log_extra)
        if code is not None:
            scripts = rae_out / "scripts" / run_id
            scripts.mkdir(parents=True, exist_ok=True)
            (scripts / "api_client.py").write_text(code)
        body = payload if payload is not None else {
            "status": "ok", "usage": {"input_tokens": 100, "output_tokens": 40, "raw": {"reasoning_tokens": 9}}, "error": None}
        return rr.ProcessResult(None if timed_out else returncode, json.dumps(body), timed_out)

    process.calls = calls
    return process


def solve(tasks_dir, tmp_path, process, name="acme_alpha", **kw):
    out = tmp_path / "cell" / "client.py"
    log = rr.rae_solve(tasks_dir / name, out, "high", "raw", settings=settings(tasks_dir), process=process, **kw)
    return log, out


# ---- configuration -----------------------------------------------------------


def test_opencode_config_reads_the_key_from_the_environment():
    text = json.dumps(rr.opencode_config("https://api.reflection.ai/openai/v1", "Beam-1.0-501B-A23B", "REFLECTION_API_KEY", "xhigh"))
    provider = json.loads(text)["provider"][rr.PROVIDER_ID]
    assert provider["options"] == {"baseURL": "https://api.reflection.ai/openai/v1", "apiKey": "{env:REFLECTION_API_KEY}"}
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["models"]["Beam-1.0-501B-A23B"]["options"]["reasoningEffort"] == "high"  # xhigh -> high


def test_rae_config_uses_opencode_and_no_local_copy():
    cfg = rr.rae_config("http://127.0.0.1:4100", "m")
    assert cfg["sdk"] == "opencode" and cfg["opencode_provider"] == rr.PROVIDER_ID and cfg["opencode_model"] == "m"
    assert cfg["real_time_sync"] is False and cfg["output_language"] == "python"


def test_run_id_is_a_valid_rae_path_component():
    run_id = rr.run_id_for(Path("/x/tierb_00 invoices.v2"))
    assert run_id == "h2c_tierb_00_invoices_v2"
    assert len(rr.run_id_for(Path("a" * 200))) == 64


# ---- adapter -------------------------------------------------------------------


def test_adapter_keeps_a_client_class_unchanged():
    assert rr.adapt_client(GOOD) == (GOOD, "none")


def test_adapter_aliases_the_single_class_that_takes_base_url():
    code, adapter = rr.adapt_client(ALIASED)
    assert adapter == "alias" and code.startswith(ALIASED.rstrip("\n")) and code.rstrip().endswith("Client = InvoiceApi  # added by run_rae.py")


def test_adapter_gives_up_on_ambiguity_missing_class_and_syntax_errors():
    two = "class A:\n    def __init__(self, base_url): pass\nclass B:\n    def __init__(self, base_url): pass\n"
    assert rr.adapt_client(two) == (None, "no_client_class")
    assert rr.adapt_client("def main(): pass\n") == (None, "no_client_class")
    assert rr.adapt_client("class (") == (None, "syntax_error")


def test_parse_result_takes_the_last_json_line():
    assert rr.parse_result("noise\n{\"a\": 1}\n{\"status\": \"ok\"}\n") == {"status": "ok"}
    assert rr.parse_result("no json") == {}


# ---- staging ---------------------------------------------------------------------


def test_stage_run_copies_only_the_session_file(tasks_dir, tmp_path):
    task = tasks_dir / "acme_alpha"
    har = rr.stage_run(task, tmp_path / "out", "h2c_x")
    assert har == tmp_path / "out" / "har" / "h2c_x" / "recording.har"
    assert har.read_text() == (task / "session.har").read_text()
    assert (har.parent / "session.har").is_file()
    assert sorted(p.name for p in har.parent.iterdir()) == ["recording.har", "session.har"]


# ---- rae_solve ---------------------------------------------------------------------


def test_ok_run_writes_client_and_log(tasks_dir, tmp_path):
    process = stub_process(GOOD)
    log, out = solve(tasks_dir, tmp_path, process)
    assert out.read_text() == GOOD
    assert (log["status"], log["adapter"]) == ("ok", "none")
    assert log["usage"] == {"prompt_tokens": 100, "completion_tokens": 40, "reasoning_tokens": 9}
    assert json.loads(out.with_name("solve_log.json").read_text())["model"] == "fake-model"
    cmd = process.calls[0]["cmd"]
    goal = (tasks_dir / "acme_alpha" / "goal.md").read_text()
    assert cmd[1:3] == ["engineer", rr.run_id_for(tasks_dir / "acme_alpha")] and "--fresh" in cmd
    assert cmd[cmd.index("-p") + 1] == goal  # goal.md is RAE's instruction
    assert cmd[-1] == "--json"


def test_rae_gets_an_isolated_home_and_no_api_key(tasks_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("REFLECTION_API_KEY", SECRET)
    process = stub_process(GOOD)
    solve(tasks_dir, tmp_path, process)
    call = process.calls[0]
    assert SECRET not in json.dumps(call["env"]) and call["env"]["HOME"].startswith(str(tmp_path))
    assert (Path(call["env"]["HOME"]) / ".reverse-api" / "config.json").is_file()
    assert call["env"]["RAE_OPENCODE_AUTO_START"] == "0"


def test_hidden_files_never_reach_the_rae_run(tasks_dir, tmp_path):
    solve(tasks_dir, tmp_path, stub_process(GOOD))
    names = {p.name for p in (tmp_path / "cell" / "rae").rglob("*") if p.is_file()}
    assert not names & set(beam.HIDDEN_FILES)


def test_alias_adapter_is_logged(tasks_dir, tmp_path):
    log, out = solve(tasks_dir, tmp_path, stub_process(ALIASED))
    assert (log["status"], log["adapter"]) == ("ok", "alias") and "Client = InvoiceApi" in out.read_text()


@pytest.mark.parametrize("kwargs,status", [
    ({"code": None}, "no_code"),
    ({"code": "def main(): pass\n"}, "no_client_class"),
    ({"code": "class ("}, "no_code"),
    ({"code": GOOD, "timed_out": True}, "timeout"),
])
def test_unusable_runs_get_a_status_and_no_client(tasks_dir, tmp_path, kwargs, status):
    log, out = solve(tasks_dir, tmp_path, stub_process(**kwargs))
    assert log["status"] == status and not out.exists()


def test_daily_allowance_marker_stops_the_batch(tasks_dir, tmp_path):
    with pytest.raises(beam.DailyAllowanceExhausted):
        solve(tasks_dir, tmp_path, stub_process(None, stderr="429 daily_allowance_exhausted"))
    assert json.loads((tmp_path / "cell" / "solve_log.json").read_text())["status"] == "daily_allowance_exhausted"


def test_agent_reading_hidden_files_is_flagged(tasks_dir, tmp_path):
    log, _ = solve(tasks_dir, tmp_path, stub_process(GOOD, log_extra='{"content": "cat /x/ref_client.py"}'))
    assert log["hidden_mentions"] == ["ref_client.py"]


def test_compact_har_mode_is_rejected(tasks_dir, tmp_path):
    with pytest.raises(ValueError, match="raw"):
        rr.rae_solve(tasks_dir / "acme_alpha", tmp_path / "c" / "client.py", "high", "compact",
                     settings=settings(tasks_dir), process=stub_process(GOOD))


def test_stale_attempt_files_are_cleared(tasks_dir, tmp_path):
    stale = tmp_path / "cell" / "rae" / "out" / "scripts" / rr.run_id_for(tasks_dir / "acme_alpha")
    stale.mkdir(parents=True)
    (stale / "api_client.py").write_text(GOOD)
    log, out = solve(tasks_dir, tmp_path, stub_process(None))
    assert log["status"] == "no_code" and not out.exists()


# ---- through run_batch (grading, csv) ---------------------------------------------


def test_batch_grades_clients_collected_from_rae(tasks_dir, tmp_path):
    refs = {rr.run_id_for(t): (t / "ref_client.py").read_text() for t in tasks_dir.iterdir()}

    def process(cmd, env, cwd, timeout_s, log_path):
        run_id, rae_out = cmd[2], Path(cmd[cmd.index("-o") + 1])
        Path(log_path).write_text("")
        scripts = rae_out / "scripts" / run_id
        scripts.mkdir(parents=True)
        (scripts / "api_client.py").write_text(refs[run_id])
        return rr.ProcessResult(0, json.dumps({"status": "ok", "usage": {"input_tokens": 5, "output_tokens": 2}}), False)

    out = tmp_path / "batch"
    result = rb.run_batch(tasks_dir, out, efforts=["high"], har_modes=["raw"], runs=1, concurrency=2,
                          solve_fn=functools.partial(rr.rae_solve, settings=settings(tasks_dir), process=process),
                          log=lambda *_: None)
    assert [r["pass_rate"] for r in result.rows] == [1.0, 1.0] and not result.remaining
    assert (out / "results.csv").is_file()


# ---- real end to end (opt in) -------------------------------------------------------


@pytest.mark.skipif(os.environ.get("H2C_RAE_E2E") != "1", reason="set H2C_RAE_E2E=1 (needs node, network, the RAE clone)")
def test_real_rae_through_opencode_against_the_fake_server(tasks_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "x")
    monkeypatch.setattr(rb, "ENV_PATH", tmp_path / "none.env")
    state = fake.FakeState(tasks_dir, None, [], tmp_path / "requests.jsonl")
    with fake.serve(state) as base_url:
        code = rr.main(["--tasks", str(tasks_dir), "--out", str(tmp_path / "o"), "--base-url", base_url,
                        "--model", "fake-model", "--api-key-env", "FAKE_KEY", "--effort", "medium",
                        "--rae-venv", os.environ.get("H2C_RAE_VENV", str(rr.DEFAULT_RAE_VENV))])
    assert code == 0
    rows = (tmp_path / "o" / "results.csv").read_text().splitlines()
    assert len(rows) == 3 and all(",1.0," in r for r in rows[1:])
    efforts = {json.loads(l)["reasoning_effort"] for l in (tmp_path / "requests.jsonl").read_text().splitlines()}
    assert efforts == {"medium"}


def test_opencode_server_env_turns_off_update_and_model_catalogue_fetches(tmp_path):
    config = rr.opencode_config("http://127.0.0.1:1/v1", "m", "KEY", "high")
    env = rr.opencode_env(config, tmp_path, {"PATH": "/bin", "KEY": "secret", "OPENCODE_SERVER_PASSWORD": "x"})
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "1" and env["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert env["HOME"] == str(tmp_path) and env["KEY"] == "secret" and "OPENCODE_SERVER_PASSWORD" not in env
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == config
