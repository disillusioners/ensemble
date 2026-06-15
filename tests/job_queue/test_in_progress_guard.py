"""Tests for the 'job event progress label' / in_progress guard feature.

This feature changes job event notifications: when a job's instance completes
but is still waiting for child agent reports (waiting_for > 0), the notification
says "in progress ⟳" with "Progress:" text instead of "completed ✓" with
"Result:" text.

Tests cover the 6 distinct code paths the feature touches:

1. JobFeedbackObserver._process_event() — emits in_progress when waiting_for>0
2. JobProcessor orphan watchdog — same in_progress guard
3. JobQueueService.notify_watchers() — formats in_progress vs terminal differently
4. JobQueueService.notify_watchers() — only cleans up watches on terminal states
5. MessageJobHandler.handle() — emits in_progress when skip_complete=True
6. Watcher filter — in_progress is opt-in via watch_events list

Reviewer fixes also covered:
7. JobProcessor._emit_in_progress_if_children_pending() — throttle/dedup, escape
   hatch, terminal lifecycle (tests added in TestJobProcessorInProgressGuardReviewFixes)
8. ChildReportsService cascade — ERROR status preserved when all children finish
   (test added in TestCascadePreservesErrorOnChildComplete)
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.watcher_models import (
    ALL_TERMINAL_STATES,
    ALL_WATCHABLE_EVENTS,
)
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService, DemandState
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_processor import JobProcessor
from daemon.services.message_job_handler import MessageJobHandler
from daemon.manager import MessageResult


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────


def make_mock_job(
    job_id: str = "job-12345678-1234-1234-1234-123456789abc",
    status: str = "processing",
    instance_id: str = "instance-456",
    agent_id: str = "coder",
) -> MagicMock:
    """Create a MagicMock(spec=JobItem) with the given attributes."""
    mock_job = MagicMock(spec=JobItem)
    mock_job.job_id = job_id
    mock_job.status = status
    mock_job.instance_id = instance_id
    mock_job.agent_id = agent_id
    mock_job.result_summary = "Test job completed"
    mock_job.error_message = None
    mock_job.project_id = "test-project"
    mock_job.queue_id = "system_fifo_queue"
    return mock_job


def make_instance_meta(
    instance_id: str = "instance-456",
    status: str = "completed",
    waiting_for: int = 0,
) -> MagicMock:
    """Create a mock instance metadata record with waiting_for attribute."""
    meta = MagicMock()
    meta.instance_id = instance_id
    meta.status = status
    meta.waiting_for = waiting_for
    return meta


# ──────────────────────────────────────────────────────────────────────────────
# Common fixtures (mirrors test_jober_watch_integration.py)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel
    from daemon.repositories.job_queue.watcher_models import JobWatcher

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    JobWatcher.metadata.create_all(eng)  # ensure watcher table
    yield eng
    eng.dispose()


@pytest.fixture
def job_repo(engine):
    return JobRepository(engine)


@pytest.fixture
def lock_repo(engine):
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo):
    return JobLockManager(lock_repo=lock_repo)


@pytest.fixture
def queue_repo(engine):
    from daemon.repositories.job_queue.queue_repository import JobQueueRepository

    repo = JobQueueRepository(engine)
    repo.create(
        project_id="test-project",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    return repo


@pytest.fixture
def watcher_repo(engine):
    return JobWatcherRepository(engine)


@pytest.fixture
def instance_manager():
    manager = MagicMock()
    manager.enqueue_message = AsyncMock(return_value=MagicMock(message_id="msg-123"))
    manager.terminate_instance = AsyncMock(return_value=True)
    manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")
    return manager


@pytest.fixture
def job_queue_service(job_repo, lock_manager, queue_repo, instance_manager, watcher_repo):
    service = JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=queue_repo,
        instance_manager=instance_manager,
    )
    service.set_watcher_repo(watcher_repo)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    service.set_event_loop(loop)
    return service


# ══════════════════════════════════════════════════════════════════════════════
# 1. JobFeedbackObserver._process_event() guard
# ══════════════════════════════════════════════════════════════════════════════


class TestJobFeedbackObserverWaitingForGuard:
    """Tests that JobFeedbackObserver defers job completion when waiting_for>0."""

    def _make_observer(
        self,
        mock_job,
        waiting_for: int,
    ) -> tuple[JobFeedbackObserver, MagicMock, MagicMock, MagicMock]:
        """Build a JobFeedbackObserver with a real instance_meta stub."""
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)

        # instance_manager must have a synchronous _instance_repository.get
        # that returns a meta with `waiting_for`. The source uses
        # asyncio.to_thread() around this call.
        instance_meta = make_instance_meta(waiting_for=waiting_for)
        mock_instance_manager = MagicMock()
        mock_instance_manager._instance_repository.get = MagicMock(
            return_value=instance_meta
        )
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
            return_value="partial child response"
        )

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        return observer, mock_job_queue_service, mock_job_repo, mock_instance_manager

    @pytest.mark.asyncio
    async def test_completed_with_waiting_for_emits_in_progress(self):
        """Test A: waiting_for > 0 + status=completed → in_progress, not completed."""
        mock_job = make_mock_job(status="processing")
        observer, mock_jqs, mock_jrepo, mock_im = self._make_observer(
            mock_job, waiting_for=2
        )

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            },
        }
        await observer._process_event(event)

        # atomic_transition must NOT have been called (no job completion)
        mock_jrepo.atomic_transition.assert_not_called()

        # notify_watchers must have been called once with in_progress
        mock_jqs.notify_watchers.assert_called_once()
        call = mock_jqs.notify_watchers.call_args
        # job_id is positional; status is a kwarg in the source
        assert call.args[0] == mock_job.job_id
        assert call.kwargs.get("status") == "in_progress"
        # waiting_for must be passed through
        assert call.kwargs.get("waiting_for") == 2
        # progress text is passed
        assert call.kwargs.get("progress") == "partial child response"

    @pytest.mark.asyncio
    async def test_error_with_waiting_for_emits_in_progress(self):
        """waiting_for > 0 + status=error → also in_progress (not failed)."""
        mock_job = make_mock_job(status="processing")
        observer, mock_jqs, mock_jrepo, _ = self._make_observer(
            mock_job, waiting_for=3
        )

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "error",
                "error": "boom",
            },
        }
        await observer._process_event(event)

        mock_jrepo.atomic_transition.assert_not_called()
        mock_jqs.notify_watchers.assert_called_once()
        assert mock_jqs.notify_watchers.call_args.kwargs.get("status") == "in_progress"
        assert mock_jqs.notify_watchers.call_args.kwargs.get("waiting_for") == 3

    @pytest.mark.asyncio
    async def test_completed_with_no_waiting_runs_normal_path(self):
        """Test B: waiting_for == 0 → normal completion path, notify 'completed'."""
        mock_job = make_mock_job(status="processing")
        observer, mock_jqs, mock_jrepo, _ = self._make_observer(
            mock_job, waiting_for=0
        )

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            },
        }
        await observer._process_event(event)

        # Normal completion path: atomic_transition to COMPLETED
        mock_jrepo.atomic_transition.assert_called_once()
        kwargs = mock_jrepo.atomic_transition.call_args.kwargs
        assert kwargs["to_status"] == JobStatus.COMPLETED.value
        assert kwargs["from_status"] == JobStatus.PROCESSING.value

        # notify_watchers was called with "completed" (terminal), not in_progress.
        # The normal-completion path passes status as a positional arg
        # (see daemon/services/job_feedback_observer.py line 314).
        mock_jqs.notify_watchers.assert_called_once()
        call = mock_jqs.notify_watchers.call_args
        # call.args[0] is job_id, call.args[1] is "completed"
        assert call.args[1] == "completed"

    @pytest.mark.asyncio
    async def test_waiting_for_none_treated_as_zero(self):
        """If waiting_for attribute is missing/None → treat as 0, run normal path."""
        mock_job = make_mock_job(status="processing")
        observer, mock_jqs, mock_jrepo, mock_im = self._make_observer(
            mock_job, waiting_for=0
        )
        # Force getattr to return None
        mock_im._instance_repository.get.return_value.waiting_for = None

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            },
        }
        await observer._process_event(event)

        # Normal path runs because wf=0
        mock_jrepo.atomic_transition.assert_called_once()
        assert mock_jqs.notify_watchers.call_args.args[1] == "completed"


# ══════════════════════════════════════════════════════════════════════════════
# 2. JobProcessor orphan watchdog in_progress guard
# ══════════════════════════════════════════════════════════════════════════════


class TestJobProcessorOrphanWatchdogWaitingForGuard:
    """Tests for the in_progress guard in JobProcessor._process_next_job()."""

    def _build_processor(self, mock_queue_service, mock_instance_manager, waiting_for):
        mock_project_repo = MagicMock()
        mock_project_repo.list_projects = MagicMock(return_value=[])

        mock_queue_repo = MagicMock()
        mock_queue_repo.list_by_project = MagicMock(return_value=[])

        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )
        # Inject a controllable instance_meta via the manager
        processor._instance_manager = mock_instance_manager
        return processor

    @staticmethod
    def _make_proc_job(job_type: str = "message", instance_id: str = "instance-456"):
        job = MagicMock()
        job.job_id = "job-abc"
        job.agent_id = "coder"
        job.message = "test"
        job.source = "api"
        job.project_id = "test-project"
        job.queue_id = "system_parallel_queue"
        job.status = JobStatus.PROCESSING.value
        job.instance_id = instance_id
        job.job_type = job_type
        return job

    def _setup_mocks(self, job, instance_status, waiting_for):
        """Configure mocks for a single-queue, single-processing-job scenario."""
        from daemon.repositories.job_queue.queue_repository import JobQueueRepository
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        # Build a tiny fresh in-memory engine for the queue repo
        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(eng)
        queue_repo = JobQueueRepository(eng)
        queue_repo.create(
            project_id="test-project",
            queue_name="system_parallel_queue",
            queue_type="parallel",
            concurrency_limit=3,
            is_system=True,
        )

        # Build a mock queue returned by list_by_project
        mock_queue = MagicMock()
        mock_queue.queue_id = queue_repo.list_by_project("test-project")[0].queue_id
        mock_queue.queue_name = "system_parallel_queue"
        mock_queue.is_paused = False
        mock_queue.queue_type = "parallel"
        mock_queue.project_id = "test-project"

        mock_project = MagicMock()
        mock_project.project_id = "test-project"
        mock_project.job_queue_paused = False

        mock_project_repo = MagicMock()
        mock_project_repo.list_projects = MagicMock(return_value=[mock_project])
        queue_repo.list_by_project = MagicMock(return_value=[mock_queue])

        # Set up queue_service
        mock_queue_service = MagicMock()
        mock_queue_service._repository = MagicMock()
        # No pending jobs (we want the orphan branch to fire)
        mock_queue_service._repository.list_pending_by_queue = MagicMock(return_value=[])
        mock_queue_service._repository.list_by_queue = MagicMock(
            return_value=([job], None)
        )
        mock_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_queue_service.complete_job = AsyncMock(return_value=None)

        # Set up instance manager
        mock_instance_manager = MagicMock()
        # _capture_result_summary uses _get_last_assistant_message_raw
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
            return_value="partial work"
        )

        # The orphan branch calls _instance_repository.get
        instance_meta = make_instance_meta(
            instance_id=job.instance_id,
            status=instance_status,
            waiting_for=waiting_for,
        )
        mock_instance_manager._instance_repository = MagicMock()
        mock_instance_manager._instance_repository.get = MagicMock(
            return_value=instance_meta
        )
        mock_instance_manager.get_instance = AsyncMock(
            side_effect=KeyError("not in memory")
        )

        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=queue_repo,
            poll_interval=0.1,
        )
        return processor, mock_queue_service, mock_instance_manager, queue_repo

    @pytest.mark.asyncio
    async def test_message_job_completed_with_waiting_for_emits_in_progress(self):
        """Test A: MESSAGE job, status=COMPLETED, waiting_for>0 → in_progress, no complete_job."""
        job = self._make_proc_job(job_type="message", instance_id="inst-1")
        processor, mock_qs, mock_im, _ = self._setup_mocks(
            job, instance_status="completed", waiting_for=2
        )

        await processor._process_next_job()

        # notify_watchers was called with in_progress
        mock_qs.notify_watchers.assert_called_once()
        call = mock_qs.notify_watchers.call_args
        # job_id is positional; status is a kwarg in the source
        assert call.args[0] == job.job_id
        assert call.kwargs.get("status") == "in_progress"
        assert call.kwargs.get("waiting_for") == 2

        # complete_job was NOT called
        mock_qs.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_job_completed_with_waiting_for_emits_in_progress(self):
        """Test B: TASK job, status=COMPLETED, waiting_for>0 → in_progress, no complete_job."""
        job = self._make_proc_job(job_type="task", instance_id="inst-2")
        processor, mock_qs, mock_im, _ = self._setup_mocks(
            job, instance_status="completed", waiting_for=1
        )

        await processor._process_next_job()

        mock_qs.notify_watchers.assert_called_once()
        assert mock_qs.notify_watchers.call_args.kwargs.get("status") == "in_progress"
        assert mock_qs.notify_watchers.call_args.kwargs.get("waiting_for") == 1
        mock_qs.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_job_completed_no_waiting_runs_normal_completion(self):
        """Test C (negative): MESSAGE job, status=COMPLETED, waiting_for=0 → complete_job(✓)."""
        job = self._make_proc_job(job_type="message", instance_id="inst-3")
        processor, mock_qs, mock_im, _ = self._setup_mocks(
            job, instance_status="completed", waiting_for=0
        )

        await processor._process_next_job()

        # Should complete the job (normal path)
        mock_qs.complete_job.assert_called_once()
        kwargs = mock_qs.complete_job.call_args.kwargs
        assert kwargs["demand_state"] == DemandState.COMPLETED

        # Should NOT emit an in_progress notification
        mock_qs.notify_watchers.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 3. notify_watchers() formatting
# ══════════════════════════════════════════════════════════════════════════════


class TestNotifyWatchersFormatting:
    """Tests for the [JOB_EVENT] message format produced by notify_watchers()."""

    @pytest.mark.asyncio
    async def test_in_progress_format(self, job_queue_service, watcher_repo, instance_manager):
        """Test A: in_progress status → 'in progress ⟳', 'Progress:', 'Waiting for: N child agent(s)'."""
        job = make_mock_job()
        watcher_repo.add_watch(job.job_id, "watcher-1")
        job_queue_service._repository.get = MagicMock(return_value=job)

        await job_queue_service.notify_watchers(
            job.job_id, "in_progress", progress="Doing stuff", waiting_for=3
        )

        assert instance_manager.enqueue_message.called
        call_args = instance_manager.enqueue_message.call_args[1]
        msg = call_args["message"]

        assert "[JOB_EVENT]" in msg
        assert "in progress ⟳" in msg
        assert "Progress:" in msg
        assert "Doing stuff" in msg
        assert "Waiting for: 3 child agent(s)" in msg

        # Negative: in_progress must NOT carry terminal fields
        assert "Result:" not in msg
        assert "Error:" not in msg

        # Source tag must include the status
        assert call_args["source"].endswith(f":in_progress")

    @pytest.mark.asyncio
    async def test_completed_format(self, job_queue_service, watcher_repo, instance_manager):
        """Test B: completed → 'completed ✓', 'Result:'. No progress, no waiting_for."""
        job = make_mock_job()
        job.result_summary = "All done"
        watcher_repo.add_watch(job.job_id, "watcher-1")
        job_queue_service._repository.get = MagicMock(return_value=job)

        await job_queue_service.notify_watchers(job.job_id, "completed")

        msg = instance_manager.enqueue_message.call_args[1]["message"]
        assert "completed ✓" in msg
        assert "Result:" in msg
        assert "All done" in msg

        # Negative
        assert "Progress:" not in msg
        assert "Waiting for:" not in msg
        assert "in progress" not in msg

    @pytest.mark.asyncio
    async def test_failed_format(self, job_queue_service, watcher_repo, instance_manager):
        """Test C: failed → 'failed ✗', 'Error: boom'."""
        job = make_mock_job()
        job.error_message = "boom"
        job.result_summary = None  # failed notifications must omit Result when no summary
        watcher_repo.add_watch(job.job_id, "watcher-1")
        job_queue_service._repository.get = MagicMock(return_value=job)

        await job_queue_service.notify_watchers(job.job_id, "failed", error="boom")

        msg = instance_manager.enqueue_message.call_args[1]["message"]
        assert "failed ✗" in msg
        assert "Error: boom" in msg
        # No result line when result_summary is None
        assert "Result:" not in msg
        # No progress terminology
        assert "Progress:" not in msg
        assert "Waiting for:" not in msg


# ══════════════════════════════════════════════════════════════════════════════
# 4. Watcher cleanup preservation
# ══════════════════════════════════════════════════════════════════════════════


class TestNotifyWatchersCleanup:
    """Tests that watches survive an in_progress notification but die on terminal."""

    @pytest.mark.asyncio
    async def test_in_progress_does_not_remove_watch(
        self, job_queue_service, watcher_repo, instance_manager
    ):
        """Test A: in_progress → watcher stays so it can receive the final terminal notification."""
        job = make_mock_job()
        watcher_repo.add_watch(job.job_id, "watcher-1")
        job_queue_service._repository.get = MagicMock(return_value=job)

        await job_queue_service.notify_watchers(
            job.job_id, "in_progress", progress="Working…", waiting_for=2
        )

        # Watch must still be present
        watches = watcher_repo.get_watchers_for_job(job.job_id)
        assert len(watches) == 1
        assert watches[0].instance_id == "watcher-1"

    @pytest.mark.asyncio
    async def test_completed_removes_watch(
        self, job_queue_service, watcher_repo, instance_manager
    ):
        """Test B: completed → watcher IS removed (terminal)."""
        job = make_mock_job()
        watcher_repo.add_watch(job.job_id, "watcher-1")
        job_queue_service._repository.get = MagicMock(return_value=job)

        await job_queue_service.notify_watchers(job.job_id, "completed")

        assert len(watcher_repo.get_watchers_for_job(job.job_id)) == 0

    @pytest.mark.asyncio
    async def test_failed_removes_watch(
        self, job_queue_service, watcher_repo, instance_manager
    ):
        """Test C: failed → watcher IS removed (terminal)."""
        job = make_mock_job()
        watcher_repo.add_watch(job.job_id, "watcher-1")
        job_queue_service._repository.get = MagicMock(return_value=job)

        await job_queue_service.notify_watchers(job.job_id, "failed", error="x")

        assert len(watcher_repo.get_watchers_for_job(job.job_id)) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 5. MessageJobHandler in_progress emit
# ══════════════════════════════════════════════════════════════════════════════


class TestMessageJobHandlerInProgressEmit:
    """Test that MessageJobHandler emits in_progress when skip_complete=True with waiting_for>0."""

    @pytest.mark.asyncio
    async def test_skip_complete_with_waiting_for_emits_in_progress(self):
        """Test A: skip_complete=True + waiting_for>0 → emit in_progress, return early."""
        # Arrange
        mock_manager = MagicMock()
        mock_manager._process_message_with_tracking = AsyncMock(
            return_value=MessageResult(content="partial child response", tool_calls=None)
        )
        # Execution gate passthrough
        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()
        from daemon.services.execution_gate import ExecutionGateService
        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        mock_manager.execution_gate = gate
        # No running task
        task_repo = MagicMock()
        task_repo.find_running_by_instance = MagicMock(return_value=None)
        mock_manager._task_repo = task_repo
        # _process_child_completion_and_notify_parent is invoked in the happy path
        mock_manager._process_child_completion_and_notify_parent = AsyncMock()

        # Build the handler
        mock_jqs = MagicMock()
        mock_jqs.complete_job = AsyncMock(return_value=None)
        mock_jqs.notify_watchers = AsyncMock(return_value=0)
        mock_jqs._lock_manager = MagicMock()
        # No dispatch bus
        mock_jqs._dispatch_bus = None

        mock_jrepo = MagicMock()
        # No other active MESSAGE jobs (pre-flight passes)
        mock_jrepo.find_processing_message_jobs_by_instance = MagicMock(return_value=[])
        # atomic_transition succeeds (requeue never triggered)
        mock_jrepo.atomic_transition = MagicMock()

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_jqs,
            job_repository=mock_jrepo,
        )

        # Build a job whose instance will be in WAITING_CHILDREN state with waiting_for=2
        job = MagicMock()
        job.job_id = "job-msg-1"
        job.instance_id = "instance-xyz"
        job.message = "hello"
        job.project_id = "test-project"
        job.queue_id = "system_parallel_queue"
        job.job_metadata = {}

        # Instance lookup returns WAITING_CHILDREN with waiting_for=2
        instance_meta = make_instance_meta(
            instance_id="instance-xyz",
            status="waiting_children",
            waiting_for=2,
        )
        mock_manager._instance_repository = MagicMock()
        mock_manager._instance_repository.get = MagicMock(return_value=instance_meta)

        # Act
        await handler.handle(job)

        # Assert: in_progress was emitted
        mock_jqs.notify_watchers.assert_called_once()
        call = mock_jqs.notify_watchers.call_args
        # job_id is positional; status is a kwarg in the source
        assert call.args[0] == "job-msg-1"
        assert call.kwargs.get("status") == "in_progress"
        assert call.kwargs.get("waiting_for") == 2
        # progress is the result.content
        assert call.kwargs.get("progress") == "partial child response"

        # And complete_job must NOT have been called
        mock_jqs.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_complete_with_zero_waiting_does_not_emit_in_progress(self):
        """Test B: skip_complete=True but waiting_for=0 → fall through to normal completion."""
        # Build the same harness
        mock_manager = MagicMock()
        mock_manager._process_message_with_tracking = AsyncMock(
            return_value=MessageResult(content="final answer", tool_calls=None)
        )
        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()
        from daemon.services.execution_gate import ExecutionGateService
        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        mock_manager.execution_gate = gate

        task_repo = MagicMock()
        task_repo.find_running_by_instance = MagicMock(return_value=None)
        mock_manager._task_repo = task_repo
        # The happy path invokes this; mock it to avoid MagicMock-not-awaitable errors
        mock_manager._process_child_completion_and_notify_parent = AsyncMock()

        mock_jqs = MagicMock()
        mock_jqs.complete_job = AsyncMock(return_value=None)
        mock_jqs.notify_watchers = AsyncMock(return_value=0)
        mock_jqs._lock_manager = MagicMock()
        mock_jqs._dispatch_bus = None

        mock_jrepo = MagicMock()
        mock_jrepo.find_processing_message_jobs_by_instance = MagicMock(return_value=[])
        mock_jrepo.atomic_transition = MagicMock()

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_jqs,
            job_repository=mock_jrepo,
        )

        job = MagicMock()
        job.job_id = "job-msg-2"
        job.instance_id = "instance-xyz"
        job.message = "hello"
        job.project_id = "test-project"
        job.queue_id = "system_parallel_queue"
        job.job_metadata = {}

        # Instance has waiting_for=0 — skip_complete path is taken but the
        # in_progress emit is guarded by `wf > 0`, so it must fall through
        # to the normal complete_job(COMPLETED) call below.
        instance_meta = make_instance_meta(
            instance_id="instance-xyz",
            status="waiting_children",
            waiting_for=0,
        )
        mock_manager._instance_repository = MagicMock()
        mock_manager._instance_repository.get = MagicMock(return_value=instance_meta)

        await handler.handle(job)

        # in_progress must NOT have been emitted (status is a kwarg in the source)
        for call in mock_jqs.notify_watchers.call_args_list:
            assert call.kwargs.get("status") != "in_progress"

        # And the job must have been completed normally
        mock_jqs.complete_job.assert_called_once()
        kwargs = mock_jqs.complete_job.call_args.kwargs
        assert kwargs["demand_state"] == DemandState.COMPLETED


# ══════════════════════════════════════════════════════════════════════════════
# 6. Watcher event filter — in_progress is opt-in
# ══════════════════════════════════════════════════════════════════════════════


class TestWatcherEventFilterForInProgress:
    """Test that watchers must opt into in_progress notifications."""

    @pytest.mark.asyncio
    async def test_watcher_with_completed_only_skips_in_progress(
        self, job_queue_service, watcher_repo, instance_manager
    ):
        """Test A: watch_events=['completed'] → does NOT receive in_progress."""
        job = make_mock_job()
        # This watcher only wants completed
        watcher_repo.add_watch(job.job_id, "watcher-strict", ["completed"])
        job_queue_service._repository.get = MagicMock(return_value=job)

        count = await job_queue_service.notify_watchers(
            job.job_id, "in_progress", progress="x", waiting_for=1
        )

        assert count == 0
        instance_manager.enqueue_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_watcher_with_both_events_receives_both(
        self, job_queue_service, watcher_repo, instance_manager
    ):
        """Test B: watch_events=['in_progress','completed'] → receives both events."""
        job = make_mock_job()
        watcher_repo.add_watch(
            job.job_id, "watcher-loose", ["in_progress", "completed"]
        )
        job_queue_service._repository.get = MagicMock(return_value=job)

        # First an in_progress
        count = await job_queue_service.notify_watchers(
            job.job_id, "in_progress", progress="x", waiting_for=1
        )
        assert count == 1
        # Then a completed
        count = await job_queue_service.notify_watchers(job.job_id, "completed")
        assert count == 1

        assert instance_manager.enqueue_message.call_count == 2

        # Inspect the two messages — each is delivered via kwargs
        messages = [
            c.kwargs.get("message")
            for c in instance_manager.enqueue_message.call_args_list
        ]
        # Confirm both in_progress and completed arrived
        assert any("in progress ⟳" in m for m in messages)
        assert any("completed ✓" in m for m in messages)


# ══════════════════════════════════════════════════════════════════════════════
# Bonus: ALL_WATCHABLE_EVENTS is exported correctly
# ══════════════════════════════════════════════════════════════════════════════


class TestAllWatchableEventsConstant:
    """Sanity checks on the ALL_WATCHABLE_EVENTS constant."""

    def test_in_progress_in_all_watchable_events(self):
        assert "in_progress" in ALL_WATCHABLE_EVENTS

    def test_in_progress_not_a_terminal_state(self):
        assert "in_progress" not in ALL_TERMINAL_STATES

    def test_watchable_is_terminal_plus_in_progress(self):
        assert set(ALL_WATCHABLE_EVENTS) == set(ALL_TERMINAL_STATES) | {"in_progress"}


# ══════════════════════════════════════════════════════════════════════════════
# 7. Reviewer fixes: throttle/dedup, escape hatch, terminal lifecycle
# ══════════════════════════════════════════════════════════════════════════════


class TestJobProcessorInProgressGuardReviewFixes:
    """Tests for the throttle/dedup, escape hatch, and terminal lifecycle
    in ``JobProcessor._emit_in_progress_if_children_pending()``.

    These cover the safety nets the original 6 inline blocks did not have:

    * **Throttle/dedup** — within a 300s window, skip re-emit when ``waiting_for``
      count is unchanged, so a hot poll loop cannot spam watchers.
    * **Escape hatch** — if a job has been sitting in the guard for more than
      ``_child_timeout_seconds``, force-complete it as FAILED so a stuck child
      never permanently blocks the job.
    * **Terminal lifecycle** — once ``waiting_for`` drops to 0, the guard
      returns ``False`` so the caller runs the normal terminal path; the
      helper itself does not emit a second notification for the terminal.
    """

    def _build_processor(self):
        """Construct a JobProcessor with mocked collaborators.

        Only ``_queue_service.notify_watchers`` and ``_queue_service.complete_job``
        are interesting here — every other collaborator is a MagicMock.
        """
        mock_project_repo = MagicMock()
        mock_project_repo.list_projects = MagicMock(return_value=[])
        mock_queue_repo = MagicMock()
        mock_queue_repo.list_by_project = MagicMock(return_value=[])

        mock_queue_service = MagicMock()
        mock_queue_service._repository = MagicMock()
        mock_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_queue_service.complete_job = AsyncMock(return_value=None)

        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
            return_value="partial work"
        )

        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )
        return processor, mock_queue_service

    @staticmethod
    def _make_instance_meta(waiting_for: int = 2) -> MagicMock:
        return make_instance_meta(
            instance_id="inst-test-12345678",
            status="completed",
            waiting_for=waiting_for,
        )

    @staticmethod
    def _make_proc_job(job_id: str = "job-throttle-001") -> MagicMock:
        job = MagicMock()
        job.job_id = job_id
        job.job_type = "task"
        job.agent_id = "coder"
        return job

    @pytest.mark.asyncio
    async def test_notification_throttle_dedups_within_window(self):
        """Test 1: Two calls within the 300s throttle window with the same
        ``waiting_for`` count must result in a single ``notify_watchers``
        invocation. The second call still returns ``True`` (guard fired) so
        the caller continues to defer the terminal notification.
        """
        processor, mock_qs = self._build_processor()
        instance_meta = self._make_instance_meta(waiting_for=2)
        proc_job = self._make_proc_job(job_id="job-throttle-001")

        # First call: should emit in_progress and return True.
        first = await processor._emit_in_progress_if_children_pending(
            instance_meta, proc_job, "TASK", "completed"
        )
        assert first is True
        mock_qs.notify_watchers.assert_called_once()
        assert mock_qs.notify_watchers.call_args.kwargs.get("status") == "in_progress"
        assert mock_qs.notify_watchers.call_args.kwargs.get("waiting_for") == 2

        # Second call in quick succession with same waiting_for: throttled.
        # It must still return True (the guard is still "fired" — caller
        # should defer), but notify_watchers must NOT be called again.
        second = await processor._emit_in_progress_if_children_pending(
            instance_meta, proc_job, "TASK", "completed"
        )
        assert second is True
        # CRITICAL: still exactly one notification.
        assert mock_qs.notify_watchers.call_count == 1

    @pytest.mark.asyncio
    async def test_throttle_window_expiry_re_emits(self):
        """Test 2: After advancing past the 300s throttle window, the
        second notification IS emitted. The test manually rewinds
        ``_last_in_progress`` so the time-based check fires deterministically.
        """
        processor, mock_qs = self._build_processor()
        instance_meta = self._make_instance_meta(waiting_for=2)
        proc_job = self._make_proc_job(job_id="job-throttle-002")

        # First call: emits notification, populates _last_in_progress.
        first = await processor._emit_in_progress_if_children_pending(
            instance_meta, proc_job, "TASK", "completed"
        )
        assert first is True
        assert mock_qs.notify_watchers.call_count == 1
        assert proc_job.job_id in processor._last_in_progress

        # Rewind the throttle window to > 300s in the past so the next call
        # is treated as a fresh emit.
        old_timestamp, old_wf = processor._last_in_progress[proc_job.job_id]
        processor._last_in_progress[proc_job.job_id] = (
            time.time() - 400.0,
            old_wf,
        )

        # Second call: throttle window expired → notify_watchers fires again.
        second = await processor._emit_in_progress_if_children_pending(
            instance_meta, proc_job, "TASK", "completed"
        )
        assert second is True
        assert mock_qs.notify_watchers.call_count == 2
        # And the new entry has a fresh timestamp.
        ts, wf = processor._last_in_progress[proc_job.job_id]
        assert wf == 2
        # Timestamp must be near "now" (not the rewound value).
        assert (time.time() - ts) < 5

    @pytest.mark.asyncio
    async def test_escape_hatch_force_fails_stuck_job(self):
        """Test 3: A job that has been waiting for children longer than
        ``_child_timeout_seconds`` is force-completed as FAILED with a
        timeout message, and the in-progress tracking dicts are cleaned up.
        """
        processor, mock_qs = self._build_processor()
        # Tighten the timeout to 1s so the test can rewind the clock.
        processor._child_timeout_seconds = 1

        instance_meta = self._make_instance_meta(waiting_for=2)
        proc_job = self._make_proc_job(job_id="job-escape-003")

        # Seed: the job has been in the guard for 100s, well past the 1s timeout.
        processor._in_progress_since[proc_job.job_id] = time.time() - 100.0
        # (No _last_in_progress entry needed for the escape-hatch path.)

        result = await processor._emit_in_progress_if_children_pending(
            instance_meta, proc_job, "TASK", "completed"
        )

        # Guard "fired" — caller should defer/return.
        assert result is True

        # complete_job must have been called with FAILED + timeout error.
        mock_qs.complete_job.assert_called_once()
        kwargs = mock_qs.complete_job.call_args.kwargs
        assert kwargs["demand_state"] == DemandState.FAILED
        assert "timeout" in (kwargs.get("error") or "").lower()

        # The escape-hatch path is a terminal — it must NOT emit an
        # in_progress notification, and the tracking dicts must be cleaned
        # up so the next guard visit starts a fresh window.
        mock_qs.notify_watchers.assert_not_called()
        assert proc_job.job_id not in processor._in_progress_since
        assert proc_job.job_id not in processor._last_in_progress

    @pytest.mark.asyncio
    async def test_terminal_lifecycle_after_in_progress(self):
        """Test 4: Lifecycle. First call (waiting_for=2) emits in_progress.
        After the child reports, waiting_for drops to 0 and the next call
        returns ``False`` (caller runs the normal terminal path) without
        emitting a second notification from the helper.
        """
        processor, mock_qs = self._build_processor()
        proc_job = self._make_proc_job(job_id="job-lifecycle-004")

        # Phase 1: 2 children still pending.
        instance_meta_pending = self._make_instance_meta(waiting_for=2)
        first = await processor._emit_in_progress_if_children_pending(
            instance_meta_pending, proc_job, "TASK", "completed"
        )
        assert first is True, "Guard must fire while children pending"
        mock_qs.notify_watchers.assert_called_once()
        assert mock_qs.notify_watchers.call_args.kwargs.get("status") == "in_progress"

        # Phase 2: simulate child reports — waiting_for drops to 0.
        instance_meta_done = self._make_instance_meta(waiting_for=0)
        second = await processor._emit_in_progress_if_children_pending(
            instance_meta_done, proc_job, "TASK", "completed"
        )
        # Guard does NOT fire — caller should run the normal terminal
        # completion path (which will emit the "completed" notification
        # in its own code, not via this helper).
        assert second is False, (
            "Guard must return False when waiting_for=0 so the caller "
            "runs the normal terminal completion path"
        )
        # And the helper itself must not have emitted a second notification.
        # (The single notify_watchers call is from the first (in_progress) call.)
        assert mock_qs.notify_watchers.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 8. ChildReportsService cascade — ERROR status preserved (W1 fix)
# ══════════════════════════════════════════════════════════════════════════════


class TestCascadePreservesErrorOnChildComplete:
    """W1 fix: a parent whose last child completed successfully should
    remain in ERROR if it errored first — its state is more useful for
    diagnostics than overwriting it with COMPLETED.

    Covers the new ``parent.status != InstanceStatus.ERROR.value`` guard
    in ``ChildReportsService._update_parent_on_child_complete`` around
    line 478–482 of ``daemon/services/child_reports.py``.
    """

    def _make_parent(
        self,
        status: str,
        waiting_for: int,
        parent_id: str = "parent-W1",
    ) -> MagicMock:
        parent = MagicMock()
        parent.instance_id = parent_id
        parent.parent_id = None
        parent.status = status
        parent.waiting_for = waiting_for
        parent.children = "[]"
        parent.instance_metadata = {}
        parent.last_activity_at = None
        parent.version = 1
        return parent

    def _make_child(self, parent_id: str = "parent-W1") -> MagicMock:
        child = MagicMock()
        child.instance_id = "child-W1"
        child.parent_id = parent_id
        child.status = "completed"
        child.instance_metadata = {}
        child.children = "[]"
        child.waiting_for = 0
        child.last_activity_at = None
        child.version = 1
        return child

    @staticmethod
    def _setup_cascade_session(parent: MagicMock, child: MagicMock) -> MagicMock:
        """Build a mock session that simulates the atomic UPDATE returning
        ``new_waiting=0`` and the post-expiry parent re-read.

        Mirrors the SQLAlchemy calls in
        ``ChildReportsService._update_parent_on_child_complete``:

        * ``session.get(Instance, child.parent_id)`` → ``parent`` (initial)
        * ``session.execute(text("UPDATE instances SET waiting_for = ... RETURNING waiting_for"))``
          → row with new value 0
        * ``session.expire(parent)``
        * ``session.get(Instance, parent.instance_id)`` → ``parent`` (re-read
          with the post-decrement ``waiting_for=0`` and the original status,
          which the test sets explicitly)
        * ``session.exec(select(func.count()).select_from(MessageQueue)...)``
          → scalar_one() == 0 (no pending messages)
        """
        session = MagicMock()
        # First session.get → parent (initial lookup at line 397).
        # Second session.get → parent (re-read after session.expire at line 444).
        # The mock just returns the same object for both — the test sets
        # waiting_for manually before the cascade call.
        session.get = MagicMock(return_value=parent)
        # SQL UPDATE … RETURNING waiting_for — return the new (decremented) value.
        update_result = MagicMock()
        update_result.first = MagicMock(return_value=(0,))
        session.execute = MagicMock(return_value=update_result)
        # Pending-message count check (line 484-493) — return 0 so the
        # cascade is not deferred to WAITING_CHILDREN.
        pending_result = MagicMock()
        pending_result.scalar_one = MagicMock(return_value=0)
        session.exec = MagicMock(return_value=pending_result)
        return session

    @pytest.mark.asyncio
    async def test_error_status_not_overwritten_to_completed(self):
        """Root scenario: parent is in ERROR, last child completes.
        Parent MUST stay in ERROR (not get flipped to COMPLETED).
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        # Arrange: parent is in ERROR with one child still pending.
        parent = self._make_parent(
            status=InstanceStatus.ERROR.value,
            waiting_for=1,  # will be decremented to 0 by the cascade
        )
        child = self._make_child()
        session = self._setup_cascade_session(parent, child)

        # Minimal manager mock — the cascade only touches the session.
        mock_manager = MagicMock()
        mock_manager._live_hub = None
        mock_manager._checkpointer = None
        mock_manager.config = MagicMock()
        mock_manager.config.llm = MagicMock()

        service = ChildReportsService(manager=mock_manager)

        # Pre-condition: parent is in ERROR and has 1 child pending.
        assert parent.status == InstanceStatus.ERROR.value
        assert parent.waiting_for == 1

        # The cascade reads `parent.waiting_for` after session.expire, so we
        # patch the post-decrement value via a property-like side effect:
        # since the mock returns the same object on session.get, we set
        # waiting_for=0 on the same object just before the cascade check.
        # The simplest approach: pre-set the "post-decrement" value here,
        # matching what the SQL UPDATE RETURNING (0,) signals to the cascade.
        parent.waiting_for = 0  # the new value the cascade observes

        # Act: call the cascade.
        transitioned, completed_parent_id, completed_parent_parent_id = (
            await service._update_parent_on_child_complete(session, child)
        )

        # Assert: the guard at line 478-482 must have skipped the
        # COMPLETED transition because parent.status == ERROR.
        assert transitioned is False, (
            "Parent with ERROR status must NOT transition to RUNNING — "
            "the cascade is a no-op for ERROR parents."
        )
        assert completed_parent_id is None, (
            "Parent must NOT be reported as 'completed' — that would "
            "tell downstream listeners to overwrite ERROR with COMPLETED."
        )
        assert completed_parent_parent_id is None
        # Parent's status MUST be untouched.
        assert parent.status == InstanceStatus.ERROR.value, (
            f"Parent status was overwritten to {parent.status!r}; "
            f"expected it to stay {InstanceStatus.ERROR.value!r}"
        )

    @pytest.mark.asyncio
    async def test_completed_status_not_overwritten_by_cascade(self):
        """Negative control: a parent already in COMPLETED must not be
        transitioned again by the cascade (the existing
        ``!= InstanceStatus.COMPLETED.value`` guard).
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        parent = self._make_parent(
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        child = self._make_child()
        session = self._setup_cascade_session(parent, child)

        mock_manager = MagicMock()
        mock_manager._live_hub = None
        mock_manager._checkpointer = None
        mock_manager.config = MagicMock()
        mock_manager.config.llm = MagicMock()

        service = ChildReportsService(manager=mock_manager)

        transitioned, completed_parent_id, _ = (
            await service._update_parent_on_child_complete(session, child)
        )

        assert transitioned is False
        assert completed_parent_id is None
        assert parent.status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_running_parent_transitions_to_completed_normally(self):
        """Sanity / regression: with the W1 fix in place, a parent in
        RUNNING (the common case) must STILL transition to COMPLETED
        when its last child finishes — only ERROR is preserved.
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.repositories.instance.models import InstanceStatus

        parent = self._make_parent(
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        child = self._make_child()
        session = self._setup_cascade_session(parent, child)

        mock_manager = MagicMock()
        mock_manager._live_hub = None
        mock_manager._checkpointer = None
        mock_manager.config = MagicMock()
        mock_manager.config.llm = MagicMock()

        service = ChildReportsService(manager=mock_manager)

        transitioned, completed_parent_id, _ = (
            await service._update_parent_on_child_complete(session, child)
        )

        # RUNNING → COMPLETED is the normal happy path; the W1 fix
        # must not have broken it.
        assert transitioned is False
        assert completed_parent_id == parent.instance_id
        assert parent.status == InstanceStatus.COMPLETED.value
