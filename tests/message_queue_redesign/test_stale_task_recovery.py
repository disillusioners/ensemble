"""Tests for StaleTaskRecovery service."""

import pytest
import threading
import time
from datetime import datetime, timezone, timedelta

from daemon.services.stale_task_recovery import (
    StaleTaskRecovery,
    DEFAULT_STALE_THRESHOLD_MINUTES,
    DEFAULT_CHECK_INTERVAL_SECONDS,
)


class TestStaleTaskRecovery:
    """Tests for StaleTaskRecovery class."""
    
    def test_recovery_finds_stale_tasks(self, mock_task_repository, mock_message_repository):
        """Recovery should find tasks running too long."""
        stale_task = mock_task_repository.stale_tasks[0] if mock_task_repository.stale_tasks else None
        if stale_task is None:
            stale_task = type('MockTask', (), {
                'id': 1,
                'task_type': 'process_message',
                'instance_id': 'test-instance',
                'message_id': 'test-message',
                'status': 'running',
                'worker_id': 'test-worker',
                'started_at': datetime.now(timezone.utc) - timedelta(minutes=20),
            })()
            mock_task_repository.stale_tasks.append(stale_task)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            threshold_minutes=15,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 1
        assert mock_task_repository.reset_count == 1
    
    def test_recovery_skips_recent_tasks(self, mock_task_repository, mock_message_repository):
        """Recovery should not find tasks that are still running normally."""
        recent_task = type('MockTask', (), {
            'id': 2,
            'task_type': 'process_message',
            'instance_id': 'test-instance',
            'message_id': 'test-message-2',
            'status': 'running',
            'worker_id': 'test-worker',
            'started_at': datetime.now(timezone.utc) - timedelta(minutes=5),
        })()
        mock_task_repository.stale_tasks.append(recent_task)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            threshold_minutes=15,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 0
    
    def test_recovery_resets_multiple_stale_tasks(self, mock_task_repository, mock_message_repository):
        """Recovery should reset all stale tasks."""
        for i in range(3):
            task = type('MockTask', (), {
                'id': i,
                'task_type': 'process_message',
                'instance_id': f'test-instance-{i}',
                'message_id': f'test-message-{i}',
                'status': 'running',
                'worker_id': 'test-worker',
                'started_at': datetime.now(timezone.utc) - timedelta(minutes=20 + i),
            })()
            mock_task_repository.stale_tasks.append(task)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            threshold_minutes=15,
        )
        
        recovered = recovery.recover_stale_tasks()
        
        assert recovered == 3
    
    def test_recovery_on_startup(self, mock_task_repository, mock_message_repository):
        """recover_on_startup should run recovery immediately."""
        stale_task = type('MockTask', (), {
            'id': 1,
            'task_type': 'process_message',
            'instance_id': 'test-instance',
            'message_id': 'test-message',
            'status': 'running',
            'worker_id': 'test-worker',
            'started_at': datetime.now(timezone.utc) - timedelta(minutes=30),
        })()
        mock_task_repository.stale_tasks.append(stale_task)
        
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
        stale_task = type('MockTask', (), {
            'id': 1,
            'task_type': 'process_message',
            'instance_id': 'test-instance',
            'message_id': 'test-message',
            'status': 'running',
            'worker_id': 'test-worker',
            'started_at': datetime.now(timezone.utc) - timedelta(minutes=30),
        })()
        mock_task_repository.stale_tasks.append(stale_task)
        
        recovery = StaleTaskRecovery(
            task_repository=mock_task_repository,
            message_repository=mock_message_repository,
            event_repository=mock_event_repository,
        )
        
        recovery.recover_stale_tasks()
        
        assert len(mock_event_repository.events) == 1
        assert mock_event_repository.events[0]["kind"] == "task_recovered"
