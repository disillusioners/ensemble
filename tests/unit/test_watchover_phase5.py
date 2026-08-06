"""Tests for Phase 5 Watchover features — context freshness (T5.4) + SSE (T5.6).

Covers:

  * **T5.4 — Context freshness check** (``create_watchover_check_node``):
      * The turn counter increments on every watchover check and is
        written back to ``instance_metadata``.
      * When ``context_turn >= refresh_interval``, the node re-derives a
        lightweight context from the current ``messages`` tail via
        ``_format_raw_tail`` (no LLM call).
      * The refreshed context re-splices the requirement.
      * A refresh failure does NOT block the watchover check (continues
        with stale context).
      * ``enable_watchover`` (manager.py) writes the
        ``watchover_context_turn=0`` and
        ``watchover_context_refresh_interval`` keys at activation.

  * **T5.6 — SSE event emission**:
      * ``_emit_watchover_sse`` helper — best-effort, never raises, builds
        the right payload.
      * ``watchover_denial`` SSE emitted on every denied batch (both the
        evaluator-escape path and the normal deny-whole-batch path).
      * ``watchover_terminated`` SSE emitted from the terminate node.
      * Degraded SSE (existing) now delegates to the helper and still
        emits the same payload shape.

All tests mock the LLM + LangGraph + DB surface (no real provider, no
real engine) following the ``tests/unit/test_watchover_decision.py``
pattern.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.graph import (
    WatchoverSlot,
    _emit_watchover_sse,
    create_watchover_check_node,
    create_watchover_terminate_node,
)


# =============================================================================
# Helpers (mirrors test_watchover_decision.py)
# =============================================================================


@dataclass
class _FakeLLMResult:
    """Drop-in for a LangChain ``AIMessage`` — only ``.content`` matters."""

    content: Any = ""


def _config(instance_id: str = "iid") -> dict:
    """LangGraph config dict with thread_id = instance_id."""
    return {"configurable": {"thread_id": instance_id}}


@dataclass
class _FakeAIMessage:
    """Lightweight AIMessage stand-in — only carries ``tool_calls``."""

    tool_calls: list[dict] | None = None
    content: str = ""
    type: str = "ai"
    additional_kwargs: dict = field(default_factory=dict)


def _state_with_tool_calls(
    calls: list[dict] | None = None,
    *,
    messages: list[Any] | None = None,
    denial_count: int = 0,
) -> dict:
    """State dict with a stub last AIMessage carrying tool_calls."""
    if calls is None:
        calls = [{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}]

    if messages is None:
        last_message = _FakeAIMessage(tool_calls=calls)
        messages = [last_message]

    return {
        "messages": messages,
        "watchover_denial_count": denial_count,
    }


def _state_without_tool_calls() -> dict:
    """State dict whose last message has NO ``tool_calls``."""
    last_message = _FakeAIMessage(tool_calls=None)
    return {"messages": [last_message]}


def make_manager(
    *,
    watchover_enabled: bool = True,
    watchover_context: str | None = None,
    context_turn: int | None = None,
    refresh_interval: int | None = None,
    watchover_requirement: str | None = None,
    instance_metadata: dict | None = None,
) -> MagicMock:
    """Build a mock ``InstanceManager`` with the watchover + DB surface wired.

    Wires:
      * ``is_watchover_enabled(instance_id) -> bool``
      * ``set_deferred_watchover_terminate(instance_id) -> None``
      * ``_instance_repository.get(instance_id)`` (with ``instance_metadata``)
      * ``_instance_repository.set_metadata(...)`` — MagicMock
      * ``_instance_repository.set_metadata_many(...)`` — MagicMock
      * ``_live_hub.stream_message(...)`` — AsyncMock
    """
    manager = MagicMock()

    manager.is_watchover_enabled.side_effect = lambda iid: watchover_enabled
    manager.set_deferred_watchover_terminate = MagicMock()

    # Build a fake instance row with metadata.
    if instance_metadata is None:
        instance_metadata = {}
    if watchover_context is not None:
        instance_metadata.setdefault("watchover_context", watchover_context)
    if context_turn is not None:
        instance_metadata.setdefault("watchover_context_turn", context_turn)
    if refresh_interval is not None:
        instance_metadata.setdefault(
            "watchover_context_refresh_interval", refresh_interval
        )
    if watchover_requirement is not None:
        instance_metadata.setdefault("watchover_requirement", watchover_requirement)

    row = MagicMock()
    row.instance_metadata = instance_metadata
    repo = MagicMock()
    repo.get.return_value = row
    repo.set_metadata = MagicMock(return_value=row)
    repo.set_metadata_many = MagicMock(return_value=row)
    manager._instance_repository = repo

    # SSE surface.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()

    # Stub the question-pause surface so build_instance_graph does not fail.
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.set_deferred_question_pause = MagicMock()
    manager.clear_question_pause_requested = MagicMock()
    manager.pause_instance_cascade = AsyncMock()

    return manager


def _make_fake_llm_class(
    responses: list[Any] | None = None,
):
    """Build a ``ThinkingChatOpenAI`` factory mock with a queued response list."""
    if responses is None:
        responses = []
    queue = list(responses)

    def _next(_messages):
        if not queue:
            raise AssertionError("LLM mock exhausted")
        item = queue.pop(0)
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        if isinstance(item, _FakeLLMResult):
            return item
        return _FakeLLMResult(content=item)

    mock_instance = MagicMock()
    mock_instance.invoke.side_effect = _next

    return (lambda **kwargs: mock_instance), mock_instance


# =============================================================================
# T5.4 — Context freshness check
# =============================================================================


class TestContextFreshnessTurnCounter:
    """The turn counter increments on every watchover check.

    The node reads ``watchover_context_turn`` from ``instance_metadata``,
    increments it, and writes it back via ``set_metadata``. This advances
    the staleness clock so the next check can detect a stale context.
    """

    async def test_turn_counter_increments_and_is_written_back(
        self, monkeypatch
    ):
        """context_turn=0, interval=99 (never stale) → writes turn=1."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(
            watchover_context="some context",
            context_turn=0,
            refresh_interval=99,
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            await node(_state_with_tool_calls(), config=_config("iid"))

        # The turn counter was written back with the incremented value.
        manager._instance_repository.set_metadata.assert_called_with(
            "iid", "watchover_context_turn", 1
        )

    async def test_turn_counter_starts_at_zero_when_missing(self, monkeypatch):
        """Missing ``watchover_context_turn`` → treated as 0, written as 1."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(
            watchover_context="some context",
            # No context_turn in metadata → defaults to 0.
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            await node(_state_with_tool_calls(), config=_config("iid"))

        manager._instance_repository.set_metadata.assert_called_with(
            "iid", "watchover_context_turn", 1
        )


class TestContextFreshnessRefresh:
    """When the context is stale, the node re-derives a lightweight snapshot.

    Staleness = ``context_turn >= refresh_interval``. The rebuild uses
    ``_format_raw_tail`` on the current ``messages`` (no LLM call). The
    refreshed context is persisted via ``set_metadata_many`` and the turn
    counter is reset to 0.
    """

    async def test_stale_context_triggers_refresh(self, monkeypatch):
        """context_turn=1, interval=1 → stale → rebuilds from messages tail."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(
            watchover_context="OLD STALE CONTEXT",
            context_turn=1,
            refresh_interval=1,
        )
        slot = WatchoverSlot(manager)

        # Messages with some real content so _format_raw_tail has
        # something to extract.
        from langchain_core.messages import HumanMessage, AIMessage

        messages = [
            HumanMessage(content="do something safe"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}],
            ),
        ]

        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            await node(
                _state_with_tool_calls(messages=messages),
                config=_config("iid"),
            )

        # The context was refreshed via set_metadata_many. The new
        # context must NOT contain the old stale marker.
        calls = manager._instance_repository.set_metadata_many.call_args_list
        refreshed_calls = [
            c for c in calls
            if "watchover_context" in (c.args[1] if len(c.args) >= 2 else c.kwargs.get("updates", {}))
        ]
        assert len(refreshed_calls) >= 1, "Expected a context refresh write"
        new_context = refreshed_calls[0].args[1]["watchover_context"]
        assert "OLD STALE CONTEXT" not in new_context
        # The new context should contain the tail content.
        assert "do something safe" in new_context
        # Turn counter reset to 0 in the same atomic write.
        assert refreshed_calls[0].args[1]["watchover_context_turn"] == 0

    async def test_refresh_resplices_requirement(self, monkeypatch):
        """Refreshed context includes the requirement prefix."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(
            watchover_context="OLD",
            context_turn=1,
            refresh_interval=1,
            watchover_requirement="do not delete files",
        )
        slot = WatchoverSlot(manager)

        from langchain_core.messages import HumanMessage, AIMessage

        messages = [
            HumanMessage(content="hello"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
            ),
        ]

        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            await node(
                _state_with_tool_calls(messages=messages),
                config=_config("iid"),
            )

        calls = manager._instance_repository.set_metadata_many.call_args_list
        refreshed = [
            c for c in calls
            if "watchover_context" in (c.args[1] if len(c.args) >= 2 else {})
        ][0]
        new_context = refreshed.args[1]["watchover_context"]
        assert "[Requirement] do not delete files" in new_context

    async def test_fresh_context_not_refreshed(self, monkeypatch):
        """context_turn=0, interval=99 → not stale → no refresh write."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(
            watchover_context="FRESH CONTEXT",
            context_turn=0,
            refresh_interval=99,
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            await node(_state_with_tool_calls(), config=_config("iid"))

        # No set_metadata_many call containing watchover_context (only
        # the turn counter write via set_metadata).
        calls = manager._instance_repository.set_metadata_many.call_args_list
        context_writes = [
            c for c in calls
            if "watchover_context" in (c.args[1] if len(c.args) >= 2 else {})
        ]
        assert len(context_writes) == 0

    async def test_refresh_failure_does_not_block(self, monkeypatch):
        """A refresh failure → continues with stale context (no exception)."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(
            watchover_context="STALE BUT USABLE",
            context_turn=1,
            refresh_interval=1,
        )
        # Make _format_raw_tail raise by patching the import.
        slot = WatchoverSlot(manager)

        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            with patch(
                "daemon.services.watchover_service._format_raw_tail",
                side_effect=RuntimeError("boom"),
            ):
                node = create_watchover_check_node(
                    manager=manager, slot=slot, llm_config={"model": "test"}
                )
                result = await node(
                    _state_with_tool_calls(), config=_config("iid")
                )

        # Node completed normally (no exception propagated) — the
        # refresh failure was swallowed.
        assert result["watchover_route"] == "tools"


class TestEnableWatchoverWritesFreshnessKeys:
    """``enable_watchover`` writes ``watchover_context_turn=0`` and the
    ``watchover_context_refresh_interval`` at activation (manager.py T5.4)."""

    def test_writes_turn_zero_and_interval(self):
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        captured: dict = {}

        def _capture(iid, updates):
            captured.update(updates)
            return MagicMock()

        manager.set_metadata_many = _capture

        manager.enable_watchover(
            "iid", requirement="req", context="ctx"
        )

        assert captured["watchover_context_turn"] == 0
        assert captured["watchover_context_refresh_interval"] == 1
        assert captured["watchover_enabled"] is True
        assert captured["watchover_context"] == "ctx"

    def test_explicit_refresh_interval(self):
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        captured: dict = {}
        manager.set_metadata_many = lambda iid, updates: captured.update(updates)

        manager.enable_watchover(
            "iid", requirement="req", context="ctx", refresh_interval=5
        )
        assert captured["watchover_context_refresh_interval"] == 5

    def test_env_var_refresh_interval(self, monkeypatch):
        from daemon.manager import InstanceManager

        monkeypatch.setenv("WATCHOVER_CONTEXT_REFRESH_INTERVAL", "3")
        manager = InstanceManager.__new__(InstanceManager)
        captured: dict = {}
        manager.set_metadata_many = lambda iid, updates: captured.update(updates)

        manager.enable_watchover("iid", requirement="req", context="ctx")
        assert captured["watchover_context_refresh_interval"] == 3

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        from daemon.manager import InstanceManager

        monkeypatch.setenv("WATCHOVER_CONTEXT_REFRESH_INTERVAL", "not-a-number")
        manager = InstanceManager.__new__(InstanceManager)
        captured: dict = {}
        manager.set_metadata_many = lambda iid, updates: captured.update(updates)

        manager.enable_watchover("iid", requirement="req", context="ctx")
        assert captured["watchover_context_refresh_interval"] == 1

    def test_zero_interval_floored_to_one(self):
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        captured: dict = {}
        manager.set_metadata_many = lambda iid, updates: captured.update(updates)

        manager.enable_watchover(
            "iid", requirement="req", context="ctx", refresh_interval=0
        )
        assert captured["watchover_context_refresh_interval"] == 1


# =============================================================================
# T5.6 — SSE helper
# =============================================================================


class TestEmitWatchoverSseHelper:
    """``_emit_watchover_sse`` — best-effort, never raises, builds the payload."""

    async def test_emits_correct_payload(self):
        manager = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_message = AsyncMock()

        await _emit_watchover_sse(
            manager, "iid-123", "denial", reason="too sensitive", denial_count=2
        )

        manager._live_hub.stream_message.assert_awaited_once()
        call_args = manager._live_hub.stream_message.await_args
        assert call_args.args[0] == "iid-123"
        payload = call_args.args[1]
        assert payload["instance_id"] == "iid-123"
        assert payload["event_type"] == "watchover_event"
        assert payload["status"] == "denial"
        assert payload["reason"] == "too sensitive"
        assert payload["denial_count"] == 2
        assert call_args.kwargs["event_type"] == "watchover_event"

    async def test_no_live_hub_silently_returns(self):
        """Manager without ``_live_hub`` → no-op, no exception."""
        manager = MagicMock()
        manager._live_hub = None
        # Should not raise.
        await _emit_watchover_sse(manager, "iid", "denial")

    async def test_no_stream_message_silently_returns(self):
        """``_live_hub`` without ``stream_message`` → no-op."""
        manager = MagicMock()
        manager._live_hub = MagicMock()
        # Remove stream_message.
        del manager._live_hub.stream_message
        await _emit_watchover_sse(manager, "iid", "denial")

    async def test_stream_message_exception_does_not_raise(self):
        """``stream_message`` raising → caught, no propagation."""
        manager = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_message = AsyncMock(
            side_effect=RuntimeError("SSE down")
        )
        # Should not raise.
        await _emit_watchover_sse(manager, "iid", "terminated")

    async def test_extra_kwargs_merged_into_payload(self):
        manager = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_message = AsyncMock()

        await _emit_watchover_sse(
            manager,
            "iid",
            "terminated",
            reason="3-strike termination",
            custom_field="value",
        )

        payload = manager._live_hub.stream_message.await_args.args[1]
        assert payload["custom_field"] == "value"
        assert payload["status"] == "terminated"


# =============================================================================
# T5.6 — Denial SSE emission
# =============================================================================


class TestDenialSseEmission:
    """``watchover_denial`` SSE emitted on every denied batch.

    Both deny paths emit: the evaluator-escape path (judgment error) and
    the normal deny-whole-batch path.
    """

    async def test_normal_deny_emits_denial_sse(self, monkeypatch):
        """Deny verdict → denial SSE with the reason + denial_count."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_context="ctx", context_turn=0, refresh_interval=99)
        slot = WatchoverSlot(manager)

        factory, _ = _make_fake_llm_class(["Deny: reads /etc/shadow"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            await node(
                _state_with_tool_calls(
                    calls=[{"id": "tc-1", "name": "read_file", "args": {}}]
                ),
                config=_config("iid"),
            )

        # Find the denial SSE call (status="denial").
        calls = manager._live_hub.stream_message.await_args_list
        denial_calls = [
            c for c in calls
            if c.args[1].get("status") == "denial"
        ]
        assert len(denial_calls) == 1
        payload = denial_calls[0].args[1]
        assert payload["denial_count"] == 1
        assert payload["reason"] == "reads /etc/shadow"

    async def test_evaluator_escape_emits_denial_sse(self, monkeypatch):
        """Evaluator raises → denial SSE with judgment-error reason.

        The evaluator is documented to never raise (it catches its own
        exceptions), so to test the escape path we patch
        ``WatchoverEvaluator.evaluate`` at the class level to raise —
        simulating a future evaluator bug.
        """
        from daemon.graph import WatchoverEvaluator

        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(
            watchover_context="ctx", context_turn=0, refresh_interval=99
        )
        slot = WatchoverSlot(manager)

        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            with patch.object(
                WatchoverEvaluator,
                "evaluate",
                new=AsyncMock(side_effect=RuntimeError("escaped")),
            ):
                await node(
                    _state_with_tool_calls(),
                    config=_config("iid"),
                )

        calls = manager._live_hub.stream_message.await_args_list
        denial_calls = [
            c for c in calls if c.args[1].get("status") == "denial"
        ]
        assert len(denial_calls) == 1
        assert "RuntimeError" in denial_calls[0].args[1]["reason"]

    async def test_allow_path_does_not_emit_denial_sse(self, monkeypatch):
        """All-allow → no denial SSE."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_context="ctx", context_turn=0, refresh_interval=99)
        slot = WatchoverSlot(manager)

        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            await node(_state_with_tool_calls(), config=_config("iid"))

        calls = manager._live_hub.stream_message.await_args_list
        denial_calls = [
            c for c in calls if c.args[1].get("status") == "denial"
        ]
        assert len(denial_calls) == 0


# =============================================================================
# T5.6 — Terminated SSE emission
# =============================================================================


class TestTerminatedSseEmission:
    """``watchover_terminated`` SSE emitted from the terminate node."""

    async def test_terminate_node_emits_terminated_sse(self, monkeypatch):
        """3-strike terminate node emits status="terminated" SSE."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        slot = WatchoverSlot(manager)

        node = create_watchover_terminate_node(slot, manager=manager)
        await node({}, config=_config("iid"))

        calls = manager._live_hub.stream_message.await_args_list
        terminated_calls = [
            c for c in calls if c.args[1].get("status") == "terminated"
        ]
        assert len(terminated_calls) == 1
        payload = terminated_calls[0].args[1]
        assert payload["reason"] == "3-strike termination"
        assert payload["event_type"] == "watchover_event"

    async def test_no_manager_skips_terminated_sse(self, monkeypatch):
        """``manager=None`` → no SSE emit (backward-compat)."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        slot = WatchoverSlot(manager)

        node = create_watchover_terminate_node(slot, manager=None)
        await node({}, config=_config("iid"))

        manager._live_hub.stream_message.assert_not_awaited()


# =============================================================================
# T5.6 — Degraded SSE (existing) still works via the helper
# =============================================================================


class TestDegradedSseViaHelper:
    """The existing degraded SSE path now delegates to ``_emit_watchover_sse``.

    Verifies the refactor did not change the observable behavior.
    """

    async def test_infra_error_emits_degraded_sse(self, monkeypatch):
        """Infra error → exactly one degraded SSE (status="degraded")."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_context="ctx", context_turn=0, refresh_interval=99)
        slot = WatchoverSlot(manager)

        factory, _ = _make_fake_llm_class([asyncio.TimeoutError()])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager,
                slot=slot,
                llm_config={"model": "test"},
                watcher_config={"timeout_seconds": 1},
            )
            await node(_state_with_tool_calls(), config=_config("iid"))

        calls = manager._live_hub.stream_message.await_args_list
        degraded_calls = [
            c for c in calls if c.args[1].get("status") == "degraded"
        ]
        assert len(degraded_calls) >= 1
        payload = degraded_calls[0].args[1]
        assert payload["event_type"] == "watchover_event"
        assert "reason" in payload
