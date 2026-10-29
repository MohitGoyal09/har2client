import textwrap
import time

from har2client import runner

TESTS = [{"method": "ping", "kwargs": {"x": 1}}, {"method": "ping", "kwargs": {"x": 2}}]


def client(tmp_path, code):
    p = tmp_path / "client.py"
    p.write_text(textwrap.dedent(code))
    return p


def run(tmp_path, code, timeout=20, tests=TESTS):
    return runner.run_client(client(tmp_path, code), "http://x/y", "key123", tests, timeout_s=timeout)


def test_returns_values_and_passes_constructor_args(tmp_path):
    out = run(
        tmp_path,
        """
        class Client:
            def __init__(self, base_url, api_key):
                self.v = (base_url, api_key)
            def ping(self, x):
                return {"x": x, "v": list(self.v), "none": None}
        """,
    )
    assert out["timed_out"] is False
    r = out["results"]
    assert [x["index"] for x in r] == [0, 1] and all(x["ok"] for x in r)
    assert r[0]["return"] == {"x": 1, "v": ["http://x/y", "key123"], "none": None}
    assert r[0]["t_start"] <= r[0]["t_end"] <= r[1]["t_start"]


def test_raising_client_is_exception(tmp_path):
    out = run(tmp_path, "class Client:\n    def __init__(self, a, b): pass\n    def ping(self, x): raise ValueError('boom')\n")
    assert [x["ok"] for x in out["results"]] == [False, False]
    assert "boom" in out["results"][0]["error"] and "ValueError" in out["results"][0]["error"]


def test_missing_method_and_broken_module_are_errors(tmp_path):
    out = run(tmp_path, "class Client:\n    def __init__(self, a, b): pass\n")
    assert not out["results"][0]["ok"] and "ping" in out["results"][0]["error"]
    out = run(tmp_path, "class Client(:\n")
    assert [x["ok"] for x in out["results"]] == [False, False]
    assert "SyntaxError" in out["results"][0]["error"]


def test_sleeping_client_times_out_and_is_killed(tmp_path):
    t0 = time.time()
    out = run(tmp_path, "import time\nclass Client:\n    def __init__(self,a,b): pass\n    def ping(self,x): time.sleep(3600)\n", timeout=2)
    assert out["timed_out"] is True and out["results"] == []
    assert time.time() - t0 < 15


def test_partial_results_survive_a_timeout(tmp_path):
    out = run(
        tmp_path,
        "import time\nclass Client:\n    def __init__(self,a,b): pass\n"
        "    def ping(self,x):\n        if x == 2: time.sleep(3600)\n        return x\n",
        timeout=3,
    )
    assert out["timed_out"] is True
    assert [x["return"] for x in out["results"]] == [1]


def test_unserializable_return_is_flagged(tmp_path):
    out = run(tmp_path, "class Client:\n    def __init__(self,a,b): pass\n    def ping(self,x): return object()\n")
    assert out["results"][0]["ok"] is True and out["results"][0]["unserializable"] is True


def test_child_does_not_inherit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "leak-me")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:1")
    out = run(
        tmp_path,
        "import os\nclass Client:\n    def __init__(self,a,b): pass\n"
        "    def ping(self,x): return [os.environ.get('OPENAI_API_KEY'), os.environ.get('HTTP_PROXY')]\n",
    )
    assert out["results"][0]["return"] == [None, None]


def test_child_cwd_is_an_empty_temp_dir_and_argv_and_env_name_no_task_dir(tmp_path):
    task_dir = tmp_path / "task_dir_marker"
    task_dir.mkdir()
    for name in ("tests.json", "ref_client.py", "engine.json", "op_table.json", "task.json"):
        (task_dir / name).write_text("{}")
    code = (
        "import os, sys\nclass Client:\n    def __init__(self,a,b): pass\n"
        "    def ping(self,x): return [os.listdir('.'), os.getcwd(), sys.argv, dict(os.environ)]\n"
    )
    out = run(tmp_path, code)
    listing, cwd, argv, env = out["results"][0]["return"]
    assert listing == [] and cwd != str(task_dir) and "h2c_run_" in cwd
    assert "task_dir_marker" not in " ".join(argv) + " ".join(env.values())


def _alive(pid):
    import os

    if os.name == "nt":
        # os.kill(pid, 0) raises WinError 87 for a dead PID on Windows.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_background_process_of_a_client_is_killed_after_a_normal_exit(tmp_path):
    pid_file = tmp_path / "bg.pid"
    t0 = time.time()
    out = run(
        tmp_path,
        f"""
        import subprocess, sys
        class Client:
            def __init__(self, a, b):
                p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
                open({str(pid_file)!r}, "w").write(str(p.pid))
            def ping(self, x): return x
        """,
        timeout=30,
    )
    assert out["timed_out"] is False and [r["return"] for r in out["results"]] == [1, 2]
    assert time.time() - t0 < 15  # the orphan must not hold the run until the timeout
    pid = int(pid_file.read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid)


def test_stderr_flood_is_killed_early_and_only_a_tail_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_STDERR_BYTES", 200_000)
    t0 = time.time()
    out = run(
        tmp_path,
        "import sys\nclass Client:\n    def __init__(self,a,b): pass\n"
        "    def ping(self,x):\n        while True: sys.stderr.write('e' * 1000 + '\\n')\n",
        timeout=60,
    )
    assert out["output_overflow"] is True and out["timed_out"] is False
    assert time.time() - t0 < 20
    assert len(out["stderr"]) <= runner.STDERR_TAIL_CHARS


def test_oversized_return_value_is_reported_as_unserializable(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_RECORD_BYTES", 1000)
    tests_file, out_file = tmp_path / "t.json", tmp_path / "o.jsonl"
    tests_file.write_text('[{"method": "ping", "kwargs": {"x": 1}}]')
    code = client(tmp_path, "class Client:\n    def __init__(self,a,b): pass\n    def ping(self,x): return 'z' * 5000\n")
    runner._child_main(str(code), "http://x", "k", str(tests_file), str(out_file))  # in-process: the limit is a module constant
    record = __import__("json").loads(out_file.read_text().splitlines()[0])
    assert record["ok"] is True and record["unserializable"] is True and len(record["return"]) <= 200


def test_client_with_a_lingering_thread_does_not_hold_the_run_until_the_timeout(tmp_path):
    t0 = time.time()
    out = run(
        tmp_path,
        """
        import threading, time
        class Client:
            def __init__(self, a, b):
                threading.Thread(target=time.sleep, args=(600,)).start()  # non-daemon: python would wait for it at exit
            def ping(self, x): return x
        """,
        timeout=40,
    )
    assert [r["return"] for r in out["results"]] == [1, 2]
    assert out["timed_out"] is False and out["returncode"] == 0
    assert time.time() - t0 < 20


def test_remove_tree_retries_a_transient_windows_sharing_violation(tmp_path, monkeypatch):
    import shutil

    target = tmp_path / "h2c_run_check"
    target.mkdir()
    (target / "err.txt").write_text("x")
    real_rmtree, calls = shutil.rmtree, []

    def flaky(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(13, "sharing violation")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runner.shutil, "rmtree", flaky)
    runner._remove_tree(target)
    assert len(calls) == 2 and not target.exists()
