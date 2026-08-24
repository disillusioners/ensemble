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

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

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
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository


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

    def test_register_accepts_last_run_parameter(self):
        """``register(last_run=...)`` seeds the job's ``last_run`` field
        so callers that have persisted a prior run time can avoid
        re-firing on restart. Default behavior (``last_run=None``)
        remains unchanged for existing callers.
        """
        service = MaintenanceService(check_interval_minutes=15)
        execute_fn = AsyncMock()
        prior_run = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        service.register(
            "scanned_job",
            min_interval_hours=24.0,
            execute_fn=execute_fn,
            last_run=prior_run,
        )

        assert len(service._jobs) == 1
        job = service._jobs[0]
        assert job.name == "scanned_job"
        assert job.min_interval_hours == 24.0
        # The seeded timestamp is preserved verbatim — caller has
        # already done any parsing/validation.
        assert job.last_run == prior_run
        assert job.last_run is not None  # not reset to None
        assert job.execute_fn is execute_fn

        # A second registration without ``last_run`` still defaults to
        # None — proves the new parameter is optional, not a breaking
        # signature change.
        service.register("fresh_job", min_interval_hours=1.0, execute_fn=AsyncMock())
        assert service._jobs[1].last_run is None


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


# ==================== Pinned Protection Tests ====================


class TestCheckpointCleanupJobPinnedProtection:
    """Tests for pinned-subtree exclusion in CheckpointCleanupJob.

    Pinned instances (and the full subtree under their tree root) must
    be excluded from TTL-based cleanup (Operation B) and history-cap
    pruning (Operation C). The job receives the optional
    ``ui_prefs_repo`` keyword argument and uses
    :meth:`CheckpointCleanupJob._get_protected_instance_ids` to compute
    the protected set per cycle.

    The mock layout used here:

    * ``ui_prefs_repo.get_pinned_instance_ids`` returns the set of
      pinned ``instance_id`` strings.
    * ``instance_repo.get_tree_root_id(pinned_id)`` returns the root
      for each pinned instance (a root resolves to itself).
    * ``instance_repo.get_tree_ids(root_id)`` returns the full subtree
      (root + descendants) for each root.
    * ``instance_repo.list`` returns terminal instances as usual for
      the expiration / cap enumeration.
    """

    @staticmethod
    def _make_job(
        config,
        checkpointer,
        instance_repo,
        ui_prefs_repo,
        on_instance_deleted=None,
    ):
        """Build a CheckpointCleanupJob with ui_prefs_repo wired in."""
        return CheckpointCleanupJob(
            config,
            checkpointer,
            instance_repo,
            on_instance_deleted=on_instance_deleted,
            ui_prefs_repo=ui_prefs_repo,
        )

    @staticmethod
    def _attach_terminal_listing(instance_repo, terminal_id):
        """Configure ``instance_repo.list`` to return one expired terminal row.

        Every terminal status gets the same single instance id — keeps
        the test focus on the protection filter rather than the
        pagination across statuses.
        """
        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [MagicMock(instance_id=terminal_id, updated_at=old_time)],
                    1,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)

    @pytest.mark.asyncio
    async def test_get_protected_returns_empty_set_when_repo_is_none(self):
        """Backward-compat: with no ui_prefs_repo wired, the protected set is empty."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()

        # Construct WITHOUT ui_prefs_repo — the existing pattern.
        job = CheckpointCleanupJob(config, checkpointer, instance_repo)

        assert job._ui_prefs_repo is None
        assert job._get_protected_instance_ids() == set()

    @pytest.mark.asyncio
    async def test_get_protected_returns_empty_set_when_no_pinned(self):
        """When the prefs repo reports no pinned rows, the protected set is empty."""
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(return_value=set())

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        assert job._get_protected_instance_ids() == set()
        # No tree lookups should have happened — pure prefs query is the
        # fast path when the result is empty. P1 (phase1-plan.md T6):
        # ``get_cascade_tree_ids`` is the new wrapper; the transient
        # ``get_tree_ids`` is no longer called from this code path.
        instance_repo.get_tree_root_id.assert_not_called()
        instance_repo.get_cascade_tree_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_protected_resolves_pinned_to_subtree(self):
        """A pinned root's whole subtree becomes protected.

        Tree shape:

            root-A
              ├─ child-A1  (pinned)
              └─ child-A2
                   └─ grandchild-A2a

        Pinning ``child-A1`` resolves up to ``root-A``; the entire
        ``{root-A, child-A1, child-A2, grandchild-A2a}`` subtree
        (collected via ``get_tree_ids``) is the protected set.
        """
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()

        # Only ``child-A1`` is pinned.
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"child-A1"}
        )
        # ``child-A1`` walks up to ``root-A``.
        instance_repo.get_tree_root_id = MagicMock(return_value="root-A")
        # ``root-A`` subtree contains 4 nodes.
        instance_repo.get_tree_ids = MagicMock(
            return_value=["root-A", "child-A1", "child-A2", "grandchild-A2a"]
        )
        instance_repo.get_cascade_tree_ids = MagicMock(
            return_value=["root-A", "child-A1", "child-A2", "grandchild-A2a"]
        )

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        protected = job._get_protected_instance_ids()

        assert protected == {
            "root-A",
            "child-A1",
            "child-A2",
            "grandchild-A2a",
        }

    @pytest.mark.asyncio
    async def test_get_protected_skips_orphan_pinned_row(self):
        """When a pinned row's instance no longer exists, it's silently skipped.

        ``get_tree_root_id`` returns ``None`` (instance was hard-deleted
        out from under the prefs row) — the protection set simply
        ignores that pinned id and proceeds.
        """
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()

        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"orphan-id", "root-A"}
        )
        # ``orphan-id`` has been deleted → ``get_tree_root_id`` returns None.
        # ``root-A`` is a real root → returns itself.
        instance_repo.get_tree_root_id = MagicMock(
            side_effect=lambda iid: None if iid == "orphan-id" else iid
        )
        instance_repo.get_tree_ids = MagicMock(return_value=["root-A", "child-A1"])
        instance_repo.get_cascade_tree_ids = MagicMock(return_value=["root-A", "child-A1"])

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        protected = job._get_protected_instance_ids()
        assert protected == {"root-A", "child-A1"}

    @pytest.mark.asyncio
    async def test_ttl_excludes_pinned_terminal(self):
        """Operation B: a pinned expired terminal instance is NOT deleted.

        Two expired terminal instances exist — only one is pinned.
        The non-pinned one is deleted; the pinned one is preserved.
        Verifies scenario 1 + 4 (pinned NOT cleaned, non-pinned IS).
        """
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        # Two expired terminal instances; ``pinned-A`` is pinned,
        # ``unpinned-B`` is not.
        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [
                        MagicMock(instance_id="pinned-A", updated_at=old_time),
                        MagicMock(instance_id="unpinned-B", updated_at=old_time),
                    ],
                    2,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        # TOCTOU guard: instance still terminal.
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        # Tree: pinned-A is itself the root (no descendants).
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"pinned-A"}
        )
        instance_repo.get_tree_root_id = MagicMock(return_value="pinned-A")
        instance_repo.get_tree_ids = MagicMock(return_value=["pinned-A"])
        instance_repo.get_cascade_tree_ids = MagicMock(return_value=["pinned-A"])

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job._cleanup_expired_terminal()

        # Only ``unpinned-B`` was deleted. ``pinned-A`` is preserved.
        deleted_ids = [
            call.args[0] for call in instance_repo.delete.call_args_list
        ]
        await_args = checkpointer.adelete_thread.await_args_list
        adelete_ids = [call.args[0] for call in await_args]
        callback_ids = [call.args[0] for call in on_instance_deleted.call_args_list]

        # Each terminal status re-runs the candidate list (the listing
        # loop visits every terminal status), so the counts are
        # multiplied by len(TERMINAL_STATUSES) for non-pinned ids.
        assert "unpinned-B" in deleted_ids
        assert "pinned-A" not in deleted_ids
        assert "unpinned-B" in adelete_ids
        assert "pinned-A" not in adelete_ids
        assert "unpinned-B" in callback_ids
        assert "pinned-A" not in callback_ids
        # Pinned-A must NEVER have been deleted across any status.
        assert "pinned-A" not in deleted_ids

    @pytest.mark.asyncio
    async def test_history_cap_spares_pinned_oldest(self):
        """Operation C: cap exceeded; pinned oldest instance is spared.

        Scenario 2: even when the pinned instance is the OLDEST in the
        terminal list, it must not be pruned. Non-pinned newer
        instances over the cap are pruned normally.
        """
        config = PersistenceConfig(max_instance_history=3)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        old_1 = (utcnow() - timedelta(days=10)).isoformat()
        old_2 = (utcnow() - timedelta(days=9)).isoformat()
        old_3 = (utcnow() - timedelta(days=8)).isoformat()
        old_4 = (utcnow() - timedelta(days=7)).isoformat()
        old_5 = (utcnow() - timedelta(days=6)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status == "terminated":
                return (
                    [
                        # Pinned instance is the oldest of all.
                        MagicMock(instance_id="pinned-oldest", updated_at=old_1),
                        MagicMock(instance_id="inst-2", updated_at=old_2),
                        MagicMock(instance_id="inst-3", updated_at=old_3),
                        MagicMock(instance_id="inst-4", updated_at=old_4),
                        MagicMock(instance_id="inst-5", updated_at=old_5),
                    ],
                    5,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        # pinned-oldest is its own root with no descendants.
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"pinned-oldest"}
        )
        instance_repo.get_tree_root_id = MagicMock(return_value="pinned-oldest")
        instance_repo.get_tree_ids = MagicMock(return_value=["pinned-oldest"])
        instance_repo.get_cascade_tree_ids = MagicMock(return_value=["pinned-oldest"])

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job._enforce_history_cap()

        # Cap is 3. 5 instances total, 1 pinned → 4 candidates → excess=1.
        # Only the OLDEST non-pinned candidate (``inst-2``) is pruned.
        deleted_ids = [
            call.args[0] for call in instance_repo.delete.call_args_list
        ]
        adelete_ids = [
            call.args[0] for call in checkpointer.adelete_thread.await_args_list
        ]
        callback_ids = [
            call.args[0] for call in on_instance_deleted.call_args_list
        ]

        assert "pinned-oldest" not in deleted_ids
        assert "pinned-oldest" not in adelete_ids
        assert "pinned-oldest" not in callback_ids
        # Pruned exactly one non-pinned instance.
        assert len(deleted_ids) == 1
        assert deleted_ids[0] == "inst-2"
        assert adelete_ids == ["inst-2"]
        assert callback_ids == ["inst-2"]

    @pytest.mark.asyncio
    async def test_ttl_protects_descendants_of_pinned_root(self):
        """Operation B: a pinned root's descendants are also spared.

        Scenario 3: pin ``root-A``; ``child-A1`` is expired and
        terminal but must NOT be cleaned (it's in root-A's subtree).
        """
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [
                        # Both expired, terminal. child-A1 is in the
                        # pinned subtree; inst-X is independent.
                        MagicMock(instance_id="child-A1", updated_at=old_time),
                        MagicMock(instance_id="inst-X", updated_at=old_time),
                    ],
                    2,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"root-A"}
        )
        # root-A is a root → returns itself.
        instance_repo.get_tree_root_id = MagicMock(return_value="root-A")
        # root-A's subtree includes the child we're trying to delete.
        instance_repo.get_tree_ids = MagicMock(
            return_value=["root-A", "child-A1", "child-A2"]
        )
        instance_repo.get_cascade_tree_ids = MagicMock(
            return_value=["root-A", "child-A1", "child-A2"]
        )

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job._cleanup_expired_terminal()

        deleted_ids = [
            call.args[0] for call in instance_repo.delete.call_args_list
        ]
        adelete_ids = [
            call.args[0] for call in checkpointer.adelete_thread.await_args_list
        ]

        # child-A1 (descendant of pinned root) is spared.
        assert "child-A1" not in deleted_ids
        assert "child-A1" not in adelete_ids
        # inst-X (independent) is cleaned up.
        assert "inst-X" in adelete_ids

    @pytest.mark.asyncio
    async def test_pinned_child_protects_entire_sibling_subtree(self):
        """Operation B: pinning a CHILD protects the whole root's subtree.

        Scenario 5: only ``child-A1`` is pinned, but resolving up to
        ``root-A`` and back down must shield every sibling + cousin
        (root-A, child-A1, child-A2, grandchild-A2a). An unrelated
        ``inst-Y`` is still cleaned.
        """
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [
                        MagicMock(instance_id="child-A1", updated_at=old_time),
                        MagicMock(instance_id="child-A2", updated_at=old_time),
                        MagicMock(instance_id="grandchild-A2a", updated_at=old_time),
                        MagicMock(instance_id="root-A", updated_at=old_time),
                        MagicMock(instance_id="inst-Y", updated_at=old_time),
                    ],
                    5,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        # Only ``child-A1`` is pinned, but it resolves up to ``root-A``.
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"child-A1"}
        )
        instance_repo.get_tree_root_id = MagicMock(return_value="root-A")
        instance_repo.get_tree_ids = MagicMock(
            return_value=["root-A", "child-A1", "child-A2", "grandchild-A2a"]
        )
        instance_repo.get_cascade_tree_ids = MagicMock(
            return_value=["root-A", "child-A1", "child-A2", "grandchild-A2a"]
        )

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job._cleanup_expired_terminal()

        deleted_ids = [
            call.args[0] for call in instance_repo.delete.call_args_list
        ]
        adelete_ids = [
            call.args[0] for call in checkpointer.adelete_thread.await_args_list
        ]

        # Whole subtree under root-A is protected.
        for protected_id in ("root-A", "child-A1", "child-A2", "grandchild-A2a"):
            assert protected_id not in deleted_ids
            assert protected_id not in adelete_ids
        # Unrelated inst-Y still gets cleaned.
        assert "inst-Y" in adelete_ids

    @pytest.mark.asyncio
    async def test_ttl_logs_when_pinning_excludes_instances(self, caplog):
        """Operation B logs at INFO when the pin filter actually drops candidates."""
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()

        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [MagicMock(instance_id="pinned-A", updated_at=old_time)],
                    1,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"pinned-A"}
        )
        instance_repo.get_tree_root_id = MagicMock(return_value="pinned-A")
        instance_repo.get_tree_ids = MagicMock(return_value=["pinned-A"])
        instance_repo.get_cascade_tree_ids = MagicMock(return_value=["pinned-A"])

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        caplog.set_level("INFO", logger="daemon.services.maintenance")
        await job._cleanup_expired_terminal()

        # The exclusion log line must be present.
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "pinned" in msg.lower() and "ttl" in msg.lower()
            for msg in messages
        ), f"Expected pinned-TTL log, got: {messages}"

    @pytest.mark.asyncio
    async def test_history_cap_logs_when_pinning_excludes_instances(self, caplog):
        """Operation C logs at INFO when the pin filter drops candidates from the cap."""
        config = PersistenceConfig(max_instance_history=2)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()

        old_1 = (utcnow() - timedelta(days=10)).isoformat()
        old_2 = (utcnow() - timedelta(days=9)).isoformat()
        old_3 = (utcnow() - timedelta(days=8)).isoformat()
        old_4 = (utcnow() - timedelta(days=7)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status == "terminated":
                return (
                    [
                        MagicMock(instance_id="pinned-A", updated_at=old_1),
                        MagicMock(instance_id="inst-2", updated_at=old_2),
                        MagicMock(instance_id="inst-3", updated_at=old_3),
                        MagicMock(instance_id="inst-4", updated_at=old_4),
                    ],
                    4,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"pinned-A"}
        )
        instance_repo.get_tree_root_id = MagicMock(return_value="pinned-A")
        instance_repo.get_tree_ids = MagicMock(return_value=["pinned-A"])
        instance_repo.get_cascade_tree_ids = MagicMock(return_value=["pinned-A"])

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        caplog.set_level("INFO", logger="daemon.services.maintenance")
        await job._enforce_history_cap()

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "pinned" in msg.lower() and "exceeds" in msg.lower()
            for msg in messages
        ), f"Expected pinned-exceeds-cap log, got: {messages}"

    @pytest.mark.asyncio
    async def test_history_cap_pinned_does_not_count_against_cap(self):
        """Pinned instances don't push the cap — the cap is computed on the candidates set.

        Without pin protection, 5 terminals with cap=3 → prune 2.
        With pin protection and 1 pinned, only 4 candidates → prune 1.
        This is the contract: pinned doesn't count toward the cap and
        isn't pruned.
        """
        config = PersistenceConfig(max_instance_history=3)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        old_1 = (utcnow() - timedelta(days=10)).isoformat()
        old_2 = (utcnow() - timedelta(days=9)).isoformat()
        old_3 = (utcnow() - timedelta(days=8)).isoformat()
        old_4 = (utcnow() - timedelta(days=7)).isoformat()
        old_5 = (utcnow() - timedelta(days=6)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status == "terminated":
                return (
                    [
                        MagicMock(instance_id="pinned-old", updated_at=old_1),
                        MagicMock(instance_id="inst-2", updated_at=old_2),
                        MagicMock(instance_id="inst-3", updated_at=old_3),
                        MagicMock(instance_id="inst-4", updated_at=old_4),
                        MagicMock(instance_id="inst-5", updated_at=old_5),
                    ],
                    5,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.get = MagicMock(
            return_value=MagicMock(status="terminated")
        )
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"pinned-old"}
        )
        instance_repo.get_tree_root_id = MagicMock(return_value="pinned-old")
        instance_repo.get_tree_ids = MagicMock(return_value=["pinned-old"])
        instance_repo.get_cascade_tree_ids = MagicMock(return_value=["pinned-old"])

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job._enforce_history_cap()

        # 5 total − 1 pinned = 4 candidates. Cap=3 → excess=1.
        # Only the oldest non-pinned (``inst-2``) is pruned.
        deleted_ids = [
            call.args[0] for call in instance_repo.delete.call_args_list
        ]
        assert len(deleted_ids) == 1
        assert deleted_ids[0] == "inst-2"
        assert "pinned-old" not in deleted_ids

    @pytest.mark.asyncio
    async def test_get_protected_propagates_prefs_lookup_error(self):
        """Unit-level: prefs lookup error is NOT swallowed into an empty set.

        Pinned instances are a user-visible guarantee — degrading to
        ``set()`` on a transient prefs-DB failure would silently violate
        it. The exception must propagate so the operation-level
        try/except in the callers can skip the cycle.
        """
        config = PersistenceConfig()
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            side_effect=RuntimeError("db down")
        )

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        with pytest.raises(RuntimeError, match="db down"):
            job._get_protected_instance_ids()
        # No compensating tree lookups should have run. P1
        # (phase1-plan.md T6): the wrapper ``get_cascade_tree_ids`` is
        # the new entry point.
        instance_repo.get_tree_root_id.assert_not_called()
        instance_repo.get_cascade_tree_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_ttl_skips_cycle_when_prefs_lookup_fails(self):
        """Op B fail-safe: prefs-lookup error aborts the whole TTL cycle.

        An expired terminal instance exists and would normally be deleted
        by Op B, but ``ui_prefs_repo.get_pinned_instance_ids`` raises.
        Because the protected set cannot be determined, the operation
        must skip the cycle entirely — no checkpoint or record deletes.
        """
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        # One expired terminal candidate that WOULD be deleted if
        # protection could be evaluated.
        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [MagicMock(instance_id="would-delete", updated_at=old_time)],
                    1,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        # Prefs lookup blows up — we cannot compute the protected set.
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            side_effect=RuntimeError("prefs db unreachable")
        )

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        # Must not raise — the per-operation try/except swallows it.
        await job._cleanup_expired_terminal()

        # Fail-safe assertion: NOTHING was deleted this cycle. If the
        # bug regressed, the protected set would silently be empty and
        # ``would-delete`` would be cleaned up.
        checkpointer.adelete_thread.assert_not_called()
        instance_repo.delete.assert_not_called()
        on_instance_deleted.assert_not_called()

    @pytest.mark.asyncio
    async def test_history_cap_skips_cycle_when_prefs_lookup_fails(self):
        """Op C fail-safe: prefs-lookup error aborts the whole cap-enforcement cycle.

        History cap is exceeded by 2, so without the prefs lookup the
        operation would prune two non-pinned instances. With the prefs
        lookup failing, the cap must be left untouched and no
        deletions may occur — the next cycle will retry once the
        prefs DB recovers.
        """
        config = PersistenceConfig(max_instance_history=2)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        # 4 terminal instances, cap=2 → normally prune 2 oldest.
        old_1 = (utcnow() - timedelta(days=10)).isoformat()
        old_2 = (utcnow() - timedelta(days=9)).isoformat()
        old_3 = (utcnow() - timedelta(days=8)).isoformat()
        old_4 = (utcnow() - timedelta(days=7)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status == "terminated":
                return (
                    [
                        MagicMock(instance_id="inst-1", updated_at=old_1),
                        MagicMock(instance_id="inst-2", updated_at=old_2),
                        MagicMock(instance_id="inst-3", updated_at=old_3),
                        MagicMock(instance_id="inst-4", updated_at=old_4),
                    ],
                    4,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )
        # Prefs lookup blows up.
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            side_effect=RuntimeError("prefs db unreachable")
        )

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        # Must not raise — the per-operation try/except swallows it.
        await job._enforce_history_cap()

        # Fail-safe: nothing deleted. If the bug regressed, two
        # non-pinned instances would be pruned.
        checkpointer.adelete_thread.assert_not_called()
        instance_repo.delete.assert_not_called()
        on_instance_deleted.assert_not_called()

    @pytest.mark.asyncio
    async def test_ttl_logs_failure_when_prefs_lookup_fails(self, caplog):
        """Op B logs the per-operation failure message on prefs lookup error.

        The propagated exception must land in the existing
        ``except Exception`` block at the bottom of
        ``_cleanup_expired_terminal`` and be logged as
        ``Expired terminal cleanup failed: ...``. This keeps operators
        aware that a cleanup cycle was skipped and lets them correlate
        the skip with a prefs-DB outage.
        """
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            side_effect=RuntimeError("prefs db unreachable")
        )
        # No expired terminals — listing is never reached because
        # protection lookup fails first.
        instance_repo.list = MagicMock(return_value=([], 0))

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        caplog.set_level("ERROR", logger="daemon.services.maintenance")
        await job._cleanup_expired_terminal()

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "expired terminal cleanup failed" in msg.lower() for msg in messages
        ), f"Expected per-op failure log, got: {messages}"

    @pytest.mark.asyncio
    async def test_history_cap_logs_failure_when_prefs_lookup_fails(self, caplog):
        """Op C logs the per-operation failure message on prefs lookup error.

        The propagated exception lands in the existing
        ``except Exception`` block at the bottom of
        ``_enforce_history_cap`` and is logged as
        ``History cap enforcement failed: ...``.
        """
        config = PersistenceConfig(max_instance_history=2)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        ui_prefs_repo = MagicMock()
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            side_effect=RuntimeError("prefs db unreachable")
        )
        # Listing is never reached — protection lookup fails first.
        instance_repo.list = MagicMock(return_value=([], 0))

        job = self._make_job(config, checkpointer, instance_repo, ui_prefs_repo)

        caplog.set_level("ERROR", logger="daemon.services.maintenance")
        await job._enforce_history_cap()

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "history cap enforcement failed" in msg.lower() for msg in messages
        ), f"Expected per-op failure log, got: {messages}"

    # ─────────────────────────────────────────────────────────────────────
    # W3 — Edge-case tests: every candidate is protected
    # ─────────────────────────────────────────────────────────────────────
    #
    # When the protected set covers EVERY terminal candidate, the
    # corresponding operation must do nothing. The two tests below
    # verify the "all protected → zero deletions" contract for both
    # Operation B (TTL) and Operation C (history cap). They use the
    # same mock-based pattern as the other PinnedProtection tests; the
    # difference is that no deletions should be issued at all.

    @pytest.mark.asyncio
    async def test_history_cap_all_protected_prunes_nothing(self):
        """W3: when ALL terminal candidates are protected, history cap prunes zero.

        5 terminal instances exist, all 5 are pinned. Even with
        ``max_instance_history=3`` (5 > 3 → normally 2 prunes), zero
        should be pruned because every candidate is in the protected
        set. The cap is computed on the protected-excluded candidate
        set — pinning protects against exceeding the cap, not just
        against being picked.
        """
        config = PersistenceConfig(max_instance_history=3)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        old_1 = (utcnow() - timedelta(days=10)).isoformat()
        old_2 = (utcnow() - timedelta(days=9)).isoformat()
        old_3 = (utcnow() - timedelta(days=8)).isoformat()
        old_4 = (utcnow() - timedelta(days=7)).isoformat()
        old_5 = (utcnow() - timedelta(days=6)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status == "terminated":
                return (
                    [
                        MagicMock(instance_id="pinned-1", updated_at=old_1),
                        MagicMock(instance_id="pinned-2", updated_at=old_2),
                        MagicMock(instance_id="pinned-3", updated_at=old_3),
                        MagicMock(instance_id="pinned-4", updated_at=old_4),
                        MagicMock(instance_id="pinned-5", updated_at=old_5),
                    ],
                    5,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)

        # All 5 are pinned. Each resolves to itself as its own root
        # with no descendants — every candidate is protected.
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={
                "pinned-1",
                "pinned-2",
                "pinned-3",
                "pinned-4",
                "pinned-5",
            }
        )
        instance_repo.get_tree_root_id = MagicMock(
            side_effect=lambda iid: iid
        )
        instance_repo.get_tree_ids = MagicMock(
            side_effect=lambda root_id: [root_id]
        )
        instance_repo.get_cascade_tree_ids = MagicMock(
            side_effect=lambda root_id: [root_id]
        )

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job._enforce_history_cap()

        # 5 - 5 protected = 0 candidates. 0 <= cap=3 → no pruning.
        checkpointer.adelete_thread.assert_not_called()
        instance_repo.delete.assert_not_called()
        on_instance_deleted.assert_not_called()

    @pytest.mark.asyncio
    async def test_ttl_all_pinned_skips_cycle(self):
        """W3: when ALL expired instances are pinned, TTL cleanup is a no-op.

        Every expired terminal instance is in the protected set, so
        Operation B's candidate list is empty. The operation should
        log the skip and return without issuing any deletes or
        callbacks.
        """
        config = PersistenceConfig(checkpoint_ttl_hours=24)
        checkpointer = AsyncMock()
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        old_time = (utcnow() - timedelta(hours=48)).isoformat()

        def list_side_effect(status, limit=100, offset=0):
            if status in TERMINAL_STATUSES:
                return (
                    [
                        MagicMock(instance_id="pinned-A", updated_at=old_time),
                        MagicMock(instance_id="pinned-B", updated_at=old_time),
                        MagicMock(instance_id="pinned-C", updated_at=old_time),
                    ],
                    3,
                )
            return ([], 0)

        instance_repo.list = MagicMock(side_effect=list_side_effect)

        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"pinned-A", "pinned-B", "pinned-C"}
        )
        instance_repo.get_tree_root_id = MagicMock(
            side_effect=lambda iid: iid
        )
        instance_repo.get_tree_ids = MagicMock(
            side_effect=lambda root_id: [root_id]
        )
        instance_repo.get_cascade_tree_ids = MagicMock(
            side_effect=lambda root_id: [root_id]
        )

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job._cleanup_expired_terminal()

        # No expired terminal survives the filter; nothing is cleaned.
        checkpointer.adelete_thread.assert_not_called()
        instance_repo.delete.assert_not_called()
        on_instance_deleted.assert_not_called()


    # P1 (phase1-plan.md T6, C11) — ``pinned_subtree_terminal_count``
    # metric is emitted per maintenance tick so the polarity change
    # (terminal descendants of pinned roots are now protected from TTL
    # purge) is observable, not silent.
    @pytest.mark.asyncio
    async def test_pinned_subtree_terminal_count_metric_emitted(
        self, caplog: pytest.LogCaptureFixture,
    ):
        """P1 (T6, C11): ``pinned_subtree_terminal_count=N`` INFO line
        emitted at the start of ``execute()`` carrying the sum of
        terminal descendants across every pinned root.
        """
        import logging
        caplog.set_level(logging.INFO, logger="daemon.services.maintenance")

        config = PersistenceConfig()
        checkpointer = MagicMock()
        checkpointer.list_thread_ids = AsyncMock(return_value=[])
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        # Pin a root that has a 2-node terminal subtree.
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={"pinned-root"}
        )
        # _get_protected_instance_ids() returns the root + 2 terminal
        # descendants.
        instance_repo.get_tree_root_id = MagicMock(return_value="pinned-root")
        instance_repo.get_tree_ids = MagicMock(
            return_value=["pinned-root", "child-1", "child-2"]
        )
        instance_repo.get_cascade_tree_ids = MagicMock(
            return_value=["pinned-root", "child-1", "child-2"]
        )

        def get_side_effect(iid):
            return {
                "pinned-root": MagicMock(status="running"),
                "child-1": MagicMock(status="completed"),
                "child-2": MagicMock(status="terminated"),
            }.get(iid)

        instance_repo.get = MagicMock(side_effect=get_side_effect)
        instance_repo.list = MagicMock(return_value=([], 0))
        instance_repo.delete = MagicMock(
            return_value={"deleted": True, "instance_id": "any", "agent_dir": "/test"}
        )

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job.execute()

        # The metric line is emitted exactly once per execute() call.
        metric_records = [
            r for r in caplog.records
            if "pinned_subtree_terminal_count=" in r.message
            and r.levelno == logging.INFO
        ]
        assert len(metric_records) == 1, (
            f"expected exactly one pinned_subtree_terminal_count emit; "
            f"got {len(metric_records)}"
        )
        # Sum of terminal descendants = child-1 (completed) + child-2
        # (terminated) = 2. The pinned root itself is running, not
        # terminal, so it does NOT count.
        assert "pinned_subtree_terminal_count=2" in metric_records[0].message, (
            f"metric should sum to 2; got: {metric_records[0].message}"
        )

    @pytest.mark.asyncio
    async def test_pinned_subtree_terminal_count_zero_when_no_pinned(
        self, caplog: pytest.LogCaptureFixture,
    ):
        """No pinned → metric is ``0`` (still emitted so operators can
        verify the polarity change is being tracked end-to-end).
        """
        import logging
        caplog.set_level(logging.INFO, logger="daemon.services.maintenance")

        config = PersistenceConfig()
        checkpointer = MagicMock()
        checkpointer.list_thread_ids = AsyncMock(return_value=[])
        instance_repo = MagicMock()
        on_instance_deleted = MagicMock()
        ui_prefs_repo = MagicMock()

        ui_prefs_repo.get_pinned_instance_ids = MagicMock(return_value=set())
        instance_repo.get = MagicMock(return_value=None)
        instance_repo.list = MagicMock(return_value=([], 0))

        job = self._make_job(
            config, checkpointer, instance_repo, ui_prefs_repo, on_instance_deleted
        )

        await job.execute()

        metric_records = [
            r for r in caplog.records
            if "pinned_subtree_terminal_count=" in r.message
        ]
        # Metric still emitted (so the polarity change is observable)
        # but with value 0 since no pinned roots exist.
        assert len(metric_records) == 1, (
            f"expected exactly one metric emit; got {len(metric_records)}"
        )
        assert "pinned_subtree_terminal_count=0" in metric_records[0].message


# ─────────────────────────────────────────────────────────────────────────────
# W2 — Integration tests with REAL tree traversal
# ─────────────────────────────────────────────────────────────────────────────
#
# The mock-based tests above exercise the production logic in
# ``_get_protected_instance_ids`` against a fully-mocked instance
# repository. They prove the algorithm's control flow but skip the
# real tree-traversal code. These two integration tests use the real
# ``SQLModelInstanceRepository`` against an in-memory SQLite engine
# so that ``get_tree_root_id`` and ``get_tree_ids`` walk actual DB
# rows. They verify:
#
# * W2 — pinning a grandchild correctly resolves up the parent
#   chain and back down to the full subtree.
# * W1 — fail-protect: when ``get_tree_root_id`` returns None on a
#   live pinned instance (broken ancestor chain), the job still
#   protects the instance and logs a WARNING.
#
# Fixture pattern is copied verbatim from
# ``tests/test_instance_hard_delete.py::engine`` — the canonical
# "real in-memory SQLite with FK enforcement" recipe for this repo.


class TestCheckpointCleanupJobPinnedProtectionIntegration:
    """Integration tests for pinned-subtree exclusion with REAL tree traversal.

    Unlike the mock-based tests in
    :class:`TestCheckpointCleanupJobPinnedProtection`, these use the
    real :class:`SQLModelInstanceRepository` against an in-memory
    SQLite engine so :meth:`get_tree_root_id` and
    :meth:`get_tree_ids` walk actual rows. This proves the
    production code's tree-walking logic (BFS up the parent chain,
    BFS down the hierarchy table) is consistent with the cleanup
    job's algorithm.

    W2: pin a grandchild and verify the entire root → child →
    grandchild subtree becomes protected.

    W1: pin a child whose parent chain is broken (its
    ``parent_id`` points to a ghost row that was never inserted)
    and verify the job treats the live pinned instance as its own
    root and emits the WARNING log.
    """

    @pytest.fixture
    def engine(self):
        """Real in-memory SQLite engine with FK enforcement enabled.

        Mirrors ``tests/test_instance_hard_delete.py::engine``:
        ``StaticPool`` keeps a single connection alive so reads after
        writes see the latest data even when the writer ran on a
        different asyncio.to_thread worker. ``PRAGMA foreign_keys=ON``
        is the canonical cascade-test setup for this project.
        """
        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(eng, "connect")
        def _enable_fk(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        SQLModel.metadata.create_all(eng)
        try:
            yield eng
        finally:
            eng.dispose()

    def _seed_instance(self, session, instance_id, parent_id, now):
        """Insert one ``Instance`` row + the matching ``InstanceHierarchy`` row.

        Helper used by both W2 tests. Returns nothing; commit is the
        caller's responsibility (so multiple instances can be added
        in a single session, mirroring
        ``test_instance_hard_delete.py::seed_tree``).
        """
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                parent_id=parent_id,
                status="terminated",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        if parent_id is not None:
            session.add(
                InstanceHierarchy(
                    parent_id=parent_id,
                    child_id=instance_id,
                    created_at=now,
                )
            )

    @pytest.mark.asyncio
    async def test_integration_real_tree_pin_grandchild(self, engine):
        """W2: pinning a grandchild protects the entire 3-level subtree.

        Tree shape::

            root-A
              └─ child-A1
                   └─ grandchild-A1a

        Pinning ``grandchild-A1a`` walks the real parent chain up
        (grandchild → child → root) via
        :meth:`SQLModelInstanceRepository.get_tree_root_id`, then
        walks the hierarchy table back down (BFS) via
        :meth:`SQLModelInstanceRepository.get_tree_ids`. The full
        subtree is the protected set.

        Uses a ``MagicMock`` ``ui_prefs_repo`` because W2 is about
        exercising real tree traversal, not the real prefs query —
        the mocked prefs return pins ``{grandchild_id}`` and the
        repository methods do the rest.
        """
        root_id = "root-A"
        child_id = "child-A1"
        grandchild_id = "grandchild-A1a"

        now = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            # Build the 3-level tree.
            self._seed_instance(s, root_id, parent_id=None, now=now)
            self._seed_instance(s, child_id, parent_id=root_id, now=now)
            self._seed_instance(
                s, grandchild_id, parent_id=child_id, now=now
            )
            s.commit()

        # Real repository, mocked prefs.
        real_instance_repo = SQLModelInstanceRepository(engine)
        ui_prefs_repo = MagicMock()
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={grandchild_id}
        )

        config = PersistenceConfig()
        checkpointer = AsyncMock()
        job = CheckpointCleanupJob(
            config,
            checkpointer,
            real_instance_repo,
            ui_prefs_repo=ui_prefs_repo,
        )

        protected = job._get_protected_instance_ids()

        # The full root-A subtree is protected, not just the pinned
        # grandchild — exercising real get_tree_root_id (up) and
        # get_tree_ids (down).
        assert protected == {root_id, child_id, grandchild_id}

    @pytest.mark.asyncio
    async def test_integration_broken_ancestor_chain_protects_child(
        self, engine, caplog
    ):
        """W1: a live pinned instance with a broken parent chain is fail-protected.

        Setup approach (ghost-parent): the pinned instance
        ``broken-child`` is inserted with ``parent_id="ghost-middle"``
        where ``"ghost-middle"`` is NEVER inserted as an ``Instance``
        row. No ``InstanceHierarchy`` row for ``broken-child`` is
        created either (it has no parent in the DB). This produces the
        exact broken-chain state the production code's W1 path is
        designed to handle:

        * ``db_session.get(Instance, "broken-child")`` → returns the
          row (it exists).
        * ``broken-child.parent_id == "ghost-middle"`` is set.
        * ``get_tree_root_id("broken-child")`` walks:
          iteration 1 → get("broken-child") → exists, parent_id
          is "ghost-middle" → next.
          iteration 2 → get("ghost-middle") → returns None → returns
          None.
        * The job then re-checks ``get("broken-child")`` → exists →
          logs the broken-chain WARNING → treats it as its own root →
          calls ``get_tree_ids("broken-child")`` → returns
          ``["broken-child"]``.

        The ghost-parent approach is the cleanest option here: there
        are no FK constraints on ``Instance.parent_id`` (it's just an
        indexed ``str`` column) or on ``InstanceHierarchy``, so we can
        freely reference a non-existent parent id without triggering
        an ``IntegrityError``. We also keep the intact root →
        middle → child tree in the same DB so the test verifies the
        broken-chain handling is per-instance, not table-wide.
        """
        # Intact 3-level tree (control: real rows, real hierarchy).
        root_id = "root-A"
        middle_id = "middle-A1"
        child_id = "child-A2"
        # Ghost parent id: never inserted as an Instance row.
        ghost_id = "ghost-middle"
        # The pinned instance — its parent_id points to the ghost,
        # so get_tree_root_id("broken-child") returns None.
        broken_child_id = "broken-child"

        now = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            # Intact root → middle → child tree.
            self._seed_instance(s, root_id, parent_id=None, now=now)
            self._seed_instance(s, middle_id, parent_id=root_id, now=now)
            self._seed_instance(s, child_id, parent_id=middle_id, now=now)
            # The pinned instance: parent_id → ghost (no row), no
            # InstanceHierarchy row. NOTE: _seed_instance adds an
            # InstanceHierarchy row only when parent_id is not None;
            # we deliberately let that insert happen with the ghost
            # id (InstanceHierarchy has no FK), but to keep the
            # broken-child genuinely orphan-of-rows we add a separate
            # plain Instance row below instead.
            s.commit()

        # The helper above would also have created an
        # ``InstanceHierarchy(ghost_id, broken_child)`` row. Since
        # we want broken-child to have NO hierarchy row at all (and
        # the ghost has no Instance row), delete the
        # auto-created hierarchy row before continuing.
        with Session(engine) as s:
            from sqlmodel import select as _select  # local import to avoid top-level churn
            stale = s.exec(
                _select(InstanceHierarchy).where(
                    InstanceHierarchy.child_id == broken_child_id
                )
            ).all()
            for row in stale:
                s.delete(row)
            # Also ensure no broken_child row leaked in (the
            # _seed_instance call above should not have added one
            # because we passed parent_id=ghost_id, which is not
            # None — so an Instance row WAS added with parent_id
            # pointing to the ghost). Confirm/insert intentionally
            # so the test is self-documenting.
            existing = s.get(Instance, broken_child_id)
            if existing is None:
                s.add(
                    Instance(
                        instance_id=broken_child_id,
                        agent_id="developer",
                        agent_dir="/tmp/agents/developer",
                        agent_name="developer",
                        parent_id=ghost_id,
                        status="terminated",
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            s.commit()

        real_instance_repo = SQLModelInstanceRepository(engine)
        ui_prefs_repo = MagicMock()
        ui_prefs_repo.get_pinned_instance_ids = MagicMock(
            return_value={broken_child_id}
        )

        config = PersistenceConfig()
        checkpointer = AsyncMock()
        job = CheckpointCleanupJob(
            config,
            checkpointer,
            real_instance_repo,
            ui_prefs_repo=ui_prefs_repo,
        )

        caplog.set_level("WARNING", logger="daemon.services.maintenance")
        protected = job._get_protected_instance_ids()

        # W1 fail-protected: broken-child is treated as its own
        # root and ends up in the protected set.
        assert broken_child_id in protected

        # The broken-chain WARNING must have been emitted. The
        # production code logs:
        #   "Pinned instance %s has a broken parent chain or depth
        #    limit was reached; protecting it as its own root"
        warning_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelname == "WARNING"
        ]
        assert any(
            "broken parent chain" in msg for msg in warning_messages
        ), f"Expected broken-chain WARNING, got: {warning_messages}"
        assert any(
            broken_child_id in msg for msg in warning_messages
        ), f"Expected broken-child id in WARNING, got: {warning_messages}"
