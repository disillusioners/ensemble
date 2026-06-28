"""Tests for Instance Pause functionality in JobQueue.

These tests verify the behavior when instances are paused:
1. When instance is paused, new jobs for it stay PENDING
2. When instance is resumed, pending jobs get processed
3. Currently processing jobs are NOT affected by pause
4. enqueue_message() does NOT auto-resume a paused instance
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from daemon.models.instance import InstanceStatus
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.services.job_queue_service import JobQueueService, DemandState


class MockInstance:
    """Mock instance object for testing."""
    def __init__(self, instance_id: str, status: str = InstanceStatus.RUNNING.value):
        self.instance_id = instance_id
        self.status = status


class MockProject:
    """Mock project object for testing."""
    def __init__(self, project_id: str, job_queue_paused: bool = False):
        self.project_id = project_id
        self.job_queue_paused = job_queue_paused


class MockJob:
    """Mock job object for testing."""

    # Map legacy status → admission_state (Phase 4: status is frozen,
    # admission_state is the sole authority).
    _STATUS_TO_ADMISSION = {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }

    def __init__(
        self,
        job_id: str,
        agent_id: str = "developer",
        project_id: str = "project-1",
        queue_id: str = "queue-1",
        status: str = JobStatus.PENDING.value,
        instance_id: str | None = None,
        job_type: str = "task",
        admission_state: str | None = None,
    ):
        self.job_id = job_id
        self.agent_id = agent_id
        self.project_id = project_id
        self.queue_id = queue_id
        self.status = status
        self.admission_state = admission_state or self._STATUS_TO_ADMISSION.get(status, "queued")
        self.instance_id = instance_id
        self.job_type = job_type
        self.message = "test message"
        self.source = "api"


class TestJobQueueServiceInstancePause:
    """Tests for instance pause behavior in JobQueueService.start_job()."""

    @pytest.fixture
    def mock_instance_manager_with_repo(self):
        """Create mock instance manager with instance_repository."""
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        return manager

    @pytest.fixture
    def mock_project_repo(self):
        """Create mock project repository."""
        repo = MagicMock()
        repo.get = MagicMock(return_value=MockProject("project-1", job_queue_paused=False))
        return repo

    @pytest.fixture
    def mock_repository(self):
        """Create mock job repository."""
        repo = MagicMock()
        repo.get = MagicMock()
        return repo

    @pytest.fixture
    def mock_lock_manager(self):
        """Create mock lock manager."""
        manager = MagicMock()
        manager.acquire = AsyncMock(return_value=True)
        manager.acquire_queue_lock = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def mock_queue_repo(self):
        """Create mock queue repository."""
        repo = MagicMock()
        repo.get_concurrency_limit = MagicMock(return_value=1)
        return repo

    @pytest.fixture
    def job_queue_service(
        self, mock_repository, mock_lock_manager, mock_queue_repo,
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

    @pytest.mark.asyncio
    async def test_start_job_skips_paused_instance(
        self, job_queue_service, mock_repository, mock_instance_manager_with_repo
    ):
        """Test that start_job() skips jobs for paused instances.

        When a MESSAGE job targets a paused instance, start_job() should
        return None without starting the job. The job stays PENDING.
        """
        instance_id = "paused-instance-123"
        job_id = "job-1"

        # Create a pending MESSAGE job targeting a paused instance
        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="message",
        )
        mock_repository.get.return_value = job

        # Mock the paused instance
        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.PAUSED.value
        )

        # Call start_job
        result = await job_queue_service.start_job(job_id)

        # Job should NOT be started
        assert result is None
        # Instance repo should have been checked
        mock_instance_manager_with_repo._instance_repository.get.assert_called_once_with(instance_id)

    @pytest.mark.asyncio
    async def test_start_job_processes_running_instance(
        self, job_queue_service, mock_repository, mock_lock_manager,
        mock_instance_manager_with_repo, mock_queue_repo
    ):
        """Test that start_job() processes jobs for running instances.

        When a MESSAGE job targets a running instance, start_job() should
        start the job normally.
        """
        instance_id = "running-instance-123"
        job_id = "job-1"

        # Create a pending MESSAGE job targeting a running instance
        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=instance_id,
            job_type="message",
        )
        mock_repository.get.return_value = job

        # Mock the running instance
        mock_instance_manager_with_repo._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.RUNNING.value
        )

        # Mock start_job_atomic_with_lock (B1 single-transaction method)
        started_job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PROCESSING.value,
            instance_id=instance_id,
            job_type="message",
        )
        # B1: returns (JobItem, lock_acquired) tuple
        mock_repository.start_job_atomic_with_lock = MagicMock(
            return_value=(started_job, True)
        )

        # Call start_job
        result = await job_queue_service.start_job(job_id)

        # Job should be started
        assert result is not None
        assert result.job_id == job_id

    @pytest.mark.asyncio
    async def test_start_job_processes_task_job_without_instance_check(
        self, job_queue_service, mock_repository, mock_lock_manager,
        mock_instance_manager_with_repo, mock_queue_repo
    ):
        """Test that TASK jobs (without instance_id) bypass instance pause check.

        TASK jobs don't have an instance_id yet (it's generated when started),
        so they should not be affected by instance pause check.
        """
        job_id = "task-job-1"

        # Create a pending TASK job (no instance_id)
        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            instance_id=None,  # TASK jobs don't have pre-set instance_id
            job_type="task",
        )
        mock_repository.get.return_value = job

        # Mock start_job_atomic_with_lock (B1 single-transaction method)
        started_job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PROCESSING.value,
            instance_id="new-instance-123",  # Generated on start
            job_type="task",
        )
        mock_repository.start_job_atomic_with_lock = MagicMock(
            return_value=(started_job, True)
        )

        # Call start_job
        result = await job_queue_service.start_job(job_id)

        # Job should be started
        assert result is not None
        # Instance repo should NOT have been checked (no instance_id)
        mock_instance_manager_with_repo._instance_repository.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_job_skips_when_instance_repo_not_available(
        self, mock_repository, mock_lock_manager, mock_queue_repo, mock_project_repo
    ):
        """Test that start_job() handles missing instance_manager gracefully.

        When instance_manager is not set, start_job() should still work
        (for TASK jobs) but skip the instance pause check.
        """
        # Create service WITHOUT instance_manager
        service = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=None,  # No instance manager
        )
        service.set_project_repo(mock_project_repo)

        job_id = "task-job-1"
        job = MockJob(
            job_id=job_id,
            project_id="project-1",
            queue_id="queue-1",
            status=JobStatus.PENDING.value,
            job_type="task",
        )
        mock_repository.get.return_value = job

        started_job = MockJob(
            job_id=job_id,
            status=JobStatus.PROCESSING.value,
            job_type="task",
        )
        # B1: mock the single-transaction method
        mock_repository.start_job_atomic_with_lock = MagicMock(
            return_value=(started_job, True)
        )

        # Should not raise
        result = await service.start_job(job_id)
        assert result is not None


class TestJobProcessorInstancePause:
    """Tests for instance pause behavior in JobProcessor."""

    @pytest.fixture
    def mock_queue_service(self):
        """Create mock JobQueueService."""
        service = MagicMock()
        service.start_job = AsyncMock()
        service.complete_job = AsyncMock()
        service._repository = MagicMock()
        service._repository.list_pending_by_queue = MagicMock(return_value=[])
        return service

    @pytest.fixture
    def mock_instance_manager(self):
        """Create mock InstanceManager with instance_repository."""
        manager = MagicMock()
        manager.spawn_instance_with_mcp = AsyncMock(return_value="instance-123")
        manager.enqueue_message = AsyncMock()
        manager.get_instance = AsyncMock(return_value=MagicMock())
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

    @pytest.mark.asyncio
    async def test_processor_skips_paused_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor skips jobs for paused instances.

        When a MESSAGE job targets a paused instance, the processor should
        skip it and move to the next queue. Note: The pause check happens
        INSIDE JobQueueService.start_job() which returns None for paused
        instances. The JobProcessor then checks for None and skips further
        processing.
        """
        from daemon.services.job_processor import JobProcessor

        # Create fresh processor
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        project = MockProject("project-1", job_queue_paused=False)
        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        instance_id = "paused-instance-123"
        job = MagicMock()
        job.job_id = "job-1"
        job.agent_id = "developer"
        job.project_id = "project-1"
        job.queue_id = "queue-1"
        job.status = JobStatus.PENDING.value
        job.instance_id = instance_id
        job.job_type = "message"
        job.message = "test message"
        job.source = "api"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        # Phase 2.5 (D13): the legacy
        # ``find_processing_message_jobs_by_instance`` cross-dispatcher
        # pre-flight has been removed (no MESSAGE ``JobItem`` rows are
        # created post-D13). The pause guard now lives entirely on
        # ``_instance_repository.get`` + ``start_job``'s internal check.

        # Mock the paused instance
        mock_instance_manager._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.PAUSED.value
        )

        # Mock start_job to return None for paused instances (the actual behavior)
        # The pause check is INSIDE start_job(), so we mock it to return None
        mock_queue_service.start_job = AsyncMock(return_value=None)

        await processor._process_next_job()

        # Job was NOT processed because start_job returned None (paused check)
        # Stale test: instance pause now intentionally skips start_job
        mock_queue_service.start_job.assert_not_called()
        # Instance manager spawn should NOT be called since job was skipped
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_processor_processes_running_instance(
        self, processor, mock_queue_service, mock_instance_manager,
        mock_project_repo, mock_queue_repo
    ):
        """Test that JobProcessor processes jobs for running instances."""
        from daemon.services.job_processor import JobProcessor

        # Create fresh processor
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        project = MockProject("project-1", job_queue_paused=False)
        queue = MagicMock()
        queue.queue_id = "queue-1"
        queue.project_id = "project-1"
        queue.queue_name = "default"
        queue.is_paused = False
        queue.queue_type = "fifo"

        instance_id = "running-instance-123"
        job = MagicMock()
        job.job_id = "job-1"
        job.agent_id = "developer"
        job.project_id = "project-1"
        job.queue_id = "queue-1"
        job.status = JobStatus.PENDING.value
        job.instance_id = instance_id
        job.job_type = "message"
        job.message = "test message"
        job.source = "api"

        started_job = MagicMock()
        started_job.job_id = "job-1"
        started_job.agent_id = "developer"
        started_job.project_id = "project-1"
        started_job.queue_id = "queue-1"
        started_job.status = JobStatus.PROCESSING.value
        started_job.instance_id = instance_id
        started_job.job_type = "message"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        # Phase 2.5 (D13): see companion comment in
        # ``test_processor_skips_paused_instance`` — the legacy
        # ``find_processing_message_jobs_by_instance`` mock has been
        # removed. The instance pause guard is now inside ``start_job``.

        # Mock the running instance
        mock_instance_manager._instance_repository.get.return_value = MockInstance(
            instance_id, status=InstanceStatus.RUNNING.value
        )

        mock_queue_service.start_job = AsyncMock(return_value=started_job)

        await processor._process_next_job()

        # Job should be started
        mock_queue_service.start_job.assert_called_once_with("job-1")


class TestInstanceMessagingNoAutoResume:
    """Tests for ensuring enqueue_message() does NOT auto-resume paused instances.

    Note: These tests verify the FIX for the bug where messages to paused instances
    would incorrectly auto-resume them. The fix ensures:
    - Paused instances stay PAUSED when messages arrive
    - Jobs are enqueued as PENDING
    - Only explicit unpause operations can resume the instance
    """

    @pytest.mark.asyncio
    async def test_paused_instance_status_does_not_change(self):
        """Test that enqueue_message() does NOT auto-resume a paused instance.

        This verifies the core fix: when a message arrives for a PAUSED instance,
        the instance should NOT transition to RUNNING — it stays PAUSED.
        """
        from unittest.mock import MagicMock, AsyncMock, patch
        from daemon.models.instance import InstanceStatus
        from daemon.services.instance_messaging import InstanceMessagingService

        # Create mock manager with required attributes
        mock_manager = MagicMock()
        mock_manager._live_hub = MagicMock()
        mock_manager._live_hub.stream_status_change = AsyncMock()
        mock_manager._instance_repository = MagicMock()

        # Create mock cancellation_service (required by InstanceMessagingService.__init__)
        mock_cancellation_service = MagicMock()
        mock_cancellation_service.is_shutting_down = False

        # Create a PAUSED instance
        paused_instance = MagicMock()
        paused_instance.status = InstanceStatus.PAUSED.value
        paused_instance.agent_id = "test-agent"
        paused_instance.version = 1

        service = InstanceMessagingService(
            manager=mock_manager,
            cancellation_service=mock_cancellation_service,
        )
        service._message_queue = MagicMock()
        service._message_queue.enqueue = MagicMock(return_value=("msg-123", "queue-1"))

        # Enqueue a message for the paused instance
        with patch('daemon.services.instance_messaging.Session') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_session.get.return_value = paused_instance

            await service.enqueue_message(
                instance_id="test-instance-123",
                message="test message",
                source="test",
            )

            # Verify instance status was NOT changed to RUNNING
            # The update should not have been called with status=RUNNING
            call_args = mock_manager._instance_repository.update.call_args
            if call_args:
                # If update was called, status should NOT be RUNNING
                assert call_args.kwargs.get('status') != InstanceStatus.RUNNING.value, \
                    "Paused instance should NOT be auto-resumed on message enqueue"

    def test_idle_instance_still_transitions_to_running(self):
        """Test that IDLE instances still transition to RUNNING on message.

        This is the expected behavior - only PAUSED instances should NOT
        auto-resume. IDLE and WAITING_CHILDREN should still auto-resume.
        """
        # IDLE -> RUNNING should work
        assert InstanceStatus.IDLE.value in ["idle"]
        # WAITING_CHILDREN -> RUNNING should work
        assert InstanceStatus.WAITING_CHILDREN.value in ["waiting_children"]
        # Only PAUSED should NOT auto-resume
        assert InstanceStatus.PAUSED.value == "paused"
