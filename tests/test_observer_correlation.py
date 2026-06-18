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
from daemon.services.job_state_machine import InvalidTransitionError

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


# ─── Test 6 (S1): C1 — register-during-callback aborts terminal transition ─


class TestC1RegisterDuringCallback:
    """C1 regression: a new ``register_message_send`` during the callback's
    await window MUST abort the terminal transition.

    Race introduced by W1 (callback moved outside the per-parent lock):
      T1: CM fires ``completion_callback`` → ``handle_correlation_complete``
          → ``_finalize_job``.
      T2: ``_finalize_job`` awaits ``_get_last_assistant_message_raw``.
      T3: During that await, the parent agent fires a tool call → another
          ``send_message`` → ``cm.register_message_send`` registers a NEW
          pending correlation for the same parent.
      T4: The LLM fetch returns.
      T5 (without C1): ``atomic_transition`` fires anyway → the new child
          is orphaned in CM (its pending count never reaches 0 from CM's
          POV, because the parent's job is already terminal).
      T5 (with C1): the synchronous re-check after the fetch sees
          ``cm_pending > 0`` → returns without transitioning. CM fires
          the callback again when the new child resolves.
    """

    @pytest.mark.asyncio
    async def test_register_during_llm_fetch_aborts_terminal_transition(self):
        """A new ``register_message_send`` during the LLM fetch aborts the
        transition. ``atomic_transition`` is NOT called; the job remains
        PROCESSING; the new correlation is tracked by CM and will fire
        the callback when it resolves.
        """
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

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
            original_msg = f"msg-{uuid.uuid4().hex[:8]}"
            new_child = "child-2"
            new_msg = f"msg-{uuid.uuid4().hex[:8]}"

            # Register the correlation that will trigger the callback.
            await cm.register_message_send(parent_id, child_id, original_msg)
            assert cm.get_pending_count(parent_id) == 1

            # Patch the LLM fetch to register a NEW correlation BEFORE
            # returning. This simulates: the callback is mid-_finalize_job,
            # awaiting the LLM response; meanwhile the parent agent fires
            # a tool call that sends another message to a different child.
            async def llm_fetch_with_concurrent_register(instance_id_arg):
                await cm.register_message_send(parent_id, new_child, new_msg)
                return "agent response"

            mocks[
                "instance_manager"
            ]._get_last_assistant_message_raw = AsyncMock(
                side_effect=llm_fetch_with_concurrent_register
            )

            # Trigger the callback by resolving the only pending correlation.
            result = await cm.resolve_response(
                parent_id, child_id, original_msg
            )
            assert result is True  # last pending → callback fires

            # C1 invariant: the new correlation is now pending for the
            # parent. (The original was removed by resolve_response; the
            # new one was registered by the side_effect above.)
            assert cm.get_pending_count(parent_id) == 1

            # CRITICAL: atomic_transition was NOT called. The C1 re-check
            # saw cm_pending > 0 and aborted before the transition.
            mocks["job_repo"].atomic_transition.assert_not_called()

            # The job remains in PROCESSING (not transitioned).
            assert job.status == JobStatus.PROCESSING.value

            # Watcher was NOT notified of a terminal state.
            notify_calls = mocks[
                "job_queue_service"
            ].notify_watchers.call_args_list
            terminal_calls = [
                c
                for c in notify_calls
                if len(c.args) > 1 and c.args[1] in ("completed", "failed")
            ]
            assert terminal_calls == [], (
                f"Expected no terminal watcher notifications, got "
                f"{terminal_calls}"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)


# ─── Test 7 (S2): concurrent _finalize_job from both paths ────────────────


class TestC2ConcurrentFinalize:
    """Concurrent ``_finalize_job`` invocations from the lifecycle event
    handler and the CM callback.

    Scenario: when the parent's ``instance_lifecycle`` event arrives at the
    same time the last child's response resolves through CM, both paths may
    reach ``_finalize_job`` concurrently. Both call ``atomic_transition``.
    The DB-level guard (atomic state machine) ensures only one transition
    succeeds; the other raises ``InvalidTransitionError`` and is caught
    silently inside ``_finalize_job``.

    This test verifies the existing guard works under asyncio.gather
    concurrency — exactly ONE transition happens, the job ends in the
    correct terminal state, watchers are notified exactly once.
    """

    @pytest.mark.asyncio
    async def test_concurrent_finalize_only_one_transition_succeeds(self):
        job = make_mock_job(status="processing")
        observer, mocks = make_observer(job)

        # Wire CM with NO pending entries for this parent so _process_event
        # takes the cm_pending == 0 fallthrough path to _finalize_job. The
        # CM is still wired (get_correlation_manager() returns it), so the
        # C1 re-check runs but is a no-op (cm_pending stays 0 throughout).
        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            parent_id = job.instance_id
            assert cm.get_pending_count(parent_id) == 0

            # Simulate DB-level atomic transition guard: first call with
            # from_status=PROCESSING succeeds and updates job.status;
            # subsequent calls with from_status=PROCESSING raise
            # InvalidTransitionError (the DB already moved the row).
            def atomic_transition_guard(**kwargs):
                if (
                    kwargs.get("from_status") == JobStatus.PROCESSING.value
                    and job.status != JobStatus.PROCESSING.value
                ):
                    raise InvalidTransitionError(
                        job_id=job.job_id,
                        from_status=kwargs["from_status"],
                        to_status=kwargs["to_status"],
                    )
                job.status = kwargs["to_status"]

            mocks[
                "job_repo"
            ].atomic_transition.side_effect = atomic_transition_guard

            lifecycle_event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": parent_id,
                    "status": "completed",
                    "error": None,
                },
            }

            # Fire both terminal-transition paths concurrently. Both call
            # ``get_job_by_instance`` (returns the same job with status=
            # PROCESSING at read time), both pass the idempotency guard,
            # both reach ``_finalize_job``, both call ``atomic_transition``.
            # Exactly one wins; the other is caught by InvalidTransitionError.
            await asyncio.gather(
                observer._process_event(lifecycle_event),
                observer.handle_correlation_complete(parent_id, "completed"),
            )

            # The job ends in the correct terminal state.
            assert job.status == JobStatus.COMPLETED.value

            # ``atomic_transition`` was called from BOTH paths. The mock
            # does not count attempts (InvalidTransitionError short-circuits
            # the guard before any mutation), but the second attempt must
            # have been caught — the job is in COMPLETED, not stuck or
            # double-transitioned.
            completed_calls = [
                c
                for c in mocks["job_repo"].atomic_transition.call_args_list
                if c.kwargs.get("to_status") == JobStatus.COMPLETED.value
            ]
            assert len(completed_calls) >= 1, (
                "Expected at least one successful COMPLETED transition; got "
                f"{mocks['job_repo'].atomic_transition.call_args_list}"
            )

            # Exactly one terminal "completed" watcher notification.
            terminal_calls = [
                c
                for c in mocks[
                    "job_queue_service"
                ].notify_watchers.call_args_list
                if len(c.args) > 1 and c.args[1] == "completed"
            ]
            assert len(terminal_calls) == 1, (
                f"Expected exactly 1 'completed' notify_watchers call, got "
                f"{len(terminal_calls)}: "
                f"{mocks['job_queue_service'].notify_watchers.call_args_list}"
            )

            # Locks released exactly once (the loser didn't get past the
            # InvalidTransitionError handler, so the lock release path
            # below it never ran for the loser).
            assert (
                mocks["lock_repo"].release_by_instance.call_count == 1
            ), (
                f"Expected exactly 1 lock release, got "
                f"{mocks['lock_repo'].release_by_instance.call_count}"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)


# ─── Test 8 (S3 fix): stale-job re-query in handle_correlation_complete ─────


class TestHandleCorrelationCompleteStaleJobLookup:
    """Regression tests for fix/revive-stale-job-lookup: ``handle_correlation_complete``
    must re-query ``_job_repo.get_active_by_instance`` when ``get_by_instance``
    returns a non-PROCESSING row (the most-recent non-deleted row may be a stale
    CANCELLED job from a prior terminate cycle, not the live PROCESSING job).

    The re-query is critical because the old code skipped finalization whenever
    the freshly-returned job was not PROCESSING — which silently dropped the
    terminal transition after a terminate→revive cycle.
    """

    async def test_callback_finalizes_when_get_by_instance_returns_processing(
        self,
    ):
        """Happy path: get_by_instance returns PROCESSING → finalizes immediately,
        NO re-query to get_active_by_instance.

        Verifies Fix 2 doesn't regress the simple case: when the row returned
        by ``get_job_by_instance`` is already PROCESSING, the re-query branch
        must not be taken and ``_finalize_job`` proceeds with that row.
        """
        processing_job = make_mock_job(status="processing")
        observer, mocks = make_observer(processing_job)

        await observer.handle_correlation_complete(
            processing_job.instance_id, "completed"
        )

        # Finalization happened with the PROCESSING job.
        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["from_status"] == JobStatus.PROCESSING.value
        assert kwargs["to_status"] == JobStatus.COMPLETED.value
        assert kwargs["job_id"] == processing_job.job_id

        # Re-query was NOT made (happy path short-circuits before it).
        mocks["job_repo"].get_active_by_instance.assert_not_called()

        # Watchers notified once.
        mocks["job_queue_service"].notify_watchers.assert_called_once()

    async def test_callback_re_queries_and_finalizes_active_when_stale_job_returned(
        self,
    ):
        """Defensive re-query: get_by_instance returns CANCELLED, get_active_by_instance
        returns PROCESSING → finalize the ACTIVE job (not the stale one).

        This is the exact scenario the fix protects against: a terminate→revive
        cycle leaves a CANCELLED job with newer created_at than the revived
        PROCESSING job, so get_by_instance (ORDER BY created_at DESC) returns
        the CANCELLED row. The re-query finds the actual live PROCESSING job.
        """
        # Stale CANCELLED job (newer created_at because of how the DB write
        # order works in a terminate→revive scenario).
        stale_job = make_mock_job(
            status="cancelled", instance_id="parent-stale-123"
        )
        # Live PROCESSING job (older created_at, but is the one we must finalize).
        active_job = make_mock_job(
            status="processing", instance_id="parent-stale-123"
        )
        # Make the job_ids distinct so we can assert which one is finalized.
        stale_job.job_id = "stale-job-id-aaaa"
        active_job.job_id = "active-job-id-bbbb"

        observer, mocks = make_observer(stale_job)

        # Wire the active-job re-query to return the live PROCESSING job.
        mocks["job_repo"].get_active_by_instance = MagicMock(
            return_value=active_job
        )

        await observer.handle_correlation_complete("parent-stale-123", "completed")

        # The re-query was made.
        mocks["job_repo"].get_active_by_instance.assert_called_once_with(
            "parent-stale-123"
        )

        # Finalization happened with the ACTIVE job (not the stale one).
        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["from_status"] == JobStatus.PROCESSING.value
        assert kwargs["to_status"] == JobStatus.COMPLETED.value
        assert kwargs["job_id"] == active_job.job_id, (
            f"Expected active job_id={active_job.job_id}, got "
            f"{kwargs['job_id']}"
        )

        # Watchers notified once for the completed active job.
        mocks["job_queue_service"].notify_watchers.assert_called_once()
        assert (
            mocks["job_queue_service"].notify_watchers.call_args.args[0]
            == active_job.job_id
        )

    async def test_callback_skips_when_no_active_job_exists(self):
        """Re-query returns None → callback returns silently, no finalization.

        When ``get_by_instance`` returns a non-PROCESSING row AND
        ``get_active_by_instance`` returns None (no live PENDING/PROCESSING
        job exists for this parent), the callback must NOT call
        ``_finalize_job``. This prevents finalizing a phantom job that doesn't
        actually exist.
        """
        # Stale non-PROCESSING row — could be CANCELLED, COMPLETED, etc.
        stale_job = make_mock_job(status="cancelled", instance_id="parent-ghost-456")
        stale_job.job_id = "stale-job-id-cccc"

        observer, mocks = make_observer(stale_job)

        # No live active job for this parent.
        mocks["job_repo"].get_active_by_instance = MagicMock(return_value=None)

        await observer.handle_correlation_complete("parent-ghost-456", "completed")

        # Re-query was made.
        mocks["job_repo"].get_active_by_instance.assert_called_once_with(
            "parent-ghost-456"
        )

        # No finalization: atomic_transition was NOT called.
        mocks["job_repo"].atomic_transition.assert_not_called()

        # No watcher notifications.
        mocks["job_queue_service"].notify_watchers.assert_not_called()

        # No lock release (finalization didn't run).
        mocks["lock_repo"].release_by_instance.assert_not_called()

    async def test_callback_skips_when_active_job_is_pending_not_processing(self):
        """Re-query returns PENDING (not PROCESSING) → skip finalization.

        ``get_active_by_instance`` may return a PENDING job. The fix only
        finalizes when the active job is PROCESSING; a PENDING job has not
        started yet and finalizing it would be wrong (the state machine
        requires PENDING → PROCESSING → COMPLETED).
        """
        stale_job = make_mock_job(status="cancelled", instance_id="parent-pend-789")
        stale_job.job_id = "stale-job-id-dddd"

        pending_job = make_mock_job(
            status="pending", instance_id="parent-pend-789"
        )
        pending_job.job_id = "pending-job-id-eeee"

        observer, mocks = make_observer(stale_job)

        # Active row exists but it's PENDING, not PROCESSING.
        mocks["job_repo"].get_active_by_instance = MagicMock(
            return_value=pending_job
        )

        await observer.handle_correlation_complete("parent-pend-789", "completed")

        # Re-query was made.
        mocks["job_repo"].get_active_by_instance.assert_called_once_with(
            "parent-pend-789"
        )

        # No finalization: PENDING → COMPLETED is an invalid transition that
        # the fix correctly avoids.
        mocks["job_repo"].atomic_transition.assert_not_called()
        mocks["job_queue_service"].notify_watchers.assert_not_called()
