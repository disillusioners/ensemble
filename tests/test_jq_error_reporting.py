"""Tests for the shared ``handle_message_processing_error`` helper.

The helper unifies error-reporting side-effects across the
WorkerPool path (``ProcessMessageProcessor``) and the now-removed
JobQueue path (``MessageJobHandler``). The helper fires three
side-effects on a processing error:

1. DB error event (``event_bus.create_error_event``)
2. Lifecycle event publish (``_publish_instance_lifecycle_event``)
3. Error report to parent (``_send_error_report``)

This file covers:

- The shared helper itself (all 3 side-effects fire)
- ``ProcessMessageProcessor.process()`` still triggers all 3
  side-effects after the refactor (regression test on the
  refactored path)
- Best-effort resilience — a failure in one side-effect does not
  prevent the others from running, and the helper never raises
- Job completion (JobQueue-only side-effect) is invoked when
  ``job_id`` is provided

The tests use ``MagicMock`` / ``AsyncMock`` for the manager and
service layer (matching the patterns in
``tests/job_queue/test_message_job_queue.py``) — no real DB or LLM
calls are made.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch

from daemon.services.message_processing_errors import (
    _classify_error_type,
    _truncate_error,
    handle_message_processing_error,
)
from daemon.services.job_queue_service import DemandState


# ── Local JobQueue infrastructure fixtures ────────────────────────────────────
#
# The integration tests below need a real JobQueueService + JobRepository
# against an in-memory SQLite. Mirroring the fixtures from
# ``tests/job_queue/conftest.py`` here so this file can live at the spec'd
# ``tests/`` root without depending on that conftest.

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService
from daemon import constants

TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


@pytest.fixture(autouse=True)
def _setup_system_default_project():
    """Mirror the autouse fixture in tests/job_queue/conftest.py."""
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID
    yield
    constants.SYSTEM_DEFAULT_PROJECT_ID = original


@pytest.fixture
def engine():
    """In-memory SQLite engine — StaticPool so cross-thread access works."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repository(engine):
    """JobRepository over the in-memory engine."""
    repo = JobRepository(engine)
    yield repo
    try:
        repo.hard_delete_terminal()
        repo.hard_delete_by_project("test-project")
    except Exception:
        pass


@pytest.fixture
def lock_repo(engine):
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo):
    manager = JobLockManager(lock_repo=lock_repo)
    yield manager
    for lock in lock_repo.get_all_locks():
        lock_repo.release(lock.lock_id)


@pytest.fixture
def queue_repository_with_system_queues(engine):
    """Pre-provision system queues for the projects the tests use."""
    repo = JobQueueRepository(engine)
    for project_id in ("test-project", "project-1", "project-2", TEST_SYSTEM_PROJECT_ID):
        repo.create(
            project_id=project_id, queue_name="system_fifo_queue",
            queue_type="fifo", concurrency_limit=1, is_system=True,
        )
        repo.create(
            project_id=project_id, queue_name="system_parallel_queue",
            queue_type="parallel", concurrency_limit=3, is_system=True,
        )
        repo.create(
            project_id=project_id, queue_name="system_kb_fifo_queue",
            queue_type="fifo", concurrency_limit=1, is_system=True,
            description="System FIFO queue for Knowledge Base import jobs",
        )
    yield repo


@pytest.fixture
def job_queue_service(repository, lock_manager, queue_repository_with_system_queues):
    return JobQueueService(
        repository, lock_manager, queue_repository_with_system_queues
    )


@pytest.fixture
def sample_job_data():
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test job message",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "job_metadata": {"test": True},
    }


# ── Shared Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_event_bus():
    """Mock EventBus with create_error_event as AsyncMock."""
    bus = MagicMock()
    bus.create_error_event = AsyncMock()
    return bus


@pytest.fixture
def mock_lifecycle_publisher():
    """Mock _publish_instance_lifecycle_event as AsyncMock."""
    return AsyncMock()


@pytest.fixture
def mock_send_error_report():
    """Mock _send_error_report as AsyncMock."""
    return AsyncMock()


@pytest.fixture
def mock_job_queue_service():
    """Mock JobQueueService with complete_job as AsyncMock."""
    service = MagicMock()
    service.complete_job = AsyncMock()
    return service


@pytest.fixture
def mock_instance_repo():
    """Mock instance repository with get() returning a parented instance."""
    repo = MagicMock()
    instance = MagicMock()
    instance.parent_id = "parent-instance-id"
    repo.get = MagicMock(return_value=instance)
    return repo


@pytest.fixture
def mock_manager(
    mock_event_bus,
    mock_lifecycle_publisher,
    mock_send_error_report,
    mock_job_queue_service,
    mock_instance_repo,
):
    """Mock InstanceManager with all Phase-0 side-effect entrypoints.

    The shared helper resolves entrypoints via ``getattr`` so a
    plain ``MagicMock`` would also work, but binding the actual
    AsyncMocks here makes the call assertions below trivially
    readable.
    """
    manager = MagicMock()
    manager._event_bus = mock_event_bus
    manager._publish_instance_lifecycle_event = mock_lifecycle_publisher
    manager._send_error_report = mock_send_error_report
    manager._instance_repository = mock_instance_repo
    manager._job_queue_service = mock_job_queue_service
    return manager


# ── 1. Pure helpers (sanity tests for the moved utilities) ──────────────────────


class TestTruncateError:
    """_truncate_error moved from task_processor.py — verify the move."""

    def test_short_error_passes_through(self):
        assert _truncate_error("boom") == "boom"

    def test_long_error_truncated(self):
        long = "x" * 1000
        out = _truncate_error(long, max_len=50)
        assert out.startswith("x" * 50)
        assert out.endswith("...")

    def test_html_tags_stripped(self):
        out = _truncate_error("<b>bold</b> error")
        assert "<b>" not in out
        assert "bold" in out
        assert "error" in out


class TestClassifyErrorType:
    """_classify_error_type moved from task_processor.py — verify behaviour."""

    def test_value_error_is_invalid_data(self):
        assert _classify_error_type(ValueError("nope")) == "invalid_data"

    def test_key_error_is_instance_not_found(self):
        assert _classify_error_type(KeyError("missing")) == "instance_not_found"

    def test_runtime_error_is_runtime_error(self):
        assert _classify_error_type(RuntimeError("boom")) == "runtime_error"

    def test_default_is_execution_error(self):
        assert _classify_error_type(Exception("mystery")) == "execution_error"

    def test_transient_llm_error_is_transient_error(self):
        """Plan work unit 6 (docs/plans/transient-channel-retry-widening.md):
        an exhausted TransientLLMError maps to transient_error (same type
        the TransientAPIError path produces) so parents see
        transient_error/warning instead of invalid_data/execution_error."""
        from daemon.llm_error_classifier import TransientLLMError

        result = _classify_error_type(
            TransientLLMError("api_error_body", Exception("All models rate limited"))
        )
        assert result == "transient_error"

    def test_transient_llm_error_valueerror_kind_not_invalid_data(self):
        """A wrapped ValueError body must NOT fall into the generic
        invalid_data ValueError branch — the wrapper type wins."""
        from daemon.llm_error_classifier import TransientLLMError

        result = _classify_error_type(
            TransientLLMError("value_error_body", ValueError("no generations found"))
        )
        assert result == "transient_error"


# ── 2. Shared helper unit tests ────────────────────────────────────────────────


class TestHandleMessageProcessingError:
    """Direct unit tests for handle_message_processing_error."""

    @pytest.mark.asyncio
    async def test_calls_create_error_event(
        self, mock_manager, mock_event_bus
    ):
        """1. The error event must be persisted via the EventBus."""
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            message_id="msg-1",
        )

        mock_event_bus.create_error_event.assert_awaited_once()
        kwargs = mock_event_bus.create_error_event.call_args.kwargs
        assert kwargs["instance_id"] == "inst-123"
        assert "error" in kwargs
        error_data = kwargs["error"]
        assert error_data["message_id"] == "msg-1"
        assert "boom" in error_data["error"]
        assert error_data["error_type"] == "invalid_data"

    @pytest.mark.asyncio
    async def test_calls_publish_lifecycle_event(
        self, mock_manager, mock_lifecycle_publisher, mock_instance_repo
    ):
        """2. The instance lifecycle event must be published with status=error."""
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
        )

        mock_lifecycle_publisher.assert_awaited_once()
        kwargs = mock_lifecycle_publisher.call_args.kwargs
        assert kwargs["instance_id"] == "inst-123"
        assert kwargs["status"] == "error"
        assert "boom" in kwargs["error"]
        # parent_id resolved from instance_repository
        assert kwargs["parent_id"] == "parent-instance-id"

    @pytest.mark.asyncio
    async def test_calls_send_error_report(
        self, mock_manager, mock_send_error_report
    ):
        """3. The parent instance must receive an error report."""
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            message_id="msg-1",
        )

        mock_send_error_report.assert_awaited_once()
        kwargs = mock_send_error_report.call_args.kwargs
        assert kwargs["instance_id"] == "inst-123"
        assert kwargs["error_type"] == "invalid_data"
        assert kwargs["message_id"] == "msg-1"
        assert "boom" in kwargs["error"]

    @pytest.mark.asyncio
    async def test_completes_job_when_job_id_provided(
        self, mock_manager, mock_job_queue_service
    ):
        """JobQueue path: complete_job must be called with FAILED."""
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            job_id="job-abc",
        )

        mock_job_queue_service.complete_job.assert_awaited_once()
        # complete_job is called with (job_id, demand_state, error) — the
        # first positional arg is job_id
        call_args = mock_job_queue_service.complete_job.call_args
        assert call_args.args[0] == "job-abc"
        assert call_args.kwargs.get("demand_state") == DemandState.FAILED
        assert "boom" in call_args.kwargs.get("error", "")

    @pytest.mark.asyncio
    async def test_does_not_complete_job_when_only_task_id(
        self, mock_manager, mock_job_queue_service
    ):
        """WorkerPool path: complete_job must NOT be called (task completes elsewhere)."""
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            task_id="task-abc",
        )

        mock_job_queue_service.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_includes_job_id_in_error_event_data(
        self, mock_manager, mock_event_bus
    ):
        """When job_id is provided, it should appear in the error event data."""
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            job_id="job-abc",
        )

        error_data = mock_event_bus.create_error_event.call_args.kwargs["error"]
        assert error_data["job_id"] == "job-abc"

    @pytest.mark.asyncio
    async def test_includes_task_id_in_error_event_data(
        self, mock_manager, mock_event_bus
    ):
        """When task_id is provided, it should appear in the error event data."""
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            task_id="task-abc",
        )

        error_data = mock_event_bus.create_error_event.call_args.kwargs["error"]
        assert error_data["task_id"] == "task-abc"

    @pytest.mark.asyncio
    async def test_handle_error_with_integer_task_id(
        self, mock_manager, mock_event_bus, mock_lifecycle_publisher,
        mock_send_error_report,
    ):
        """Integer task_id (from Task.id, an INTEGER PK) must NOT crash the handler.

        Regression: ``task_id`` arrives as an int from
        ``task_processor.py`` (``task_id=task.id`` where ``task.id`` is
        ``INTEGER PRIMARY KEY AUTOINCREMENT``). The logging line used
        ``task_id[:8]`` which raises ``TypeError: 'int' object is not
        subscriptable``, killing the error handler before any of its
        3 critical side-effects run — leaving child instances orphaned
        and parents stuck in WAITING_CHILDREN.

        The fix wraps the slice in ``str(task_id)[:8]`` so the handler
        completes all 3 side-effects regardless of task_id type.
        """
        # Must NOT raise TypeError — the call itself is the assertion
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            task_id=18441,  # int, not str — mirrors task.id from the DB
        )

        # 1. Error event in DB still fires
        mock_event_bus.create_error_event.assert_awaited_once()
        error_data = mock_event_bus.create_error_event.call_args.kwargs["error"]
        assert error_data["task_id"] == 18441
        assert "boom" in error_data["error"]

        # 2. Lifecycle event publish still fires
        mock_lifecycle_publisher.assert_awaited_once()
        lifecycle_kwargs = mock_lifecycle_publisher.call_args.kwargs
        assert lifecycle_kwargs["instance_id"] == "inst-123"
        assert lifecycle_kwargs["status"] == "error"

        # 3. Error report to parent still fires
        mock_send_error_report.assert_awaited_once()
        report_kwargs = mock_send_error_report.call_args.kwargs
        assert report_kwargs["instance_id"] == "inst-123"

    @pytest.mark.asyncio
    async def test_no_event_bus_falls_back_to_event_repo(self):
        """If the manager has no _event_bus, fall back to _event_repo."""
        manager = MagicMock(spec=["_event_repo", "_instance_repository",
                                  "_publish_instance_lifecycle_event",
                                  "_send_error_report", "_job_queue_service"])
        manager._event_bus = None
        manager._event_repo = MagicMock()
        manager._event_repo.create_event = MagicMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        manager._publish_instance_lifecycle_event = AsyncMock()
        manager._send_error_report = AsyncMock()
        manager._job_queue_service = MagicMock()
        manager._job_queue_service.complete_job = AsyncMock()

        await handle_message_processing_error(
            instance_manager=manager,
            instance_id="inst-123",
            error=ValueError("boom"),
        )

        # EventRepository.create_event must be called (in a thread)
        manager._event_repo.create_event.assert_called_once()
        kwargs = manager._event_repo.create_event.call_args.kwargs
        assert kwargs["instance_id"] == "inst-123"
        assert kwargs["kind"] == "error"
        assert "boom" in str(kwargs["data"]["error"])

    # ── Best-effort resilience ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_error_event_failure_does_not_block_lifecycle(
        self, mock_manager, mock_event_bus, mock_lifecycle_publisher
    ):
        """If create_error_event fails, the lifecycle event must still fire."""
        mock_event_bus.create_error_event.side_effect = RuntimeError("event bus down")

        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
        )

        mock_lifecycle_publisher.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifecycle_failure_does_not_block_error_report(
        self, mock_manager, mock_lifecycle_publisher, mock_send_error_report
    ):
        """If _publish_instance_lifecycle_event fails, _send_error_report must still fire."""
        mock_lifecycle_publisher.side_effect = RuntimeError("event bus down")

        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
        )

        mock_send_error_report.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_report_failure_does_not_block_job_completion(
        self, mock_manager, mock_send_error_report, mock_job_queue_service
    ):
        """If _send_error_report fails, complete_job must still be called."""
        mock_send_error_report.side_effect = RuntimeError("db down")

        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            job_id="job-abc",
        )

        mock_job_queue_service.complete_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_helper_never_raises(self, mock_manager):
        """If every side-effect raises, the helper must still return cleanly."""
        mock_manager._event_bus.create_error_event.side_effect = RuntimeError("e1")
        mock_manager._publish_instance_lifecycle_event.side_effect = RuntimeError("e2")
        mock_manager._send_error_report.side_effect = RuntimeError("e3")
        mock_manager._job_queue_service.complete_job.side_effect = RuntimeError("e4")

        # Must not raise
        await handle_message_processing_error(
            instance_manager=mock_manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            job_id="job-abc",
        )

    @pytest.mark.asyncio
    async def test_no_lifecycle_method_does_not_raise(self, mock_event_bus):
        """If the manager lacks _publish_instance_lifecycle_event, the helper is robust."""
        manager = MagicMock()
        manager._event_bus = mock_event_bus
        manager._publish_instance_lifecycle_event = None  # not present
        manager._send_error_report = AsyncMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        manager._job_queue_service = MagicMock()
        manager._job_queue_service.complete_job = AsyncMock()

        await handle_message_processing_error(
            instance_manager=manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            job_id="job-abc",
        )

        # Lifecycle was skipped but the rest still ran
        mock_event_bus.create_error_event.assert_awaited_once()
        manager._send_error_report.assert_awaited_once()
        manager._job_queue_service.complete_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_send_error_report_does_not_raise(self, mock_event_bus):
        """If the manager lacks _send_error_report, the helper is robust."""
        manager = MagicMock()
        manager._event_bus = mock_event_bus
        manager._publish_instance_lifecycle_event = AsyncMock()
        manager._send_error_report = None  # not present
        manager._instance_repository.get = MagicMock(return_value=None)
        manager._job_queue_service = MagicMock()
        manager._job_queue_service.complete_job = AsyncMock()

        await handle_message_processing_error(
            instance_manager=manager,
            instance_id="inst-123",
            error=ValueError("boom"),
            job_id="job-abc",
        )

        mock_event_bus.create_error_event.assert_awaited_once()
        manager._publish_instance_lifecycle_event.assert_awaited_once()
        manager._job_queue_service.complete_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_parent_id_does_not_raise(self, mock_lifecycle_publisher):
        """Instance with no parent must not break lifecycle event publish."""
        manager = MagicMock()
        manager._event_bus = MagicMock()
        manager._event_bus.create_error_event = AsyncMock()
        manager._publish_instance_lifecycle_event = mock_lifecycle_publisher
        manager._send_error_report = AsyncMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        manager._job_queue_service = MagicMock()
        manager._job_queue_service.complete_job = AsyncMock()

        await handle_message_processing_error(
            instance_manager=manager,
            instance_id="inst-123",
            error=ValueError("boom"),
        )

        # Lifecycle fired with parent_id=None
        kwargs = mock_lifecycle_publisher.call_args.kwargs
        assert kwargs["parent_id"] is None


# ── 3. ProcessMessageProcessor regression (refactor verification) ──────────────


class TestProcessMessageProcessorStillTriggersAllSideEffects:
    """The refactored WorkerPool path must still produce all 3 side-effects."""

    @pytest.mark.asyncio
    async def test_error_event_lifecycle_and_parent_report_all_fire(self):
        """After refactoring task_processor.py to call the shared helper,
        the WorkerPool path must still produce all 3 side-effects.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        # Build a mock manager
        manager = MagicMock()
        manager._event_bus = MagicMock()
        manager._event_bus.create_error_event = AsyncMock()
        manager._publish_instance_lifecycle_event = AsyncMock()
        manager._send_error_report = AsyncMock()
        manager._process_message_with_tracking = AsyncMock(
            side_effect=ValueError("worker pool boom")
        )

        # Build instance metadata (used by the processor)
        instance_meta = MagicMock()
        instance_meta.parent_id = "parent-id"
        instance_meta.status = "running"
        instance_meta.waiting_for = 0
        instance_meta.instance_id = "inst-worker-123"
        instance_meta.agent_id = "test-agent"
        instance_repo = MagicMock()
        instance_repo.get = MagicMock(return_value=instance_meta)
        manager._instance_repository = instance_repo

        # Build a fake task that fails processing
        task = MagicMock()
        task.id = "task-abc"
        task.instance_id = "inst-worker-123"
        task.message_id = "msg-abc"
        task.retry_count = 0
        task.task_type = "process_message"

        # Message repository stub
        message_repo = MagicMock()
        message_repo.complete = MagicMock()
        manager._queue_repository = message_repo

        # Message lookup stub — the processor reads the message from
        # ``message_repo.get`` before driving the pipeline.
        msg = MagicMock()
        msg.content = "test"
        msg.source = "api"
        msg.message_metadata = None
        message_repo.get = MagicMock(return_value=msg)

        # Execution gate stub (transparent passthrough). Phase 5 of
        # the CorrelationManager migration: the pipeline snapshots
        # ``manager.execution_gate`` at construction time, so the
        # gate MUST be installed BEFORE constructing the
        # processor. (Pre-Phase-5 code did dynamic lookup of
        # ``self._manager.execution_gate`` at call time, which
        # masked this ordering issue.)
        from daemon.services.execution_gate import ExecutionGateService

        async def _passthrough(*a, **kw):
            return await kw["work_fn"]()

        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        manager.execution_gate = gate

        task_repo = MagicMock()
        event_repo = MagicMock()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=event_repo,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        with pytest.raises(ValueError, match="worker pool boom"):
            await processor.process(task)

        # All 3 side-effects must still fire
        manager._event_bus.create_error_event.assert_awaited_once()
        manager._publish_instance_lifecycle_event.assert_awaited_once()
        manager._send_error_report.assert_awaited_once()

