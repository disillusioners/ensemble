"""Tests for ``daemon.tools.todo_tools.create_todo_tools``.

Mirrors the structure of ``tests/test_chart_tools.py``:

  1. **Factory** — returns 4 tools with the documented names.
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
    """Build the 4 todo tools with default instance_id.

    Returns the list ``[todo_create, todo_update, todo_list, todo_clear]``.
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

    def test_factory_returns_list_of_four_tools(self):
        """Factory produces exactly the 4 documented tools."""
        tools = _build_tools()

        assert isinstance(tools, list)
        assert len(tools) == 4

    def test_factory_returns_documented_tool_names(self):
        """The 4 tools are named ``todo_create``, ``todo_update``,
        ``todo_list``, ``todo_clear`` — no more, no less."""
        tools = _build_tools()
        names = [t.name for t in tools]

        assert names == ["todo_create", "todo_update", "todo_list", "todo_clear"]

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

    def test_all_four_tools_registered_under_todo_category(self):
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

        # Header indicates the update
        assert "Updated item [0]" in result
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
        manager._todo_manager.update("test-instance-id", 0, "done")
        manager._todo_manager.update("test-instance-id", 1, "in_progress")
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
        """Output starts with the documented ``📋 Current todo list:`` header."""
        manager = _make_manager()
        manager._todo_manager.create("test-instance-id", ["A"])
        tools = _build_tools(manager=manager)
        list_tool = tools[2]

        result = await list_tool.coroutine()

        assert "Current todo list:" in result


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
