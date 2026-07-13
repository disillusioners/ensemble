"""Tests for resume_processing_job message ID uniqueness.

These tests verify that resume_processing_job always generates a fresh UUID
for the message_id parameter.

With the new implementation:
1. Child instances: enqueue_message() generates a fresh message_id
2. Root instances: resume_processing_job generates a fresh UUID via uuid.uuid4()

This ensures LangGraph's add_messages reducer appends messages correctly.

Phase 2.5 (D13 / Phase 2 migration): the root-vs-child routing moved
off ``JobRepository.find_processing_message_jobs_by_instance`` (no
MESSAGE ``JobItem`` rows exist post-D13) onto
``TaskRepository.find_paused_or_running_by_instance`` (Task 2.5.2).
The legacy ``MockJob`` keeps working as a ``Task`` row alias.
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from daemon.request_registry import ActiveRequestRegistry


class MockJob:
    """Mock ``Task`` row returned by ``TaskRepository.find_paused_or_running_by_instance``.

    Phase 2.5 (D13): the legacy ``MockJob`` simulated a ``JobItem``
    row; after D13 the equivalent primitive returns a ``Task`` row
    instead. The attribute shape was updated to match what
    ``resume_processing_job`` reads (``existing_task.id``,
    ``existing_task.status``) plus the pre-D13 attributes kept for
    backwards-compat with call sites that haven't been rewritten.

    The ``work_id`` attribute (a UUID4 string) is the stable Task
    handle the production code derives ``old_job_id`` from when
    building the resume context. Tests that need a specific work_id
    should pass it explicitly via ``work_id=``; the default
    ``uuid.uuid4()`` keeps each instance unique so concurrent
    resume flows cannot collide on a shared value.
    """

    def __init__(
        self,
        job_id: str = "test-job-123",
        instance_id: str = "test-instance",
        message_id: str | None = "original-msg-123",
        status: str = "running",
        task_type: str = "process_message",
        work_id: str | None = None,
    ):
        # Post-D13 ``Task`` attributes.
        self.id = job_id
        self.task_type = task_type
        self.status = status
        self.worker_id = "worker-0"
        # Stable UUID4 work_id — production derives ``old_job_id`` from
        # this attribute (the Task's stable identifier, NOT the integer
        # PK ``id``). Tests that pin a specific value should pass
        # ``work_id="..."`` explicitly.
        self.work_id = work_id if work_id is not None else str(uuid.uuid4())
        # Pre-D13 ``JobItem`` attributes (kept for backwards-compat).
        self.job_id = job_id
        self.instance_id = instance_id
        self.message = "test message"
        self.job_type = "message"
        self.project_id = "project-1"
        self.queue_id = "queue-1"
        self.job_metadata = {"message_id": message_id} if message_id is not None else {}


class MockAsyncMessageResult:
    """Mock async message result returned by enqueue_message."""

    def __init__(self, message_id: str = None):
        self.message_id = message_id or str(uuid.uuid4())
        self.instance_id = None
        self.status = "queued"


class MockMessageResult:
    """Mock message result returned by _process_message_with_tracking."""

    def __init__(self, content: str = "Resume completed"):
        self.content = content


@pytest.fixture
def mock_job_queue_service():
    """Create mock job queue service with repository."""
    service = MagicMock()
    service._repository = MagicMock()
    service.complete_job = AsyncMock()
    return service


@pytest.fixture
def mock_queue_repository():
    """Create mock queue repository."""
    repo = MagicMock()
    repo.complete = MagicMock()
    repo.list_by_instance = MagicMock(return_value=[])
    return repo


@pytest.fixture
def mock_instance_repository():
    """Create mock instance repository."""
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_task_repository():
    """Create mock ``TaskRepository`` (Phase 2.5 / D13 routing primitive).

    ``resume_processing_job`` calls
    ``self._task_repo.find_paused_or_running_by_instance(instance_id)``
    — a non-None ``Task`` → root path (checkpoint resume); ``None`` →
    child path (WorkerPool enqueue).
    """
    repo = MagicMock()
    repo.find_paused_or_running_by_instance = MagicMock(return_value=None)
    return repo


@pytest.fixture
def mock_manager(
    mock_job_queue_service,
    mock_queue_repository,
    mock_instance_repository,
    mock_task_repository,
):
    """Create mock manager with all required dependencies."""
    manager = MagicMock()
    manager._job_queue_service = mock_job_queue_service
    manager._queue_repository = mock_queue_repository
    manager._instance_repository = mock_instance_repository
    # Phase 2.5 (Task 2.5.2): routing primitive moved onto
    # ``TaskRepository.find_paused_or_running_by_instance``.
    manager._task_repo = mock_task_repository
    # Mock enqueue_message for WorkerPool path (child instances)
    manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
    # Mock _process_message_with_tracking for JobQueue path (root instances)
    manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())
    # Mock _process_child_completion_and_notify_parent
    manager._process_child_completion_and_notify_parent = AsyncMock()
    manager._graph_tasks = {}
    # W4: real registry so register()/unregister() return real
    # CancellationTokenSource. See test_resume_waiting_children for
    # the rationale.
    manager._request_registry = ActiveRequestRegistry()
    return manager


@pytest.fixture
def instance_manager(mock_manager):
    """Create InstanceManager with mocked dependencies."""
    from daemon.manager import InstanceManager
    from daemon.config import Config

    config = MagicMock(spec=Config)
    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = mock_manager._job_queue_service
    manager._queue_repository = mock_manager._queue_repository
    manager._instance_repository = mock_manager._instance_repository
    manager._task_repo = mock_manager._task_repo
    manager.enqueue_message = mock_manager.enqueue_message
    manager._process_message_with_tracking = mock_manager._process_message_with_tracking
    manager._process_child_completion_and_notify_parent = mock_manager._process_child_completion_and_notify_parent
    manager._graph_tasks = {}
    manager._request_registry = mock_manager._request_registry
    return manager


class TestResumeMessageIdUniqueness:
    """Test suite for resume_processing_job message ID uniqueness."""

    @pytest.mark.asyncio
    async def test_child_resume_generates_unique_message_id(self, instance_manager, mock_manager):
        """Test that child resume (no old_jobs) generates a fresh message_id.

        With the new implementation, message_id comes from enqueue_message result.
        """
        old_message_id = "original-msg-123"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_running_by_instance = MagicMock(
            return_value=None
        )

        # Call resume_processing_job
        result = await instance_manager.resume_processing_job(
            "test-instance", message="resume", silent=False
        )

        # Verify enqueue_message was called
        mock_manager.enqueue_message.assert_called_once()

        # Result should have a fresh message_id
        assert result["message_id"] is not None
        assert result["message_id"] != old_message_id

        # Verify it's a valid UUID
        try:
            uuid.UUID(result["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {result['message_id']}")

    @pytest.mark.asyncio
    async def test_parent_resume_generates_unique_message_id(self, instance_manager, mock_manager):
        """Test that parent resume (has old_jobs) generates a fresh message_id and returns immediately."""
        from daemon.repositories.instance.models import InstanceStatus

        old_message_id = "original-msg-123"
        old_job = MockJob(message_id=old_message_id)

        # Root path: PAUSED/RUNNING PROCESS_MESSAGE task exists for this instance.
        mock_manager._task_repo.find_paused_or_running_by_instance = MagicMock(
            return_value=old_job
        )

        # Instance is complete (waiting_for=0)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MagicMock(
                instance_id="test-instance",
                status=InstanceStatus.RUNNING.value,
                waiting_for=0
            )
        )

        # Call resume_processing_job
        result = await instance_manager.resume_processing_job(
            "test-instance", message="resume", silent=False
        )

        # Should return immediately with "resuming" status
        assert result["status"] == "resuming"

        # Should NOT call _process_message_with_tracking synchronously
        mock_manager._process_message_with_tracking.assert_not_called()

        # Result should have a fresh message_id (not the old one)
        assert result["message_id"] is not None

        # Verify it's a valid UUID
        try:
            uuid.UUID(result["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {result['message_id']}")

    @pytest.mark.asyncio
    async def test_each_call_generates_different_message_id(self, instance_manager, mock_manager):
        """Test that each resume call generates a different message_id."""
        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_running_by_instance = MagicMock(
            return_value=None
        )

        # Configure mock to return different message_ids
        mock_manager.enqueue_message = AsyncMock(
            side_effect=[
                MockAsyncMessageResult(message_id=str(uuid.uuid4())),
                MockAsyncMessageResult(message_id=str(uuid.uuid4())),
            ]
        )
        instance_manager.enqueue_message = mock_manager.enqueue_message

        # First call
        result1 = await instance_manager.resume_processing_job(
            "test-instance", message="resume"
        )
        msg_id1 = result1["message_id"]

        # Second call
        result2 = await instance_manager.resume_processing_job(
            "test-instance", message="resume"
        )
        msg_id2 = result2["message_id"]

        # They should be different
        assert msg_id1 != msg_id2, "Each resume should generate a unique message_id"

    @pytest.mark.asyncio
    async def test_silent_mode_skips_enqueue_and_returns_none_message_id(self, instance_manager, mock_manager):
        """Test that silent=True skips enqueue and returns None for message_id.
        
        When silent=True (cascade resume), the child should NOT receive a message
        enqueued. The parent's send_message tool will deliver the actual work.
        """
        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_running_by_instance = MagicMock(
            return_value=None
        )

        # Call with silent=True
        result = await instance_manager.resume_processing_job(
            "test-instance", message="resume", silent=True
        )

        # Silent mode should NOT enqueue any message
        mock_manager.enqueue_message.assert_not_called()

        # Should return silent resume result with None message_id
        assert result["message_id"] is None
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_message_content_preserved_in_enqueue(self, instance_manager, mock_manager):
        """Test that the resume message content is passed to enqueue."""
        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_running_by_instance = MagicMock(
            return_value=None
        )

        custom_message = "continue from where we left off"

        result = await instance_manager.resume_processing_job(
            "test-instance", message=custom_message, silent=False
        )

        # Verify enqueue was called with correct message
        kwargs = mock_manager.enqueue_message.call_args[1]
        assert kwargs["message"] == custom_message

    @pytest.mark.asyncio
    async def test_source_is_cascade_resume(self, instance_manager, mock_manager):
        """Test that source is set to cascade_resume for resume messages."""
        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_running_by_instance = MagicMock(
            return_value=None
        )

        await instance_manager.resume_processing_job(
            "test-instance", message="resume"
        )

        # Verify source is cascade_resume
        kwargs = mock_manager.enqueue_message.call_args[1]
        assert kwargs["source"] == "cascade_resume"
