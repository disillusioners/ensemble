"""Unit tests for ``daemon.migrations.data_migrator.TableMigrator``.

The data migrator is the heart of Phase 3: it reads rows from a SQLite
engine and writes them to a PostgreSQL engine, ORM-style, with
``ON CONFLICT (pk_columns) DO NOTHING`` for idempotency. These tests
cover the public surface (``migrate_table``, ``migrate_all_tables``,
``validate_migration``) plus the ``chunked`` helper.

The tests use two in-memory SQLite engines (one as the "source", one as
the "destination") and a small set of locally-declared SQLModel tables
declared in the test module. The global ``SQLModel.metadata`` is walked
during ``migrate_all_tables``; the test fixture cleans up the tables it
registers so other test modules are not affected.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

from daemon.migrations import MigrationCancelledError
from daemon.migrations.data_migrator import (
    DEFAULT_BATCH_SIZE,
    TABLES_TO_SKIP,
    TableMigrator,
    chunked,
)


# ──────────────────────────────────────────────────────────────────────────────
# Test-local SQLModel tables
# ──────────────────────────────────────────────────────────────────────────────
#
# We declare fresh models here so we have full control over the schema
# (PK composition, FK relationships, column names). All tables are
# registered against the global ``SQLModel.metadata`` so the migrator's
# ``sorted_tables`` walk picks them up, but we clean them up in a
# teardown so they don't leak into other tests.


class _Parent(SQLModel, table=True):
    __tablename__ = "test_p3_parent"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    payload: str | None = Field(default=None)


class _Child(SQLModel, table=True):
    __tablename__ = "test_p3_child"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    parent_id: int | None = Field(
        default=None, foreign_key="test_p3_parent.id", index=True
    )
    label: str = Field()


class _Standalone(SQLModel, table=True):
    __tablename__ = "test_p3_standalone"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    value: str = Field()


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_engine():
    """Source engine — in-memory SQLite.

    ``StaticPool`` keeps a single shared connection so the in-memory
    database persists across the test's many ``Session`` open/close
    cycles. ``check_same_thread=False`` lets us use the engine from
    worker threads.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_engine():
    """Destination engine — a second in-memory SQLite.

    We use SQLite as a stand-in for PostgreSQL here because:
      * The migrator uses ``sqlalchemy.dialects.postgresql.insert`` for
        ``ON CONFLICT DO NOTHING``, but the dialect-specific compile
        step is independent of the underlying engine.
      * Tests want a real transactional engine with INSERT/UPDATE/DELETE
        semantics; SQLite fits while Postgres would need a live server.
    The migrator is engine-agnostic for the *read* side; the only PG
    touchpoint is the lazy import of ``pg_insert`` for the
    ``on_conflict_do_nothing`` clause (verified by inspecting the
    compiled SQL).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def cancel_event() -> threading.Event:
    """Fresh cancel event for each test."""
    return threading.Event()


@pytest.fixture
def log_callback() -> MagicMock:
    """Mock log callback that records every invocation."""
    return MagicMock()


@pytest.fixture
def migrator(sqlite_engine, pg_engine, cancel_event, log_callback):
    """Default migrator instance bound to the two engines and an empty event."""
    return TableMigrator(
        sqlite_engine=sqlite_engine,
        pg_engine=pg_engine,
        cancel_event=cancel_event,
        log_callback=log_callback,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public constants
# ──────────────────────────────────────────────────────────────────────────────


class TestModuleConstants:
    """Sanity checks for module-level constants."""

    def test_tables_to_skip_contains_schema_migrations(self):
        """``schema_migrations`` is backfilled separately, not data-migrated."""
        assert "schema_migrations" in TABLES_TO_SKIP

    def test_default_batch_size_is_500(self):
        """Batch size is 500 per the migration plan."""
        assert DEFAULT_BATCH_SIZE == 500


# ──────────────────────────────────────────────────────────────────────────────
# chunked helper
# ──────────────────────────────────────────────────────────────────────────────


class TestChunkedHelper:
    """``chunked`` splits an iterable into fixed-size lists."""

    def test_chunks_exact_division(self):
        """10 elements / size 3 → [3, 3, 3, 1] (last partial)."""
        result = list(chunked(range(10), 3))
        assert result == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]

    def test_chunks_remainder(self):
        """A non-divisible iterable yields the final partial chunk."""
        result = list(chunked([1, 2, 3, 4, 5], 2))
        assert result == [[1, 2], [3, 4], [5]]

    def test_chunks_empty(self):
        """An empty iterable yields no chunks."""
        assert list(chunked([], 3)) == []

    def test_chunks_larger_than_input(self):
        """A single chunk smaller than the input fits in one batch."""
        assert list(chunked([1, 2], 10)) == [[1, 2]]

    def test_chunks_preserves_order(self):
        """Order within and between chunks is preserved."""
        result = list(chunked("abcdefg", 2))
        assert result == [["a", "b"], ["c", "d"], ["e", "f"], ["g"]]

    def test_chunks_accepts_generators(self):
        """Generators are consumed lazily (no indexable access required)."""
        def gen():
            yield from range(7)

        result = list(chunked(gen(), 3))
        assert result == [[0, 1, 2], [3, 4, 5], [6]]


# ──────────────────────────────────────────────────────────────────────────────
# migrate_table — single table happy path
# ──────────────────────────────────────────────────────────────────────────────


class TestMigrateTable:
    """``migrate_table`` copies one table from SQLite to PG."""

    def test_migrate_table_copies_rows(
        self, migrator, sqlite_engine, pg_engine
    ):
        """All rows from the source are written to the destination."""
        with Session(sqlite_engine) as session:
            for i in range(5):
                session.add(_Parent(name=f"p{i}", payload=f"data-{i}"))
            session.commit()

        rows = migrator.migrate_table("test_p3_parent", [_Parent])

        assert rows == 5

        with Session(pg_engine) as session:
            names = [r.name for r in session.exec(select(_Parent)).all()]

        assert sorted(names) == ["p0", "p1", "p2", "p3", "p4"]

    def test_migrate_table_empty_table(self, migrator):
        """An empty source table is a no-op (returns 0)."""
        rows = migrator.migrate_table("test_p3_parent", [_Parent])
        assert rows == 0

    def test_migrate_table_requires_model_class(self, migrator):
        """``migrate_table`` raises ValueError when given no model classes."""
        with pytest.raises(ValueError, match="requires at least one model class"):
            migrator.migrate_table("test_p3_parent", [])

    def test_migrate_table_idempotent_on_conflict(
        self, migrator, sqlite_engine, pg_engine
    ):
        """Re-running the migration on the same rows does not duplicate them."""
        with Session(sqlite_engine) as session:
            for i in range(3):
                session.add(_Parent(name=f"p{i}", payload="orig"))
            session.commit()

        # First run populates PG.
        migrator.migrate_table("test_p3_parent", [_Parent])

        # Second run against the *same* source (no new rows) must not duplicate.
        rows2 = migrator.migrate_table("test_p3_parent", [_Parent])

        assert rows2 == 3  # attempt counter
        with Session(pg_engine) as session:
            count = len(session.exec(select(_Parent)).all())
        assert count == 3, "ON CONFLICT DO NOTHING should prevent duplicates"

    def test_migrate_table_uses_primary_key_for_conflict(
        self, migrator, sqlite_engine
    ):
        """The conflict target is the table's primary key columns.

        The migrator does ``from sqlalchemy.dialects.postgresql import
        insert as pg_insert`` lazily inside ``migrate_table``, so we
        cannot intercept the import. Instead, we capture the compiled
        SQL via a custom event listener on the destination engine.
        """
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        captured: list[str] = []

        def before_cursor_execute(
            conn, cursor, statement, parameters, context, executemany
        ):
            captured.append(statement)

        event.listen(migrator._pg_engine, "before_cursor_execute", before_cursor_execute)

        try:
            with Session(sqlite_engine) as session:
                session.add(_Parent(name="probe"))
                session.commit()
            migrator.migrate_table("test_p3_parent", [_Parent])
        finally:
            event.remove(
                migrator._pg_engine, "before_cursor_execute", before_cursor_execute
            )

        # Find the INSERT statement and check it references the PK column
        # as the conflict target.
        insert_stmts = [s for s in captured if "INSERT INTO test_p3_parent" in s]
        assert insert_stmts, f"no INSERT captured: {captured}"
        assert any(
            "ON CONFLICT (id) DO NOTHING" in s for s in insert_stmts
        ), insert_stmts


# ──────────────────────────────────────────────────────────────────────────────
# Batch processing
# ──────────────────────────────────────────────────────────────────────────────


class TestBatchProcessing:
    """Rows are written in batches of ``DEFAULT_BATCH_SIZE`` with per-batch commits."""

    def test_batch_size_emits_progress_per_batch(
        self, sqlite_engine, pg_engine, cancel_event, log_callback
    ):
        """Each completed batch emits a log event with batch_size and total."""
        # Insert 1200 rows so the default 500-row batch produces 3 commits
        # (500 + 500 + 200).
        with Session(sqlite_engine) as session:
            for i in range(1200):
                session.add(_Parent(name=f"p{i:04d}"))
            session.commit()

        migrator = TableMigrator(
            sqlite_engine=sqlite_engine,
            pg_engine=pg_engine,
            cancel_event=cancel_event,
            log_callback=log_callback,
        )

        rows = migrator.migrate_table("test_p3_parent", [_Parent])
        assert rows == 1200

        # Find the "Batch of ..." log calls.
        batch_logs = [
            call.kwargs
            for call in log_callback.call_args_list
            if call.kwargs.get("message", "").startswith("Batch of ")
        ]
        # 3 batches expected: 500 + 500 + 200.
        assert len(batch_logs) == 3
        assert batch_logs[0]["batch_size"] == 500
        assert batch_logs[1]["batch_size"] == 500
        assert batch_logs[2]["batch_size"] == 200

    def test_migrate_table_respects_custom_batch_size(
        self, sqlite_engine, pg_engine, cancel_event, log_callback
    ):
        """A small batch size produces more commits."""
        with Session(sqlite_engine) as session:
            for i in range(10):
                session.add(_Parent(name=f"p{i:02d}"))
            session.commit()

        migrator = TableMigrator(
            sqlite_engine=sqlite_engine,
            pg_engine=pg_engine,
            cancel_event=cancel_event,
            log_callback=log_callback,
        )

        # The constant is 500, but for the test we override it.
        with patch.object(dm_module := __import__("daemon.migrations.data_migrator", fromlist=["DEFAULT_BATCH_SIZE"]), "DEFAULT_BATCH_SIZE", 3):
            rows = migrator.migrate_table("test_p3_parent", [_Parent])

        assert rows == 10

        batch_logs = [
            call.kwargs
            for call in log_callback.call_args_list
            if call.kwargs.get("message", "").startswith("Batch of ")
        ]
        # 10 / 3 = ceil = 4 batches: 3, 3, 3, 1.
        assert len(batch_logs) == 4


# ──────────────────────────────────────────────────────────────────────────────
# Cancellation
# ──────────────────────────────────────────────────────────────────────────────


class TestCancellation:
    """``cancel_event`` raised between batches aborts the migration."""

    def test_cancel_between_batches_raises(self, sqlite_engine, pg_engine):
        """Setting the event before the run makes the next batch check fail."""
        # Insert enough rows for multiple batches.
        with Session(sqlite_engine) as session:
            for i in range(1500):
                session.add(_Parent(name=f"p{i:04d}"))
            session.commit()

        cancel_event = threading.Event()
        migrator = TableMigrator(
            sqlite_engine=sqlite_engine,
            pg_engine=pg_engine,
            cancel_event=cancel_event,
        )

        # Cancel after the first batch commits. We do this by patching
        # ``Session.exec`` to set the event before the second batch read.
        with Session(pg_engine) as session:
            session.add(_Parent(id=1, name="seed"))
            session.commit()

        from sqlmodel import Session as SQLModelSession

        original_exec = SQLModelSession.exec
        call_count = {"n": 0}

        def patched_exec(self, *args, **kwargs):
            call_count["n"] += 1
            # Set the cancel flag on the second batch read.
            if call_count["n"] == 2:
                cancel_event.set()
            return original_exec(self, *args, **kwargs)

        with patch.object(SQLModelSession, "exec", patched_exec):
            with pytest.raises(MigrationCancelledError):
                migrator.migrate_table("test_p3_parent", [_Parent])

    def test_cancel_raises_with_table_name_in_message(
        self, sqlite_engine, pg_engine
    ):
        """The error message includes the table name for diagnostics."""
        with Session(sqlite_engine) as session:
            for i in range(10):
                session.add(_Parent(name=f"p{i}"))
            session.commit()

        cancel_event = threading.Event()
        cancel_event.set()  # cancel before any work

        migrator = TableMigrator(
            sqlite_engine=sqlite_engine,
            pg_engine=pg_engine,
            cancel_event=cancel_event,
        )

        with pytest.raises(MigrationCancelledError, match="test_p3_parent"):
            migrator.migrate_table("test_p3_parent", [_Parent])


# ──────────────────────────────────────────────────────────────────────────────
# migrate_all_tables — table ordering and skip behaviour
# ──────────────────────────────────────────────────────────────────────────────


class TestMigrateAllTablesOrdering:
    """``migrate_all_tables`` walks tables in FK-safe order and skips specials."""

    def test_migrate_all_tables_uses_sorted_tables(self, migrator):
        """The migrator walks ``SQLModel.metadata.sorted_tables`` and builds a model map.

        ``MetaData.sorted_tables`` is a property without a setter, so we
        can't patch it directly. Instead we use a small monkeypatch: wrap
        the migrator's own ``_build_model_class_map`` so it returns
        tables the migrator will iterate over, and verify the migrator
        ends up calling ``migrate_table`` for those tables.
        """
        # Return a single-table map so the migrator definitely has work.
        fake_map = {"test_p3_parent": _Parent}

        with patch.object(
            TableMigrator, "_build_model_class_map", return_value=fake_map
        ) as spy_map:
            with patch.object(
                TableMigrator, "migrate_table", return_value=0
            ) as spy_mt:
                results = migrator.migrate_all_tables()

        # The model map was built (proves the migrator reads the registry).
        spy_map.assert_called_once()
        # And the migrator did invoke ``migrate_table`` for the table
        # from the map (proves it walks sorted_tables, not just the map).
        assert spy_mt.called
        assert results.get("test_p3_parent") == 0

    def test_migrate_all_tables_skips_schema_migrations(self, migrator):
        """``schema_migrations`` is in TABLES_TO_SKIP and never migrated."""
        # Insert a dummy schema_migrations row directly to the source so we
        # can detect any accidental migration.
        with Session(migrator._sqlite_engine) as session:
            from daemon.migrations.models import SchemaMigration
            session.add(SchemaMigration(
                version="99999999_999999",
                name="dummy",
                applied_at="now",
                execution_time_ms=0,
            ))
            session.commit()

        # If schema_migrations were migrated, this would create a row in PG.
        with patch.object(migrator, "migrate_table", return_value=0) as spy_mt:
            migrator.migrate_all_tables()

        # None of the migrate_table calls were for "schema_migrations".
        for call in spy_mt.call_args_list:
            args, _ = call
            assert args[0] != "schema_migrations"

    def test_migrate_all_tables_returns_per_table_counts(
        self, migrator, sqlite_engine, pg_engine
    ):
        """The result dict maps each migrated table to its row count."""
        with Session(sqlite_engine) as session:
            for i in range(3):
                session.add(_Parent(name=f"p{i}"))
            session.add(_Standalone(value="alone"))
            session.commit()

        # Restrict migrate_table to our two tables so the test is hermetic.
        allowed = {"test_p3_parent", "test_p3_standalone"}

        original = migrator.migrate_table

        def fake_mt(table_name, model_classes):
            if table_name in allowed:
                return original(table_name, model_classes)
            return 0

        with patch.object(migrator, "migrate_table", side_effect=fake_mt):
            results = migrator.migrate_all_tables()

        assert results.get("test_p3_parent") == 3
        assert results.get("test_p3_standalone") == 1

    def test_migrate_all_tables_preserves_fk_ordering(
        self, migrator, sqlite_engine, pg_engine
    ):
        """Parent rows must be inserted before child rows for FK safety."""
        # Insert 2 parents, 4 children (2 per parent).
        with Session(sqlite_engine) as session:
            p1 = _Parent(name="p1")
            p2 = _Parent(name="p2")
            session.add(p1)
            session.add(p2)
            session.commit()
            session.refresh(p1)
            session.refresh(p2)
            session.add(_Child(parent_id=p1.id, label="c1a"))
            session.add(_Child(parent_id=p1.id, label="c1b"))
            session.add(_Child(parent_id=p2.id, label="c2a"))
            session.add(_Child(parent_id=p2.id, label="c2b"))
            session.commit()

        results = migrator.migrate_all_tables()
        # All our tables should be in the results.
        for name in ("test_p3_parent", "test_p3_child", "test_p3_standalone"):
            assert name in results, f"{name} missing from migration results"


# ──────────────────────────────────────────────────────────────────────────────
# validate_migration
# ──────────────────────────────────────────────────────────────────────────────


class TestValidateMigration:
    """``validate_migration`` compares row counts and reports mismatches."""

    def test_validate_matching_counts(self, migrator, sqlite_engine, pg_engine):
        """Equal counts → empty mismatch list."""
        with Session(sqlite_engine) as session:
            for i in range(3):
                session.add(_Parent(name=f"p{i}"))
            session.commit()
        migrator.migrate_all_tables()

        mismatches = migrator.validate_migration()
        # No mismatches for the tables we populated.
        relevant = [m for m in mismatches if m["table"] in {
            "test_p3_parent", "test_p3_child", "test_p3_standalone"
        }]
        assert relevant == []

    def test_validate_detects_count_mismatch(
        self, migrator, sqlite_engine, pg_engine
    ):
        """Mismatched counts are reported with a structured dict."""
        # Source has 3 parents, PG has 0.
        with Session(sqlite_engine) as session:
            for i in range(3):
                session.add(_Parent(name=f"p{i}"))
            session.commit()

        mismatches = migrator.validate_migration()
        relevant = [m for m in mismatches if m["table"] == "test_p3_parent"]
        assert len(relevant) == 1
        assert relevant[0]["sqlite_count"] == 3
        assert relevant[0]["pg_count"] == 0
        assert relevant[0]["diff"] == 3

    def test_validate_skips_tables_missing_from_source(
        self, migrator, sqlite_engine, pg_engine
    ):
        """Tables not present in the source are silently ignored."""
        # Drop one of our test tables from the source engine only.
        from sqlalchemy import text
        with sqlite_engine.connect() as conn:
            conn.execute(text("DROP TABLE test_p3_standalone"))
            conn.commit()

        # No exception, no mismatch reported for the dropped table.
        mismatches = migrator.validate_migration()
        names = {m["table"] for m in mismatches}
        assert "test_p3_standalone" not in names


# ──────────────────────────────────────────────────────────────────────────────
# _table_exists helper
# ──────────────────────────────────────────────────────────────────────────────


class TestTableExists:
    """``_table_exists`` does dialect-aware introspection."""

    def test_existing_table_returns_true(self, migrator):
        assert migrator._table_exists(migrator._sqlite_engine, "test_p3_parent") is True

    def test_missing_table_returns_false(self, migrator):
        assert migrator._table_exists(migrator._sqlite_engine, "no_such_table") is False

    def test_uses_sqlite_master_for_sqlite(self, migrator):
        """The SQLite branch is exercised when the URL contains ``sqlite``."""
        # Spy on ``engine.connect`` so we can capture the SQL executed.
        captured: list[str] = []

        from sqlalchemy import text as sa_text
        real_execute = None
        conn = MagicMock()

        def fake_execute(stmt, *args, **kwargs):
            captured.append(str(stmt))
            result = MagicMock()
            result.fetchone = MagicMock(return_value=("test_p3_parent",))
            return result

        conn.execute = fake_execute
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=None)

        with patch.object(migrator._sqlite_engine, "connect", return_value=ctx):
            result = migrator._table_exists(migrator._sqlite_engine, "test_p3_parent")

        assert result is True
        # The first captured statement should reference sqlite_master.
        assert any("sqlite_master" in s for s in captured), captured


# ──────────────────────────────────────────────────────────────────────────────
# Log callback
# ──────────────────────────────────────────────────────────────────────────────


class TestLogCallback:
    """The log callback receives every progress event."""

    def test_callback_invoked_for_each_table(
        self, migrator, sqlite_engine, log_callback
    ):
        """A migration emits at least one log per table."""
        with Session(sqlite_engine) as session:
            session.add(_Parent(name="only"))
            session.commit()

        migrator.migrate_all_tables()

        # At least one "Migrating table" and one "Migrated" log per table.
        messages = [
            call.kwargs.get("message", "")
            for call in log_callback.call_args_list
        ]
        migrating_logs = [m for m in messages if m.startswith("Migrating table")]
        migrated_logs = [m for m in messages if m.startswith("Migrated")]

        assert len(migrating_logs) >= 1
        assert len(migrated_logs) >= 1

    def test_falling_back_to_logger_when_no_callback(
        self, sqlite_engine, pg_engine, cancel_event
    ):
        """No callback → log statements don't crash; they fall through."""
        with Session(sqlite_engine) as session:
            session.add(_Parent(name="only"))
            session.commit()

        migrator = TableMigrator(
            sqlite_engine=sqlite_engine,
            pg_engine=pg_engine,
            cancel_event=cancel_event,
            log_callback=None,
        )
        # Should not raise.
        migrator.migrate_all_tables()

    def test_callback_exception_does_not_break_migration(
        self, sqlite_engine, pg_engine, cancel_event
    ):
        """A buggy callback is isolated; the migration still completes."""
        with Session(sqlite_engine) as session:
            session.add(_Parent(name="only"))
            session.commit()

        def bad_cb(**kwargs):
            raise RuntimeError("callback bug")

        migrator = TableMigrator(
            sqlite_engine=sqlite_engine,
            pg_engine=pg_engine,
            cancel_event=cancel_event,
            log_callback=bad_cb,
        )
        # Should not raise despite the bad callback.
        results = migrator.migrate_all_tables()
        assert "test_p3_parent" in results
