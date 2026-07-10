"""Tests for ``daemon.tools.todo_tools.create_todo_tools``.

Mirrors the structure of ``tests/test_chart_tools.py``:

  1. **Factory** — returns 6 tools with the documented names
     (Phase 2 added ``todo_add_edge`` and ``todo_remove_edge``).
  2. **Registration** — each tool is tagged with ``_tool_category == "todo"``
     and NEVER ``"instance"`` (security counterpart of
     ``INNATE_SKILL_TOOL_CATEGORIES``).
  3. **Invocation** — each tool delegates to ``manager._todo_manager`` and
     returns the documented string format.

The mocking pattern is identical to chart_tools: ``MagicMock`` manager with
a real ``TodoManager`` attached at ``_todo_manager``. Tools are invoked via
``await tool.coroutine(...)`` (langchain ``@tool`` contract).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daemon.services.todo_manager import TodoManager


# =============================================================================
# Helpers
# =============================================================================


def _make_manager() -> MagicMock:
    """Build a mock ``InstanceManager`` with a real TodoManager.

    The todo tools only touch ``manager._todo_manager``; everything else on
    the manager is unused. A real ``TodoManager`` (not a MagicMock) is
    attached so we exercise actual state mutation semantics — the tool is
    a thin facade and shouldn't hide bugs in the manager.
    """
    manager = MagicMock()
    manager._todo_manager = TodoManager()
    return manager


def _build_tools(manager: MagicMock | None = None, live_event_hub=None):
    """Build the 6 todo tools with default instance_id.

    Returns the list ``[todo_create, todo_update, todo_list, todo_clear,
    todo_add_edge, todo_remove_edge]``.
    """
    from daemon.tools.todo_tools import create_todo_tools

    if manager is None:
        manager = _make_manager()
    return create_todo_tools(
        manager=manager,
        current_instance_id="test-instance-id",
        live_event_hub=live_event_hub,
    )


# =============================================================================
# Factory
# =============================================================================


class TestCreateTodoToolsFactory:
    """Factory shape for ``create_todo_tools``."""

    def test_factory_returns_list_of_six_tools(self):
        """Factory produces exactly the 6 documented tools (Phase 2: was 4)."""
        tools = _build_tools()

        assert isinstance(tools, list)
        assert len(tools) == 6

    def test_factory_returns_documented_tool_names(self):
        """The 6 tools are named ``todo_create``, ``todo_update``,
        ``todo_list``, ``todo_clear``, ``todo_add_edge``,
        ``todo_remove_edge`` — no more, no less."""
        tools = _build_tools()
        names = [t.name for t in tools]

        assert names == [
            "todo_create",
            "todo_update",
            "todo_list",
            "todo_clear",
            "todo_add_edge",
            "todo_remove_edge",
        ]

    def test_factory_creates_independent_closures_per_call(self):
        """Each call returns fresh tool objects (no shared closure state).

        Two factory calls with different ``current_instance_id`` must
        produce distinct tool objects — critical for per-instance tool
        lists so they don't cross-talk.
        """
        tools_a = _build_tools()  # closes over "test-instance-id"
        manager_b = _make_manager()
        from daemon.tools.todo_tools import create_todo_tools

        tools_b = create_todo_tools(
            manager=manager_b,
            current_instance_id="other-instance",
            live_event_hub=None,
        )

        # Same names, distinct objects.
        for ta, tb in zip(tools_a, tools_b):
            assert ta is not tb
            assert ta.name == tb.name


# =============================================================================
# Registration
# =============================================================================


class TestTodoToolRegistration:
    """Every tool must be tagged ``_tool_category == "todo"``."""

    def test_all_six_tools_registered_under_todo_category(self):
        """The decorator ``@register_tool_category(\"todo\")`` tags every tool."""
        tools = _build_tools()

        for tool in tools:
            assert getattr(tool, "_tool_category", None) == "todo", (
                f"Tool {tool.name} missing _tool_category='todo'"
            )

    def test_no_todo_tool_registered_under_instance_category(self):
        """SECURITY: todo tools must NOT inherit the ``"instance"`` category.

        Companion check to ``test_tool_filter.py`` for the
        ``INNATE_SKILL_TOOL_CATEGORIES`` allowlist. A todo-enabled agent
        must not be implicitly granted the full instance-management suite.
        """
        tools = _build_tools()

        for tool in tools:
            assert getattr(tool, "_tool_category", None) != "instance", (
                f"Tool {tool.name} incorrectly tagged as 'instance'"
            )


# =============================================================================
# todo_create
# =============================================================================


class TestTodoCreate:
    """``todo_create(items)`` — replace the list with all-pending items."""

    async def test_todo_create_returns_formatted_list(self):
        """Output includes the count, header, and per-item index/text/status."""
        tools = _build_tools()
        create_tool = tools[0]

        result = await create_tool.coroutine(
            items=["Buy milk", "Buy eggs"],
        )

        assert isinstance(result, str)
        assert "2 items" in result
        assert "Buy milk" in result
        assert "Buy eggs" in result
        # Status icons appear (○ for pending)
        assert "○" in result
        # Numeric indices appear
        assert "[0]" in result
        assert "[1]" in result

    async def test_todo_create_empty_list_message(self):
        """Creating with ``[]`` succeeds and reports ``0 items``.

        The empty-list output is the header ``"Todo list created with 0 items:"``
        followed by ``_format_list([])`` which yields ``"No todo items."``.
        No ``[N]`` per-item markers should appear.
        """
        tools = _build_tools()
        create_tool = tools[0]

        result = await create_tool.coroutine(items=[])

        assert "0 items" in result
        # _format_list([]) returns the documented empty-list message
        assert "No todo items." in result
        # No per-item index markers (e.g. "[0]", "[1]") should be present
        assert "[" not in result

    async def test_todo_create_persists_into_manager(self):
        """After ``todo_create``, ``manager._todo_manager.get_all`` sees the items."""
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        create_tool = tools[0]

        await create_tool.coroutine(items=["alpha", "beta", "gamma"])

        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored) == 3
        assert [item["text"] for item in stored] == ["alpha", "beta", "gamma"]
        # All pending
        assert all(item["status"] == "pending" for item in stored)


# =============================================================================
# todo_update
# =============================================================================


class TestTodoUpdate:
    """``todo_update(index, status)`` — mutate one item, return full list + reminder."""

    async def test_todo_update_returns_confirmation_and_reminder(self):
        """Updating an item returns the formatted list and the next pending reminder."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B", "C"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        result = await update_tool.coroutine(index=0, status="done")

        # Header indicates the update (Phase 2: lookup description includes
        # the kind of identifier that resolved the node — ``index=0`` here).
        assert "Updated index=0" in result
        assert "done" in result
        # Items are shown
        assert "A" in result and "B" in result and "C" in result
        # Next pending reminder points to B
        assert "Next:" in result
        assert "B" in result

    async def test_todo_update_all_done_returns_completion_message(self):
        """When no items remain pending, output says ``All items completed!``."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["Only task"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        result = await update_tool.coroutine(index=0, status="done")

        assert "All items completed!" in result
        # No 'Next:' reminder when nothing is left pending
        assert "Next:" not in result

    async def test_todo_update_invalid_index_returns_error_string(self):
        """Out-of-range index → ``ERROR:``-prefixed result, no mutation."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        result = await update_tool.coroutine(index=99, status="done")

        assert result.startswith("ERROR:")
        # Stored state unchanged
        assert manager._todo_manager.get_all("test-instance-id")[0]["status"] == "pending"

    async def test_todo_update_invalid_status_returns_error_string(self):
        """Bogus status → ``ERROR:``-prefixed result, no mutation.

        Note: ``"completed"`` is now a valid alias (→ ``done``) per W3 alias
        normalization, so we use a genuinely unknown value here.
        """
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        result = await update_tool.coroutine(index=0, status="blah")

        assert result.startswith("ERROR:")
        # State untouched
        assert manager._todo_manager.get_all("test-instance-id")[0]["status"] == "pending"

    async def test_todo_update_persists_status_change(self):
        """After ``todo_update``, the stored item reflects the new status."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        await update_tool.coroutine(index=1, status="in_progress")

        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["status"] == "pending"
        assert stored[1]["status"] == "in_progress"


# =============================================================================
# todo_list
# =============================================================================


class TestTodoList:
    """``todo_list()`` — read-only display of current todos."""

    async def test_todo_list_shows_all_items_with_icons(self):
        """All items appear in the output with their index and text."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["Task X", "Task Y"])
        # Phase 2: TodoGraphManager.update() keys on string node_id, not int.
        # Use the backward-compat shim ``update_by_index`` for index-based
        # mutations (it resolves int → node_id under the hood).
        manager._todo_manager.update_by_index("test-instance-id", 0, "done")
        manager._todo_manager.update_by_index(
            "test-instance-id", 1, "in_progress"
        )
        tools = _build_tools(manager=manager)
        list_tool = tools[2]

        result = await list_tool.coroutine()

        assert "Task X" in result
        assert "Task Y" in result
        assert "[0]" in result
        assert "[1]" in result
        # Both icons rendered
        assert "●" in result   # done icon for Task X
        assert "◐" in result   # in_progress icon for Task Y

    async def test_todo_list_empty_reports_no_items(self):
        """Empty list → documented ``No todo items.`` message."""
        tools = _build_tools()
        list_tool = tools[2]

        result = await list_tool.coroutine()

        assert "No todo items." in result

    async def test_todo_list_header_included(self):
        """Output starts with the documented ``📋 Current todo graph:`` header."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        list_tool = tools[2]

        result = await list_tool.coroutine()

        assert "Current todo graph:" in result


# =============================================================================
# todo_clear
# =============================================================================


class TestTodoClear:
    """``todo_clear()`` — drop the list entirely and emit empty payload."""

    async def test_todo_clear_returns_confirmation(self):
        """``todo_clear`` returns the documented trash-can confirmation."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B"])
        tools = _build_tools(manager=manager)
        clear_tool = tools[3]

        result = await clear_tool.coroutine()

        assert isinstance(result, str)
        # Trash-can emoji + "cleared" wording is documented
        assert "cleared" in result.lower()

    async def test_todo_clear_empties_state(self):
        """After clear, ``get_all`` returns ``[]``."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B", "C"])
        tools = _build_tools(manager=manager)
        clear_tool = tools[3]

        await clear_tool.coroutine()

        assert manager._todo_manager.get_all("test-instance-id") == []

    async def test_todo_clear_when_empty_succeeds(self):
        """Clearing an already-empty list is a no-op that still returns the message."""
        manager = _make_manager()
        # No items created at all.
        tools = _build_tools(manager=manager)
        clear_tool = tools[3]

        result = await clear_tool.coroutine()

        assert "cleared" in result.lower()


# =============================================================================
# todo_create — graph mode (Phase 2)
# =============================================================================


class TestTodoCreateGraphMode:
    """``todo_create(nodes, edges)`` — explicit graph input."""

    async def test_todo_create_nodes_only_builds_independent_nodes(self):
        """Passing ``nodes`` without ``edges`` creates isolated nodes
        (no edges). All nodes share ``pending`` status.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        create_tool = tools[0]

        result = await create_tool.coroutine(
            nodes=[
                {"id": "alpha", "text": "Step Alpha"},
                {"id": "beta", "text": "Step Beta"},
            ],
        )

        # Header advertises graph mode
        assert "Todo graph created" in result
        assert "2 nodes" in result
        # Both nodes visible
        assert "Step Alpha" in result
        assert "Step Beta" in result
        # Stored state matches input
        stored = manager._todo_manager.get_all("test-instance-id")
        assert {n["id"] for n in stored} == {"alpha", "beta"}

    async def test_todo_create_with_edges_renders_branching_graph(self):
        """Passing ``nodes`` + ``edges`` produces a graph whose render uses
        the ``└→`` arrow for children and merges on shared descendants.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        create_tool = tools[0]

        result = await create_tool.coroutine(
            nodes=[
                {"id": "root", "text": "Root"},
                {"id": "left", "text": "Left branch"},
                {"id": "right", "text": "Right branch"},
                {"id": "sink", "text": "Merge sink"},
            ],
            edges=[
                {"from": "root", "to": "left"},
                {"from": "root", "to": "right"},
                {"from": "left", "to": "sink"},
                {"from": "right", "to": "sink"},
            ],
        )

        # Tree connector appears
        assert "\u2514\u2192" in result
        # Merge annotation appears on the second visit of ``sink``
        assert "(merged)" in result
        # All four nodes visible
        assert "Root" in result
        assert "Left branch" in result
        assert "Right branch" in result
        assert "Merge sink" in result

    async def test_todo_create_with_neither_returns_error(self):
        """Calling ``todo_create()`` with no ``items`` and no ``nodes``
        returns ``ERROR:`` and does not mutate state.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        create_tool = tools[0]

        result = await create_tool.coroutine()

        assert result.startswith("ERROR:")
        assert manager._todo_manager.get_all("test-instance-id") == []

    async def test_todo_create_items_takes_precedence_over_nodes(self):
        """When both ``items`` and ``nodes`` are provided, ``items`` wins
        (backward-compat path is taken).
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        create_tool = tools[0]

        result = await create_tool.coroutine(
            items=["Flat A", "Flat B"],
            nodes=[{"id": "ignored", "text": "should not appear"}],
        )

        # Header reflects flat-list path
        assert "Todo list created" in result
        assert "Flat A" in result
        assert "Flat B" in result
        assert "should not appear" not in result


# =============================================================================
# todo_update — node_id path (Phase 2)
# =============================================================================


class TestTodoUpdateNodeId:
    """``todo_update(node_id=..., status=...)`` — graph-aware lookup."""

    async def test_todo_update_by_node_id_returns_confirmation(self):
        """Updating by ``node_id`` succeeds and the lookup description
        advertises the ``node_id=...`` form (vs the ``index=N`` form).
        """
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        # Fetch a node_id from the manager
        stored = manager._todo_manager.get_all("test-instance-id")
        target_id = stored[0]["id"]

        result = await update_tool.coroutine(node_id=target_id, status="done")

        assert f"Updated node_id={target_id!r}" in result
        assert "done" in result

    async def test_todo_update_node_id_takes_precedence_over_index(self):
        """When both ``node_id`` and ``index`` are provided, ``node_id``
        wins (documented precedence rule).
        """
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        stored = manager._todo_manager.get_all("test-instance-id")
        target_id = stored[1]["id"]  # update the SECOND node

        # Pass index=0 (would update "A") AND node_id=<B's id> → should update B
        result = await update_tool.coroutine(
            index=0, status="done", node_id=target_id
        )

        # Lookup description should reflect node_id path
        assert f"node_id={target_id!r}" in result
        # State should reflect B (index=1) being done, not A (index=0)
        post = manager._todo_manager.get_all("test-instance-id")
        assert post[0]["status"] == "pending"
        assert post[1]["status"] == "done"

    async def test_todo_update_with_neither_index_nor_node_id_returns_error(self):
        """Calling ``todo_update(status='done')`` with no identifier
        returns ``ERROR:`` without mutation.
        """
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        result = await update_tool.coroutine(status="done")

        assert result.startswith("ERROR:")
        assert "Provide either index or node_id" in result


# =============================================================================
# todo_add_edge (Phase 2)
# =============================================================================


class TestTodoAddEdge:
    """``todo_add_edge(from_id, to_id)`` — incremental edge insertion."""

    async def test_todo_add_edge_returns_confirmation_and_graph(self):
        """Adding a valid edge returns ``Edge added:`` and the updated graph."""
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
            ],
            edges=[{"from": "a", "to": "b"}],
        )
        tools = _build_tools(manager=manager)
        add_edge_tool = tools[4]

        # Create a third node via create_graph, then add an edge to it
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
                {"id": "c", "text": "C"},
            ],
            edges=[
                {"from": "a", "to": "b"},
                {"from": "b", "to": "c"},
            ],
        )

        result = await add_edge_tool.coroutine(from_id="a", to_id="c")

        assert "Edge added" in result
        assert "a \u2192 c" in result
        # Graph state shows the new edge
        graph = manager._todo_manager.get_graph("test-instance-id")
        edges = {(e["from"], e["to"]) for e in graph["edges"]}
        assert ("a", "c") in edges

    async def test_todo_add_edge_cycle_returns_error(self):
        """Adding an edge that would create a cycle is rejected and
        returns ``ERROR:``. State is not mutated.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
            ],
            edges=[{"from": "a", "to": "b"}],
        )
        tools = _build_tools(manager=manager)
        add_edge_tool = tools[4]

        result = await add_edge_tool.coroutine(from_id="b", to_id="a")

        assert result.startswith("ERROR:")
        # State unchanged
        graph = manager._todo_manager.get_graph("test-instance-id")
        edges = {(e["from"], e["to"]) for e in graph["edges"]}
        assert ("b", "a") not in edges

    async def test_todo_add_edge_unknown_node_returns_error(self):
        """Adding an edge referencing a missing node returns ``ERROR:``."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        add_edge_tool = tools[4]

        result = await add_edge_tool.coroutine(from_id="n-aaaa", to_id="n-bbbb")

        assert result.startswith("ERROR:")


# =============================================================================
# todo_remove_edge (Phase 2)
# =============================================================================


class TestTodoRemoveEdge:
    """``todo_remove_edge(from_id, to_id)`` — incremental edge removal."""

    async def test_todo_remove_edge_returns_confirmation(self):
        """Removing an existing edge returns ``Edge removed:`` and the
        updated graph (without that edge).
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
            ],
            edges=[{"from": "a", "to": "b"}],
        )
        tools = _build_tools(manager=manager)
        remove_edge_tool = tools[5]

        result = await remove_edge_tool.coroutine(from_id="a", to_id="b")

        assert "Edge removed" in result
        assert "a \u2192 b" in result
        # State confirms edge gone
        graph = manager._todo_manager.get_graph("test-instance-id")
        edges = {(e["from"], e["to"]) for e in graph["edges"]}
        assert ("a", "b") not in edges

    async def test_todo_remove_edge_missing_returns_error(self):
        """Removing a non-existent edge returns ``ERROR:`` without mutation."""
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {"id": "a", "text": "A"},
                {"id": "b", "text": "B"},
            ],
            edges=[{"from": "a", "to": "b"}],
        )
        tools = _build_tools(manager=manager)
        remove_edge_tool = tools[5]

        # No edge b→c exists
        result = await remove_edge_tool.coroutine(from_id="b", to_id="c")

        assert result.startswith("ERROR:")

    async def test_todo_remove_edge_unknown_node_returns_error(self):
        """Removing with an unknown node returns ``ERROR:``."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        remove_edge_tool = tools[5]

        result = await remove_edge_tool.coroutine(
            from_id="n-aaaa", to_id="n-bbbb"
        )

        assert result.startswith("ERROR:")
