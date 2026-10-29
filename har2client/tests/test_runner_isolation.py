"""The child runs a client without a path into the task dir and without `har2client` (grader isolation).

Not an OS sandbox (see runner.py): these tests cover the two holes a red-team review found."""

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from har2client import build_task, grade, ops, refclient, runner

FIX = Path(__file__).parent / "fixtures"

# Searches upward from its own file for a hidden file, loads the reference client from it, and uses that.
CHEAT_REF = '''
from pathlib import Path
_ns = {}
for _parent in Path(__file__).resolve().parents:
    _ref = _parent / "ref_client.py"
    if _ref.is_file():
        exec(compile(_ref.read_text(), str(_ref), "exec"), _ns)
        break
Client = _ns["Client"]
'''

# Searches upward for tests.json and engine.json and reports whether it found either.
CHEAT_FIND = '''
from pathlib import Path
_found = [str(p / n) for p in Path(__file__).resolve().parents for n in ("tests.json", "engine.json", "ref_client.py", "task.json") if (p / n).is_file()]
class Client:
    def __init__(self, base_url, api_key):
        pass
    def ping(self, x):
        return {"found": _found}
'''

IMPORT_HAR2CLIENT = '''
import har2client
class Client:
    def __init__(self, base_url, api_key):
        pass
    def ping(self, x):
        return "imported"
'''


@pytest.fixture(scope="module")
def task_dir(tmp_path_factory):
    spec = ops.load_spec(FIX / "bookshelf_apikey.yaml")
    d = build_task.build_task(spec, "acme:bookshelf", tmp_path_factory.mktemp("t"), noise=0.7, seed=3)
    (d / "ref_client.py").write_text(refclient.generate_client_source(json.loads((d / "op_table.json").read_text())))
    (d / "tests.json").exists() or pytest.fail("build_task wrote no tests.json")
    return d


def place(task_dir, tmp_path, code, depth=2):
    """Copy the task dir and put the client `depth` levels below it (like runs/batch/<step>/<task>/<cell>/client.py)."""
    task = tmp_path / "task"
    shutil.copytree(task_dir, task)
    cell = task.joinpath(*[f"d{i}" for i in range(depth)])
    cell.mkdir(parents=True)
    client = cell / "client.py"
    client.write_text(textwrap.dedent(code))
    return task, client


def test_normal_reference_client_still_scores_one(task_dir, tmp_path):
    g = grade.grade(task_dir, task_dir / "ref_client.py")
    assert g["pass_rate"] == 1.0 and g["total"] > 0


def test_client_cannot_load_ref_client_through_file_parents(task_dir, tmp_path):
    task, client = place(task_dir, tmp_path, CHEAT_REF)
    g = grade.grade(task, client)
    assert g["pass_rate"] == 0.0
    assert "KeyError" in g["runner"]["stderr"] or g["failure_counts"].get("exception")


@pytest.mark.parametrize("depth", [0, 1, 2, 4])
def test_client_finds_no_hidden_file_above_its_file(tmp_path, depth):
    task = tmp_path / "task"
    cell = task.joinpath(*[f"d{i}" for i in range(depth)])
    cell.mkdir(parents=True)
    for name in ("tests.json", "engine.json", "ref_client.py", "task.json"):
        (task / name).write_text("{}")
    client = cell / "client.py"
    client.write_text(CHEAT_FIND)
    out = runner.run_client(client, "http://x", "k", [{"method": "ping", "kwargs": {"x": 1}}])
    assert out["results"][0]["return"] == {"found": []}, out["results"][0]


def test_child_arguments_hold_no_path_into_the_task_dir(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    client = task / "client.py"
    client.write_text("import sys\nclass Client:\n    def __init__(self, b, k):\n        pass\n    def ping(self, x):\n        return sys.argv\n")
    out = runner.run_client(client, "http://x", "k", [{"method": "ping", "kwargs": {"x": 1}}])
    argv = out["results"][0]["return"]
    assert argv and not any(str(task) in arg for arg in argv), argv
    assert not any(str(Path(runner.__file__).resolve().parent) in arg for arg in argv), argv


def test_client_cannot_import_har2client(task_dir, tmp_path):
    out = runner.run_client(_write(tmp_path, IMPORT_HAR2CLIENT), "http://x", "k", [{"method": "ping", "kwargs": {"x": 1}}])
    assert not out["results"][0]["ok"]
    assert "ImportError" in out["results"][0]["error"] or "ModuleNotFoundError" in out["results"][0]["error"]


def test_client_cannot_import_har2client_submodule_or_via_importlib(tmp_path):
    code = (
        "import importlib\nclass Client:\n    def __init__(self, b, k):\n        pass\n"
        "    def ping(self, x):\n        return importlib.import_module('har2client.grade').__name__\n"
    )
    out = runner.run_client(_write(tmp_path, code), "http://x", "k", [{"method": "ping", "kwargs": {"x": 1}}])
    assert not out["results"][0]["ok"] and "Error" in out["results"][0]["error"]


def test_httpx_and_requests_stay_importable(tmp_path):
    code = (
        "import httpx, requests\nclass Client:\n    def __init__(self, b, k):\n        pass\n"
        "    def ping(self, x):\n        return [httpx.__name__, requests.__name__]\n"
    )
    out = runner.run_client(_write(tmp_path, code), "http://x", "k", [{"method": "ping", "kwargs": {"x": 1}}])
    assert out["results"][0]["return"] == ["httpx", "requests"], out


def test_client_file_is_a_copy_inside_the_work_dir(tmp_path):
    code = "import os\nclass Client:\n    def __init__(self, b, k):\n        pass\n    def ping(self, x):\n        return [__file__, os.getcwd()]\n"
    src = _write(tmp_path, code)
    out = runner.run_client(src, "http://x", "k", [{"method": "ping", "kwargs": {"x": 1}}])
    file, cwd = out["results"][0]["return"]
    assert Path(file).name == "client.py" and file != str(src.resolve())
    assert str(tmp_path) not in file
    assert Path(file).resolve().parent.parent == Path(cwd).resolve().parent  # <tmp>/client/client.py next to <tmp>/cwd


def _write(tmp_path, code):
    p = tmp_path / "client.py"
    p.write_text(code)
    return p
