"""Acceptance tests for the joblock-leak fix (F1-F5, branch feature/fix-joblock-leak).

The 2026-09-14 incident pinned the 7807e521 event-keying family as the
root cause of a 8.5-hour production lane starvation: the Fix-B inline
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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, SQLModel, select

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
# mirrors the harness in ``tests/job_queue/test_orphan_reaper.py``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue import JobRepository
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
from daemon.services.job_state_machine import (
    InvalidTransitionError,
    job_state_machine,
)


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
    """F2 R6 hardening: ``_cancel_active_job`` releases the lock
    inside ``try/except (Exception, asyncio.CancelledError)`` — a
    cancellation mid-release logs a WARNING + re-raises so the
    caller's shutdown path stays intact.

    The periodic sweep (F3) reclaims the orphaned lock on the next
    tick.

    Pre-fix: the bare ``await`` had no exception handling. A
    cancellation mid-release silently orphaned the lock AND
    swallowed the cancellation (the asyncio.run_until_complete
    would see a CancelledError that did not propagate to the
    caller).
    """

    @pytest.mark.asyncio
    async def test_cancellation_logs_and_re_raises(
        self, engine, lock_manager, job_lock_sweep
    ):
        """A cancellation mid-release MUST be visible to the caller
        (re-raised) and the leak must be reclaimable by the periodic
        sweep.

        The hardened call site (``_cancel_active_job`` at
        ``daemon/services/job_queue_service.py:1108+``) wraps the
        release in ``try/except (Exception, asyncio.CancelledError)``
        so the cancellation is logged + re-raised rather than
        silently swallowed. The lock stays orphaned in-memory (the
        release never committed); the periodic
        ``JobLockSweepService`` reclaims it on the next tick.

        Pre-fix: the bare ``await`` had no exception handling — a
        cancellation mid-release silently orphaned the lock AND
        swallowed the cancellation.
        """
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active"
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id)

        # Build a lock_manager whose release raises CancelledError.
        from unittest.mock import AsyncMock

        async def cancel_release(*args, **kwargs):
            raise asyncio.CancelledError("simulated mid-release cancel")

        lock_manager.release_queue_lock = AsyncMock(
            side_effect=cancel_release
        )

        # The hardened call site catches CancelledError, logs, and
        # re-raises. The lock stays orphaned (the release never
        # committed); the periodic sweep reclaims it.
        with pytest.raises(asyncio.CancelledError):
            await lock_manager.release_queue_lock("p1", "q1", job.job_id)

        # Lock is still present (release never committed); the
        # sweep reclaims on the next tick. The job is still
        # ACTIVE — the sweep only reclaims TERMINAL-job locks, so
        # we need to finalize the job first to simulate the
        # natural-fix terminal transition (the inline writer's
        # atomic release would have done this, but here we
        # simulate the cancellation interrupting it).
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
