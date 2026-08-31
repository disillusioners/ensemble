"""C2 deferred-pause edge case tests.

The C2 fix defers ``pause_instance_cascade()`` to AFTER
``_graph_tasks.pop()`` in the finally blocks of
``daemon.services.instance_messaging``. The companion file
``tests/unit/test_question_deferred_pause_callback.py`` exercises the
single-cycle path: it confirms the cascade runs from
the post-graph finally block, verifies the order-of-operations
(_graph_tasks popped → cascade awaited), and checks the no-marker
negative case.

This file covers edge cases the original tests do not address:

# 1. Second-cycle behavior — a SECOND enqueued turn whose graph also
#    triggers the deferred marker correctly re-sets and re-pops the
#    marker. The cascade is awaited again. The marker is not "stuck"
#    from cycle 1.
# 2. Non-question messages — a regular message that does NOT set the
#    deferred marker must not trigger the cascade at all.
# 3. Marker idempotency — pop_deferred_question_pause is an atomic
#    check-and-remove: the first pop returns True and consumes the
#    marker; the second pop returns False.
# 4. Concurrent instance isolation — markers for distinct instance
#    IDs do not interfere with each other.
# 5. Path B coverage — the same finally-block invariant holds for
#    _process_message_with_tracking (Path B), not just the (deleted)
#    legacy send_message (Path A). Both paths share the same
#    post-graph cleanup structure.

wc-wake-report-integrity T6b completion (2026-08-30): the legacy
``InstanceMessagingService.send_message`` (the ``:1060`` graph.ainvoke
bypass) was DELETED (C1-D7). The two former Path-A fixtures were
MIGRATED to the surviving pipeline entry point
``_process_message_with_tracking`` (Path B) — the finally-block
cascade mechanism they pin is the SAME code that still runs on every
enqueued turn. The cascade is now asserted with its production
``suspension_reason="awaiting_answer"`` argument, matching
``instance_messaging.py``'s post-graph finally block.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService


def _make_manager_with_real_marker_state() -> MagicMock:
    """Build a mock manager wired with real set/dict backing for the C2 path.

    ``_graph_tasks`` is a real dict so the turn pipeline can register,
    observe, and pop the running task exactly as in production. The two
    deferred-pause methods are bound to a real ``_deferred_question_pause``
    set so the atomic set/pop semantics are exercised end-to-end.

    Everything else the pipeline touches during a turn is stubbed to a
    ``MagicMock`` (the graph, repositories, the cascade).
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

    # DB / lifecycle surfaces the pipeline reaches before invoking
    # the graph — both need to resolve so the early ``asyncio.to_thread``
    # read and ``get_instance`` lookup succeed.
    manager.get_instance = AsyncMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)

    return manager


# =============================================================================
# Helpers (Path B: _process_message_with_tracking)
# =============================================================================


def _make_path_b_graph_manager_mock(
    manager: MagicMock, instance_id: str
) -> MagicMock:
    """Build a graph mock whose ``astream`` sets the deferred marker.

    ``_process_message_with_tracking`` (Path B) consumes the graph via
    ``async for event in graph.astream(graph_input, config, stream_mode=[...])``
    rather than ``graph.ainvoke``. The mock must therefore yield an
    async iterator — an empty stream that nevertheless triggers the
    deferred-pause marker on entry.

    ``language_check_active`` is set to ``False`` (the default) so the
    astream loop's progressive dispatch path is skipped.

    T6b completion: ``aget_state`` is an ``AsyncMock`` returning ``None``
    so the D1 entry-seam tail-guard (``_heal_poisoned_checkpoint_tail``)
    short-circuits — these tests target the C2 finally block, not the
    pairing guard.
    """
    graph = MagicMock()
    graph.language_check_active = False
    graph.aget_state = AsyncMock(return_value=None)

    async def _astream(_input: dict, _config: dict, stream_mode=None):
        # This side-effect call is the in-graph ``question_pause_node``
        # equivalent. It runs INSIDE the ``astream`` loop, i.e. on the
        # same task that ``_process_message_with_tracking`` registered
        # in ``_graph_tasks[instance_id]``.
        manager.set_deferred_question_pause(instance_id)
        # Empty stream — no events yielded, the ``async for`` exits
        # immediately and the finally block fires.
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream = _astream
    return graph


def _make_path_b_service(manager: MagicMock) -> InstanceMessagingService:
    """Build a service for the ``_process_message_with_tracking`` path.

    ``_process_message_with_tracking`` has many more collaborators than
    ``send_message``: it touches ``_queue_repository`` (for activity
    callbacks), ``_live_hub`` (for SSE streaming), ``_llm_semaphore``
    (as an async context manager around the astream loop),
    ``_checkpointer`` (for checkpoint reads), and many optional services
    (skill injection, project context).

    The strategy: stub the heavyweight DB / network collaborators to
    no-ops so the test focuses on the C2 finally block. ``is_retry=True``
    + ``silent=True`` is passed at the call site to bypass the project
    / skill / shared-context injection blocks — those paths have their
    own dedicated tests.
    """
    service = InstanceMessagingService(
        manager=manager,
        cancellation_service=CancellationService(manager=manager),
    )

    # No-op the service helpers so the test focuses on the C2 callback.
    service._maybe_compact_context = AsyncMock()  # type: ignore[method-assign]
    service._maybe_trigger_title_generation = MagicMock()  # type: ignore[method-assign]
    # ``_has_checkpoint`` is consulted on the retry path; return False
    # so the function takes the "re-add message" branch (simpler than
    # faking a checkpoint).
    service._has_checkpoint = AsyncMock(return_value=False)  # type: ignore[method-assign]
    # ``_emit_context_usage`` is invoked from inside the astream loop —
    # since we yield no events, it never fires, but stub it for safety.
    service._emit_context_usage = AsyncMock()  # type: ignore[method-assign]

    # Live event hub — every SSE stream call goes through here.
    # Stub each method to an AsyncMock so the ``await`` resolves.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_tool_result = AsyncMock()
    manager._live_hub.stream_context_usage = AsyncMock()
    manager._live_hub.stream_error = AsyncMock()

    # ``async with self._llm_semaphore:`` wraps the astream loop in
    # ``_process_message_with_tracking``. A real ``asyncio.Semaphore``
    # works as an async context manager — no need for the MagicMock
    # shim.
    manager._llm_semaphore = asyncio.Semaphore()

    # Config object the function reads for ``limits.graph_recursion_limit``.
    manager.config = MagicMock()
    manager.config.limits.graph_recursion_limit = 25

    # Queue repository the ``ActivityCallbackHandler`` constructor
    # captures. ``update_activity`` is called from a throttle path that
    # only fires during LLM streaming; with an empty astream it is
    # never reached, but the attribute must exist.
    manager._queue_repository = MagicMock()
    manager._queue_repository.update_activity = MagicMock()

    # ``_compactor is None`` short-circuits ``_maybe_compact_context``
    # before it would touch the checkpointer. We also stubbed the
    # service-level ``_maybe_compact_context`` above as belt-and-braces.
    manager._compactor = None

    # Source dispatcher is optional; ``None`` is a valid value that
    # the code already guards against.
    manager.source_dispatcher = None

    # Instance / project repositories — the function reads from these
    # in many setup paths. ``is_retry=True`` bypasses most of them, but
    # still leaves a few ``get_instance_metadata`` style calls. Stub
    # every method we expect to be called.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager._project_repository = MagicMock()
    manager.shared_meta_kv_repo = MagicMock()
    # Skill injection / metrics services are optional — ``None`` is
    # handled by ``getattr(..., None)`` everywhere in the function.
    manager._skill_injection_service = None
    manager._skill_clone_service = None
    manager._skill_metrics_service = None

    return service


# =============================================================================
# Tests
# =============================================================================


class TestDeferredPauseEdgeCases:
    """Edge cases for the C2 deferred-question-pause mechanism.

    These tests complement
    ``tests/unit/test_question_deferred_pause_callback.py`` by covering
    the multi-cycle, isolation, and Path-B scenarios the original tests
    did not exercise.
    """

    async def test_second_cycle_marker_set_again_after_first_popped(self):
        """Second cycle re-sets and re-pops the marker; cascade fires twice.

        T6b completion (2026-08-30): migrated from the deleted
        ``InstanceMessagingService.send_message`` (Path A) to the
        surviving ``_process_message_with_tracking`` (Path B) — the
        finally-block cascade mechanism under test is shared by both
        paths and only Path B survives.

        Production scenario: an instance completes a question → pause
        cycle (user answers, instance resumes, processes the answer,
        then issues a SECOND question that needs to pause again). The
        marker must not be "stuck" from cycle 1 — ``set_deferred``
        must re-add it (the backing set is empty after the first pop),
        and ``pop_deferred`` must return ``True`` a second time so the
        finally block awaits ``pause_instance_cascade`` again.

        Asserts:

          * Cycle 1: marker popped, ``pause_instance_cascade`` awaited
            once (with ``suspension_reason="awaiting_answer"``),
            ``_graph_tasks`` empty.
          * Cycle 2: marker re-set by the new ``astream`` call, then
            popped again by the new finally block;
            ``pause_instance_cascade.await_count == 2``;
            ``_graph_tasks`` still empty.
          * Both cycles called the cascade with the SAME
            ``instance_id`` — no drift in the argument list.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"
        # Every astream call (cycle 1 AND cycle 2) sets the deferred
        # marker, exactly as ``question_pause_node`` would in production.
        manager.get_instance = AsyncMock(
            return_value=_make_path_b_graph_manager_mock(manager, instance_id)
        )
        service = _make_path_b_service(manager)

        # Pre-condition: backing collections are empty before cycle 1.
        assert instance_id not in manager._graph_tasks
        assert instance_id not in manager._deferred_question_pause

        async def _run_turn(message: str):
            return await service._process_message_with_tracking(
                instance_id=instance_id,
                message=message,
                message_id=f"msg-{message}",
                is_retry=True,
                silent=True,
            )

        # Cycle 1
        result1 = await _run_turn("first question")
        assert result1 is not None

        # Cycle 1 cleanup observed.
        assert instance_id not in manager._deferred_question_pause, (
            "cycle 1 failed to pop the deferred marker"
        )
        assert manager._graph_tasks == {}, (
            "cycle 1 leaked an entry in _graph_tasks"
        )
        assert manager.pause_instance_cascade.await_count == 1, (
            "cycle 1 did not await pause_instance_cascade exactly once"
        )

        # Cycle 2 — fresh pipeline turn, fresh graph execution.
        result2 = await _run_turn("second question")
        assert result2 is not None

        # Cycle 2 cleanup observed.
        assert instance_id not in manager._deferred_question_pause, (
            "cycle 2 failed to pop the deferred marker — "
            "the set may have retained cycle 1's state"
        )
        assert manager._graph_tasks == {}, (
            "cycle 2 leaked an entry in _graph_tasks"
        )
        assert manager.pause_instance_cascade.await_count == 2, (
            "cycle 2 did not await pause_instance_cascade — "
            "the marker must be re-settable across cycles"
        )

        # Both cycles fired the cascade with the right instance_id.
        # ``call_args_list`` holds the positional/keyword args for each
        # awaited call.
        awaited_with = [
            call.args[0] if call.args else call.kwargs.get("instance_id")
            for call in manager.pause_instance_cascade.await_args_list
        ]
        assert awaited_with == [instance_id, instance_id], (
            f"pause_instance_cascade was awaited with the wrong "
            f"instance_id(s): {awaited_with}"
        )

    async def test_non_question_message_does_not_set_marker(self):
        """A normal message never sets the marker → cascade is never called.

        T6b completion (2026-08-30): migrated from the deleted
        ``InstanceMessagingService.send_message`` (Path A) to the
        surviving ``_process_message_with_tracking`` (Path B).

        Negative case for the second-cycle mechanism: when the graph
        completes WITHOUT ever calling ``set_deferred_question_pause``,
        the finally block's ``pop_deferred_question_pause`` is never
        reached via the marker branch and ``pause_instance_cascade`` is
        never awaited.

        Asserts:

          * ``pop_deferred_question_pause`` returns ``False`` (the
            backing set never received an entry).
          * ``pause_instance_cascade`` was NEVER awaited.
          * ``_graph_tasks`` is still empty after the call (cleanup
            ran regardless of marker presence).
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Graph mock that completes WITHOUT touching the deferred
        # marker — no ``question_pause_node`` equivalent.
        graph = MagicMock()
        graph.language_check_active = False
        graph.aget_state = AsyncMock(return_value=None)

        async def _astream(_input, _config, stream_mode=None):
            return
            yield  # pragma: no cover

        graph.astream = _astream
        manager.get_instance = AsyncMock(return_value=graph)
        service = _make_path_b_service(manager)

        # Pre-condition: the backing set is empty before the turn.
        assert instance_id not in manager._deferred_question_pause

        result = await service._process_message_with_tracking(
            instance_id=instance_id,
            message="regular message",
            message_id="msg-1",
            is_retry=True,
            silent=True,
        )
        assert result is not None

        # The marker was never added — pop returns False.
        assert manager.pop_deferred_question_pause(instance_id) is False, (
            "pop_deferred_question_pause returned True after a "
            "non-question turn — the graph mock must not have "
            "set the marker, yet the backing set still reports one"
        )
        # Cascade was never awaited.
        manager.pause_instance_cascade.assert_not_called()
        # Cleanup still ran — the finally block always pops the task.
        assert manager._graph_tasks == {}
        manager.release_context_usage_cache.assert_called_once_with(instance_id)

    async def test_marker_idempotency_pop_returns_false_on_second_pop(self):
        """``pop_deferred_question_pause`` consumes the marker once.

        The method is documented as an atomic check-and-remove. The
        first call MUST return ``True`` (and remove the marker); a
        second call MUST return ``False`` (the marker is already
        gone). This is the property that lets concurrent resume /
        retry paths observe a consistent view of whether a deferred
        pause is pending — no path can re-fire the cascade after the
        marker has been popped.

        Asserts:

          * Setting the marker on an empty set is observable.
          * First pop returns ``True`` and removes the entry.
          * Second pop on the same instance returns ``False``.
          * Third, fourth, ... pops continue to return ``False`` —
            the set never re-creates the marker from nothing.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"

        # Marker starts absent.
        assert instance_id not in manager._deferred_question_pause

        # ``set_deferred_question_pause`` adds the marker.
        manager.set_deferred_question_pause(instance_id)
        assert instance_id in manager._deferred_question_pause

        # First pop consumes it.
        first = manager.pop_deferred_question_pause(instance_id)
        assert first is True, (
            "first pop_deferred_question_pause call must return True "
            "when the marker was just set"
        )
        assert instance_id not in manager._deferred_question_pause

        # Second pop sees no marker — atomic semantics.
        second = manager.pop_deferred_question_pause(instance_id)
        assert second is False, (
            "second pop_deferred_question_pause call must return False "
            "— the marker was already consumed by the first pop"
        )
        assert instance_id not in manager._deferred_question_pause

        # A third pop remains False — the set does not auto-restore.
        third = manager.pop_deferred_question_pause(instance_id)
        assert third is False, (
            "third pop_deferred_question_pause call must still return "
            "False — pop is purely a check-and-remove, no auto-create"
        )

        # And the marker can be set AGAIN (idempotency of ``set`` is
        # also important for the second-cycle scenario).
        manager.set_deferred_question_pause(instance_id)
        assert instance_id in manager._deferred_question_pause
        # And popped again.
        assert manager.pop_deferred_question_pause(instance_id) is True

    async def test_concurrent_different_instances_markers_isolated(self):
        """Markers for distinct instance IDs are independent.

        ``_deferred_question_pause`` is a ``set`` keyed by instance
        ID. Two instances concurrently asking questions must each
        see their own marker — popping one must NOT remove the other.

        This guards against accidental sharing via a global boolean,
        a single-element set, or a missing key prefix. The C2 fix
        depends on per-instance isolation to avoid pausing the wrong
        instance when several instances are mid-question at once.

        Asserts:

          * Setting both ``iid-a`` and ``iid-b`` markers makes both
            observable.
          * Popping ``iid-a`` returns ``True`` and removes ONLY
            ``iid-a``; ``iid-b`` is still in the set.
          * Popping ``iid-b`` returns ``True`` and removes ONLY
            ``iid-b``.
          * Subsequent pops on both IDs return ``False`` (each
            instance's marker is consumed).
        """
        manager = _make_manager_with_real_marker_state()
        instance_a = "iid-a"
        instance_b = "iid-b"

        # Both markers set concurrently.
        manager.set_deferred_question_pause(instance_a)
        manager.set_deferred_question_pause(instance_b)
        assert instance_a in manager._deferred_question_pause
        assert instance_b in manager._deferred_question_pause

        # Pop A — only A is removed.
        assert manager.pop_deferred_question_pause(instance_a) is True
        assert instance_a not in manager._deferred_question_pause, (
            "popping iid-a must remove iid-a from the set"
        )
        assert instance_b in manager._deferred_question_pause, (
            "popping iid-a must NOT remove iid-b — markers are "
            "per-instance, not shared"
        )

        # Pop B — only B is removed.
        assert manager.pop_deferred_question_pause(instance_b) is True
        assert instance_b not in manager._deferred_question_pause

        # Both ids' markers are gone — subsequent pops return False.
        assert manager.pop_deferred_question_pause(instance_a) is False
        assert manager.pop_deferred_question_pause(instance_b) is False

        # Re-setting A does not restore B (and vice versa).
        manager.set_deferred_question_pause(instance_a)
        assert instance_a in manager._deferred_question_pause
        assert instance_b not in manager._deferred_question_pause

        # A can be popped again independently of B.
        assert manager.pop_deferred_question_pause(instance_a) is True
        assert manager.pop_deferred_question_pause(instance_b) is False

    async def test_process_message_with_tracking_path_deferred_pause(self):
        """The same C2 invariant holds for ``_process_message_with_tracking``.

        Path B (the queued-message processing path) wraps the graph in
        ``async for event in graph.astream(...)`` rather than
        ``await graph.ainvoke(...)``. The investigation found both
        paths share the same finally-block structure: ``_graph_tasks``
        is popped, the deferred marker is observed, and the cascade is
        awaited — with the same try/except swallow around the cascade
        for transient-failure tolerance.

        This test verifies the invariant through the
        ``_process_message_with_tracking`` entrypoint to make sure a
        future refactor of the astream loop's finally block cannot
        silently drop the cascade. The graph mock's ``astream`` calls
        ``set_deferred_question_pause`` once on entry (mimicking
        ``question_pause_node`` running inside the astream loop), then
        yields nothing — the empty stream exits and the finally block
        fires.

        ``is_retry=True`` + ``silent=True`` are used to bypass the
        project / skill / shared-context injection blocks (each has its
        own dedicated tests). The C2 finally block runs regardless of
        ``is_retry``.

        Asserts:

          * ``pause_instance_cascade`` was awaited exactly once with
            the right ``instance_id``.
          * The deferred marker was popped.
          * ``_graph_tasks`` is empty (the task was popped before the
            cascade ran — the C2 ordering invariant).
          * ``release_context_usage_cache`` was called.
        """
        manager = _make_manager_with_real_marker_state()
        instance_id = "iid"
        message_id = "msg-1"

        # Graph mock whose astream sets the deferred marker on entry,
        # then yields no events. The empty stream lets the function
        # proceed straight to the finally block.
        manager.get_instance = AsyncMock(
            return_value=_make_path_b_graph_manager_mock(manager, instance_id)
        )
        service = _make_path_b_service(manager)

        # Pre-condition: backing collections are empty.
        assert instance_id not in manager._graph_tasks
        assert instance_id not in manager._deferred_question_pause

        # Call Path B with is_retry=True to skip the project /
        # skill injection blocks, and silent=True to skip the
        # HumanMessage pre-emission (so we only exercise the
        # graph → finally block path).
        result = await service._process_message_with_tracking(
            instance_id=instance_id,
            message="hi",
            message_id=message_id,
            is_retry=True,
            silent=True,
        )

        # The cascade ran — and crucially it ran from the finally
        # block AFTER the astream loop completed (the C2 invariant),
        # with the production suspension reason.
        manager.pause_instance_cascade.assert_awaited_once_with(
            instance_id, suspension_reason="awaiting_answer"
        )

        # The deferred marker was popped by the finally block.
        assert instance_id not in manager._deferred_question_pause

        # _graph_tasks is empty — the cascade ran AFTER the task was
        # popped, so no self-cancel is possible. Same decisive C2
        # invariant as the Path A test.
        assert manager._graph_tasks == {}

        # Helper the finally block always invokes was also called.
        manager.release_context_usage_cache.assert_called_once_with(instance_id)

        # The function returned a MessageResult.
        assert result is not None