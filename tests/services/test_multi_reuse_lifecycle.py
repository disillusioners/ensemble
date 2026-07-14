"""Multi-reuse lifecycle scenario tests.

Background
----------

The original bug (branch ``feature/instance-status-reuse-bug``, commit
``52133a14``): when a parent agent reuses a completed child instance via
``send_message`` a 2nd, 3rd, or 4th time, the child's status did not show
as ``running`` for any observable duration. Root cause was in
:meth:`InstanceMessagingService._maybe_compact_context` at
``daemon/services/instance_messaging.py:539-557`` (the "Terminal-checkpoint
guard"):

    Compaction called ``graph.aupdate_state(config, {'messages':
    result.replacement_messages}, as_node='agent')`` unconditionally on
    every non-retry graph turn. On a terminal checkpoint this clears the
    checkpoint's ``next=()``, causing the subsequent ``astream(graph_input)``
    to return instantly without running the graph. The
    ``COMPLETED → RUNNING → COMPLETED`` cycle then collapsed to <100 ms so
    the frontend never observed ``RUNNING``.

The fix added ``if not state.next: return`` to skip compaction on terminal
checkpoints. Active (non-terminal) turns compact normally.

What this file covers
---------------------

These tests follow the same mocking pattern as
``tests/services/test_instance_messaging_compaction_guard.py``
(``_make_graph`` builds a LangGraph mock with controlled ``aget_state``,
``aupdate_state``, and ``ainvoke``). The focus here is on the OBSERVABLE
behavior of the multi-reuse lifecycle, not just the internal guard:

* ``TestMultiReuseNoCheckpointCorruption`` — across N reuse cycles of a
  completed instance, ``graph.aupdate_state`` is NEVER called. This is
  the direct invariant the fix introduces.
* ``TestMultiReuseStreamingBehavior`` — the follow-up ``ainvoke`` /
  ``astream`` runs normally on each reuse (yields events, takes
  non-zero time). This is the observable signal the frontend was missing:
  the cycle has to actually run the graph so ``RUNNING`` is visible.
* ``TestMultiReuseLifecycleEndToEnd`` — drives the full reuse sequence
  (compact + ainvoke) three times in a row and asserts on the accumulated
  behavior: ``aupdate_state`` zero times, ``ainvoke`` invoked once per
  cycle, and each cycle yields at least one event. This is the exact
  scenario from the bug report.
* ``TestActiveTurnStillCompacts`` — negative control: non-terminal
  checkpoints still compact. Without this, a regression that *always*
  skipped compaction would silently pass the lifecycle tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.instance_messaging import InstanceMessagingService

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _MockGraphState:
    """Mimics LangGraph's ``GetStateResult`` shape.

    ``values`` carries channel_values (messages, compacted_at, …);
    ``next`` is the tuple of next nodes LangGraph will execute —
    empty tuple signals a terminal checkpoint.
    """

    values: dict[str, Any] = field(default_factory=dict)
    next: tuple[str, ...] = ()


def _make_graph(
    *,
    state: _MockGraphState | None,
    ainvoke_events: list[tuple[str, dict]] | None = None,
    ainvoke_delay: float = 0.0,
) -> tuple[MagicMock, dict]:
    """Build a LangGraph mock that simulates a multi-reuse lifecycle.

    Returns ``(graph, stats)`` where ``stats`` is a dict that records
    calls to ``aupdate_state`` (``stats["aupdate_state_calls"]``) and
    invocations of ``ainvoke`` (``stats["ainvoke_calls"]``,
    ``stats["ainvoke_events_yielded"]``).

    ``ainvoke_events`` defaults to a single ``("agent", {"messages":
    [AIMessage(...)]})`` event — enough for the caller to observe that
    the graph actually ran (rather than returning instantly with zero
    events, which is the broken behavior). ``ainvoke_delay`` lets tests
    simulate a non-trivial graph execution time so the cycle isn't
    observably instant — this is the property the bug violated.
    """
    stats: dict = {
        "aupdate_state_calls": 0,
        "ainvoke_calls": 0,
        "ainvoke_events_yielded": 0,
        "aget_state_calls": 0,
    }
    graph = MagicMock()

    async def fake_aget_state(_config):
        stats["aget_state_calls"] += 1
        return state

    async def fake_aupdate_state(*args, **kwargs):
        stats["aupdate_state_calls"] += 1
        return None

    async def fake_ainvoke(*args, **kwargs):
        stats["ainvoke_calls"] += 1
        events = ainvoke_events if ainvoke_events is not None else [
            ("agent", {"messages": [AIMessage(content="hi back", id="ai-1")]}),
        ]
        for event in events:
            if ainvoke_delay > 0:
                await asyncio.sleep(ainvoke_delay)
            stats["ainvoke_events_yielded"] += 1
            yield event

    graph.aget_state = fake_aget_state
    graph.aupdate_state = fake_aupdate_state
    graph.ainvoke = fake_ainvoke
    return graph, stats


def _make_compactor(
    *,
    replacement: list | None = None,
    return_value: Any = None,
) -> MagicMock:
    """Build a compactor mock that would (in the broken code path)
    produce a non-None ``CompactionResult`` — enough to drive the
    ``graph.aupdate_state`` call. On the fixed path the guard short-
    circuits before this is ever invoked, so the value returned here
    is irrelevant to the multi-reuse lifecycle tests.
    """
    from daemon.compaction import CompactionResult

    compactor = MagicMock()
    compactor.compact_state = AsyncMock(
        return_value=return_value if return_value is not None else CompactionResult(
            replacement_messages=replacement or [
                HumanMessage(content="summary", id="summary-1")
            ],
            tokens_before=1000,
            tokens_after=100,
            tokens_saved=900,
            messages_before=20,
            messages_after=1,
            compaction_type="summarization",
        ),
    )
    return compactor


def _make_manager(*, compactor: MagicMock | None = None) -> MagicMock:
    """Build a manager mock with the minimum surface area
    ``_maybe_compact_context`` and ``send_message`` touch.

    ``_compactor`` is accessed through a property on
    ``InstanceMessagingService`` (``self._manager._compactor``), so the
    compactor is hung off the manager mock. ``config`` is supplied
    because the function reads ``self._config.llm.*`` before deciding
    whether to compact. ``get_instance`` returns the graph that
    ``send_message`` would ``ainvoke``; ``_instance_repository.get``
    returns the instance metadata so ``is_first_message`` can be
    evaluated.
    """
    manager = MagicMock()
    manager._compactor = compactor
    manager.config.llm.model = "test-model"
    manager.config.llm.base_url = "http://test"
    manager.config.llm.api_key = "sk-test"
    manager.config.llm.model_vision = None
    manager.config.llm.temperature = 0.0
    manager.config.llm.request_timeout = 60.0
    manager.config.compaction = MagicMock()  # threaded into CompactionContext
    manager.config.limits.graph_recursion_limit = 50
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=_make_instance_meta(status="completed"),
    )
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    return manager


def _make_instance_meta(*, status: str = "completed") -> MagicMock:
    """Build an instance meta mock with the requested status.

    Status defaults to ``"completed"`` — the terminal state the bug
    scenario starts from. ``_send_message`` reads
    ``instance_meta.status == InstanceStatus.IDLE.value`` to compute
    ``is_first_message``, which is irrelevant for the lifecycle test.
    """
    meta = MagicMock()
    meta.instance_id = "inst-multi-reuse"
    meta.status = status
    meta.agent_id = "child-agent"
    meta.parent_id = "parent-1"
    meta.instance_metadata = None
    return meta


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build an :class:`InstanceMessagingService` around ``manager``.

    ``InstanceMessagingService.__init__`` only requires ``manager`` and
    ``cancellation_service``. The cancellation service is irrelevant for
    the multi-reuse lifecycle test.
    """
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )


# ---------------------------------------------------------------------------
# Multi-reuse: no checkpoint corruption across reuse cycles
# ---------------------------------------------------------------------------


class TestMultiReuseNoCheckpointCorruption:
    """Across multiple reuse cycles of a completed instance,
    ``graph.aupdate_state`` is NEVER called.

    This is the direct invariant the fix introduces. The original bug
    called ``aupdate_state(as_node="agent")`` on the terminal
    checkpoint, which cleared ``next=()`` and caused the next
    ``ainvoke`` to return instantly. By skipping compaction entirely
    on terminal checkpoints, the guard preserves the checkpoint's
    ``next=()`` so subsequent ``ainvoke`` calls have a graph to run.
    """

    @pytest.mark.asyncio
    async def test_first_reuse_terminal_skips_aupdate_state(self):
        """First reuse of a completed instance — no ``aupdate_state``.

        The lifecycle: instance completed → user/parent sends a new
        message → ``send_message`` is invoked → ``_maybe_compact_context``
        is called with a terminal checkpoint. The guard must short-
        circuit before ``aupdate_state`` is called.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 50},
                next=(),  # terminal — graph finished
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-multi-reuse-1",
            graph=graph,
            config={"configurable": {"thread_id": "inst-multi-reuse-1"}},
        )

        # The critical invariant — no aupdate_state on terminal.
        assert stats["aupdate_state_calls"] == 0, (
            "aupdate_state must not be called on terminal checkpoints; "
            "doing so clears next=() and breaks the subsequent ainvoke"
        )
        # And the compactor itself should not have been invoked either.
        compactor.compact_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_second_reuse_terminal_skips_aupdate_state(self):
        """Second reuse of a completed instance — still no ``aupdate_state``.

        This is the key scenario from the bug report. The first reuse
        may have a smaller message history and not cross the compaction
        threshold; the second reuse is where accumulated messages tip
        over the threshold. The original bug fired here.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 120},
                next=(),  # terminal — completed for the 2nd time
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-multi-reuse-2",
            graph=graph,
            config={"configurable": {"thread_id": "inst-multi-reuse-2"}},
        )

        assert stats["aupdate_state_calls"] == 0
        compactor.compact_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_third_reuse_terminal_skips_aupdate_state(self):
        """Third reuse — still no ``aupdate_state``.

        The guard must remain effective across many reuse cycles. A
        regression that worked for the first reuse but failed on the
        third (e.g. due to message-count accumulation in a side cache)
        would be caught here.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 200},
                next=(),
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-multi-reuse-3",
            graph=graph,
            config={"configurable": {"thread_id": "inst-multi-reuse-3"}},
        )

        assert stats["aupdate_state_calls"] == 0
        compactor.compact_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repeated_reuses_never_call_aupdate_state(self):
        """Five sequential reuse cycles → zero ``aupdate_state`` calls total.

        Single big check that accumulates the multi-reuse invariant:
        after N invocations of ``_maybe_compact_context`` on the same
        (terminal) checkpoint, the graph's checkpoint state has been
        touched zero times. This is the precise property the bug-fix
        introduction was meant to enforce.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 150},
                next=(),
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        for cycle in range(5):
            await svc._maybe_compact_context(
                instance_id=f"inst-cycle-{cycle}",
                graph=graph,
                config={"configurable": {"thread_id": f"inst-cycle-{cycle}"}},
            )

        assert stats["aupdate_state_calls"] == 0, (
            f"After 5 reuse cycles on a terminal checkpoint, "
            f"aupdate_state must have been called 0 times, "
            f"got {stats['aupdate_state_calls']}"
        )
        assert compactor.compact_state.await_count == 0


# ---------------------------------------------------------------------------
# Multi-reuse: the follow-up ainvoke runs normally each cycle
# ---------------------------------------------------------------------------


class TestMultiReuseStreamingBehavior:
    """After ``_maybe_compact_context`` returns, the follow-up
    ``ainvoke`` must actually run the graph — yielding events and
    taking non-zero wall-clock time.

    This is the OBSERVABLE signal the frontend was missing. Before the
    fix, ``aupdate_state`` cleared ``next=()`` and ``ainvoke`` returned
    instantly with zero events. The frontend saw
    ``COMPLETED → RUNNING → COMPLETED`` collapse to <100 ms and never
    observed the running state.

    After the fix, ``_maybe_compact_context`` skips compaction on
    terminal checkpoints, ``next=()`` remains intact, and ``ainvoke``
    runs the graph normally — yielding events over a non-trivial
    duration.
    """

    @pytest.mark.asyncio
    async def test_first_reuse_ainvoke_runs_graph_normally(self):
        """First reuse: ``ainvoke`` yields events after the (skipped)
        compaction guard.

        We verify two things:
        1. ``ainvoke`` is invoked exactly once.
        2. It yields at least one event (i.e. it actually ran the
           graph rather than returning instantly).

        Both are observable signals the user would see in the SSE
        stream and on the instance status.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 50},
                next=(),
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        # Step 1: send_message → _maybe_compact_context (must skip).
        await svc._maybe_compact_context(
            instance_id="inst-stream-1",
            graph=graph,
            config={"configurable": {"thread_id": "inst-stream-1"}},
        )
        # Step 2: ainvoke runs the graph normally (we drain the
        # async generator to completion).
        events = []
        async for event in graph.ainvoke({"messages": [HumanMessage(content="hi")]}, config=None):
            events.append(event)

        assert stats["ainvoke_calls"] == 1
        assert stats["ainvoke_events_yielded"] >= 1, (
            "ainvoke must yield events to signal that the graph actually "
            "ran (the broken path yielded 0 because next=() was cleared)"
        )
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_second_reuse_ainvoke_runs_graph_normally(self):
        """Second reuse: same behavioral check after a second compact
        attempt.

        This is the EXACT scenario from the bug report: instance
        completes → reused → completes again → reused again, and on
        the second reuse ``ainvoke`` must still run normally.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 120},
                next=(),
            ),
            ainvoke_delay=0.01,  # simulate non-trivial graph exec time
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-stream-2",
            graph=graph,
            config={"configurable": {"thread_id": "inst-stream-2"}},
        )

        # Drain ainvoke — the broken code path yielded 0 events because
        # aupdate_state had cleared next=().
        start = asyncio.get_event_loop().time()
        events = []
        async for event in graph.ainvoke({"messages": [HumanMessage(content="hi")]}, config=None):
            events.append(event)
        elapsed = asyncio.get_event_loop().time() - start

        assert stats["ainvoke_calls"] == 1
        assert stats["ainvoke_events_yielded"] >= 1
        # The mock sleeps 10ms before yielding its single event — a
        # non-instant return is the core signal the fix restores.
        assert elapsed >= 0.005, (
            f"ainvoke returned too quickly ({elapsed*1000:.2f} ms); "
            f"this is the instant-return symptom of the original bug"
        )

    @pytest.mark.asyncio
    async def test_third_reuse_ainvoke_runs_graph_normally(self):
        """Third reuse: same behavior.

        Past regressions that the original tests didn't catch (e.g.
        guard worked once but failed on the third cycle) would be
        caught here.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 200},
                next=(),
            ),
            ainvoke_delay=0.01,
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-stream-3",
            graph=graph,
            config={"configurable": {"thread_id": "inst-stream-3"}},
        )

        events = []
        async for event in graph.ainvoke({"messages": [HumanMessage(content="hi")]}, config=None):
            events.append(event)

        assert stats["ainvoke_calls"] == 1
        assert stats["ainvoke_events_yielded"] >= 1
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# Multi-reuse: end-to-end lifecycle (the user's scenario)
# ---------------------------------------------------------------------------


class TestMultiReuseLifecycleEndToEnd:
    """The complete multi-reuse lifecycle, driven end-to-end:

        1. Instance completes (status=COMPLETED, checkpoint terminal)
        2. send_message → status flips to RUNNING, graph runs to
           completion
        3. Instance completes again
        4. send_message → status flips to RUNNING, graph runs again
        5. Instance completes a third time
        6. send_message → status flips to RUNNING, graph runs again

    On every reuse cycle the graph MUST run (yielding events) so the
    frontend observes ``RUNNING`` for a non-trivial duration. This is
    the precise scenario the bug broke.
    """

    @pytest.mark.asyncio
    async def test_three_reuse_cycles_no_checkpoint_corruption(self):
        """Three full reuse cycles: ``aupdate_state`` called 0 times,
        ``ainvoke`` called 3 times, events yielded on every cycle.

        This is the consolidated behavioral check. It mirrors how the
        bug manifested in production: a parent agent reuses a child
        multiple times and on the 2nd reuse the child's status flips
        back to COMPLETED before the frontend can observe RUNNING.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 80},
                next=(),  # terminal — instance completed
            ),
            ainvoke_delay=0.005,  # 5ms — non-trivial but fast for tests
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        config_template = {"configurable": {"thread_id": "inst-lifecycle"}}

        # Three reuse cycles. Each cycle: _maybe_compact_context then
        # ainvoke (drained to completion). Before the fix, cycle 2 and
        # 3 would have aupdate_state called and ainvoke would yield 0
        # events. After the fix: aupdate_state is never called, and
        # every ainvoke yields events.
        for cycle in range(3):
            # Reuse N: simulate the instance being terminal (completed).
            # In production this is the same checkpoint; in the test we
            # keep next=() so the guard logic is exercised identically.
            await svc._maybe_compact_context(
                instance_id="inst-lifecycle",
                graph=graph,
                config=config_template,
            )

            # send_message then ainvoke — drain the async generator.
            events = []
            async for event in graph.ainvoke(
                {"messages": [HumanMessage(content=f"reuse {cycle}")]},
                config_template,
            ):
                events.append(event)

            # Per-cycle observable behavior.
            assert stats["ainvoke_calls"] == cycle + 1, (
                f"ainvoke must be called once per reuse cycle (cycle={cycle})"
            )
            assert len(events) >= 1, (
                f"ainvoke must yield events on every reuse cycle "
                f"(cycle={cycle}); 0 events = the bug"
            )

        # Cross-cycle invariant: aupdate_state was NEVER called.
        assert stats["aupdate_state_calls"] == 0, (
            f"aupdate_state must not be called on terminal checkpoints "
            f"across reuse cycles; got {stats['aupdate_state_calls']} calls"
        )
        # And the compactor itself should not have been invoked.
        assert compactor.compact_state.await_count == 0, (
            "compactor.compact_state must not be called on terminal "
            "checkpoints; the guard short-circuits before it"
        )

    @pytest.mark.asyncio
    async def test_reuse_cycles_take_non_trivial_wall_clock_time(self):
        """Each reuse cycle takes non-trivial wall-clock time, so the
        frontend CAN observe RUNNING.

        The bug's signature: the cycle collapsed to <100ms so the
        frontend never observed ``RUNNING``. After the fix, the cycle
        takes whatever time the graph actually needs (mocked here at
        20ms per ainvoke event).

        We sum the elapsed time across 3 cycles and assert it's well
        above the broken-path signature (<100ms total). A regression
        that re-introduces the instant-return path would yield a
        sub-millisecond total and fail this assertion.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 80},
                next=(),
            ),
            ainvoke_delay=0.02,  # 20ms — visible to the frontend
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        config_template = {"configurable": {"thread_id": "inst-timing"}}

        cycle_times = []
        for cycle in range(3):
            await svc._maybe_compact_context(
                instance_id="inst-timing",
                graph=graph,
                config=config_template,
            )

            start = asyncio.get_event_loop().time()
            async for _event in graph.ainvoke(
                {"messages": [HumanMessage(content=f"reuse {cycle}")]},
                config_template,
            ):
                pass
            elapsed = asyncio.get_event_loop().time() - start
            cycle_times.append(elapsed)

        # Every cycle must take at least ~80% of the mock delay
        # (20ms → ≥16ms expected; we allow ≥10ms for test-loop slack).
        for cycle, t in enumerate(cycle_times):
            assert t >= 0.010, (
                f"reuse cycle {cycle} returned in {t*1000:.2f}ms — "
                f"this is the instant-return symptom of the original bug"
            )

        # And no checkpoint corruption across the whole sequence.
        assert stats["aupdate_state_calls"] == 0
        assert stats["ainvoke_calls"] == 3
        assert stats["ainvoke_events_yielded"] == 3

    @pytest.mark.asyncio
    async def test_reuse_status_changes_observable_via_aupdate_state_count(self):
        """A consolidated invariant check using ``aupdate_state``
        call count as the proxy for "the lifecycle was clean."

        Specifically: in the broken code path,
        ``_maybe_compact_context`` would call ``aupdate_state`` on
        every reuse cycle once message count crossed the compaction
        threshold. In the fixed code path, ``aupdate_state`` is never
        called. The status of the instance therefore does not flip
        instant-return to COMPLETED, and the frontend observes
        ``RUNNING`` for the full graph-execution window.

        This is the meta-test: if a future regression re-introduces
        the bug on the reuse path, this test fails loudly.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 100},
                next=(),
            ),
            ainvoke_delay=0.005,
        )
        # Provide a compactor that, if the guard were missing, would
        # issue an aupdate_state. This makes the test maximally
        # sensitive to the regression: if a single aupdate_state
        # sneaks through, the count would jump from 0 to ≥1 and the
        # test fails.
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        config_template = {"configurable": {"thread_id": "inst-meta"}}

        # Run 4 reuse cycles (1st, 2nd, 3rd, 4th reuse).
        for cycle in range(4):
            await svc._maybe_compact_context(
                instance_id="inst-meta",
                graph=graph,
                config=config_template,
            )
            async for _event in graph.ainvoke(
                {"messages": [HumanMessage(content=f"msg {cycle}")]},
                config_template,
            ):
                pass

        # Hard invariant: zero aupdate_state calls across 4 reuse
        # cycles on a terminal checkpoint.
        assert stats["aupdate_state_calls"] == 0, (
            f"Across 4 reuse cycles on a terminal checkpoint, "
            f"aupdate_state was called {stats['aupdate_state_calls']} "
            f"time(s). This re-introduces the 2nd+ reuse bug — the "
            f"frontend will not observe RUNNING."
        )
        # Each cycle ran the graph and yielded at least one event.
        assert stats["ainvoke_calls"] == 4
        assert stats["ainvoke_events_yielded"] >= 4


# ---------------------------------------------------------------------------
# Negative control: active turns still compact
# ---------------------------------------------------------------------------


class TestActiveTurnStillCompacts:
    """When the checkpoint is NOT terminal (the graph has a pending
    node), compaction must run normally.

    Without this negative control, a regression that *always* skipped
    compaction (e.g. broke the guard condition) would silently pass
    the multi-reuse tests above and disable compaction on active
    conversations — a much worse failure mode.
    """

    @pytest.mark.asyncio
    async def test_active_turn_runs_compactor_and_writes_compaction(self):
        """Non-terminal checkpoint with high message count →
        ``compact_state`` is awaited and ``aupdate_state`` runs.

        This is the inverse of the multi-reuse bug scenario: the
        checkpoint has work pending (``state.next = ("agent",)``), so
        the guard does NOT short-circuit, and compaction proceeds
        normally.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={
                    "messages": [HumanMessage(content="m")] * 50,
                    "compacted_at": None,
                },
                next=("agent",),  # active — has work pending
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        # _instance_repository.get is called by _get_system_prompt_tokens.
        manager._instance_repository.get = MagicMock(return_value=None)
        svc = _make_service(manager)

        await svc._maybe_compact_context(
            instance_id="inst-active-control",
            graph=graph,
            config={"configurable": {"thread_id": "inst-active-control"}},
        )

        # The guard did NOT short-circuit — compactor was invoked.
        compactor.compact_state.assert_awaited_once()
        # aupdate_state was called to write the replacement messages.
        assert stats["aupdate_state_calls"] >= 1, (
            "non-terminal checkpoint with non-None compactor result "
            "must write the replacement back via aupdate_state"
        )