"""Run a candidate client.py in a subprocess (stdlib only; this file is also the child script).

Parent:  run_client(client_path, base_url, api_key, tests, timeout_s, ctor_args=None) -> dict
Child:   python -I runner.py <client.py> <base_url> <api_key> <tests.json> <out.jsonl> [<ctor_args JSON list>]

The client is built as `Client(base_url, api_key)`; with `ctor_args` (Tier B) it is `Client(base_url, *ctor_args)`.

Isolation (what this module does, so a client cannot read the hidden grading files through its own path):
- the client file and this script are copied into the per-run temp dir (`<tmp>/client/client.py`, `<tmp>/child_runner.py`); the child gets only paths inside it, so `__file__`
  and `sys.argv[0]` never point at the task dir or the repo, and the tests file is deleted once the child has read it;
- before the client is imported, `har2client` is made unimportable (repo and `src` entries leave `sys.path`, and a meta-path
  finder rejects the name); the stdlib, httpx and requests stay importable.

This is NOT an OS sandbox. A client can still open any file its user can read, by an absolute path it guesses or finds
(for example `/proc`, `ps`, or a path hard-coded in the client). For RL training, or for any untrusted client, run grading
in a container, or as a user with no read access to the task dirs.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

DEFAULT_TIMEOUT_S = 60
STDERR_TAIL_CHARS = 2000
MAX_STDERR_BYTES = 20_000_000  # a chatty or looping client is killed once it has written this much to stderr
MAX_RECORD_BYTES = 5_000_000  # one result line; a larger return value is reported as unserializable
POLL_S = 0.2
CLIENT_COPY_NAME = "client.py"
CHILD_SCRIPT_NAME = "child_runner.py"
HIDDEN_PACKAGE = "har2client"
CLEANUP_RETRY_S = 2.0  # Windows can hold inherited handles briefly after a killed child


# ------------------------------------------------------------------ parent


def _child_env(home: str) -> dict[str, str]:
    """Minimal environment: no inherited secrets, no proxies."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    for key in ("LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _kill(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        # Closing the job also kills descendants after their parent has exited.
        job = getattr(proc, "_h2c_job", None)
        if job is not None:
            import ctypes
            from ctypes import wintypes

            close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
            close(job)
            proc._h2c_job = None
        if proc.poll() is None:
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _attach_windows_job(proc: subprocess.Popen) -> None:
    """Keep the candidate and its descendants in a kill-on-close Windows job."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel.CloseHandle(job)
        proc.kill()
        proc.wait()
        raise
    proc._h2c_job = job


def _remove_tree(path: Path) -> None:
    """Remove the run directory, retrying Windows sharing violations.

    After a killed child, Windows may still hold its inherited stderr or cwd
    handle for a moment even though `proc.wait()` has returned. Failing the
    whole grade on that race would lose an otherwise valid timeout result.
    """
    deadline = time.monotonic() + CLEANUP_RETRY_S
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.05)


def _read_results(path: Path) -> list[dict]:
    results = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                results.append(json.loads(line))
            except ValueError:
                break  # a half-written last line after a kill
    return results


def run_client(
    client_path: str | Path,
    base_url: str,
    api_key: str,
    tests: list[dict],
    timeout_s: float = DEFAULT_TIMEOUT_S,
    ctor_args: list[str] | None = None,
) -> dict:
    """Run every test call in one child process. Returns
    {results: [{index, ok, return|error, t_start, t_end, unserializable?}], timed_out, returncode, stderr, output_overflow}.
    Results written before a timeout are kept. The child and its descendants are killed on every exit path
    (POSIX process group; Windows kill-on-close job object), so a client cannot leave background processes
    behind. stdout is discarded; stderr goes to a file (not to parent memory)."""
    tmp = tempfile.mkdtemp(prefix="h2c_run_")
    try:
        tmp_path = Path(tmp)
        work = tmp_path / "cwd"
        work.mkdir()
        tests_file, out_file = tmp_path / "tests.json", tmp_path / "out.jsonl"
        tests_file.write_text(json.dumps(tests), encoding="utf-8")
        # the child sees copies inside the temp dir only: its __file__ and argv hold no path into the task dir or the repo
        client_dir = tmp_path / "client"  # not the cwd: the client's working dir stays empty
        client_dir.mkdir()
        client_copy, child_script = client_dir / CLIENT_COPY_NAME, tmp_path / CHILD_SCRIPT_NAME
        shutil.copyfile(client_path, client_copy)
        shutil.copyfile(Path(__file__).resolve(), child_script)
        cmd = [sys.executable, "-I", "-B", str(child_script), str(client_copy), base_url, api_key, str(tests_file), str(out_file)]
        if ctor_args is not None:
            cmd.append(json.dumps(ctor_args))
        err_file = tmp_path / "err.txt"
        child_env = _child_env(str(work))
        if os.name == "nt":
            child_env["_HAR2CLIENT_JOB_GATE"] = "1"
        with open(err_file, "wb") as err:
            proc = subprocess.Popen(
                cmd, cwd=work, env=child_env, stdin=subprocess.PIPE if os.name == "nt" else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=err, start_new_session=True,
            )
            if os.name == "nt":
                _attach_windows_job(proc)
                # Candidate code cannot spawn anything before job assignment.
                try:
                    proc.stdin.write(b"1")
                    proc.stdin.close()
                except BaseException:
                    _kill(proc)
                    proc.wait()
                    raise
            timed_out = overflow = False
            deadline = time.monotonic() + timeout_s
            try:
                while True:
                    try:
                        proc.wait(timeout=max(0.0, min(POLL_S, deadline - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            timed_out = True
                            break
                        if err_file.stat().st_size > MAX_STDERR_BYTES:
                            overflow = True
                            break
            finally:
                _kill(proc)  # also reaps background processes of a client that exited normally
                proc.wait()
        return {
            "results": _read_results(out_file),
            "timed_out": timed_out,
            "returncode": None if timed_out or overflow else proc.returncode,
            "stderr": _tail(err_file),
            "output_overflow": overflow,
        }
    finally:
        _remove_tree(Path(tmp))


def _tail(path: Path) -> str:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - 4 * STDERR_TAIL_CHARS))
        data = fh.read()
    return data.decode("utf-8", errors="replace")[-STDERR_TAIL_CHARS:]


# ------------------------------------------------------------------ child


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class _BlockHidden:
    """Meta-path finder that makes the grader's own package unimportable (also for editable-install finders)."""

    @staticmethod
    def find_spec(fullname, path=None, target=None):
        if fullname == HIDDEN_PACKAGE or fullname.startswith(HIDDEN_PACKAGE + "."):
            raise ImportError(f"No module named {fullname!r}")
        return None


def _isolate_imports() -> None:
    """Drop every sys.path entry that holds the grader package (the repo `src` dir of an editable install), then block the
    name. Runs before the client is imported. The stdlib and site-packages (httpx, requests) stay."""
    def holds_package(entry: str) -> bool:
        base = Path(entry or ".")
        return (base / HIDDEN_PACKAGE).exists() or (base / f"{HIDDEN_PACKAGE}.py").exists()

    sys.path[:] = [entry for entry in sys.path if not holds_package(entry)]
    sys.meta_path.insert(0, _BlockHidden())
    sys.path_importer_cache.clear()
    for name in [n for n in sys.modules if n == HIDDEN_PACKAGE or n.startswith(HIDDEN_PACKAGE + ".")]:
        del sys.modules[name]


def _load_client(path: str):
    spec = importlib.util.spec_from_file_location("candidate_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Client


def _child_main(
    client_path: str, base_url: str, api_key: str, tests_path: str, out_path: str, ctor_json: str | None = None
) -> None:
    tests = json.loads(Path(tests_path).read_text(encoding="utf-8"))
    try:
        os.unlink(tests_path)  # the candidate must not find the test calls on disk
    except OSError:
        pass
    out = open(out_path, "w", encoding="utf-8")

    def emit(record: dict) -> None:
        line = json.dumps(record)
        if len(line) > MAX_RECORD_BYTES:
            record.update(unserializable=True, **{"return": line[:200]})
            line = json.dumps(record)
        out.write(line + "\n")
        out.flush()

    try:
        ctor_args = json.loads(ctor_json) if ctor_json is not None else [api_key]
        client = _load_client(client_path)(base_url, *ctor_args)
        setup_error = None
    except BaseException as exc:  # noqa: BLE001 - candidate code can raise anything
        client, setup_error = None, _error(exc)
        traceback.print_exc()
    for index, test in enumerate(tests):
        t_start = time.perf_counter()
        record: dict = {"index": index}
        try:
            if setup_error:
                raise RuntimeError(f"client setup failed: {setup_error}")
            value = getattr(client, test["method"])(**test["kwargs"])
            try:
                json.dumps(value)
                record.update(ok=True, **{"return": value})
            except (TypeError, ValueError):
                record.update(ok=True, unserializable=True, **{"return": repr(value)[:200]})
        except BaseException as exc:  # noqa: BLE001
            record.update(ok=False, error=_error(exc))
        record.update(t_start=t_start, t_end=time.perf_counter())
        emit(record)
    out.close()


if __name__ == "__main__":
    if os.environ.pop("_HAR2CLIENT_JOB_GATE", None) == "1":
        if sys.stdin.buffer.read(1) != b"1":
            sys.exit(1)  # parent exited before assigning the process to its job
    _isolate_imports()  # only in the child process: a test that calls _child_main in-process keeps its own imports
    _child_main(*sys.argv[1:7])
    sys.stderr.flush()
    os._exit(0)  # every result is written: do not wait at exit for threads the candidate left running
