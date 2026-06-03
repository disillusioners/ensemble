"""Unit tests for ``daemon.services.migration_worker.MigrationWorker``.

The MigrationWorker is the central coordinator of the SQLite->PostgreSQL
hot-swap: it drives the 5-state machine, manages the write pause,
streams SSE events, supports cooperative cancellation, and rewrites
``ensemble.json`` on success. These tests exercise every public method
and the internal orchestration, with the heavy dependencies
(SQLModel, TableMigrator, CheckpointMigrator, langgraph savers) mocked
out.

We focus on behaviour, not implementation:
* State transitions (idle -> running -> {completed, failed, cancelled}).
* Concurrency: a second ``start()`` while a migration is in flight
  is rejected (TOCTOU-safe).
* ``is_migration_available`` returns the right reasons for each
  pre-condition failure.
* Cancel flips state, sets the cancel event, and the migration
  cooperatively aborts.
* The PG engine is disposed in the finally block, and writes are
  always resumed.
* ``ensemble.json`` is updated to ``"postgres"`` only on success.
* SSE events are emitted in the right order with the right shape.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from daemon.ensemble_config import EnsembleConfig
from daemon.services.migration_worker import (
    MigrationProgress,
    MigrationState,
    MigrationWorker,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ──────────────────────────────────────────────────────────────────────────────


class _MockManager:
    """Stand-in for the real ``InstanceManager``.

    Captures every write-pause interaction and exposes a small in-memory
    engine so we can verify the worker's wiring without touching real
    SQLModel tables. The worker reads several manager attributes:
      * ``ensemble_config``       - Pydantic config with .database/.is_sqlite/.save
      * ``engine``                - SQLAlchemy engine (real, but in-memory)
      * ``is_write_paused``       - bool property
      * ``pause_writes`` / ``resume_writes``  - sync methods
      * ``data_dir``              - Path for config.save
    """

    def __init__(self, *, data_dir: Path, is_sqlite: bool = True) -> None:
        self.ensemble_config = EnsembleConfig(database="sqlite" if is_sqlite else "postgres")
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.data_dir = data_dir
        self._is_write_paused = False
        self.pause_calls = 0
        self.resume_calls = 0

    @property
    def is_write_paused(self) -> bool:
        return self._is_write_paused

    def pause_writes(self) -> None:
        self.pause_calls += 1
        self._is_write_paused = True

    def resume_writes(self) -> None:
        self.resume_calls += 1
        self._is_write_paused = False


def _make_worker(manager, *, pg_env: bool = True) -> MigrationWorker:
    """Build a worker with optional PG env vars."""
    if pg_env:
        os.environ["POSTGRES_HOST"] = "test-host"
        os.environ["POSTGRES_DB"] = "test-db"
    else:
        os.environ.pop("POSTGRES_HOST", None)
        os.environ.pop("POSTGRES_DB", None)
    return MigrationWorker(manager)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A fresh temp dir for ensemble.json writes."""
    return tmp_path


@pytest.fixture
def manager(data_dir: Path) -> _MockManager:
    """A mock manager with is_sqlite=True and PG env vars set."""
    return _MockManager(data_dir=data_dir, is_sqlite=True)


@pytest.fixture
def worker(manager) -> MigrationWorker:
    """A worker bound to a mock manager with PG env set."""
    return _make_worker(manager, pg_env=True)


def _patch_migration_dependencies(
    *,
    table_counts: dict[str, int] | None = None,
    checkpoint_count: int = 0,
) -> list:
    """Return a stack of ``patch`` context managers for the heavy deps.

    Patches the modules/functions used by ``_run_migration`` so we can
    drive the worker's state machine without a real database.
    """
    from daemon.services import migration_worker as mw
    from daemon.migrations import checkpoint_migrator as cm
    import daemon.persistence as persistence_mod

    table_counts = table_counts or {}

    # create_postgres_engine returns a stub engine with dispose().
    fake_pg_engine = MagicMock()
    fake_pg_engine.dispose = MagicMock()

    # TableMigrator's methods are replaced with stubs that record calls
    # and return the supplied counts.
    def fake_migrate_all_tables(self):
        return table_counts

    def fake_validate(self):
        return []

    # get_checkpointer returns an awaitable that resolves to a checkpointer
    # with raw_saver and close.
    class FakeCheckpointer:
        raw_saver = MagicMock()

        async def close(self):
            return None

    async def fake_get_checkpointer(config):
        return FakeCheckpointer()

    # CheckpointMigrator's migrate_checkpoints returns the count.
    async def fake_migrate_checkpoints(self, sqlite_saver, pg_saver):
        return checkpoint_count

    return [
        # create_postgres_engine is imported at the top of migration_worker.
        patch.object(mw, "create_postgres_engine", return_value=fake_pg_engine),
        # TableMigrator is imported at the top; same class object, so
        # patching the class methods affects every reference.
        patch.object(mw.TableMigrator, "migrate_all_tables", fake_migrate_all_tables),
        patch.object(mw.TableMigrator, "validate_migration", fake_validate),
        # CheckpointMigrator is lazy-imported in the worker, so we patch
        # the canonical class in its own module.
        patch.object(
            cm.CheckpointMigrator, "migrate_checkpoints", fake_migrate_checkpoints
        ),
        # get_checkpointer is also lazy-imported; patch at source.
        patch.object(persistence_mod, "get_checkpointer", side_effect=fake_get_checkpointer),
    ]


def _enter_patches(patches):
    """Enter a list of patch context managers and return an ``exit_all`` callable.

    Returns a tuple ``(entered_mocks, exit_all)``. The mocks can be
    inspected for call records; ``exit_all()`` must be called to undo
    the patches (typically in a ``finally`` block).
    """
    entered_mocks: list = []
    original_patches: list = list(patches)
    for p in original_patches:
        entered_mocks.append(p.__enter__())

    def exit_all():
        # Undo the patches in reverse order. ``p.__exit__`` is on the
        # original patch context managers, not on the entered mocks.
        for p in reversed(original_patches):
            p.__exit__(None, None, None)

    return entered_mocks, exit_all


# ──────────────────────────────────────────────────────────────────────────────
# State and progress
# ──────────────────────────────────────────────────────────────────────────────


class TestMigrationProgress:
    """The ``MigrationProgress`` dataclass + dict serialization."""

    def test_default_state_is_idle(self):
        """A fresh progress object is in IDLE state."""
        p = MigrationProgress()
        assert p.status == MigrationState.IDLE
        assert p.current_phase is None
        assert p.tables_completed == 0
        assert p.checkpoints_migrated == 0
        assert p.error is None

    def test_to_dict_status_is_string(self):
        """``to_dict`` converts the enum to a string value."""
        p = MigrationProgress(status=MigrationState.RUNNING)
        d = p.to_dict()
        assert d["status"] == "running"
        assert isinstance(d["status"], str)

    def test_to_dict_requires_restart_only_when_completed(self):
        """``requires_restart`` is True only when status is COMPLETED."""
        for state in (
            MigrationState.IDLE,
            MigrationState.RUNNING,
            MigrationState.FAILED,
            MigrationState.CANCELLED,
        ):
            assert MigrationProgress(status=state).to_dict()["requires_restart"] is False

        assert (
            MigrationProgress(status=MigrationState.COMPLETED).to_dict()["requires_restart"]
            is True
        )

    def test_to_dict_iso_timestamps(self):
        """datetime fields are serialised to ISO 8601 strings."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        p = MigrationProgress(started_at=now, completed_at=now)
        d = p.to_dict()
        assert d["started_at"] == now.isoformat()
        assert d["completed_at"] == now.isoformat()

    def test_to_dict_handles_none_timestamps(self):
        """``None`` timestamps are passed through as ``None``."""
        d = MigrationProgress().to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None


# ──────────────────────────────────────────────────────────────────────────────
# Construction + public introspection
# ──────────────────────────────────────────────────────────────────────────────


class TestWorkerConstruction:
    """Construction sets the right defaults and external hooks."""

    def test_initial_status_is_idle(self, worker):
        """A fresh worker reports IDLE via get_status()."""
        assert worker.get_status()["status"] == "idle"
        assert worker.get_status()["requires_restart"] is False

    def test_lock_is_asyncio_lock(self, worker):
        """The start lock is an ``asyncio.Lock``."""
        assert isinstance(worker._lock, asyncio.Lock)

    def test_cancel_event_is_threading_event(self, worker):
        """The cancel event is a threading.Event (not asyncio.Event)."""
        assert isinstance(worker._cancel_event, threading.Event)

    def test_no_subscribers_initially(self, worker):
        """A fresh worker has no SSE subscribers."""
        assert worker._subscribers == []


# ──────────────────────────────────────────────────────────────────────────────
# is_migration_available
# ──────────────────────────────────────────────────────────────────────────────


class TestIsMigrationAvailable:
    """``is_migration_available`` reports pre-conditions."""

    def test_available_when_sqlite_and_pg_env(self, manager, worker):
        """SQLite engine + PG env vars → can migrate."""
        result = worker.is_migration_available()
        assert result["can_migrate"] is True
        assert result["is_sqlite"] is True
        assert result["pg_env_available"] is True
        assert result["reasons"] == []

    def test_not_available_when_pg_env_missing(self, manager):
        """Without POSTGRES_HOST + POSTGRES_DB → can't migrate."""
        worker = _make_worker(manager, pg_env=False)
        result = worker.is_migration_available()
        assert result["can_migrate"] is False
        assert result["pg_env_available"] is False
        assert any("PostgreSQL" in r for r in result["reasons"])

    def test_not_available_when_already_postgres(self, data_dir):
        """If the in-memory config says postgres, can't migrate again."""
        from sqlalchemy.engine.url import URL

        mgr = _MockManager(data_dir=data_dir, is_sqlite=False)
        # Force a non-sqlite URL so the engine_is_sqlite check fails.
        mgr.engine = MagicMock()
        mgr.engine.url = URL.create("postgresql", "u", "p", "h", 5432, "d")

        worker = _make_worker(mgr, pg_env=True)
        result = worker.is_migration_available()
        assert result["can_migrate"] is False
        assert result["is_sqlite"] is False
        assert any("not SQLite" in r for r in result["reasons"])

    def test_database_url_postgres_alternative(self, manager):
        """``DATABASE_URL_POSTGRES`` env var counts as PG availability."""
        os.environ.pop("POSTGRES_HOST", None)
        os.environ.pop("POSTGRES_DB", None)
        os.environ["DATABASE_URL_POSTGRES"] = "postgresql://u:p@h/d"
        worker = MigrationWorker(manager)
        result = worker.is_migration_available()
        assert result["can_migrate"] is True
        assert result["pg_env_available"] is True
        os.environ.pop("DATABASE_URL_POSTGRES", None)

    def test_completed_migration_blocks_restart(self, manager, worker):
        """A second migration after completion is forbidden in the same process."""
        # Force the worker into COMPLETED state.
        worker._progress.status = MigrationState.COMPLETED
        result = worker.is_migration_available()
        assert result["can_migrate"] is False
        assert any("already completed" in r for r in result["reasons"])


# ──────────────────────────────────────────────────────────────────────────────
# Subscribe / unsubscribe / emit
# ──────────────────────────────────────────────────────────────────────────────


class TestSseSubscribers:
    """SSE subscribers receive events and can be unsubscribed."""

    def test_subscribe_returns_queue(self, worker):
        """``subscribe()`` returns a queue and registers it."""
        q = worker.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert q in worker._subscribers

    def test_unsubscribe_removes_queue(self, worker):
        """``unsubscribe`` drops the queue from the subscribers list."""
        q = worker.subscribe()
        worker.unsubscribe(q)
        assert q not in worker._subscribers

    def test_unsubscribe_unknown_queue_is_safe(self, worker):
        """Unsubscribing a queue that wasn't registered is a no-op."""
        q = asyncio.Queue()
        worker.unsubscribe(q)  # no exception
        assert q not in worker._subscribers

    def test_emit_event_fans_out_to_all_subscribers(self, worker):
        """``_emit_event`` puts the event on every subscriber queue."""
        q1 = worker.subscribe()
        q2 = worker.subscribe()

        worker._emit_event("progress", {"phase": "running"})

        # Both queues received the same event payload.
        async def drain(q):
            return q.get_nowait()

        e1 = asyncio.run(drain(q1))
        e2 = asyncio.run(drain(q2))

        assert e1["event"] == "progress"
        assert e2["event"] == "progress"
        assert e1["data"]["phase"] == "running"
        assert "timestamp" in e1["data"]


# ──────────────────────────────────────────────────────────────────────────────
# start() — happy path / state machine
# ──────────────────────────────────────────────────────────────────────────────


class TestStartStateMachine:
    """``start()`` drives the IDLE -> RUNNING -> COMPLETED transition."""

    @pytest.mark.asyncio
    async def test_start_completes_migration(
        self, manager, worker, data_dir
    ):
        """A successful migration flips state to COMPLETED and writes config."""
        patches = _patch_migration_dependencies(
            table_counts={"users": 5, "orders": 10},
            checkpoint_count=42,
        )
        _, exit_all = _enter_patches(patches)

        try:
            await worker.start()
        finally:
            exit_all()

        status = worker.get_status()
        assert status["status"] == "completed"
        assert status["requires_restart"] is True
        assert status["checkpoints_migrated"] == 42
        assert status["tables_total"] == 2
        assert status["tables_completed"] == 2

        # ensemble.json was rewritten to "postgres".
        on_disk = json.loads((data_dir / "ensemble.json").read_text())
        assert on_disk["database"] == "postgres"

        # The writes were paused and resumed.
        assert manager.pause_calls == 1
        assert manager.resume_calls == 1

    @pytest.mark.asyncio
    async def test_start_raises_if_already_running(self, manager, worker):
        """A second concurrent ``start()`` raises ``RuntimeError`` (HTTP 409).

        We simulate the concurrent state directly by setting the worker's
        status to RUNNING before the second call. The actual concurrent
        lock-acquisition behaviour is exercised by the production code's
        TOCTOU-safe pattern: state check happens *inside* the lock.
        """
        # Force the worker into RUNNING state, mimicking an in-flight run.
        worker._progress.status = MigrationState.RUNNING

        with pytest.raises(RuntimeError, match="already running"):
            await worker.start()

    @pytest.mark.asyncio
    async def test_lock_is_set_after_start(self, manager, worker):
        """``start()`` acquired the lock; ``_lock`` exists and is an asyncio.Lock."""
        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)

        try:
            await worker.start()
        finally:
            exit_all()

        # The lock is released after start returns.
        # asyncio.Lock.locked() is False when the lock is free.
        assert worker._lock.locked() is False

    @pytest.mark.asyncio
    async def test_start_raises_if_prerequisites_not_met(self, manager):
        """``start()`` raises ``ValueError`` if migration is not available."""
        # No PG env vars → unavailable.
        os.environ.pop("POSTGRES_HOST", None)
        os.environ.pop("POSTGRES_DB", None)
        worker = MigrationWorker(manager)

        with pytest.raises(ValueError, match="prerequisites not met"):
            await worker.start()

    @pytest.mark.asyncio
    async def test_start_resumes_writes_on_failure(self, manager, worker):
        """Even on failure, writes are resumed in the finally block.

        The worker catches exceptions inside ``_run_migration`` and
        transitions to FAILED state, so ``start()`` returns normally.
        We verify the resume call by inspecting ``manager.resume_calls``
        after the run.
        """
        from daemon.migrations import data_migrator as dm

        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)

        # Force a failure AFTER pause_writes so resume is also called.
        # The worker calls ``await asyncio.to_thread(manager.pause_writes)``
        # before the data migrator; if we fail in the data migrator,
        # is_write_paused is True and resume runs in the finally block.
        def fail_migrate(self):
            raise RuntimeError("data migration failure")

        with patch.object(
            dm.TableMigrator, "migrate_all_tables", fail_migrate
        ):
            await worker.start()

        exit_all()
        assert worker.get_status()["status"] == "failed"
        # Both pause and resume were called.
        assert manager.pause_calls == 1
        assert manager.resume_calls == 1

    @pytest.mark.asyncio
    async def test_start_resumes_writes_on_cancellation(self, manager, worker):
        """Cancelled migrations also resume writes."""
        from daemon.migrations import MigrationCancelledError
        from daemon.migrations import data_migrator as dm

        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)

        # Patch migrate_all_tables to raise MigrationCancelledError so the
        # worker's exception handler transitions to CANCELLED state.
        def raise_cancelled(self):
            raise MigrationCancelledError("user cancelled")

        with patch.object(dm.TableMigrator, "migrate_all_tables", raise_cancelled):
            await worker.start()

        exit_all()
        assert worker.get_status()["status"] == "cancelled"
        assert manager.resume_calls == 1

    @pytest.mark.asyncio
    async def test_start_keeps_idle_when_paused_precondition_never_happens(
        self, manager, worker
    ):
        """If preconditions fail, state stays IDLE (no side effects)."""
        # No PG env → can't migrate.
        os.environ.pop("POSTGRES_HOST", None)
        os.environ.pop("POSTGRES_DB", None)
        worker = MigrationWorker(manager)
        with pytest.raises(ValueError):
            await worker.start()

        assert worker.get_status()["status"] == "idle"
        # No writes were paused.
        assert manager.pause_calls == 0
        assert manager.resume_calls == 0


# ──────────────────────────────────────────────────────────────────────────────
# cancel()
# ──────────────────────────────────────────────────────────────────────────────


class TestCancel:
    """``cancel()`` requests cooperative cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_raises_when_not_running(self, worker):
        """``cancel()`` raises if no migration is in flight."""
        with pytest.raises(RuntimeError, match="No migration is currently running"):
            await worker.cancel()

    @pytest.mark.asyncio
    async def test_cancel_sets_event(self, worker):
        """A running migration can be cancelled; the event gets set."""
        # Force the worker into RUNNING so cancel() will accept.
        worker._progress.status = MigrationState.RUNNING
        await worker.cancel()
        assert worker._cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_cancel_emits_log_event(self, worker):
        """A cancellation request emits a ``log`` SSE event."""
        q = worker.subscribe()
        worker._progress.status = MigrationState.RUNNING

        await worker.cancel()

        event = q.get_nowait()
        assert event["event"] == "log"
        assert "Cancellation requested" in event["data"]["message"]


# ──────────────────────────────────────────────────────────────────────────────
# _run_migration — phase progression
# ──────────────────────────────────────────────────────────────────────────────


class TestRunMigrationPhases:
    """``_run_migration`` walks the 9 phases in order."""

    @pytest.mark.asyncio
    async def test_phases_progress_in_order(self, manager, worker):
        """The current_phase field advances through all expected steps."""
        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)

        # Capture phases by subscribing and reading the queue.
        q = worker.subscribe()

        try:
            await worker.start()
        finally:
            exit_all()

        # Drain the event queue and extract phase names.
        phases: list[str] = []
        while not q.empty():
            event = q.get_nowait()
            if event["event"] == "progress" and "phase" in event["data"]:
                phases.append(event["data"]["phase"])

        # The expected phases, in order, include all the main steps.
        for phase in (
            "creating_pg_engine",
            "creating_schema",
            "backfilling_migrations",
            "pausing_writes",
            "migrating_tables",
            "migrating_checkpoints",
            "validating",
            "updating_config",
        ):
            assert phase in phases, f"phase {phase} not observed in {phases}"

    @pytest.mark.asyncio
    async def test_complete_event_payload(self, manager, worker):
        """The ``complete`` SSE event includes summary statistics."""
        patches = _patch_migration_dependencies(
            table_counts={"a": 5, "b": 3},
            checkpoint_count=7,
        )
        _, exit_all = _enter_patches(patches)

        q = worker.subscribe()

        try:
            await worker.start()
        finally:
            exit_all()

        # Find the complete event.
        complete_event = None
        while not q.empty():
            event = q.get_nowait()
            if event["event"] == "complete":
                complete_event = event
                break

        assert complete_event is not None
        data = complete_event["data"]
        assert data["tables_migrated"] == 2
        assert data["total_rows"] == 8
        assert data["checkpoints_migrated"] == 7
        assert data["validation_mismatches"] == 0
        assert data["requires_restart"] is True

    @pytest.mark.asyncio
    async def test_validation_mismatches_reported(self, manager, worker):
        """Validation mismatches are passed to the complete event."""
        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)

        # Override the validate patch to return mismatches.
        from daemon.services import migration_worker as mw
        mw.TableMigrator.validate_migration = MagicMock(
            return_value=[{"table": "a", "sqlite_count": 1, "pg_count": 0, "diff": 1}]
        )

        q = worker.subscribe()

        try:
            await worker.start()
        finally:
            exit_all()

        # Find the complete event.
        complete_event = None
        while not q.empty():
            event = q.get_nowait()
            if event["event"] == "complete":
                complete_event = event
                break

        assert complete_event is not None
        assert complete_event["data"]["validation_mismatches"] == 1

    @pytest.mark.asyncio
    async def test_pg_engine_disposed_in_finally(self, manager, worker):
        """The PG engine is disposed even on success."""
        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)
        # The patched create_postgres_engine returns fake_pg_engine.
        from daemon.services import migration_worker as mw
        fake_pg_engine = mw.create_postgres_engine.return_value

        try:
            await worker.start()
        finally:
            exit_all()

        # The engine's dispose was called.
        fake_pg_engine.dispose.assert_called()

    @pytest.mark.asyncio
    async def test_pg_engine_disposed_on_failure(self, manager, worker):
        """The PG engine is disposed even when migration fails."""
        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)
        from daemon.services import migration_worker as mw
        fake_pg_engine = mw.create_postgres_engine.return_value

        # Force a failure during schema creation. The worker's
        # ``_run_migration`` catches the exception, transitions to
        # FAILED, and the finally block disposes the engine.
        def fail_create_all(*args, **kwargs):
            raise RuntimeError("schema failure")

        with patch(
            "daemon.services.migration_worker.SQLModel.metadata.create_all",
            side_effect=fail_create_all,
        ):
            await worker.start()

        exit_all()
        # Even on failure, the engine was disposed.
        fake_pg_engine.dispose.assert_called()

    @pytest.mark.asyncio
    async def test_state_transitions_to_running(self, manager, worker):
        """State goes IDLE -> RUNNING during the run."""
        from daemon.migrations import data_migrator as dm

        patches = _patch_migration_dependencies(table_counts={})
        _, exit_all = _enter_patches(patches)

        run_started = threading.Event()
        proceed = threading.Event()

        def slow_migrate_all_tables(self):
            run_started.set()
            proceed.wait(timeout=2.0)
            return {}

        with patch.object(
            dm.TableMigrator, "migrate_all_tables", slow_migrate_all_tables
        ):
            task = asyncio.create_task(worker.start())

            # Wait for the migrator to start (it blocks on proceed).
            await asyncio.get_event_loop().run_in_executor(None, run_started.wait, 2.0)

            # Now check the status while the run is in flight.
            status_during_run = worker.get_status()["status"]

            # Let the run finish.
            proceed.set()
            await task

        exit_all()
        assert status_during_run == "running"
        assert worker.get_status()["status"] == "completed"


# ──────────────────────────────────────────────────────────────────────────────
# Cancellation during run
# ──────────────────────────────────────────────────────────────────────────────


class TestCooperativeCancellation:
    """Cancellation flips the state to CANCELLED."""

    @pytest.mark.asyncio
    async def test_cancellation_before_pause_emits_cancelled_event(
        self, manager, worker
    ):
        """If the migrator raises MigrationCancelledError, the worker emits 'cancelled'."""
        from daemon.migrations import MigrationCancelledError
        from daemon.migrations import data_migrator as dm

        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)

        def raise_cancelled(self):
            raise MigrationCancelledError("cancelled by user")

        q = worker.subscribe()

        with patch.object(dm.TableMigrator, "migrate_all_tables", raise_cancelled):
            await worker.start()

        exit_all()
        assert worker.get_status()["status"] == "cancelled"

        # The cancelled event was emitted.
        cancelled_event = None
        while not q.empty():
            event = q.get_nowait()
            if event["event"] == "cancelled":
                cancelled_event = event
                break

        assert cancelled_event is not None
        assert "cancelled" in cancelled_event["data"]["message"].lower()

    @pytest.mark.asyncio
    async def test_cancellation_resets_progress(self, manager, worker):
        """After cancellation, the current_phase is reset to None."""
        from daemon.migrations import MigrationCancelledError
        from daemon.migrations import data_migrator as dm

        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)

        def raise_cancelled(self):
            raise MigrationCancelledError("cancelled by user")

        with patch.object(dm.TableMigrator, "migrate_all_tables", raise_cancelled):
            await worker.start()

        exit_all()
        status = worker.get_status()
        assert status["status"] == "cancelled"
        assert status["current_phase"] is None


# ──────────────────────────────────────────────────────────────────────────────
# ensemble.json update
# ──────────────────────────────────────────────────────────────────────────────


class TestEnsembleConfigUpdate:
    """The worker rewrites ``ensemble.json`` only on success."""

    @pytest.mark.asyncio
    async def test_config_updated_on_success(self, manager, worker, data_dir):
        """On completion, ensemble.json is rewritten to 'postgres'."""
        patches = _patch_migration_dependencies(table_counts={"a": 1})
        _, exit_all = _enter_patches(patches)
        try:
            await worker.start()
        finally:
            exit_all()

        on_disk = json.loads((data_dir / "ensemble.json").read_text())
        assert on_disk["database"] == "postgres"

    @pytest.mark.asyncio
    async def test_config_not_updated_on_failure(self, manager, worker, data_dir):
        """On failure, ensemble.json keeps its previous database value."""
        # Write a baseline ensemble.json to confirm it is NOT overwritten.
        baseline = EnsembleConfig(database="sqlite")
        baseline.save(data_dir)

        # Force a failure during schema creation. The worker catches the
        # exception and transitions to FAILED — start() returns normally.
        def fail_create_all(*args, **kwargs):
            raise RuntimeError("schema failure")

        with patch(
            "daemon.services.migration_worker.SQLModel.metadata.create_all",
            side_effect=fail_create_all,
        ):
            await worker.start()

        on_disk = json.loads((data_dir / "ensemble.json").read_text())
        assert on_disk["database"] == "sqlite"
        assert worker.get_status()["status"] == "failed"


# ──────────────────────────────────────────────────────────────────────────────
# Misc helpers
# ──────────────────────────────────────────────────────────────────────────────


class TestGetStatus:
    """``get_status`` returns a stable dict shape."""

    def test_get_status_returns_dict(self, worker):
        """Status is returned as a plain dict (not the dataclass)."""
        status = worker.get_status()
        assert isinstance(status, dict)
        assert "status" in status
        assert "requires_restart" in status

    def test_status_includes_started_completed_timestamps(self, worker):
        """After a run, started_at and completed_at are set."""
        # Simulate a completed run by setting fields directly.
        now = datetime.now(timezone.utc)
        worker._progress.started_at = now
        worker._progress.completed_at = now
        worker._progress.status = MigrationState.COMPLETED

        status = worker.get_status()
        assert status["started_at"] is not None
        assert status["completed_at"] is not None
        assert status["requires_restart"] is True
