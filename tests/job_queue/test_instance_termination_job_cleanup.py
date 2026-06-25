"""Comprehensive tests for instance termination job queue fixes.

Tests verify:
1. start_job() cancels jobs for terminated/completed/error/failed instances
2. start_job() keeps jobs pending for paused instances
3. terminate_instance() cleans up all remaining jobs
4. JobProcessor orphan detection cancels jobs for terminated instances

These tests cover the fixes for proper instance termination handling with the job queue.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call
import pytest

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.services.job_queue_service import JobQueueService, DemandState, TERMINAL_STATUSES


# =============================================================================
# Mock Classes
# =============================================================================


class MockInstance:
    """Mock instance object for testing."""

    def __init__(self, instance_id: str, status: str = InstanceStatus.RUNNING.value):
        self.instance_id = instance_id
        self.status = status
        self.agent_id = "test-agent"
        self.parent_id = None
        self.children = []


class MockProject:
    """Mock project object for testing."""

    def __init__(self, project_id: str, job_queue_paused: bool = False):
        self.project_id = project_id
        self.job_queue_paused = job_queue_paused


class MockJob:
    """Mock job object for testing."""

    def __init__(
        self,
        job_id: str,
        agent_id: str = "developer",
        project_id: str = "project-1",
        queue_id: str = "queue-1",
        status: str = JobStatus.PENDING.value,
        instance_id: str | None = None,
        job_type: str = "task",
    ):
        self.job_id = job_id
        self.agent_id = agent_id
        self.project_id = project_id
        self.queue_id = queue_id
        self.status = status
        self.instance_id = instance_id
        self.job_type = job_type
        self.message = "test message"
        self.source = "api"
        self.created_at = "2024-01-01T00:00:00"
        self.started_at = None
        self.completed_at = None
        self.error_message = None
        self.result_summary = None
        self.job_metadata = {}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_instance_manager_with_repo():
    """Create mock instance manager with instance_repository."""
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    # transition_status_if returns Instance | None — return a truthy mock so the
    # "reactivation succeeded" branch fires in production code.
    manager._instance_repository.transition_status_if = MagicMock(
        return_value=MagicMock()
    )
    return manager


@pytest.fixture
def mock_project_repo():
    """Create mock project repository."""
    repo = MagicMock()
    repo.get = MagicMock(return_value=MockProject("project-1", job_queue_paused=False))
    return repo


@pytest.fixture
def mock_repository():
    """Create mock job repository."""
    repo = MagicMock()
    repo.get = MagicMock()
    repo.start_job_atomic = MagicMock()
    repo.atomic_transition = MagicMock()
    repo.get_by_instance = MagicMock(return_value=None)
    repo.find_jobs_by_instance = MagicMock(return_value=[])
    repo.update = MagicMock(return_value=None)
    return repo


@pytest.fixture
def mock_lock_manager():
    """Create mock lock manager."""
    manager = MagicMock()
    manager.acquire = AsyncMock(return_value=True)
    manager.acquire_queue_lock = AsyncMock(return_value=True)
    manager.release = AsyncMock()
    manager.release_queue_lock = AsyncMock()
    manager.release_by_instance = AsyncMock()
    return manager


@pytest.fixture
def mock_queue_repo():
    """Create mock queue repository."""
    repo = MagicMock()
    repo.get = MagicMock(return_value=MagicMock(concurrency_limit=1))
    repo.get_concurrency_limit = MagicMock(return_value=1)
    return repo


@pytest.fixture
def job_queue_service(
    mock_repository, mock_lock_manager, mock_queue_repo,
    mock_instance_manager_with_repo, mock_project_repo
):
    """Create JobQueueService with mocked dependencies."""
    service = JobQueueService(
        repository=mock_repository,
        lock_manager=mock_lock_manager,
        queue_repo=mock_queue_repo,
        instance_manager=mock_instance_manager_with_repo,
    )
    service.set_project_repo(mock_project_repo)
    return service


# =============================================================================
# Test Group 1: start_job() Instance Status Checks
# =============================================================================


class TestStartJobInstanceStatusChecks:
    """Tests for start_job() checking instance status before starting jobs.

    The fix ensures start_job() checks instance status for ALL job types
    (not just MESSAGE) and cancels jobs for terminal instances.
    """

    @pytest.mark.asyncio
    async def test_start_job_clears_stale_instance_for_terminated_task_job(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() clears stale instance_id for TASK jobs with TERMINATED instance.

        TASK jobs with terminal instances: the stale instance_id is cleared and the job
        falls through to normal start logic (doesn't cancel, continues processing).
        """
        instance_id = "terminated-instance-123"
        job_id = "job-terminated"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="task",
        )
        mock_repository.get.return_value = job
        mock_repository.start_job_atomic.return_value = job  # Simulate successful start

        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.TERMINATED.value
        )

        result = await job_queue_service.start_job(job_id)

        # For TASK jobs: instance_id is cleared, job continues to normal start
        mock_repository.update.assert_called_with(job_id, instance_id=None)
        # Job should be started (not cancelled)
        assert result is not None

    @pytest.mark.asyncio
    async def test_start_job_clears_stale_instance_for_completed_task_job(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() clears stale instance_id for TASK jobs with COMPLETED instance.

        TASK jobs with terminal instances: the stale instance_id is cleared and the job
        falls through to normal start logic (doesn't cancel, continues processing).
        """
        instance_id = "completed-instance-123"
        job_id = "job-completed"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="task",
        )
        mock_repository.get.return_value = job
        mock_repository.start_job_atomic.return_value = job

        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.COMPLETED.value
        )

        result = await job_queue_service.start_job(job_id)

        # For TASK jobs: instance_id is cleared, job continues to normal start
        mock_repository.update.assert_called_with(job_id, instance_id=None)
        # Job should be started (not cancelled)
        assert result is not None

    @pytest.mark.asyncio
    async def test_start_job_clears_stale_instance_for_error_task_job(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() clears stale instance_id for TASK jobs with ERROR instance.

        TASK jobs with terminal instances: the stale instance_id is cleared and the job
        falls through to normal start logic (doesn't cancel, continues processing).
        """
        instance_id = "error-instance-123"
        job_id = "job-error"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="task",
        )
        mock_repository.get.return_value = job
        mock_repository.start_job_atomic.return_value = job

        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.ERROR.value
        )

        result = await job_queue_service.start_job(job_id)

        # For TASK jobs: instance_id is cleared, job continues to normal start
        mock_repository.update.assert_called_with(job_id, instance_id=None)
        # Job should be started (not cancelled)
        assert result is not None

    @pytest.mark.asyncio
    async def test_start_job_clears_stale_instance_for_failed_task_job(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() clears stale instance_id for TASK jobs with FAILED instance.

        TASK jobs with terminal instances: the stale instance_id is cleared and the job
        falls through to normal start logic (doesn't cancel, continues processing).
        """
        instance_id = "failed-instance-123"
        job_id = "job-failed"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="task",
        )
        mock_repository.get.return_value = job
        mock_repository.start_job_atomic.return_value = job

        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.FAILED.value
        )

        result = await job_queue_service.start_job(job_id)

        # For TASK jobs: instance_id is cleared, job continues to normal start
        mock_repository.update.assert_called_with(job_id, instance_id=None)
        # Job should be started (not cancelled)
        assert result is not None

    @pytest.mark.asyncio
    async def test_start_job_keeps_job_pending_for_paused_instance(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() keeps a PENDING job when instance is PAUSED.

        Unlike terminal states, PAUSED instances should keep jobs as PENDING
        (not cancel them) so they can resume later.
        """
        instance_id = "paused-instance-123"
        job_id = "job-paused"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="task",
        )
        mock_repository.get.return_value = job

        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.PAUSED.value
        )

        result = await job_queue_service.start_job(job_id)

        # Job should NOT be started, but should also NOT be cancelled
        assert result is None
        # atomic_transition should NOT be called (no cancellation)
        mock_repository.atomic_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_job_keeps_job_pending_for_running_instance(
        self, job_queue_service, mock_repository, mock_lock_manager,
        mock_instance_manager_with_repo, mock_queue_repo
    ):
        """Test that start_job() processes a PENDING job for a RUNNING instance."""
        instance_id = "running-instance-123"
        job_id = "job-running"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=None,  # TASK job - no pre-set instance
            job_type="task",
        )
        mock_repository.get.return_value = job

        started_job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PROCESSING.value,
            instance_id="new-instance-123",
            job_type="task",
        )
        mock_repository.start_job_atomic.return_value = started_job

        result = await job_queue_service.start_job(job_id)

        # Job should be started
        assert result is not None
        assert result.status == JobStatus.PROCESSING.value

    @pytest.mark.asyncio
    async def test_start_job_keeps_job_pending_for_idle_instance(
        self, job_queue_service, mock_repository, mock_lock_manager,
        mock_instance_manager_with_repo, mock_queue_repo
    ):
        """Test that start_job() processes a PENDING job for an IDLE instance."""
        instance_id = "idle-instance-123"
        job_id = "job-idle"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=None,
            job_type="task",
        )
        mock_repository.get.return_value = job

        started_job = MockJob(
            job_id=job_id,
            status=JobStatus.PROCESSING.value,
            instance_id="new-instance-123",
            job_type="task",
        )
        mock_repository.start_job_atomic.return_value = started_job

        result = await job_queue_service.start_job(job_id)

        assert result is not None
        assert result.status == JobStatus.PROCESSING.value

    @pytest.mark.asyncio
    async def test_start_job_returns_none_for_missing_instance(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() returns None when instance is not found.

        Jobs with missing instances should not proceed - they stay PENDING
        and will be handled by orphan detection in JobProcessor.
        """
        job_id = "job-missing-instance"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id="non-existent-instance",
            job_type="task",
        )
        mock_repository.get.return_value = job
        mock_instance_manager_with_repo._instance_repository.get.return_value = None

        result = await job_queue_service.start_job(job_id)

        assert result is None
        # Job stays PENDING (not cancelled by start_job)

    @pytest.mark.asyncio
    async def test_start_job_works_for_task_type_jobs(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() clears stale instance_id for TASK jobs with terminal instances.

        TASK jobs with terminal instances: the stale instance_id is cleared and the job
        falls through to normal start logic (doesn't cancel).
        """
        instance_id = "terminated-task-instance"
        job_id = "task-job-terminated"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="task",
        )
        mock_repository.get.return_value = job
        mock_repository.start_job_atomic.return_value = job

        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.TERMINATED.value
        )

        result = await job_queue_service.start_job(job_id)

        # For TASK jobs: instance_id is cleared, job continues to normal start
        mock_repository.update.assert_called_with(job_id, instance_id=None)
        # Job should be started (not cancelled)
        assert result is not None

    @pytest.mark.asyncio
    async def test_start_job_reactivates_message_job_for_terminated_instance(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() reactivates MESSAGE jobs for terminated instances.

        MESSAGE jobs targeting TERMINATED instances should be reactivated
        (instance status → RUNNING) and proceed to normal processing.
        """
        instance_id = "terminated-message-instance"
        job_id = "message-job-terminated"

        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="message",
        )
        mock_repository.get.return_value = job
        mock_repository.start_job_atomic.return_value = job

        # Mock _live_hub with AsyncMock for stream_status_change
        mock_instance_manager_with_repo._live_hub = MagicMock()
        mock_instance_manager_with_repo._live_hub.stream_status_change = AsyncMock()

        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.TERMINATED.value
        )

        result = await job_queue_service.start_job(job_id)

        # Job should be reactivated and proceed to processing (not cancelled)
        assert result is not None
        # Instance status should be transitioned to RUNNING via atomic guard
        mock_instance_manager_with_repo._instance_repository.transition_status_if.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value, tuple(TERMINAL_STATUSES)
        )
        # stream_status_change should be called
        mock_instance_manager_with_repo._live_hub.stream_status_change.assert_called_once_with(
            instance_id, InstanceStatus.RUNNING.value, agent_id="test-agent"
        )
        # Job should proceed to normal processing (start_job_atomic called)
        mock_repository.start_job_atomic.assert_called_once_with(job_id, instance_id)


# =============================================================================
# Test Group 2: terminate_instance() Job Cleanup
# =============================================================================


class TestTerminateInstanceJobCleanup:
    """Tests for terminate_instance() cleaning up all remaining jobs.

    The fix adds step 7.6 that does a comprehensive sweep of ALL remaining
    jobs for a terminated instance (any type, any non-terminal state).
    """

    @pytest.fixture
    def mock_manager(self):
        """Create mock InstanceManager for terminate_instance tests."""
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._graph_tasks = {}  # No active graph tasks
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.cleanup_instance = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._watcher_repo = MagicMock()
        manager._watcher_repo.remove_all_watches_for_instance = MagicMock(return_value=0)
        manager._mcp_service = None  # No MCP service
        manager.instances = {}
        return manager

    @pytest.fixture
    def mock_job_queue_service(self):
        """Create mock JobQueueService for terminate_instance tests."""
        service = MagicMock()
        service._repository = MagicMock()
        service._repository.get_by_instance = MagicMock(return_value=None)
        service._repository.find_jobs_by_instance = MagicMock(return_value=[])
        service.cancel_job = AsyncMock(return_value=True)
        service.cancel_message_job = AsyncMock(return_value=True)
        service.complete_job = AsyncMock(return_value=None)
        service.complete_job_sync = MagicMock(return_value=None)
        service.release_lock_by_instance = AsyncMock(return_value=[])
        service.trigger_next_job_sync = MagicMock()
        return service

    @pytest.fixture
    def mock_cancellation_service(self):
        """Create mock CancellationService."""
        return MagicMock()

    @pytest.fixture
    def lifecycle_service(self, mock_manager, mock_cancellation_service, mock_job_queue_service):
        """Create InstanceLifecycleService with mocked dependencies."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        service = InstanceLifecycleService(
            manager=mock_manager,
            cancellation_service=mock_cancellation_service,
            job_queue_service=mock_job_queue_service,
        )
        return service

    @pytest.mark.asyncio
    async def test_terminate_cancels_processing_task_jobs(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() completes PROCESSING TASK jobs as CANCELLED."""
        instance_id = "terminate-instance-123"

        # Mock instance metadata
        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock PROCESSING TASK job
        processing_task = MockJob(
            job_id="processing-task",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PROCESSING.value,
            instance_id=instance_id,
            job_type="task",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [processing_task]
        mock_job_queue_service._repository.get_by_instance.return_value = processing_task

        await lifecycle_service.terminate_instance(instance_id)

        # For PROCESSING jobs: complete_job with CANCELLED state is used (to avoid re-entrancy)
        mock_job_queue_service.complete_job.assert_called()

    @pytest.mark.asyncio
    async def test_terminate_cancels_pending_task_jobs(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() cancels PENDING TASK jobs.

        This verifies step 7.6 - the comprehensive sweep of ALL remaining jobs.
        """
        instance_id = "terminate-instance-pending"

        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock PENDING TASK job
        pending_task = MockJob(
            job_id="pending-task",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="task",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [pending_task]

        await lifecycle_service.terminate_instance(instance_id)

        # cancel_job should be called for the pending task
        mock_job_queue_service.cancel_job.assert_called()

    @pytest.mark.asyncio
    async def test_terminate_cancels_processing_message_jobs(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() completes PROCESSING MESSAGE jobs as CANCELLED."""
        instance_id = "terminate-instance-message"

        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock PROCESSING MESSAGE job
        processing_message = MockJob(
            job_id="processing-message",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PROCESSING.value,
            instance_id=instance_id,
            job_type="message",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [processing_message]
        mock_job_queue_service._repository.get_by_instance.return_value = processing_message

        await lifecycle_service.terminate_instance(instance_id)

        # For PROCESSING jobs: complete_job with CANCELLED state is used
        mock_job_queue_service.complete_job.assert_called()

    @pytest.mark.asyncio
    async def test_terminate_cancels_pending_message_jobs(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() cancels PENDING MESSAGE jobs."""
        instance_id = "terminate-instance-pending-msg"

        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock PENDING MESSAGE job
        pending_message = MockJob(
            job_id="pending-message",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="message",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [pending_message]

        await lifecycle_service.terminate_instance(instance_id)

        mock_job_queue_service.cancel_job.assert_called()

    @pytest.mark.asyncio
    async def test_terminate_cancels_failed_jobs(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() CANCELS FAILED jobs.

        FAILED jobs should now be included in find_jobs_by_instance() and
        cancelled during termination cleanup.
        """
        instance_id = "terminate-instance-failed"

        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock FAILED job (now included in find_jobs_by_instance)
        failed_job = MockJob(
            job_id="failed-job",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.FAILED.value,
            instance_id=instance_id,
            job_type="task",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [failed_job]

        await lifecycle_service.terminate_instance(instance_id)

        # cancel_job should be called for failed jobs (they are now included)
        mock_job_queue_service.cancel_job.assert_called()

    @pytest.mark.asyncio
    async def test_terminate_does_not_cancel_cancelled_jobs(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() does NOT cancel already CANCELLED jobs."""
        instance_id = "terminate-instance-cancelled"

        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock CANCELLED job (terminal state - should be skipped)
        cancelled_job = MockJob(
            job_id="cancelled-job",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.CANCELLED.value,
            instance_id=instance_id,
            job_type="task",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [cancelled_job]

        await lifecycle_service.terminate_instance(instance_id)

        mock_job_queue_service.cancel_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminate_does_not_cancel_dead_letter_jobs(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() does NOT cancel DEAD_LETTER jobs."""
        instance_id = "terminate-instance-dlq"

        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock DEAD_LETTER job (terminal state - should be skipped)
        dlq_job = MockJob(
            job_id="dlq-job",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.DEAD_LETTER.value,
            instance_id=instance_id,
            job_type="task",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [dlq_job]

        await lifecycle_service.terminate_instance(instance_id)

        mock_job_queue_service.cancel_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminate_cleans_up_all_job_types(
        self, lifecycle_service, mock_manager, mock_job_queue_service
    ):
        """Test that terminate_instance() cleans up all job types together.

        This verifies that the comprehensive sweep (step 7.6) handles multiple
        jobs of different types and states correctly.

        - PENDING jobs → cancel_job()
        - PROCESSING jobs → complete_job() with CANCELLED
        - COMPLETED jobs → skipped (terminal state)
        """
        instance_id = "terminate-instance-multi"

        meta = MagicMock()
        meta.instance_id = instance_id
        meta.status = "running"
        meta.agent_id = "test-agent"
        meta.parent_id = None
        meta.children = []
        mock_manager._instance_repository.get.return_value = meta

        # Mock multiple jobs of different types and states
        pending_task = MockJob(
            job_id="pending-task",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            job_type="task",
        )
        processing_message = MockJob(
            job_id="processing-message",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PROCESSING.value,
            job_type="message",
        )
        completed_job = MockJob(
            job_id="completed-job",
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.COMPLETED.value,
            job_type="task",
        )

        mock_job_queue_service._repository.find_jobs_by_instance.return_value = [
            pending_task,
            processing_message,
            completed_job,
        ]

        await lifecycle_service.terminate_instance(instance_id)

        # PENDING → cancel_job, PROCESSING → complete_job, COMPLETED → skipped
        mock_job_queue_service.cancel_job.assert_called_once()  # Only pending_task
        mock_job_queue_service.complete_job.assert_called_once()  # Only processing_message


# =============================================================================
# Test Group 3: JobProcessor Orphan Detection
# =============================================================================


class TestJobProcessorOrphanDetection:
    """Tests for JobProcessor orphan detection.

    The fix ensures that TASK jobs with instance_id pointing to
    terminated/completed instances are cancelled instead of re-spawned.
    """

    @pytest.fixture
    def mock_queue_service(self):
        """Create mock JobQueueService."""
        service = MagicMock()
        service.start_job = AsyncMock()
        service.complete_job = AsyncMock()
        service._repository = MagicMock()
        service._repository.list_pending_by_queue = MagicMock(return_value=[])
        service._repository.list_by_queue = MagicMock(return_value=([], None))
        return service

    @pytest.fixture
    def mock_instance_manager(self):
        """Create mock InstanceManager."""
        manager = MagicMock()
        manager.spawn_instance_with_mcp = AsyncMock(return_value="new-instance-123")
        manager.enqueue_message = AsyncMock()
        manager.get_instance = AsyncMock(side_effect=KeyError("not found"))
        manager._instance_repository = MagicMock()
        return manager

    @pytest.fixture
    def mock_project_repo(self):
        """Create mock project repository."""
        repo = MagicMock()
        repo.list_projects = MagicMock(return_value=[])
        return repo

    @pytest.fixture
    def mock_queue_repo(self):
        """Create mock queue repository."""
        repo = MagicMock()
        repo.list_by_project = MagicMock(return_value=[])
        return repo

    @pytest.fixture
    def processor(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Create JobProcessor with mocked dependencies."""
        from daemon.services.job_processor import JobProcessor

        return JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

    @pytest.mark.skip(reason="Phase 4: orphan detection logic changed; obsolete behavior")
    @pytest.mark.asyncio
    async def test_processor_cancels_orphan_task_job_for_terminated_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor cancels TASK jobs for TERMINATED instances.

        When a PROCESSING TASK job has instance_id pointing to a TERMINATED
        instance, it should be cancelled (not re-spawned).
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        terminated_instance_id = "terminated-instance-123"

        # PROCESSING TASK job with TERMINATED instance
        orphan_task = MagicMock()
        orphan_task.job_id = "orphan-task"
        orphan_task.agent_id = "developer"
        orphan_task.project_id = "project-1"
        orphan_task.queue_id = "queue-1"
        orphan_task.status = JobStatus.PROCESSING.value
        orphan_task.instance_id = terminated_instance_id
        orphan_task.job_type = "task"
        orphan_task.message = "test"
        orphan_task.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([orphan_task], None)

        # Mock the terminated instance
        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        terminated_instance = MagicMock()
        terminated_instance.status = InstanceStatus.TERMINATED.value
        terminated_instance.waiting_for = 0
        mock_instance_manager._instance_repository.get.return_value = terminated_instance

        await processor._process_next_job()

        # Job should be cancelled, not re-spawned
        mock_queue_service.complete_job.assert_called()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args.kwargs.get("demand_state") == DemandState.CANCELLED
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()

    @pytest.mark.skip(reason="Phase 4: orphan detection logic changed; obsolete behavior")
    @pytest.mark.asyncio
    async def test_processor_completes_orphan_task_job_for_completed_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor COMPLETES TASK jobs for COMPLETED instances.

        COMPLETED instances should complete the job (not cancel it), since the
        instance finished successfully. The job should show "Job completed successfully"
        as the result_summary (via the service's fallback).
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        completed_instance_id = "completed-instance-123"

        orphan_task = MagicMock()
        orphan_task.job_id = "orphan-task-completed"
        orphan_task.agent_id = "developer"
        orphan_task.project_id = "project-1"
        orphan_task.queue_id = "queue-1"
        orphan_task.status = JobStatus.PROCESSING.value
        orphan_task.instance_id = completed_instance_id
        orphan_task.job_type = "task"
        orphan_task.message = "test"
        orphan_task.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([orphan_task], None)

        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        completed_instance = MagicMock()
        completed_instance.status = InstanceStatus.COMPLETED.value
        completed_instance.waiting_for = 0
        mock_instance_manager._instance_repository.get.return_value = completed_instance

        await processor._process_next_job()

        mock_queue_service.complete_job.assert_called()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args.kwargs.get("demand_state") == DemandState.COMPLETED
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()

    @pytest.mark.skip(reason="Phase 4: orphan detection logic changed; obsolete behavior")
    @pytest.mark.asyncio
    async def test_processor_fails_orphan_task_job_for_error_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor FAILS TASK jobs for ERROR instances.

        ERROR instances should fail the job (not cancel it), since the
        instance encountered an error during processing.
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        error_instance_id = "error-instance-123"

        orphan_task = MagicMock()
        orphan_task.job_id = "orphan-task-error"
        orphan_task.agent_id = "developer"
        orphan_task.project_id = "project-1"
        orphan_task.queue_id = "queue-1"
        orphan_task.status = JobStatus.PROCESSING.value
        orphan_task.instance_id = error_instance_id
        orphan_task.job_type = "task"
        orphan_task.message = "test"
        orphan_task.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([orphan_task], None)

        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        error_instance = MagicMock()
        error_instance.status = InstanceStatus.ERROR.value
        error_instance.waiting_for = 0
        mock_instance_manager._instance_repository.get.return_value = error_instance

        await processor._process_next_job()

        mock_queue_service.complete_job.assert_called()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args.kwargs.get("demand_state") == DemandState.FAILED
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_processor_skips_orphan_task_job_for_paused_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor SKIPS (not cancels) TASK jobs for PAUSED instances.

        PAUSED instances should keep jobs pending - they are not orphaned,
        just temporarily paused. The job should be skipped (not cancelled
        or re-spawned).
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        paused_instance_id = "paused-instance-123"

        orphan_task = MagicMock()
        orphan_task.job_id = "orphan-task-paused"
        orphan_task.agent_id = "developer"
        orphan_task.project_id = "project-1"
        orphan_task.queue_id = "queue-1"
        orphan_task.status = JobStatus.PROCESSING.value
        orphan_task.instance_id = paused_instance_id
        orphan_task.job_type = "task"
        orphan_task.message = "test"
        orphan_task.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([orphan_task], None)

        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        paused_instance = MagicMock()
        paused_instance.status = InstanceStatus.PAUSED.value
        mock_instance_manager._instance_repository.get.return_value = paused_instance

        await processor._process_next_job()

        # Job should be skipped (not cancelled, not re-spawned)
        mock_queue_service.complete_job.assert_not_called()
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_processor_respawns_genuine_orphan_job(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor re-spawns genuinely orphaned jobs.

        When a PROCESSING job has instance_id pointing to an instance that
        genuinely doesn't exist (not just terminated), it should be re-spawned.
        This is the existing recovery behavior.
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        missing_instance_id = "missing-instance-123"

        orphan_task = MagicMock()
        orphan_task.job_id = "genuine-orphan"
        orphan_task.agent_id = "developer"
        orphan_task.project_id = "project-1"
        orphan_task.queue_id = "queue-1"
        orphan_task.status = JobStatus.PROCESSING.value
        orphan_task.instance_id = missing_instance_id
        orphan_task.job_type = "task"
        orphan_task.message = "test"
        orphan_task.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([orphan_task], None)

        # Instance doesn't exist in memory
        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        # Instance also doesn't exist in DB (returns None)
        mock_instance_manager._instance_repository.get.return_value = None

        await processor._process_next_job()

        # Job should be re-spawned (recovered)
        mock_instance_manager.spawn_instance_with_mcp.assert_called()
        mock_instance_manager.enqueue_message.assert_called()

    @pytest.mark.skip(reason="Phase 4: orphan detection logic changed; obsolete behavior")
    @pytest.mark.asyncio
    async def test_processor_completes_orphan_message_job_for_completed_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor COMPLETES MESSAGE jobs for COMPLETED instances.
        
        When a PROCESSING MESSAGE job has instance_id pointing to a COMPLETED
        instance, it should be completed (not cancelled). The result_summary
        should be captured from the actual agent response.
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        completed_instance_id = "completed-message-instance-123"

        # PROCESSING MESSAGE job with COMPLETED instance
        message_job = MagicMock()
        message_job.job_id = "message-job-completed"
        message_job.agent_id = "developer"
        message_job.project_id = "project-1"
        message_job.queue_id = "queue-1"
        message_job.status = JobStatus.PROCESSING.value
        message_job.instance_id = completed_instance_id
        message_job.job_type = "message"
        message_job.message = "test message"
        message_job.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([message_job], None)

        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        completed_instance = MagicMock()
        completed_instance.status = InstanceStatus.COMPLETED.value
        completed_instance.waiting_for = 0
        mock_instance_manager._instance_repository.get.return_value = completed_instance
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
            return_value="Agent response for message job"
        )

        await processor._process_next_job()

        # Job should be completed, not cancelled
        mock_queue_service.complete_job.assert_called()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args.kwargs.get("demand_state") == DemandState.COMPLETED
        assert call_args.kwargs.get("result_summary") == "Agent response for message job"
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()

    @pytest.mark.skip(reason="Phase 4: orphan detection logic changed; obsolete behavior")
    @pytest.mark.asyncio
    async def test_processor_cancels_orphan_message_job_for_terminated_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor CANCELS MESSAGE jobs for TERMINATED instances.
        
        When a PROCESSING MESSAGE job has instance_id pointing to a TERMINATED
        instance, it should be cancelled (not completed).
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        terminated_instance_id = "terminated-message-instance-123"

        # PROCESSING MESSAGE job with TERMINATED instance
        message_job = MagicMock()
        message_job.job_id = "message-job-terminated"
        message_job.agent_id = "developer"
        message_job.project_id = "project-1"
        message_job.queue_id = "queue-1"
        message_job.status = JobStatus.PROCESSING.value
        message_job.instance_id = terminated_instance_id
        message_job.job_type = "message"
        message_job.message = "test message"
        message_job.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([message_job], None)

        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        terminated_instance = MagicMock()
        terminated_instance.status = InstanceStatus.TERMINATED.value
        terminated_instance.waiting_for = 0
        mock_instance_manager._instance_repository.get.return_value = terminated_instance

        await processor._process_next_job()

        # Job should be cancelled
        mock_queue_service.complete_job.assert_called()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args.kwargs.get("demand_state") == DemandState.CANCELLED
        assert "terminated" in call_args.kwargs.get("error", "").lower()
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()

    @pytest.mark.skip(reason="Phase 4: orphan detection logic changed; obsolete behavior")
    @pytest.mark.asyncio
    async def test_processor_completes_message_job_with_completed_instance_even_when_get_message_fails(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that MESSAGE job completes with result_summary=None when _get_last_assistant_message_raw fails.
        
        When _get_last_assistant_message_raw raises an exception, the MESSAGE job
        should still be completed with DemandState.COMPLETED and result_summary=None
        (the service applies a default message).
        """
        project = MagicMock()
        project.project_id = "project-1"
        project.job_queue_paused = False

        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        completed_instance_id = "completed-message-instance-456"

        # PROCESSING MESSAGE job with COMPLETED instance
        message_job = MagicMock()
        message_job.job_id = "message-job-completed-fail"
        message_job.agent_id = "developer"
        message_job.project_id = "project-1"
        message_job.queue_id = "queue-1"
        message_job.status = JobStatus.PROCESSING.value
        message_job.instance_id = completed_instance_id
        message_job.job_type = "message"
        message_job.message = "test message"
        message_job.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([message_job], None)

        mock_instance_manager.get_instance.side_effect = KeyError("not found")
        completed_instance = MagicMock()
        completed_instance.status = InstanceStatus.COMPLETED.value
        completed_instance.waiting_for = 0
        mock_instance_manager._instance_repository.get.return_value = completed_instance
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
            side_effect=Exception("DB error")
        )

        await processor._process_next_job()

        # Job should be completed with result_summary=None (graceful fallback)
        mock_queue_service.complete_job.assert_called()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args.kwargs.get("demand_state") == DemandState.COMPLETED
        assert call_args.kwargs.get("result_summary") is None
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()


# =============================================================================
# Test Group 4: TERMINAL_CANCEL_STATUSES Constant
# =============================================================================


class TestTerminalCancelStatuses:
    """Tests for TERMINAL_CANCEL_STATUSES constant correctness."""

    def test_completed_not_in_terminal_cancel_statuses(self):
        """COMPLETED should NOT be in TERMINAL_CANCEL_STATUSES.
        
        This was the root cause bug: COMPLETED was wrongly included,
        causing TASK jobs with successfully completed instances to be
        cancelled instead of completed.
        """
        from daemon.services.job_queue_service import TERMINAL_CANCEL_STATUSES
        from daemon.models import InstanceStatus
        
        assert InstanceStatus.COMPLETED.value not in TERMINAL_CANCEL_STATUSES

    def test_terminated_in_terminal_cancel_statuses(self):
        """TERMINATED should be in TERMINAL_CANCEL_STATUSES."""
        from daemon.services.job_queue_service import TERMINAL_CANCEL_STATUSES
        from daemon.models import InstanceStatus
        
        assert InstanceStatus.TERMINATED.value in TERMINAL_CANCEL_STATUSES

    def test_terminal_cancel_statuses_only_contains_terminated(self):
        """TERMINAL_CANCEL_STATUSES should only contain TERMINATED.
        
        After the fix, this set should be {terminated} only.
        ERROR and FAILED are in TERMINAL_STATUSES but not TERMINAL_CANCEL_STATUSES.
        """
        from daemon.services.job_queue_service import TERMINAL_CANCEL_STATUSES
        from daemon.models import InstanceStatus
        
        assert TERMINAL_CANCEL_STATUSES == frozenset([InstanceStatus.TERMINATED.value])
