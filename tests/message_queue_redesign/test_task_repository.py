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
    }.get(status, status)  # Default to status (matches the production _LEGACY_TO_ADMISSION semantics — see daemon/repositories/job_queue/repository.py)


def _create_task_with_status(
    engine,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    instance_id: str = "test-instance",
    message_id: str = "test-message",
    status: str = TaskStatus.PENDING.value,
    is_deferred: bool = False,
    is_background: bool = False,
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

    The ``is_background`` parameter (defer-leak bug fix, 2026-07-23)
    mirrors the pattern: lets background-queue tests insert
    ``is_background=True`` task rows directly. Without this the
    helper would silently default to ``is_background=False`` and the
    background-gate regression test could not set up a candidate
    background task.
    """
    created_at = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, created_at, cancel_requested,
                                  retry_scheduled, work_id, is_deferred,
                                  is_background)
                VALUES (:task_type, :instance_id, :message_id, :status,
                        :retry_count, :created_at, :cancel_requested,
                        :retry_scheduled, :work_id, :is_deferred,
                        :is_background)
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
                "is_background": is_background,
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
        job carrying a stamped ``message_id`` must NOT be claimed concurrently.
        This prevents the langgraph checkpoint race where the task forks from
        a stale state and shadows the AIMessage produced by the job (the
        "Done! 👋 lost" bug).

        Phase 3 P1 fix (2026-06-30): the carve-out was made NULL-safe. A
        JobItem without ``message_id`` (legacy / dispatch-only case) no
        longer blocks its own instance's task — the
        ``json_extract(metadata, '$.message_id') IS NOT NULL`` predicate
        must hold before the matching-Task carve-out can fire. The guard
        STILL fires for stamped JobItems because the matching Task row
        exists in PENDING status and the carve-out releases the block;
        the test exercises the stamped-blocked path so we don't regress
        the carve-out semantics while validating the NULL-safe
        exemption is a NEW exemption, not a global unblock.
        """
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        # Insert a PROCESSING MESSAGE job for inst-J with an instance in
        # running status (waiting_for=0) — the job is actively driving
        # graph.astream and must block the task. The job carries a
        # stamped ``message_id`` so the P1 NULL-safe guard releases
        # the matching-Task carve-out (NOT EXISTS returns TRUE → block
        # fires). Without message_id stamped, the carve-out would be
        # skipped and the task would be claimable.
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
                # Stamped message_id is REQUIRED for the cross-system
                # guard to fire (Phase 3 P1 NULL-safe fix). Without
                # this, the JobItem would NOT block its own instance's
                # task — see ``test_claim_unaffected_by_null_message_id_job``.
                job_metadata={"message_id": "m1"},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A pending task with the SAME message_id must NOT be claimable
        # — the carve-out releases the guard when a matching Task row
        # exists, but a different instance's task (with a non-matching
        # message_id) is still blocked.
        t_other = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-J",
            message_id="m-other",
        )
        assert repository.claim_pending_task(worker_id="worker-1") is None

        # has_pending_tasks_blocked_by_busy_instance should also report True
        assert repository.has_pending_tasks_blocked_by_busy_instance() is True

        # Complete the job → the other-instance task becomes claimable.
        # Phase 2 dual-write contract: every status mutation must
        # co-move ``admission_state`` in the same transaction.
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
        assert claimed.id == t_other.id

    def test_claim_unaffected_by_null_message_id_job(self, repository, engine):
        """Phase 3 P1 fix (2026-06-30): a JobItem with NULL message_id
        in its metadata does NOT block its own instance's task.

        Before the fix, ``json_extract(NULL/'{}', '$.message_id')``
        returned NULL and the matching-Task carve-out compared
        ``t.message_id = NULL`` (UNKNOWN), so ``NOT EXISTS`` defaulted
        to TRUE and the JobItem spuriously blocked its own task —
        self-deadlock. The P1 fix added an
        ``IS NOT NULL`` requirement so a NULL/empty JobItem metadata
        is treated as a non-blocker (the legacy dual-path /
        dispatch-only case).

        This is the inverse of ``test_claim_skips_when_message_job_processing_for_instance``
        (which still pins the stamped-message-id blocking case). Both
        tests pin a contract the production carve-out must keep.
        """
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        # PROCESSING MESSAGE job with NULL/empty job_metadata. The
        # carved-out guard cannot fire because ``message_id`` is NULL,
        # so the task MUST be claimable.
        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id="inst-NULL-MID",
                agent_id="leader",
                agent_dir="agents/leader",
                status="running",
            ))
            session.add(JobItem(
                job_id="job-NULL-MID",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",

                admission_state=status_to_admission(AdmissionState.ACTIVE.value),
                job_type="message",
                instance_id="inst-NULL-MID",
                # No message_id — the NULL-safe carve-out does NOT
                # fire for this row, so it cannot block its own
                # instance's task.
                job_metadata={},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A pending task for the same instance MUST be claimable —
        # the JobItem without stamped message_id is the legacy /
        # dispatch-only case the P1 fix accommodates.
        t1 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-NULL-MID",
            message_id="m1",
        )
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "Phase 3 P1 NULL-safe fix: a JobItem with no message_id "
            "must NOT block its own instance's task. Got None — the "
            "self-deadlock regressed."
        )
        assert claimed.id == t1.id

        # The busy-instance probe must report False: the JobItem has
        # no message_id so the matching-Task carve-out cannot fire,
        # so the instance is treated as not actively blocked.
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

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
        """Phase 3 P1 fix (2026-06-30) update: defensive contract test
        for the NULL-safe cross-system guard.

        If the job's instance_id has no matching ``instances`` row
        (e.g. mid-creation), COALESCE(waiting_for, 0) = 0 falls
        through and the missing-status NULL check treats it as not
        WAITING_CHILDREN. Pre-P1, this case ALSO meant the JobItem
        blocked its own instance's task via the
        ``t.message_id = NULL`` UNKNOWN comparison. Post-P1, the
        NULL-safe carve-out requires a stamped message_id before
        firing, so a JobItem without ``job_metadata->>'message_id'``
        does NOT block.

        This test pins the post-P1 contract: a JobItem with empty
        ``job_metadata`` AND no instance row MUST NOT block its own
        instance's task (the carve-out is inert without a message_id).
        The pre-P1 self-deadlock regression is exactly what the new
        ``test_claim_unaffected_by_null_message_id_job`` covers; the
        "no instance row" wrinkle is folded in here as a defensive
        assertion on the JOIN's COALESCE fallback.
        """
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, AdmissionState

        now = datetime.now(timezone.utc).isoformat()
        # JobItem with no matching instance row AND no stamped
        # message_id. Pre-P1 this would self-deadlock; post-P1 the
        # task is claimable.
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
                # Empty job_metadata — no message_id stamped. The
                # P1 NULL-safe carve-out does NOT fire here, so the
                # JobItem does NOT block its own instance's task.
                job_metadata={},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # Post-P1: the task IS claimable. The no-instance-row edge
        # case preserves the NULL-safe carve-out's exemption: a
        # JobItem without message_id is not a blocker.
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-X-no-row", message_id="m1")
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "Phase 3 P1 fix: a JobItem without stamped message_id must "
            "not block its own instance's task (NULL-safe carve-out). "
            "The no-instance-row edge case does not regress this contract."
        )
        assert claimed.id == t1.id

    def test_claim_unaffected_by_non_message_job_types(self, repository, engine):
        """Phase 2.5 (D13) pin: the cross-system guard in
        ``claim_pending_task`` no longer filters ``j.job_type =
        'message'`` — it now blocks on ANY processing ``JobItem``
        for the instance, regardless of job type. After D13, all
        ``JobItem`` rows are TASK-type (message-type jobs are no
        longer created), so the previous "only MESSAGE jobs block"
        carve-out is no longer relevant in the post-D13 world.

        Phase 3 P1 update (2026-06-30): the carve-out was made
        NULL-safe. A CLEANUP ``JobItem`` with empty
        ``job_metadata`` (no ``message_id``) does NOT block its own
        instance's task — the matching-Task carve-out cannot fire
        without a stamped message_id, and the NULL-safe guard treats
        the JobItem as a non-blocker.

        This test pins the new behaviour: a CLEANUP processing
        job with empty ``job_metadata`` (no message_id) does NOT
        block the task claim, because the NULL-safe carve-out
        exemption applies to non-MESSAGE jobs just as it does to
        MESSAGE ones. The matching-Task carve-out's premise (a
        stamped message_id means the dispatcher has admitted the
        task) still holds for CLEANUP jobs IF their metadata carries
        a message_id; this test focuses on the legacy / dispatch-only
        no-message-id case.
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
                # Empty job_metadata — no message_id stamped. Per
                # the Phase 3 P1 NULL-safe fix, this JobItem does
                # NOT block its own instance's task (the carve-out
                # cannot fire without a stamped message_id).
                job_metadata={},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A pending task for inst-K MUST be claimable — the
        # processing CLEANUP job has no stamped message_id, so the
        # NULL-safe carve-out exemption applies (Phase 3 P1 fix).
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-K", message_id="m1")
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            f"Phase 3 P1 NULL-safe fix: a non-MESSAGE JobItem without "
            f"stamped message_id must NOT block its own instance's task. "
            f"Got: {claimed}"
        )
        assert claimed.id == t1.id
        # The busy-instance probe also reports False (same reasoning).
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

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

    def test_claim_wave_spawn_two_children_both_claimable(self, repository, engine):
        """Wave spawn: leader spawns 2 children in one LLM turn; both
        initial-message Tasks + their JobItem mirrors (stuck-queued on
        PG) must be claimable concurrently.

        Production scenario (E2E test_wave_spawn_with_defer_queue,
        regressed in 2026-07-06): the leader's wave message creates
        two child instances via spawn_instance x2, then two initial
        messages via send_message x2 — all inside ONE LLM turn. The
        two child Tasks land in PENDING with two queued JobItem mirrors
        (one per child, distinct instance_ids). The pre-fix carve-out
        could falsely identify the queued mirrors as cross-system
        blockers for the second child's Task under specific
        message_id bookkeeping — the regression surfaced as the second
        child never completing.

        Bifurcated carve-out (2026-07-06): for ``queued`` JobItem mirrors
        (the stuck-mirror case F1 fixed), ANY matching Task (regardless
        of status) releases the guard. Both child Tasks carry matching
        message_ids on their own mirrors → both are claimable in FIFO
        order, one worker per child.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        from daemon.repositories.instance.models import Instance

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            # Child 1 — instance + queued mirror with stamped message_id
            session.add(Instance(
                instance_id="wave-child-1",
                agent_id="developer",
                agent_dir="agents/developer",
                status="running",
            ))
            session.add(JobItem(
                job_id="wave-ji-1",
                agent_id="developer",
                agent_dir="agents/developer",
                message="m1",
                source="api",
                # Stuck-queued mirror (PostgreSQL
                # trg_job_queue_items_active_lock_guard rejects
                # eager activation for MESSAGE-type rows).
                admission_state=status_to_admission(AdmissionState.QUEUED.value),
                job_type="message",
                instance_id="wave-child-1",
                job_metadata={"message_id": "wave-msg-1"},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            # Child 2 — independent instance + queued mirror with
            # distinct message_id (different message_id stamps;
            # cross-system guard must not link them via JobItem).
            session.add(Instance(
                instance_id="wave-child-2",
                agent_id="developer",
                agent_dir="agents/developer",
                status="running",
            ))
            session.add(JobItem(
                job_id="wave-ji-2",
                agent_id="developer",
                agent_dir="agents/developer",
                message="m2",
                source="api",
                admission_state=status_to_admission(AdmissionState.QUEUED.value),
                job_type="message",
                instance_id="wave-child-2",
                job_metadata={"message_id": "wave-msg-2"},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # Two PENDING Tasks, one per child instance.
        t1 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="wave-child-1",
            message_id="wave-msg-1",
        )
        t2 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="wave-child-2",
            message_id="wave-msg-2",
        )

        # Both must be claimable (FIFO order by created_at; the test
        # creates t1 first so it claims first).
        c1 = repository.claim_pending_task(worker_id="wave-w1")
        assert c1 is not None, (
            "Wave spawn regression: child 1's Task was not claimable. "
            "The bifurcated carve-out must release the guard for the "
            "queued mirror's matching Task."
        )
        assert c1.id == t1.id
        assert c1.instance_id == "wave-child-1"

        c2 = repository.claim_pending_task(worker_id="wave-w2")
        assert c2 is not None, (
            "Wave spawn regression: child 2's Task was not claimable "
            "after child 1 was claimed. The bifurcated carve-out must "
            "release the guard independently per-instance."
        )
        assert c2.id == t2.id
        assert c2.instance_id == "wave-child-2"

        # No more pending — the busy-instance probe also reports False
        # because both mirrors are now matched by RUNNING Tasks on
        # their respective instances.
        c3 = repository.claim_pending_task(worker_id="wave-w3")
        assert c3 is None
        assert repository.has_pending_tasks_blocked_by_busy_instance() is False

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
        """Phase 3 P1 update (2026-06-30): the contract pinned by this
        test changed when the cross-system guard was made NULL-safe.

        Pre-P1: a JobItem with empty ``job_metadata`` blocked its own
        instance's task because ``json_extract(NULL/'{}', '$.message_id')``
        returned NULL and the matching-Task carve-out compared
        ``t.message_id = NULL`` (UNKNOWN), so ``NOT EXISTS`` defaulted
        to TRUE (blocker fires). The legacy test asserted this as
        "defensive: empty metadata → block".

        Post-P1: the carve-out requires
        ``json_extract(metadata, '$.message_id') IS NOT NULL`` before
        firing. A JobItem with empty ``job_metadata`` (json_extract
        returns NULL) is therefore NOT a blocker — the
        NULL-safe exemption applies. This test now pins the post-P1
        contract: empty ``job_metadata`` means the JobItem is treated
        as a non-blocker (the legacy / dispatch-only case). The
        ``NULL-extraction fallback`` is still pinned — just with the
        new semantics that "no message_id" means "no block".
        """
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
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
                # Empty job_metadata — same as
                # ``test_claim_skips_when_message_job_processing_for_instance``
                # but for a fresh instance so the test is self-contained.
                # Post-P1: this JobItem does NOT block its own
                # instance's task (NULL-safe carve-out).
                job_metadata={},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # Post-P1: the task IS claimable. Empty job_metadata means
        # json_extract returns NULL → carve-out does not fire →
        # the JobItem is a non-blocker. This is the inverse of the
        # pre-P1 behaviour that this test originally pinned.
        t1 = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-EMPTY-1",
            message_id="anything",
        )
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "Phase 3 P1 NULL-safe fix: empty job_metadata must NOT "
            "block the task claim. Got None — the self-deadlock regressed."
        )
        assert claimed.id == t1.id


# ──────────────────────────────────────────────────────────────────────────────
# FIFO concurrency bypass fix (2026-07-26)
# ──────────────────────────────────────────────────────────────────────────────


class TestFifoConcurrencyBypass:
    """FIFO ``concurrency_limit`` bypass via Task-claim path (Phase 5
    Option B, 2026-07-26).

    The pre-fix behavior: when a FIFO queue denies the slot for a
    message's JobItem (``start_job_atomic_with_lock`` returns None and
    the JobItem stays in ``admission_state='queued'``), the Task-claim
    path was queue-concurrency-blind. The cross-system guard's
    ``_admitted_task_carve_out_sql`` Branch 1 released the guard for
    any queued JobItem with a matching Task row, which let the Task
    race the slot and bypass ``concurrency_limit``.

    The fix has two parts:
      * Part 1 — A new "genuinely orphaned mirror" exclusion inside
        the cross-system guard's WHERE clause (see the ``AND NOT``
        block at the end of the blocking set in
        ``claim_pending_task``). It excludes ``queued`` JobItems that
        have NO matching Task at all (Task was deleted, or never
        existed because the Task transaction was rolled back). These
        are real orphans — they cannot be coordinating any in-flight
        work, so they must not block the instance. ``Branch 1`` of
        :meth:`_admitted_task_carve_out_sql` was NOT changed; it
        still releases for any ``queued`` JobItem with a matching
        Task (the F1 stuck-mirror case from 2026-07-03).
      * Part 2 — A new queue-awareness guard in
        ``claim_pending_task`` blocks claiming a Task whose linked
        JobItem (``work_id == job_queue_items.job_id``) is in
        ``admission_state='queued'``. The Task is claimable only when
        the linked JobItem is ``active`` (slot held) or has no
        linkage (the legacy / report-task path).

    These tests pin both layers of the fix.
    """

    def _seed_instance(self, engine, instance_id: str, status: str = "running") -> None:
        """Insert a minimal ``instances`` row required for the
        per-instance guards in ``claim_pending_task``."""
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.instance.models import Instance

        with SQLModelSession(engine) as session:
            session.add(Instance(
                instance_id=instance_id,
                agent_id="leader",
                agent_dir="agents/leader",
                status=status,
            ))
            session.commit()

    def _seed_message_job_item(
        self,
        engine,
        *,
        job_id: str,
        instance_id: str,
        message_id: str,
        admission_state: str,
    ) -> None:
        """Insert a ``job_queue_items`` row for a message-type job.

        ``job_id`` is the shared linkage UUID4 — it matches
        ``Task.work_id`` so the queue-awareness guard can correlate
        the Task with its JobItem.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.job_queue.models import JobItem

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(JobItem(
                job_id=job_id,
                agent_id="leader",
                agent_dir="agents/leader",
                message="test message",
                source="api",
                admission_state=admission_state,
                job_type="message",
                instance_id=instance_id,
                job_metadata={"message_id": message_id},
                created_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

    def test_msg1_active_claimable_msg2_queued_blocked(
        self, repository, engine
    ):
        """Test 1 — FIFO concurrency enforcement at Task-claim time.

        Setup: two messages on the same instance.
          * msg1: JobItem ``admission_state='active'`` (slot acquired),
            Task PENDING.
          * msg2: JobItem ``admission_state='queued'`` (slot denied by
            ``start_job_atomic_with_lock``), Task PENDING.

        Expected:
          * msg1's Task is claimable (slot held, Part 2 guard allows
            active linked JobItems).
          * msg2's Task is NOT claimable (slot denied, Part 2 guard
            blocks queued linked JobItems). This is the FIFO bypass
            fix.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.task.models import Task, TaskStatus, TaskType

        self._seed_instance(engine, instance_id="fifo-inst-1")

        # msg1: active JobItem + PENDING Task (shared work_id="fifo-w-1")
        self._seed_message_job_item(
            engine,
            job_id="fifo-w-1",
            instance_id="fifo-inst-1",
            message_id="fifo-msg-1",
            admission_state="active",
        )
        with SQLModelSession(engine) as session:
            session.add(Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="fifo-inst-1",
                work_id="fifo-w-1",
                message_id="fifo-msg-1",
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        # msg2: queued JobItem + PENDING Task (shared work_id="fifo-w-2")
        self._seed_message_job_item(
            engine,
            job_id="fifo-w-2",
            instance_id="fifo-inst-1",
            message_id="fifo-msg-2",
            admission_state="queued",
        )
        with SQLModelSession(engine) as session:
            session.add(Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="fifo-inst-1",
                work_id="fifo-w-2",
                message_id="fifo-msg-2",
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        # Claim should pick msg1 (active JobItem → Part 2 guard allows).
        claimed_1 = repository.claim_pending_task(worker_id="fifo-w-1")
        assert claimed_1 is not None, (
            "msg1's Task must be claimable: its JobItem holds the slot "
            "(admission_state='active'). The Part 2 queue-awareness "
            "guard only blocks queued linked JobItems."
        )
        assert claimed_1.work_id == "fifo-w-1"

        # Second claim must NOT pick msg2: its JobItem is queued
        # (slot denied) → Part 2 guard blocks.
        claimed_2 = repository.claim_pending_task(worker_id="fifo-w-2")
        assert claimed_2 is None, (
            "FIFO concurrency bypass regression: msg2's Task was "
            "claimable while its JobItem was queued (slot denied by "
            "start_job_atomic_with_lock). The Part 2 queue-awareness "
            "guard must block queued linked JobItems."
        )

    def test_msg2_claimable_after_slot_frees(
        self, repository, engine
    ):
        """Test 2 — FIFO recovery: once msg1 finishes and msg2's
        JobItem transitions to ``active``, msg2's Task becomes
        claimable.

        SCOPE: this is a **repository-state test**, not an
        end-to-end dispatch test. It applies three state
        transitions manually in a single block (msg1 Task →
        COMPLETED, msg1 JobItem → done, msg2 JobItem → active) and
        then asserts that the predicate accepts msg2's Task under
        the resulting post-transition state. It does NOT verify
        the real ``JobProcessor`` dispatch / observer ordering
        that would produce those transitions in production — the
        observer's finalize sweep is a separate concern tested
        elsewhere (see ``job_feedback_observer`` tests). What this
        test pins is: given the IDEAL post-recovery state
        (sibling terminal, target active, per-instance guard
        cleared), the ``claim_pending_task`` SQL predicate
        correctly releases msg2.

        Setup: two messages on the same instance, msg1 already
        RUNNING (so the per-instance guard would normally block
        msg2). But after simulating msg1's completion (set Task to
        COMPLETED, transition msg2's JobItem from queued to active)
        and clearing the per-instance RUNNING guard, msg2's Task
        must become claimable.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.task.models import Task, TaskStatus, TaskType

        self._seed_instance(engine, instance_id="fifo-inst-2")

        # msg1: active JobItem + RUNNING Task (already claimed)
        self._seed_message_job_item(
            engine,
            job_id="fifo-w-3",
            instance_id="fifo-inst-2",
            message_id="fifo-msg-3",
            admission_state="active",
        )
        with SQLModelSession(engine) as session:
            session.add(Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="fifo-inst-2",
                work_id="fifo-w-3",
                message_id="fifo-msg-3",
                status=TaskStatus.RUNNING.value,
                started_at=datetime.now(timezone.utc),
                worker_id="fifo-worker-1",
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        # msg2: queued JobItem + PENDING Task (slot denied, blocked)
        self._seed_message_job_item(
            engine,
            job_id="fifo-w-4",
            instance_id="fifo-inst-2",
            message_id="fifo-msg-4",
            admission_state="queued",
        )
        with SQLModelSession(engine) as session:
            session.add(Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="fifo-inst-2",
                work_id="fifo-w-4",
                message_id="fifo-msg-4",
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        # Sanity: msg2 is NOT claimable while queued (slot denied).
        assert repository.claim_pending_task(worker_id="fifo-w-sanity") is None, (
            "Pre-transition sanity: msg2's Task must not be claimable "
            "while its JobItem is queued."
        )

        # Simulate msg1 completion: Task → COMPLETED, msg1's
        # JobItem → done (the observer finalizes the mirror), and
        # msg2's JobItem → active (slot now free and claimed by
        # msg2). All three transitions must happen for the FIFO
        # recovery to work: the cross-system guard's Branch 2
        # (``active AND NOT EXISTS(matching Task in pending/running)``)
        # blocks when msg1's JobItem is still active with a
        # COMPLETED Task — that is the stuck-mirror case which is
        # a recovery concern, not a claim-time concern. The
        # observer's finalize sweep is what clears the mirror.
        with SQLModelSession(engine) as session:
            from sqlalchemy import text
            session.execute(
                text(
                    "UPDATE task SET status = :completed, completed_at = :now "
                    "WHERE work_id = :work_id"
                ),
                {
                    "completed": TaskStatus.COMPLETED.value,
                    "now": datetime.now(timezone.utc),
                    "work_id": "fifo-w-3",
                },
            )
            session.execute(
                text(
                    "UPDATE job_queue_items SET admission_state = :done "
                    "WHERE job_id = :job_id"
                ),
                {"done": "done", "job_id": "fifo-w-3"},
            )
            session.execute(
                text(
                    "UPDATE job_queue_items SET admission_state = :active "
                    "WHERE job_id = :job_id"
                ),
                {"active": "active", "job_id": "fifo-w-4"},
            )
            session.commit()

        # Now msg2's Task must be claimable: linked JobItem is
        # active (Part 2 guard allows), per-instance RUNNING guard
        # cleared (msg1 completed), cross-system guard's
        # _admitted_task_carve_out_sql Branch 2 releases (active
        # JobItem with matching PENDING Task).
        claimed = repository.claim_pending_task(worker_id="fifo-w-final")
        assert claimed is not None, (
            "FIFO recovery regression: msg2's Task was not claimable "
            "after msg1 completed and msg2's JobItem transitioned to "
            "active. The Part 2 guard must allow active linked "
            "JobItems, and the cross-system guard's Branch 2 must "
            "release for an active JobItem with a matching PENDING "
            "Task."
        )
        assert claimed.work_id == "fifo-w-4"

    def test_orphaned_queued_jobitem_no_matching_task_recovers(
        self, repository, engine
    ):
        """Test 3 — F1 orphan case preservation.

        A queued JobItem with NO matching Task (truly orphaned
        mirror) must not block the claim. The Task is claimable
        because the mirror cannot be coordinating any in-flight
        work — there is no Task row for it to block.

        This is the "genuinely orphaned mirror" carve-out: the
        F1 case from commit ``386a22be`` (stuck mirror that never
        drove astream because the PG constraint rejected eager
        activation) is now restricted to the truly-orphaned
        sub-case where the Task row was deleted or never existed.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.task.models import Task, TaskStatus, TaskType

        self._seed_instance(engine, instance_id="orphan-inst-1")

        # Orphaned queued JobItem — no matching Task row exists.
        # ``work_id="orphan-w-1"`` is deliberately not used by any
        # Task to simulate the "Task was deleted / never existed"
        # scenario.
        self._seed_message_job_item(
            engine,
            job_id="orphan-w-1",
            instance_id="orphan-inst-1",
            message_id="orphan-msg-1",
            admission_state="queued",
        )

        # A Task for a DIFFERENT message on the SAME instance. The
        # cross-system guard's ``_admitted_task_carve_out_sql`` Branch
        # 1 fires when ``admission_state='queued' AND NOT EXISTS(
        # matching Task)``. The orphan has NO matching Task → Branch
        # 1 is FALSE → the orphan is excluded from the blocking set
        # (the orphan is a non-blocker). The Task below carries a
        # DIFFERENT message_id so it cannot match the orphan's
        # message_id even if Branch 1 evaluated it; the per-instance
        # guards and the per-Task Part 2 guard both allow the claim
        # because the orphan does not block.
        with SQLModelSession(engine) as session:
            session.add(Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="orphan-inst-1",
                work_id="orphan-task-w-1",
                message_id="orphan-msg-2",
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        # The Task must be claimable: the orphaned mirror does not
        # block (no matching Task → carve-out releases), the
        # per-instance RUNNING guard is clear (no RUNNING task),
        # and Part 2 guard allows (the orphan's work_id
        # "orphan-w-1" does NOT match the Task's work_id
        # "orphan-task-w-1").
        claimed = repository.claim_pending_task(worker_id="orphan-w-final")
        assert claimed is not None, (
            "F1 orphan regression: an orphaned queued JobItem "
            "(no matching Task row) must not block a Task with a "
            "non-matching message_id. The cross-system guard's "
            "Branch 1 releases orphans because Branch 1 requires "
            "EXISTS(matching Task) — the orphan has no Task, so "
            "Branch 1 is FALSE and the orphan is excluded from "
            "the blocking set."
        )
        assert claimed.work_id == "orphan-task-w-1"

    def test_queued_mirror_with_completed_matching_task_does_not_block_fresh_task(
        self, repository, engine
    ):
        """F1 regression — the actual F1 scenario, not just the
        "no Task" orphan sub-case.

        Production scenario (F1, 2026-07-03): a message's JobItem
        mirror is stuck in ``admission_state='queued'`` (PG
        ``trg_job_queue_items_active_lock_guard`` rejected the
        eager activation because MESSAGE-type rows have no
        ``job_locks`` row). The Task from the prior cycle has
        already completed (e.g. the LangGraph astream finished
        and the observer marked the Task COMPLETED) but the
        mirror was never finalised — it sits as a queued
        stuck-mirror. A fresh message arrives for the same
        instance; its Task is created with a NEW work_id and
        message_id. The fresh Task MUST be claimable: the
        stuck-mirror is for the prior cycle (different work_id)
        and cannot be coordinating the new message.

        Pre-fix carve-out (Branch 1, 2026-07-03): any queued
        JobItem with a matching Task (regardless of Task
        status) releases the guard. The COMPLETED Task from the
        prior cycle matches the mirror's message_id, so the
        guard releases — but the release is keyed on the
        PRIOR cycle's Task, not the new cycle's. The fresh
        Task (different work_id, different message_id) is
        claimable.

        Why this differs from Test 3: Test 3 covers the
        "no Task at all" sub-case (truly orphaned mirror —
        Task was deleted, never existed). This test covers
        the more common production case: the Task DID exist
        and completed, but the mirror was never finalised.
        The F1 fix from 2026-07-03 must still hold for this
        case after the 2026-07-26 FIFO concurrency fix.

        The 2026-07-26 fix added:
          * a new "no matching Task" orphan-exclusion WHERE
            filter (covered by Test 3), AND
          * the Part 2 queue-awareness guard that blocks
            claimed Tasks whose linked JobItem is queued
            (covered by Test 1).

        Part 2 must NOT block the fresh Task here because
        the fresh Task's work_id does NOT match the queued
        mirror's job_id (different UUIDs) — the queued
        mirror is from a prior cycle and is not the
        fresh Task's linked JobItem.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.task.models import Task, TaskStatus, TaskType

        self._seed_instance(engine, instance_id="f1-inst-1")

        # Prior cycle: queued stuck-mirror + COMPLETED Task sharing
        # the same message_id "f1-prior-msg" and the same work_id
        # "f1-prior-w". The mirror never transitioned to active
        # (PG trigger rejected eager activation for MESSAGE-type
        # rows) so it remains queued even after the Task completed.
        self._seed_message_job_item(
            engine,
            job_id="f1-prior-w",
            instance_id="f1-inst-1",
            message_id="f1-prior-msg",
            admission_state="queued",
        )
        prior_completed = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="f1-inst-1",
            message_id="f1-prior-msg",
            status=TaskStatus.COMPLETED.value,
        )
        # Stamp the prior Task's work_id so the linkage matches
        # the mirror's job_id. ``_create_task_with_status`` mints
        # a fresh work_id; we overwrite it so the prior Task is
        # correctly identified as the mirror's linked Task.
        with SQLModelSession(engine) as session:
            session.execute(
                text("UPDATE task SET work_id = :work_id WHERE id = :id"),
                {"work_id": "f1-prior-w", "id": prior_completed.id},
            )
            session.commit()

        # New cycle: a fresh PENDING Task with a NEW work_id and
        # a NEW message_id. The fresh Task is for a different
        # message on the same instance — the queued stuck-mirror
        # from the prior cycle must NOT block it.
        with SQLModelSession(engine) as session:
            session.add(Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="f1-inst-1",
                work_id="f1-fresh-w",
                message_id="f1-fresh-msg",
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        # The fresh Task MUST be claimable. The stuck-mirror
        # (work_id "f1-prior-w", message_id "f1-prior-msg") is
        # excluded from the blocking set by:
        #   1. the new orphan-exclusion filter — wait, the
        #      stuck-mirror HAS a matching Task (the prior
        #      COMPLETED Task), so the orphan-exclusion does
        #      NOT fire.
        #   2. Branch 1 of the carve-out — a queued JobItem
        #      WITHOUT a matching Task releases; but the
        #      stuck-mirror HAS a matching Task. The branch
        #      fires only when ``NOT EXISTS(matching Task)``,
        #      which is FALSE here. So Branch 1 does NOT
        #      release the stuck-mirror.
        #   3. Branch 2 of the carve-out — needs
        #      admission_state='active', so it does NOT
        #      apply to a queued mirror.
        # So the stuck-mirror IS in the blocking set per the
        # cross-system guard. The fresh Task is unblocked
        # because:
        #   * the per-instance RUNNING guard is clear (the
        #     prior Task is COMPLETED, not RUNNING);
        #   * the Part 2 queue-awareness guard does NOT fire
        #     because the fresh Task's work_id
        #     ("f1-fresh-w") does NOT match any queued
        #     JobItem's job_id — the queued mirror's
        #     job_id is "f1-prior-w", not "f1-fresh-w";
        #   * the cross-system guard's "blocking set" only
        #     blocks the FRESH Task if the fresh Task
        #     matches the stuck-mirror's message_id
        #     ("f1-prior-msg") — but the fresh Task has
        #     message_id "f1-fresh-msg", so the stuck-mirror
        #     does not block the fresh Task.
        claimed = repository.claim_pending_task(worker_id="f1-fresh-w")
        assert claimed is not None, (
            "F1 regression: a fresh Task for the same instance "
            "must be claimable even when a prior-cycle "
            "stuck-mirror JobItem (admission_state='queued') "
            "with a matching COMPLETED Task still exists. The "
            "queued mirror is for the prior cycle (different "
            "work_id, different message_id) and cannot be "
            "coordinating the fresh Task. The Part 2 guard "
            "correctly does NOT fire because the fresh Task's "
            "work_id does not match any queued JobItem's "
            "job_id."
        )
        assert claimed.work_id == "f1-fresh-w"
        assert claimed.message_id == "f1-fresh-msg"

        # And the prior Task remains untouched (COMPLETED, not
        # re-claimed) — the fresh Task is the one the worker
        # pool picks up.
        db_prior = repository.get(prior_completed.id)
        assert db_prior is not None
        assert db_prior.status == TaskStatus.COMPLETED.value
        assert db_prior.work_id == "f1-prior-w"


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


class TestBackgroundQueueGate:
    """Regression tests for the background queue idle gate in
    :meth:`TaskRepository.claim_pending_task`.

    Defer-leak bug fix (2026-07-23): the predicate-based background
    gate (``has_active_non_background_work``) and the predicate-based
    defer gate (``has_active_non_deferred_work``) were already fixed
    by removing ``is_deferred = false`` so defer work IS counted as
    non-background work. The same fix had to be applied to the
    inlined copy of the background gate inside
    :meth:`claim_pending_task` — the predicate and the atomic claim
    path must agree, or background tasks can still be admitted via
    the atomic claim while defer work is active. This test class pins
    the inline-gate behaviour:

    1. A background task cannot be claimed while a defer task is
       PENDING and the defer task's instance is paused (so the
       candidate falls through to the background task).
    2. A background task CAN be claimed when no defer or
       non-background work is active.
    3. A defer task CAN still be claimed normally (verifies the
       defer gate section is unaffected by the fix).
    """

    def _insert_instance(
        self,
        engine,
        instance_id: str,
        project_id: str,
        status: str = "running",
    ) -> None:
        """Insert a minimal Instance row directly via raw SQL.

        Mirrors the helper in :class:`TestDeferQueueGate`. Kept local
        because both gate-test classes want a private insertion
        helper and refactoring to module level is out of scope for
        this regression fix.
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

    def test_background_task_blocked_when_defer_task_active_without_jobitem(
        self, repository, engine
    ):
        """Inline background-gate regression (defer-leak fix,
        2026-07-23).

        Setup: a PENDING defer task on a paused instance + a PENDING
        background task on a running instance. The defer task is
        OLDER (created first) so it is the inner SELECT's first
        candidate, but the pause gate blocks it — the SELECT falls
        through to the background task. The background task is the
        candidate; the background gate must then block it because
        the defer task is non-background work that counts as a
        blocker.

        Pre-fix: the inline SQL had
        ``AND t3.is_deferred = :is_deferred_false`` in the EXISTS,
        so the defer task was invisible to the background gate and
        the background task was wrongly claimed.
        Post-fix: the predicate is just
        ``AND t3.is_background = :is_background_false``; the defer
        task matches and the background task is correctly held back.
        """
        # 1. Older PENDING defer task on a paused instance.
        self._insert_instance(engine, "inst-bg-1", "project-bg", status="paused")
        defer_task = _create_task_with_status(
            engine,
            task_type=TaskType.SEND_REPORT.value,
            instance_id="inst-bg-1",
            message_id="m-bg-defer",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
            is_background=False,
        )

        # 2. Younger PENDING background task on a running instance.
        self._insert_instance(engine, "inst-bg-2", "project-bg", status="running")
        bg_task = _create_task_with_status(
            engine,
            task_type=TaskType.SEND_REPORT.value,
            instance_id="inst-bg-2",
            message_id="m-bg-bg",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=True,
        )

        # Sanity: bg_task is strictly younger so it is reached only
        # after the pause gate filters out defer_task.
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT created_at FROM task WHERE id = :id"),
                {"id": defer_task.id},
            ).fetchone()
            defer_created_at_raw = row.created_at
            defer_created_at = (
                datetime.fromisoformat(defer_created_at_raw)
                if isinstance(defer_created_at_raw, str)
                else defer_created_at_raw
            )
            conn.execute(
                text("UPDATE task SET created_at = :created_at WHERE id = :id"),
                {
                    "created_at": defer_created_at + timedelta(seconds=10),
                    "id": bg_task.id,
                },
            )

        # 3. claim_pending_task must NOT claim the background task.
        claimed = repository.claim_pending_task(worker_id="worker-bg-1")

        assert claimed is None, (
            "claim_pending_task returned a background task while a "
            "defer task is active in the system — the inline "
            "background gate in claim_pending_task still had the "
            "defer-leak bug (its EXISTS subquery filtered out "
            "is_deferred=true rows, so defer tasks were invisible "
            "to the background gate via the atomic claim path)."
        )

        # 4. Both tasks remain untouched.
        db_defer = repository.get(defer_task.id)
        assert db_defer is not None
        assert db_defer.status == TaskStatus.PENDING.value
        db_bg = repository.get(bg_task.id)
        assert db_bg is not None
        assert db_bg.status == TaskStatus.PENDING.value

    def test_background_task_claimable_when_only_background_work_active(
        self, repository, engine
    ):
        """Background task IS claimable when no defer or non-background
        work is active anywhere.

        Sanity check: the background gate must only block the
        background task when there is something non-background to
        wait for. A system with only background tasks pending should
        let one through.
        """
        self._insert_instance(engine, "inst-bg-only", "project-bg-only", status="running")
        bg_task = _create_task_with_status(
            engine,
            task_type=TaskType.SEND_REPORT.value,
            instance_id="inst-bg-only",
            message_id="m-bg-only",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=True,
        )

        claimed = repository.claim_pending_task(worker_id="worker-bg-only")

        assert claimed is not None, (
            "claim_pending_task returned None for a lone background "
            "task — the background gate is wrongly firing when there "
            "is no non-background work to wait for."
        )
        assert claimed.id == bg_task.id
        assert claimed.status == TaskStatus.RUNNING.value

    def test_defer_task_still_claimable_after_background_gate_fix(
        self, repository, engine
    ):
        """Sanity check: the defer gate section is unaffected by the
        background-gate fix.

        A PENDING defer task on a running instance in an idle project
        (no other active work) must still be claimable. If this test
        fails after the fix, the change accidentally touched the
        defer gate SQL.
        """
        self._insert_instance(engine, "inst-defer-still", "project-defer-still", status="running")
        defer_task = _create_task_with_status(
            engine,
            task_type=TaskType.SEND_REPORT.value,
            instance_id="inst-defer-still",
            message_id="m-defer-still",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
            is_background=False,
        )

        claimed = repository.claim_pending_task(worker_id="worker-defer-still")

        assert claimed is not None, (
            "claim_pending_task returned None for a defer task in an "
            "idle project — the defer gate section was accidentally "
            "broken by the background-gate fix."
        )
        assert claimed.id == defer_task.id
        assert claimed.status == TaskStatus.RUNNING.value

    def test_background_gate_blocks_with_only_defer_in_other_project(
        self, repository, engine
    ):
        """Background gate is system-wide: a defer task in project A
        blocks a background candidate in project B.

        This pins the documented scope asymmetry — unlike the defer
        gate (project-scoped), the background gate waits across
        projects. After the defer-leak fix, the inline SQL must
        still recognise a defer task in any project as a blocker
        for the background candidate.
        """
        # Project A: PENDING defer task on a paused instance (so the
        # candidate ordering falls through to the background task
        # without the pause gate trying to claim the defer task).
        self._insert_instance(engine, "inst-A-paused", "project-A", status="paused")
        defer_A = _create_task_with_status(
            engine,
            task_type=TaskType.SEND_REPORT.value,
            instance_id="inst-A-paused",
            message_id="m-A-defer",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
            is_background=False,
        )

        # Project B: PENDING background task on a running instance.
        self._insert_instance(engine, "inst-B-run", "project-B", status="running")
        bg_B = _create_task_with_status(
            engine,
            task_type=TaskType.SEND_REPORT.value,
            instance_id="inst-B-run",
            message_id="m-B-bg",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=True,
        )

        # Force ordering: defer_A older so it is the inner SELECT's
        # first candidate; pause gate filters it out; bg_B becomes
        # the candidate.
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT created_at FROM task WHERE id = :id"),
                {"id": defer_A.id},
            ).fetchone()
            defer_created_at_raw = row.created_at
            defer_created_at = (
                datetime.fromisoformat(defer_created_at_raw)
                if isinstance(defer_created_at_raw, str)
                else defer_created_at_raw
            )
            conn.execute(
                text("UPDATE task SET created_at = :created_at WHERE id = :id"),
                {
                    "created_at": defer_created_at + timedelta(seconds=10),
                    "id": bg_B.id,
                },
            )

        claimed = repository.claim_pending_task(worker_id="worker-cross")

        assert claimed is None, (
            "claim_pending_task returned the background task while a "
            "defer task is PENDING in another project — the inline "
            "background gate's system-wide scope is broken (it "
            "should block cross-project, not just same-project)."
        )
        db_bg = repository.get(bg_B.id)
        assert db_bg is not None
        assert db_bg.status == TaskStatus.PENDING.value


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
