"""Tests for child instance resume in resume_processing_job.

Child instances (sub-agents in a tree) don't have a PAUSED/RUNNING
PROCESS_MESSAGE ``Task`` row of their own — they use WorkerPool directly.
When resuming a child instance, the code should call
_process_message_with_tracking directly instead of looking for old jobs.

These tests verify the fix in the `if not old_jobs:` branch of resume_processing_job.

Phase 2.5 (D13 / Phase 2 migration): the routing primitive moved off
``JobRepository.find_processing_message_jobs_by_instance`` (no MESSAGE
``JobItem`` rows exist post-D13) onto
``TaskRepository.find_paused_or_running_by_instance`` (Task 2.5.2).

Phase 3 (Increment 4, 2026-08-01): that primitive is now superseded
by ``TaskRepository.find_paused_or_cancellable_turn`` (the
pause-cascade selector that includes PROCESS_REPORT alongside
PROCESS_MESSAGE).
"""

import uuid
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.cancellation import CancellationTokenSource
from daemon.repositories.instance.models import InstanceStatus


class MockInstanceMeta:
    """Mock instance metadata returned by _instance_repository.get."""

    def __init__(self, instance_id: str = "test-instance", status: str = InstanceStatus.PAUSED.value):
        self.instance_id = instance_id
        self.status = status


class MockMessageResult:
    """Mock message result returned by _process_message_with_tracking."""

    def __init__(self, content: str = "Resume completed"):
        self.content = content


class MockAsyncMessageResult:
    """Mock async message result returned by enqueue_message."""

    def __init__(self, message_id: str = None):
        self.message_id = message_id or str(uuid.uuid4())
        self.instance_id = None
        self.status = "queued"


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
    return repo


@pytest.fixture
def mock_instance_repository():
    """Create mock instance repository."""
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_task_repository():
    """Create mock ``TaskRepository`` (Phase 3 explicit-handle selector).

    ``resume_processing_job`` calls
    :meth:`TaskRepository.find_paused_or_cancellable_turn` to
    decide root-vs-child routing. The fixture pre-configures it to
    return ``None`` so the active-orphan / root path does NOT
    fire in tests that exercise the child route.

    History: prior phases also configured the Bug-A
    ``find_resume_root_candidate_by_active_job`` mock. Phase 3
    (Increment 4) deletes that heuristic.
    """
    repo = MagicMock()
    repo.find_paused_or_cancellable_turn = MagicMock(return_value=None)
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
    # Phase 3 (Increment 4): routing primitive moves to
    # ``TaskRepository.find_paused_or_cancellable_turn`` (the
    # replacement for the deleted
    # ``find_paused_or_running_by_instance``).
    manager._task_repo = mock_task_repository
    # Mock enqueue_message (used by both WorkerPool and JobQueue paths)
    manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
    manager._graph_tasks = {}
    return manager


@pytest.fixture
def instance_manager(mock_manager):
    """Create InstanceManager with mocked dependencies."""
    config = MagicMock(spec=Config)
    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = mock_manager._job_queue_service
    manager._queue_repository = mock_manager._queue_repository
    manager._instance_repository = mock_manager._instance_repository
    manager._task_repo = mock_manager._task_repo
    manager.enqueue_message = mock_manager.enqueue_message
    manager._graph_tasks = {}
    return manager


class TestChildInstanceResume:
    """Test suite for child instance resume behavior."""

    @pytest.mark.asyncio
    async def test_child_resume_non_silent_target_resume(self, instance_manager, mock_manager):
        """Scenario 1: Child instance resume (non-silent / target resume).

        Verify enqueue_message is called with correct args
        when resuming a child instance with silent=False.
        """
        instance_id = "child-instance-123"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        # Instance meta exists with PAUSED status
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.PAUSED.value)
        )

        # Call resume_processing_job with silent=False
        result = await instance_manager.resume_processing_job(
            instance_id, message="continue working", silent=False
        )

        mock_manager.enqueue_message.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_child_resume_silent_cascade_resume(self, instance_manager, mock_manager):
        """Scenario 2: Child instance resume (silent / cascade resume).

        When silent=True, the child should NOT receive a message enqueued.
        The parent's send_message tool will deliver the actual work.
        """
        instance_id = "child-instance-456"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        # Instance meta exists with RUNNING status
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.RUNNING.value)
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Silent mode should NOT enqueue any message
        mock_manager.enqueue_message.assert_not_called()

        # Verify return value indicates silent resume
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is None
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_child_resume_silent_mode_no_enqueue(self, instance_manager, mock_manager):
        """Scenario 3: Silent mode (cascade resume) skips enqueue entirely.

        When silent=True, the child should NOT receive a message enqueued,
        so there's no enqueue to fail. This tests the silent path.
        """
        instance_id = "child-instance-789"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance
        # (Phase 2.5 / D13 — see module docstring).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Silent mode should NOT call enqueue_message
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Should return silent resume result (no enqueue)
        assert result["instance_id"] == instance_id
        assert result["status"] == "silent_resume"
        assert result["message_id"] is None

    @pytest.mark.asyncio
    async def test_child_resume_general_exception_raised(self, instance_manager, mock_manager):
        """Scenario 4: General exception from enqueue is caught and returns None.

        For non-silent mode, exceptions from enqueue_message should be caught.
        """
        instance_id = "child-instance-error"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance
        # (Phase 2.5 / D13 — see module docstring).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Simulate RuntimeError from enqueue (non-silent mode)
        mock_manager.enqueue_message.side_effect = RuntimeError("something broke")

        # Non-silent mode: exception should be caught and return None
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_child_resume_instance_not_found(self, instance_manager, mock_manager):
        """Scenario 5: Instance not found (meta is None).

        Verify the code handles missing instance metadata gracefully.
        For non-silent mode, it should still call enqueue_message.
        """
        instance_id = "nonexistent-instance"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance
        # (Phase 2.5 / D13 — see module docstring).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        # Instance meta is None
        mock_manager._instance_repository.get = MagicMock(return_value=None)

        # Non-silent mode: should still call enqueue_message
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        mock_manager.enqueue_message.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_child_resume_unexpected_state(self, instance_manager, mock_manager):
        """Scenario 6: Instance in unexpected state.

        Verify the code still proceeds when instance is in a state other than
        PAUSED or RUNNING (the new implementation doesn't check state).
        For non-silent mode, it should still call enqueue_message.
        """
        instance_id = "completed-instance"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance
        # (Phase 2.5 / D13 — see module docstring).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        # Instance meta with COMPLETED status (unexpected)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status="COMPLETED")
        )

        # Non-silent mode: should still proceed
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        mock_manager.enqueue_message.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_child_resume_fresh_uuid_each_call(self, instance_manager, mock_manager):
        """Scenario 7: Fresh UUID generated for message_id.

        Verify that each call to resume_processing_job generates a different message_id.
        The message_id is generated by enqueue_message, not by resume_processing_job.
        """
        instance_id = "child-instance-duplicate"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance
        # (Phase 2.5 / D13 — see module docstring).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Configure mock to return different message_ids on each call
        mock_manager.enqueue_message = AsyncMock(
            side_effect=[
                MockAsyncMessageResult(message_id=str(uuid.uuid4())),
                MockAsyncMessageResult(message_id=str(uuid.uuid4())),
            ]
        )
        instance_manager.enqueue_message = mock_manager.enqueue_message

        result1 = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )
        result2 = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        mock_manager.enqueue_message.assert_not_called()
        assert result1 is None
        assert result2 is None

    @pytest.mark.asyncio
    async def test_child_resume_calls_enqueue_message(self, instance_manager, mock_manager):
        """Scenario 8: Verify enqueue_message is called (not _process_message_with_tracking).

        This test confirms the new implementation uses the normal queue flow.
        """
        instance_id = "child-instance-token"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance
        # (Phase 2.5 / D13 — see module docstring).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Verify enqueue_message was called (not _process_message_with_tracking)
        mock_manager.enqueue_message.assert_called_once()
        call_kwargs = mock_manager.enqueue_message.call_args.kwargs

        # Verify the metadata includes resume_mode
        assert "metadata" in call_kwargs
        assert "resume_mode" in call_kwargs["metadata"]


