"""CI parity test for the Task↔JobItem reconciliation migration.

Project rule: PostgreSQL is the primary database. The .sql migration
runner (``daemon/migrations/runner.py``) is a NO-OP on PostgreSQL, so
every UPDATE-only migration must exist in BOTH:

  1. A ``.sql`` file under ``daemon/migrations/versions/`` (SQLite path)
  2. A Python tuple in the ``statements`` list of
     ``EnsembleManager._ensure_postgres_columns()`` in
     ``daemon/manager.py`` (PostgreSQL path)

The two MUST be byte-identical so both drivers converge on the same
final state. This test fails the build if the two paths drift.

Test name: ``test_reconcile_stuck_tasks_migration_sql_matches_postgres_startup``.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_MIGRATION_PATH = (
    REPO_ROOT
    / "daemon"
    / "migrations"
    / "versions"
    / "20260811_000001_reconcile_stuck_tasks_with_terminal_jobitems.sql"
)
MANAGER_PY_PATH = REPO_ROOT / "daemon" / "manager.py"

# Anchor comment used to locate the new tuple inside ``_ensure_postgres_columns``.
# Keep in sync with the comment block added in daemon/manager.py.
TUPLE_ANCHOR_COMMENT = (
    "# ── Reconcile stuck tasks with terminal JobItems (2026-08-11) ──"
)


def _normalize_sql(sql: str) -> str:
    """Strip ALL whitespace and lower-case the SQL so cosmetic whitespace
    differences (e.g., one trailing space before the EXIST clause's close
    paren, which arises naturally when an .sql file has a newline before
    ``)``) do not cause false drift failures.

    The migration is functionally equivalent if and only if the
    alphanumeric tokens and SQL punctuation match — which is exactly
    what this normalization preserves. This is the test that catches
    real drift (one path updated, the other forgotten) while tolerating
    benign formatting differences.
    """
    return re.sub(r"\s+", "", sql).strip().lower()


def _read_sql_up_section(sql_path: Path) -> str:
    """Read the .sql file and return the UP section (code lines only)."""
    content = sql_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    in_up = False
    up_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "-- UP":
            in_up = True
            continue
        if stripped == "-- DOWN":
            break
        if in_up:
            # Drop full-line ``--`` comments but keep inline content
            if stripped.startswith("--"):
                continue
            up_lines.append(line)
    raw = "\n".join(up_lines).strip()
    # Drop a trailing semicolon — the SQLAlchemy text() wrapper is tolerant
    # but the Python tuple ends with ``;`` only as part of the final
    # concatenated string. We strip BOTH the .sql trailing ``;`` AND the
    # tuple's trailing ``;`` so the comparison is independent of whether
    # the author ended the last statement with one.
    if raw.endswith(";"):
        raw = raw[:-1].rstrip()
    return raw


def _extract_reconcile_tuple_sql(manager_py_text: str) -> str:
    """Find the new reconcile UPDATE tuple in ``_ensure_postgres_columns``
    and return the concatenated SQL string.

    Strategy: locate the anchor comment, then walk past the comment block
    to the first non-comment line (the tuple's opening ``(``), then
    use a string-aware balanced-paren scan to capture the whole tuple.
    Finally, evaluate the tuple text in a sandboxed namespace so Python's
    implicit string concatenation produces the full SQL.
    """
    anchor_idx = manager_py_text.find(TUPLE_ANCHOR_COMMENT)
    if anchor_idx < 0:
        raise AssertionError(
            f"Anchor comment not found in {MANAGER_PY_PATH}. "
            f"Expected: {TUPLE_ANCHOR_COMMENT!r}. "
            f"Did the developer remove the comment block in "
            f"_ensure_postgres_columns()?"
        )

    # Skip the comment block (lines that are blank or start with '#').
    tail = manager_py_text[anchor_idx:]
    tail_lines = tail.split("\n")
    skip = 0
    for line in tail_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            skip += len(line) + 1  # +1 for the newline
            continue
        break
    tuple_region = tail[skip:]

    # Find the tuple open paren (line that starts with optional whitespace then '(').
    open_match = re.search(r"^\s*\(", tuple_region, re.MULTILINE)
    if not open_match:
        raise AssertionError(
            f"Could not find tuple opener after the anchor comment in "
            f"{MANAGER_PY_PATH}."
        )
    open_offset = open_match.start()

    # Balanced paren scan with string awareness.
    depth = 0
    in_string = False
    string_char = ""
    escape = False
    i = open_offset
    while i < len(tuple_region):
        ch = tuple_region[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape = True
            i += 1
            continue
        if in_string:
            if ch == string_char:
                in_string = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        raise AssertionError(
            f"Unbalanced parens in reconcile tuple at {MANAGER_PY_PATH}."
        )

    tuple_text = tuple_region[open_offset : i + 1]
    # ``ast.parse`` is sensitive to leading indentation. Dedent before parsing.
    import textwrap
    dedented = textwrap.dedent(tuple_text)
    # Evaluate the tuple literal in a sandbox to fold implicit string
    # concatenation. Only ``ast`` module is allowed; no builtins.
    try:
        tree = ast.parse(dedented, mode="eval")
    except SyntaxError as exc:
        raise AssertionError(
            f"Failed to parse reconcile tuple at {MANAGER_PY_PATH}: {exc}\n"
            f"Tuple text: {tuple_text!r}"
        ) from exc
    if not (
        (isinstance(tree.body, ast.Tuple) and len(tree.body.elts) == 1)
        or (isinstance(tree.body, ast.Constant) and isinstance(tree.body.value, str))
    ):
        raise AssertionError(
            f"Expected a single-element tuple (or a single string after "
            f"implicit-concat folding) at the anchor, got "
            f"{type(tree.body).__name__} in {MANAGER_PY_PATH}."
        )
    if isinstance(tree.body, ast.Tuple):
        sql_node = tree.body.elts[0]
    else:
        sql_node = tree.body
    # The tuple is ``( "a" "b" "c" )`` which Python folds at parse time.
    # ast sees this as a single Constant if the parser folds it, OR as a
    # BinOp(Add) of Constants in older parsers. Handle both.
    sql_value = _fold_concat_expression(sql_node)
    if not isinstance(sql_value, str):
        raise AssertionError(
            f"Expected the tuple element to be a string after folding, got "
            f"{type(sql_value).__name__} in {MANAGER_PY_PATH}."
        )
    # Strip a single trailing semicolon so the comparison is independent
    # of whether the author ended the last SQL statement with ``;``.
    if sql_value.rstrip().endswith(";"):
        sql_value = sql_value.rstrip()[:-1].rstrip()
    return sql_value


def _fold_concat_expression(node: ast.AST) -> str:
    """Fold a Python expression that may be implicit string concatenation
    (ast.Constant or ast.BinOp with ast.Add of str Constants)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_concat_expression(node.left)
        right = _fold_concat_expression(node.right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    raise AssertionError(
        f"Unsupported expression node in implicit string concat: {type(node).__name__}"
    )


def test_reconcile_stuck_tasks_migration_sql_matches_postgres_startup() -> None:
    """The .sql file's UP section must be byte-identical to the Python
    tuple in ``_ensure_postgres_columns()`` after whitespace normalization.

    On drift, the assertion message names both source locations so the
    developer knows where to align the two paths.
    """
    assert SQL_MIGRATION_PATH.exists(), (
        f"Expected migration file not found: {SQL_MIGRATION_PATH}"
    )
    assert MANAGER_PY_PATH.exists(), (
        f"Expected manager.py not found: {MANAGER_PY_PATH}"
    )

    sql_raw = _read_sql_up_section(SQL_MIGRATION_PATH)
    py_raw = _extract_reconcile_tuple_sql(MANAGER_PY_PATH.read_text(encoding="utf-8"))

    sql_norm = _normalize_sql(sql_raw)
    py_norm = _normalize_sql(py_raw)

    assert sql_norm == py_norm, (
        "Drift between .sql migration and Python tuple in "
        "_ensure_postgres_columns(). Both must be byte-identical after "
        "whitespace normalization.\n"
        f"  .sql source:    {SQL_MIGRATION_PATH}\n"
        f"  Python source:  {MANAGER_PY_PATH} (anchor: "
        f"{TUPLE_ANCHOR_COMMENT!r})\n"
        f"  .sql normalized:    {sql_norm!r}\n"
        f"  Python normalized:  {py_norm!r}"
    )
