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
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlmodel import Session as SQLModelSession

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
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.job_queue_service import JobQueueService
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
) -> None:
    """Insert an Instance row directly via SQL. Mirrors the
    helper in test_seam_invariants.py.
    """
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
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
) -> int:
    """Insert a Task row directly via SQL with an explicit
    ``work_id`` (the cross-system linkage key — must match
    the JobItem's ``job_id`` for the f2 candidate to be
    detected).
    """
    now = (created_at or datetime.now(timezone.utc))
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background)
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
        _insert_instance(engine, "inst-f1-1", project_id="test-project")
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
        """
        _insert_instance(engine, "inst-f2-1", project_id="test-project")
        _insert_job_item(
            engine,
            job_id="job-f2-1",
            instance_id="inst-f2-1",
            project_id="test-project",
            queue_id="queue-f2-1",
            admission_state=AdmissionState.ACTIVE.value,
        )
        # Task with work_id == job_id (linkage contract)
        # and status = COMPLETED. The age is irrelevant
        # for f2 — only the existence + status of the Task
        # matter. ``created_at`` is fresh.
        task_id = _insert_task_with_status(
            engine,
            work_id="job-f2-1",
            instance_id="inst-f2-1",
            status=TaskStatus.COMPLETED.value,
        )

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
        # sanity belt.
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
        """
        _insert_instance(engine, "inst-f-grace-past", project_id="test-project")
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
        _insert_instance(engine, "inst-f-wiring", project_id="test-project")
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
