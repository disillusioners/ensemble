"""Tests for ``daemon.tools.todo_tools.create_todo_tools``.

Mirrors the structure of ``tests/test_chart_tools.py``:

  1. **Factory** — returns 11 tools with the documented names, split into
     two sets: ``todo_list_*`` (flat, index-based), ``todo_graph_*`` (DAG,
     node_id-based), plus shared ``todo_view`` / ``todo_clear``.
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
    """Build the 11 todo tools with default instance_id.

    Returns the list ``[todo_list_create, todo_list_update,
    todo_graph_create, todo_graph_update, todo_graph_add_edge,
    todo_graph_remove_edge, todo_graph_add_subtask,
    todo_graph_update_subtask, todo_graph_remove_subtask, todo_view,
    todo_clear]``.
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

    def test_factory_returns_list_of_eleven_tools(self):
        """Factory produces exactly the 11 documented tools.

        The todo surface is split into two sets by planning shape —
        ``todo_list_*`` (flat, index-based) and ``todo_graph_*`` (DAG,
        node_id-based) — plus two shared tools (``todo_view``,
        ``todo_clear``).
        """
        tools = _build_tools()

        assert isinstance(tools, list)
        assert len(tools) == 11

    def test_factory_returns_documented_tool_names(self):
        """The 11 tools are named in canonical order: list-set, graph-set,
        then shared."""
        tools = _build_tools()
        names = [t.name for t in tools]

        assert names == [
            "todo_list_create",
            "todo_list_update",
            "todo_graph_create",
            "todo_graph_update",
            "todo_graph_add_edge",
            "todo_graph_remove_edge",
            "todo_graph_add_subtask",
            "todo_graph_update_subtask",
            "todo_graph_remove_subtask",
            "todo_view",
            "todo_clear",
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

    def test_all_tools_registered_under_todo_category(self):
        """The decorator ``@register_tool_category(\"todo")`` tags every tool."""
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
# todo_list_create
# =============================================================================


class TestTodoListCreate:
    """``todo_list_create(items)`` — replace the list with all-pending items."""

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
        """After ``todo_list_create``, ``manager._todo_manager.get_all`` sees the items."""
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
# todo_list_update
# =============================================================================


class TestTodoListUpdate:
    """``todo_list_update(index, status)`` — mutate one item, return full list + reminder."""

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
        """After ``todo_list_update``, the stored item reflects the new status."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B"])
        tools = _build_tools(manager=manager)
        update_tool = tools[1]

        await update_tool.coroutine(index=1, status="in_progress")

        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["status"] == "pending"
        assert stored[1]["status"] == "in_progress"


# =============================================================================
# todo_view
# =============================================================================


class TestTodoView:
    """``todo_view()`` — read-only display of current todos."""

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
        list_tool = tools[9]

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
        list_tool = tools[9]

        result = await list_tool.coroutine()

        assert "No todo items." in result

    async def test_todo_list_header_included(self):
        """Output starts with the documented ``📋 Current todo graph:`` header."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        list_tool = tools[9]

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
        clear_tool = tools[10]

        result = await clear_tool.coroutine()

        assert isinstance(result, str)
        # Trash-can emoji + "cleared" wording is documented
        assert "cleared" in result.lower()

    async def test_todo_clear_empties_state(self):
        """After clear, ``get_all`` returns ``[]``."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B", "C"])
        tools = _build_tools(manager=manager)
        clear_tool = tools[10]

        await clear_tool.coroutine()

        assert manager._todo_manager.get_all("test-instance-id") == []

    async def test_todo_clear_when_empty_succeeds(self):
        """Clearing an already-empty list is a no-op that still returns the message."""
        manager = _make_manager()
        # No items created at all.
        tools = _build_tools(manager=manager)
        clear_tool = tools[10]

        result = await clear_tool.coroutine()

        assert "cleared" in result.lower()


# =============================================================================
# todo_graph_create
# =============================================================================


class TestTodoGraphCreate:
    """``todo_graph_create(nodes, edges)`` — explicit graph input."""

    async def test_todo_create_nodes_only_builds_independent_nodes(self):
        """Passing ``nodes`` without ``edges`` creates isolated nodes
        (no edges). All nodes share ``pending`` status.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        create_tool = tools[2]

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
        create_tool = tools[2]

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
        """Calling ``todo_graph_create()`` with no ``nodes``
        returns ``ERROR:`` and does not mutate state.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        create_tool = tools[2]

        result = await create_tool.coroutine()

        assert result.startswith("ERROR:")
        assert manager._todo_manager.get_all("test-instance-id") == []


# =============================================================================
# todo_graph_update — node_id path
# =============================================================================


class TestTodoGraphUpdate:
    """``todo_graph_update(node_id=..., status=...)`` — graph-aware lookup."""

    async def test_todo_update_by_node_id_returns_confirmation(self):
        """Updating by ``node_id`` succeeds and the lookup description
        advertises the ``node_id=...`` form (vs the ``index=N`` form).
        """
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A", "B"])
        tools = _build_tools(manager=manager)
        update_tool = tools[3]

        # Fetch a node_id from the manager
        stored = manager._todo_manager.get_all("test-instance-id")
        target_id = stored[0]["id"]

        result = await update_tool.coroutine(node_id=target_id, status="done")

        assert f"Updated node_id={target_id!r}" in result
        assert "done" in result


# =============================================================================
# todo_graph_add_edge
# =============================================================================


class TestTodoAddEdge:
    """``todo_graph_add_edge(from_id, to_id)`` — incremental edge insertion."""

    async def test_todo_graph_add_edge_returns_confirmation_and_graph(self):
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

    async def test_todo_graph_add_edge_cycle_returns_error(self):
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

    async def test_todo_graph_add_edge_unknown_node_returns_error(self):
        """Adding an edge referencing a missing node returns ``ERROR:``."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        add_edge_tool = tools[4]

        result = await add_edge_tool.coroutine(from_id="n-aaaa", to_id="n-bbbb")

        assert result.startswith("ERROR:")


# =============================================================================
# todo_graph_remove_edge (Phase 2)
# =============================================================================


class TestTodoRemoveEdge:
    """``todo_graph_remove_edge(from_id, to_id)`` — incremental edge removal."""

    async def test_todo_graph_remove_edge_returns_confirmation(self):
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

    async def test_todo_graph_remove_edge_missing_returns_error(self):
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

    async def test_todo_graph_remove_edge_unknown_node_returns_error(self):
        """Removing with an unknown node returns ``ERROR:``."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        remove_edge_tool = tools[5]

        result = await remove_edge_tool.coroutine(
            from_id="n-aaaa", to_id="n-bbbb"
        )

        assert result.startswith("ERROR:")


# =============================================================================
# todo_graph_add_subtask (Sub-Task Phase 1)
# =============================================================================


class TestTodoAddSubtask:
    """``todo_graph_add_subtask(node_id, text)`` — append a binary checklist item."""

    async def test_todo_graph_add_subtask_returns_confirmation_with_subtask_id(self):
        """Adding a sub-task returns a confirmation line containing the
        auto-generated ``s-``-prefixed sub-task id, plus the formatted
        graph with the new item visible (``\u2610`` icon for pending).
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha task"}],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", text="Write unit tests"
        )

        # Confirmation line includes node_id and a generated sub-task id
        assert "Added sub-task" in result
        assert "alpha" in result
        assert "s-" in result
        # Pending sub-task icon appears in the rendered graph
        assert "\u2610" in result
        assert "Write unit tests" in result

        # State mutated: parent node now has 1 pending sub-task
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1
        assert stored[0]["subtasks"][0]["text"] == "Write unit tests"
        assert stored[0]["subtasks"][0]["status"] == "pending"

    async def test_todo_graph_add_subtask_node_not_found_returns_error(self):
        """``node_id`` that does not exist returns ``ERROR:`` without
        creating any sub-task state.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="n-missing", text="orphan"
        )

        assert result.startswith("ERROR:")
        assert "n-missing" in result

    async def test_todo_graph_add_subtask_max_exceeded_returns_error(self):
        """When the node already has ``MAX_SUBTASKS_PER_NODE`` (20) sub-tasks,
        an additional ``todo_graph_add_subtask`` call returns ``ERROR:`` without
        mutating state.
        """
        from daemon.services.todo_manager import MAX_SUBTASKS_PER_NODE

        manager = _make_manager()
        # Seed with 20 sub-tasks via the manager directly (bypassing the
        # tool so we exercise only the cap-detection path).
        seed_result = manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {
                    "id": "alpha",
                    "text": "Alpha",
                    "subtasks": [
                        {"text": f"item {i}"}
                        for i in range(MAX_SUBTASKS_PER_NODE)
                    ],
                }
            ],
            edges=[],
        )
        assert len(seed_result[0]["subtasks"]) == MAX_SUBTASKS_PER_NODE

        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", text="overflow"
        )

        assert result.startswith("ERROR:")
        # State unchanged — still exactly MAX sub-tasks.
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == MAX_SUBTASKS_PER_NODE


# =============================================================================
# todo_graph_add_subtask — batch (list[str]) mode
# =============================================================================


class TestTodoAddSubtaskBatch:
    """``todo_graph_add_subtask(node_id, text)`` accepts a ``list[str]`` for
    atomic batched insertion.
    """

    async def test_batch_returns_confirmation_with_all_ids(self):
        """Passing a list appends every item in one call; the confirmation
        line lists the count and every generated ``s-``-prefixed id, and
        the rendered graph shows all items.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha task"}],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha",
            text=["Create schema", "Run migration", "Seed data"],
        )

        assert "Added 3 sub-tasks" in result
        assert "alpha" in result
        # Three s-prefixed ids appear in the confirmation line.
        assert result.count("s-") >= 3
        for label in ("Create schema", "Run migration", "Seed data"):
            assert label in result

        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 3
        assert [st["text"] for st in stored[0]["subtasks"]] == [
            "Create schema",
            "Run migration",
            "Seed data",
        ]
        assert all(st["status"] == "pending" for st in stored[0]["subtasks"])

    async def test_single_string_still_uses_singular_confirmation(self):
        """Backward compat: a bare string still adds exactly one sub-task
        and uses the singular ``Added sub-task '<id>'`` confirmation.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha task"}],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", text="Write unit tests"
        )

        assert "Added sub-task" in result
        assert "s-" in result
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1

    async def test_batch_node_not_found_returns_error(self):
        """A missing node on a batch call returns ``ERROR:`` without
        creating any state.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="n-missing", text=["a", "b"]
        )

        assert result.startswith("ERROR:")
        assert "n-missing" in result

    async def test_batch_combined_cap_exceeded_returns_error_atomically(
        self,
    ):
        """When a batch would push the node over the cap, the tool returns
        ``ERROR:`` and NONE of the batched items are appended.
        """
        from daemon.services.todo_manager import MAX_SUBTASKS_PER_NODE

        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {
                    "id": "alpha",
                    "text": "Alpha",
                    "subtasks": [
                        {"text": f"item {i}"}
                        for i in range(MAX_SUBTASKS_PER_NODE - 1)
                    ],
                }
            ],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        # Two more would exceed the cap (cap - 1 + 2 = cap + 1).
        result = await add_subtask_tool.coroutine(
            node_id="alpha", text=["over-1", "over-2"]
        )

        assert result.startswith("ERROR:")
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == MAX_SUBTASKS_PER_NODE - 1
        assert not any(
            st["text"] in ("over-1", "over-2")
            for st in stored[0]["subtasks"]
        )

    async def test_batch_empty_entry_returns_error_atomically(self):
        """An empty string in the batch rejects the whole call; nothing is
        appended (atomic all-or-nothing).
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha task"}],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", text=["ok", "", "also-ok"]
        )

        assert result.startswith("ERROR:")
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["subtasks"] == []


# =============================================================================
# todo_graph_update_subtask (Sub-Task Phase 1)
# =============================================================================


class TestTodoUpdateSubtask:
    """``todo_graph_update_subtask(node_id, subtask_id, status, auto_complete)``."""

    async def test_todo_graph_update_subtask_pending_to_done_returns_confirmation(self):
        """Flipping a pending sub-task to ``done`` returns a confirmation
        line naming the sub-task id, and the rendered graph now shows the
        ``\u2611`` done icon.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha"}],
            edges=[],
        )
        add_result = manager._todo_manager.add_subtask(
            "test-instance-id", "alpha", "Write tests"
        )
        subtask_id = add_result["todos"][0]["subtasks"][0]["id"]
        tools = _build_tools(manager=manager)
        update_subtask_tool = tools[7]

        result = await update_subtask_tool.coroutine(
            node_id="alpha",
            subtask_id=subtask_id,
            status="done",
        )

        # Header advertises the update
        assert "Updated sub-task" in result
        assert subtask_id in result
        assert "done" in result
        # Done icon appears in the rendered graph
        assert "\u2611" in result

        # State mutated: subtask is now done
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["subtasks"][0]["status"] == "done"

    async def test_todo_graph_update_subtask_auto_complete_all_done_flips_parent(self):
        """When ``auto_complete=True`` and ALL sub-tasks are done, the
        parent's status flips to ``done`` and the response includes the
        ``"Parent node ... auto-completed"`` confirmation line.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha"}],
            edges=[],
        )
        # Seed with one sub-task so the vacuous-truth guard is satisfied.
        add_result = manager._todo_manager.add_subtask(
            "test-instance-id", "alpha", "Single subtask"
        )
        subtask_id = add_result["todos"][0]["subtasks"][0]["id"]
        tools = _build_tools(manager=manager)
        update_subtask_tool = tools[7]

        result = await update_subtask_tool.coroutine(
            node_id="alpha",
            subtask_id=subtask_id,
            status="done",
            auto_complete=True,
        )

        assert "auto-completed" in result
        assert "alpha" in result

        # State mutated: parent node status is now "done"
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["status"] == "done"

    async def test_todo_graph_update_subtask_auto_complete_remaining_pending(self):
        """When ``auto_complete=True`` but NOT all sub-tasks are done,
        the parent is NOT auto-completed and the response includes the
        ``"N sub-task(s) remain pending"`` note.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha"}],
            edges=[],
        )
        # Seed two pending sub-tasks.
        ids: list[str] = []
        for text in ("first", "second"):
            add_result = manager._todo_manager.add_subtask(
                "test-instance-id", "alpha", text
            )
            ids.append(add_result["todos"][0]["subtasks"][-1]["id"])
        tools = _build_tools(manager=manager)
        update_subtask_tool = tools[7]

        # Mark only the FIRST one done with auto_complete=True. The
        # second is still pending -> parent must NOT auto-complete.
        result = await update_subtask_tool.coroutine(
            node_id="alpha",
            subtask_id=ids[0],
            status="done",
            auto_complete=True,
        )

        assert "1 sub-task(s) remain pending" in result
        # Parent stays pending
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["status"] == "pending"

    async def test_todo_graph_update_subtask_auto_complete_false_no_propagation(self):
        """Default ``auto_complete=False`` (omitted) means the parent's
        status is NEVER touched by sub-task updates, even when every
        sub-task is done.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha"}],
            edges=[],
        )
        add_result = manager._todo_manager.add_subtask(
            "test-instance-id", "alpha", "single"
        )
        subtask_id = add_result["todos"][0]["subtasks"][0]["id"]
        tools = _build_tools(manager=manager)
        update_subtask_tool = tools[7]

        # auto_complete NOT passed -> defaults to False.
        result = await update_subtask_tool.coroutine(
            node_id="alpha",
            subtask_id=subtask_id,
            status="done",
        )

        # No auto-completion note in the response
        assert "auto-completed" not in result
        assert "remain pending" not in result

        # Parent status unchanged (still pending)
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["status"] == "pending"

    async def test_todo_graph_update_subtask_invalid_status_returns_error(self):
        """Sub-task statuses are STRICTLY BINARY -- passing
        ``"in_progress"`` (and its aliases) returns ``ERROR:`` without
        mutating state.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha"}],
            edges=[],
        )
        add_result = manager._todo_manager.add_subtask(
            "test-instance-id", "alpha", "x"
        )
        subtask_id = add_result["todos"][0]["subtasks"][0]["id"]
        tools = _build_tools(manager=manager)
        update_subtask_tool = tools[7]

        result = await update_subtask_tool.coroutine(
            node_id="alpha",
            subtask_id=subtask_id,
            status="in_progress",  # rejected for sub-tasks
        )

        assert result.startswith("ERROR:")
        # Sub-task status unchanged
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["subtasks"][0]["status"] == "pending"

    async def test_todo_graph_update_subtask_not_found_returns_error(self):
        """Unknown ``node_id`` OR unknown ``subtask_id`` returns
        ``ERROR:`` without mutating state.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        update_subtask_tool = tools[7]

        # No instance exists -- both lookups miss.
        result = await update_subtask_tool.coroutine(
            node_id="n-missing",
            subtask_id="s-missing",
            status="done",
        )

        assert result.startswith("ERROR:")
        assert "n-missing" in result
        assert "s-missing" in result

    async def test_todo_graph_update_subtask_auto_complete_parent_already_done(self):
        """When ``auto_complete=True`` and ALL sub-tasks are done but the
        parent node's status is ALREADY ``"done"``, the tool reports the
        parent-already-done case explicitly — not the misleading
        ``"0 sub-task(s) remain pending"`` wording that this branch
        produced before the W2 fix.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha"}],
            edges=[],
        )
        # Seed two sub-tasks.
        ids: list[str] = []
        for text in ("first", "second"):
            add_result = manager._todo_manager.add_subtask(
                "test-instance-id", "alpha", text
            )
            ids.append(add_result["todos"][0]["subtasks"][-1]["id"])
        tools = _build_tools(manager=manager)
        update_subtask_tool = tools[7]
        update_tool = tools[3]

        # Mark both sub-tasks done WITHOUT auto_complete (default
        # False), so the parent stays ``pending``.
        for sid in ids:
            await update_subtask_tool.coroutine(
                node_id="alpha",
                subtask_id=sid,
                status="done",
            )
        # Now set the parent node to ``"done"`` via todo_graph_update.
        await update_tool.coroutine(node_id="alpha", status="done")

        # Sanity check: parent is done and all sub-tasks are done.
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["status"] == "done"
        assert all(st["status"] == "done" for st in stored[0]["subtasks"])

        # Call todo_graph_update_subtask with auto_complete=True on one
        # already-done sub-task. The manager will NOT re-flip the
        # parent (it's already done) and returns auto_completed=False,
        # so the tool's ``else`` branch fires with pending_count == 0
        # and should emit the parent-already-done message.
        result = await update_subtask_tool.coroutine(
            node_id="alpha",
            subtask_id=ids[0],
            status="done",
            auto_complete=True,
        )

        # The new (W2) message is present.
        assert "already" in result
        assert "auto_complete requested" in result
        # The misleading old "0 ... remain pending" wording is gone.
        assert "remain pending" not in result
        # Parent status is unchanged (still done) — no demotion.
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["status"] == "done"


# =============================================================================
# todo_graph_remove_subtask (Sub-Task Phase 1)
# =============================================================================


class TestTodoRemoveSubtask:
    """``todo_graph_remove_subtask(node_id, subtask_id)`` -- delete a sub-task."""

    async def test_todo_graph_remove_subtask_returns_confirmation(self):
        """Removing an existing sub-task returns a confirmation line and
        shrinks the parent's checklist.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[{"id": "alpha", "text": "Alpha"}],
            edges=[],
        )
        # Seed two sub-tasks.
        ids: list[str] = []
        for text in ("keep", "drop"):
            add_result = manager._todo_manager.add_subtask(
                "test-instance-id", "alpha", text
            )
            ids.append(add_result["todos"][0]["subtasks"][-1]["id"])
        tools = _build_tools(manager=manager)
        remove_subtask_tool = tools[8]

        result = await remove_subtask_tool.coroutine(
            node_id="alpha", subtask_id=ids[1]
        )

        assert "Removed sub-task" in result
        assert ids[1] in result
        assert "alpha" in result

        # State: only "keep" remains
        stored = manager._todo_manager.get_all("test-instance-id")
        remaining = stored[0]["subtasks"]
        assert len(remaining) == 1
        assert remaining[0]["id"] == ids[0]
        assert remaining[0]["text"] == "keep"

    async def test_todo_graph_remove_subtask_not_found_returns_error(self):
        """Unknown ``node_id`` OR unknown ``subtask_id`` returns
        ``ERROR:`` without mutating state.
        """
        manager = _make_manager()
        tools = _build_tools(manager=manager)
        remove_subtask_tool = tools[8]

        result = await remove_subtask_tool.coroutine(
            node_id="n-missing", subtask_id="s-missing"
        )

        assert result.startswith("ERROR:")


# =============================================================================
# Factory — _full_doc_ attribute on the three new sub-task tools
# =============================================================================


class TestFactoryFullDocSubtask:
    """All 11 tools carry a ``_full_doc_`` string used by the agent's
    tool-discovery layer. Each tool must advertise its full doc.
    """

    def test_all_tools_carry_full_doc_attribute(self):
        """Every tool in the factory has ``_full_doc_`` set to a non-empty
        string. Guards against a missing string assignment silently breaking
        the tool-description endpoint.
        """
        tools = _build_tools()

        for tool in tools:
            doc = getattr(tool, "_full_doc_", None)
            assert isinstance(doc, str), (
                f"Tool {tool.name} missing or non-string _full_doc_ attribute"
            )
            assert doc, f"Tool {tool.name} has empty _full_doc_ string"

    def test_three_subtask_tools_carry_full_doc_attribute(self):
        """``todo_graph_add_subtask``, ``todo_graph_update_subtask``,
        ``todo_graph_remove_subtask`` — the three graph sub-task tools —
        each carry a ``_full_doc_`` string.
        """
        tools = _build_tools()
        by_name = {t.name: t for t in tools}

        new_tools = [
            "todo_graph_add_subtask",
            "todo_graph_update_subtask",
            "todo_graph_remove_subtask",
        ]
        for name in new_tools:
            assert name in by_name, f"Factory missing {name!r}"
            doc = getattr(by_name[name], "_full_doc_", None)
            assert isinstance(doc, str), (
                f"{name} missing or non-string _full_doc_"
            )
            assert doc, f"{name} has empty _full_doc_"


# =============================================================================
# _format_graph -- sub-task rendering (Sub-Task Phase 1)
# =============================================================================


class TestFormatGraphSubtasks:
    """``_format_graph`` renders sub-tasks as ``\u2610``/``\u2611``
    checklists with verbose truncation at 5 items per node.
    """

    async def test_format_graph_renders_subtasks_in_linear_mode(self):
        """Linear-mode rendering emits ``\u2610`` for pending and
        ``\u2611`` for done sub-tasks, indented four spaces under the
        parent node.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {
                    "id": "alpha",
                    "text": "Alpha",
                    "subtasks": [
                        {"text": "todo item"},
                        {"text": "done item", "status": "done"},
                    ],
                }
            ],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        list_tool = tools[9]

        result = await list_tool.coroutine()

        # Node line present
        assert "Alpha" in result
        # Pending icon for the first sub-task
        assert "\u2610" in result
        # Done icon for the second sub-task
        assert "\u2611" in result
        # Both texts visible
        assert "todo item" in result
        assert "done item" in result

    async def test_format_graph_verbose_truncates_at_five(self):
        """With 7 sub-tasks on one node, ``verbose=False`` (default)
        shows 5 + ``+2 more`` marker; ``verbose=True`` shows all 7.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {
                    "id": "alpha",
                    "text": "Alpha",
                    "subtasks": [{"text": f"item {i}"} for i in range(7)],
                }
            ],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        list_tool = tools[9]

        # Default (verbose=False) -- truncate at 5
        compact = await list_tool.coroutine()
        assert "+2 more" in compact
        # Items 0..4 visible, item 5 and 6 NOT visible
        for i in range(5):
            assert f"item {i}" in compact
        assert "item 5" not in compact
        assert "item 6" not in compact

        # verbose=True -- show all 7
        verbose = await list_tool.coroutine(verbose=True)
        assert "+2 more" not in verbose
        for i in range(7):
            assert f"item {i}" in verbose

    async def test_format_graph_renders_subtasks_in_branching_mode(self):
        """Branching-mode rendering (a node with multiple successors) keeps
        sub-task checklists under each node AND uses ``\u2514\u2192`` arrows
        for the graph edges. This guards the interaction between the two
        rendering passes so sub-tasks don't disappear when the linear
        fallback is not taken.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {
                    "id": "root",
                    "text": "Root",
                    "subtasks": [
                        {"text": "root step 1"},
                        {"text": "root step 2", "status": "done"},
                    ],
                },
                {
                    "id": "left",
                    "text": "Left branch",
                    "subtasks": [{"text": "left only"}],
                },
                {
                    "id": "right",
                    "text": "Right branch",
                    "subtasks": [{"text": "right a"}, {"text": "right b"}],
                },
                {"id": "sink", "text": "Merge sink"},
            ],
            edges=[
                {"from": "root", "to": "left"},
                {"from": "root", "to": "right"},
                {"from": "left", "to": "sink"},
                {"from": "right", "to": "sink"},
            ],
        )
        tools = _build_tools(manager=manager)
        list_tool = tools[9]

        result = await list_tool.coroutine()

        # Branching arrow present (proves branching mode was triggered)
        assert "\u2514\u2192" in result
        # All sub-tasks of all three non-sink nodes appear
        assert "root step 1" in result
        assert "root step 2" in result
        assert "left only" in result
        assert "right a" in result
        assert "right b" in result
        # Both pending (``\u2610``) and done (``\u2611``) checklist icons
        # appear -- confirming sub-task rendering doesn't collapse in
        # branching mode.
        assert "\u2610" in result
        assert "\u2611" in result

    async def test_format_graph_branching_subtasks_skip_merged_visits(self):
        """When a merge node is re-encountered in the DFS walk, the
        ``(merged)`` annotation is rendered and its sub-tasks are NOT
        re-emitted (the first visit already covered them). This protects
        against the doubled-checklist bug in branching mode.
        """
        manager = _make_manager()
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {"id": "root", "text": "Root"},
                {"id": "left", "text": "Left branch"},
                {
                    "id": "sink",
                    "text": "Merge sink",
                    "subtasks": [{"text": "sink item"}, {"text": "another"}],
                },
                {"id": "right", "text": "Right branch"},
            ],
            edges=[
                {"from": "root", "to": "left"},
                {"from": "left", "to": "sink"},
                {"from": "root", "to": "right"},
                {"from": "right", "to": "sink"},
            ],
        )
        tools = _build_tools(manager=manager)
        list_tool = tools[9]

        result = await list_tool.coroutine()

        # Merge annotation present
        assert "(merged)" in result
        # Sub-tasks of the merge node appear at most once for the sink
        # node -- if duplicated, they'd be present twice each.
        assert result.count("sink item") == 1
        assert result.count("another") == 1
