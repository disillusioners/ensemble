"""SELECT-only guard + per-tx ``SET LOCAL statement_timeout`` (W1-P2, task 2.10a).

Pins:

1. ``ens_db_postgres_select`` rejects INSERT/UPDATE/DELETE/DDL with
   :class:`daemon.tools.ens_db_tools.SelectOnlyViolation` (also a
   ``ValueError``).
2. The per-transaction ``SET LOCAL statement_timeout`` is applied
   INSIDE the tool's own connection block — NOT engine-wide
   ``connect_args`` (architect §3.1, §7.5). Verified by listening to
   the SQLAlchemy ``before_cursor_execute`` event on the SHARED
   engine: the tool's tx must emit ``SET LOCAL statement_timeout=...``
   on a connection that did NOT have it pre-set.
3. Dual-engine coverage (architect §7.5) — the guard fires on BOTH
   SQLite and PG backends. The test file parameterizes over both
   engines via a pytest fixture (sqlite: file-backed at tmp_path +
   ``NullPool`` + ``PRAGMA journal_mode=WAL`` + ``busy_timeout=10000``
   per the repo file-backed SQLite recipe; pg: connection-string
   env-resolved, see ``QUARANTINE.md``).

Reference: ``daemon/tools/db_tools.py:97`` for the guard function;
``daemon/tools/ens_db_tools.py`` for the tool body.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from daemon.tools.ens_db_tools import (
    SelectOnlyViolation,
    _validate_select_only,
    create_ens_db_tools,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _sqlite_engine(tmp_path: Path) -> Engine:
    """File-backed SQLite engine at tmp_path (repo recipe — see QUARANTINE.md)."""
    db_path = tmp_path / "ens_db_select_only.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()
    return engine


def _maybe_pg_engine() -> Engine | None:
    """Return a PG engine if env points at a runnable instance; None otherwise."""
    url = os.environ.get("ENSEMBLE_TEST_PG_URL")
    if not url:
        return None
    return create_engine(url, poolclass=NullPool)


@pytest.fixture(
    params=["sqlite", "pg_if_available"],
    ids=["sqlite-file-backed", "pg-if-available"],
)
def shared_engine(request: Any, tmp_path: Path) -> Engine:
    """Yields a SHARED engine for the test. PG is conditional — skipped if no env."""
    if request.param == "sqlite":
        return _sqlite_engine(tmp_path)
    eng = _maybe_pg_engine()
    if eng is None:
        pytest.skip("ENSEMBLE_TEST_PG_URL not set")
    return eng


def _manager_with(engine: Engine) -> MagicMock:
    """Mock manager with the shared engine attached."""
    mgr = MagicMock()
    mgr.engine = engine
    return mgr


# ── Part 1: SELECT-only guard rejects forbidden keywords ────────────────────


class TestSelectOnlyGuard:
    """Defensive copy of the SELECT-only contract from db_tools.py:97.

    We exercise the import-through alias the tool uses
    (``from .db_tools import _validate_select_only``) so a
    refactor of db_tools.py is caught if its behavior changes.
    """

    @pytest.mark.parametrize(
        "bad_sql",
        [
            "INSERT INTO foo VALUES (1)",
            "UPDATE foo SET x=1",
            "DELETE FROM foo",
            "DROP TABLE foo",
            "CREATE TABLE foo (id int)",
            "ALTER TABLE foo ADD COLUMN x int",
            "TRUNCATE foo",
            "GRANT ALL ON foo TO public",
        ],
    )
    def test_forbidden_keyword_raises_select_only_violation(self, bad_sql: str) -> None:
        with pytest.raises((SelectOnlyViolation, ValueError)):
            _validate_select_only(bad_sql)

    @pytest.mark.parametrize(
        "good_sql",
        [
            "SELECT 1",
            "SELECT * FROM foo",
            "SELECT id, name FROM foo WHERE x = 'y'",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "SELECT * FROM foo WHERE msg LIKE '%INTO%'",  # string-literal safety
        ],
    )
    def test_valid_select_passes(self, good_sql: str) -> None:
        # Should NOT raise.
        _validate_select_only(good_sql)


# ── Part 2: Per-tx SET LOCAL statement_timeout (architect §3.1) ─────────────


class TestPerTxSetLocalStatementTimeout:
    """Verify ``SET LOCAL statement_timeout`` is applied INSIDE the
    tool's own connection block — NOT engine-wide.

    We hook the SQLAlchemy ``before_cursor_execute`` event on the
    shared engine and assert the tool's tx emits the SET LOCAL
    statement alongside the user query.
    """

    @pytest.mark.asyncio
    async def test_set_local_emitted_inside_tool_tx(
        self, shared_engine: Engine
    ) -> None:
        seen_statements: list[str] = []

        def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            seen_statements.append(statement)

        event.listen(shared_engine, "before_cursor_execute", _capture)
        try:
            # Seed the table so the SELECT has something to return.
            from sqlalchemy import text
            with shared_engine.begin() as conn:
                conn.execute(text("CREATE TABLE IF NOT EXISTS _t (id INTEGER)"))
                conn.execute(text("INSERT INTO _t (id) VALUES (1)"))

            tools = create_ens_db_tools(_manager_with(shared_engine), "test-inst-1")
            select_tool = next(t for t in tools if t.name == "ens_db_postgres_select")
            out = await select_tool.ainvoke({"query": "SELECT id FROM _t"})
            assert "id" in out and "1" in out

            # Assert: SET LOCAL was emitted ONLY on PG (architect §7.5
            # dual-engine — SQLite does NOT support the statement).
            # The user SELECT must have been emitted on either engine.
            is_pg = shared_engine.url.get_backend_name().startswith("postgres")
            set_local_idx = next(
                (
                    i for i, s in enumerate(seen_statements)
                    if "statement_timeout" in s.lower() and "set local" in s.lower()
                ),
                None,
            )
            select_idx = next(
                (
                    i for i, s in enumerate(seen_statements)
                    if "SELECT id FROM _t" in s
                ),
                None,
            )
            assert select_idx is not None, (
                f"User SELECT not seen in stream; saw: {seen_statements[:5]}"
            )
            if is_pg:
                assert set_local_idx is not None, (
                    f"Per-tx SET LOCAL statement_timeout not emitted on PG; "
                    f"saw: {seen_statements[:5]}"
                )
                assert set_local_idx < select_idx, (
                    f"SET LOCAL must precede user SELECT (architect §3.1); "
                    f"set_local_idx={set_local_idx} select_idx={select_idx}"
                )
            else:
                # SQLite — SET LOCAL is forbidden; assert it was NOT emitted.
                assert set_local_idx is None, (
                    f"SET LOCAL statement_timeout must NOT be emitted on "
                    f"SQLite (architect §7.5); saw: {seen_statements[:5]}"
                )
        finally:
            event.remove(shared_engine, "before_cursor_execute", _capture)


# ── Part 3: DUAL-ENGINE COVERAGE (architect §7.5) ────────────────────────────


class TestDualEngineCoverage:
    """Same SELECT-only contract must hold on BOTH SQLite and PG.

    The shared_engine fixture parameterizes over both. If PG is not
    available (no ``ENSEMBLE_TEST_PG_URL``), the PG parametrization
    skips — the SQLite case remains load-bearing.
    """

    @pytest.mark.asyncio
    async def test_select_only_guard_runs_on_either_engine(
        self, shared_engine: Engine
    ) -> None:
        """SELECT executes successfully; non-SELECT raises on BOTH engines."""
        tools = create_ens_db_tools(_manager_with(shared_engine), "test-inst-2")
        select_tool = next(t for t in tools if t.name == "ens_db_postgres_select")

        from sqlalchemy import text
        with shared_engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS _t2 (n INTEGER)"))
            conn.execute(text("INSERT INTO _t2 (n) VALUES (42)"))

        # Valid SELECT works.
        out_ok = await select_tool.ainvoke({"query": "SELECT n FROM _t2"})
        assert "42" in out_ok

        # Forbidden keyword returns SelectOnlyViolation, not raise.
        out_bad = await select_tool.ainvoke({"query": "DROP TABLE _t2"})
        assert "select-only-guard" in out_bad.lower(), (
            f"Expected select-only-guard refusal; got: {out_bad!r}"
        )
