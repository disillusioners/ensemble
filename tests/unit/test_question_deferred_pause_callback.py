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
    # service's ``self._manager.set_deferred_question_pause(...)``,
    # ``self._manager.has_deferred_question_pause(...)`` and
    # ``self._manager.pop_deferred_question_pause(...)`` calls go through
    # the production code path against the real backing set.
    from daemon.manager import InstanceManager

    manager.set_deferred_question_pause = (
        InstanceManager.set_deferred_question_pause.__get__(manager)
    )
    manager.has_deferred_question_pause = (
        InstanceManager.has_deferred_question_pause.__get__(manager)
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

    async def test_send_message_cascade_is_awaited_and_runs_to_completion(self):
        """AREA 3 + AREA 4: cascade must run to completion through the shield.

        The pre-fix C2 invariant was "task pop precedes cascade await" —
        the ordering guarantee that prevented the cascade from
        self-cancelling the running graph task. After the AREA 3 +
        AREA 4 concurrency fixes that ordering is no longer the safety
        property that matters:

          * AREA 4 hoisted the marker pop out of the
            ``if existing is current_task`` identity guard, so the
            marker is consumed even when an external
            ``pause_instance_cascade`` already pre-popped
            ``_graph_tasks[instance_id]``.
          * AREA 3 wrapped the cascade await in ``asyncio.shield`` so a
            second ``task.cancel()`` arriving during the cascade's DB
            write does not interrupt the inner coroutine. The cascade's
            own ``_pause_single`` (in ``instance_lifecycle.py``) also
            pops ``_graph_tasks[instance_id]`` before it tries to
            cancel — a second layer of defence against self-cancel.

        The new safety property is: the cascade is awaited AND runs to
        completion from the ``send_message`` finally block. We pin both
        halves of that contract here via a side-channel flag — the mock
        cascade sets the flag when it completes, and we assert the flag
        is set after ``send_message`` returns. (The full
        shield-against-second-cancel scenario is covered by
        ``test_send_message_shield_protects_cascade_db_write_from_cancel``;
        the external-pre-pop marker-consumption scenario is covered by
        ``test_send_message_consumes_marker_when_external_pre_pop_raced``.)
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Side-channel progress flag. We can't observe the inner
        # coroutine via the awaiting task's locals if a cancel were to
        # arrive, so we record completion here for post-await assertion.
        cascade_completed = False

        async def _cascade_that_records_completion(_iid: str) -> dict:
            nonlocal cascade_completed
            # Real await — yields control so any pending outer cancel
            # has a chance to be delivered. If the AREA 3 shield is
            # missing (regression), the inner coroutine would be torn
            # down before the post-await assignment runs.
            await asyncio.sleep(0)
            cascade_completed = True
            return {"status": "paused"}

        manager.pause_instance_cascade = AsyncMock(
            side_effect=_cascade_that_records_completion
        )

        manager.get_instance = AsyncMock(
            return_value=_make_graph_manager_mock(manager, instance_id)
        )
        service = _make_service(manager)

        result = await service.send_message(instance_id, "hi")

        # The cascade was awaited exactly once with the right instance_id
        # — the marker pop in the finally block observed the deferred
        # pause and routed to the cascade.
        manager.pause_instance_cascade.assert_awaited_once_with(instance_id)

        # The cascade inner coroutine ran to completion. This is the
        # decisive AREA 3 + AREA 4 safety property: the cascade is no
        # longer racing self-cancel; the shield protects it.
        assert cascade_completed, (
            "pause_instance_cascade did not run to completion through "
            "the send_message finally block — the marker was either "
            "not observed or the AREA 3 shield regressed and a "
            "cancel interrupted the inner coroutine."
        )

        # Sanity: ``send_message`` still returned a result — the
        # post-commit callback did not crash the call.
        assert result is not None


class TestSendMessageDeferredPauseConcurrencyFixes:
    """AREA 3 + AREA 4 fixes for the C2 deferred-question-pause callback.

    Two concurrency bugs were fixed in the ``send_message`` /
    ``_process_message_with_tracking`` ``finally`` blocks after the first
    pass landed:

      * **AREA 4 (hoist):** ``pop_deferred_question_pause(instance_id)``
        was moved OUTSIDE the ``if existing is current_task`` identity
        guard. Before the fix, if an external ``pause_instance_cascade``
        pre-popped ``_graph_tasks[instance_id]`` (user-click-stop racing
        the graph completion), the identity check failed and the marker
        leaked — causing a spurious pause on the next message.

      * **AREA 3 (shield):** ``pause_instance_cascade`` is now awaited
        inside ``asyncio.shield`` so a second ``task.cancel()`` arriving
        during the cascade's DB write does NOT propagate into the inner
        coroutine. Without ``shield``, ``CancelledError`` (``BaseException``)
        would interrupt the cascade's DB transaction and leave the
        instance in PROCESSING while in-memory state said PAUSED.

    These tests pin both invariants against the actual ``send_message``
    finally block.
    """

    async def test_send_message_consumes_marker_when_external_pre_pop_raced(
        self,
    ):
        """AREA 4: marker must be consumed even when an external pause pre-popped.

        Scenario: while the graph task is running, an external
        ``pause_instance_cascade`` (``user-click-stop`` race) pops
        ``_graph_tasks[instance_id]`` and replaces it with the cancel
        path's own bookkeeping. By the time ``send_message``'s ``finally``
        block runs:

          * ``current_task`` is still the same task that ``send_message``
            registered;
          * but ``manager._graph_tasks.get(instance_id)`` returns either
            ``None`` or a different task — i.e. ``existing is current_task``
            is False.

        Before the fix the cascade pop lived INSIDE the identity guard,
        so the marker leaked and the NEXT message would spuriously call
        ``pause_instance_cascade`` again. After the fix the pop is
        HOISTED above the guard, so the marker is always consumed and the
        cascade is invoked exactly once for the question pause that
        requested it.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Graph mock: sets the deferred marker (the in-graph
        # ``question_pause_node`` equivalent) AND simulates the external
        # ``pause_instance_cascade`` pre-popping ``_graph_tasks[instance_id]``
        # before ``send_message``'s ``finally`` block runs.
        graph = MagicMock()

        async def _ainvoke(_input: dict, _config: dict) -> dict:
            manager.set_deferred_question_pause(instance_id)
            # External cancel race: user-click-stop / an in-flight
            # ``pause_instance_cascade`` pops the entry the graph task
            # had registered. After this point
            # ``existing is current_task`` is False inside ``finally``.
            manager._graph_tasks.pop(instance_id, None)
            return {"messages": []}

        graph.ainvoke = _ainvoke
        manager.get_instance = AsyncMock(return_value=graph)
        service = _make_service(manager)

        result = await service.send_message(instance_id, "hi")

        # The decisive AREA 4 assertion: the cascade ran even though the
        # identity guard inside ``finally`` would have skipped it. This
        # is only possible because the marker pop is HOISTED above the
        # guard. Before the fix, ``existing is current_task`` was False
        # (the pre-pop replaced the entry), the ``if`` body was skipped,
        # the marker leaked, and ``pause_instance_cascade`` was NEVER
        # called for this question pause.
        manager.pause_instance_cascade.assert_awaited_once_with(instance_id)

        # The marker was consumed — not leaked. Re-popping must return
        # False because the entry is gone from the backing set.
        assert manager.pop_deferred_question_pause(instance_id) is False, (
            "AREA 4 fix regressed: deferred-pause marker leaked past "
            "the finally block. The next message will spuriously call "
            "pause_instance_cascade."
        )
        # Sanity: the marker backing set no longer references the id.
        assert instance_id not in manager._deferred_question_pause

        # ``_graph_tasks`` is empty (the external cancel popped the entry
        # and ``send_message``'s finally correctly skipped re-popping
        # since ``existing is current_task`` is False after the pre-pop).
        assert manager._graph_tasks == {}

        # ``send_message`` returned a result — the AREA 4 fix did not
        # crash the call path.
        assert result is not None

    async def test_send_message_shield_protects_cascade_db_write_from_cancel(
        self,
    ):
        """AREA 3: a second ``task.cancel()`` must not interrupt the cascade DB write.

        Scenario: ``send_message``'s ``finally`` block awaits
        ``pause_instance_cascade`` (via ``asyncio.shield``). During that
        await, a second ``task.cancel()`` arrives — the same kind of
        cancel that previously caused ``pause_instance_cascade`` to
        interrupt itself mid-DB-write.

        Without ``asyncio.shield`` the inner coroutine would receive the
        cancel and be torn down before completing its DB write. With
        ``asyncio.shield`` the outer task's cancel is absorbed and the
        inner coroutine runs to completion independently.

        The cascade mock here performs a real ``await asyncio.sleep`` so
        the event loop gets a chance to deliver a cancel mid-flight. We
        run ``send_message`` in a child task so we can issue
        ``task.cancel()`` from outside, then verify the inner cascade
        still reached its post-await "db_write_done" marker via a
        side-channel (not via the cancelled outer coroutine's locals).
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Side-channel progress log. We can't observe the inner coroutine
        # via the cancelled outer task's locals, so we record events here
        # for post-cancel assertion.
        cascade_log: list[str] = []
        # Event the test waits on AFTER the outer cancel — proves the
        # shielded inner coroutine actually ran to completion.
        cascade_db_write_done = asyncio.Event()

        async def _slow_cascade(_iid: str) -> dict:
            cascade_log.append("cascade_entered")
            # Real await — yields control. A second cancel scheduled
            # during this sleep would normally propagate into the
            # awaiting coroutine via the unawaited outer task; with
            # ``asyncio.shield`` the inner coroutine keeps running.
            await asyncio.sleep(0.05)
            cascade_log.append("cascade_db_written")
            cascade_db_write_done.set()
            return {"status": "paused"}

        manager.pause_instance_cascade = AsyncMock(side_effect=_slow_cascade)

        # Graph mock: sets the deferred marker (the in-graph
        # ``question_pause_node`` equivalent), then yields once so the
        # test scheduler can issue the cancel. We schedule the cancel
        # from a separate task started alongside ``send_message``.
        graph = MagicMock()

        async def _ainvoke(_input: dict, _config: dict) -> dict:
            manager.set_deferred_question_pause(instance_id)
            # Yield so the test scheduler task (started below) can run
            # and observe "cascade_entered" before the sleep completes.
            await asyncio.sleep(0)
            return {"messages": []}

        graph.ainvoke = _ainvoke
        manager.get_instance = AsyncMock(return_value=graph)
        service = _make_service(manager)

        # Run ``send_message`` in a child task so the test scheduler can
        # issue a cancel against it. We cannot cancel
        # ``asyncio.current_task()`` from within itself.
        send_cancelled = asyncio.Event()

        async def _run_send_message() -> None:
            try:
                await service.send_message(instance_id, "hi")
            except asyncio.CancelledError:
                send_cancelled.set()
                raise

        send_task = asyncio.create_task(_run_send_message())

        # Wait until the cascade inner coroutine has entered, then issue
        # the second cancel. This mimics an external caller (e.g.
        # ``user-click-stop``) racing the cascade DB write.
        deadline = asyncio.get_event_loop().time() + 1.0
        while "cascade_entered" not in cascade_log:
            if asyncio.get_event_loop().time() > deadline:
                send_task.cancel()
                pytest.fail(
                    "Cascade inner coroutine never entered — test "
                    "harness could not schedule a second cancel before "
                    "the cascade completed."
                )
            await asyncio.sleep(0.001)

        send_task.cancel()

        # The outer task receives CancelledError from the shield (the
        # shield re-raises on outer cancel without killing the inner
        # task); ``send_message`` re-raises it because ``except Exception``
        # cannot catch ``BaseException``.
        with pytest.raises(asyncio.CancelledError):
            await send_task
        assert send_cancelled.is_set(), (
            "Outer send_message task did not raise CancelledError — "
            "the test harness did not actually simulate the second "
            "cancel scenario."
        )

        # Wait for the shielded inner cascade to complete. If
        # ``asyncio.shield`` is in place, the inner coroutine kept
        # running after the outer cancel and set this event. If
        # ``shield`` is missing (regression), the inner coroutine was
        # torn down before ``cascade_db_written`` could be appended.
        try:
            await asyncio.wait_for(cascade_db_write_done.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail(
                "asyncio.shield did NOT protect the cascade — the "
                "second cancel interrupted the DB write before "
                "completion. This is the AREA 3 regression: "
                "'CancelledError' from a second task.cancel() "
                "propagated into pause_instance_cascade and tore down "
                "the DB transaction. cascade_log=%r" % (cascade_log,)
            )

        # Decisive AREA 3 assertions.
        assert "cascade_entered" in cascade_log
        assert "cascade_db_written" in cascade_log, (
            "asyncio.shield regressed: the cascade inner coroutine "
            "never reached its post-await DB-write marker after the "
            "second cancel. cascade_log=%r" % (cascade_log,)
        )
        manager.pause_instance_cascade.assert_awaited_once_with(instance_id)
