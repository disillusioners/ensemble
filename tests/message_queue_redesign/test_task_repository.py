"""Tests for TaskRepository."""

import pytest
import json

from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


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
        from daemon.repositories.job_queue.models import JobItem, JobStatus
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
                waiting_for=0,
            ))
            session.add(JobItem(
                job_id="job-J1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",
                status=JobStatus.PROCESSING.value,
                job_type="message",
                instance_id="inst-J",
                created_at=now,
                started_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A pending task for the same instance must NOT be claimable
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-J", message_id="m1")
        assert repository.claim_pending_task(worker_id="worker-1") is None

        # has_pending_tasks_blocked_by_busy_instance should also report True
        assert repository.has_pending_tasks_blocked_by_busy_instance() is True

        # Complete the job → t1 becomes claimable
        with SQLModelSession(engine) as session:
            job = session.get(JobItem, "job-J1")
            job.status = JobStatus.COMPLETED.value
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
        from daemon.repositories.job_queue.models import JobItem, JobStatus
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
                waiting_for=1,
            ))
            session.add(JobItem(
                job_id="job-W1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",
                status=JobStatus.PROCESSING.value,
                job_type="message",
                instance_id="inst-W",
                created_at=now,
                started_at=now,
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
        from daemon.repositories.job_queue.models import JobItem, JobStatus

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(JobItem(
                job_id="job-X1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",
                status=JobStatus.PROCESSING.value,
                job_type="message",
                instance_id="inst-X-no-row",
                created_at=now,
                started_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-X-no-row", message_id="m1")
        # No instance row → COALESCE makes waiting_for=0, status NULL → job
        # IS treated as actively processing → task is blocked.
        assert repository.claim_pending_task(worker_id="worker-1") is None

    def test_claim_unaffected_by_non_message_job_types(self, repository, engine):
        """Cross-system guard only blocks on MESSAGE jobs, not other job types
        (cleanup, send_report, etc.) that don't touch the langgraph thread."""
        from sqlmodel import Session as SQLModelSession
        from datetime import datetime, timezone
        from daemon.repositories.job_queue.models import JobItem, JobStatus

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(JobItem(
                job_id="job-K1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="cleanup",
                source="system",
                status=JobStatus.PROCESSING.value,
                job_type="cleanup",
                instance_id="inst-K",
                created_at=now,
                started_at=now,
                priority=0,
                retry_count=0,
            ))
            session.commit()

        # A pending task for inst-K SHOULD be claimable because the active
        # job is cleanup, not message — no langgraph thread contention.
        t1 = repository.create(task_type=TaskType.PROCESS_MESSAGE.value, instance_id="inst-K", message_id="m1")
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == t1.id

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
        from daemon.repositories.job_queue.models import JobItem, JobStatus

        now = datetime.now(timezone.utc).isoformat()
        with SQLModelSession(engine) as session:
            session.add(JobItem(
                job_id="job-L1",
                agent_id="leader",
                agent_dir="agents/leader",
                message="hi",
                source="api",
                status=JobStatus.PROCESSING.value,
                job_type="message",
                instance_id="inst-L",
                created_at=now,
                started_at=now,
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
        """Test completing an already completed task still works."""
        created = repository.create(**sample_task_data)
        repository.claim_pending_task(worker_id="worker-1")
        repository.complete_task(created.id, {"first": "result"})

        second = repository.complete_task(created.id, {"second": "result"})

        assert second is not None
        assert second.status == TaskStatus.COMPLETED.value


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
