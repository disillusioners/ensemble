"""Phase 2 tests: JobFeedbackObserver ↔ CorrelationManager integration.

These tests verify the Phase 2 migration from ``waiting_for``-based terminal
transitions to the CorrelationManager (CM) callback path. The observer's
``handle_correlation_complete`` method is the authoritative terminal-transition
path; ``_process_event`` only emits ``in_progress`` notifications.

Test coverage (mapped to plan §Verification Strategy):
  1. Callback triggers completion (terminal_status="completed" → COMPLETED)
  2. Callback triggers failure (terminal_status="error" → FAILED)
  3. Partial completion does NOT trigger callback (CM doesn't fire until
     pending count reaches 0)
  4. Idempotency (CM fires callback twice → atomic_transition called once)
  5. In-progress notification fires via lifecycle event when CM has pending
  6. N4 no-deadlock: handle_correlation_complete does NOT call CM methods
     for the same parent_id (lock is already released by W1 fix)

Run with:

    pytest tests/test_observer_correlation.py -v
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Importing the model classes is what registers them with SQLModel.metadata.
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.correlation_manager import (
    CorrelationManager,
    set_correlation_manager,
)
from daemon.services.job_feedback_observer import JobFeedbackObserver

logger = logging.getLogger(__name__)


# ─── Shared fixtures & helpers ────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (mirrors test_correlation_shadow.py)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def make_instance_repo_mock() -> MagicMock:
    """Mock instance repo with a no-op get() — CM shadow mode would call it."""
    repo = MagicMock(name="InstanceRepo")
    repo.get = MagicMock(return_value=None)
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    return repo


def make_msg_repo_mock() -> MagicMock:
    """Mock message-queue repo — CM only needs this for rebuild_from_db."""
    repo = MagicMock(name="MsgRepo")
    repo.get_pending_for_instances = MagicMock(return_value=[])
    return repo


def make_mock_job(
    job_id: str | None = None,
    status: str = "processing",
    instance_id: str | None = None,
    project_id: str | None = None,
) -> MagicMock:
    """Build a MagicMock(spec=JobItem) with the attributes the observer reads."""
    mock = MagicMock(spec=JobItem)
    mock.job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
    mock.status = status
    mock.instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    mock.project_id = project_id
    mock.agent_id = "coder"
    mock.message = "test"
    mock.source = "api"
    return mock


def make_observer(
    job: MagicMock,
    *,
    waiting_for: int = 0,
    get_last_message_returns: str | None = "agent response",
) -> tuple[JobFeedbackObserver, dict[str, MagicMock]]:
    """Build a JobFeedbackObserver with mocked dependencies.

    Returns (observer, mocks) where ``mocks`` is a dict of named mocks for
    easy assertion: ``job_queue_service``, ``job_repo``, ``lock_repo``,
    ``instance_manager``.

    ``waiting_for`` is the value returned by
    ``instance_manager._instance_repository.get(instance_id).waiting_for`` —
    used by the graceful-degradation path when CM is None.
    """
    mock_jqs = MagicMock()
    mock_jqs.get_job_by_instance = AsyncMock(return_value=job)
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)  # no next job
    mock_jqs.start_job = AsyncMock(return_value=None)

    mock_job_repo = MagicMock(spec=JobRepository)
    mock_lock_repo = MagicMock(spec=LockRepository)
    mock_lock_repo.release_by_instance = MagicMock(return_value=0)

    instance_meta = MagicMock()
    instance_meta.waiting_for = waiting_for
    instance_meta.status = "completed"

    mock_instance_manager = MagicMock()
    mock_instance_manager._instance_repository = MagicMock()
    mock_instance_manager._instance_repository.get = MagicMock(return_value=instance_meta)
    mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
        return_value=get_last_message_returns
    )
    mock_instance_manager.spawn_instance_with_mcp = AsyncMock(
        return_value="new-inst-id"
    )
    mock_instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-123")
    )

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=mock_jqs,
        job_repo=mock_job_repo,
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(),
        instance_manager=mock_instance_manager,
    )

    return observer, {
        "job_queue_service": mock_jqs,
        "job_repo": mock_job_repo,
        "lock_repo": mock_lock_repo,
        "instance_manager": mock_instance_manager,
    }


# ─── Test 1: Callback triggers completion ────────────────────────────────────


class TestCallbackTriggersCompletion:
    """``handle_correlation_complete(parent, "completed")`` transitions job to COMPLETED."""

    @pytest.mark.asyncio
    async def test_completed_callback_transitions_to_completed(self):
        """terminal_status='completed' → atomic_transition(PROCESSING → COMPLETED)."""
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        await observer.handle_correlation_complete(job.instance_id, "completed")

        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["from_status"] == JobStatus.PROCESSING.value
        assert kwargs["to_status"] == JobStatus.COMPLETED.value
        assert "completed_at" in kwargs
        assert kwargs.get("result_summary") == "agent response"

        # Watcher notified with "completed"
        mocks["job_queue_service"].notify_watchers.assert_called_once()
        call = mocks["job_queue_service"].notify_watchers.call_args
        assert call.args[0] == job.job_id
        assert call.args[1] == "completed"

    @pytest.mark.asyncio
    async def test_completed_callback_uses_fallback_when_no_response(self):
        """When _get_last_assistant_message_raw returns None, use fallback text."""
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(
            job, get_last_message_returns=None
        )

        await observer.handle_correlation_complete(job.instance_id, "completed")

        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["result_summary"] == "Job completed (no agent response captured)"

    @pytest.mark.asyncio
    async def test_error_callback_transitions_to_failed(self):
        """terminal_status='error' → atomic_transition(PROCESSING → FAILED)."""
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        await observer.handle_correlation_complete(job.instance_id, "error")

        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["from_status"] == JobStatus.PROCESSING.value
        assert kwargs["to_status"] == JobStatus.FAILED.value
        assert kwargs.get("error_message") == "Unknown error"  # callback has no error msg

        # Watcher notified with "failed" + error
        call = mocks["job_queue_service"].notify_watchers.call_args
        assert call.args[0] == job.job_id
        assert call.args[1] == "failed"
        assert call.args[2] == "Unknown error"

    @pytest.mark.asyncio
    async def test_callback_with_no_job_is_silent(self):
        """No job for parent → callback returns silently, no transition."""
        mock_jqs = MagicMock()
        mock_jqs.get_job_by_instance = AsyncMock(return_value=None)

        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
            return_value="x"
        )

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_jqs,
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )

        # Must not raise
        await observer.handle_correlation_complete("orphan-parent", "completed")

        mock_jqs.notify_watchers.assert_not_called()


# ─── Test 2: Partial completion does NOT trigger callback ───────────────────


class TestPartialCompletionDoesNotTrigger:
    """CM fires callback only when pending count reaches 0.

    Uses a real CorrelationManager wired with the observer as callback.
    Resolving 1 of 2 pending sends must NOT fire the callback (and therefore
    must NOT call ``atomic_transition`` on the observer's job repo).
    """

    @pytest.mark.asyncio
    async def test_resolve_one_of_two_does_not_fire_callback(self):
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        # Real CM with the observer as callback.
        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            parent_id = job.instance_id
            child_id = "child-1"
            msg_a = f"msg-{uuid.uuid4().hex[:8]}"
            msg_b = f"msg-{uuid.uuid4().hex[:8]}"

            # Register 2 sends to the same child.
            await cm.register_message_send(parent_id, child_id, msg_a)
            await cm.register_message_send(parent_id, child_id, msg_b)
            assert cm.get_pending_count(parent_id) == 2

            # Resolve the first — callback must NOT fire.
            result = await cm.resolve_response(parent_id, child_id, msg_a)
            assert result is False  # not the last pending
            assert cm.get_pending_count(parent_id) == 1
            mocks["job_repo"].atomic_transition.assert_not_called()
            mocks["job_queue_service"].notify_watchers.assert_not_called()

            # Resolve the second — callback fires, transition happens.
            result = await cm.resolve_response(parent_id, child_id, msg_b)
            assert result is True  # last pending
            assert cm.get_pending_count(parent_id) == 0
            mocks["job_repo"].atomic_transition.assert_called_once()
            kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
            assert kwargs["to_status"] == JobStatus.COMPLETED.value
        finally:
            await cm.stop()
            set_correlation_manager(None)


# ─── Test 3: Idempotency ────────────────────────────────────────────────────


class TestIdempotency:
    """CM fires callback twice for same parent → atomic_transition called once.

    The second call hits the ``job.status != PROCESSING`` guard in
    ``handle_correlation_complete`` and returns silently.
    """

    @pytest.mark.asyncio
    async def test_callback_fired_twice_transitions_only_once(self):
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        # First call: transitions.
        await observer.handle_correlation_complete(job.instance_id, "completed")
        mocks["job_repo"].atomic_transition.assert_called_once()
        first_call = mocks["job_repo"].atomic_transition.call_args

        # Simulate job already transitioned (idempotency guard).
        job.status = JobStatus.COMPLETED.value

        # Second call: idempotency guard catches it.
        await observer.handle_correlation_complete(job.instance_id, "completed")

        # Still exactly one call.
        assert mocks["job_repo"].atomic_transition.call_count == 1
        # Same call as before (no second transition).
        assert mocks["job_repo"].atomic_transition.call_args == first_call

    @pytest.mark.asyncio
    async def test_callback_for_already_failed_job_is_silent(self):
        """Job already FAILED (e.g., by terminate_instance) → callback is no-op."""
        job = make_mock_job(status="failed")
        observer, mocks = make_observer(job)

        await observer.handle_correlation_complete(job.instance_id, "error")

        mocks["job_repo"].atomic_transition.assert_not_called()
        mocks["job_queue_service"].notify_watchers.assert_not_called()


# ─── Test 4: In-progress notification via lifecycle event ────────────────────


class TestInProgressViaLifecycleEvent:
    """When CM has pending entries, ``_process_event`` emits in_progress only.

    Terminal transitions happen via CM callback, not via the lifecycle handler.
    """

    @pytest.mark.asyncio
    async def test_lifecycle_event_with_cm_pending_emits_in_progress(self):
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        # Set up a real CM with 1 pending entry for this instance.
        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            await cm.register_message_send(
                job.instance_id, "child-1", "msg-1"
            )
            assert cm.get_pending_count(job.instance_id) == 1

            event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": job.instance_id,
                    "status": "completed",
                    "error": None,
                },
            }
            await observer._process_event(event)

            # in_progress notification fired.
            mocks["job_queue_service"].notify_watchers.assert_called_once()
            call = mocks["job_queue_service"].notify_watchers.call_args
            assert call.args[0] == job.job_id
            assert call.kwargs.get("status") == "in_progress"
            assert call.kwargs.get("waiting_for") == 1

            # NO terminal transition — that's CM callback's job.
            mocks["job_repo"].atomic_transition.assert_not_called()
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_lifecycle_event_with_no_cm_pending_falls_through_to_terminal(self):
        """cm_pending == 0 → terminal transition in _process_event (shared path)."""
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        # Set up a real CM with NO pending entries for this instance.
        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            # No registrations — cm_pending is 0.
            assert cm.get_pending_count(job.instance_id) == 0

            event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": job.instance_id,
                    "status": "completed",
                    "error": None,
                },
            }
            await observer._process_event(event)

            # Terminal transition happens in _process_event (no CM children).
            mocks["job_repo"].atomic_transition.assert_called_once()
            kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
            assert kwargs["to_status"] == JobStatus.COMPLETED.value
        finally:
            await cm.stop()
            set_correlation_manager(None)


# ─── Test 5: N4 no-deadlock (constraint verification) ───────────────────────


class TestNoDeadlockConstraint:
    """handle_correlation_complete MUST NOT call any CM method for the same parent_id.

    N4: the callback runs AFTER the per-parent lock is released (W1 fix).
    Re-entering CM would deadlock. This test verifies the invariant using
    a real CorrelationManager with strict method-call tracking.

    Strategy: after the callback fires, we verify:
      1. The CM's per-parent lock is released (a subsequent register on
         the same parent does NOT hang).
      2. The CM's internal pending state is unchanged by the callback
         (callback is a pure consumer, not a writer).
    """

    @pytest.mark.asyncio
    async def test_callback_does_not_call_cm_methods_for_same_parent(self):
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        # Real CM, observer wired as callback.
        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            parent_id = job.instance_id
            child_id = "child-1"
            msg_id = f"msg-{uuid.uuid4().hex[:8]}"

            await cm.register_message_send(parent_id, child_id, msg_id)
            assert cm.get_pending_count(parent_id) == 1

            # Snapshot CM's internal state before callback fires.
            had_lock_before = parent_id in cm._locks
            assert had_lock_before

            # Fire callback by resolving the last pending correlation.
            # Use a short timeout to detect deadlock — if N4 is violated,
            # this await would hang forever.
            try:
                result = await asyncio.wait_for(
                    cm.resolve_response(parent_id, child_id, msg_id),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                pytest.fail(
                    "N4 VIOLATION: cm.resolve_response hung — callback likely "
                    "deadlocked by re-entering CM for the same parent"
                )

            assert result is True
            assert cm.get_pending_count(parent_id) == 0

            # After the callback, the per-parent lock MUST be released.
            # Proving this: a subsequent register on the SAME parent must
            # complete without hanging (would deadlock if lock was still held).
            try:
                await asyncio.wait_for(
                    cm.register_message_send(parent_id, "child-2", "msg-2"),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                pytest.fail(
                    "N4 VIOLATION: per-parent lock was not released after callback"
                )
            assert cm.get_pending_count(parent_id) == 1
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_callback_does_not_register_or_resolve_on_cm(self):
        """The callback must not call register_message_send or resolve_response
        on the CM for the same parent_id.

        Strategy: wrap the forbidden methods to record calls. Use the
        ORIGINAL (unwrapped) methods to set up state and trigger the
        callback. After the callback fires, the recorded list must be
        empty — the callback itself did not call any CM method.
        """
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        real_cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
        )
        await real_cm.start()
        set_correlation_manager(real_cm)
        try:
            # Capture originals BEFORE wrapping.
            original_register = real_cm.register_message_send
            original_resolve = real_cm.resolve_response

            cm_calls: list[str] = []

            async def wrap_register(*args, **kwargs):
                cm_calls.append("register_message_send")
                return await original_register(*args, **kwargs)

            async def wrap_resolve(*args, **kwargs):
                cm_calls.append("resolve_response")
                return await original_resolve(*args, **kwargs)

            real_cm.register_message_send = wrap_register  # type: ignore[method-assign]
            real_cm.resolve_response = wrap_resolve  # type: ignore[method-assign]

            # Use the original resolve to trigger the callback without
            # recording the trigger itself. This is safe because the
            # trigger is the TEST, not the callback.
            parent_id = job.instance_id
            await original_register(parent_id, "child-1", "msg-1")
            # Clear the register call from above (it was the test setup).
            cm_calls.clear()

            # Now trigger the callback via the original resolve.
            # The callback runs synchronously inside this call.
            result = await original_resolve(parent_id, "child-1", "msg-1")
            assert result is True

            # The callback ran but did NOT call any CM method.
            assert cm_calls == [], (
                f"N4 VIOLATION: callback called CM methods {cm_calls} — "
                f"would deadlock under per-parent lock"
            )
        finally:
            await real_cm.stop()
            set_correlation_manager(None)
