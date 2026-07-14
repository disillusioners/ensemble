"""Unit tests for the injection SSE event contract (Phase 2 / Task 1, W5).

The SSE event contract is shared between three call sites:

    1. ``daemon/routers/messages.py`` — emits ``injection_pending`` (and
       ``injection_cleared`` on replacement) when ``send_message`` accepts
       a message targeting a RUNNING / WAITING_CHILDREN instance.
    2. ``daemon/graph.py`` — emits ``injection_consumed`` when the
       agent_node pulls and clears a pending slot from the LLM turn.
    3. ``daemon/services/instance_lifecycle.py`` — emits
       ``injection_cleared`` after a pause cascade clears a pending slot.

All three call sites reuse ``LiveEventHub.stream_message`` with a custom
``event_type`` (W5 contract — no new method on the hub). The tests in
this file verify:

    * ``stream_message(instance_id, message, event_type=...)`` accepts
      the custom event_type and serializes the payload correctly.
    * The agent_node consumption site calls stream_message with
      ``event_type="injection_consumed"`` after clearing the slot.
    * The pause cascade site calls stream_message with
      ``event_type="injection_cleared"`` after clearing a pending slot.
    * The replacement path emits cleared-then-pending in order.
    * W5 enforcement: ``LiveEventHub`` does NOT have a
      ``stream_injection`` or similar bespoke method — only the existing
      ``stream_message`` is reused.

These tests intentionally avoid the LiveEventHub unit suite at
``tests/unit/test_live_event_hub.py`` (which focuses on the connection
registry mechanics) — instead, they target the integration surface
that Phase 3 (frontend) will subscribe to.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# W5 enforcement — LiveEventHub must NOT grow a new injection method
# ---------------------------------------------------------------------------


class TestNoNewHubMethod:
    """W5: Only ``stream_message`` is reused — no ``stream_injection`` etc."""

    def test_live_event_hub_does_not_define_stream_injection(self):
        """The hub surface is unchanged. ``stream_injection`` does not exist.

        Catches an accidental regression where a maintainer "helpfully"
        adds a bespoke ``stream_injection(instance_id, event_type, ...)``
        method. Phase 2's W5 contract is firm: reuse ``stream_message``
        with custom ``event_type`` so the SSE consumer (frontend) sees
        the same envelope shape as every other message event.
        """
        from daemon.services.live_event_hub import LiveEventHub

        assert not hasattr(LiveEventHub, "stream_injection"), (
            "W5 violation: LiveEventHub must NOT grow a new method for "
            "injection events. Reuse stream_message with custom event_type."
        )
        # Also check the obvious near-namesakes
        for forbidden in ("emit_injection", "broadcast_injection", "publish_injection"):
            assert not hasattr(LiveEventHub, forbidden), (
                f"W5 violation: LiveEventHub must NOT define {forbidden!r}. "
                f"Reuse stream_message(event_type=...) instead."
            )

    def test_stream_message_accepts_custom_event_type(self):
        """``stream_message`` accepts a custom ``event_type`` and routes it through.

        This is the load-bearing W5 capability — without this signature,
        Phase 2 would have no way to distinguish ``injection_pending``
        from a regular ``message`` event on the SSE consumer side.
        """
        import inspect

        from daemon.services.live_event_hub import LiveEventHub

        sig = inspect.signature(LiveEventHub.stream_message)
        assert "event_type" in sig.parameters, (
            "stream_message must accept an event_type parameter (W5 contract)"
        )
        # event_type is the existing parameter — default 'message'.
        param = sig.parameters["event_type"]
        assert param.default == "message", (
            f"event_type default should remain 'message' for backward "
            f"compatibility; got {param.default!r}"
        )


# ---------------------------------------------------------------------------
# Direct LiveEventHub shape verification
# ---------------------------------------------------------------------------


class TestStreamMessageShape:
    """``stream_message`` with custom event_type produces the right SSE envelope."""

    @pytest.mark.asyncio
    async def test_injection_pending_event_shape(self):
        """stream_message(..., event_type='injection_pending') emits the correct payload."""
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-1", queue)

        payload = {
            "instance_id": "inst-1",
            "event_type": "injection_pending",
            "content": "user msg",
            "timestamp": "2026-07-13T00:00:00+00:00",
        }
        await hub.stream_message("inst-1", message=payload, event_type="injection_pending")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["event_type"] == "injection_pending"
        assert event["instance_id"] == "inst-1"
        # stream_message wraps the payload under event["message"] (the
        # SSE envelope pattern; Phase 3 frontend reads content via
        # event.message.content).
        assert event["message"] == payload
        assert event["message"]["content"] == "user msg"
        assert event["message"]["timestamp"] == "2026-07-13T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_injection_consumed_event_shape(self):
        """stream_message(..., event_type='injection_consumed') emits the right shape."""
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-2", queue)

        payload = {
            "instance_id": "inst-2",
            "event_type": "injection_consumed",
            "content": "consumed msg",
            "timestamp": "2026-07-13T00:00:01+00:00",
        }
        await hub.stream_message("inst-2", message=payload, event_type="injection_consumed")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["event_type"] == "injection_consumed"
        assert event["message"]["content"] == "consumed msg"

    @pytest.mark.asyncio
    async def test_injection_cleared_event_shape(self):
        """stream_message(..., event_type='injection_cleared') emits the right shape."""
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-3", queue)

        payload = {
            "instance_id": "inst-3",
            "event_type": "injection_cleared",
            "content": "old msg",
            "timestamp": "2026-07-13T00:00:02+00:00",
        }
        await hub.stream_message("inst-3", message=payload, event_type="injection_cleared")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["event_type"] == "injection_cleared"
        assert event["message"]["content"] == "old msg"

    @pytest.mark.asyncio
    async def test_no_connections_drops_event_silently(self):
        """SSE fire-and-forget: no connections → event is silently dropped.

        Matches the existing ``LiveEventHub`` semantics. The injection
        path must not raise if no SSE listener is connected.
        """
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()  # No add_connection called
        # Should not raise.
        await hub.stream_message(
            "inst-x",
            message={"instance_id": "inst-x", "event_type": "injection_pending", "content": "x", "timestamp": "t"},
            event_type="injection_pending",
        )


# ---------------------------------------------------------------------------
# Agent node consumption site — Task 7
# ---------------------------------------------------------------------------


class TestAgentNodeConsumptionSSE:
    """The agent_node emits ``injection_consumed`` after clearing the slot.

    Wires up ``create_agent_node`` with a stub LLM and a stub injection_slot
    + live_hub, runs one agent turn, and asserts that ``stream_message``
    was called exactly once with ``event_type='injection_consumed'`` and
    the cleared content + timestamp in the payload.
    """

    def _make_agent(self, *, injection_slot: Any, live_hub: Any):
        """Build an agent_node with stub LLM and the supplied slot/hub handles."""
        from daemon.graph import create_agent_node

        class _StubLLM:
            def __init__(self):
                self.calls = []

            def invoke(self, messages):
                self.calls.append(list(messages))
                from langchain_core.messages import AIMessage
                return AIMessage(content="stubbed response")

        llm = _StubLLM()
        return create_agent_node(
            llm_with_tools=llm,
            system_prompt="you are a test",
            compactor=None,
            graph_ref=[None],
            config={"configurable": {"thread_id": "inst-1"}},
            llm_config={"model": "stub"},
            retry_config={"transient_attempts": 1, "timeout_attempts": 1},
            llm_standard=None,
            injection_slot=injection_slot,
            live_hub=live_hub,
        ), llm

    @pytest.mark.asyncio
    async def test_agent_node_emits_injection_consumed_after_clear(self):
        """Agent turn with a pending injection emits SSE after clearing the slot.

        Bug fix (injection-sse-echo-fix): the injection path now emits two
        SSE events at the consumption point, mirroring the normal
        ``send_message`` path so the frontend renders a user-bubble update
        even for injected messages:

            1. ``user_message`` (serialized HumanMessage carrying the
               injected ``content`` so the FE echoes it like a normal
               user turn).
            2. ``injection_consumed`` (W5 envelope — content + timestamp
               of the slot entry that was just cleared).

        Order matters: ``user_message`` MUST arrive before
        ``injection_consumed`` so the FE paints the user bubble before
        it removes the "pending" indicator.
        """
        from langchain_core.messages import AIMessage

        class _StubInjectionSlot:
            def __init__(self):
                self.cleared_entry = {
                    "content": "user pending msg",
                    "timestamp": "2026-07-13T00:00:00+00:00",
                }
                self.clear_called = False

            def get(self, instance_id):
                return self.cleared_entry

            def clear(self, instance_id):
                self.clear_called = True
                return self.cleared_entry

        slot = _StubInjectionSlot()
        hub = MagicMock()
        hub.stream_message = AsyncMock()

        agent_node, llm = self._make_agent(injection_slot=slot, live_hub=hub)

        # Run one agent turn. Use the LangGraph MessagesState schema.
        state = {"messages": []}
        result = await agent_node(state, config={"configurable": {"thread_id": "inst-1"}})

        # Slot was consumed
        assert slot.clear_called

        # Two SSE events fire at the consumption point: user_message
        # first (echoes the injected text to the FE) and
        # injection_consumed second (clears the pending indicator).
        assert hub.stream_message.await_count == 2
        calls = hub.stream_message.await_args_list

        # ---- Call 1: user_message — must carry the injected content ----
        first = calls[0]
        assert first.kwargs["event_type"] == "user_message"
        assert first.kwargs["instance_id"] == "inst-1"
        user_payload = first.kwargs["message"]
        assert user_payload["instance_id"] == "inst-1"
        assert user_payload["role"] == "user"
        assert user_payload["content"] == "user pending msg"

        # ---- Call 2: injection_consumed — slot entry echoed ----
        second = calls[1]
        assert second.kwargs["event_type"] == "injection_consumed"
        assert second.args[0] == "inst-1"
        consumed_payload = second.kwargs["message"]
        assert consumed_payload["event_type"] == "injection_consumed"
        assert consumed_payload["content"] == "user pending msg"
        assert consumed_payload["timestamp"] == "2026-07-13T00:00:00+00:00"
        assert consumed_payload["instance_id"] == "inst-1"

        # The return value still persists both messages (Phase 1 C2 contract)
        assert len(result["messages"]) == 2

    @pytest.mark.asyncio
    async def test_agent_node_does_not_emit_when_no_pending_injection(self):
        """Empty slot → no SSE event (the W5 contract is "emit only when meaningful")."""
        class _StubInjectionSlot:
            def get(self, instance_id):
                return None

            def clear(self, instance_id):
                return None

        slot = _StubInjectionSlot()
        hub = MagicMock()
        hub.stream_message = AsyncMock()

        agent_node, llm = self._make_agent(injection_slot=slot, live_hub=hub)

        state = {"messages": []}
        result = await agent_node(state, config={"configurable": {"thread_id": "inst-1"}})

        # No SSE event — the slot was empty.
        hub.stream_message.assert_not_called()
        # Return value is single message (Phase 1 C2 fallback).
        assert len(result["messages"]) == 1

    @pytest.mark.asyncio
    async def test_agent_node_swallows_sse_errors(self):
        """An SSE outage must NOT block the LLM turn.

        The LLM call is the critical path; SSE is best-effort and
        errors are logged + swallowed. This test simulates an SSE error
        and asserts the agent_node still returns the expected messages.
        """
        class _StubInjectionSlot:
            def __init__(self):
                self.cleared = {"content": "x", "timestamp": "t"}

            def get(self, instance_id):
                return self.cleared

            def clear(self, instance_id):
                return self.cleared

        slot = _StubInjectionSlot()
        hub = MagicMock()
        hub.stream_message = AsyncMock(side_effect=RuntimeError("SSE down"))

        agent_node, llm = self._make_agent(injection_slot=slot, live_hub=hub)

        state = {"messages": []}
        # Must not raise — SSE failures are swallowed in graph.py.
        result = await agent_node(state, config={"configurable": {"thread_id": "inst-1"}})

        # The agent_node still produced the injected HumanMessage + AI response
        assert len(result["messages"]) == 2


# ---------------------------------------------------------------------------
# Pause cascade clearing site — Task 8
# ---------------------------------------------------------------------------


class TestPauseCascadeClearedSSE:
    """The pause cascade emits ``injection_cleared`` for each cleared slot.

    Tests ``InstanceLifecycleService.pause_instance_cascade`` with a
    heavily-mocked manager: the DB write helper (``_pause_cascade_db_sync``)
    is mocked to return a fake ``_CascadeUpdateResult`` so the post-DB
    SSE emit loop runs against a controlled state.
    """

    def _build_manager_for_pause_test(
        self,
        *,
        tree_ids: list[str],
        per_node_meta: dict[str, dict[str, Any]],
        cleared_per_node: dict[str, dict[str, str] | None],
    ) -> tuple[MagicMock, Any, MagicMock]:
        """Build a mock manager that drives pause_instance_cascade through the SSE loop.

        Wires up:
            * ``_instance_repository`` — ``get_tree_root_id``,
              ``get_tree_ids``, ``get`` (per-node meta lookup).
            * ``_request_registry.cancel_by_instance`` — no-op.
            * ``_graph_tasks.pop`` — returns None (no live tasks).
            * ``release_context_usage_cache`` — no-op.
            * ``clear_injection`` — returns the configured
              ``cleared_per_node[node_id]``.
            * ``_pause_cascade_db_sync`` — returns a fake
              ``_CascadeUpdateResult`` so the post-DB SSE loop fires.
        """
        manager = MagicMock()
        # is_write_paused — irrelevant for pause; pin to False for safety.
        manager.is_write_paused = False

        repo = MagicMock()
        repo.get_tree_root_id = MagicMock(return_value=tree_ids[0] if tree_ids else "root-x")
        repo.get_tree_ids = MagicMock(return_value=tree_ids)

        def _get(node_id):
            meta_dict = per_node_meta.get(node_id)
            if meta_dict is None:
                return None
            m = MagicMock()
            m.status = meta_dict["status"]
            m.agent_id = meta_dict.get("agent_id", "test_agent")
            return m

        repo.get = MagicMock(side_effect=_get)
        manager._instance_repository = repo

        # Request registry / graph tasks — no-op for this test.
        manager._request_registry = MagicMock()
        manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
        manager._graph_tasks = {}
        manager.release_context_usage_cache = MagicMock()

        # clear_injection — per-node mapping.
        manager.clear_injection = MagicMock(side_effect=lambda nid: cleared_per_node.get(nid))

        # Live hub — capture every SSE call for assertion.
        hub = MagicMock()
        hub.stream_message = AsyncMock()
        hub.stream_status_change = AsyncMock()
        manager._live_hub = hub

        # _pause_cascade_db_sync — fake post-DB result.
        from daemon.services.instance_lifecycle import _CascadeUpdateResult

        async def _fake_pause_db_sync(*args, **kwargs):
            return _CascadeUpdateResult(
                updated_ids=list(tree_ids),
                skipped_ids=[],
                agent_ids_by_instance={
                    nid: per_node_meta[nid].get("agent_id", "test_agent")
                    for nid in tree_ids
                    if nid in per_node_meta
                },
            )

        return manager, _fake_pause_db_sync, hub

    @pytest.mark.asyncio
    async def test_pause_emits_injection_cleared_when_slot_was_populated(self):
        """pause_instance_cascade emits ``injection_cleared`` for each cleared slot."""
        manager, _fake_db_sync, hub = self._build_manager_for_pause_test(
            tree_ids=["inst-1"],
            per_node_meta={"inst-1": {"status": "running", "agent_id": "leader"}},
            cleared_per_node={
                "inst-1": {
                    "content": "old pending msg",
                    "timestamp": "2026-07-13T00:00:00+00:00",
                }
            },
        )
        # Bind the fake DB sync helper onto the service instance.
        manager._pause_cascade_db_sync = _fake_db_sync

        from daemon.services.instance_lifecycle import InstanceLifecycleService

        # Build the service with the mocks. The constructor expects
        # cancellation_service, events_service, job_queue_service — all
        # unused by the pause path, so we pass MagicMocks.
        svc = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            events_service=None,
            job_queue_service=None,
        )

        result = await svc.pause_instance_cascade("inst-1")

        # The status_change SSE fired (existing path)
        assert hub.stream_status_change.await_count == 1
        # The injection_cleared SSE fired (Phase 2 / Task 8)
        assert hub.stream_message.await_count == 1
        call = hub.stream_message.await_args
        assert call.kwargs["event_type"] == "injection_cleared"
        assert call.args[0] == "inst-1"
        payload = call.kwargs["message"]
        assert payload["event_type"] == "injection_cleared"
        assert payload["instance_id"] == "inst-1"
        assert payload["content"] == "old pending msg"
        assert payload["timestamp"] == "2026-07-13T00:00:00+00:00"

        assert "inst-1" in result["paused_ids"]

    @pytest.mark.asyncio
    async def test_pause_does_not_emit_cleared_when_slot_was_empty(self):
        """No pending injection → no ``injection_cleared`` SSE event."""
        manager, _fake_db_sync, hub = self._build_manager_for_pause_test(
            tree_ids=["inst-1"],
            per_node_meta={"inst-1": {"status": "running", "agent_id": "leader"}},
            cleared_per_node={"inst-1": None},  # Empty slot — nothing to emit
        )
        manager._pause_cascade_db_sync = _fake_db_sync

        from daemon.services.instance_lifecycle import InstanceLifecycleService

        svc = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            events_service=None,
            job_queue_service=None,
        )

        await svc.pause_instance_cascade("inst-1")

        # status_change fired (existing path)
        assert hub.stream_status_change.await_count == 1
        # No injection_cleared event because the slot was empty
        hub.stream_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_cascade_emits_cleared_per_node(self):
        """Multi-node cascade → one ``injection_cleared`` per node with a cleared slot."""
        manager, _fake_db_sync, hub = self._build_manager_for_pause_test(
            tree_ids=["inst-1", "inst-2", "inst-3"],
            per_node_meta={
                "inst-1": {"status": "running", "agent_id": "leader"},
                "inst-2": {"status": "running", "agent_id": "developer"},
                "inst-3": {"status": "running", "agent_id": "tester"},
            },
            cleared_per_node={
                "inst-1": {"content": "m1", "timestamp": "t1"},
                "inst-2": None,  # Empty — should NOT emit
                "inst-3": {"content": "m3", "timestamp": "t3"},
            },
        )
        manager._pause_cascade_db_sync = _fake_db_sync

        from daemon.services.instance_lifecycle import InstanceLifecycleService

        svc = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            events_service=None,
            job_queue_service=None,
        )

        await svc.pause_instance_cascade("inst-1")

        # Two injection_cleared events — inst-1 and inst-3. inst-2 was empty.
        cleared_calls = [
            c for c in hub.stream_message.await_args_list
            if c.kwargs["event_type"] == "injection_cleared"
        ]
        assert len(cleared_calls) == 2
        cleared_instance_ids = {c.args[0] for c in cleared_calls}
        assert cleared_instance_ids == {"inst-1", "inst-3"}


# ---------------------------------------------------------------------------
# Replacement — already covered in tests/test_injection_api.py. Here we
# verify the same contract holds when called directly on LiveEventHub
# (i.e., the hub itself does not enforce any ordering — the call site
# must call cleared BEFORE pending).
# ---------------------------------------------------------------------------


class TestReplacementOrdering:
    """Replacement call site emits cleared BEFORE pending (in order)."""

    @pytest.mark.asyncio
    async def test_cleared_then_pending_reaches_queue_in_order(self):
        """The SSE queue receives cleared first, then pending — order matters for FE.

        This test verifies that the W5 contract (call sites own the
        ordering) is honored when both events are emitted from the same
        hub. Listeners that subscribe mid-stream should never see
        pending-before-cleared, which would leave them with a stale
        pending state.
        """
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-1", queue)

        # Emit cleared first
        await hub.stream_message(
            "inst-1",
            message={
                "instance_id": "inst-1",
                "event_type": "injection_cleared",
                "content": "old",
                "timestamp": "t-old",
            },
            event_type="injection_cleared",
        )
        # Then pending
        await hub.stream_message(
            "inst-1",
            message={
                "instance_id": "inst-1",
                "event_type": "injection_pending",
                "content": "new",
                "timestamp": "t-new",
            },
            event_type="injection_pending",
        )

        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        second = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert first["event_type"] == "injection_cleared"
        assert first["message"]["content"] == "old"
        assert second["event_type"] == "injection_pending"
        assert second["message"]["content"] == "new"