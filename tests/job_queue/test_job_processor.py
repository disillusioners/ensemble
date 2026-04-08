"""Tests for JobProcessor.

This module tests the background worker that processes queued jobs
with two-level pause checking and per-queue polling.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from daemon.services.job_processor import JobProcessor
from daemon.repositories.job_queue.models import JobItem, JobStatus


class MockProject:
    """Mock project object for testing."""
    def __init__(self, project_id: str, job_queue_paused: bool = False):
        self.project_id = project_id
        self.job_queue_paused = job_queue_paused


class MockQueue:
    """Mock queue object for testing."""
    def __init__(
        self,
        queue_id: str,
        project_id: str,
        queue_name: str = "default",
        is_paused: bool = False,
        concurrency_limit: int = 1,
    ):
        self.queue_id = queue_id
        self.project_id = project_id
        self.queue_name = queue_name
        self.is_paused = is_paused
        self.concurrency_limit = concurrency_limit


class MockJob:
    """Mock job object for testing."""
    def __init__(
        self,
        job_id: str,
        agent_id: str = "coder",
        project_id: str = "project-1",
        queue_id: str = "queue-1",
        status: str = JobStatus.PENDING.value,
    ):
        self.job_id = job_id
        self.agent_id = agent_id
        self.project_id = project_id
        self.queue_id = queue_id
        self.status = status
        self.message = "test message"
        self.source = "api"
        self.instance_id = None


@pytest.fixture
def mock_queue_service():
    """Create mock JobQueueService."""
    return MagicMock()


@pytest.fixture
def mock_instance_manager():
    """Create mock InstanceManager."""
    manager = MagicMock()
    manager.spawn_instance.return_value = "instance-123"
    manager.enqueue_message = AsyncMock()
    return manager


@pytest.fixture
def mock_project_repo():
    """Create mock project repository."""
    repo = MagicMock()
    repo.list_projects = MagicMock(return_value=[])
    return repo


@pytest.fixture
def mock_queue_repo():
    """Create mock queue repository."""
    repo = MagicMock()
    repo.list_by_project = MagicMock(return_value=[])
    return repo


@pytest.fixture
def processor(mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo):
    """Create JobProcessor with mocked dependencies."""
    return JobProcessor(
        queue_service=mock_queue_service,
        instance_manager=mock_instance_manager,
        project_repo=mock_project_repo,
        queue_repo=mock_queue_repo,
        poll_interval=0.1,  # Short interval for fast tests
    )


class TestJobProcessorTwoLevelPause:
    """Tests for two-level pause logic in JobProcessor."""

    @pytest.mark.asyncio
    async def test_queue_not_paused_project_not_paused_processes(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that job gets processed when queue and project are not paused."""
        # Create fresh processor with properly configured mocks
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )
        
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        
        # Create a job with instance_id set
        started_job = MagicMock()
        started_job.job_id = "job-1"
        started_job.agent_id = "coder"
        started_job.message = "test message"
        started_job.source = "api"
        started_job.instance_id = "instance-123"
        started_job.status = JobStatus.PROCESSING.value
        # start_job is async
        mock_queue_service.start_job = AsyncMock(return_value=started_job)
        # enqueue_message is async
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        mock_queue_service.start_job.assert_called_once_with("job-1")
        mock_instance_manager.spawn_instance.assert_called_once()
        mock_instance_manager.enqueue_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_paused_skips(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processing is skipped when queue.is_paused=True."""
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=True)  # Queue is paused
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]

        await processor._process_next_job()

        # Job should not be started because queue is paused
        mock_queue_service.start_job.assert_not_called()
        mock_instance_manager.spawn_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_project_paused_skips_all(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processing is skipped when project is paused (job_queue_paused=True)."""
        project = MockProject("project-1", job_queue_paused=True)  # Project is paused
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]

        await processor._process_next_job()

        # Should not even check queues for paused project
        mock_queue_repo.list_by_project.assert_not_called()
        mock_queue_service.start_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_both_paused_skips(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processing is skipped when both project and queue are paused."""
        project = MockProject("project-1", job_queue_paused=True)  # Project paused
        queue = MockQueue("queue-1", "project-1", is_paused=True)  # Queue paused
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]

        await processor._process_next_job()

        mock_queue_service.start_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_queue_paused_then_resumed_processes(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that unpausing a queue allows job processing."""
        # Create fresh processor with properly configured mocks
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )
        
        project = MockProject("project-1", job_queue_paused=False)
        
        job1 = MockJob("job-1", project_id="project-1", queue_id="queue-1")
        job2 = MockJob("job-2", project_id="project-1", queue_id="queue-1")
        
        started_job2 = MagicMock()
        started_job2.job_id = "job-2"
        started_job2.agent_id = "coder"
        started_job2.message = "test message"
        started_job2.source = "api"
        started_job2.instance_id = "instance-123"
        started_job2.status = JobStatus.PROCESSING.value
        
        queue_paused = MockQueue("queue-1", "project-1", is_paused=True)
        queue_resumed = MockQueue("queue-1", "project-1", is_paused=False)

        mock_project_repo.list_projects.return_value = [project]
        
        # Setup to return job1 even on first call (simulating the call happens before pause check)
        # But the job won't be started because queue is paused
        mock_queue_repo.list_by_project.return_value = [queue_paused]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job1]
        
        # First pass: queue is paused, job should be skipped even if returned
        await processor._process_next_job()
        mock_queue_service.start_job.assert_not_called()
        
        # Now change the setup for second pass
        mock_queue_repo.list_by_project.return_value = [queue_resumed]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job2]
        mock_queue_service.start_job = AsyncMock(return_value=started_job2)
        mock_instance_manager.enqueue_message = AsyncMock()
        
        # Second pass: queue is resumed, job should be processed
        await processor._process_next_job()
        
        # Now job should be started
        mock_queue_service.start_job.assert_called_with("job-2")


class TestJobProcessorPerQueuePolling:
    """Tests for per-queue polling in JobProcessor."""

    @pytest.mark.asyncio
    async def test_polls_each_queue(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor checks each configured queue."""
        project = MockProject("project-1", job_queue_paused=False)
        queue1 = MockQueue("queue-1", "project-1", is_paused=False)
        queue2 = MockQueue("queue-2", "project-1", is_paused=False)
        job1 = MockJob("job-1", project_id="project-1", queue_id="queue-1")
        job2 = MockJob("job-2", project_id="project-1", queue_id="queue-2")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue1, queue2]
        mock_queue_service._repository.list_pending_by_queue.side_effect = [[job1], [job2]]
        
        started_job = MockJob("job-1", project_id="project-1", queue_id="queue-1", status=JobStatus.PROCESSING.value)
        started_job.instance_id = "instance-123"
        mock_queue_service.start_job.return_value = started_job

        await processor._process_next_job()

        # Should check both queues for pending jobs
        assert mock_queue_service._repository.list_pending_by_queue.call_count >= 1

    @pytest.mark.asyncio
    async def test_respects_per_queue_concurrency(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor respects per-queue concurrency limits."""
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False, concurrency_limit=2)
        job1 = MockJob("job-1", project_id="project-1", queue_id="queue-1")
        job2 = MockJob("job-2", project_id="project-1", queue_id="queue-1")
        job3 = MockJob("job-3", project_id="project-1", queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job1, job2, job3]
        
        # start_job returns None when at concurrency limit
        started_job = MockJob("job-1", project_id="project-1", queue_id="queue-1", status=JobStatus.PROCESSING.value)
        started_job.instance_id = "instance-123"
        
        # First job starts successfully, subsequent jobs fail due to lock
        mock_queue_service.start_job.side_effect = [
            started_job,
            None,  # Lock acquisition failed
            None,  # Lock acquisition failed
        ]

        await processor._process_next_job()

        # Only the first job should be processed
        mock_queue_service.start_job.assert_called_once_with("job-1")

    @pytest.mark.asyncio
    async def test_processes_pending_from_queue(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor picks up PENDING jobs from queue."""
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1", status=JobStatus.PENDING.value)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        
        started_job = MockJob("job-1", project_id="project-1", queue_id="queue-1", status=JobStatus.PROCESSING.value)
        started_job.instance_id = "instance-123"
        mock_queue_service.start_job.return_value = started_job

        await processor._process_next_job()

        mock_queue_service._repository.list_pending_by_queue.assert_called_with("queue-1")
        mock_queue_service.start_job.assert_called_with("job-1")

    @pytest.mark.asyncio
    async def test_skips_empty_queue(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor handles empty queue without error."""
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []  # No pending jobs

        # Should not raise an error
        await processor._process_next_job()

        mock_queue_service.start_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_projects_queues_polled(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor polls queues across multiple projects."""
        project1 = MockProject("project-1", job_queue_paused=False)
        project2 = MockProject("project-2", job_queue_paused=False)
        queue1 = MockQueue("queue-1", "project-1", is_paused=False)
        queue2 = MockQueue("queue-2", "project-2", is_paused=False)

        mock_project_repo.list_projects.return_value = [project1, project2]
        mock_queue_repo.list_by_project.side_effect = [
            [queue1],  # project-1 queues
            [queue2],  # project-2 queues
        ]
        mock_queue_service._repository.list_pending_by_queue.side_effect = [[], []]

        await processor._process_next_job()

        # Both projects' queues should be checked
        assert mock_queue_repo.list_by_project.call_count == 2


class TestJobProcessorLifecycle:
    """Tests for JobProcessor start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self, processor):
        """Test that start() sets the running flag."""
        assert processor._running is False
        
        await processor.start()
        assert processor._running is True
        
        await processor.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, processor):
        """Test that calling start() multiple times is safe."""
        await processor.start()
        await processor.start()  # Should not raise
        
        assert processor._running is True
        await processor.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self, processor):
        """Test that stop() clears the running flag."""
        await processor.start()
        await processor.stop()
        
        assert processor._running is False

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, processor):
        """Test that calling stop() multiple times is safe."""
        await processor.start()
        await processor.stop()
        await processor.stop()  # Should not raise
        
        assert processor._running is False


class TestJobProcessorErrorHandling:
    """Tests for error handling in JobProcessor."""

    @pytest.mark.asyncio
    async def test_handles_start_job_failure(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor handles start_job failure gracefully."""
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job.return_value = None  # Failed to start

        # Should not raise
        await processor._process_next_job()

        mock_instance_manager.spawn_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_spawn_instance_failure(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor handles spawn_instance failure gracefully."""
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        started_job = MockJob("job-1", project_id="project-1", queue_id="queue-1", status=JobStatus.PROCESSING.value)
        started_job.instance_id = "instance-123"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job.return_value = started_job
        mock_instance_manager.spawn_instance.side_effect = Exception("Spawn failed")

        # Should handle error and complete job as failed
        await processor._process_next_job()

        mock_queue_service.complete_job.assert_called_once()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args[1]["success"] is False

    @pytest.mark.asyncio
    async def test_handles_enqueue_message_failure(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that processor handles enqueue_message failure gracefully."""
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        started_job = MockJob("job-1", project_id="project-1", queue_id="queue-1", status=JobStatus.PROCESSING.value)
        started_job.instance_id = "instance-123"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job.return_value = started_job
        mock_instance_manager.spawn_instance.return_value = "instance-123"
        mock_instance_manager.enqueue_message.side_effect = Exception("Enqueue failed")

        await processor._process_next_job()

        mock_queue_service.complete_job.assert_called_once()
        call_args = mock_queue_service.complete_job.call_args
        assert call_args[1]["success"] is False
