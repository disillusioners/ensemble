"""Unit tests for ``JobFeedbackObserver._deferred_finalize_check``.

The deferred safety net fires when a resume graph turn spawns a child
(``bus_pending > 0``) but there is no JobItem (post-D13 MESSAGE path)
and the natural lifecycle-event path did not arrive in time. After a
``delay`` seconds wait, the method re-checks the bus and drives
``_finalize_job`` when no children are still pending and the instance
is not already terminal.

Hardening 1 (2026-06-27): the deferred path now mirrors the
``_process_event`` happy path and clears the sticky ``parent_error``
flag AFTER ``_finalize_job`` completes. This file covers the four
behavioural surfaces the hardening introduced / relied on:

  1. Happy path: bus cleared, ctx available, instance non-terminal
     → ``_finalize_job`` is awaited exactly once.
  2. Bus still pending → ``_finalize_job`` is NOT awaited.
  3. Instance already terminal → ``_finalize_job`` is NOT awaited.
  4. Shutdown cancellation: ``observer.stop()`` cancels the in-flight
     deferred task and the ``CancelledError`` propagates cleanly.

The tests use ``unittest.mock`` + ``set_dependency_bus`` (the same
singleton-install pattern used by ``tests/job_queue/test_job_feedback_observer.py``)
so no real database is required. The 5s default delay is bypassed by
explicitly passing a tiny ``delay`` (0.01 for direct calls, 0.5 for the
shutdown-cancellation test which needs enough headroom for ``stop()``
to be invoked).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _ProcessingJobContext,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_observer() -> JobFeedbackObserver:
    """Build a ``JobFeedbackObserver`` with mocked dependencies.

    Only ``_deferred_finalize_check`` is exercised — every other
    surface (``event_bus``, ``job_queue_service``, ``job_repo``,
    ``lock_repo``, ``project_repo``, ``instance_manager``) is a
    MagicMock since the deferred method never touches them.
    """
    return JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=MagicMock(),
        job_repo=MagicMock(spec=JobRepository),
        lock_repo=MagicMock(spec=LockRepository),
        project_repo=MagicMock(),
        instance_manager=MagicMock(),
    )


def _make_bus_mock(
    *,
    count_pending: int = 0,
    had_parent_error: bool = False,
) -> MagicMock:
    """Build a ``DependencyBus`` mock with the surface used by
    ``_deferred_finalize_check``.
    """
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target = AsyncMock(return_value=count_pending)
    bus_mock.had_parent_error = MagicMock(return_value=had_parent_error)
    bus_mock.clear_parent_error = MagicMock()
    return bus_mock


def _make_processing_ctx(
    instance_id: str = "instance-deferred-1",
) -> _ProcessingJobContext:
    """Build a minimal ``_ProcessingJobContext`` (post-D13 MESSAGE path)."""
    return _ProcessingJobContext(instance_id=instance_id, job_id=None)


# ─── Test 1 ───────────────────────────────────────────────────────────────────


class TestDeferredDrivesFinalizeWhenBusClears:
    """Bus cleared after delay → drive ``_finalize_job``."""

    @pytest.mark.asyncio
    async def test_drives_finalize_job_when_bus_clears_after_delay(self):
        """When the bus shows no pending children, the instance is
        non-terminal, and a processing context is available, the
        deferred check awaits ``_finalize_job`` exactly once.
        """
        observer = _make_observer()
        bus_mock = _make_bus_mock(count_pending=0, had_parent_error=False)
        ctx = _make_processing_ctx(instance_id="inst-defer-1")

        # Spy on ``_finalize_job`` so the assertion is deterministic.
        finalize_spy = AsyncMock(return_value=None)
        observer._finalize_job = finalize_spy

        # Stub the two collaborators the deferred check calls.
        observer._get_processing_job_for_instance = AsyncMock(return_value=ctx)
        # Patch the sync helper so it returns "running" without
        # touching the real DB. ``asyncio.to_thread`` runs the patched
        # sync function in a worker thread.
        with patch.object(
            observer,
            "_read_instance_status_sync",
            return_value=InstanceStatus.RUNNING.value,
        ):
            set_dependency_bus(bus_mock)
            try:
                await observer._deferred_finalize_check(
                    "inst-defer-1", delay=0.01
                )
            finally:
                set_dependency_bus(None)

        # The bus was consulted.
        bus_mock.count_pending_for_target.assert_awaited_once_with(
            "inst-defer-1"
        )
        # The processing context was looked up. F15 (2026-07-01): the helper
        # now accepts an optional ``job_id`` parameter. The deferred
        # check threads ``expected_job_id=None`` (legacy post-D13
        # MESSAGE path — no JobItem at scheduling time) through the
        # second positional arg.
        observer._get_processing_job_for_instance.assert_awaited_once_with(
            "inst-defer-1", None
        )
        # Finalize was driven — exactly once, with the deferred
        # path's default status "completed" (the bus's
        # ``had_parent_error`` is False so the override does not
        # flip the status).
        finalize_spy.assert_awaited_once()
        args = finalize_spy.call_args.args
        assert isinstance(args[0], _ProcessingJobContext)
        assert args[0].instance_id == "inst-defer-1"
        assert args[1] == "inst-defer-1"
        assert args[2] == InstanceStatus.COMPLETED.value
        # ``error=None`` because no parent error was set.
        assert finalize_spy.call_args.kwargs.get("error") is None


# ─── Test 2 ───────────────────────────────────────────────────────────────────


class TestDeferredNoopsWhenBusStillPending:
    """Bus still pending after delay → no-op."""

    @pytest.mark.asyncio
    async def test_noops_when_bus_still_pending(self):
        """When the bus reports children still resolving, the
        deferred check returns silently without driving finalize.
        """
        observer = _make_observer()
        bus_mock = _make_bus_mock(count_pending=1, had_parent_error=False)

        # Spy on the collaborators — none of them should be reached
        # when the bus still has pending children.
        finalize_spy = AsyncMock(return_value=None)
        observer._finalize_job = finalize_spy
        ctx_spy = AsyncMock(return_value=_make_processing_ctx())
        observer._get_processing_job_for_instance = ctx_spy
        status_spy = MagicMock(return_value=InstanceStatus.RUNNING.value)
        observer._read_instance_status_sync = status_spy

        set_dependency_bus(bus_mock)
        try:
            await observer._deferred_finalize_check(
                "inst-defer-pending", delay=0.01
            )
        finally:
            set_dependency_bus(None)

        # The bus was consulted exactly once.
        bus_mock.count_pending_for_target.assert_awaited_once_with(
            "inst-defer-pending"
        )
        # The processing-context lookup was NOT reached (early return).
        ctx_spy.assert_not_called()
        # The sync status helper was NOT reached.
        status_spy.assert_not_called()
        # Finalize was NOT driven.
        finalize_spy.assert_not_called()


# ─── Test 3 ───────────────────────────────────────────────────────────────────


class TestDeferredNoopsWhenInstanceTerminal:
    """Instance already terminal → no-op."""

    @pytest.mark.asyncio
    async def test_noops_when_instance_already_terminal(self):
        """When the bus shows no pending children but the instance
        is already in a terminal status, the deferred check returns
        silently without driving finalize. This avoids redundant
        work when the natural lifecycle-event path already fired.
        """
        observer = _make_observer()
        bus_mock = _make_bus_mock(count_pending=0, had_parent_error=False)
        ctx = _make_processing_ctx(instance_id="inst-defer-terminal")

        finalize_spy = AsyncMock(return_value=None)
        observer._finalize_job = finalize_spy
        observer._get_processing_job_for_instance = AsyncMock(return_value=ctx)

        # Instance is already completed — the lifecycle-event path
        # already drove finalize.
        with patch.object(
            observer,
            "_read_instance_status_sync",
            return_value=InstanceStatus.COMPLETED.value,
        ):
            set_dependency_bus(bus_mock)
            try:
                await observer._deferred_finalize_check(
                    "inst-defer-terminal", delay=0.01
                )
            finally:
                set_dependency_bus(None)

        # The bus was consulted (gate passed).
        bus_mock.count_pending_for_target.assert_awaited_once_with(
            "inst-defer-terminal"
        )
        # The processing-context lookup WAS reached (it precedes the
        # terminal-status check), but... F15 (2026-07-01): the helper
        # now accepts an optional ``job_id`` parameter; the deferred
        # check threads ``expected_job_id=None`` (legacy path) through.
        observer._get_processing_job_for_instance.assert_awaited_once_with(
            "inst-defer-terminal", None
        )
        # ...finalize was NOT driven because the instance was already
        # terminal — the second early-return guard inside the method
        # caught it.
        finalize_spy.assert_not_called()
        # ``clear_parent_error`` was NOT called either (we never
        # reached the post-finalize hardening hook).
        bus_mock.clear_parent_error.assert_not_called()


# ─── Test 4 ───────────────────────────────────────────────────────────────────


class TestDeferredCancelledOnShutdown:
    """Observer shutdown cancels the in-flight deferred task."""

    @pytest.mark.asyncio
    async def test_cancelled_when_observer_stop_is_called(self):
        """When ``observer.stop()`` is invoked while a deferred
        check is in flight, the in-flight task is cancelled and
        ``CancelledError`` propagates cleanly (the method re-raises
        from both the sleep try-block and the main try-block).
        """
        observer = _make_observer()
        # delay=0.5 gives the test enough headroom to invoke
        # ``observer.stop()`` before the sleep completes.
        delay = 0.5

        # Schedule via ``asyncio.create_task`` the SAME way the
        # production call site registers the task — including the
        # ``_deferred_finalize_tasks`` set membership and the
        # done-callback that drains the set.
        task = asyncio.create_task(
            observer._deferred_finalize_check("inst-defer-cancel", delay=delay)
        )
        observer._deferred_finalize_tasks.add(task)
        task.add_done_callback(observer._deferred_finalize_tasks.discard)

        # Give the task a tick to enter ``asyncio.sleep(delay)``.
        await asyncio.sleep(0)

        # Trigger observer shutdown — ``stop`` drains the queue,
        # cancels the main loop task (none here), and cancels every
        # entry in ``_deferred_finalize_tasks``.
        await observer.stop()

        # The deferred task should be cancelled. ``stop`` already
        # awaited the set via ``asyncio.gather(..., return_exceptions=True)``
        # so the task is done by the time we reach this line.
        assert task.done(), (
            "deferred task should be done after observer.stop() awaited it"
        )
        assert task.cancelled(), (
            f"deferred task should be cancelled, got exception={task.exception()!r}"
        )
        # The set was drained by the done-callback.
        assert task not in observer._deferred_finalize_tasks