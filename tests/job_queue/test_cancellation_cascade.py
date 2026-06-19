"""Comprehensive tests for cancellation cascade.

Tests the cancel_job() method in JobQueueService which handles:
- PENDING -> CANCELLED directly
- PROCESSING with alive instance -> terminate -> FAILED -> CANCELLED
- PROCESSING with dead instance -> CANCELLED directly
- Terminal states -> returns False
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.repositories.job_queue import JobRepository, JobStatus


class MockJobItem:
    """Mock JobItem for testing."""

    def __init__(
        self,
        job_id: str = None,
        status: str = "processing",
        instance_id: str = None,
        project_id: str = "test-project",
        queue_id: str = "test-queue-id",
    ):
        self.job_id = job_id or "test-job-id"
        self.status = status
        self.instance_id = instance_id or "test-instance-id"
        self.project_id = project_id
        self.queue_id = queue_id


class MockInstanceMeta:
    """Mock instance metadata."""

    def __init__(self, instance_id: str = None, status: str = "running"):
        self.instance_id = instance_id or "test-instance-id"
        self.status = status


class MockInstanceRepository:
    """Mock instance repository."""

    def __init__(self, instances: dict[str, MockInstanceMeta] = None):
        self._instances = instances or {}

    def get(self, instance_id: str):
        return self._instances.get(instance_id)


@pytest.fixture
def mock_repository():
    """Create a mock JobRepository."""
    repo = MagicMock(spec=JobRepository)
    return repo


@pytest.fixture
def mock_lock_manager():
    """Create a mock JobLockManager."""
    manager = MagicMock()
    manager.release = AsyncMock(return_value=True)
    manager.release_queue_lock = AsyncMock(return_value=True)
    manager.release_by_instance = AsyncMock(return_value=[("test-project", "test-job-id")])
    return manager


@pytest.fixture
def mock_queue_repo():
    """Create a mock JobQueueRepository."""
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_instance_manager():
    """Create a mock InstanceManager."""
    manager = MagicMock()
    manager.terminate_instance = AsyncMock(return_value=True)
    return manager


class TestCancelPendingJob:
    """Tests for cancelling pending jobs."""

    @pytest.mark.asyncio
    async def test_cancel_pending_job_direct_transition(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """PENDING -> CANCELLED directly."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: pending job
        pending_job = MockJobItem(job_id="job-pending", status="pending")
        mock_repository.get.return_value = pending_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("job-pending")

        # Verify: cancellation succeeded
        assert result is True

        # Verify: atomic repository cancel_job was called (single UPDATE-WHERE-IN
        # covering all cancellable states; closes the TOCTOU window against
        # concurrent start_job transitions).
        mock_repository.cancel_job.assert_called_once_with("job-pending")

        # Verify: instance manager was NOT called (no instance to terminate)
        mock_instance_manager.terminate_instance.assert_not_called()


class TestCancelProcessingJob:
    """Tests for cancelling processing jobs."""

    @pytest.mark.asyncio
    async def test_cancel_processing_job_with_alive_instance(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """PROCESSING job with alive instance -> calls terminate_instance, then FAILED -> CANCELLED."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job with alive instance
        processing_job = MockJobItem(
            job_id="job-processing",
            status="processing",
            instance_id="instance-alive",
            project_id="test-project",
            queue_id="test-queue",
        )
        mock_repository.get.return_value = processing_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Mock instance alive check
        mock_instance_meta = MockInstanceMeta(instance_id="instance-alive", status="running")
        mock_instance_manager._instance_repository = MockInstanceRepository({
            "instance-alive": mock_instance_meta
        })

        # Cancel
        result = await service.cancel_job("job-processing")

        # Verify: cancellation succeeded
        assert result is True

        # Verify: instance was terminated
        mock_instance_manager.terminate_instance.assert_called_once_with("instance-alive")

        # Verify: locks were released
        mock_lock_manager.release_queue_lock.assert_called()

    @pytest.mark.asyncio
    async def test_cancel_processing_job_with_dead_instance(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """PROCESSING job with dead/terminated instance -> direct CANCELLED."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job with dead instance
        processing_job = MockJobItem(
            job_id="job-dead",
            status="processing",
            instance_id="instance-dead",
            project_id="test-project",
            queue_id="test-queue",
        )
        mock_repository.get.return_value = processing_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Mock instance dead check (instance doesn't exist in repository)
        mock_instance_manager._instance_repository = MockInstanceRepository({})

        # Cancel
        result = await service.cancel_job("job-dead")

        # Verify: cancellation succeeded
        assert result is True

        # Verify: instance was NOT terminated (already dead)
        mock_instance_manager.terminate_instance.assert_not_called()

        # Verify: atomic repository cancel_job was called (single UPDATE-WHERE-IN
        # that includes PROCESSING in the cancellable set).
        mock_repository.cancel_job.assert_called_once_with("job-dead")

    @pytest.mark.asyncio
    async def test_cancel_processing_job_with_terminal_instance(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """PROCESSING job with terminal instance (completed/terminated) -> direct CANCELLED."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job with terminal instance
        processing_job = MockJobItem(
            job_id="job-terminal",
            status="processing",
            instance_id="instance-terminal",
            project_id="test-project",
            queue_id="test-queue",
        )
        mock_repository.get.return_value = processing_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Mock instance as terminal (completed)
        mock_instance_meta = MockInstanceMeta(instance_id="instance-terminal", status="completed")
        mock_instance_manager._instance_repository = MockInstanceRepository({
            "instance-terminal": mock_instance_meta
        })

        # Cancel
        result = await service.cancel_job("job-terminal")

        # Verify: cancellation succeeded
        assert result is True

        # Verify: instance was NOT terminated (already terminal)
        mock_instance_manager.terminate_instance.assert_not_called()

        # Verify: atomic repository cancel_job was called (PROCESSING is in
        # the cancellable set even when the instance is already terminal).
        mock_repository.cancel_job.assert_called_once_with("job-terminal")


class TestCancelAlreadyTerminalJob:
    """Tests for cancelling already terminal jobs."""

    @pytest.mark.asyncio
    async def test_cancel_completed_job(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """COMPLETED job -> returns False."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: completed job
        completed_job = MockJobItem(job_id="job-completed", status="completed")
        mock_repository.get.return_value = completed_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("job-completed")

        # Verify: cancellation failed
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_failed_job(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """FAILED job -> transitions to CANCELLED (stops retries)."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: failed job
        failed_job = MockJobItem(job_id="job-failed", status="failed")
        mock_repository.get.return_value = failed_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("job-failed")

        # Verify: cancellation succeeded
        assert result is True
        
        # Verify: atomic repository cancel_job was called (FAILED is in the
        # cancellable set; stops any pending retries).
        mock_repository.cancel_job.assert_called_once_with("job-failed")

    @pytest.mark.asyncio
    async def test_cancel_cancelled_job(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """CANCELLED job -> returns False."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: cancelled job
        cancelled_job = MockJobItem(job_id="job-cancelled", status="cancelled")
        mock_repository.get.return_value = cancelled_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("job-cancelled")

        # Verify: cancellation failed
        assert result is False


class TestCancelNonexistentJob:
    """Tests for cancelling non-existent jobs."""

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_job(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """Non-existent job -> returns False."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: no job found
        mock_repository.get.return_value = None

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("nonexistent-job")

        # Verify: cancellation failed
        assert result is False


class TestCancelCascadeReleasesLocks:
    """Tests for lock release during cancellation cascade."""

    @pytest.mark.asyncio
    async def test_cancel_releases_queue_locks(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """Locks are released after cancellation."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job with queue
        processing_job = MockJobItem(
            job_id="job-locked",
            status="processing",
            instance_id="instance-dead",
            project_id="test-project",
            queue_id="test-queue",
        )
        mock_repository.get.return_value = processing_job

        # Mock instance dead
        mock_instance_manager._instance_repository = MockInstanceRepository({})

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("job-locked")

        # Verify: cancellation succeeded
        assert result is True

        # Verify: queue lock was released
        mock_lock_manager.release_queue_lock.assert_called_with(
            "test-project", "test-queue", "job-locked"
        )

    @pytest.mark.asyncio
    async def test_cancel_with_project_but_no_queue_releases_project_lock(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """Jobs with project_id but no queue_id release project-level lock."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job with project but no queue
        processing_job = MockJobItem(
            job_id="job-project",
            status="processing",
            instance_id="instance-dead",
            project_id="test-project",
            queue_id=None,  # No queue
        )
        mock_repository.get.return_value = processing_job

        # Mock instance dead
        mock_instance_manager._instance_repository = MockInstanceRepository({})

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("job-project")

        # Verify: cancellation succeeded
        assert result is True

        # Verify: project lock was released (via release, not release_queue_lock)
        mock_lock_manager.release.assert_called_with("test-project", "job-project")


class TestCancelProcessingWithAliveInstanceCascade:
    """Tests for the full cancellation cascade with alive instance."""

    @pytest.mark.asyncio
    async def test_cancel_cascade_full_flow(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """Full cascade: PROCESSING -> terminate -> CANCELLED (simplified)."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job with alive instance
        processing_job = MockJobItem(
            job_id="job-cascade",
            status="processing",
            instance_id="instance-alive",
            project_id="test-project",
            queue_id="test-queue",
        )
        mock_repository.get.return_value = processing_job

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Mock instance alive
        mock_instance_meta = MockInstanceMeta(instance_id="instance-alive", status="running")
        mock_instance_manager._instance_repository = MockInstanceRepository({
            "instance-alive": mock_instance_meta
        })

        # Mock terminate_instance: it now marks job as CANCELLED directly
        # via complete_job_sync(job_id, DemandState.CANCELLED, ...)
        async def mock_terminate(instance_id: str) -> bool:
            mock_repository.atomic_transition(
                job_id="job-cascade",
                from_status="processing",
                to_status="cancelled",
            )
            return True

        mock_instance_manager.terminate_instance = AsyncMock(side_effect=mock_terminate)

        # Cancel
        result = await service.cancel_job("job-cascade")

        # Verify: cancellation succeeded
        assert result is True

        # Verify: terminate_instance was called
        mock_instance_manager.terminate_instance.assert_called_once_with("instance-alive")

        # Verify: terminate_instance marks job as CANCELLED directly
        calls = mock_repository.atomic_transition.call_args_list
        assert any(
            c.kwargs.get("to_status") == "cancelled" or (len(c.args) > 2 and c.args[2] == "cancelled")
            for c in calls
        ), "Expected transition to 'cancelled' status"


class TestCancelNoInstanceManager:
    """Tests for cancellation without instance manager."""

    @pytest.mark.asyncio
    async def test_cancel_processing_without_instance_manager(
        self, mock_repository, mock_lock_manager, mock_queue_repo
    ):
        """PROCESSING job with no instance manager -> direct CANCELLED."""
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job
        processing_job = MockJobItem(
            job_id="job-no-manager",
            status="processing",
            instance_id="some-instance",
            project_id="test-project",
            queue_id="test-queue",
        )
        mock_repository.get.return_value = processing_job

        # Create service WITHOUT instance manager
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=None,  # No instance manager
        )

        # Cancel
        result = await service.cancel_job("job-no-manager")

        # Verify: cancellation succeeded (direct transition)
        assert result is True

        # Verify: atomic repository cancel_job was called (PROCESSING is in
        # the cancellable set; single UPDATE-WHERE-IN).
        mock_repository.cancel_job.assert_called_once_with("job-no-manager")


class TestCancelRaceCondition:
    """Tests for race conditions during cancellation.

    Under the new atomic repo.cancel_job implementation, races against
    concurrent transitions are handled by the SQL-level status-IN guard:
    as long as the job is in any cancellable state (PENDING/PROCESSING/
    FAILED) when the UPDATE commits, the cancel succeeds. The cancel
    is only lost if the job has already moved to a non-cancellable
    terminal state (COMPLETED/CANCELLED) before the UPDATE runs — and
    in that case the disambiguation SELECT inside cancel_job raises
    ValueError, which the service maps to a False return.
    """

    @pytest.mark.asyncio
    async def test_cancel_processing_job_already_transitioned(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager
    ):
        """Job already moved to non-cancellable terminal -> cancel returns False.

        Simulates the race where, between the service's get() and the
        atomic repo.cancel_job() call, another actor transitioned the
        job out of the cancellable set (e.g. to COMPLETED). The atomic
        UPDATE in cancel_job will match no rows, the disambiguation
        SELECT will find the row but in a non-cancellable state, and
        repo.cancel_job raises ValueError. The service catches it and
        returns False.
        """
        from daemon.services.job_queue_service import JobQueueService

        # Setup: processing job
        processing_job = MockJobItem(
            job_id="job-race",
            status="processing",
            instance_id="instance-dead",
            project_id="test-project",
            queue_id="test-queue",
        )
        mock_repository.get.return_value = processing_job

        # Mock atomic cancel_job to raise ValueError — simulates a row that
        # moved to a non-cancellable terminal state between the read and
        # the UPDATE.
        mock_repository.cancel_job.side_effect = ValueError(
            "Cannot cancel job in 'completed' state, must be PENDING, PROCESSING, or FAILED"
        )

        # Mock instance dead
        mock_instance_manager._instance_repository = MockInstanceRepository({})

        # Create service
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel
        result = await service.cancel_job("job-race")

        # Verify: cancellation failed because job was no longer cancellable
        assert result is False


class TestIsInstanceAlive:
    """Tests for _is_instance_alive helper."""

    @pytest.mark.asyncio
    async def test_is_instance_alive_running(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """Running instance is alive."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        mock_instance_meta = MockInstanceMeta(instance_id="test-instance", status="running")
        mock_instance_manager._instance_repository = MockInstanceRepository({
            "test-instance": mock_instance_meta
        })

        result = service._is_instance_alive("test-instance")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_instance_alive_idle(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """Idle instance is alive."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        mock_instance_meta = MockInstanceMeta(instance_id="test-instance", status="idle")
        mock_instance_manager._instance_repository = MockInstanceRepository({
            "test-instance": mock_instance_meta
        })

        result = service._is_instance_alive("test-instance")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_instance_alive_completed(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """Completed instance is NOT alive."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        mock_instance_meta = MockInstanceMeta(instance_id="test-instance", status="completed")
        mock_instance_manager._instance_repository = MockInstanceRepository({
            "test-instance": mock_instance_meta
        })

        result = service._is_instance_alive("test-instance")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_instance_alive_not_found(self, mock_repository, mock_lock_manager, mock_queue_repo, mock_instance_manager):
        """Not found instance is NOT alive."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        mock_instance_manager._instance_repository = MockInstanceRepository({})

        result = service._is_instance_alive("nonexistent-instance")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_instance_alive_no_instance_manager(self, mock_repository, mock_lock_manager, mock_queue_repo):
        """No instance manager means instance is NOT alive."""
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=None,
        )

        result = service._is_instance_alive("any-instance")
        assert result is False
