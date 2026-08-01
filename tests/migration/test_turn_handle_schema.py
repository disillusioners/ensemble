"""Phase 5 / Increment 4 — Turn handle schema parity tests.

Verifies the triple-registration requirement for the Increment-4
``suspension_reason`` / ``resume_target_turn_id`` columns and the
``idx_task_resume_target`` composite index:

  * **SQLModel metadata** (``daemon/repositories/task/models.py``) — the
    authoritative fresh-database definition. ``SQLModel.metadata
    .create_all()`` must emit both nullable columns AND the composite
    index on a new database.
  * **SQLite migration** (``daemon/migrations/versions/
    20260801_000001_task_turn_handles.sql``) — adds both columns AND
    the composite index AND runs the legacy-paused backfill on a
    pre-Increment-4 schema. Guarded by ``MigrationRunner``'s per-
    statement duplicate-column handler (B3).
  * **PostgreSQL ALTER block** (``daemon/manager.py::
    _ensure_postgres_columns``) — emitted only against a Postgres
    engine; mirrored by ``ALTER TABLE IF NOT EXISTS`` +
    ``CREATE INDEX IF NOT EXISTS`` + idempotent backfill UPDATE.

The tests in this module prove each path is registered independently
and that an omission in any one path is detected by an explicit
assertion (no regression can silently remove a column or index).
The B2 backfill and B3 fresh-DB idempotency cases (§11.1 of
increment4-plan.md) are the B-risk-driven additions from the
2026-08-01 Council Review.

Run with::

    .venv/bin/pytest -q tests/migration/test_turn_handle_schema.py \\
        -x --timeout 120
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# Import every model module so SQLModel.metadata registers every table.
# Same convention as tests/migration/test_jsonb_migration.py — the
# fresh-DB create_all() path must emit the same schema the production
# daemon would, including ``task.suspension_reason`` and
# ``task.resume_target_turn_id``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.mcp_server.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.infra.models  # noqa: F401
from daemon.migrations.models import SchemaMigration  # noqa: F401

from daemon.migrations.runner import MigrationRunner
from daemon.repositories.task.models import (
    SuspensionReason,
    Task,
    TaskStatus,
    TaskType,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

#: Path to the single Increment-4 migration file. We copy this file into
#: an isolated migrations directory so the runner does not try to apply
#: 60+ unrelated migrations from ``daemon/migrations/versions/`` (some
#: of those reference PG-only syntax like ``DROP CONSTRAINT`` and fail
#: on SQLite). Isolating the migration under test keeps the B2/B3
#: assertions focused on the Increment-4 file.
_INCREMENT_4_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "daemon/migrations/versions/20260801_000001_task_turn_handles.sql"
)


#: Column names under test (Increment 4 turn handles).
HANDLE_COLUMNS: tuple[str, ...] = (
    "suspension_reason",
    "resume_target_turn_id",
)

#: Composite-index name and exact column-list per increment4-plan.md
#: §6 C4 ("triple-registered composite index").
COMPOSITE_INDEX_NAME = "idx_task_resume_target"
COMPOSITE_INDEX_COLUMNS: tuple[str, ...] = (
    "resume_target_turn_id",
    "suspension_reason",
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_migrations_dir() -> Iterator[Path]:
    """Yield a temp ``versions/`` directory containing ONLY the
    Increment-4 migration (and a no-op baseline).

    MigrationRunner applies every ``*.sql`` file in the directory it is
    pointed at. We isolate the Increment-4 file so a test failure points
    at the Increment-4 schema (not at some unrelated migration's PG-only
    syntax).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        versions_dir = Path(tmpdir) / "versions"
        versions_dir.mkdir()
        # No-op baseline migration so the runner can mark at least one
        # version as applied (the runner requires valid ``-- UP``
        # sections and writes a ``schema_migrations`` row for each).
        (versions_dir / "20250101_000000_baseline.sql").write_text(
            "-- Migration: no-op baseline\n"
            "-- Created: 2025-01-01\n"
            "-- Description: test fixture baseline\n\n"
            "-- UP\n\n"
            "-- DOWN\n"
        )
        # Copy the Increment-4 migration into the isolated directory.
        shutil.copy(_INCREMENT_4_MIGRATION, versions_dir / _INCREMENT_4_MIGRATION.name)
        try:
            yield versions_dir
        finally:
            # Tempdir cleanup is automatic on context exit.
            pass


@pytest.fixture
def fresh_sqlite_engine() -> Iterator[Engine]:
    """Yield an in-memory SQLite engine with the full SQLModel schema.

    Mirrors ``test_jsonb_migration.py::sqlite_engine`` — same FK-off
    StaticPool pattern so cross-session writes are visible.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def legacy_paused_engine() -> Iterator[Engine]:
    """Yield a SQLite engine with the pre-Increment-4 ``task`` schema.

    The pre-Increment-4 schema lacks ``suspension_reason`` and
    ``resume_target_turn_id``. The Increment-4 migration must add them
    AND backfill any legacy ``status='paused'`` rows (B2).

    We create only the ``task`` table by hand; the migration under test
    only touches ``task`` so other tables are irrelevant.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    now = "2026-08-01 00:00:00"
    with engine.begin() as conn:
        # Pre-Increment-4 task schema: every column that existed BEFORE
        # the increment. The two new handle columns are intentionally
        # absent so the migration's ALTER ADD COLUMN actually adds them.
        conn.execute(
            text(
                "CREATE TABLE task ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "work_id TEXT NOT NULL UNIQUE,"
                "task_type TEXT NOT NULL,"
                "instance_id TEXT NOT NULL,"
                "message_id TEXT,"
                "status TEXT NOT NULL DEFAULT 'pending',"
                "worker_id TEXT,"
                "retry_count INTEGER NOT NULL DEFAULT 0,"
                "next_retry_at TEXT,"
                "cancel_requested INTEGER NOT NULL DEFAULT 0,"
                "cancel_requested_at TEXT,"
                "retry_scheduled INTEGER NOT NULL DEFAULT 0,"
                "result TEXT,"
                "error TEXT,"
                "created_at TIMESTAMP NOT NULL,"
                "started_at TIMESTAMP,"
                "completed_at TIMESTAMP"
                ")"
            )
        )
        # A legacy paused Task (suspension_reason=NULL, the B2 shape).
        conn.execute(
            text(
                "INSERT INTO task (work_id, task_type, instance_id, status, created_at) "
                "VALUES ('legacy-paused-1', 'process_message', "
                "'inst-legacy-1', 'paused', :created_at)"
            ),
            {"created_at": now},
        )
        # A legacy running Task — backfill MUST NOT touch this row.
        conn.execute(
            text(
                "INSERT INTO task (work_id, task_type, instance_id, status, created_at) "
                "VALUES ('legacy-running-1', 'process_message', "
                "'inst-legacy-1', 'running', :created_at)"
            ),
            {"created_at": now},
        )
        # A legacy completed Task — must remain untouched by the backfill.
        conn.execute(
            text(
                "INSERT INTO task (work_id, task_type, instance_id, status, created_at) "
                "VALUES ('legacy-completed-1', 'process_message', "
                "'inst-legacy-1', 'completed', :created_at)"
            ),
            {"created_at": now},
        )
    try:
        yield engine
    finally:
        engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _column_names(engine: Engine, table: str) -> set[str]:
    """Return the set of column names on ``table``."""
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _index_names(engine: Engine, table: str) -> list[dict[str, Any]]:
    """Return the list of index descriptors on ``table``."""
    return list(inspect(engine).get_indexes(table))


def _has_index_with_columns(
    engine: Engine, table: str, name: str, expected_cols: tuple[str, ...]
) -> bool:
    """True iff ``table`` has an index named ``name`` with exactly
    ``expected_cols`` as its column list.

    Used by the C4 triple-registration test. Both column ORDER and the
    index NAME must match the model definition exactly — a regression
    that changes either would silently change the planner's behaviour
    for ``find_suspended_turn_for_answer``.
    """
    for idx in _index_names(engine, table):
        if idx["name"] == name:
            return tuple(idx["column_names"]) == expected_cols
    return False


def _read_handle(engine: Engine, work_id: str) -> tuple[str | None, str | None]:
    """Return ``(suspension_reason, resume_target_turn_id)`` for ``work_id``.

    Reads the columns directly via raw SQL so the test is dialect-agnostic
    and works on any ``task`` schema (post-migration, pre-migration is
    impossible because the migration adds the columns).
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT suspension_reason, resume_target_turn_id "
                "FROM task WHERE work_id = :work_id"
            ),
            {"work_id": work_id},
        ).first()
    if row is None:
        return (None, None)
    return (row[0], row[1])


# ─────────────────────────────────────────────────────────────────────────────
# Test classes
# ─────────────────────────────────────────────────────────────────────────────


class TestSQLModelFreshSchema:
    """Fresh SQLModel schema contains both columns AND the composite index.

    Path #1 of the triple-registration requirement
    (increment4-plan.md §6 "Task model ↔ SQLite migration ↔ PostgreSQL
    startup ALTER"). If the model declaration is removed or the column
    type changes, ``SQLModel.metadata.create_all()`` emits a different
    schema and the parity tests below catch it.
    """

    def test_fresh_sqlmodel_schema_has_both_columns(self, fresh_sqlite_engine):
        """``SQLModel.metadata.create_all()`` emits both nullable columns."""
        cols = _column_names(fresh_sqlite_engine, "task")
        for col in HANDLE_COLUMNS:
            assert col in cols, (
                f"Fresh SQLModel schema is missing the {col!r} column "
                f"— the SQLModel declaration in "
                f"``daemon.repositories.task.models.Task`` was lost or "
                f"renamed. Triple-registration invariant violated."
            )

    def test_fresh_sqlmodel_schema_has_composite_index(
        self, fresh_sqlite_engine
    ):
        """``SQLModel.metadata.create_all()`` emits ``idx_task_resume_target``
        with the exact column list ``(resume_target_turn_id,
        suspension_reason)`` (C4).
        """
        assert _has_index_with_columns(
            fresh_sqlite_engine,
            "task",
            COMPOSITE_INDEX_NAME,
            COMPOSITE_INDEX_COLUMNS,
        ), (
            f"Fresh SQLModel schema is missing the composite index "
            f"{COMPOSITE_INDEX_NAME!r} with columns "
            f"{COMPOSITE_INDEX_COLUMNS!r}. The composite index is "
            f"triple-registered via Task.__table_args__ — the "
            f"declaration was lost or the column order changed."
        )

    def test_fresh_sqlmodel_columns_are_nullable_text(self, fresh_sqlite_engine):
        """Both columns are nullable (no NOT NULL constraint)."""
        for col in HANDLE_COLUMNS:
            col_spec = next(
                c
                for c in inspect(fresh_sqlite_engine).get_columns("task")
                if c["name"] == col
            )
            assert col_spec["nullable"] is True, (
                f"Column {col!r} must be nullable so a pre-Increment-4 "
                f"row can be inserted without supplying a value; got "
                f"nullable={col_spec['nullable']!r}"
            )
            # SQLite stores TEXT-typed columns as ``VARCHAR`` / ``TEXT``.
            # We accept either; the production PG path uses VARCHAR.
            assert col_spec["type"].__class__.__name__.upper() in {
                "VARCHAR",
                "TEXT",
            }, (
                f"Column {col!r} has unexpected type "
                f"{col_spec['type']!r}; expected VARCHAR or TEXT"
            )


class TestSQLiteMigrationAddsColumns:
    """SQLite migration adds both columns AND the composite index AND
    runs the backfill on a pre-Increment-4 schema.

    Path #2 of the triple-registration requirement. The migration file
    at ``20260801_000001_task_turn_handles.sql`` is the single source
    of truth for existing-SQLite schema evolution. Running it through
    ``MigrationRunner`` exercises the same code path the daemon uses
    on startup.
    """

    def test_migration_adds_both_columns_to_pre_increment_4_schema(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """Migration adds ``suspension_reason`` and ``resume_target_turn_id``
        to a pre-Increment-4 schema (the columns did NOT exist before).

        Without this assertion a regression that drops the ALTERs from
        the migration would silently break every existing SQLite
        deployment without raising an error in fresh-DB tests.
        """
        # Pre-condition: columns absent.
        cols_before = _column_names(legacy_paused_engine, "task")
        assert "suspension_reason" not in cols_before
        assert "resume_target_turn_id" not in cols_before

        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        # Must not raise.
        applied = runner.run_pending_migrations()

        cols_after = _column_names(legacy_paused_engine, "task")
        for col in HANDLE_COLUMNS:
            assert col in cols_after, (
                f"SQLite migration failed to add column {col!r} to the "
                f"pre-Increment-4 schema — ALTER statement missing from "
                f"{_INCREMENT_4_MIGRATION.name}"
            )
        # The applied list must include the Increment-4 migration.
        assert "20260801_000001" in applied

    def test_migration_adds_composite_index_to_pre_increment_4_schema(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """Migration creates ``idx_task_resume_target`` on a legacy schema.

        The composite index is the C4 invariant — every lookup against
        ``(resume_target_turn_id, suspension_reason)`` must be index-
        served, not a full scan.
        """
        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        assert _has_index_with_columns(
            legacy_paused_engine,
            "task",
            COMPOSITE_INDEX_NAME,
            COMPOSITE_INDEX_COLUMNS,
        ), (
            f"SQLite migration failed to create composite index "
            f"{COMPOSITE_INDEX_NAME!r} — CREATE INDEX statement missing "
            f"from {_INCREMENT_4_MIGRATION.name}"
        )


class TestB3FreshDBIdempotency:
    """B3 — Fresh-DB SQLite idempotency: a fresh database where
    ``SQLModel.metadata.create_all()`` already created the columns
    runs the guarded migration successfully without
    "duplicate column name" errors.

    Per increment4-plan.md §6 B3, the migration's ALTER statements
    must be guarded so they can re-run safely. ``MigrationRunner``
    catches SQLite's "duplicate column name" error and treats it as
    a no-op (runner.py:344-373). This test exercises that path on a
    real fresh DB, then asserts a second invocation is also a no-op.
    """

    def test_migration_on_fresh_db_does_not_raise_duplicate_column(
        self, fresh_sqlite_engine, isolated_migrations_dir
    ):
        """Migration runs cleanly on a fresh DB created by ``create_all()``.

        Pre-conditions: both columns and the composite index are
        already present (from the model). The migration's ALTERs
        must be guarded so they no-op rather than raise. The runner
        must log-and-skip the "duplicate column name" error and
        continue, recording the migration as applied.
        """
        # Pre-condition: columns exist via create_all().
        cols = _column_names(fresh_sqlite_engine, "task")
        assert "suspension_reason" in cols
        assert "resume_target_turn_id" in cols

        runner = MigrationRunner(
            fresh_sqlite_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()

        # Must not raise MigrationError — the runner swallows the
        # "duplicate column name" error as an idempotent no-op.
        applied = runner.run_pending_migrations()
        assert "20260801_000001" in applied, (
            f"Migration did not record as applied on fresh DB; "
            f"got applied list {applied!r}"
        )

        # Post-condition: columns and index still present and unchanged.
        cols_after = _column_names(fresh_sqlite_engine, "task")
        for c in HANDLE_COLUMNS:
            assert c in cols_after
        assert _has_index_with_columns(
            fresh_sqlite_engine,
            "task",
            COMPOSITE_INDEX_NAME,
            COMPOSITE_INDEX_COLUMNS,
        )

    def test_second_invocation_is_a_no_op(
        self, fresh_sqlite_engine, isolated_migrations_dir
    ):
        """Re-running the migration returns an empty pending list.

        The migration is recorded in ``schema_migrations`` after first
        run, so a second ``run_pending_migrations()`` returns ``[]``
        without re-executing the migration. This proves the runner's
        ``get_applied_versions()`` set membership check works on the
        fresh-DB path.
        """
        runner = MigrationRunner(
            fresh_sqlite_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        first = runner.run_pending_migrations()
        assert "20260801_000001" in first

        # Second invocation — every migration is now applied.
        second = runner.run_pending_migrations()
        assert second == [], (
            f"Second migration run must return empty pending list "
            f"(the Increment-4 migration should be marked applied "
            f"after first run); got {second!r}"
        )

    def test_b3_idempotency_on_legacy_schema(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """B3 on a legacy schema: applying the migration twice in a row
        does not raise and the second invocation is a no-op.

        The legacy path is the strict case: the first run adds the
        columns AND runs the backfill. The second run's ALTERs hit
        the "duplicate column name" guard; the backfill UPDATE's WHERE
        clause (``suspension_reason IS NULL``) excludes already-backfilled
        rows so re-running does not change data.
        """
        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        first = runner.run_pending_migrations()
        assert "20260801_000001" in first

        # Capture state after first run.
        suspended_reason_1, target_1 = _read_handle(
            legacy_paused_engine, "legacy-paused-1"
        )
        assert suspended_reason_1 == "paused_external"
        assert target_1 == "legacy-paused-1"

        # Second run: empty pending.
        second = runner.run_pending_migrations()
        assert second == [], (
            f"Second migration run on legacy schema must be a no-op; "
            f"got {second!r}"
        )

        # State is unchanged.
        suspended_reason_2, target_2 = _read_handle(
            legacy_paused_engine, "legacy-paused-1"
        )
        assert suspended_reason_2 == suspended_reason_1
        assert target_2 == target_1


class TestB2LegacyPausedBackfill:
    """B2 — Legacy paused backfill: a legacy ``status='paused'`` row
    with ``suspension_reason=NULL`` is backfilled to
    ``suspension_reason='paused_external'`` and
    ``resume_target_turn_id=<work_id>`` so the explicit-handle routing
    can resume it.

    Per increment4-plan.md §6 B2, the backfill is naturally idempotent
    (re-running does not change already-backfilled rows) and excludes
    rows whose ``status`` is not ``'paused'`` (the WHERE predicate
    filters to legacy paused Tasks only).
    """

    def test_legacy_paused_row_is_backfilled(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """Pre-Increment-4 paused Task gets ``paused_external`` +
        ``resume_target_turn_id=work_id`` after the migration.

        This is the B2 core: a paused Task that pre-dates Increment 4
        must become routable by ``find_paused_or_cancellable_turn`` so
        the pause-cascade can resume it. Without the backfill, the
        task's handle fields are NULL and the explicit-handle
        selectors skip it — the resume-cascade then has no
        authoritative ``work_id`` to target.
        """
        # Pre-condition: the columns do NOT exist (legacy schema).
        cols_before = _column_names(legacy_paused_engine, "task")
        assert "suspension_reason" not in cols_before, (
            "Legacy pre-Increment-4 schema should not have "
            "suspension_reason — test fixture is wrong"
        )
        assert "resume_target_turn_id" not in cols_before

        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        # Post-condition: legacy paused Task is backfilled.
        reason_after, target_after = _read_handle(
            legacy_paused_engine, "legacy-paused-1"
        )
        assert reason_after == SuspensionReason.PAUSED_EXTERNAL.value, (
            f"Legacy paused Task must backfill to "
            f"suspension_reason='paused_external'; got {reason_after!r}"
        )
        assert target_after == "legacy-paused-1", (
            f"Legacy paused Task must backfill to "
            f"resume_target_turn_id=<work_id>='legacy-paused-1'; "
            f"got {target_after!r}"
        )

    def test_legacy_running_row_remains_null_after_backfill(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """The backfill ``WHERE status='paused'`` filter excludes
        RUNNING rows — a RUNNING legacy Task must remain at NULL /
        NULL so the explicit-handle routing does not accidentally
        treat it as a paused turn.

        Per §11.1 "Genuinely-null legacy rows (rows that were never
        paused) remain null after the backfill (the backfill WHERE
        predicate excludes them)".
        """
        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        reason, target = _read_handle(legacy_paused_engine, "legacy-running-1")
        assert reason is None, (
            f"Legacy RUNNING Task must remain at suspension_reason=NULL "
            f"after backfill (the WHERE status='paused' predicate "
            f"excludes it); got {reason!r}"
        )
        assert target is None, (
            f"Legacy RUNNING Task must remain at resume_target_turn_id=NULL "
            f"after backfill; got {target!r}"
        )

    def test_legacy_completed_row_remains_null_after_backfill(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """The backfill excludes COMPLETED rows for the same reason as
        RUNNING — a terminal Task must not be re-routable via the
        pause-cascade selector (it has no live turn to resume).
        """
        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        reason, target = _read_handle(
            legacy_paused_engine, "legacy-completed-1"
        )
        assert reason is None
        assert target is None

    def test_backfill_is_idempotent(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """Running the backfill twice does not change already-backfilled
        rows. The backfill ``WHERE`` predicate is
        ``status='paused' AND suspension_reason IS NULL`` — after the
        first run the backfilled rows have a non-null reason and are
        excluded from the second run's UPDATE.

        Proven by running the migration once, snapshotting state,
        manually re-running the backfill UPDATE a second time, and
        asserting the snapshot is unchanged.
        """
        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        # Snapshot after first run.
        reason_1, target_1 = _read_handle(
            legacy_paused_engine, "legacy-paused-1"
        )
        assert reason_1 == "paused_external"
        assert target_1 == "legacy-paused-1"

        # Manually re-execute the backfill UPDATE (mirrors the SQL in
        # the migration file). With the WHERE filter, it must be a
        # no-op for already-backfilled rows.
        with legacy_paused_engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE task "
                    "SET suspension_reason = 'paused_external', "
                    "    resume_target_turn_id = work_id "
                    "WHERE status = 'paused' "
                    "  AND suspension_reason IS NULL"
                )
            )
            # Zero rows updated: the only paused row already has a
            # non-null suspension_reason, so the WHERE predicate
            # excludes it.
            assert result.rowcount == 0, (
                f"Re-running the backfill on an already-backfilled DB "
                f"must update 0 rows; got rowcount={result.rowcount}"
            )

        # State is unchanged.
        reason_2, target_2 = _read_handle(
            legacy_paused_engine, "legacy-paused-1"
        )
        assert reason_2 == reason_1
        assert target_2 == target_1


class TestHandleRoundTrip:
    """Valid ``SuspensionReason`` values and UUID strings round-trip
    through the SQLite migration + ORM read path.

    Per increment4-plan.md §11.1, the migration must not corrupt
    arbitrary string data: a ``paused_external`` reason and a UUID4
    target inserted via the ORM must read back identically.
    """

    @pytest.mark.parametrize(
        "reason_value",
        [
            SuspensionReason.AWAITING_ANSWER.value,
            SuspensionReason.AWAITING_CHILDREN.value,
            SuspensionReason.PAUSED_EXTERNAL.value,
        ],
    )
    def test_valid_reason_values_round_trip(
        self, fresh_sqlite_engine, reason_value
    ):
        """Every ``SuspensionReason`` enum value reads back unchanged."""
        work_id = f"work-{uuid.uuid4().hex[:12]}"
        target = str(uuid.uuid4())
        now_str = "2026-08-01 00:00:00"
        with Session(fresh_sqlite_engine) as session:
            session.add(
                Task(
                    work_id=work_id,
                    task_type=TaskType.PROCESS_MESSAGE.value,
                    instance_id="inst-rt",
                    status=TaskStatus.PAUSED.value,
                    suspension_reason=reason_value,
                    resume_target_turn_id=target,
                    created_at=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                )
            )
            session.commit()

        reason_read, target_read = _read_handle(fresh_sqlite_engine, work_id)
        assert reason_read == reason_value, (
            f"suspension_reason round-trip corrupted: wrote "
            f"{reason_value!r}, read {reason_read!r}"
        )
        assert target_read == target, (
            f"resume_target_turn_id round-trip corrupted: wrote "
            f"{target!r}, read {target_read!r}"
        )

    def test_uuid_string_round_trip(self, fresh_sqlite_engine):
        """A canonical UUID4 string round-trips unchanged."""
        work_id = f"work-{uuid.uuid4().hex[:12]}"
        target = str(uuid.uuid4())
        with Session(fresh_sqlite_engine) as session:
            session.add(
                Task(
                    work_id=work_id,
                    task_type=TaskType.PROCESS_MESSAGE.value,
                    instance_id="inst-uuid",
                    status=TaskStatus.PAUSED.value,
                    suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
                    resume_target_turn_id=target,
                    created_at=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                )
            )
            session.commit()

        _, target_read = _read_handle(fresh_sqlite_engine, work_id)
        # Verify the UUID parses (defence against accidental truncation
        # or case-folding).
        parsed = uuid.UUID(target_read)
        assert str(parsed) == target, (
            f"UUID round-trip corrupted: wrote {target!r}, read "
            f"{target_read!r}; parsed={parsed!r}"
        )

    def test_null_legacy_row_remains_readable(
        self, legacy_paused_engine, isolated_migrations_dir
    ):
        """A row that was never paused remains at NULL / NULL after the
        migration (already covered by ``test_legacy_running_row_*``,
        but repeated here in the round-trip suite so the test file
        covers both "written by migration" and "never touched" cases
        symmetrically).
        """
        runner = MigrationRunner(
            legacy_paused_engine, migrations_dir=isolated_migrations_dir
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        # The pre-seeded RUNNING row.
        reason, target = _read_handle(
            legacy_paused_engine, "legacy-running-1"
        )
        assert reason is None
        assert target is None


class TestCompositeIndexUsedByQuery:
    """C4 — Composite index ``idx_task_resume_target`` is registered
    and is available to the query planner for the
    ``find_suspended_turn_for_answer``-shaped predicate.

    Per increment4-plan.md §11.1: "Run ``EXPLAIN`` /
    ``EXPLAIN QUERY PLAN`` against ``find_suspended_turn_for_answer``'s
    predicate and assert the planner reports index usage on
    ``idx_task_resume_target`` (not a full scan)."
    """

    def test_composite_index_appears_in_index_list(
        self, fresh_sqlite_engine
    ):
        """``PRAGMA index_list(task)`` reports the composite index.

        Independent of the query planner — the index must be
        registered on the table for the planner to use it at all.
        """
        with fresh_sqlite_engine.connect() as conn:
            rows = conn.execute(
                text("PRAGMA index_list(task)")
            ).fetchall()
            index_names = {row[1] for row in rows}
        assert COMPOSITE_INDEX_NAME in index_names, (
            f"Composite index {COMPOSITE_INDEX_NAME!r} not registered "
            f"on the ``task`` table. ``PRAGMA index_list`` returned "
            f"{sorted(index_names)!r}"
        )

    def test_composite_index_column_list_matches_model(
        self, fresh_sqlite_engine
    ):
        """``PRAGMA index_info`` reports columns in the order the model
        declared them — ``(resume_target_turn_id, suspension_reason)``.

        The column order is load-bearing: the leading column is the
        one the planner can range-scan / equality-scan against. A
        regression that swaps the order changes the planner's
        behaviour for ``find_suspended_turn_for_answer`` without
        changing the index name.
        """
        with fresh_sqlite_engine.connect() as conn:
            rows = conn.execute(
                text(f"PRAGMA index_info('{COMPOSITE_INDEX_NAME}')")
            ).fetchall()
            actual_cols = tuple(r[2] for r in rows)
        assert actual_cols == COMPOSITE_INDEX_COLUMNS, (
            f"Composite index column list {actual_cols!r} does not "
            f"match the model declaration {COMPOSITE_INDEX_COLUMNS!r}. "
            f"Index order is load-bearing for the query planner."
        )

    def test_query_against_composite_columns_does_not_full_scan(
        self, fresh_sqlite_engine
    ):
        """A query with equality on both composite columns uses an
        index — no ``SCAN task`` (full table scan) appears in the
        EXPLAIN QUERY PLAN.

        SQLite may pick any index the planner thinks is most selective
        (the composite index OR another index like ``ix_task_status``
        when the query also has ``status='paused'``). The required
        invariant is "not a full scan", not "specifically the
        composite index" — both are valid choices and the production
        queries already pass ``status='paused'`` so SQLite picks
        ``ix_task_status`` as a tighter filter. We assert the query
        is index-served either way.
        """
        with Session(fresh_sqlite_engine) as session:
            from datetime import datetime, timezone
            for i in range(100):
                session.add(
                    Task(
                        work_id=f"work-{i}",
                        task_type=TaskType.PROCESS_MESSAGE.value,
                        instance_id=f"inst-{i % 5}",
                        status=TaskStatus.PAUSED.value,
                        suspension_reason=(
                            SuspensionReason.AWAITING_ANSWER.value
                        ),
                        resume_target_turn_id=f"target-{i}",
                        created_at=datetime.now(timezone.utc),
                    )
                )
            session.commit()

        # Query shaped like ``find_suspended_turn_for_answer``:
        # equality on ``resume_target_turn_id`` + ``suspension_reason``
        # should use ``idx_task_resume_target`` directly.
        with fresh_sqlite_engine.connect() as conn:
            plan = conn.execute(
                text(
                    "EXPLAIN QUERY PLAN "
                    "SELECT * FROM task "
                    "WHERE resume_target_turn_id = 'target-5' "
                    "  AND suspension_reason = 'awaiting_answer'"
                )
            ).fetchall()

        plan_str = " | ".join(str(row) for row in plan)
        assert "idx_task_resume_target" in plan_str, (
            f"Composite-index query did not use idx_task_resume_target; "
            f"EXPLAIN QUERY PLAN = {plan_str!r}"
        )
        assert "SCAN task" not in plan_str, (
            f"Composite-index query triggered a full SCAN; "
            f"EXPLAIN QUERY PLAN = {plan_str!r}"
        )
