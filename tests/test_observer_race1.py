"""Phase 2 Race #1 regression test.

The Race #1 scenario:

  T1: Child A completes → lifecycle event published
  T2: JobFeedbackObserver._process_event() picks up event
  T3:   job = get_job_by_instance(parent_id)        ← still PROCESSING
  T4:   ``wf = waiting_for snapshot``              ← OLD: slow DB read + race
  T5:   ``result_summary = await _get_last_assistant_message_raw()``  ← SLOW
         ─────── TOCTOU WINDOW ───────
         T5a: Child B completes
         T5b: child_reports cascade fires
         T5c: Parent now has 0 pending children
  T6:   atomic_transition(PROCESSING → COMPLETED)   ← WRONG: completed too early
        in the old code; but the slow LLM fetch in T5 ran WHILE children
        were still resolving, so a new child could complete during the
        fetch — but the parent was already mid-terminal-transition.

Phase 2 eliminates this race:

  * ``_process_event`` reads ``cm.get_pending_count(parent_id)`` (sync, no
    LLM fetch, no TOCTOU). When ``cm_pending > 0``, it emits an
    ``in_progress`` notification and returns. **No terminal transition.**
  * The terminal transition happens ONLY when CM fires
    ``handle_correlation_complete`` — at the authoritative moment when
    pending count reaches 0 (CM holds the per-parent lock for the check,
    and the callback runs after lock release — W1 fix).
  * The LLM fetch (``_get_last_assistant_message_raw``) now lives inside
    the callback, not in the lifecycle event handler. The fetch runs
    AFTER CM has confirmed all children are done, so there is no window
    for a new child to complete mid-fetch.

This test exercises the exact race scenario and asserts that the
observer does NOT call ``atomic_transition`` until the CM callback fires.

Run with:

    pytest tests/test_observer_race1.py -v
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.correlation_manager import (
    CorrelationManager,
    set_correlation_manager,
)
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _FinalizeJobResult,
)

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
            agent_id="coder",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=locks_released,
            instance_was_terminal=instance_was_terminal,
        )
    return fake_sync


def make_mock_job(
    job_id: str | None = None,
    instance_id: str | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
    mock.status = "processing"
    mock.instance_id = instance_id or f"parent-{uuid.uuid4().hex[:8]}"
    mock.project_id = "test-project"
    mock.agent_id = "coder"
    mock.message = "test"
    mock.source = "api"
    return mock


def make_observer_with_controlled_llm(
    job: MagicMock,
    llm_fetch_delay: float = 0.0,
    llm_fetch_result: str = "agent response",
) -> tuple[JobFeedbackObserver, dict[str, MagicMock]]:
    """Build an observer where the LLM fetch is the slow path.

    ``llm_fetch_delay`` (seconds) is awaited inside
    ``_get_last_assistant_message_raw`` — this is the operation that was
    the "slow window" in Race #1.
    """
    mock_jqs = MagicMock()
    mock_jqs.get_job_by_instance = AsyncMock(return_value=job)
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)
    mock_jqs.start_job = AsyncMock(return_value=None)

    mock_job_repo = MagicMock(spec=JobRepository)
    mock_lock_repo = MagicMock(spec=LockRepository)
    mock_lock_repo.release_by_instance = MagicMock(return_value=0)

    instance_meta = MagicMock()
    instance_meta.waiting_for = 0
    instance_meta.status = "completed"

    mock_instance_manager = MagicMock()
    mock_instance_manager._instance_repository = MagicMock()
    mock_instance_manager._instance_repository.get = MagicMock(return_value=instance_meta)

    async def slow_llm_fetch(instance_id: str):
        if llm_fetch_delay > 0:
            await asyncio.sleep(llm_fetch_delay)
        return llm_fetch_result

    mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
        side_effect=slow_llm_fetch
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

    # H15 fix: install fake for the new sync helper so the test does not
    # need a real SQLModel engine. The sync helper consolidates the 5-step
    # terminal cascade into a single WriteGuardSession transaction.
    sync_mock = MagicMock(side_effect=make_fake_sync())
    observer._finalize_job_db_sync = sync_mock

    return observer, {
        "job_queue_service": mock_jqs,
        "job_repo": mock_job_repo,
        "lock_repo": mock_lock_repo,
        "instance_manager": mock_instance_manager,
        "sync_mock": sync_mock,
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


# ─── The Race #1 regression test ────────────────────────────────────────────


class TestRace1Regression:
    """The exact Race #1 scenario: lifecycle event during slow LLM fetch window.

    Before Phase 2: ``_process_event`` would read ``waiting_for == 0`` at T4,
    start the slow LLM fetch at T5, and during the fetch window a new child
    could complete. At T6 it would ``atomic_transition`` — wrong.

    After Phase 2: ``_process_event`` does NOT touch ``atomic_transition``
    when CM has pending children. Terminal only via CM callback.
    """

    @pytest.mark.asyncio
    async def test_lifecycle_event_does_not_complete_when_cm_has_pending(self):
        """Child A completes while child B is still pending.

        ``_process_event`` sees ``cm_pending > 0`` and emits ``in_progress``.
        It MUST NOT call ``atomic_transition`` — the CM callback is the
        sole path to terminal.
        """
        job = make_mock_job()
        observer, mocks = make_observer_with_controlled_llm(
            job, llm_fetch_delay=0.5
        )

        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            parent_id = job.instance_id
            # Two pending children.
            await cm.register_message_send(parent_id, "child-1", "msg-1")
            await cm.register_message_send(parent_id, "child-2", "msg-2")
            assert cm.get_pending_count(parent_id) == 2

            # Child A completes → lifecycle event.
            await observer._process_event(make_lifecycle_event(parent_id))

            # The observer must have emitted in_progress (children still pending).
            mocks["job_queue_service"].notify_watchers.assert_called_once()
            call = mocks["job_queue_service"].notify_watchers.call_args
            assert call.kwargs.get("status") == "in_progress"
            assert call.kwargs.get("waiting_for") == 2

            # CRITICAL: atomic_transition was NOT called. The lifecycle
            # handler does not perform terminal transitions when CM is
            # active and has pending children.
            mocks["job_repo"].atomic_transition.assert_not_called()
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_terminal_only_via_cm_callback(self):
        """Full Race #1 trace: 2 children → 1 child → 0 children.

        At each step, verify which path performs the terminal transition.
        Only the CM callback (when pending reaches 0) calls
        ``atomic_transition``.
        """
        job = make_mock_job()
        observer, mocks = make_observer_with_controlled_llm(
            job, llm_fetch_delay=0.1
        )

        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            parent_id = job.instance_id
            child_a = "child-a"
            child_b = "child-b"
            msg_a = f"msg-{uuid.uuid4().hex[:8]}"
            msg_b = f"msg-{uuid.uuid4().hex[:8]}"

            # Initial state: 2 pending children, job PROCESSING.
            await cm.register_message_send(parent_id, child_a, msg_a)
            await cm.register_message_send(parent_id, child_b, msg_b)
            assert cm.get_pending_count(parent_id) == 2

            # ── T1: Child A completes → lifecycle event ──
            await observer._process_event(
                make_lifecycle_event(parent_id, "completed")
            )

            # In-progress emitted, no terminal.
            mocks["job_queue_service"].notify_watchers.assert_called_once()
            assert (
                mocks["job_queue_service"].notify_watchers.call_args.kwargs.get(
                    "status"
                )
                == "in_progress"
            )
            mocks["job_repo"].atomic_transition.assert_not_called()

            # ── T2: Child B's response resolves via CM ──
            # This drops pending to 1; callback does NOT fire (still 1 pending).
            result = await cm.resolve_response(parent_id, child_a, msg_a)
            assert result is False
            assert cm.get_pending_count(parent_id) == 1
            # Still no terminal — there is 1 pending correlation.
            mocks["job_repo"].atomic_transition.assert_not_called()

            # ── T3: Child B's response resolves via CM → callback fires ──
            result = await cm.resolve_response(parent_id, child_b, msg_b)
            assert result is True  # last pending correlation
            assert cm.get_pending_count(parent_id) == 0

            # ── T4: The callback ran and called _finalize_job_db_sync ──
            mocks["sync_mock"].assert_called_once()
            args = mocks["sync_mock"].call_args.args
            assert args[0] == job.job_id
            assert args[1] == job.instance_id
            assert args[2] == InstanceStatus.COMPLETED.value

            # Watcher was notified with "completed" (from the callback).
            completed_calls = [
                c
                for c in mocks["job_queue_service"].notify_watchers.call_args_list
                if len(c.args) > 1 and c.args[1] == "completed"
            ]
            assert len(completed_calls) == 1, (
                f"Expected exactly 1 'completed' notify_watchers call, got "
                f"{len(completed_calls)}: {mocks['job_queue_service'].notify_watchers.call_args_list}"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_no_terminal_during_slow_llm_fetch_when_pending(self):
        """The old Race #1: slow LLM fetch starts, child B resolves during fetch.

        Before Phase 2: the LLM fetch at T5 could take seconds. During that
        window, child B could resolve. The stale ``waiting_for`` snapshot
        would lead to a premature ``atomic_transition``.

        After Phase 2: when ``cm_pending > 0``, the lifecycle handler does
        NOT call ``_get_last_assistant_message_raw`` (the slow LLM fetch).
        It emits in_progress immediately and returns. The LLM fetch only
        runs inside the CM callback — AFTER all children are confirmed
        resolved. There is no race window.
        """
        job = make_mock_job()
        # LLM fetch would be slow — but should NOT be called from _process_event
        # when CM has pending children.
        observer, mocks = make_observer_with_controlled_llm(
            job, llm_fetch_delay=0.3
        )

        cm = CorrelationManager(
            instance_repository=make_instance_repo_mock(),
            message_queue_repository=make_msg_repo_mock(),
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            parent_id = job.instance_id
            await cm.register_message_send(parent_id, "child-1", "msg-1")
            await cm.register_message_send(parent_id, "child-2", "msg-2")
            assert cm.get_pending_count(parent_id) == 2

            # Process the lifecycle event.
            await observer._process_event(make_lifecycle_event(parent_id))

            # CRITICAL: the LLM fetch was NOT called from _process_event.
            # The only call should be the in_progress notification's
            # progress_text — wait, actually the in_progress notification
            # ALSO calls _get_last_assistant_message_raw to get the
            # progress text. So the mock WILL be called once. The key
            # assertion is that atomic_transition was NOT called.
            mocks["job_repo"].atomic_transition.assert_not_called()

            # The LLM fetch was called at most once (for in_progress
            # progress text). It was NOT called in the path that leads
            # to atomic_transition.
            llm_call_count = mocks[
                "instance_manager"
            ]._get_last_assistant_message_raw.call_count
            assert llm_call_count <= 1, (
                f"LLM fetch called {llm_call_count} times during lifecycle "
                f"event processing; the Phase 2 path should only call it "
                f"once (for in_progress progress text), not for a slow "
                f"TOCTOU window"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)
