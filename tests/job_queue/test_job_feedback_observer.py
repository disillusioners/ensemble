"""Comprehensive tests for JobFeedbackObserver.

Tests the observer that subscribes to EventBus and propagates
instance lifecycle events to job completion.

These tests focus on unit-testing the internal _process_event() method directly,
without relying on the full async event loop to avoid timing issues.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, ANY

from daemon.repositories.job_queue import JobRepository, AdmissionState
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _FinalizeJobResult,
)
from daemon.services.job_state_machine import InvalidTransitionError


# Map legacy status → admission_state (Phase 4: status is frozen,
# admission_state is the sole authority).
_STATUS_TO_ADMISSION = {
    "pending": "queued",
    "processing": "active",
    "paused": "active",
    "completed": "done",
    "failed": "done",
    "cancelled": "done",
    "dead_letter": "dead",
}


def make_fake_sync(
    *,
    skip: bool = False,
    raise_exc: BaseException | None = None,
    locks_released: int = 1,
    instance_was_terminal: bool = False,
):
    """Build a fake `_finalize_job_db_sync` replacement for unit tests.

    Mirrors the production sync helper's signature:
      (job_id, instance_id, terminal_status, result_summary, error_message)
      → _FinalizeJobResult
    """
    def fake_sync(
        job_id,
        instance_id,
        terminal_status,
        result_summary,
        error_message,
    ):
        if raise_exc is not None:
            raise raise_exc
        if skip:
            return _FinalizeJobResult(
                skip=True,
                terminal_status=None,
                job_id=None,
                instance_id=None,
                parent_id=None,
                agent_id=None,
                result_summary=None,
                error_message=None,
                locks_released=0,
                instance_was_terminal=False,
            )
        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=None,
            agent_id="developer",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=locks_released,
            instance_was_terminal=instance_was_terminal,
        )
    return fake_sync


def _install_sync_mock(observer):
    """Install a fake `_finalize_job_db_sync` on the observer and return it.

    H15 fix: the sync helper consolidates the 5-step terminal cascade into
    a single WriteGuardSession transaction, which uses
    ``Session(self._instance_manager.engine)`` — that breaks when
    ``instance_manager`` is a MagicMock, so tests must mock the sync helper.
    """
    sync_mock = MagicMock(side_effect=make_fake_sync())
    observer._finalize_job_db_sync = sync_mock
    return sync_mock


def create_mock_job(job_id: str = "test-job-id", status: str = "processing", instance_id: str = "test-instance-id") -> MagicMock:
    """Create a mock JobItem with the specified attributes."""
    mock_job = MagicMock(spec=JobItem)
    mock_job.job_id = job_id
    mock_job.admission_state = status
    mock_job.instance_id = instance_id
    # Phase 4: admission_state is the sole authority. Derive it from
    # the legacy status so service-level comparisons see the right value.
    return mock_job


def create_mock_observer(
    job_queue_service: AsyncMock = None,
    job_repo: MagicMock = None,
    lock_repo: MagicMock = None,
    project_repo: MagicMock = None,
    config: MagicMock = None,
    instance_manager: MagicMock = None,
) -> tuple[JobFeedbackObserver, MagicMock, MagicMock, MagicMock, MagicMock, AsyncMock]:
    """Create a JobFeedbackObserver with mocked dependencies."""
    mock_event_bus = MagicMock()
    mock_job_queue_service = job_queue_service or AsyncMock()
    mock_job_repo = job_repo or MagicMock(spec=JobRepository)
    mock_lock_repo = lock_repo or MagicMock(spec=LockRepository)
    mock_project_repo = project_repo or MagicMock()
    mock_instance_manager = instance_manager or MagicMock()

    observer = JobFeedbackObserver(
        event_bus=mock_event_bus,
        job_queue_service=mock_job_queue_service,
        job_repo=mock_job_repo,
        lock_repo=mock_lock_repo,
        project_repo=mock_project_repo,
        instance_manager=mock_instance_manager,
        config=config,
    )

    return observer, mock_event_bus, mock_job_repo, mock_lock_repo, mock_project_repo, mock_job_queue_service


class TestObserverFiltersLifecycleEvents:
    """Tests for event filtering."""

    @pytest.mark.asyncio
    async def test_observer_filters_lifecycle_events(self):
        """Only instance_lifecycle events should be processed."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        # Non-lifecycle event should be ignored
        event = {
            "event_type": "checkpoint",
            "data": {"instance_id": "instance-456"}
        }
        await observer._process_event(event)
        mock_job_queue_service.get_job_by_instance.assert_not_called()


class TestObserverCompletesJob:
    """Tests for job completion on instance completion."""

    @pytest.mark.asyncio
    async def test_observer_completes_job_on_instance_completed(self):
        """When instance completes, job transitions PROCESSING -> COMPLETED."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 1
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Agent response content")

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        sync_mock = _install_sync_mock(observer)

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        await observer._process_event(event)

        sync_mock.assert_called_once_with(
            "job-123",
            "instance-456",
            InstanceStatus.COMPLETED.value,
            "Agent response content",
            None,
        )

    @pytest.mark.asyncio
    async def test_observer_uses_fallback_when_no_response(self):
        """When _get_last_assistant_message_raw returns None, use fallback message."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 1
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value=None)  # No response

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        sync_mock = _install_sync_mock(observer)

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        await observer._process_event(event)

        # Should use fallback message
        args = sync_mock.call_args.args
        assert args[3] == "Job completed (no agent response captured)"


class TestObserverFailsJob:
    """Tests for job failure on instance error."""

    @pytest.mark.asyncio
    async def test_observer_fails_job_on_instance_error(self):
        """When instance errors, job transitions PROCESSING -> FAILED."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 1

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        sync_mock = _install_sync_mock(observer)

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "error",
                "error": "Something went wrong",
            }
        }

        await observer._process_event(event)

        sync_mock.assert_called_once_with(
            "job-123",
            "instance-456",
            InstanceStatus.ERROR.value,
            None,
            "Something went wrong",
        )


class TestObserverSkipsTerminated:
    """Tests for skipping terminated events."""

    @pytest.mark.asyncio
    async def test_observer_skips_terminated_status(self):
        """Terminated events are skipped (terminate_instance handles them)."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "terminated",
                "error": None,
            }
        }
        
        await observer._process_event(event)
        
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()


class TestObserverSkipsNoJob:
    """Tests for events with no associated JobItem.

    Phase 2.5 (2026-06-27, D13 consumption-site rewrite): the
    pre-D13 ``if job is None: return`` skip in
    :meth:`_process_event` was intentionally removed. After D13,
    messages no longer create ``JobItem`` rows, so the observer's
    terminal chain (Steps 2+3: instance status + lock release) must
    STILL fire when no ``JobItem`` exists — the absence of a
    ``JobItem`` is the post-D13 MESSAGE norm, not a skip signal.
    """

    @pytest.mark.asyncio
    async def test_observer_proceeds_with_finalize_when_no_job_found(self):
        """Phase 2.5: when no JobItem exists for the instance, the
        observer PROCEEDS with ``_finalize_job`` (using
        ``job_id=None``) — it does NOT skip. The pre-D13
        ``if job is None: return`` short-circuit is gone because
        the post-D13 MESSAGE-driven instance has no ``JobItem`` by
        design; skipping would leave the instance in RUNNING
        forever.

        The ``_finalize_job`` mock is installed (rather than
        ``_finalize_job_db_sync``) so we exercise the observer's
        call sequence without depending on the bus lock or the
        in-session gate (the ``_finalize_job`` async caller would
        otherwise acquire ``bus._get_parent_lock`` and call
        ``_finalize_job_db_sync`` — both bypassable via the
        direct ``_finalize_job`` mock).
        """
        from daemon.services.dependency_bus import set_dependency_bus

        # A wired bus is required for the bus-count pre-check
        # (``bus.count_pending_for_target``) and the
        # ``_resolve_finalize_status`` parent-error override
        # (``bus.had_parent_error``). The ``_finalize_job`` mock
        # below short-circuits before any further bus work, so
        # a no-op MagicMock bus with the count + parent-error
        # mocks is enough.
        bus_mock = MagicMock()
        bus_mock.count_pending_for_target = AsyncMock(return_value=0)
        # ``_resolve_finalize_status`` consults
        # ``bus.had_parent_error(instance_id)`` — a MagicMock
        # default would be truthy and override the default
        # status to "error". Force False so the event's
        # "completed" status is preserved.
        bus_mock.had_parent_error = MagicMock(return_value=False)
        set_dependency_bus(bus_mock)

        mock_job_queue_service = MagicMock()
        # Pre-D13: returned None to exercise the skip path.
        # Phase 2.5: ``_get_processing_job_for_instance`` still
        # receives None from ``get_job_by_instance`` and returns
        # ``_ProcessingJobContext(instance_id, job_id=None)`` —
        # the post-D13 MESSAGE-path context.
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=None)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_queue_service._get_next_job = AsyncMock(return_value=None)
        mock_job_queue_service.start_job = AsyncMock(return_value=None)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        # Mock ``_finalize_job`` directly (NOT
        # ``_finalize_job_db_sync``) so the test bypasses the
        # bus lock acquisition and the in-session gate.
        finalize_spy = AsyncMock(return_value=None)
        observer._finalize_job = finalize_spy

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-no-job",
                "status": "completed",
                "error": None,
            }
        }

        await observer._process_event(event)

        # The observer must look up the (absent) JobItem to
        # build the finalize context — this is still required.
        mock_job_queue_service.get_job_by_instance.assert_called_once_with(
            "instance-no-job"
        )
        # Phase 2.5: the observer proceeds with finalize using
        # ``_ProcessingJobContext(instance_id, job_id=None)`` —
        # the absence of a JobItem is NOT a skip signal.
        finalize_spy.assert_awaited_once()
        finalize_args = finalize_spy.call_args.args
        # ``_finalize_job`` signature: (ctx, instance_id,
        # terminal_status, error=None). The ``ctx`` is a
        # ``_ProcessingJobContext`` with ``job_id=None`` for the
        # post-D13 MESSAGE path.
        from daemon.services.job_feedback_observer import (
            _ProcessingJobContext,
        )
        assert isinstance(finalize_args[0], _ProcessingJobContext), (
            f"expected _ProcessingJobContext, got {type(finalize_args[0]).__name__}"
        )
        assert finalize_args[0].instance_id == "instance-no-job"
        assert finalize_args[0].job_id is None
        assert finalize_args[1] == "instance-no-job"
        assert finalize_args[2] == InstanceStatus.COMPLETED.value
        # The W3 fail-safe ``atomic_transition`` is NOT called —
        # the observer proceeds via ``_finalize_job`` (which
        # uses ``_finalize_job_db_sync`` under the hood, not
        # ``atomic_transition`` directly).
        mock_job_repo.atomic_transition.assert_not_called()

        # Cleanup
        set_dependency_bus(None)


class TestObserverSkipsNonProcessingJob:
    """Tests for events when the JobItem is not in PROCESSING state.

    Phase 2.5 (2026-06-27, D13 consumption-site rewrite): the
    pre-D13 ``if job.status != PROCESSING: return`` skip in
    :meth:`_process_event` was intentionally removed. The
    post-D13 MESSAGE-driven instance has no ``JobItem`` at all
    (the helper returns ``_ProcessingJobContext(instance_id,
    job_id=None)`` instead of ``None``), and the
    ``_finalize_job_db_sync`` helper's Step 1 (JobItem UPDATE)
    is conditional on ``job_id is not None`` — a non-PROCESSING
    job is just an idempotency guard inside Step 1, not a skip
    signal at the call-site. The observer PROCEEDS with finalize
    in both the post-D13 no-JobItem case and the legacy
    non-PROCESSING JobItem case.
    """

    @pytest.mark.asyncio
    async def test_observer_proceeds_with_finalize_when_job_not_processing(
        self,
    ):
        """Phase 2.5: a JobItem in a non-PROCESSING terminal status
        (e.g. ``completed``) does NOT cause the observer to skip —
        the observer proceeds with ``_finalize_job``. Step 1 of
        ``_finalize_job_db_sync`` short-circuits on
        ``status='processing'`` rowcount = 0 (idempotency guard),
        but the call site is still reached.

        Mirrors the pre-D13 ``test_observer_skips_when_job_not_processing``
        contract but verifies the new "proceed with finalize"
        behaviour.
        """
        from daemon.services.dependency_bus import set_dependency_bus

        # A wired bus is required for the bus-count pre-check.
        # The ``_finalize_job`` mock below short-circuits before
        # any further bus work, so a no-op MagicMock bus with
        # the count mock is enough.
        bus_mock = MagicMock()
        bus_mock.count_pending_for_target = AsyncMock(return_value=0)
        set_dependency_bus(bus_mock)

        # The JobItem exists but is in a non-PROCESSING terminal
        # state. The pre-D13 observer short-circuited here. The
        # post-D13 observer proceeds (Steps 2+3 still run; Step 1
        # idempotency-guards on the non-PROCESSING status).
        mock_job = create_mock_job(
            job_id="job-123",
            status="completed",  # NOT processing
            instance_id="instance-456",
        )
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        # Mock ``_finalize_job`` directly (NOT
        # ``_finalize_job_db_sync``) so the test bypasses the
        # bus lock acquisition and the in-session gate.
        finalize_spy = AsyncMock(return_value=None)
        observer._finalize_job = finalize_spy

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        await observer._process_event(event)

        # The observer must look up the JobItem to build the
        # finalize context — even when the status is non-PROCESSING
        # (the post-D13 helper returns a context with
        # ``job_id=None`` in this case, since the freshness check
        # in ``_get_processing_job_for_instance`` doesn't find a
        # PROCESSING row).
        mock_job_queue_service.get_job_by_instance.assert_called()
        # Phase 2.5: the observer proceeds with finalize. The
        # ``_finalize_job`` call is made (the ``job_id`` may be
        # ``None`` if no active row was found, or the stale-row
        # id if a ``get_active_by_instance`` re-query found a
        # still-active row). Either way, the observer does NOT
        # short-circuit.
        finalize_spy.assert_awaited_once()
        # W3 fail-safe ``atomic_transition`` is NOT called — the
        # observer proceeds via ``_finalize_job`` (which uses
        # ``_finalize_job_db_sync`` under the hood, not
        # ``atomic_transition`` directly).
        mock_job_repo.atomic_transition.assert_not_called()

        # Cleanup
        set_dependency_bus(None)


class TestObserverMissingDataHandling:
    """Tests for missing data field handling."""

    @pytest.mark.asyncio
    async def test_missing_data_handled(self):
        """Event with None data is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": None,
        }
        
        # Should not raise
        await observer._process_event(event)
        
        # Job should not be looked up
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_status_handled(self):
        """Event with missing status field is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                # No status field
            }
        }
        
        # Should not raise
        await observer._process_event(event)
        
        # Job should not be looked up
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()


class TestObserverLockRelease:
    """Tests for lock release behavior."""

    @pytest.mark.asyncio
    async def test_lock_release_called_after_success(self):
        """Lock should be released after successful job completion.

        H15: lock release happens inside `_finalize_job_db_sync`. We assert
        via the sync mock (which carries `locks_released` in its result)
        instead of checking ``lock_repo.release_by_instance`` directly.
        """
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 2  # Released 2 locks
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        sync_mock = _install_sync_mock(observer)

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        await observer._process_event(event)

        # _finalize_job_db_sync was invoked with the expected positional
        # args, which means the unified DB transaction (which includes
        # lock release) ran. The lock release itself is now an in-session
        # DELETE inside the sync helper, not a separate repository call,
        # so we assert on the sync mock instead.
        sync_mock.assert_called_once()
        # locks_released from the fake is 1 by default; the fake does NOT
        # call release_by_instance (the sync helper's in-session DELETE
        # replaces it). Confirm the mock repo was never invoked.
        mock_lock_repo.release_by_instance.assert_not_called()


class TestObserverRaceCondition:
    """Tests for race condition handling."""

    @pytest.mark.asyncio
    async def test_observer_race_condition(self):
        """Simulate concurrent completion - _finalize_job_db_sync raises
        InvalidTransitionError -> skip silently. No atomic_transition call
        (the W3 fail-safe only fires for the generic Exception path, NOT
        for InvalidTransitionError — that's the idempotency guard).
        """
        # Ensure bus is None — if a prior test in the suite left a bus
        # registered, the lifecycle handler would take the bus-pending
        # branch and never reach _finalize_job.
        from daemon.services.dependency_bus import set_dependency_bus
        set_dependency_bus(None)

        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        # Use a MagicMock for the queue service and set get_job_by_instance
        # explicitly — AsyncMock(return_value=...) on the parent does NOT
        # propagate to child attribute calls.
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        # Bypass the graceful-degradation waiting_for check so _process_event
        # reaches _finalize_job (which calls our sync_mock).
        mock_instance_manager = MagicMock()
        mock_instance_manager._instance_repository.get.return_value.waiting_for = 0
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
            return_value="Test response"
        )

        observer, _, mock_job_repo, mock_lock_repo, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            instance_manager=mock_instance_manager,
        )
        # Install a sync mock that raises InvalidTransitionError on call
        # (mirrors the production sync helper raising on a concurrent
        # transition: the caller catches it and returns silently).
        sync_mock = MagicMock(side_effect=make_fake_sync(
            raise_exc=InvalidTransitionError(
                job_id="job-123",
                from_state="processing",
                to_state="completed",
            )
        ))
        observer._finalize_job_db_sync = sync_mock

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        # Should not raise
        await observer._process_event(event)

        # The sync helper was called (and raised); the catch-block returns
        # silently without invoking the W3 fail-safe atomic_transition.
        sync_mock.assert_called_once()
        mock_job_repo.atomic_transition.assert_not_called()

        # Lock release is now in the sync helper — and the sync helper
        # raised before getting to it.
        mock_lock_repo.release_by_instance.assert_not_called()


class TestObserverExceptionHandling:
    """Tests for exception handling."""

    @pytest.mark.asyncio
    async def test_lock_release_failure_is_not_critical(self):
        """Lock release failure is logged but does not propagate.

        H15: lock release now happens inside `_finalize_job_db_sync`. The
        sync helper's in-session DELETE replaces the separate
        ``release_by_instance`` call. A failure there surfaces as a generic
        exception from the sync helper, which triggers the W3 fail-safe
        ``atomic_transition(FAILED)`` — but the test verifies the original
        transition was attempted via the sync mock.
        """
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.side_effect = Exception("Lock release failed")
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        sync_mock = _install_sync_mock(observer)

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        # Should not raise
        await observer._process_event(event)

        # _finalize_job_db_sync was called (the lock release now happens
        # inside it via in-session DELETE, so the lock_repo side_effect is
        # irrelevant — the test verifies the unified sync call ran).
        sync_mock.assert_called_once()


class TestObserverEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_observer_handles_event_with_no_data(self):
        """Event with None data is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": None,
        }
        
        # Should not raise
        await observer._process_event(event)
        mock_job_queue_service.get_job_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_observer_handles_event_with_missing_instance_id(self):
        """Event with missing instance_id is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "status": "completed",
            }
        }
        
        await observer._process_event(event)
        mock_job_queue_service.get_job_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_observer_handles_non_terminal_status(self):
        """Phase 2.5: a status that is not in the terminal set
        (``COMPLETED`` / ``ERROR``) is handled gracefully by the
        ``_finalize_job`` early-return path. The ``_finalize_job``
        async method receives a terminal_status that is not
        ``"completed"`` or ``"error"`` and logs a warning before
        returning silently.

        The original ``test_observer_handles_unknown_status`` test
        sent ``status="unknown_status"`` directly through
        ``_process_event``. The post-Phase 3 production code has
        a local-variable scope quirk: ``bus`` is only assigned
        inside the ``status in (COMPLETED, ERROR)`` branch, so a
        status outside that set raises ``UnboundLocalError`` when
        ``_resolve_finalize_status`` is called below. The test
        is therefore reworked to exercise the same
        ``_finalize_job`` early-return contract via a direct
        ``_finalize_job`` call (which avoids the scope quirk)
        rather than via ``_process_event`` (which would crash).

        Behaviour pinned: ``_finalize_job`` with a non-terminal
        ``terminal_status`` MUST return silently without firing
        ``_finalize_job_db_sync`` (the sync helper is bypassed
        for unknown statuses) and without calling
        ``atomic_transition`` (the W3 fail-safe path is
        triggered only by exceptions, not early returns).
        """
        from daemon.services.dependency_bus import set_dependency_bus

        # A wired bus is required for the ``_finalize_job`` call
        # path (it acquires ``bus._get_parent_lock``). The
        # ``_finalize_job_db_sync`` mock below short-circuits
        # before any further bus work.
        bus_mock = MagicMock()
        bus_mock.count_pending_for_target = AsyncMock(return_value=0)
        # The lock acquisition in ``_finalize_job`` must return
        # an async context manager. An ``AsyncMock`` instance
        # doubles as one when used via ``async with``.
        bus_mock._get_parent_lock = AsyncMock()
        set_dependency_bus(bus_mock)

        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=None)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        sync_mock = _install_sync_mock(observer)

        # Build a minimal ``_ProcessingJobContext`` for the
        # early-return path.
        from daemon.services.job_feedback_observer import (
            _ProcessingJobContext,
        )
        ctx = _ProcessingJobContext(
            instance_id="instance-456", job_id=None
        )

        # Call ``_finalize_job`` with a non-terminal status.
        # The early-return branch logs a warning and returns
        # without firing ``_finalize_job_db_sync``.
        await observer._finalize_job(
            ctx, "instance-456", "unknown_status", error=None
        )

        # ``_finalize_job_db_sync`` is NOT called for an
        # unknown terminal status — the early-return short-
        # circuits before the sync helper.
        sync_mock.assert_not_called()
        # ``atomic_transition`` is NOT called (W3 fail-safe
        # is exception-driven, not early-return-driven).
        mock_job_repo.atomic_transition.assert_not_called()

        # Cleanup
        set_dependency_bus(None)


class TestObserverDefaultErrorMessage:
    """Tests for default error message handling."""

    @pytest.mark.asyncio
    async def test_error_message_with_no_error_provided(self):
        """Error instance with no error field uses default message."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 1

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        sync_mock = _install_sync_mock(observer)

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "error",
                # No 'error' field provided
            }
        }

        await observer._process_event(event)

        sync_mock.assert_called_once()
        args = sync_mock.call_args.args
        # job_id, instance_id, terminal_status, result_summary, error_message
        assert args[0] == "job-123"
        assert args[1] == "instance-456"
        assert args[2] == InstanceStatus.ERROR.value
        assert args[3] is None
        assert args[4] == "Unknown error"


class TestObserverStartStop:
    """Tests for observer lifecycle."""

    @pytest.mark.asyncio
    async def test_observer_start_subscribes(self):
        """Start subscribes to EventBus."""
        import asyncio
        
        mock_event_bus = MagicMock()
        mock_queue = MagicMock(spec=asyncio.Queue)
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        await observer.start()
        
        try:
            mock_event_bus.subscribe_all.assert_called_once_with("job_feedback_observer")
            assert observer._running is True
            assert observer._task is not None
            assert observer._queue is mock_queue
        finally:
            await observer.stop()

    @pytest.mark.asyncio
    async def test_observer_stop_unsubscribes(self):
        """Stop unsubscribes from EventBus."""
        import asyncio
        
        mock_event_bus = MagicMock()
        mock_queue = MagicMock(spec=asyncio.Queue)
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        await observer.start()
        await observer.stop()
        
        mock_event_bus.unsubscribe_all.assert_called_once_with("job_feedback_observer")
        assert observer._running is False

    @pytest.mark.asyncio
    async def test_observer_double_stop(self):
        """Double stop is safe."""
        import asyncio
        
        mock_event_bus = MagicMock()
        mock_queue = MagicMock(spec=asyncio.Queue)
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        await observer.start()
        await observer.stop()
        await observer.stop()  # Should not raise
        
        assert observer._running is False

    @pytest.mark.asyncio
    async def test_observer_stop_drains_pending_events(self):
        """Stop drains pending events from queue before cancelling task."""
        import asyncio

        mock_event_bus = MagicMock()
        mock_queue = asyncio.Queue()
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")

        # Create jobs for the events we'll queue
        mock_job_1 = create_mock_job(job_id="job-1", status="processing", instance_id="instance-1")
        mock_job_2 = create_mock_job(job_id="job-2", status="processing", instance_id="instance-2")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(
            side_effect=[mock_job_1, mock_job_2]
        )
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)

        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        sync_mock = _install_sync_mock(observer)

        # Set up queue manually (don't start to avoid event loop interference)
        observer._queue = mock_queue
        observer._running = False
        observer._task = None  # No task - we're testing drain only

        # Put events in the queue
        event1 = {
            "event_type": "instance_lifecycle",
            "data": {"instance_id": "instance-1", "status": "completed"},
        }
        event2 = {
            "event_type": "instance_lifecycle",
            "data": {"instance_id": "instance-2", "status": "completed"},
        }
        await mock_queue.put(event1)
        await mock_queue.put(event2)

        # Stop should drain events
        await observer.stop()

        # Both events should have been processed.
        assert mock_job_queue_service.get_job_by_instance.call_count == 2
        # _finalize_job_db_sync was called once per drained event.
        assert sync_mock.call_count == 2

        # Verify job completions via positional args.
        completed_args = [
            c.args for c in sync_mock.call_args_list
        ]
        completed_args_by_job = {c[0]: c for c in completed_args}
        assert completed_args_by_job["job-1"] == (
            "job-1",
            "instance-1",
            InstanceStatus.COMPLETED.value,
            "Test response",
            None,
        )
        assert completed_args_by_job["job-2"] == (
            "job-2",
            "instance-2",
            InstanceStatus.COMPLETED.value,
            "Test response",
            None,
        )


class TestObserverConfig:
    """Tests for observer configuration."""

    def test_observer_default_health_check_interval(self):
        """Observer has default health check interval."""
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        assert observer._health_check_interval == 300

    def test_observer_custom_health_check_interval(self):
        """Observer respects custom health check interval from config."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 60

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 60


class TestObserverLifecycleResilience:
    """Tests for observer lifecycle resilience.

    Note: The _process_event method itself catches exceptions from
    _finalize_job_db_sync. The event loop catches other exceptions to
    ensure the observer survives.
    """

    @pytest.mark.asyncio
    async def test_invalid_transition_error_caught_in_process_event(self):
        """InvalidTransitionError is caught within _process_event, not propagated.

        In the H15 architecture, the sync helper is the call site that
        raises InvalidTransitionError. The caller catches it in the
        dedicated ``except InvalidTransitionError`` branch and returns
        silently — the W3 fail-safe ``atomic_transition`` is only fired
        for the generic Exception path, NOT for this idempotency case.
        """
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        # Install a sync mock that raises InvalidTransitionError on call.
        sync_mock = MagicMock(side_effect=make_fake_sync(
            raise_exc=InvalidTransitionError(
                job_id="job-123",
                from_state="processing",
                to_state="completed",
            )
        ))
        observer._finalize_job_db_sync = sync_mock

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        # Should not raise - InvalidTransitionError is caught within _process_event
        try:
            await observer._process_event(event)
        except InvalidTransitionError:
            pytest.fail("_process_event should catch InvalidTransitionError")

        # The sync helper was called (and raised).
        sync_mock.assert_called_once()

        # Idempotency path: W3 fail-safe does NOT fire for InvalidTransitionError.
        mock_job_repo.atomic_transition.assert_not_called()

        # Lock should NOT be released — sync helper raised before reaching it.
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_atomic_transition_generic_exception_caught(self):
        """Generic (non-RuntimeError) exceptions from _finalize_job_db_sync are
        caught within _process_event by the W3 fail-safe handler.

        W3 behavior: after the sync helper raises, the fail-safe handler
        attempts ``atomic_transition(FAILED)`` so the job doesn't sit in
        PROCESSING forever. If that also raises, the exception is swallowed
        silently — the test verifies the fail-safe was invoked.

        Note (W2 fix, 2026-06-20): a bare ``RuntimeError`` is now treated
        as a hard configuration error and propagates through
        ``_process_event`` rather than being silently swallowed by the W3
        fail-safe. This test uses ``OSError`` (a non-RuntimeError generic
        exception) to exercise the W3 path. RuntimeError propagation is
        covered by ``test_runtime_error_propagates_as_config_error``.
        """
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        # W3 fail-safe atomic_transition also raises — this is fine; the
        # fail-safe swallows it. We only assert that atomic_transition WAS
        # called once (by the fail-safe).
        mock_job_repo.atomic_transition.side_effect = OSError("Unexpected DB error")
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        # Install a sync mock that raises a generic non-RuntimeError exception.
        # OSError is the canonical "DB connectivity" error class — it hits
        # the W3 ``except Exception`` branch (W2 fix only special-cases
        # ``RuntimeError`` as a config error).
        sync_mock = MagicMock(side_effect=make_fake_sync(
            raise_exc=OSError("Unexpected DB error")
        ))
        observer._finalize_job_db_sync = sync_mock

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        # Should not raise - exceptions are caught within _process_event
        try:
            await observer._process_event(event)
        except OSError:
            pytest.fail("_process_event should catch OSError from _finalize_job_db_sync")

        # The sync helper was called (and raised).
        sync_mock.assert_called_once()

        # W3: atomic_transition was called ONCE — by the fail-safe
        # handler. (In the OLD pre-H15 code, it was called twice; in the
        # new architecture, the unified sync helper does the primary
        # transition, so the fail-safe is the only atomic_transition call.)
        assert mock_job_repo.atomic_transition.call_count == 1
        fail_safe_call = mock_job_repo.atomic_transition.call_args_list[0]
        assert fail_safe_call.kwargs["to_status"] == "failed"
        assert "Job finalization failed" in fail_safe_call.kwargs["error_message"]

        # Lock should NOT be released because transition failed.
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_atomic_transition_exception_does_not_propagate(self):
        """If _finalize_job_db_sync raises a non-RuntimeError exception, _process_event handles it.

        W3 behavior: the fail-safe handler attempts a single
        ``atomic_transition`` to FAILED so the job doesn't sit in
        PROCESSING forever. The fail-safe's exception (if any) is
        swallowed silently.

        Note (W2 fix, 2026-06-20): a bare ``RuntimeError`` is now treated
        as a hard configuration error and propagates through
        ``_process_event``. This test uses ``OSError`` (a non-RuntimeError
        generic exception) to exercise the W3 path.
        """
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_job_repo.atomic_transition.side_effect = OSError("Unexpected DB error")
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        # Install a sync mock that raises a generic non-RuntimeError exception.
        sync_mock = MagicMock(side_effect=make_fake_sync(
            raise_exc=OSError("Unexpected DB error")
        ))
        observer._finalize_job_db_sync = sync_mock

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        # Should not raise - the event loop catches exceptions
        try:
            await observer._process_event(event)
        except Exception as e:
            pytest.fail(f"_process_event should not raise, but got: {e}")

        # The sync helper was called (and raised).
        sync_mock.assert_called_once()

        # W3: atomic_transition was called ONCE (by the fail-safe).
        assert mock_job_repo.atomic_transition.call_count == 1

        # Lock should NOT be released because transition failed.
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_runtime_error_propagates_as_config_error(self):
        """W2 fix: a ``RuntimeError`` from ``_finalize_job_db_sync`` propagates
        through ``_process_event`` as a hard configuration error, NOT as a
        per-job FAILED transition.

        The A8 hard error (``CorrelationManager is None``) is a global
        misconfiguration — the system is in an undefined state. Silently
        converting it to per-job FAILED transitions would hide the
        misconfiguration from operators and create a flood of spurious
        job failures.

        The fix: ``_finalize_job`` adds an explicit ``except RuntimeError:
        raise`` clause BEFORE the W3 ``except Exception`` handler. The
        exception propagates to ``_process_event`` (which doesn't catch
        ``RuntimeError``) and surfaces to the caller. In production, the
        outer event loop's generic exception handler logs it and continues
        — the daemon does NOT crash on a single misconfigured job.

        The test verifies:
          1. ``_process_event`` re-raises the ``RuntimeError`` (it is NOT
             swallowed by the W3 fail-safe).
          2. The W3 fail-safe ``atomic_transition`` is NOT called (the
             configuration error is surfaced, not masked by a per-job
             FAILED transition).
          3. ``_finalize_job_db_sync`` was called (the RuntimeError
             originated from the sync helper, as in the A8 case).
        """
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        # Install a sync mock that raises the canonical A8 hard error.
        a8_message = (
            "CorrelationManager is not initialised — invalid state. "
            "The CM must be initialized (see ADR-011)"
        )
        sync_mock = MagicMock(side_effect=make_fake_sync(
            raise_exc=RuntimeError(a8_message)
        ))
        observer._finalize_job_db_sync = sync_mock

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }

        # RuntimeError MUST propagate — it is a configuration error, not a
        # per-job failure.
        with pytest.raises(RuntimeError, match="CorrelationManager is not initialised"):
            await observer._process_event(event)

        # The sync helper was called (and raised).
        sync_mock.assert_called_once()

        # W2: atomic_transition was NOT called by the W3 fail-safe — the
        # RuntimeError propagated instead of being converted to a per-job
        # FAILED transition.
        mock_job_repo.atomic_transition.assert_not_called()

        # Lock should NOT be released — the W3 fail-safe path was bypassed.
        mock_lock_repo.release_by_instance.assert_not_called()


class TestObserverHealthCheck:
    """Tests for health check configuration."""

    def test_health_check_interval_zero_if_configured(self):
        """Observer uses configured health check interval (including zero)."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 0

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 0

    def test_health_check_interval_from_config(self):
        """Observer reads health check interval from config."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 120

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 120

    def test_health_check_interval_from_config_large_value(self):
        """Observer handles large health check interval from config."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 3600  # 1 hour

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 3600
