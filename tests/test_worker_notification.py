"""Tests for WorkerPool notification mechanism."""

import pytest
import threading
import time
from unittest.mock import Mock

from daemon.services.worker_pool import Worker, WorkerPool


class MockTaskProcessor:
    """Mock task processor that always returns None (no tasks)."""
    
    def __init__(self):
        self.claim_count = 0
        self.run_count = 0
        self.claimed_tasks = []
    
    def claim_task(self, worker_id):
        self.claim_count += 1
        return None  # Always signal no work available
    
    def run_task(self, task, cancellation_token=None):
        self.run_count += 1
    
    def get_pending_count(self):
        return 0


class TestNotificationMechanism:
    """Tests for the WorkerPool notification coordination."""
    
    def test_notify_work_wakes_waiting_thread(self):
        """wait_for_work should return True when notify_work() is called."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)
        
        result_holder = {"result": None, "elapsed": None}
        start_event = threading.Event()
        
        def wait_thread():
            start_time = time.time()
            start_event.set()  # Signal that we're about to wait
            result = pool.wait_for_work(timeout=5.0)
            elapsed = time.time() - start_time
            result_holder["result"] = result
            result_holder["elapsed"] = elapsed
        
        # Start waiting thread
        wait_thread_handle = threading.Thread(target=wait_thread)
        wait_thread_handle.start()
        
        # Wait for the thread to start waiting
        start_event.wait(timeout=1.0)
        time.sleep(0.05)  # Small buffer to ensure wait() is called
        
        # Notify from main thread
        pool.notify_work()
        
        # Wait for result
        wait_thread_handle.join(timeout=2.0)
        
        assert result_holder["result"] is True, "wait_for_work should return True when notified"
        assert result_holder["elapsed"] < 1.0, f"Should wake quickly, took {result_holder['elapsed']:.3f}s"
    
    def test_wait_for_work_returns_false_on_timeout(self):
        """wait_for_work should return False when no notification arrives."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)
        
        start_time = time.time()
        result = pool.wait_for_work(timeout=0.1)
        elapsed = time.time() - start_time
        
        assert result is False, "wait_for_work should return False on timeout"
        assert elapsed >= 0.1, "Should have waited approximately the timeout duration"
        assert pool._stats["workers_woken_by_timeout"] == 1, \
            "workers_woken_by_timeout should increment on timeout"
    
    def test_stop_wakes_sleeping_workers(self):
        """stop() should wake all workers and complete within timeout."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=2)
        
        pool.start()
        time.sleep(0.2)  # Let workers settle into waiting
        
        # Stop should not hang
        start_time = time.time()
        pool.stop(timeout=5.0)
        elapsed = time.time() - start_time
        
        assert elapsed < 2.0, f"stop() took too long: {elapsed:.3f}s"
        assert not pool.is_running(), "Pool should not be running after stop"
        assert pool._stopped, "Pool should be marked as stopped"
    
    def test_metrics_increments(self):
        """Pool should track notification metrics correctly."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)
        
        # Test notifications_sent increment
        pool.notify_work()
        pool.notify_work()
        pool.notify_work()
        assert pool._stats["notifications_sent"] == 3, \
            "notifications_sent should be 3 after 3 notify_work() calls"
        
        # Consume all notifications first (they return True, no timeout increment)
        assert pool.wait_for_work(timeout=0.01) is True
        assert pool.wait_for_work(timeout=0.01) is True
        assert pool.wait_for_work(timeout=0.01) is True
        
        # Now test workers_woken_by_timeout increment with no pending notifications
        pool.wait_for_work(timeout=0.05)
        pool.wait_for_work(timeout=0.05)
        assert pool._stats["workers_woken_by_timeout"] == 2, \
            "workers_woken_by_timeout should be 2 after 2 timeouts"
        
        # Verify via get_stats()
        stats = pool.get_stats()
        assert stats["notifications_sent"] == 3
        assert stats["workers_woken_by_timeout"] == 2
    
    def test_concurrent_notify_work(self):
        """Multiple threads calling notify_work() concurrently should not cause issues."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)
        
        errors = []
        
        def notify_many():
            try:
                for _ in range(10):
                    pool.notify_work()
            except Exception as e:
                errors.append(e)
        
        # Spawn 10 threads, each calling notify_work() 10 times = 100 total
        threads = [threading.Thread(target=notify_many) for _ in range(10)]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join(timeout=5.0)
        
        assert len(errors) == 0, f"notify_work() should not raise: {errors}"
        assert pool._stats["notifications_sent"] == 100, \
            f"notifications_sent should be 100, got {pool._stats['notifications_sent']}"
        assert pool._notification_count == 100, \
            f"_notification_count should be 100, got {pool._notification_count}"


class TestNotificationRaceConditions:
    """Tests for race conditions in notification mechanism."""
    
    def test_notification_consumed_by_single_waiter(self):
        """A single notification should be consumed by only one waiter."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)
        
        pool.notify_work()
        
        # First waiter should consume it
        result1 = pool.wait_for_work(timeout=0.05)
        assert result1 is True
        
        # Second waiter should timeout (notification consumed)
        result2 = pool.wait_for_work(timeout=0.05)
        assert result2 is False
    
    def test_multiple_notifications_for_multiple_waiters(self):
        """Multiple notifications should satisfy multiple waiters."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)
        
        # Send 3 notifications
        pool.notify_work()
        pool.notify_work()
        pool.notify_work()
        
        results = []
        for _ in range(3):
            results.append(pool.wait_for_work(timeout=0.05))
        
        assert all(results), "All 3 waiters should receive notifications"
        assert pool._notification_count == 0, "All notifications should be consumed"
    
    def test_wait_for_work_with_zero_timeout(self):
        """wait_for_work with timeout=0 should return immediately."""
        mock_processor = MockTaskProcessor()
        pool = WorkerPool(task_processor=mock_processor, num_workers=0)
        
        start_time = time.time()
        result = pool.wait_for_work(timeout=0.0)
        elapsed = time.time() - start_time
        
        assert result is False
        assert elapsed < 0.05, "Zero timeout should return immediately"
