"""scripts/run_probe_batch.py with the scripted dry-run client (no network)."""

import csv
import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from har2client.env import tasks

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_probe_batch.py"
_spec = importlib.util.spec_from_file_location("run_probe_batch", SCRIPT)
rpb = importlib.util.module_from_spec(_spec)
sys.modules["run_probe_batch"] = rpb  # dataclasses look the module up here
_spec.loader.exec_module(rpb)

CONFIG = {"auth": "apikey_header", "pagination": "offset", "token_expiry": "off", "etag_update": "off", "hidden_header": "on", "envelope": "off"}


@pytest.fixture(scope="module")
def tasks_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("probe_tasks")
    for i, domain in enumerate(("tickets", "invoices")):
        built = tasks.ensure_task(tasks.make_spec(domain, CONFIG, 50 + i, noise=0.0), root / "cache")
        built.rename(root / f"tierb_{i:02d}_{domain}")
    return root


def read_csv(out):
    with open(Path(out) / "results.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def run(tasks_dir, out, **kw):
    args = dict(arms=list(rpb.ARMS), efforts=["high"], har_modes=["raw"], runs=1, concurrency=2, client=rpb.DryRunClient(tasks_dir), log=lambda *_: None)
    args.update(kw)
    return rpb.run_batch(tasks_dir, out, **args)


def test_every_arm_runs_and_the_csv_has_the_probe_columns(tasks_dir, tmp_path):
    result = run(tasks_dir, tmp_path)
    rows = read_csv(tmp_path)
    assert len(rows) == 6 and not result.remaining
    by_arm = {r["arm"]: r for r in rows if r["task"].endswith("tickets")}
    assert {a: int(r["probes_used"]) for a, r in by_arm.items()} == {"placebo": 0, "P5": 1, "P20": 1}
    assert {a: int(r["probe_budget"]) for a, r in by_arm.items()} == {"placebo": 0, "P5": 5, "P20": 20}
    assert all(r["pass_rate"] == "1.0" and r["strict_pass_rate"] == "1.0" for r in rows)
    assert {"probes_header_ablation", "probes_auth_test", "probes_paging_test", "probes_etag_test", "probes_other", "cached_share"} <= set(rows[0])
    assert float(by_arm["P5"]["cached_share"]) > 0 and float(by_arm["placebo"]["cached_share"]) == 0
    p5 = by_arm["P5"]
    assert sum(int(p5[f"probes_{label}"]) for label in rpb.LABELS) == int(p5["probes_used"])


def test_batch_is_resumable_and_writes_per_cell_files(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, arms=["P5"])
    cell = tmp_path / "tierb_00_tickets" / "P5-high-raw-r1"
    assert {p.name for p in cell.iterdir()} >= {"client.py", "solve_log.json", "probes.jsonl", "grade.json"}

    class Boom:
        def __getattr__(self, name):
            raise AssertionError("a finished cell must not call the API again")

    run(tasks_dir, tmp_path, arms=["P5"], client=Boom())
    assert len(read_csv(tmp_path)) == 2


def test_summary_splits_hidden_header_tasks(tasks_dir, tmp_path):
    result = run(tasks_dir, tmp_path)
    text = rpb.summarize(result.rows)
    assert "placebo" in text and "P20" in text and "on " in text


# ---------------------------------------------------------------- the safety rules of run_batch.py hold here too


class Failing:
    """Fake OpenAI client whose every call raises `exc`; counts the calls."""

    def __init__(self, exc):
        self.exc, self.calls = exc, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create)))

    def _create(self, **_):
        self.calls += 1
        raise self.exc


def test_interrupt_stops_the_queued_cells_and_still_writes_results(tasks_dir, tmp_path):
    client = Failing(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run(tasks_dir, tmp_path, client=client, concurrency=1)
    assert client.calls == 1  # the other 5 cells were skipped, not run (each is a paid call)
    assert (tmp_path / "results.csv").is_file()  # what is done is kept


def test_a_corrupt_grade_json_is_not_a_finished_cell(tasks_dir, tmp_path):
    run(tasks_dir, tmp_path, arms=["placebo"])
    (tmp_path / "tierb_01_invoices" / "placebo-high-raw-r1" / "grade.json").write_text('{"pass_rate": 0.')  # killed mid-write
    again = Failing(RuntimeError("must run exactly the cut cell"))
    result = run(tasks_dir, tmp_path, arms=["placebo"], client=again)
    assert again.calls == 1 and [c.task_dir.name for c in result.remaining] == ["tierb_01_invoices"]  # api_error: retried next time
    fixed = run(tasks_dir, tmp_path, arms=["placebo"])
    assert fixed.remaining == [] and {r["status"] for r in read_csv(tmp_path)} == {"ok"}


def test_api_error_is_recorded_but_retried_on_resume_and_left_out_of_the_means(tasks_dir, tmp_path):
    failing = Failing(RuntimeError("boom"))
    result = run(tasks_dir, tmp_path, arms=["placebo"], client=failing)
    assert failing.calls == 2 and len(result.remaining) == 2 and not result.stopped
    assert {r["status"] for r in read_csv(tmp_path)} == {"api_error"}
    text = rpb.summarize(result.rows)
    assert "api_error rows excluded: 2" in text and not any(line.startswith("placebo") for line in text.splitlines())
    resumed = run(tasks_dir, tmp_path, arms=["placebo"])
    assert resumed.remaining == [] and {r["status"] for r in read_csv(tmp_path)} == {"ok"}


def test_a_daily_limit_stops_the_batch_and_the_cell_stays_open(tasks_dir, tmp_path):
    import openai
    import httpx2
    request = httpx2.Request("POST", "https://api.reflection.ai/openai/v1/chat/completions")
    exc = openai.RateLimitError("limit", response=httpx2.Response(429, request=request, json={"error": {"code": "daily_allowance_exhausted"}}), body={"error": {"code": "daily_allowance_exhausted"}})
    client = Failing(exc)
    result = run(tasks_dir, tmp_path, client=client, concurrency=1)
    assert result.stopped and client.calls == 1 and len(result.remaining) == 6
    assert not list(tmp_path.glob("*/*/grade.json"))


# ---------------------------------------------------------------- case 2 end to end, offline: scripted agent, real practice server, real grader


class ScriptedAgent:
    """Fake OpenAI client that plays a scientist. With tools it asks for `n_probes` list requests in one turn
    (without credentials, so each is a probe that a real server answers with 400/401), then submits the reference
    client. Without tools (placebo) it answers with the reference client in a code block. Stateless per request."""

    def __init__(self, tasks_dir, n_probes):
        self.dry, self.n_probes, self.requests = rpb.DryRunClient(tasks_dir), n_probes, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create)))

    def _create(self, **request):
        self.requests.append(request)
        messages = request["messages"]
        task = self.dry._task_of(messages[1]["content"])
        ref = (task / "ref_client.py").read_text(encoding="utf-8")
        word = json.loads((task / "engine.json").read_text(encoding="utf-8"))["path_word"].strip("/")
        if "tools" not in request:
            message = SimpleNamespace(content=f"```python\n{ref}```", tool_calls=None, reasoning_content="r0")
        elif not any(m["role"] == "tool" for m in messages):
            calls = [
                SimpleNamespace(id=f"p{i}", function=SimpleNamespace(name="http_request", arguments=json.dumps({"method": "GET", "path": f"/{word}"})))
                for i in range(self.n_probes)
            ]
            message = SimpleNamespace(content=None, tool_calls=calls, reasoning_content="r1")
        else:
            call = SimpleNamespace(id="s", function=SimpleNamespace(name="submit_client", arguments=json.dumps({"code": ref})))
            message = SimpleNamespace(content=None, tool_calls=[call], reasoning_content="r2")
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, completion_tokens_details=SimpleNamespace(reasoning_tokens=5), prompt_tokens_details=None)
        parsed = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)
        return SimpleNamespace(parse=lambda: parsed, headers={})


def test_case_2_end_to_end_offline_with_placebo_and_the_probe_budget_limit(tasks_dir, tmp_path, monkeypatch):
    monkeypatch.setitem(rpb.ARMS, "P2", 2)
    started, stopped = [], []
    real_start, real_stop = rpb.beam_agent.PracticeInstance.start, rpb.beam_agent.PracticeInstance.stop
    monkeypatch.setattr(rpb.beam_agent.PracticeInstance, "start", lambda self: (started.append(self), real_start(self))[1])
    monkeypatch.setattr(rpb.beam_agent.PracticeInstance, "stop", lambda self: (stopped.append(self), real_stop(self))[1])
    agent = ScriptedAgent(tasks_dir, n_probes=3)  # asks for 3 probes; the P2 arm may send 2
    result = run(tasks_dir, tmp_path, arms=["placebo", "P2"], client=agent, concurrency=1)
    assert not result.remaining and not result.stopped
    rows = {(r["task"], r["arm"]): r for r in read_csv(tmp_path)}
    assert len(rows) == 4
    for task in ("tierb_00_tickets", "tierb_01_invoices"):
        placebo, p2 = rows[(task, "placebo")], rows[(task, "P2")]
        assert placebo["status"] == p2["status"] == "ok" and placebo["pass_rate"] == p2["pass_rate"] == "1.0"
        assert (placebo["probe_budget"], placebo["probes_used"], placebo["probes_refused"], placebo["turns"]) == ("0", "0", "0", "1")
        assert (p2["probe_budget"], p2["probes_used"], p2["probes_refused"], p2["turns"]) == ("2", "2", "1", "2")
        assert sum(int(p2[f"probes_{label}"]) for label in rpb.LABELS) == 2
        cell = tmp_path / task / "P2-high-raw-r1"
        entries = [json.loads(line) for line in (cell / "probes.jsonl").read_text().splitlines()]
        assert [e["sent"] for e in entries] == [True, True, False] and entries[2]["refused"] == "budget"
        assert entries[0]["response"]["status"] in (400, 401)  # a real answer of the practice server
        assert (tmp_path / task / "placebo-high-raw-r1" / "probes.jsonl").read_text() == ""
    placebo_requests = [r for r in agent.requests if "tools" not in r]
    assert len(placebo_requests) == 2 and len(agent.requests) == 2 + 2 * 2  # placebo: 1 turn; P2: probe turn + submit turn
    assert all("Probe budget: you may send at most 0 requests" in r["messages"][1]["content"] for r in placebo_requests)
    assert len(started) == 2 and len(stopped) == 2  # only the P2 cells start a server, and each is stopped
    lines = rpb.summarize(result.rows).splitlines()
    assert next(l for l in lines if l.startswith("placebo")).split()[:6] == ["placebo", "all", "2", "1.000", "1.000", "0.000"]
    assert next(l for l in lines if l.startswith("P2")).split()[:6] == ["P2", "all", "2", "1.000", "1.000", "2.000"]  # mean probes used


def test_main_dry_run_exits_zero_and_prints_the_csv(tasks_dir, tmp_path, capsys):
    code = rpb.main(["--tasks", str(tasks_dir), "--out", str(tmp_path), "--arms", "placebo", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "api_error rows excluded: 0" in out and len(read_csv(tmp_path)) == 2


def test_cli_refuses_to_resume_fake_results_as_real(tasks_dir, tmp_path, capsys):
    (tmp_path / "run_manifest.json").write_text(json.dumps({"dry_run": True}))
    code = rpb.main(["--tasks", str(tasks_dir), "--out", str(tmp_path)])
    assert code == 2 and "cannot mix" in capsys.readouterr().err


def test_cli_refuses_results_without_provenance(tasks_dir, tmp_path, capsys):
    cell = tmp_path / "task" / "placebo-high-raw-r1"
    cell.mkdir(parents=True)
    (cell / "grade.json").write_text('{"pass_rate": 1}')
    code = rpb.main(["--tasks", str(tasks_dir), "--out", str(tmp_path), "--dry-run"])
    assert code == 2 and "no provenance" in capsys.readouterr().err


def test_cli_returns_failure_for_retryable_api_errors(tasks_dir, tmp_path):
    code = rpb.main(["--tasks", str(tasks_dir), "--out", str(tmp_path), "--arms", "placebo"],
                    client=Failing(RuntimeError("offline")))
    assert code == 1
