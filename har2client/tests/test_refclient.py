import inspect
from pathlib import Path

from har2client import ops, refclient

FIX = Path(__file__).parent / "fixtures"


def source(name="bookshelf_apikey.yaml"):
    t = ops.normalize(ops.load_spec(FIX / name), "acme:bookshelf")
    return t, refclient.generate_client_source(t)


def load_client(src):
    ns = {}
    exec(compile(src, "client.py", "exec"), ns)  # noqa: S102 - our own generated code
    return ns["Client"]


def test_generated_source_defines_client_with_all_ops_and_signatures():
    t, src = source()
    Client = load_client(src)
    for op in t["operations"]:
        sig = inspect.signature(getattr(Client, op["op_id"]))
        assert list(sig.parameters)[0] == "self"
        names = [a["py_name"] for a in ops.ordered_args(op)]
        assert list(sig.parameters)[1:] == names
        for a in ops.ordered_args(op):
            assert (sig.parameters[a["py_name"]].default is inspect.Parameter.empty) == a["required"]
    assert list(inspect.signature(Client.__init__).parameters)[1:] == ["base_url", "api_key"]


def test_source_has_no_host_secret_or_base_path():
    t, src = source()
    assert "bookshelf.example" not in src and "127.0.0.1" not in src
    assert "/v1" not in src
    assert "import httpx" in src


def test_path_params_are_quoted_and_built_from_template():
    t, src = source()
    assert "quote(" in src and "/books/" in src


def test_auth_variants_are_in_source():
    assert "X-API-Key" in source()[1]
    assert "Bearer" in source("bookshelf_bearer.yaml")[1]
    assert "b64encode" in source("bookshelf_basic.yaml")[1]
