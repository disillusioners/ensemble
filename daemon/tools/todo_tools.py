"""Todo management tools for per-instance task tracking.

Mirrors the closure-injection pattern of daemon.tools.chart_tools:
create_todo_tools(manager, current_instance_id, live_event_hub=None) is
invoked from create_instance_tools to assemble the per-instance tool list.
The 6 tools delegate to manager._todo_manager for state mutations and
emit a todo_update SSE event on every successful change so the frontend
sees real-time updates.

Phase 2 of the todo graph transformation introduces:

  * DAG-aware ``todo_create`` — accepts either a flat list (backward
    compatible, auto-chained) or explicit ``nodes`` + ``edges`` for
    branching graphs.
  * Node-id lookup in ``todo_update`` — ``node_id`` takes precedence
    over the legacy positional ``index`` parameter.
  * Two new edge tools: ``todo_add_edge`` and ``todo_remove_edge`` for
    incremental graph edits.
  * ``_format_graph`` renderer that displays branching structure with
    ``└→`` arrows, depth-based indentation, and ``(merged)`` annotations
    on re-encountered nodes. Linear chains fall back to the simple
    ``[idx] <icon> text`` format.

Status aliasing (``completed`` → ``done``, ``wip`` → ``in_progress``, ...)
is owned by ``TodoGraphManager._normalize_status`` — tools pass status
strings through unchanged.
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
Todo list and DAG management tools for tracking per-instance work items.

Phase 2 introduces 6 tools (was 4). The todo graph is a DAG — every node
has a stable string ``id`` and a list of successor ``next_ids``; edges
are stored as adjacency lists on each node.

The 6 tools (todo_create, todo_update, todo_list, todo_clear,
todo_add_edge, todo_remove_edge) mutate a per-instance graph via
manager._todo_manager and emit a todo_update SSE event so the frontend
re-renders without polling. Status indicators: \u25cb pending,
\u25d0 in_progress, \u25cf done.

Branching graph example::

    [0] \u25cb Setup DB
      \u2514\u2192 [1] \u25d0 Build API
           \u2514\u2192 [2] \u25cf Write tests
           \u2514\u2192 [3] \u25cb Write docs
      \u2514\u2192 [4] \u25cb Deploy

Linear chains render as a flat ``[idx] <icon> text`` list.

Node identity precedence: ``node_id`` (e.g. ``n-a1b2c3d4``) is preferred
over insertion-order ``index`` for graph workflows; ``index`` is kept
as the first positional argument of ``todo_update`` for backward
compatibility with agents that call ``todo_update(0, "done")``.
"""


_STATUS_ICONS = {
    "pending": "\u25cb",
    "in_progress": "\u25d0",
    "done": "\u25cf",
}


def _format_graph(todos: list[dict]) -> str:
    """Format todo nodes as a text-based graph.

    Two rendering modes:

    1. **Linear fallback** — when every node has \u22641 successor AND
       \u22641 predecessor (pure chain or isolated nodes), render a simple
       ``[idx] <icon> text`` list. This preserves the visual contract of
       the legacy ``_format_list`` for the common case.

    2. **Branching tree** — when at least one node has multiple
       successors (branch) or multiple predecessors (merge), perform a
       DFS from each root (no predecessors), indenting 2 spaces per
       depth and prefixing children with ``\u2514\u2192 ``. Already-visited
       nodes render as ``[idx] (merged)`` to avoid re-walking their
       subtree.

    Edge cases:

    * Empty input \u2192 ``"No todo items."``
    * Cyclic graph (defensive \u2014 the manager rejects cycles at write
      time) \u2192 degrades gracefully, re-encountered nodes render as
      ``(merged)``.

    Args:
        todos: List of node dicts (frozen Phase 1 schema: ``id``,
            ``index``, ``text``, ``status``, ``comment``, ``next_ids``).

    Returns:
        Newline-separated graph representation.
    """
    if not todos:
        return "No todo items."

    # Adjacency (successors) and predecessor maps keyed by node id.
    next_ids_map: dict[str, list[str]] = {
        n["id"]: list(n["next_ids"]) for n in todos
    }
    pred_map: dict[str, list[str]] = {n["id"]: [] for n in todos}
    for node in todos:
        for successor_id in node["next_ids"]:
            if successor_id in pred_map:
                pred_map[successor_id].append(node["id"])

    # Linearity test: every node has at most one successor AND at most
    # one predecessor. A single isolated node trivially qualifies (0 \u2264 1).
    is_linear = all(
        len(next_ids_map[n["id"]]) <= 1 and len(pred_map[n["id"]]) <= 1
        for n in todos
    )

    if is_linear:
        lines: list[str] = []
        for item in todos:
            idx = item["index"]
            text = item["text"]
            status = item["status"]
            icon = _STATUS_ICONS.get(status, "?")
            lines.append(f"[{idx}] {icon} {text}")
        return "\n".join(lines)

    # Branching graph \u2014 DFS from each root (no predecessors).
    by_id: dict[str, dict] = {n["id"]: n for n in todos}
    roots = [n for n in todos if len(pred_map[n["id"]]) == 0]
    lines = []
    visited: set[str] = set()

    def render(node: dict, depth: int) -> None:
        """Recursively render ``node`` and its descendants."""
        indent = "  " * depth
        if node["id"] in visited:
            # Merge node \u2014 already rendered via another branch.
            lines.append(f"{indent}\u2514\u2192 [{node['index']}] (merged)")
            return
        visited.add(node["id"])
        idx = node["index"]
        text = node["text"]
        status = node["status"]
        icon = _STATUS_ICONS.get(status, "?")
        if depth == 0:
            lines.append(f"[{idx}] {icon} {text}")
        else:
            lines.append(f"{indent}\u2514\u2192 [{idx}] {icon} {text}")
        for successor_id in node["next_ids"]:
            if successor_id in by_id:
                render(by_id[successor_id], depth + 1)

    for root in roots:
        render(root, 0)

    return "\n".join(lines)


async def _emit_update(
    live_event_hub: "LiveEventHub | None",
    current_instance_id: str,
    todos: list[dict],
) -> None:
    """Best-effort SSE emission \u2014 never raises."""
    if live_event_hub is None:
        return
    event_type = "todo_update"
    try:
        await live_event_hub.stream_todo_update(current_instance_id, todos)
    except Exception as e:
        logger.warning(
            f"todo SSE emission failed (event_type={event_type}, {len(todos)} items): {e}"
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
        List of 6 tool functions:
        ``[todo_create, todo_update, todo_list, todo_clear,
        todo_add_edge, todo_remove_edge]``.
    """

    @register_tool_category("todo")
    @tool
    async def todo_create(
        items: list[str] | None = None,
        nodes: list[dict] | None = None,
        edges: list[dict] | None = None,
    ) -> str:
        """Create or replace the instance's todo graph.

        Two modes:

        1. **Flat list** (backward compatible): pass ``items`` \u2014 a linear
           chain is auto-built (``A \u2192 B \u2192 C``).
        2. **Explicit graph**: pass ``nodes`` (each ``{"id": str, "text":
           str, "next_ids"?: list[str]}``) and optional ``edges`` (each
           ``{"from": str, "to": str}``). Use this for branching plans.

        If ``items`` is provided, ``nodes`` and ``edges`` are ignored. If
        neither is provided, the call returns an error.

        Args:
            items: Ordered list of todo text entries. Builds a linear
                chain DAG. Replaces any existing graph.
            nodes: List of node specs for an explicit graph. Each spec
                must have ``id`` (non-empty, non-numeric) and ``text``;
                ``next_ids`` is optional.
            edges: Optional list of ``{"from": ..., "to": ...}`` edges.
                Layered on top of any per-node ``next_ids``.

        Returns:
            Formatted graph representation, or an ``ERROR:`` string.
        """
        try:
            if items is not None:
                todos = manager._todo_manager.create(
                    current_instance_id, items
                )
            elif nodes is not None:
                todos = manager._todo_manager.create_graph(
                    current_instance_id, nodes, edges or []
                )
            else:
                return (
                    "ERROR: Provide either items (flat list) or nodes "
                    "(explicit graph)."
                )
            await _emit_update(live_event_hub, current_instance_id, todos)
            header = (
                f"\u2705 Todo graph created with {len(todos)} nodes:"
                if nodes is not None and items is None
                else f"\u2705 Todo list created with {len(todos)} items:"
            )
            return f"{header}\n{_format_graph(todos)}"
        except Exception as e:
            return f"ERROR: Failed to create todo graph: {e}"

    todo_create._full_doc_ = """\
Create or replace the instance's todo graph (DAG).

Two modes:

1. Flat list (backward compatible)::

    todo_create(items=["Setup", "Build", "Test"])

   Auto-builds a linear chain ``Setup \u2192 Build \u2192 Test``.

2. Explicit graph (branching)::

    todo_create(
        nodes=[
            {"id": "setup", "text": "Setup DB"},
            {"id": "api", "text": "Build API"},
            {"id": "ui", "text": "Build UI"},
            {"id": "test", "text": "Run tests"},
        ],
        edges=[
            {"from": "setup", "to": "api"},
            {"from": "setup", "to": "ui"},
            {"from": "api", "to": "test"},
            {"from": "ui", "to": "test"},
        ],
    )

Args:
    items: Ordered list of todo text entries. Replaces the entire graph
        with a linear chain. Mutually exclusive with ``nodes``.
    nodes: List of node specs for an explicit graph. Each spec must
        contain ``id`` (non-empty, non-numeric string) and ``text``;
        ``next_ids`` is optional and is merged with any ``edges``.
    edges: Optional list of ``{"from": str, "to": str}`` edges. Layered
        on top of any per-node ``next_ids``. Ignored when ``items`` is
        provided.

Returns:
    Formatted graph (linear fallback or branching tree) on success, or
    an ``ERROR:`` string on validation failure (cycle, dangling
    reference, all-numeric id, duplicate id, count > 200).

The graph is validated for DAG-ness before storage \u2014 any cycle,
dangling ``next_ids``/edge reference, all-numeric id, or duplicate id
is rejected with a clear error message. A 200-node cap is enforced.
"""

    @register_tool_category("todo")
    @tool
    async def todo_update(
        index: int | None = None,
        status: str = "",
        node_id: str | None = None,
    ) -> str:
        """Update the status of a single todo node.

        Identify the node by either:

        * ``index`` \u2014 zero-based insertion-order position (first
          parameter, preserved for backward compat with agents that
          call ``todo_update(0, "done")`` positionally).
        * ``node_id`` \u2014 the node's stable string ID (preferred for
          graph workflows; takes precedence when both are provided).

        If neither is provided, returns an error. Status accepts canonical
        values (``pending``, ``in_progress``, ``done``) plus 16
        case-insensitive aliases (``completed``, ``wip``, ``started``,
        ``cancelled``, ...) normalized by the manager.

        Args:
            index: Zero-based insertion-order position. Kept as the
                first positional argument for backward compatibility.
            status: New status (canonical or alias).
            node_id: Node ID (e.g. ``n-a1b2c3d4``). Takes precedence
                over ``index`` when both are supplied.

        Returns:
            Formatted graph plus a graph-aware reminder pointing to the
            next ready items (pending nodes whose predecessors are all
            ``done``), or an ``ERROR:`` string.
        """
        try:
            if node_id is not None:
                result = manager._todo_manager.update(
                    current_instance_id, node_id, status
                )
                lookup_desc = f"node_id={node_id!r}"
            elif index is not None:
                result = manager._todo_manager.update_by_index(
                    current_instance_id, index, status
                )
                lookup_desc = f"index={index}"
            else:
                return "ERROR: Provide either index or node_id."

            if result is None:
                return (
                    f"ERROR: Could not update {lookup_desc} \u2192 {status!r}. "
                    "Either the node does not exist or the status is "
                    "invalid (expected pending, in_progress, done or an "
                    "alias)."
                )

            todos = result["todos"]
            reminder = result["reminder"]
            await _emit_update(live_event_hub, current_instance_id, todos)
            head = f"\U0001f4cb Updated {lookup_desc} \u2192 {status}."
            body = _format_graph(todos)
            return f"{head}\n\n{body}{reminder}"
        except Exception as e:
            return f"ERROR: Failed to update todo: {e}"

    todo_update._full_doc_ = """\
Update the status of a single todo node.

Args:
    index: Zero-based insertion-order position of the node. Kept as
        the FIRST positional argument so agents can call
        ``todo_update(0, "done")`` without keyword arguments.
    status: New status \u2014 ``pending``, ``in_progress``, or ``done``
        (plus 16 case-insensitive aliases like ``completed``,
        ``started``, ``wip``, ``cancelled``).
    node_id: Stable node ID (e.g. ``n-a1b2c3d4``). Takes precedence
        over ``index`` when both are supplied.

Returns:
    Formatted graph plus a graph-aware reminder:

    * If ``status == "done"`` AND the completed node carries a non-empty
      ``comment``, the reminder is prefixed with
      ``"User commented:\\n---\\n{comment}\\n---\\n"`` (the fences guard
      against prompt injection).
    * Otherwise the reminder lists pending nodes whose predecessors are
      all done (``"\u23ed\ufe0f Next: A, B, ..."``), reports blocked
      items (``"\u23f3 Waiting: N blocked items"``), or confirms
      completion (``"All items completed! \u2705"``).

On error (missing node, invalid status, neither ``index`` nor
``node_id`` provided) returns ``ERROR: ...`` \u2014 no mutation occurs.
"""

    @register_tool_category("todo")
    @tool
    async def todo_list() -> str:
        """List the instance's current todo graph.

        Returns:
            Formatted graph representation (linear fallback or
            branching tree), or ``"No todo items."`` if empty.
        """
        try:
            todos = manager._todo_manager.get_all(current_instance_id)
            return f"\U0001f4cb Current todo graph:\n{_format_graph(todos)}"
        except Exception as e:
            return f"ERROR: Failed to read todo graph: {e}"

    todo_list._full_doc_ = """\
List the instance's current todo graph.

Linear chains render as ``[idx] <icon> text``. Branching graphs render
as a depth-indented tree with ``\u2514\u2192`` arrows and ``(merged)``
annotations on re-encountered nodes. Empty graph returns
``"No todo items."``.

Returns:
    Formatted graph representation, or ``"No todo items."`` if empty.
"""

    @register_tool_category("todo")
    @tool
    async def todo_clear() -> str:
        """Clear the entire todo graph for this instance.

        Returns:
            Short confirmation string.
        """
        try:
            manager._todo_manager.clear(current_instance_id)
            await _emit_update(live_event_hub, current_instance_id, [])
            return "\U0001f5d1\ufe0f Todo graph cleared."
        except Exception as e:
            return f"ERROR: Failed to clear todo graph: {e}"

    todo_clear._full_doc_ = """\
Clear the entire todo graph for this instance.

After calling this, ``todo_list()`` will report ``"No todo items."``
until a new graph is created via ``todo_create()``. Any pending edge
mutations on a cleared graph return ``ERROR:`` because the nodes no
longer exist.
"""

    @register_tool_category("todo")
    @tool
    async def todo_add_edge(from_id: str, to_id: str) -> str:
        """Add a directed edge between two todo nodes.

        Creates a dependency: ``to_id`` becomes a successor of
        ``from_id``. The edge must not create a cycle; self-loops are
        also rejected. Idempotent \u2014 re-adding an existing edge is a
        no-op.

        Args:
            from_id: ID of the predecessor node.
            to_id: ID of the successor node.

        Returns:
            Formatted graph on success, or ``ERROR:`` if either node
            does not exist or the edge would introduce a cycle.
        """
        try:
            result = manager._todo_manager.add_edge(
                current_instance_id, from_id, to_id
            )
            if result is None:
                return (
                    f"ERROR: Could not add edge {from_id!r} \u2192 {to_id!r}. "
                    "Either node does not exist, or the edge would "
                    "create a cycle."
                )
            nodes = result["nodes"]
            await _emit_update(live_event_hub, current_instance_id, nodes)
            return f"\u2705 Edge added: {from_id} \u2192 {to_id}\n{_format_graph(nodes)}"
        except Exception as e:
            return f"ERROR: Failed to add edge: {e}"

    todo_add_edge._full_doc_ = """\
Add a directed edge between two existing todo nodes.

Args:
    from_id: ID of the predecessor node (the source of the new edge).
    to_id: ID of the successor node (the target of the new edge).

Behavior:

* Adds ``to_id`` to ``from_id``'s ``next_ids`` list.
* Validates DAG-ness via Kahn's algorithm; rolls back if the new edge
  would create a cycle (or a self-loop, which is always a cycle).
* Self-loops (``from_id == to_id``) are rejected as cycles.
* Idempotent: if the edge already exists, this is a no-op and returns
  the current graph unchanged.

Returns:
    The updated graph (formatted as a linear list or branching tree)
    on success, or ``ERROR: ...`` if either node does not exist or the
    edge would create a cycle. No mutation occurs on error.
"""

    @register_tool_category("todo")
    @tool
    async def todo_remove_edge(from_id: str, to_id: str) -> str:
        """Remove a directed edge between two todo nodes.

        Args:
            from_id: ID of the predecessor node.
            to_id: ID of the successor node.

        Returns:
            Formatted graph on success, or ``ERROR:`` if the edge does
            not exist (or either node is missing).
        """
        try:
            result = manager._todo_manager.remove_edge(
                current_instance_id, from_id, to_id
            )
            if result is None:
                return (
                    f"ERROR: Could not remove edge {from_id!r} \u2192 {to_id!r}. "
                    "The edge (or one of its endpoints) does not exist."
                )
            nodes = result["nodes"]
            await _emit_update(live_event_hub, current_instance_id, nodes)
            return (
                f"\u2705 Edge removed: {from_id} \u2192 {to_id}\n"
                f"{_format_graph(nodes)}"
            )
        except Exception as e:
            return f"ERROR: Failed to remove edge: {e}"

    todo_remove_edge._full_doc_ = """\
Remove a directed edge between two todo nodes.

Args:
    from_id: ID of the predecessor node.
    to_id: ID of the successor node.

Behavior:

* Removes ``to_id`` from ``from_id``'s ``next_ids`` list.
* Treats missing edges and missing endpoints uniformly: any of
  ``{instance missing, from_id missing, to_id missing, edge missing}``
  returns ``ERROR:`` \u2014 no mutation occurs.
* Does NOT remove the endpoint nodes themselves; use
  ``manager._todo_manager.remove_node`` directly if node removal is
  needed (not exposed as a tool).

Returns:
    The updated graph (formatted as a linear list or branching tree)
    on success, or ``ERROR: ...`` if the edge or one of its endpoints
    does not exist.
"""

    return [
        todo_create,
        todo_update,
        todo_list,
        todo_clear,
        todo_add_edge,
        todo_remove_edge,
    ]