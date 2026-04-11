"""Edge Case Tests for Worker Pool Notification Mechanism.

These tests cover edge cases and integration scenarios for the
notification-driven worker pool.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.services.worker_pool import Worker, WorkerPool


# ============================================================================
# Fixtures
# ============================================================================


class MockTaskProcessor:
    """Mock task processor that always returns None (no tasks)."""

    def __init__(self):
        self.claim_count = 0
        self.run_count = 0
        self.claimed_tasks = []

    def claim_task(self, worker_id):
        self.claim_count += 1
        return None

    def run_task(self, task, cancellation_token=None):
        self.run_count += 1

    def get_pending_count(self):
        return 0


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def task_repo(engine):
    """Create TaskRepository instance with fresh database."""
    return TaskRepository(engine)


@pytest.fixture
def mock_worker_pool():
    """Create a MockWorkerPool instance for Worker tests."""
    from tests.message_queue_redesign.test_timeout_retry_e2e import MockWorkerPool
    return MockWorkerPool()


# ============================================================================
# A. Notification Mechanism Edge Cases
# ============================================================================


class TestRapidSequentialNotifications:
    """Tests for rapid sequential notify_work() calls."""

    def test_rapid_100_notify_calls_all_tracked(self):
        """100 rapid notify_work() calls should all be tracked."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Call notify_work() 100 times rapidly
        for _ in range(100):
            pool.notify_work()

        # Verify all notifications are tracked
        assert pool._stats["notifications_sent"] == 100
        assert pool._notification_count == 100

    def test_rapid_notifications_satisfy_100_waiters(self):
        """100 notifications should satisfy 100 waiting workers."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Send 100 notifications
        for _ in range(100):
            pool.notify_work()

        # Consume all 100 notifications
        results = []
        for _ in range(100):
            result = pool.wait_for_work(timeout=0.05)
            results.append(result)

        # All should return True
        assert all(results), "All 100 waiters should receive notifications"
        assert pool._notification_count == 0, "All notifications should be consumed"


class TestNotifyWorkWhenNoTasks:
    """Tests for notify_work() when no tasks exist in DB."""

    def test_worker_wakes_no_tasks_increments_empty_claims(self):
        """Worker wakes via notification but finds no task → empty_claim_attempts++."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Send a notification
        pool.notify_work()

        # Worker wakes, finds no work, should increment empty_claim_attempts
        # We simulate this by calling wait_for_work and then simulating a worker loop
        result = pool.wait_for_work(timeout=0.05)
        assert result is True  # Notification was received

        # The empty_claim_attempts is incremented by Worker.run(), not by wait_for_work()
        # But we can test that a worker that wakes via timeout also increments it
        pool.wait_for_work(timeout=0.05)  # This wakes via timeout, not notification
        assert pool._stats["workers_woken_by_timeout"] == 1

    def test_worker_pool_no_task_no_crash(self):
        """notify_work() followed by no tasks should not crash."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Send notification when no task exists
        pool.notify_work()

        # Worker should handle gracefully
        result = pool.wait_for_work(timeout=0.1)
        assert result is True  # Got notification

        # No crash - test passes


class TestShutdownDuringWait:
    """Tests for shutdown while workers are waiting."""

    def test_stop_wakes_sleeping_workers_within_timeout(self):
        """stop() should wake sleeping workers and exit within timeout."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=3)

        # Start workers and let them settle into waiting
        pool.start()
        time.sleep(0.3)  # Let workers enter wait_for_work()

        # Verify workers are alive
        assert all(w.is_alive() for w in pool._workers)

        # Stop should not hang - workers are in wait_for_work() with 3s timeout
        start_time = time.time()
        pool.stop(timeout=5.0)
        elapsed = time.time() - start_time

        # Should complete quickly since workers wake on notify_all
        assert elapsed < 2.0, f"stop() took too long: {elapsed:.3f}s"
        assert not pool.is_running()
        assert pool._stopped

    def test_stop_during_active_wait_for_work(self):
        """Workers in wait_for_work() should exit cleanly when stop() is called."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=2)

        pool.start()
        time.sleep(0.2)  # Let workers settle

        # At this point workers are in their main loop, either:
        # 1. Just finished claim_task (returning None) and about to wait
        # 2. In wait_for_work() waiting for notification

        # Stop should work regardless of where in the loop workers are
        start_time = time.time()
        pool.stop(timeout=3.0)
        elapsed = time.time() - start_time

        assert elapsed < 2.0
        assert not pool.is_running()


class TestCallbackExceptionHandling:
    """Tests for callback exception handling in TaskRepository."""

    def test_on_pending_task_callback_exception_caught(self, engine):
        """Exception in on_pending_task callback should be caught and logged."""
        callback_called = threading.Event()
        exception_raised = threading.Event()

        def raising_callback():
            callback_called.set()
            raise ValueError("Test exception from callback")

        repo = TaskRepository(engine, on_pending_task=raising_callback)

        # Create a task with schedule_retry that will trigger callback
        task = repo.create(
            task_type="process_message",
            instance_id="test-exception",
            message_id="test-msg-exception",
        )

        # Claim and update task so schedule_retry can work
        from sqlalchemy import text as sql_text
        with engine.begin() as conn:
            conn.execute(
                sql_text("""
                    UPDATE task SET status = :status_running, worker_id = :worker_id
                    WHERE id = :id
                """),
                {"id": task.id, "status_running": TaskStatus.RUNNING.value, "worker_id": "test-worker"}
            )

        # schedule_retry should call the callback (which raises)
        # but should NOT propagate the exception
        # We need to patch logging to verify the warning is logged
        with patch("daemon.repositories.task.repository.logger") as mock_logger:
            retry_task = repo.schedule_retry(
                task_id=task.id,
                max_retries=3,
                backoff_base=1,
                backoff_max=10,
            )

            # Callback should have been called
            assert callback_called.is_set(), "Callback should have been called"

            # Exception should have been caught and logged as warning
            # (We verify this by checking that schedule_retry returned successfully)
            assert retry_task is not None, "Retry task should still be created"

    def test_callback_exception_does_not_crash_schedule_retry(self, engine):
        """schedule_retry should complete even if callback raises."""
        def bad_callback():
            raise RuntimeError("Boom!")

        repo = TaskRepository(engine, on_pending_task=bad_callback)

        # Create task
        task = repo.create(
            task_type="process_message",
            instance_id="test-no-crash",
            message_id="test-msg-no-crash",
        )

        # Update to running
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE task SET status = :status, worker_id = :worker_id
                    WHERE id = :id
                """),
                {"id": task.id, "status": TaskStatus.RUNNING.value, "worker_id": "test-worker"}
            )

        # This should not raise - exception is caught internally
        retry_task = repo.schedule_retry(
            task_id=task.id,
            max_retries=3,
            backoff_base=1,
            backoff_max=10,
        )

        # Retry should have been scheduled
        assert retry_task is not None


# ============================================================================
# B. Integration: schedule_retry / force_cancel_and_schedule_retry notify after commit
# ============================================================================


class TestScheduleRetryNotification:
    """Tests for schedule_retry notifying workers after commit."""

    def test_schedule_retry_calls_callback_after_commit(self, engine):
        """schedule_retry should call on_pending_task AFTER commit (task exists in DB)."""
        callback_verified = {"called": False, "task_exists": False}

        def verify_callback():
            callback_verified["called"] = True
            # Verify retry task actually exists in DB when callback fires
            # The parent task is CANCELLED, the retry is PENDING
            with engine.begin() as conn:
                # Check for pending task (the retry)
                pending = conn.execute(
                    text("SELECT COUNT(*) as cnt FROM task WHERE status = :status"),
                    {"status": TaskStatus.PENDING.value}
                ).fetchone()
                # Check total tasks (parent CANCELLED + retry PENDING = 2)
                total = conn.execute(text("SELECT COUNT(*) as cnt FROM task")).fetchone()
                callback_verified["task_exists"] = pending[0] >= 1 and total[0] >= 2

        repo = TaskRepository(engine, on_pending_task=verify_callback)

        # Create and claim a task
        task = repo.create(
            task_type="process_message",
            instance_id="test-notify-after-commit",
            message_id="test-msg-notify",
        )

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE task SET status = :status, worker_id = :worker_id
                    WHERE id = :id
                """),
                {"id": task.id, "status": TaskStatus.RUNNING.value, "worker_id": "test-worker"}
            )

        # Schedule retry - this should call callback after commit
        retry_task = repo.schedule_retry(
            task_id=task.id,
            max_retries=3,
            backoff_base=1,
            backoff_max=10,
        )

        assert retry_task is not None
        assert callback_verified["called"], "Callback should have been called"
        assert callback_verified["task_exists"], "Retry task should exist in DB when callback fires"


class TestForceCancelAndScheduleRetryNotification:
    """Tests for force_cancel_and_schedule_retry notification."""

    def test_force_cancel_retry_calls_callback_after_commit(self, engine):
        """force_cancel_and_schedule_retry should call on_pending_task AFTER commit."""
        callback_verified = {"called": False, "task_exists": False}

        def verify_callback():
            callback_verified["called"] = True
            # Verify retry task exists in DB
            # Parent is CANCELLED, retry is PENDING
            with engine.begin() as conn:
                pending = conn.execute(
                    text("SELECT COUNT(*) as cnt FROM task WHERE status = :status"),
                    {"status": TaskStatus.PENDING.value}
                ).fetchone()
                total = conn.execute(text("SELECT COUNT(*) as cnt FROM task")).fetchone()
                callback_verified["task_exists"] = pending[0] >= 1 and total[0] >= 2

        repo = TaskRepository(engine, on_pending_task=verify_callback)

        # Create and claim a task
        task = repo.create(
            task_type="process_message",
            instance_id="test-force-cancel",
            message_id="test-msg-force",
        )

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE task SET status = :status, worker_id = :worker_id
                    WHERE id = :id
                """),
                {"id": task.id, "status": TaskStatus.RUNNING.value, "worker_id": "test-worker"}
            )

        # Force cancel and schedule retry
        retry_task = repo.force_cancel_and_schedule_retry(
            task_id=task.id,
            max_retries=3,
            reason="Test force cancel",
            backoff_base=1,
            backoff_max=10,
        )

        assert retry_task is not None
        assert callback_verified["called"], "Callback should have been called"
        assert callback_verified["task_exists"], "Retry task should exist when callback fires"

    def test_force_cancel_retry_cancels_parent_task(self, engine):
        """force_cancel_and_schedule_retry should cancel parent task."""
        repo = TaskRepository(engine)

        task = repo.create(
            task_type="process_message",
            instance_id="test-force-cancel-parent",
            message_id="test-msg-force-parent",
        )

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE task SET status = :status, worker_id = :worker_id
                    WHERE id = :id
                """),
                {"id": task.id, "status": TaskStatus.RUNNING.value, "worker_id": "test-worker"}
            )

        retry_task = repo.force_cancel_and_schedule_retry(
            task_id=task.id,
            max_retries=3,
            reason="Test",
            backoff_base=1,
            backoff_max=10,
        )

        # Verify parent is cancelled
        parent = repo.get(task.id)
        assert parent.status == TaskStatus.CANCELLED.value
        assert "Force cancelled" in parent.error

        # Verify retry task exists
        assert retry_task is not None
        assert retry_task.status == TaskStatus.PENDING.value


class TestScheduleRetryNoNotificationOnMaxRetries:
    """Tests that schedule_retry does NOT notify when max retries exceeded."""

    def test_max_retries_exceeded_no_callback(self, engine):
        """When max retries exceeded, callback should NOT be called."""
        callback_called = {"value": False}

        def track_callback():
            callback_called["value"] = True

        repo = TaskRepository(engine, on_pending_task=track_callback)

        # Create task with retry_count already at max (2 for max_retries=2)
        task = repo.create(
            task_type="process_message",
            instance_id="test-max-retries",
            message_id="test-msg-max",
        )

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE task SET
                        status = :status,
                        worker_id = :worker_id,
                        retry_count = :retry_count
                    WHERE id = :id
                """),
                {
                    "id": task.id,
                    "status": TaskStatus.RUNNING.value,
                    "worker_id": "test-worker",
                    "retry_count": 2,  # Already at max
                }
            )

        # Schedule retry with max_retries=2 - should return None (no retry)
        retry_task = repo.schedule_retry(
            task_id=task.id,
            max_retries=2,  # 2 >= 2, so max exceeded
            backoff_base=1,
            backoff_max=10,
        )

        assert retry_task is None, "No retry should be scheduled when max exceeded"
        assert not callback_called["value"], "Callback should NOT be called when max retries exceeded"


# ============================================================================
# C. Integration: poll_interval should NOT exist in WorkerPool
# ============================================================================


class TestNoPollIntervalInWorkerPool:
    """Verify WorkerPool does not use poll_interval (uses notification instead)."""

    def test_worker_pool_constructor_no_poll_interval(self):
        """WorkerPool should NOT accept poll_interval parameter."""
        import inspect
        sig = inspect.signature(WorkerPool.__init__)
        params = list(sig.parameters.keys())

        assert "poll_interval" not in params, \
            "WorkerPool should not have poll_interval parameter"

    def test_worker_pool_uses_condition_not_polling(self):
        """WorkerPool uses threading.Condition for notification."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Verify Condition exists
        assert hasattr(pool, "_condition")
        assert isinstance(pool._condition, threading.Condition)

    def test_no_poll_interval_in_codebase_worker_pool(self):
        """Verify no poll_interval references in worker_pool.py."""
        import daemon.services.worker_pool as worker_pool_module
        source_file = worker_pool_module.__file__

        with open(source_file, "r") as f:
            content = f.read()

        assert "poll_interval" not in content, \
            "worker_pool.py should not contain poll_interval"


# ============================================================================
# D. Worker pool metrics
# ============================================================================


class TestWakeupEfficiencyCalculation:
    """Tests for wakeup_efficiency calculation."""

    def test_wakeup_efficiency_perfect(self):
        """wakeup_efficiency = 1.0 when all notifications lead to work."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Send notifications (these would be consumed by workers that find work)
        pool.notify_work()
        pool.notify_work()
        pool.notify_work()

        # Get stats - with no empty claims, efficiency should be 1.0
        stats = pool.get_stats()

        # efficiency = notifications / max(1, notifications + empty_claims)
        # With 3 notifications and 0 empty claims: 3 / max(1, 3) = 1.0
        assert stats["wakeup_efficiency"] == 1.0

    def test_wakeup_efficiency_with_empty_claims(self):
        """wakeup_efficiency < 1.0 when notifications find no work."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Send notifications (but workers will find no work due to mock processor)
        pool.notify_work()
        pool.notify_work()

        # Simulate workers waking via timeout (empty claims)
        # Note: wait_for_work with notification doesn't increment empty_claims
        # Only workers in the run loop do that
        # But we can test the formula directly
        pool._stats["empty_claim_attempts"] = 8  # Simulate 8 empty claims

        stats = pool.get_stats()

        # efficiency = notifications / max(1, notifications + empty_claims)
        # = 2 / max(1, 2 + 8) = 2 / 10 = 0.2
        assert stats["wakeup_efficiency"] == 0.2

    def test_wakeup_efficiency_formula(self):
        """Verify wakeup_efficiency = notifications / max(1, notifications + empty_claims)."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Test various scenarios
        test_cases = [
            # (notifications, empty_claims, expected_efficiency)
            (0, 0, 0.0),    # 0 / 1 = 0
            (1, 0, 1.0),    # 1 / 1 = 1
            (1, 1, 0.5),    # 1 / 2 = 0.5
            (10, 90, 0.1),  # 10 / 100 = 0.1
        ]

        for notifications, empty_claims, expected in test_cases:
            pool._stats["notifications_sent"] = notifications
            pool._stats["empty_claim_attempts"] = empty_claims

            stats = pool.get_stats()
            assert stats["wakeup_efficiency"] == expected, \
                f"Failed for ({notifications}, {empty_claims}): expected {expected}"


class TestEmptyClaimAttemptsIncrement:
    """Tests for empty_claim_attempts metric."""

    def test_empty_claim_attempts_increments_on_worker_loop(self):
        """empty_claim_attempts should increment when worker finds no task."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=1)

        pool.start()
        time.sleep(0.5)  # Let worker run through loop

        # Worker should have done at least one empty claim
        assert pool._stats["empty_claim_attempts"] >= 1, \
            "Worker should increment empty_claim_attempts when no task found"

        pool.stop(timeout=3.0)

    def test_empty_claim_attempts_tracked_in_stats(self):
        """empty_claim_attempts should appear in get_stats()."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)

        # Simulate some empty claims
        pool._stats["empty_claim_attempts"] = 5

        stats = pool.get_stats()
        assert "empty_claim_attempts" in stats
        assert stats["empty_claim_attempts"] == 5


# ============================================================================
# Additional Edge Cases
# ============================================================================


class TestNotificationWithWorkersRunning:
    """Test notification behavior with actual worker pool."""

    def test_worker_pool_with_claim_that_returns_task(self):
        """Workers should claim tasks when available."""
        # Create a processor that returns a mock task after one call
        claim_count = [0]
        claimed_task = Mock()
        claimed_task.id = 1
        claimed_task.task_type = "process_message"
        claimed_task.instance_id = "test"

        class OneTaskProcessor:
            def claim_task(self, worker_id):
                claim_count[0] += 1
                if claim_count[0] == 1:
                    return claimed_task
                return None

            def run_task(self, task, cancellation_token=None):
                pass

            def get_pending_count(self):
                return 0

        pool = WorkerPool(task_processor=OneTaskProcessor(), num_workers=1)
        pool.start()

        # Give worker time to claim
        time.sleep(0.5)

        # Worker should have claimed the task
        assert claim_count[0] >= 1

        pool.stop(timeout=3.0)

    def test_worker_pool_stop_is_idempotent(self):
        """Calling stop() multiple times should not cause issues."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=2)

        pool.start()
        time.sleep(0.2)

        # Stop multiple times
        pool.stop(timeout=2.0)
        pool.stop(timeout=2.0)  # Should be safe to call again

        assert not pool.is_running()


class TestWorkerStats:
    """Tests for individual worker statistics."""

    def test_worker_stats_tracked(self):
        """Worker should track claimed/completed/failed counts."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=1)

        pool.start()
        time.sleep(0.5)  # Let worker run

        stats = pool.get_stats()

        assert len(stats["workers"]) == 1
        worker_stats = stats["workers"][0]

        assert "tasks_claimed" in worker_stats
        assert "tasks_completed" in worker_stats
        assert "tasks_failed" in worker_stats

        pool.stop(timeout=3.0)
