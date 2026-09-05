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
import logging
import uuid
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
    message_tap_slot: Any | None = None,
    report_injection_slot: Any | None = None,
):
    """Build a fresh agent_node for a test, bypassing build_instance_graph.

    ``message_tap_slot`` threads the :class:`MessageTapSlot` handle into
    the ``create_agent_node`` closure so tests can assert against the
    ``message_metadata`` side-table writes the F2 single-return site
    fires (``daemon/graph.py:4196-4209``). ``report_injection_slot``
    is the duck-typed ``drain(instance_id) -> list[dict]`` handle used
    by the child-report pre-LLM drain (``daemon/graph.py:3642-3693``).
    """
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
        report_injection_slot=report_injection_slot,
        live_hub=live_hub,
        message_tap_slot=message_tap_slot,
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
    async def test_injection_queue_cleared_after_consumption(self):
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

    @pytest.mark.asyncio
    async def test_multi_entry_injection_re_appended_after_reactive_compaction(self):
        """Phase 3: multiple pending injections must ALL survive reactive
        compaction and be re-appended in FIFO order before the LLM retry."""
        from daemon.llm_error_classifier import ContextLengthExceededError

        class _StubContextLengthError(ContextLengthExceededError):
            def __init__(self):
                Exception.__init__(self, "stub context length error")
                self.original_error = None
                self.model = "test-model"

        markers = ["MUST-SURVIVE-1", "MUST-SURVIVE-2", "MUST-SURVIVE-3"]
        slot = _StubInjectionSlot()
        slot.set_many("iid-1", markers)

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

        summary_msg = SystemMessage(content="[summary]")
        compactor = _StubCompactor(replacement_messages=[summary_msg])
        graph = _StubGraph(state_messages=[HumanMessage(content="history")])
        graph_ref = [graph]

        from daemon.graph import create_agent_node
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

        # Second call (after compaction): all 3 injected messages at the END,
        # in FIFO order, each marked as injected_message.
        second_attempt = two_shot.calls[1]
        tail = second_attempt[-3:]
        assert all(isinstance(m, HumanMessage) for m in tail)
        assert [m.content for m in tail] == markers
        assert all(
            (m.additional_kwargs or {}).get("injected_message") is True for m in tail
        )

        # Result persists all 3 injected + 1 response
        assert len(result["messages"]) == 4
        result_contents = [m.content for m in result["messages"][:3]]
        assert result_contents == markers
        assert isinstance(result["messages"][3], AIMessage)


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


# ---------------------------------------------------------------------------
# Quick-win #1 (S scope) — provenance ``source`` parameter for
# ``Manager.set_injection``. The FIFO entry's ``"source"`` key is
# propagated onto the drained ``HumanMessage.additional_kwargs["source"]``
# at the agent_node drain site. The default (``source`` not in the
# entry) keeps the message bytes byte-identical to the pre-quick-win
# shape (no ``"source"`` key added).
# ---------------------------------------------------------------------------


class TestInjectionSourceProvenance:
    """Quick-win #1: provenance ``source`` on the agent_node drain site.

    Verifies the three SPEC test cases (T1, T2, T3):

      * T1 — an entry carrying ``source="internal_agent:<id>"`` produces
        a drained ``HumanMessage`` whose
        ``additional_kwargs["source"] == "internal_agent:<id>"``.
      * T2 — an entry WITHOUT ``source`` produces a drained
        ``HumanMessage`` whose ``additional_kwargs`` does NOT contain a
        ``"source"`` key (byte-identical to pre-quick-win).
      * T3 — the drain INFO log surfaces the source value when present.
    """

    @pytest.mark.asyncio
    async def test_t1_source_in_entry_propagates_to_drained_human_message(self):
        """T1: source=``"internal_agent:<id>"`` on the FIFO entry lands
        on ``HumanMessage.additional_kwargs["source"]`` after the
        agent_node consumes the slot. Also verifies the message is
        returned via C2 (persisted in the checkpoint) and is what the
        LLM was called with.
        """
        src = "internal_agent:caller-uuid-1"
        slot = _StubInjectionSlot(initial={
            "iid-1": [
                {
                    "content": "hello from caller",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "source": src,
                },
            ],
        })
        agent_node, llm = _make_agent(injection_slot=slot)

        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # The drained message is appended to the LLM input and is the
        # first message in the C2 return list (C2 persists the full
        # inbox via add_messages).
        sent_to_llm = llm.calls[0]
        # LLM input is [System, injected HumanMessage]
        assert isinstance(sent_to_llm[1], HumanMessage)
        assert sent_to_llm[1].content == "hello from caller"
        # The provenance marker is carried on additional_kwargs.
        assert (sent_to_llm[1].additional_kwargs or {}).get("source") == src
        # The legacy injected_message flag is still set.
        assert (sent_to_llm[1].additional_kwargs or {}).get("injected_message") is True

        # C2 return: same message persisted for the checkpoint.
        assert len(result["messages"]) == 2
        persisted = result["messages"][0]
        assert isinstance(persisted, HumanMessage)
        assert (persisted.additional_kwargs or {}).get("source") == src

    @pytest.mark.asyncio
    async def test_t2_no_source_in_entry_drains_with_no_source_key(self):
        """T2: default (no source on the entry) drains a message with NO
        ``"source"`` key in ``additional_kwargs`` — byte-identical to
        the pre-quick-win shape. Asserts the exact
        ``additional_kwargs`` dict so any inadvertent ``"source"`` key
        addition (even with value ``None``) is caught.
        """
        slot = _StubInjectionSlot(initial={
            "iid-1": [{"content": "plain user msg", "timestamp": "ts"}],
        })
        agent_node, llm = _make_agent(injection_slot=slot)

        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        sent_to_llm = llm.calls[0]
        # The injected message is appended to the LLM input.
        assert isinstance(sent_to_llm[1], HumanMessage)
        assert sent_to_llm[1].content == "plain user msg"

        # Byte-identical back-compat: ``additional_kwargs`` has exactly
        # one key (``"injected_message"``) — no ``"source"`` key, even
        # with value ``None``.
        assert sent_to_llm[1].additional_kwargs == {"injected_message": True}
        # Explicit assertions to make the back-compat intent obvious if
        # someone later refactors the dict literal to include a default
        # ``"source": None``.
        assert "source" not in (sent_to_llm[1].additional_kwargs or {})

        # C2 return carries the same byte-identical message.
        persisted = result["messages"][0]
        assert isinstance(persisted, HumanMessage)
        assert persisted.additional_kwargs == {"injected_message": True}
        assert "source" not in (persisted.additional_kwargs or {})

    @pytest.mark.asyncio
    async def test_t3_enhanced_drain_log_includes_source_value(self, caplog):
        """T3: when an entry carries ``source=...``, the drain INFO log
        surfaces it as a `` source=<value>`` suffix. Uses pytest's
        built-in ``caplog`` fixture (project's standard log-capture
        pattern — see ``tests/unit/tools/test_instance_tools.py``).
        """
        src = "internal_agent:caller-uuid-2"
        slot = _StubInjectionSlot(initial={
            "iid-1": [
                {
                    "content": "msg with provenance",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "source": src,
                },
            ],
        })
        agent_node, _ = _make_agent(injection_slot=slot)

        with caplog.at_level(logging.INFO, logger="daemon.graph"):
            await agent_node(
                {"messages": []},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        pull_records = [
            r for r in caplog.records
            if r.name == "daemon.graph"
            and "[Injection] Pulled" in r.getMessage()
        ]
        assert len(pull_records) == 1, (
            f"Expected exactly one '[Injection] Pulled' log line; got "
            f"{[r.getMessage() for r in pull_records]}"
        )
        assert src in pull_records[0].getMessage()
        # The suffix shape is `` source=<value>`` (single space prefix).
        assert f" source={src}" in pull_records[0].getMessage()

    @pytest.mark.asyncio
    async def test_t2_log_line_is_byte_identical_when_no_source(self, caplog):
        """T2 companion: the drain INFO log is byte-identical to the
        pre-quick-win shape when no entry carries ``source`` (no
        `` source=...`` suffix appended). Locks the back-compat log
        contract independently from the message-bytes assertion in T2.
        """
        slot = _StubInjectionSlot(initial={
            "iid-1": [{"content": "plain user msg", "timestamp": "ts"}],
        })
        agent_node, _ = _make_agent(injection_slot=slot)

        with caplog.at_level(logging.INFO, logger="daemon.graph"):
            await agent_node(
                {"messages": []},
                config={"configurable": {"thread_id": "iid-1"}},
            )

        pull_records = [
            r for r in caplog.records
            if r.name == "daemon.graph"
            and "[Injection] Pulled" in r.getMessage()
        ]
        assert len(pull_records) == 1, (
            f"Expected exactly one '[Injection] Pulled' log line; got "
            f"{[r.getMessage() for r in pull_records]}"
        )
        msg = pull_records[0].getMessage()
        # No `` source=`` suffix when no entry carries one.
        assert "source=" not in msg


# ---------------------------------------------------------------------------
# message-display-latency Phase 1 — drain builds HumanMessage with the
# FIFO entry's optional ``echo_id`` as ``id`` (id field ONLY; the
# ``additional_kwargs`` byte-identical contract is untouched).
# ---------------------------------------------------------------------------


class TestDrainEchoId:
    """message-display-latency Phase 1: the drained ``HumanMessage``
    carries ``id = entry.get("echo_id")``.

    * Entry WITH ``echo_id`` → ``HumanMessage.id == echo_id`` and
      ``additional_kwargs == {"injected_message": True}`` (byte-identical
      kwargs contract — the id lives on the message, NOT in kwargs).
    * Entry WITHOUT ``echo_id`` (agent-tool ``instance.py:2811`` /
      ``job_inject`` ``job_queue.py:1868``) — MAJ-1 fix: the drain
      mints a uuid4 ONCE at HumanMessage construction time so the id
      is stable across SSE re-emit + GET /messages + reconnect refetch
      (the FE union-by-id merge can now collapse duplicates). Pre-MAJ-1
      these entries drained with ``id=None`` and ``serialize_message``
      re-minted a fresh uuid4 per call, producing duplicate bubbles on
      reconnect refetch.
    * Coexistence with the quick-win #1 ``source`` kwarg is per-entry.
    """

    @pytest.mark.asyncio
    async def test_echo_id_entry_sets_human_message_id(self):
        slot = _StubInjectionSlot(initial={
            "iid-1": [{
                "content": "stable id message",
                "timestamp": "2026-08-30T00:00:00+00:00",
                "echo_id": "aabbccdd-1122-4333-8444-556677889900",
            }],
        })
        agent_node, llm = _make_agent(injection_slot=slot)

        result = await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        # What the LLM was called with: last message is the injected HM.
        drained = llm.calls[0][-1]
        assert isinstance(drained, HumanMessage)
        assert drained.id == "aabbccdd-1122-4333-8444-556677889900"
        assert drained.content == "stable id message"
        # additional_kwargs byte-identical contract (id is NOT smuggled
        # into kwargs).
        assert drained.additional_kwargs == {"injected_message": True}

        # C2 persistence: the returned messages carry the same id — this
        # is what the checkpoint commit stores for GET /messages.
        persisted = [m for m in result["messages"] if isinstance(m, HumanMessage)]
        assert persisted[0].id == "aabbccdd-1122-4333-8444-556677889900"

    @pytest.mark.asyncio
    async def test_no_echo_id_entry_mints_uuid_in_drain(self):
        """MAJ-1 fix — tool-path id stability: entry without ``echo_id``
        mints a uuid4 ONCE in the drain loop. The minted id is BOTH the
        HumanMessage.id (so the checkpoint + GET /messages surface a
        stable id) AND the id ``serialize_message`` reuses on the SSE
        re-emit (so reconnect refetch + FE merge collapse duplicates).

        Pre-MAJ-1: ``id is None`` → ``serialize_message`` minted a fresh
        uuid at every call → duplicates on refetch.
        Post-MAJ-1: id is a uuid4 minted once at HumanMessage construction;
        re-emit message_id == HumanMessage.id == id on subsequent reads.
        """
        slot = _StubInjectionSlot(initial={
            "iid-1": [{"content": "tool msg", "timestamp": "ts"}],
        })
        agent_node, llm = _make_agent(injection_slot=slot)

        await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        drained = llm.calls[0][-1]
        assert isinstance(drained, HumanMessage)
        # MAJ-1: id is no longer None for echo_id-less entries; it's a
        # uuid4 minted at drain time so the id is stable across the
        # checkpoint + GET /messages + SSE re-emit.
        assert drained.id is not None
        parsed = uuid.UUID(drained.id)  # raises if not a UUID
        assert parsed.version == 4
        # additional_kwargs byte-identical contract preserved.
        assert drained.additional_kwargs == {"injected_message": True}

    @pytest.mark.asyncio
    async def test_echo_id_and_source_coexist_per_entry(self):
        """A user-API entry (echo_id) and an agent-tool entry (source) in
        the same FIFO drain with their own per-entry fields."""
        slot = _StubInjectionSlot(initial={
            "iid-1": [
                {
                    "content": "from api",
                    "timestamp": "t1",
                    "echo_id": "11111111-2222-4333-8444-555555555555",
                },
                {
                    "content": "from tool",
                    "timestamp": "t2",
                    "source": "internal_agent:caller-1",
                },
            ],
        })
        agent_node, llm = _make_agent(injection_slot=slot)

        await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-1"}},
        )

        api_hm, tool_hm = llm.calls[0][-2], llm.calls[0][-1]
        assert api_hm.id == "11111111-2222-4333-8444-555555555555"
        assert api_hm.additional_kwargs == {"injected_message": True}
        # MAJ-1: tool-path entry (no echo_id) mints a uuid4 in the drain
        # loop so the id is stable across SSE re-emit + GET /messages —
        # NOT ``None`` (the pre-MAJ-1 shape that caused reconnect refetch
        # duplicate bubbles).
        assert tool_hm.id is not None
        assert uuid.UUID(tool_hm.id).version == 4
        assert tool_hm.additional_kwargs == {
            "injected_message": True,
            "source": "internal_agent:caller-1",
        }

    @pytest.mark.asyncio
    async def test_echo_id_entry_tap_writes_metadata_row(self):
        """Acceptance criterion (2) — tap-firing proof: the
        ``message_metadata`` side-table tap at the F2 single-return
        site (``daemon/graph.py:4196-4209``) MUST record a row for a
        FIFO entry that carries ``echo_id``, and the recorded id MUST
        equal the FIFO entry's ``echo_id`` (NOT a fresh uuid4 — that
        would be the seam-drain identity regression the MAJ-1 fix
        closed at the SSE layer).

        Extends ``test_echo_id_entry_sets_human_message_id`` (which
        asserted the LLM-bound HumanMessage.id): this test wires the
        ``MessageTapSlot`` into ``create_agent_node`` via the
        ``message_tap_slot`` parameter and asserts the side-table
        write fired. Without this assertion, a regression that
        mints a fresh uuid inside the tap (decoupled from the
        drain's id) would slip past the existing LLM/SSE assertions.
        """
        from daemon.services.message_tap import (
            MessageTapSlot,
            SOURCE_AGENT_NODE_RETURN,
        )

        echo_id = "aabbccdd-1122-4333-8444-556677889900"
        slot = _StubInjectionSlot(initial={
            "iid-tap-echo": [{
                "content": "stable id message",
                "timestamp": "2026-08-30T00:00:00+00:00",
                "echo_id": echo_id,
            }],
        })
        metadata_repo = MagicMock()
        metadata_repo.upsert_batch = MagicMock(return_value=1)
        tap = MessageTapSlot(metadata_repo, SOURCE_AGENT_NODE_RETURN)
        agent_node, _ = _make_agent(
            injection_slot=slot, message_tap_slot=tap,
        )

        await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-tap-echo"}},
        )

        # Exactly one upsert_batch, threaded with (thread_id, [(id, ts, None)]).
        metadata_repo.upsert_batch.assert_called_once()
        args, _ = metadata_repo.upsert_batch.call_args
        assert args[0] == "iid-tap-echo"
        items = args[1]
        assert isinstance(items, list) and len(items) >= 1
        ids = [mid for mid, _ts, _seq in items]
        # Identity contract: the tap records the drain's HumanMessage.id,
        # which is the FIFO entry's echo_id (NOT a fresh uuid).
        assert echo_id in ids
        # Truthful first-seen ts: a non-empty ISO string from
        # ``datetime.now(UTC).isoformat()`` inside MessageTapSlot —
        # not the empty-string sentinel and not None.
        for _mid, ts, seq in items:
            assert isinstance(ts, str) and len(ts) > 0
            assert seq is None

    @pytest.mark.asyncio
    async def test_no_echo_id_entry_tap_records_minted_uuid(self):
        """Acceptance criterion (2) — tap-firing proof for the
        tool-path (no ``echo_id``) seam-drain branch: the
        ``message_metadata`` side-table tap MUST record the drain's
        minted uuid4 (the same id the LLM-bound HumanMessage and
        the SSE re-emit carry — see MAJ-1). Pre-MAJ-1, the drain
        produced an id-less HumanMessage; the id-less shape
        silently fell to the state.ts fallback on read, which
        is exactly the bug the PR1 read-path fix is built around.

        Extends ``test_no_echo_id_entry_mints_uuid_in_drain``:
        the LLM/SSE layer is already pinned, but the side-table
        write was never proven.
        """
        from daemon.services.message_tap import (
            MessageTapSlot,
            SOURCE_AGENT_NODE_RETURN,
        )

        slot = _StubInjectionSlot(initial={
            "iid-tap-mint": [{"content": "tool msg", "timestamp": "ts"}],
        })
        metadata_repo = MagicMock()
        metadata_repo.upsert_batch = MagicMock(return_value=1)
        tap = MessageTapSlot(metadata_repo, SOURCE_AGENT_NODE_RETURN)
        agent_node, llm = _make_agent(
            injection_slot=slot, message_tap_slot=tap,
        )

        await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-tap-mint"}},
        )

        # The LLM-bound HumanMessage.id is the drain's minted uuid4.
        drained_hm = llm.calls[0][-1]
        assert isinstance(drained_hm, HumanMessage)
        assert drained_hm.id is not None
        minted = uuid.UUID(drained_hm.id)
        assert minted.version == 4

        # The tap must have written the SAME id (NOT a fresh uuid4
        # — that would be a regression that decouples the side-table
        # row from the SSE/LLM contract).
        metadata_repo.upsert_batch.assert_called_once()
        args, _ = metadata_repo.upsert_batch.call_args
        assert args[0] == "iid-tap-mint"
        items = args[1]
        assert isinstance(items, list) and len(items) >= 1
        ids = [mid for mid, _ts, _seq in items]
        assert drained_hm.id in ids
        for _mid, ts, seq in items:
            assert isinstance(ts, str) and len(ts) > 0
            assert seq is None


# ---------------------------------------------------------------------------
# Child-report injection — the DB-backed report drain at
# ``daemon/graph.py:3642-3693`` stamps a uuid4 id on each
# ``HumanMessage`` BEFORE the F2 single-return tap at :4196-4209 fires.
# The id-stamp change (the ``+id=str(uuid.uuid4())`` line at
# graph.py:3687-3692 in this branch) is the same correctness fix the
# MAJ-1 / seam-drain branches already carry; this class proves the
# side-table write also fires for child reports.
# ---------------------------------------------------------------------------


class _StubReportInjectionSlot:
    """Duck-typed handle for the child-report drain.

    The agent_node contract is only ``drain(instance_id) -> list[dict]``;
    each dict must carry at least ``content`` (the report text) and
    optionally ``child_instance_id`` (used for the
    ``internal_report:<iid>`` source provenance).
    """

    def __init__(self, reports: list[dict[str, str]]):
        self._reports = list(reports)
        self.drain_calls: list[str] = []

    def drain(self, instance_id: str) -> list[dict[str, str]]:
        self.drain_calls.append(instance_id)
        # Drain is single-shot: the production ``ReportInjectionSlot``
        # atomically claims rows PENDING→INJECTED; the test surrogate
        # just returns the prepared list and empties itself, mirroring
        # the production contract.
        drained, self._reports = self._reports, []
        return drained


class TestChildReportTapFires:
    """Child-report pre-LLM drain writes a ``message_metadata`` row.

    The pre-existing report-injection tests (e.g. ``test_message_
    metadata_liveness_round_trip`` in
    ``tests/unit/repositories/test_message_tap_to_repo_liveness.py``)
    cover the in-memory repo, but no test in the corpus pins that the
    ``agent_node`` closure at ``daemon/graph.py:3642-3693`` actually
    gets the row through the F2 single-return tap. Without this, a
    regression that drops the report's id (returns to pre-fix
    id-less ``HumanMessage``) would slip past the existing
    LLM/SSE/path tests.

    The id-stamp at ``daemon/graph.py:3687-3692`` (the diff
    ``+id=str(uuid.uuid4())`` on the child-report branch) is the
    correctness hinge: the id is stamped BEFORE the F2 tap, so the
    tap records the same id the LLM/SSE/path surfaces. Pre-fix
    the report was id-less and the side-table row would either
    carry an empty id (broken PK) or be skipped entirely.
    """

    @pytest.mark.asyncio
    async def test_child_report_drain_writes_metadata_row(self):
        from daemon.services.message_tap import (
            MessageTapSlot,
            SOURCE_AGENT_NODE_RETURN,
        )

        report_slot = _StubReportInjectionSlot(reports=[{
            "content": "child finished with answer X",
            "child_instance_id": "child-iid-1",
            "report_message_id": "child-msg-1",
        }])
        metadata_repo = MagicMock()
        metadata_repo.upsert_batch = MagicMock(return_value=1)
        tap = MessageTapSlot(metadata_repo, SOURCE_AGENT_NODE_RETURN)
        agent_node, llm = _make_agent(
            report_injection_slot=report_slot,
            message_tap_slot=tap,
        )

        await agent_node(
            {"messages": []},
            config={"configurable": {"thread_id": "iid-parent"}},
        )

        # Drain ran for the parent instance.
        assert report_slot.drain_calls == ["iid-parent"]

        # The LLM saw a HumanMessage carrying the report content AND
        # the report's stamped uuid id (NOT None — the pre-fix shape).
        llm_messages = llm.calls[0]
        report_hm = next(
            m for m in llm_messages
            if isinstance(m, HumanMessage) and "child finished" in m.content
        )
        assert report_hm.id is not None
        report_id = uuid.UUID(report_hm.id)
        assert report_id.version == 4

        # Tap fired: exactly one upsert_batch on the parent's thread_id.
        metadata_repo.upsert_batch.assert_called_once()
        args, _ = metadata_repo.upsert_batch.call_args
        assert args[0] == "iid-parent"
        items = args[1]
        assert isinstance(items, list) and len(items) >= 1
        ids = [mid for mid, _ts, _seq in items]
        # The stamped id (NOT a fresh uuid4) is the row the side-table
        # records — this is the LLM/SSE/side-table alignment the
        # report-injection id-stamp at graph.py:3687-3692 closed.
        assert report_hm.id in ids
        for _mid, ts, seq in items:
            assert isinstance(ts, str) and len(ts) > 0
            assert seq is None

