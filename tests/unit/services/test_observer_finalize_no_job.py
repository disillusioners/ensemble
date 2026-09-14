"""Phase 2.5 / Task 2.5.12 — Observer finalize without a JobItem.

The post-D13 instance may complete processing WITHOUT ever having had
a ``JobItem`` row — messages now flow through the WorkerPool ``Task``
table exclusively (Phase 2 of the decouple-architecture migration
eliminated MESSAGE ``JobItem`` creation). The
``_finalize_job_db_sync`` helper accepts ``job_id=None`` and behaves:

  * Step 1 (JobItem UPDATE PROCESSING → COMPLETED/FAILED) is SKIPPED
    entirely — there is no row to update.
  * Step 2 (Instance status update → COMPLETED/ERROR) still runs.
  * Step 3 (Lock release — W1 job-scoped, keyed by THIS job's
    ``job_id``) correctly releases nothing here: with no ``JobItem``
    the finalized virtual job holds no lock of its own.

The bus gate (``_bus_count_pending_for_target_sync > 0``) is preserved
regardless of ``job_id`` — the gates protect the instance, not the
JobItem.

W1 (job-scoped lock release, 2026-09-15): Step 3 is now scoped to the
finalized job's OWN ``job_id``. On the ``job_id=None`` path the
finalized virtual job cannot hold a lock of its own
(``job_locks.job_id`` is non-nullable; virtual jobs never acquire
per-queue locks), so Step 3 releases NOTHING there — the previous
instance-wide DELETE could only ever delete OTHER jobs' locks (the W1
defect class). Crash-cleanup backstops: ``JobLockSweepService``
(terminal-job locks, ≤90s) + F5 ``force_finalize_orphan`` (stuck-active
jobs). The None-path pins below were updated to this contract; the
"leaked lock is recovered here" expectation they previously encoded is
superseded by those backstops.

Test surface (Task 2.5.12):

  * ``_finalize_job_db_sync(job_id=None, ...)`` transitions the
    instance to COMPLETED and releases NO locks (job-scoped Step 3:
    nothing of the virtual job's own to release).
  * Step 1's no-op path is verified by asserting no ``JobItem`` row
    is touched (and no error is raised).
  * The instance status guard (already-terminal → skip) still works
    in the no-JobItem branch.
  * The ERROR path (terminal_status="error") also runs Step 2 with
    ``job_id=None``.

Run with::

    pytest tests/unit/services/test_observer_finalize_no_job.py -v --tb=short
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem, JobLock
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.dependency_bus.models import (  # noqa: F401
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.report_injection.models import ReportInjection  # noqa: F401
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services import job_feedback_observer as _observer_module
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
    Step 1 is skipped; Step 2 still runs; Step 3 is a W1 job-scoped
    no-op (the virtual job holds no lock of its own).
    """

    def test_step1_skipped_instance_reaches_completed(
        self, engine, _wire_bus_mock
    ):
        """Happy path: ``job_id=None`` → instance COMPLETED, OTHER jobs' locks survive.

        Steps:

          1. Seed an instance (RUNNING) + a lock + a PROCESS_MESSAGE
             task (RUNNING). NO ``JobItem`` is seeded (post-D13 norm).
          2. Call ``_finalize_job_db_sync(job_id=None,
             terminal_status="completed", result_summary=..., ...)``.
          3. Verify Step 1 was skipped: no ``JobItem`` row was
             touched, no error was raised for the missing row.
          4. Verify Step 2 ran: ``instance.status`` is now COMPLETED.
          5. Verify Step 3 (W1 job-scoped) no-ops: locks keyed to
             other jobs' ``job_id`` survive.

        Returns the ``_FinalizeJobResult`` so callers can fire
        post-commit side effects (SSE, CompletionRegistry, lifecycle
        event).
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)
        _seed_lock(engine, instance_id=iid, queue_id="queue-A")
        _seed_lock(engine, instance_id=iid, queue_id="queue-B")
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

        # 5. Step 3 (W1 job-scoped): releases NOTHING on the
        # job_id=None path — the virtual job holds no lock of its
        # own; the seeded locks belong to OTHER jobs and survive
        # (pre-fix: instance-wide DELETE removed them all).
        assert result.locks_released == 0, (
            "W1: job_id=None → nothing of this finalize's own to "
            "release; locks_released must be 0."
        )
        assert _count_locks(engine, iid) == 2, (
            "W1: other-job locks must survive the None-path finalize "
            "(pre-fix this was 0 — instance-wide release)."
        )

    def test_error_path_with_no_job_id(
        self, engine, _wire_bus_mock
    ):
        """ERROR path: ``job_id=None, terminal_status="error"`` → ERROR
        transition, no lock release.

        Sister scenario to the COMPLETED happy path: the no-JobItem
        ERROR path must still run Step 2 — Step 1 is still skipped
        because there is no ``JobItem`` row to fail, the instance
        transitions to ``error``, and the W1 job-scoped Step 3
        releases nothing (the seeded lock belongs to another job and
        survives).
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
        assert result.locks_released == 0
        assert _count_locks(engine, iid) == 1, (
            "W1: the other-job lock must survive the None-path ERROR "
            "finalize."
        )

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

        W1: the lock release is job-scoped — with ``job_id=None``
        there is nothing of this finalize's own to release, so the
        seeded (other-job) lock survives. A leaked lock from a prior
        crash is reclaimed by ``JobLockSweepService`` (terminal-job
        locks, ≤90s) or F5 ``force_finalize_orphan`` (stuck-active
        jobs) — not incidentally here.
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

        # Skip the write — Step 3 is job-scoped (releases nothing).
        assert result.skip is False
        assert result.instance_was_terminal is True
        inst = _read_instance(engine, iid)
        # Status preserved (no clobber).
        assert inst.status == InstanceStatus.COMPLETED.value
        # W1: other-job lock survives; nothing released.
        assert _count_locks(engine, iid) == 1
        assert result.locks_released == 0

    def test_missing_instance_skipped_without_error(
        self, engine, _wire_bus_mock
    ):
        """Instance row missing → skip both writes; locks keyed to other jobs survive (W1 job-scoped).

        Defensive path: if the ``instances`` row was deleted between
        the pre-fetch and the WriteGuardSession, ``_finalize_job_db_sync``
        must NOT crash. It returns ``instance_was_terminal=True`` and
        the caller's downstream side effects (SSE / CompletionRegistry
        / lifecycle event) are skipped via that flag.

        W1: the lock release is job-scoped — with ``job_id=None``
        nothing of this finalize's own is released, so the seeded
        (other-job) lock survives. The helper's Step 3 (job-scoped
        SELECT-then-DELETE) is a no-op when the job-scoped predicate
        matches no rows.
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        # Do NOT seed the instance. Seed only a lock to probe Step 3.
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
        # W1: job-scoped Step 3 — the other-job lock survives.
        assert _count_locks(engine, ghost_id) == 1
        assert result.locks_released == 0

    def test_no_job_id_with_no_step1_failure(
        self, engine, _wire_bus_mock
    ):
        """``job_id=None`` must not raise on the Step-1 error branch.

        The pre-D13 ``InvalidTransitionError`` short-circuit (status
        mismatch on concurrent transition) does NOT apply in the
        ``job_id=None`` branch — there is no ``JobItem`` to mismatch
        on. The helper must fall through to Step 2 unconditionally;
        Step 3 is a W1 job-scoped no-op on this path.

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
        # Step 2 runs (and W1 job-scoped Step 3 releases nothing).
        result = observer._finalize_job_db_sync(
            job_id=None,
            instance_id=iid,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="ok",
            error_message=None,
        )

        assert result.skip is False
        assert result.terminal_status == InstanceStatus.COMPLETED.value
        assert result.locks_released == 0
        assert _count_locks(engine, iid) == 1, (
            "W1: the other-job lock must survive the None-path "
            "finalize."
        )


# ─── B.S.1-ii: (b) declared-waiting predicate-attached LOG at the ────────────
# Step-2 parent-COMPLETED stamp (LOG ONLY, stage ii).
#
# Wave 2 wc-wake-report-integrity (decisions.md C2-D2.6/D2.8 LOCKED,
# phase2-plan §4.2 B.S.1-ii + B.S.6/B.S.7). The Step-2 stamp inside
# ``_finalize_job_db_sync`` is THE production parent-COMPLETED path
# (the bus callback re-triggers ``_finalize_job`` on the parent when
# its last watcher resolves). Per D2.8 the (b) evaluation runs AFTER
# the in-session bus gate and the in-session tasks gate — ONLY on the
# both-zero path — and is fail-OPEN LOG ONLY: zero flow disruption.


def _seed_terminal_child(
    engine: Engine, *, parent_id: str
) -> str:
    """Insert a terminal (COMPLETED) child Instance linked to parent."""
    cid = f"child-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=cid,
                agent_id="worker",
                agent_dir="/tmp/agents/worker",
                agent_name="worker",
                project_id="test-project",
                parent_id=parent_id,
                status=InstanceStatus.COMPLETED.value,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()
    return cid


def _seed_pending_injection(
    engine: Engine, *, parent_id: str, child_id: str
) -> None:
    """Seed a PENDING report_injections row (the PRIMARY (b) signal)."""
    ReportInjectionRepository(engine).enqueue(
        parent_instance_id=parent_id,
        child_instance_id=child_id,
        child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        report_message_id=f"rmsg-{uuid.uuid4().hex[:8]}",
        content="junk opener body",
    )


def _guard_records(caplog) -> list:
    """Captured ``[ReportIntegrityGuard]`` violation records only."""
    return [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and "[ReportIntegrityGuard]" in r.getMessage()
        and "declared-waiting violation" in r.getMessage()
    ]


class TestObserverFinalizeIntegrityLog:
    """Stage-ii log behavior at the observer's parent-COMPLETED stamp."""

    def test_incident_shape_logs_at_completed_stamp(
        self, engine, _wire_bus_mock, caplog
    ):
        """Parent stamps COMPLETED while a terminal child's report is
        PENDING → exactly one [ReportIntegrityGuard] line; stamp intact.
        """
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        child_id = _seed_terminal_child(engine, parent_id=iid)
        _seed_pending_injection(engine, parent_id=iid, child_id=child_id)

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_job_db_sync(
                job_id=None,
                instance_id=iid,
                terminal_status=InstanceStatus.COMPLETED.value,
                result_summary="done",
                error_message=None,
            )

        # LOG ONLY — finalization is unchanged.
        assert result.skip is False
        assert _read_instance(engine, iid).status == (
            InstanceStatus.COMPLETED.value
        ), "LOG ONLY — the stamp must still happen"

        guard = _guard_records(caplog)
        assert len(guard) == 1, (
            f"expected exactly one [ReportIntegrityGuard] line, got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = guard[0].getMessage()
        assert "[ReportIntegrityGuard]" in msg
        assert iid in msg, "parent id missing"
        assert child_id in msg, "terminal child id missing"
        assert "status=completed" in msg, "child terminal status missing"
        assert "PRIMARY" in msg
        assert "observer_finalize_job" in msg, "context tag missing"

    def test_healthy_finalize_is_silent(self, engine, _wire_bus_mock, caplog):
        """No undelivered obligations → finalization with NO guard log."""
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_job_db_sync(
                job_id=None,
                instance_id=iid,
                terminal_status=InstanceStatus.COMPLETED.value,
                result_summary="done",
                error_message=None,
            )

        assert result.skip is False
        assert _read_instance(engine, iid).status == (
            InstanceStatus.COMPLETED.value
        )
        assert _guard_records(caplog) == [], (
            f"healthy path must be silent; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_error_stamp_does_not_log(self, engine, _wire_bus_mock, caplog):
        """The (b) question is the parent-COMPLETED stamp: an ERROR
        finalization must NOT emit the guard line (even with the
        violation rows present)."""
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        child_id = _seed_terminal_child(engine, parent_id=iid)
        _seed_pending_injection(engine, parent_id=iid, child_id=child_id)

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_job_db_sync(
                job_id=None,
                instance_id=iid,
                terminal_status=InstanceStatus.ERROR.value,
                result_summary=None,
                error_message="boom",
            )

        assert result.skip is False
        assert _read_instance(engine, iid).status == InstanceStatus.ERROR.value
        assert _guard_records(caplog) == []

    def test_early_bus_gate_defer_skips_predicate(
        self, engine, _wire_bus_mock, monkeypatch, caplog
    ):
        """bus_pending > 0 (early gate) → skip=True and (b) NOT evaluated."""
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        child_id = _seed_terminal_child(engine, parent_id=iid)
        _seed_pending_injection(engine, parent_id=iid, child_id=child_id)
        observer._bus_count_pending_for_target_sync = lambda _iid: 1

        calls: list[str] = []
        monkeypatch.setattr(
            _observer_module,
            "log_declared_waiting_violations",
            lambda *a, **k: calls.append(k.get("context_tag", "?")),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_job_db_sync(
                job_id=None,
                instance_id=iid,
                terminal_status=InstanceStatus.COMPLETED.value,
                result_summary=None,
                error_message=None,
            )

        assert result.skip is True and result.gate_deferred is True
        assert _read_instance(engine, iid).status == InstanceStatus.RUNNING.value
        assert calls == [], (
            f"(b) must NOT be evaluated when the bus gate short-circuits; "
            f"got {calls}"
        )
        assert _guard_records(caplog) == []

    def test_in_session_bus_gate_defer_skips_predicate(
        self, engine, _wire_bus_mock, monkeypatch, caplog
    ):
        """PENDING watcher row visible to the IN-SESSION bus gate (the
        early stub says 0) → in-session gate defers and (b) NOT evaluated.
        Pins the in-session bus gate as prior to (b) (D2.8)."""
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        child_id = _seed_terminal_child(engine, parent_id=iid)
        _seed_pending_injection(engine, parent_id=iid, child_id=child_id)
        # Real PENDING watcher row: invisible to the early stub (0), but
        # the in-session inline COUNT reads the table directly.
        with Session(engine) as s:
            s.add(
                DependencyWatcher(
                    watch_id=f"watch-{uuid.uuid4().hex[:8]}",
                    source_task_id=f"task-{uuid.uuid4().hex[:8]}",
                    target_instance_id=iid,
                    follow_up_payload={"message": "wake"},
                    watcher_metadata={"kind": "test"},
                    created_at=datetime.now(timezone.utc).isoformat(),
                    state=DependencyWatcherState.PENDING.value,
                )
            )
            s.commit()

        calls: list[str] = []
        monkeypatch.setattr(
            _observer_module,
            "log_declared_waiting_violations",
            lambda *a, **k: calls.append(k.get("context_tag", "?")),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_job_db_sync(
                job_id=None,
                instance_id=iid,
                terminal_status=InstanceStatus.COMPLETED.value,
                result_summary=None,
                error_message=None,
            )

        assert result.skip is True and result.gate_deferred is True
        assert calls == []
        assert _guard_records(caplog) == []

    def test_in_session_tasks_gate_defer_skips_predicate(
        self, engine, _wire_bus_mock, monkeypatch, caplog
    ):
        """PENDING task row → in-session tasks gate defers and (b) NOT
        evaluated (D2.8: tasks gate is prior to (b))."""
        write_guard = WritePauseGuard()
        observer = _make_observer(engine, write_guard)

        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        child_id = _seed_terminal_child(engine, parent_id=iid)
        _seed_pending_injection(engine, parent_id=iid, child_id=child_id)
        _seed_task(engine, instance_id=iid, status=TaskStatus.PENDING.value)
        # Stub the EARLY tasks counter to 0 so the seeded PENDING task is
        # first seen by the IN-SESSION tasks gate (the real inline COUNT
        # inside the WriteGuardSession) — pinning the in-session gate as
        # prior to (b), not just the early one.
        observer._count_pending_tasks_for_instance_sync = lambda _iid: 0

        calls: list[str] = []
        monkeypatch.setattr(
            _observer_module,
            "log_declared_waiting_violations",
            lambda *a, **k: calls.append(k.get("context_tag", "?")),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_job_db_sync(
                job_id=None,
                instance_id=iid,
                terminal_status=InstanceStatus.COMPLETED.value,
                result_summary=None,
                error_message=None,
            )

        assert result.skip is True and result.gate_deferred is True
        assert _read_instance(engine, iid).status == InstanceStatus.RUNNING.value
        assert calls == []
        assert _guard_records(caplog) == []
