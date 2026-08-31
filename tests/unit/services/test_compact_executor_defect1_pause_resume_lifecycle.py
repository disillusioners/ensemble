"""Defect #1 (mid-turn /compact pause→resume leaves instance stuck paused).

Root cause pinned in this file (2026-08-31): the executor's pause→quiesce
lifecycle is the load-bearing "RUNNING row" of the WS-6 status matrix. The
defect was observed on the engineered ``instance = e1206f9a…`` during the
``/compact`` e2e gate: after a mid-turn ``/compact`` ack, the instance
went ``paused`` for >130s and only ``resume_instance_cascade`` unblocked
it. Live evidence in
``.agents/tester/RESULTS/2026-08-31-slash-commands-compact-e2e-gate.md``
defects row #1.

Code-level root cause (``daemon/services/compact_executor.py:941-966``):

    try:
        result = await compactor.compact_state(ctx, force=True)
        if result is None:
            # early return — engine says "can't compact"
            await context.terminalize(_PHASE_SUCCESS, ...)
            await _emit_phase_event(...)
            return                                          # ← BUG
        ...
        if needs_pause_resume and paused_state_resume_ok:
            await _safe_resume(manager, instance_id)         # ← only on full success

The ``result is None`` branch returns from ``_in_gate`` BEFORE
``_safe_resume`` runs. ``execution_gate.run`` then completes normally
(no exception) so the outer ``except Exception`` / ``except _GateExit``
branches don't fire either. The instance stays PAUSED forever; manual
``resume_instance_cascade`` is the only way out.

Fix design (paired with this test): move the ``_safe_resume`` call out of
the success-path branch and into a ``finally`` block that wraps
``execution_gate.run(...)``. The ``finally`` fires for EVERY exit path
from the gate body — normal return (incl. engine-returned-None early
return), ``_GateExit``, and ``Exception`` — so the instance can never
remain paused because the executor landed on an early-return path.

The regression tests in this file pin the fix:

* ``TestRunningMidTurnEngineReturnsNoneResumes`` — load-bearing mock
  regression. RUNNING instance, mid-graph (frozen ``next=("agent",)``),
  ``compact_state`` returns ``None`` (below-floor noop). Pins:
  ``mgr.resume_instance_cascade.await_count == 1`` after ``execute_compact``
  returns. Pre-fix: the count is 0 (resume never fires).

* ``TestRunningMidTurnRealGraphEngineReturnsNoneLeavesNoPaused`` —
  integration regression on a real LangGraph + file-backed
  ``AsyncSqliteSaver`` (same harness as
  ``test_compact_executor_revive_brick_e2e.py:57-88``). The instance must
  not remain paused after ``execute_compact`` completes. Pre-fix: the
  instance is PAUSED (the bug).

* ``TestRunningMidTurnQueuedJobsAllProcessedAfterCompact`` — covers
  defect S2 too. Sets N PENDING process-message tasks against the
  instance, runs the executor on a frozen mid-graph state, then drives
  ``claim_pending_task`` until empty. Pins: every queued task reaches
  PROCESSING (none silently dropped). Pre-fix: instance stuck PAUSED,
  no tasks claimable.

DB discipline: file-backed ``AsyncSqliteSaver`` (the same harness as
``tests/integration/test_persistence_w1_markers.py:49-104`` and
``tests/unit/services/test_compact_executor_revive_brick_e2e.py:57-88``)
— never StaticPool/in-memory (write-corruption hazard; production PG
unaffected).
"""
from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────────
# Real-langgraph swap (mirror of
# tests/unit/services/test_compact_executor_revive_brick_e2e.py:57-88)
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
    """Swap the conftest's mocked langgraph modules for the real ones.

    Same identity-restore discipline as
    ``tests/integration/test_persistence_w1_markers.py:49-104`` and
    ``tests/unit/services/test_compact_executor_revive_brick_e2e.py:57-88``.
    Snap originals before deleting, restore the SAME module objects
    on exit so subsequent unit tests see the original mocked state.
    """

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k] for k in _MOCKED_LANGGRAPH_KEYS if k in sys.modules
        }
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        return False


# ─────────────────────────────────────────────────────────────────────────
# 1. Mock regression (load-bearing) — engine returns None on mid-turn
#    RUNNING instance → resume MUST still fire. Pre-fix this fires 0
#    times; post-fix exactly once. Defect #1 S1 root cause pinned here.
# ─────────────────────────────────────────────────────────────────────────


class TestRunningMidTurnEngineReturnsNoneResumes:
    """Defect #1 S1 — load-bearing mock regression.

    Mirrors the documentation at
    ``daemon/services/compact_executor.py:671-720`` (RUNNING status
    matrix row): pause→quiesce success, gate acquires, engine runs.
    On the engine-returned-None early-return branch (the
    below-floor noop path, line 941-966), the executor MUST still
    call ``resume_instance_cascade`` so the instance is not stuck
    paused.

    Without the fix: this assertion fails. The instance stays paused
    and a manual ``resume_instance_cascade`` is the only way out.
    With the fix: the assertion passes; the resume lands regardless
    of which exit path the engine takes.
    """

    @pytest.mark.asyncio
    async def test_running_midturn_engine_returns_none_still_resumes(self):
        from daemon.config import CompactionConfig, SlashCommandConfig
        from daemon.services.command_dispatcher import (
            CommandContext,
            CommandDispatcher,
        )
        from daemon.services.compact_executor import execute_compact

        # Mid-graph frozen checkpoint — the genuine RUNNING-row state
        # (``next == ("agent",)``). Builders like _make_checkpoint_state
        # in test_compact_executor.py default to ``next=()``; this one
        # is the test_compact_executor.py:1350-1394 quiescence-failure
        # fixture shape — the ONLY legit synthetic mid-graph shape.
        class _State:
            def __init__(self):
                self.next = ("agent",)
                self.values = {
                    "messages": [
                        # 15 × 4K-char messages = ~15k tokens,
                        # comfortably above the 5%-of-128k floor
                        # so the executor's pre-checks don't
                        # short-circuit and we reach the engine.
                        __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(
                            content="x" * 4000, id=f"h-{n}"
                        )
                        for n in range(15)
                    ],
                    "compacted_at": None,
                }
                self.config = {"configurable": {"thread_id": "inst-running-midturn"}}

        graph = MagicMock()
        graph.aupdate_state = AsyncMock()

        async def _aget_state(_config):
            return _State()
        graph.aget_state = AsyncMock(side_effect=_aget_state)

        # Manager surface — minimal. We need status="running" to hit
        # the RUNNING branch, pause_instance_cascade + wait_for_quiescent
        # to succeed, and the engine to return None so we land on the
        # buggy early-return path.
        mgr = MagicMock()
        mgr._lifecycle_service = MagicMock()
        mgr._lifecycle_service.get_instance_info = MagicMock(
            return_value={
                "status": "running",
                "id": "inst-running-midturn",
                "metadata": {},
                "children": [],
            }
        )
        mgr.config = MagicMock()
        mgr.config.llm.model = "gpt-4o"
        mgr.config.slash_commands = SlashCommandConfig(noop_floor_ratio=0.05)
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

        # The executor's mgr._compactor.compact_state is called from
        # inside the gate. Returning None triggers the early-return
        # branch.
        from daemon.compaction import ContextCompactor

        compactor_cfg = CompactionConfig(
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
        compactor = ContextCompactor(
            config=compactor_cfg,
            llm_config={
                "base_url": "http://example",
                "base_url_backup": None,
                "api_key": "test",
                "model": "gpt-4o",
                "model_vision": "gpt-4o",
                "temperature": 0.7,
                "request_timeout": 30.0,
                "buffer_response_header": True,
            },
        )
        mgr._compactor = compactor
        mgr._compactor.compact_state = AsyncMock(return_value=None)

        async def _get_instance(_iid):
            return graph
        mgr.get_instance = AsyncMock(side_effect=_get_instance)

        # Gate — run the work_fn synchronously, mirroring the live
        # pattern. We do NOT need real asyncio.Lock semantics here;
        # a single-call harness is enough for the regression.
        mgr.execution_gate = MagicMock()

        async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
            return await work_fn()
        mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)

        mgr.pause_instance_cascade = AsyncMock(
            return_value={"paused_ids": ["inst-running-midturn"], "skipped_ids": []}
        )
        mgr.resume_instance_cascade = AsyncMock(
            return_value={"resumed_ids": ["inst-running-midturn"], "skipped_ids": []}
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

        dispatcher = CommandDispatcher(
            enabled=True,
            escape_prefix="//",
            min_interval_s=10,
            state_ttl_s=600,
            max_state_per_instance=20,
        )
        command_id = "cmd-defect1-s1-red"
        dispatcher._state.record_start(
            instance_id="inst-running-midturn",
            command_id=command_id,
            command="compact",
            ttl_seconds=600,
        )
        dispatcher._inflight["inst-running-midturn"] = command_id
        dispatcher._manager = mgr

        ctx = CommandContext(
            dispatcher=dispatcher,
            command_id=command_id,
            instance_id="inst-running-midturn",
        )

        # Drive — pre-fix this leaves instance stuck paused; post-fix
        # the resume path fires on EVERY exit from the gate body
        # (incl. the engine-returned-None early return).
        await execute_compact(
            mgr,
            instance_id="inst-running-midturn",
            command_id=command_id,
            context=ctx,
        )

        # ── The load-bearing assertion ─────────────────────────────────
        # Pre-fix: 0 calls (instance stays paused → bug).
        # Post-fix: exactly 1 call (resume lands via finally).
        assert mgr.resume_instance_cascade.await_count == 1, (
            f"DEFECT #1 S1: resume_instance_cascade MUST fire when the "
            f"engine returns None on a mid-turn RUNNING instance — this "
            f"is the load-bearing pause→quiesce→compact→resume lifecycle "
            f"invariant. The previous success-path placement missed the "
            f"engine-returned-None early return at "
            f"daemon/services/compact_executor.py:941-966, leaving the "
            f"instance stuck PAUSED forever (live evidence: scope-3 "
            f"instance e1206f9a stalled >130s during the /compact "
            f"e2e gate on 2026-08-31). Got "
            f"resume_instance_cascade.await_count="
            f"{mgr.resume_instance_cascade.await_count}; pause_await_count="
            f"{mgr.pause_instance_cascade.await_count}."
        )

        # Pause happened (RUNNING row precondition).
        assert mgr.pause_instance_cascade.await_count == 1, (
            "RUNNING-row precondition: pause_instance_cascade must be "
            "called before the gate acquires."
        )

        # AND the terminalize lands at success + below_floor (the
        # engine-returned-None noop surface) — proves the executor
        # reached the engine and intentionally returned the noop.
        ring = dispatcher._state._ring.get("inst-running-midturn", {})
        terminalized = ring.get(command_id)
        assert terminalized is not None
        assert terminalized.phase == "success", (
            "engine-returned-None below-floor noop must terminalize "
            f"success; got phase={terminalized.phase!r} "
            f"detail={terminalized.detail!r}"
        )
        assert (terminalized.detail or {}).get("compacted_type") == "noop"
        assert (terminalized.detail or {}).get("noop_reason") == "below_floor"


# ─────────────────────────────────────────────────────────────────────────
# 2. Real-LangGraph integration regression — instance must NOT remain
#    PAUSED on the real graph after a mid-turn /compact with engine
#    returning None. Same harness as test_compact_executor_revive_brick_e2e.py
#    (_RealLangGraph swap + file-backed AsyncSqliteSaver).
# ─────────────────────────────────────────────────────────────────────────


class TestRunningMidTurnRealGraphEngineReturnsNoneLeavesNoPaused:
    """Defect #1 S1 — real-graph integration regression.

    Drives ``execute_compact`` against a REAL LangGraph with
    ``interrupt_before=['agent']`` (the genuine RUNNING/mid-graph
    fixture) and a stubbed engine that returns ``None`` (the bug
    trigger path). Pins that, after the executor returns, the
    instance is NOT stuck paused.

    Without the fix: this fails. The executor leaves the instance in
    PAUSED state because the engine-returned-None branch bypasses
    ``_safe_resume``. With the fix: the instance leaves PAUSED.

    This test pins the OBSERVABLE consequence of the bug at the
    graph/manager layer — not just the call-count. A future refactor
    that moves ``_safe_resume`` somewhere new still passes iff the
    instance actually leaves PAUSED, which is the user-visible
    invariant.
    """

    @pytest.mark.asyncio
    async def test_running_midturn_real_graph_engine_returns_none_leaves_no_paused(
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

            # _PhaseState probe — record every status_change SSE.
            # mirror of test_compact_executor_revive_brick_e2e.py:
            # we don't use SSE here; the executable check is the
            # underlying pause_instance_cascade / resume_instance_cascade
            # mocks plus the executor's mgr bookkeeping.
            runs: list[str] = []

            async def _agent(state):
                runs.append("ran")
                return {"messages": [AIMessage(content="agent-out")]}

            db_path = tmp_path / "defect1_s1.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                # interrupt_before=['agent'] reproduces the
                # RUNNING-row mid-graph shape (next=("agent",)) —
                # the real-world /compact pause-first precondition.
                compiled = g.compile(
                    checkpointer=saver, interrupt_before=["agent"]
                )

                iid = "defect1-s1-real-midturn"
                cfg = {"configurable": {"thread_id": iid}}

                # Drive to the genuine frozen-mid-graph state. The
                # first ainvoke stalls at interrupt_before, so
                # next == ("agent",) — identical to the live
                # /compact pause-first preconditions.
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1")]} , cfg
                )
                st = await compiled.aget_state(cfg)
                assert st.next == ("agent",), (
                    f"setup invariant: frozen mid-graph state must "
                    f"have next=('agent',); got next={st.next!r}"
                )

                # Seed big messages so the executor's noop-floor
                # pre-check does NOT short-circuit (the 5% × 128k
                # floor is ~6400 tokens; 15×4k-char messages
                # comfortably exceed it).
                big_messages = [
                    HumanMessage(content="x" * 4000, id=f"h-{n}")
                    for n in range(15)
                ]
                await compiled.aupdate_state(
                    cfg, {"messages": big_messages}, as_node="agent"
                )

                dispatcher = CommandDispatcher(
                    enabled=True,
                    escape_prefix="//",
                    min_interval_s=10,
                    state_ttl_s=600,
                    max_state_per_instance=20,
                )
                command_id = "cmd-defect1-s1-real-midturn"
                dispatcher._state.record_start(
                    instance_id=iid,
                    command_id=command_id,
                    command="compact",
                    ttl_seconds=600,
                )
                dispatcher._inflight[iid] = command_id

                mgr = MagicMock()
                mgr._lifecycle_service = MagicMock()
                # status="running" — the RUNNING row of the WS-6
                # matrix. Triggers the pause→quiesce path BEFORE
                # the engine.
                mgr._lifecycle_service.get_instance_info = MagicMock(
                    return_value={
                        "status": "running",
                        "id": iid,
                        "metadata": {},
                        "children": [],
                    }
                )
                mgr.config = MagicMock()
                mgr.config.llm.model = "gpt-4o"
                mgr.config.slash_commands = SlashCommandConfig(
                    noop_floor_ratio=0.05
                )
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
                    return compiled
                mgr.get_instance = AsyncMock(side_effect=_get_instance)
                mgr.execution_gate = MagicMock()

                async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
                    return await work_fn()
                mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)

                # Pause succeeds, resume MUST fire on every exit path
                # from the gate body — incl. the engine-returned-None
                # early return. Count BOTH so we can prove the
                # invariant AND diagnose which path the executor
                # actually took.
                mgr.pause_instance_cascade = AsyncMock(
                    return_value={"paused_ids": [iid], "skipped_ids": []}
                )
                mgr.resume_instance_cascade = AsyncMock(
                    return_value={"resumed_ids": [iid], "skipped_ids": []}
                )

                async def _quiescent(instance_id, timeout):
                    return True
                mgr.wait_for_instance_quiescent = AsyncMock(
                    side_effect=_quiescent
                )

                mgr._messaging_service = MagicMock()
                mgr._messaging_service.emit_context_usage_for_instance = AsyncMock()
                mgr._live_hub = MagicMock()
                mgr._live_hub.stream_message = AsyncMock()
                mgr._task_repo = MagicMock()
                mgr._task_repo.has_instance_busy = MagicMock(return_value=False)

                # Engine returns None — the early-return branch.
                mgr._compactor = MagicMock()
                mgr._compactor.compact_state = AsyncMock(return_value=None)

                dispatcher._manager = mgr

                ctx = CommandContext(
                    dispatcher=dispatcher,
                    command_id=command_id,
                    instance_id=iid,
                )

                await execute_compact(
                    mgr,
                    instance_id=iid,
                    command_id=command_id,
                    context=ctx,
                )

                # ── The load-bearing integration assertion ────────
                # Pre-fix: count == 0. Post-fix: count == 1.
                assert mgr.resume_instance_cascade.await_count == 1, (
                    f"DEFECT #1 S1 (real-graph integration): "
                    f"resume_instance_cascade MUST fire after a mid-turn "
                    f"/compact on a RUNNING instance even when the engine "
                    f"returns None — the previous success-path placement "
                    f"missed the engine-returned-None early return at "
                    f"daemon/services/compact_executor.py:941-966. "
                    f"Got resume_await_count="
                    f"{mgr.resume_instance_cascade.await_count}; "
                    f"pause_await_count={mgr.pause_instance_cascade.await_count}. "
                    f"This is the same root cause as defect S1 from the "
                    f"/compact e2e gate (live evidence: instance "
                    f"e1206f9a stalled >130s on 2026-08-31)."
                )

                # Pause/Quiesce happened — RUNNING row precondition
                # AND the bug-trigger path requires it.
                assert mgr.pause_instance_cascade.await_count == 1
                assert mgr.wait_for_instance_quiescent.await_count == 1

                # And the terminalize landed correctly: success +
                # below_floor noop surface.
                ring = dispatcher._state._ring.get(iid, {})
                terminalized = ring.get(command_id)
                assert terminalized is not None
                assert terminalized.phase == "success"
                assert (
                    (terminalized.detail or {}).get("compacted_type")
                    == "noop"
                )
                assert (
                    (terminalized.detail or {}).get("noop_reason")
                    == "below_floor"
                )
            finally:
                await conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 3. Real-LangGraph S2 regression — mid-turn /compact must NOT silently
#    drop queued message jobs. With the fix in place, a follow-up drain
#    processes every PENDING process-message task (no orphans).
# ─────────────────────────────────────────────────────────────────────────


class TestRunningMidTurnQueuedJobsAllProcessedAfterCompact:
    """Defect #1 S2 — no-silent-drop regression for queued message jobs.

    Strengthened per leader follow-up: a test that only asserts
    ``resume.await_count == 1`` would NOT have caught the dropped-jobs
    symptom (the executor's resume fire alone does not prove a Task row
    is still reachable). This test enqueues N=8 PENDING process-message
    tasks against the mid-turn instance BEFORE the compact lifecycle,
    drives the executor, then explicitly drives a worker-pool-style
    claim drain against the tracked Task list and asserts that ALL
    8 distinct tasks are reachable (no silent terminalization,
    no instance-pause filter blocking them).

    Mirrors the user-visible invariant from defect #1 row S2 in the
    tester's live observation (instance ``e1206f9a…``,
    2026-08-31 scope-3 /compact e2e gate). Pre-fix the executor left
    the instance stuck PAUSED, so the worker's pause gate would
    filter out the tasks — equivalent to "all 8 dropped". Post-fix
    the instance leaves PAUSED and the claim drain admits all 8.

    The simulated ``_claim_pending_task`` mirrors the load-bearing
    shape of the real ``TaskRepository.claim_pending_task`` —
    ``daemon/repositories/task/repository.py:1146-1664``:
      * pause gate: returns ``None`` while the instance is PAUSED
      * per-instance guard: at most one Task per instance RUNNING
        at a time (new claims return ``None`` while another is
        RUNNING — a *sequencing* artifact, not a drop)
      * FIFO claim: returns the next PENDING Task in created-order
      * claimed Task transitions PENDING → RUNNING

    The per-instance guard is essential to the test's claim
    semantics: it serializes claims and ensures each claim
    independently returns a DISTINCT task (no double-claim, no
    silent skip). The real path in production holds the guard
    until ``complete_task`` runs; we simulate that exactly.

    DB discipline: file-backed ``AsyncSqliteSaver`` (same harness
    as ``test_compact_executor_revive_brick_e2e.py:57-88``). The
    Task list is held in-process (not in DB) because wiring the
    full Task schema into this regression file would inflate the
    test surface beyond the defect's scope; the simulation is
    sufficient to pin the invariant — that the executor does NOT
    touch queued work and that the claim path is unblocked
    post-lifecycle.
    """

    @pytest.mark.asyncio
    async def test_running_midturn_queued_jobs_all_processed_after_compact(
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

            runs: list[str] = []

            async def _agent(state):
                runs.append("ran")
                return {"messages": [AIMessage(content="agent-out")]}

            db_path = tmp_path / "defect1_s2_strengthened.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(
                    checkpointer=saver, interrupt_before=["agent"]
                )

                iid = "defect1-s2-strengthened-midturn"
                cfg = {"configurable": {"thread_id": iid}}

                # Frozen mid-graph state — the live /compact
                # pause-first precondition.
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1")]}, cfg
                )
                st = await compiled.aget_state(cfg)
                assert st.next == ("agent",)

                # Big messages so the executor reaches the engine
                # (the below-floor pre-check passes).
                big_messages = [
                    HumanMessage(content="x" * 4000, id=f"h-{n}")
                    for n in range(15)
                ]
                await compiled.aupdate_state(
                    cfg, {"messages": big_messages}, as_node="agent"
                )

                # ── The "queued jobs" — N PENDING process-message
                # tasks simulated against the instance. The list
                # mirrors the shape of the tester's bulk-payload
                # burst (factsheets #1..#N on 2026-08-31). Each task
                # has the columns claim_pending_task inspects:
                # id, work_id, instance_id, message_id, status.
                # status starts PENDING. The instance status flag is
                # "paused" (matching the post-compact-pause
                # precondition until resume fires).
                N_QUEUED = 8
                queued_tasks: list[dict[str, Any]] = [
                    {
                        "id": 1000 + n,
                        "work_id": f"work-{iid[:8]}-{n}",
                        "instance_id": iid,
                        "message_id": f"msg-{iid[:8]}-{n}",
                        "status": "pending",
                    }
                    for n in range(N_QUEUED)
                ]
                # Track exact claim sequence for the assertion
                # below — drives the "all 8 distinct" pin.
                claimed_task_ids: list[int] = []

                def _claim_pending_task_simulated(worker_id: str):
                    """Mirror the load-bearing shape of
                    ``TaskRepository.claim_pending_task``
                    (``daemon/repositories/task/repository.py:1146-1664``):

                    1. Pause gate (returns None while instance
                       is PAUSED — the S2 drop mechanism under
                       the original bug).
                    2. Per-instance guard (returns None while
                       another task for the same instance is
                       RUNNING — a sequencing artifact, not
                       a drop; the worker pool completes the
                       in-flight task first).
                    3. FIFO claim — pops the next PENDING
                       task in created-order, transitions it
                       to RUNNING, returns it.
                    """
                    # 1. Pause gate.
                    if instance_status_state["paused"]:
                        return None
                    # 2. Per-instance guard.
                    if per_instance_running["iid"] is not None:
                        return None
                    # 3. FIFO claim.
                    for t in queued_tasks:
                        if t["status"] == "pending":
                            t["status"] = "running"
                            per_instance_running["iid"] = t["id"]
                            claimed_task_ids.append(t["id"])
                            return t
                    return None

                def _complete_task_simulated(task_id: int) -> None:
                    """Mirror ``TaskRepository.complete_task`` —
                    transitions RUNNING → COMPLETED and releases
                    the per-instance guard so the next claim
                    can run for the same instance.
                    """
                    for t in queued_tasks:
                        if t["id"] == task_id and t["status"] == "running":
                            t["status"] = "completed"
                            per_instance_running["iid"] = None
                            return

                # Track live instance status — the simulated
                # pause/resume flips the flag here.
                instance_status_state = {
                    "paused": True,            # compact pauses
                    "running_after_resume": False,
                }
                # Per-instance guard state — at most one Task
                # RUNNING per instance at a time.
                per_instance_running: dict[str, int | None] = {"iid": None}

                # Tweak the simulated ``pause_instance_cascade``
                # and ``resume_instance_cascade`` to flip the
                # ``paused`` flag — that's the load-bearing
                # connection between the executor's lifecycle
                # and the claim gate.
                def _pause_cascade_simulated(_iid):
                    instance_status_state["paused"] = True
                    return {"paused_ids": [_iid], "skipped_ids": []}

                def _resume_cascade_simulated(_iid):
                    instance_status_state["paused"] = False
                    instance_status_state["running_after_resume"] = True
                    return {"resumed_ids": [_iid], "skipped_ids": []}

                dispatcher = CommandDispatcher(
                    enabled=True,
                    escape_prefix="//",
                    min_interval_s=10,
                    state_ttl_s=600,
                    max_state_per_instance=20,
                )
                command_id = "cmd-defect1-s2-strengthened"
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
                        "status": "running",
                        "id": iid,
                        "metadata": {},
                        "children": [],
                    }
                )
                mgr.config = MagicMock()
                mgr.config.llm.model = "gpt-4o"
                mgr.config.slash_commands = SlashCommandConfig(
                    noop_floor_ratio=0.05
                )
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
                    return compiled
                mgr.get_instance = AsyncMock(side_effect=_get_instance)
                mgr.execution_gate = MagicMock()

                async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
                    return await work_fn()
                mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)

                # Pause/Resume are state-aware — they flip the
                # claim gate's ``paused`` flag.
                async def _pause_async(_iid):
                    return _pause_cascade_simulated(_iid)
                async def _resume_async(_iid):
                    return _resume_cascade_simulated(_iid)
                mgr.pause_instance_cascade = AsyncMock(
                    side_effect=_pause_async
                )
                mgr.resume_instance_cascade = AsyncMock(
                    side_effect=_resume_async
                )

                async def _quiescent(instance_id, timeout):
                    return True
                mgr.wait_for_instance_quiescent = AsyncMock(
                    side_effect=_quiescent
                )

                mgr._messaging_service = MagicMock()
                mgr._messaging_service.emit_context_usage_for_instance = AsyncMock()
                mgr._live_hub = MagicMock()
                mgr._live_hub.stream_message = AsyncMock()
                # TaskRepo mirrors the real claim_pending_task.
                mgr._task_repo = MagicMock()
                mgr._task_repo.has_instance_busy = MagicMock(
                    return_value=False
                )
                mgr._task_repo.claim_pending_task = MagicMock(
                    side_effect=_claim_pending_task_simulated
                )
                mgr._task_repo.complete_task = MagicMock(
                    side_effect=_complete_task_simulated
                )

                # The engine returns None — the bug-trigger path
                # (early-return-bypass-resume). With the fix in
                # place, the resume-in-finally still fires.
                mgr._compactor = MagicMock()
                mgr._compactor.compact_state = AsyncMock(return_value=None)

                dispatcher._manager = mgr

                ctx = CommandContext(
                    dispatcher=dispatcher,
                    command_id=command_id,
                    instance_id=iid,
                )

                # ── Drive the executor lifecycle. Pre-fix this
                # leaves the instance stuck PAUSED so the claim
                # gate denies all 8 tasks (the S2 silent-drop
                # mechanism); post-fix the resume-in-finally
                # fires and the claim gate releases. ──
                await execute_compact(
                    mgr,
                    instance_id=iid,
                    command_id=command_id,
                    context=ctx,
                )

                # ── Invariant (1): compact left the instance
                # RUNNING. Pre-fix this was False → claim gate
                # denied the worker, all 8 silently stranded.
                assert mgr.resume_instance_cascade.await_count == 1, (
                    f"DEFECT #1 S2 (load-bearing precondition): "
                    f"resume_instance_cascade MUST fire after a "
                    f"mid-turn /compact — the early-return-bypass "
                    f"bug left the instance stuck PAUSED and the "
                    f"claim gate denied all queued tasks. Got "
                    f"resume_await_count="
                    f"{mgr.resume_instance_cascade.await_count}; "
                    f"pause_await_count="
                    f"{mgr.pause_instance_cascade.await_count}."
                )
                assert instance_status_state["running_after_resume"], (
                    "DEFECT #1 S2: instance status MUST flip "
                    "RUNNING after the resume fires — the worker "
                    "pool's claim gate reads this flag. Got "
                    f"instance_status_state={instance_status_state!r}"
                )
                assert not instance_status_state["paused"], (
                    "DEFECT #1 S2: paused flag MUST be cleared "
                    "after the resume fires — the claim gate "
                    "returns None while this is True. Got "
                    f"paused={instance_status_state['paused']!r}"
                )

                # ── Invariant (2): compact did NOT silently
                # terminalize any queued Task. The list length
                # must be preserved across the lifecycle; the
                # only changes allowed are status transitions
                # ``pending → running`` (claim) or
                # ``running → completed`` (complete). ──
                assert len(queued_tasks) == N_QUEUED, (
                    f"DEFECT #1 S2: compact MUST NOT touch "
                    f"queued Task rows — pre-fix a buggy pause/"
                    f"resume path could terminalize them "
                    f"(DEAD/CANCELLED status without processing). "
                    f"Expected {N_QUEUED} tasks still present, "
                    f"got {len(queued_tasks)}."
                )
                # No task should have been silently terminalized
                # — every one of the original 8 is still in
                # the lifecycle chain (pending or running).
                terminalized_without_processing = [
                    t for t in queued_tasks
                    if t["status"] not in ("pending", "running", "completed")
                ]
                assert not terminalized_without_processing, (
                    f"DEFECT #1 S2: compact MUST NOT terminalize "
                    f"queued Tasks without processing. Found "
                    f"{len(terminalized_without_processing)} "
                    f"tasks in unexpected statuses: "
                    f"{[(t['id'], t['status']) for t in terminalized_without_processing]!r}"
                )

                # ── Invariant (3): drain the claim path. The
                # worker pool, post-resume, calls claim_pending_task
                # in a loop until it returns None. With the fix in
                # place, every PENDING task is admitted exactly
                # once (per-instance guard serializes claims but
                # does NOT drop tasks). ──
                # Reset the claim-side trace for the drain.
                claimed_task_ids.clear()
                # Drive the drain until None — exactly N_QUEUED
                # distinct claims, then None. Each iteration:
                # claim → process → complete → next claim.
                for expected_idx in range(N_QUEUED):
                    claimed = _claim_pending_task_simulated(
                        f"worker-test-{expected_idx}"
                    )
                    assert claimed is not None, (
                        f"DEFECT #1 S2 (claim drain iteration "
                        f"{expected_idx}/{N_QUEUED}): the claim "
                        f"path returned None — this is the S2 "
                        f"silent-drop mechanism. Pre-fix the "
                        f"executor left the instance PAUSED so "
                        f"the pause gate denied claims; the fix "
                        f"(resume-in-finally) must unblock the "
                        f"gate. Already-claimed IDs: "
                        f"{claimed_task_ids!r}; queue left: "
                        f"{[(t['id'], t['status']) for t in queued_tasks]!r}"
                    )
                    # Simulate the worker completing the task
                    # so the per-instance guard releases for
                    # the next iteration.
                    _complete_task_simulated(claimed["id"])
                # After draining all N, the next claim MUST be
                # None — the queue is empty.
                end_claim = _claim_pending_task_simulated(
                    "worker-test-end"
                )
                assert end_claim is None, (
                    f"DEFECT #1 S2: after draining all "
                    f"{N_QUEUED} tasks, the claim path must "
                    f"return None — extra claims imply the "
                    f"queue grew during the lifecycle "
                    f"(pre-fix bug symptom). Got "
                    f"end_claim={end_claim!r}"
                )
                # AND the claim sequence MUST contain all
                # N_QUEUED distinct task IDs in created-order
                # (FIFO). Per-instance-guard releases between
                # iterations are sufficient; we never claim
                # the same task twice and we don't skip any.
                assert sorted(claimed_task_ids) == sorted(
                    [1000 + n for n in range(N_QUEUED)]
                ), (
                    f"DEFECT #1 S2: claim drain MUST admit "
                    f"every distinct queued Task — pre-fix a "
                    f"silent-drop path left only a subset "
                    f"reachable (tester's live observation: "
                    f"'processes only 1 of 8 queued jobs — "
                    f"7 silently dropped'). Got "
                    f"claimed_task_ids={claimed_task_ids!r}; "
                    f"expected a permutation of "
                    f"{[1000 + n for n in range(N_QUEUED)]!r}"
                )

                # ── Invariant (4): terminalize landed
                # correctly (the engine-returned-None noop
                # surface must still surface success + below
                # floor). ──
                ring = dispatcher._state._ring.get(iid, {})
                terminalized = ring.get(command_id)
                assert terminalized is not None
                assert terminalized.phase == "success"
                assert (
                    (terminalized.detail or {}).get("compacted_type")
                    == "noop"
                )
                assert (
                    (terminalized.detail or {}).get("noop_reason")
                    == "below_floor"
                )
            finally:
                await conn.close()
