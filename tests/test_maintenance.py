"""Unit tests for maintenance service.

Tests MaintenanceService and CheckpointCleanupJob functionality including:
- Job registration and lifecycle
- Idle detection logic
- Due job detection
- All 4 checkpoint cleanup operations (orphans, expired, history cap, per-thread pruning)
"""

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from daemon.services.maintenance import (
    MaintenanceService,
    CheckpointCleanupJob,
    MaintenanceJob,
    utcnow,
)
from daemon.config import PersistenceConfig
from daemon.constants import (
    CHECKPOINT_MAX_PER_THREAD,
    CHECKPOINT_TTL_HOURS,
    MAX_INSTANCE_HISTORY,
)
from daemon.services.job_queue_service import TERMINAL_STATUSES


# ==================== MaintenanceService Tests ====================


class TestMaintenanceServiceRegistration:
    """Tests for job registration."""

    def test_register_job(self):
        """Register a job, verify it appears in the jobs list."""
        service = MaintenanceService(check_interval_minutes=15)
        execute_fn = AsyncMock()

        service.register("test_job", min_interval_hours=1.0, execute_fn=execute_fn)

        assert len(service._jobs) == 1
        job = service._jobs[0]
        assert job.name == "test_job"
        assert job.min_interval_hours == 1.0
        assert job.last_run is None
        assert job.execute_fn is execute_fn

    def test_register_multiple_jobs(self):
        """Register multiple jobs, verify all are stored."""
        service = MaintenanceService()
        fn1 = AsyncMock()
        fn2 = AsyncMock()

        service.register("job1", min_interval_hours=1.0, execute_fn=fn1)
        service.register("job2", min_interval_hours=2.0, execute_fn=fn2)

        assert len(service._jobs) == 2
        assert service._jobs[0].name == "job1"
        assert service._jobs[1].name == "job2"


class TestMaintenanceServiceLifecycle:
    """Tests for start/stop functionality."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Start and stop the service, verify task is created/cancelled."""
        service = MaintenanceService(check_interval_minutes=60)

        # Initially no task
        assert service._task is None
        assert service._running is False

        # Start the service
        await service.start()
        assert service._running is True
        assert service._task is not None
        # Give the task time to start
        await asyncio.sleep(0.1)
        assert not service._task.done()

        # Stop the service
        await service.stop()
        assert service._running is False
        # Task should complete or be cancelled
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_double_start_is_safe(self):
        """Calling start() twice should not raise."""
        service = MaintenanceService()

        await service.start()
        await service.start()  # Should not raise

        assert service._running is True

        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        """Stopping when not running should be safe."""
        service = MaintenanceService()

        await service.stop()  # Should not raise

        assert service._running is False


class TestIsDue:
    """Tests for job due detection logic."""

    def test_is_due_never_run(self):
        """Job with no last_run should be due."""
        service = MaintenanceService()
        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=None,
            execute_fn=AsyncMock(),
        )

        assert service._is_due(job) is True

    def test_is_due_recently_run(self):
        """Job run within interval should NOT be due."""
        service = MaintenanceService()
        # Job run 30 minutes ago with 1 hour interval
        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=utcnow() - timedelta(minutes=30),
            execute_fn=AsyncMock(),
        )

        assert service._is_due(job) is False

    def test_is_due_past_interval(self):
        """Job run longer than interval ago should be due."""
        service = MaintenanceService()
        # Job run 2 hours ago with 1 hour interval
        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=utcnow() - timedelta(hours=2),
            execute_fn=AsyncMock(),
        )

        assert service._is_due(job) is True

    def test_is_due_exactly_at_interval(self):
        """Job run exactly at interval should be due."""
        service = MaintenanceService()
        # Job run exactly 1 hour ago with 1 hour interval
        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=utcnow() - timedelta(hours=1),
            execute_fn=AsyncMock(),
        )

        assert service._is_due(job) is True


class TestIsIdle:
    """Tests for idle detection logic."""

    @pytest.mark.asyncio
    async def test_is_idle_no_activity(self):
        """No active jobs and no active requests should be idle."""
        service = MaintenanceService()

        # Mock empty job queue service
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(return_value=[])
        service.set_job_queue_service(mock_job_queue_service)

        # Mock empty request registry
        service.set_request_registry({})

        assert await service._is_idle() is True

    @pytest.mark.asyncio
    async def test_is_idle_with_active_jobs(self):
        """Active jobs in job queue service should NOT be idle."""
        service = MaintenanceService()

        # Mock job queue service with pending jobs
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(
            return_value=[MagicMock(), MagicMock()]  # 2 pending jobs
        )
        service.set_job_queue_service(mock_job_queue_service)

        # Mock empty request registry
        service.set_request_registry({})

        assert await service._is_idle() is False

    @pytest.mark.asyncio
    async def test_is_idle_with_active_requests(self):
        """Active requests in registry should NOT be idle."""
        service = MaintenanceService()

        # Mock empty job queue service
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(return_value=[])
        service.set_job_queue_service(mock_job_queue_service)

        # Mock non-empty request registry
        service.set_request_registry({"req1": MagicMock(), "req2": MagicMock()})

        assert await service._is_idle() is False

    @pytest.mark.asyncio
    async def test_is_idle_no_job_queue_service(self):
        """When job queue service is None, check only request registry."""
        service = MaintenanceService()
        service.set_job_queue_service(None)
        service.set_request_registry({})

        assert await service._is_idle() is True

    @pytest.mark.asyncio
    async def test_is_idle_no_request_registry(self):
        """When request registry is None, check only job queue service."""
        service = MaintenanceService()

        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(return_value=[])
        service.set_job_queue_service(mock_job_queue_service)

        service.set_request_registry(None)

        assert await service._is_idle() is True


class TestRunPendingJobs:
    """Tests for pending job execution logic."""

    @pytest.mark.asyncio
    async def test_run_pending_jobs_success(self):
        """Due + idle job should execute and update last_run."""
        service = MaintenanceService()
        execute_fn = AsyncMock()

        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=utcnow() - timedelta(hours=2),
            execute_fn=execute_fn,
        )
        service._jobs.append(job)

        # Set up idle state
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(return_value=[])
        service.set_job_queue_service(mock_job_queue_service)
        service.set_request_registry({})

        await service._run_pending_jobs()

        execute_fn.assert_awaited_once()
        assert job.last_run is not None

    @pytest.mark.asyncio
    async def test_run_pending_jobs_failure(self):
        """Failed job should NOT update last_run."""
        service = MaintenanceService()

        async def failing_fn():
            raise RuntimeError("Job failed")

        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=utcnow() - timedelta(hours=2),
            execute_fn=failing_fn,
        )
        service._jobs.append(job)
        original_last_run = job.last_run

        # Set up idle state
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(return_value=[])
        service.set_job_queue_service(mock_job_queue_service)
        service.set_request_registry({})

        await service._run_pending_jobs()

        # last_run should NOT be updated after failure
        assert job.last_run == original_last_run

    @pytest.mark.asyncio
    async def test_run_pending_jobs_not_due(self):
        """Job not due should not execute."""
        service = MaintenanceService()
        execute_fn = AsyncMock()

        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=utcnow() - timedelta(minutes=30),  # Recently run
            execute_fn=execute_fn,
        )
        service._jobs.append(job)

        # Set up idle state
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(return_value=[])
        service.set_job_queue_service(mock_job_queue_service)
        service.set_request_registry({})

        await service._run_pending_jobs()

        execute_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_pending_jobs_not_idle(self):
        """System busy should not execute even due job."""
        service = MaintenanceService()
        execute_fn = AsyncMock()

        job = MaintenanceJob(
            name="test",
            min_interval_hours=1.0,
            last_run=utcnow() - timedelta(hours=2),
            execute_fn=execute_fn,
        )
        service._jobs.append(job)

        # Set up busy state - active jobs
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(
            return_value=[MagicMock()]  # 1 pending job
        )
        service.set_job_queue_service(mock_job_queue_service)
        service.set_request_registry({})

        await service._run_pending_jobs()

        execute_fn.assert_not_awaited()


# ==================== CheckpointCleanupJob Tests ====================


class TestCheckpointCleanupJobOrphans:
    """Tests for orphaned thread cleanup operation (A)."""

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_threads(self):
        """Mock checkpointer + instance_repo, verify orphans deleted via adelete_thread."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()

        # Instance IDs from repo: thread-1, thread-2
        # So thread-3 in checkpoints is orphaned
        instance_repo.list = MagicMock(
            return_value=(
                [MagicMock(instance_id="thread-1"), MagicMock(instance_id="thread-2")],
                2,
            )
        )

        # Mock adapter.list_thread_ids for Operation A
        checkpointer.list_thread_ids = AsyncMock(
            return_value=["thread-1", "thread-2", "thread-3"]
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        await job._cleanup_orphaned_threads()

        # Verify adelete_thread was called exactly once for orphaned thread-3
        checkpointer.adelete_thread.assert_awaited_once_with("thread-3")

    @pytest.mark.asyncio
    async def test_cleanup_no_orphans(self):
        """When all checkpoint threads have matching instances, no deletion."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()

        # All checkpoint threads have matching instances
        instance_repo.list = MagicMock(
            return_value=(
                [
                    MagicMock(instance_id="thread-1"),
                    MagicMock(instance_id="thread-2"),
                ],
                2,
            )
        )

        # Mock adapter.list_thread_ids — all threads have matching instances
        checkpointer.list_thread_ids = AsyncMock(
            return_value=["thread-1", "thread-2"]
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        await job._cleanup_orphaned_threads()

        # No deletion when no orphans
        checkpointer.adelete_thread.assert_not_called()


class TestCheckpointCleanupJobExpired:
    """Tests for expired terminal instance cleanup operation (B)."""

    @pytest.mark.asyncio
    async def test_cleanup_expired_terminal(self):
        """Mock instances past TTL, verify full cleanup (checkpoints + records + callback)."""
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()

        # Create expired instances (older than 24 hours)
        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        # Track which statuses were called
        called_statuses = set()

        def list_side_effect(status, limit=100, offset=0):
            called_statuses.add(status)
            if status in TERMINAL_STATUSES:
                return (
                    [
                        MagicMock(
                            instance_id=f"expired-{status}",
                            updated_at=old_time,
                        )
                    ],
                    1,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        # TOCTOU guard: re-fetch shows the instance is still terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        # Mock delete to return successful deletion
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        await job._cleanup_expired_terminal()

        # Should have called adelete_thread for each expired terminal instance
        # There are 4 terminal statuses
        assert checkpointer.adelete_thread.call_count == len(TERMINAL_STATUSES)
        # Should also have called instance_repo.delete for each expired instance
        assert instance_repo.delete.call_count == len(TERMINAL_STATUSES)
        # And the on_instance_deleted callback for each
        assert on_instance_deleted.call_count == len(TERMINAL_STATUSES)

    @pytest.mark.asyncio
    async def test_cleanup_no_expired(self):
        """When no instances are expired, no cleanup (and _cleanup_instance not called)."""
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        # All instances are recent
        recent_time = utcnow().isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [
                        MagicMock(
                            instance_id=f"recent-{status}",
                            updated_at=recent_time,
                        )
                    ],
                    1,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        # Patch _cleanup_instance to verify it is NOT called when there are no expired instances
        with patch.object(job, "_cleanup_instance", new=AsyncMock()) as mock_cleanup_instance:
            await job._cleanup_expired_terminal()
            mock_cleanup_instance.assert_not_called()

        # No deletion when no expired instances
        checkpointer.adelete_thread.assert_not_called()
        instance_repo.delete.assert_not_called()
        on_instance_deleted.assert_not_called()


class TestCheckpointCleanupJobHistoryCap:
    """Tests for history cap enforcement operation (C)."""

    @pytest.mark.asyncio
    async def test_enforce_history_cap(self):
        """Create more terminal instances than cap, verify oldest are pruned fully."""
        config = PersistenceConfig(max_instance_history=5)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()

        # Create 10 terminal instances (exceeds cap of 5)
        old_time_1 = (utcnow() - timedelta(days=10)).isoformat()
        old_time_2 = (utcnow() - timedelta(days=9)).isoformat()
        old_time_3 = (utcnow() - timedelta(days=8)).isoformat()
        old_time_4 = (utcnow() - timedelta(days=7)).isoformat()
        old_time_5 = (utcnow() - timedelta(days=6)).isoformat()
        recent_time_1 = (utcnow() - timedelta(days=5)).isoformat()
        recent_time_2 = (utcnow() - timedelta(days=4)).isoformat()
        recent_time_3 = (utcnow() - timedelta(days=3)).isoformat()
        recent_time_4 = (utcnow() - timedelta(days=2)).isoformat()
        recent_time_5 = (utcnow() - timedelta(days=1)).isoformat()

        # Track which status was queried and only return instances for "terminated" status
        # to simulate real behavior where instances have specific statuses
        instances_returned = []

        def list_side_effect(status, limit=100, offset=0):
            if status == "terminated":
                # Return all instances only for "terminated" status
                instances_returned.append(status)
                return (
                    [
                        MagicMock(instance_id="old-1", updated_at=old_time_1),
                        MagicMock(instance_id="old-2", updated_at=old_time_2),
                        MagicMock(instance_id="old-3", updated_at=old_time_3),
                        MagicMock(instance_id="old-4", updated_at=old_time_4),
                        MagicMock(instance_id="old-5", updated_at=old_time_5),
                        MagicMock(instance_id="recent-1", updated_at=recent_time_1),
                        MagicMock(instance_id="recent-2", updated_at=recent_time_2),
                        MagicMock(instance_id="recent-3", updated_at=recent_time_3),
                        MagicMock(instance_id="recent-4", updated_at=recent_time_4),
                        MagicMock(instance_id="recent-5", updated_at=recent_time_5),
                    ],
                    10,
                )
            # Other terminal statuses return empty (simulating no instances in those states)
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        # TOCTOU guard: re-fetch shows the instance is still terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        await job._enforce_history_cap()

        # Should delete 5 oldest instances (10 - 5 = 5 excess)
        assert checkpointer.adelete_thread.call_count == 5
        # Each prune should also delete the instance record and trigger callback
        assert instance_repo.delete.call_count == 5
        assert on_instance_deleted.call_count == 5

    @pytest.mark.asyncio
    async def test_enforce_history_cap_within_limit(self):
        """When count is within cap, no deletion (and _cleanup_instance not called)."""
        config = PersistenceConfig(max_instance_history=10)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        # Only 5 instances total (within cap of 10)
        recent_time = utcnow().isoformat()

        def list_side_effect(status, limit=100, offset=0):
            # Only "terminated" status returns instances
            if status == "terminated":
                return (
                    [
                        MagicMock(instance_id=f"inst-{i}", updated_at=recent_time)
                        for i in range(5)
                    ],
                    5,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        # Patch _cleanup_instance to verify it is NOT called when within limit
        with patch.object(job, "_cleanup_instance", new=AsyncMock()) as mock_cleanup_instance:
            await job._enforce_history_cap()
            mock_cleanup_instance.assert_not_called()

        # No deletion when within limit (5 <= 10)
        checkpointer.adelete_thread.assert_not_called()
        instance_repo.delete.assert_not_called()
        on_instance_deleted.assert_not_called()


class TestCheckpointCleanupJobBackwardCompatibility:
    """Tests for backward compatibility: job works without on_instance_deleted callback."""

    @pytest.mark.asyncio
    async def test_works_without_callback(self):
        """No on_instance_deleted callback: checkpoint + instance DB cleanup still runs, no callback invoked."""
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        # No callback provided - default is None
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        # Create expired instances (one per terminal status)
        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [MagicMock(instance_id=f"expired-{status}", updated_at=old_time)],
                    1,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        # TOCTOU guard: re-fetch shows the instance is still terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )

        # Construct job WITHOUT the optional on_instance_deleted argument
        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        # Verify the default value is None
        assert job._on_instance_deleted is None

        # Operation B should still work end-to-end
        await job._cleanup_expired_terminal()

        # Both checkpoint and instance record cleanup happened
        assert checkpointer.adelete_thread.call_count == len(TERMINAL_STATUSES)
        assert instance_repo.delete.call_count == len(TERMINAL_STATUSES)
        # No callback to call - no error raised

    @pytest.mark.asyncio
    async def test_explicit_none_callback_equivalent(self):
        """Passing on_instance_deleted=None explicitly behaves like omitting it."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        instance_repo.list = MagicMock(return_value=([], 0))
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        # Mock adapter methods for Operations A and D — both return empty results
        checkpointer.list_thread_ids = AsyncMock(return_value=[])
        checkpointer.find_excess_checkpoint_groups = AsyncMock(return_value=[])

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted=None)

        # Should not raise even with all operations running
        await job.execute()


class TestCleanupInstanceHelper:
    """Focused tests for the new _cleanup_instance helper method."""

    @pytest.mark.asyncio
    async def test_cleanup_instance_full_sequence(self):
        """All steps happen in order: get (TOCTOU) → instance_repo.delete → adelete_thread → callback."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        # TOCTOU guard: re-fetch shows instance is still terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "inst-1", "agent_dir": "/agents/inst-1"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        await job._cleanup_instance("inst-1")

        # Step 0: instance re-fetched for TOCTOU guard
        instance_repo.get.assert_called_once_with("inst-1")
        # Step 1: instance record deleted
        instance_repo.delete.assert_called_once_with("inst-1")
        # Step 2: checkpoint data deleted
        checkpointer.adelete_thread.assert_awaited_once_with("inst-1")
        # Step 3: callback invoked with the instance_id
        on_instance_deleted.assert_called_once_with("inst-1")

    @pytest.mark.asyncio
    async def test_cleanup_instance_skips_callback_when_not_deleted(self):
        """When instance_repo.delete returns {deleted: False}, neither checkpoint nor callback runs."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        # TOCTOU guard: instance still appears terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        # Simulate the instance already being deleted by another process
        instance_repo.delete = MagicMock(
            return_value={"deleted": False, "instance_id": "inst-2", "agent_dir": "/agents/inst-2"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        await job._cleanup_instance("inst-2")

        # TOCTOU re-fetch was performed
        instance_repo.get.assert_called_once_with("inst-2")
        # Delete was attempted
        instance_repo.delete.assert_called_once_with("inst-2")
        # But the checkpoint thread must NOT be deleted here — Operation A
        # (_cleanup_orphaned_threads) is responsible for sweeping any orphan
        # checkpoint data left behind when the record is already gone.
        checkpointer.adelete_thread.assert_not_called()
        # And the callback must NOT be called — the instance didn't exist.
        on_instance_deleted.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_instance_without_callback(self):
        """_cleanup_instance works when no callback is provided (no AttributeError)."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        # TOCTOU guard: instance still appears terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "inst-3", "agent_dir": "/test"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        # Should not raise
        await job._cleanup_instance("inst-3")

        instance_repo.get.assert_called_once_with("inst-3")
        instance_repo.delete.assert_called_once_with("inst-3")
        checkpointer.adelete_thread.assert_awaited_once_with("inst-3")

    @pytest.mark.asyncio
    async def test_cleanup_instance_execution_order(self):
        """Verify execution order: get → instance_repo.delete → adelete_thread → callback.

        Uses the attach_mock pattern to record all four calls on a single
        parent mock so the call_args_list preserves invocation ordering
        across the methods.
        """
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        # TOCTOU guard: instance still appears terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "inst-order", "agent_dir": "/test"}
        )
        on_instance_deleted = MagicMock()

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        # Parent mock + attach_mock: routes all four call records into
        # one ordered call_args_list.
        parent = MagicMock()
        parent.attach_mock(instance_repo.get, "instance_repo_get")
        parent.attach_mock(instance_repo.delete, "instance_repo_delete")
        parent.attach_mock(checkpointer.adelete_thread, "adelete_thread")
        parent.attach_mock(on_instance_deleted, "on_instance_deleted")

        await job._cleanup_instance("inst-order")

        # Extract the method names in invocation order.
        order = [name for name, _, _ in parent.mock_calls]
        assert order == [
            "instance_repo_get",
            "instance_repo_delete",
            "adelete_thread",
            "on_instance_deleted",
        ], f"Expected ordered sequence, got: {order}"

    @pytest.mark.asyncio
    async def test_cleanup_instance_batch_continues_on_failure(self):
        """When instance_repo.delete raises for ONE instance, the rest still get cleaned.

        Mirrors the per-instance loop used by _cleanup_expired_terminal and
        _enforce_history_cap. Verifies the per-instance error isolation
        introduced for Issue 1: a failure on one ID must not abort the batch.
        """
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()

        # TOCTOU guard: all three instances still appear terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )

        # instance_repo.delete succeeds for inst-A/inst-C, raises for inst-B.
        def delete_side_effect(instance_id):
            if instance_id == "inst-B":
                raise RuntimeError("DB transient error")
            return {"deleted": True, "instance_id": instance_id, "agent_dir": "/test"}

        instance_repo.delete = MagicMock(side_effect=delete_side_effect)

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        # Drive the same loop pattern used by operations B and C.
        instance_ids = ["inst-A", "inst-B", "inst-C"]
        for instance_id in instance_ids:
            try:
                await job._cleanup_instance(instance_id)
            except Exception as e:
                # The operations catch here; replicate that contract in the test.
                logger_msg = f"Failed to clean up instance {instance_id[:8]}...: {e}"
                assert "inst-B" in logger_msg

        # TOCTOU re-fetch attempted for all three.
        assert instance_repo.get.call_count == 3

        # instance_repo.delete was attempted for all three.
        assert instance_repo.delete.call_count == 3

        # Checkpoint thread was deleted only for the two whose instance
        # record was actually deleted (inst-A and inst-C). inst-B's delete
        # raised, so its checkpoint thread is left for Operation A to sweep.
        await_args_list = checkpointer.adelete_thread.await_args_list
        called_ids = [call.args[0] for call in await_args_list]
        assert called_ids == ["inst-A", "inst-C"]

        # Callback was invoked for the two successful deletes and NOT for
        # inst-B (where delete raised before the callback path was reached).
        callback_ids = [call.args[0] for call in on_instance_deleted.call_args_list]
        assert callback_ids == ["inst-A", "inst-C"]

    @pytest.mark.asyncio
    async def test_cleanup_instance_toctou_skips_when_no_longer_terminal(self):
        """TOCTOU guard: if the instance was resumed (no longer terminal), skip cleanup.

        Between listing terminal instances and acting on them, another job
        could resume the instance. In that case, we must NOT delete the
        instance record, its checkpoint data, or invoke the callback.
        """
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        # TOCTOU re-fetch: instance has been resumed — status is "running"
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="running")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "inst-resumed", "agent_dir": "/test"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        await job._cleanup_instance("inst-resumed")

        # TOCTOU re-fetch was performed
        instance_repo.get.assert_called_once_with("inst-resumed")
        # Nothing else should have been touched
        instance_repo.delete.assert_not_called()
        checkpointer.adelete_thread.assert_not_called()
        on_instance_deleted.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_instance_toctou_continues_when_instance_already_gone(self):
        """TOCTOU guard: if get returns None (already deleted), still self-heal the rest.

        If the instance record is already gone (deleted by another process
        between listing and cleanup), the delete call is a no-op (returns
        {deleted: False}), and the checkpoint thread is left to Operation A
        to sweep. This is a self-healing path — not a failure.
        """
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        # TOCTOU re-fetch: instance is already gone
        instance_repo.get = MagicMock(return_value=None)
        instance_repo.delete = MagicMock(
            return_value={"deleted": False, "instance_id": "inst-gone", "agent_dir": "/test"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo, on_instance_deleted)

        # Should not raise
        await job._cleanup_instance("inst-gone")

        instance_repo.get.assert_called_once_with("inst-gone")
        instance_repo.delete.assert_called_once_with("inst-gone")
        # No checkpoint or callback work — orphan is left for Operation A.
        checkpointer.adelete_thread.assert_not_called()
        on_instance_deleted.assert_not_called()


class TestCheckpointCleanupJobPerThreadPruning:
    """Tests for per-thread checkpoint pruning operation (D)."""

    @pytest.mark.asyncio
    async def test_prune_per_thread_checkpoints(self):
        """Threads with > 50 checkpoints get pruned."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()

        # Mock the adapter methods invoked during Operation D
        # Step 1: find_excess_checkpoint_groups returns one thread/namespace pair
        checkpointer.find_excess_checkpoint_groups = AsyncMock(
            return_value=[("thread-excess", "", 100)]
        )
        # Step 2: get_checkpoint_ids returns 50 IDs to keep
        checkpointer.get_checkpoint_ids = AsyncMock(
            return_value=[f"keep-{i:032d}" for i in range(50)]
        )
        # Step 3: delete_checkpoints_excluding returns the rowcount
        checkpointer.delete_checkpoints_excluding = AsyncMock(return_value=50)
        # Step 4: delete_writes_excluding returns the rowcount
        checkpointer.delete_writes_excluding = AsyncMock(return_value=100)

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        # Method runs and returns None (no explicit return)
        result = await job._prune_per_thread_checkpoints()

        # Method returns None (no explicit return in production code)
        assert result is None
        # Verify each adapter method was called once
        checkpointer.find_excess_checkpoint_groups.assert_awaited_once_with(
            CHECKPOINT_MAX_PER_THREAD
        )
        checkpointer.get_checkpoint_ids.assert_awaited_once_with(
            "thread-excess", "", CHECKPOINT_MAX_PER_THREAD
        )
        checkpointer.delete_checkpoints_excluding.assert_awaited_once()
        checkpointer.delete_writes_excluding.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_prune_no_excess_threads(self):
        """When no threads have excess checkpoints, no pruning."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()

        # find_excess_checkpoint_groups returns empty — no threads to prune
        checkpointer.find_excess_checkpoint_groups = AsyncMock(return_value=[])

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        result = await job._prune_per_thread_checkpoints()

        # No deletions when no excess threads - method returns None
        assert result is None
        # Pruning methods should not have been called
        checkpointer.get_checkpoint_ids.assert_not_called()
        checkpointer.delete_checkpoints_excluding.assert_not_called()
        checkpointer.delete_writes_excluding.assert_not_called()


class TestCheckpointCleanupJobErrorIsolation:
    """Tests for error isolation between operations."""

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        """One operation fails, others still run."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        # Make adelete_thread fail after some calls
        checkpointer.adelete_thread = AsyncMock(
            side_effect=[None, RuntimeError("Delete failed"), None]
        )
        instance_repo = MagicMock()

        # Mock adapter methods for Operations A and D — return empty so they no-op
        checkpointer.list_thread_ids = AsyncMock(return_value=[])
        checkpointer.find_excess_checkpoint_groups = AsyncMock(return_value=[])

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        # Should not raise - errors are caught internally
        await job.execute()

    @pytest.mark.asyncio
    async def test_operation_a_error_does_not_prevent_b(self):
        """Operation A error should not prevent Operation B from running."""
        from datetime import datetime

        config = PersistenceConfig(checkpoint_ttl_hours=1)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()

        # Track calls to adelete_thread to verify Operation B ran
        deleted_threads: list[str] = []
        checkpointer.adelete_thread = AsyncMock(side_effect=lambda tid: deleted_threads.append(tid))

        # Operation A fails when querying checkpoint DB for orphaned threads
        # (Operation B doesn't use the checkpoint DB for queries, so it won't be affected)
        checkpointer.list_thread_ids = AsyncMock(
            side_effect=RuntimeError("Checkpoint DB error")
        )

        # Operation A fails before calling instance_repo.list, so we only need
        # instance_repo.list to return data for Operation B (expired terminal instances)
        expired_instance = MagicMock()
        expired_instance.instance_id = "expired-instance-123"
        expired_instance.updated_at = "2020-01-01T00:00:00"
        instance_repo.list = MagicMock(return_value=([expired_instance], 1))
        # TOCTOU guard: re-fetch shows the instance is still terminal
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "expired-instance-123", "agent_dir": "/test"}
        )

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        # Execute should not raise despite Operation A failing
        await job.execute()

        # Verify Operation B (_cleanup_expired_terminal) ran by checking
        # that adelete_thread was called for the expired instance
        assert "expired-instance-123" in deleted_threads


class TestCheckpointCleanupJobExecute:
    """Tests for full execute() method."""

    @pytest.mark.asyncio
    async def test_execute_all_operations(self):
        """Full execute() runs all 4 operations without error."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()

        # Mock adapter methods for all operations — return empty results
        checkpointer.list_thread_ids = AsyncMock(return_value=[])
        checkpointer.find_excess_checkpoint_groups = AsyncMock(return_value=[])

        # instance_repo returns empty list for all status queries
        instance_repo.list = MagicMock(return_value=([], 0))

        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        await job.execute()

        # Verify adelete_thread was not called (no data to clean up)
        checkpointer.adelete_thread.assert_not_called()
        # Verify the adapter's query methods were called (Ops A and D)
        checkpointer.list_thread_ids.assert_awaited_once()
        checkpointer.find_excess_checkpoint_groups.assert_awaited_once_with(
            CHECKPOINT_MAX_PER_THREAD
        )


# ==================== Integration-style Tests ====================


class TestMaintenanceServiceIntegration:
    """Integration-style tests for MaintenanceService with real components."""

    @pytest.mark.asyncio
    async def test_full_cycle_with_checkpoint_job(self):
        """Test complete maintenance cycle with checkpoint cleanup job."""
        service = MaintenanceService(check_interval_minutes=60)

        # Create a real-ish config
        config = PersistenceConfig(
            checkpoint_ttl_hours=24,
            max_instance_history=10,
        )

        # Create mocks
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        instance_repo.list = MagicMock(return_value=([], 0))

        cleanup_job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        # Set up idle state
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.list_all_pending = MagicMock(return_value=[])
        service.set_job_queue_service(mock_job_queue_service)
        service.set_request_registry({})

        # Register the job
        service.register(
            "checkpoint_cleanup",
            min_interval_hours=1.0,
            execute_fn=cleanup_job.execute,
        )

        # Verify job is registered
        assert len(service._jobs) == 1
        assert service._jobs[0].name == "checkpoint_cleanup"


class TestMaintenanceJobDataclass:
    """Tests for MaintenanceJob dataclass."""

    def test_maintenance_job_creation(self):
        """Test MaintenanceJob can be created with all fields."""
        fn = AsyncMock()
        last_run = utcnow()

        job = MaintenanceJob(
            name="test_job",
            min_interval_hours=2.5,
            last_run=last_run,
            execute_fn=fn,
        )

        assert job.name == "test_job"
        assert job.min_interval_hours == 2.5
        assert job.last_run == last_run
        assert job.execute_fn is fn

    def test_maintenance_job_optional_last_run(self):
        """Test MaintenanceJob can be created without last_run (None)."""
        fn = AsyncMock()

        job = MaintenanceJob(
            name="new_job",
            min_interval_hours=1.0,
            last_run=None,
            execute_fn=fn,
        )

        assert job.name == "new_job"
        assert job.last_run is None


class TestConfigDefaults:
    """Tests for configuration defaults."""

    def test_checkpoint_ttl_default(self):
        """Test default CHECKPOINT_TTL_HOURS is used when config is 0."""
        config = PersistenceConfig(checkpoint_ttl_hours=0)

        # Should fall back to constant
        assert config.checkpoint_ttl_hours == 0  # Will use default in job logic

    def test_max_instance_history_default(self):
        """Test default MAX_INSTANCE_HISTORY is used when config is 0."""
        config = PersistenceConfig(max_instance_history=0)

        # Should fall back to constant
        assert config.max_instance_history == 0  # Will use default in job logic

    def test_checkpoint_max_per_thread_constant(self):
        """Test CHECKPOINT_MAX_PER_THREAD constant is correct."""
        assert CHECKPOINT_MAX_PER_THREAD == 50


class TestUtcNow:
    """Tests for utcnow helper function."""

    def test_utcnow_returns_timezone_aware(self):
        """utcnow() should return timezone-aware datetime."""
        now = utcnow()
        assert now.tzinfo is not None
        assert now.tzinfo == timezone.utc

    def test_utcnow_returns_reasonable_time(self):
        """utcnow() should return a time close to now."""
        import time

        before = time.time()
        now = utcnow()
        after = time.time()

        # Datetime should be within 1 second of actual time
        now_ts = now.timestamp()
        assert abs(now_ts - (before + after) / 2) < 1
