"""End-of-turn injection drain — regression pins (turn-end-injection-drain).

The RAM user-message injection FIFO (``InstanceManager._pending_injections``)
is drained by the agent_node at the START of every LLM pass. A user message
that arrives while the turn's FINAL LLM pass is in flight used to be
silently stranded: the turn ended with the entry still queued, the FIFO is
RAM-only, and the next lifecycle cleanup popped it — the message never
reached the model.

Two seams close the drop:

  * ``daemon/graph.py::create_injection_aware_router`` — the agent's
    terminal router PEEKS the queue and routes back to ``agent`` while
    entries are pending, so the turn CONTINUES and the agent_node's
    EXISTING drain site consumes them (same SSE / echo-id / checkpoint
    contract as a mid-turn injection). This covers BOTH instance-level
    finalization paths — the normal turn completion and the RUNNING →
    WAITING_CHILDREN transition — because both of them run AFTER the
    graph run.

  * ``daemon/services/message_processing_pipeline.py`` —
    ``_drain_end_of_turn_injections`` closes the narrow residual window
    between that router's verdict and the instance-level finalization by
    scheduling a durable follow-up turn, whose dispatch-time D2 seam
    drain consumes the FIFO.

Every pin in this module is mutation-verified: reverting the fix makes
the corresponding test RED.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    INJECTION_CONTINUATION_MAX,
    create_injection_aware_router,
    should_continue,
)
from daemon.services.message_processing_pipeline import (
    MessageProcessingPipeline,
    PipelineCallbacks,
    ProcessingContext,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubInjectionSlot:
    """In-memory stand-in for ``daemon.graph.InjectionSlot``.

    Mirrors the real handle's list-shaped get/clear contract:

        get(instance_id)    -> list[dict] | None
        clear(instance_id)  -> list[dict] | None
    """

    def __init__(self, initial: dict[str, list[dict[str, str]]] | None = None):
        self._store: dict[str, list[dict[str, str]]] = dict(initial or {})
        self.get_calls: list[str] = []
        self.clear_calls: list[str] = []

    def get(self, instance_id: str) -> list[dict[str, str]] | None:
        self.get_calls.append(instance_id)
        queue = self._store.get(instance_id)
        if not queue:
            return None
        return list(queue)

    def clear(self, instance_id: str) -> list[dict[str, str]] | None:
        self.clear_calls.append(instance_id)
        return self._store.pop(instance_id, None)

    def set(self, instance_id: str, content: str) -> None:
        self._store.setdefault(instance_id, []).append(
            {"content": content, "timestamp": "2026-01-01T00:00:00+00:00"}
        )


class _ScriptedLLM:
    """LLM stub whose ``invoke`` side effects are scripted per call index.

    ``on_call`` is a list of callables (or ``None``) — one per invocation.
    A callable receives the messages and may mutate external state (the
    injection slot) BEFORE the response is returned, which is exactly how
    a mid-turn injection arrives: while the LLM call is in flight.
    """

    def __init__(self, on_call: list[Any] | None = None):
        self.on_call = list(on_call or [])
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> Any:
        index = len(self.calls)
        self.calls.append(list(messages))
        hook = self.on_call[index] if index < len(self.on_call) else None
        if hook is not None:
            hook(messages)
        return AIMessage(content=f"answer-{index}")


# ---------------------------------------------------------------------------
# Seam 1 — the graph-level turn-end routing (loop-continue)
# ---------------------------------------------------------------------------


class TestTurnEndRouter:
    """The terminal router keeps the turn alive while injections are queued."""

    def test_no_slot_is_inert(self):
        """Without an injection slot the base router is returned unchanged."""
        base = MagicMock(return_value="__end__")
        router = create_injection_aware_router(base, None)
        assert router is base

    def test_pending_injection_routes_back_to_agent(self):
        base = MagicMock(return_value="__end__")
        slot = _StubInjectionSlot({"iid-1": [{"content": "hi", "timestamp": "ts"}]})
        router = create_injection_aware_router(base, slot)

        verdict = router({}, {"configurable": {"thread_id": "iid-1"}})

        assert verdict == "agent"
        assert base.call_count == 1

    def test_empty_queue_ends_the_turn(self):
        base = MagicMock(return_value="__end__")
        slot = _StubInjectionSlot()
        router = create_injection_aware_router(base, slot)

        verdict = router({}, {"configurable": {"thread_id": "iid-1"}})

        assert verdict == "__end__"

    def test_non_terminal_verdicts_pass_through(self):
        """A tool-calling pass must still route to ``tools``."""
        for verdict in ("tools", "nudge", "agent", "agent_repair_ghost"):
            base = MagicMock(return_value=verdict)
            slot = _StubInjectionSlot(
                {"iid-1": [{"content": "hi", "timestamp": "ts"}]}
            )
            router = create_injection_aware_router(base, slot)

            assert (
                router({}, {"configurable": {"thread_id": "iid-1"}})
                == verdict
            ), verdict

    def test_every_terminal_verdict_is_intercepted(self):
        """END, the language-check route and the attestation gate all end the
        turn — all three must be intercepted."""
        for terminal in ("__end__", "end_candidate", "attestation_gate"):
            base = MagicMock(return_value=terminal)
            slot = _StubInjectionSlot(
                {"iid-1": [{"content": "hi", "timestamp": "ts"}]}
            )
            router = create_injection_aware_router(base, slot)

            assert (
                router({}, {"configurable": {"thread_id": "iid-1"}})
                == "agent"
            ), terminal

    def test_continuation_is_bounded(self):
        """A producer that keeps injecting must not spin the graph forever."""
        slot = _StubInjectionSlot(
            {"iid-1": [{"content": "hi", "timestamp": "ts"}]}
        )
        router = create_injection_aware_router(
            MagicMock(return_value="__end__"), slot, max_continuations=2
        )

        verdicts = [
            router({}, {"configurable": {"thread_id": "iid-1"}})
            for _ in range(4)
        ]

        assert verdicts == ["agent", "agent", "__end__", "__end__"]

    def test_budget_resets_after_an_ordinary_turn(self):
        """The continuation budget is per-turn: an empty queue resets it."""
        slot = _StubInjectionSlot(
            {"iid-1": [{"content": "hi", "timestamp": "ts"}]}
        )
        router = create_injection_aware_router(
            MagicMock(return_value="__end__"), slot, max_continuations=1
        )
        cfg = {"configurable": {"thread_id": "iid-1"}}

        assert router({}, cfg) == "agent"
        # The queue drains → ordinary turn end → budget reset.
        slot.clear("iid-1")
        assert router({}, cfg) == "__end__"
        # A NEW injection in a later turn gets its full budget again.
        slot.set("iid-1", "later")
        assert router({}, cfg) == "agent"

    def test_missing_thread_id_does_not_continue(self):
        """No instance id → no probe → the base verdict stands."""
        base = MagicMock(return_value="__end__")
        slot = _StubInjectionSlot(
            {"iid-1": [{"content": "hi", "timestamp": "ts"}]}
        )
        router = create_injection_aware_router(base, slot)

        assert router({}, None) == "__end__"
        assert router({}, {"configurable": {}}) == "__end__"

    def test_probe_failure_does_not_break_the_turn(self):
        """A raising injection slot must not take the turn down."""

        class _BrokenSlot:
            def get(self, instance_id):
                raise RuntimeError("boom")

        base = MagicMock(return_value="__end__")
        router = create_injection_aware_router(base, _BrokenSlot())

        assert router({}, {"configurable": {"thread_id": "iid-1"}}) == "__end__"


class TestTurnEndInjectionEndToEnd:
    """The graph-level proof: a message injected mid-turn is consumed.

    These legs build a REAL compiled LangGraph. The unit shard mocks the
    ``langgraph`` package (``tests/conftest.py``), so the
    ``real_graph_module`` fixture evicts the mocks and re-imports
    ``daemon.graph`` against the real package for the duration of the
    test — the repo's established pattern for real-graph unit legs.
    """

    @pytest.mark.asyncio
    async def test_injection_arriving_during_the_final_llm_call_is_consumed(
        self, real_graph_module
    ):
        """Acceptance 1 — the message is NOT dropped; the model sees it.

        The stub LLM injects the message while its FIRST (final) call is in
        flight — exactly the deterministic end-of-turn drop. The router must
        keep the turn alive, and the agent_node's existing drain site must
        consume the entry on the next pass.
        """
        from langgraph.graph import END, START, MessagesState, StateGraph

        iid = "iid-turn-end"
        slot = _StubInjectionSlot()
        llm = _ScriptedLLM(
            on_call=[
                # The user types while the final LLM call is in flight.
                lambda _messages: slot.set(iid, "also do X"),
                None,
            ]
        )
        agent_node = real_graph_module.create_agent_node(
            llm_with_tools=llm,
            system_prompt="you are a test assistant",
            compactor=None,
            graph_ref=[None],
            config=None,
            llm_config={"model": "test-model", "model_vision": None},
            retry_config={"transient_attempts": 1, "timeout_attempts": 1},
            llm_standard=None,
            injection_slot=slot,
            report_injection_slot=None,
            live_hub=None,
        )
        router = real_graph_module.create_injection_aware_router(
            real_graph_module.should_continue, slot
        )
        graph = StateGraph(MessagesState)
        graph.add_node("agent", agent_node)
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", router, {"agent": "agent", END: END})
        compiled = graph.compile()

        async for _ in compiled.astream(
            {"messages": [HumanMessage(content="original question")]},
            {"configurable": {"thread_id": iid}},
        ):
            pass

        # The turn CONTINUED: the LLM was invoked twice.
        assert len(llm.calls) == 2
        # The second call carried the injected message to the model.
        injected = [
            m
            for m in llm.calls[1]
            if isinstance(m, HumanMessage) and m.content == "also do X"
        ]
        assert len(injected) == 1
        assert injected[0].additional_kwargs.get("injected_message") is True
        # The queue was drained — the message was consumed, not stranded.
        assert slot.get(iid) is None
        assert slot.clear_calls == [iid]

    @pytest.mark.asyncio
    async def test_no_injection_ends_the_turn_after_one_llm_call(
        self, real_graph_module
    ):
        """The control: nothing queued → the turn ends immediately."""
        from langgraph.graph import END, START, MessagesState, StateGraph

        iid = "iid-quiet"
        slot = _StubInjectionSlot()
        llm = _ScriptedLLM()
        agent_node = real_graph_module.create_agent_node(
            llm_with_tools=llm,
            system_prompt="you are a test assistant",
            compactor=None,
            graph_ref=[None],
            config=None,
            llm_config={"model": "test-model", "model_vision": None},
            retry_config={"transient_attempts": 1, "timeout_attempts": 1},
            llm_standard=None,
            injection_slot=slot,
            report_injection_slot=None,
            live_hub=None,
        )
        router = real_graph_module.create_injection_aware_router(
            real_graph_module.should_continue, slot
        )
        graph = StateGraph(MessagesState)
        graph.add_node("agent", agent_node)
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", router, {"agent": "agent", END: END})
        compiled = graph.compile()

        async for _ in compiled.astream(
            {"messages": [HumanMessage(content="original question")]},
            {"configurable": {"thread_id": iid}},
        ):
            pass

        assert len(llm.calls) == 1
        assert slot.clear_calls == []


class TestProductionGraphWiring:
    """The PRODUCTION wiring must keep the turn alive too.

    The router factory is only half the fix — ``build_instance_graph``
    must actually wrap the agent's terminal router with it, in BOTH
    wiring shapes (language check ON and OFF). These legs build the real
    production graph and drive a real turn.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_language", ["Auto", "English"])
    async def test_production_graph_consumes_a_mid_turn_injection(
        self, real_graph_module, user_language
    ):
        """A message injected while the final LLM call is in flight must be
        consumed by the PRODUCTION graph, not dropped."""
        from langchain_core.messages import AIMessage

        iid = "iid-production"

        class _Slot(_StubInjectionSlot):
            pass

        slot = _Slot()

        class _LLM:
            def __init__(self):
                self.calls: list[list[Any]] = []

            def invoke(self, messages):
                self.calls.append(list(messages))
                if len(self.calls) == 1:
                    # The user types while the final LLM call is in flight.
                    slot.set(iid, "also do X")
                return AIMessage(content="ok")

        llm = _LLM()
        real_graph_module.build_instance_llms = lambda **_: (llm, llm)
        graph = real_graph_module.build_instance_graph(
            tools=[],
            checkpointer=None,
            llm_config={"model": "test-model", "base_url": "http://example.invalid"},
            system_prompt="you are a test assistant",
            retry_config={"transient_attempts": 1, "timeout_attempts": 1},
            injection_slot=slot,
            user_language=user_language,
        )

        async for _ in graph.astream(
            {"messages": [HumanMessage(content="original question")]},
            {"configurable": {"thread_id": iid}},
        ):
            pass

        assert len(llm.calls) == 2, user_language
        assert slot.get(iid) is None, user_language
        injected = [
            m
            for m in llm.calls[1]
            if isinstance(m, HumanMessage) and m.content == "also do X"
        ]
        assert len(injected) == 1, user_language


# ---------------------------------------------------------------------------
# Seam 2 — the instance-level end-of-turn drain (pipeline)
# ---------------------------------------------------------------------------

def _make_manager(pending: list[dict] | None):
    manager = MagicMock(name="InstanceManager")
    manager.get_injection.return_value = pending
    manager.enqueue_message = AsyncMock(return_value=MagicMock())
    manager._process_message_with_tracking = AsyncMock(
        return_value=MagicMock(content="ok")
    )
    manager._process_child_completion_and_notify_parent = AsyncMock()
    manager._live_hub = None
    manager._job_feedback_observer = None
    repo = MagicMock(name="InstanceRepository")
    repo.transition_status_if.return_value = MagicMock(agent_id="agent-1")
    manager._instance_repository = repo
    return manager


def _make_pipeline(manager) -> MessageProcessingPipeline:
    gate = MagicMock(name="ExecutionGate")

    async def _run(instance_id, holder_id, holder_kind, work_fn):
        return await work_fn()

    gate.run = _run
    return MessageProcessingPipeline(execution_gate=gate, manager=manager)


def _make_context(instance_id: str = "iid-pipeline") -> ProcessingContext:
    return ProcessingContext(
        instance_id=instance_id,
        message_id="msg-1",
        message="hello",
    )


class TestPipelineEndOfTurnDrain:
    """Acceptance 2 + the normal-completion seam."""

    @pytest.mark.asyncio
    async def test_waiting_children_transition_drains_queued_injections(self):
        """The WAITING_CHILDREN path must not strand a queued message.

        A parked parent may never run another turn on its own, so the drain
        runs INSIDE the transition.
        """
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        pipeline = _make_pipeline(manager)
        bus = MagicMock(name="DependencyBus")
        bus.count_pending_for_target = AsyncMock(return_value=1)

        with patch(
            "daemon.services.message_processing_pipeline.get_dependency_bus",
            return_value=bus,
        ):
            transitioned = await pipeline._maybe_transition_to_waiting_children(
                "iid-pipeline"
            )

        assert transitioned is True
        manager.enqueue_message.assert_awaited_once()
        args, kwargs = manager.enqueue_message.await_args
        assert args[0] == "iid-pipeline"
        assert kwargs["source"] == "system:injection_drain"

    @pytest.mark.asyncio
    async def test_waiting_children_transition_is_a_noop_without_pending(self):
        """No bus watchers → no transition → no drain."""
        manager = _make_manager(None)
        pipeline = _make_pipeline(manager)
        bus = MagicMock(name="DependencyBus")
        bus.count_pending_for_target = AsyncMock(return_value=0)

        with patch(
            "daemon.services.message_processing_pipeline.get_dependency_bus",
            return_value=bus,
        ):
            transitioned = await pipeline._maybe_transition_to_waiting_children(
                "iid-pipeline"
            )

        assert transitioned is False
        manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_normal_turn_completion_drains_queued_injections(self):
        """The NORMAL turn-completion path must not strand a queued message."""
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        pipeline = _make_pipeline(manager)
        bus = MagicMock(name="DependencyBus")
        bus.count_pending_for_target = AsyncMock(return_value=0)

        with patch(
            "daemon.services.message_processing_pipeline.get_dependency_bus",
            return_value=bus,
        ):
            result = await pipeline.execute(
                _make_context(),
                holder_id="holder-1",
                holder_kind="task",
                callbacks=PipelineCallbacks(),
            )

        assert result.success is True
        manager.enqueue_message.assert_awaited_once()
        _, kwargs = manager.enqueue_message.await_args
        assert kwargs["source"] == "system:injection_drain"

    @pytest.mark.asyncio
    async def test_no_queued_injection_means_no_follow_up_turn(self):
        """The control: an empty queue schedules nothing."""
        manager = _make_manager(None)
        pipeline = _make_pipeline(manager)
        bus = MagicMock(name="DependencyBus")
        bus.count_pending_for_target = AsyncMock(return_value=0)

        with patch(
            "daemon.services.message_processing_pipeline.get_dependency_bus",
            return_value=bus,
        ):
            result = await pipeline.execute(
                _make_context(),
                holder_id="holder-1",
                holder_kind="task",
                callbacks=PipelineCallbacks(),
            )

        assert result.success is True
        manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_failure_is_non_fatal(self):
        """A failing follow-up enqueue must never fail the message."""
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        manager.enqueue_message = AsyncMock(side_effect=RuntimeError("boom"))
        pipeline = _make_pipeline(manager)
        bus = MagicMock(name="DependencyBus")
        bus.count_pending_for_target = AsyncMock(return_value=0)

        with patch(
            "daemon.services.message_processing_pipeline.get_dependency_bus",
            return_value=bus,
        ):
            result = await pipeline.execute(
                _make_context(),
                holder_id="holder-1",
                holder_kind="task",
                callbacks=PipelineCallbacks(),
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_drain_keeps_the_fifo_for_the_follow_up_turn(self):
        """The drain must NOT clear the queue.

        Clearing it would drop the server-minted ``echo_id`` and break the
        emit-twice-same-id SSE/checkpoint contract — the follow-up turn's
        dispatch-time D2 seam drain consumes the queue instead.
        """
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        pipeline = _make_pipeline(manager)

        drained = await pipeline._drain_end_of_turn_injections("iid-pipeline")

        assert drained is True
        manager.get_injection.assert_called_once_with("iid-pipeline")
        assert manager.clear_injection.call_count == 0


class TestContinuationBudgetConstant:
    """The bound is a module constant with a sane value."""

    def test_cap_is_positive_and_bounded(self):
        assert 0 < INJECTION_CONTINUATION_MAX <= 32


# ---------------------------------------------------------------------------
# FIX-BACK Y3 — the TERMINATED-revive guard
# ---------------------------------------------------------------------------


class TestHardTerminalReviveGuard:
    """A hard-terminal instance must NOT be revived by the drain enqueue.

    Stage 7 runs after every normal completion INCLUDING
    ``watchover_terminate_node → END``. A non-empty FIFO at that point
    used to make the "continue" enqueue flip TERMINATED / ERROR / FAILED
    back to RUNNING (``_prepare_enqueued_message``'s
    ``is_terminal_revival`` branch), overriding the watcher's explicit
    termination decision. The guard reads the FRESH status from the
    repository and skips the enqueue (log-only) on a hard-terminal
    status; the FIFO stays queued for the next revival turn's
    dispatch-time D2 seam drain — the no-drop guarantee is preserved.
    """

    @staticmethod
    def _instance_with_status(status: str):
        from types import SimpleNamespace

        return SimpleNamespace(status=status)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            "terminated",
            "error",
            "failed",
        ],
    )
    async def test_hard_terminal_instance_is_not_revived(self, status):
        """No enqueue / no revive; the FIFO is retained, not cleared."""
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        repo = manager._instance_repository
        repo.get.return_value = self._instance_with_status(status)
        pipeline = _make_pipeline(manager)

        drained = await pipeline._drain_end_of_turn_injections("iid-pipeline")

        assert drained is False
        manager.enqueue_message.assert_not_awaited()
        # The status was read FRESH from the repository.
        repo.get.assert_called_once_with("iid-pipeline")
        # The FIFO is retained for the next revival turn's D2 drain.
        manager.clear_injection.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["running", "idle", "completed"])
    async def test_non_hard_terminal_instance_still_gets_the_follow_up(self, status):
        """The control: the guard only blocks TERMINATED / ERROR / FAILED.

        COMPLETED is deliberately NOT blocked — reviving a completed root
        to answer a queued user message is the established new-message
        lane.
        """
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        repo = manager._instance_repository
        repo.get.return_value = self._instance_with_status(status)
        pipeline = _make_pipeline(manager)

        drained = await pipeline._drain_end_of_turn_injections("iid-pipeline")

        assert drained is True
        manager.enqueue_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_probe_failure_fails_open(self):
        """A failed fresh-status read does NOT silently skip the enqueue.

        The enqueue below is itself a DB write that fails on its own under
        the same conditions (already caught + logged), so fail-open cannot
        silently revive anything — while failing closed would degrade the
        delivery guarantee on a transient blip.
        """
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        repo = manager._instance_repository
        repo.get.side_effect = RuntimeError("db down")
        pipeline = _make_pipeline(manager)

        drained = await pipeline._drain_end_of_turn_injections("iid-pipeline")

        assert drained is True
        manager.enqueue_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_repository_still_gets_the_follow_up(self):
        """No repository wired → no probe possible → the enqueue proceeds."""
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        manager._instance_repository = None
        pipeline = _make_pipeline(manager)

        drained = await pipeline._drain_end_of_turn_injections("iid-pipeline")

        assert drained is True
        manager.enqueue_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_terminated_instance_is_not_revived_on_the_normal_path(self):
        """The guard holds through the full normal-completion pipeline."""
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        repo = manager._instance_repository
        repo.get.return_value = self._instance_with_status("terminated")
        pipeline = _make_pipeline(manager)
        bus = MagicMock(name="DependencyBus")
        bus.count_pending_for_target = AsyncMock(return_value=0)

        with patch(
            "daemon.services.message_processing_pipeline.get_dependency_bus",
            return_value=bus,
        ):
            result = await pipeline.execute(
                _make_context(),
                holder_id="holder-1",
                holder_kind="task",
                callbacks=PipelineCallbacks(),
            )

        assert result.success is True
        manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_terminated_instance_guard_also_holds_on_the_wc_path(self):
        """The WAITING_CHILDREN transition's drain honours the guard too."""
        manager = _make_manager([{"content": "also do X", "timestamp": "ts"}])
        repo = manager._instance_repository
        repo.get.return_value = self._instance_with_status("terminated")
        repo.transition_status_if.return_value = MagicMock(agent_id="agent-1")
        pipeline = _make_pipeline(manager)
        bus = MagicMock(name="DependencyBus")
        bus.count_pending_for_target = AsyncMock(return_value=1)

        with patch(
            "daemon.services.message_processing_pipeline.get_dependency_bus",
            return_value=bus,
        ):
            transitioned = await pipeline._maybe_transition_to_waiting_children(
                "iid-pipeline"
            )

        assert transitioned is True
        manager.enqueue_message.assert_not_awaited()
