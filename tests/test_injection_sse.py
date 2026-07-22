"""Unit tests for the injection SSE event contract (Phase 3 / Task 1, W5).

The SSE event contract is shared between three call sites:

    1. ``daemon/routers/messages.py`` — emits ``injection_pending`` (with
       ``pending_count``) when ``send_message`` accepts a message
       targeting a RUNNING / WAITING_CHILDREN instance. Phase 3: no
       longer emits ``injection_cleared`` on replacement (we APPEND, not
       REPLACE).
    2. ``daemon/graph.py`` — emits one ``user_message`` per consumed
       pending entry AND one ``injection_consumed`` event (with
       ``pending_count``) when the agent_node pulls and clears the
       queue on its LLM turn.
    3. ``daemon/services/instance_lifecycle.py`` — emits
       ``injection_consumed`` after a pause/terminate cascade clears the
       queue. Phase 3 lifecycle: the new contract is
       ``injection_pending`` (per message) → ``injection_consumed``
       (once, for all). No ``injection_cleared`` events.

All three call sites reuse ``LiveEventHub.stream_message`` with a custom
``event_type`` (W5 contract — no new method on the hub). The tests in
this file verify:

    * ``stream_message(instance_id, message, event_type=...)`` accepts
      the custom event_type and serializes the payload correctly.
    * The agent_node consumption site calls stream_message with
      ``event_type="user_message"`` for each consumed entry and one
      ``event_type="injection_consumed"`` for the closure.
    * The pause cascade site calls stream_message with
      ``event_type="injection_consumed"`` after clearing the queue.
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
        """stream_message(..., event_type='injection_pending') emits the correct payload.

        Phase 3: ``injection_pending`` payloads include the ``pending_count``
        so the frontend can show a "N messages queued" indicator.
        """
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-1", queue)

        payload = {
            "instance_id": "inst-1",
            "event_type": "injection_pending",
            "content": "user msg",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "pending_count": 2,
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
        assert event["message"]["pending_count"] == 2

    @pytest.mark.asyncio
    async def test_injection_consumed_event_shape(self):
        """stream_message(..., event_type='injection_consumed') emits the right shape.

        Phase 3: includes the ``pending_count`` for the queue that was
        just consumed (so the FE can drop the pending indicator).
        """
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.add_connection("inst-2", queue)

        payload = {
            "instance_id": "inst-2",
            "event_type": "injection_consumed",
            "content": "consumed msg",
            "timestamp": "2026-07-13T00:00:01+00:00",
            "pending_count": 3,
        }
        await hub.stream_message("inst-2", message=payload, event_type="injection_consumed")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["event_type"] == "injection_consumed"
        assert event["message"]["content"] == "consumed msg"
        assert event["message"]["pending_count"] == 3

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
# Agent node consumption site — Task 7 (Phase 3: per-message + closure)
# ---------------------------------------------------------------------------


class TestAgentNodeConsumptionSSE:
    """The agent_node emits one ``user_message`` per consumed entry PLUS
    one ``injection_consumed`` for the whole queue.

    Wires up ``create_agent_node`` with a stub LLM and a stub injection_slot
    + live_hub, runs one agent turn, and asserts that ``stream_message``
    fires the expected SSE sequence.
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
        """Agent turn with a single-entry queue emits user_message + injection_consumed.

        Phase 3: each pending entry emits its own ``user_message`` echo so
        the FE renders a user bubble for each. Closing the lifecycle is
        ONE ``injection_consumed`` event (with ``pending_count``).
        """
        from langchain_core.messages import AIMessage

        class _StubInjectionSlot:
            """Phase 3: get returns a LIST of pending entries."""

            def __init__(self):
                self.cleared_entries = [
                    {"content": "user pending msg", "timestamp": "2026-07-13T00:00:00+00:00"},
                ]
                self.clear_called = False

            def get(self, instance_id):
                return list(self.cleared_entries)  # defensive copy

            def clear(self, instance_id):
                self.clear_called = True
                result = self.cleared_entries
                self.cleared_entries = []
                return result

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

        # ---- Call 2: injection_consumed — queue entry echoed ----
        second = calls[1]
        assert second.kwargs["event_type"] == "injection_consumed"
        assert second.args[0] == "inst-1"
        consumed_payload = second.kwargs["message"]
        assert consumed_payload["event_type"] == "injection_consumed"
        assert consumed_payload["content"] == "user pending msg"
        assert consumed_payload["timestamp"] == "2026-07-13T00:00:00+00:00"
        assert consumed_payload["instance_id"] == "inst-1"
        # Phase 3: pending_count is in the consumed payload
        assert consumed_payload["pending_count"] == 1

        # The return value still persists both messages (Phase 1 C2 contract)
        assert len(result["messages"]) == 2

    @pytest.mark.asyncio
    async def test_agent_node_emits_one_user_message_per_pending_entry(self):
        """Phase 3: a multi-entry queue emits one user_message per entry,
        FIFO order, then ONE closing injection_consumed.
        """
        from langchain_core.messages import AIMessage

        class _StubInjectionSlot:
            def __init__(self):
                self.cleared_entries = [
                    {"content": "first", "timestamp": "2026-07-13T00:00:00+00:00"},
                    {"content": "second", "timestamp": "2026-07-13T00:00:01+00:00"},
                    {"content": "third", "timestamp": "2026-07-13T00:00:02+00:00"},
                ]
                self.clear_called = False

            def get(self, instance_id):
                return list(self.cleared_entries)

            def clear(self, instance_id):
                self.clear_called = True
                result = self.cleared_entries
                self.cleared_entries = []
                return result

        slot = _StubInjectionSlot()
        hub = MagicMock()
        hub.stream_message = AsyncMock()

        agent_node, llm = self._make_agent(injection_slot=slot, live_hub=hub)

        state = {"messages": []}
        await agent_node(state, config={"configurable": {"thread_id": "inst-1"}})

        # 3 user_message + 1 injection_consumed = 4 SSE events
        assert hub.stream_message.await_count == 4
        calls = hub.stream_message.await_args_list

        # First three are user_message in FIFO order
        for i, expected_content in enumerate(["first", "second", "third"]):
            assert calls[i].kwargs["event_type"] == "user_message"
            assert calls[i].kwargs["message"]["content"] == expected_content

        # Last call is injection_consumed with pending_count = 3
        last = calls[3]
        assert last.kwargs["event_type"] == "injection_consumed"
        assert last.kwargs["message"]["pending_count"] == 3
        # Content is the head entry (oldest)
        assert last.kwargs["message"]["content"] == "first"

    @pytest.mark.asyncio
    async def test_agent_node_does_not_emit_when_no_pending_injection(self):
        """Empty queue → no SSE event (the W5 contract is "emit only when meaningful")."""
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

        # No SSE event — the queue was empty.
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
                self.cleared = [
                    {"content": "x", "timestamp": "t"},
                ]

            def get(self, instance_id):
                return list(self.cleared)

            def clear(self, instance_id):
                result = self.cleared
                self.cleared = []
                return result

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
# Pause cascade clearing site — Phase 3 emits injection_consumed
# ---------------------------------------------------------------------------


class TestPauseCascadeClearedSSE:
    """The pause cascade emits ``injection_consumed`` for each cleared queue.

    Phase 3 lifecycle: the new contract is
    ``injection_pending`` (per message) → ``injection_consumed``
    (once, for all). There is no longer an ``injection_cleared`` event
    anywhere — the pause cascade emits ``injection_consumed`` to close
    the pending state.

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
        cleared_per_node: dict[str, list[dict[str, str]] | None],
    ) -> tuple[MagicMock, Any, MagicMock]:
        """Build a mock manager that drives pause_instance_cascade through the SSE loop.

        Wires up:
            * ``_instance_repository`` — ``get_tree_root_id``,
              ``get_tree_ids``, ``get`` (per-node meta lookup).
            * ``_request_registry.cancel_by_instance`` — no-op.
            * ``_graph_tasks.pop`` — returns None (no live tasks).
            * ``release_context_usage_cache`` — no-op.
            * ``clear_injection`` — returns the configured
              ``cleared_per_node[node_id]`` (a list under Phase 3).
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

        # clear_injection — per-node mapping. Phase 3 returns a list.
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
    async def test_pause_emits_injection_consumed_when_queue_was_populated(self):
        """pause_instance_cascade emits ``injection_consumed`` for each cleared queue."""
        manager, _fake_db_sync, hub = self._build_manager_for_pause_test(
            tree_ids=["inst-1"],
            per_node_meta={"inst-1": {"status": "running", "agent_id": "leader"}},
            cleared_per_node={
                "inst-1": [
                    {"content": "old pending msg", "timestamp": "2026-07-13T00:00:00+00:00"},
                ]
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
        # The injection_consumed SSE fired (Phase 3 lifecycle)
        assert hub.stream_message.await_count == 1
        call = hub.stream_message.await_args
        assert call.kwargs["event_type"] == "injection_consumed"
        assert call.args[0] == "inst-1"
        payload = call.kwargs["message"]
        assert payload["event_type"] == "injection_consumed"
        assert payload["instance_id"] == "inst-1"
        assert payload["content"] == "old pending msg"
        assert payload["timestamp"] == "2026-07-13T00:00:00+00:00"
        assert payload["pending_count"] == 1

        assert "inst-1" in result["paused_ids"]

    @pytest.mark.asyncio
    async def test_pause_does_not_emit_when_queue_was_empty(self):
        """No pending injection → no ``injection_consumed`` SSE event."""
        manager, _fake_db_sync, hub = self._build_manager_for_pause_test(
            tree_ids=["inst-1"],
            per_node_meta={"inst-1": {"status": "running", "agent_id": "leader"}},
            cleared_per_node={"inst-1": None},  # Empty queue — nothing to emit
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
        # No injection_consumed event because the queue was empty
        hub.stream_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_cascade_emits_consumed_per_node(self):
        """Multi-node cascade → one ``injection_consumed`` per node with a cleared queue."""
        manager, _fake_db_sync, hub = self._build_manager_for_pause_test(
            tree_ids=["inst-1", "inst-2", "inst-3"],
            per_node_meta={
                "inst-1": {"status": "running", "agent_id": "leader"},
                "inst-2": {"status": "running", "agent_id": "developer"},
                "inst-3": {"status": "running", "agent_id": "tester"},
            },
            cleared_per_node={
                "inst-1": [
                    {"content": "m1", "timestamp": "t1"},
                    {"content": "m1b", "timestamp": "t1b"},
                ],
                "inst-2": None,  # Empty — should NOT emit
                "inst-3": [
                    {"content": "m3", "timestamp": "t3"},
                ],
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

        # 2 injection_consumed events (inst-1, inst-3) — inst-2 was empty
        consumed_calls = [
            call for call in hub.stream_message.await_args_list
            if call.kwargs["event_type"] == "injection_consumed"
        ]
        assert len(consumed_calls) == 2

        # inst-1 emitted with pending_count=2
        inst1_calls = [c for c in consumed_calls if c.args[0] == "inst-1"]
        assert len(inst1_calls) == 1
        assert inst1_calls[0].kwargs["message"]["pending_count"] == 2

        # inst-3 emitted with pending_count=1
        inst3_calls = [c for c in consumed_calls if c.args[0] == "inst-3"]
        assert len(inst3_calls) == 1
        assert inst3_calls[0].kwargs["message"]["pending_count"] == 1
