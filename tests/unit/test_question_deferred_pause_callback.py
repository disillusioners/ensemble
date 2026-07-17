"""End-to-end test for the C2 deferred-question-pause callback path.

The C2 fix changed where ``pause_instance_cascade`` is invoked when the
``question`` tool triggers a pause:

  * **Before the fix:** ``question_pause_node`` (which runs INSIDE the
    graph task stored at ``_graph_tasks[instance_id]``) called
    ``pause_instance_cascade`` directly. The cascade popped
    ``_graph_tasks[instance_id]`` and called ``task.cancel()`` on the
    running graph task, raising ``CancelledError`` at the cascade's first
    ``await`` — the DB transaction never committed, leaving the instance
    in PROCESSING while in-memory state said PAUSED (torn state).

  * **After the fix:** ``question_pause_node`` only sets a deferred-pause
    marker via ``manager.set_deferred_question_pause(instance_id)``. The
    actual ``pause_instance_cascade`` invocation runs from the
    ``finally`` block of ``InstanceMessagingService.send_message`` /
    ``_process_message_with_tracking`` AFTER ``_graph_tasks`` has been
    popped — there is no graph task left to self-cancel and the DB
    write completes cleanly.

This test exercises the actual ``send_message`` finally block to verify
the post-graph callback path:

  * the deferred marker is observed as set after the graph completes;
  * ``pause_instance_cascade`` is called by the finally block (NOT from
    inside the graph task);
  * ``_graph_tasks`` is empty by the time ``pause_instance_cascade`` is
    invoked — proving no self-cancel is possible.

Mirrors the harness style used by ``tests/test_message_job_bridge.py``
and ``tests/test_progressive_dispatch.py`` — a real
``InstanceMessagingService`` instance wired to a heavily-mocked manager.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService


# =============================================================================
# Helpers
# =============================================================================


def _make_manager_with_real_marker_state() -> MagicMock:
    """Build a mock manager wired with real set/dict backing for the C2 path.

    ``_graph_tasks`` is a real dict so ``send_message`` can register,
    observe, and pop the running task exactly as in production. The two
    deferred-pause methods are bound to a real ``_deferred_question_pause``
    set so the atomic set/pop semantics are exercised end-to-end.

    Everything else the service touches during ``send_message`` is
    stubbed to a ``MagicMock`` (the graph, repositories, the cascade).
    """
    manager = MagicMock()
    manager._graph_tasks = {}
    manager._deferred_question_pause = set()

    # Bind the real production methods to the mock instance so the
    # service's ``self._manager.set_deferred_question_pause(...)`` and
    # ``self._manager.pop_deferred_question_pause(...)`` calls go through
    # the production code path against the real backing set.
    from daemon.manager import InstanceManager

    manager.set_deferred_question_pause = (
        InstanceManager.set_deferred_question_pause.__get__(manager)
    )
    manager.pop_deferred_question_pause = (
        InstanceManager.pop_deferred_question_pause.__get__(manager)
    )

    # Stub the cascade the finally block invokes — ``AsyncMock`` so the
    # service's ``await self._manager.pause_instance_cascade(...)``
    # call resolves cleanly.
    manager.pause_instance_cascade = AsyncMock(return_value={"status": "paused"})

    # Helper the finally block calls to release the per-instance cache.
    manager.release_context_usage_cache = MagicMock()

    # DB / lifecycle surfaces ``send_message`` reaches before invoking
    # the graph — both need to resolve so the early ``asyncio.to_thread``
    # read and ``get_instance`` lookup succeed.
    manager.get_instance = AsyncMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)

    return manager


def _make_graph_manager_mock(
    manager: MagicMock, instance_id: str
) -> MagicMock:
    """Build a graph mock whose ``ainvoke`` sets the deferred marker.

    Mimics the LangGraph execution surface that ``send_message`` awaits:
    while the graph is "running" it invokes ``question_pause_node``,
    which calls ``manager.set_deferred_question_pause(instance_id)``.
    After the node returns ``{}``, LangGraph routes to END and ``ainvoke``
    resolves with an empty message state.
    """
    graph = MagicMock()

    async def _ainvoke(_input: dict, _config: dict) -> dict:
        # This side-effect call is the in-graph ``question_pause_node``
        # equivalent — it runs INSIDE the awaited coroutine, i.e. on the
        # same task that ``send_message`` registered in
        # ``_graph_tasks[instance_id]``.
        manager.set_deferred_question_pause(instance_id)
        return {"messages": []}

    graph.ainvoke = _ainvoke
    return graph


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build a real ``InstanceMessagingService`` with mocked collaborators.

    ``send_message`` calls ``self._maybe_compact_context`` and
    ``self._maybe_trigger_title_generation`` on the service instance
    itself — stub both so they are no-ops in the test (they have
    heavy DB / lifecycle side-effects in production).
    """
    service = InstanceMessagingService(
        manager=manager,
        cancellation_service=CancellationService(manager=manager),
    )
    # No-op the helper methods so the test focuses on the C2 callback.
    service._maybe_compact_context = AsyncMock()  # type: ignore[method-assign]
    service._maybe_trigger_title_generation = MagicMock()  # type: ignore[method-assign]
    return service


# =============================================================================
# Tests
# =============================================================================


class TestSendMessageDeferredPauseCallback:
    """``send_message`` finally block must invoke the cascade post-graph.

    These tests pin the C2 invariant: when ``question_pause_node`` runs
    and sets the deferred marker, the cascade runs from the finally
    block AFTER the graph task has been popped from ``_graph_tasks``.
    """

    async def test_send_message_pops_marker_and_calls_cascade_after_graph(self):
        """Marker → graph completes → finally pops marker → cascade called.

        Sets up the full post-graph callback pipeline:

          1. ``send_message`` registers ``asyncio.current_task()`` in
             ``manager._graph_tasks["iid"]`` before awaiting the graph.
          2. The graph's mocked ``ainvoke`` calls
             ``manager.set_deferred_question_pause("iid")`` (the
             ``question_pause_node`` equivalent).
          3. ``ainvoke`` returns normally; ``send_message`` enters its
             ``finally`` block.
          4. The finally block pops the task from ``_graph_tasks`` and
             then calls ``pop_deferred_question_pause`` — observing
             ``True``, so it awaits ``pause_instance_cascade``.
          5. After the finally block, ``manager._graph_tasks`` is empty
             and ``pause_instance_cascade`` was called exactly once with
             the right ``instance_id``.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"
        manager.get_instance = AsyncMock(
            return_value=_make_graph_manager_mock(manager, instance_id)
        )
        service = _make_service(manager)

        # Pre-condition: the test coroutine will be registered into
        # ``_graph_tasks`` by ``send_message`` — confirm the dict is
        # empty before invocation so the registration step is observable.
        assert instance_id not in manager._graph_tasks

        result = await service.send_message(instance_id, "hi")

        # The cascade ran — and crucially it ran from the finally block
        # (NOT from inside the graph task, which is the C2 bug).
        manager.pause_instance_cascade.assert_awaited_once_with(instance_id)

        # The deferred marker was popped (atomic check-and-remove on
        # the real backing set).
        assert instance_id not in manager._deferred_question_pause

        # _graph_tasks is empty — the cascade ran AFTER the task was
        # popped, so no self-cancel is possible. This is the decisive
        # C2 invariant.
        assert manager._graph_tasks == {}

        # Helper the finally block always invokes was also called.
        manager.release_context_usage_cache.assert_called_once_with(instance_id)

        # ``send_message`` still returned a MessageResult — the
        # post-commit callback did not crash the call.
        assert result is not None

    async def test_send_message_does_not_call_cascade_without_marker(self):
        """No marker → cascade is NOT invoked, _graph_tasks is still cleaned up.

        Negative case: when the graph completes without ever setting the
        deferred marker (e.g. no question was asked, or the graph was
        cancelled before reaching ``question_pause_node``), the cascade
        must NOT fire. The task pop still runs.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Graph mock that completes WITHOUT setting the marker.
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={"messages": []})
        manager.get_instance = AsyncMock(return_value=graph)

        service = _make_service(manager)

        await service.send_message(instance_id, "hi")

        # Cascade was never called — there was no marker to pop.
        manager.pause_instance_cascade.assert_not_called()

        # Cleanup still ran: task popped, context cache released.
        assert manager._graph_tasks == {}
        manager.release_context_usage_cache.assert_called_once_with(instance_id)

    async def test_send_message_cascade_failure_does_not_propagate(self):
        """A cascade failure inside the finally block is swallowed.

        The production finally block wraps ``pause_instance_cascade`` in
        ``try/except`` so a transient cascade failure cannot crash the
        message-processing call. The question pack SSE has already fired
        from the tool, so the user can still answer; the instance will
        just remain in whatever status the graph completed in.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Cascade raises — the finally block must absorb it.
        manager.pause_instance_cascade = AsyncMock(
            side_effect=RuntimeError("cascade DB exploded")
        )

        manager.get_instance = AsyncMock(
            return_value=_make_graph_manager_mock(manager, instance_id)
        )
        service = _make_service(manager)

        # send_message does NOT raise — the inner try/except swallows.
        result = await service.send_message(instance_id, "hi")

        manager.pause_instance_cascade.assert_awaited_once_with(instance_id)
        # Even though the cascade failed, the marker was popped and the
        # task was popped — the next graph completion for this instance
        # will start from a clean state, not re-fire the cascade.
        assert instance_id not in manager._deferred_question_pause
        assert manager._graph_tasks == {}
        assert result is not None

    async def test_send_message_current_task_is_popped_before_cascade_runs(self):
        """Order-of-operations: task pop precedes the cascade await.

        The C2 fix depends on the cascade running AFTER
        ``_graph_tasks[instance_id]`` has been popped — that is the
        "we are safely OUTSIDE the graph-task context" guarantee that
        prevents self-cancel. This test asserts the ordering directly
        by capturing ``_graph_tasks`` state at the moment
        ``pause_instance_cascade`` is awaited.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Track what ``_graph_tasks`` looks like at the moment the
        # cascade is awaited — that must be empty (the task has been
        # popped by the finally block BEFORE the cascade runs).
        graph_tasks_at_cascade_time: dict | None = None

        async def _capture_then_cascade(_instance_id: str) -> dict:
            nonlocal graph_tasks_at_cascade_time
            # Snapshot the manager's view of running graph tasks. The
            # finally block has already popped the task by this point;
            # if the dict still contains the instance, the cascade is
            # running INSIDE the graph task (C2 bug).
            graph_tasks_at_cascade_time = dict(manager._graph_tasks)
            return {"status": "paused"}

        manager.pause_instance_cascade = AsyncMock(
            side_effect=_capture_then_cascade
        )

        manager.get_instance = AsyncMock(
            return_value=_make_graph_manager_mock(manager, instance_id)
        )
        service = _make_service(manager)

        await service.send_message(instance_id, "hi")

        # The decisive C2 invariant: at the moment the cascade runs,
        # ``_graph_tasks`` no longer contains the running task.
        assert graph_tasks_at_cascade_time is not None, (
            "pause_instance_cascade was never awaited — the finally "
            "block skipped it, which would mean the marker was not "
            "observed or was observed twice"
        )
        assert instance_id not in graph_tasks_at_cascade_time, (
            "pause_instance_cascade ran while _graph_tasks still held "
            "the running task — that is the C2 self-cancel bug. The "
            "finally block must pop the task BEFORE calling the cascade."
        )
        # Sanity check: the cascade was actually awaited exactly once.
        manager.pause_instance_cascade.assert_awaited_once_with(instance_id)
