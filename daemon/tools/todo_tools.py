"""Todo management tools for per-instance task tracking.

Mirrors the closure-injection pattern of daemon.tools.chart_tools:
create_todo_tools(manager, current_instance_id, live_event_hub=None) is
invoked from create_instance_tools to assemble the per-instance tool list.
The 4 tools delegate to manager._todo_manager for state mutations and
emit a todo_update SSE event on every successful change so the frontend
sees real-time updates.
"""

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.live_event_hub import LiveEventHub

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Todo Management"
CATEGORY_DOC = """\
Todo list management tools for tracking per-instance work items.

The 4 tools (todo_create, todo_update, todo_list, todo_clear) mutate a
per-instance todo list via manager._todo_manager and emit a todo_update
SSE event so the frontend re-renders without polling. Status indicators:
\u25cb pending, \u25d0 in_progress, \u25cf done.
"""


_STATUS_ICONS = {
    "pending": "\u25cb",
    "in_progress": "\u25d0",
    "done": "\u25cf",
}


def _format_list(todos: list[dict]) -> str:
    """Format a todo list dict into the multi-line display string."""
    if not todos:
        return "No todo items."
    lines = []
    for item in todos:
        idx = item["index"]
        text = item["text"]
        status = item["status"]
        icon = _STATUS_ICONS.get(status, "?")
        lines.append(f"[{idx}] {icon} {text}")
    return "\n".join(lines)


async def _emit_update(
    live_event_hub: "LiveEventHub | None",
    current_instance_id: str,
    todos: list[dict],
) -> None:
    """Best-effort SSE emission \u2014 never raises."""
    if live_event_hub is None:
        return
    try:
        await live_event_hub.stream_todo_update(current_instance_id, todos)
    except Exception as e:
        logger.warning(
            "SSE todo_update emission failed for %s: %s",
            current_instance_id,
            e,
        )


def create_todo_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    live_event_hub: "LiveEventHub | None" = None,
) -> list:
    """Create todo management tools with injected manager and SSE hub.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the owning instance.
        live_event_hub: Optional LiveEventHub for SSE emission. When None,
            mutations still succeed but no event is emitted.

    Returns:
        List of tool functions: [todo_create, todo_update, todo_list, todo_clear]
    """

    @register_tool_category("todo")
    @tool
    async def todo_create(items: list[str]) -> str:
        """Create or replace the instance's todo list. All items start as pending.

        Args:
            items: Ordered list of todo text entries (replaces the entire list).

        Returns:
            Formatted string with the new todo list.
        """
        try:
            todos = manager._todo_manager.create(current_instance_id, items)
            await _emit_update(live_event_hub, current_instance_id, todos)
            return f"\u2705 Todo list created with {len(todos)} items:\n{_format_list(todos)}"
        except Exception as e:
            return f"ERROR: Failed to create todo list: {e}"

    todo_create._full_doc_ = """\
Create or replace the instance's todo list (all items start as pending).

Args:
    items: Ordered list of todo text entries. Replaces the entire
        current list \u2014 any previous items are discarded.

Returns:
    Formatted string showing the new todo list with index, status
    indicator, and text per item.
"""

    @register_tool_category("todo")
    @tool
    async def todo_update(index: int, status: str) -> str:
        """Update the status of a single todo item.

        Args:
            index: Zero-based position of the item to update.
            status: New status \u2014 one of "pending", "in_progress", "done".

        Returns:
            Formatted string with the full list plus a reminder of the
            next pending item (if any).
        """
        try:
            todos = manager._todo_manager.update(current_instance_id, index, status)
            if todos is None:
                return (
                    f"ERROR: Could not update item [{index}] \u2192 {status!r}. "
                    "Either the index is out of range or the status is invalid "
                    "(expected one of: pending, in_progress, done)."
                )
            await _emit_update(live_event_hub, current_instance_id, todos)
            head = f"\U0001f4cb Updated item [{index}] \u2192 {status}."
            body = _format_list(todos)
            next_pending = next(
                (t for t in todos if t["status"] == "pending"), None
            )
            if next_pending is not None:
                tail = f"\n\n\u23ed\ufe0f Next: {next_pending['text']}"
            else:
                tail = "\n\nAll items completed! \u2705"
            return f"{head}\n\n{body}{tail}"
        except Exception as e:
            return f"ERROR: Failed to update todo: {e}"

    todo_update._full_doc_ = """\
Update the status of a single todo item by index.

Args:
    index: Zero-based position of the item to update.
    status: New status \u2014 one of "pending", "in_progress", "done".

Returns:
    Formatted string with the full list plus a reminder of the next
    pending item (or "All items completed!" if none remain).
"""

    @register_tool_category("todo")
    @tool
    async def todo_list() -> str:
        """List the instance's current todo items.

        Returns:
            Formatted string with all items and statuses, or
            "No todo items." if empty.
        """
        try:
            todos = manager._todo_manager.get_all(current_instance_id)
            return f"\U0001f4cb Current todo list:\n{_format_list(todos)}"
        except Exception as e:
            return f"ERROR: Failed to read todo list: {e}"

    todo_list._full_doc_ = """\
List the instance's current todo items.

Returns:
    Formatted string with all items and statuses, or "No todo items."
    if the list is empty.
"""

    @register_tool_category("todo")
    @tool
    async def todo_clear() -> str:
        """Clear the entire todo list for this instance.

        Returns:
            Short confirmation string.
        """
        try:
            manager._todo_manager.clear(current_instance_id)
            await _emit_update(live_event_hub, current_instance_id, [])
            return "\U0001f5d1\ufe0f Todo list cleared."
        except Exception as e:
            return f"ERROR: Failed to clear todo list: {e}"

    todo_clear._full_doc_ = """\
Clear the entire todo list for this instance.

After calling this, todo_list() will report "No todo items." until a
new list is created via todo_create().
"""

    return [todo_create, todo_update, todo_list, todo_clear]
