"""Acceptance tests for the joblock-leak fix (F1-F5, branch feature/fix-joblock-leak).

The 2026-09-14 incident pinned the 7807e521 event-keying family as the
root cause of a 8h45m production lane starvation: the Fix-B inline
mirror writer (``finalize_mirror_job_at_completion``) and the F-1
backstop (``reconcile_terminal_message_mirrors``) transitioned the
``admission_state='active' → 'done'`` BUT did NOT release the
``job_locks`` row — release was delegated to the observer event that
was never published. With the instance parked ``PAUSED``, every
recovery seam (boot-time, periodic, reconcile) was structurally
unreachable. The orphan lock persisted for 8h45m until a manual
``DELETE /api/instances/<id>`` triggered the terminate lane.

This file pins the F1-F5 fixes against a real file-backed SQLite
engine so each scenario produces a genuine SQL state, not a mock.

Acceptance bar (4 scenarios):

  (a) Scenario repro: a job finalizes via the Fix-B inline writer;
      the lock for the SAME instance is released in the same commit;
      pausing the instance does NOT resurrect the lock (pre-fix: lock
      was orphaned forever).

  (b) Backstop + sweep: the F-1 backstop writer also releases the
      lock in the same commit; the periodic
      ``JobLockSweepService.sweep_once`` reclaims any straggler locks
      for terminal jobs.

  (c) Healthy-path pin: the observer's R8 atomic release path
      (``job_feedback_observer.py:_finalize_job_db_sync:3977-3981``)
      continues to release locks — the new inline/backstop releases
      are ADDITIVE, not a regression.

  (d) Hardening: a ``asyncio.CancelledError`` mid-release (F2 R6) is
      caught + logged + re-raised; the dead-loop sync path (F2 R7)
      no longer silently skips the release (it now logs a WARNING).
      The periodic sweep reclaims both classes on the next tick.

F11 / shared-worktree hazard: each test builds its OWN local
file-backed SQLite engine under ``tmp_path`` with ``QueuePool`` so
the cross-thread behavior (the F1 inline writer uses
``asyncio.to_thread`` to off-load to a worker thread) is genuine DB
behavior, not StaticPool simulation.

Worktree regression proof discipline: the same tests against the
``56391477`` base (no F1-F5 fixes) MUST fail in their primary
contract — the exact-failure proves the test pins a fixed defect,
not a coincidental pass. See ``exact_failure_proof_at_base`` below
for a sample proof runbook (run on a pre-fix worktree).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, SQLModel

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue import Decision, JobQueueRepository
from daemon.repositories.job_queue import (
    JobRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_lock_sweep import JobLockSweepService
from daemon.services.job_queue_service import JobQueueService


# ─────────────────────────────────────────────────────────────────────
# Fixtures — local file-backed SQLite engine per test (F11 shared-WT safe)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Iterator:
    """Real file-backed SQLite engine under tmp_path with QueuePool.

    File-backed (NOT in-memory) + QueuePool so cross-thread writes
    from ``asyncio.to_thread`` reflect real DB behavior — the F1
    inline writer off-loads to a worker thread, and the
    StaticPool/in-memory pattern would silently mask connection-
    level races. ``check_same_thread=False`` because the SQLAlchemy
    QueuePool hands different connections to different threads.

    A fresh ``db_<uuid>.sqlite`` file is built per test for full
    isolation; cleanup is best-effort (tmp_path is auto-cleaned).
    """
    db_path = tmp_path / f"db_{uuid.uuid4().hex[:8]}.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=4,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        # Best-effort cleanup; tmp_path is auto-cleaned by pytest
        if db_path.exists():
            db_path.unlink()


@pytest.fixture
def job_repo(engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def lock_repo(engine) -> LockRepository:
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo) -> JobLockManager:
    return JobLockManager(lock_repo=lock_repo)


@pytest.fixture
def job_lock_sweep(lock_manager) -> JobLockSweepService:
    """A ``JobLockSweepService`` wired to the test's lock manager.

    Tests call ``await sweep_once()`` directly rather than
    ``start()`` so the test gets a single deterministic tick
    without the asyncio task lifecycle.
    """
    return JobLockSweepService(
        job_lock_manager=lock_manager,
        interval_seconds=1,
    )


@pytest.fixture
def job_queue_service(
    job_repo: JobRepository,
    lock_manager: JobLockManager,
) -> JobQueueService:
    """A real ``JobQueueService`` wired for F2 (R6/R7) hardening tests.

    ``queue_repo`` is a MagicMock (only ``find_jobs_by_instance`` is
    consulted in non-retry paths; the boundary tests use the SQL path
    directly). ``instance_manager`` is None — ``_is_instance_alive``
    short-circuits to False on None (job_queue_service.py:1848-1849)
    so the R6 cancellation test exercises the lock-release catch +
    re-raise path WITHOUT cascading into ``terminate_instance``.

    Tests that exercise a particular release-site behavior replace
    ``service._lock_manager.release_queue_lock`` /
    ``service._lock_manager.release`` with an AsyncMock that raises
    or returns a controlled value. Tests that need a running event
    loop for ``_finalize_terminal_sync`` use ``_start_loop_in_thread``
    (mirrors ``tests/job_queue/test_seam_invariants.py:1395``).
    """
    service = JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=MagicMock(spec=JobQueueRepository),
        instance_manager=None,
    )
    return service


def _start_loop_in_thread():
    """Spin up a background event loop and return ``(loop, thread, stop)``.

    The sync twin dispatches its async lock release via
    ``asyncio.run_coroutine_threadsafe`` onto ``self._loop``. For
    that to actually run the lock-release coroutine, the loop must
    be running — ``new_event_loop()`` alone leaves
    ``loop.is_running()`` False and the boundary's
    ``if self._loop and self._loop.is_running()`` guard falls
    through to the R7 WARNING branch.

    Mirrors ``tests/job_queue/test_seam_invariants.py:_start_loop_in_thread``.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def stop():
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    return loop, thread, stop


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_dir="/tmp",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_message_job(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
) -> JobItem:
    job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="test",
            agent_dir="/tmp",
            message="m",
            source="api",
            project_id="p1",
            queue_id="q1",
            priority=1,
            admission_state=admission_state,
            created_at=datetime.now(timezone.utc).isoformat(),
            instance_id=instance_id,
            job_type="message",
            retry_count=0,
            version=1,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def _seed_task(
    engine,
    *,
    work_id: str,
    instance_id: str | None = None,
    status: str = TaskStatus.RUNNING.value,
) -> Task:
    """Seed a ``Task`` row using the same minimal-shape pattern as
    ``tests/unit/job_queue/test_fix_b_terminal_message_mirror_backstop.py::_seed_task``.
    The default values for ``created_at`` / ``updated_at`` /
    ``task_type`` are populated by the model's
    ``default_factory`` — explicit ISO strings cause a SQLite
    ``DATETIME`` column "datatype mismatch" error in file-backed
    engines.
    """
    with Session(engine) as session:
        task = Task(
            work_id=work_id,
            instance_id=instance_id,
            task_type="process_message",
            status=status,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def _acquire_lock(
    engine,
    *,
    instance_id: str,
    job_id: str,
    project_id: str = "p1",
    queue_id: str = "q1",
    lock_slot: int = 0,
) -> JobLock:
    with Session(engine) as session:
        lock = JobLock(
            lock_id=f"lock-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            queue_id=queue_id,
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=lock_slot,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(lock)
        session.commit()
        session.refresh(lock)
    return lock


def _lock_count(engine, instance_id: str) -> int:
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM job_locks "
                    "WHERE instance_id = :instance_id"
                ),
                {"instance_id": instance_id},
            ).scalar()
            or 0
        )


def _lock_count_for_job(engine, job_id: str) -> int:
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM job_locks "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            ).scalar()
            or 0
        )


# ─────────────────────────────────────────────────────────────────────
# F1 — Inline writer (Fix B) releases the lock atomically
# ─────────────────────────────────────────────────────────────────────


class TestFixBInlineWriterReleasesLock:
    """F1 root fix: ``finalize_mirror_job_at_completion`` releases the
    lock in the same ``session.commit()`` as the JobItem UPDATE.

    Pre-fix: the inline writer finalized the JobItem but did NOT
    touch ``job_locks``. Release was delegated to the observer
    event that never fires when the inline writer wins the SQL
    guard race. The lock orphaned forever.
    """

    def test_active_message_job_lock_released_same_commit(
        self, engine, job_repo, lock_repo
    ):
        """The exact bug class: ACTIVE message JobItem + lock for the
        SAME instance + ``finalize_mirror_job_at_completion`` → lock
        released atomically."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        assert _lock_count(engine, instance_id) == 1, (
            "PRECONDITION: instance must hold a lock before fix"
        )

        # Run the F1 fix.
        finalized = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert finalized is not None
        assert finalized.admission_state == AdmissionState.DONE.value
        assert _lock_count(engine, instance_id) == 0, (
            "F1 fix: lock MUST be released in the same commit as the "
            "JobItem transition. Pre-fix this is 1 (orphan)."
        )

    def test_queued_message_job_lock_released_same_commit(
        self, engine, job_repo
    ):
        """``queued → done`` is also a legal transition; the lock
        release must fire for queued rows too — pre-fix queued jobs
        with locks (the B1 single-transaction window) would orphan."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="queued"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        finalized = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert finalized is not None
        assert _lock_count(engine, instance_id) == 0

    def test_already_terminal_job_noop_does_not_touch_lock(
        self, engine, job_repo
    ):
        """Idempotency: a second ``finalize_mirror_job_at_completion``
        call (already-terminal) is a silent no-op and MUST NOT touch
        the lock — a live lock on an unrelated terminal row is
        someone else's responsibility."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="done"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        result = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert result is None
        assert _lock_count(engine, instance_id) == 1, (
            "Idempotency: a no-op finalize MUST NOT delete locks."
        )

    def test_non_message_job_leaves_lock_alone(
        self, engine, job_repo
    ):
        """Scope discipline: ``task`` JobItems stay on the bus-gated
        finalize path — the inline writer leaves them alone AND
        does NOT touch their locks."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        # Flip to task-type after the seed (JobItem is created as message).
        with Session(engine) as session:
            row = session.get(JobItem, job.job_id)
            row.job_type = "task"
            session.add(row)
            session.commit()
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        result = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert result is None
        assert _lock_count(engine, instance_id) == 1, (
            "Scope discipline: task-type rows keep their locks."
        )

    def test_paused_instance_lock_released_same_commit(
        self, engine, job_repo
    ):
        """The 2026-09-14 prod incident: paused instance + lock +
        finalize → lock MUST be released even though the instance is
        parked. Pre-fix this is the exact 8h45m orphan."""
        instance_id = _seed_instance(
            engine, status=InstanceStatus.PAUSED.value
        )
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        # F1 fix fires BEFORE the bus event (which never publishes
        # for an already-terminal row); the lock MUST be gone.
        finalized = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert finalized is not None
        assert _lock_count(engine, instance_id) == 0, (
            "F1 fix: lock released same-commit even when instance is "
            "paused — pre-fix this was the prod 8h45m orphan."
        )

    def test_null_instance_id_job_no_lock_release_attempt(
        self, engine, job_repo
    ):
        """A virtual-job row (``instance_id IS NULL``) holds no
        per-queue lock; the release must be a safe no-op (no DELETE
        issued against ``instance_id=NULL``)."""
        job = _seed_message_job(
            engine, instance_id=None, admission_state="active"
        )

        finalized = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert finalized is not None
        # No exception; no rows affected on job_locks (there were
        # none to begin with).
        assert _lock_count_for_job(engine, job.job_id) == 0


# ─────────────────────────────────────────────────────────────────────
# F1 — Backstop writer (``reconcile_terminal_message_mirrors``) releases the lock
# ─────────────────────────────────────────────────────────────────────


class TestFixBBackstopReleasesLock:
    """F1 backstop fix: ``reconcile_terminal_message_mirrors``
    releases the lock in the same per-row ``session.commit()`` as the
    JobItem UPDATE.
    """

    def test_terminal_task_mirror_lock_released_same_commit(
        self, engine, job_repo, task_repo
    ):
        """A mirror whose linked Task is terminal but the JobItem is
        still ACTIVE — backstop reconciles it AND releases the lock
        in the same per-row commit."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        task = _seed_task(
            engine,
            work_id=job.job_id,
            instance_id=instance_id,
            status=TaskStatus.COMPLETED.value,
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        reaped = job_repo.reconcile_terminal_message_mirrors(
            task_repository=task_repo,
        )

        assert len(reaped) == 1
        assert _lock_count(engine, instance_id) == 0, (
            "F1 backstop fix: lock released same-commit as the mirror "
            "transition. Pre-fix this is 1 (orphan)."
        )

    def test_live_task_mirror_does_not_release_lock(
        self, engine, job_repo, task_repo
    ):
        """A mirror whose linked Task is still live must NOT be
        reconciled (and the lock must stay). The backstop is
        guarded by terminal task status."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _seed_task(
            engine,
            work_id=job.job_id,
            instance_id=instance_id,
            status=TaskStatus.RUNNING.value,
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        reaped = job_repo.reconcile_terminal_message_mirrors(
            task_repository=task_repo,
        )

        assert reaped == []
        assert _lock_count(engine, instance_id) == 1, (
            "Backstop must not release live-task locks."
        )

    def test_paused_instance_terminal_task_lock_released(
        self, engine, job_repo, task_repo
    ):
        """The 4h+ wedge: paused instance + active JobItem + terminal
        Task → backstop reconciles + releases lock. Pre-fix the lock
        was orphaned until terminate."""
        instance_id = _seed_instance(
            engine, status=InstanceStatus.PAUSED.value
        )
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _seed_task(
            engine,
            work_id=job.job_id,
            instance_id=instance_id,
            status=TaskStatus.CANCELLED.value,
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        reaped = job_repo.reconcile_terminal_message_mirrors(
            task_repository=task_repo,
        )

        assert len(reaped) == 1
        assert _lock_count(engine, instance_id) == 0, (
            "F4 alignment: paused instance + finalized job → lock "
            "released by the backstop (same-commit). The 2026-09-14 "
            "prod wedge was exactly this shape."
        )


# ─────────────────────────────────────────────────────────────────────
# F5 — ``force_finalize_orphan`` releases the lock atomically
# ─────────────────────────────────────────────────────────────────────


class TestForceFinalizeOrphanReleasesLock:
    """F5 fix: the orphan reaper (``force_finalize_orphan``) now
    DELETEs the lock row in the same ``engine.begin()`` transaction
    as the JobItem UPDATE.

    Pre-fix: the reaper was a bare UPDATE — the orphan instance
    kept its lock. The orphan reaper is invoked by the cleanup
    endpoint (``/api/jobs/cleanup``) and the recovery service;
    each invocation left a lock orphan behind.
    """

    def test_orphan_reap_releases_lock_same_transaction(
        self, engine, job_repo
    ):
        instance_id = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        reaped = job_repo.force_finalize_orphan(job.job_id, "cancelled")

        assert reaped is not None
        assert reaped.admission_state == AdmissionState.DONE.value
        assert _lock_count(engine, instance_id) == 0, (
            "F5 fix: orphan reap releases the lock in the same "
            "engine.begin() as the UPDATE. Pre-fix this is 1."
        )

    def test_orphan_reap_null_instance_id_no_lock_attempt(
        self, engine, job_repo
    ):
        """A virtual-job orphan (``instance_id IS NULL``) holds no
        lock; the DELETE must be skipped cleanly (no
        ``instance_id=NULL`` DELETE)."""
        job = _seed_message_job(
            engine, instance_id=None, admission_state="active"
        )

        reaped = job_repo.force_finalize_orphan(job.job_id, "cancelled")

        assert reaped is not None
        assert _lock_count_for_job(engine, job.job_id) == 0

    def test_orphan_reap_race_loss_does_not_release_lock(
        self, engine, job_repo
    ):
        """A concurrent legitimate finalize flipped the row first
        → ``force_finalize_orphan`` returns ``None`` and MUST NOT
        release any lock (the OTHER writer owns the release
        atomically)."""
        instance_id = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        # Concurrent winner: flip the row to done via raw SQL.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items "
                    "SET admission_state = 'done' "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job.job_id},
            )

        reaped = job_repo.force_finalize_orphan(job.job_id, "cancelled")

        assert reaped is None
        assert _lock_count(engine, instance_id) == 1, (
            "Race-loss: the OTHER writer owns the release; we MUST "
            "NOT delete the lock here."
        )


# ─────────────────────────────────────────────────────────────────────
# F3 + F4 — Periodic ``JobLockSweepService`` reclaims terminal-job locks
# ─────────────────────────────────────────────────────────────────────


class TestJobLockSweepReclaimsTerminalJobLocks:
    """F3 + F4 alignment: the periodic
    ``JobLockSweepService.sweep_once`` reclaims locks for jobs whose
    ``admission_state`` is terminal (``done`` / ``dead``),
    regardless of the parent instance's status.

    F4 contract: "paused instance holds ZERO locks for FINALIZED
    jobs" — the sweep is the smallest-risk carve-out that closes
    the gap. The 2026-08-24 paused-race guard (on
    ``reconcile_turn_mirror``) is a DIFFERENT path (resume races
    only) and is NOT touched by this sweep.
    """

    @pytest.mark.asyncio
    async def test_sweep_releases_terminal_job_lock(
        self, engine, lock_manager, job_lock_sweep
    ):
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="done"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        cleared = await job_lock_sweep.sweep_once()

        assert cleared == 1
        assert _lock_count(engine, instance_id) == 0

    @pytest.mark.asyncio
    async def test_sweep_releases_paused_instance_terminal_lock(
        self, engine, lock_manager, job_lock_sweep
    ):
        """F4 contract: paused instance + DONE job → sweep reclaims."""
        instance_id = _seed_instance(
            engine, status=InstanceStatus.PAUSED.value
        )
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="done"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        cleared = await job_lock_sweep.sweep_once()

        assert cleared == 1
        assert _lock_count(engine, instance_id) == 0

    @pytest.mark.asyncio
    async def test_sweep_preserves_active_job_lock(
        self, engine, lock_manager, job_lock_sweep
    ):
        """Active-job locks are NEVER reclaimed — only terminal-job
        locks. The SQL guard ``job_id NOT IN (active job ids)``
        prevents false-positive reclaim."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        cleared = await job_lock_sweep.sweep_once()

        assert cleared == 0
        assert _lock_count(engine, instance_id) == 1

    @pytest.mark.asyncio
    async def test_sweep_preserves_queued_job_lock(
        self, engine, lock_manager, job_lock_sweep
    ):
        """Queued-job locks are preserved (the B1 single-transaction
        window is still in flight)."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="queued"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        cleared = await job_lock_sweep.sweep_once()

        assert cleared == 0
        assert _lock_count(engine, instance_id) == 1

    @pytest.mark.asyncio
    async def test_sweep_releases_dead_job_lock(
        self, engine, lock_manager, job_lock_sweep
    ):
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="dead"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        cleared = await job_lock_sweep.sweep_once()

        assert cleared == 1
        assert _lock_count(engine, instance_id) == 0

    @pytest.mark.asyncio
    async def test_sweep_releases_paused_job_lock(
        self, engine, lock_manager, job_lock_sweep
    ):
        """A ``paused`` JobItem's lock is reclaimed — the B1/B2
        transition semantics consider a paused job non-active, so
        its lock would never be released naturally."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="paused"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        cleared = await job_lock_sweep.sweep_once()

        assert cleared == 1
        assert _lock_count(engine, instance_id) == 0

    @pytest.mark.asyncio
    async def test_sweep_idempotent_no_op_second_run(
        self, engine, lock_manager, job_lock_sweep
    ):
        """A second ``sweep_once`` after the first reclaim must be
        idempotent — no rows reclaimed, no errors."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="done"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        first = await job_lock_sweep.sweep_once()
        second = await job_lock_sweep.sweep_once()

        assert first == 1
        assert second == 0
        assert _lock_count(engine, instance_id) == 0


# ─────────────────────────────────────────────────────────────────────
# F2 — CancelledError hardening at async release sites (R6)
# ─────────────────────────────────────────────────────────────────────


class TestF2R6CancellationMidRelease:
    """F2 R6 hardening: ``cancel_job`` (the public entry to
    ``_cancel_active_job``) releases the lock inside
    ``try/except (Exception, asyncio.CancelledError)`` — a
    cancellation mid-release logs a WARNING + re-raises so the
    caller's shutdown path stays intact.

    Pre-fix: the bare ``await`` had no exception handling. A
    cancellation mid-release silently orphaned the lock AND
    swallowed the cancellation.

    Acceptance: the rewritten tests actually invoke ``cancel_job``
    with a raising mock ``release_queue_lock`` — they exercise the
    production hardening, not just the mock's behavior. The
    sweep-reclaim contract (the recovery seam for leaks the hardening
    could not prevent) gets its own dedicated test below.
    """

    @pytest.mark.asyncio
    async def test_cancellation_mid_release_propagates_and_logs(
        self, engine, job_repo, job_queue_service, caplog
    ):
        """A ``asyncio.CancelledError`` raised from inside
        ``release_queue_lock`` during ``cancel_job`` MUST:

        1. Reach the caller's ``pytest.raises`` (``CancelledError`` is
           a ``BaseException`` since Python 3.8, so ``except
           Exception`` does NOT catch it — the production hardening
           explicitly catches ``(Exception, asyncio.CancelledError)``
           then re-raises via ``isinstance`` discriminator at
           ``daemon/services/job_queue_service.py:1139-1142``).
        2. Be surfaced as a WARNING with the ``(R6 hardening)``
           marker so operators see the leak.
        3. NOT silently swallow the cancellation — the caller's
           shutdown path stays intact.

        Pre-fix: the bare ``await self._lock_manager.release_queue_lock(...)``
        had no exception handling. A cancellation mid-release
        silently orphaned the lock AND swallowed the cancellation
        (``asyncio.run_until_complete`` would see a ``CancelledError``
        that did not propagate to the caller).
        """
        import logging

        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        # Replace the lock_manager's release_queue_lock with one
        # that raises CancelledError mid-release. The hardening in
        # ``cancel_job`` MUST catch + log + re-raise.
        release_calls: list[tuple[str, str, str]] = []

        async def cancel_release(project_id, queue_id, job_id):
            release_calls.append((project_id, queue_id, job_id))
            raise asyncio.CancelledError(
                "simulated mid-release cancel"
            )

        job_queue_service._lock_manager.release_queue_lock = cancel_release

        # The hardened call site (job_queue_service.py:1124-1142)
        # catches CancelledError, logs a WARNING, then re-raises.
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.job_queue_service"
        ):
            with pytest.raises(asyncio.CancelledError):
                await job_queue_service.cancel_job(job.job_id)

        # (a) CancelledError propagated to caller — re-raise worked.
        # (b) WARNING logged with the R6 marker.
        r6_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "(R6 hardening)" in r.getMessage()
            and "_cancel_active_job" in r.getMessage()
        ]
        assert r6_records, (
            "F2 R6 hardening: cancel_job MUST log a WARNING naming "
            "the (R6 hardening) marker when release_queue_lock "
            "raises CancelledError. caplog records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        # (c) release_queue_lock was attempted (the catch block ran).
        assert release_calls == [("p1", "q1", job.job_id)], (
            "R6: the hardened call site MUST attempt the lock "
            "release before catching the CancelledError. "
            f"Got: {release_calls}"
        )

    @pytest.mark.asyncio
    async def test_cancellation_during_cancel_lock_reclaimed_by_sweep(
        self, engine, job_repo, lock_manager, job_lock_sweep, job_queue_service
    ):
        """The recovery seam for the R6 hardening: when the
        hardening catches a CancelledError and re-raises, the lock
        is NOT deleted (the release never committed). The periodic
        ``JobLockSweepService.sweep_once`` reclaims it once the
        job reaches a terminal ``admission_state``.

        Companion to ``test_cancellation_mid_release_propagates_and_logs``
        — split so each test pins exactly one contract (the
        hardening OR the recovery seam), not both. Pre-fix this
        class leaked forever (no sweep + no atomic release).
        """
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        async def cancel_release(*args, **kwargs):
            raise asyncio.CancelledError("simulated mid-release cancel")

        lock_manager.release_queue_lock = AsyncMock(side_effect=cancel_release)

        with pytest.raises(asyncio.CancelledError):
            await job_queue_service.cancel_job(job.job_id)

        # Lock is still present (release never committed); the
        # sweep reclaims only TERMINAL-job locks, so we finalize
        # the job first to simulate the natural-fix terminal
        # transition (the inline writer's atomic release would
        # have done this, but here we simulate the cancellation
        # interrupting it).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items "
                    "SET admission_state = 'done', "
                    "    terminal_reason = 'completed' "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job.job_id},
            )

        cleared = await job_lock_sweep.sweep_once()
        assert cleared == 1, (
            "Sweep reclaims the lock orphaned by a cancelled release. "
            "Pre-fix this class leaked forever (no sweep + no atomic "
            "release)."
        )
        assert _lock_count(engine, instance_id) == 0


# ─────────────────────────────────────────────────────────────────────
# F2 — ``_finalize_terminal_sync`` hardening (R7) — sync twin CancelledError
# ─────────────────────────────────────────────────────────────────────


class TestF2R7FinalizeTerminalSyncHardening:
    """F2 R7 hardening: the sync twin
    ``_finalize_terminal_sync`` (called from worker-thread contexts like
    ``trigger_next_job_sync``) also releases locks via
    ``asyncio.run_coroutine_threadsafe`` and must catch
    ``asyncio.CancelledError`` around ``future.result(...)``. The
    pre-fix code used bare ``except Exception``, which silently
    orphaned the lock when Python 3.8+ promoted
    ``asyncio.CancelledError`` to a ``BaseException``.

    Two branches need pinning:

    (a) **Event-loop-unavailable branch** (job_queue_service.py:4322-4339):
        when ``self._loop`` is unset OR not running, the sync twin
        must log a WARNING naming the leaked job's
        ``project_id`` / ``queue_id`` / ``instance_id`` so operators
        can trace the leak. The sweep (F3) reclaims it on the next
        tick. Pre-fix this branch silently no-op'd the release
        with NO diagnostic.

    (b) **``future.result()`` CancelledError branch**
        (job_queue_service.py:4303-4321): a ``CancelledError``
        raised from ``future.result(timeout=5)`` MUST be caught +
        logged + NOT re-raised. The sweep reclaims. Pre-fix the bare
        ``except Exception`` missed it; the cancellation either
        propagated (orphaning the lock + confusing the worker
        thread) or was silently dropped.

    Module docstring claimed R7 coverage but no test exercised
    either branch — this class closes that gap.
    """

    def test_event_loop_unavailable_logs_r7_warning_no_raise(
        self, engine, job_repo, job_queue_service, caplog
    ):
        """R7 branch (a): with ``self._loop`` unset, the sync twin's
        lock-release path is unreachable (it requires a running loop
        for ``asyncio.run_coroutine_threadsafe``). The hardening
        logs a WARNING with the R7 marker naming the leaked job's
        ``project_id``, ``queue_id``, and ``instance_id`` — operators
        can trace which lock was orphaned. No exception propagates
        (the lock release is a soft-fail; the sweep recovers).

        Pre-fix: this branch silently no-op'd the release with NO
        diagnostic.
        """
        import logging

        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        # Critical: ensure no loop is set so the "loop unavailable"
        # branch (else-clause at job_queue_service.py:4322-4339)
        # fires instead of the run_coroutine_threadsafe path.
        job_queue_service._loop = None

        # If the loop-unavailable branch is wired correctly, the
        # sync twin MUST NOT attempt any lock release.
        release_called: list[tuple] = []

        async def _should_not_run(*args, **kwargs):
            release_called.append((args, kwargs))
            return True

        job_queue_service._lock_manager.release_queue_lock = _should_not_run
        job_queue_service._lock_manager.release_by_instance = _should_not_run

        # Act — invoke the sync twin with caplog capturing WARNING+ records.
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.job_queue_service"
        ):
            canonical_job_id, _final_status = (
                job_queue_service._finalize_terminal_sync(
                    instance_id=instance_id,
                    decision=Decision.NO_RETRY,
                    job_id=job.job_id,
                )
            )

        # The sync twin should return the canonical job_id (it
        # found the job and ran the SQL UPDATE). The lock-release
        # step is what we are pinning here.
        assert canonical_job_id == job.job_id

        # (a) WARNING emitted with the R7 marker.
        r7_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "(R7 hardening)" in r.getMessage()
            and "_finalize_terminal_sync skipped lock release" in r.getMessage()
        ]
        assert r7_records, (
            "R7 branch (a): when the event loop is unavailable, "
            "_finalize_terminal_sync MUST log a WARNING naming the "
            "(R7 hardening) marker. caplog records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        warning_msg = r7_records[0].getMessage()
        # The WARNING must name the leaked job's identity so
        # operators can trace which lock was orphaned.
        assert job.job_id[:8] in warning_msg, (
            f"R7 WARNING must name the leaked job_id. Got: {warning_msg!r}"
        )
        assert "p1" in warning_msg, (
            f"R7 WARNING must name the project_id. Got: {warning_msg!r}"
        )
        assert "q1" in warning_msg, (
            f"R7 WARNING must name the queue_id. Got: {warning_msg!r}"
        )
        assert instance_id in warning_msg, (
            f"R7 WARNING must name the instance_id. Got: {warning_msg!r}"
        )

        # Lock manager MUST NOT have been called (loop unavailable).
        assert release_called == [], (
            "Lock release must not be attempted when the event loop "
            "is unavailable — the R7 WARNING is the operator-facing "
            f"signal. Got: {release_called}"
        )

    @pytest.mark.asyncio
    async def test_future_result_cancelled_error_caught_and_logged(
        self, engine, job_repo, job_queue_service, caplog
    ):
        """R7 branch (b): when ``self._loop`` IS running and the
        lock release coroutine raises ``CancelledError``, the sync
        twin's `try/except (Exception, asyncio.CancelledError)`
        around ``future.result(timeout=5)`` MUST catch it, log a
        WARNING with the R7 marker, and NOT re-raise — the
        cancellation would otherwise confuse the worker thread that
        called this sync method.

        Pre-fix: the bare ``except Exception`` did NOT catch
        ``asyncio.CancelledError`` (Python 3.8+ promotes it to
        ``BaseException``). The cancellation either propagated
        (orphaning the lock + crashing the worker thread) or was
        silently dropped depending on the call-site.
        """
        import logging

        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        # Replace release_queue_lock with a stub that yields once
        # then raises CancelledError. The future.result() in the sync
        # twin surfaces CancelledError exactly as a real cancellation
        # would.
        async def cancel_release(*args, **kwargs):
            # Yield once so the coroutine is actually scheduled on
            # the running loop (otherwise run_coroutine_threadsafe
            # returns immediately without giving the cancellation a
            # chance to fire mid-flight).
            await asyncio.sleep(0)
            raise asyncio.CancelledError(
                "simulated sync-twin mid-release cancel"
            )

        job_queue_service._lock_manager.release_queue_lock = cancel_release

        # Start a live event loop on a background thread so the
        # sync twin's run_coroutine_threadsafe actually executes.
        loop, _thread, stop_loop = _start_loop_in_thread()
        job_queue_service.set_event_loop(loop)

        try:
            # Act — invoke the sync twin. Pre-fix this would
            # propagate CancelledError to the caller (or be silently
            # dropped if the bare except Exception path masked it).
            # The R7 hardening catches + logs + DOES NOT re-raise.
            with caplog.at_level(
                logging.WARNING,
                logger="daemon.services.job_queue_service",
            ):
                canonical_job_id, _final_status = (
                    job_queue_service._finalize_terminal_sync(
                        instance_id=instance_id,
                        decision=Decision.NO_RETRY,
                        job_id=job.job_id,
                    )
                )

            # The sync twin returns the canonical job_id — the SQL
            # UPDATE succeeded; only the lock-release step is
            # exercised by this test.
            assert canonical_job_id == job.job_id

            # (b) WARNING emitted with the R7 marker.
            r7_records = [
                r for r in caplog.records
                if r.levelno >= logging.WARNING
                and "(R7 hardening)" in r.getMessage()
                and "_finalize_terminal_sync: lock release failed"
                in r.getMessage()
            ]
            assert r7_records, (
                "R7 branch (b): future.result() raising "
                "CancelledError MUST be caught and logged with the "
                "(R7 hardening) marker. caplog records: "
                f"{[r.getMessage() for r in caplog.records]}"
            )
            warning_msg = r7_records[0].getMessage()
            assert job.job_id[:8] in warning_msg, (
                f"R7 WARNING must name the leaked job_id. "
                f"Got: {warning_msg!r}"
            )

            # The lock is still present (release never committed);
            # the periodic sweep reclaims it once the job is
            # terminal. The job is now terminal (the sync twin
            # transitioned it to 'done'), so a sweep should reclaim.
            from daemon.services.job_lock_sweep import (
                JobLockSweepService,
            )

            sweep = JobLockSweepService(
                job_lock_manager=job_queue_service._lock_manager,
                interval_seconds=1,
            )
            cleared = await sweep.sweep_once()
            assert cleared == 1, (
                "Sweep reclaims the lock orphaned by a cancelled "
                "sync-twin release. Pre-fix this class leaked "
                "forever (no sweep + no hardening)."
            )
            assert _lock_count(engine, instance_id) == 0
        finally:
            stop_loop()


# ─────────────────────────────────────────────────────────────────────
# Healthy-path pin: the R8 atomic release pattern still works
# ─────────────────────────────────────────────────────────────────────


class TestR8HealthyPathPin:
    """The observer's R8 atomic release
    (``job_feedback_observer.py:_finalize_job_db_sync:3977-3981``)
    continues to release locks. The new inline/backstop releases
    are ADDITIVE — they catch the race-loss cases that the
    observer was previously the only path for.

    These tests pin the lock-release contract directly via the
    LockRepository primitives the observer uses, so a future
    refactor of the observer that breaks the atomic shape would
    surface here.
    """

    def test_release_by_instance_deletes_lock(
        self, engine, lock_repo
    ):
        instance_id = _seed_instance(engine)
        _acquire_lock(engine, instance_id=instance_id, job_id="j1")
        assert _lock_count(engine, instance_id) == 1

        deleted = lock_repo.release_by_instance(instance_id)
        assert deleted == 1
        assert _lock_count(engine, instance_id) == 0

    def test_release_by_job_deletes_lock(
        self, engine, lock_repo
    ):
        instance_id = _seed_instance(engine)
        _acquire_lock(engine, instance_id=instance_id, job_id="j2")
        assert _lock_count_for_job(engine, "j2") == 1

        deleted = lock_repo.release_by_job("p1", "q1", "j2")
        assert deleted is True
        assert _lock_count_for_job(engine, "j2") == 0

    def test_release_by_job_idempotent(
        self, engine, lock_repo
    ):
        """A second ``release_by_job`` call is a no-op
        (``rowcount == 0``). The atomic DELETE protects against
        double-release."""
        instance_id = _seed_instance(engine)
        _acquire_lock(engine, instance_id=instance_id, job_id="j3")

        first = lock_repo.release_by_job("p1", "q1", "j3")
        second = lock_repo.release_by_job("p1", "q1", "j3")

        assert first is True
        assert second is False


# ─────────────────────────────────────────────────────────────────────
# Worktree regression proof runbook (DOC ONLY)
# ─────────────────────────────────────────────────────────────────────
#
# The acceptance bar requires "exact-failure proof at base" — i.e.
# the same tests run against the pre-fix ``56391477`` base MUST fail
# in their primary contract. To reproduce:
#
#   1. Check out a pre-fix worktree at ``56391477`` (no F1-F5 fixes):
#
#        git worktree add /tmp/wt-pre-fix 56391477
#
#   2. From the worktree root, run:
#
#        uv run python -m pytest \
#            tests/unit/job_queue/test_joblock_leak_fixes.py \
#            -k "test_active_message_job_lock_released_same_commit \
#                or test_orphan_reap_releases_lock_same_transaction \
#                or test_sweep_releases_terminal_job_lock" \
#            -v
#
#   3. Expected exact failures (pre-fix):
#
#        test_active_message_job_lock_released_same_commit:
#            AssertionError: F1 fix: lock MUST be released in the
#            same commit as the JobItem transition. Pre-fix this is
#            1 (orphan). assert _lock_count(...) == 0
#
#        test_orphan_reap_releases_lock_same_transaction:
#            AssertionError: F5 fix: orphan reap releases the lock
#            in the same engine.begin() as the UPDATE. Pre-fix this
#            is 1. assert _lock_count(...) == 0
#
#        test_sweep_releases_terminal_job_lock: PASS (the sweep
#            primitive existed at base; this test pins the
#            wiring-to-sweep contract that F3 introduces).
#
#   4. Revert the worktree when done:
#
#        git worktree remove /tmp/wt-pre-fix --force
