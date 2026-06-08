"""Comprehensive tests for StaleTaskRecovery 5-step protocol using real repository."""

import pytest
import threading
import time
from datetime import datetime, timezone, timedelta

from daemon.services.stale_task_recovery import (
    StaleTaskRecovery,
    DEFAULT_STALE_THRESHOLD_MINUTES,
    DEFAULT_CHECK_INTERVAL_SECONDS,
    DEFAULT_CANCEL_GRACE_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BACKOFF_BASE,
    DEFAULT_RETRY_BACKOFF_MAX,
)
from daemon.repositories.task.models import TaskStatus


# ============================================================================
# Helper Functions
# ============================================================================


def create_stale_running_task(
    repository,
    instance_id="test-instance",
    message_id="test-message",
    age_minutes=20,
    worker_id="test-worker",
    retry_count=0,
):
    """Create a stale RUNNING task by creating, claiming, and backdating started_at."""
    task = repository.create(
        task_type="process_message",
        instance_id=instance_id,
        message_id=message_id,
    )
    
    # Claim the task to make it RUNNING
    claimed = repository.claim_pending_task(worker_id=worker_id)
    assert claimed is not None
    assert claimed.id == task.id

    # Backdate BOTH started_at and last_heartbeat_at to simulate a
    # crashed worker. The recovery predicate is
    #     COALESCE(last_heartbeat_at, started_at) < threshold
    # so a stale task is identified by a stale heartbeat (or, for
    # legacy rows, a stale started_at). A live task that has been
    # running for ``age_minutes`` but is still heartbeating must NOT
    # be flagged — see the liveness-signal tests in
    # test_task_heartbeat.py for that path.
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)

    # Update started_at and last_heartbeat_at directly in DB
    from sqlalchemy import text
    with repository.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE task SET started_at = :started_at, "
                "last_heartbeat_at = :stale_time, "
                "retry_count = :retry_count "
                "WHERE id = :id"
            ),
            {
                "started_at": stale_time,
                "stale_time": stale_time,
                "retry_count": retry_count,
                "id": task.id,
            },
        )

    return repository.get(task.id)


def create_cancelled_task(
    repository,
    instance_id="test-instance",
    message_id="test-message",
    retry_count=1,
    retry_scheduled=False,
):
    """Create a CANCELLED task (for orphaned task tests)."""
    # Create task
    task = repository.create(
        task_type="process_message",
        instance_id=instance_id,
        message_id=message_id,
    )
    
    # Manually set to CANCELLED state
    from sqlalchemy import text
    with repository.engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE task SET
                    status = :status_cancelled,
                    retry_count = :retry_count,
                    retry_scheduled = :retry_scheduled,
                    cancel_requested = :cancel_requested
                WHERE id = :id
            """),
            {
                "status_cancelled": TaskStatus.CANCELLED.value,
                "retry_count": retry_count,
                "retry_scheduled": bool(retry_scheduled),
                "cancel_requested": True,
                "id": task.id,
            }
        )
    
    return repository.get(task.id)


def create_task_with_retry_child(
    repository,
    instance_id="test-instance",
    message_id="test-message",
    parent_retry_count=1,
    child_retry_count=2,
):
    """Create a cancelled task with its retry child already scheduled."""
    # Create parent (cancelled)
    parent = repository.create(
        task_type="process_message",
        instance_id=instance_id,
        message_id=message_id,
    )
    
    # Create retry child
    child = repository.create(
        task_type="process_message",
        instance_id=instance_id,
        message_id=message_id,
    )
    
    # Mark parent as cancelled with retry scheduled
    from sqlalchemy import text
    with repository.engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE task SET
                    status = :status_cancelled,
                    retry_count = :retry_count,
                    retry_scheduled = :retry_scheduled,
                    cancel_requested = :cancel_requested
                WHERE id = :parent_id
            """),
            {
                "status_cancelled": TaskStatus.CANCELLED.value,
                "retry_count": parent_retry_count,
                "retry_scheduled": True,
                "cancel_requested": True,
                "parent_id": parent.id,
            }
        )
        
        conn.execute(
            text("""
                UPDATE task SET
                    retry_count = :retry_count,
                    next_retry_at = :next_retry_at
                WHERE id = :child_id
            """),
            {
                "retry_count": child_retry_count,
                "next_retry_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                "child_id": child.id,
            }
        )
    
    return repository.get(parent.id), repository.get(child.id)


class MockEventRepository:
    """Simple mock event repository for testing."""
    def __init__(self):
        self.events = []
    
    def create_event(self, instance_id, kind, data):
        self.events.append({
            "instance_id": instance_id,
            "kind": kind,
            "data": data,
        })


class MockMessageRepository:
    """Simple mock message repository for testing."""
    def __init__(self):
        self.failed_messages = []
    
    def fail(self, message_id, error):
        self.failed_messages.append({"message_id": message_id, "error": error})


# ============================================================================
# 5-Step Protocol Tests
# ============================================================================


class Test5StepProtocol:
    """Tests for the 5-step recovery protocol."""
    
    def test_no_stale_tasks_no_action(self, repository):
        """No stale tasks → recover_stale_tasks returns 0."""
        # Create a fresh task that's not stale
        task = repository.create(
            task_type="process_message",
            instance_id="fresh-instance",
            message_id="fresh-message",
        )
        # Don't claim it - it's still PENDING
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 0
        # Task should still be PENDING
        updated = repository.get(task.id)
        assert updated.status == TaskStatus.PENDING.value
    
    def test_step1_find_stale_tasks(self, repository):
        """Step 1: Finds running tasks past threshold, not yet cancelled."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="stale-1",
            message_id="stale-msg-1",
            age_minutes=20,  # Past 15 min threshold
        )
        
        # Create task still running but not stale (recent)
        recent_task = create_stale_running_task(
            repository,
            instance_id="recent-1",
            message_id="recent-msg-1",
            age_minutes=5,  # Under 15 min threshold
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
        )
        
        # Only stale task should be found
        from daemon.repositories.task.repository import TaskRepository
        repo = repository  # Already have a repository
        cancellable = repo.find_cancellable_tasks(threshold_minutes=15)
        
        assert len(cancellable) == 1
        assert cancellable[0].id == stale_task.id
        assert cancellable[0].status == TaskStatus.RUNNING.value
        # SQLite returns 0/1 for booleans, check falsy instead of strict False
        assert not cancellable[0].cancel_requested
    
    def test_step2_request_cancel_sets_flag(self, repository):
        """Step 2: request_cancel called for each stale task."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="cancel-test",
            message_id="cancel-msg",
            age_minutes=20,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
        )
        
        # Call request_cancel directly
        cancelled = repository.request_cancel(stale_task.id)
        
        assert cancelled is True
        
        # Verify flag was set
        updated = repository.get(stale_task.id)
        assert updated.cancel_requested is True
        assert updated.cancel_requested_at is not None
    
    def test_step3_grace_period_wait(self, repository):
        """Step 3: Grace period is respected (use short grace for test speed)."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="grace-test",
            message_id="grace-msg",
            age_minutes=20,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0.1,  # 100ms grace period
        )
        
        start = time.time()
        recovered = recovery.recover_stale_tasks()
        elapsed = time.time() - start
        
        assert recovered == 1
        # Should have waited at least the grace period
        assert elapsed >= 0.1
    
    def test_step4_force_cancel_unresponsive(self, repository):
        """Step 4: Task still RUNNING after grace → force_cancel_and_schedule_retry."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="force-test",
            message_id="force-msg",
            age_minutes=20,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,  # Skip grace
            max_retries=3,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
        
        # Original task should be CANCELLED
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.CANCELLED.value
        assert updated.retry_scheduled is True
    
    def test_step5_retry_scheduled(self, repository):
        """Step 5: Retry task created after force cancel."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="retry-test",
            message_id="retry-msg",
            age_minutes=20,
            retry_count=0,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
        
        # Find the retry task (same instance_id and message_id, higher retry_count)
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            retry_task_row = conn.execute(
                text("""
                    SELECT * FROM task
                    WHERE instance_id = :instance_id
                    AND message_id = :message_id
                    AND retry_count > :parent_retry_count
                """),
                {
                    "instance_id": "retry-test",
                    "message_id": "retry-msg",
                    "parent_retry_count": 0,
                }
            ).fetchone()
        
        assert retry_task_row is not None
        retry_task = repository.get(retry_task_row.id)
        assert retry_task.status == TaskStatus.PENDING.value
        assert retry_task.retry_count == 1
        assert retry_task.next_retry_at is not None
    
    def test_step5_max_retries_exceeded(self, repository):
        """Permanent fail when max retries exceeded."""
        # Create stale task at max retry count
        stale_task = create_stale_running_task(
            repository,
            instance_id="max-retry-test",
            message_id="max-retry-msg",
            age_minutes=20,
            retry_count=3,  # At max retries
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,  # Max retries = 3
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
        
        # Task should be FAILED (not cancelled with retry)
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.FAILED.value
        # Check error message mentions permanent failure
        assert "permanently failed" in updated.error.lower()
        assert "3" in updated.error  # mentions retry count


# ============================================================================
# Worker Cooperation Tests
# ============================================================================


class TestWorkerCooperation:
    """Tests for worker cooperation with recovery (FIX: C2)."""
    
    def test_worker_cancelled_with_retry_scheduled(self, repository):
        """FIX: C2 — Worker cancelled + retry_scheduled=True → recovery skips."""
        # Worker already cancelled task and scheduled retry
        parent, child = create_task_with_retry_child(
            repository,
            instance_id="worker-done",
            message_id="worker-done-msg",
            parent_retry_count=1,
            child_retry_count=2,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        # This task shouldn't be found by find_cancellable_tasks (it's not RUNNING)
        cancellable = repository.find_cancellable_tasks(threshold_minutes=15)
        
        # Parent is CANCELLED, not RUNNING, so not in cancellable list
        cancellable_ids = [t.id for t in cancellable]
        assert parent.id not in cancellable_ids
    
    def test_worker_cancelled_without_retry(self, repository):
        """FIX: C2 — Worker cancelled + retry_scheduled=False → recovery schedules retry."""
        # Create orphaned cancelled task (cancelled but no retry scheduled)
        parent = create_cancelled_task(
            repository,
            instance_id="orphan",
            message_id="orphan-msg",
            retry_count=0,
            retry_scheduled=False,
        )
        
        assert parent.status == TaskStatus.CANCELLED.value
        assert parent.retry_scheduled is False
        
        # Schedule retry directly
        retry_task = repository.schedule_retry(
            task_id=parent.id,
            max_retries=3,
            backoff_base=60,
            backoff_max=3600,
        )
        
        assert retry_task is not None
        assert retry_task.retry_count == 1
        assert retry_task.status == TaskStatus.PENDING.value
        
        # Parent should now have retry_scheduled=True
        updated_parent = repository.get(parent.id)
        assert updated_parent.retry_scheduled is True
    
    def test_double_retry_guard(self, repository):
        """FIX: C2 — If Worker already scheduled retry, StaleTaskRecovery does NOT create duplicate."""
        # Create parent with child already
        parent, child = create_task_with_retry_child(
            repository,
            instance_id="double-retry",
            message_id="double-retry-msg",
            parent_retry_count=1,
            child_retry_count=2,
        )
        
        # Try to schedule another retry
        duplicate_retry = repository.schedule_retry(
            task_id=parent.id,
            max_retries=3,
            backoff_base=60,
            backoff_max=3600,
        )
        
        # Should return None (double-retry guard)
        assert duplicate_retry is None
        
        # Verify no new task was created
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            task_count = conn.execute(
                text("""
                    SELECT COUNT(*) FROM task
                    WHERE instance_id = :instance_id
                    AND message_id = :message_id
                """),
                {"instance_id": "double-retry", "message_id": "double-retry-msg"}
            ).fetchone()[0]
        
        assert task_count == 2  # Parent + original child only


# ============================================================================
# Startup Recovery Tests
# ============================================================================


class TestStartupRecovery:
    """Tests for recover_on_startup method."""
    
    def test_startup_recovery_force_cancels_running(self, repository):
        """No grace period, immediate force cancel on startup."""
        # Create stale RUNNING task
        stale_task = create_stale_running_task(
            repository,
            instance_id="startup-force",
            message_id="startup-force-msg",
            age_minutes=30,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            max_retries=3,
        )
        
        start = time.time()
        recovered = recovery.recover_on_startup()
        elapsed = time.time() - start
        
        assert recovered == 1
        assert elapsed < 0.5  # No grace period
        
        # Task should be cancelled
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.CANCELLED.value
    
    def test_startup_recovery_orphaned_cancelled(self, repository):
        """FIX: S3 — Detects orphaned CANCELLED tasks without retry child."""
        # Create orphaned cancelled task
        parent = create_cancelled_task(
            repository,
            instance_id="startup-orphan",
            message_id="startup-orphan-msg",
            retry_count=1,
            retry_scheduled=False,  # Orphaned!
        )
        
        # Verify it's orphaned
        orphaned = repository.find_orphaned_cancelled_tasks()
        orphaned_ids = [t.id for t in orphaned]
        assert parent.id in orphaned_ids
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            max_retries=3,
        )
        
        recovered = recovery.recover_on_startup()
        
        assert recovered == 1
        
        # Parent should now have retry_scheduled=True
        updated_parent = repository.get(parent.id)
        assert updated_parent.retry_scheduled is True
    
    def test_startup_recovery_max_retries_exceeded(self, repository):
        """Permanent fail on startup when max exceeded."""
        # Create stale task at max retries
        stale_task = create_stale_running_task(
            repository,
            instance_id="startup-max",
            message_id="startup-max-msg",
            age_minutes=30,
            retry_count=3,  # At max
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            max_retries=3,
        )
        
        recovered = recovery.recover_on_startup()
        
        assert recovered == 1
        
        # Task should be FAILED
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.FAILED.value
    
    def test_startup_recovery_no_stale(self, repository):
        """No stale tasks → returns 0."""
        # Create a fresh pending task
        repository.create(
            task_type="process_message",
            instance_id="no-stale",
            message_id="no-stale-msg",
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            max_retries=3,
        )
        
        recovered = recovery.recover_on_startup()
        
        assert recovered == 0


# ============================================================================
# Grace Period Tests
# ============================================================================


class TestGracePeriod:
    """Tests for grace period behavior."""
    
    def test_grace_period_respects_stop_event(self, repository):
        """Recovery stops during grace period if stop() called."""
        # Create stale running task
        create_stale_running_task(
            repository,
            instance_id="stop-test",
            message_id="stop-test-msg",
            age_minutes=20,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=60,  # Long grace
            check_interval_seconds=1,
        )
        
        recovery.start()
        
        # Stop in a separate thread after a short delay
        def stop_recovery():
            time.sleep(0.1)
            recovery.stop()
        
        stop_thread = threading.Thread(target=stop_recovery)
        stop_thread.start()
        
        # Recovery should stop quickly
        start = time.time()
        while recovery.is_running():
            time.sleep(0.01)
            if time.time() - start > 2.0:
                break
        
        elapsed = time.time() - start
        stop_thread.join()
        
        # Should not have waited full grace period
        assert elapsed < 1.5
    
    def test_grace_period_zero(self, repository):
        """Zero grace period → immediate force cancel."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="zero-grace",
            message_id="zero-grace-msg",
            age_minutes=20,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,  # Zero grace
            max_retries=3,
        )
        
        start = time.time()
        recovered = recovery.recover_stale_tasks()
        elapsed = time.time() - start
        
        assert recovered == 1
        assert elapsed < 0.05  # Almost instant
        
        # Task should be cancelled
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.CANCELLED.value


# ============================================================================
# Config Tests
# ============================================================================


class TestConfig:
    """Tests for configuration handling."""
    
    def test_constructor_stores_config(self, repository):
        """All config params stored correctly."""
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=10,
            check_interval_seconds=30,
            cancel_grace_seconds=5,
            max_retries=5,
            retry_backoff_base=30,
            retry_backoff_max=1800,
            event_repository=None,
        )
        
        assert recovery._threshold_minutes == 10
        assert recovery._check_interval == 30
        assert recovery._cancel_grace_seconds == 5
        assert recovery._max_retries == 5
        assert recovery._retry_backoff_base == 30
        assert recovery._retry_backoff_max == 1800
        assert recovery._task_repo is repository
        assert recovery._event_repo is None
    
    def test_default_config_values(self, repository):
        """Default values correct."""
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
        )
        
        assert recovery._threshold_minutes == DEFAULT_STALE_THRESHOLD_MINUTES
        assert recovery._check_interval == DEFAULT_CHECK_INTERVAL_SECONDS
        assert recovery._cancel_grace_seconds == DEFAULT_CANCEL_GRACE_SECONDS
        assert recovery._max_retries == DEFAULT_MAX_RETRIES
        assert recovery._retry_backoff_base == DEFAULT_RETRY_BACKOFF_BASE
        assert recovery._retry_backoff_max == DEFAULT_RETRY_BACKOFF_MAX


# ============================================================================
# Event Logging Tests
# ============================================================================


class TestEventLogging:
    """Tests for recovery event logging."""
    
    def test_recovery_event_logged(self, repository):
        """Recovery events logged to event_repo."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="event-test",
            message_id="event-test-msg",
            age_minutes=20,
        )
        
        event_repo = MockEventRepository()
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
            event_repository=event_repo,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
        assert len(event_repo.events) > 0
        
        # Check event structure
        event = event_repo.events[0]
        assert event["instance_id"] == "event-test"
        assert "recovery" in event["kind"]
        assert "task_id" in event["data"]
    
    def test_recovery_event_includes_retry_id(self, repository):
        """Recovery event includes retry_task_id when retry is scheduled."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="retry-event-test",
            message_id="retry-event-msg",
            age_minutes=20,
        )
        
        event_repo = MockEventRepository()
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
            event_repository=event_repo,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        # Find event with retry_task_id
        retry_events = [
            e for e in event_repo.events
            if e["data"].get("retry_task_id") is not None
        ]
        
        assert len(retry_events) > 0


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests with full recovery flow."""
    
    def test_full_recovery_flow(self, repository):
        """Test complete recovery flow from stale task to retry."""
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="full-flow",
            message_id="full-flow-msg",
            age_minutes=20,
            retry_count=0,
        )
        
        event_repo = MockEventRepository()
        message_repo = MockMessageRepository()
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=message_repo,
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
            event_repository=event_repo,
        )
        
        # Execute recovery
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
        
        # Verify task states
        updated_task = repository.get(stale_task.id)
        assert updated_task.status == TaskStatus.CANCELLED.value
        assert updated_task.retry_scheduled is True
        
        # Find retry task
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            retry_row = conn.execute(
                text("""
                    SELECT * FROM task
                    WHERE instance_id = :instance_id
                    AND message_id = :message_id
                    AND retry_count = 1
                """),
                {"instance_id": "full-flow", "message_id": "full-flow-msg"}
            ).fetchone()
        
        assert retry_row is not None
        assert retry_row.status == TaskStatus.PENDING.value
        
        # Verify events logged
        assert len(event_repo.events) > 0
    
    def test_multiple_stale_tasks_recovery(self, repository):
        """Test recovery of multiple stale tasks."""
        # Create multiple stale tasks
        for i in range(3):
            create_stale_running_task(
                repository,
                instance_id=f"multi-{i}",
                message_id=f"multi-msg-{i}",
                age_minutes=20 + i,
            )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 3
        
        # All should be cancelled with retries scheduled
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            cancelled_count = conn.execute(
                text("SELECT COUNT(*) FROM task WHERE status = :status"),
                {"status": TaskStatus.CANCELLED.value}
            ).fetchone()[0]
            
            retry_count = conn.execute(
                text("""
                    SELECT COUNT(*) FROM task
                    WHERE status = :status_pending
                    AND retry_count > 0
                """),
                {"status_pending": TaskStatus.PENDING.value}
            ).fetchone()[0]
        
        assert cancelled_count == 3
        assert retry_count == 3


# ============================================================================
# Phase 5 Fix Tests
# ============================================================================


class TestPhase5Fixes:
    """Tests for Phase 5 critical bug fixes."""
    
    def test_task_completes_during_grace_period_message_not_failed(self, repository):
        """FIX: C1 — Task completes during grace period → message NOT failed.
        
        If a worker completes a task during the grace period, the recovery
        should skip the task entirely and NOT fail the associated message.
        """
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="grace-complete-test",
            message_id="grace-complete-msg",
            age_minutes=20,
        )
        
        mock_message_repo = MockMessageRepository()
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=mock_message_repo,
            threshold_minutes=15,
            cancel_grace_seconds=0,  # Minimal grace for test speed
            max_retries=3,
        )
        
        # BEFORE recovery runs, simulate the worker completing the task
        repository.complete_task(
            stale_task.id,
            result={"completed": True, "message": "done"}
        )
        
        # Run recovery
        recovered = recovery.recover_stale_tasks()
        
        # Should recover 0 tasks (the completed one was skipped)
        assert recovered == 0
        
        # Task should still be COMPLETED (not cancelled)
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.COMPLETED.value
        
        # Message should NOT have been failed
        assert len(mock_message_repo.failed_messages) == 0
    
    def test_task_fails_during_grace_period_message_not_failed(self, repository):
        """FIX: C1 — Task fails during grace period → message NOT failed.
        
        If a worker fails a task during the grace period, the recovery
        should skip the task entirely and NOT fail the associated message.
        """
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="grace-fail-test",
            message_id="grace-fail-msg",
            age_minutes=20,
        )
        
        mock_message_repo = MockMessageRepository()
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=mock_message_repo,
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        # BEFORE recovery runs, simulate the worker failing the task
        repository.fail_task(
            stale_task.id,
            error="Worker encountered an error"
        )
        
        # Run recovery
        recovered = recovery.recover_stale_tasks()
        
        # Should recover 0 tasks (the failed one was skipped)
        assert recovered == 0
        
        # Task should still be FAILED (not cancelled)
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.FAILED.value
        
        # Message should NOT have been failed
        assert len(mock_message_repo.failed_messages) == 0
    
    def test_startup_recovery_handles_both_stale_and_orphaned(self, repository):
        """FIX: C3 — recover_on_startup handles both stale RUNNING + orphaned CANCELLED.
        
        The startup recovery should properly handle both:
        1. Stale RUNNING tasks (worker crashed mid-execution)
        2. Orphaned CANCELLED tasks (crash between cancel and retry)
        """
        # Create stale RUNNING task
        stale_running = create_stale_running_task(
            repository,
            instance_id="startup-stale",
            message_id="startup-stale-msg",
            age_minutes=30,
            retry_count=0,
        )
        
        # Create orphaned CANCELLED task (no retry child)
        orphaned = create_cancelled_task(
            repository,
            instance_id="startup-orphan",
            message_id="startup-orphan-msg",
            retry_count=1,
            retry_scheduled=False,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=MockMessageRepository(),
            threshold_minutes=15,
            max_retries=3,
        )
        
        recovered = recovery.recover_on_startup()
        
        # Both tasks should be recovered
        assert recovered == 2
        
        # Stale RUNNING should be cancelled with retry_scheduled=True
        updated_running = repository.get(stale_running.id)
        assert updated_running.status == TaskStatus.CANCELLED.value
        assert updated_running.retry_scheduled is True
        
        # Orphaned CANCELLED should now have retry_scheduled=True
        updated_orphaned = repository.get(orphaned.id)
        assert updated_orphaned.retry_scheduled is True
        
        # Verify retry tasks were created
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            retry_count = conn.execute(
                text("""
                    SELECT COUNT(*) FROM task
                    WHERE status = :status_pending
                    AND retry_count > 0
                """),
                {"status_pending": TaskStatus.PENDING.value}
            ).fetchone()[0]
        
        assert retry_count == 2  # One retry for each recovered task
    
    def test_recovery_idempotent_second_run_finds_nothing(self, repository):
        """FIX: W4 — recover_stale_tasks is idempotent.
        
        Running recovery twice should not cause issues. The second run
        should find that all stale tasks have already been handled.
        """
        # Create stale running task
        stale_task = create_stale_running_task(
            repository,
            instance_id="idempotent-test",
            message_id="idempotent-msg",
            age_minutes=20,
        )
        
        mock_message_repo = MockMessageRepository()
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=mock_message_repo,
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        # First run
        recovered1 = recovery.recover_stale_tasks()
        assert recovered1 == 1
        
        # Verify task was cancelled with retry
        updated = repository.get(stale_task.id)
        assert updated.status == TaskStatus.CANCELLED.value
        assert updated.retry_scheduled is True
        
        # Second run — should find nothing to do
        recovered2 = recovery.recover_stale_tasks()
        assert recovered2 == 0
        
        # No additional messages should have been failed
        assert len(mock_message_repo.failed_messages) == 1  # Only from first run
    
    def test_recovered_count_only_increments_for_acted_tasks(self, repository):
        """FIX: W4 — recovered_count only increments for tasks actually acted upon.
        
        Tasks that are skipped (COMPLETED, FAILED, or already handled) should
        not contribute to the recovered_count.
        """
        # Create stale running task that will be completed before we check it
        stale_task = create_stale_running_task(
            repository,
            instance_id="count-test",
            message_id="count-msg",
            age_minutes=20,
        )
        
        mock_message_repo = MockMessageRepository()
        
        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=mock_message_repo,
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        # Complete the task before recovery checks it
        repository.complete_task(stale_task.id, result={"done": True})
        
        # Run recovery
        recovered = recovery.recover_stale_tasks()
        
        # Should be 0 because we skipped the completed task
        assert recovered == 0
        
        # Message should not have been failed
        assert len(mock_message_repo.failed_messages) == 0


class TestPausedInstanceSkipped:
    """Regression: StaleTaskRecovery must not auto-resume a paused instance.

    A paused instance intentionally leaves its in-flight task RUNNING so that
    user-initiated resume can continue from the same task row. Recovery's
    liveness signal (stale heartbeat) cannot distinguish "crashed worker" from
    "user-paused" — the fix is to consult the instance's status and skip
    paused/terminated instances entirely.
    """

    def _insert_instance(self, engine, instance_id: str, status: str) -> None:
        """Insert an Instance row with the given status.

        Required because ``SQLModel.metadata.create_all`` in the conftest
        only creates tables for SQLModels that have been imported by the
        time the engine fixture runs. We import the Instance model here
        and insert via the ORM so all NOT NULL columns are populated.
        """
        from sqlmodel import Session as SQLModelSession
        from daemon.repositories.instance.models import Instance
        with SQLModelSession(engine) as session:
            existing = session.get(Instance, instance_id)
            if existing is not None:
                existing.status = status
                session.add(existing)
            else:
                session.add(
                    Instance(
                        instance_id=instance_id,
                        agent_id="test-agent",
                        agent_dir="/tmp/test",
                        status=status,
                    )
                )
            session.commit()

    def test_find_cancellable_tasks_skips_paused_instance(self, repository, engine):
        """find_cancellable_tasks must not return tasks whose instance is PAUSED."""
        # Instance is paused (the user paused it) but the in-flight task
        # is still RUNNING with a stale heartbeat (the worker was cancelled).
        self._insert_instance(engine, "paused-inst", "paused")
        paused_task = create_stale_running_task(
            repository,
            instance_id="paused-inst",
            message_id="paused-msg",
            age_minutes=20,
        )

        # A non-paused instance's stale task — control case, must be returned.
        self._insert_instance(engine, "active-inst", "running")
        active_task = create_stale_running_task(
            repository,
            instance_id="active-inst",
            message_id="active-msg",
            age_minutes=20,
        )

        cancellable = repository.find_cancellable_tasks(threshold_minutes=15)

        assert len(cancellable) == 1
        assert cancellable[0].id == active_task.id
        assert paused_task.id not in [t.id for t in cancellable]

    def test_find_stale_running_tasks_skips_paused_instance(self, repository, engine):
        """find_stale_running_tasks must not return tasks on paused instances."""
        self._insert_instance(engine, "paused-inst", "paused")
        paused_task = create_stale_running_task(
            repository,
            instance_id="paused-inst",
            message_id="paused-msg",
            age_minutes=20,
        )

        self._insert_instance(engine, "terminated-inst", "terminated")
        terminated_task = create_stale_running_task(
            repository,
            instance_id="terminated-inst",
            message_id="terminated-msg",
            age_minutes=20,
        )

        self._insert_instance(engine, "active-inst", "running")
        active_task = create_stale_running_task(
            repository,
            instance_id="active-inst",
            message_id="active-msg",
            age_minutes=20,
        )

        stale = repository.find_stale_running_tasks(threshold_minutes=15)

        stale_ids = [t.id for t in stale]
        assert active_task.id in stale_ids
        assert paused_task.id not in stale_ids
        assert terminated_task.id not in stale_ids

    def test_recover_stale_tasks_does_not_resume_paused_instance(self, repository, engine):
        """End-to-end: recover_stale_tasks must not act on paused instances."""
        self._insert_instance(engine, "paused-inst", "paused")
        paused_task = create_stale_running_task(
            repository,
            instance_id="paused-inst",
            message_id="paused-msg",
            age_minutes=20,
        )

        mock_message_repo = MockMessageRepository()

        recovery = StaleTaskRecovery(
            task_repository=repository,
            message_repository=mock_message_repo,
            threshold_minutes=15,
            cancel_grace_seconds=0,
            max_retries=3,
        )

        recovered = recovery.recover_stale_tasks()

        # No recovery action taken on the paused instance.
        assert recovered == 0

        # Task must still be RUNNING — the user has not resumed yet.
        updated = repository.get(paused_task.id)
        assert updated.status == TaskStatus.RUNNING.value

        # No retry task was created.
        from sqlalchemy import text
        with engine.begin() as conn:
            retry_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM task "
                    "WHERE instance_id = :instance_id AND id != :id"
                ),
                {"instance_id": "paused-inst", "id": paused_task.id},
            ).scalar()
        assert retry_count == 0

        # No message was failed (this is the symptom that triggered the
        # user-visible auto-resume in the original bug).
        assert len(mock_message_repo.failed_messages) == 0
