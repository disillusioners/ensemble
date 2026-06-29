"""Tests for defer queue type.

This module tests the defer queue behavior:
- count_active_jobs_by_project repository method
- Defer queue idle check in JobProcessor

Defer queue type:
- Only dequeues jobs when the ENTIRE project is idle
- "Idle" means zero jobs in PENDING or PROCESSING status in ANY OTHER queue in the project
- The defer queue's own pending jobs don't count as "active" for this check
- Defer queues always have concurrency_limit=1
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.services.job_processor import JobProcessor
from daemon.repositories.job_queue.models import JobItem, AdmissionState, QueueType


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
        queue_type: str = "fifo",
    ):
        self.queue_id = queue_id
        self.project_id = project_id
        self.queue_name = queue_name
        self.is_paused = is_paused
        self.concurrency_limit = concurrency_limit
        self.queue_type = queue_type


class MockJob:
    """Mock job object for testing."""
    def __init__(
        self,
        job_id: str,
        agent_id: str = "developer",
        project_id: str = "project-1",
        queue_id: str = "queue-1",
        status: str = AdmissionState.QUEUED.value,
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
    manager.spawn_instance_with_mcp = AsyncMock(return_value="instance-123")
    manager.enqueue_message = AsyncMock()
    manager.get_instance = AsyncMock(return_value=MagicMock())
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
    """Create JobProcessor with mocked dependencies (shared by multiple test classes)."""
    mock_queue_service.complete_job = AsyncMock()
    mock_queue_service.start_job = AsyncMock()
    return JobProcessor(
        queue_service=mock_queue_service,
        instance_manager=mock_instance_manager,
        project_repo=mock_project_repo,
        queue_repo=mock_queue_repo,
        poll_interval=0.1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Repository Tests for count_active_jobs_by_project
# ─────────────────────────────────────────────────────────────────────────────

class TestCountActiveJobsByProject:
    """Tests for count_active_jobs_by_project repository method.

    This method counts all PENDING and PROCESSING jobs for a project,
    excluding deleted jobs. It's used by the defer queue idle check to
    determine if a project is "idle".
    """

    def test_counts_pending_jobs(self, repository):
        """Test that PENDING jobs are counted."""
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        assert repository.count_active_jobs_by_project("test-project") == 1

    def test_counts_processing_jobs(self, repository):
        """Test that PROCESSING jobs are counted."""
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        repository.start_job_atomic(job.job_id, "test-instance")
        assert repository.count_active_jobs_by_project("test-project") == 1

    def test_does_not_count_completed_jobs(self, repository):
        """Test that COMPLETED jobs are not counted."""
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        repository.start_job_atomic(job.job_id, "test-instance")
        repository.complete_job(job.job_id)
        assert repository.count_active_jobs_by_project("test-project") == 0

    def test_does_not_count_failed_jobs(self, repository):
        """Test that FAILED jobs are not counted."""
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        repository.start_job_atomic(job.job_id, "test-instance")
        repository.fail_job(job.job_id, "test error")
        assert repository.count_active_jobs_by_project("test-project") == 0

    def test_does_not_count_cancelled_jobs(self, repository):
        """Test that CANCELLED jobs are not counted."""
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        repository.cancel_job(job.job_id)
        assert repository.count_active_jobs_by_project("test-project") == 0

    def test_counts_only_specific_project(self, repository):
        """Test that only jobs for the specified project are counted."""
        repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="project-1",
        )
        repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="project-2",
        )
        assert repository.count_active_jobs_by_project("project-1") == 1
        assert repository.count_active_jobs_by_project("project-2") == 1
        assert repository.count_active_jobs_by_project("project-3") == 0

    def test_counts_pending_and_processing_combined(self, repository):
        """Test that both PENDING and PROCESSING jobs are counted."""
        job1 = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        job2 = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        repository.start_job_atomic(job2.job_id, "test-instance")
        assert repository.count_active_jobs_by_project("test-project") == 2

    def test_counts_multiple_active_jobs(self, repository):
        """Test counting multiple active jobs in various states."""
        # Create 3 pending jobs
        job1 = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        job2 = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        job3 = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        # Set 2 to PROCESSING
        repository.start_job_atomic(job1.job_id, "test-instance")
        repository.start_job_atomic(job2.job_id, "test-instance")
        # Leave job3 as PENDING
        assert repository.count_active_jobs_by_project("test-project") == 3

    def test_does_not_count_soft_deleted_jobs(self, repository):
        """Test that soft-deleted jobs are not counted."""
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
        )
        # Soft delete the job
        repository.soft_delete(job.job_id)
        assert repository.count_active_jobs_by_project("test-project") == 0

    def test_returns_zero_for_nonexistent_project(self, repository):
        """Test that nonexistent project returns 0."""
        assert repository.count_active_jobs_by_project("nonexistent-project") == 0

    def test_counts_jobs_across_different_queues(self, repository):
        """Test counting jobs that belong to different queues in the same project."""
        from daemon.repositories.job_queue.queue_repository import JobQueueRepository
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        # Create a queue repository with a queue
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        queue_repo = JobQueueRepository(engine)
        queue = queue_repo.create(
            project_id="test-project",
            queue_name="test-queue",
            queue_type="fifo",
            concurrency_limit=1,
        )

        # Create jobs in different queues
        job1 = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
            queue_id=queue.queue_id,
        )
        job2 = repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test",
            message="test",
            source="api",
            project_id="test-project",
            queue_id="another-queue-id",  # Different queue
        )
        repository.start_job_atomic(job2.job_id, "test-instance")

        # Should count both jobs
        assert repository.count_active_jobs_by_project("test-project") == 2


# ─────────────────────────────────────────────────────────────────────────────
# Processor Tests for Defer Queue Idle Check
# ─────────────────────────────────────────────────────────────────────────────

class TestDeferQueueIdleCheck:
    """Tests for defer queue idle check in JobProcessor.

    The defer queue idle check ensures that defer queues only process jobs
    when the ENTIRE project is idle (no active jobs in any other queue).
    """

    @pytest.mark.asyncio
    async def test_defer_queue_skips_when_other_queue_has_processing_job(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue does NOT dequeue when project has PROCESSING jobs in other queues.

        When a FIFO queue has a job in PROCESSING state, the defer queue should
        wait until that job completes before processing its own jobs.

        The idle check: total_active (all jobs in project) > len(pending of defer).
        Here: total_active=2 (1 fifo processing + 1 defer pending) > len(defer_pending)=1
        So the defer queue is skipped.
        """
        project = MockProject("project-1", job_queue_paused=False)
        fifo_queue = MockQueue("fifo-queue", "project-1", is_paused=False, queue_type="fifo")
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer")

        # FIFO queue has a PROCESSING job
        fifo_processing_job = MockJob("job-processing", project_id="project-1", queue_id="fifo-queue", status=AdmissionState.ACTIVE.value)
        # Defer queue has a PENDING job
        defer_pending_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [fifo_queue, defer_queue]

        # FIFO queue has no PENDING (but has PROCESSING via list_by_queue)
        # Defer queue has PENDING job
        # Note: defer queue calls list_pending_by_queue twice:
        # 1. First call at line 174 for idle check
        # 2. Second call at line 186 for getting jobs (if not skipped)
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [],  # fifo-queue has no PENDING
            [defer_pending_job],  # defer-queue idle check (first call)
            [defer_pending_job],  # defer-queue getting jobs (second call, if not skipped)
        ]
        # FIFO queue's PROCESSING job is found via list_by_queue
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([fifo_processing_job], 1),  # fifo-queue has PROCESSING job
            ([], 0),  # defer-queue has no PROCESSING jobs
        ]

        # count_active_jobs_in_non_defer_queues returns 1 (fifo processing job only)
        # Since 1 > 0, defer queue is skipped
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 1

        await processor._process_next_job()

        # FIFO has no pending jobs, so nothing should be started
        mock_queue_service.start_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_defer_queue_skips_when_other_queue_has_pending_job(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue does NOT dequeue when project has PENDING jobs in other queues.

        When another queue has PENDING jobs, the defer queue should wait until
        all other queues are idle (no PENDING or PROCESSING jobs).

        The idle check: total_active (all jobs in project) > len(pending of defer).
        Here: total_active=2 (1 fifo pending + 1 defer pending) > len(defer_pending)=1
        So the defer queue is skipped, allowing FIFO to be processed first.
        """
        project = MockProject("project-1", job_queue_paused=False)
        fifo_queue = MockQueue("fifo-queue", "project-1", is_paused=False, queue_type="fifo")
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer")

        # FIFO queue has a PENDING job
        fifo_pending_job = MockJob("job-fifo", project_id="project-1", queue_id="fifo-queue", status=AdmissionState.QUEUED.value)
        # Defer queue has a PENDING job
        defer_pending_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [fifo_queue, defer_queue]

        # FIFO queue has PENDING, defer queue has PENDING
        # Note: defer queue calls list_pending_by_queue twice
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [fifo_pending_job],  # fifo-queue has PENDING job
            [defer_pending_job],  # defer-queue idle check (first call)
            [defer_pending_job],  # defer-queue getting jobs (second call, if not skipped)
        ]
        # No PROCESSING jobs in either queue
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # fifo-queue has no PROCESSING jobs
            ([], 0),  # defer-queue has no PROCESSING jobs
        ]

        # count_active_jobs_in_non_defer_queues returns 1 (fifo pending job only)
        # Since 1 > 0, defer queue is skipped
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 1

        await processor._process_next_job()

        # FIFO job should be started (it's the first queue and has pending jobs)
        # But defer job should NOT be started (defer queue is skipped)
        mock_queue_service.start_job.assert_called_with("job-fifo")
        # Verify defer job was NOT started
        for call in mock_queue_service.start_job.call_args_list:
            assert call[0][0] != "job-defer", "Defer queue job should not be started when other queues have pending jobs"

    @pytest.mark.asyncio
    async def test_defer_queue_dequeues_when_project_idle(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue dequeues when project has no active jobs in other queues.

        When the project is idle (no jobs in FIFO or PARALLEL queues), the defer queue
        should process its pending jobs.
        """
        project = MockProject("project-1", job_queue_paused=False)
        fifo_queue = MockQueue("fifo-queue", "project-1", is_paused=False, queue_type="fifo")
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer")

        # FIFO queue has NO jobs
        # Defer queue has a PENDING job
        defer_pending_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        started_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.ACTIVE.value)
        started_job.instance_id = "instance-123"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [fifo_queue, defer_queue]

        # FIFO has no pending, defer has pending
        # Note: defer queue calls list_pending_by_queue twice
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [],  # fifo-queue has NO jobs
            [defer_pending_job],  # defer-queue idle check (first call)
            [defer_pending_job],  # defer-queue getting jobs (second call)
        ]
        # No PROCESSING jobs
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # fifo-queue has no PROCESSING jobs
            ([], 0),  # defer-queue has no PROCESSING jobs
        ]

        # Project is idle: count_active_jobs_in_non_defer_queues returns 0
        # Since 0 > 0 is False, defer queue is NOT skipped
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # Defer queue job SHOULD be started
        mock_queue_service.start_job.assert_called_with("job-defer")

    @pytest.mark.asyncio
    async def test_defer_queue_respects_concurrency_limit_1(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue with multiple pending jobs only processes 1 at a time.

        Defer queues have concurrency_limit=1 enforced by the model, so even
        with multiple pending jobs, only one should be processed at a time.
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer", concurrency_limit=1)

        # Project is idle (no other queues)
        # Defer queue has 2 pending jobs
        defer_job1 = MockJob("job-defer-1", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)
        defer_job2 = MockJob("job-defer-2", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        started_job = MockJob("job-defer-1", project_id="project-1", queue_id="defer-queue", status=AdmissionState.ACTIVE.value)
        started_job.instance_id = "instance-123"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue]

        # Defer queue has 2 pending jobs
        mock_queue_service._repository.list_pending_by_queue.return_value = [defer_job1, defer_job2]
        # No PROCESSING jobs
        mock_queue_service._repository.list_by_queue.return_value = ([], 0)

        # Project is idle (count_active_jobs_in_non_defer_queues returns 0)
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # Only 1 job should be started (concurrency_limit=1)
        mock_queue_service.start_job.assert_called_once_with("job-defer-1")

    @pytest.mark.asyncio
    async def test_defer_queue_own_processing_job_does_not_count(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue's own PENDING jobs don't count as 'active' for idle check.

        The idle check compares total_active (all queues) against len(pending)
        (the defer queue's own pending). If they're equal, it means only the
        defer queue has jobs, so it should proceed.
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer")

        # Defer queue has 1 PENDING job (and nothing else)
        defer_pending_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        started_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.ACTIVE.value)
        started_job.instance_id = "instance-123"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue]

        # Defer queue has 1 pending job
        mock_queue_service._repository.list_pending_by_queue.return_value = [defer_pending_job]
        # No PROCESSING jobs
        mock_queue_service._repository.list_by_queue.return_value = ([], 0)

        # count_active_jobs_in_non_defer_queues returns 0 (defer queue's own job doesn't count)
        # Since 0 > 0 is False, defer queue is NOT skipped
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # Defer queue job SHOULD be started
        mock_queue_service.start_job.assert_called_with("job-defer")

    @pytest.mark.asyncio
    async def test_defer_queue_only_dequeues_when_explicitly_idle(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue checks count_active_jobs_by_project > len(pending) logic.

        The idle check: if total_active > len(pending), skip.
        This means:
        - If other queues have jobs: total_active > len(defer_pending), skip
        - If only defer queue has jobs: total_active == len(defer_pending), proceed
        """
        project = MockProject("project-1", job_queue_paused=False)
        fifo_queue = MockQueue("fifo-queue", "project-1", is_paused=False, queue_type="fifo")
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer")

        # Defer queue has 1 PENDING job
        defer_pending_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [fifo_queue, defer_queue]

        # FIFO has no PENDING, defer has 1 PENDING
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [],  # fifo-queue has no PENDING
            [defer_pending_job],  # defer-queue has 1 PENDING
        ]
        # FIFO has 1 PROCESSING job
        fifo_processing = MockJob("job-fifo-processing", project_id="project-1", queue_id="fifo-queue", status=AdmissionState.ACTIVE.value)
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([fifo_processing], 1),  # fifo-queue has PROCESSING
            ([], 0),  # defer-queue has no PROCESSING
        ]

        # count_active_jobs_in_non_defer_queues returns 1 (fifo processing job only)
        # Since 1 > 0, defer queue should SKIP
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 1

        await processor._process_next_job()

        # Defer queue job should NOT be started
        mock_queue_service.start_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_defer_queue_with_empty_pending_processes(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue with no pending jobs is skipped (no check needed).

        If a defer queue has no pending jobs, the idle check is skipped
        because there are no jobs to process anyway.
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer")

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue]

        # Defer queue has no pending jobs
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        # No PROCESSING jobs
        mock_queue_service._repository.list_by_queue.return_value = ([], 0)

        await processor._process_next_job()

        # No jobs should be started (there are none to start)
        mock_queue_service.start_job.assert_not_called()
        # count_active_jobs_in_non_defer_queues should NOT be called (skipped when no pending)
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.assert_not_called()

    @pytest.mark.asyncio
    async def test_defer_queue_paused_respects_pause(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queue respects is_paused flag (Level 2 pause check).

        Even if project is idle, a paused defer queue should not process jobs.
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue = MockQueue("defer-queue", "project-1", is_paused=True, queue_type="defer")  # PAUSED

        defer_pending_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue]

        # Defer queue has pending job but is paused
        mock_queue_service._repository.list_pending_by_queue.return_value = [defer_pending_job]
        mock_queue_service._repository.list_by_queue.return_value = ([], 0)

        await processor._process_next_job()

        # Job should NOT be started (queue is paused)
        mock_queue_service.start_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_defer_queues_no_deadlock(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Two defer queues with pending jobs and no non-defer queues should both be able to process.

        This verifies that the deadlock between two defer queues is resolved.
        When there are no non-defer queues (or they have no active jobs),
        count_active_jobs_in_non_defer_queues returns 0, allowing defer queues to process.
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue1 = MockQueue("defer-queue-1", "project-1", is_paused=False, queue_type="defer")
        defer_queue2 = MockQueue("defer-queue-2", "project-1", is_paused=False, queue_type="defer")

        # Both defer queues have PENDING jobs
        defer1_pending = MockJob("job-defer-1", project_id="project-1", queue_id="defer-queue-1", status=AdmissionState.QUEUED.value)
        defer2_pending = MockJob("job-defer-2", project_id="project-1", queue_id="defer-queue-2", status=AdmissionState.QUEUED.value)

        started_job = MockJob("job-defer-1", project_id="project-1", queue_id="defer-queue-1", status=AdmissionState.ACTIVE.value)
        started_job.instance_id = "instance-123"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue1, defer_queue2]

        # Both defer queues have PENDING jobs
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [defer1_pending],  # defer-queue-1 idle check
            [defer1_pending],  # defer-queue-1 getting jobs
            [defer2_pending],  # defer-queue-2 idle check
            [defer2_pending],  # defer-queue-2 getting jobs
        ]
        # No PROCESSING jobs
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # defer-queue-1
            ([], 0),  # defer-queue-2
        ]

        # No non-defer queues have active jobs - this is the key to avoiding deadlock
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # At least one defer job SHOULD be started (the first one processed)
        mock_queue_service.start_job.assert_called()

        # Verify that count_active_jobs_in_non_defer_queues was called
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.assert_called()


class TestDeferQueueIntegration:
    """Integration-style tests for defer queue behavior."""

    @pytest.mark.asyncio
    async def test_full_idle_cycle(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test the full cycle: busy -> idle -> defer queue processes.

        This tests the scenario where:
        1. FIFO queue has a job running
        2. FIFO job completes
        3. Now project is idle, defer queue can process
        """
        project = MockProject("project-1", job_queue_paused=False)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [
            MockQueue("fifo-queue", "project-1", is_paused=False, queue_type="fifo"),
            MockQueue("defer-queue", "project-1", is_paused=False, queue_type="defer"),
        ]

        # FIFO has a PROCESSING job (from a previous run)
        fifo_processing = MockJob("job-fifo", project_id="project-1", queue_id="fifo-queue", status=AdmissionState.ACTIVE.value)
        fifo_processing.instance_id = "existing-instance"  # Instance already spawned

        # Defer queue has a PENDING job
        defer_pending = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.QUEUED.value)

        # First pass: FIFO has PROCESSING job
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [],  # fifo-queue: no PENDING
            [defer_pending],  # defer-queue: idle check (first call)
            [defer_pending],  # defer-queue: getting jobs (second call, if not skipped)
        ]
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([fifo_processing], 1),  # fifo-queue: has PROCESSING
            ([], 0),  # defer-queue: no PROCESSING
        ]

        # count_active_jobs_in_non_defer_queues returns 1 (fifo processing job only)
        # Since 1 > 0, defer queue is skipped
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 1

        await processor._process_next_job()

        # FIFO instance exists, so spawn_instance not called
        mock_instance_manager.spawn_instance_with_mcp.assert_not_called()
        # No jobs should be started (fifo has no pending, defer is skipped)
        mock_queue_service.start_job.assert_not_called()

        # Simulate FIFO completing - reset the mocks for second pass
        mock_queue_service.start_job.reset_mock()
        mock_instance_manager.spawn_instance_with_mcp.reset_mock()

        # Second pass: FIFO completed (no PROCESSING), defer can now process
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [],  # fifo-queue: no PENDING
            [defer_pending],  # defer-queue: idle check
            [defer_pending],  # defer-queue: getting jobs
        ]
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # fifo-queue: no PROCESSING (completed!)
            ([], 0),  # defer-queue: no PROCESSING
        ]
        # Project is now idle: count_active_jobs_in_non_defer_queues returns 0
        # Since 0 > 0 is False, defer queue is NOT skipped
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        started_job = MockJob("job-defer", project_id="project-1", queue_id="defer-queue", status=AdmissionState.ACTIVE.value)
        started_job.instance_id = "defer-instance"
        mock_queue_service.start_job.return_value = started_job
        mock_instance_manager.spawn_instance_with_mcp.return_value = "defer-instance"
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # Now defer queue job SHOULD be started
        mock_queue_service.start_job.assert_called_with("job-defer")
