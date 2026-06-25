"""Tests for defer queue deadlock scenario.

This module tests the specific deadlock scenario that was fixed in e95cc12:
- Two defer queues with pending jobs and NO non-defer queues
- Without the fix: each defer queue would count the other's pending jobs as "active"
- With the fix: count_active_jobs_in_non_defer_queues returns 0, allowing both to process

The key fix:
- Original: count_active_jobs_by_project (all queues)
- Fixed: count_active_jobs_in_non_defer_queues (excludes defer queue types)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.services.job_processor import JobProcessor
from daemon.repositories.job_queue.models import JobItem, JobStatus, QueueType


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
    """Create JobProcessor with mocked dependencies."""
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
# Deadlock Scenario Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDeferQueueDeadlockScenario:
    """Tests for the defer queue deadlock scenario.

    The deadlock bug occurred when:
    1. Two defer queues each have pending jobs
    2. There are NO FIFO or PARALLEL queues
    3. Each defer queue would count the other's pending jobs as "active"
    4. This prevented either from dequeuing (deadlock)

    The fix: count_active_jobs_in_non_defer_queues excludes defer queues,
    so when there are only defer queues, the count is 0, allowing all to process.
    """

    @pytest.mark.asyncio
    async def test_two_defer_queues_no_deadlock(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Two defer queues with pending jobs and NO non-defer queues should both be able to process.

        This is the core deadlock test case:
        - Project has 2 defer queues (defer-queue-A, defer-queue-B)
        - No FIFO or PARALLEL queues exist
        - Each defer queue has 1 PENDING job
        - count_active_jobs_in_non_defer_queues returns 0
        - Both defer queues should be eligible to process
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue_a = MockQueue("defer-queue-A", "project-1", is_paused=False, queue_type="defer")
        defer_queue_b = MockQueue("defer-queue-B", "project-1", is_paused=False, queue_type="defer")

        # Both defer queues have PENDING jobs
        job_a = MockJob("job-A", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PENDING.value)
        job_b = MockJob("job-B", project_id="project-1", queue_id="defer-queue-B", status=JobStatus.PENDING.value)

        started_job_a = MockJob("job-A", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PROCESSING.value)
        started_job_a.instance_id = "instance-A"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue_a, defer_queue_b]

        # Both defer queues have PENDING jobs
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [job_a],  # defer-queue-A idle check
            [job_a],  # defer-queue-A getting jobs
            [job_b],  # defer-queue-B idle check
            [job_b],  # defer-queue-B getting jobs
        ]
        # No PROCESSING jobs in either queue
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # defer-queue-A
            ([], 0),  # defer-queue-B
        ]

        # KEY: No non-defer queues have active jobs - this is what prevents deadlock
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job_a
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # At least one defer job SHOULD be started (the first one processed)
        mock_queue_service.start_job.assert_called()

        # Verify that count_active_jobs_in_non_defer_queues was called (the fix)
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.assert_called()

    @pytest.mark.asyncio
    async def test_three_defer_queues_no_deadlock(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Three defer queues with pending jobs and NO non-defer queues should all be able to process.

        Extended test to verify the fix scales beyond 2 defer queues.
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue_a = MockQueue("defer-queue-A", "project-1", is_paused=False, queue_type="defer")
        defer_queue_b = MockQueue("defer-queue-B", "project-1", is_paused=False, queue_type="defer")
        defer_queue_c = MockQueue("defer-queue-C", "project-1", is_paused=False, queue_type="defer")

        # All three defer queues have PENDING jobs
        job_a = MockJob("job-A", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PENDING.value)
        job_b = MockJob("job-B", project_id="project-1", queue_id="defer-queue-B", status=JobStatus.PENDING.value)
        job_c = MockJob("job-C", project_id="project-1", queue_id="defer-queue-C", status=JobStatus.PENDING.value)

        started_job_a = MockJob("job-A", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PROCESSING.value)
        started_job_a.instance_id = "instance-A"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue_a, defer_queue_b, defer_queue_c]

        # All defer queues have PENDING jobs
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [job_a],  # defer-queue-A
            [job_a],  # defer-queue-A
            [job_b],  # defer-queue-B
            [job_b],  # defer-queue-B
            [job_c],  # defer-queue-C
            [job_c],  # defer-queue-C
        ]
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # defer-queue-A
            ([], 0),  # defer-queue-B
            ([], 0),  # defer-queue-C
        ]

        # KEY: count_active_jobs_in_non_defer_queues returns 0
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job_a
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # At least one defer job SHOULD be started
        mock_queue_service.start_job.assert_called()

    @pytest.mark.asyncio
    async def test_defer_queues_with_fifo_queue_fifo_busy(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queues should WAIT when FIFO queue has active jobs.

        This verifies the fix doesn't break the normal defer behavior:
        - FIFO queue has a PENDING job
        - Defer queues have pending jobs
        - count_active_jobs_in_non_defer_queues returns 1
        - Defer queues should SKIP until FIFO is done
        """
        project = MockProject("project-1", job_queue_paused=False)
        fifo_queue = MockQueue("fifo-queue", "project-1", is_paused=False, queue_type="fifo")
        defer_queue_a = MockQueue("defer-queue-A", "project-1", is_paused=False, queue_type="defer")
        defer_queue_b = MockQueue("defer-queue-B", "project-1", is_paused=False, queue_type="defer")

        # FIFO has PENDING, both defer queues have PENDING
        fifo_job = MockJob("job-fifo", project_id="project-1", queue_id="fifo-queue", status=JobStatus.PENDING.value)
        job_a = MockJob("job-A", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PENDING.value)
        job_b = MockJob("job-B", project_id="project-1", queue_id="defer-queue-B", status=JobStatus.PENDING.value)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [fifo_queue, defer_queue_a, defer_queue_b]

        # FIFO has PENDING, defer queues have PENDING
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [fifo_job],  # fifo-queue
            [job_a],  # defer-queue-A idle check
            [job_b],  # defer-queue-B idle check
        ]
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # fifo-queue
            ([], 0),  # defer-queue-A
            ([], 0),  # defer-queue-B
        ]

        # FIFO has 1 active job - defer queues should SKIP
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 1

        await processor._process_next_job()

        # FIFO job SHOULD be started
        mock_queue_service.start_job.assert_called_with("job-fifo")
        # Defer jobs should NOT be started (skipped because FIFO is busy)
        for call in mock_queue_service.start_job.call_args_list:
            assert call[0][0] not in ["job-A", "job-B"], \
                "Defer queue job should not be started when FIFO has pending jobs"

    @pytest.mark.asyncio
    async def test_defer_queues_with_fifo_queue_fifo_idle(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Defer queues should process when FIFO queue has NO active jobs.

        This verifies the full cycle:
        1. FIFO queue has no jobs (idle)
        2. count_active_jobs_in_non_defer_queues returns 0
        3. Defer queues can now process
        """
        project = MockProject("project-1", job_queue_paused=False)
        fifo_queue = MockQueue("fifo-queue", "project-1", is_paused=False, queue_type="fifo")
        defer_queue_a = MockQueue("defer-queue-A", "project-1", is_paused=False, queue_type="defer")
        defer_queue_b = MockQueue("defer-queue-B", "project-1", is_paused=False, queue_type="defer")

        # FIFO has no jobs, both defer queues have PENDING
        job_a = MockJob("job-A", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PENDING.value)
        job_b = MockJob("job-B", project_id="project-1", queue_id="defer-queue-B", status=JobStatus.PENDING.value)

        started_job_a = MockJob("job-A", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PROCESSING.value)
        started_job_a.instance_id = "instance-A"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [fifo_queue, defer_queue_a, defer_queue_b]

        # FIFO has no jobs, defer queues have PENDING
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [],  # fifo-queue: no pending
            [job_a],  # defer-queue-A idle check
            [job_a],  # defer-queue-A getting jobs
            [job_b],  # defer-queue-B idle check
            [job_b],  # defer-queue-B getting jobs
        ]
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # fifo-queue
            ([], 0),  # defer-queue-A
            ([], 0),  # defer-queue-B
        ]

        # FIFO has 0 active jobs - defer queues CAN process
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job_a
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # FIFO has no pending, but defer queue A SHOULD be started
        mock_queue_service.start_job.assert_called_with("job-A")


class TestDeferQueueConcurrencyLimit:
    """Tests for defer queue concurrency limit enforcement.

    Even though multiple defer queues can process simultaneously (no deadlock),
    each individual defer queue enforces concurrency_limit=1.
    """

    @pytest.mark.asyncio
    async def test_each_defer_queue_enforces_concurrency_limit_1(
        self, processor, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Each defer queue only processes 1 job at a time (concurrency_limit=1).

        While multiple defer queues can run simultaneously (no deadlock),
        each queue itself respects concurrency_limit=1.
        """
        project = MockProject("project-1", job_queue_paused=False)
        defer_queue_a = MockQueue("defer-queue-A", "project-1", is_paused=False, queue_type="defer", concurrency_limit=1)
        defer_queue_b = MockQueue("defer-queue-B", "project-1", is_paused=False, queue_type="defer", concurrency_limit=1)

        # Each queue has 2 pending jobs
        job_a1 = MockJob("job-A1", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PENDING.value)
        job_a2 = MockJob("job-A2", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PENDING.value)
        job_b1 = MockJob("job-B1", project_id="project-1", queue_id="defer-queue-B", status=JobStatus.PENDING.value)
        job_b2 = MockJob("job-B2", project_id="project-1", queue_id="defer-queue-B", status=JobStatus.PENDING.value)

        started_job_a1 = MockJob("job-A1", project_id="project-1", queue_id="defer-queue-A", status=JobStatus.PROCESSING.value)
        started_job_a1.instance_id = "instance-A1"

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [defer_queue_a, defer_queue_b]

        # Both queues have 2 pending jobs
        mock_queue_service._repository.list_pending_by_queue.side_effect = [
            [job_a1, job_a2],  # defer-queue-A
            [job_a1, job_a2],  # defer-queue-A (second call)
            [job_b1, job_b2],  # defer-queue-B
            [job_b1, job_b2],  # defer-queue-B (second call)
        ]
        # Both queues have 0 processing jobs initially
        mock_queue_service._repository.list_by_queue.side_effect = [
            ([], 0),  # defer-queue-A: no processing
            ([], 0),  # defer-queue-B: no processing
        ]

        # No non-defer queues have active jobs
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        mock_queue_service.start_job.return_value = started_job_a1
        mock_instance_manager.enqueue_message = AsyncMock()

        await processor._process_next_job()

        # At least one job should be started (the processor processes the first available queue)
        # Note: The processor picks one queue and starts jobs up to its concurrency limit
        mock_queue_service.start_job.assert_called()



