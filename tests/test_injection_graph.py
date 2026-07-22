"""Unit tests for agent_node injection consumption (Phase 1 / C1, C2).

Covers the create_agent_node factory's handling of the InjectionSlot handle:
    * When no injection is present, agent_node returns {'messages': [response]}.
    * When an injection is present, agent_node appends a HumanMessage with
      ``additional_kwargs={'injected_message': True}`` to the LLM call AND
      returns BOTH messages in the result dict (for checkpoint persistence).
    * The injection slot is cleared after consumption (peek-then-clear).
    * The reactive compaction path re-appends the injected message after
      a checkpoint re-read so a ContextLengthExceededError doesn't drop it.

These tests construct ``create_agent_node`` directly with stub LLMs and
mock injection_slot / live_hub handles — no LangGraph, no LangChain
runtime, no daemon manager. The goal is to verify the slot consumption
contract in isolation.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubInjectionSlot:
    """In-memory mock of the InjectionSlot handle.

    Phase 3: mirrors the real handle's list-shaped get/clear contract:
        get(instance_id)    -> list[dict] | None
        clear(instance_id)  -> list[dict] | None

    Records every call so tests can assert against the call sequence.
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
        return list(queue)  # defensive copy

    def clear(self, instance_id: str) -> list[dict[str, str]] | None:
        self.clear_calls.append(instance_id)
        return self._store.pop(instance_id, None)

    # Convenience helpers for tests
    def set(self, instance_id: str, content: str) -> None:
        queue = self._store.setdefault(instance_id, [])
        queue.append({"content": content, "timestamp": "2026-01-01T00:00:00+00:00"})

    def set_many(self, instance_id: str, contents: list[str]) -> None:
        queue = self._store.setdefault(instance_id, [])
        for c in contents:
            queue.append({"content": c, "timestamp": "2026-01-01T00:00:00+00:00"})


class _StubLLM:
    """Returns a configured response (or raises) on invoke.

    Captures the messages it was called with so tests can verify that the
    injected HumanMessage was appended to the LLM input.
    """

    def __init__(self, response: Any = None, raise_on_invoke: Exception | None = None):
        self.response = response if response is not None else AIMessage(content="ok")
        self.raise_on_invoke = raise_on_invoke
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(list(messages))
        if self.raise_on_invoke is not None:
            raise self.raise_on_invoke
        return self.response


def _make_agent(
    injection_slot: Any | None = None,
    live_hub: Any | None = None,
    llm: Any | None = None,
    compactor: Any = None,
    graph_ref: Any = None,
):
    """Build a fresh agent_node for a test, bypassing build_instance_graph."""
    from daemon.graph import create_agent_node

    if llm is None:
        # Default to "ok" so simple tests asserting on the response content
        # match the stub's own default — see ``_StubLLM.__init__``.
        llm = _StubLLM()
    if graph_ref is None:
        # No reactive compaction by default; agent_node expects graph_ref[0]=None
        graph_ref = [None]

    agent_node = create_agent_node(
        llm_with_tools=llm,
        system_prompt="you are a test assistant",
        compactor=compactor,
        graph_ref=graph_ref,
        config=None,
        llm_config={"model": "test-model", "model_vision": None},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        llm_standard=None,
        injection_slot=injection_slot,
        live_hub=live_hub,
    )
    return agent_node, llm


# ---------------------------------------------------------------------------
# Consumption behavior
# ---------------------------------------------------------------------------


class TestAgentNodeInjectionConsumption:
    """When the slot has content, the agent must consume it before LLM call."""

    @pytest.mark.asyncio
    async def test_no_injection_returns_only_response(self):
        """No injection -> return {'messages': [response]} (single message)."""
        slot = _StubInjectionSlot()
        agent_node, llm = _make_agent(injection_slot=slot)

        result = await agent_node(
            {"messages": [HumanMessage(content="hi")]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        assert "messages" in result
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)
        # Slot was peeked but not cleared (because nothing was there)
        assert slot.get_calls == ["iid-1"]
        assert slot.clear_calls == []
        # LLM saw only the user's HumanMessage (plus system prompt)
        sent_to_llm = llm.calls[0]
        assert isinstance(sent_to_llm[0], SystemMessage)
        assert any(
            isinstance(m, HumanMessage) and m.content == "hi" for m in sent_to_llm
        )

    @pytest.mark.asyncio
    async def test_injection_present_appends_human_message_to_llm(self):
        """Injection must be appended to LLM input with the injected_message flag."""
        slot = _StubInjectionSlot(initial={"iid-1": [{"content": "INTERRUPT", "timestamp": "ts"}]})
        agent_node, llm = _make_agent(injection_slot=slot)

        result = await agent_node(
            {"messages": [HumanMessage(content="orig")]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # LLM call saw [System, orig Human, injected Human]
        sent_to_llm = llm.calls[0]
        assert isinstance(sent_to_llm[0], SystemMessage)
        assert sent_to_llm[1].content == "orig"
        injected = sent_to_llm[2]
        assert isinstance(injected, HumanMessage)
        assert injected.content == "INTERRUPT"
        # C2 flag is set so downstream compaction knows to preserve
        assert (injected.additional_kwargs or {}).get("injected_message") is True

    @pytest.mark.asyncio
    async def test_injection_present_returns_both_messages(self):
        """C2: return {'messages': [injected, response]} for checkpoint persistence."""
        slot = _StubInjectionSlot(initial={"iid-1": [{"content": "USER-INJECT", "timestamp": "ts"}]})
        agent_node, llm = _make_agent(injection_slot=slot)

        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        msgs = result["messages"]
        assert len(msgs) == 2
        # First is the injected HumanMessage
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "USER-INJECT"
        assert (msgs[0].additional_kwargs or {}).get("injected_message") is True
        # Second is the LLM response
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].content == "ok"

    @pytest.mark.asyncio
    async def test_injection_cleared_after_consumption(self):
        """Slot is cleared synchronously after the LLM call (peek-then-clear)."""
        slot = _StubInjectionSlot(initial={"iid-1": [{"content": "X", "timestamp": "ts"}]})
        agent_node, _ = _make_agent(injection_slot=slot)

        await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        assert slot.clear_calls == ["iid-1"]
        # Slot is empty afterwards (next get returns None)
        assert slot.get("iid-1") is None

    @pytest.mark.asyncio
    async def test_get_called_before_clear(self):
        """Order matters: get first (to capture content), then clear.

        We verify ordering by appending the call TYPE to a single shared
        timeline (rather than checking per-method call indices, which is
        brittle when each list has a single entry).
        """
        slot = _StubInjectionSlot(initial={"iid-1": [{"content": "X", "timestamp": "ts"}]})

        # Wrap the methods so we record a single ordered log of which call
        # happened first.
        original_get = slot.get
        original_clear = slot.clear
        timeline: list[str] = []

        def traced_get(iid):
            timeline.append("get")
            return original_get(iid)
        def traced_clear(iid):
            timeline.append("clear")
            return original_clear(iid)
        slot.get = traced_get  # type: ignore[method-assign]
        slot.clear = traced_clear  # type: ignore[method-assign]

        agent_node, _ = _make_agent(injection_slot=slot)

        await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # The slot was peeked once and cleared once, in that order.
        assert slot.get_calls == ["iid-1"]
        assert slot.clear_calls == ["iid-1"]
        # And the timeline records the order unambiguously.
        assert timeline == ["get", "clear"]
        assert timeline.index("get") < timeline.index("clear")

    @pytest.mark.asyncio
    async def test_multi_entry_queue_consumed_in_fifo_order(self):
        """Phase 3: a multi-entry queue is consumed in FIFO order —
        oldest first, all messages appended to the LLM input, all
        returned in the result.
        """
        slot = _StubInjectionSlot(initial={
            "iid-1": [
                {"content": "first", "timestamp": "ts1"},
                {"content": "second", "timestamp": "ts2"},
                {"content": "third", "timestamp": "ts3"},
            ],
        })
        agent_node, llm = _make_agent(injection_slot=slot)

        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # LLM saw [System, first, second, third] in FIFO order
        sent_to_llm = llm.calls[0]
        assert isinstance(sent_to_llm[0], SystemMessage)
        assert sent_to_llm[1].content == "first"
        assert sent_to_llm[2].content == "second"
        assert sent_to_llm[3].content == "third"
        # All carry the injected_message flag
        for i in range(1, 4):
            assert (sent_to_llm[i].additional_kwargs or {}).get("injected_message") is True

        # Result: [first, second, third, response] — C2 persists all
        msgs = result["messages"]
        assert len(msgs) == 4
        assert msgs[0].content == "first"
        assert msgs[1].content == "second"
        assert msgs[2].content == "third"
        assert isinstance(msgs[3], AIMessage)

        # Queue is fully cleared
        assert slot.get("iid-1") is None


# ---------------------------------------------------------------------------
# Backward-compat: no injection_slot
# ---------------------------------------------------------------------------


class TestAgentNodeNoInjectionSlot:
    """Backward compat: agent_node must work when no slot is provided."""

    @pytest.mark.asyncio
    async def test_no_slot_returns_only_response(self):
        """When injection_slot=None, behavior is identical to the pre-Phase-1 path."""
        agent_node, llm = _make_agent(injection_slot=None)

        result = await agent_node(
            {"messages": [HumanMessage(content="hi")]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Single message (no injected HumanMessage in the result)
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)

        # LLM only saw [System, Human("hi")]
        sent_to_llm = llm.calls[0]
        assert len(sent_to_llm) == 2
        assert isinstance(sent_to_llm[0], SystemMessage)
        assert isinstance(sent_to_llm[1], HumanMessage)
        assert sent_to_llm[1].content == "hi"


# ---------------------------------------------------------------------------
# Reactive compaction re-append (C3)
# ---------------------------------------------------------------------------


class _StubCompactor:
    """Reactive compactor stub: returns a synthetic replacement list."""

    def __init__(self, replacement_messages: list[Any] | None = None):
        self.replacement_messages = replacement_messages or []
        self.compact_state_calls: list[Any] = []
        self.config = MagicMock()  # used by the reactive path
        self.llm_config = MagicMock()

    async def compact_state(self, ctx):
        self.compact_state_calls.append(ctx)
        from daemon.compaction import CompactionResult
        return CompactionResult(
            replacement_messages=self.replacement_messages,
            tokens_before=999,
            tokens_after=10,
            tokens_saved=989,
            messages_before=20,
            messages_after=1,
            compaction_type="summarization",
        )


class _StubGraph:
    """Stub for graph_ref[0] used by the reactive compaction path."""

    def __init__(self, state_messages: list[Any]):
        self._state = {"messages": state_messages}
        self.aget_state_calls: list[Any] = []
        self.aupdate_state_calls: list[tuple[Any, str]] = []

    async def aget_state(self, config):
        self.aget_state_calls.append(config)
        # Mimic LangGraph's GetStateResult shape (values + config keys)
        class _State:
            values = self._state
        return _State()

    async def aupdate_state(self, config, values, as_node=None):
        self.aupdate_state_calls.append((values, as_node or ""))
        # Apply messages update in-place so subsequent aget_state reflects it
        if "messages" in values:
            self._state["messages"] = values["messages"]
        if "compacted_at" in values:
            self._state["compacted_at"] = values["compacted_at"]


class TestReactiveCompactionReAppendsInjection:
    """C3: When the LLM raises ContextLengthExceededError AND an injection was
    just consumed, the reactive compaction path must re-append the injected
    message to the compacted list before re-invoking the LLM. Otherwise the
    user's injected message would be silently dropped."""

    @pytest.mark.asyncio
    async def test_injection_re_appended_after_reactive_compaction(self):
        from daemon.llm_error_classifier import ContextLengthExceededError

        # Use a subclass so we can construct without a real BadRequestError.
        # The agent_node only checks ``isinstance`` against
        # ``ContextLengthExceededError``, so a no-arg subclass is fine.
        class _StubContextLengthError(ContextLengthExceededError):
            def __init__(self):
                # Skip parent __init__ (which expects a BadRequestError)
                # and just initialize Exception directly.
                Exception.__init__(self, "stub context length error")
                self.original_error = None
                self.model = "test-model"

        slot = _StubInjectionSlot(initial={"iid-1": [{"content": "MUST-SURVIVE", "timestamp": "ts"}]})

        # Two-shot LLM: first invoke raises, second returns success.
        class _TwoShotLLM:
            def __init__(self):
                self.calls: list[list[Any]] = []
                self.attempt = 0
            def invoke(self, messages):
                self.calls.append(list(messages))
                self.attempt += 1
                if self.attempt == 1:
                    raise _StubContextLengthError()
                return AIMessage(content="post-compaction")
        two_shot = _TwoShotLLM()

        # Compactor returns ONE replacement message: a summary SystemMessage
        summary_msg = SystemMessage(content="[summary]")
        compactor = _StubCompactor(replacement_messages=[summary_msg])
        graph = _StubGraph(state_messages=[HumanMessage(content="history")])
        graph_ref = [graph]

        from daemon.graph import create_agent_node
        # Stub injection slot is duck-compatible with InjectionSlot; bypass
        # the strict type-check with a cast (runtime contract matches).
        agent_node = create_agent_node(
            llm_with_tools=two_shot,
            system_prompt="sys",
            compactor=compactor,
            graph_ref=graph_ref,
            config={"configurable": {"thread_id": "iid-1"}},
            llm_config={"model": "test-model", "model_vision": None},
            retry_config={"transient_attempts": 1, "timeout_attempts": 1},
            llm_standard=None,
            injection_slot=slot,  # type: ignore[arg-type]
            live_hub=None,
        )

        result = await agent_node(
            {"messages": [HumanMessage(content="orig")]},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # Two LLM calls happened
        assert len(two_shot.calls) == 2

        # First call: original messages + injected
        first_attempt = two_shot.calls[0]
        assert any(m.content == "MUST-SURVIVE" for m in first_attempt)

        # Second call (after compaction): re-appended injection at the END
        second_attempt = two_shot.calls[1]
        # Last message must be the injected HumanMessage (post-compaction
        # re-append, preserving the order: [System, summary, injected]).
        assert isinstance(second_attempt[-1], HumanMessage)
        assert second_attempt[-1].content == "MUST-SURVIVE"
        assert (second_attempt[-1].additional_kwargs or {}).get("injected_message") is True

        # C2: result still has BOTH messages (so the injected message
        # is persisted via add_messages reducer)
        assert len(result["messages"]) == 2
        assert isinstance(result["messages"][0], HumanMessage)
        assert result["messages"][0].content == "MUST-SURVIVE"
        assert isinstance(result["messages"][1], AIMessage)


# ---------------------------------------------------------------------------
# SSE placeholder (Phase 1)
# ---------------------------------------------------------------------------


class TestSSEPlaceholder:
    """The live_hub parameter is wired in Phase 1 so Phase 2 can plug in
    the actual ``stream_message`` call without touching the closure. The
    Phase 1 path must NOT fail when ``live_hub`` is provided — it only
    logs a structural placeholder."""

    @pytest.mark.asyncio
    async def test_live_hub_does_not_break_agent_node(self):
        """live_hub=non-None must not raise — Phase 1 stub logs only."""
        live_hub = MagicMock()
        slot = _StubInjectionSlot(initial={"iid-1": [{"content": "X", "timestamp": "ts"}]})
        agent_node, _ = _make_agent(injection_slot=slot, live_hub=live_hub)

        # Must not raise
        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        assert len(result["messages"]) == 2

    @pytest.mark.asyncio
    async def test_live_hub_none_is_safe(self):
        """live_hub=None is the default — must not raise."""
        slot = _StubInjectionSlot(initial={"iid-1": [{"content": "X", "timestamp": "ts"}]})
        agent_node, _ = _make_agent(injection_slot=slot, live_hub=None)

        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        assert len(result["messages"]) == 2