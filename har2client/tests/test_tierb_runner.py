import json
import subprocess
import sys
import textwrap
from pathlib import Path

from har2client import runner

CODE = """
class Client:
    def __init__(self, base_url, username, password):
        self.v = [base_url, username, password]
    def ping(self, x):
        return {"v": self.v, "x": x}
"""


def client(tmp_path, code=CODE):
    p = tmp_path / "client.py"
    p.write_text(textwrap.dedent(code))
    return p


def test_ctor_args_replace_the_single_api_key(tmp_path):
    out = runner.run_client(client(tmp_path), "http://h/v1", "", [{"method": "ping", "kwargs": {"x": 1}}], ctor_args=["alice", "pw"])
    assert out["results"][0]["return"] == {"v": ["http://h/v1", "alice", "pw"], "x": 1}


def test_default_still_passes_one_api_key(tmp_path):
    code = "class Client:\n    def __init__(self, base_url, api_key):\n        self.v = [base_url, api_key]\n    def ping(self, x):\n        return self.v\n"
    out = runner.run_client(client(tmp_path, code), "http://h", "key9", [{"method": "ping", "kwargs": {"x": 1}}])
    assert out["results"][0]["return"] == ["http://h", "key9"]


def test_child_accepts_five_or_six_arguments(tmp_path):
    tests = tmp_path / "t.json"
    tests.write_text(json.dumps([{"method": "ping", "kwargs": {"x": 2}}]))
    out = tmp_path / "o.jsonl"
    script = str(Path(runner.__file__).resolve())
    subprocess.run([sys.executable, "-I", script, str(client(tmp_path)), "http://h", "ignored", str(tests), str(out), json.dumps(["u", "p"])], check=True, cwd=tmp_path)
    assert json.loads(out.read_text().splitlines()[0])["return"]["v"] == ["http://h", "u", "p"]
