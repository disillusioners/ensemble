"""Tests for resume_processing_job new queue flow behavior.

The new implementation routes based on instance type:
- Child instances (no PAUSED/RUNNING PROCESS_MESSAGE Task): enqueue_message() via WorkerPool
- Root instances (has PAUSED/RUNNING PROCESS_MESSAGE Task): resume existing Task from checkpoint via _process_message_with_tracking

The waiting_for > 0 check is now handled by JobFeedbackObserver, not resume_processing_job.

Phase 2.5 (D13 / Phase 2 migration): the root-vs-child routing decision
moved off the legacy ``JobRepository.find_processing_message_jobs_by_instance``
(no MESSAGE ``JobItem`` rows exist post-D13) onto the new
``TaskRepository.find_paused_or_running_by_instance`` primitive (Task 2.5.2).
These tests mock the new primitive on ``manager._task_repo``.

Phase 3 (Increment 4, 2026-08-01): that selector is replaced by
``TaskRepository.find_paused_or_cancellable_turn`` (the
pause-cascade selector that includes PROCESS_REPORT alongside
PROCESS_MESSAGE).
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock
import logging

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.repositories.instance.models import InstanceStatus
from daemon.request_registry import ActiveRequestRegistry
from daemon.services.job_queue_service import DemandState


class MockInstanceMeta:
    """Mock instance metadata returned by _instance_repository.get."""

    def __init__(
        self,
        instance_id: str = "test-instance",
        status: str = InstanceStatus.PAUSED.value,
        waiting_for: int = 0
    ):
        self.instance_id = instance_id
        self.status = status
        self.waiting_for = waiting_for


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


class MockTask:
    """Mock task returned by the Phase 3 explicit-handle selector.

    Phase 2.5 (D13): the legacy ``MockJob`` (which simulated a ``JobItem``
    row) has been replaced by ``MockTask`` (simulating a WorkerPool ``Task``
    row). ``resume_processing_job`` now branches on the presence of a
    PAUSED/RUNNING ``PROCESS_MESSAGE`` task — pre-D13 it branched on the
    presence of a PROCESSING MESSAGE ``JobItem``.

    ``task_id`` is intentionally a string (not an int) so the legacy
    ``job_id`` assertions (``result["job_id"] == "job-..."``) keep
    working unchanged — ``resume_processing_job`` derives
    ``old_job_id = existing_task.work_id`` (the Task's stable UUID4
    cross-system handle, NOT the integer PK ``id``).

    ``work_id`` defaults to ``str(uuid.uuid4())`` so each mock has a
    unique UUID4. Tests that compare ``result["job_id"]`` against a
    known value should pass ``work_id="..."`` explicitly to pin the
    assertion to a deterministic value.
    """

    def __init__(
        self,
        task_id: str | None = None,
        task_type: str = "process_message",
        status: str = "running",
        message_id: str | None = "test-msg-456",
        # Backwards-compat: pre-D13 callers used ``job_id=`` instead of
        # ``task_id=``. Accept both so existing test bodies do not need
        # to be rewritten. ``job_id`` wins when both are supplied.
        job_id: str | None = None,
        work_id: str | None = None,
    ):
        self.id = job_id if job_id is not None else (task_id or "test-job-123")
        self.task_type = task_type
        self.instance_id = "test-instance"
        self.message_id = message_id
        self.status = status
        self.worker_id = "worker-0"
        # Stable UUID4 cross-system handle. Production
        # ``resume_processing_job`` derives ``old_job_id`` from this
        # attribute (NOT the integer PK ``id``). Tests should pin a
        # deterministic value via ``work_id="..."`` when asserting
        # ``result["job_id"]``.
        self.work_id = work_id if work_id is not None else str(uuid.uuid4())


class MockJob(MockTask):
    """Backwards-compatibility alias — old name in pre-D13 tests.

    The previous version of this test suite used ``MockJob`` to stand in
    for the ``JobItem`` returned by the removed
    ``find_processing_message_jobs_by_instance`` method. After D13 the
    equivalent primitive returns a ``Task`` row instead, so
    ``MockJob = MockTask`` keeps the existing test bodies
    (``MockJob(...)``) working without renaming every call site.
    """


@pytest.fixture
def mock_job_queue_service():
    """Create mock job queue service with repository."""
    service = MagicMock()
    service._repository = MagicMock()
    service.complete_job = AsyncMock()
    return service


@pytest.fixture
def mock_task_repository():
    """Create mock ``TaskRepository`` (Phase 3 explicit-handle selector).

    ``resume_processing_job`` routes root-vs-child by calling
    ``self._task_repo.find_paused_or_cancellable_turn(instance_id)``.
    Tests override ``find_paused_or_cancellable_turn.return_value``
    to flip the routing: a non-None ``Task`` → root path
    (checkpoint resume); ``None`` → child path (WorkerPool enqueue).

    History: prior phases also configured the Bug-A
    ``find_resume_root_candidate_by_active_job`` mock to ``None``
    so the fallback did not accidentally fire in tests
    exercising the child route. Phase 3 (Increment 4) deletes
    that heuristic.
    """
    repo = MagicMock()
    repo.find_paused_or_cancellable_turn = MagicMock(return_value=None)
    return repo


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
    # Phase 3 (Increment 4): the root-vs-child routing decision
    # moved to ``TaskRepository.find_paused_or_cancellable_turn``
    # (the pause-cascade selector that supersedes the deleted
    # ``find_paused_or_running_by_instance``).
    manager._task_repo = mock_task_repository
    # Mock enqueue_message for WorkerPool path
    manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
    # Mock _process_message_with_tracking for JobQueue path (root instances)
    manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())
    # Mock _process_child_completion_and_notify_parent
    manager._process_child_completion_and_notify_parent = AsyncMock()
    manager._graph_tasks = {}
    # W4: real registry so register()/unregister() return real
    # CancellationTokenSource. ``resume_processing_job`` calls
    # ``register`` and the resume background task calls ``unregister``
    # in its finally block.
    manager._request_registry = ActiveRequestRegistry()
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
    manager._process_message_with_tracking = mock_manager._process_message_with_tracking
    manager._process_child_completion_and_notify_parent = mock_manager._process_child_completion_and_notify_parent
    manager._graph_tasks = {}
    manager._request_registry = mock_manager._request_registry
    return manager


class TestResumeQueueFlow:
    """Test suite for new resume queue flow behavior.

    The new implementation routes based on instance type:
    - Child instances (no old_jobs): enqueue_message() via WorkerPool
    - Root instances (has old_jobs): resume from checkpoint via _process_message_with_tracking

    The waiting_for > 0 check is now handled by JobFeedbackObserver, not resume_processing_job.
    """

    @pytest.mark.asyncio
    async def test_parent_instance_with_old_jobs_resumes_from_checkpoint(
        self, instance_manager, mock_manager
    ):
        """Root instance with old_jobs should schedule background processing and return immediately."""
        instance_id = "parent-instance-123"
        job_id = "job-abc-789"
        work_id = "work-abc-789"
        message_id = "msg-xyz-456"

        # Setup: TaskRepository reports a PAUSED/RUNNING PROCESS_MESSAGE
        # task for this instance → root path (checkpoint resume). The
        # legacy ``find_processing_message_jobs_by_instance`` mock has
        # been removed; the new primitive lives on ``_task_repo``
        # (Task 2.5.2).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=MockJob(
                job_id=job_id,
                message_id=message_id,
                work_id=work_id,
            )
        )

        # Instance is complete (waiting_for=0)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=0
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Should return immediately with "resuming" status
        assert result["instance_id"] == instance_id
        # Production derives ``result["job_id"]`` from
        # ``existing_task.work_id`` (the Task's stable UUID4 cross-system
        # handle, NOT the integer PK ``id``). Compare against the pinned
        # ``work_id`` so the assertion is deterministic.
        assert result["job_id"] == work_id
        assert result["message_id"] is not None
        assert result["status"] == "resuming"

        # Should NOT call _process_message_with_tracking synchronously (it's in background)
        mock_manager._process_message_with_tracking.assert_not_called()

        # Should NOT complete the job synchronously (it's in background)
        mock_manager._job_queue_service.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_child_instance_no_old_jobs_enqueues_via_workerpool(
        self, instance_manager, mock_manager
    ):
        """Child instance (no PAUSED/RUNNING Task) with silent=False should enqueue via WorkerPool."""
        instance_id = "child-instance-456"

        # Setup: TaskRepository reports NO PAUSED/RUNNING PROCESS_MESSAGE
        # task for this instance → child path (WorkerPool enqueue).
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Child resume routes through the unified dispatcher.
        mock_manager.enqueue_message.assert_called_once()
        wp_kwargs = mock_manager.enqueue_message.call_args[1]
        assert wp_kwargs["instance_id"] == instance_id
        assert wp_kwargs["message"] == "resume"
        assert wp_kwargs["source"] == "cascade_resume"
        assert wp_kwargs["metadata"]["resume_mode"] is True

        # Return should include message_id
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is not None

    @pytest.mark.asyncio
    async def test_silent_mode_skips_enqueue(
        self, instance_manager, mock_manager
    ):
        """silent=True for child instance skips enqueue entirely."""
        instance_id = "child-instance-silent"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Silent mode should NOT enqueue any message
        mock_manager.enqueue_message.assert_not_called()
        
        # Should return silent resume result
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is None
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_non_silent_mode_passes_resume_mode_true(
        self, instance_manager, mock_manager
    ):
        """silent=False should pass resume_mode=True to enqueue (always True for resume ops)."""
        instance_id = "child-instance-non-silent"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task for this instance.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        await instance_manager.resume_processing_job(
            instance_id, message="continue working", silent=False
        )

        mock_manager.enqueue_message.assert_called_once()
        wp_kwargs = mock_manager.enqueue_message.call_args[1]
        assert wp_kwargs["metadata"]["resume_mode"] is True

    @pytest.mark.asyncio
    async def test_multiple_old_jobs_uses_first_job(
        self, instance_manager, mock_manager
    ):
        """Multiple old jobs: first one is used for resume, others are CANCELLED (W4).

        Phase 2.5 (D13): the new routing primitive returns a single Task
        row (the first PAUSED/RUNNING PROCESS_MESSAGE Task). The
        "first one is used" assertion is preserved — the post-D13 code
        derives ``old_job_id = str(existing_task.id)`` from whichever
        Task the repository returns.
        """
        instance_id = "parent-instance-multi"
        job_id_1 = "job-1-123"
        work_id_1 = "work-1-123"
        job_id_2 = "job-2-456"

        # Setup: the first PAUSED/RUNNING PROCESS_MESSAGE task for this
        # instance. (Pre-D13 mock returned a list; the new primitive
        # returns a single Task — the "extra" sibling is no longer
        # queried because the task is the canonical source of truth.)
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=MockJob(
                job_id=job_id_1,
                message_id="msg-1",
                work_id=work_id_1,
            )
        )

        # Instance is complete (waiting_for=0)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=0
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Should return immediately with "resuming" status
        assert result["status"] == "resuming"
        # ``result["job_id"]`` is the Task's ``work_id`` (production
        # contract); compare against the pinned ``work_id_1``.
        assert result["job_id"] == work_id_1  # First (only) task is used

        # Should NOT call _process_message_with_tracking synchronously
        mock_manager._process_message_with_tracking.assert_not_called()

        # Phase 2.5 (D13): the new routing primitive returns a single
        # Task row, so there are no "extra" siblings to cancel. The
        # legacy ``complete_job(job_id_2, CANCELLED)`` call that this
        # test used to assert no longer fires — the task is the
        # canonical source of truth and a single PROCESS_MESSAGE task
        # owns the resume path.
        mock_manager._job_queue_service.complete_job.assert_not_called()

        # The primary task is completed in background, not here

    @pytest.mark.asyncio
    async def test_enqueue_failure_returns_none(
        self, instance_manager, mock_manager
    ):
        """When enqueue fails, should return None."""
        instance_id = "child-instance-fail"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        # Simulate enqueue failure
        mock_manager.enqueue_message.side_effect = RuntimeError("enqueue failed")

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_parent_resume_with_waiting_for_keeps_job_processing(
        self, instance_manager, mock_manager
    ):
        """Root instance with waiting_for > 0 keeps job as PROCESSING - background task handles this."""
        instance_id = "parent-waiting-for"
        job_id = "job-waiting-123"
        work_id = "work-waiting-123"

        # Root path: PAUSED/RUNNING PROCESS_MESSAGE task exists.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=MockJob(
                job_id=job_id,
                message_id="msg-1",
                work_id=work_id,
            )
        )

        # With waiting_for > 0, job should stay PROCESSING
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.WAITING_CHILDREN.value,
                waiting_for=2
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Should return immediately with "resuming" status
        assert result["status"] == "resuming"
        # ``result["job_id"]`` is the Task's ``work_id`` (production
        # contract); compare against the pinned ``work_id``.
        assert result["job_id"] == work_id
        assert result["message_id"] is not None

        # Should NOT call _process_message_with_tracking synchronously
        mock_manager._process_message_with_tracking.assert_not_called()

        # Should NOT complete the job synchronously
        mock_manager._job_queue_service.complete_job.assert_not_called()
