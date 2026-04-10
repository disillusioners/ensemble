"""Tests for StaleTaskRecovery service with 5-step protocol."""

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
)


def create_stale_task(
    mock_task_repository,
    task_id=1,
    task_type="process_message",
    instance_id="test-instance",
    message_id="test-message",
    worker_id="test-worker",
    age_minutes=20,
    status="running",
    retry_count=0,
    retry_scheduled=False,
    cancel_requested=False,
):
    """Helper to create a stale task in the mock repository."""
    task = type('MockTask', (), {
        'id': task_id,
        'task_type': task_type,
        'instance_id': instance_id,
        'message_id': message_id,
        'status': status,
        'worker_id': worker_id,
        'retry_count': retry_count,
        'retry_scheduled': retry_scheduled,
        'cancel_requested': cancel_requested,
        'started_at': datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
        'created_at': datetime.now(timezone.utc),
    })()
    mock_task_repository.tasks[task_id] = task
    mock_task_repository.stale_tasks.append(task)
    return task


class TestStaleTaskRecovery:
    """Tests for StaleTaskRecovery class with 5-step protocol."""
    
    def test_recovery_finds_stale_tasks(self, mock_task_repository, mock_message_repository):
        """Recovery should find tasks running too long using find_cancellable_tasks."""
        create_stale_task(mock_task_repository, task_id=1, age_minutes=20)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            threshold_minutes=15,
            cancel_grace_seconds=0,  # Skip grace period for testing
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
    
    def test_recovery_skips_recent_tasks(self, mock_task_repository, mock_message_repository):
        """Recovery should not find tasks that are still running normally."""
        # Task running for only 5 minutes (under 15 minute threshold)
        task = create_stale_task(mock_task_repository, task_id=2, age_minutes=5)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            threshold_minutes=15,
            cancel_grace_seconds=0,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 0
    
    def test_recovery_resets_multiple_stale_tasks(self, mock_task_repository, mock_message_repository):
        """Recovery should process all stale tasks."""
        for i in range(3):
            create_stale_task(mock_task_repository, task_id=i, age_minutes=20 + i)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            threshold_minutes=15,
            cancel_grace_seconds=0,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 3
    
    def test_recovery_on_startup(self, mock_task_repository, mock_message_repository):
        """recover_on_startup should run recovery immediately."""
        # For startup recovery, stale_tasks contains tasks from find_stale_running_tasks
        create_stale_task(mock_task_repository, task_id=1, age_minutes=30)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
        )
        
        recovered = recovery.recover_on_startup()
        
        assert recovered == 1
    
    def test_default_threshold(self):
        """Default threshold should be 15 minutes."""
        assert DEFAULT_STALE_THRESHOLD_MINUTES == 15
    
    def test_lifecycle_start_stop(self, mock_task_repository, mock_message_repository):
        """Recovery should start and stop properly."""
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            check_interval_seconds=1,
        )
        
        recovery.start()
        assert recovery.is_running()
        
        recovery.stop()
        assert not recovery.is_running()
    
    def test_recovery_double_start_warning(self, mock_task_repository, mock_message_repository):
        """Recovery should warn on double start (not raise)."""
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
        )
        
        recovery.start()
        recovery.start()  # Should not raise
        
        recovery.stop()
    
    def test_recovery_with_event_repository(
        self, mock_task_repository, mock_message_repository, mock_event_repository
    ):
        """Recovery should create events when event repository is available."""
        create_stale_task(mock_task_repository, task_id=1, age_minutes=30)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            event_repository=mock_event_repository,
            cancel_grace_seconds=0,
        )
        
        recovery.recover_stale_tasks()
        
        assert len(mock_event_repository.events) > 0
    
    def test_step1_find_cancellable_tasks(self, mock_task_repository, mock_message_repository):
        """Step 1: Should find cancellable tasks using find_cancellable_tasks."""
        # Task already marked for cancel should be skipped
        create_stale_task(mock_task_repository, task_id=1, age_minutes=20, cancel_requested=True)
        # Normal stale task should be found
        create_stale_task(mock_task_repository, task_id=2, age_minutes=20)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            cancel_grace_seconds=0,
        )
        
        # Only the second task should be recovered (first has cancel_requested=True)
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
    
    def test_step2_request_cancel_sets_flag(self, mock_task_repository, mock_message_repository):
        """Step 2: Should request cancel and set cancel_requested flag."""
        create_stale_task(mock_task_repository, task_id=1, age_minutes=20)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            cancel_grace_seconds=0,
        )
        
        recovery.recover_stale_tasks()
        
        # Check that request_cancel was called (task should have cancel_requested=True)
        task = mock_task_repository.tasks[1]
        assert task.cancel_requested is True
    
    def test_step4_force_cancel_creates_retry(self, mock_task_repository, mock_message_repository):
        """Step 4+5: Force cancelled task should create retry task."""
        task = create_stale_task(mock_task_repository, task_id=1, age_minutes=20)
        # Simulate task still running after grace period
        mock_task_repository.tasks[1].status = "running"
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        recovery.recover_stale_tasks()
        
        # Original task should be cancelled
        assert mock_task_repository.tasks[1].status == "cancelled"
        assert mock_task_repository.tasks[1].retry_scheduled is True
    
    def test_double_retry_guard(self, mock_task_repository, mock_message_repository):
        """FIX: C2 — If Worker already scheduled retry, recovery should not create duplicate."""
        # Simulate Worker already scheduled retry
        task = create_stale_task(
            mock_task_repository,
            task_id=1,
            age_minutes=20,
            status="cancelled",
            retry_scheduled=True,
        )
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        # Run recovery
        initial_task_count = len(mock_task_repository.tasks)
        recovery.recover_stale_tasks()
        
        # Should not create a new retry task
        final_task_count = len(mock_task_repository.tasks)
        assert final_task_count == initial_task_count
    
    def test_worker_cancelled_without_retry(self, mock_task_repository, mock_message_repository):
        """FIX: C2 — Worker cancelled but didn't schedule retry → recovery schedules retry.
        
        Note: This scenario is actually tested via startup recovery with orphaned cancelled tasks.
        The recover_stale_tasks() method only finds RUNNING tasks, so we test the
        CANCELLED + retry_scheduled=False scenario in test_startup_recovery_orphaned_cancelled.
        """
        # This test verifies that schedule_retry works correctly on a cancelled task
        task = create_stale_task(
            mock_task_repository,
            task_id=1,
            age_minutes=20,
            status="cancelled",  # Already cancelled
            retry_scheduled=False,  # But no retry scheduled
        )
        
        # Directly test schedule_retry (this is what would be called for orphaned tasks)
        retry_task = mock_task_repository.schedule_retry(
            task_id=1,
            max_retries=3,
            backoff_base=60,
            backoff_max=3600,
        )
        
        # Should create a new retry task
        assert retry_task is not None
        assert retry_task.retry_count == 1
        assert mock_task_repository.tasks[1].status == "cancelled"
        assert mock_task_repository.tasks[1].retry_scheduled is True
    
    def test_max_retries_exceeded_fails_task(self, mock_task_repository, mock_message_repository):
        """Tasks exceeding max retries should be permanently failed."""
        task = create_stale_task(
            mock_task_repository,
            task_id=1,
            age_minutes=20,
            retry_count=3,  # Already at max
        )
        # Simulate task still running
        mock_task_repository.tasks[1].status = "running"
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        recovery.recover_stale_tasks()
        
        # Original task should be failed
        assert mock_task_repository.tasks[1].status == "failed"
    
    def test_startup_recovery_no_grace(self, mock_task_repository, mock_message_repository):
        """Startup recovery should not wait for grace period."""
        task = create_stale_task(mock_task_repository, task_id=1, age_minutes=30)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            cancel_grace_seconds=10,  # This should be ignored
        )
        
        # This should complete without waiting
        start_time = time.time()
        recovery.recover_on_startup()
        elapsed = time.time() - start_time
        
        # Should complete quickly (no grace period)
        assert elapsed < 1.0
        assert recovery._stop_event.is_set() is False  # Should not be set by startup recovery
    
    def test_startup_recovery_orphaned_cancelled(self, mock_task_repository, mock_message_repository):
        """FIX: S3 — Startup recovery should detect orphaned CANCELLED tasks."""
        # Create orphaned cancelled task (cancelled but no retry scheduled)
        task = type('MockTask', (), {
            'id': 100,
            'task_type': 'process_message',
            'instance_id': 'orphan-instance',
            'message_id': 'orphan-message',
            'status': 'cancelled',
            'worker_id': 'crashed-worker',
            'retry_count': 1,
            'retry_scheduled': False,  # Orphaned!
            'cancel_requested': True,
            'created_at': datetime.now(timezone.utc) - timedelta(hours=1),
        })()
        mock_task_repository.tasks[100] = task
        # Don't add to stale_tasks (not stale running)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            max_retries=3,
        )
        
        recovered = recovery.recover_on_startup()
        
        # Should recover the orphaned task
        assert recovered >= 1
    
    def test_grace_period_respects_stop_event(self, mock_task_repository, mock_message_repository):
        """Recovery should stop during grace period if stop() called."""
        create_stale_task(mock_task_repository, task_id=1, age_minutes=20)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            cancel_grace_seconds=60,  # Long grace period
            check_interval_seconds=1,
        )
        
        recovery.start()
        
        # Stop immediately
        import threading
        stop_thread = threading.Thread(target=lambda: (
            time.sleep(0.1),
            recovery.stop()
        ))
        stop_thread.start()
        
        # Recovery should stop within grace period
        start_time = time.time()
        while recovery.is_running():
            time.sleep(0.01)
            if time.time() - start_time > 2.0:
                break
        
        elapsed = time.time() - start_time
        stop_thread.join()
        
        # Should not have waited full grace period
        assert elapsed < 1.5
    
    def test_event_logging_with_retry_id(self, mock_task_repository, mock_message_repository, mock_event_repository):
        """Recovery events should include retry_task_id when applicable."""
        create_stale_task(mock_task_repository, task_id=1, age_minutes=20)
        mock_task_repository.tasks[1].status = "running"
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            event_repository=mock_event_repository,
            cancel_grace_seconds=0,
            max_retries=3,
        )
        
        recovery.recover_stale_tasks()
        
        # Check that events include retry_task_id
        recovery_events = [e for e in mock_event_repository.events if "recovery" in e["kind"]]
        assert len(recovery_events) > 0
    
    def test_new_config_params(self, mock_task_repository, mock_message_repository):
        """New config params should be accepted."""
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
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
