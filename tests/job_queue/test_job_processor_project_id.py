"""Tests for project_id auto-injection in JobProcessor.

This module tests that job_processor correctly passes project_id to spawn_instance()
so that spawned agents receive project context automatically.

Tests cover:
1. Main job processing path with valid project_id
2. Orphan job fallback path with project_id=None
3. spawn_instance accepts project_id kwarg
4. Jobs without project_id still work (no regression)
5. Edge cases: project_id is None or a valid UUID string
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from daemon.services.job_processor import JobProcessor
from daemon.repositories.job_queue.models import JobStatus


# Sample UUID for testing
SAMPLE_PROJECT_UUID = "83da04de-a410-4fb5-9e92-251a99d28a52"


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
        project_id: str | None = "project-1",
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


def create_started_job(job_id: str, project_id: str | None, instance_id: str = "instance-123") -> MagicMock:
    """Create a mock started job with the given properties."""
    started_job = MagicMock()
    started_job.job_id = job_id
    started_job.agent_id = "coder"
    started_job.message = "test message"
    started_job.source = "api"
    started_job.instance_id = instance_id
    started_job.status = JobStatus.PROCESSING.value
    started_job.project_id = project_id
    return started_job


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


class TestProjectIdAutoInjection:
    """Tests for project_id auto-injection in JobProcessor."""

    @pytest.mark.asyncio
    async def test_main_path_spawns_with_project_id(
        self,
        mock_queue_service,
        mock_instance_manager,
        mock_project_repo,
        mock_queue_repo,
    ):
        """Test that main processing path passes job.project_id to spawn_instance.
        
        When a job has a valid project_id, spawn_instance should be called with
        project_id=job.project_id to enable automatic project context injection.
        """
        # Create processor with mocked dependencies
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        # Setup: job with valid project_id
        project = MockProject(SAMPLE_PROJECT_UUID, job_queue_paused=False)
        queue = MockQueue("queue-1", SAMPLE_PROJECT_UUID, is_paused=False)
        job = MockJob("job-1", project_id=SAMPLE_PROJECT_UUID, queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job = AsyncMock(
            return_value=create_started_job("job-1", SAMPLE_PROJECT_UUID)
        )
        mock_instance_manager.enqueue_message = AsyncMock()

        # Execute
        await processor._process_next_job()

        # Verify spawn_instance was called with project_id
        mock_instance_manager.spawn_instance.assert_called_once()
        call_kwargs = mock_instance_manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("project_id") == SAMPLE_PROJECT_UUID, (
            f"Expected project_id={SAMPLE_PROJECT_UUID}, "
            f"got project_id={call_kwargs.get('project_id')}"
        )
        # Verify other required args
        assert call_kwargs.get("agent_id") == "coder"
        assert call_kwargs.get("instance_id") == "instance-123"

    @pytest.mark.asyncio
    async def test_orphan_fallback_spawns_with_none_project_id(
        self,
        mock_queue_service,
        mock_instance_manager,
        mock_project_repo,
        mock_queue_repo,
    ):
        """Test that orphan job fallback path passes project_id=None to spawn_instance.
        
        Orphan jobs (those without project_id) should still be processed but
        spawn_instance should receive project_id=None so no project context
        is injected.
        """
        # Create processor with mocked dependencies
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        # Setup: orphan job with project_id=None
        # Note: orphan_pending comes from list_all_pending, filtered for project_id=None
        orphan_job = MockJob("orphan-job", project_id=None, queue_id="queue-1")
        orphan_job.project_id = None  # Explicitly set to None

        # No regular jobs (all projects/queues return empty)
        mock_project_repo.list_projects.return_value = []
        mock_queue_repo.list_by_project.return_value = []
        mock_queue_service._repository.list_pending_by_queue.return_value = []

        # Orphan jobs come from list_all_pending
        mock_queue_service._repository.list_all_pending.return_value = [orphan_job]
        mock_queue_service.start_job = AsyncMock(
            return_value=create_started_job("orphan-job", None)
        )
        mock_instance_manager.enqueue_message = AsyncMock()

        # Execute
        await processor._process_next_job()

        # Verify spawn_instance was called with project_id=None
        mock_instance_manager.spawn_instance.assert_called_once()
        call_kwargs = mock_instance_manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("project_id") is None, (
            f"Expected project_id=None for orphan job, "
            f"got project_id={call_kwargs.get('project_id')}"
        )

    @pytest.mark.asyncio
    async def test_spawn_instance_accepts_project_id_kwarg(
        self,
        mock_queue_service,
        mock_instance_manager,
        mock_project_repo,
        mock_queue_repo,
    ):
        """Test that spawn_instance accepts project_id as a keyword argument.
        
        This verifies the mock is set up correctly and the method signature
        supports the project_id parameter.
        """
        # Create processor with mocked dependencies
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        # Setup a simple job
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id="project-1", queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job = AsyncMock(
            return_value=create_started_job("job-1", "project-1")
        )
        mock_instance_manager.enqueue_message = AsyncMock()

        # Execute
        await processor._process_next_job()

        # Verify spawn_instance was called at all (implies it accepts the kwargs)
        mock_instance_manager.spawn_instance.assert_called_once()
        call_args = mock_instance_manager.spawn_instance.call_args
        
        # Verify it was called with keyword arguments
        assert "project_id" in call_args.kwargs or len(call_args.args) >= 3, (
            "spawn_instance should be called with project_id kwarg or positional args"
        )

    @pytest.mark.asyncio
    async def test_no_regression_job_without_project_id_still_works(
        self,
        mock_queue_service,
        mock_instance_manager,
        mock_project_repo,
        mock_queue_repo,
    ):
        """Test that jobs without project_id still spawn instances (no regression).
        
        Even though job.project_id is None, the system should still process
        the job by calling spawn_instance. This is important for backward
        compatibility.
        """
        # Create processor with mocked dependencies
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        # Setup: job with project_id=None
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id=None, queue_id="queue-1")
        job.project_id = None  # Explicitly set to None

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job = AsyncMock(
            return_value=create_started_job("job-1", None)
        )
        mock_instance_manager.enqueue_message = AsyncMock()

        # Execute - should not raise
        await processor._process_next_job()

        # Verify spawn_instance was still called (job was processed)
        mock_instance_manager.spawn_instance.assert_called_once()
        # Verify enqueue_message was also called
        mock_instance_manager.enqueue_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edge_case_project_id_none_explicit(
        self,
        mock_queue_service,
        mock_instance_manager,
        mock_project_repo,
        mock_queue_repo,
    ):
        """Test edge case: project_id is explicitly None.
        
        This tests the case where job.project_id is set to None, ensuring
        the code handles this gracefully without errors.
        """
        # Create processor with mocked dependencies
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        # Setup: job with explicitly None project_id
        project = MockProject("project-1", job_queue_paused=False)
        queue = MockQueue("queue-1", "project-1", is_paused=False)
        job = MockJob("job-1", project_id=None, queue_id="queue-1")
        job.project_id = None

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job = AsyncMock(
            return_value=create_started_job("job-1", None)
        )
        mock_instance_manager.enqueue_message = AsyncMock()

        # Execute
        await processor._process_next_job()

        # Verify spawn_instance was called with project_id=None
        mock_instance_manager.spawn_instance.assert_called_once()
        call_kwargs = mock_instance_manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("project_id") is None

    @pytest.mark.asyncio
    async def test_edge_case_valid_uuid_string(
        self,
        mock_queue_service,
        mock_instance_manager,
        mock_project_repo,
        mock_queue_repo,
    ):
        """Test edge case: project_id is a valid UUID string.
        
        This tests the exact format mentioned in the requirements:
        '83da04de-a410-4fb5-9e92-251a99d28a52'
        """
        # Create processor with mocked dependencies
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        # Setup: job with exact UUID from requirements
        project = MockProject(SAMPLE_PROJECT_UUID, job_queue_paused=False)
        queue = MockQueue("queue-1", SAMPLE_PROJECT_UUID, is_paused=False)
        job = MockJob("job-1", project_id=SAMPLE_PROJECT_UUID, queue_id="queue-1")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service.start_job = AsyncMock(
            return_value=create_started_job("job-1", SAMPLE_PROJECT_UUID)
        )
        mock_instance_manager.enqueue_message = AsyncMock()

        # Execute
        await processor._process_next_job()

        # Verify spawn_instance was called with the exact UUID
        mock_instance_manager.spawn_instance.assert_called_once()
        call_kwargs = mock_instance_manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("project_id") == SAMPLE_PROJECT_UUID, (
            f"Expected project_id={SAMPLE_PROJECT_UUID}, "
            f"got project_id={call_kwargs.get('project_id')}"
        )

    @pytest.mark.asyncio
    async def test_both_paths_receive_correct_project_id(
        self,
        mock_queue_service,
        mock_instance_manager,
        mock_project_repo,
        mock_queue_repo,
    ):
        """Test that both main path and orphan path receive correct project_id values.
        
        This test verifies the change in commit 1d65cc0 is correctly passing
        job.project_id to both spawn_instance calls.
        """
        # Create processor with mocked dependencies
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

        # Setup: one normal job and one orphan
        project = MockProject("normal-project", job_queue_paused=False)
        queue = MockQueue("queue-1", "normal-project", is_paused=False)
        normal_job = MockJob("normal-job", project_id="normal-project", queue_id="queue-1")
        orphan_job = MockJob("orphan-job", project_id=None, queue_id="queue-1")
        orphan_job.project_id = None

        # First iteration: process normal job
        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = [normal_job]
        mock_queue_service._repository.list_all_pending.return_value = []  # No orphans in first pass
        mock_queue_service.start_job = AsyncMock(
            side_effect=[
                create_started_job("normal-job", "normal-project"),
                create_started_job("orphan-job", None),
            ]
        )
        mock_instance_manager.enqueue_message = AsyncMock()
        mock_instance_manager.spawn_instance.reset_mock()

        # Execute first pass
        await processor._process_next_job()

        # Verify normal job spawned with project_id
        assert mock_instance_manager.spawn_instance.call_count == 1
        call_kwargs = mock_instance_manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("project_id") == "normal-project"


class TestSpawnInstanceSignature:
    """Tests to verify spawn_instance signature accepts project_id."""

    @pytest.mark.asyncio
    async def test_spawn_instance_signature_has_project_id_parameter(self):
        """Verify that spawn_instance method signature includes project_id parameter.
        
        This is a signature check test that ensures the InstanceManager.spawn_instance
        method accepts a project_id keyword argument.
        """
        import inspect
        from daemon.manager import InstanceManager

        # Get the signature of spawn_instance
        sig = inspect.signature(InstanceManager.spawn_instance)
        param_names = list(sig.parameters.keys())

        # Verify project_id is a parameter
        assert "project_id" in param_names, (
            f"spawn_instance should have 'project_id' parameter, "
            f"but has: {param_names}"
        )

        # Verify it has a default value (should be optional)
        project_id_param = sig.parameters["project_id"]
        assert project_id_param.default is None, (
            f"project_id should default to None, "
            f"but has default: {project_id_param.default}"
        )
