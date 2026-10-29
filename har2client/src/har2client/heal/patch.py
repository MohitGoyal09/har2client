"""Patch size between `client_v1.py` and a submitted `client.py` (reported separately; it is not part of the score).

- Lines: a line-level diff (trailing spaces ignored). A replaced line counts once as removed and once as added.
- AST nodes: both files are parsed, every node becomes a token (its type and its plain fields such as names,
  constants and operators, no positions), the trees are flattened in pre-order, and the two token lists are diffed.
  A change of formatting or comments costs no node."""

from __future__ import annotations

import ast
import difflib
from typing import Any

_SKIP_FIELDS = {"lineno", "col_offset", "end_lineno", "end_col_offset", "type_comment", "ctx"}


def _lines(source: str) -> list[str]:
    return [line.rstrip() for line in source.splitlines()]


def _token(node: ast.AST) -> tuple:
    fields = []
    for name, value in ast.iter_fields(node):
        if name in _SKIP_FIELDS or isinstance(value, ast.AST):
            continue
        if isinstance(value, list):
            if any(isinstance(v, ast.AST) for v in value):
                continue
            fields.append((name, repr(value)))
        else:
            fields.append((name, repr(value)))
    return (type(node).__name__, tuple(fields))


def _diff_sizes(a: list[Any], b: list[Any]) -> tuple[int, int]:
    """(removed from a, added in b) by the longest matching blocks."""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return len(a) - matched, len(b) - matched


def patch_size(old_source: str, new_source: str) -> dict:
    """{lines_removed, lines_added, changed_lines, nodes_removed, nodes_added, changed_ast_nodes}.
    The node numbers are None when either file does not parse."""
    removed, added = _diff_sizes(_lines(old_source), _lines(new_source))
    out: dict[str, Any] = {"lines_removed": removed, "lines_added": added, "changed_lines": removed + added}
    try:
        n_removed, n_added = _diff_sizes(_walk(old_source), _walk(new_source))
    except (SyntaxError, RecursionError, ValueError):
        out.update(nodes_removed=None, nodes_added=None, changed_ast_nodes=None)
    else:
        out.update(nodes_removed=n_removed, nodes_added=n_added, changed_ast_nodes=n_removed + n_added)
    return out


def _walk(source: str) -> list[tuple]:
    """Pre-order tokens (ast.walk is breadth-first, which scatters one edit; pre-order keeps it in one place)."""
    tokens: list[tuple] = []

    def visit(node: ast.AST) -> None:
        tokens.append(_token(node))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(ast.parse(source))
    return tokens
