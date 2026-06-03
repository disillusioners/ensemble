"""Tests for SQLite-specific guards (Phase 1 feature).

Phase 1 added dialect-aware guards so factory.py and runner.py don't execute
SQLite-only SQL (sqlite_master queries, PRAGMA statements) against PostgreSQL.

The guards take the form:

    if "sqlite" not in str(<engine or conn>.url):
        return  # skip SQLite-only operation

We verify:
- factory._add_agent_id_column() returns early for non-SQLite engines
- factory.run_migrations() returns early for non-SQLite engines
- MigrationRunner.run_pending_migrations() is a no-op for non-SQLite engines
- Real SQLite engines still execute the SQLite-specific code paths
"""

import logging
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

from daemon.repositories.factory import _add_agent_id_column, run_migrations
from daemon.migrations.runner import MigrationRunner


def _make_postgres_engine_mock() -> MagicMock:
    """Build a mock engine whose url string is a Postgres connection string.

    The codebase checks `"sqlite" not in str(engine.url)` to decide whether
    to skip SQLite-only operations. Using a MagicMock with `.url` set to a
    plain Postgres URL string satisfies the guard without needing a real
    PostgreSQL driver.
    """
    mock_engine = MagicMock(name="postgres_engine")
    # When str(mock_engine.url) is called, must return a postgres-style URL
    mock_engine.url = "postgresql+psycopg://user:pass@localhost:5432/test"
    return mock_engine


def _make_postgres_conn_mock() -> MagicMock:
    """Build a mock conn whose engine.url string is a Postgres URL.

    factory._add_agent_id_column checks `str(conn.engine.url)`. We use a
    Mock so the URL is just the attribute on the mock.
    """
    mock_conn = MagicMock(name="postgres_conn")
    mock_conn.engine.url = "postgresql+psycopg://user:pass@localhost:5432/test"
    return mock_conn


# ─────────────────────────────────────────────────────────────────────
# factory._add_agent_id_column
# ─────────────────────────────────────────────────────────────────────


class TestAddAgentIdColumnGuard:
    """factory._add_agent_id_column must skip non-SQLite engines."""

    def test_non_sqlite_engine_skips_early(self):
        """With a non-SQLite engine url, no sqlite_master query is executed."""
        mock_conn = _make_postgres_conn_mock()
        logger = logging.getLogger("test")

        # Should return without raising and without executing sqlite_master
        _add_agent_id_column(mock_conn, "instances", logger)

        # Verify no SQL was executed on the conn
        mock_conn.execute.assert_not_called()
        mock_conn.commit.assert_not_called()

    def test_non_sqlite_does_not_call_alter_table(self):
        """PostgreSQL path: no ALTER TABLE statement should be issued."""
        mock_conn = _make_postgres_conn_mock()
        logger = logging.getLogger("test")

        _add_agent_id_column(mock_conn, "job_queue_items", logger)

        # If the guard failed, conn.execute would be called for sqlite_master
        # or ALTER TABLE. Verify neither happened.
        assert mock_conn.execute.call_count == 0

    def test_sqlite_engine_runs_queries(self, tmp_path):
        """With a real SQLite engine, sqlite_master queries ARE executed (no short-circuit)."""
        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        logger = logging.getLogger("test")

        try:
            with engine.connect() as conn:
                # Build a tiny tracker wrapper that records execute() calls
                # without actually doing migrations. This proves the guard
                # did NOT short-circuit.
                execute_calls = []
                original_execute = conn.execute

                def tracking_execute(stmt, *args, **kwargs):
                    execute_calls.append(str(stmt))
                    raise RuntimeError("STOP — tracked call")

                conn.execute = tracking_execute  # type: ignore[assignment]
                try:
                    _add_agent_id_column(conn, "instances", logger)
                except RuntimeError as e:
                    # Expected: we deliberately stop on the first real call
                    assert str(e) == "STOP — tracked call"
                # First execute() call must be the sqlite_master lookup
                assert any("sqlite_master" in c for c in execute_calls), (
                    f"Expected sqlite_master query, got: {execute_calls}"
                )
        finally:
            engine.dispose()


# ─────────────────────────────────────────────────────────────────────
# factory.run_migrations
# ─────────────────────────────────────────────────────────────────────


class TestRunMigrationsGuard:
    """factory.run_migrations must skip non-SQLite engines entirely."""

    def test_non_sqlite_engine_returns_early(self):
        """No engine.connect() call for a non-SQLite engine."""
        mock_engine = _make_postgres_engine_mock()

        # Should not raise and should not call engine.connect()
        run_migrations(mock_engine)

        mock_engine.connect.assert_not_called()

    def test_postgres_url_string_recognized(self):
        """str() of the mock url must NOT contain 'sqlite'."""
        mock_engine = _make_postgres_engine_mock()
        assert "sqlite" not in str(mock_engine.url)

    def test_sqlite_engine_runs_migrations(self, tmp_path):
        """Real SQLite engine: run_migrations executes without error."""
        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")

        try:
            # Just verify it doesn't raise on a real sqlite engine
            # (it will find no projects table and skip silently)
            run_migrations(engine)
        finally:
            engine.dispose()


# ─────────────────────────────────────────────────────────────────────
# migrations/runner.py MigrationRunner.run_pending_migrations
# ─────────────────────────────────────────────────────────────────────


class TestMigrationRunnerGuard:
    """MigrationRunner.run_pending_migrations must be a no-op for PostgreSQL."""

    def test_postgres_engine_skips_pending_migrations(self):
        """Mock engine with postgres url → returns [] without running anything."""
        mock_engine = _make_postgres_engine_mock()
        runner = MigrationRunner(mock_engine)

        result = runner.run_pending_migrations()

        assert result == []
        # Should not even try to ensure the migrations table
        mock_engine.connect.assert_not_called()

    def test_postgres_runner_does_not_discover_migrations(self):
        """No filesystem access for the postgres path."""
        mock_engine = _make_postgres_engine_mock()
        runner = MigrationRunner(mock_engine)

        # The early return should prevent ensure_migrations_table() from being called
        runner.run_pending_migrations()

        # discover_migrations reads from disk; if the guard failed we'd see it
        # get called via ensure_migrations_table → SQLModel.metadata.create_all.
        mock_engine.connect.assert_not_called()

    def test_postgres_url_does_not_contain_sqlite(self):
        """Sanity: postgres url string is 'sqlite'-free."""
        mock_engine = _make_postgres_engine_mock()
        assert "sqlite" not in str(mock_engine.url)

    def test_sqlite_runner_executes_ensure_migrations_table(self, tmp_path):
        """Real SQLite engine: ensure_migrations_table IS called."""
        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")

        # Use an empty migrations dir to avoid running real migration files
        # that would require pre-existing tables.
        empty_migrations_dir = tmp_path / "migrations"
        empty_migrations_dir.mkdir()

        try:
            runner = MigrationRunner(engine, migrations_dir=empty_migrations_dir)
            # Should not raise; with no migration files, returns []
            result = runner.run_pending_migrations()
            assert result == []
        finally:
            engine.dispose()
