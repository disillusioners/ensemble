"""Parity test: lock the PG backfill in ``_ensure_postgres_columns`` to the
SQLite migration predicate (reviewer W2).

The idle-gate deadlock task-flag backfill is applied by two paths:

* **SQLite** — ``daemon/migrations/versions/20260810_000001_fix_idle_gate_stuck_task_flags.sql``
  (the canonical reference, applied by ``MigrationRunner`` only when the
  engine dialect is SQLite).
* **PostgreSQL** — the equivalent ``UPDATE`` statements are emitted from
  ``EnsembleManager._ensure_postgres_columns`` in ``daemon/manager.py``
  (the ``.sql`` runner is a no-op on PostgreSQL).

These two paths MUST touch the same row set, otherwise a row that one
path leaves un-backfilled on its dialect would still deadlock on the
other. The reviewer raised the concern that the PG UPDATE may touch a
*wider* row set than the SQLite migration if the two predicates ever
drift (e.g. PG missing ``AND ji.deleted_at IS NULL``, or missing
``AND q.queue_type = '...'``, or a different join condition).

This test is a static textual parity check — it does not require a live
PostgreSQL connection. It reads both source files, extracts the two
``UPDATE task ... WHERE ... EXISTS (...)`` blocks, normalizes whitespace
and case, and asserts the ``WHERE``/``EXISTS`` predicates are
character-for-character equal (modulo whitespace). The test is
dependency-light (stdlib only: ``pathlib``, ``re``).

If this test fails after editing either file, one of the two paths has
drifted and the reviewer W2 concern is now a real bug — fix the diverged
side to match the canonical SQLite migration, do NOT loosen the
normalization here.
"""

from __future__ import annotations

import re
from pathlib import Path


# Repository root (this test file lives at tests/job_queue/...).
REPO_ROOT = Path(__file__).resolve().parents[2]

PG_PATH = REPO_ROOT / "daemon" / "manager.py"
SQLITE_PATH = (
    REPO_ROOT
    / "daemon"
    / "migrations"
    / "versions"
    / "20260810_000001_fix_idle_gate_stuck_task_flags.sql"
)


# Header that marks the PG backfill block inside ``_ensure_postgres_columns``.
_PG_BLOCK_HEADER = "Idle-gate deadlock task-flag backfill (2026-08-10)"


def _extract_pg_backfill_statements(manager_text: str) -> list[str]:
    """Return the two ``UPDATE task ...`` Python string-concatenated statements.

    The statements live inside the ``statements`` list passed to
    ``self._engine.begin() as conn:`` in
    ``EnsembleManager._ensure_postgres_columns``. We extract them by
    string-concatenating the adjacent Python string literals that
    belong to each ``("...", "...")`` tuple.
    """
    header_idx = manager_text.find(_PG_BLOCK_HEADER)
    if header_idx == -1:
        raise AssertionError(
            f"PG backfill header {_PG_BLOCK_HEADER!r} not found in {PG_PATH}"
        )
    # Slice forward to the next top-level statement (the legacy-status
    # backfill that follows in the same function).
    end_marker = manager_text.find("legacy_status_backfill", header_idx)
    if end_marker == -1:
        raise AssertionError(
            "PG backfill block end marker 'legacy_status_backfill' not found"
        )
    block = manager_text[header_idx:end_marker]

    # Pull every ("...", "...", ...) tuple, concatenate the string
    # fragments inside it. The block contains exactly two
    # backfill statements (is_deferred, is_background) plus the
    # unrelated CHECK-constraint ALTERs that come before them — we
    # filter to only those that look like ``UPDATE task SET is_* = TRUE``.
    tuples = re.findall(r"\(\s*((?:\"[^\"]*\"\s*)+)\)", block)

    statements: list[str] = []
    for raw in tuples:
        joined = "".join(re.findall(r"\"([^\"]*)\"", raw))
        if joined.lstrip().upper().startswith("UPDATE TASK SET IS_"):
            statements.append(joined)
    if len(statements) != 2:
        raise AssertionError(
            f"expected 2 PG backfill UPDATE statements, found {len(statements)}: "
            f"{statements!r}"
        )
    return statements


def _extract_sqlite_backfill_statements(sql_text: str) -> list[str]:
    """Return the two ``UPDATE task ...`` statements from the SQLite migration.

    The migration file is a single ``-- UP`` / ``-- DOWN`` script with two
    ``UPDATE task ...`` blocks separated by ``;``. We strip the ``--`` line
    comments first so they do not pollute the predicate text, then split
    on ``;`` and keep only the ``UPDATE task`` statements.
    """
    sql_clean = re.sub(r"--[^\n]*", "", sql_text)
    raw_stmts = [s.strip() for s in sql_clean.split(";") if s.strip()]
    return [s for s in raw_stmts if s.lstrip().upper().startswith("UPDATE TASK")]


def _normalize_sql(sql: str) -> str:
    """Lowercase + collapse all whitespace to a single space + strip.

    Whitespace is not semantically significant in SQL, so the only
    legitimate difference between the PG and SQLite forms is the exact
    whitespace layout (newlines, indentation, consecutive spaces,
    spaces adjacent to parentheses). Collapsing to a single space and
    stripping spaces adjacent to ``(`` / ``)`` lets the assertion
    compare the *predicate text* without false negatives on layout
    drift (e.g. the SQLite form has ``SELECT 1`` on its own line, the
    PG form has it inline with ``EXISTS (``, so the raw collapsed text
    would differ by a single space).
    """
    collapsed = re.sub(r"\s+", " ", sql).strip().lower()
    # Remove spaces immediately after ``(`` and immediately before ``)``.
    collapsed = re.sub(r"\(\s+", "(", collapsed)
    collapsed = re.sub(r"\s+\)", ")", collapsed)
    return collapsed


def _predicate_only(stmt: str) -> str:
    """Return the ``WHERE ...`` clause (lowercased, whitespace-collapsed).

    Strips the leading ``UPDATE task SET is_X = TRUE`` so the assertion
    compares only the row-selection predicate, which is the part that
    determines whether the two backfill paths touch the same row set.
    """
    parts = re.split(r"\bwhere\b", stmt, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        raise AssertionError(
            f"statement has no WHERE clause: {stmt!r}"
        )
    # parts[0] is the UPDATE/SET prefix, parts[1] is the predicate.
    return _normalize_sql(parts[1])


def test_pg_and_sqlite_idle_gate_backfill_predicates_match():
    """PG ``UPDATE ... WHERE ... EXISTS (...)`` must equal SQLite's, per axis.

    Locks the PG backfill in ``_ensure_postgres_columns`` to the SQLite
    migration predicate so they cannot diverge (reviewer W2). One
    assertion per axis (defer, background) — both must hold.
    """
    manager_text = PG_PATH.read_text(encoding="utf-8")
    sql_text = SQLITE_PATH.read_text(encoding="utf-8")

    pg_stmts = _extract_pg_backfill_statements(manager_text)
    sql_stmts = _extract_sqlite_backfill_statements(sql_text)

    # Sanity: two statements on each side, in the expected order
    # (is_deferred first, is_background second).
    assert len(pg_stmts) == 2, f"expected 2 PG statements, got {len(pg_stmts)}"
    assert len(sql_stmts) == 2, f"expected 2 SQLite statements, got {len(sql_stmts)}"

    for axis, pg_stmt, sql_stmt in zip(
        ("is_deferred", "is_background"), pg_stmts, sql_stmts
    ):
        pg_pred = _predicate_only(pg_stmt)
        sql_pred = _predicate_only(sql_stmt)
        assert pg_pred == sql_pred, (
            f"{axis!r} backfill predicate drifted between PG and SQLite.\n"
            f"  PG     : {pg_pred}\n"
            f"  SQLite : {sql_pred}\n"
            f"Align the diverged side to the canonical SQLite migration in "
            f"daemon/migrations/versions/20260810_000001_fix_idle_gate_stuck_task_flags.sql. "
            f"Do NOT loosen the normalization here."
        )
