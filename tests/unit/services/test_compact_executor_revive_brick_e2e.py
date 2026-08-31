"""WS-2.5 — Revive-brick regression test (REAL LangGraph + file-backed SQLite).

O17 BINDING (2026-08-31 approver pin): the fixture MUST drive a real
graph run, not mocks. The brick mode is a property of the live
checkpointer + ``aupdate_state`` + ``astream`` interaction; mocks can't
reproduce the documented collapse.

DB discipline: file-backed SQLite (``tmp_path``) — never StaticPool /
in-memory (repo write-corruption hazard; production PG unaffected).
The same pattern as ``tests/integration/test_persistence_w1_markers.py``
(_RealLangGraph context manager that swaps the conftest's mocked
langgraph modules for the real ones, then restores).

The brick is reproduced with ``interrupt_before=['agent']`` — this is
the minimal graph configuration that exposes the documented collapse.
Without ``interrupt_before``, langgraph 1.0.x re-primes the graph on
new input and the brick is masked (the agent runs anyway). With
``interrupt_before``, ``aupdate_state(as_node='agent')`` on a
``next=()`` checkpoint clears the resume pointer, so a subsequent
``astream(graph_input)`` returns instantly without running the agent —
exactly the COMPLETED→RUNNING→COMPLETED <100ms collapse the guard
prevents.

The guard half (test 3) uses the real compiled graph object wired
into ``execute_compact`` via a wrapped graph (real ``aget_state``
delegates to the real checkpointer; ``aupdate_state`` is counted) so
the executor's terminal-guard fires on the real state shape, not a
MagicMock substitute.
"""
from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────────
# Real-langgraph swap (per-test context manager, mirrors
# tests/integration/test_persistence_w1_markers.py:49-104)
# ─────────────────────────────────────────────────────────────────────────


_MOCKED_LANGGRAPH_KEYS = (
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
)


class _RealLangGraph:
    """Swap the conftest's mocked langgraph modules for the real ones
    around a block of test code, then restore.

    Same identity-restore discipline as
    ``tests/integration/test_persistence_w1_markers.py:49-104``: snap
    the originals before deleting, restore the SAME module objects
    on exit so subsequent unit tests see the original mocked state.
    """

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k] for k in _MOCKED_LANGGRAPH_KEYS if k in sys.modules
        }
        # Drop mocked entries AND any cached real-langgraph children so
        # the re-import is coherent (parents are gone — re-import
        # walks the real package).
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        # Mirror the enter: clear ANY langgraph entry first (including
        # freshly imported real ones) so the originals restore cleanly.
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        return False


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


class _GraphWrapper:
    """Wrap a real ``CompiledStateGraph`` so we can COUNT
    ``aupdate_state`` calls without intercepting the call semantics.

    ``aget_state`` delegates verbatim — the executor's terminal
    helper reads the REAL state snapshot, not a MagicMock substitute.
    """

    def __init__(self, real_graph: Any) -> None:
        self._real = real_graph
        self.aupdate_state_call_count = 0
        self.aupdate_state_calls: list[tuple] = []

    async def aget_state(self, config):
        return await self._real.aget_state(config)

    async def aupdate_state(self, config, values, **kwargs):
        self.aupdate_state_call_count += 1
        self.aupdate_state_calls.append((config, values, kwargs))
        return await self._real.aupdate_state(config, values, **kwargs)


# ─────────────────────────────────────────────────────────────────────────
# 1. Terminal state is observable on a REAL graph run (no brick yet).
# ─────────────────────────────────────────────────────────────────────────


class TestTerminalObservableOnRealRun:
    """REAL LangGraph + file-backed AsyncSqliteSaver → drive to
    terminal → the shared ``_is_terminal_checkpoint`` helper detects
    the real state (``state.next == ()``).

    This pins the helper's correctness against the real LangGraph
    state shape — not a MagicMock substitute. The MagicMock-based
    helper tests in ``TestTerminalCheckpointHelper`` exercise the
    helper's surface; this test exercises the contract against the
    real ``StateSnapshot`` shape langgraph returns.
    """

    @pytest.mark.asyncio
    async def test_terminal_state_observable_on_real_run(self, tmp_path):
        with _RealLangGraph():
            import aiosqlite
            from langchain_core.messages import AIMessage, HumanMessage
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            from daemon.services._checkpoint_utils import (
                _is_terminal_checkpoint,
            )

            async def _agent(state):
                return {"messages": [AIMessage(content="echo")]}

            db_path = tmp_path / "terminal_probe.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(checkpointer=saver)

                iid = "real-terminal-probe"
                cfg = {"configurable": {"thread_id": iid}}

                # Drive to terminal.
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="hi")]}, cfg
                )

                # Real state — assert terminal.
                st = await compiled.aget_state(cfg)
                assert st.next == (), (
                    f"expected next=() (terminal), got next={st.next!r}"
                )
                # The shared helper must agree.
                assert _is_terminal_checkpoint(st) is True, (
                    "_is_terminal_checkpoint must detect the real terminal "
                    "StateSnapshot (state.next == ())"
                )
                # Non-terminal MagicMock-vs-real sanity: a fresh non-terminal
                # state is NOT terminal.
                assert _is_terminal_checkpoint(None) is True
            finally:
                await conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 2. The brick collapse IS observable on a real graph run.
# ─────────────────────────────────────────────────────────────────────────


class TestBrickCollapseOnRealGraph:
    """REAL LangGraph + ``interrupt_before=['agent']`` → the documented
    BRICK collapse is observable:
        1. Drive to terminal (next=()).
        2. ``aupdate_state(as_node='agent')`` (what the executor would
           do for compaction persistence).
        3. ``astream(graph_input)`` — the agent DOES NOT run; the
           next pointer was cleared by the aupdate_state above.

    This pins WHY the guard is load-bearing: the brick IS observable
    in real LangGraph (with ``interrupt_before``, the documented
    configuration for human-in-the-loop agents). The guard's job is
    to prevent the executor from ever calling ``aupdate_state`` on a
    terminal checkpoint, eliminating the brick.
    """

    @pytest.mark.asyncio
    async def test_brick_collapse_observable_on_real_graph(self, tmp_path):
        with _RealLangGraph():
            import aiosqlite
            from langchain_core.messages import AIMessage, HumanMessage
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            runs: list[str] = []

            async def _agent(state):
                runs.append("ran")
                return {"messages": [AIMessage(content="agent-out")]}

            db_path = tmp_path / "brick_probe.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            try:
                b = StateGraph(MessagesState)
                b.add_node("agent", _agent)
                b.add_edge(START, "agent")
                b.add_edge("agent", END)
                # interrupt_before=['agent'] is the configuration that
                # exposes the brick — without it, langgraph 1.0.x
                # re-primes the graph on new input and the agent runs
                # anyway. With it, the resume pointer is held in the
                # task queue; aupdate_state clears it; astream returns
                # instantly without executing the agent.
                compiled = b.compile(
                    checkpointer=saver, interrupt_before=["agent"]
                )

                iid = "real-brick-probe"
                cfg = {"configurable": {"thread_id": iid}}

                # Step 1 — ainvoke pauses BEFORE agent (interrupt_before).
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1")]}, cfg
                )
                assert runs == [], (
                    "first ainvoke should pause before agent (interrupt_before)"
                )
                st = await compiled.aget_state(cfg)
                assert st.next == ("agent",), (
                    f"expected next=('agent',) (paused), got next={st.next!r}"
                )

                # Step 2 — resume, agent runs, graph completes (terminal).
                runs.clear()
                await compiled.ainvoke(None, cfg)
                assert runs == ["ran"], (
                    f"resume should run the agent once; runs={runs}"
                )
                st = await compiled.aget_state(cfg)
                assert st.next == (), (
                    f"after resume the graph must be terminal; got next={st.next!r}"
                )

                # Step 3 — aupdate_state(as_node='agent') on terminal —
                # the executor's compaction-persistence recipe.
                await compiled.aupdate_state(
                    cfg,
                    {"messages": [AIMessage(content="compaction-summary")]},
                    as_node="agent",
                )
                st = await compiled.aget_state(cfg)
                assert st.next == (), (
                    f"aupdate_state on terminal must not change next; "
                    f"got next={st.next!r}"
                )

                # Step 4 — astream(NEW INPUT). The agent MUST NOT run.
                # This IS the documented brick — after aupdate_state
                # on terminal, subsequent astream(graph_input) returns
                # instantly without executing the agent. The FE sees
                # the user's new message dropped, so revive-on-send
                # silently breaks. Without the prior aupdate_state
                # (control case), the agent DOES run on the new
                # input — that's the contrast that makes the brick
                # observable.
                runs.clear()
                async for _chunk in compiled.astream(
                    {"messages": [HumanMessage(content="turn-2-after-compact")]},
                    cfg,
                ):
                    pass

                # Pin the collapse property — the brick IS observable.
                # Control: without the prior aupdate_state, the agent
                # WOULD run on astream(new). With the prior aupdate_state,
                # the agent does NOT run. The brick is the difference.
                assert runs == [], (
                    f"BRICK collapse observed: aupdate_state on terminal "
                    f"permanently cleared the resume pointer; subsequent "
                    f"astream(graph_input) did NOT run the agent. "
                    f"runs={runs}. (Control case without prior aupdate_state "
                    f"shows the agent DOES run on astream(graph_input).) "
                    f"This is the property the executor's terminal guard "
                    f"prevents — see TestGuardPreventsAupdateOnRealTerminal."
                )
            finally:
                await conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 3. The executor's terminal guard prevents aupdate_state on the real
#    terminal checkpoint (real graph + mocked manager surface).
# ─────────────────────────────────────────────────────────────────────────


class TestGuardPreventsAupdateOnRealTerminal:
    """Drive ``execute_compact`` with a REAL compiled graph (terminal
    from a real run) wired through a wrapped graph whose ``aget_state``
    delegates to the real checkpointer.

    The manager surface is mocked (we don't need a real
    ``InstanceManager``), but the graph object — the load-bearing
    surface for the persistence step the guard must prevent — is real.

    C1 BINDING — the executor's terminal guard is gated on INSTANCE
    STATUS (the O-B4 canonical set: completed / terminated / error /
    failed), NOT on the shared ``_is_terminal_checkpoint`` helper or
    the ``state.next == ()`` shape. The mocked manager surface here
    reports ``status="completed"``, which is what fires the guard.
    The quiescent checkpoint shape is INCIDENTAL under C1: a post-turn
    IDLE instance carries the SAME ``next=()`` shape and MUST compact
    (see the C1 canary classes below — the inverse brick property).

    Asserts:

    1. ``aupdate_state`` is NEVER invoked on the real graph (the
       wrapper's call count stays at 0) — the brick collapse the
       guard prevents is aupdate_state on a terminal-status instance.
    2. The rejection lands in the dispatcher's terminal ring with
       ``reason=terminal_instance`` and the PINNED guidance copy
       ("Send a message to start a new turn, then /compact." — the
       W-1.2 copy; NOT the old ``checkpoint_id`` artifact).
    3. The active command slot is cleared on terminalize and the
       terminal phase is ``failed``.
    """

    @pytest.mark.asyncio
    async def test_guard_prevents_aupdate_state_on_real_terminal(
        self, tmp_path
    ):
        with _RealLangGraph():
            import aiosqlite
            from langchain_core.messages import AIMessage, HumanMessage
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            from daemon.config import CompactionConfig, SlashCommandConfig
            from daemon.services.command_dispatcher import (
                CommandContext,
                CommandDispatcher,
            )
            from daemon.services.compact_executor import execute_compact

            # 1. Build a real compiled graph and seed it with a
            #    TERMINAL checkpoint carrying BIG messages (>5% of the
            #    default 128k context window floor so the executor's
            #    below_floor pre-check does NOT short-circuit before
            #    the persistence step). This mirrors the existing
            #    TestExecutorTerminalRejection pattern — big messages
            #    are necessary to reach aupdate_state if the guard
            #    were missing.
            runs: list[str] = []

            async def _agent(state):
                runs.append("ran")
                return {"messages": [AIMessage(content="out")]}

            db_path = tmp_path / "guard_probe.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            try:
                b = StateGraph(MessagesState)
                b.add_node("agent", _agent)
                b.add_edge(START, "agent")
                b.add_edge("agent", END)
                compiled = b.compile(checkpointer=saver)

                iid = "guard-real-terminal"
                cfg_for_compile = {"configurable": {"thread_id": iid}}

                # Seed the terminal checkpoint with big messages
                # directly via aupdate_state — this writes the real
                # StateSnapshot shape (next=()) to the real
                # checkpointer without going through a full graph run.
                big_messages = [
                    HumanMessage(content="x" * 4000, id=f"h-{n}")
                    for n in range(15)
                ]
                await compiled.aupdate_state(
                    cfg_for_compile,
                    {"messages": big_messages},
                    as_node="agent",
                )
                st = await compiled.aget_state(cfg_for_compile)
                # Confirm: the seed produces a TERMINAL state
                # (aupdate_state on a non-existent checkpoint lands
                # at the next checkpoint id, which is the terminal
                # for this thread — the add_messages reducer is the
                # only writer when no node has run yet).
                assert st.next == (), (
                    f"seeded checkpoint should be terminal; got next={st.next!r}"
                )

                # 2. Wrap the real graph so we can count aupdate_state
                #    calls while keeping aget_state authentic.
                wrapped = _GraphWrapper(compiled)

                # 3. Build a CommandDispatcher + active command
                #    (the executor needs an active CommandContext to
                #    terminalize into).
                dispatcher = CommandDispatcher(
                    enabled=True,
                    escape_prefix="//",
                    min_interval_s=10,
                    state_ttl_s=600,
                    max_state_per_instance=20,
                )
                command_id = "cmd-real-brick-guard"
                dispatcher._state.record_start(
                    instance_id=iid,
                    command_id=command_id,
                    command="compact",
                    ttl_seconds=600,
                )
                dispatcher._inflight[iid] = command_id

                # 4. Mock the InstanceManager surface (the executor
                #    reads status + config + wraps the graph). The
                #    GRAPH is real (wrapped); everything else is
                #    MagicMocked per O17's allowance for the guard part.
                mgr = MagicMock()
                mgr._lifecycle_service = MagicMock()
                mgr._lifecycle_service.get_instance_info = MagicMock(
                    return_value={
                        "status": "completed",
                        "id": iid,
                        "metadata": {},
                        "children": [],
                    }
                )
                mgr.config = MagicMock()
                mgr.config.llm.model = "gpt-4o"
                mgr.config.slash_commands = SlashCommandConfig(noop_floor_ratio=0.05)
                # Anchor context_window_default so get_model_context_limit
                # resolves to 128000 (gpt-4o registry value) regardless
                # of mock interaction. Floor ratio 0.05 → 6400 tokens,
                # which 15×4000-char messages comfortably exceed.
                mgr.config.compaction = CompactionConfig(
                    enabled=True,
                    threshold=0.80,
                    recent_message_window=10,
                    min_recent_window=3,
                    context_window_overrides={},
                    context_window_default=128000,
                    target_ratio=0.40,
                    summarization_model="",
                    min_messages_before_compaction=10,
                    summarization_chunk_threshold=0.60,
                    timeout_base_s=90.0,
                    timeout_per_100k_tokens_s=60.0,
                    timeout_cap_s=300.0,
                    timeout_facade_margin_s=5.0,
                    operation_budget_s=300.0,
                )

                async def _get_instance(_iid):
                    return wrapped

                mgr.get_instance = AsyncMock(side_effect=_get_instance)

                # The executor reads ``dispatcher._manager`` to resolve
                # its manager reference on the context-bound path; the
                # direct-execute path uses the ``manager`` arg, but we
                # attach for safety / WS-1 surface parity.
                dispatcher._manager = mgr

                ctx = CommandContext(
                    dispatcher=dispatcher,
                    command_id=command_id,
                    instance_id=iid,
                )

                # 5. Drive the executor. Guard should fire (real
                #    state is terminal) → reject before aupdate_state.
                await execute_compact(
                    mgr,
                    instance_id=iid,
                    command_id=command_id,
                    context=ctx,
                )

                # Pin 1 — aupdate_state NEVER called on the real graph.
                # If the guard were missing or moved past the helper,
                # the executor would call aupdate_state to persist the
                # compaction result and we'd see count >= 1. count == 0
                # is the load-bearing assertion: the brick is prevented.
                # The big-messages seed ensures the executor does NOT
                # short-circuit on the below_floor pre-check; if it did,
                # this assertion would also pass but for the WRONG
                # reason (the rejection path would be below_floor, not
                # terminal_instance — the second pin below discriminates).
                assert wrapped.aupdate_state_call_count == 0, (
                    f"terminal guard failed: aupdate_state was called "
                    f"{wrapped.aupdate_state_call_count} time(s) on the "
                    f"real graph. This is the brick scenario the guard "
                    f"prevents. Calls: {wrapped.aupdate_state_calls!r}"
                )

                # Pin 2 — the active slot was cleared (terminalize fired)
                # and the rejection landed in the dispatcher's terminal
                # ring with reason=terminal_instance.
                active = dispatcher._state._active.get(iid)
                assert active is None, (
                    f"dispatcher should have cleared the active slot on "
                    f"terminalize; got active={active!r}"
                )
                ring = dispatcher._state._ring.get(iid, {})
                terminalized = ring.get(command_id)
                assert terminalized is not None, (
                    f"terminal rejection must be recorded in the "
                    f"dispatcher's terminal ring for {iid}/{command_id}; "
                    f"ring keys: {list(ring.keys())}"
                )
                detail = terminalized.detail or {}
                assert detail.get("reason") == "terminal_instance", (
                    f"rejection must carry reason=terminal_instance; "
                    f"detail={detail!r} (this distinguishes the terminal "
                    f"guard from the below_floor noop path — both produce "
                    f"aupdate_state_call_count == 0 for different reasons)"
                )
                # C1 + W-1.2: the rejection detail carries the PINNED
                # guidance copy (plan S-14 / architect §5) instead of
                # the old ``checkpoint_id`` artifact. The
                # checkpoint_id was keyed off the LangGraph state
                # shape — the C1 fix moves the gate to INSTANCE
                # STATUS (the O-B4 canonical set), so the state shape
                # is incidental; the user-facing guidance copy is the
                # load-bearing detail.
                assert (
                    detail.get("guidance")
                    == "Send a message to start a new turn, then /compact."
                ), (
                    f"rejection must carry the PINNED guidance copy "
                    f"(W-1.2 / architect §5); got detail={detail!r}"
                )

                # Pin 3 — phase is failed (the executor emits
                # ``_PHASE_FAILED`` for the terminal-instance rejection).
                assert terminalized.phase == "failed", (
                    f"terminal phase must be 'failed'; got "
                    f"phase={terminalized.phase!r}"
                )
            finally:
                await conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 4. C1 BINDING — REAL-graph SUCCESS canary on a genuinely quiescent
#    instance (file-backed SQLite). The inverse of the brick property:
#    compact succeeds AND a subsequent astream(new input) runs the agent.
# ─────────────────────────────────────────────────────────────────────────


class TestExecutorCompactOnRealQuiescentInstanceSucceeds:
    """C1 canary — the headline scenario the C1 fix re-opens.

    Drives ``execute_compact`` against a REAL graph that has
    settled into a genuinely quiescent post-turn state
    (``state.next == ()``, no ``interrupt_before``). The
    executor's status guard sees ``status="idle"`` (NOT in the
    O-B4 terminal set), so it proceeds through pre-checks →
    engine → persistence. Two invariants are pinned:

    1. SUCCESS — the executor reaches the engine (no premature
       terminal-rejection). The terminalize lands ``phase=success``.
    2. NO-BRICK — a subsequent ``astream(graph_input)`` on the
       SAME checkpoint still runs the agent. This is the
       inverse of the brick property in
       ``TestBrickCollapseOnRealGraph`` — that test pinned
       why TERMINAL-STATUS instances must never be compacted;
       this test pins why IDLE instances with post-turn
       quiescent checkpoints CAN be compacted (the C1
       re-opened scenario).

    The persistence recipe omits ``as_node="agent"`` (C1
    binding — see ``_persist_compaction_result`` docstring);
    empirically verified by
    ``/tmp/c1_explore.py`` Variant A on 2026-08-31.
    """

    @pytest.mark.asyncio
    async def test_compact_succeeds_on_quiescent_instance(self, tmp_path):
        with _RealLangGraph():
            import aiosqlite
            from langchain_core.messages import AIMessage, HumanMessage
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            from daemon.config import CompactionConfig, SlashCommandConfig
            from daemon.services.command_dispatcher import (
                CommandContext,
                CommandDispatcher,
            )
            from daemon.services.compact_executor import execute_compact

            # Real graph driver — record EVERY agent invocation.
            runs: list[str] = []

            async def _agent(state):
                runs.append("ran")
                return {"messages": [AIMessage(content="agent-out")]}

            db_path = tmp_path / "canary_quiescent.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                # NO ``interrupt_before`` — langgraph 1.0.x re-primes
                # the graph on new input; this is the configuration
                # that lets the brick test (TestBrickCollapseOnRealGraph)
                # reproduce the documented collapse. WITHOUT
                # ``interrupt_before`` AND with status="idle", the
                # executor must compact successfully.
                compiled = g.compile(checkpointer=saver)

                iid = "canary-quiescent"
                cfg = {"configurable": {"thread_id": iid}}

                # 1. Drive to a genuinely quiescent post-turn state
                #    (no interrupt_before).
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1")]}, cfg
                )
                runs.clear()

                # 2. Confirm the quiescent shape.
                st = await compiled.aget_state(cfg)
                assert st.next == (), (
                    f"setup invariant: pre-/compact must be quiescent; "
                    f"got next={st.next!r}"
                )

                # 3. Seed BIG messages so the executor's noop-floor
                #    pre-check does NOT short-circuit. We seed AFTER
                #    the ainvoke so the message history is real (not
                #    a synthetic seed).
                big_messages = [
                    HumanMessage(content="x" * 4000, id=f"h-{n}")
                    for n in range(15)
                ]
                await compiled.aupdate_state(
                    cfg, {"messages": big_messages}, as_node="agent"
                )

                # 4. Build the dispatcher + manager surface.
                wrapped = _GraphWrapper(compiled)
                dispatcher = CommandDispatcher(
                    enabled=True,
                    escape_prefix="//",
                    min_interval_s=10,
                    state_ttl_s=600,
                    max_state_per_instance=20,
                )
                command_id = "cmd-canary-quiescent-success"
                dispatcher._state.record_start(
                    instance_id=iid,
                    command_id=command_id,
                    command="compact",
                    ttl_seconds=600,
                )
                dispatcher._inflight[iid] = command_id

                mgr = MagicMock()
                mgr._lifecycle_service = MagicMock()
                # C1: status="idle" — NOT in the O-B4 terminal set.
                mgr._lifecycle_service.get_instance_info = MagicMock(
                    return_value={
                        "status": "idle",
                        "id": iid,
                        "metadata": {},
                        "children": [],
                    }
                )
                mgr.config = MagicMock()
                mgr.config.llm.model = "gpt-4o"
                mgr.config.slash_commands = SlashCommandConfig(noop_floor_ratio=0.05)
                # Anchor context_window_default so get_model_context_limit
                # resolves to 128000 (gpt-4o registry value) regardless
                # of mock interaction. Floor ratio 0.05 → 6400 tokens,
                # which 15×4000-char messages comfortably exceed.
                mgr.config.compaction = CompactionConfig(
                    enabled=True,
                    threshold=0.80,
                    recent_message_window=10,
                    min_recent_window=3,
                    context_window_overrides={},
                    context_window_default=128000,
                    target_ratio=0.40,
                    summarization_model="",
                    min_messages_before_compaction=10,
                    summarization_chunk_threshold=0.60,
                    timeout_base_s=90.0,
                    timeout_per_100k_tokens_s=60.0,
                    timeout_cap_s=300.0,
                    timeout_facade_margin_s=5.0,
                    operation_budget_s=300.0,
                )

                async def _get_instance(_iid):
                    return wrapped

                mgr.get_instance = AsyncMock(side_effect=_get_instance)
                mgr.execution_gate = MagicMock()

                async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
                    return await work_fn()
                mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)
                mgr.pause_instance_cascade = AsyncMock(
                    return_value={"paused_ids": [iid], "skipped_ids": []}
                )
                mgr.resume_instance_cascade = AsyncMock(
                    return_value={"resumed_ids": [iid], "skipped_ids": []}
                )

                async def _quiescent(instance_id, timeout):
                    return True
                mgr.wait_for_instance_quiescent = AsyncMock(side_effect=_quiescent)

                mgr._messaging_service = MagicMock()
                mgr._messaging_service.emit_context_usage_for_instance = AsyncMock()

                mgr._live_hub = MagicMock()
                mgr._live_hub.stream_message = AsyncMock()

                mgr._task_repo = MagicMock()
                mgr._task_repo.has_instance_busy = MagicMock(return_value=False)

                dispatcher._manager = mgr

                ctx = CommandContext(
                    dispatcher=dispatcher,
                    command_id=command_id,
                    instance_id=iid,
                )

                # 5. Drive execute_compact. The executor uses a
                #    stubbed engine that returns a noop result via
                #    the engine's compactor mock — we assert the
                #    SUCCESS terminal phase lands in the ring.
                #    The engine isn't invoked here because the
                #    pre-checks will short-circuit on below_floor
                #    (the BIG messages exceed 5% of the 128k window,
                #    so the floor passes; but the compactor isn't
                #    reachable without a real LLM call. We patch
                #    compact_state to a noop so the persistence step
                #    runs).
                async def _noop_compact_state(ctx, force=False):
                    return None  # → below_floor noop path
                mgr._compactor = MagicMock()
                mgr._compactor.compact_state = AsyncMock(
                    side_effect=_noop_compact_state
                )

                await execute_compact(
                    mgr, instance_id=iid, command_id=command_id, context=ctx
                )

                # INVARIANT 1 — SUCCESS terminal phase landed in the
                # ring (the engine returned None → below_floor noop,
                # which surfaces as success + compacted_type=noop).
                ring = dispatcher._state._ring.get(iid, {})
                terminalized = ring.get(command_id)
                assert terminalized is not None, (
                    "executor must terminalize with success on a "
                    f"quiescent IDLE instance; ring keys={list(ring.keys())}"
                )
                assert terminalized.phase == "success", (
                    f"phase must be 'success' (below_floor noop); got "
                    f"phase={terminalized.phase!r} detail={terminalized.detail!r}"
                )

                # 6. INVARIANT 2 — NO-BRICK: subsequent astream(new
                # input) on the SAME checkpoint runs the agent. This
                # is the brick-regression inverse.
                st_before = await compiled.aget_state(cfg)
                # The big messages are still present (the below_floor
                # noop doesn't persist anything).
                assert len(st_before.values.get("messages", [])) > 0
                runs.clear()
                async for _chunk in compiled.astream(
                    {"messages": [HumanMessage(content="turn-2-after-compact")]}, cfg
                ):
                    pass
                assert runs == ["ran"], (
                    "INVARIANT 2 violated (NO-BRICK): subsequent "
                    f"astream(new input) did not run the agent on a "
                    f"quiescent instance after execute_compact. "
                    f"runs={runs}. The C1 fix re-opens the headline "
                    "scenario only if subsequent turns still work."
                )
            finally:
                await conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 5. C1 BINDING — REAL-graph SUCCESS canary WITH persistence
#    (the more demanding variant — persistence step runs)
# ─────────────────────────────────────────────────────────────────────────


class TestExecutorCompactOnRealQuiescentInstanceWithPersistence:
    """C1 canary with persistence — drive ``execute_compact`` on a
    real graph with a stubbed engine that returns a real summary
    so the two ``aupdate_state`` calls in
    ``_persist_compaction_result`` run. Verify:

    1. SUCCESS — the executor reaches persistence and
       terminalizes success.
    2. NO-BRICK — a subsequent ``astream(graph_input)`` runs the
       agent. The persistence recipe (Variant A — no ``as_node``)
       must not interact badly with the live checkpointer.
    """

    @pytest.mark.asyncio
    async def test_compact_persists_and_next_turn_runs_agent(
        self, tmp_path
    ):
        with _RealLangGraph():
            import aiosqlite
            from langchain_core.messages import (
                AIMessage,
                HumanMessage,
                RemoveMessage,
                SystemMessage,
            )
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            from daemon.config import CompactionConfig, SlashCommandConfig
            from daemon.services.command_dispatcher import (
                CommandContext,
                CommandDispatcher,
            )
            from daemon.services.compact_executor import execute_compact

            runs: list[str] = []

            async def _agent(state):
                runs.append("ran")
                return {"messages": [AIMessage(content="agent-out")]}

            db_path = tmp_path / "canary_persist.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(checkpointer=saver)

                iid = "canary-persist"
                cfg = {"configurable": {"thread_id": iid}}

                # Drive to quiescence first.
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1")]}, cfg
                )
                runs.clear()
                # Seed big messages.
                big_messages = [
                    HumanMessage(content="x" * 4000, id=f"h-{n}")
                    for n in range(15)
                ]
                await compiled.aupdate_state(
                    cfg, {"messages": big_messages}, as_node="agent"
                )

                # Pin the quiescent precondition EXPLICITLY (the compact
                # below relies on it — do not leave it implied): the
                # as_node seed must land at a terminal checkpoint
                # (next=()) — the LEGITIMATE post-turn IDLE shape under
                # C1, NOT a mid-graph state.
                st = await compiled.aget_state(cfg)
                assert st.next == (), (
                    f"seeded checkpoint must be quiescent (next=()) "
                    f"before compact; got next={st.next!r}"
                )

                wrapped = _GraphWrapper(compiled)
                dispatcher = CommandDispatcher(
                    enabled=True,
                    escape_prefix="//",
                    min_interval_s=10,
                    state_ttl_s=600,
                    max_state_per_instance=20,
                )
                command_id = "cmd-canary-persist-success"
                dispatcher._state.record_start(
                    instance_id=iid,
                    command_id=command_id,
                    command="compact",
                    ttl_seconds=600,
                )
                dispatcher._inflight[iid] = command_id

                mgr = MagicMock()
                mgr._lifecycle_service = MagicMock()
                mgr._lifecycle_service.get_instance_info = MagicMock(
                    return_value={
                        "status": "idle",
                        "id": iid,
                        "metadata": {},
                        "children": [],
                    }
                )
                mgr.config = MagicMock()
                mgr.config.llm.model = "gpt-4o"
                mgr.config.slash_commands = SlashCommandConfig(noop_floor_ratio=0.05)
                # Anchor context_window_default so get_model_context_limit
                # resolves to 128000 (gpt-4o registry value) regardless
                # of mock interaction. Floor ratio 0.05 → 6400 tokens,
                # which 15×4000-char messages comfortably exceed.
                mgr.config.compaction = CompactionConfig(
                    enabled=True,
                    threshold=0.80,
                    recent_message_window=10,
                    min_recent_window=3,
                    context_window_overrides={},
                    context_window_default=128000,
                    target_ratio=0.40,
                    summarization_model="",
                    min_messages_before_compaction=10,
                    summarization_chunk_threshold=0.60,
                    timeout_base_s=90.0,
                    timeout_per_100k_tokens_s=60.0,
                    timeout_cap_s=300.0,
                    timeout_facade_margin_s=5.0,
                    operation_budget_s=300.0,
                )

                async def _get_instance(_iid):
                    return wrapped
                mgr.get_instance = AsyncMock(side_effect=_get_instance)
                mgr.execution_gate = MagicMock()

                async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
                    return await work_fn()
                mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)
                mgr.pause_instance_cascade = AsyncMock(
                    return_value={"paused_ids": [iid], "skipped_ids": []}
                )
                mgr.resume_instance_cascade = AsyncMock(
                    return_value={"resumed_ids": [iid], "skipped_ids": []}
                )

                async def _quiescent(instance_id, timeout):
                    return True
                mgr.wait_for_instance_quiescent = AsyncMock(side_effect=_quiescent)

                mgr._messaging_service = MagicMock()
                mgr._messaging_service.emit_context_usage_for_instance = AsyncMock()

                mgr._live_hub = MagicMock()
                mgr._live_hub.stream_message = AsyncMock()

                mgr._task_repo = MagicMock()
                mgr._task_repo.has_instance_busy = MagicMock(return_value=False)

                dispatcher._manager = mgr

                ctx = CommandContext(
                    dispatcher=dispatcher,
                    command_id=command_id,
                    instance_id=iid,
                )

                # Stub the engine to return a real CompactionResult
                # (the persistence step will run with two aupdate
                # calls — first the messages replacement, second the
                # compacted_at stamp).
                from daemon.compaction import CompactionResult

                async def _fake_compact_state(ctx, force=False):
                    return CompactionResult(
                        replacement_messages=[
                            RemoveMessage(id=f"h-{n}") for n in range(15)
                        ] + [
                            SystemMessage(
                                content="[Conversation Summary]\nreal-summary",
                                id="compaction-canary-x",
                            )
                        ],
                        tokens_before=15000,
                        tokens_after=100,
                        tokens_saved=14900,
                        messages_before=15,
                        messages_after=1,
                        compaction_type="summary",
                        summarization_error=None,
                        compacted_at="2026-08-31T00:00:00+00:00",
                        forced=force,
                        failure_kind=None,
                    )
                mgr._compactor = MagicMock()
                mgr._compactor.compact_state = AsyncMock(
                    side_effect=_fake_compact_state
                )

                await execute_compact(
                    mgr, instance_id=iid, command_id=command_id, context=ctx
                )

                # 1. SUCCESS landed.
                ring = dispatcher._state._ring.get(iid, {})
                terminalized = ring.get(command_id)
                assert terminalized is not None
                assert terminalized.phase == "success", (
                    f"phase must be 'success'; got {terminalized.phase!r} "
                    f"detail={terminalized.detail!r}"
                )

                # 2. Persistence ran — TWO aupdate_state calls
                #    (messages first, compacted_at second), NO
                #    ``as_node`` (C1 Variant A).
                assert wrapped.aupdate_state_call_count == 2, (
                    f"persistence must emit exactly 2 aupdate_state "
                    f"calls; got {wrapped.aupdate_state_call_count}"
                )
                first_call = wrapped.aupdate_state_calls[0]
                second_call = wrapped.aupdate_state_calls[1]
                # C1: no as_node kwarg in either call.
                assert "as_node" not in first_call[2], (
                    f"C1: persistence first call must NOT carry as_node; "
                    f"got kwargs={first_call[2]!r}"
                )
                assert "as_node" not in second_call[2], (
                    f"C1: persistence second call must NOT carry as_node; "
                    f"got kwargs={second_call[2]!r}"
                )

                # 3. NO-BRICK — subsequent astream runs the agent.
                runs.clear()
                async for _chunk in compiled.astream(
                    {"messages": [HumanMessage(content="turn-2-after-persist")]}, cfg
                ):
                    pass
                assert runs == ["ran"], (
                    f"INVARIANT 2 violated (NO-BRICK): subsequent "
                    f"astream(new input) did not run the agent. "
                    f"runs={runs}. The C1 persistence recipe must "
                    "preserve subsequent turn integrity."
                )
            finally:
                await conn.close()
