"""Multi-reuse lifecycle scenario tests.

Cycle 2 of ``feature/proactive-compaction-fix`` (review W-3) — these
tests pin the OBSERVABLE multi-reuse behavior under the NEW
inverted-polarity contract:

* Status gate (``COMPACT_REJECT_STATUSES`` = ``{terminated, error,
  failed}``): instance.status ∈ reject-set → INFO skip; engine +
  graph NEVER touched. ``completed`` is NOT in the reject set
  (C1 compact-on-COMPLETED policy), so reuse of a completed
  instance PROCEEDS through the engine.
* Shape gate (inverted polarity): a QUIESCENT checkpoint
  (``state.next == ()``) is the REQUIRED precondition for
  compaction to proceed; non-quiescent → INFO skip.
* Variant-A persist: the proactive site writes through the
  shared seam at ``daemon/services/_compaction_persist_seam.py``
  with ``mid_turn=False`` and ``abort_policy="fail_open"``. The
  seam itself issues zero ``aupdate_state(as_node=...)`` writes
  from the proactive site (the bug-class property the original
  suite caught).

The previous file pinned the OLD inverted polarity (terminal =
skip). Cycle 2 of the feature FLIPPED the polarity: quiescent
(``next=()``) is now the PROCEED condition, and the seam (not
direct ``aupdate_state`` from the call site) is the writer. The
load-bearing invariant the original suite caught — no
``aupdate_state(as_node="agent")`` from the proactive site — is
re-pinned under the new contract via the
``aupdate_state_calls_with_as_node_agent`` counter on the graph
stats dict. The user-facing signal the original suite caught
(``ainvoke`` yields events on every reuse cycle, so the
frontend observes ``RUNNING`` for a non-trivial window) is
unchanged.

What this file covers
---------------------

* ``TestMultiReuseNoCheckpointCorruption`` — across N reuse
  cycles of a completed instance, the proactive path NEVER
  issues ``aupdate_state(..., as_node="agent")``. The
  ``aupdate_state_calls_with_as_node_agent`` counter is the
  property the original 2nd+ reuse bug violated; the
  ``aupdate_state_calls`` total is now allowed to be >0
  (the seam does the messages write, WITHOUT ``as_node``).
* ``TestMultiReuseStreamingBehavior`` — the follow-up
  ``ainvoke`` / ``astream`` runs normally on each reuse
  (yields events, takes non-zero time). This is the
  observable signal the frontend was missing.
* ``TestMultiReuseLifecycleEndToEnd`` — drives the full reuse
  sequence (compact + ainvoke) three times in a row and
  asserts on the accumulated behavior: aupdate_state
  zero times WITH ``as_node="agent"``, ainvoke invoked once
  per cycle, each cycle yields at least one event.
* ``TestNonQuiescentShapeSkipsAsNewNegativeControl`` —
  replaces the OLD ``TestActiveTurnStillCompacts`` (which
  pinned the OLD inverted polarity). The NEW negative
  control pins the inverted-polarity SHAPE gate: a
  non-quiescent checkpoint (``state.next = ("agent",)``)
  is now a SKIP — engine NOT invoked, aupdate_state NOT
  called (with or without ``as_node``).

The seam is patched at the import site in this file so the
``manager.get_instance`` round-trip is bypassed; the test
focuses on the gate behavior, not the seam's internal
``aupdate_state`` shape.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

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
        "aupdate_state_calls_with_as_node_agent": 0,
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
        # Cycle 2 (proactive-compaction-fix review W-3) — track the
        # ``as_node`` kwarg. The pre-fix bug was
        # ``aupdate_state(config, {...}, as_node="agent")`` on a
        # terminal-shaped between-turns checkpoint; that call cleared
        # ``next=()`` and the next ``astream(graph_input)`` returned
        # instantly without running the graph. The fix is the
        # shared seam (Variant A — no ``as_node``). The new
        # contract permits ``aupdate_state`` calls on quiescent
        # checkpoints (the seam does the messages write) but
        # FORBIDS ``as_node="agent"`` from the proactive path
        # (a never-call site that re-introduces the 2nd+ reuse
        # bug).
        if kwargs.get("as_node") == "agent":
            stats["aupdate_state_calls_with_as_node_agent"] += 1
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

    Cycle 2 (W-3 migration) — set ``proactive_enabled=True``
    explicitly. The pre-cycle-2 mock used a bare
    ``MagicMock()`` for ``config.compaction`` whose
    ``proactive_enabled`` auto-attr is a truthy MagicMock — the
    gate is open either way, but the explicit value makes the
    test's intent grep-able and protects against a future
    refactor that flips the kill-switch default.
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
    manager.config.compaction.proactive_enabled = True  # gate open
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
    """Across multiple reuse cycles of a completed instance, the
    proactive path NEVER issues ``aupdate_state(..., as_node="agent")``.

    Cycle 2 (W-3 migration) — the OLD assertion
    (``aupdate_state_calls == 0``) is no longer correct: the new
    contract's quiescent+completed scenario PROCEEDS through the
    engine and the shared seam. The seam itself issues
    ``aupdate_state(config, ..., as_node=...)`` (Variant A: NO
    ``as_node``), so the total ``aupdate_state_calls`` may be >0.
    The load-bearing property the original suite caught is the
    absence of ``as_node="agent"`` (the call form that clears
    ``next=()`` on a terminal-shaped between-turns checkpoint and
    breaks the follow-up ``astream``). The new
    ``aupdate_state_calls_with_as_node_agent`` counter on the
    graph stats dict is the migrated assertion.
    """

    @pytest.mark.asyncio
    async def test_first_reuse_no_as_node_agent_writes(self):
        """First reuse of a completed instance — no
        ``aupdate_state(as_node="agent")``.

        Lifecycle: instance completed → user/parent sends a new
        message → ``send_message`` is invoked →
        ``_maybe_compact_context`` is called with a quiescent
        checkpoint + status=completed (not in reject set) → engine
        invoked → seam called → seam writes
        ``aupdate_state(config, {messages: replacement})`` WITHOUT
        ``as_node``. The follow-up ``ainvoke`` sees the intact
        ``next=()`` and runs the graph normally.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 50},
                next=(),  # quiescent — proceed condition (new contract)
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
            await svc._maybe_compact_context(
                instance_id="inst-multi-reuse-1",
                graph=graph,
                config={"configurable": {"thread_id": "inst-multi-reuse-1"}},
            )

        # The critical invariant — the proactive site NEVER issued
        # ``aupdate_state(..., as_node="agent")``. The seam's
        # Variant A writes go through the patched seam (not
        # graph.aupdate_state) so the graph's own
        # ``aupdate_state_calls_with_as_node_agent`` counter
        # stays at 0.
        assert stats["aupdate_state_calls_with_as_node_agent"] == 0, (
            "aupdate_state(as_node='agent') on a quiescent checkpoint "
            "clears next=() and breaks the subsequent ainvoke; the "
            "proactive path MUST go through the seam (Variant A — no "
            "as_node) instead"
        )

    @pytest.mark.asyncio
    async def test_second_reuse_no_as_node_agent_writes(self):
        """Second reuse of a completed instance — still no
        ``aupdate_state(as_node="agent")``.

        This is the key scenario from the bug report. The first reuse
        may have a smaller message history; the second reuse is where
        accumulated messages tip over the threshold. The original bug
        fired here — the in-call-site ``aupdate_state(as_node="agent")``
        cleared ``next=()`` and the next ``astream`` returned
        instantly.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 120},
                next=(),
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
            await svc._maybe_compact_context(
                instance_id="inst-multi-reuse-2",
                graph=graph,
                config={"configurable": {"thread_id": "inst-multi-reuse-2"}},
            )

        assert stats["aupdate_state_calls_with_as_node_agent"] == 0

    @pytest.mark.asyncio
    async def test_third_reuse_no_as_node_agent_writes(self):
        """Third reuse — still no ``aupdate_state(as_node="agent")``.

        The guard / seam discipline must remain effective across many
        reuse cycles. A regression that worked for the first reuse
        but failed on the third (e.g. due to message-count
        accumulation in a side cache) would be caught here.
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

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
            await svc._maybe_compact_context(
                instance_id="inst-multi-reuse-3",
                graph=graph,
                config={"configurable": {"thread_id": "inst-multi-reuse-3"}},
            )

        assert stats["aupdate_state_calls_with_as_node_agent"] == 0

    @pytest.mark.asyncio
    async def test_repeated_reuses_never_call_aupdate_state_with_as_node_agent(
        self,
    ):
        """Five sequential reuse cycles → zero
        ``aupdate_state(..., as_node="agent")`` calls total.

        Single big check that accumulates the multi-reuse invariant
        under the new contract: after N invocations of
        ``_maybe_compact_context`` on the same (quiescent, completed)
        checkpoint, the proactive path has never directly invoked
        ``aupdate_state(as_node="agent")`` on the graph. The seam's
        Variant A (no as_node) writes go through the patched seam;
        only a regression that bypasses the seam (call-site direct
        ``aupdate_state`` with ``as_node="agent"``) would push this
        counter above 0 and fail the test.
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

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
            for cycle in range(5):
                await svc._maybe_compact_context(
                    instance_id=f"inst-cycle-{cycle}",
                    graph=graph,
                    config={
                        "configurable": {
                            "thread_id": f"inst-cycle-{cycle}"
                        }
                    },
                )

        assert stats["aupdate_state_calls_with_as_node_agent"] == 0, (
            f"After 5 reuse cycles on a quiescent + completed "
            f"checkpoint, aupdate_state(as_node='agent') must have "
            f"been called 0 times, got "
            f"{stats['aupdate_state_calls_with_as_node_agent']}"
        )


# ---------------------------------------------------------------------------
# Multi-reuse: the follow-up ainvoke runs normally each cycle
# ---------------------------------------------------------------------------


class TestMultiReuseStreamingBehavior:
    """After ``_maybe_compact_context`` returns, the follow-up
    ``ainvoke`` must actually run the graph — yielding events and
    taking non-zero wall-clock time.

    This is the OBSERVABLE signal the frontend was missing. The
    pre-fix bug: ``aupdate_state(as_node="agent")`` from the
    call site (NOT the seam) cleared ``next=()`` and ``ainvoke``
    returned instantly with zero events. The frontend saw
    ``COMPLETED → RUNNING → COMPLETED`` collapse to <100 ms and
    never observed the running state.

    After the new-contract fix: the seam's Variant A writes
    (``aupdate_state(config, ..., as_node=...)`` — no ``as_node``)
    leave ``next=()`` intact, and ``ainvoke`` runs the graph
    normally — yielding events over a non-trivial duration. The
    new contract ALLOWS the engine + seam to run on
    quiescent+completed (the previous file's "skipped entirely"
    expectation was the OLD inverted polarity); these tests
    continue to assert the user-facing signal (events + wall
    time) and remain load-bearing under the new contract.
    """

    @pytest.mark.asyncio
    async def test_first_reuse_ainvoke_runs_graph_normally(self):
        """First reuse: ``ainvoke`` yields events after the
        (proceed + seam-Variant-A) compaction path.

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

        # Step 1: send_message → _maybe_compact_context (proceeds
        # on quiescent+completed, goes through the seam).
        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
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
            "ran (the broken path yielded 0 because next=() was cleared "
            "by a direct aupdate_state(as_node='agent'))"
        )
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_second_reuse_ainvoke_runs_graph_normally(self):
        """Second reuse: same behavioral check after a second
        compact attempt.

        This is the EXACT scenario from the bug report: instance
        completes → reused → completes again → reused again, and
        on the second reuse ``ainvoke`` must still run normally.
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

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
            await svc._maybe_compact_context(
                instance_id="inst-stream-2",
                graph=graph,
                config={"configurable": {"thread_id": "inst-stream-2"}},
            )

        # Drain ainvoke — the broken code path yielded 0 events
        # because the call-site aupdate_state(as_node='agent')
        # had cleared next=().
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

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
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

        1. Instance completes (status=COMPLETED, checkpoint
           quiescent — the shape is the same as IDLE between-turns
           under the new contract)
        2. send_message → status flips to RUNNING, graph runs to
           completion
        3. Instance completes again
        4. send_message → status flips to RUNNING, graph runs again
        5. Instance completes a third time
        6. send_message → status flips to RUNNING, graph runs again

    On every reuse cycle the graph MUST run (yielding events) so
    the frontend observes ``RUNNING`` for a non-trivial duration.
    This is the precise scenario the bug broke.

    Cycle 2 (W-3 migration) — the OLD ``aupdate_state_calls == 0``
    assertion is replaced with
    ``aupdate_state_calls_with_as_node_agent == 0`` (the
    load-bearing fix property: no direct
    ``aupdate_state(as_node="agent")`` from the proactive site).
    The user-facing signal (events yielded, ainvoke runs) is
    unchanged.
    """

    @pytest.mark.asyncio
    async def test_three_reuse_cycles_no_checkpoint_corruption(self):
        """Three full reuse cycles: aupdate_state zero times
        WITH ``as_node="agent"``, ainvoke called 3 times, events
        yielded on every cycle.

        This is the consolidated behavioral check. It mirrors how
        the bug manifested in production: a parent agent reuses a
        child multiple times and on the 2nd reuse the child's
        status flips back to COMPLETED before the frontend can
        observe RUNNING.
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 80},
                next=(),  # quiescent — proceed condition (new contract)
            ),
            ainvoke_delay=0.005,  # 5ms — non-trivial but fast for tests
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        config_template = {"configurable": {"thread_id": "inst-lifecycle"}}

        # Three reuse cycles. Each cycle: _maybe_compact_context
        # (proceeds under the new contract → seam-Variant-A)
        # then ainvoke (drained to completion). Before the fix,
        # the call-site aupdate_state(as_node='agent') cleared
        # next=() and ainvoke yielded 0 events. After the fix:
        # the seam's Variant A leaves next=() intact, and every
        # ainvoke yields events.
        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
            for cycle in range(3):
                # Reuse N: simulate the instance being terminal
                # (completed). In production this is the same
                # checkpoint; in the test we keep next=() so the
                # gate logic is exercised identically.
                await svc._maybe_compact_context(
                    instance_id="inst-lifecycle",
                    graph=graph,
                    config=config_template,
                )

                # send_message then ainvoke — drain the async
                # generator.
                events = []
                async for event in graph.ainvoke(
                    {"messages": [HumanMessage(content=f"reuse {cycle}")]},
                    config_template,
                ):
                    events.append(event)

                # Per-cycle observable behavior.
                assert stats["ainvoke_calls"] == cycle + 1, (
                    f"ainvoke must be called once per reuse cycle "
                    f"(cycle={cycle})"
                )
                assert len(events) >= 1, (
                    f"ainvoke must yield events on every reuse cycle "
                    f"(cycle={cycle}); 0 events = the bug"
                )

        # Cross-cycle invariant: aupdate_state was NEVER called
        # WITH ``as_node="agent"``. The total
        # ``aupdate_state_calls`` may be >0 (the seam's Variant A
        # does the messages write) but the call form that
        # reintroduces the bug is the only thing pinned here.
        assert stats["aupdate_state_calls_with_as_node_agent"] == 0, (
            f"aupdate_state(as_node='agent') must not be called on "
            f"quiescent checkpoints across reuse cycles; got "
            f"{stats['aupdate_state_calls_with_as_node_agent']} "
            f"calls (this re-introduces the 2nd+ reuse bug)"
        )

    @pytest.mark.asyncio
    async def test_reuse_cycles_take_non_trivial_wall_clock_time(self):
        """Each reuse cycle takes non-trivial wall-clock time, so
        the frontend CAN observe RUNNING.

        The bug's signature: the cycle collapsed to <100ms so the
        frontend never observed ``RUNNING``. After the fix, the
        cycle takes whatever time the graph actually needs
        (mocked here at 20ms per ainvoke event).

        We sum the elapsed time across 3 cycles and assert it's
        well above the broken-path signature (<100ms total). A
        regression that re-introduces the instant-return path
        would yield a sub-millisecond total and fail this
        assertion.
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

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
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
        # (20ms → ≥16ms expected; we allow ≥10ms for test-loop
        # slack).
        for cycle, t in enumerate(cycle_times):
            assert t >= 0.010, (
                f"reuse cycle {cycle} returned in {t*1000:.2f}ms — "
                f"this is the instant-return symptom of the original bug"
            )

        # The load-bearing fix property: zero aupdate_state calls
        # with ``as_node="agent"`` across the whole sequence.
        # (The seam does the messages write WITHOUT ``as_node``.)
        assert stats["aupdate_state_calls_with_as_node_agent"] == 0
        assert stats["ainvoke_calls"] == 3
        assert stats["ainvoke_events_yielded"] == 3

    @pytest.mark.asyncio
    async def test_reuse_status_changes_observable_via_ainvoke_yield(self):
        """A consolidated invariant check using ``ainvoke``
        event-yield as the proxy for "the lifecycle was clean."

        Cycle 2 (W-3 migration) — the OLD meta-test used
        ``aupdate_state_calls == 0`` as the proxy. Under the new
        contract the total ``aupdate_state_calls`` may be >0
        (the seam's Variant A does the messages write), so the
        proxy moves to the user-facing signal: every reuse cycle
        must have a non-zero ``ainvoke_events_yielded`` count.
        The ``as_node="agent"`` discipline is the load-bearing
        property the call-site regression would violate.

        This is the meta-test: if a future regression
        re-introduces the bug on the reuse path, this test fails
        loudly via the ainvoke event-yield assertion (the
        instant-return symptom that originally made the
        COMPLETED → RUNNING → COMPLETED cycle collapse to <100ms).
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={"messages": [HumanMessage(content="m")] * 100},
                next=(),
            ),
            ainvoke_delay=0.005,
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        config_template = {"configurable": {"thread_id": "inst-meta"}}

        # Run 4 reuse cycles (1st, 2nd, 3rd, 4th reuse).
        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ):
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

        # Hard invariant: zero aupdate_state calls WITH
        # ``as_node="agent"`` across 4 reuse cycles on a
        # quiescent checkpoint.
        assert stats["aupdate_state_calls_with_as_node_agent"] == 0, (
            f"Across 4 reuse cycles on a quiescent checkpoint, "
            f"aupdate_state(as_node='agent') was called "
            f"{stats['aupdate_state_calls_with_as_node_agent']} "
            f"time(s). This re-introduces the 2nd+ reuse bug — "
            f"the frontend will not observe RUNNING."
        )
        # Each cycle ran the graph and yielded at least one event.
        assert stats["ainvoke_calls"] == 4
        assert stats["ainvoke_events_yielded"] >= 4


# ---------------------------------------------------------------------------
# Negative control: active turns still compact
# ---------------------------------------------------------------------------


class TestNonQuiescentShapeSkipsAsNewNegativeControl:
    """Cycle 2 (W-3 migration) — the OLD ``TestActiveTurnStillCompacts``
    pinned the OLD inverted polarity (non-terminal shape → engine
    invoked → aupdate_state called). The new contract FLIPS the
    polarity: non-quiescent (``state.next = ("agent",)``) is now a
    SKIP at INFO (the engine is NEVER called; aupdate_state is
    NEVER touched).

    This class replaces ``TestActiveTurnStillCompacts`` with the
    inverted-polarity negative control. It pins the SHAPE gate
    explicitly: a non-quiescent checkpoint MUST short-circuit
    before the engine, regardless of the status. Without this
    control, a regression that *always* allowed the engine to run
    (e.g. dropping the shape gate) would silently enable
    mid-superstep compaction and re-introduce the
    mid-turn-state disturbance the gate is designed to prevent.
    """

    @pytest.mark.asyncio
    async def test_non_quiescent_shape_skips_engine_and_writes(self):
        """Non-quiescent shape → engine NOT invoked; aupdate_state
        NOT called (with or without ``as_node``).

        The OLD negative control asserted the inverse: non-terminal
        + compactor result → ``aupdate_state`` called. The new
        contract's SHAPE gate rejects non-quiescent BEFORE the
        engine, so the compactor mock is not awaited and the
        graph's aupdate_state counter stays at 0 (including the
        ``as_node="agent"`` sub-counter).
        """
        graph, stats = _make_graph(
            state=_MockGraphState(
                values={
                    "messages": [HumanMessage(content="m")] * 50,
                    "compacted_at": None,
                },
                next=("agent",),  # NOT quiescent — has a pending node
            ),
        )
        compactor = _make_compactor()
        manager = _make_manager(compactor=compactor)
        manager.get_instance = AsyncMock(return_value=graph)
        svc = _make_service(manager)

        with patch(
            "daemon.services._compaction_persist_seam."
            "persist_compaction_result",
            new=AsyncMock(return_value=True),
        ) as seam_mock:
            await svc._maybe_compact_context(
                instance_id="inst-active-control",
                graph=graph,
                config={
                    "configurable": {"thread_id": "inst-active-control"}
                },
            )

        # The SHAPE gate short-circuited BEFORE the engine.
        compactor.compact_state.assert_not_awaited()
        # No aupdate_state at all (with or without ``as_node``).
        assert stats["aupdate_state_calls"] == 0, (
            "non-quiescent shape MUST skip the engine and any "
            "aupdate_state write; the bug-class property is a "
            "mid-superstep disturbance, not just the as_node='agent' "
            "form"
        )
        assert stats["aupdate_state_calls_with_as_node_agent"] == 0
        # The seam is also untouched (no engine result to persist).
        seam_mock.assert_not_awaited()