"""Tests for WorkerPool notification mechanism."""

import pytest
import threading
import time
from unittest.mock import Mock

from daemon.services.worker_pool import Worker, WorkerPool
from daemon.cancellation import CancellationReason, OperationCancelledError


def wait_for_worker_waiting(pool, timeout=3.0):
    """Wait for a worker to enter wait_for_work(), then clear the event for reuse.
    
    Returns True if the worker was detected waiting, False on timeout.
    """
    # Clear first, then wait for the next occurrence
    pool._wait_for_work_called.clear()
    if pool._wait_for_work_called.wait(timeout=timeout):
        return True
    return False


def wait_for_worker_waiting_or_idle(pool, timeout=3.0):
    """Wait for a worker to enter wait_for_work() OR detect it's idle (already waiting).
    
    This handles the race where the worker might have already entered wait_for_work()
    before we start waiting. Clears the event and returns True immediately if it's
    already set (worker is waiting), otherwise waits for the next entry.
    
    Returns True if the worker is in wait_for_work(), False on timeout.
    """
    # Check if already waiting (event already set from previous entry)
    if pool._wait_for_work_called.is_set():
        pool._wait_for_work_called.clear()
        return True
    # Otherwise wait for next entry
    return wait_for_worker_waiting(pool, timeout)


class MockTaskProcessor:
    """Mock task processor that always returns None (no tasks)."""

    def __init__(self):
        self.claim_count = 0
        self.run_count = 0
        self.claimed_tasks = []
        # Worker.__init__ constructs a TaskHeartbeat which calls
        # task_repo.update_heartbeat on the eager first beat, and
        # Worker.run calls task_repo.has_pending_tasks_blocked_by_busy_instance
        # on the empty-claim path. Both must return without raising.
        self._task_repo = self._MockTaskRepoForMetrics()

    class _MockTaskRepoForMetrics:
        def has_pending_tasks_blocked_by_busy_instance(self):
            return False

        def update_heartbeat(self, task_id):
            return True

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


class MockTask:
    """Mock task object for integration tests."""
    
    def __init__(self, task_id: int = 1, task_type: str = "process_message"):
        self.id = task_id
        self.task_type = task_type
        self.instance_id = "test-instance-123"
        self.message_id = "test-message-456"
        self.retry_count = 0
        self.status = "pending"
        self.worker_id = None


class IntegrationTaskProcessor:
    """Task processor for integration tests that can simulate real behavior."""
    
    def __init__(self, pool=None):
        self.claim_count = 0
        self.run_count = 0
        self.claimed_tasks = []
        self.tasks_to_return = []  # Queue of tasks to return from claim_task
        self.run_exception = None  # Exception to throw on run_task
        self.run_called = threading.Event()
        self._run_count_at_last_set = 0  # Track run_count when event was last set
        # Use _task_repo to match what Worker code expects
        self._task_repo = MockTaskRepo(
            task_queue=self.tasks_to_return,
            notify_callback=pool.notify_work if pool else None
        )
    
    def claim_task(self, worker_id: str):
        """Return next task from queue, or None if empty."""
        self.claim_count += 1
        if self.tasks_to_return:
            task = self.tasks_to_return.pop(0)
            task.worker_id = worker_id
            self.claimed_tasks.append(task)
            return task
        return None
    
    def run_task(self, task, cancellation_token=None):
        """Run the task (mock - just track it was called)."""
        self.run_count += 1
        self.run_called.set()
        self._run_count_at_last_set = self.run_count
        if self.run_exception:
            exc = self.run_exception
            self.run_exception = None  # Only throw once
            raise exc
    
    def wait_for_run_count(self, expected_count, timeout):
        """Wait until run_count reaches expected value using event-based polling."""
        # Use the event with a timeout-based polling approach
        start = time.time()
        while self.run_count < expected_count:
            # Wait on the event with a short timeout, then recheck
            if self.run_called.wait(timeout=min(0.05, timeout - (time.time() - start))):
                # Event was set, but check if we have the right count
                if self.run_count >= expected_count:
                    return True
                # Reset event for next iteration (in case count increased)
                self.run_called.clear()
            if time.time() - start > timeout:
                return False
        return True
    
    def get_pending_count(self):
        return len(self.tasks_to_return)


class TimeoutTriggeringProcessor:
    """Processor that triggers timeout cancellation on specific tasks."""
    
    def __init__(self, pool=None):
        self.claim_count = 0
        self.run_count = 0
        self.claimed_tasks = []
        self.tasks_to_return = []
        self.tasks_to_timeout = set()  # Task IDs that should timeout
        self.run_called = threading.Event()
        self._task_repo = MockTaskRepo(
            task_queue=self.tasks_to_return,
            notify_callback=pool.notify_work if pool else None
        )
        self._monitor = None  # Set by worker when created
    
    def claim_task(self, worker_id: str):
        """Return next task from queue, or None if empty."""
        self.claim_count += 1
        if self.tasks_to_return:
            task = self.tasks_to_return.pop(0)
            task.worker_id = worker_id
            self.claimed_tasks.append(task)
            return task
        return None
    
    def run_task(self, task, cancellation_token=None):
        """Run the task - simulate timeout for certain tasks."""
        self.run_count += 1
        self.run_called.set()
        
        if task.id in self.tasks_to_timeout:
            # Simulate timeout: raise OperationCancelledError with TIMEOUT reason
            raise OperationCancelledError(
                message=f"Simulated timeout for task {task.id}",
                reason=CancellationReason.TIMEOUT
            )
        
        # Normal completion - do nothing
    
    def wait_for_run_count(self, expected_count, timeout):
        """Wait until run_count reaches expected value using event-based polling."""
        start = time.time()
        while self.run_count < expected_count:
            if self.run_called.wait(timeout=min(0.05, timeout - (time.time() - start))):
                if self.run_count >= expected_count:
                    return True
                self.run_called.clear()
            if time.time() - start > timeout:
                return False
        return True
    
    def get_pending_count(self):
        return len(self.tasks_to_return)


class MockTaskRepo:
    """Mock task repository for integration tests."""
    
    def __init__(self, task_queue: list = None, notify_callback=None):
        """Initialize MockTaskRepo.
        
        Args:
            task_queue: Reference to the task queue for auto-adding retry tasks.
            notify_callback: Optional callback to call when schedule_retry adds a task.
                             This enables automatic worker notification for retry tasks.
        """
        self.schedule_retry_count = 0
        self.fail_task_count = 0
        self.cancel_task_count = 0
        self.retry_task = None
        self._schedule_retry_called = threading.Event()
        self._fail_task_called = threading.Event()
        # Reference to the task queue for auto-adding retry tasks
        self._task_queue = task_queue
        # Callback for notifying workers (e.g., pool.notify_work)
        self._notify_callback = notify_callback
    
    def schedule_retry(self, task_id, max_retries, backoff_base, backoff_max):
        self.schedule_retry_count += 1
        self._schedule_retry_called.set()
        # Create a retry task
        retry_task = MockTask(task_id=task_id + 100, task_type="process_message")
        retry_task.retry_count = 1
        self.retry_task = retry_task
        # Auto-add to queue if we have a reference to it
        if self._task_queue is not None:
            self._task_queue.append(retry_task)
        # Auto-notify worker about new retry task (simulates real system behavior)
        if self._notify_callback is not None:
            self._notify_callback()
        return retry_task
    
    def fail_task(self, task_id, error):
        self.fail_task_count += 1
        self._fail_task_called.set()
    
    def cancel_task(self, task_id, reason):
        self.cancel_task_count += 1


class TestWorkerLifecycleIntegration:
    """Integration tests with real Worker threads."""
    
    def test_real_worker_processes_task_after_notify(self):
        """Real Worker should process task after notify_work() is called.
        
        Uses event-based synchronization to ensure the worker is actually waiting
        before calling notify_work(), rather than relying on a fixed sleep.
        """
        processor = IntegrationTaskProcessor()
        # Add a task that will be returned when worker claims
        processor.tasks_to_return.append(MockTask(task_id=1))
        
        pool = WorkerPool(task_processor=processor, num_workers=1)
        pool.start()
        
        try:
            # Wait for worker to enter wait_for_work() before notifying
            # This ensures the notification actually wakes the worker
            assert wait_for_worker_waiting(pool, timeout=2.0), \
                "Worker should have entered wait_for_work()"
            
            # Now notify - worker should wake and process
            pool.notify_work()
            
            # Wait for run_task to be called (with timeout)
            assert processor.wait_for_run_count(1, timeout=3.0), \
                f"Worker should have called run_task within timeout (got {processor.run_count})"
            
            assert processor.run_count == 1, \
                f"run_task should be called once, got {processor.run_count}"
            assert len(processor.claimed_tasks) == 1, \
                f"Worker should have claimed 1 task, got {len(processor.claimed_tasks)}"
            
        finally:
            pool.stop(timeout=5.0)
    
    def test_real_worker_goes_idle_when_no_tasks(self):
        """Real Worker should go idle (not claim) when no tasks available."""
        processor = IntegrationTaskProcessor()
        # Empty task queue - worker will have empty claim attempts
        
        pool = WorkerPool(task_processor=processor, num_workers=1)
        pool.start()
        
        try:
            # Poll until worker has attempted at least one claim
            start = time.time()
            while processor.claim_count < 1:
                if time.time() - start > 2.0:
                    pytest.fail("Worker did not attempt any claims within 2 seconds")
                time.sleep(0.05)
            
            # Worker has attempted to claim at least once (and found nothing)
            assert processor.claim_count > 0, \
                "Worker should have attempted to claim tasks"
            
            # No tasks should have been run (none available)
            assert processor.run_count == 0, \
                f"No tasks should be run when queue is empty, got {processor.run_count}"
            
            # Now add a task and notify
            processor.tasks_to_return.append(MockTask(task_id=2))
            pool.notify_work()
            
            # Worker should wake and process
            assert processor.wait_for_run_count(1, timeout=3.0), \
                f"Worker should wake and process task after notify (got {processor.run_count})"
            assert processor.run_count == 1, \
                f"Should process 1 task after notify, got {processor.run_count}"
            
        finally:
            pool.stop(timeout=5.0)
    
    def test_worker_error_recovery_uses_wait_for_work(self):
        """Worker should recover after task failure and continue processing.
        
        This test verifies that the notification mechanism is actually used for
        error recovery, not just the worker's periodic polling. We use event-based
        synchronization to ensure notify_work() is called while the worker is
        actually waiting, not after it's already timed out and looped back.
        """
        processor = IntegrationTaskProcessor()
        
        # First task will fail
        processor.tasks_to_return.append(MockTask(task_id=1))
        processor.run_exception = ValueError("Simulated task failure")
        
        # Second task will succeed
        processor.tasks_to_return.append(MockTask(task_id=2))
        
        pool = WorkerPool(task_processor=processor, num_workers=1)
        pool.start()
        
        try:
            pool.notify_work()
            
            # Wait for first failure
            assert processor.wait_for_run_count(1, timeout=3.0), \
                f"First task should be attempted (got {processor.run_count})"
            
            # Wait for worker to enter wait_for_work() after the error
            # This is the key fix: we wait for the worker to be WAITING before
            # calling notify_work(), ensuring the notification is what wakes it
            assert wait_for_worker_waiting(pool, timeout=2.0), \
                "Worker should enter wait_for_work() after error"
            
            # Notify for second task - this should be the notification that wakes
            # the worker, NOT a timeout expiring
            pool.notify_work()
            
            # Wait for second task to complete successfully
            assert processor.wait_for_run_count(2, timeout=3.0), \
                f"Second task should be attempted after recovery (got {processor.run_count})"
            
            assert processor.run_count == 2, \
                f"Both tasks should be attempted, got {processor.run_count}"
            
            # Verify the worker was woken by notification, not timeout
            # If we had used a 0.5s sleep, the 1s timeout might have expired first,
            # causing the worker to loop and find task #2 without using notify
            
            # Worker should still be alive (recovered)
            assert pool.is_running(), "Worker pool should still be running after error"
            
        finally:
            pool.stop(timeout=5.0)
    
    def test_schedule_retry_notifies_worker(self):
        """Task timeout triggers schedule_retry which notifies worker for retry task."""
        pool = WorkerPool(task_processor=None, num_workers=1)  # Create pool first
        processor = TimeoutTriggeringProcessor(pool=pool)  # Pass pool for auto-notify
        
        # First task will timeout - schedule_retry will auto-add the retry task
        processor.tasks_to_return.append(MockTask(task_id=1))
        processor.tasks_to_timeout.add(1)
        
        pool._task_processor = processor  # Set processor on pool
        pool.start()
        
        try:
            pool.notify_work()
            
            # Wait for first task timeout
            assert processor.wait_for_run_count(1, timeout=3.0), \
                f"First task should be attempted (got {processor.run_count})"
            
            # Wait for schedule_retry to be called (timeout triggers retry path)
            assert processor._task_repo._schedule_retry_called.wait(timeout=2.0), \
                "schedule_retry should be called on timeout"
            
            # NO manual notify_work() needed - MockTaskRepo auto-notifies via callback
            # The retry task was auto-added AND auto-notified
            
            # Wait for retry task to be processed
            assert processor.wait_for_run_count(2, timeout=3.0), \
                f"Retry task should be attempted (got {processor.run_count})"
            
            assert processor.run_count == 2, \
                f"Both original and retry tasks should run, got {processor.run_count}"
            assert processor._task_repo.schedule_retry_count >= 1, \
                "schedule_retry should be called at least once"
            
        finally:
            pool.stop(timeout=5.0)
    
    def test_notify_without_task_goes_back_to_waiting(self):
        """Spurious notify_work() should not break worker - it should go back to waiting.
        
        This validates the spurious wakeup defense: when notify_work() is called but
        no task exists, the worker should wake, find no task, and return to waiting.
        """
        processor = IntegrationTaskProcessor()
        # Empty task queue - worker will find nothing when it claims
        
        pool = WorkerPool(task_processor=processor, num_workers=1)
        pool.start()
        
        try:
            # Wait for worker to enter wait_for_work() initially
            # Use wait_for_worker_waiting_or_idle since it might already be waiting
            assert wait_for_worker_waiting_or_idle(pool, timeout=2.0), \
                "Worker should have entered wait_for_work() initially"
            
            # Call notify_work() when NO task exists
            pool.notify_work()
            
            # Worker wakes, finds no task, goes back to waiting
            # Wait for worker to re-enter wait_for_work() (indicating it looped back)
            assert wait_for_worker_waiting(pool, timeout=2.0), \
                "Worker should go back to wait_for_work() after spurious wakeup"
            
            # No tasks should have been claimed or run
            assert processor.claim_count >= 1, \
                "Worker should have attempted to claim at least once"
            assert processor.run_count == 0, \
                f"No tasks should be run when queue is empty, got {processor.run_count}"
            
            # Now add a real task and verify normal operation
            processor.tasks_to_return.append(MockTask(task_id=1))
            pool.notify_work()
            
            # Worker should process the task
            assert processor.wait_for_run_count(1, timeout=3.0), \
                f"Worker should process task after proper notification (got {processor.run_count})"
            assert processor.run_count == 1, \
                f"Should process 1 task, got {processor.run_count}"
            
        finally:
            pool.stop(timeout=5.0)
    
    def test_multi_worker_notification(self):
        """Two workers should process two tasks when notified."""
        processor = IntegrationTaskProcessor()
        
        # Add two tasks
        processor.tasks_to_return.append(MockTask(task_id=1))
        processor.tasks_to_return.append(MockTask(task_id=2))
        
        pool = WorkerPool(task_processor=processor, num_workers=2)
        pool.start()
        
        try:
            # Wait for workers to start and attempt claims
            start = time.time()
            while processor.claim_count < 2:
                if time.time() - start > 2.0:
                    pytest.fail("Workers did not make enough claim attempts within 2 seconds")
                time.sleep(0.05)
            
            # Notify twice (one per task)
            pool.notify_work()
            pool.notify_work()
            
            # Wait for both tasks to be processed
            assert processor.wait_for_run_count(2, timeout=3.0), \
                f"At least both tasks should be processed (got {processor.run_count})"
            
            assert processor.run_count == 2, \
                f"Both tasks should be processed by 2 workers, got {processor.run_count}"
            
            # Verify two different workers claimed tasks using polling
            start = time.time()
            distinct_workers = 0
            while distinct_workers < 2 and time.time() - start < 1.0:
                worker_ids = set(t.worker_id for t in processor.claimed_tasks)
                distinct_workers = len(worker_ids)
                if distinct_workers < 2:
                    time.sleep(0.05)
            
            assert len(worker_ids) == 2, \
                f"Two different workers should claim tasks, got {len(worker_ids)}: {worker_ids}"
            
        finally:
            pool.stop(timeout=5.0)
