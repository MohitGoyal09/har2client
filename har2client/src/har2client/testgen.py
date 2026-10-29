"""Hidden test cases: calls with fresh arguments (not the ones in the recorded session)."""

from __future__ import annotations

import random

from har2client import ops
from har2client.player import gen_args

CALLS_PER_OP = 2
MAX_TRIES = 25

_PY_TYPES = {"int": int, "float": (int, float), "bool": bool, "str": str, "list": list, "dict": dict}


class ArgTypeMismatch(RuntimeError):
    """A test or recorded argument does not match the Python type shown in goal.md.
    This is a harness bug, not an unusable API, so it must never be treated as a skip."""


def _type_ok(value: object, type_name: str) -> bool:
    if type_name != "bool" and isinstance(value, bool):  # bool is an int subclass; do not let it pass
        return False
    return isinstance(value, _PY_TYPES[type_name])


def check_arg_types(op_table: dict, op_id: str, kwargs: dict) -> None:
    """Raise ArgTypeMismatch unless every kwarg has the type `ops.signature` shows in goal.md."""
    op = next(o for o in op_table["operations"] if o["op_id"] == op_id)
    declared = {a["py_name"]: a["type"] for a in ops.ordered_args(op)}
    for name, value in kwargs.items():
        want = declared.get(name)
        if want is not None and not _type_ok(value, want):
            raise ArgTypeMismatch(
                f"{op_id}.{name}: goal.md says {want}, got {type(value).__name__} {value!r}"
            )


def _optional_params(op: dict) -> list[str]:
    """py_names of the optional query/path params: each must appear in at least one test of the op."""
    return [p["py_name"] for p in op["params"] if not p["required"] and p["in"] in ("query", "path")]


def make_tests(op_table: dict, goal_ops: list[str], seed: int | str, avoid: list[dict] | None = None) -> list[dict]:
    """Two calls per goal op: [{"method": op_id, "kwargs": {...}}]. Required args always,
    optional args with p=0.5. Coverage rule: every optional query/path param is given to at least one
    of the op's calls (an optional boolean or integer param is one of them, so a bool and an int value are
    covered whenever the op has such params). Each optional param is forced into one randomly chosen call,
    so two calls always suffice. Kwargs differ from `avoid` (recorded calls) and from each other
    when the argument space allows it."""
    rng = random.Random(f"{seed}:tests")
    by_id = {o["op_id"]: o for o in op_table["operations"]}
    used: list[dict] = list(avoid or [])
    tests = []
    for op_id in goal_ops:
        op = by_id[op_id]
        owners = {name: rng.randrange(CALLS_PER_OP) for name in _optional_params(op)}
        for call in range(CALLS_PER_OP):
            forced = frozenset(name for name, owner in owners.items() if owner == call)
            kwargs = gen_args(op, rng, include=forced)
            for _ in range(MAX_TRIES):
                if not kwargs or kwargs not in used:
                    break
                kwargs = gen_args(op, rng, include=forced)
            used.append(kwargs)
            tests.append({"method": op_id, "kwargs": kwargs})
    return tests
