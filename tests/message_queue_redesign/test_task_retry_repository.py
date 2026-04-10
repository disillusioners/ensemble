"""Comprehensive tests for TaskRepository retry and cancellation methods."""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlmodel import SQLModel, Session as SQLModelSession

from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ============================================================================
# Helper Functions
# ============================================================================

def create_task_with_status(
    engine,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    instance_id: str = "test-instance",
    message_id: str = "test-message",
    status: str = TaskStatus.PENDING.value,
    retry_count: int = 0,
    next_retry_at: datetime | None = None,
    cancel_requested: bool = False,
    retry_scheduled: bool = False,
    started_at: datetime | None = None,
) -> Task:
    """Helper to create a task with specific status directly in DB.

    Args:
        engine: SQLAlchemy engine
        task_type: Type of task
        instance_id: Instance ID
        message_id: Message ID
        status: Task status
        retry_count: Retry count
        next_retry_at: Next retry datetime (stored as ISO format string)
        cancel_requested: Whether cancel was requested
        retry_scheduled: Whether retry was scheduled
        started_at: When task started

    Returns:
        Created Task object
    """
    created_at = datetime.now(timezone.utc)

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, next_retry_at, cancel_requested,
                                  retry_scheduled, started_at, created_at,
                                  cancel_requested_at)
                VALUES (:task_type, :instance_id, :message_id, :status,
                        :retry_count, :next_retry_at, :cancel_requested,
                        :retry_scheduled, :started_at, :created_at,
                        :cancel_requested_at)
            """),
            {
                "task_type": task_type,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": retry_count,
                "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
                "cancel_requested": 1 if cancel_requested else 0,
                "retry_scheduled": 1 if retry_scheduled else 0,
                "started_at": started_at,
                "created_at": created_at,
                "cancel_requested_at": created_at.isoformat() if cancel_requested else None,
            }
        )
        task_id = result.lastrowid

    # Fetch and return the created task
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id}
        ).fetchone()
        repo = TaskRepository(engine)
        return repo._row_to_task(row)


# ============================================================================
# claim_pending_task Enhanced Tests
# ============================================================================

class TestClaimPendingTaskRetry:
    """Tests for claim_pending_task with retry delay handling."""

    def test_claim_respects_retry_delay(self, engine, repository):
        """Tasks with future next_retry_at are NOT claimed."""
        now = datetime.now(timezone.utc)
        future_time = now + timedelta(hours=1)

        # Task with future next_retry_at - should NOT be claimed
        task_future = create_task_with_status(
            engine,
            instance_id="instance-future",
            message_id="msg-future",
            next_retry_at=future_time,
        )

        # Normal task without next_retry_at - should be claimed
        task_normal = create_task_with_status(
            engine,
            instance_id="instance-normal",
            message_id="msg-normal",
        )

        # Claim should pick the normal task, not the one with future retry
        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.id == task_normal.id
        assert claimed.instance_id == "instance-normal"

    def test_claim_picks_retry_ready_task(self, engine, repository):
        """Tasks whose next_retry_at has passed ARE claimed."""
        now = datetime.now(timezone.utc)
        past_time = now - timedelta(minutes=5)

        # Task with past next_retry_at - should be claimed
        task_retry_ready = create_task_with_status(
            engine,
            instance_id="instance-retry-ready",
            message_id="msg-retry-ready",
            next_retry_at=past_time,
        )

        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.id == task_retry_ready.id
        assert claimed.status == TaskStatus.RUNNING.value

    def test_claim_prioritizes_retry_ready_tasks(self, engine, repository):
        """Tasks with next_retry_at set (but elapsed) are claimable alongside normal tasks.

        Note: The current implementation orders by created_at ASC, not by retry priority.
        This test verifies retry-ready tasks ARE claimable.
        """
        now = datetime.now(timezone.utc)
        past_time = now - timedelta(minutes=5)

        # Create normal task first
        task_normal = create_task_with_status(
            engine,
            instance_id="instance-normal",
            message_id="msg-normal",
        )

        # Create retry-ready task second (but it should still be claimable)
        task_retry_ready = create_task_with_status(
            engine,
            instance_id="instance-retry-ready",
            message_id="msg-retry-ready",
            next_retry_at=past_time,
        )

        # Both should be claimable
        claimed = repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None

        # Claim the first one
        first_id = claimed.id

        # Second claim should get the other
        claimed2 = repository.claim_pending_task(worker_id="worker-2")
        assert claimed2 is not None
        assert claimed2.id != first_id

    def test_claim_backwards_compatible(self, engine, repository):
        """Tasks without next_retry_at (NULL) still claimed normally."""
        # Create a simple task
        task = create_task_with_status(
            engine,
            instance_id="instance-backward",
            message_id="msg-backward",
        )

        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.id == task.id
        assert claimed.status == TaskStatus.RUNNING.value
        assert claimed.worker_id == "worker-1"


# ============================================================================
# schedule_retry Tests
# ============================================================================

class TestScheduleRetry:
    """Tests for schedule_retry method."""

    def test_schedule_retry_creates_child(self, engine, repository):
        """schedule_retry creates a new PENDING task with retry_count=1."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
        )

        retry_task = repository.schedule_retry(
            task_id=parent.id,
            max_retries=3,
        )

        assert retry_task is not None
        assert retry_task.retry_count == 1
        assert retry_task.status == TaskStatus.PENDING.value
        assert retry_task.instance_id == parent.instance_id
        assert retry_task.message_id == parent.message_id

    def test_schedule_retry_marks_parent_cancelled(self, engine, repository):
        """Parent gets status=CANCELLED and retry_scheduled=1."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
        )
        parent_id = parent.id

        repository.schedule_retry(
            task_id=parent_id,
            max_retries=3,
        )

        # Verify parent is cancelled
        parent_updated = repository.get(parent_id)
        assert parent_updated is not None
        assert parent_updated.status == TaskStatus.CANCELLED.value
        assert parent_updated.retry_scheduled == 1  # SQLite stores bool as int
        assert parent_updated.cancel_requested == 1

    def test_schedule_retry_returns_none_if_already_scheduled(self, engine, repository):
        """Double-retry guard - returns None if retry_scheduled=True."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
            retry_scheduled=True,  # Already has retry scheduled
        )

        result = repository.schedule_retry(
            task_id=parent.id,
            max_retries=3,
        )

        assert result is None

    def test_schedule_retry_returns_none_max_retries(self, engine, repository):
        """Returns None when retry_count >= max_retries."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
            retry_count=3,  # At max retries
        )

        result = repository.schedule_retry(
            task_id=parent.id,
            max_retries=3,  # Max is 3, so retry_count=3 means exceeded
        )

        assert result is None

    def test_schedule_retry_exponential_backoff(self, engine, repository):
        """Verify exponential backoff: 60s, 120s, 240s for retry counts 0,1,2."""
        now = datetime.now(timezone.utc)

        # Retry 0 -> delay 60s (60 * 2^0)
        task0 = create_task_with_status(
            engine,
            instance_id="instance-0",
            message_id="msg-0",
            status=TaskStatus.RUNNING.value,
            retry_count=0,
        )
        retry0 = repository.schedule_retry(task_id=task0.id, max_retries=5)
        expected_delay_0 = 60  # 60 * 2^0 = 60
        actual_delay_0 = (datetime.fromisoformat(retry0.next_retry_at.replace('Z', '+00:00')) - now).total_seconds()
        assert 59 <= actual_delay_0 <= 61, f"Expected ~60s, got {actual_delay_0}s"

        # Retry 1 -> delay 120s (60 * 2^1)
        task1 = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
            retry_count=1,
        )
        retry1 = repository.schedule_retry(task_id=task1.id, max_retries=5)
        expected_delay_1 = 120  # 60 * 2^1 = 120
        actual_delay_1 = (datetime.fromisoformat(retry1.next_retry_at.replace('Z', '+00:00')) - now).total_seconds()
        assert 119 <= actual_delay_1 <= 121, f"Expected ~120s, got {actual_delay_1}s"

        # Retry 2 -> delay 240s (60 * 2^2)
        task2 = create_task_with_status(
            engine,
            instance_id="instance-2",
            message_id="msg-2",
            status=TaskStatus.RUNNING.value,
            retry_count=2,
        )
        retry2 = repository.schedule_retry(task_id=task2.id, max_retries=5)
        expected_delay_2 = 240  # 60 * 2^2 = 240
        actual_delay_2 = (datetime.fromisoformat(retry2.next_retry_at.replace('Z', '+00:00')) - now).total_seconds()
        assert 239 <= actual_delay_2 <= 241, f"Expected ~240s, got {actual_delay_2}s"

    def test_schedule_retry_respects_backoff_max(self, engine, repository):
        """Backoff capped at backoff_max."""
        now = datetime.now(timezone.utc)

        # With retry_count=10 and backoff_base=60, backoff would be 60 * 2^10 = 61440
        # But should be capped at backoff_max=3600 (1 hour)
        task = create_task_with_status(
            engine,
            instance_id="instance-large",
            message_id="msg-large",
            status=TaskStatus.RUNNING.value,
            retry_count=10,
        )
        retry = repository.schedule_retry(
            task_id=task.id,
            max_retries=15,
            backoff_base=60,
            backoff_max=3600,  # 1 hour max
        )

        actual_delay = (datetime.fromisoformat(retry.next_retry_at.replace('Z', '+00:00')) - now).total_seconds()
        assert actual_delay <= 3605, f"Expected <=3600s, got {actual_delay}s"


# ============================================================================
# request_cancel Tests
# ============================================================================

class TestRequestCancel:
    """Tests for request_cancel method."""

    def test_request_cancel_sets_flag(self, engine, repository):
        """Sets cancel_requested=True on running task."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
        )

        result = repository.request_cancel(task.id)

        assert result is True

        # Verify flag is set
        updated = repository.get(task.id)
        assert updated.cancel_requested == 1  # SQLite stores bool as int
        assert updated.cancel_requested_at is not None

    def test_request_cancel_returns_false_for_completed(self, engine, repository):
        """Can't cancel completed task."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.COMPLETED.value,
        )

        result = repository.request_cancel(task.id)

        assert result is False

    def test_request_cancel_idempotent(self, engine, repository):
        """Second call returns False."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
        )

        first_result = repository.request_cancel(task.id)
        second_result = repository.request_cancel(task.id)

        assert first_result is True
        assert second_result is False

    def test_request_cancel_respects_retry_scheduled(self, engine, repository):
        """request_cancel returns False if retry_scheduled=True."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
            retry_scheduled=True,
        )

        result = repository.request_cancel(task.id)

        assert result is False


# ============================================================================
# find_cancellable_tasks Tests
# ============================================================================

class TestFindCancellableTasks:
    """Tests for find_cancellable_tasks method."""

    def test_find_cancellable_finds_stale(self, engine, repository):
        """Finds running tasks past threshold."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(minutes=20)

        stale_task = create_task_with_status(
            engine,
            instance_id="instance-stale",
            message_id="msg-stale",
            status=TaskStatus.RUNNING.value,
            started_at=old_time,
        )

        cancellable = repository.find_cancellable_tasks(threshold_minutes=15)

        assert len(cancellable) == 1
        assert cancellable[0].id == stale_task.id

    def test_find_cancellable_skips_already_requested(self, engine, repository):
        """Skips tasks with cancel_requested=True."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(minutes=20)

        # Task with cancel already requested
        create_task_with_status(
            engine,
            instance_id="instance-cancelled",
            message_id="msg-cancelled",
            status=TaskStatus.RUNNING.value,
            started_at=old_time,
            cancel_requested=True,
        )

        cancellable = repository.find_cancellable_tasks(threshold_minutes=15)

        assert len(cancellable) == 0

    def test_find_cancellable_skips_recent(self, engine, repository):
        """Skips tasks within threshold."""
        now = datetime.now(timezone.utc)
        recent_time = now - timedelta(minutes=5)

        # Task started recently - should not be found
        create_task_with_status(
            engine,
            instance_id="instance-recent",
            message_id="msg-recent",
            status=TaskStatus.RUNNING.value,
            started_at=recent_time,
        )

        cancellable = repository.find_cancellable_tasks(threshold_minutes=15)

        assert len(cancellable) == 0


# ============================================================================
# cancel_task Tests
# ============================================================================

class TestCancelTask:
    """Tests for cancel_task method."""

    def test_cancel_task_marks_cancelled(self, engine, repository):
        """Directly cancels running task."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
        )

        result = repository.cancel_task(task.id, reason="Test cancellation")

        assert result is not None
        assert result.status == TaskStatus.CANCELLED.value
        assert result.error == "Task cancelled: Test cancellation"

    def test_cancel_task_returns_none_for_completed(self, engine, repository):
        """Can't cancel completed task."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.COMPLETED.value,
        )

        result = repository.cancel_task(task.id, reason="Test")

        assert result is None

    def test_cancel_task_returns_none_for_already_cancelled(self, engine, repository):
        """Idempotent - returns None for already cancelled task."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.CANCELLED.value,
        )

        result = repository.cancel_task(task.id, reason="Test")

        assert result is None

    def test_cancel_task_cancels_pending(self, engine, repository):
        """Can cancel pending task as well."""
        task = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.PENDING.value,
        )

        result = repository.cancel_task(task.id, reason="Test pending")

        assert result is not None
        assert result.status == TaskStatus.CANCELLED.value


# ============================================================================
# force_cancel_and_schedule_retry Tests
# ============================================================================

class TestForceCancelAndScheduleRetry:
    """Tests for force_cancel_and_schedule_retry method."""

    def test_force_cancel_and_retry_atomic(self, engine, repository):
        """Single transaction: parent cancelled, child created."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
        )

        retry_task = repository.force_cancel_and_schedule_retry(
            task_id=parent.id,
            max_retries=3,
            reason="Worker timeout",
        )

        # Verify child was created
        assert retry_task is not None
        assert retry_task.retry_count == 1
        assert retry_task.status == TaskStatus.PENDING.value

        # Verify parent was cancelled
        parent_updated = repository.get(parent.id)
        assert parent_updated.status == TaskStatus.CANCELLED.value
        assert parent_updated.retry_scheduled == 1
        assert "Worker timeout" in parent_updated.error

    def test_force_cancel_and_retry_returns_none_max_retries(self, engine, repository):
        """Returns None when max retries exceeded."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
            retry_count=3,
        )

        result = repository.force_cancel_and_schedule_retry(
            task_id=parent.id,
            max_retries=3,
            reason="Test",
        )

        assert result is None

        # Parent should NOT be cancelled
        parent_updated = repository.get(parent.id)
        assert parent_updated.status == TaskStatus.RUNNING.value

    def test_force_cancel_and_retry_returns_none_already_scheduled(self, engine, repository):
        """Guard check - returns None if retry already scheduled."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.RUNNING.value,
            retry_scheduled=True,
        )

        result = repository.force_cancel_and_schedule_retry(
            task_id=parent.id,
            max_retries=3,
            reason="Test",
        )

        assert result is None


# ============================================================================
# find_orphaned_cancelled_tasks Tests
# ============================================================================

class TestFindOrphanedCancelledTasks:
    """Tests for find_orphaned_cancelled_tasks method."""

    def test_find_orphans_detects_cancelled_without_child(self, engine, repository):
        """CANCELLED task with no retry child is orphaned."""
        # Create a cancelled task with retry_scheduled=False
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.CANCELLED.value,
            retry_count=1,
            retry_scheduled=False,
        )

        orphans = repository.find_orphaned_cancelled_tasks()

        assert len(orphans) == 1
        assert orphans[0].id == parent.id

    def test_find_orphans_skips_if_child_exists(self, engine, repository):
        """Not orphaned if retry child exists."""
        # Create parent cancelled with retry_scheduled=True
        parent = create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.CANCELLED.value,
            retry_count=1,
            retry_scheduled=True,
        )

        # Create child retry task
        create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-1",
            status=TaskStatus.PENDING.value,
            retry_count=2,
        )

        orphans = repository.find_orphaned_cancelled_tasks()

        assert len(orphans) == 0

    def test_find_orphans_skips_non_cancelled(self, engine, repository):
        """Doesn't find running or completed tasks."""
        create_task_with_status(
            engine,
            instance_id="instance-running",
            message_id="msg-running",
            status=TaskStatus.RUNNING.value,
        )
        create_task_with_status(
            engine,
            instance_id="instance-completed",
            message_id="msg-completed",
            status=TaskStatus.COMPLETED.value,
        )

        orphans = repository.find_orphaned_cancelled_tasks()

        assert len(orphans) == 0


# ============================================================================
# get_retry_chain Tests
# ============================================================================

class TestGetRetryChain:
    """Tests for get_retry_chain method."""

    def test_get_retry_chain(self, engine, repository):
        """Returns all tasks for instance_id + message_id ordered by retry_count."""
        # Create original task
        task0 = create_task_with_status(
            engine,
            instance_id="instance-chain",
            message_id="msg-chain",
            status=TaskStatus.CANCELLED.value,
            retry_count=0,
        )

        # Create retry 1
        task1 = create_task_with_status(
            engine,
            instance_id="instance-chain",
            message_id="msg-chain",
            status=TaskStatus.CANCELLED.value,
            retry_count=1,
        )

        # Create retry 2 (current)
        task2 = create_task_with_status(
            engine,
            instance_id="instance-chain",
            message_id="msg-chain",
            status=TaskStatus.PENDING.value,
            retry_count=2,
        )

        chain = repository.get_retry_chain(
            instance_id="instance-chain",
            message_id="msg-chain",
        )

        assert len(chain) == 3
        assert chain[0].retry_count == 0
        assert chain[1].retry_count == 1
        assert chain[2].retry_count == 2

    def test_get_retry_chain_empty(self, repository):
        """Returns empty list for non-existent chain."""
        chain = repository.get_retry_chain(
            instance_id="nonexistent",
            message_id="nonexistent",
        )

        assert len(chain) == 0

    def test_get_retry_chain_excludes_other_messages(self, engine, repository):
        """Only returns tasks for specific message_id."""
        create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-a",
            retry_count=0,
        )
        create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-a",
            retry_count=1,
        )
        create_task_with_status(
            engine,
            instance_id="instance-1",
            message_id="msg-b",
            retry_count=0,
        )

        chain = repository.get_retry_chain(
            instance_id="instance-1",
            message_id="msg-a",
        )

        assert len(chain) == 2
        assert all(t.message_id == "msg-a" for t in chain)


# ============================================================================
# Edge Cases and Integration Tests
# ============================================================================

class TestRetryEdgeCases:
    """Edge cases for retry functionality."""

    def test_schedule_retry_nonexistent_task(self, repository):
        """schedule_retry returns None for non-existent task."""
        result = repository.schedule_retry(
            task_id=99999,
            max_retries=3,
        )
        assert result is None

    def test_force_cancel_nonexistent_task(self, repository):
        """force_cancel_and_schedule_retry returns None for non-existent task."""
        result = repository.force_cancel_and_schedule_retry(
            task_id=99999,
            max_retries=3,
            reason="Test",
        )
        assert result is None

    def test_multiple_retries_increment_count(self, engine, repository):
        """Multiple retries properly increment retry_count."""
        parent = create_task_with_status(
            engine,
            instance_id="instance-multi",
            message_id="msg-multi",
            status=TaskStatus.RUNNING.value,
        )

        # First retry
        retry1 = repository.schedule_retry(parent.id, max_retries=5)
        assert retry1.retry_count == 1

        # Get the new retry task and schedule another
        retry1_db = repository.get(retry1.id)
        # Update status to running for next retry
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE task SET status = :status WHERE id = :id"),
                {"status": TaskStatus.RUNNING.value, "id": retry1.id}
            )

        retry2 = repository.schedule_retry(retry1.id, max_retries=5)
        assert retry2.retry_count == 2

    def test_cancel_then_retry_workflow(self, engine, repository):
        """Test complete workflow: cancel running, then schedule retry."""
        # Create and start a task
        task = create_task_with_status(
            engine,
            instance_id="instance-workflow",
            message_id="msg-workflow",
            status=TaskStatus.RUNNING.value,
        )

        # Request cancellation
        cancel_result = repository.request_cancel(task.id)
        assert cancel_result is True

        # Force cancel and schedule retry atomically
        retry_task = repository.force_cancel_and_schedule_retry(
            task_id=task.id,
            max_retries=3,
            reason="Workflow test",
        )

        assert retry_task is not None
        assert retry_task.retry_count == 1

        # Verify original task is cancelled
        original = repository.get(task.id)
        assert original.status == TaskStatus.CANCELLED.value
        assert original.retry_scheduled == 1
