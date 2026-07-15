"""Unit tests for context-usage event emission.

Covers:
- LiveEventHub.stream_context_usage emits a well-formed event and clamps
  percent to [0, 100] (defends against a misbehaving estimator).
- InstanceMessaging._emit_context_usage dedupes successive identical
  snapshots so a long streaming response doesn't spam the SSE channel.
- InstanceMessaging.emit_context_usage_for_instance loads from the
  instance message store and forwards to the hub.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import HumanMessage

from daemon.services.live_event_hub import LiveEventHub


# =============================================================================
# LiveEventHub.stream_context_usage
# =============================================================================


class TestStreamContextUsage:
    """Tests for LiveEventHub.stream_context_usage."""

    @pytest.mark.asyncio
    async def test_emits_well_formed_event(self):
        """Event payload must contain tokens, window, percent, model_name."""
        hub = LiveEventHub()
        received: list[dict] = []

        # Register a queue and capture what the hub pushes onto it.
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-1", queue)

        # Patch the internal broadcast to capture the dict directly so the
        # test doesn't depend on consumer timing.
        original_stream = hub._stream_to_connections

        async def capture(instance_id, event):
            received.append(event)
            await original_stream(instance_id, event)

        hub._stream_to_connections = capture
        await hub.stream_context_usage(
            instance_id="inst-1", tokens=64000, context_window=128000, model_name="gpt-4o"
        )

        assert len(received) == 1
        event = received[0]
        assert event["event_type"] == "context_usage"
        assert event["instance_id"] == "inst-1"
        assert event["tokens"] == 64000
        assert event["context_window"] == 128000
        assert event["percent"] == 50.0
        assert event["model_name"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_clamps_percent_at_100(self):
        """Tokens exceeding the window must clamp to 100%, not 137%."""
        hub = LiveEventHub()
        received: list[dict] = []

        async def capture(instance_id, event):
            received.append(event)

        hub._stream_to_connections = capture
        await hub.stream_context_usage(
            instance_id="inst-1", tokens=200000, context_window=128000, model_name="gpt-4o"
        )
        assert received[0]["percent"] == 100.0

    @pytest.mark.asyncio
    async def test_clamps_percent_at_zero(self):
        """Negative tokens (defensive) must clamp to 0%."""
        hub = LiveEventHub()
        received: list[dict] = []

        async def capture(instance_id, event):
            received.append(event)

        hub._stream_to_connections = capture
        await hub.stream_context_usage(
            instance_id="inst-1", tokens=-100, context_window=128000, model_name="gpt-4o"
        )
        assert received[0]["percent"] == 0.0

    @pytest.mark.asyncio
    async def test_zero_context_window_yields_zero_percent(self):
        """A 0 context window (misconfig) must not divide by zero."""
        hub = LiveEventHub()
        received: list[dict] = []

        async def capture(instance_id, event):
            received.append(event)

        hub._stream_to_connections = capture
        await hub.stream_context_usage(
            instance_id="inst-1", tokens=100, context_window=0, model_name="gpt-4o"
        )
        assert received[0]["percent"] == 0.0


# =============================================================================
# InstanceMessaging dedup behavior
# =============================================================================


class TestEmitContextUsageDedup:
    """Tests that _emit_context_usage suppresses redundant snapshots."""

    def _make_service(self, system_tokens: int = 0):
        from daemon.services.instance_messaging import InstanceMessagingService

        service = InstanceMessagingService.__new__(InstanceMessagingService)
        # Wire up just enough state for the helper. _config is a property
        # backed by self._manager.config, so we set the manager instead.
        service._manager = MagicMock()
        service._manager._last_context_usage = {}
        service._manager._live_hub = MagicMock()
        service._manager._live_hub.stream_context_usage = AsyncMock()
        service._manager.config = MagicMock()
        service._manager.config.llm.model = "gpt-4o"
        service._manager.config.compaction = MagicMock()
        service._get_system_prompt_tokens = AsyncMock(return_value=system_tokens)
        return service

    @pytest.mark.asyncio
    async def test_duplicate_tokens_are_deduped(self):
        """Two calls with the same token count must result in one broadcast."""
        service = self._make_service()

        messages = [HumanMessage(content="hello", id="m1")]

        await service._emit_context_usage("inst-1", messages)
        await service._emit_context_usage("inst-1", messages)

        assert service._manager._live_hub.stream_context_usage.await_count == 1

    @pytest.mark.asyncio
    async def test_increasing_tokens_broadcast_each_change(self):
        """Token-count growth must result in N broadcasts, not 1."""
        service = self._make_service()

        # Three messages, each larger than the last so the count grows.
        msgs = [
            [HumanMessage(content="a", id="m1")],
            [HumanMessage(content="a" * 50, id="m1"), HumanMessage(content="b" * 50, id="m2")],
            [HumanMessage(content="x" * 200, id="m3")],
        ]
        for batch in msgs:
            await service._emit_context_usage("inst-1", batch)

        assert service._manager._live_hub.stream_context_usage.await_count == 3

    @pytest.mark.asyncio
    async def test_compute_returns_none_on_error_is_silent(self):
        """A misbehaving config must not raise; it should just skip the emit."""
        service = self._make_service()
        # Force an exception inside _compute by making model attribute
        # access blow up. _compute_context_usage wraps everything in
        # try/except and returns None on any error.
        type(service._manager.config).llm = property(
            lambda _self: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        await service._emit_context_usage("inst-1", [HumanMessage(content="hi")])
        service._manager._live_hub.stream_context_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_true_bypasses_dedup(self):
        """force=True must always broadcast, even if the token count is unchanged."""
        service = self._make_service()

        messages = [HumanMessage(content="hello", id="m1")]
        await service._emit_context_usage("inst-1", messages)
        # Second call with the same messages but force=True should still
        # emit — this is what the SSE connect handler relies on so a
        # freshly-connected client always gets a snapshot.
        await service._emit_context_usage("inst-1", messages, force=True)

        assert service._manager._live_hub.stream_context_usage.await_count == 2


# =============================================================================
# emit_context_usage_for_instance
# =============================================================================


class TestEmitContextUsageForInstance:
    """The public wrapper used by the SSE connect handler.

    Reads raw LangChain ``BaseMessage`` objects directly from the checkpointer
    state so token counts on initial page load match what the SSE update path
    computes (no lossy ``serialize_message`` round-trip that would skip
    ``ToolMessage`` entries, strip thinking content, or rewrite tool-call arg
    keys).
    """

    def _make_service(self):
        from daemon.services.instance_messaging import InstanceMessagingService

        service = InstanceMessagingService.__new__(InstanceMessagingService)
        service._manager = MagicMock()
        service._manager._last_context_usage = {}
        service._manager._live_hub = MagicMock()
        service._manager._live_hub.stream_context_usage = AsyncMock()
        service._manager.config = MagicMock()
        service._manager.config.llm.model = "gpt-4o"
        service._manager.config.compaction = MagicMock()
        service._manager.get_instance = AsyncMock(return_value=object())
        # Adapter with a raw_saver that returns the checkpoint state.
        raw_saver = MagicMock()
        adapter = MagicMock()
        adapter.raw_saver = raw_saver
        service._manager._checkpointer = adapter
        service._raw_saver = raw_saver
        service._get_system_prompt_tokens = AsyncMock(return_value=0)
        return service

    @pytest.mark.asyncio
    async def test_loads_messages_and_emits(self):
        """Reads raw checkpoint messages via saver.aget and emits context usage."""
        from langchain_core.messages import AIMessage, HumanMessage

        service = self._make_service()

        # Raw state from the checkpointer (matches aget() return shape).
        raw_messages = [
            HumanMessage(content="hello", id="m1"),
            AIMessage(content="hi there", id="m2"),
        ]
        state = {"channel_values": {"messages": raw_messages}}
        service._raw_saver.aget = AsyncMock(return_value=state)

        await service.emit_context_usage_for_instance("inst-1")

        # saver.aget was called with the thread_id config the SSE path uses.
        service._raw_saver.aget.assert_awaited_once_with(
            {"configurable": {"thread_id": "inst-1"}}
        )
        # Should have produced exactly one broadcast with non-zero tokens.
        service._manager._live_hub.stream_context_usage.assert_awaited_once()
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["instance_id"] == "inst-1"
        assert kwargs["model_name"] == "gpt-4o"
        assert kwargs["tokens"] > 0
        assert kwargs["context_window"] > 0

    @pytest.mark.asyncio
    async def test_includes_tool_messages_in_token_count(self):
        """ToolMessages from the checkpoint state must contribute to tokens.

        Regression: previously the path went through ``get_messages`` which
        strips ``ToolMessage`` entries (``daemon/persistence.py``), causing
        the initial-load token count to be artificially low compared to the
        SSE update path.
        """
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        service = self._make_service()

        raw_messages = [
            HumanMessage(content="run ls", id="m1"),
            AIMessage(
                content="",
                id="m2",
                tool_calls=[{"id": "tc-1", "name": "ls", "args": {"path": "/"}}],
            ),
            ToolMessage(
                content="file1.txt\nfile2.txt",
                tool_call_id="tc-1",
                name="ls",
                id="m3",
            ),
        ]
        service._raw_saver.aget = AsyncMock(
            return_value={"channel_values": {"messages": raw_messages}}
        )

        await service.emit_context_usage_for_instance("inst-1")
        service._manager._live_hub.stream_context_usage.assert_awaited_once()

        tokens = service._manager._live_hub.stream_context_usage.await_args.kwargs["tokens"]
        assert tokens > 0

        # Compare against the same message list without the ToolMessage —
        # tokens must be strictly higher when ToolMessage is included.
        service._manager._live_hub.stream_context_usage.reset_mock()
        service._manager._last_context_usage.pop("inst-1", None)
        service._raw_saver.aget = AsyncMock(
            return_value={
                "channel_values": {"messages": raw_messages[:2]},  # no ToolMessage
            }
        )
        await service.emit_context_usage_for_instance("inst-1")
        tokens_without_tool = service._manager._live_hub.stream_context_usage.await_args.kwargs["tokens"]
        assert tokens > tokens_without_tool, (
            f"ToolMessage should add tokens: with={tokens}, without={tokens_without_tool}"
        )

    @pytest.mark.asyncio
    async def test_silent_on_checkpointer_failure(self):
        """A checkpointer failure must not raise — it must be swallowed silently."""
        service = self._make_service()

        async def boom(_config):
            raise RuntimeError("checkpoint store down")

        service._raw_saver.aget = boom
        # Must not raise.
        await service.emit_context_usage_for_instance("inst-1")
        service._manager._live_hub.stream_context_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_silent_on_instance_lookup_failure(self):
        """A missing instance must not poke the checkpointer."""
        service = self._make_service()

        async def boom(_id):
            raise KeyError("not found")

        service._manager.get_instance = boom
        # Must not raise and must not call the checkpointer.
        await service.emit_context_usage_for_instance("inst-1")
        # _raw_saver.aget was never created as AsyncMock by default, so
        # assert the underlying mock was never called instead.
        service._raw_saver.aget.assert_not_called()
        service._manager._live_hub.stream_context_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_silent_when_no_checkpointer(self):
        """A missing checkpointer (early startup) must be a no-op."""
        service = self._make_service()
        service._manager._checkpointer = None

        await service.emit_context_usage_for_instance("inst-1")
        service._manager._live_hub.stream_context_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_state_still_emits_zero_tokens(self):
        """A checkpointer with no state must still broadcast (force=True)."""
        service = self._make_service()
        service._raw_saver.aget = AsyncMock(return_value=None)

        await service.emit_context_usage_for_instance("inst-1")
        # The force=True path means we still broadcast even if tokens == 0.
        service._manager._live_hub.stream_context_usage.assert_awaited_once()
        assert service._manager._live_hub.stream_context_usage.await_args.kwargs["tokens"] == 0
