"""Phase 2 tests: late message arrival and graceful degradation.

Two scenarios for the Phase 2 migration:

1. **Late message arrival**: A parent's job transitions to COMPLETED via the
   CM callback. Then a new ``send_message`` from the same parent re-registers
   in CM. When the new child responds, CM fires the callback again — but
   the job is already terminal, so the idempotency guard catches it.

2. **Graceful degradation**: When ``get_correlation_manager()`` returns
   ``None`` (CM disabled / not wired), the observer falls back to the
   legacy ``waiting_for``-based check. This keeps the system safe even if
   CM is broken or not yet enabled.

Run with:

    pytest tests/test_observer_late_msg.py -v
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.correlation_manager import (
    CorrelationManager,
    set_correlation_manager,
)
from daemon.services.job_feedback_observer import JobFeedbackObserver

logger = logging.getLogger(__name__)


# ─── Shared helpers ─────────────────────────────────────────────────────────


def make_instance_repo_mock() -> MagicMock:
    repo = MagicMock(name="InstanceRepo")
    repo.get = MagicMock(return_value=None)
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    return repo


def make_msg_repo_mock() -> MagicMock:
    repo = MagicMock(name="MsgRepo")
    repo.get_pending_for_instances = MagicMock(return_value=[])
    return repo


def make_mock_job(
    job_id: str | None = None,
    instance_id: str | None = None,
    project_id: str | None = "test-project",
) -> MagicMock:
    mock = MagicMock()
    mock.job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
    mock.status = "processing"
    mock.instance_id = instance_id or f"parent-{uuid.uuid4().hex[:8]}"
    mock.project_id = project_id
    mock.agent_id = "coder"
    mock.message = "test"
    mock.source = "api"
    return mock


def make_observer(
    job: MagicMock,
    *,
    waiting_for: int = 0,
) -> tuple[JobFeedbackObserver, dict[str, MagicMock]]:
    """Build observer with configurable waiting_for for graceful-degradation tests."""
    mock_jqs = MagicMock()
    mock_jqs.get_job_by_instance = AsyncMock(return_value=job)
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)
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
        return_value="agent response"
    )
    mock_instance_manager.spawn_instance_with_mcp = AsyncMock(return_value="new-inst")
    mock_instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-1")
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


def make_lifecycle_event(instance_id: str, status: str = "completed") -> dict:
    return {
        "event_type": "instance_lifecycle",
        "data": {
            "instance_id": instance_id,
            "status": status,
            "error": None,
        },
    }


# ─── Test 1: Late message arrival ───────────────────────────────────────────


class TestLateMessageArrival:
    """After CM fires callback (job COMPLETED), a new send_message re-registers
    in CM. The second callback fires the idempotency guard, not a re-transition.
    """

    @pytest.mark.asyncio
    async def test_new_send_message_after_callback_re_registers(self):
        """After the first cycle completes, a new send_message from the same
        parent re-registers in CM and starts a new correlation cycle.

        The second callback (when the new child responds) is a no-op because
        the job is already in a terminal state — the idempotency guard
        prevents a duplicate transition.
        """
        job = make_mock_job()
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
            child_1 = "child-1"
            child_2 = "child-2"
            msg_1 = f"msg-{uuid.uuid4().hex[:8]}"
            msg_2 = f"msg-{uuid.uuid4().hex[:8]}"

            # ── Cycle 1: send_message → resolve → callback → COMPLETED ──
            await cm.register_message_send(parent_id, child_1, msg_1)
            assert cm.get_pending_count(parent_id) == 1

            # Child 1 responds.
            result = await cm.resolve_response(parent_id, child_1, msg_1)
            assert result is True  # last pending

            # Callback fired; job transitioned to COMPLETED.
            mocks["job_repo"].atomic_transition.assert_called_once()
            kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
            assert kwargs["to_status"] == JobStatus.COMPLETED.value
            assert cm.get_pending_count(parent_id) == 0

            # Simulate the job now being in COMPLETED state (the real DB
            # would have this; the mock's atomic_transition doesn't mutate
            # job.status automatically).
            job.status = JobStatus.COMPLETED.value
            first_transition_count = mocks["job_repo"].atomic_transition.call_count

            # ── Cycle 2: new send_message → re-register in CM ──
            await cm.register_message_send(parent_id, child_2, msg_2)
            # CM tracks the new correlation normally.
            assert cm.get_pending_count(parent_id) == 1

            # Child 2 responds — callback fires again.
            result = await cm.resolve_response(parent_id, child_2, msg_2)
            assert result is True

            # Idempotency guard: job.status != PROCESSING → no transition.
            # atomic_transition was NOT called a second time.
            assert mocks["job_repo"].atomic_transition.call_count == first_transition_count, (
                "Second callback should NOT call atomic_transition — "
                "job is already terminal (idempotency guard)"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_lifecycle_event_after_callback_is_idempotent(self):
        """After the job is COMPLETED, a lifecycle event for the same
        instance is a no-op — _process_event sees job.status != PROCESSING
        and returns early.
        """
        job = make_mock_job()
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
            child_1 = "child-1"
            msg_1 = f"msg-{uuid.uuid4().hex[:8]}"

            # First cycle.
            await cm.register_message_send(parent_id, child_1, msg_1)
            await cm.resolve_response(parent_id, child_1, msg_1)
            mocks["job_repo"].atomic_transition.assert_called_once()

            # Simulate job in COMPLETED state.
            job.status = JobStatus.COMPLETED.value

            # Now a new lifecycle event arrives for the same instance.
            # _process_event checks job.status and returns early.
            await observer._process_event(make_lifecycle_event(parent_id))

            # No new transitions.
            mocks["job_repo"].atomic_transition.assert_called_once()
        finally:
            await cm.stop()
            set_correlation_manager(None)


# ─── Test 2: Graceful degradation (CM disabled) ────────────────────────────


class TestGracefulDegradation:
    """When CM is None, the observer falls back to the legacy waiting_for check.

    This keeps the system safe even if CM is broken or not wired up. The
    legacy behavior is preserved: read ``waiting_for`` from the DB, if > 0
    emit in_progress, else proceed to terminal transition.
    """

    @pytest.mark.asyncio
    async def test_cm_none_waiting_for_zero_proceeds_to_terminal(self):
        """CM is None, waiting_for == 0 → terminal transition (completed)."""
        job = make_mock_job()
        observer, mocks = make_observer(job, waiting_for=0)

        # Ensure CM is None.
        set_correlation_manager(None)

        event = make_lifecycle_event(job.instance_id, "completed")
        await observer._process_event(event)

        # Terminal transition happened.
        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["to_status"] == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_cm_none_waiting_for_positive_emits_in_progress(self):
        """CM is None, waiting_for > 0 → in_progress notification, no terminal."""
        job = make_mock_job()
        observer, mocks = make_observer(job, waiting_for=3)

        set_correlation_manager(None)

        event = make_lifecycle_event(job.instance_id, "completed")
        await observer._process_event(event)

        # in_progress notification.
        mocks["job_queue_service"].notify_watchers.assert_called_once()
        call = mocks["job_queue_service"].notify_watchers.call_args
        assert call.kwargs.get("status") == "in_progress"
        assert call.kwargs.get("waiting_for") == 3

        # No terminal transition.
        mocks["job_repo"].atomic_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_cm_none_error_with_no_waiting_fails_job(self):
        """CM is None, waiting_for == 0, status=error → FAILED transition."""
        job = make_mock_job()
        observer, mocks = make_observer(job, waiting_for=0)

        set_correlation_manager(None)

        event = make_lifecycle_event(job.instance_id, "error")
        # Manually inject error into the event.
        event["data"]["error"] = "boom"

        await observer._process_event(event)

        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["to_status"] == JobStatus.FAILED.value
        assert kwargs.get("error_message") == "boom"

    @pytest.mark.asyncio
    async def test_cm_none_falls_back_then_resolves(self):
        """Full graceful-degradation cycle: pending → resolved → terminal.

        Mirrors the pre-Phase-2 behavior: lifecycle event with waiting_for>0
        defers, then when waiting_for drops to 0, the next lifecycle event
        proceeds to terminal.
        """
        job = make_mock_job()
        observer, mocks = make_observer(job, waiting_for=2)

        set_correlation_manager(None)

        # First event: waiting_for=2 → in_progress, no terminal.
        await observer._process_event(
            make_lifecycle_event(job.instance_id, "completed")
        )
        mocks["job_repo"].atomic_transition.assert_not_called()
        mocks["job_queue_service"].notify_watchers.assert_called_once()
        assert (
            mocks["job_queue_service"].notify_watchers.call_args.kwargs.get(
                "status"
            )
            == "in_progress"
        )

        # Simulate children completing: waiting_for drops to 0.
        mocks["instance_manager"]._instance_repository.get.return_value.waiting_for = 0

        # Second event: waiting_for=0 → terminal.
        await observer._process_event(
            make_lifecycle_event(job.instance_id, "completed")
        )
        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["to_status"] == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_cm_none_handles_db_error_gracefully(self):
        """If the DB read for waiting_for fails, fall through to terminal.

        Better to complete the job than to silently drop the event.
        """
        job = make_mock_job()
        observer, mocks = make_observer(job, waiting_for=0)

        # Make the DB read raise an exception.
        mocks[
            "instance_manager"
        ]._instance_repository.get.side_effect = RuntimeError("DB down")

        set_correlation_manager(None)

        event = make_lifecycle_event(job.instance_id, "completed")
        # Must not raise.
        await observer._process_event(event)

        # Terminal transition still happened (fall-through after the
        # except block).
        mocks["job_repo"].atomic_transition.assert_called_once()
        kwargs = mocks["job_repo"].atomic_transition.call_args.kwargs
        assert kwargs["to_status"] == JobStatus.COMPLETED.value
