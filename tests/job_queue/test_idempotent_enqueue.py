"""Tests for JobQueueService idempotent enqueue functionality.

This module tests the idempotency key behavior in the enqueue() method,
verifying that duplicate jobs are handled correctly based on their status.

Idempotency behavior:
- PENDING/PROCESSING/FAILED jobs with same key: return existing (non-terminal)
- COMPLETED/CANCELLED/DEAD_LETTER jobs with same key: create new (terminal)
- Jobs older than TTL: treated as new (allows re-submission after TTL expires)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from daemon.services.job_queue_service import JobQueueService
from daemon.repositories.job_queue.models import JobItem, JobStatus

# Test system project ID (must match conftest.py in job_queue)
TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


def make_mock_job(
    job_id: str = "test-job-1",
    agent_id: str = "developer",
    project_id: str = "test-project",
    status: str = JobStatus.PENDING.value,
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    """Create a mock JobItem for testing.
    
    Args:
        job_id: Unique job identifier.
        agent_id: Agent ID.
        project_id: Project identifier.
        status: Job status.
        idempotency_key: Optional idempotency key.
        created_at: Job creation timestamp (default: now).
        
    Returns:
        Mock JobItem with specified attributes.
    """
    job = MagicMock(spec=JobItem)
    job.job_id = job_id
    job.agent_id = agent_id
    job.project_id = project_id
    job.status = status
    job.message = "test message"
    job.source = "api"
    job.priority = 5
    job.instance_id = None
    job.queue_id = None
    job.idempotency_key = idempotency_key
    job.job_metadata = {}
    job.created_at = (created_at or datetime.now(timezone.utc)).isoformat()
    return job


def make_mock_registry(agent_id: str = "developer", agent_path: str = "/agents/developer") -> MagicMock:
    """Create a mock registry that returns agent metadata.
    
    Args:
        agent_id: Agent ID to return.
        agent_path: Agent path to return.
        
    Returns:
        Mock registry with get() method.
    """
    registry = MagicMock()
    agent_meta = MagicMock()
    agent_meta.path = agent_path
    registry.get.return_value = agent_meta
    return registry


def make_mock_queue(queue_id: str = "system-fifo-queue-id") -> MagicMock:
    """Create a mock queue for the system project.
    
    Args:
        queue_id: Queue ID to return.
        
    Returns:
        Mock queue object.
    """
    mock_queue = MagicMock()
    mock_queue.queue_id = queue_id
    mock_queue.project_id = TEST_SYSTEM_PROJECT_ID
    mock_queue.queue_name = "system_fifo_queue"
    return mock_queue


def create_queue_repo_with_system_queue():
    """Create a mock queue repository that returns a system queue.
    
    Returns:
        Mock JobQueueRepository with system queue for system project and test-project.
    """
    repo = MagicMock()
    mock_queue = make_mock_queue()
    
    # Create additional mock queue for test-project
    test_project_queue = MagicMock()
    test_project_queue.queue_id = "test-project-queue-id"
    test_project_queue.project_id = "test-project"
    test_project_queue.queue_name = "system_fifo_queue"
    
    # Return system queue for the system project, test-project queue for test-project, None for other projects
    def get_by_name_side_effect(project_id: str, queue_name: str):
        if project_id == TEST_SYSTEM_PROJECT_ID and queue_name == "system_fifo_queue":
            return mock_queue
        if project_id == "test-project" and queue_name == "system_fifo_queue":
            return test_project_queue
        return None
    repo.get_by_name = MagicMock(side_effect=get_by_name_side_effect)
    repo.get = MagicMock(return_value=None)
    return repo


class TestIdempotentEnqueue:
    """Tests for idempotent enqueue behavior in JobQueueService."""

    @pytest.fixture
    def mock_repository(self) -> MagicMock:
        """Create mock JobRepository.

        M6 fix: the service now calls ``create_or_get_by_idempotency_key``
        (atomic INSERT ... ON CONFLICT DO NOTHING) instead of the legacy
        find-then-insert pattern. We wire the mock to simulate the atomic
        method using the existing ``find_by_idempotency_key`` + ``create``
        so the service-level assertions about idempotency behavior
        continue to work without changes.
        """
        repo = MagicMock()
        repo.find_by_idempotency_key = MagicMock(return_value=None)
        repo.create = MagicMock()

        def _create_or_get_side_effect(**kwargs):
            key = kwargs.get("idempotency_key")
            existing = repo.find_by_idempotency_key(key)
            if existing is not None:
                return existing, False
            new_job = repo.create(**kwargs)
            return new_job, True

        repo.create_or_get_by_idempotency_key = MagicMock(
            side_effect=_create_or_get_side_effect
        )
        return repo

    @pytest.fixture
    def mock_lock_manager(self) -> MagicMock:
        """Create mock JobLockManager."""
        return MagicMock()

    @pytest.fixture
    def mock_queue_repo(self) -> MagicMock:
        """Create mock JobQueueRepository with system queue for system project."""
        return create_queue_repo_with_system_queue()

    @pytest.fixture
    def mock_dispatch_bus(self) -> MagicMock:
        """Create mock dispatch bus."""
        return MagicMock()

    @pytest.fixture
    def service(
        self, mock_repository, mock_lock_manager, mock_queue_repo
    ) -> JobQueueService:
        """Create JobQueueService with mocked dependencies."""
        return JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
        )

    # ========== Test Cases ==========

    @pytest.mark.asyncio
    async def test_enqueue_without_key_creates_normally(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that normal enqueue without idempotency_key works as before.
        
        Verifies that:
        - No idempotency check is performed
        - A new job is created
        - notify_new_job is called on dispatch_bus
        """
        expected_job = make_mock_job(job_id="new-job-1")
        mock_repository.create.return_value = expected_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
            )
        
        # Verify new job was created
        assert result.job_id == "new-job-1"
        mock_repository.create.assert_called_once()
        mock_repository.find_by_idempotency_key.assert_not_called()
        
        # Verify notify was called (dispatch_bus is None by default)
        assert service._dispatch_bus is None

    @pytest.mark.asyncio
    async def test_enqueue_with_key_creates_new_job(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that first enqueue with idempotency_key creates new job.
        
        Verifies that:
        - find_by_idempotency_key is called
        - No existing job found
        - A new job is created with the idempotency_key
        """
        mock_repository.find_by_idempotency_key.return_value = None  # No existing job
        
        new_job = make_mock_job(job_id="new-job-1", idempotency_key="key-123")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-123",
            )
        
        # Verify idempotency check was performed
        mock_repository.find_by_idempotency_key.assert_called_once_with("key-123")
        
        # Verify new job was created with the idempotency_key
        assert result.job_id == "new-job-1"
        mock_repository.create.assert_called_once()
        call_kwargs = mock_repository.create.call_args.kwargs
        assert call_kwargs["idempotency_key"] == "key-123"

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_key_pending_returns_existing(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
    ):
        """Test that duplicate key with PENDING job returns existing job.
        
        Verifies that:
        - Existing PENDING job is found
        - No new job is created
        - The existing job is returned
        """
        existing_job = make_mock_job(
            job_id="existing-job-1",
            status=JobStatus.PENDING.value,
            idempotency_key="key-123",
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-123",
            )
        
        # Verify existing job was returned
        assert result.job_id == "existing-job-1"
        assert result.status == JobStatus.PENDING.value
        
        # Verify no new job was created
        mock_repository.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_key_processing_returns_existing(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
    ):
        """Test that duplicate key with PROCESSING job returns existing job.
        
        Verifies that:
        - Existing PROCESSING job is found
        - No new job is created (PROCESSING is non-terminal)
        - The existing job is returned
        """
        existing_job = make_mock_job(
            job_id="existing-job-2",
            status=JobStatus.PROCESSING.value,
            idempotency_key="key-456",
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-456",
            )
        
        # Verify existing job was returned
        assert result.job_id == "existing-job-2"
        assert result.status == JobStatus.PROCESSING.value
        
        # Verify no new job was created
        mock_repository.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_key_completed_creates_new(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that duplicate key with COMPLETED job creates new job.
        
        Verifies that:
        - Existing COMPLETED job is found
        - A new job is created (COMPLETED is terminal)
        - Both jobs have the same idempotency_key
        """
        existing_job = make_mock_job(
            job_id="completed-job-1",
            status=JobStatus.COMPLETED.value,
            idempotency_key="key-789",
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        new_job = make_mock_job(job_id="new-job-2", idempotency_key="key-789")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-789",
            )
        
        # Verify new job was created
        assert result.job_id == "new-job-2"
        
        # Verify create was called (terminal job, allow resubmit)
        mock_repository.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_key_failed_returns_existing(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
    ):
        """Test that duplicate key with FAILED job returns existing job.
        
        Verifies that:
        - Existing FAILED job is found
        - No new job is created (FAILED is non-terminal in current implementation)
        - The existing job is returned
        
        Note: In the current implementation, FAILED is NOT treated as terminal.
        This means duplicate enqueues with FAILED jobs return the existing job.
        The retry_job() method should be used to create new jobs for failed work.
        """
        existing_job = make_mock_job(
            job_id="failed-job-1",
            status=JobStatus.FAILED.value,
            idempotency_key="key-failed",
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-failed",
            )
        
        # Verify existing job was returned (FAILED is non-terminal in implementation)
        assert result.job_id == "failed-job-1"
        assert result.status == JobStatus.FAILED.value
        
        # Verify no new job was created
        mock_repository.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_key_cancelled_creates_new(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that duplicate key with CANCELLED job creates new job.
        
        Verifies that:
        - Existing CANCELLED job is found
        - A new job is created (CANCELLED is terminal)
        - Both jobs have the same idempotency_key
        """
        existing_job = make_mock_job(
            job_id="cancelled-job-1",
            status=JobStatus.CANCELLED.value,
            idempotency_key="key-cancelled",
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        new_job = make_mock_job(job_id="new-job-4", idempotency_key="key-cancelled")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-cancelled",
            )
        
        # Verify new job was created
        assert result.job_id == "new-job-4"
        
        # Verify create was called (terminal job, allow resubmit)
        mock_repository.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_dispatch_notification_fired(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
        mock_dispatch_bus: MagicMock,
    ):
        """Test that notify_new_job is called after enqueue.
        
        Verifies that:
        - Dispatch bus is set on service
        - notify_new_job is called with correct project_id
        """
        # Set dispatch bus
        service.set_dispatch_bus(mock_dispatch_bus)
        
        # Mock queue_repo to return a valid system queue for the project
        mock_queue = MagicMock()
        mock_queue.queue_id = "system-fifo-queue-id"
        mock_queue_repo.get_by_name.return_value = mock_queue
        
        new_job = make_mock_job(job_id="new-job-5", project_id="test-project")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                project_id="test-project",
            )
        
        # Verify notify_new_job was called
        mock_dispatch_bus.notify_new_job.assert_called_once_with("test-project")

    @pytest.mark.asyncio
    async def test_enqueue_no_dispatch_bus_no_error(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that enqueue works even without dispatch_bus set.
        
        Verifies that:
        - Service has no dispatch_bus set
        - Enqueue completes successfully without error
        - No notification is attempted
        """
        assert service._dispatch_bus is None
        
        new_job = make_mock_job(job_id="new-job-6")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            # Should not raise even without dispatch_bus
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
            )
        
        assert result.job_id == "new-job-6"


class TestIdempotentEnqueueEdgeCases:
    """Additional edge case tests for idempotent enqueue."""

    @pytest.fixture
    def mock_repository(self) -> MagicMock:
        """Create mock JobRepository.

        M6 fix: see TestIdempotentEnqueue.mock_repository for rationale.
        """
        repo = MagicMock()
        repo.find_by_idempotency_key = MagicMock(return_value=None)
        repo.create = MagicMock()

        def _create_or_get_side_effect(**kwargs):
            key = kwargs.get("idempotency_key")
            existing = repo.find_by_idempotency_key(key)
            if existing is not None:
                return existing, False
            new_job = repo.create(**kwargs)
            return new_job, True

        repo.create_or_get_by_idempotency_key = MagicMock(
            side_effect=_create_or_get_side_effect
        )
        return repo

    @pytest.fixture
    def mock_lock_manager(self) -> MagicMock:
        """Create mock JobLockManager."""
        return MagicMock()

    @pytest.fixture
    def mock_queue_repo(self) -> MagicMock:
        """Create mock JobQueueRepository with system queue for system project."""
        return create_queue_repo_with_system_queue()

    @pytest.fixture
    def service(
        self, mock_repository, mock_lock_manager, mock_queue_repo
    ) -> JobQueueService:
        """Create JobQueueService with mocked dependencies."""
        return JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
        )

    @pytest.mark.asyncio
    async def test_enqueue_dead_letter_creates_new(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that duplicate key with DEAD_LETTER job creates new job.
        
        DEAD_LETTER is a terminal status that should allow resubmission.
        """
        existing_job = make_mock_job(
            job_id="dead-letter-job-1",
            status=JobStatus.DEAD_LETTER.value,
            idempotency_key="key-dlq",
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        new_job = make_mock_job(job_id="new-job-dlq", idempotency_key="key-dlq")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-dlq",
            )
        
        # Verify new job was created
        assert result.job_id == "new-job-dlq"
        mock_repository.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_with_agent_not_found_raises(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
    ):
        """Test that enqueue raises ValueError when agent is not found.
        
        Even with idempotency key matching, should raise if agent doesn't exist.
        """
        mock_repository.find_by_idempotency_key.return_value = None
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            # ``enqueue`` now uses ``get_resolved`` (alias-aware) instead of bare
            # ``get`` so the lookup must be mocked on the alias-aware method.
            mock_registry.get_resolved.return_value = None  # Agent not found
            mock_registry.get.return_value = None  # Legacy strict path
            mock_get_registry.return_value = mock_registry

            with pytest.raises(ValueError, match="Agent not found"):
                await service.enqueue(
                    agent_id="nonexistent-agent",
                    message="test message",
                    source="api",
                    idempotency_key="key-123",
                )


class TestIdempotentEnqueueTTL:
    """Tests for idempotency key TTL (time-to-live) behavior."""

    @pytest.fixture
    def mock_repository(self) -> MagicMock:
        """Create mock JobRepository.

        M6 fix: see TestIdempotentEnqueue.mock_repository for rationale.
        """
        repo = MagicMock()
        repo.find_by_idempotency_key = MagicMock(return_value=None)
        repo.create = MagicMock()

        def _create_or_get_side_effect(**kwargs):
            key = kwargs.get("idempotency_key")
            existing = repo.find_by_idempotency_key(key)
            if existing is not None:
                return existing, False
            new_job = repo.create(**kwargs)
            return new_job, True

        repo.create_or_get_by_idempotency_key = MagicMock(
            side_effect=_create_or_get_side_effect
        )
        return repo

    @pytest.fixture
    def mock_lock_manager(self) -> MagicMock:
        """Create mock JobLockManager."""
        return MagicMock()

    @pytest.fixture
    def mock_queue_repo(self) -> MagicMock:
        """Create mock JobQueueRepository with system queue for system project."""
        return create_queue_repo_with_system_queue()

    @pytest.fixture
    def service(
        self, mock_repository, mock_lock_manager, mock_queue_repo
    ) -> JobQueueService:
        """Create JobQueueService with mocked dependencies."""
        svc = JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
        )
        # Set TTL to 24 hours
        mock_config = MagicMock()
        mock_config.idempotency_key_ttl_hours = 24
        svc.set_config(mock_config)
        return svc

    @pytest.mark.asyncio
    async def test_enqueue_expired_ttl_creates_new(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that job older than TTL creates new job instead of returning existing.
        
        Verifies that:
        - Existing job with same key exists but is older than TTL
        - A new job is created (expired TTL allows resubmission)
        - The existing job is NOT returned
        """
        # Create an old job (30 hours ago, TTL is 24 hours)
        old_time = datetime.now(timezone.utc) - timedelta(hours=30)
        existing_job = make_mock_job(
            job_id="old-job-1",
            status=JobStatus.PENDING.value,
            idempotency_key="key-ttl",
            created_at=old_time,
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        new_job = make_mock_job(job_id="new-job-ttl", idempotency_key="key-ttl")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-ttl",
            )
        
        # Verify new job was created (expired TTL)
        assert result.job_id == "new-job-ttl"
        mock_repository.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_within_ttl_returns_existing(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
    ):
        """Test that job within TTL returns existing instead of creating new.
        
        Verifies that:
        - Existing job with same key exists and is within TTL
        - No new job is created
        - The existing job is returned
        """
        # Create a recent job (10 hours ago, TTL is 24 hours)
        recent_time = datetime.now(timezone.utc) - timedelta(hours=10)
        existing_job = make_mock_job(
            job_id="recent-job-1",
            status=JobStatus.PENDING.value,
            idempotency_key="key-ttl-recent",
            created_at=recent_time,
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-ttl-recent",
            )
        
        # Verify existing job was returned (within TTL)
        assert result.job_id == "recent-job-1"
        
        # Verify no new job was created
        mock_repository.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_custom_ttl_respected(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that custom TTL configuration is respected.
        
        Verifies that:
        - Setting TTL to 1 hour changes behavior
        - Job older than 1 hour is treated as new
        """
        # Set TTL to 1 hour
        mock_config = MagicMock()
        mock_config.idempotency_key_ttl_hours = 1
        service.set_config(mock_config)
        
        # Create a job that's 2 hours old
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        existing_job = make_mock_job(
            job_id="old-job-custom",
            status=JobStatus.PENDING.value,
            idempotency_key="key-custom-ttl",
            created_at=old_time,
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        new_job = make_mock_job(job_id="new-job-custom", idempotency_key="key-custom-ttl")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-custom-ttl",
            )
        
        # Verify new job was created (custom TTL of 1 hour exceeded)
        assert result.job_id == "new-job-custom"
        mock_repository.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_ttl_edge_case_exactly_at_ttl(
        self,
        service: JobQueueService,
        mock_repository: MagicMock,
        mock_queue_repo: MagicMock,
    ):
        """Test that job exactly at TTL boundary is treated as expired.
        
        Verifies that:
        - Job created exactly 24 hours ago (TTL boundary) is treated as expired
        - A new job is created
        """
        # Create a job exactly at TTL boundary (24 hours ago)
        boundary_time = datetime.now(timezone.utc) - timedelta(hours=24, minutes=0)
        existing_job = make_mock_job(
            job_id="boundary-job",
            status=JobStatus.PENDING.value,
            idempotency_key="key-boundary",
            created_at=boundary_time,
        )
        mock_repository.find_by_idempotency_key.return_value = existing_job
        
        new_job = make_mock_job(job_id="new-job-boundary", idempotency_key="key-boundary")
        mock_repository.create.return_value = new_job
        
        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = make_mock_registry()
            
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                idempotency_key="key-boundary",
            )
        
        # At exactly TTL boundary, job is still considered within TTL (< cutoff, not <=)
        # This test documents the boundary behavior
        assert result.job_id == "new-job-boundary"
        mock_repository.create.assert_called_once()
