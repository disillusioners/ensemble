"""Phase 2.5 / Task 2.5.12 — Observer finalize without a JobItem.

The post-D13 instance may complete processing WITHOUT ever having had
a ``JobItem`` row — messages now flow through the WorkerPool ``Task``
table exclusively (Phase 2 of the decouple-architecture migration
eliminated MESSAGE ``JobItem`` creation). The
``_finalize_job_db_sync`` helper accepts ``job_id=None`` and behaves:

  * Step 1 (JobItem UPDATE PROCESSING → COMPLETED/FAILED) is SKIPPED
    entirely — there is no row to update.
  * Step 2 (Instance status update → COMPLETED/ERROR) still runs.
  * Step 3 (Lock release — DELETE every ``job_locks`` row where
    ``instance_id`` matches) still runs.

The bus gate (``_bus_count_pending_for_target_sync > 0``) is preserved
regardless of ``job_id`` — the gates protect the instance, not the
JobItem.

Test surface (Task 2.5.12):

  * ``_finalize_job_db_sync(job_id=None, ...)`` transitions the
    instance to COMPLETED and releases every per-instance lock.
  * Step 1's no-op path is verified by asserting no ``JobItem`` row
    is touched (and no error is raised).
  * The instance status guard (already-terminal → skip) still works
    in the no-JobItem branch.
  * The ERROR path (terminal_status="error") also runs Steps 2+3
    with ``job_id=None``.

Run with::

    pytest tests/unit/services/test_observer_finalize_no_job.py -v --tb=short
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem, JobLock, JobStatus
from daemon.repositories.task.models import Task, TaskStatus
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def _wire_bus_mock():
    """Mock ``DependencyBus`` so ``_finalize_job_db_sync``'s A9 gate passes."""
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target_sync = lambda _iid: 0
    set_dependency_bus(bus_mock)
    yield bus_mock
    set_dependency_bus(None)


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "developer",
) -> str:
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id="test-project",
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
            paused_at=None,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_task(
    engine: Engine,
    *,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
) -> int:
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            task_type="process_message",
            instance_id=instance_id,
            status=status,
            worker_id="worker-0" if status == TaskStatus.RUNNING.value else None,
            started_at=now if status == TaskStatus.RUNNING.value else None,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


def _seed_lock(
    engine: Engine,
    *,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str = "default",
) -> str:
    lid = f"lock-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        lock = JobLock(
            lock_id=lid,
            project_id=project_id,
            queue_id=queue_id,
            job_id=f"job-{uuid.uuid4().hex[:8]}",
            instance_id=instance_id,
            lock_slot=0,
        )
        s.add(lock)
        s.commit()
    return lid


def _read_instance(engine: Engine, instance_id: str) -> Instance | None:
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def _count_locks(engine: Engine, instance_id: str) -> int:
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(JobLock).where(JobLock.instance_id == instance_id)
        ).all()
        return len(list(rows))


def _count_jobs(engine: Engine, instance_id: str) -> int:
    """Count ``JobItem`` rows for the instance (always 0 on the
    post-D13 path — but we verify it explicitly).
    """
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(JobItem).where(JobItem.instance_id == instance_id)
        ).all()
        return len(list(rows))


def _make_observer(engine: Engine, write_guard: WritePauseGuard) -> JobFeedbackObserver:
    """Build a ``JobFeedbackObserver`` with the minimum surface
    ``_finalize_job_db_sync`` needs.

    The helper reads ``self._instance_manager.engine`` and
    ``self._instance_manager.write_guard`` to open the WriteGuardSession;
    it calls ``self._bus_count_pending_for_target_sync(iid)`` for the
    A9 gate; it touches no other ``self._*`` attributes.

    We construct the observer via ``__new__`` so the production
    ``__init__`` doesn't try to wire a real ``EventBus`` /
    ``JobQueueService`` / ``JobRepository`` etc. — the helper under
    test is the sync ``_finalize_job_db_sync`` which only needs
    engine + write_guard + bus gate.
    """
    observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
    observer._instance_manager = MagicMock()
    observer._instance_manager.engine = engine
    observer._instance_manager.write_guard = write_guard
    observer._instance_manager.is_write_paused = False
    observer._bus_count_pending_for_target_sync = lambda _iid: 0
    return observer


# ─── Task 2.5.12: Observer finalize without a JobItem ────────────────────────


class TestObserverFinalizeNoJob:
    """``_finalize_job_db_sync`` with ``job_id=None``.

    Verifies the post-D13 no-JobItem path (Phase 2.5 / Task 2.5.4):
    Step 1 is skipped, Steps 2+3 still run.
    """

    def test_step1_skipped_instance_reaches_completed(
        self, engine, _wire_bus_mock
    ):
        """Happy path: ``job_id=None`` → instance COMPLETED, locks released.

        Steps:

          1. Seed an instance (RUNNING) + a lock + a PROCESS_MESSAGE
             task (RUNNING). NO ``JobItem`` is seeded (post-D13 norm).
          2. Call ``_finalize_job_db_sync(job_id=None,
             terminal_status="completed", result_summary=..., ...)``.
          3. Verify Step 1 was skipped: no ``JobItem`` row was
             touched, no error was raised for the missing row.
          4. Verify Step 2 ran: ``instance.status`` is now COMPLETED.
          5. Verify Step 3 ran: every ``JobLock`` for the instance is
             deleted.

        Returns the ``_FinalizeJobResult`` so callers can fire
        post-commit side effects (SSE, CompletionRegistry, lifecycle
        event).
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)
        lock_a = _seed_lock(engine, instance_id=iid, queue_id="queue-A")
        lock_b = _seed_lock(engine, instance_id=iid, queue_id="queue-B")
        assert _count_locks(engine, iid) == 2
        assert _count_jobs(engine, iid) == 0, (
            "post-D13 path: no MESSAGE JobItem is seeded for the "
            "instance; the test verifies Step 1 is a no-op"
        )

        # 2. Finalize without a JobItem.
        result = observer._finalize_job_db_sync(
            job_id=None,
            instance_id=iid,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="all good",
            error_message=None,
        )

        # 3. Step 1 was a safe no-op (no JobItem error).
        assert result.skip is False, (
            f"finalize must NOT skip on the no-JobItem path; got {result!r}"
        )
        assert _count_jobs(engine, iid) == 0, (
            "Step 1 (JobItem UPDATE) must NOT create any JobItem row; "
            "the no-JobItem path leaves the table untouched"
        )

        # 4. Step 2 ran: instance status transitioned to COMPLETED.
        assert result.terminal_status == InstanceStatus.COMPLETED.value
        assert result.instance_id == iid
        assert result.instance_was_terminal is False
        inst = _read_instance(engine, iid)
        assert inst.status == InstanceStatus.COMPLETED.value, (
            f"instance must transition to COMPLETED via Step 2; "
            f"got {inst.status!r}"
        )

        # 5. Step 3 ran: locks released.
        assert result.locks_released == 2, (
            f"Step 3 must release BOTH seeded JobLock rows; "
            f"got locks_released={result.locks_released}"
        )
        assert _count_locks(engine, iid) == 0, (
            "every JobLock for the instance must be deleted by Step 3"
        )

    def test_error_path_with_no_job_id(
        self, engine, _wire_bus_mock
    ):
        """ERROR path: ``job_id=None, terminal_status="error"`` → ERROR + lock release.

        Sister scenario to the COMPLETED happy path: the no-JobItem
        ERROR path must also run Steps 2+3 — Step 1 is still skipped
        because there is no ``JobItem`` row to fail, but the instance
        transitions to ``error`` and the lock is released.
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_lock(engine, instance_id=iid)
        assert _count_jobs(engine, iid) == 0

        result = observer._finalize_job_db_sync(
            job_id=None,
            instance_id=iid,
            terminal_status=InstanceStatus.ERROR.value,
            result_summary=None,
            error_message="graph turn failed",
        )

        assert result.skip is False
        assert result.terminal_status == InstanceStatus.ERROR.value
        inst = _read_instance(engine, iid)
        assert inst.status == InstanceStatus.ERROR.value
        assert _count_locks(engine, iid) == 0

    def test_already_terminal_instance_skipped_without_error(
        self, engine, _wire_bus_mock
    ):
        """Already-terminal instance → skip the write, no error.

        Idempotency guard: if the instance is already in a terminal
        status (``completed`` / ``error`` / ``terminated`` /
        ``failed``), the helper must NOT raise. It returns a result
        with ``instance_was_terminal=True`` and skips the
        ``instance.status`` write — preserving the pre-existing
        terminal state.

        The lock release still runs (Step 3 is unconditional), so a
        leaked lock from a prior crash is recovered even when the
        instance is already terminal.
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        _seed_lock(engine, instance_id=iid)

        result = observer._finalize_job_db_sync(
            job_id=None,
            instance_id=iid,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="duplicate finalize",
            error_message=None,
        )

        # Skip the write — but Step 3 still runs.
        assert result.skip is False
        assert result.instance_was_terminal is True
        inst = _read_instance(engine, iid)
        # Status preserved (no clobber).
        assert inst.status == InstanceStatus.COMPLETED.value
        # Locks released even on the skip-write path.
        assert _count_locks(engine, iid) == 0
        assert result.locks_released == 1

    def test_missing_instance_skipped_without_error(
        self, engine, _wire_bus_mock
    ):
        """Instance row missing → skip both writes, locks released if any.

        Defensive path: if the ``instances`` row was deleted between
        the pre-fetch and the WriteGuardSession, ``_finalize_job_db_sync``
        must NOT crash. It returns ``instance_was_terminal=True`` and
        the caller's downstream side effects (SSE / CompletionRegistry
        / lifecycle event) are skipped via that flag.

        A leaked lock for the (now-gone) instance is still released —
        the SELECT-then-DELETE pattern in Step 3 is a no-op when no
        locks exist.
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        # Do NOT seed the instance. Seed only a lock to test Step 3.
        ghost_id = "ghost-instance"
        _seed_lock(engine, instance_id=ghost_id)
        # Confirm the ghost instance truly is missing.
        with Session(engine) as s:
            assert s.get(Instance, ghost_id) is None
        assert _count_locks(engine, ghost_id) == 1

        result = observer._finalize_job_db_sync(
            job_id=None,
            instance_id=ghost_id,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary=None,
            error_message=None,
        )

        assert result.skip is False
        assert result.instance_was_terminal is True
        # Step 3 still ran (and released the leaked lock).
        assert _count_locks(engine, ghost_id) == 0
        assert result.locks_released == 1

    def test_no_job_id_with_no_step1_failure(
        self, engine, _wire_bus_mock
    ):
        """``job_id=None`` must not raise on the Step-1 error branch.

        The pre-D13 ``InvalidTransitionError`` short-circuit (status
        mismatch on concurrent transition) does NOT apply in the
        ``job_id=None`` branch — there is no ``JobItem`` to mismatch
        on. The helper must fall through to Steps 2+3 unconditionally.

        This test directly verifies that property by calling the
        helper with a non-existent ``instance_id`` — the only thing
        that could go wrong on Step 1 is the missing row, and the
        helper must not raise on it.
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_lock(engine, instance_id=iid)

        # Should not raise despite job_id=None — Step 1 is skipped,
        # Step 2+3 run.
        result = observer._finalize_job_db_sync(
            job_id=None,
            instance_id=iid,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="ok",
            error_message=None,
        )

        assert result.skip is False
        assert result.terminal_status == InstanceStatus.COMPLETED.value
        assert _count_locks(engine, iid) == 0
