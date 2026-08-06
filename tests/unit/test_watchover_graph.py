"""Tests for the Watchover graph wiring in ``daemon.graph``.

Phase 1 — core graph interception. Covers:

  * **T1.0 Topology invariant (IO-3, W-10).** When a manager is provided,
    ``build_instance_graph`` must route the ``agent`` conditional edge's
    ``"tools"`` key to ``"watchover_check"`` (NOT ``"tools"`` directly), and
    must register the ``watchover_check`` + ``watchover_terminate_node``
    nodes. When ``manager is None``, the direct ``"tools": "tools"`` edge
    is preserved (backward compat).
  * **T1.0b Kill-switch.** ``WATCHOVER_ENABLED=false`` →
    :meth:`WatchoverSlot.is_enabled` returns ``False`` even for enabled
    instances (zero-cost global disable).
  * **Node units.** ``create_watchover_check_node``,
    ``create_watchover_terminate_node``, ``should_end_watchover`` — Phase 1
    stub semantics (always Allow).
  * **Manager accessors.** ``is_watchover_enabled`` reads
    ``instance_metadata`` JSONB; the deferred-terminate marker lifecycle.

All tests mock the LLM + LangGraph surface (same pattern as
``tests/unit/test_question_graph.py``) so they run without a real server
or database.
"""

from __future__ import annotations

from typing import Any

from unittest.mock import AsyncMock, MagicMock, patch

from daemon.graph import (
    WatchoverSlot,
    build_instance_graph,
    create_watchover_check_node,
    create_watchover_terminate_node,
    should_end_watchover,
)


# =============================================================================
# Helpers
# =============================================================================


def make_manager(*, watchover_enabled: bool = False) -> MagicMock:
    """Build a mock ``InstanceManager`` with the watchover surface wired.

    Wires the methods the watchover slot + nodes consume:

      * ``is_watchover_enabled(instance_id) -> bool`` — per-instance flag.
      * ``set_deferred_watchover_terminate(instance_id) -> None`` — C2-safe
        deferred marker setter.

    Flag state is backed by a real dict so ``is_watchover_enabled`` returns
    a consistent value. ``set_deferred_watchover_terminate`` is a real
    ``MagicMock`` (tests assert call count / args).

    Args:
        watchover_enabled: Initial value returned by
            ``is_watchover_enabled`` for any instance_id.

    Returns:
        A ``MagicMock`` with the watchover surface wired.
    """
    manager = MagicMock()

    def _is_enabled(instance_id: str) -> bool:
        return watchover_enabled

    manager.is_watchover_enabled.side_effect = _is_enabled
    manager.set_deferred_watchover_terminate = MagicMock()
    # Also stub the question-pause surface so build_instance_graph
    # doesn't fail if it touches it.
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.set_deferred_question_pause = MagicMock()
    manager.clear_question_pause_requested = MagicMock()
    manager.pause_instance_cascade = AsyncMock()

    return manager


def make_bare_manager() -> Any:
    """Build a bare ``InstanceManager`` for accessor-level tests.

    Uses ``InstanceManager.__new__`` to skip the heavy ``__init__``. Only
    the ``_instance_repository`` and ``_deferred_watchover_terminate``
    attributes are seeded — the accessor methods only touch those.
    """
    from daemon.manager import InstanceManager

    manager = InstanceManager.__new__(InstanceManager)
    manager._deferred_watchover_terminate = set()
    return manager


def _config(instance_id: str = "iid") -> dict:
    """LangGraph-style config dict with thread_id = instance_id."""
    return {"configurable": {"thread_id": instance_id}}


def _state_with_tool_calls() -> dict:
    """State dict whose last message has ``tool_calls`` (triggers watchover)."""
    msg = MagicMock()
    msg.tool_calls = [{"name": "bash", "args": {"command": "ls"}}]
    return {"messages": [msg]}


def _state_without_tool_calls() -> dict:
    """State dict whose last message has NO ``tool_calls``."""
    msg = MagicMock()
    msg.tool_calls = None
    return {"messages": [msg]}


# =============================================================================
# T1.0 — Topology invariant
# =============================================================================


class TestWatchoverTopology:
    """``build_instance_graph`` must wire ``watchover_check`` when a manager is given.

    The topology invariant (IO-3): there must be NO direct ``agent → tools``
    edge when watchover is active — the ``agent`` conditional edge's
    ``"tools"`` key must map to ``"watchover_check"``. When ``manager is
    None``, the direct ``"tools": "tools"`` edge is preserved for backward
    compatibility.
    """

    @staticmethod
    def _build_and_capture(*, manager: Any) -> tuple[MagicMock, MagicMock]:
        """Run ``build_instance_graph`` with mocked internals, return (graph, compiled).

        Patches ``ThinkingChatOpenAI``, ``StateGraph``, and ``ToolNode`` so
        the call succeeds without a real LLM or DB. Returns the captured
        ``StateGraph`` mock instance and the compiled mock.
        """
        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance

            with patch("daemon.graph.StateGraph") as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance

                with patch("daemon.graph.ToolNode"):
                    compiled = build_instance_graph(
                        tools=[],
                        checkpointer=MagicMock(),
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="x",
                        manager=manager,
                    )

        return mock_graph_instance, compiled

    def test_no_direct_agent_to_tools_edge_when_manager_provided(self):
        """When manager is provided, 'agent' conditional 'tools' → 'watchover_check'."""
        manager = make_manager()
        graph, compiled = self._build_and_capture(manager=manager)

        # Find the add_conditional_edges call(s) for the "agent" node.
        agent_conditional_calls = [
            call for call in graph.add_conditional_edges.call_args_list
            if call.args and call.args[0] == "agent"
        ]
        assert len(agent_conditional_calls) >= 1, (
            "Expected at least one add_conditional_edges('agent', ...) call"
        )

        # In each agent conditional call, the mapping's "tools" key must
        # point to "watchover_check" (NOT "tools").
        for call in agent_conditional_calls:
            mapping = call.args[2] if len(call.args) >= 3 else call.kwargs.get("path_map", {})
            assert mapping.get("tools") == "watchover_check", (
                f"Expected 'tools' → 'watchover_check' in agent conditional "
                f"edges mapping, got: {mapping}"
            )

        # watchover_check node must be registered.
        add_node_calls = graph.add_node.call_args_list
        node_names = [call.args[0] for call in add_node_calls]
        assert "watchover_check" in node_names, (
            f"Expected 'watchover_check' in add_node calls, got: {node_names}"
        )
        assert "watchover_terminate_node" in node_names, (
            f"Expected 'watchover_terminate_node' in add_node calls, got: {node_names}"
        )

        # watchover_check must have a conditional edge → tools / agent / terminate.
        watchover_conditional_calls = [
            call for call in graph.add_conditional_edges.call_args_list
            if call.args and call.args[0] == "watchover_check"
        ]
        assert len(watchover_conditional_calls) == 1, (
            "Expected exactly one add_conditional_edges('watchover_check', ...) call"
        )
        mapping = watchover_conditional_calls[0].args[2]
        assert mapping == {
            "tools": "tools",
            "agent": "agent",
            "watchover_terminate_node": "watchover_terminate_node",
        }, f"Unexpected watchover_check mapping: {mapping}"

        # watchover_terminate_node → END edge must be registered.
        terminate_edges = [
            call for call in graph.add_edge.call_args_list
            if call.args and len(call.args) >= 2
            and call.args[0] == "watchover_terminate_node"
        ]
        assert len(terminate_edges) == 1, (
            "Expected add_edge('watchover_terminate_node', END)"
        )

    def test_direct_edge_preserved_when_no_manager(self):
        """When manager is None, 'tools' → 'tools' direct edge is kept."""
        graph, compiled = self._build_and_capture(manager=None)

        agent_conditional_calls = [
            call for call in graph.add_conditional_edges.call_args_list
            if call.args and call.args[0] == "agent"
        ]
        assert len(agent_conditional_calls) >= 1

        for call in agent_conditional_calls:
            mapping = call.args[2] if len(call.args) >= 3 else call.kwargs.get("path_map", {})
            assert mapping.get("tools") == "tools", (
                f"Expected direct 'tools' → 'tools' when manager is None, "
                f"got: {mapping}"
            )

        # watchover nodes must NOT be registered.
        add_node_calls = graph.add_node.call_args_list
        node_names = [call.args[0] for call in add_node_calls]
        assert "watchover_check" not in node_names, (
            "watchover_check should NOT be registered when manager is None"
        )

    def test_no_direct_agent_to_tools_edge_when_manager_and_language_check_disabled(self):
        """language_check_enabled=False + manager provided → 'tools' still routes to 'watchover_check'."""
        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance

            with patch("daemon.graph.StateGraph") as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance

                with patch("daemon.graph.ToolNode"):
                    compiled = build_instance_graph(
                        tools=[],
                        checkpointer=MagicMock(),
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="x",
                        manager=make_manager(),
                        language_check_enabled=False,
                    )

        # Find the add_conditional_edges call(s) for the "agent" node.
        agent_conditional_calls = [
            call for call in mock_graph_instance.add_conditional_edges.call_args_list
            if call.args and call.args[0] == "agent"
        ]
        assert len(agent_conditional_calls) >= 1, (
            "Expected at least one add_conditional_edges('agent', ...) call"
        )

        # In each agent conditional call, the mapping's "tools" key must
        # point to "watchover_check" (NOT "tools") even when language_check
        # is disabled — watchover interception is independent of language_check.
        for call in agent_conditional_calls:
            mapping = call.args[2] if len(call.args) >= 3 else call.kwargs.get("path_map", {})
            assert mapping.get("tools") == "watchover_check", (
                f"Expected 'tools' → 'watchover_check' in agent conditional "
                f"edges mapping, got: {mapping}"
            )

        # watchover_check node must be registered (manager was provided).
        add_node_calls = mock_graph_instance.add_node.call_args_list
        node_names = [call.args[0] for call in add_node_calls]
        assert "watchover_check" in node_names, (
            f"Expected 'watchover_check' in add_node calls, got: {node_names}"
        )

        # language_check node must NOT be registered (language_check_enabled=False).
        assert "language_check" not in node_names, (
            "language_check should NOT be registered when language_check_enabled=False"
        )


# =============================================================================
# T1.0b — Kill-switch
# =============================================================================


class TestWatchoverKillSwitch:
    """The ``WATCHOVER_ENABLED`` env flag is the global kill-switch.

    When ``False``, :meth:`WatchoverSlot.is_enabled` must return ``False``
    regardless of the per-instance flag — this is the zero-cost global
    disable path. No DB lookup should happen.
    """

    def test_watchover_disabled_env_passthrough(self, monkeypatch):
        """WATCHOVER_ENABLED=false → is_enabled() returns False even for enabled instances."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "false")
        manager = make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)

        assert slot.is_enabled("iid") is False

        # The per-instance checker must NOT have been called (zero-cost).
        manager.is_watchover_enabled.assert_not_called()

    def test_watchover_enabled_env_normal(self, monkeypatch):
        """WATCHOVER_ENABLED=true → is_enabled() respects per-instance flag."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")

        # Enabled instance.
        manager_on = make_manager(watchover_enabled=True)
        slot_on = WatchoverSlot(manager_on)
        assert slot_on.is_enabled("iid") is True

        # Disabled instance.
        manager_off = make_manager(watchover_enabled=False)
        slot_off = WatchoverSlot(manager_off)
        assert slot_off.is_enabled("iid") is False

    def test_kill_switch_defaults_to_true(self, monkeypatch):
        """When WATCHOVER_ENABLED is unset, the default is True (normal operation)."""
        monkeypatch.delenv("WATCHOVER_ENABLED", raising=False)
        manager = make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)

        assert slot.is_enabled("iid") is True

    def test_kill_switch_accepts_1_and_yes(self, monkeypatch):
        """WATCHOVER_ENABLED accepts '1' and 'yes' as truthy."""
        for val in ("1", "yes", "YES", "True"):
            monkeypatch.setenv("WATCHOVER_ENABLED", val)
            manager = make_manager(watchover_enabled=True)
            slot = WatchoverSlot(manager)
            assert slot.is_enabled("iid") is True, f"Failed for WATCHOVER_ENABLED={val!r}"


# =============================================================================
# create_watchover_check_node
# =============================================================================


class TestWatchoverCheckNode:
    """The ``watchover_check`` node — Phase 2 (real decision logic).

    The Phase 1 stubs have been replaced with real wiring (T2.1-T2.6).
    These tests exercise the fast-path passthroughs (kill-switch off,
    no tool_calls). Full Allow / Deny / 3-strike paths are covered in
    ``tests/unit/test_watchover_decision.py``.
    """

    async def test_passthrough_when_no_tool_calls(self, monkeypatch):
        """Last message without tool_calls → routes to ``tools``."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)
        node = create_watchover_check_node(
            manager=manager, slot=slot, llm_config={}
        )
        result = await node(_state_without_tool_calls(), config=_config("iid"))
        assert result == {"watchover_route": "tools"}

    async def test_passthrough_when_watchover_disabled(self, monkeypatch):
        """``WATCHOVER_ENABLED=false`` → kill-switch → routes to ``tools``."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "false")
        manager = make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)
        node = create_watchover_check_node(
            manager=manager, slot=slot, llm_config={}
        )
        result = await node(_state_with_tool_calls(), config=_config("iid"))
        assert result == {"watchover_route": "tools"}

    async def test_passthrough_when_instance_not_watched(self, monkeypatch):
        """Instance not in the per-instance enable set → routes to ``tools``."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=False)
        slot = WatchoverSlot(manager)
        node = create_watchover_check_node(
            manager=manager, slot=slot, llm_config={}
        )
        result = await node(_state_with_tool_calls(), config=_config("iid"))
        assert result == {"watchover_route": "tools"}

    async def test_handles_missing_config(self, monkeypatch):
        """Missing config (instance_id = None) → fast-path tools route."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        slot = WatchoverSlot(manager)
        node = create_watchover_check_node(
            manager=manager, slot=slot, llm_config={}
        )
        result = await node(_state_with_tool_calls(), config=None)
        assert result == {"watchover_route": "tools"}


# =============================================================================
# create_watchover_terminate_node
# =============================================================================


class TestWatchoverTerminateNode:
    """The ``watchover_terminate_node`` — C2-safe deferred marker + DB persist (TD-8)."""

    async def test_sets_deferred_marker_and_returns_empty(self, monkeypatch):
        """Node sets the deferred-terminate marker and returns ``{}``."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        slot = WatchoverSlot(manager)

        node = create_watchover_terminate_node(slot, manager=manager)
        result = await node({}, config=_config("iid"))

        # C2: deferred marker set via the slot.
        manager.set_deferred_watchover_terminate.assert_called_once_with("iid")
        # Returns empty dict so LangGraph routes to END.
        assert result == {}

    async def test_persists_db_marker_when_manager_provided(self, monkeypatch):
        """TD-8: node persists ``watchover_pending_termination=True`` to DB
        BEFORE the RAM marker.

        T5.1 updated the node to use ``set_metadata_many`` (writes both
        ``watchover_pending_termination`` and a timestamp anchor). T5.6
        added a best-effort SSE emit, so the mock manager must wire
        ``_live_hub.stream_message`` as an AsyncMock.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        slot = WatchoverSlot(manager)

        # Wire a fake _instance_repository with a stub set_metadata_many.
        repo = MagicMock()
        set_many = MagicMock(return_value=MagicMock())
        repo.set_metadata_many = set_many
        manager._instance_repository = repo
        # T5.6: the terminate node emits an SSE — wire an AsyncMock so
        # the best-effort emit does not interfere with the test.
        manager._live_hub = MagicMock()
        manager._live_hub.stream_message = AsyncMock()

        node = create_watchover_terminate_node(slot, manager=manager)
        await node({}, config=_config("iid"))

        # DB write happened with the right key (via set_metadata_many).
        set_many.assert_called_once()
        args = set_many.call_args.args
        assert args[0] == "iid"
        updates = args[1]
        assert updates["watchover_pending_termination"] is True
        # T5.1 timestamp anchor is also written.
        assert "watchover_pending_termination_at" in updates
        # RAM marker still set (the DB write is the crash-safety net,
        # the RAM marker is the normal path).
        manager.set_deferred_watchover_terminate.assert_called_once_with("iid")

    async def test_handles_missing_instance_id(self, monkeypatch):
        """Missing config → instance_id = None → logs warning, returns ``{}``."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        slot = WatchoverSlot(manager)

        node = create_watchover_terminate_node(slot, manager=manager)
        result = await node({}, config=None)

        assert result == {}
        manager.set_deferred_watchover_terminate.assert_not_called()

    async def test_no_manager_skips_db_persist(self, monkeypatch):
        """Phase 1 backwards compat: ``manager=None`` → only RAM marker set."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        slot = WatchoverSlot(manager)

        node = create_watchover_terminate_node(slot, manager=None)
        result = await node({}, config=_config("iid"))

        manager.set_deferred_watchover_terminate.assert_called_once_with("iid")
        assert result == {}


# =============================================================================
# should_end_watchover (Phase 2 router)
# =============================================================================


class TestShouldEndWatchover:
    """The ``watchover_check`` router — reads ``state["watchover_route"]``."""

    def test_returns_tools_when_route_hint_is_tools(self):
        """Allow path: state.watchover_route == 'tools' → return 'tools'."""
        result = should_end_watchover(
            {"watchover_route": "tools"}, config=_config("iid")
        )
        assert result == "tools"

    def test_returns_agent_when_route_hint_is_agent(self):
        """Deny path: state.watchover_route == 'agent' → return 'agent'."""
        result = should_end_watchover(
            {"watchover_route": "agent"}, config=_config("iid")
        )
        assert result == "agent"

    def test_returns_terminate_when_route_hint_is_terminate(self):
        """3-strike path: state.watchover_route == 'watchover_terminate_node'."""
        result = should_end_watchover(
            {"watchover_route": "watchover_terminate_node"},
            config=_config("iid"),
        )
        assert result == "watchover_terminate_node"

    def test_defaults_to_agent_when_route_hint_missing(self):
        """Empty state → fail-closed default to 'agent'."""
        result = should_end_watchover({}, config=_config("iid"))
        assert result == "agent"

    def test_defaults_to_agent_when_route_hint_invalid(self):
        """Invalid hint value → fail-closed default to 'agent'."""
        result = should_end_watchover(
            {"watchover_route": "bogus"}, config=_config("iid")
        )
        assert result == "agent"


# =============================================================================
# Manager watchover accessors
# =============================================================================


class TestManagerWatchoverAccessors:
    """``InstanceManager`` watchover accessors.

    Tests the accessor methods on a bare ``InstanceManager`` (``__new__``
    bypass) so no DB engine or MCP is required. The ``is_watchover_enabled``
    method reads ``instance_metadata`` from ``_instance_repository``.
    """

    def test_is_watchover_enabled_reads_metadata(self):
        """``is_watchover_enabled`` reads ``instance_metadata["watchover_enabled"]``."""
        manager = make_bare_manager()

        # Mock the instance repository.
        instance = MagicMock()
        instance.instance_metadata = {"watchover_enabled": True}
        repo = MagicMock()
        repo.get.return_value = instance
        manager._instance_repository = repo

        assert manager.is_watchover_enabled("iid") is True
        repo.get.assert_called_once_with("iid")

    def test_is_watchover_enabled_returns_false_when_flag_absent(self):
        """Missing ``watchover_enabled`` key → ``False``."""
        manager = make_bare_manager()
        instance = MagicMock()
        instance.instance_metadata = {"other_key": "value"}
        repo = MagicMock()
        repo.get.return_value = instance
        manager._instance_repository = repo

        assert manager.is_watchover_enabled("iid") is False

    def test_is_watchover_enabled_returns_false_when_metadata_none(self):
        """``instance_metadata`` is None → ``False``."""
        manager = make_bare_manager()
        instance = MagicMock()
        instance.instance_metadata = None
        repo = MagicMock()
        repo.get.return_value = instance
        manager._instance_repository = repo

        assert manager.is_watchover_enabled("iid") is False

    def test_is_watchover_enabled_returns_false_when_instance_not_found(self):
        """Unknown instance_id → ``False``."""
        manager = make_bare_manager()
        repo = MagicMock()
        repo.get.return_value = None
        manager._instance_repository = repo

        assert manager.is_watchover_enabled("iid") is False

    def test_is_watchover_enabled_returns_false_on_exception(self):
        """Repository exception → ``False`` (fail-safe)."""
        manager = make_bare_manager()
        repo = MagicMock()
        repo.get.side_effect = RuntimeError("DB down")
        manager._instance_repository = repo

        assert manager.is_watchover_enabled("iid") is False

    def test_deferred_terminate_lifecycle(self):
        """set → is → clear → is returns False (full lifecycle)."""
        manager = make_bare_manager()

        # Initially not set.
        assert manager.is_watchover_terminate_requested("iid") is False

        # Set the marker.
        manager.set_deferred_watchover_terminate("iid")
        assert manager.is_watchover_terminate_requested("iid") is True
        assert "iid" in manager._deferred_watchover_terminate

        # Clear it.
        manager.clear_watchover_terminate_requested("iid")
        assert manager.is_watchover_terminate_requested("iid") is False
        assert "iid" not in manager._deferred_watchover_terminate

    def test_clear_is_idempotent(self):
        """Clearing an un-set marker is a no-op (no exception)."""
        manager = make_bare_manager()
        manager.clear_watchover_terminate_requested("iid")  # should not raise

    def test_markers_are_per_instance(self):
        """Setting one instance's marker does not leak to another."""
        manager = make_bare_manager()
        manager.set_deferred_watchover_terminate("a")
        manager.set_deferred_watchover_terminate("b")

        assert manager.is_watchover_terminate_requested("a") is True
        assert manager.is_watchover_terminate_requested("b") is True

        manager.clear_watchover_terminate_requested("a")
        assert manager.is_watchover_terminate_requested("a") is False
        assert manager.is_watchover_terminate_requested("b") is True  # b survived
