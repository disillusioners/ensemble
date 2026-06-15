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
"""

import asyncio
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
