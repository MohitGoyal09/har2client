#!/usr/bin/env python3
"""Run reverse-api-engineer (RAE) on every task's HAR, collect its client, grade it.

RAE (https://github.com/kalil0321/reverse-api-engineer, MIT) is an agent: it reads a HAR, writes
`api_client.py`, and tests it. Its offline mode is `reverse-api-engineer engineer <run_id>`; here it is
driven through OpenCode (`sdk: opencode`) so that any OpenAI-compatible model can be the brain, by default
Reflection's Beam (`https://api.reflection.ai/openai/v1`, `Beam-1.0-501B-A23B`, key `REFLECTION_API_KEY`).

Per task (one cell):
  1. stage the HAR as an RAE run: `<cell>/rae/out/har/<run_id>/recording.har` (plus a `session.har` copy,
     because goal.md calls the file `session.har`); RAE's own history is not needed;
  2. run `engineer <run_id> --fresh -p <goal.md> -o <cell>/rae/out --json` with an isolated HOME and cwd.
     goal.md replaces RAE's original goal, so the agent is told our `Client` interface. RAE's own system
     prompt is left untouched (it says "hardcode credentials", which conflicts with goal.md; that is part
     of what we measure);
  3. read `<cell>/rae/out/scripts/<run_id>/api_client.py` and write `client.py` through a thin adapter:
       - it defines `class Client`            -> copied unchanged (adapter "none")
       - one class takes `base_url` in `__init__` -> same code plus the line `Client = <Name>` (adapter "alias")
       - otherwise                             -> no client.py, status `no_client_class`
     No other change is made to RAE's code. The adapter used is logged in `solve_log.json`;
  4. grade with `har2client.grade` exactly like `run_batch.py` (this script reuses its cell loop, resume,
     results.csv and summary).

One OpenCode server (opencode-ai 1.18.4, the version RAE 0.14.0 was tested with; 2.x has another API) is
started for the whole batch with the provider config in `OPENCODE_CONFIG_CONTENT`; the API key stays in the
environment (`{env:NAME}`) and is never written to disk or printed.

Usage:
  uv run python scripts/run_rae.py --tasks runs/tierb50 --out runs/rae_beam --effort high
  # plumbing check with the fake server (no key, no cost):
  uv run python scripts/fake_openai_server.py --tasks runs/tierb50 --port 8099 &
  FAKE_KEY=x uv run python scripts/run_rae.py --tasks runs/tierb50 --out /tmp/rae_dry \\
      --base-url http://127.0.0.1:8099/v1 --model fake-model --api-key-env FAKE_KEY
Exit code: 0 done, 2 bad setup, 3 stopped by the daily limit.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_batch as rb  # noqa: E402

from har2client import runner  # noqa: E402
from har2client.solve import beam, openai_compat  # noqa: E402

PROVIDER_ID = "h2c"
OPENCODE_PACKAGE = "opencode-ai@1.18.4"
DEFAULT_BASE_URL = beam.BASE_URL
DEFAULT_MODEL = beam.MODEL
DEFAULT_KEY_ENV = beam.API_KEY_ENV
DEFAULT_RAE_DIR = Path(os.environ.get("RAE_DIR", "~/Code/reverse-api-engineer")).expanduser()
DEFAULT_RAE_VENV = Path("~/.cache/har2client/rae-venv").expanduser()
DEFAULT_TIMEOUT_S = 900.0
SERVER_START_TIMEOUT_S = 240.0  # first start downloads opencode and the provider package
DEFAULT_CONTEXT, DEFAULT_MAX_OUTPUT = 131_072, 32_768
RAE_CLIENT_NAME = "api_client.py"
DAILY_MARKERS = ("daily_allowance_exhausted",)
RUN_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]")


# ---- OpenCode / RAE configuration (pure) ----------------------------------


def opencode_config(
    base_url: str, model: str, api_key_env: str, effort: str, context: int = DEFAULT_CONTEXT,
    max_output: int = DEFAULT_MAX_OUTPUT,
) -> dict:
    """OpenCode config (`OPENCODE_CONFIG_CONTENT`) for one OpenAI-compatible provider; the key is read from the env."""
    return {
        "provider": {
            PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "har2client baseline",
                "options": {"baseURL": base_url, "apiKey": f"{{env:{api_key_env}}}"},
                "models": {
                    model: {
                        "name": model,
                        "tool_call": True,
                        "reasoning": True,
                        "limit": {"context": context, "output": max_output},
                        "options": {"reasoningEffort": openai_compat.EFFORT_ALIASES.get(effort, effort)},
                    }
                },
            }
        }
    }


def rae_config(server_url: str, model: str) -> dict:
    """Contents of `<HOME>/.reverse-api/config.json`: engineer with OpenCode, Python output, no copy to cwd."""
    return {
        "sdk": "opencode",
        "opencode_provider": PROVIDER_ID,
        "opencode_model": model,
        "opencode_base_url": server_url,
        "opencode_auto_start": False,
        "output_language": "python",
        "real_time_sync": False,
    }


def run_id_for(task_dir: Path) -> str:
    return ("h2c_" + RUN_ID_CHARS.sub("_", Path(task_dir).name))[:64]


def stage_run(task_dir: Path, rae_out: Path, run_id: str) -> Path:
    """Put the task's HAR where RAE's `engineer` looks for it. Only the visible session file is copied."""
    session = beam.find_session_file(task_dir)
    if not session.endswith(".har"):
        raise ValueError(f"RAE takes HAR input; {task_dir} has {session}")
    har_dir = rae_out / "har" / run_id
    har_dir.mkdir(parents=True, exist_ok=True)
    text = beam._read_visible(task_dir, session)
    (har_dir / "recording.har").write_text(text, encoding="utf-8")
    (har_dir / "session.har").write_text(text, encoding="utf-8")  # goal.md names the file `session.har`
    return har_dir / "recording.har"


# ---- adapter (pure) --------------------------------------------------------


def adapt_client(source: str) -> tuple[str | None, str]:
    """(client.py text or None, adapter name) for RAE's `api_client.py`. See the module docstring."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, "syntax_error"
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if any(c.name == "Client" for c in classes):
        return source, "none"
    takes_base_url = []
    for cls in classes:
        init = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
        if init is not None and any(a.arg == "base_url" for a in init.args.args + init.args.kwonlyargs):
            takes_base_url.append(cls.name)
    if len(takes_base_url) == 1:
        return source.rstrip("\n") + f"\n\n\nClient = {takes_base_url[0]}  # added by run_rae.py\n", "alias"
    return None, "no_client_class"


def hidden_mentions(messages_path: Path, task_dir: Path, tasks_root: Path) -> list[str]:
    """Hidden file names or task paths that show up in RAE's message log (the agent looked outside its run dir)."""
    try:
        text = messages_path.read_text(encoding="utf-8")
    except OSError:
        return []
    needles = [*beam.HIDDEN_FILES, str(Path(task_dir).resolve()), str(Path(tasks_root).resolve())]
    return [n for n in needles if n in text]


def parse_result(stdout: str) -> dict:
    """The JSON document `engineer --json` prints (last line that is a JSON object); {} if none."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


# ---- one task ---------------------------------------------------------------


@dataclass(frozen=True)
class RaeSettings:
    rae_bin: str
    server_url: str
    model: str
    base_url: str
    tasks_root: Path
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    stdout: str
    timed_out: bool


def run_rae_process(cmd: list[str], env: dict[str, str], cwd: Path, timeout_s: float, log_path: Path) -> ProcessResult:
    """Run RAE in its own process group; stderr goes to `log_path`; the group is killed on timeout."""
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=log, start_new_session=True, text=True)
        try:
            out, _ = proc.communicate(timeout=timeout_s)
            return ProcessResult(proc.returncode, out or "", False)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            return ProcessResult(None, out or "", True)


def _usage(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    raw = usage.get("raw") or {}
    return {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "reasoning_tokens": raw.get("reasoning_tokens"),
    }


def rae_solve(
    task_dir: Path,
    out_path: Path,
    effort: str = "high",
    har_mode: str = "raw",
    *,
    settings: RaeSettings,
    client: Any = None,
    process: Callable[..., ProcessResult] = run_rae_process,
) -> dict:
    """Same call shape as `beam.solve`: writes `client.py` and `solve_log.json` next to `out_path`; returns the log."""
    if har_mode != "raw":
        raise ValueError("RAE reads the raw HAR; use --har-mode raw")
    task_dir, out_path = Path(task_dir), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = out_path.parent / "rae"
    shutil.rmtree(work, ignore_errors=True)  # a half-finished earlier attempt must not leak into this one
    rae_out, home, cwd = work / "out", work / "home", work / "cwd"
    for d in (rae_out, home / ".reverse-api", cwd):
        d.mkdir(parents=True, exist_ok=True)
    (home / ".reverse-api" / "config.json").write_text(
        json.dumps(rae_config(settings.server_url, settings.model), indent=2), encoding="utf-8")
    run_id = run_id_for(task_dir)
    stage_run(task_dir, rae_out, run_id)
    goal = beam._read_visible(task_dir, "goal.md")

    cmd = [settings.rae_bin, "engineer", run_id, "--fresh", "-p", goal, "-o", str(rae_out), "--json"]
    env = {
        "HOME": str(home), "PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "TERM": "dumb",
        "NO_COLOR": "1", "OPENCODE_BASE_URL": settings.server_url, "RAE_OPENCODE_AUTO_START": "0",
    }
    log: dict[str, Any] = {
        "provider": "reverse-api-engineer+opencode", "model": settings.model, "base_url": settings.base_url,
        "effort": effort, "har_mode": har_mode, "run_id": run_id, "usage": {}, "rate_limit_headers": {},
        "wall_time_s": None, "status": None, "adapter": None, "rae_returncode": None, "rae_error": None,
        "hidden_mentions": [],
    }
    started = time.monotonic()
    result = process(cmd, env, cwd, settings.timeout_s, work / "rae.log")
    log["wall_time_s"] = round(time.monotonic() - started, 3)
    payload = parse_result(result.stdout)
    (work / "rae_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.update(rae_returncode=result.returncode, usage=_usage(payload), rae_error=payload.get("error"))
    log["hidden_mentions"] = hidden_mentions(rae_out / "messages" / f"{run_id}.jsonl", task_dir, settings.tasks_root)

    try:
        log_text = (work / "rae.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    if any(m in log_text or m in json.dumps(payload) for m in DAILY_MARKERS):
        log["status"] = "daily_allowance_exhausted"
        beam._write_log(out_path, log)
        raise beam.DailyAllowanceExhausted("RAE reported a spent daily allowance")

    produced = rae_out / "scripts" / run_id / RAE_CLIENT_NAME
    if result.timed_out:
        log["status"] = "timeout"
    elif not produced.is_file():
        log["status"] = "no_code"
    else:
        code, adapter = adapt_client(produced.read_text(encoding="utf-8"))
        log["adapter"] = adapter
        if code is None:
            log["status"] = "no_client_class" if adapter == "no_client_class" else "no_code"
        else:
            out_path.write_text(code, encoding="utf-8")
            log["status"] = "ok"
    beam._write_log(out_path, log)
    return log


# ---- OpenCode server --------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/global/health", timeout=2) as resp:
            return resp.status == 200 and "version" in json.loads(resp.read().decode())
    except (OSError, ValueError, urllib.error.URLError):
        return False


def opencode_env(config: dict, home: Path, environ: dict[str, str]) -> dict[str, str]:
    """Environment of the OpenCode server. It keeps the key variable of `environ` (the config reads `{env:NAME}`),
    and turns off the self-update and the models.dev catalogue fetch, so the model endpoint is the only host it needs."""
    env = {**environ, "HOME": str(home), "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
           "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1"}
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    return env


@contextmanager
def opencode_server(config: dict, work_dir: Path, package: str = OPENCODE_PACKAGE) -> Iterator[str]:
    """Start `npx opencode-ai serve` with `config`; yield its URL; stop it (its own process group) on exit."""
    npx = shutil.which("npx")
    if npx is None or shutil.which("node") is None:
        raise RuntimeError("node/npx not found; install Node.js 20+")
    home, cwd = work_dir / "_opencode_home", work_dir / "_opencode_cwd"
    home.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = opencode_env(config, home, dict(os.environ))
    with open(work_dir / "_opencode_server.log", "wb") as log:
        proc = subprocess.Popen([npx, "-y", package, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                                env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True)
        try:
            deadline = time.monotonic() + SERVER_START_TIMEOUT_S
            while not server_healthy(url):
                if proc.poll() is not None:
                    raise RuntimeError(f"opencode server exited ({proc.returncode}); see {work_dir / '_opencode_server.log'}")
                if time.monotonic() > deadline:
                    raise RuntimeError(f"opencode server not healthy after {SERVER_START_TIMEOUT_S:.0f}s")
                time.sleep(0.5)
            yield url
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)


def ensure_rae(rae_dir: Path, venv: Path) -> str:
    """Path of the `reverse-api-engineer` executable; installs RAE into `venv` (not into the clone) when missing."""
    exe = venv / "bin" / "reverse-api-engineer"
    if not exe.is_file():
        if not (rae_dir / "pyproject.toml").is_file():
            raise RuntimeError(f"reverse-api-engineer clone not found at {rae_dir} (set --rae-dir or RAE_DIR)")
        venv.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["uv", "sync", "--quiet", "--project", str(rae_dir)], check=True,
                       env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv)})
    return str(exe)


# ---- CLI ---------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run reverse-api-engineer on every task and grade its client.")
    p.add_argument("--tasks", required=True, type=Path, help="directory of task dirs (each has task.json)")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--effort", default="high", choices=beam.EFFORTS, help="reasoning effort for the model (one per batch)")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S, help="RAE time limit per task, seconds")
    p.add_argument("--grade-timeout", type=float, default=runner.DEFAULT_TIMEOUT_S)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--api-key-env", default=DEFAULT_KEY_ENV)
    p.add_argument("--context", type=int, default=DEFAULT_CONTEXT, help="model context length told to OpenCode")
    p.add_argument("--max-output", type=int, default=DEFAULT_MAX_OUTPUT)
    p.add_argument("--rae-dir", type=Path, default=DEFAULT_RAE_DIR)
    p.add_argument("--rae-venv", type=Path, default=DEFAULT_RAE_VENV)
    p.add_argument("--opencode-package", default=OPENCODE_PACKAGE)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.tasks.is_dir() or not rb.find_tasks(args.tasks):
        print(f"error: no task dirs (with task.json) in {args.tasks}", file=sys.stderr)
        return rb.EXIT_SETUP
    if args.runs < 1 or args.concurrency < 1:
        print("error: --runs and --concurrency must be at least 1", file=sys.stderr)
        return rb.EXIT_SETUP
    rb.load_env_key(rb.ENV_PATH, os.environ, args.api_key_env)
    if not os.environ.get(args.api_key_env):
        print(f"error: {args.api_key_env} is not set (environment or {rb.ENV_PATH})", file=sys.stderr)
        return rb.EXIT_SETUP
    try:
        rae_bin = ensure_rae(args.rae_dir, args.rae_venv)
        args.out.mkdir(parents=True, exist_ok=True)
        config = opencode_config(args.base_url, args.model, args.api_key_env, args.effort, args.context, args.max_output)
        with opencode_server(config, args.out, args.opencode_package) as server_url:
            settings = RaeSettings(rae_bin, server_url, args.model, args.base_url, args.tasks.resolve(), args.timeout_s)
            result = rb.run_batch(
                args.tasks, args.out, efforts=[args.effort], har_modes=["raw"], runs=args.runs,
                concurrency=args.concurrency, solve_fn=functools.partial(rae_solve, settings=settings),
                timeout=args.grade_timeout,
            )
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return rb.EXIT_SETUP
    print()
    print(rb.summarize(result.rows))
    print(f"results: {args.out / 'results.csv'}")
    if result.remaining:
        print(f"\n{len(result.remaining)} cells remaining. Run the same command to resume.")
    return rb.EXIT_STOPPED if result.stopped else rb.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
