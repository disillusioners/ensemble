"""TIER-A de-dup (row2-2a) — the context-usage path's cheap pre-filter.

The usage path counted the WHOLE history through tiktoken on EVERY
emit (every node update of a turn, and every SSE connect) BEFORE the
dedup comparison decided whether the event was worth broadcasting.

TIER-A moves a cheap "did the history change?" comparison AHEAD of the
count so an unchanged history skips the count. INVARIANT: the EMITTED
value is unchanged — only the recomputation of an unchanged value is
skipped.

The skip is only sound when the pre-filter PROVES the history is
unchanged, so the pre-filter compares the estimator's INPUTS (the
message count first, then a fingerprint of every field the estimator
reads). This module pins:

* an unchanged history ⇒ no re-count, same emitted value,
* a CHANGED history (same message COUNT, different content — the
  operator-note replacement path) ⇒ re-counted, new emitted value. A
  message-count-only proxy would silently emit a stale value here,
* ``force=True`` ⇒ still broadcasts, same emitted value.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import daemon.services.instance_messaging as messaging_module
from daemon.loader import estimate_messages_tokens


def _make_service(system_tokens: int = 0):
    from daemon.services.instance_messaging import InstanceMessagingService

    service = InstanceMessagingService.__new__(InstanceMessagingService)
    service._manager = MagicMock()
    service._manager._last_context_usage = {}
    service._manager._live_hub = MagicMock()
    service._manager._live_hub.stream_context_usage = AsyncMock()
    service._manager.config = MagicMock()
    service._manager.config.llm.model = "gpt-4o"
    service._manager.config.compaction = MagicMock()
    service._get_system_prompt_tokens = AsyncMock(return_value=system_tokens)
    return service


class _Counter:
    """Counts the calls to the token estimator."""

    def __init__(self):
        self.calls = 0

    def __call__(self, msgs):
        self.calls += 1
        return estimate_messages_tokens(msgs)


@pytest.fixture
def counted(monkeypatch):
    counter = _Counter()
    monkeypatch.setattr(
        messaging_module, "estimate_messages_tokens", counter,
    )
    return counter


class TestUsagePathDedup:
    """The emitted value never changes; only the work does."""

    @pytest.mark.asyncio
    async def test_unchanged_history_is_not_recounted(self, counted):
        """A second emit of the SAME history must not re-count.

        The broadcast is still suppressed (the dedup), and the value the
        first emit produced is unchanged.
        """
        service = _make_service()
        messages = [
            HumanMessage(content="hello", id="m1"),
            AIMessage(content="hi there", id="m2"),
        ]

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1, (
            "an unchanged history must not be re-counted "
            f"(counted {counted.calls}×)"
        )
        # The dedup still suppresses the duplicate broadcast.
        assert service._manager._live_hub.stream_context_usage.await_count == 1

    @pytest.mark.asyncio
    async def test_emitted_value_is_unchanged(self, counted):
        """The emitted token value is the pre-change value."""
        service = _make_service(system_tokens=100)
        messages = [
            HumanMessage(content="hello", id="m1"),
            AIMessage(content="hi there", id="m2"),
            ToolMessage(content="tool output", tool_call_id="c1", name="t", id="m3"),
        ]

        await service._emit_context_usage("inst-1", messages)

        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        expected = estimate_messages_tokens(messages) + 100
        assert kwargs["tokens"] == expected

    @pytest.mark.asyncio
    async def test_same_count_different_content_is_recounted(self, counted):
        """Same message COUNT, different content ⇒ re-count.

        This is the operator-note replacement path (a message is
        REPLACED in place, so the message count does not move but the
        token count does). A message-count-only proxy would emit a
        STALE value here — the fingerprint catches it.
        """
        service = _make_service()
        messages = [HumanMessage(content="a", id="m1")]

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1

        # Replace the message IN PLACE (same count, different content).
        messages[0] = HumanMessage(content="a much longer body", id="m1")
        await service._emit_context_usage("inst-1", messages)

        assert counted.calls == 2, (
            "a changed history must be re-counted even when the message "
            f"count is unchanged (counted {counted.calls}×)"
        )
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages)

    @pytest.mark.asyncio
    async def test_appended_message_is_recounted(self, counted):
        """An appended message changes the count ⇒ re-count + new value."""
        service = _make_service()
        messages = [HumanMessage(content="a", id="m1")]

        await service._emit_context_usage("inst-1", messages)
        messages.append(HumanMessage(content="b", id="m2"))
        await service._emit_context_usage("inst-1", messages)

        assert counted.calls == 2
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages)

    @pytest.mark.asyncio
    async def test_force_true_emits_without_recounting(self, counted):
        """force=True broadcasts even for an unchanged history.

        The SSE-connect path relies on it; the VALUE must be the same
        as the pre-change implementation.
        """
        service = _make_service(system_tokens=50)
        messages = [HumanMessage(content="hello", id="m1")]

        await service._emit_context_usage("inst-1", messages)
        await service._emit_context_usage("inst-1", messages, force=True)

        assert service._manager._live_hub.stream_context_usage.await_count == 2
        assert counted.calls == 1, (
            "force=True on an unchanged history must not re-count "
            f"(counted {counted.calls}×)"
        )
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages) + 50

    @pytest.mark.asyncio
    async def test_tool_call_fields_are_part_of_the_fingerprint(self, counted):
        """A changed tool-call payload must be detected.

        ``estimate_messages_tokens`` counts the tool-call name and
        arguments — the fingerprint must cover them, or a changed tool
        call would emit a stale value.
        """
        from langchain_core.messages.tool import ToolCall

        service = _make_service()
        tc = ToolCall(name="search", args={"q": "alpha"}, id="call-1")
        messages = [AIMessage(content="", tool_calls=[tc], id="m1")]

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1

        messages[0] = AIMessage(
            content="",
            tool_calls=[ToolCall(name="search", args={"q": "beta"}, id="call-1")],
            id="m1",
        )
        await service._emit_context_usage("inst-1", messages)

        assert counted.calls == 2, (
            "a changed tool-call payload must be re-counted "
            f"(counted {counted.calls}×)"
        )
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages)

    @pytest.mark.asyncio
    async def test_count_failure_stays_silent(self, monkeypatch):
        """A failing count must not raise — the emit is skipped."""
        service = _make_service()

        def boom(_msgs):
            raise RuntimeError("boom")

        monkeypatch.setattr(messaging_module, "estimate_messages_tokens", boom)
        await service._emit_context_usage("inst-1", [HumanMessage(content="hi")])
        service._manager._live_hub.stream_context_usage.assert_not_awaited()
