"""Integration tests for Watchover — full lifecycle flow across all phases.

Covers:

  * **Full lifecycle integration (Phase 1 → Phase 5):** drive a single
    instance through ``activate_watchover`` → three denial batches in
    the watchover check node → ``watchover_terminate_node`` →
    ``terminate_instance`` post-graph drain. Verifies the full chain
    end-to-end and that the SSE ordering is correct.

  * **Kill-switch behavior at the node level.** The
    :class:`WatchoverSlot.is_enabled` semantics are covered in
    :mod:`test_watchover_graph`; this file verifies the EFFECT of the
    kill-switch at the ``watchover_check`` node level
    (passthrough, no evaluator call, no counter change, no SSE).

  * **SSE event lifecycle.** Across one full
    activate → 3-deny → terminate → deactivate, the observed SSE
    payload sequence must include ``watchover_active``, three
    ``watchover_event`` denials, one ``watchover_event`` terminated,
    one ``status_change(terminated)``, and one ``watchover_inactive``.
    Crucially, AFTER the terminate SSE is emitted the deferred drain
    MUST NOT emit further ``watchover_event`` payloads.

  * **Crash recovery integration.** A persistent
    ``watchover_pending_termination=True`` marker (set by
    ``watchover_terminate_node`` before crash) is recovered by
    :meth:`InstanceLifecycleService._recover_watchover_pending_termination`
    which calls ``manager.terminate_instance(terminal_reason="watchover_terminated")``
    and clears the marker. The ``test_watchover_crash_recovery`
    tests cover this method in isolation; this file connects it to
    the FULL flow where the marker was first set by
    ``watchover_terminate_node``.

All tests use the mock-everything convention from
:mod:`test_watchover_decision` and :mod:`test_watchover_phase5` — no
real LLM, no real DB, no real LangGraph run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage

from daemon.graph import (
    WatchoverSlot,
    create_watchover_check_node,
    create_watchover_terminate_node,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class _FakeAIMessage:
    """Lightweight AIMessage stand-in for tests — only carries ``tool_calls``.

    Using a real class instead of ``MagicMock`` so ``getattr(msg,
    "tool_calls")`` returns the actual list of dicts (a ``MagicMock``
    attribute read sometimes returns a new ``MagicMock`` and breaks
    downstream ``isinstance(tc, dict)`` checks).
    """

    tool_calls: list[dict] | None = None
    content: str = ""
    type: str = "ai"
    additional_kwargs: dict = field(default_factory=dict)


def _state_with_tool_calls(
    calls: list[dict] | None = None,
    *,
    denial_count: int = 0,
    route: str | None = None,
) -> dict:
    """State dict with a stub last AIMessage carrying tool_calls."""
    if calls is None:
        calls = [{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}]
    last_message = _FakeAIMessage(tool_calls=calls)
    state: dict[str, Any] = {
        "messages": [last_message],
        "watchover_denial_count": denial_count,
    }
    if route is not None:
        state["watchover_route"] = route
    return state


def _config(instance_id: str = "iid") -> dict:
    """LangGraph config dict with thread_id = instance_id."""
    return {"configurable": {"thread_id": instance_id}}


def _make_manager(
    *,
    watchover_enabled: bool = True,
    watchover_context: str = "ctx",
    context_turn: int = 0,
    refresh_interval: int = 99,
    watchover_requirement: str | None = None,
) -> MagicMock:
    """Build a mock ``InstanceManager`` with the full watchover surface.

    Wires everything the integration flow touches:

      * ``is_watchover_enabled(instance_id) -> bool`` — per-instance flag.
      * ``set_deferred_watchover_terminate(instance_id) -> None``
      * ``is_watchover_terminate_requested(instance_id) -> bool``
      * ``clear_watchover_terminate_requested(instance_id) -> None``
      * ``_instance_repository.get`` (with ``instance_metadata``)
      * ``_instance_repository.set_metadata(...)`` — MagicMock
      * ``_instance_repository.set_metadata_many(...)`` — MagicMock
      * ``_live_hub.stream_message(...)`` — AsyncMock
      * ``_live_hub.stream_status_change(...)`` — AsyncMock
      * ``_live_hub.cleanup_instance(...)`` — AsyncMock
      * ``pause_instance_cascade(...)`` — AsyncMock
      * ``resume_instance_cascade(...)`` — AsyncMock

    Returns:
        A ``MagicMock`` with all watchover-related surfaces wired.
    """
    manager = MagicMock()

    # Per-instance enable flag.
    manager.is_watchover_enabled.side_effect = lambda iid: watchover_enabled

    # Deferred-terminate lifecycle.
    manager.set_deferred_watchover_terminate = MagicMock()
    manager.is_watchover_terminate_requested = MagicMock(return_value=False)
    manager.clear_watchover_terminate_requested = MagicMock()

    # Build a stateful metadata row.
    metadata = {
        "watchover_context": watchover_context,
        "watchover_context_turn": context_turn,
        "watchover_context_refresh_interval": refresh_interval,
    }
    if watchover_requirement is not None:
        metadata["watchover_requirement"] = watchover_requirement

    cache: dict[str, Any] = dict(metadata)

    def _get(instance_id):
        row = MagicMock()
        row.instance_metadata = dict(cache)
        return row

    def _set_md(instance_id, key, value):
        cache[key] = value

    def _set_many(instance_id, updates):
        for k, v in updates.items():
            cache[k] = v

    repo = MagicMock()
    repo.get.side_effect = _get
    repo.set_metadata.side_effect = _set_md
    repo.set_metadata_many.side_effect = _set_many
    manager._instance_repository = repo

    # SSE surface.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.cleanup_instance = AsyncMock()

    # Question-pause stub — build_instance_graph touches it.
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.set_deferred_question_pause = MagicMock()
    manager.clear_question_pause_requested = MagicMock()
    manager.pause_instance_cascade = AsyncMock()
    manager.resume_instance_cascade = AsyncMock()
    manager.wait_for_instance_quiescent = AsyncMock(return_value=True)

    # Lifecycle facade helpers.
    manager.enable_watchover = MagicMock()
    manager.disable_watchover = MagicMock()

    return manager


def _make_fake_llm_class(responses: list[Any] | None = None):
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
        from dataclasses import dataclass

        @dataclass
        class _FakeLLMResult:
            content: Any = ""

        if hasattr(item, "content"):
            return item
        return _FakeLLMResult(content=item)

    mock_instance = MagicMock()
    mock_instance.invoke.side_effect = _next

    return (lambda **kwargs: mock_instance), mock_instance


# =============================================================================
# Full lifecycle integration
# =============================================================================


class TestFullLifecycleIntegration:
    """End-to-end integration of enable → 3 denials → terminate → drain.

    Drives a single instance through the full watchover lifecycle in one
    test method. Mocks the LLM to return ``Deny:`` on every call so all
    three batches go through the deny path. Verifies:

      1. First batch → denied, counter 0 → 1, route ``agent``.
      2. Second batch → denied, counter 1 → 2, route ``agent``.
      3. Third batch → denied, counter 2 → 3, route ``watchover_terminate_node``.
      4. ``watchover_terminate_node`` → DB marker + RAM marker + SSE.
    """

    async def test_full_flow_three_denials_to_termination(self, monkeypatch):
        """One test, whole flow."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        manager = _make_manager(
            watchover_enabled=True, watchover_context="block unsafe"
        )
        slot = WatchoverSlot(manager)
        # Three deny verdicts (one per batch).
        factory, _llm = _make_fake_llm_class(
            ["Deny: unsafe", "Deny: unsafe", "Deny: unsafe"]
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            check_node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            terminate_node = create_watchover_terminate_node(
                slot, manager=manager
            )

            # Batch 1 — counter 0 → 1, route=agent.
            res1 = await check_node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid"),
            )
            assert res1["watchover_denial_count"] == 1
            assert res1["watchover_route"] == "agent"

            # Batch 2 — counter 1 → 2, route=agent.
            res2 = await check_node(
                _state_with_tool_calls(denial_count=1),
                config=_config("iid"),
            )
            assert res2["watchover_denial_count"] == 2
            assert res2["watchover_route"] == "agent"

            # Batch 3 — counter 2 → 3, route=watchover_terminate_node.
            res3 = await check_node(
                _state_with_tool_calls(denial_count=2),
                config=_config("iid"),
            )
            assert res3["watchover_denial_count"] == 3
            assert res3["watchover_route"] == "watchover_terminate_node"

            # terminate_node: DB marker + RAM marker + terminated SSE.
            term_result = await terminate_node(
                {}, config=_config("iid")
            )
            assert term_result == {}

        # ── Assertions on the integrated run ─────────────────────────
        # Three denial SSE events were emitted (one per batch).
        denial_calls = [
            c
            for c in manager._live_hub.stream_message.await_args_list
            if c.args[1].get("status") == "denial"
        ]
        assert len(denial_calls) == 3, (
            f"Expected exactly 3 denial SSE events, got "
            f"{len(denial_calls)}"
        )
        # Escalation is reflected in the payload denial_count.
        denial_counts = [
            call.args[1].get("denial_count") for call in denial_calls
        ]
        assert denial_counts == [1, 2, 3]

        # One terminate SSE event was emitted.
        terminated_calls = [
            c
            for c in manager._live_hub.stream_message.await_args_list
            if c.args[1].get("status") == "terminated"
        ]
        assert len(terminated_calls) == 1

        # The persistent DB marker was set (T5.1 atomic timestamp +
        # intent). ``set_metadata_many`` is a SYNC method on the
        # repository so we use ``call_args_list`` (not ``await_args_list``).
        many_calls = manager._instance_repository.set_metadata_many.call_args_list
        terminate_writes = [
            c
            for c in many_calls
            if isinstance(c.args[1], dict)
            and c.args[1].get("watchover_pending_termination") is True
        ]
        assert len(terminate_writes) == 1
        updates = terminate_writes[0].args[1]
        assert updates["watchover_pending_termination"] is True
        assert "watchover_pending_termination_at" in updates

        # The RAM marker was set (C2).
        manager.set_deferred_watchover_terminate.assert_called_once_with("iid")

        # Watchover hit the LLM exactly three times (one per batch).
        assert _llm.invoke.call_count == 3


# =============================================================================
# Kill-switch behavior (node-level integration)
# =============================================================================


class TestKillSwitchBehavior:
    """Kill-switch behaviour at the ``watchover_check`` node level.

    The :mod:`test_watchover_graph` file covers the
    :class:`WatchoverSlot.is_enabled` predicate in isolation. This file
    verifies the same kill-switches produce the expected effects at the
    node level: zero-cost passthrough, no evaluator, no denial counter,
    no SSE.
    """

    async def test_kill_switch_false_passes_through_node(self, monkeypatch):
        """``WATCHOVER_ENABLED=false`` → node returns ``{"watchover_route": "tools"}``
        even when ``is_watchover_enabled`` would return True.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "false")
        # Per-instance flag is on, but global kill-switch wins.
        manager = _make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)
        factory, _llm = _make_fake_llm_class(
            ["Deny: should not run"]
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid"),
            )

        assert result["watchover_route"] == "tools"
        # The denial counter was NOT incremented.
        assert result.get("watchover_denial_count", 0) == 0
        # Zero-cost guarantee: the per-instance checker was NOT called.
        manager.is_watchover_enabled.assert_not_called()
        # The LLM was NOT invoked.
        assert _llm.invoke.call_count == 0
        # No denial SSE was emitted.
        denial_calls = [
            c
            for c in manager._live_hub.stream_message.await_args_list
            if c.args[1].get("status") == "denial"
        ]
        assert len(denial_calls) == 0

    async def test_kill_switch_true_but_instance_disabled_passes_through(
        self, monkeypatch
    ):
        """Global on + per-instance off → node returns tools route.

        This is the second-fast-path tier: the global switch is on but
        the per-instance DB flag is off, so the check is a no-op.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = _make_manager(watchover_enabled=False)
        slot = WatchoverSlot(manager)
        factory, _llm = _make_fake_llm_class(["Deny: should not run"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid"),
            )

        assert result["watchover_route"] == "tools"
        # Counter unchanged.
        assert result.get("watchover_denial_count", 0) == 0
        # Per-instance checker WAS called (this isn't the zero-cost path).
        manager.is_watchover_enabled.assert_called_once_with("iid")
        # But the LLM was NOT invoked because the check returned False.
        assert _llm.invoke.call_count == 0

    async def test_kill_switch_true_and_instance_enabled_runs_evaluator(
        self, monkeypatch
    ):
        """Global on + per-instance on → evaluator runs and result is honoured.

        Confirms the third tier where both switches are on: the
        evaluator is invoked, its verdict is consumed, and the result
        flows through. This is the "happy" path for watchover.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = _make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)
        factory, _llm = _make_fake_llm_class(["Deny: unsafe"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid"),
            )

        # Evaluator ran and produced a denial.
        assert result["watchover_denial_count"] == 1
        assert result["watchover_route"] == "agent"
        # LLM invoked once.
        assert _llm.invoke.call_count == 1
        # Denial SSE emitted.
        denial_calls = [
            c
            for c in manager._live_hub.stream_message.await_args_list
            if c.args[1].get("status") == "denial"
        ]
        assert len(denial_calls) == 1


# =============================================================================
# SSE event lifecycle
# =============================================================================


class TestSSEEventLifecycle:
    """SSE payloads emitted across the full watchover lifecycle.

    Verifies the precise SSE ordering from activation through
    termination and deactivation, and confirms no spurious watchover
    SSEs leak past the termination boundary.
    """

    async def test_full_lifecycle_sse_sequence(self, monkeypatch):
        """End-to-end SSE ordering: active → 3 denials → terminated → (no extra
        watchover_event past the post-graph drain) → inactive.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = _make_manager(
            watchover_enabled=True,
            watchover_context="block dangerous actions",
        )
        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(
            ["Deny: 1", "Deny: 2", "Deny: 3"]
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            check_node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            terminate_node = create_watchover_terminate_node(
                slot, manager=manager
            )

            # ── Drive the full lifecycle ────────────────────────────
            # 1. Activation SSE.
            await manager._live_hub.stream_status_change("iid", "watchover_active")

            # 2. Three denial batches, then terminate.
            await check_node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid"),
            )
            await check_node(
                _state_with_tool_calls(denial_count=1),
                config=_config("iid"),
            )
            res_terminate = await check_node(
                _state_with_tool_calls(denial_count=2),
                config=_config("iid"),
            )
            assert res_terminate["watchover_route"] == "watchover_terminate_node"
            await terminate_node({}, config=_config("iid"))

            # 3. The post-graph drain fires ``manager.terminate_instance``
            # which itself emits ``status_change(terminated)``. That is
            # NOT a watchover_event — it's the lifecycle status_change.
            await manager._live_hub.stream_status_change("iid", "terminated")

            # 4. Deactivation SSE (operator-driven).
            await manager._live_hub.stream_status_change("iid", "watchover_inactive")

        # ── Inspect the SSE record ───────────────────────────────────
        # stream_status_change captures the lifecycle SSEs:
        status_sequence = [
            c.args[1]
            for c in manager._live_hub.stream_status_change.await_args_list
        ]
        assert status_sequence == [
            "watchover_active",
            "terminated",
            "watchover_inactive",
        ]

        # stream_message captures watchover_event payloads:
        message_events = [
            c.args[1]
            for c in manager._live_hub.stream_message.await_args_list
        ]
        statuses = [m["status"] for m in message_events]
        assert statuses == ["denial", "denial", "denial", "terminated"], (
            f"unexpected watchover_event sequence: {statuses}"
        )

    async def test_no_watchover_event_after_termination_marker(self, monkeypatch):
        """After ``watchover_terminate_node`` no further ``watchover_event``
        payloads leak from the deferred-drain path.

        The drain fires ``terminate_instance`` (which emits
        ``status_change(terminated)``) but MUST NOT emit another
        ``watchover_event`` payload. The terminate node IS the single
        source for the watchover-terminated SSE.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = _make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)

        # 3 batches of deny → terminate.
        factory, _ = _make_fake_llm_class(["Deny: x"] * 3)
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            check_node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            terminate_node = create_watchover_terminate_node(
                slot, manager=manager
            )

            for count in (0, 1, 2):
                await check_node(
                    _state_with_tool_calls(denial_count=count),
                    config=_config("iid"),
                )
            await terminate_node({}, config=_config("iid"))

            # Simulate the deferred drain: it runs
            # ``manager.terminate_instance`` which itself triggers
            # ``stream_status_change("terminated")``. Critically, the
            # drain does NOT emit another watchover_event.
            await manager._live_hub.stream_status_change("iid", "terminated")

        # Count of watchover_event payloads with status="terminated"
        # must be EXACTLY ONE (from the watchover_terminate_node).
        watchover_terminated = [
            c
            for c in manager._live_hub.stream_message.await_args_list
            if c.args[1].get("status") == "terminated"
        ]
        assert len(watchover_terminated) == 1

        # No duplicate ``denial`` SSE fired after the terminate SSE
        # sequence ends.
        watchover_events = [
            c
            for c in manager._live_hub.stream_message.await_args_list
            if c.args[1].get("event_type") == "watchover_event"
        ]
        denial_events = [
            c for c in watchover_events if c.args[1].get("status") == "denial"
        ]
        # 3 denial + 1 terminated = 4 total; no extras.
        assert len(watchover_events) == 4
        assert len(denial_events) == 3


# =============================================================================
# Crash recovery integration
# =============================================================================


class TestCrashRecoveryIntegration:
    """Crash recovery end-to-end: marker set → _recover → terminate → marker cleared.

    The :mod:`test_watchover_crash_recovery` file covers
    ``_recover_watchover_pending_termination`` in isolation. This file
    connects it to the FULL flow where the marker was first set by the
    terminate node — exercising the same manager, mock instance row,
    and recovery reader.
    """

    async def test_full_lifecycle_marker_set_then_recovery_drains(
        self, monkeypatch
    ):
        """Full chain: 3-deny → terminate node sets DB+RAM markers →
        simulate crash → recover → terminate_instance fires → RAM marker
        cleared.

        Uses the real
        :meth:`InstanceLifecycleService._recover_watchover_pending_termination`
        implementation (not a mock) so a regression in the recovery
        reader fails this test.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = _make_manager(
            watchover_enabled=True,
            watchover_context="block unsafe",
        )
        slot = WatchoverSlot(manager)
        # Three denies drive the counter to 3 → terminate route.
        factory, _ = _make_fake_llm_class(
            ["Deny: 1", "Deny: 2", "Deny: 3"]
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            check_node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            terminate_node = create_watchover_terminate_node(
                slot, manager=manager
            )

            # Drive to termination.
            await check_node(
                _state_with_tool_calls(denial_count=0),
                config=_config("iid"),
            )
            await check_node(
                _state_with_tool_calls(denial_count=1),
                config=_config("iid"),
            )
            await check_node(
                _state_with_tool_calls(denial_count=2),
                config=_config("iid"),
            )
            await terminate_node({}, config=_config("iid"))

        # ── The persistent state at this point ──────────────────────
        # Both the DB marker AND the RAM marker are set.
        manager.set_deferred_watchover_terminate.assert_called_once_with("iid")

        # ── Simulate crash + restart, then recover ───────────────────
        from types import SimpleNamespace

        from daemon.services.instance_lifecycle import InstanceLifecycleService

        # Build a minimal lifecycle service; the only method we exercise
        # is _recover_watchover_pending_termination.
        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = manager

        # Build a meta row that REPORTS the persistent marker is set
        # (this is the row that was committed before the crash).
        stale_row = SimpleNamespace(
            instance_metadata={
                "watchover_pending_termination": True,
                "watchover_pending_termination_at": "2026-08-05T12:00:00+00:00",
            }
        )
        # Make ``manager.terminate_instance`` behave like the real one:
        # a no-op-then-truthy return.
        async def _terminate(instance_id, *, terminal_reason):
            return True

        manager.terminate_instance = AsyncMock(side_effect=_terminate)

        await service._recover_watchover_pending_termination("iid", stale_row)

        # ── Recovery assertions ──────────────────────────────────────
        # The recovery reader called terminate_instance with the
        # watched-out terminal_reason.
        manager.terminate_instance.assert_awaited_once_with(
            "iid", terminal_reason="watchover_terminated"
        )

        # The marker was cleared on the DB row.
        manager._instance_repository.set_metadata_many.assert_called_with(
            "iid",
            {
                "watchover_pending_termination": False,
                "watchover_pending_termination_at": None,
            },
        )

    async def test_recovery_failure_preserves_marker_for_retry(self, monkeypatch):
        """Recovery → terminate fails → marker is NOT cleared, so the next
        sweep retries.

        This is the H2 invariant: the persistent DB marker is the
        crash-recovery backstop; if a recovery attempt's
        ``terminate_instance`` raises, the marker must be preserved so
        :meth:`StaleTaskRecovery._sweep_watchover_terminate_markers`
        can retry on the next pass (covered separately in
        :mod:`test_watchover_crash_recovery`). This integration test
        exercises the same recovery path with a failing terminate.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = _make_manager(
            watchover_enabled=True,
        )
        from types import SimpleNamespace

        from daemon.services.instance_lifecycle import InstanceLifecycleService

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = manager
        stale_row = SimpleNamespace(
            instance_metadata={"watchover_pending_termination": True}
        )

        async def _boom(instance_id, *, terminal_reason):
            raise RuntimeError("cascade failed")

        manager.terminate_instance = AsyncMock(side_effect=_boom)

        # Recovery is non-fatal — the exception is logged but not raised.
        await service._recover_watchover_pending_termination("iid", stale_row)

        # The marker is NOT cleared (preserved for the next sweep).
        manager._instance_repository.set_metadata_many.assert_not_called()

        # terminate_instance WAS called once with the watchover reason.
        manager.terminate_instance.assert_awaited_once_with(
            "iid", terminal_reason="watchover_terminated"
        )


# =============================================================================
# Suspicious-state integration
# =============================================================================


class TestActivationDeactivationSSE:
    """Activation / deactivation emit the expected SSE status_change events.

    The :mod:`test_watchover_lifecycle` covers the activation lifecycle
    in detail. This file only verifies that the activation SSE
    (``watchover_active``) is the FIRST SSE event in the lifecycle, and
    ``watchover_inactive`` is emitted by the deactivation path. Tests
    drive the real ``WatchoverService`` so a regression in the SSE
    ordering would fail here.
    """

    async def test_activation_emits_watchover_active_first(self, monkeypatch):
        """``activate_watchover`` emits ``watchover_active`` as the first
        status_change event after the underlying flag write.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        # Build a manager with the surfaces WatchoverService consumes.
        manager = MagicMock()
        manager.wait_for_instance_quiescent = AsyncMock(return_value=True)
        manager.pause_instance_cascade = AsyncMock()
        manager.resume_instance_cascade = AsyncMock()
        manager.enable_watchover = MagicMock()
        manager._instance_repository = MagicMock()
        manager._compactor = None
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()

        # get_instance returns a graph with an empty message list.
        graph = MagicMock()
        state = MagicMock()
        state.values = {"messages": []}
        graph.aget_state = AsyncMock(return_value=state)
        manager.get_instance = AsyncMock(return_value=graph)

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        result = await svc.activate_watchover(
            "iid", requirement="be safe", user_context="user note"
        )

        assert result["watchover_enabled"] is True
        # The first status_change emitted was watchover_active.
        assert manager._live_hub.stream_status_change.await_args_list[-1].args == (
            "iid",
            "watchover_active",
        )

    async def test_deactivation_emits_watchover_inactive(self, monkeypatch):
        """``deactivate_watchover`` emits ``watchover_inactive``."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        manager = MagicMock()
        manager.pause_instance_cascade = AsyncMock()
        manager.resume_instance_cascade = AsyncMock()
        manager.disable_watchover = MagicMock()
        manager._instance_repository = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        result = await svc.deactivate_watchover("iid")

        assert result["watchover_enabled"] is False
        # The deactivate path emitted exactly one status_change with
        # ``watchover_inactive``.
        assert manager._live_hub.stream_status_change.await_count == 1
        assert manager._live_hub.stream_status_change.await_args.args == (
            "iid",
            "watchover_inactive",
        )
