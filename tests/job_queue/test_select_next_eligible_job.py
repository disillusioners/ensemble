"""Tests for _select_next_eligible_job method.

This module tests the race condition fix for defer job selection.
The _select_next_eligible_job method centralizes defer idle checking logic
to prevent defer queues from starting jobs while non-defer work is active.

The fix ensures that when a job completes and triggers the observer path:
Observer path: JobFeedbackObserver._process_event() -> _get_next_job(project_id) -> _select_next_eligible_job()

Non-defer jobs (FIFO, PARALLEL) are always returned immediately.
Defer jobs are only returned when count_active_jobs_in_non_defer_queues == 0.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.services.job_queue_service import JobQueueService
from daemon.repositories.job_queue.models import JobItem, JobStatus


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
        agent_id: str = "coder",
        project_id: str = "project-1",
        queue_id: str = "queue-1",
        status: str = JobStatus.PENDING.value,
        priority: int = 5,
        created_at: str = "2025-01-01T00:00:00",
    ):
        self.job_id = job_id
        self.agent_id = agent_id
        self.project_id = project_id
        self.queue_id = queue_id
        self.status = status
        self.priority = priority
        self.created_at = created_at
        self.message = "test message"
        self.source = "api"
        self.instance_id = None


def create_mock_queue_repo():
    """Create mock queue repository with queue type lookup."""
    mock_repo = MagicMock()
    # Map of queue_id -> MockQueue for get() calls
    mock_repo._queue_map = {}

    def mock_get(queue_id: str):
        return mock_repo._queue_map.get(queue_id)

    mock_repo.get = mock_get
    return mock_repo


def create_mock_repository():
    """Create mock job repository."""
    mock_repo = MagicMock()
    mock_repo.count_active_jobs_in_non_defer_queues = MagicMock(return_value=0)
    return mock_repo


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 1: TestSelectNextEligibleJobBasic
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectNextEligibleJobBasic:
    """Basic tests for _select_next_eligible_job method."""

    @pytest.mark.asyncio
    async def test_non_defer_job_returned_immediately(self):
        """When pending list has only FIFO jobs, the first one is returned regardless of active jobs."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["fifo-queue"] = MockQueue("fifo-queue", "project-1", queue_type="fifo")

        mock_repo = create_mock_repository()
        # Non-defer active count doesn't matter for non-defer jobs

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-1", queue_id="fifo-queue", priority=5),
            MockJob("job-2", queue_id="fifo-queue", priority=4),
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is not None
        assert result.job_id == "job-1"  # First job (highest priority)

    @pytest.mark.asyncio
    async def test_non_defer_job_returned_even_with_active_jobs(self):
        """When non-defer work is active AND there's a pending FIFO job, the FIFO job is returned (not blocked)."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["fifo-queue"] = MockQueue("fifo-queue", "project-1", queue_type="fifo")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1  # Non-defer work active

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-1", queue_id="fifo-queue", priority=5),
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is not None
        assert result.job_id == "job-1"
        # count_active_jobs_in_non_defer_queues should NOT be called for non-defer jobs
        mock_repo.count_active_jobs_in_non_defer_queues.assert_not_called()

    @pytest.mark.asyncio
    async def test_defer_job_skipped_when_non_defer_active(self):
        """THE CORE RACE FIX: When non-defer jobs are active and only defer jobs are pending, returns None."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1  # Non-defer work IS active

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-defer", queue_id="defer-queue", priority=5),
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is None  # Defer job should be skipped
        # count_active_jobs_in_non_defer_queues SHOULD be called for defer jobs
        mock_repo.count_active_jobs_in_non_defer_queues.assert_called_once_with("project-1")

    @pytest.mark.asyncio
    async def test_defer_job_returned_when_project_idle(self):
        """When no non-defer work is active, defer job is returned."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 0  # Project is idle

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-defer", queue_id="defer-queue", priority=5),
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is not None
        assert result.job_id == "job-defer"


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 2: TestSelectNextEligibleJobPriority
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectNextEligibleJobPriority:
    """Tests for priority handling in _select_next_eligible_job."""

    @pytest.mark.asyncio
    async def test_high_priority_defer_skipped_when_non_defer_active(self):
        """A high-priority defer job is still skipped when non-defer work is active."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1  # Non-defer work active

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-defer-high", queue_id="defer-queue", priority=10),  # High priority
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is None  # Even high-priority defer is skipped

    @pytest.mark.asyncio
    async def test_non_defer_preferred_over_defer_regardless_of_position(self):
        """When pending list has defer first but non-defer second, and non-defer is active, return None.

        The method iterates through pending jobs in order. If the first job is defer
        but non-defer work is active, the defer is skipped and we continue iterating.
        Since there's no other eligible job, None is returned.
        """
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")
        queue_repo._queue_map["fifo-queue"] = MockQueue("fifo-queue", "project-1", queue_type="fifo")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1  # Non-defer work active

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        # FIFO job is second in the pending list
        pending = [
            MockJob("job-defer-high", queue_id="defer-queue", priority=10),  # High priority defer
            MockJob("job-fifo-low", queue_id="fifo-queue", priority=1),   # Low priority FIFO
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        # Defer is skipped (non-defer active), FIFO is non-defer so returned
        assert result is not None
        assert result.job_id == "job-fifo-low"


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 3: TestSelectNextEligibleJobMultipleQueues
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectNextEligibleJobMultipleQueues:
    """Tests for multiple queue handling."""

    @pytest.mark.asyncio
    async def test_multiple_defer_queues_all_respect_idle_check(self):
        """Defer jobs from different queues all respect the idle check."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue-1"] = MockQueue("defer-queue-1", "project-1", queue_type="defer")
        queue_repo._queue_map["defer-queue-2"] = MockQueue("defer-queue-2", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 0  # Project idle

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-defer-1", queue_id="defer-queue-1", priority=5),
            MockJob("job-defer-2", queue_id="defer-queue-2", priority=5),
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is not None
        assert result.job_id == "job-defer-1"  # First defer job returned

    @pytest.mark.asyncio
    async def test_mixed_queues_defer_first_when_idle(self):
        """When pending list has defer first and project is idle, defer job is returned.

        The method iterates through pending jobs in order. If the first job is defer
        and project is idle, it returns the defer job.
        """
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["fifo-queue"] = MockQueue("fifo-queue", "project-1", queue_type="fifo")
        queue_repo._queue_map["parallel-queue"] = MockQueue("parallel-queue", "project-1", queue_type="parallel")
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 0  # Project idle

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-defer", queue_id="defer-queue", priority=10),       # High priority defer first
            MockJob("job-fifo", queue_id="fifo-queue", priority=5),          # FIFO second
            MockJob("job-parallel", queue_id="parallel-queue", priority=5), # Parallel third
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is not None
        # Defer is first in pending list and project is idle, so defer is returned
        assert result.job_id == "job-defer"


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 4: TestSelectNextEligibleJobEdgeCases
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectNextEligibleJobEdgeCases:
    """Edge case tests for _select_next_eligible_job."""

    @pytest.mark.asyncio
    async def test_empty_pending_returns_none(self):
        """No pending jobs -> returns None."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        mock_repo = create_mock_repository()

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = []

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_all_pending_are_defer_and_idle(self):
        """When only defer jobs exist and project is idle, first defer job is returned."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 0  # Project idle

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-defer-1", queue_id="defer-queue", priority=5),
            MockJob("job-defer-2", queue_id="defer-queue", priority=5),
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is not None
        assert result.job_id == "job-defer-1"

    @pytest.mark.asyncio
    async def test_all_pending_are_defer_and_busy(self):
        """When only defer jobs exist and non-defer work is active, returns None."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1  # Non-defer work active

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-defer-1", queue_id="defer-queue", priority=5),
            MockJob("job-defer-2", queue_id="defer-queue", priority=5),
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_defer_job_with_no_queue_id(self):
        """Job with queue_id=None is treated as non-defer (safe default)."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1  # Non-defer work active

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        pending = [
            MockJob("job-no-queue", queue_id=None, priority=5),  # No queue_id
        ]

        # Act
        result = await service._select_next_eligible_job(pending, "project-1")

        # Assert
        assert result is not None
        assert result.job_id == "job-no-queue"
        # count_active_jobs_in_non_defer_queues should NOT be called (treated as non-defer)


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 5: TestGetNextJobIntegration
# ─────────────────────────────────────────────────────────────────────────────

class TestGetNextJobIntegration:
    """Integration tests for _get_next_job with project_id, verifying it uses _select_next_eligible_job."""

    @pytest.mark.asyncio
    async def test_get_next_job_with_project_id_uses_select_next_eligible(self):
        """Integration test: _get_next_job(project_id=...) properly delegates to _select_next_eligible_job."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1  # Non-defer work active
        mock_repo.list_pending_by_project.return_value = [
            MockJob("job-defer", queue_id="defer-queue", priority=5),
        ]

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        # Act
        result = await service._get_next_job(project_id="project-1")

        # Assert
        assert result is None  # Defer job blocked by active non-defer work
        mock_repo.list_pending_by_project.assert_called_once_with("project-1")

    @pytest.mark.asyncio
    async def test_get_next_job_without_project_id_returns_first_pending(self):
        """_get_next_job() without project_id uses the old path (list_all_pending -> first)."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        mock_repo = create_mock_repository()
        mock_repo.list_all_pending.return_value = [
            MockJob("job-1", queue_id="some-queue", priority=5),
            MockJob("job-2", queue_id="some-queue", priority=4),
        ]

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        # Act
        result = await service._get_next_job()

        # Assert
        assert result is not None
        assert result.job_id == "job-1"
        mock_repo.list_all_pending.assert_called_once()
        # Should NOT call list_pending_by_project or _select_next_eligible_job
        mock_repo.list_pending_by_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_next_job_with_queue_id_returns_first_pending(self):
        """_get_next_job(queue_id=...) returns first pending without defer check."""
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.list_pending_by_queue.return_value = [
            MockJob("job-defer", queue_id="defer-queue", priority=5),
        ]
        # count_active_jobs should NOT be called when queue_id is specified
        mock_repo.count_active_jobs_in_non_defer_queues.return_value = 1

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        # Act
        result = await service._get_next_job(queue_id="defer-queue")

        # Assert
        assert result is not None
        assert result.job_id == "job-defer"
        # Should NOT check for active non-defer jobs (queue_id takes precedence)
        mock_repo.count_active_jobs_in_non_defer_queues.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_next_job_integration_mixed_queues(self):
        """Integration test: Mixed queues with project_id returns first eligible job.

        When pending list has defer first and project is idle, defer job is returned.
        """
        # Arrange
        queue_repo = create_mock_queue_repo()
        queue_repo._queue_map["fifo-queue"] = MockQueue("fifo-queue", "project-1", queue_type="fifo")
        queue_repo._queue_map["defer-queue"] = MockQueue("defer-queue", "project-1", queue_type="defer")

        mock_repo = create_mock_repository()
        mock_repo.list_pending_by_project.return_value = [
            MockJob("job-defer", queue_id="defer-queue", priority=10),  # High priority defer FIRST
            MockJob("job-fifo", queue_id="fifo-queue", priority=5),     # Lower priority FIFO second
        ]

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )

        # Act
        result = await service._get_next_job(project_id="project-1")

        # Assert
        assert result is not None
        # Defer is first in pending list and project is idle (count returns 0), so defer is returned
        assert result.job_id == "job-defer"
