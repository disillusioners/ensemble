"""Tests for resume_processing_job new queue flow behavior.

With the new implementation:
1. Child instances (no PAUSED/RUNNING task): enqueue_message() via WorkerPool
2. Root instances (has PAUSED/RUNNING task): resume from checkpoint via _process_message_with_tracking

The _process_child_completion_and_notify_parent is now called directly by resume_processing_job
for root instances, after processing completes successfully.

Phase 2.5 (D13 / Phase 2 migration): the routing primitive moved off
``JobRepository.find_processing_message_jobs_by_instance`` onto
``TaskRepository.find_paused_or_running_by_instance`` (Task 2.5.2).

Phase 3 (Increment 4, 2026-08-01): that primitive is deleted.
The replacement selector ``find_paused_or_cancellable_turn``
includes PROCESS_REPORT and PROCESS_MESSAGE; answer-gate routing
uses ``find_suspended_turn_for_answer`` against the persisted
``suspension_reason`` / ``resume_target_turn_id`` handle (see
``daemon/services/turn_transitions.py`` for the SUSPEND_TURN /
RESUME_TURN extensions).
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.cancellation import CancellationTokenSource
from daemon.repositories.instance.models import InstanceStatus
from daemon.request_registry import ActiveRequestRegistry


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


class MockJob:
    """Mock ``Task`` row returned by the Phase 3 explicit-handle selector.

    Phase 2.5 (D13): the legacy ``MockJob`` (which simulated a
    ``JobItem`` row) now stands in for a ``Task`` row instead. The
    ``.id`` attribute matches what ``resume_processing_job`` reads
    (``existing_task.id``) — the new routing primitive returns a Task
    row, not a JobItem.

    Phase 3 (Increment 4): ``MockJob`` is now the candidate returned
    by ``find_paused_or_cancellable_turn`` (the pause-cascade
    selector that supersedes
    ``find_paused_or_running_by_instance``).
    """

    def __init__(self, job_id: str = "test-job-123", message_id: str = "test-msg-456"):
        # Post-D13 ``Task`` attributes.
        self.id = job_id
        self.work_id = job_id  # Stable UUID4 string used by ``resume_processing_job``
        self.task_type = "process_message"
        self.instance_id = "test-instance"
        self.message_id = message_id
        self.status = "running"
        self.worker_id = "worker-0"
        # Pre-D13 ``JobItem`` attributes (kept for backwards-compat).
        self.job_id = job_id
        self.job_metadata = {"message_id": message_id}


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
    """Create mock ``TaskRepository`` (Phase 2.5 / D13 routing primitive;
    Phase 3 routing moved to explicit turn handles — Increment 4).

    ``resume_processing_job`` calls
    :meth:`TaskRepository.find_paused_or_cancellable_turn` to
    decide root-vs-child routing. Default behavior: returns
    ``None`` (no paused/cancellable turn detected) so the resume
    routes through the child branch — matching the typical case
    where the resume does NOT trigger the root path.

    History: prior phases exposed the inference-based
    ``find_paused_or_running_by_instance`` selector and the
    Bug-A ``find_resume_root_candidate_by_active_job``
    heuristic. Phase 3 (Increment 4) deletes both; routing is
    now driven by the explicit
    ``suspension_reason`` / ``resume_target_turn_id`` handle
    persisted at SUSPEND_TURN time.
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
    # ``TaskRepository.find_paused_or_cancellable_turn``
    # (the replacement for the deleted
    # ``find_paused_or_running_by_instance``).
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


class TestChildNotificationWorkerPoolPath:
    """Test suite for WorkerPool path (child instances) in new queue flow."""

    @pytest.mark.asyncio
    async def test_child_enqueues_via_workerpool(self, instance_manager, mock_manager):
        """Child instance (no old_jobs) should enqueue via WorkerPool."""
        instance_id = "child-instance-123"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Child resume routes through the unified dispatcher.
        mock_manager.enqueue_message.assert_called_once()
        kwargs = mock_manager.enqueue_message.call_args[1]

        # Verify enqueue was called with correct args
        assert kwargs["instance_id"] == instance_id
        assert kwargs["message"] == "resume"
        assert kwargs["source"] == "cascade_resume"
        assert kwargs["metadata"]["resume_mode"] is True

        # Verify return
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is not None

    @pytest.mark.asyncio
    async def test_child_silent_mode_skips_enqueue(self, instance_manager, mock_manager):
        """Silent mode for child instance skips enqueue entirely.
        
        When silent=True (cascade resume), the child should NOT receive a message
        enqueued. The parent's send_message tool will deliver the actual work.
        """
        instance_id = "child-instance-silent"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Silent mode should NOT enqueue any message
        mock_manager.enqueue_message.assert_not_called()
        
        # Should return a silent resume result
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is None
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_child_enqueues_message_id_from_enqueue(self, instance_manager, mock_manager):
        """message_id should come from enqueue_message result."""
        instance_id = "child-instance-msgid"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        # Configure mock to return specific message_id
        expected_msg_id = str(uuid.uuid4())
        mock_manager.enqueue_message = AsyncMock(
            return_value=MockAsyncMessageResult(message_id=expected_msg_id)
        )
        instance_manager.enqueue_message = mock_manager.enqueue_message

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        assert result["message_id"] == expected_msg_id


class TestChildNotificationJobQueuePath:
    """Test suite for JobQueue path (root instances) in new queue flow."""

    @pytest.mark.asyncio
    async def test_parent_resumes_from_checkpoint(self, instance_manager, mock_manager):
        """Root instance (PAUSED/RUNNING PROCESS_MESSAGE Task) should schedule background processing and return immediately."""
        instance_id = "parent-instance-123"
        job_id = "job-abc-789"

        # Root path: PAUSED/RUNNING PROCESS_MESSAGE task exists.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=MockJob(job_id=job_id, message_id="msg-456")
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
            instance_id, message="resume", silent=False
        )

        # Should return immediately with "resuming" status
        assert result["instance_id"] == instance_id
        assert result["job_id"] == job_id
        assert result["message_id"] is not None
        assert result["status"] == "resuming"

        # Should NOT call _process_message_with_tracking synchronously (it's in background)
        mock_manager._process_message_with_tracking.assert_not_called()

        # Should NOT complete the job synchronously (it's in background)
        mock_manager._job_queue_service.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_parent_silent_mode_resumes_checkpoint(self, instance_manager, mock_manager):
        """Silent mode should schedule background processing with empty message."""
        instance_id = "parent-instance-silent"
        job_id = "job-xyz-789"

        # Root path: PAUSED/RUNNING PROCESS_MESSAGE task exists.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=MockJob(job_id=job_id, message_id="msg-456")
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
            instance_id, message="resume", silent=True
        )

        # Should return immediately with "resuming" status
        assert result["status"] == "resuming"

        # Should NOT call _process_message_with_tracking synchronously
        mock_manager._process_message_with_tracking.assert_not_called()


class TestChildNotificationErrorHandling:
    """Test suite for error handling in new queue flow."""

    @pytest.mark.asyncio
    async def test_workerpool_enqueue_failure_returns_none(self, instance_manager, mock_manager):
        """When enqueue_message fails, should return None."""
        instance_id = "child-instance-fail"

        # Child path: no PAUSED/RUNNING PROCESS_MESSAGE task.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        mock_manager.enqueue_message.side_effect = RuntimeError("enqueue failed")

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_jobqueue_process_failure_returns_none(self, instance_manager, mock_manager):
        """When _process_message_with_tracking fails, the background task handles it gracefully.

        Note: Since processing now happens in background, we can't test failure directly
        through resume_processing_job. The error handling is done in _resume_processing_background.
        """
        instance_id = "parent-instance-fail"
        job_id = "job-123"

        # Root path: PAUSED/RUNNING PROCESS_MESSAGE task exists.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=MockJob(job_id=job_id, message_id="msg-456")
        )

        # The test verifies that resume_processing_job returns "resuming" immediately
        # Error handling happens in _resume_processing_background
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Should return immediately with "resuming" status
        assert result["status"] == "resuming"

        # Should NOT call _process_message_with_tracking synchronously
        mock_manager._process_message_with_tracking.assert_not_called()


# ─── Phase 1 Bug A / Step B: Manager-level routing tests ─────────────────────


class MockTerminalTask:
    """Mock Task row used by phase-3 explicit-handle tests.

    History: previously returned by the Bug-A
    ``find_resume_root_candidate_by_active_job`` heuristic. Phase 3
    (Increment 4) deletes that heuristic; the explicit
    ``suspension_reason`` / ``resume_target_turn_id`` handle
    declared at SUSPEND_TURN time is now the authoritative resume
    routing input. The class is retained for the resumed-task test
    fixtures in this file (e.g. a Task object that resumes routes
    to ROOT).
    """

    def __init__(
        self,
        work_id: str = "terminal-task-work-id",
        status: str = "completed",
        task_id: int = 42,
        suspension_reason: str | None = None,
        resume_target_turn_id: str | None = None,
    ):
        self.work_id = work_id
        self.status = status
        self.id = task_id
        self.task_type = "process_message"
        self.instance_id = "test-instance"
        self.message_id = "msg-original"
        self.suspension_reason = suspension_reason
        self.resume_target_turn_id = resume_target_turn_id


class TestSuspendedTurnForAnswerRouting:
    """Manager-level routing tests for Phase 3 explicit-handle routing.

    ``resume_processing_job`` calls
    :meth:`TaskRepository.find_paused_or_cancellable_turn` to decide
    root-vs-child. The previous ``find_resume_root_candidate_by_
    active_job`` heuristic is removed; the explicit
    ``suspension_reason`` / ``resume_target_turn_id`` handle
    persisted at SUSPEND_TURN time is the authoritative routing
    input.

    These tests mock the graph/queue dependencies but exercise the
    real ``resume_processing_job`` routing logic.
    """

    @pytest.mark.asyncio
    async def test_no_suspended_turn_routes_child(
        self, instance_manager, mock_manager
    ):
        """No paused/cancellable turn → child branch.

        When the pause-cascade selector returns ``None``, the
        resume routes to the child branch via WorkerPool
        (existing behavior, preserved).
        """
        instance_id = "ordinary-child-instance"

        # No paused/cancellable turn.
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Routes to child — enqueues via WorkerPool.
        mock_manager.enqueue_message.assert_called_once()
        assert result["status"] == "queued"
        assert result["job_id"] is None

    @pytest.mark.asyncio
    async def test_paused_cancellable_turn_routes_root(
        self, instance_manager, mock_manager
    ):
        """``find_paused_or_cancellable_turn`` returns a Task → ROOT.

        When the pause-cascade selector returns a Task (the
        normal pause-then-resume case), the resume routes to the
        ROOT branch via ``_resume_processing_background``.
        """
        instance_id = "regular-paused-instance"
        existing_job_id = "existing-job-xyz"

        # Cancellable turn (PAUSED/RUNNING) — root-route.
        existing_task = MockJob(job_id=existing_job_id)
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=existing_task
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Routes to ROOT via the pause-cascade selector.
        assert result["status"] == "resuming"
        assert result["job_id"] == existing_job_id

    @pytest.mark.asyncio
    async def test_paused_during_report_turn_routes_root(
        self, instance_manager, mock_manager
    ):
        """PROCESS_REPORT pause → ROOT branch (Bug A incident regression).

        After the pause-during-report-turn fix (Phase 3, Increment 4),
        a PAUSED/RUNNING PROCESS_REPORT task is treated as a root
        candidate by ``find_paused_or_cancellable_turn`` (it now
        includes PROCESS_REPORT alongside PROCESS_MESSAGE). The
        manager routes to ROOT — NOT child — even though no
        PROCESS_MESSAGE task exists on the instance. This is the
        regression test for the historical Bug A incident state.
        """
        instance_id = "report-paused-instance"
        report_work_id = "report-turn-work-id"

        report_task = MockTerminalTask(
            work_id=report_work_id,
            status="paused",
            task_id=77,
            task_type="process_report" if hasattr(MockTerminalTask, "task_type") else "process_report",
        )
        # task_type is hardcoded in MockTerminalTask; override here
        # so this case reflects a paused PROCESS_REPORT row.
        report_task.task_type = "process_report"
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=report_task
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Routes to ROOT via the report turn's work_id — the
        # explicit-handle routing fix for Bug A.
        mock_manager.enqueue_message.assert_not_called()
        assert result["status"] == "resuming"
        assert result["job_id"] == report_work_id

    @pytest.mark.asyncio
    async def test_concurrent_resume_dedup_on_root_path(
        self, instance_manager, mock_manager
    ):
        """Concurrent graph_tasks dedup works on the explicit-handle ROOT path.

        If the resumable turn is already being resumed in
        ``_graph_tasks`` (another concurrent resume is in flight),
        the ROOT path MUST deduplicate (return ``already_resuming``)
        rather than start a second graph turn. This is the W4 case 5
        dedup invariant preserved after the heuristic removal.
        """
        instance_id = "concurrent-resume-instance"
        existing_work_id = "paused-task-concurrent"

        # Pause-cascade selector returns a Task.
        existing_task = MockJob(job_id=existing_work_id)
        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=existing_task
        )

        # Pre-populate _graph_tasks with an in-flight resume.
        existing_graph_task = MagicMock()
        existing_graph_task.done = MagicMock(return_value=False)
        instance_manager._graph_tasks[instance_id] = existing_graph_task

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Dedup kicks in BEFORE the routing decision.
        assert result["status"] == "already_resuming"
        assert result["job_id"] == existing_work_id

    @pytest.mark.asyncio
    async def test_silent_cascade_resume_child_returns_silent_resume(
        self, instance_manager, mock_manager
    ):
        """W4 case 4: silent cascade-resume for a child remains a no-op.

        A cascade-resume with ``silent=True`` for an ordinary child
        (no paused/cancellable turn) must still return
        ``silent_resume`` without enqueuing any message.
        """
        instance_id = "silent-child-instance"

        mock_manager._task_repo.find_paused_or_cancellable_turn = MagicMock(
            return_value=None
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Silent cascade-resume: no enqueue, no JobItem mirror.
        mock_manager.enqueue_message.assert_not_called()
        assert result["status"] == "silent_resume"
        assert result["job_id"] is None
        assert result["message_id"] is None
