"""Tests for TaskRepository."""

import pytest
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")


def _create_task_with_status(
    engine,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    instance_id: str = "test-instance",
    message_id: str = "test-message",
    status: str = TaskStatus.PENDING.value,
    is_deferred: bool = False,
) -> Task:
    """Insert a task with a specific status directly via raw SQL.

    Used by the status-guard tests to set up rows in non-default
    statuses (e.g. CANCELLED, FAILED, COMPLETED) without going through
    the repository's claim/complete/cancel lifecycle. Mirrors the
    helper in ``test_task_retry_repository.py``; kept local to avoid
    cross-file test coupling.

    The ``is_deferred`` parameter (Phase 3 Part B2, 2026-06-27) lets
    defer-queue tests create deferred task rows directly — the
    repository's public API does not yet expose a "create deferred
    task" call path for tests to use, so we use this helper to insert
    deferred rows for the defer-gate tests.
    """
    created_at = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, created_at, cancel_requested,
                                  retry_scheduled, work_id, is_deferred)
                VALUES (:task_type, :instance_id, :message_id, :status,
                        :retry_count, :created_at, :cancel_requested,
                        :retry_scheduled, :work_id, :is_deferred)
                """
            ),
            {
                "task_type": task_type,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": created_at,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": str(uuid.uuid4()),
                # Python bool so the bind works on both SQLite
                # (INTEGER 0/1) and PostgreSQL (BOOLEAN false/true).
                "is_deferred": is_deferred,
            },
        )
        task_id = result.lastrowid

        row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id},
        ).fetchone()
        repo = TaskRepository(engine)
        return repo._row_to_task(row)


class TestTaskCreation:
    """Tests for task creation."""

    def test_create_task(self, repository, sample_task_data):
        """Test creating a basic task."""
        task = repository.create(**sample_task_data)

        assert task.id is not None
        assert task.task_type == sample_task_data["task_type"]
        assert task.instance_id == sample_task_data["instance_id"]
        assert task.message_id == sample_task_data["message_id"]
        assert task.status == TaskStatus.PENDING.value
        assert task.worker_id is None
        assert task.result is None
        assert task.error is None
        assert task.started_at is None
        assert task.completed_at is None

    def test_create_task_with_defaults(self, repository):
        """Test creating a task with minimal data."""
        task = repository.create(
            task_type=TaskType.CLEANUP.value,
            instance_id="instance-1",
        )

        assert task.id is not None
        assert task.task_type == TaskType.CLEANUP.value
        assert task.status == TaskStatus.PENDING.value
        assert task.message_id is None

    def test_create_multiple_tasks_ordered(self, repository):
        """Test that multiple tasks are ordered by created_at."""
        task1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        task2 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")
        task3 = repository.create(task_type=TaskType.SEND_REPORT.value, instance_id="i3")

        assert task1.id < task2.id < task3.id
        assert task1.created_at <= task2.created_at <= task3.created_at


class TestTaskRetrieval:
    """Tests for task retrieval methods."""

    def test_get_existing_task(self, repository, sample_task_data):
        """Test getting an existing task by ID."""
        created = repository.create(**sample_task_data)

        retrieved = repository.get(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.task_type == created.task_type

    def test_get_nonexistent_task(self, repository):
        """Test getting a non-existent task returns None."""
        result = repository.get(99999)
        assert result is None

    def test_get_by_instance(self, repository):
        """Test getting all tasks for an instance."""
        instance_id = "test-instance"

        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id=instance_id)
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id=instance_id)
        repository.create(task_type=TaskType.CLEANUP.value, instance_id=instance_id)
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="other-instance")

        tasks = repository.get_by_instance(instance_id)

        assert len(tasks) == 3
        assert all(t.instance_id == instance_id for t in tasks)

    def test_get_by_message(self, repository, sample_task_data):
        """Test getting task by message ID."""
        created = repository.create(**sample_task_data)

        retrieved = repository.get_by_message(sample_task_data["message_id"])

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.message_id == sample_task_data["message_id"]

    def test_get_by_message_not_found(self, repository):
        """Test getting task by non-existent message ID."""
        result = repository.get_by_message("nonexistent-message")
        assert result is None


class TestTaskClaiming:
    """Tests for atomic task claiming."""

    def test_claim_pending_task(self, repository, sample_task_data):
        """Test claiming a pending task."""
        created_task = repository.create(**sample_task_data)
        assert created_task.status == TaskStatus.PENDING.value

        claimed_task = repository.claim_pending_task(worker_id="worker-1")

        assert claimed_task is not None
        assert claimed_task.id == created_task.id
        assert claimed_task.status == TaskStatus.RUNNING.value
        assert claimed_task.worker_id == "worker-1"
        assert claimed_task.started_at is not None

    def test_concurrent_claim_only_one_wins(self, repository, sample_task_data):
        """Smoke test only: that two claim_pending_task calls don't both claim the same task.

        Regression test for the task-claim race where the outer UPDATE WHERE
        clause only checked id=(subquery) without re-verifying status='pending'.
        After the fix, the outer WHERE includes 'AND status = :status_pending'
        so the losing worker gets None.

        Note: This is a smoke test only. The actual EvalPlanQual recheck fix
        for PostgreSQL concurrency is NOT covered by any integration test in
        this repository — the claim race itself cannot manifest on in-memory
        SQLite (writes are serialized), and no PostgreSQL integration test
        suite exists in this codebase. If a real PG integration test is
        added, update this docstring to point at it.
        """
        # Create a single pending task
        created_task = repository.create(**sample_task_data)

        # Worker-1 claims first
        claimed_by_1 = repository.claim_pending_task(worker_id="worker-1")

        # Worker-2 tries to claim the same task
        claimed_by_2 = repository.claim_pending_task(worker_id="worker-2")

        # Worker-1 wins (task was pending)
        assert claimed_by_1 is not None
        assert claimed_by_1.id == created_task.id
        assert claimed_by_1.status == TaskStatus.RUNNING.value
        assert claimed_by_1.worker_id == "worker-1"

        # Worker-2 gets None (task is no longer pending — already claimed by worker-1)
        # This is exactly what the 'AND status = :status_pending' outer guard ensures:
        # even if the inner subquery somehow returns the same id, the outer UPDATE
        # rechecks status and finds it's now 'running', not 'pending'.
        assert claimed_by_2 is None, (
            f"Worker-2 should NOT have claimed a task that worker-1 already took. "
            f"Got: {claimed_by_2}"
        )

        # Verify the task in DB is claimed by exactly one worker
        db_task = repository.get(created_task.id)
        assert db_task.status == TaskStatus.RUNNING.value
        assert db_task.worker_id == "worker-1"

    def test_claim_pending_task_none_when_empty(self, repository):
        """Test claiming when no tasks available."""
        result = repository.claim_pending_task(worker_id="worker-1")
        assert result is None

    def test_claim_pending_task_with_type_filter(self, repository):
        """Test claiming tasks - task_type filter was removed."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="instance-1",
        )
        repository.create(
            task_type=TaskType.SEND_REPORT.value,
            instance_id="instance-1",
        )

        # task_type parameter was removed from claim_pending_task
        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.id == task.id

    def test_claim_only_returns_pending(self, repository):
        """Test that claiming only returns pending tasks."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="instance-1",
        )
        repository.claim_pending_task(worker_id="worker-1")

        result = repository.claim_pending_task(worker_id="worker-2")
        assert result is None

    def test_claim_fifo_ordering(self, repository):
        """Test that claiming returns oldest pending task first."""
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i3")

        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.instance_id == "i1"

    def test_claim_skips_pending_tasks_for_busy_instance(self, repository):
        """Fix B: a pending task for an instance with a RUNNING task must not be claimed."""
        # Two pending tasks for inst-A, one for inst-B
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-A", message_id="m1")
        t2 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-A", message_id="m2")
        t3 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-B", message_id="m3")

        # Worker 1 claims t1 (now RUNNING for inst-A)
        claimed1 = repository.claim_pending_task(worker_id="worker-1")
        assert claimed1 is not None
        assert claimed1.id == t1.id

        # Worker 2 cannot claim t2 (inst-A is busy); must claim t3 (inst-B) instead
        claimed2 = repository.claim_pending_task(worker_id="worker-2")
        assert claimed2 is not None
        assert claimed2.id == t3.id
        assert claimed2.instance_id == "inst-B"

        # Worker 3 cannot claim t2 — inst-A still busy
        claimed3 = repository.claim_pending_task(worker_id="worker-3")
        assert claimed3 is None

        # Worker 1 finishes t1
        repository.complete_task(t1.id, {"success": True})

        # Now t2 is claimable
        claimed4 = repository.claim_pending_task(worker_id="worker-3")
        assert claimed4 is not None
        assert claimed4.id == t2.id

    def test_claim_unblocks_when_sibling_fails(self, repository):
        """Fix B: a sibling task for a busy instance becomes claimable after the
        running task fails (not just completes)."""
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-X", message_id="m1")
        t2 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-X", message_id="m2")

        claimed1 = repository.claim_pending_task(worker_id="worker-1")
        assert claimed1.id == t1.id

        # t2 not claimable while t1 running
        assert repository.claim_pending_task(worker_id="worker-2") is None

        # t1 fails → t2 should be claimable
        repository.fail_task(t1.id, "boom")
        claimed2 = repository.claim_pending_task(worker_id="worker-2")
        assert claimed2 is not None
        assert claimed2.id == t2.id

    def test_has_pending_tasks_blocked_by_busy_instance(self, repository):
        """The empty-claim-due-to-guard signal returns True when a pending
        task is blocked by a busy instance, False otherwise."""
        # Initially: no pending, no busy → False
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

        # Need TWO tasks for the same instance: one RUNNING (blocks), one
        # PENDING (blocked).
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-Y", message_id="m1")
        t2 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-Y", message_id="m2")
        # Both PENDING, no RUNNING → False
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

        # Claim t1 → t1 is RUNNING, t2 is PENDING and blocked by t1's instance
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed.id == t1.id
        assert repository.has_pending_tasks_blocked_by_busy_instance() is True

        # Complete t1 → t2 is still PENDING but no RUNNING blocks it → False
        repository.complete_task(t1.id, {"ok": True})
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

    def test_claim_skips_when_message_job_processing_for_instance(self, repository, engine):
        """Cross-system guard: a task for an instance with a PROCESSING MESSAGE
        job in job_queue_items must not be claimed concurrently. This prevents
        the langgraph checkpoint race where the task forks from a stale state
        and shadows the AIMessage produced by the job (the
        "Done! 👋 lost" bug)."""
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        # Insert a PROCESSING MESSAGE job for inst-J with an instance in
        # running status (waiting_for=0) — the job is actively driving
        # graph.astream and must block the task.
        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id="inst-J",
                agent_id="leader",
                agent_dir="agents/leader",
                status="running",
            ))
            session.add(JobItem(
                job_id="job-J1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-J",
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A pending task for the same instance must NOT be claimable
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-J", message_id="m1")
        assert repository.claim_pending_task(worker_id="worker-1") is None

        # has_pending_tasks_blocked_by_busy_instance should also report True
        assert repository.has_pending_tasks_blocked_by_busy_instance() is True

        # Complete the job → t1 becomes claimable. Phase 2 dual-write
        # contract (see ``status_to_admission``): every status mutation
        # must co-move ``admission_state`` in the same transaction.
        # COMPLETED → admission_state='done', which is excluded by the
        # new ``admission_state IN ('queued', 'active')`` predicate in
        # claim_pending_task / has_blocked_pending_tasks (Phase 3
        # admission-decision migration). Setting only ``admission_state``
        # is enough now — ``status`` column is gone in Phase 5.
        with SQLModelSession(engine) as session:
            job = session.get(JobItem, "job-J1")
            job.admission_state = "done"
            session.commit()

        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == t1.id

    def test_claim_allowed_when_job_processing_but_instance_waiting_for_children(self, repository, engine):
        """WAITING_CHILDREN carve-out: when the instance is in WAITING_CHILDREN
        (the job is just a FIFO placeholder waiting for the instance
        lifecycle, NOT driving graph.astream), a child-completion report
        task for that instance MUST be claimable. Without this carve-out, the
        job waits for the child report and the child report waits for the
        job: deadlock."""
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        # Instance has spawned children and is waiting for them. The job
        # stays PROCESSING (so the FIFO queue doesn't start the next job),
        # but the job is NOT holding the langgraph thread.
        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id="inst-W",
                agent_id="leader",
                agent_dir="agents/leader",
                status="waiting_children",
            ))
            session.add(JobItem(
                job_id="job-W1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-W",
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # The child-completion report task MUST be claimable despite the
        # PROCESSING job — the job is just a FIFO placeholder.
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-W", message_id="m1")
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == t1.id

        # has_pending_tasks_blocked_by_busy_instance should report False:
        # the job is in PROCESSING but the instance is WAITING_CHILDREN,
        # so there is no actual langgraph contention.
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

    def test_claim_blocked_when_instance_waiting_for_children_but_no_instance_row(self, repository, engine):
        """Defensive: if the job's instance_id has no matching `instances`
        row (e.g. mid-creation), COALESCE(waiting_for, 0) = 0 falls
        through and the missing-status NULL check treats it as not
        WAITING_CHILDREN, so the job blocks as before. This preserves the
        original cross-system guard for the no-instance-row edge case."""
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, AdmissionState

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(JobItem(
                job_id="job-X1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-X-no-row",
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-X-no-row", message_id="m1")
        # No instance row → COALESCE makes waiting_for=0, status NULL → job
        # IS treated as actively processing → task is blocked.
        assert repository.claim_pending_task(worker_id="worker-1") is None

    def test_claim_unaffected_by_non_message_job_types(self, repository, engine):
        """Phase 2.5 (D13) pin: the cross-system guard in
        ``claim_pending_task`` no longer filters ``j.job_type =
        'message'`` — it now blocks on ANY processing ``JobItem``
        for the instance, regardless of job type. After D13, all
        ``JobItem`` rows are TASK-type (message-type jobs are no
        longer created), so the previous "only MESSAGE jobs block"
        carve-out is no longer relevant in the post-D13 world.

        The new contract: a non-MESSAGE (e.g. ``cleanup``) processing
        job for the instance DOES block a ``claim_pending_task``
        call for the same instance. The pre-D13 carve-out (a
        CLEANUP job did NOT block because it doesn't touch the
        langgraph thread) was correct for the legacy dual-path
        architecture, but after D13 the carve-out's premise (MESSAGE
        is the only "graph-driving" job_type) no longer holds.
        WorkerPool admission now happens via the
        ``NOT EXISTS (... task t message_id ...)`` carve-out in
        the subquery (matching Task + pending/running status), not
        via the job_type filter.

        This test pins the new behaviour: a CLEANUP processing
        job with empty ``job_metadata`` (no message_id) blocks
        the task claim, because the ``NOT EXISTS`` carve-out
        returns TRUE (no matching Task row exists) and the
        inner subquery returns the instance.
        """
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, AdmissionState

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(JobItem(
                job_id="job-K1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="cleanup",
                source="system",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="cleanup",
                instance_id="inst-K",
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A pending task for inst-K MUST be blocked — the
        # processing CLEANUP job still holds the slot in the
        # post-D13 world (the cross-system guard fires for any
        # processing JobItem; the job_type filter is removed).
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-K", message_id="m1")
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is None, (
            f"Task for inst-K must be blocked by the processing "
            f"CLEANUP job (post-D13: any processing JobItem blocks, "
            f"not just MESSAGE); got: {claimed}"
        )
        # The busy-instance probe also reports True (a
        # non-MESSAGE processing job still blocks).
        assert repository.has_pending_tasks_blocked_by_busy_instance() is True

    def test_claim_unaffected_by_soft_deleted_processing_job(self, repository, engine):
        """Regression: a soft-deleted PROCESSING MESSAGE job must NOT block
        task claiming. The cross-system guard must filter ``deleted_at IS NULL``
        just like the canonical job-side query
        (``find_processing_message_jobs_by_instance``). Without that filter a
        soft-deleted processing job lingers forever (soft-deleted jobs are
        never auto-completed) and permanently blocks the instance's task
        queue — a livelock."""
        from datetime import datetime, timezone
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem, AdmissionState

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(JobItem(
                job_id="job-L1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-L",
                created_at=now,
                deleted_at=now,  # soft-deleted while still PROCESSING
                priority=0,
                retry_count=0,
            ))
            session.commit()

        t1 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-L", message_id="m1"
        )

        # The busy-instance probe must not report a false block: inst-L has a
        # PENDING task and a soft-deleted processing job, but no RUNNING task.
        # If the guard forgets ``deleted_at IS NULL`` this returns True.
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

        # The task MUST be claimable — the only blocker is a soft-deleted job.
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == t1.id

    def test_claim_allowed_when_matching_task_exists_for_processing_job(self, repository, engine):
        """Unified-dispatcher admission carve-out: when a MESSAGE job is in
        PROCESSING and a Task row exists for the same ``message_id`` with
        status ``pending`` or ``running`` (the unified dispatcher has
        already admitted the job to the worker pool), the worker MUST be
        allowed to claim the Task. Without this carve-out, the Task
        can't claim (the MESSAGE job is PROCESSING) and the JobItem
        can't reach its terminal transition (the Task never claimed) —
        self-deadlock that wedges every message in PROCESSING.

        Mirror of ``test_claim_allowed_when_job_processing_but_instance_waiting_for_children``
        but for the unified-dispatcher admission case (which is the
        NORMAL case in Phase D — every HTTP message send creates a
        JobItem in PROCESSING and a Task in PENDING for the same
        message_id).
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        # Parent instance exists in normal RUNNING state (NOT
        # WAITING_CHILDREN) — this is the path that was deadlocked
        # before the fix. The JobItem is PROCESSING (set by
        # JobProcessor.start_job before the observer admits the
        # Task), and the Task is PENDING with the same message_id.
        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id="inst-UD-1",
                agent_id="leader",
                agent_dir="agents/leader",
                status="running",
            ))
            session.add(JobItem(
                job_id="job-UD-1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-UD-1",
                # message_id in job_metadata must match the Task's
                # message_id for the carve-out to fire. This is the
                # admission signal that distinguishes the unified
                # dispatcher from the legacy dual-path.
                job_metadata={"message_id": "m-UD-1"},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # Task created by the unified observer admission with the same
        # message_id.
        t1 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-UD-1",
            message_id="m-UD-1",
        )
        # The Task MUST be claimable — the MESSAGE job is the FIFO
        # placeholder for the unified dispatcher's admission, NOT
        # driving graph.astream.
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == t1.id

        # has_pending_tasks_blocked_by_busy_instance must report False:
        # the only blocker would be a PROCESSING MESSAGE job, but the
        # carve-out releases it because a corresponding Task row exists.
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

    def test_claim_blocked_when_message_id_mismatches(self, repository, engine):
        """message_id specificity: a Task row for a DIFFERENT message_id
        does NOT release the cross-system guard. This is critical because
        ``PROCESS_MESSAGE`` tasks are reused for child-completion reports
        (see ``daemon.services.child_reports``), which have a fresh
        ``report_message_id`` UUID — not the parent's user message_id. A
        child-completion report Task for a different message_id must NOT
        release the guard when the parent's MESSAGE job is PROCESSING:
        the parent's astream is still driving, and the child task must
        wait for either the parent to reach WAITING_CHILDREN or the
        parent's Task to admit itself.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id="inst-MIS-1",
                agent_id="leader",
                agent_dir="agents/leader",
                status="running",
            ))
            session.add(JobItem(
                job_id="job-MIS-1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-MIS-1",
                # Parent's user message_id is "m-parent".
                job_metadata={"message_id": "m-parent"},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A different message_id (e.g. a child-completion report) does
        # NOT release the guard — the parent's MESSAGE job is still
        # actively driving graph.astream in the legacy path.
        t1 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-MIS-1",
            message_id="m-child-report",
        )
        assert repository.claim_pending_task(worker_id="worker-1") is None
        # Busy-instance probe must report True: the only Task is pending
        # with a non-matching message_id, so the parent's PROCESSING
        # MESSAGE job still actively blocks.
        assert repository.has_pending_tasks_blocked_by_busy_instance() is True

    def test_claim_blocked_when_matching_task_is_terminal(self, repository, engine):
        """status filter: a Task row in a terminal status
        (COMPLETED/FAILED/CANCELLED) does NOT release the guard. The
        Task is no longer driving graph.astream, so releasing the guard
        would race a concurrent astream call. The window is bounded by
        the observer's event subscription; during it, the guard must
        still block to prevent two concurrent astream calls for the
        same instance.

        This test pins the ``status IN ('pending', 'running')`` filter
        in the carve-out — a future "simplification" that drops the
        status filter would silently reintroduce a different deadlock
        in this race window.

        Setup: a MESSAGE job is PROCESSING with message_id="m-TERM-1".
        A Task with the same message_id exists in COMPLETED status
        (e.g. from a prior cycle that the observer hasn't fully
        finalised yet). A fresh PENDING Task for a DIFFERENT message_id
        (e.g. a child-completion report) is being claimed. The COMPLETED
        Task must NOT release the guard for this fresh Task — the
        fresh Task has a different message_id so it can't be the
        unified-dispatcher admission for the MESSAGE job anyway, and
        the guard must still fire.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id="inst-TERM-1",
                agent_id="leader",
                agent_dir="agents/leader",
                status="running",
            ))
            session.add(JobItem(
                job_id="job-TERM-1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-TERM-1",
                # Parent's user message_id is "m-TERM-1".
                job_metadata={"message_id": "m-TERM-1"},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A Task with the same message_id as the parent's user message,
        # but in COMPLETED status (e.g. from a prior cycle). This Task
        # does NOT release the guard because ``status IN
        # ('pending', 'running')`` excludes COMPLETED.
        t_completed = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-TERM-1",
            message_id="m-TERM-1",
            status=TaskStatus.COMPLETED.value,
        )
        assert t_completed.id is not None

        # A fresh PENDING Task for a DIFFERENT message_id (e.g. a
        # child-completion report) must NOT be claimable — the parent's
        # PROCESSING MESSAGE job is still actively driving astream.
        t_pending = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-TERM-1",
            message_id="m-TERM-1-child-report",
        )
        assert repository.claim_pending_task(worker_id="worker-1") is None
        # Busy-instance probe must report True: the guard still fires
        # because the matching Task is COMPLETED (excluded by the
        # status filter) and the fresh Task has a non-matching
        # message_id.
        assert repository.has_pending_tasks_blocked_by_busy_instance() is True

    def test_claim_blocked_when_job_metadata_is_empty(self, repository, engine):
        """Defensive: a JobItem with empty ``job_metadata`` (e.g. a
        manually-injected or legacy row) must NOT release the guard.
        On both backends, ``json_extract(NULL/'{}', '$.message_id')``
        returns NULL, so the ``t.message_id = NULL`` comparison is
        UNKNOWN and the ``NOT EXISTS`` defaults to TRUE (blocker
        fires). This pins the NULL-extraction fallback so a future
        refactor doesn't silently invert it.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id="inst-EMPTY-1",
                agent_id="leader",
                agent_dir="agents/leader",
                status="running",
            ))
            session.add(JobItem(
                job_id="job-EMPTY-1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-EMPTY-1",
                # Explicitly empty job_metadata — same as the
                # ``test_claim_skips_when_message_job_processing_for_instance``
                # fixture (line 324) but for a fresh instance so the
                # test is self-contained.
                job_metadata={},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # Even with a matching Task, the empty job_metadata means
        # json_extract returns NULL → NOT EXISTS TRUE → blocker fires.
        t1 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-EMPTY-1",
            message_id="anything",
        )
        assert repository.claim_pending_task(worker_id="worker-1") is None


class TestDeferQueueGate:
    """Tests for the defer queue idle gate (Phase 3 Part B2, 2026-06-27).

    The gate holds back deferred tasks (``is_deferred=True``) when the
    candidate's project has at least one RUNNING non-deferred task.
    Non-deferred tasks bypass the gate entirely. The gate is
    project-scoped — non-deferred work in project A does NOT block
    deferred tasks in project B.
    """

    def _insert_instance(
        self,
        engine,
        instance_id: str,
        project_id: str,
        status: str = "running",
    ) -> None:
        """Insert a minimal Instance row directly via raw SQL.

        The Task model has no ``project_id`` column — the defer gate
        joins through ``instances`` to scope the active-non-deferred
        count. Helper keeps the test self-contained.
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
                    "agent_id": "developer",
                    "agent_dir": "agents/developer",
                    "status": status,
                    "project_id": project_id,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    def _create_deferred_task(
        self,
        repository,
        engine,
        instance_id: str,
        project_id: str,
        message_id: str,
    ) -> Task:
        """Create a PENDING deferred task and ensure its instance has a
        project_id (required for the defer gate's project-scoped count).
        """
        self._insert_instance(engine, instance_id, project_id)
        return _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
            is_deferred=True,
        )

    def test_deferred_task_blocked_when_project_has_active_non_deferred(
        self, repository, engine
    ):
        """Gate fires: a deferred task is NOT claimable while the same
        project has a RUNNING non-deferred task. The pre-check returns
        None before the atomic claim runs."""
        # Project A: one RUNNING non-deferred task (already claimed) +
        # one PENDING deferred task. The deferred task must wait.
        non_deferred = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-defer-A",
            message_id="m-nondefer",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # Move the non-deferred task to RUNNING via the atomic claim path.
        # Important: we must use ``running`` instances so the pause gate
        # in the claim SQL does not block the claim. Insert the instance
        # row first with status=running.
        self._insert_instance(engine, "inst-defer-A", "project-A")
        # Force the task to RUNNING without re-running claim_pending_task
        # (we want the pre-existing RUNNING task to be the gate's input).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET status = :running, "
                    "worker_id = :worker, started_at = :now, "
                    "last_heartbeat_at = :now WHERE id = :id"
                ),
                {
                    "running": TaskStatus.RUNNING.value,
                    "worker": "pre-existing-worker",
                    "now": datetime.now(timezone.utc),
                    "id": non_deferred.id,
                },
            )

        # Now insert a PENDING deferred task for the same project.
        deferred = self._create_deferred_task(
            repository, engine, "inst-defer-B", "project-A", "m-defer"
        )

        # The deferred task must NOT be claimable — the gate holds it back.
        assert repository.claim_pending_task(worker_id="worker-1") is None

        # Verify the deferred task is still PENDING (untouched by the gate).
        db_task = repository.get(deferred.id)
        assert db_task is not None
        assert db_task.status == TaskStatus.PENDING.value

    def test_deferred_task_claimable_when_project_is_idle(
        self, repository, engine
    ):
        """Gate does NOT fire: a deferred task IS claimable when the
        project has no RUNNING non-deferred tasks."""
        deferred = self._create_deferred_task(
            repository, engine, "inst-defer-C", "project-C", "m-defer-2"
        )

        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.id == deferred.id
        assert claimed.status == TaskStatus.RUNNING.value

    def test_non_deferred_task_unaffected_by_defer_gate(
        self, repository, engine
    ):
        """Gate does NOT apply to non-deferred tasks: a non-deferred
        candidate is always claimable, even when another non-deferred
        task is RUNNING in the same project."""
        # Two non-deferred tasks for the same project. t1 is RUNNING
        # (forces the per-instance guard for inst-X so claim_pending_task
        # skips t1 — but t2 is for a different instance, so the
        # per-instance guard does not block it).
        t1 = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-nondef-1",
            message_id="m-nd1",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        t2 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-nondef-2",
            message_id="m-nd2",
        )
        # Make t1 RUNNING with a healthy instance so the per-instance
        # guard has nothing to do for inst-nondef-2.
        self._insert_instance(engine, "inst-nondef-1", "project-D")
        self._insert_instance(engine, "inst-nondef-2", "project-D")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET status = :running, "
                    "worker_id = :worker, started_at = :now, "
                    "last_heartbeat_at = :now WHERE id = :id"
                ),
                {
                    "running": TaskStatus.RUNNING.value,
                    "worker": "pre-existing-worker",
                    "now": datetime.now(timezone.utc),
                    "id": t1.id,
                },
            )

        # t2 (non-deferred, inst-nondef-2) must be claimable despite t1
        # being RUNNING in the same project — the defer gate is
        # NON-defer-invisible.
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == t2.id

    def test_defer_gate_is_project_scoped(self, repository, engine):
        """Gate is project-scoped: a non-deferred task in project A
        does NOT block a deferred task in project B."""
        # Project A: one RUNNING non-deferred task.
        non_deferred_A = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-A-1",
            message_id="m-A-nd",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        self._insert_instance(engine, "inst-A-1", "project-A")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET status = :running, "
                    "worker_id = :worker, started_at = :now, "
                    "last_heartbeat_at = :now WHERE id = :id"
                ),
                {
                    "running": TaskStatus.RUNNING.value,
                    "worker": "pre-existing-worker",
                    "now": datetime.now(timezone.utc),
                    "id": non_deferred_A.id,
                },
            )

        # Project B: one PENDING deferred task (different project).
        deferred_B = self._create_deferred_task(
            repository, engine, "inst-B-1", "project-B", "m-B-defer"
        )

        # Deferred task in project B must be claimable — non-defer work
        # in project A does not cross the project boundary.
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == deferred_B.id
        assert claimed.status == TaskStatus.RUNNING.value

    def test_defer_gate_releases_when_non_deferred_completes(
        self, repository, engine
    ):
        """Gate releases: once the RUNNING non-deferred task completes,
        the deferred task becomes claimable."""
        non_deferred = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-E-1",
            message_id="m-E-nd",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        self._insert_instance(engine, "inst-E-1", "project-E")
        # Claim the non-deferred task via the normal claim path so it
        # is RUNNING with a valid worker + heartbeat.
        claimed_nd = repository.claim_pending_task(worker_id="worker-nd")
        assert claimed_nd is not None
        assert claimed_nd.id == non_deferred.id

        # Insert a deferred task for the same project.
        deferred = self._create_deferred_task(
            repository, engine, "inst-E-2", "project-E", "m-E-defer"
        )

        # Gate holds the deferred task.
        assert repository.claim_pending_task(worker_id="worker-1") is None

        # Complete the non-deferred task.
        repository.complete_task(claimed_nd.id, {"ok": True})

        # Gate releases — the deferred task is now claimable.
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == deferred.id

    def test_claim_skips_deferred_paused_instance_to_younger_non_deferred(
        self, repository, engine
    ):
        """Phase 3 Part B2 starvation regression (2026-06-27).

        The deterministic starvation bug: when the OLDEST PENDING task
        is ``is_deferred=True`` AND its instance is PAUSED, the
        Python pre-check (which did NOT apply the pause gate) would
        still pick the deferred task as the candidate. The defer gate
        then counted the project's active non-deferred work, found
        count > 0, and returned ``None`` for the entire method —
        starving a YOUNGER non-deferred eligible task that the
        atomic claim would otherwise have picked.

        With the defer gate folded INTO the atomic SQL's inner SELECT,
        the pause gate and the defer gate evaluate together: the
        deferred paused task is filtered out by the pause gate (its
        instance is PAUSED), and the next eligible non-deferred task
        is selected by the inner SELECT.

        Setup:
            * project-starve:
              - inst-A (RUNNING): one RUNNING non-deferred task
                (this makes the project's active non-deferred count
                > 0 — the gate's blocker condition).
              - inst-B (PAUSED):  one PENDING deferred task (OLDER
                — created first). Ineligible due to pause gate AND
                defer gate. THIS is the starved candidate.
              - inst-C (RUNNING): one PENDING non-deferred task
                (YOUNGER — created last). The eligible candidate
                the prior pre-check would have starved.
        """
        # 1. Project's already-running non-deferred task (inst-A).
        self._insert_instance(engine, "inst-A", "project-starve")
        running_nd = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-A",
            message_id="m-running-nd",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET worker_id = :worker, "
                    "started_at = :now, last_heartbeat_at = :now "
                    "WHERE id = :id"
                ),
                {
                    "worker": "pre-existing-worker",
                    "now": datetime.now(timezone.utc),
                    "id": running_nd.id,
                },
            )

        # 2. Older deferred task on a PAUSED instance (inst-B).
        self._insert_instance(
            engine, "inst-B", "project-starve", status="paused"
        )
        deferred_paused = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-B",
            message_id="m-defer-paused",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
        )
        # Capture created_at so we can pin ordering — this row must
        # be OLDER than the eligible row below for the pre-check
        # bug to manifest. SQLite returns the column as a string;
        # parse it so we can do timedelta arithmetic.
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT created_at FROM task WHERE id = :id"),
                {"id": deferred_paused.id},
            ).fetchone()
            paused_created_at_raw = row.created_at
            paused_created_at = (
                datetime.fromisoformat(paused_created_at_raw)
                if isinstance(paused_created_at_raw, str)
                else paused_created_at_raw
            )

        # 3. Younger non-deferred task on a RUNNING instance (inst-C).
        self._insert_instance(engine, "inst-C", "project-starve")
        eligible_nd = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-C",
            message_id="m-eligible-nd",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET created_at = :created_at "
                    "WHERE id = :id"
                ),
                {
                    "created_at": paused_created_at + timedelta(seconds=10),
                    "id": eligible_nd.id,
                },
            )

        # Pre-fix: claim_pending_task returns None (defer gate holds
        # back the deferred paused task, starving the eligible one).
        # Post-fix: claim_pending_task picks the eligible non-deferred
        # task — the pause gate excludes the deferred paused task from
        # the inner SELECT, and the defer gate evaluates together with
        # the pause gate for any remaining deferred candidates.
        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None, (
            "claim_pending_task returned None — younger non-deferred "
            "eligible task was starved by the older deferred paused "
            "task's defer gate."
        )
        assert claimed.id == eligible_nd.id
        assert claimed.status == TaskStatus.RUNNING.value
        assert claimed.worker_id == "worker-1"

        # The deferred paused task is still PENDING (untouched — it is
        # ineligible due to the pause gate, not the defer gate).
        db_deferred = repository.get(deferred_paused.id)
        assert db_deferred is not None
        assert db_deferred.status == TaskStatus.PENDING.value

        # The original RUNNING non-deferred task is still RUNNING
        # (gate-counted but not disturbed).
        db_running = repository.get(running_nd.id)
        assert db_running is not None
        assert db_running.status == TaskStatus.RUNNING.value

    def test_defer_gate_allows_when_candidate_instance_has_no_project(
        self, repository, engine
    ):
        """No-project-context fallback: when the candidate's instance
        has no matching ``instances`` row (e.g. legacy), the LEFT JOIN
        yields ``project_id = NULL`` and the gate defaults to "allow".
        This mirrors the COALESCE fallback pattern used by the
        cross-system guard elsewhere in the method."""
        # Insert a RUNNING non-deferred task WITHOUT an instance row
        # (project context unknown). This deliberately puts the
        # non-deferred task in a project-unknown state.
        non_deferred = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-no-row",
            message_id="m-no-row",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET status = :running, "
                    "worker_id = :worker, started_at = :now, "
                    "last_heartbeat_at = :now WHERE id = :id"
                ),
                {
                    "running": TaskStatus.RUNNING.value,
                    "worker": "pre-existing-worker",
                    "now": datetime.now(timezone.utc),
                    "id": non_deferred.id,
                },
            )

        # Insert a deferred task whose instance also has no row →
        # project_id NULL on the LEFT JOIN.
        deferred = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-no-project",
            message_id="m-no-proj",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
        )

        # Gate defaults to "allow" because the candidate's project_id
        # is NULL — the deferred task is claimable.
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == deferred.id


class TestTaskCompletion:
    """Tests for task completion."""

    def test_complete_task(self, repository, sample_task_data):
        """Test completing a task with result."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        result_data = {"success": True, "output": "test output"}
        completed = repository.complete_task(created.id, result_data)

        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED.value
        assert completed.completed_at is not None
        assert completed.result is not None
        assert json.loads(completed.result) == result_data

    def test_complete_task_not_found(self, repository):
        """Test completing non-existent task."""
        result = repository.complete_task(99999, {"success": True})
        assert result is None

    def test_complete_already_completed_task(self, repository, sample_task_data):
        """Second complete_task on an already-completed task returns None.

        Pattern A atomicity: complete_task guards on status='running'
        (PostgreSQL EvalPlanQual recheck, SQLite write serialization).
        A task already in a terminal status (COMPLETED/FAILED/CANCELLED)
        cannot be re-completed — the second call returns None to signal
        "already transitioned by another worker". The original result is
        preserved (not overwritten).
        """
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")
        repository.complete_task(created.id, {"first": "result"})

        second = repository.complete_task(created.id, {"second": "result"})

        assert second is None

        # Verify original result is preserved (no clobber).
        row = repository.get(created.id)
        assert row.status == TaskStatus.COMPLETED.value
        assert json.loads(row.result) == {"first": "result"}

    def test_complete_task_returns_none_for_cancelled(self, repository, sample_task_data):
        """complete_task on a CANCELLED task returns None (terminal status guard)."""
        task = _create_task_with_status(
            repository.engine,
            instance_id="instance-cancelled",
            message_id="msg-cancelled",
            status=TaskStatus.CANCELLED.value,
        )

        result = repository.complete_task(task.id, {"late": "result"})

        assert result is None

    def test_complete_task_returns_none_for_failed(self, repository, sample_task_data):
        """complete_task on a FAILED task returns None (terminal status guard)."""
        task = _create_task_with_status(
            repository.engine,
            instance_id="instance-failed",
            message_id="msg-failed",
            status=TaskStatus.FAILED.value,
        )

        result = repository.complete_task(task.id, {"late": "result"})

        assert result is None

    def test_complete_task_returns_none_for_pending(self, repository, sample_task_data):
        """complete_task on a PENDING task returns None (not RUNNING).

        Only a worker that successfully claimed the task (status=running)
        may complete it. A completion attempt on a PENDING task is
        rejected — the caller forgot to claim first.
        """
        created = repository.create(**sample_task_data)
        # Don't claim — task is still PENDING.

        result = repository.complete_task(created.id, {"early": "result"})

        assert result is None


class TestTaskFailure:
    """Tests for task failure."""

    def test_fail_task(self, repository, sample_task_data):
        """Test failing a task with error."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        failed = repository.fail_task(created.id, "Test error message")

        assert failed is not None
        assert failed.status == TaskStatus.FAILED.value
        assert failed.error == "Test error message"
        assert failed.completed_at is not None

    def test_fail_task_not_found(self, repository):
        """Test failing non-existent task."""
        result = repository.fail_task(99999, "Error")
        assert result is None

    def test_fail_task_preserves_other_fields(self, repository, sample_task_data):
        """Test that failing preserves other task fields."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        failed = repository.fail_task(created.id, "Error message")

        assert failed.id == created.id
        assert failed.task_type == created.task_type
        assert failed.instance_id == created.instance_id
        assert failed.message_id == created.message_id
        assert failed.worker_id == "worker-1"

    def test_fail_task_returns_none_for_completed(self, repository, sample_task_data):
        """fail_task on a COMPLETED task returns None (terminal status guard)."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")
        repository.complete_task(created.id, {"ok": True})

        result = repository.fail_task(created.id, "late failure")

        assert result is None
        # Original status preserved.
        row = repository.get(created.id)
        assert row.status == TaskStatus.COMPLETED.value

    def test_fail_task_returns_none_for_cancelled(self, repository, sample_task_data):
        """fail_task on a CANCELLED task returns None (terminal status guard)."""
        task = _create_task_with_status(
            repository.engine,
            instance_id="instance-fail-cancelled",
            message_id="msg-fail-cancelled",
            status=TaskStatus.CANCELLED.value,
        )

        result = repository.fail_task(task.id, "late failure")

        assert result is None

    def test_fail_task_returns_none_for_already_failed(self, repository, sample_task_data):
        """Second fail_task on an already-FAILED task returns None (status guard).

        Replaces the pre-fix semantics where a second fail_task would
        silently overwrite the original error message. Pattern A makes
        the second call a no-op so the original failure record survives.
        """
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")
        repository.fail_task(created.id, "first error")

        second = repository.fail_task(created.id, "second error")

        assert second is None
        # Original error preserved (no clobber).
        row = repository.get(created.id)
        assert row.status == TaskStatus.FAILED.value
        assert row.error == "first error"

    def test_fail_task_returns_none_for_pending(self, repository, sample_task_data):
        """fail_task on a PENDING task returns None (not RUNNING).

        A worker that hasn't claimed the task can't fail it either —
        status guard prevents a stray failure from a non-claimer.
        """
        created = repository.create(**sample_task_data)
        # Don't claim — task is still PENDING.

        result = repository.fail_task(created.id, "early failure")

        assert result is None


class TestCancelTaskStatusGuard:
    """Pattern A status-guard tests for cancel_task.

    cancel_task allows transition from RUNNING or PENDING (both
    non-terminal). COMPLETED, FAILED, and CANCELLED rows must yield
    None — the row is already in a terminal status and a duplicate
    cancel would clobber the original terminal record.
    """

    def test_cancel_task_returns_none_for_completed(self, repository, sample_task_data):
        """cancel_task on COMPLETED returns None."""
        task = _create_task_with_status(
            repository.engine,
            instance_id="instance-cancel-completed",
            message_id="msg-cancel-completed",
            status=TaskStatus.COMPLETED.value,
        )

        result = repository.cancel_task(task.id, reason="late")

        assert result is None
        row = repository.get(task.id)
        assert row.status == TaskStatus.COMPLETED.value

    def test_cancel_task_returns_none_for_failed(self, repository, sample_task_data):
        """cancel_task on FAILED returns None."""
        task = _create_task_with_status(
            repository.engine,
            instance_id="instance-cancel-failed",
            message_id="msg-cancel-failed",
            status=TaskStatus.FAILED.value,
        )

        result = repository.cancel_task(task.id, reason="late")

        assert result is None
        row = repository.get(task.id)
        assert row.status == TaskStatus.FAILED.value

    def test_cancel_task_cancels_running(self, repository, sample_task_data):
        """cancel_task on RUNNING succeeds (sets CANCELLED + error + completed_at)."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")
        assert repository.get(created.id).status == TaskStatus.RUNNING.value

        result = repository.cancel_task(created.id, reason="shutdown")

        assert result is not None
        assert result.status == TaskStatus.CANCELLED.value
        assert result.error == "Task cancelled: shutdown"
        assert result.cancel_requested is True
        assert result.cancel_requested_at is not None
        assert result.completed_at is not None

    def test_cancel_task_cancels_pending(self, repository, sample_task_data):
        """cancel_task on PENDING succeeds."""
        created = repository.create(**sample_task_data)
        # Don't claim — stays PENDING.

        result = repository.cancel_task(created.id, reason="rejected")

        assert result is not None
        assert result.status == TaskStatus.CANCELLED.value


class TestTaskRecovery:
    """Tests for task recovery (stale task handling)."""

    def test_find_stale_running_tasks_empty(self, repository, sample_task_data):
        """Test finding stale tasks when none exist."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        stale = repository.find_stale_running_tasks(threshold_minutes=15)

        assert len(stale) == 0

    def test_reset_stale_tasks_empty(self, repository, sample_task_data):
        """Test resetting stale tasks when none are stale."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        count = repository.reset_stale_tasks(threshold_minutes=15)

        assert count == 0

    def test_reset_stale_tasks_resets_tasks(self, repository, sample_task_data):
        """Test that reset_stale_tasks resets running tasks to pending."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")

        task = repository.get(created.id)
        assert task.status == TaskStatus.RUNNING.value

        count = repository.reset_stale_tasks(threshold_minutes=0)

        assert count == 1

        task = repository.get(created.id)
        assert task.status == TaskStatus.PENDING.value
        assert task.worker_id is None
        assert task.started_at is None


class TestTaskStats:
    """Tests for task statistics."""

    def test_get_pending_count(self, repository):
        """Test getting pending task count."""
        assert repository.get_pending_count() == 0

        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")
        repository.create(task_type=TaskType.CLEANUP.value, instance_id="i3")

        assert repository.get_pending_count() == 3

    def test_count_by_status(self, repository):
        """Test counting tasks by status."""
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i2")

        repository.claim_pending_task(worker_id="worker-1")

        counts = repository.count_by_status()

        assert counts["pending"] == 1
        assert counts["running"] == 1
        assert counts["completed"] == 0
        assert counts["failed"] == 0

    def test_count_by_status_after_completion(self, repository):
        """Test counts update after task completion."""
        task = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="i1")
        repository.claim_pending_task(worker_id="worker-1")
        repository.complete_task(task.id, {"result": "ok"})

        counts = repository.count_by_status()

        assert counts["pending"] == 0
        assert counts["running"] == 0
        assert counts["completed"] == 1


class TestTaskDeletion:
    """Tests for task deletion."""

    def test_delete_task(self, repository, sample_task_data):
        """Test deleting a task."""
        task = repository.create(**sample_task_data)
        task_id = task.id

        result = repository.delete(task_id)
        assert result is True

        assert repository.get(task_id) is None

    def test_delete_task_not_found(self, repository):
        """Test deleting non-existent task."""
        result = repository.delete(99999)
        assert result is False

    def test_delete_by_instance(self, repository):
        """Test deleting all tasks for an instance."""
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="instance-1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="instance-1")
        repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="instance-2")

        count = repository.delete_by_instance("instance-1")
        assert count == 2

        remaining = repository.get_by_instance("instance-2")
        assert len(remaining) == 1

        assert repository.get_pending_count() == 1
