"""Pattern (f) — orphan ACTIVE JobItem recovery tests.

RCA: incident 802095d8 (2026-08-29) — a daemon restart cleared
the ``task`` table ("Cleared 737 backlog task(s)") but the
``job_queue_items`` rows SURVIVED the restart-wipe asymmetry.
Result: an ``admission_state='active'`` JobItem with no Task
rows and an alive/stale instance. The JobItem sat ``active``
forever, the defer idle gate held forever, and the operator had
no way to clean the row up.

Pattern (f) is the leader-locked fix. Two sub-shapes:

  * **(f1)** active JobItem + NO Task rows + alive instance
    (older than the grace) → finalize to
    ``admission_state='dead'`` (DEAD, distinct from Pattern (a)'s
    ``failed`` outcome) + release the per-job queue lock
    (scoped via ``release_by_job`` per the F4/F7 contract).

  * **(f2)** active JobItem + COMPLETED Task → finalize to
    ``admission_state='done'`` (DONE) + fire/cancel
    dependency watchers via
    ``JobQueueService.notify_watchers``.

Healthy shapes (active JobItem + pending Task; active JobItem +
running Task) are EXPLICITLY EXCLUDED via guard clauses —
encoded as ``continue`` branches with explicit pattern names
(``orphan_active_skipped_healthy_shape``) so observability
can confirm a misconfigured deploy hasn't accidentally
collapsed the guard.

The brief is explicit on the EXCLUSION contract:
"EXPLICITLY EXCLUDE healthy shapes: active JobItem + pending
Task (awaiting claim) and active JobItem + running Task (in
flight) must NEVER match — encode as explicit guards, not
incidental side effects." Both the b-shape tests push
``updated_at``/``created_at`` far past the grace and assert
the JobItem is left alone (the grace does NOT bypass the
healthy-shape guard).

A/B convention: tests are designed to FAIL on the pre-fix base
(``b4dbfda2``) and PASS on the post-fix tree. The
red-green evidence is collected in the same turn that the
post-fix tests pass.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session as SQLModelSession, SQLModel

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services import job_recovery_service as _jrs
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_processor import JobProcessor
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.job_queue_service import JobQueueService
from daemon.services.instance_messaging import AsyncMessageResult
from daemon.services.stale_task_recovery import StaleTaskRecovery


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers — raw-SQL seeders matching the conventions in
# tests/job_queue/test_seam_invariants.py.
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
    agent_id: str = "developer",
    created_at: datetime | None = None,
    parent_id: str | None = None,
    last_activity_at: datetime | None = None,
) -> None:
    """Insert an Instance row directly via SQL. Mirrors the
    helper in test_seam_invariants.py.

    The optional ``created_at`` lets tests backdate the
    instance past the W1 mid-mint guard without an
    additional UPDATE round-trip.

    The optional ``parent_id`` + ``last_activity_at``
    (f1-misfire batch, incident 2026-08-31) let tests
    build the subtree shape the new tree-alive guard
    enumerates: ``parent_id`` links a grandchild into the
    permanent lineage and ``last_activity_at`` backdates /
    freshens the tree-activity signal.
    """
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    activity_iso = (
        last_activity_at.isoformat()
        if last_activity_at is not None
        else None
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id,
                     last_activity_at)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1, :parent_id,
                     :last_activity_at)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "parent_id": parent_id,
                "last_activity_at": activity_iso,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_metadata: dict | None = None,
    created_at: datetime | None = None,
) -> None:
    """Insert a JobItem directly via SQL. The optional
    ``created_at`` lets tests backdate the row past the
    grace period without an additional UPDATE round-trip.
    """
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    metadata_json = json.dumps(job_metadata or {})
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "hi",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


def _insert_task_with_status(
    engine,
    *,
    work_id: str,
    instance_id: str,
    message_id: str | None = None,
    status: str = TaskStatus.PENDING.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> int:
    """Insert a Task row directly via SQL with an explicit
    ``work_id`` (the cross-system linkage key — must match
    the JobItem's ``job_id`` for the f2 candidate to be
    detected).

    The optional ``completed_at`` lets tests backdate the
    Task past the
    ``_F2_COMPLETED_AGE_FLOOR_SECONDS`` age floor without
    an additional UPDATE round-trip. ``completed_at`` is
    only meaningful when ``status`` is terminal
    (COMPLETED / FAILED / CANCELLED).
    """
    now = (created_at or datetime.now(timezone.utc))
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background,
                     completed_at)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background,
                     :completed_at)
                """
            ),
            {
                "task_type": task_type,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": False,
                "is_background": False,
                "completed_at": completed_iso,
            },
        )
        return result.lastrowid


def _insert_lock(
    engine,
    *,
    project_id: str,
    queue_id: str,
    job_id: str,
    instance_id: str,
    lock_slot: int = 0,
) -> None:
    """Insert a JobLock row directly via SQL. The lock is
    required for the f1 lock-release assertion (the test
    confirms the per-job lock is released after DEAD
    finalization, per the F4/F7 contract).
    """
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_locks
                    (lock_id, project_id, queue_id, job_id,
                     instance_id, lock_slot, acquired_at)
                VALUES
                    (:lock_id, :project_id, :queue_id, :job_id,
                     :instance_id, :lock_slot, :acquired_at)
                """
            ),
            {
                "lock_id": f"lock-{job_id}",
                "project_id": project_id,
                "queue_id": queue_id,
                "job_id": job_id,
                "instance_id": instance_id,
                "lock_slot": lock_slot,
                "acquired_at": now,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def stale_recovery(task_repository) -> StaleTaskRecovery:
    """StaleTaskRecovery with the message/event/notifier deps
    stubbed (Pattern (f) doesn't touch them).
    """
    return StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )


@pytest.fixture
def job_queue_service_mock() -> MagicMock:
    """A MagicMock for ``JobQueueService``. The ``notify_watchers``
    method is wired as an ``AsyncMock`` so Pattern (f2)'s
    watcher-cleanup contract can be asserted on.
    """
    mock = MagicMock()
    mock.notify_watchers = AsyncMock(return_value=None)
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPatternF1OrphanActiveDead:
    """Pattern (f1) — active JobItem + NO Task rows + alive
    instance (older than the grace) → DEAD finalization +
    per-job lock release.
    """

    @pytest.mark.asyncio
    async def test_f1_finalizes_orphan_to_dead_and_releases_lock(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """f1 happy path: instance alive, no Task rows,
        JobItem ``created_at`` pushed past the grace → the
        reconciler finalizes the JobItem to
        ``admission_state='dead'`` AND releases the
        per-job lock scoped by
        ``(project_id, queue_id, job_id)``.

        The test pins:

        1. ``reconcile_drift_states`` returns
           ``reconciled >= 1`` and the detail record
           has ``pattern='orphan_active_no_task_dead'``.
        2. The JobItem row's ``admission_state`` is now
           ``'dead'`` (NOT ``'done'`` — DEAD is
           distinct from Pattern (a)'s FAILED outcome).
        3. The JobLock row is gone (released via
           ``release_by_job``). Sibling locks (a
           different job on the same queue) are NOT
           released — F4/F7 invariant.
        """
        # Arrange — instance alive (running), JobItem active,
        # backdated past the grace. NO Task rows (the
        # restart-orphan signature).
        #
        # Council REJECT 2026-08-29 W1: backdate BOTH
        # ``instance.created_at`` and ``JobItem.created_at``
        # past the grace — the W1 mid-mint guard closes the
        # spawn→Task-mint window for just-spawned instances.
        _insert_instance(
            engine,
            "inst-f1-1",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_job_item(
            engine,
            job_id="job-f1-1",
            instance_id="inst-f1-1",
            project_id="test-project",
            queue_id="queue-f1-1",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f1-1",
            job_id="job-f1-1",
            instance_id="inst-f1-1",
        )
        # Sibling lock on the same queue — must SURVIVE
        # the f1 release (F4/F7 invariant).
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f1-1",
            job_id="job-f1-sibling",
            instance_id="inst-f1-sibling",
            lock_slot=1,
        )

        # Sanity — pre-reconcile, the lock is held.
        pre_locks = lock_repo.get_all_locks()
        pre_lock_ids = {lk.lock_id for lk in pre_locks}
        assert "lock-job-f1-1" in pre_lock_ids, (
            f"Pre-reconcile lock for job-f1-1 should exist. "
            f"Found: {pre_lock_ids}"
        )
        assert "lock-job-f1-sibling" in pre_lock_ids, (
            f"Pre-reconcile sibling lock should exist. "
            f"Found: {pre_lock_ids}"
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Act — use a 60s grace so the 1800s backdate is
        # clearly past the line.
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — reconciled count includes the f1 row.
        assert stats["reconciled"] >= 1, (
            f"Reconciler must apply at least one f1 correction. "
            f"Got stats: {stats}"
        )

        # Assert — detail record for the f1 row.
        f1_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f1-1"
        ]
        assert f1_records, (
            f"Reconciler must record an orphan_active_no_task_dead "
            f"detail for job-f1-1. Got details: {stats['details']}"
        )
        f1_detail = f1_records[0]
        assert f1_detail.get("instance_id") == "inst-f1-1", (
            f"f1 detail must carry the instance_id. "
            f"Got: {f1_detail}"
        )
        assert f1_detail.get("task_id") is None, (
            f"f1 detail must have task_id=None (no Task rows). "
            f"Got: {f1_detail}"
        )

        # Assert — JobItem is now DEAD.
        job_after = repository.get("job-f1-1")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DEAD.value, (
            f"f1 must finalize the JobItem to admission_state='dead'. "
            f"Got admission_state={job_after.admission_state!r}"
        )

        # Assert — the per-job lock is released.
        post_locks = lock_repo.get_all_locks()
        post_lock_ids = {lk.lock_id for lk in post_locks}
        assert "lock-job-f1-1" not in post_lock_ids, (
            f"f1 must release the per-job lock scoped by "
            f"(project_id, queue_id, job_id). "
            f"Found locks: {post_lock_ids}"
        )
        # Assert — sibling lock SURVIVES (F4/F7 invariant).
        assert "lock-job-f1-sibling" in post_lock_ids, (
            f"F4/F7 invariant: sibling lock must NOT be released. "
            f"Found locks: {post_lock_ids}"
        )

    @pytest.mark.asyncio
    async def test_f1_within_grace_is_left_alone(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """f1 grace boundary: JobItem with ``created_at``
        INSIDE the grace → NOT finalized, detail recorded
        as ``orphan_active_skipped_grace``.

        The brief is explicit: "updated_at just inside
        grace → no match". The age signal we use is
        ``JobItem.created_at`` (the model has no
        ``updated_at`` column; ``created_at`` is the
        canonical age signal for an active JobItem —
        documented in the pattern's docstring).
        """
        _insert_instance(engine, "inst-f1-grace", project_id="test-project")
        # JobItem created 30s ago — INSIDE the 60s grace.
        _insert_job_item(
            engine,
            job_id="job-f1-grace",
            instance_id="inst-f1-grace",
            project_id="test-project",
            queue_id="queue-f1-grace",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f1-grace",
            job_id="job-f1-grace",
            instance_id="inst-f1-grace",
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Act — grace=60s, JobItem is 30s old.
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NOT corrected (reconciled=0 for the
        # f1 row), but the grace detail was recorded.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f1-grace"
        ]
        assert not f1_corrected, (
            f"JobItem inside the grace must NOT be finalized. "
            f"Got: {f1_corrected}"
        )
        grace_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_grace"
            and d.get("job_id") == "job-f1-grace"
        ]
        assert grace_records, (
            f"JobItem inside the grace must be recorded as "
            f"orphan_active_skipped_grace. "
            f"Got details: {stats['details']}"
        )

        # Assert — JobItem is still active.
        job_after = repository.get("job-f1-grace")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"JobItem inside the grace must stay active. "
            f"Got admission_state={job_after.admission_state!r}"
        )

        # Assert — lock is still held (no release inside grace).
        post_locks = lock_repo.get_all_locks()
        assert any(lk.lock_id == "lock-job-f1-grace" for lk in post_locks), (
            "Lock must NOT be released while JobItem is inside the grace."
        )


class TestPatternF2OrphanActiveCompletedTask:
    """Pattern (f2) — active JobItem + COMPLETED Task →
    DONE finalization + watcher fire/cancel.
    """

    @pytest.mark.asyncio
    async def test_f2_finalizes_completed_task_to_done_and_notifies(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """f2 happy path: active JobItem + COMPLETED Task
        (the Task finished but the JobItem side never
        transitioned) → the reconciler finalizes the
        JobItem to ``admission_state='done'`` and fires
        the dependency watchers via
        ``JobQueueService.notify_watchers``.

        The Task is the authoritative "this work is
        done" signal — the JobItem side is the lagging
        observer. The Task's ``work_id`` matches the
        JobItem's ``job_id`` per the dispatch contract.

        Council REJECT 2026-08-29 Critical #3:
        the f2 path is gated on bus_pending==0,
        no PENDING instance tasks, and a 60s age
        floor on ``task.completed_at``. W1: instance
        must also be past the grace. This test
        wires all the supporting seams so the
        gate passes (bus wired with zero pending
        watchers, no PENDING instance tasks,
        completed_at backdated past the floor,
        instance backdated past the grace).
        """
        from unittest.mock import patch
        from daemon.services.dependency_bus import (
            DependencyBus,
            FollowUp,
            Outcome,
        )
        from daemon.repositories.task.models import Task as TaskModel
        from sqlmodel import Session as _Session

        _insert_instance(
            engine,
            "inst-f2-1",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_job_item(
            engine,
            job_id="job-f2-1",
            instance_id="inst-f2-1",
            project_id="test-project",
            queue_id="queue-f2-1",
            admission_state=AdmissionState.ACTIVE.value,
        )
        # Task with work_id == job_id (linkage contract)
        # and status = COMPLETED. Backdate
        # ``completed_at`` past the
        # ``_F2_COMPLETED_AGE_FLOOR_SECONDS`` (60s) gate.
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f2-1",
            instance_id="inst-f2-1",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        # Wire a dependency-bus singleton stub that
        # reports zero pending watchers — the f2 gate
        # calls ``bus.pending_watchers(task_id)`` and
        # expects an empty list.
        class _BusStub:
            async def pending_watchers(self, source_task_id):
                return []

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Act — grace doesn't matter for f2 (f2 has no
        # grace; only f1 does). The 0s grace is a
        # sanity belt. Patch the bus singleton so the
        # gate sees the empty-pending-watcher stub.
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_BusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # Assert — f2 detail record.
        f2_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f2-1"
        ]
        assert f2_records, (
            f"Reconciler must record an "
            f"orphan_active_completed_task_done detail for job-f2-1. "
            f"Got details: {stats['details']}"
        )
        f2_detail = f2_records[0]
        assert f2_detail.get("task_id") == task_id, (
            f"f2 detail must carry the Task id. "
            f"Got: {f2_detail}"
        )

        # Assert — JobItem is DONE.
        job_after = repository.get("job-f2-1")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"f2 must finalize the JobItem to admission_state='done'. "
            f"Got admission_state={job_after.admission_state!r}"
        )

        # Assert — notify_watchers fired.
        job_queue_service_mock.notify_watchers.assert_awaited()
        # Pull the most recent call's positional args.
        last_call = job_queue_service_mock.notify_watchers.await_args
        assert last_call is not None, (
            "notify_watchers must have been awaited by Pattern (f2)"
        )
        # The call should reference job-f2-1 and "completed".
        args = last_call.args
        assert args[0] == "job-f2-1", (
            f"notify_watchers must be called with the JobItem id. "
            f"Got: {args}"
        )
        assert args[1] == "completed", (
            f"notify_watchers must be called with 'completed' status. "
            f"Got: {args}"
        )


class TestPatternFHealthyShapeExclusion:
    """Healthy-shape exclusion — explicit guard test.

    The brief is explicit: "EXPLICITLY EXCLUDE healthy
    shapes: active JobItem + pending Task (awaiting
    claim) and active JobItem + running Task (in flight)
    must NEVER match — encode as explicit guards, not
    incidental side effects."

    Both sub-shapes are tested with ``created_at``
    pushed far past the grace (so the f1 grace guard
    is NOT the thing keeping the JobItem out of the
    correction set) and assert the JobItem stays
    ``active``. The detail record must use the
    ``orphan_active_skipped_healthy_shape`` pattern
    name.
    """

    @pytest.mark.asyncio
    async def test_pending_task_is_excluded_even_past_grace(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Active JobItem + PENDING Task + alive instance
        + past the grace → must NOT be matched. The
        healthy-shape guard pre-empts the grace.
        """
        _insert_instance(engine, "inst-f-shape-pending", project_id="test-project")
        _insert_job_item(
            engine,
            job_id="job-f-shape-pending",
            instance_id="inst-f-shape-pending",
            project_id="test-project",
            queue_id="queue-f-shape-pending",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f-shape-pending",
            instance_id="inst-f-shape-pending",
            status=TaskStatus.PENDING.value,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NO f1 / f2 correction.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-shape-pending"
        ]
        f2_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f-shape-pending"
        ]
        assert not f1_corrected, (
            f"Active JobItem + PENDING Task must NOT be matched by f1. "
            f"Got: {f1_corrected}"
        )
        assert not f2_corrected, (
            f"Active JobItem + PENDING Task must NOT be matched by f2. "
            f"Got: {f2_corrected}"
        )

        # Assert — the healthy-shape guard detail was recorded.
        healthy_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_healthy_shape"
            and d.get("job_id") == "job-f-shape-pending"
        ]
        assert healthy_records, (
            f"PENDING Task must produce an "
            f"orphan_active_skipped_healthy_shape detail. "
            f"Got details: {stats['details']}"
        )
        assert healthy_records[0].get("task_id") == task_id, (
            f"Detail must carry the Task id. "
            f"Got: {healthy_records[0]}"
        )

        # Assert — JobItem is still active (NOT DEAD).
        job_after = repository.get("job-f-shape-pending")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"Active JobItem + PENDING Task must stay active. "
            f"Got admission_state={job_after.admission_state!r}"
        )

    @pytest.mark.asyncio
    async def test_running_task_is_excluded_even_past_grace(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Active JobItem + RUNNING Task + alive instance
        + past the grace → must NOT be matched.
        """
        _insert_instance(engine, "inst-f-shape-running", project_id="test-project")
        _insert_job_item(
            engine,
            job_id="job-f-shape-running",
            instance_id="inst-f-shape-running",
            project_id="test-project",
            queue_id="queue-f-shape-running",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f-shape-running",
            instance_id="inst-f-shape-running",
            status=TaskStatus.RUNNING.value,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NO f1 / f2 correction.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-shape-running"
        ]
        f2_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f-shape-running"
        ]
        assert not f1_corrected, (
            f"Active JobItem + RUNNING Task must NOT be matched by f1. "
            f"Got: {f1_corrected}"
        )
        assert not f2_corrected, (
            f"Active JobItem + RUNNING Task must NOT be matched by f2. "
            f"Got: {f2_corrected}"
        )

        # Assert — the healthy-shape guard detail was recorded.
        healthy_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_healthy_shape"
            and d.get("job_id") == "job-f-shape-running"
        ]
        assert healthy_records, (
            f"RUNNING Task must produce an "
            f"orphan_active_skipped_healthy_shape detail. "
            f"Got details: {stats['details']}"
        )
        assert healthy_records[0].get("task_id") == task_id, (
            f"Detail must carry the Task id. "
            f"Got: {healthy_records[0]}"
        )

        # Assert — JobItem is still active.
        job_after = repository.get("job-f-shape-running")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"Active JobItem + RUNNING Task must stay active. "
            f"Got admission_state={job_after.admission_state!r}"
        )


class TestPatternFGraceBoundary:
    """Grace boundary — strict less-than semantics.

    The brief is explicit: "updated_at just inside
    grace → no match; at/just past grace → match."
    """

    @pytest.mark.asyncio
    async def test_just_inside_grace_no_match(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """JobItem with ``created_at`` 59s ago, grace=60s →
        NO match (strict less-than boundary).
        """
        _insert_instance(engine, "inst-f-grace-inside", project_id="test-project")
        # 59s old — inside the 60s grace.
        _insert_job_item(
            engine,
            job_id="job-f-grace-inside",
            instance_id="inst-f-grace-inside",
            project_id="test-project",
            queue_id="queue-f-grace-inside",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=59),
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NO f1 correction.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-grace-inside"
        ]
        assert not f1_corrected, (
            f"JobItem 59s old (inside 60s grace) must NOT match. "
            f"Got: {f1_corrected}"
        )

        # Assert — grace detail recorded.
        grace_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_grace"
            and d.get("job_id") == "job-f-grace-inside"
        ]
        assert grace_records, (
            f"JobItem 59s old must record "
            f"orphan_active_skipped_grace. "
            f"Got details: {stats['details']}"
        )

    @pytest.mark.asyncio
    async def test_just_past_grace_match(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """JobItem with ``created_at`` 61s ago, grace=60s →
        MATCH (strict less-than: 61 > 60 is past the line).
        Council REJECT 2026-08-29 W1: also backdate the
        instance past the grace — the mid-mint guard
        applies to BOTH ``JobItem.created_at`` and
        ``Instance.created_at``.
        """
        _insert_instance(
            engine,
            "inst-f-grace-past",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=61),
        )
        # 61s old — just past the 60s grace.
        _insert_job_item(
            engine,
            job_id="job-f-grace-past",
            instance_id="inst-f-grace-past",
            project_id="test-project",
            queue_id="queue-f-grace-past",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=61),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f-grace-past",
            job_id="job-f-grace-past",
            instance_id="inst-f-grace-past",
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — f1 correction happened.
        f1_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-grace-past"
        ]
        assert f1_records, (
            f"JobItem 61s old (just past 60s grace) MUST match f1. "
            f"Got details: {stats['details']}"
        )

        # Assert — JobItem is DEAD.
        job_after = repository.get("job-f-grace-past")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DEAD.value, (
            f"JobItem 61s old (past grace) must be DEAD. "
            f"Got admission_state={job_after.admission_state!r}"
        )


class TestPatternFWiring:
    """Pattern (f) wiring — the pattern is actually invoked
    by the periodic reconciler entry point. A/B sanity:
    this test exists primarily to catch dead-code
    regressions (a future refactor accidentally moving
    the call out of ``reconcile_drift_states``).
    """

    @pytest.mark.asyncio
    async def test_pattern_f_invoked_from_reconcile_drift_states(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Sanity: a candidate setup that ONLY Pattern (f1)
        can detect (active JobItem + no Task + alive
        instance + past grace) is corrected by
        ``reconcile_drift_states``. If Pattern (f) is
        unwired, this test fails.
        """
        _insert_instance(
            engine,
            "inst-f-wiring",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-wiring",
            instance_id="inst-f-wiring",
            project_id="test-project",
            queue_id="queue-f-wiring",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f-wiring",
            job_id="job-f-wiring",
            instance_id="inst-f-wiring",
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # The pattern MUST have fired (or the test fails
        # — which means the pattern was unwired).
        assert stats["reconciled"] >= 1, (
            f"Pattern (f) must fire from reconcile_drift_states. "
            f"Got stats: {stats}"
        )
        assert any(
            d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-wiring"
            for d in stats["details"]
        ), (
            f"Pattern (f) must record the f1 detail. "
            f"Got details: {stats['details']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Council REJECT 2026-08-29 — new tests for Critical #1, Critical #2,
# Critical #3, and W1 fixes.
#
# A/B convention: each new test is designed to FAIL on the pre-fix base
# (``97103462``) and PASS on the post-fix tree. The four Critical-#1
# tests + the lock-release test + the gate tests are the red/green
# acceptance suite; the report captures the A/B evidence in a single
# turn with the green run.
# ─────────────────────────────────────────────────────────────────────────────


class TestPatternFCouncilCritical1Guard:
    """Council REJECT 2026-08-29 Critical #1 — strict f1
    guard: any Task row present means NOT an f1
    candidate. PENDING/RUNNING → existing healthy
    shape. PAUSED → new ``orphan_active_skipped_paused``.
    FAILED/CANCELLED → route through the
    ``_fail_orphaned_job``-style boundary (lock release
    + failed_at + terminal_reason + notify_watchers),
    NOT bare DEAD.
    """

    @pytest.mark.asyncio
    async def test_paused_task_is_excluded_even_past_grace(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """PAUSED Task + alive instance + past grace →
        NOT bare DEAD. The reconciler must skip with
        ``orphan_active_skipped_paused`` (NOT match f1,
        NOT fall through to f2). Resume will re-mint
        a fresh Task on the same JobItem — bare DEAD
        would make the resume find DEAD and the work
        would silently die (kill-path (a) in the
        council brief).
        """
        from daemon.services.job_feedback_observer import (
            JobFeedbackObserver,  # noqa: F401
        )

        _insert_instance(
            engine,
            "inst-f-paused",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-paused",
            instance_id="inst-f-paused",
            project_id="test-project",
            queue_id="queue-f-paused",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f-paused",
            instance_id="inst-f-paused",
            status=TaskStatus.PAUSED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NOT bare DEAD (no f1 correction).
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-paused"
        ]
        assert not f1_corrected, (
            f"PAUSED Task must NOT be matched by f1 — "
            f"bare DEAD would make resume find DEAD and "
            f"silently kill the work (kill-path (a)). "
            f"Got: {f1_corrected}"
        )
        # Assert — NOT f2 either (PAUSED is not COMPLETED).
        f2_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f-paused"
        ]
        assert not f2_corrected, (
            f"PAUSED Task must NOT be matched by f2. "
            f"Got: {f2_corrected}"
        )
        # Assert — NOT routed through terminal-boundary
        # (FAILED/CANCELLED routing). The boundary
        # exists for FAILED/CANCELLED only.
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-f-paused"
        ]
        assert not terminal_routed, (
            f"PAUSED Task must NOT be routed through "
            f"the terminal-boundary. Got: {terminal_routed}"
        )
        # Assert — paused skip detail recorded.
        paused_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_paused"
            and d.get("job_id") == "job-f-paused"
        ]
        assert paused_records, (
            f"PAUSED Task must produce an "
            f"orphan_active_skipped_paused detail. "
            f"Got details: {stats['details']}"
        )
        assert paused_records[0].get("task_id") == task_id, (
            f"Detail must carry the Task id. "
            f"Got: {paused_records[0]}"
        )
        # Assert — JobItem is still active (NOT DEAD).
        job_after = repository.get("job-f-paused")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"PAUSED Task must keep the JobItem active "
            f"(resume must be able to re-mint a Task on "
            f"the same JobItem). "
            f"Got admission_state={job_after.admission_state!r}"
        )

    @pytest.mark.asyncio
    async def test_failed_task_is_not_dead_lettered(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """FAILED Task + alive instance + past grace →
        NOT bare DEAD. The reconciler routes through
        the ``_fail_orphaned_job``-style boundary
        (lock release + failed_at + terminal_reason +
        notify_watchers), producing
        ``orphan_active_failed_terminal``. Bare DEAD
        would foreclose atomic_retry (kill-path (c)
        in the council brief — the observer's
        ``failed_at`` marker is the gate
        ``repository.py:2230`` requires).
        """
        from unittest.mock import AsyncMock

        _insert_instance(
            engine,
            "inst-f-failed",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-failed",
            instance_id="inst-f-failed",
            project_id="test-project",
            queue_id="queue-f-failed",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f-failed",
            instance_id="inst-f-failed",
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f-failed",
            job_id="job-f-failed",
            instance_id="inst-f-failed",
        )

        # Wire the boundary mock so the preferred
        # path (via ``_finalize_terminal``) succeeds
        # AND actually persists the transition (the
        # mock is a side_effect that performs the
        # real atomic_transition — without this the
        # JobItem would stay ACTIVE because the mock
        # is not a real boundary).
        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NOT bare DEAD (no f1 correction).
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-failed"
        ]
        assert not f1_corrected, (
            f"FAILED Task must NOT be matched by f1 — "
            f"bare DEAD forecloses atomic_retry. "
            f"Got: {f1_corrected}"
        )
        # Assert — routed through terminal boundary.
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-f-failed"
        ]
        assert terminal_routed, (
            f"FAILED Task must produce an "
            f"orphan_active_failed_terminal detail "
            f"via the _fail_orphaned_job-style boundary. "
            f"Got details: {stats['details']}"
        )
        assert terminal_routed[0].get("task_id") == task_id, (
            f"Detail must carry the Task id. "
            f"Got: {terminal_routed[0]}"
        )
        # Assert — JobItem is DONE (NOT DEAD — atomic_retry
        # must remain viable).
        job_after = repository.get("job-f-failed")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"FAILED Task must finalize the JobItem to "
            f"admission_state='done' (NOT DEAD — atomic_retry "
            f"must remain viable). "
            f"Got admission_state={job_after.admission_state!r}"
        )

    @pytest.mark.asyncio
    async def test_cancelled_task_is_not_dead_lettered(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """CANCELLED Task + alive instance + past grace →
        NOT bare DEAD. The reconciler routes through
        the terminal-boundary with
        ``target_status='cancelled'``. Bare DEAD
        would foreclose atomic_retry (kill-path (c)).
        """
        from unittest.mock import AsyncMock
        from daemon.repositories.job_queue.models import Decision

        _insert_instance(
            engine,
            "inst-f-cancelled",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-cancelled",
            instance_id="inst-f-cancelled",
            project_id="test-project",
            queue_id="queue-f-cancelled",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f-cancelled",
            instance_id="inst-f-cancelled",
            status=TaskStatus.CANCELLED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f-cancelled",
            job_id="job-f-cancelled",
            instance_id="inst-f-cancelled",
        )

        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NOT bare DEAD.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-cancelled"
        ]
        assert not f1_corrected, (
            f"CANCELLED Task must NOT be matched by f1 — "
            f"bare DEAD forecloses atomic_retry. "
            f"Got: {f1_corrected}"
        )
        # Assert — routed through terminal boundary.
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-f-cancelled"
        ]
        assert terminal_routed, (
            f"CANCELLED Task must produce an "
            f"orphan_active_failed_terminal detail "
            f"via the _fail_orphaned_job-style boundary. "
            f"Got details: {stats['details']}"
        )
        # Assert — the boundary was called with
        # ``target_status='cancelled'`` so
        # ``terminal_reason='cancelled'`` is preserved.
        last_call = job_queue_service_mock._finalize_terminal.await_args
        assert last_call is not None
        kwargs = last_call.kwargs
        assert kwargs.get("target_status") == "cancelled", (
            f"terminal-boundary must be called with "
            f"target_status='cancelled' so "
            f"terminal_reason='cancelled' is preserved. "
            f"Got: {kwargs}"
        )
        # Assert — the boundary was called with
        # ``decision=NO_RETRY`` (recovery path
        # never retries — the instance is gone/terminal).
        assert kwargs.get("decision") == Decision.NO_RETRY, (
            f"terminal-boundary must be called with "
            f"decision=NO_RETRY. Got: {kwargs}"
        )
        # Assert — JobItem is DONE (NOT DEAD).
        job_after = repository.get("job-f-cancelled")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"CANCELLED Task must finalize the JobItem to "
            f"admission_state='done'. "
            f"Got admission_state={job_after.admission_state!r}"
        )

    @pytest.mark.asyncio
    async def test_cancelled_with_live_retry_child_skips_finalize_retry_child_live(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Council W1 fix reconciliation — CANCELLED parent
        Task + alive instance + past grace + a live
        retry-child Task on the same instance
        (instance_id lineage key; retry child carries a
        FRESH ``work_id`` because ``schedule_retry`` /
        ``force_cancel_and_schedule_retry`` mint a new
        UUID to avoid the parent's UNIQUE) → the W1
        lineage-conjunct gate fires BEFORE the
        terminal-routing boundary. The parent JobItem is
        SKIPPED (not bare-DEAD, not terminal-routed); the
        boundary is NEVER invoked; the live retry child
        continues normally; the JobItem stays ACTIVE so
        the next 60s sweep can re-evaluate once the retry
        lineage quiesces.

        The test mirrors the AC1 expectation shape from
        the authoritative W1 reference test
        ``tests/job_queue/test_w1_retry_child_lineage_conjunct.py``:

        1. The parent is NOT bare-DEAD — no
           ``orphan_active_no_task_dead`` detail.
        2. The parent is NOT terminal-routed — no
           ``orphan_active_failed_terminal`` detail
           (the W1 gate short-circuits before the
           boundary).
        3. ``_finalize_terminal.await_count == 0`` —
           the boundary is NEVER called (proves the
           gate fired before the boundary, not after).
        4. ``orphan_active_skipped_retry_child_live``
           detail IS recorded, carrying the parent
           ``task_id`` and the instance_id (the
           lineage key).
        5. The parent JobItem's ``admission_state``
           stays ``'active'`` (left for the next 60s
           sweep to re-evaluate).
        6. The live retry-child Task remains
           ``PENDING`` (the reconciler only walked the
           parent JobItem; the child has a different
           ``work_id`` and is invisible to the
           strict-guard work_id check).
        """
        from unittest.mock import AsyncMock

        _insert_instance(
            engine,
            "inst-f-retry",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-retry-parent",
            instance_id="inst-f-retry",
            project_id="test-project",
            queue_id="queue-f-retry-parent",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        # Parent task CANCELLED, completed_at backdated.
        parent_task_id = _insert_task_with_status(
            engine,
            work_id="job-f-retry-parent",
            instance_id="inst-f-retry",
            status=TaskStatus.CANCELLED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # Live retry child: DIFFERENT work_id (the
        # brief is explicit — ``force_cancel_and_
        # schedule_retry`` mints a fresh child_work_id).
        # The retry child is PENDING (mid-claim by
        # the worker_pool). The W1 lineage gate keys
        # on ``instance_id`` (NOT work_id) so the
        # fresh-work_id child is still visible to the
        # gate via the parent-instance linkage.
        retry_task_id = _insert_task_with_status(
            engine,
            work_id="job-f-retry-child",
            instance_id="inst-f-retry",
            status=TaskStatus.PENDING.value,
        )

        # Wire the boundary mock so a stray
        # finalization call WOULD actually persist
        # (the W1 fix should never reach it). The
        # mock's ``await_count`` stays ``0`` on
        # success — asserting that proves the gate
        # fired before the boundary, NOT that the
        # boundary was a no-op.
        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # ── Assert: NOT bare DEAD (the council
        # critical #1 path that foreclosed
        # atomic_retry).
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-retry-parent"
        ]
        assert not f1_corrected, (
            f"CANCELLED parent with live retry child "
            f"MUST NOT be bare-DEAD. "
            f"Got: {f1_corrected}"
        )
        # ── Assert: NOT terminal-routed either (the
        # W1 fix skips the parent before the boundary
        # call — mirror of AC1).
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-f-retry-parent"
        ]
        assert not terminal_routed, (
            f"CANCELLED parent with live retry child "
            f"MUST NOT be terminal-routed (W1 fix: skip "
            f"the sweep, leave JobItem active). "
            f"Got: {terminal_routed}"
        )
        # ── Assert: the boundary was NEVER called
        # (the W1 gate short-circuits before it —
        # mirror of AC1's await_count assertion).
        assert (
            job_queue_service_mock._finalize_terminal.await_count == 0
        ), (
            f"_finalize_terminal MUST NOT be called "
            f"when a live retry child exists "
            f"(W1 gate fires first). "
            f"Got await_count="
            f"{job_queue_service_mock._finalize_terminal.await_count}"
        )
        # ── Assert: W1 skip detail recorded
        # (mirror of AC1 expectation shape).
        skip_records = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_retry_child_live"
            )
            and d.get("job_id") == "job-f-retry-parent"
        ]
        assert skip_records, (
            f"W1 fix MUST record an "
            f"orphan_active_skipped_retry_child_live "
            f"detail. Got details: {stats['details']}"
        )
        assert skip_records[0].get("task_id") == parent_task_id, (
            f"Detail MUST carry the parent Task id. "
            f"Got: {skip_records[0]}"
        )
        assert (
            skip_records[0].get("instance_id")
            == "inst-f-retry"
        ), (
            f"Detail MUST carry the instance_id "
            f"(the lineage key — not the parent "
            f"work_id, since the retry child carries "
            f"a fresh work_id). "
            f"Got: {skip_records[0]}"
        )
        # ── Assert: JobItem is still ACTIVE (the
        # W1 fix leaves it for the next 60s cycle
        # to re-evaluate once the retry lineage
        # quiesces).
        job_after = repository.get("job-f-retry-parent")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"CANCELLED parent with live retry child "
            f"MUST stay ACTIVE (W1 fix: skip the "
            f"sweep). "
            f"Got admission_state="
            f"{job_after.admission_state!r}"
        )
        # ── Assert: live retry child is still
        # PENDING (the reconciler only walked the
        # parent JobItem; the child has a fresh
        # work_id and is invisible to the parent's
        # strict-guard work_id check).
        with engine.begin() as conn:
            retry_status_row = conn.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": retry_task_id},
            ).first()
        assert retry_status_row is not None
        assert retry_status_row[0] == TaskStatus.PENDING.value, (
            f"live retry child Task MUST remain "
            f"PENDING (reconciler only walked the "
            f"parent JobItem). "
            f"Got status={retry_status_row[0]!r}"
        )
        # Sanity — the parent_task_id was CANCELLED.
        assert parent_task_id is not None

    @pytest.mark.asyncio
    async def test_failed_task_terminal_route_releases_lock_via_real_boundary(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service,
    ):
        """Council W4 record-honesty: FAILED parent Task +
        alive instance + past grace → the reconciler drives
        the REAL ``JobQueueService._finalize_terminal``
        boundary (NOT a mock) which releases the per-job
        lock scoped by ``(project_id, queue_id, job_id)``
        AND stamps ``failed_at`` on the JobItem so
        ``atomic_retry`` (``repository.py:2230``) can gate
        on it. Sibling locks on the same queue MUST
        SURVIVE (F4/F7 invariant).

        The C1 mock-based tests
        (``test_failed_task_is_not_dead_lettered`` and
        ``test_cancelled_task_is_not_dead_lettered``) only
        verify the boundary is CALLED — the mock's
        ``side_effect`` performs an
        ``atomic_transition`` but bypasses BOTH the
        lock-release ``finally`` block AND the
        ``finalize_active_to_done`` ``failed_at`` stamp.
        Lock release and ``failed_at`` inheritance are
        code-read only — never executed by the mock path.
        This test wires a REAL ``JobQueueService`` (the
        ``job_queue_service`` fixture) so the boundary's
        side-effects actually land, and asserts the lock
        row is GONE, ``terminal_reason='failed'`` is
        preserved, and ``failed_at`` is STAMPED on the
        JobItem after the reconcile.

        Test pins:

        1. ``reconcile_drift_states`` returns the
           ``orphan_active_failed_terminal`` detail
           (terminal-routing fired via the real
           boundary).
        2. ``JobItem.admission_state == 'done'`` (NOT
           ``'dead'`` — atomic_retry preserved).
        3. ``JobItem.terminal_reason == 'failed'``
           (boundary preserved the cause via
           ``_derive_terminal_reason``).
        4. ``JobItem.failed_at`` is NOT NULL —
           ``finalize_active_to_done`` stamps
           ``failed_at`` when ``terminal_reason='failed'``
           (the marker ``repository.py:2230`` requires
           for atomic_retry).
        5. The per-job ``job_locks`` row is GONE — the
           boundary's ``finally`` block called
           ``release_queue_lock`` → ``lock_repo.release_by_job``.
        6. The sibling ``job_locks`` row SURVIVES
           (F4/F7 scoped-release invariant).
        """
        # Seed — alive instance, ACTIVE JobItem
        # backdated past grace, FAILED Task (Task
        # reached terminal but the JobItem side never
        # transitioned — the orphan signature).
        _insert_instance(
            engine,
            "inst-f-failed-real",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-failed-real",
            instance_id="inst-f-failed-real",
            project_id="test-project",
            queue_id="queue-f-failed-real",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f-failed-real",
            instance_id="inst-f-failed-real",
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # The per-job lock — must be RELEASED by the
        # real ``_finalize_terminal`` finally block
        # (mirror of the C2 ``test_f2_releases_lock_after_done``
        # lock-row assertion at :1637).
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f-failed-real",
            job_id="job-f-failed-real",
            instance_id="inst-f-failed-real",
        )
        # Sibling lock on the same queue — must
        # SURVIVE the scoped release (F4/F7
        # invariant).
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f-failed-real",
            job_id="job-f-failed-sibling",
            instance_id="inst-f-failed-sibling",
            lock_slot=1,
        )

        # Sanity — pre-reconcile, both locks are held.
        pre_locks = lock_repo.get_all_locks()
        pre_lock_ids = {lk.lock_id for lk in pre_locks}
        assert "lock-job-f-failed-real" in pre_lock_ids, (
            f"Pre-reconcile per-job lock must exist. "
            f"Found locks: {pre_lock_ids}"
        )
        assert "lock-job-f-failed-sibling" in pre_lock_ids, (
            f"Pre-reconcile sibling lock must exist. "
            f"Found locks: {pre_lock_ids}"
        )

        # Wire the REAL ``JobQueueService`` — the
        # ``_finalize_terminal`` boundary is NOT mocked
        # so the lock-release ``finally`` block and the
        # ``finalize_active_to_done`` ``failed_at``
        # stamp actually run.
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — terminal-route detail recorded.
        terminal = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-f-failed-real"
        ]
        assert terminal, (
            f"FAILED Task must produce an "
            f"orphan_active_failed_terminal detail "
            f"via the real _finalize_terminal "
            f"boundary. Got details: {stats['details']}"
        )
        assert terminal[0].get("task_id") == task_id, (
            f"Detail must carry the Task id. "
            f"Got: {terminal[0]}"
        )

        # Assert — JobItem is DONE (NOT DEAD).
        job_after = repository.get("job-f-failed-real")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"FAILED Task must finalize the JobItem to "
            f"admission_state='done' (NOT DEAD — "
            f"atomic_retry must remain viable). "
            f"Got admission_state={job_after.admission_state!r}"
        )
        assert job_after.terminal_reason == "failed", (
            f"Real _finalize_terminal MUST preserve "
            f"terminal_reason='failed' on the JobItem "
            f"via finalize_active_to_done — atomic_retry "
            f"gates on this discriminator. "
            f"Got terminal_reason={job_after.terminal_reason!r}"
        )

        # Assert — failed_at STAMPED on JobItem
        # (failed_at inheritance — the marker
        # ``repository.py:2230`` requires for
        # atomic_retry). This is the critical W4
        # assertion: the mocked boundary never
        # executes ``finalize_active_to_done``, so
        # this stamp only lands when the REAL
        # boundary runs.
        assert job_after.failed_at is not None, (
            f"failed_at MUST be stamped on the JobItem "
            f"by finalize_active_to_done when "
            f"terminal_reason='failed' — this is the "
            f"gate atomic_retry reads (repository.py:2230). "
            f"The mocked C1 boundary bypassed this "
            f"side-effect; the real boundary writes it. "
            f"Pre-fix behaviour would silently leave "
            f"failed_at=None and break retry gating. "
            f"Got failed_at={job_after.failed_at!r}"
        )

        # Assert — per-job lock is RELEASED via the
        # REAL boundary finally block (the lock
        # release is INSIDE the real
        # ``_finalize_terminal``; mocks bypass it; this
        # test exercises the real path so the lock row
        # is actually gone).
        post_locks = lock_repo.get_all_locks()
        post_lock_ids = {lk.lock_id for lk in post_locks}
        assert "lock-job-f-failed-real" not in post_lock_ids, (
            f"Real _finalize_terminal MUST release the "
            f"per-job lock scoped by "
            f"(project_id, queue_id, job_id) in its "
            f"finally block — the mocked boundary "
            f"bypassed this side-effect; the real "
            f"boundary writes it. Pre-fix behaviour "
            f"would leak the lock and wedge c=1 queues. "
            f"Found locks: {post_lock_ids}"
        )

        # Assert — sibling lock SURVIVES (F4/F7
        # invariant: scoped release must not delete
        # sibling locks on the same queue).
        assert "lock-job-f-failed-sibling" in post_lock_ids, (
            f"F4/F7 invariant: sibling lock on the "
            f"same queue must NOT be released by the "
            f"scoped release_queue_lock. "
            f"Found locks: {post_lock_ids}"
        )


class TestPatternFCouncilCritical2LockRelease:
    """Council REJECT 2026-08-29 Critical #2 — f2 path
    leaks the per-job lock on c=1 queues. Pre-fix
    had no ``release_by_job`` between the transition
    and ``notify_watchers``; every f2 firing on a
    defer / background queue wedged that queue until
    restart. Mirror the sibling-survival shape from
    the existing f1 test (test_orphan_active_job_recovery.py:
    301-430): sibling lock on the same queue SURVIVES
    the f2 release.
    """

    @pytest.mark.asyncio
    async def test_f2_releases_lock_after_done(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Active JobItem + COMPLETED Task (gate clears)
        → the reconciler finalizes to DONE AND releases
        the per-job lock scoped by
        ``(project_id, queue_id, job_id)``. Sibling
        locks on the same queue MUST SURVIVE
        (F4/F7 invariant).

        The pre-fix code transitioned active→done
        WITHOUT ``release_by_job`` — every f2 firing
        on a c=1 queue wedged that queue. The fix
        inserts ``release_by_job`` between the
        transition and ``notify_watchers`` (mirrors
        f1's lock-release-first ordering).
        """
        from unittest.mock import patch

        _insert_instance(
            engine,
            "inst-f2-lock",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f2-lock",
            instance_id="inst-f2-lock",
            project_id="test-project",
            queue_id="queue-f2-lock",
            admission_state=AdmissionState.ACTIVE.value,
        )
        _insert_task_with_status(
            engine,
            work_id="job-f2-lock",
            instance_id="inst-f2-lock",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # The per-job lock — must be RELEASED by f2.
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f2-lock",
            job_id="job-f2-lock",
            instance_id="inst-f2-lock",
        )
        # Sibling lock on the same queue — must SURVIVE
        # the f2 release (F4/F7 invariant).
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-f2-lock",
            job_id="job-f2-sibling",
            instance_id="inst-f2-sibling",
            lock_slot=1,
        )

        # Sanity — pre-reconcile, both locks are held.
        pre_locks = lock_repo.get_all_locks()
        pre_lock_ids = {lk.lock_id for lk in pre_locks}
        assert "lock-job-f2-lock" in pre_lock_ids
        assert "lock-job-f2-sibling" in pre_lock_ids

        # Empty-pending bus stub (f2 gate).
        class _BusStub:
            async def pending_watchers(self, source_task_id):
                return []

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_BusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # Assert — f2 correction recorded.
        f2_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f2-lock"
        ]
        assert f2_records, (
            f"f2 must record an "
            f"orphan_active_completed_task_done detail. "
            f"Got details: {stats['details']}"
        )

        # Assert — the per-job lock is RELEASED.
        post_locks = lock_repo.get_all_locks()
        post_lock_ids = {lk.lock_id for lk in post_locks}
        assert "lock-job-f2-lock" not in post_lock_ids, (
            f"Critical #2 fix: f2 MUST release the "
            f"per-job lock scoped by "
            f"(project_id, queue_id, job_id) — pre-fix "
            f"leaked the lock and wedged c=1 queues "
            f"(defer / background queues!) until restart. "
            f"Found locks: {post_lock_ids}"
        )

        # Assert — sibling lock SURVIVES (F4/F7 invariant).
        assert "lock-job-f2-sibling" in post_lock_ids, (
            f"F4/F7 invariant: sibling lock must NOT "
            f"be released. Found locks: {post_lock_ids}"
        )

        # Assert — JobItem is DONE.
        job_after = repository.get("job-f2-lock")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value

        # Assert — notify_watchers fired (the f2 path's
        # terminal step).
        job_queue_service_mock.notify_watchers.assert_awaited()


class TestPatternFCouncilCritical3F2Gate:
    """Council REJECT 2026-08-29 Critical #3 — f2 gate:
    bus_pending==0, no PENDING instance tasks, 60s age
    floor on ``task.completed_at``. FAIL-SAFE on bus
    unavailable (never guess).
    """

    @pytest.mark.asyncio
    async def test_f2_does_not_finalize_while_bus_pending(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Active JobItem + COMPLETED Task + bus_pending>0
        → f2 MUST NOT finalize (Mechanism B: a
        waiting_children parent's observer gate is
        still holding the JobItem open). Detail
        pattern: ``orphan_active_skipped_bus_pending``.
        """
        from unittest.mock import patch

        _insert_instance(
            engine,
            "inst-f2-bus-pending",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f2-bus-pending",
            instance_id="inst-f2-bus-pending",
            project_id="test-project",
            queue_id="queue-f2-bus-pending",
            admission_state=AdmissionState.ACTIVE.value,
        )
        _insert_task_with_status(
            engine,
            work_id="job-f2-bus-pending",
            instance_id="inst-f2-bus-pending",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        # Bus stub reports a pending watcher (the
        # waiting_children parent's gate).
        class _BusStub:
            async def pending_watchers(self, source_task_id):
                return ["watcher-1"]

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_BusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # Assert — NOT finalized (no f2 detail).
        f2_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f2-bus-pending"
        ]
        assert not f2_corrected, (
            f"bus_pending>0 MUST defer f2 finalize "
            f"(Mechanism B). Got: {f2_corrected}"
        )
        # Assert — bus-pending skip detail recorded.
        bus_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_bus_pending"
            and d.get("job_id") == "job-f2-bus-pending"
        ]
        assert bus_records, (
            f"bus_pending>0 must produce an "
            f"orphan_active_skipped_bus_pending detail. "
            f"Got details: {stats['details']}"
        )
        # Assert — JobItem is still ACTIVE.
        job_after = repository.get("job-f2-bus-pending")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"bus_pending>0 MUST leave the JobItem "
            f"active (next cycle retries once bus "
            f"drains). Got admission_state="
            f"{job_after.admission_state!r}"
        )

    @pytest.mark.asyncio
    async def test_f2_does_not_finalize_with_pending_instance_tasks(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Active JobItem + COMPLETED Task + bus
        pending==0 BUT instance has a PENDING Task
        row → f2 MUST NOT finalize (the instance is
        still processing claimable work). Detail
        pattern:
        ``orphan_active_skipped_pending_instance_tasks``.
        """
        from unittest.mock import patch

        _insert_instance(
            engine,
            "inst-f2-instance-pending",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f2-instance-pending",
            instance_id="inst-f2-instance-pending",
            project_id="test-project",
            queue_id="queue-f2-instance-pending",
            admission_state=AdmissionState.ACTIVE.value,
        )
        # The driving task is COMPLETED.
        _insert_task_with_status(
            engine,
            work_id="job-f2-instance-pending",
            instance_id="inst-f2-instance-pending",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # A SECOND Task on the same instance —
        # PENDING (claimable, the observer gate has
        # NOT cleared). Different work_id from the
        # driving task.
        _insert_task_with_status(
            engine,
            work_id="job-f2-instance-pending-other",
            instance_id="inst-f2-instance-pending",
            status=TaskStatus.PENDING.value,
        )

        class _BusStub:
            async def pending_watchers(self, source_task_id):
                return []

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_BusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # Assert — NOT finalized.
        f2_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f2-instance-pending"
        ]
        assert not f2_corrected, (
            f"instance with PENDING Task MUST defer "
            f"f2 finalize. Got: {f2_corrected}"
        )
        # Assert — pending-instance-tasks skip detail.
        skip_records = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_pending_instance_tasks"
            )
            and d.get("job_id") == "job-f2-instance-pending"
        ]
        assert skip_records, (
            f"instance with PENDING Task must produce "
            f"an "
            f"orphan_active_skipped_pending_instance_tasks "
            f"detail. Got details: {stats['details']}"
        )
        # Assert — JobItem is still ACTIVE.
        job_after = repository.get("job-f2-instance-pending")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_f2_requires_completed_at_age_floor(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Active JobItem + COMPLETED Task with
        ``completed_at`` JUST NOW (inside the 60s
        floor) → f2 MUST NOT finalize (Mechanism A
        residual — the observer's ``failed_at`` stamp
        must land before we foreclose retry). Detail
        pattern: ``orphan_active_skipped_age_floor``.
        """
        from unittest.mock import patch

        _insert_instance(
            engine,
            "inst-f2-age-floor",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f2-age-floor",
            instance_id="inst-f2-age-floor",
            project_id="test-project",
            queue_id="queue-f2-age-floor",
            admission_state=AdmissionState.ACTIVE.value,
        )
        # completed_at = JUST NOW (10s ago, inside
        # the 60s floor).
        _insert_task_with_status(
            engine,
            work_id="job-f2-age-floor",
            instance_id="inst-f2-age-floor",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )

        class _BusStub:
            async def pending_watchers(self, source_task_id):
                return []

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_BusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # Assert — NOT finalized.
        f2_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f2-age-floor"
        ]
        assert not f2_corrected, (
            f"completed_at inside the 60s floor MUST "
            f"defer f2 finalize (Mechanism A residual). "
            f"Got: {f2_corrected}"
        )
        # Assert — age-floor skip detail.
        age_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_age_floor"
            and d.get("job_id") == "job-f2-age-floor"
        ]
        assert age_records, (
            f"completed_at inside the 60s floor must "
            f"produce an "
            f"orphan_active_skipped_age_floor detail. "
            f"Got details: {stats['details']}"
        )
        # Assert — JobItem is still ACTIVE.
        job_after = repository.get("job-f2-age-floor")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value

        # ALSO: verify the inverse — completed_at
        # PAST the floor MATCHES. (The brief asks for
        # both shapes in the same test; "just-completed
        # → skip; past floor → match".)
        _insert_instance(
            engine,
            "inst-f2-age-past",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f2-age-past",
            instance_id="inst-f2-age-past",
            project_id="test-project",
            queue_id="queue-f2-age-past",
            admission_state=AdmissionState.ACTIVE.value,
        )
        _insert_task_with_status(
            engine,
            work_id="job-f2-age-past",
            instance_id="inst-f2-age-past",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_BusStub(),
        ):
            stats2 = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        f2_past_records = [
            d for d in stats2["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f2-age-past"
        ]
        assert f2_past_records, (
            f"completed_at PAST the 60s floor MUST "
            f"finalize f2. Got details: {stats2['details']}"
        )

    @pytest.mark.asyncio
    async def test_f2_skips_fail_safe_when_bus_unavailable(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Bus singleton NOT wired (returns ``None``
        from ``get_dependency_bus()``) → f2 MUST
        skip with FAIL-SAFE behavior
        (``orphan_active_skipped_bus_unavailable``).
        The JobItem is left ACTIVE; the next 60s
        cycle retries once the bus is wired. NEVER
        GUESS.
        """
        from unittest.mock import patch

        _insert_instance(
            engine,
            "inst-f2-bus-unavail",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f2-bus-unavail",
            instance_id="inst-f2-bus-unavail",
            project_id="test-project",
            queue_id="queue-f2-bus-unavail",
            admission_state=AdmissionState.ACTIVE.value,
        )
        _insert_task_with_status(
            engine,
            work_id="job-f2-bus-unavail",
            instance_id="inst-f2-bus-unavail",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Bus NOT wired — get_dependency_bus returns None.
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=None,
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # Assert — NOT finalized (FAIL-SAFE).
        f2_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-f2-bus-unavail"
        ]
        assert not f2_corrected, (
            f"bus unavailable MUST NOT finalize f2 "
            f"(FAIL-SAFE — never guess). Got: {f2_corrected}"
        )
        # Assert — bus-unavailable skip detail recorded.
        unavail_records = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_bus_unavailable"
            )
            and d.get("job_id") == "job-f2-bus-unavail"
        ]
        assert unavail_records, (
            f"bus unavailable must produce an "
            f"orphan_active_skipped_bus_unavailable "
            f"detail (FAIL-SAFE contract). "
            f"Got details: {stats['details']}"
        )
        # Assert — JobItem is still ACTIVE (left for
        # next cycle to retry once the bus is wired).
        job_after = repository.get("job-f2-bus-unavail")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"bus unavailable MUST leave the JobItem "
            f"active (next 60s cycle retries). "
            f"Got admission_state={job_after.admission_state!r}"
        )


class TestPatternFW1MidMintGuard:
    """Council REJECT 2026-08-29 W1 — mid-mint window.
    Queue-aged defer jobs can sit past
    ``created_at``-grace at dispatch; the
    spawn→Task-mint window is unguarded. The fix
    adds a conjunct: ``instance.created_at < threshold``
    (same grace threshold as the JobItem side) so a
    just-spawned instance whose Task mint is in
    flight never matches.
    """

    @pytest.mark.asyncio
    async def test_recent_instance_mid_mint_is_skipped(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """A JobItem is 1800s old (past the 60s grace)
        but the INSTANCE was created 30s ago (inside
        the grace) → the W1 mid-mint guard MUST skip
        the f1 path. Task mint is likely in flight;
        matching the f1 path on a fresh instance
        would lose the live work (the Task is being
        minted RIGHT NOW).
        """
        _insert_instance(
            engine,
            "inst-f-w1",
            project_id="test-project",
            # 30s ago — INSIDE the 60s grace.
            created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        # JobItem created 1800s ago — past the grace.
        _insert_job_item(
            engine,
            job_id="job-f-w1",
            instance_id="inst-f-w1",
            project_id="test-project",
            queue_id="queue-f-w1",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Assert — NOT matched by f1 (W1 mid-mint guard).
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-w1"
        ]
        assert not f1_corrected, (
            f"W1 mid-mint guard: a just-spawned "
            f"instance (instance.created_at inside "
            f"the grace) MUST NOT match f1 even when "
            f"the JobItem is past the grace — Task "
            f"mint is likely in flight; matching "
            f"would lose the live work. "
            f"Got: {f1_corrected}"
        )
        # Assert — grace detail recorded (operator
        # visibility — the row was observed).
        grace_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_grace"
            and d.get("job_id") == "job-f-w1"
        ]
        assert grace_records, (
            f"W1 mid-mint skip must produce an "
            f"orphan_active_skipped_grace detail. "
            f"Got details: {stats['details']}"
        )
        # The reason must reference the W1 mid-mint
        # guard so operators can distinguish it from
        # the JobItem-side grace.
        reason = grace_records[0].get("reason", "")
        assert "W1 mid-mint" in reason or "instance is within" in reason, (
            f"W1 grace detail must reference the "
            f"mid-mint guard or instance-side grace. "
            f"Got reason: {reason!r}"
        )
        # Assert — JobItem is still ACTIVE.
        job_after = repository.get("job-f-w1")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value


# ─────────────────────────────────────────────────────────────────────────────
# Council REJECT 2026-08-29 W1 residual — paired fix ~3 LOC
# (a) PAUSED retry child on the same instance must be detected by the
#     W1 lineage gate (helper was on ``has_inflight_task`` = PENDING+RUNNING
#     only — a PAUSED retry child slipped through and was over-finalized).
# (b) A transient lookup error on the busy/inflight query must FAIL-SAFE
#     to SKIP (the helper returned False → proceed-to-finalize, while the
#     sister bus-gate returns SKIP — inconsistency that can over-finalize
#     a live retry child during a brief DB error).
#
# Both residuals live in one helper:
# ``daemon/services/job_recovery_service.py`` ::
# ``_pattern_f_instance_has_inflight_task`` (Pattern (f) W1 gate,
# call site at the FAILED/CANCELLED terminal-routing branch).
#
# Fix is ~3 LOC: swap to ``TaskRepository.has_instance_busy``
# (PENDING + RUNNING + PAUSED) and flip the ``except Exception`` fail-safe
# to ``return True`` (SKIP, matching the sister bus-gate).
# ─────────────────────────────────────────────────────────────────────────────


class TestPatternFResidualsPairedFix:
    """W1 residuals (paired fix): (a) PAUSED retry-child gap,
    (b) lineage-lookup fail-safe direction.

    Both tests FAIL on ``c6c9dfac`` (pre-fix helper uses
    ``has_inflight_task`` = PENDING+RUNNING only, plus
    ``return False`` fail-safe). Both tests PASS after the
    ~3-LOC paired fix that swaps to ``has_instance_busy`` and
    flips the ``except`` return to ``True``.

    Reference:
    - Gate spec §7 (residual adjudication): two 🟠
      non-blocking residuals on the W1 lineage gate.
    - Sister helper ``_pattern_f_check_bus_pending`` (the
      fail-safe reference, returns ``True`` SKIP on lookup
      error — `:3138-3153`).
    """

    @pytest.mark.asyncio
    async def test_failed_parent_with_paused_retry_child_is_skipped(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Residual (a) — PAUSED retry-child gap.

        Scenario:
            - FAILED parent Task (terminal, completed_at backdated).
            - PAUSED retry-child Task on the SAME instance, FRESH
              ``work_id`` (``force_cancel_and_schedule_retry`` →
              ``pause_instance_cascade`` sequence; the worker
              claimed the retry, then the operator paused the
              instance — narrow but documented window, see
              ``instance_lifecycle.py:4172-4178``).
            - Live retry-child is PAUSED, not PENDING/RUNNING.

        Expected (post-fix):
            - The W1 lineage gate fires (helper uses
              ``has_instance_busy`` = PENDING+RUNNING+PAUSED).
            - Parent JobItem is SKIPPED (stays ACTIVE).
            - ``_finalize_terminal.await_count == 0`` (boundary
              is never reached).
            - ``orphan_active_skipped_retry_child_live`` detail
              is recorded.

        Pre-fix (c6c9dfac): helper uses ``has_inflight_task``
        = PENDING+RUNNING only → the PAUSED retry child is
        invisible → the gate misses → parent JobItem is
        terminal-routed → boundary called → JobItem flipped
        to DONE while the live retry-child is still alive
        on the instance (over-finalization regression).
        """
        from unittest.mock import AsyncMock

        _insert_instance(
            engine,
            "inst-f-resid-paused",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-resid-paused-parent",
            instance_id="inst-f-resid-paused",
            project_id="test-project",
            queue_id="queue-f-resid-paused-parent",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        # Parent Task FAILED — completed_at backdated past
        # the 60s floor so the floor is NOT what saves us.
        parent_task_id = _insert_task_with_status(
            engine,
            work_id="job-f-resid-paused-parent",
            instance_id="inst-f-resid-paused",
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # Retry child: PAUSED (NOT pending/running — the
        # narrow window the pre-fix helper missed). FRESH
        # ``work_id`` (the brief is explicit — schedule_retry
        # mints a new UUID to avoid the parent's UNIQUE).
        # Same ``instance_id`` (the lineage key). ``paused``
        # is the active retry's mid-state after
        # ``pause_instance_cascade`` flipped RUNNING→PAUSED.
        retry_task_id = _insert_task_with_status(
            engine,
            work_id="job-f-resid-paused-child",
            instance_id="inst-f-resid-paused",
            status=TaskStatus.PAUSED.value,
        )

        # Wire the boundary mock so a stray finalization
        # call WOULD actually persist (the W1 fix should
        # never reach it). Mirrors the W1 AC1 convention.
        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # ── Assert: NOT bare DEAD.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-resid-paused-parent"
        ]
        assert not f1_corrected, (
            f"FAILED parent with PAUSED retry child MUST "
            f"NOT be bare-DEAD. Got: {f1_corrected}"
        )
        # ── Assert: NOT terminal-routed (the W1 fix
        # skips the parent before the boundary call).
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-f-resid-paused-parent"
        ]
        assert not terminal_routed, (
            f"FAILED parent with PAUSED retry child MUST "
            f"NOT be terminal-routed (residual (a) fix: "
            f"the W1 lineage gate must include PAUSED "
            f"children). Got: {terminal_routed}"
        )
        # ── Assert: the boundary was NEVER called
        # (the W1 gate short-circuits before it).
        assert (
            job_queue_service_mock._finalize_terminal.await_count == 0
        ), (
            f"_finalize_terminal MUST NOT be called when "
            f"a PAUSED retry child exists (W1 gate). "
            f"Got await_count="
            f"{job_queue_service_mock._finalize_terminal.await_count}"
        )
        # ── Assert: W1 skip detail recorded.
        skip_records = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_retry_child_live"
            )
            and d.get("job_id") == "job-f-resid-paused-parent"
        ]
        assert skip_records, (
            f"W1 residual (a) fix MUST record an "
            f"orphan_active_skipped_retry_child_live "
            f"detail (PAUSED retry child lineage gate). "
            f"Got details: {stats['details']}"
        )
        assert skip_records[0].get("task_id") == parent_task_id, (
            f"Detail MUST carry the parent Task id. "
            f"Got: {skip_records[0]}"
        )
        assert (
            skip_records[0].get("instance_id")
            == "inst-f-resid-paused"
        ), (
            f"Detail MUST carry the instance_id. "
            f"Got: {skip_records[0]}"
        )
        # ── Assert: JobItem is still ACTIVE
        # (left for the next 60s cycle to re-evaluate).
        job_after = repository.get("job-f-resid-paused-parent")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"FAILED parent with PAUSED retry child MUST "
            f"stay ACTIVE (residual (a) fix). "
            f"Got admission_state="
            f"{job_after.admission_state!r}"
        )
        # ── Assert: live retry child is still PAUSED
        # (the reconciler only walked the parent JobItem;
        # the retry-child Task is on a different work_id
        # and was inspected via the lineage query, not
        # mutated).
        with engine.begin() as conn:
            retry_status_row = conn.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": retry_task_id},
            ).first()
        assert retry_status_row is not None
        assert retry_status_row[0] == TaskStatus.PAUSED.value, (
            f"Live retry child Task MUST remain PAUSED "
            f"(reconciler only walked the parent). "
            f"Got status={retry_status_row[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_lineage_lookup_error_fails_safe_to_skip(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
        monkeypatch,
    ):
        """Residual (b) — lineage-lookup fail-safe direction.

        Scenario:
            - FAILED parent Task (terminal, completed_at backdated).
            - Lineage is QUIESCENT (no retry child — AC2-shaped).
            - ``TaskRepository.has_instance_busy`` (the post-fix
              helper symbol) raises a transient DB error.

        Expected (post-fix):
            - The helper's ``except Exception`` branch returns
              ``True`` (SKIP), consistent with the sister bus-gate
              ``_pattern_f_check_bus_pending``: "FAIL-SAFE:
              skip finalize, leave JobItem active; next 60s
              cycle retries. Never guess." (`:3151-3153`)
            - Parent JobItem is SKIPPED (stays ACTIVE).
            - ``_finalize_terminal.await_count == 0``
              (boundary is never reached).
            - WARNING is logged (fail-safe observability).

        Pre-fix (c6c9dfac): helper uses ``has_inflight_task`` so
        the patch on ``has_instance_busy`` is a no-op; helper
        returns False (no PENDING/RUNNING); proceed to
        terminal-routing → JobItem flipped to DONE despite the
        transient error (over-finalization regression).
        """
        from unittest.mock import AsyncMock

        _insert_instance(
            engine,
            "inst-f-resid-failsafe",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-f-resid-failsafe-parent",
            instance_id="inst-f-resid-failsafe",
            project_id="test-project",
            queue_id="queue-f-resid-failsafe-parent",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        # Parent Task FAILED — completed_at backdated past
        # the 60s floor. QUIESCENT lineage (no retry child).
        _insert_task_with_status(
            engine,
            work_id="job-f-resid-failsafe-parent",
            instance_id="inst-f-resid-failsafe",
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        # Simulate a transient DB error on the busy/inflight
        # query. Patch the POST-FIX symbol
        # (``has_instance_busy``) so the test is fail-safe
        # forward: when the helper is fixed to call
        # ``has_instance_busy`` instead of
        # ``has_inflight_task``, the patch bites.
        def _raise_busy(*args, **kwargs):
            raise RuntimeError(
                "simulated transient DB error on "
                "has_instance_busy lineage query"
            )

        monkeypatch.setattr(
            task_repository,
            "has_instance_busy",
            _raise_busy,
        )

        # Wire the boundary mock so a stray finalization
        # call WOULD actually persist (the fail-safe path
        # must skip BEFORE the boundary is reached).
        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # ── Assert: NOT bare DEAD.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f-resid-failsafe-parent"
        ]
        assert not f1_corrected, (
            f"FAILED parent with transient lookup error "
            f"MUST NOT be bare-DEAD. Got: {f1_corrected}"
        )
        # ── Assert: NOT terminal-routed (the fail-safe
        # path returns True → SKIP, not False → finalize).
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-f-resid-failsafe-parent"
        ]
        assert not terminal_routed, (
            f"FAILED parent with transient lookup error "
            f"MUST NOT be terminal-routed (residual (b) "
            f"fix: FAIL-SAFE skip, matching the sister "
            f"bus-gate). Got: {terminal_routed}"
        )
        # ── Assert: the boundary was NEVER called
        # (the FAIL-SAFE path short-circuits before it).
        assert (
            job_queue_service_mock._finalize_terminal.await_count == 0
        ), (
            f"_finalize_terminal MUST NOT be called when "
            f"the lineage lookup errored (FAIL-SAFE skip). "
            f"Got await_count="
            f"{job_queue_service_mock._finalize_terminal.await_count}"
        )
        # ── Assert: W1 skip detail recorded (the
        # retry-child-live detail family is the canonical
        # skip reason for the lineage-gate — observable
        # via the detail record so operators can see the
        # row was deferred).
        skip_records = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_retry_child_live"
            )
            and d.get("job_id") == "job-f-resid-failsafe-parent"
        ]
        assert skip_records, (
            f"W1 residual (b) fix MUST record an "
            f"orphan_active_skipped_retry_child_live "
            f"detail (FAIL-SAFE skip on lookup error). "
            f"Got details: {stats['details']}"
        )
        # ── Assert: JobItem is still ACTIVE.
        job_after = repository.get("job-f-resid-failsafe-parent")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"FAILED parent with transient lookup error "
            f"MUST stay ACTIVE (FAIL-SAFE skip, next 60s "
            f"cycle retries). Got admission_state="
            f"{job_after.admission_state!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# f1-misfire fix batch (incident 2026-08-31, JobItem 69a34b35, instance
# 28c6421b). An observer-triggered start_job dispatch minted the driving
# process_message Task with a FRESH work_id instead of JobItem.job_id, so
# Pattern-f1's ``get_by_work_id(job_id)`` returned None and the strict
# ``task is None`` predicate misread a LIVE subtree as a restart-orphan →
# active→DEAD while the leader waited in waiting_children and a grandchild
# ran mid-LLM.
#
# The batch adds:
#   * a subtree-alive guard (T1/T2) — f1 must aggregate the TREE, not the row
#   * durable terminal_reason='pattern_f1_orphan' (T3)
#   * an ENSEMBLE_ORPHAN_F1_ENABLED kill-switch (default ON)
#   * the observer mint-site linkage contract (work_id == job_id)
#
# Test-env convention for this batch: FILE-BACKED SQLite (tmp_path) — the
# shared ``engine`` fixture is in-memory StaticPool, which corrupts writes
# when dependency-bus repo sessions share one open transaction.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def f1_engine(tmp_path):
    """File-backed SQLite engine for the f1-misfire batch tests.

    Deliberately NOT the shared in-memory StaticPool engine: each
    session gets its own connection against a real file, so no
    shared-open-transaction write corruption is possible.
    """
    db_path = tmp_path / "f1_misfire_batch.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


class TestPatternF1SubtreeAliveGuard:
    """Point 2 — the f1 subtree-alive guard (misfire shield).

    Per-row checks provably fail: ``last_activity_at`` freezes on a
    waiting_children parent for 16min+. The guard MUST aggregate the
    TREE via ``get_tree_ids_permanent`` and skip when ANY live Task
    (any work_id) sits in the lineage OR the tree's max
    ``last_activity_at`` is within the activity window.
    """

    @pytest.mark.asyncio
    async def test_f1_incident_replay_tree_alive_skips_dead_finalize(
        self, f1_engine,
    ):
        """T1 — incident replay (RED on e863f010).

        Shape: leader in waiting_children (its last_activity_at
        FROZEN 1000s ago), grandchild RUNNING a Task mid-LLM
        (last_activity_at 5s ago), and the driving Task's work_id
        MISMATCHES the JobItem's job_id (the mint-site defect) →
        ``get_by_work_id(job_id)`` returns None.

        The guard MUST skip f1: the JobItem stays ACTIVE. On the
        pre-fix tree the strict ``task is None`` predicate
        DEAD-finalizes the live subtree — this test is RED there.
        """
        repository = JobRepository(f1_engine)
        task_repository = TaskRepository(f1_engine)
        lock_repo = LockRepository(f1_engine)
        instance_repo = SQLModelInstanceRepository(engine=f1_engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        jq_mock = MagicMock()
        jq_mock.notify_watchers = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)

        # Leader — waiting_children, activity FROZEN 1000s ago
        # (the incident's per-row trap: outside the 900s window).
        _insert_instance(
            f1_engine,
            "inst-leader-28c6",
            status="waiting_children",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=now - timedelta(seconds=1000),
        )
        # Grandchild — mid-LLM, activity FRESH (5s ago).
        _insert_instance(
            f1_engine,
            "inst-grandchild-run",
            status="running",
            created_at=now - timedelta(seconds=600),
            parent_id="inst-leader-28c6",
            last_activity_at=now - timedelta(seconds=5),
        )
        # JobItem pinned to the LEADER, past the grace.
        _insert_job_item(
            f1_engine,
            job_id="job-f1-incident",
            instance_id="inst-leader-28c6",
            project_id="test-project",
            queue_id="queue-f1-incident",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-incident",
            job_id="job-f1-incident",
            instance_id="inst-leader-28c6",
        )
        # The live grandchild Task carries a FRESH work_id (the
        # mint-site defect) — NOT the JobItem's job_id.
        _insert_task_with_status(
            f1_engine,
            work_id="fresh-uuid-NOT-the-job-id",
            instance_id="inst-grandchild-run",
            status=TaskStatus.RUNNING.value,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=jq_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # The live subtree MUST be skipped, not killed.
        job_after = repository.get("job-f1-incident")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"f1 misfire: live subtree (leader waiting_children + "
            f"grandchild RUNNING task, work_id mismatch) was "
            f"DEAD-finalized. admission_state="
            f"{job_after.admission_state!r}, details: {stats['details']}"
        )
        skip_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_tree_alive"
            and d.get("job_id") == "job-f1-incident"
        ]
        assert skip_records, (
            f"f1 must record an orphan_active_skipped_tree_alive "
            f"detail for the live subtree. Got details: "
            f"{stats['details']}"
        )

    @pytest.mark.asyncio
    async def test_f1_zombie_still_fires_with_stale_tree(
        self, f1_engine,
    ):
        """T2 — zombie preservation (802095d8 class).

        Stale-running instance, NO tasks anywhere in the tree, tree
        activity stale (outside the window) → f1 MUST still fire
        after the grace. The guard shields LIVE trees only; genuine
        restart-orphan zombies keep their recovery path.
        """
        repository = JobRepository(f1_engine)
        task_repository = TaskRepository(f1_engine)
        lock_repo = LockRepository(f1_engine)
        instance_repo = SQLModelInstanceRepository(engine=f1_engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        jq_mock = MagicMock()
        jq_mock.notify_watchers = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine,
            "inst-zombie-stale",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=now - timedelta(seconds=7200),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-f1-zombie",
            instance_id="inst-zombie-stale",
            project_id="test-project",
            queue_id="queue-f1-zombie",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-zombie",
            job_id="job-f1-zombie",
            instance_id="inst-zombie-stale",
        )
        # NO Task rows — the genuine zombie shape.

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=jq_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        job_after = repository.get("job-f1-zombie")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DEAD.value, (
            f"f1 must still fire on the zombie shape (stale instance, "
            f"no tasks, stale tree activity). Got admission_state="
            f"{job_after.admission_state!r}, details: {stats['details']}"
        )
        f1_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f1-zombie"
        ]
        assert f1_records, (
            f"f1 must record orphan_active_no_task_dead for the zombie. "
            f"Got details: {stats['details']}"
        )

    @pytest.mark.asyncio
    async def test_f1_zombie_fires_with_tz_naive_stale_tree_activity(
        self, f1_engine,
    ):
        """T3 — tz-naive tree-activity isolation (PG leg-2 round-trip).

        PostgreSQL reads ``last_activity_at`` back TIMEZONE-NAIVE
        (the watchdog subsystem deliberately computes age in SQL for
        exactly this reason — instance/repository.py:2180-2184). A
        naive MAX that parses back ``tzinfo is None`` must NOT blow
        up the leg-2 comparison (naive datetime vs aware cutoff →
        TypeError): the parsed value is tz-normalized to UTC.

        Shape: alive running instance, tree max ``last_activity_at``
        stored NAIVE and STALE (outside the window), no live tasks
        (leg 1 silent) → the genuine zombie MUST still fire AND the
        recovery pass MUST NOT abort on this row.

        RED on 04fd0c52: the naive-vs-aware TypeError skips this
        row via the per-row handler EVERY cycle — the zombie is
        never finalized and the row spams an ERROR per cycle.
        """
        repository = JobRepository(f1_engine)
        task_repository = TaskRepository(f1_engine)
        lock_repo = LockRepository(f1_engine)
        instance_repo = SQLModelInstanceRepository(engine=f1_engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        jq_mock = MagicMock()
        jq_mock.notify_watchers = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)

        # Alive running instance (created_at mirrors T2 — AWARE and
        # backdated past the W1 mid-mint window). ONLY
        # last_activity_at is NAIVE: no tz offset in the stored ISO
        # text, and STALE (7200s — far outside the leg-2 window).
        _insert_instance(
            f1_engine,
            "inst-zombie-naive-tz",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=(now - timedelta(seconds=7200)).replace(
                tzinfo=None
            ),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-f1-naive-tz",
            instance_id="inst-zombie-naive-tz",
            project_id="test-project",
            queue_id="queue-f1-naive-tz",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-naive-tz",
            job_id="job-f1-naive-tz",
            instance_id="inst-zombie-naive-tz",
        )
        # NO Task rows — leg 1 is silent; only the naive leg-2 MAX
        # is in play.

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=jq_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        details = (stats or {}).get("details", [])
        job_after = repository.get("job-f1-naive-tz")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DEAD.value, (
            f"f1 must still fire on the tz-naive stale zombie (the "
            f"naive MAX must be tz-normalized, not crash the leg-2 "
            f"comparison). Got admission_state="
            f"{job_after.admission_state!r}, details: {details}"
        )
        f1_records = [
            d for d in details
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f1-naive-tz"
        ]
        assert f1_records, (
            f"f1 must record orphan_active_no_task_dead for the "
            f"tz-naive zombie. Got details: {details}"
        )
        # The pass itself must NOT abort: reconcile returned a tally
        # (not None) and the naive-stale row never masqueraded as a
        # live tree.
        assert stats is not None, (
            f"recovery pass must not abort on the tz-naive row — "
            f"reconcile_drift_states returned None. Details: {details}"
        )
        tree_alive_records = [
            d for d in details
            if d.get("pattern") == "orphan_active_skipped_tree_alive"
            and d.get("job_id") == "job-f1-naive-tz"
        ]
        assert not tree_alive_records, (
            f"stale naive tree activity must NOT read as alive. "
            f"Got: {tree_alive_records}"
        )

    @pytest.mark.asyncio
    async def test_f1_finalize_persists_terminal_reason(
        self, f1_engine,
    ):
        """T3 — durable terminal_reason (RED on e863f010).

        ``error_message``/``completed_at`` are silently stripped by
        ``_REMOVED_JOB_COLUMNS`` — pre-fix f1 kills carry an EMPTY
        terminal_reason. The finalize must write the durable
        ``terminal_reason='pattern_f1_orphan'`` column.
        """
        repository = JobRepository(f1_engine)
        task_repository = TaskRepository(f1_engine)
        lock_repo = LockRepository(f1_engine)
        instance_repo = SQLModelInstanceRepository(engine=f1_engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        jq_mock = MagicMock()
        jq_mock.notify_watchers = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine,
            "inst-term-reason",
            status="running",
            created_at=now - timedelta(seconds=1800),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-f1-term-reason",
            instance_id="inst-term-reason",
            project_id="test-project",
            queue_id="queue-f1-term-reason",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-term-reason",
            job_id="job-f1-term-reason",
            instance_id="inst-term-reason",
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=jq_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        job_after = repository.get("job-f1-term-reason")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DEAD.value, (
            f"f1 should have DEAD-finalized the zombie. Got "
            f"admission_state={job_after.admission_state!r}, "
            f"details: {stats['details']}"
        )
        assert (
            job_after.terminal_reason == "pattern_f1_orphan"
        ), (
            f"f1 finalize MUST persist the durable "
            f"terminal_reason='pattern_f1_orphan'. Got "
            f"terminal_reason={job_after.terminal_reason!r} "
            f"(error_message/completed_at are silently stripped by "
            f"_REMOVED_JOB_COLUMNS — terminal_reason is the only "
            f"durable record)"
        )


class TestPatternF1KillSwitch:
    """Point 4 — ENSEMBLE_ORPHAN_F1_ENABLED (default ON, restart-to-flip).

    OFF makes f1 fully inert (Pattern-f sweep lane only — patterns
    a-e and f2 are untouched).
    """

    def _build_service(self, f1_engine):
        repository = JobRepository(f1_engine)
        task_repository = TaskRepository(f1_engine)
        lock_repo = LockRepository(f1_engine)
        instance_repo = SQLModelInstanceRepository(engine=f1_engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        jq_mock = MagicMock()
        jq_mock.notify_watchers = AsyncMock(return_value=None)
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=jq_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )
        return service, repository

    @pytest.mark.asyncio
    async def test_kill_switch_off_makes_f1_inert(
        self, f1_engine, monkeypatch,
    ):
        """Kill-switch OFF → the zombie shape is NOT DEAD-finalized;
        a skip detail names the switch. (RED on HEAD — the switch
        does not exist there, so f1 fires.)
        """
        monkeypatch.setenv("ENSEMBLE_ORPHAN_F1_ENABLED", "0")
        _jrs._reset_orphan_f1_for_tests()
        try:
            service, repository = self._build_service(f1_engine)
            now = datetime.now(timezone.utc)

            _insert_instance(
                f1_engine,
                "inst-ks-off",
                status="running",
                created_at=now - timedelta(seconds=1800),
            )
            _insert_job_item(
                f1_engine,
                job_id="job-f1-ks-off",
                instance_id="inst-ks-off",
                project_id="test-project",
                queue_id="queue-f1-ks-off",
                admission_state=AdmissionState.ACTIVE.value,
                created_at=now - timedelta(seconds=1800),
            )

            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )

            job_after = repository.get("job-f1-ks-off")
            assert job_after is not None
            assert (
                job_after.admission_state == AdmissionState.ACTIVE.value
            ), (
                f"Kill-switch OFF must make f1 fully inert (JobItem "
                f"stays ACTIVE). Got admission_state="
                f"{job_after.admission_state!r}, details: "
                f"{stats['details']}"
            )
            # Inert-path detail contract (review polish d, 96a66e50
            # follow-up): the skip record MUST use the canonical
            # ``orphan_active_skipped_f1_disabled`` pattern key and
            # name this job — operators and log scrapers key on the
            # exact detail family, not merely "a skip happened".
            disabled_details = [
                d for d in stats.get("details", [])
                if d.get("pattern")
                == "orphan_active_skipped_f1_disabled"
                and d.get("job_id") == "job-f1-ks-off"
            ]
            assert disabled_details, (
                f"Kill-switch OFF must emit an explicit "
                f"'orphan_active_skipped_f1_disabled' detail record "
                f"for the inert-skipped JobItem. Got details: "
                f"{stats.get('details')}"
            )
        finally:
            _jrs._reset_orphan_f1_for_tests()

    @pytest.mark.asyncio
    async def test_kill_switch_off_leaves_f2_working(
        self, f1_engine, monkeypatch,
    ):
        """Kill-switch OFF gates the f1 sub-shape ONLY — the f2
        (active + COMPLETED Task → DONE) sub-shape keeps firing.
        """
        from unittest.mock import patch

        monkeypatch.setenv("ENSEMBLE_ORPHAN_F1_ENABLED", "0")
        _jrs._reset_orphan_f1_for_tests()
        try:
            service, repository = self._build_service(f1_engine)
            now = datetime.now(timezone.utc)

            _insert_instance(
                f1_engine,
                "inst-ks-off-f2",
                status="running",
                created_at=now - timedelta(seconds=1800),
            )
            _insert_job_item(
                f1_engine,
                job_id="job-f2-ks-off",
                instance_id="inst-ks-off-f2",
                project_id="test-project",
                queue_id="queue-f2-ks-off",
                admission_state=AdmissionState.ACTIVE.value,
                created_at=now - timedelta(seconds=1800),
            )
            task_id = _insert_task_with_status(
                f1_engine,
                work_id="job-f2-ks-off",
                instance_id="inst-ks-off-f2",
                status=TaskStatus.COMPLETED.value,
                created_at=now - timedelta(seconds=1800),
                completed_at=now - timedelta(seconds=300),
            )

            # Wire the dependency-bus stub (zero pending watchers)
            # so the f2 gate passes — mirrors the existing f2 tests.
            class _BusStub:
                async def pending_watchers(self, source_task_id):
                    return []

            with patch(
                "daemon.services.job_recovery_service"
                ".get_dependency_bus",
                return_value=_BusStub(),
            ):
                stats = await service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )

            job_after = repository.get("job-f2-ks-off")
            assert job_after is not None
            assert (
                job_after.admission_state == AdmissionState.DONE.value
            ), (
                f"Kill-switch OFF must NOT disturb the f2 sub-shape "
                f"(active + COMPLETED Task → DONE). Got "
                f"admission_state={job_after.admission_state!r}, "
                f"details: {stats['details']}"
            )
        finally:
            _jrs._reset_orphan_f1_for_tests()


class TestObserverMintLinkageContract:
    """Point 1 — the observer mint-site linkage contract.

    ``JobFeedbackObserver._trigger_next_job`` is the ONLY JobItem-driven
    ``enqueue_message`` dispatch that did NOT pass ``work_id=job_id``
    (JobProcessor's dispatch does — the documented
    ``work_id == job_id`` contract at job_processor.py). The
    downstream ``_prepare_enqueued_message`` then auto-mints a fresh
    UUID4, breaking the Task↔JobItem linkage that Pattern-f1's
    ``get_by_work_id(job_id)`` depends on.
    """

    @staticmethod
    def _build_observer(enqueue_result_job_id: str):
        """Observer with a mocked dispatch pipeline for a TASK-type job."""
        started = SimpleNamespace(
            job_id="job-mint-1",
            job_type="task",
            instance_id="inst-mint-target",
            agent_id="developer",
            project_id="proj-mint-1",
            message="drive the work",
            source="api",
            admission_state=AdmissionState.ACTIVE.value,
        )
        jq = MagicMock()
        jq._get_next_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-mint-1")
        )
        jq.start_job = AsyncMock(return_value=started)
        jq._repository = MagicMock()
        manager = MagicMock()
        manager.spawn_instance_with_mcp = AsyncMock(
            return_value="inst-mint-target"
        )
        manager.enqueue_message = AsyncMock(
            return_value=AsyncMessageResult(
                message_id="msg-mint-1",
                instance_id="inst-mint-target",
                status="queued",
                job_id=enqueue_result_job_id,
            )
        )
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=jq,
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=manager,
        )
        return observer, manager

    @pytest.mark.asyncio
    async def test_observer_triggered_dispatch_carries_work_id(self):
        """Mint contract — the observer's dispatch MUST carry
        ``work_id=job_id`` so the driving Task links to its JobItem.
        (RED on HEAD: the call omits work_id.)
        """
        observer, manager = self._build_observer(
            enqueue_result_job_id="job-mint-1"
        )

        await observer._trigger_next_job(
            SimpleNamespace(project_id="proj-mint-1")
        )

        assert manager.enqueue_message.await_count == 1, (
            "The observer must dispatch exactly one enqueue_message "
            "for a TASK-type job."
        )
        kwargs = manager.enqueue_message.call_args.kwargs
        assert kwargs.get("work_id") == "job-mint-1", (
            f"Mint-site linkage contract violated: the observer's "
            f"enqueue_message dispatch MUST pass work_id=job_id so "
            f"Task.work_id == JobItem.job_id (Pattern-f1's "
            f"get_by_work_id(job_id) depends on it). Got kwargs keys: "
            f"{sorted(kwargs.keys())}"
        )

    @pytest.mark.asyncio
    async def test_observer_warns_on_linkage_contract_violation(
        self, caplog,
    ):
        """Tripwire — a dispatch whose returned Task work_id does not
        match the JobItem's job_id must emit a WARNING naming the
        linkage-contract violation (future-regression detector).
        (RED on HEAD: no such tripwire exists.)
        """
        observer, manager = self._build_observer(
            enqueue_result_job_id="mismatched-fresh-uuid"
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.job_feedback_observer"
        ):
            await observer._trigger_next_job(
                SimpleNamespace(project_id="proj-mint-1")
            )

        violations = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "work_id" in r.getMessage()
            and "job-mint" in r.getMessage()
        ]
        assert violations, (
            f"The observer must WARN when a task-job dispatch returns "
            f"a Task work_id that does not match JobItem.job_id "
            f"(linkage-contract tripwire). Captured: "
            f"{[r.getMessage() for r in caplog.records]}"
        )


class TestProcessorRespawnMintLinkageContract:
    """W1 (council 2026-08-31) — the JobProcessor re-spawn mint sites.

    ``JobProcessor._process_next_job`` dispatches from TWO orphan
    re-spawn sites in its ACTIVE-admission recovery loop, and BOTH
    omitted ``work_id=proc_job.job_id`` — the same fresh-UUID mint /
    linkage-break class as the observer incident (69a34b35):

      * crash-recovery re-spawn — instance genuinely crashed/missing
        (``get_instance`` → KeyError, DB row alive + non-terminal)
        re-spawns and re-dispatches (~:959),
      * orphan-resume re-spawn — ``instance_id`` never set (safety
        net) resumes and re-dispatches (~:1019).

    The downstream ``_prepare_enqueued_message`` auto-mints a fresh
    UUID4 for the driving Task when ``work_id`` is absent, so
    Pattern-f1's ``get_by_work_id(job_id)`` misses the Task and the
    subtree-alive guard is the only backstop during PENDING/RUNNING
    life — completed work still DEAD-finalizes after ~900s tree
    quiet. Both sites MUST carry ``work_id=proc_job.job_id``.
    """

    @staticmethod
    def _build_processor(
        enqueue_result_job_id: str,
        proc_instance_id: str | None,
    ):
        """Processor with a mocked pipeline driving ONE ACTIVE task job
        through the crash-recovery (instance_id set) or orphan-resume
        (instance_id None) re-spawn branch.
        """
        queue = SimpleNamespace(
            queue_id="queue-w1-respawn",
            project_id="proj-w1-respawn",
            queue_name="default",
            is_paused=False,
            concurrency_limit=1,
            queue_type="fifo",
        )
        proc_job = SimpleNamespace(
            job_id="job-w1-respawn-1",
            job_type="task",
            instance_id=proc_instance_id,
            agent_id="developer",
            project_id="proj-w1-respawn",
            message="drive the recovered work",
            source="api",
            admission_state=AdmissionState.ACTIVE.value,
        )

        jq = MagicMock()
        jq._repository = MagicMock()
        jq._repository.list_pending_by_queue = MagicMock(return_value=[])
        jq._repository.list_by_queue = MagicMock(
            return_value=([proc_job], 1)
        )
        jq._repository.stamp_message_id = MagicMock(return_value=None)

        manager = MagicMock()
        if proc_instance_id is not None:
            # Crash-recovery shape: the instance is NOT in memory
            # (KeyError) but its DB row is alive and non-terminal —
            # the "genuine crash" fall-through to re-spawn.
            manager.get_instance = AsyncMock(
                side_effect=KeyError(proc_instance_id)
            )
            manager._instance_repository = MagicMock()
            manager._instance_repository.get = MagicMock(
                return_value=SimpleNamespace(
                    instance_id=proc_instance_id, status="running",
                )
            )
        manager.spawn_instance_with_mcp = AsyncMock(
            return_value=proc_instance_id or "inst-w1-respawn"
        )
        manager.enqueue_message = AsyncMock(
            return_value=AsyncMessageResult(
                message_id="msg-w1-respawn-1",
                instance_id=proc_instance_id or "inst-w1-respawn",
                status="queued",
                job_id=enqueue_result_job_id,
            )
        )

        project_repo = MagicMock()
        project_repo.get = MagicMock(
            return_value=SimpleNamespace(job_queue_paused=False)
        )
        queue_repo = MagicMock()
        queue_repo.list_queues_with_admittable_work = MagicMock(
            return_value=[queue]
        )

        processor = JobProcessor(
            queue_service=jq,
            instance_manager=manager,
            project_repo=project_repo,
            queue_repo=queue_repo,
            poll_interval=0.1,
        )
        return processor, manager

    @pytest.mark.asyncio
    async def test_crash_recovery_respawn_carries_work_id(self):
        """Crash-recovery re-spawn dispatch MUST carry
        ``work_id=proc_job.job_id`` (RED on 96a66e50: kwargs lack
        work_id → fresh-UUID mint → linkage break).
        """
        processor, manager = self._build_processor(
            enqueue_result_job_id="job-w1-respawn-1",
            proc_instance_id="inst-w1-crashed",
        )

        await processor._process_next_job()

        assert manager.enqueue_message.await_count == 1, (
            "The crash-recovery re-spawn must dispatch exactly one "
            "enqueue_message for the orphaned ACTIVE task job."
        )
        kwargs = manager.enqueue_message.call_args.kwargs
        assert kwargs.get("work_id") == "job-w1-respawn-1", (
            f"Crash-recovery re-spawn mint-site linkage contract "
            f"violated: the re-spawn dispatch MUST pass "
            f"work_id=proc_job.job_id so Task.work_id == "
            f"JobItem.job_id (Pattern-f1's get_by_work_id(job_id) "
            f"depends on it). Got kwargs keys: "
            f"{sorted(kwargs.keys())}"
        )

    @pytest.mark.asyncio
    async def test_orphan_resume_respawn_carries_work_id(self):
        """Orphan-resume re-spawn dispatch (no instance_id safety
        net) MUST carry ``work_id=proc_job.job_id`` (RED on 96a66e50:
        kwargs lack work_id).
        """
        processor, manager = self._build_processor(
            enqueue_result_job_id="job-w1-respawn-1",
            proc_instance_id=None,
        )

        await processor._process_next_job()

        assert manager.enqueue_message.await_count == 1, (
            "The orphan-resume re-spawn must dispatch exactly one "
            "enqueue_message for the instance-less ACTIVE task job."
        )
        kwargs = manager.enqueue_message.call_args.kwargs
        assert kwargs.get("work_id") == "job-w1-respawn-1", (
            f"Orphan-resume re-spawn mint-site linkage contract "
            f"violated: the re-spawn dispatch MUST pass "
            f"work_id=proc_job.job_id so Task.work_id == "
            f"JobItem.job_id (Pattern-f1's get_by_work_id(job_id) "
            f"depends on it). Got kwargs keys: "
            f"{sorted(kwargs.keys())}"
        )
