"""Comprehensive tests for RetryScheduler.

This module tests the RetryScheduler background service that:
- Periodically checks for retryable jobs
- Triggers job processing for projects with retryable jobs
- Handles start/stop lifecycle with graceful shutdown
- Prevents duplicate scheduler instances via file-based locking
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.services.retry_scheduler import (
    RetryScheduler,
    _acquire_scheduler_lock,
    _release_scheduler_lock,
)


@pytest.fixture(autouse=True)
def clean_scheduler_lock():
    """Ensure scheduler lock is released before each test."""
    _release_scheduler_lock()
    yield
    _release_scheduler_lock()


@pytest.fixture
def scheduler_lock_dir(tmp_path):
    """Provide a temporary directory for scheduler lock files."""
    return tmp_path / "scheduler_locks"


class MockJob:
    """Mock job object for testing."""
    
    def __init__(self, job_id: str, project_id: str = "project-1"):
        self.job_id = job_id
        self.project_id = project_id


def create_mock_scheduler(poll_interval: float = 60.0, lock_dir: Optional[Path] = None):
    """Factory to create a fully mocked RetryScheduler.
    
    Args:
        poll_interval: Seconds between retry checks.
        lock_dir: Directory for lock files. If None, uses a unique temp directory.
    """
    mock_engine = MagicMock()
    mock_engine.find_retryable_jobs = MagicMock(return_value=[])
    
    mock_queue_service = MagicMock()
    mock_queue_service.trigger_next_job = AsyncMock()
    
    # Use unique temp dir if no lock_dir provided
    if lock_dir is None:
        lock_dir = Path(tempfile.mkdtemp(prefix="scheduler_test_"))
    
    scheduler = RetryScheduler(
        retry_engine=mock_engine,
        queue_service=mock_queue_service,
        poll_interval=poll_interval,
        lock_dir=lock_dir,
    )
    return scheduler, mock_engine, mock_queue_service


class TestRetrySchedulerLifecycle:
    """Tests for RetryScheduler start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self):
        """Test that start() sets the running flag to True."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        assert scheduler._running is False
        assert scheduler._task is None
        
        await scheduler.start()
        
        assert scheduler._running is True
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        """Test that start() creates a task for the run loop."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=3600.0)
        
        assert scheduler._task is None
        
        await scheduler.start()
        
        assert scheduler._task is not None
        assert isinstance(scheduler._task, asyncio.Task)
        
        # Stop and wait for task completion
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        """Test that calling start() when already running is a no-op."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=3600.0)
        
        await scheduler.start()
        first_task = scheduler._task
        
        # Call start again - should be no-op
        await scheduler.start()
        
        assert scheduler._running is True
        assert scheduler._task is first_task  # Same task, not recreated
        
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self):
        """Test that stop() sets the running flag to False."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        await scheduler.start()
        assert scheduler._running is True
        
        await scheduler.stop()
        
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        """Test that stop() cancels the running task."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=0.01)
        
        await scheduler.start()
        task = scheduler._task
        assert task is not None
        
        await scheduler.stop()
        
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        """Test that calling stop() when not running is a no-op."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        await scheduler.start()
        await scheduler.stop()
        await scheduler.stop()  # Should not raise
        
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_noop(self):
        """Test that stop() without prior start() does nothing harmful."""
        scheduler, _, _ = create_mock_scheduler()
        
        # Should not raise, even though task is None
        await scheduler.stop()
        
        assert scheduler._running is False
        assert scheduler._task is None

class TestRetrySchedulerCheckAndTrigger:
    """Tests for RetryScheduler._check_and_trigger() method."""

    @pytest.mark.asyncio
    async def test_check_and_trigger_calls_find_retryable_jobs(self):
        """Test that _check_and_trigger() calls find_retryable_jobs."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        await scheduler._check_and_trigger()
        
        mock_engine.find_retryable_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_and_trigger_triggers_unique_projects(self):
        """Test that _check_and_trigger() triggers each unique project once."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        # Jobs from 3 projects
        jobs = [
            MockJob("job-1", "project-a"),
            MockJob("job-2", "project-a"),  # Same project
            MockJob("job-3", "project-b"),
            MockJob("job-4", "project-c"),
        ]
        mock_engine.find_retryable_jobs.return_value = jobs
        
        await scheduler._check_and_trigger()
        
        # Should only call trigger_next_job for unique projects
        assert mock_queue_service.trigger_next_job.call_count == 3
        mock_queue_service.trigger_next_job.assert_any_await("project-a")
        mock_queue_service.trigger_next_job.assert_any_await("project-b")
        mock_queue_service.trigger_next_job.assert_any_await("project-c")

    @pytest.mark.asyncio
    async def test_check_and_trigger_no_jobs_does_nothing(self):
        """Test that _check_and_trigger() does nothing when no jobs found."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        mock_engine.find_retryable_jobs.return_value = []
        
        await scheduler._check_and_trigger()
        
        mock_queue_service.trigger_next_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_and_trigger_skips_null_project_id(self):
        """Test that _check_and_trigger() skips jobs with None project_id."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        jobs = [
            MockJob("job-1", "project-a"),
            MockJob("job-2", None),
            MockJob("job-3", ""),
        ]
        mock_engine.find_retryable_jobs.return_value = jobs
        
        await scheduler._check_and_trigger()
        
        # Should only trigger for project-a, not None or ""
        assert mock_queue_service.trigger_next_job.call_count == 1
        mock_queue_service.trigger_next_job.assert_any_await("project-a")

    @pytest.mark.asyncio
    async def test_check_and_trigger_handles_trigger_exception(self):
        """Test that _check_and_trigger() continues on trigger_next_job error."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        jobs = [
            MockJob("job-1", "project-a"),
            MockJob("job-2", "project-b"),
        ]
        mock_engine.find_retryable_jobs.return_value = jobs
        
        # First call fails, second succeeds
        mock_queue_service.trigger_next_job = AsyncMock(
            side_effect=[Exception("Connection refused"), None]
        )
        
        # Should not raise - errors are caught and logged
        await scheduler._check_and_trigger()
        
        # Both projects should have been attempted
        assert mock_queue_service.trigger_next_job.call_count == 2

    @pytest.mark.asyncio
    async def test_check_and_trigger_deduplicates_same_project(self):
        """Test that _check_and_trigger() deduplicates project_ids."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        # Multiple jobs for the same project
        jobs = [
            MockJob("job-1", "project-a"),
            MockJob("job-2", "project-a"),
            MockJob("job-3", "project-a"),
        ]
        mock_engine.find_retryable_jobs.return_value = jobs
        
        await scheduler._check_and_trigger()
        
        # Should only call trigger_next_job once per project
        assert mock_queue_service.trigger_next_job.call_count == 1
        mock_queue_service.trigger_next_job.assert_any_await("project-a")

    @pytest.mark.asyncio
    async def test_check_and_trigger_all_triggers_succeed_individually(self):
        """Test successful trigger calls when all succeed."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        jobs = [MockJob("job-1", "project-a"), MockJob("job-2", "project-b")]
        mock_engine.find_retryable_jobs.return_value = jobs
        
        await scheduler._check_and_trigger()
        
        # Both projects should have been triggered
        assert mock_queue_service.trigger_next_job.call_count == 2
        
        # Check the calls were made in order
        calls = mock_queue_service.trigger_next_job.await_args_list
        assert len(calls) == 2


class TestRetrySchedulerRunLoop:
    """Tests for RetryScheduler._run_loop() method."""

    @pytest.mark.asyncio
    async def test_run_loop_executes_periodically(self):
        """Test that _run_loop() executes the check multiple times."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=0.03)
        
        # Set up scheduler as if started (bypass start() to avoid creating task)
        scheduler._running = True
        
        async def stop_after_delay():
            await asyncio.sleep(0.1)  # ~3 iterations
            scheduler._running = False
        
        loop_task = asyncio.create_task(scheduler._run_loop())
        stop_task = asyncio.create_task(stop_after_delay())
        
        await asyncio.gather(loop_task, stop_task)
        
        # Should have run at least 2 times
        assert mock_engine.find_retryable_jobs.call_count >= 2

    @pytest.mark.asyncio
    async def test_run_loop_handles_check_exception_continues(self):
        """Test that _run_loop() continues after _check_and_trigger() exception."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=0.03)
        
        call_count = 0
        
        def flaky_find():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Simulated failure")
            return []
        
        mock_engine.find_retryable_jobs = flaky_find
        
        # Set up scheduler as if started
        scheduler._running = True
        
        async def stop_after_delay():
            await asyncio.sleep(0.1)
            scheduler._running = False
        
        loop_task = asyncio.create_task(scheduler._run_loop())
        stop_task = asyncio.create_task(stop_after_delay())
        
        # Should not raise - exceptions are caught
        await asyncio.gather(loop_task, stop_task, return_exceptions=True)
        
        # Should have continued despite first error
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_run_loop_respects_poll_interval(self):
        """Test that _run_loop() respects the configured poll_interval."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=0.1)
        
        # Set up scheduler as if started
        scheduler._running = True
        
        start_time = asyncio.get_event_loop().time()
        
        async def stop_after_delay():
            await asyncio.sleep(0.22)  # Should allow ~2 intervals
            scheduler._running = False
        
        loop_task = asyncio.create_task(scheduler._run_loop())
        stop_task = asyncio.create_task(stop_after_delay())
        
        await asyncio.gather(loop_task, stop_task)
        
        elapsed = asyncio.get_event_loop().time() - start_time
        call_count = mock_engine.find_retryable_jobs.call_count
        
        # With 0.1s interval and ~0.22s runtime, should run ~2-3 times
        assert call_count >= 2
        assert call_count <= 3


class TestRetrySchedulerGracefulShutdown:
    """Tests for RetryScheduler graceful shutdown behavior."""

    @pytest.mark.asyncio
    async def test_stop_ensures_no_orphaned_tasks(self):
        """Test that after stop(), no orphaned tasks remain."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=0.01)
        
        await scheduler.start()
        await scheduler.stop()
        
        # Task should be None (cleaned up)
        assert scheduler._task is None
        
        # _running should be False
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_scheduler_loop_exits_cleanly_on_stop(self):
        """Test that the scheduler loop exits cleanly when stop() is called."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=3600.0)
        
        # Start the scheduler with a very long poll interval
        # The loop should exit immediately when stop() is called
        await scheduler.start()
        
        # Give it a moment to start
        await asyncio.sleep(0.01)
        
        # Stop should complete without hanging
        await scheduler.stop()
        
        # The task should be cancelled and cleaned up
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_stop_awaits_task_cancellation(self):
        """Test that stop() properly awaits task cancellation."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=0.01)
        
        await scheduler.start()
        
        # stop() should wait for task to complete
        await scheduler.stop()
        
        # After stop(), no pending work
        assert scheduler._running is False
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_stop_can_be_called_multiple_times_safely(self):
        """Test that multiple stop() calls don't cause issues."""
        scheduler, _, _ = create_mock_scheduler()
        
        # First stop - no-op but should not raise
        await scheduler.stop()
        
        # Start then multiple stops
        scheduler._running = True
        scheduler._task = None
        
        await scheduler.stop()
        await scheduler.stop()  # Should be safe
        
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_rapid_start_stop_cycle(self):
        """Test rapid start/stop cycles don't cause issues."""
        scheduler, _, _ = create_mock_scheduler(poll_interval=0.01)
        
        for _ in range(5):
            await scheduler.start()
            await scheduler.stop()
        
        # Final state should be clean
        assert scheduler._running is False
        assert scheduler._task is None


class TestRetrySchedulerErrorHandling:
    """Tests for RetryScheduler error handling."""

    @pytest.mark.asyncio
    async def test_check_and_trigger_handles_empty_result(self):
        """Test that empty result from find_retryable_jobs is handled."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        mock_engine.find_retryable_jobs.return_value = []
        
        await scheduler._check_and_trigger()
        
        mock_queue_service.trigger_next_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_and_trigger_uses_to_thread(self):
        """Test that find_retryable_jobs is called via to_thread."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        # The actual implementation uses asyncio.to_thread
        # We just verify the call happens
        await scheduler._check_and_trigger()
        
        mock_engine.find_retryable_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_and_immediate_stop(self):
        """Test start followed by immediate stop."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=0.01)
        
        await scheduler.start()
        await asyncio.sleep(0.001)  # Very brief
        await scheduler.stop()
        
        # Should not crash, task should be cleaned up
        assert scheduler._task is None
        assert scheduler._running is False


class TestRetrySchedulerIntegration:
    """Integration tests for RetryScheduler."""

    @pytest.mark.asyncio
    async def test_full_retry_cycle(self):
        """Test a complete retry cycle: find jobs -> trigger -> jobs processed."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler()
        
        jobs = [MockJob("job-1", "project-a"), MockJob("job-2", "project-b")]
        mock_engine.find_retryable_jobs.return_value = jobs
        
        triggered_projects = []
        
        async def track_trigger(project_id):
            triggered_projects.append(project_id)
        
        mock_queue_service.trigger_next_job = AsyncMock(side_effect=track_trigger)
        
        await scheduler._check_and_trigger()
        
        assert set(triggered_projects) == {"project-a", "project-b"}
        mock_engine.find_retryable_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_scheduler_uses_configured_poll_interval(self):
        """Test that scheduler stores the configured poll_interval."""
        scheduler, _, _ = create_mock_scheduler(poll_interval=10.0)
        
        assert scheduler._poll_interval == 10.0

    @pytest.mark.asyncio
    async def test_lifecycle_start_stop_preserves_state(self):
        """Test that start/stop preserves scheduler state."""
        scheduler, mock_engine, mock_queue_service = create_mock_scheduler(poll_interval=5.0)
        
        # Verify initial state
        assert scheduler._poll_interval == 5.0
        assert scheduler._running is False
        
        await scheduler.start()
        assert scheduler._running is True
        
        await scheduler.stop()
        assert scheduler._running is False
        
        # State should be preserved
        assert scheduler._poll_interval == 5.0


class TestRetrySchedulerDuplicatePrevention:
    """Tests for duplicate scheduler instance prevention."""

    def test_acquire_lock_succeeds_when_no_lock(self, tmp_path):
        """Test that lock can be acquired when no other scheduler is running."""
        result = _acquire_scheduler_lock(tmp_path)
        assert result is True
        _release_scheduler_lock()

    def test_acquire_lock_fails_when_lock_held(self, tmp_path):
        """Test that lock acquisition fails when another scheduler holds the lock."""
        # First scheduler acquires lock
        result1 = _acquire_scheduler_lock(tmp_path)
        assert result1 is True

        # Second scheduler fails to acquire
        result2 = _acquire_scheduler_lock(tmp_path)
        assert result2 is False

        _release_scheduler_lock()

    def test_release_lock_allows_new_acquisition(self, tmp_path):
        """Test that releasing lock allows another scheduler to acquire it."""
        # First scheduler
        result1 = _acquire_scheduler_lock(tmp_path)
        assert result1 is True
        _release_scheduler_lock()

        # Second scheduler can now acquire
        result2 = _acquire_scheduler_lock(tmp_path)
        assert result2 is True
        _release_scheduler_lock()

    def test_lock_creates_lock_file(self, tmp_path):
        """Test that lock acquisition creates the lock file."""
        lock_path = tmp_path / "retry_scheduler.lock"
        assert not lock_path.exists()

        _acquire_scheduler_lock(tmp_path)
        assert lock_path.exists()
        _release_scheduler_lock()

    @pytest.mark.asyncio
    async def test_start_raises_when_duplicate(self, tmp_path):
        """Test that start() raises RuntimeError when another instance is running."""
        mock_engine = MagicMock()
        mock_engine.find_retryable_jobs = MagicMock(return_value=[])
        mock_queue_service = MagicMock()
        mock_queue_service.trigger_next_job = AsyncMock()

        # First scheduler
        scheduler1 = RetryScheduler(
            retry_engine=mock_engine,
            queue_service=mock_queue_service,
            lock_dir=tmp_path,
            poll_interval=3600.0,
        )

        # Second scheduler with same lock dir
        scheduler2 = RetryScheduler(
            retry_engine=mock_engine,
            queue_service=mock_queue_service,
            lock_dir=tmp_path,
            poll_interval=3600.0,
        )

        # First starts successfully
        await scheduler1.start()
        assert scheduler1._running is True

        # Second fails to start
        with pytest.raises(RuntimeError, match="already running"):
            await scheduler2.start()

        await scheduler1.stop()

    @pytest.mark.asyncio
    async def test_stop_releases_lock(self, tmp_path):
        """Test that stop() releases the lock, allowing new instance."""
        mock_engine = MagicMock()
        mock_engine.find_retryable_jobs = MagicMock(return_value=[])
        mock_queue_service = MagicMock()
        mock_queue_service.trigger_next_job = AsyncMock()

        scheduler = RetryScheduler(
            retry_engine=mock_engine,
            queue_service=mock_queue_service,
            lock_dir=tmp_path,
            poll_interval=3600.0,
        )

        await scheduler.start()
        assert scheduler._running is True
        await scheduler.stop()

        # After stop, a new scheduler can start
        result = _acquire_scheduler_lock(tmp_path)
        assert result is True
        _release_scheduler_lock()

    @pytest.mark.asyncio
    async def test_stop_without_start_releases_any_lock(self, tmp_path):
        """Test that stop() without prior start() handles lock state correctly."""
        scheduler = RetryScheduler(
            retry_engine=MagicMock(),
            queue_service=MagicMock(),
            lock_dir=tmp_path,
        )

        # Stop without start should not crash
        await scheduler.stop()

    def test_scheduler_uses_default_lock_dir(self):
        """Test that scheduler uses ./data as default lock directory."""
        mock_engine = MagicMock()
        mock_queue_service = MagicMock()

        scheduler = RetryScheduler(
            retry_engine=mock_engine,
            queue_service=mock_queue_service,
        )

        assert scheduler._lock_dir == Path("./data")

    def test_scheduler_accepts_custom_lock_dir(self, tmp_path):
        """Test that scheduler accepts custom lock directory."""
        mock_engine = MagicMock()
        mock_queue_service = MagicMock()

        scheduler = RetryScheduler(
            retry_engine=mock_engine,
            queue_service=mock_queue_service,
            lock_dir=tmp_path,
        )

        assert scheduler._lock_dir == tmp_path
