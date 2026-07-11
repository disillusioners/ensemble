"""Todo management tools for per-instance task tracking.

Mirrors the closure-injection pattern of daemon.tools.chart_tools:
create_todo_tools(manager, current_instance_id, live_event_hub=None) is
invoked from create_instance_tools to assemble the per-instance tool list.
The 11 tools delegate to manager._todo_manager for state mutations and
emit a todo_update SSE event on every successful change so the frontend
sees real-time updates.

The tools are organized into two workflow sets plus two shared
read/reset tools — each set targets a distinct planning shape so an
agent picks the prefix that matches its intent instead of one overloaded
call:

**Flat list set** (``todo_list_*``) — strictly sequential work
(``A → B → C`` auto-chained), identified by insertion-order ``index``:

  * ``todo_list_create(items)`` — replace the todo with a linear chain.
  * ``todo_list_update(index, status)`` — mutate one item by position.

**Graph set** (``todo_graph_*``) — branching / parallel / fan-in DAGs,
identified by stable ``node_id``:

  * ``todo_graph_create(nodes, edges)`` — replace the todo with an
    explicit DAG (optionally pre-seeded with sub-task checklists).
  * ``todo_graph_update(node_id, status)`` — mutate one node by id.
  * ``todo_graph_add_edge`` / ``todo_graph_remove_edge`` — incremental
    dependency edits.
  * ``todo_graph_add_subtask`` / ``todo_graph_update_subtask`` /
    ``todo_graph_remove_subtask`` — per-node binary checklists.

**Shared (unprefixed):**

  * ``todo_view(verbose=False)`` — read-only render of the current graph.
  * ``todo_clear()`` — drop the entire graph.

Sub-tasks are STRICTLY BINARY (``pending`` or ``done``) — they model
fine-grained acceptance criteria under a parent node, not a multi-state
workflow. The ``_format_graph`` renderer prints them as an indented
checklist (``☐`` / ``☑``) under each node, with a ``+N more`` truncation
marker at 5 items by default; pass ``verbose=True`` to ``todo_view`` to
see them all.

Status aliasing (``completed`` → ``done``, ``wip`` → ``in_progress``, ...)
is owned by ``TodoGraphManager._normalize_status`` — tools pass status
strings through unchanged.
"""

import json
import logging
from builtins import list as _list_type
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.services.todo_manager import _normalize_subtask_status

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.live_event_hub import LiveEventHub

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Todo Management"
CATEGORY_DOC = """\
Todo tools for per-instance task tracking, in two sets by planning shape:
- todo_list_* (flat, index-based): todo_list_create, todo_list_update.
- todo_graph_* (DAG, node_id-based): todo_graph_create, todo_graph_update,
  todo_graph_add_edge, todo_graph_remove_edge, todo_graph_*_subtask checklists.
- shared: todo_view (read), todo_clear (reset).
Both sets share one store; todo_view/todo_clear work on either. Statuses:
pending, in_progress, done (aliases accepted). Sub-tasks are binary (pending/done).
"""


_STATUS_ICONS = {
    "pending": "\u25cb",
    "in_progress": "\u25d0",
    "done": "\u25cf",
}

# Sub-task checklist icons. STRICTLY BINARY: sub-tasks are either
# ``pending`` or ``done`` — there is no ``in_progress`` state. Distinct
# from the node-level ``_STATUS_ICONS`` (``○◐●``) so the two layers are
# visually unambiguous in the rendered graph.
_SUBTASK_ICONS = {"pending": "\u2610", "done": "\u2611"}

# Maximum sub-tasks rendered per node when ``verbose=False``. Sub-tasks
# beyond this count are collapsed into a single ``+N more`` marker.
_SUBTASK_VERBOSE_LIMIT = 5


def _format_graph(todos: list[dict], verbose: bool = False) -> str:
    """Format todo nodes as a text-based graph with sub-task checklists.

    Two rendering modes:

    1. **Linear fallback** — when every node has ≤1 successor AND
       ≤1 predecessor (pure chain or isolated nodes), render a simple
       ``[idx] <icon> text`` list. This preserves the visual contract of
       the legacy ``_format_list`` for the common case.

    2. **Branching tree** — when at least one node has multiple
       successors (branch) or multiple predecessors (merge), perform a
       DFS from each root (no predecessors), indenting 2 spaces per
       depth and prefixing children with ``└→ ``. Already-visited
       nodes render as ``[idx] (merged)`` to avoid re-walking their
       subtree.

    Sub-task rendering: in BOTH modes, every node that carries a
    non-empty ``subtasks`` list gets an indented checklist rendered
    immediately below it. The sub-task indent is the node's own
    leading-space prefix plus 4 more spaces, so a depth-0 node's
    sub-tasks sit at 4 spaces and a depth-1 node's sub-tasks sit at
    6 spaces. Sub-tasks use ``☐`` / ``☑`` icons (distinct from the
    node-level ``○◐●``) so the two layers read unambiguously.

    Verbosity: when ``verbose=False`` (default) each node shows at most
    :data:`_SUBTASK_VERBOSE_LIMIT` sub-tasks; any overflow is collapsed
    into a single ``+N more`` marker. ``verbose=True`` shows every
    sub-task.

    Edge cases:

    * Empty input → ``"No todo items."``
    * Cyclic graph (defensive — the manager rejects cycles at write
      time) → degrades gracefully, re-encountered nodes render as
      ``(merged)`` and their sub-tasks are NOT re-rendered (the
      first visit already covered them).
    * Nodes missing the ``subtasks`` key are tolerated — the helper
      falls back to ``[]`` and renders no checklist under them.

    Args:
        todos: List of node dicts (frozen v2 schema: ``id``, ``index``,
            ``text``, ``status``, ``comment``, ``next_ids``,
            ``subtasks``).
        verbose: When ``True``, render every sub-task under every node
            with no truncation. When ``False`` (default), cap each
            node's sub-task list at :data:`_SUBTASK_VERBOSE_LIMIT`
            entries and append a ``+N more`` line if more exist.

    Returns:
        Newline-separated graph representation.
    """
    if not todos:
        return "No todo items."

    def render_subtasks(node: dict, node_indent: str) -> list[str]:
        """Render sub-task lines under a node, indented below it.

        Args:
            node: The node dict (may or may not carry ``subtasks``).
            node_indent: Leading-space prefix of the node's own line
                (e.g. ``""`` for a depth-0 node, ``"  "`` for depth 1).
                Sub-tasks are offset by 4 more spaces.

        Returns:
            A list of formatted sub-task lines (empty if the node has
            no sub-tasks). When ``verbose=False`` and the node has
            more than :data:`_SUBTASK_VERBOSE_LIMIT` sub-tasks, the
            overflow is represented by a single ``+N more`` line.
        """
        subtasks = node.get("subtasks", [])
        if not subtasks:
            return []
        sub_indent = node_indent + "    "
        lines: list[str] = []
        limit = len(subtasks) if verbose else _SUBTASK_VERBOSE_LIMIT
        for i, sub in enumerate(subtasks):
            if i >= limit:
                remaining = len(subtasks) - limit
                lines.append(f"{sub_indent}+{remaining} more")
                break
            icon = _SUBTASK_ICONS.get(sub.get("status", "pending"), "?")
            text = sub.get("text", "")
            lines.append(f"{sub_indent}{icon} {text}")
        return lines

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
            lines.extend(render_subtasks(item, ""))
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
            # Merge node — already rendered via another branch.
            # Sub-tasks are NOT re-emitted on merge lines; the first
            # visit covered them and re-printing would clutter.
            lines.append(f"{indent}└→ [{node['index']}] (merged)")
            return
        visited.add(node["id"])
        idx = node["index"]
        text = node["text"]
        status = node["status"]
        icon = _STATUS_ICONS.get(status, "?")
        if depth == 0:
            # Root nodes render without an arrow prefix; the leading
            # indent is empty so the sub-task offset is just 4 spaces.
            lines.append(f"[{idx}] {icon} {text}")
            lines.extend(render_subtasks(node, ""))
        else:
            # Non-root nodes carry the ``└→ `` branch arrow; the
            # node's "indent" is the spaces BEFORE the arrow, and
            # sub-tasks are offset 4 more spaces from that.
            lines.append(f"{indent}└→ [{idx}] {icon} {text}")
            lines.extend(render_subtasks(node, indent))
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
        List of 11 tool functions, ordered list-set → graph-set → shared:
        ``[todo_list_create, todo_list_update, todo_graph_create,
        todo_graph_update, todo_graph_add_edge, todo_graph_remove_edge,
        todo_graph_add_subtask, todo_graph_update_subtask,
        todo_graph_remove_subtask, todo_view, todo_clear]``.
    """

    @register_tool_category("todo")
    @tool
    async def todo_list_create(items: list[str] | None = None) -> str:
        """Create/replace a flat sequential todo list (auto-chains A -> B -> C).
        Replaces any existing graph. For branching use todo_graph_create.

        Args:
            items: Ordered todo text entries.
        """
        try:
            if items is None:
                return "ERROR: Provide items (a flat list of strings)."
            todos = manager._todo_manager.create(
                current_instance_id, items
            )
            await _emit_update(live_event_hub, current_instance_id, todos)
            return (
                f"\u2705 Todo list created with {len(todos)} items:\n"
                f"{_format_graph(todos)}"
            )
        except Exception as e:
            return f"ERROR: Failed to create todo list: {e}"

    todo_list_create._full_doc_ = """\
Create or replace the instance's todo as a flat sequential list (linear
chain ``A \u2192 B \u2192 C``). Replaces any existing graph.

::

    todo_list_create(items=["Setup", "Build", "Test"])

Args:
    items: Ordered list of todo text entries. Mutually exclusive with
        the graph tools \u2014 pass ``items`` for a flat list, or use
        ``todo_graph_create(nodes=..., edges=...)`` for branching plans.

Returns:
    Formatted list representation on success, or an ``ERROR:`` string
    on validation failure (count > 200).

A 200-node cap is enforced.
"""

    @register_tool_category("todo")
    @tool
    async def todo_graph_create(
        nodes: list[dict] | None = None,
        edges: list[dict] | None = None,
    ) -> str:
        """Create/replace an explicit todo DAG (branching/parallel/fan-in).

        Args:
            nodes: Node specs, each {"id": str, "text": str, "next_ids"?: list[str],
                "subtasks"?: list[{"text": str}]}. id must be non-empty, non-numeric.
            edges: Optional [{"from": str, "to": str}], merged with per-node next_ids.
        """
        try:
            if nodes is None:
                return "ERROR: Provide nodes (an explicit graph spec)."
            todos = manager._todo_manager.create_graph(
                current_instance_id, nodes, edges or []
            )
            await _emit_update(live_event_hub, current_instance_id, todos)
            return (
                f"\u2705 Todo graph created with {len(todos)} nodes:\n"
                f"{_format_graph(todos)}"
            )
        except Exception as e:
            return f"ERROR: Failed to create todo graph: {e}"

    todo_graph_create._full_doc_ = """\
Create or replace the instance's todo as an explicit graph (DAG).

::

    todo_graph_create(
        nodes=[
            {"id": "setup", "text": "Setup DB",
             "subtasks": [{"text": "Create schema"}, {"text": "Run migration"}]},
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
    nodes: List of node specs for an explicit graph. Each spec must
        contain ``id`` (non-empty, non-numeric string) and ``text``;
        ``next_ids`` and ``subtasks`` are optional and are merged with
        any ``edges``. The ``subtasks`` key, when present, is a list of
        ``{"text": str}`` dicts that seed a binary ``pending``/``done``
        checklist under that node (max 20 items, each \u2264 500 chars).
    edges: Optional list of ``{"from": str, "to": str}`` edges. Layered
        on top of any per-node ``next_ids``.

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
    async def todo_list_update(index: int, status: str) -> str:
        """Update one item's status by 0-based index.

        Args:
            index: Zero-based insertion-order position.
            status: pending | in_progress | done (aliases accepted).
        """
        try:
            result = manager._todo_manager.update_by_index(
                current_instance_id, index, status
            )
            lookup_desc = f"index={index}"
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

    todo_list_update._full_doc_ = """\
Update the status of a single todo node by insertion-order index.

Args:
    index: Zero-based insertion-order position of the node.
    status: New status \u2014 ``pending``, ``in_progress``, or ``done``
        (plus 16 case-insensitive aliases like ``completed``,
        ``started``, ``wip``, ``cancelled``).

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

On error (missing index, invalid status) returns ``ERROR: ...`` \u2014 no
mutation occurs. For node-id-based updates, use ``todo_graph_update``.
"""

    @register_tool_category("todo")
    @tool
    async def todo_graph_update(node_id: str, status: str) -> str:
        """Update one node's status by stable node_id.

        Args:
            node_id: Stable node ID (e.g. "n-a1b2c3d4").
            status: pending | in_progress | done (aliases accepted).
        """
        try:
            result = manager._todo_manager.update(
                current_instance_id, node_id, status
            )
            lookup_desc = f"node_id={node_id!r}"
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

    todo_graph_update._full_doc_ = """\
Update the status of a single todo node by its stable string ID.

Args:
    node_id: Stable node ID (e.g. ``n-a1b2c3d4``).
    status: New status \u2014 ``pending``, ``in_progress``, or ``done``
        (plus 16 case-insensitive aliases like ``completed``,
        ``started``, ``wip``, ``cancelled``).

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

On error (missing node, invalid status) returns ``ERROR: ...`` \u2014 no
mutation occurs. For index-based updates on a flat list, use
``todo_list_update``.
"""

    @register_tool_category("todo")
    @tool
    async def todo_view(verbose: bool = False) -> str:
        """View the current todo graph.

        Args:
            verbose: If True, show all sub-tasks per node (default truncates at 5).
        """
        try:
            todos = manager._todo_manager.get_all(current_instance_id)
            return (
                f"\U0001f4cb Current todo graph:\n"
                f"{_format_graph(todos, verbose=verbose)}"
            )
        except Exception as e:
            return f"ERROR: Failed to read todo graph: {e}"

    todo_view._full_doc_ = """\
View the instance's current todo graph.

Linear chains render as ``[idx] <icon> text``. Branching graphs render
as a depth-indented tree with ``\u2514\u2192`` arrows and ``(merged)``
annotations on re-encountered nodes. Empty graph returns
``"No todo items."``.

Sub-tasks render as an indented checklist (4-space offset from the
parent node) using ``\u2610`` / ``\u2611`` icons. By default, each
node shows at most 5 sub-tasks; nodes with more append a
``+N more`` marker. Pass ``verbose=True`` to render every sub-task.

Args:
    verbose: When ``True``, render every sub-task under every node with
        no truncation. Default is ``False`` (cap of 5 per node).

Returns:
    Formatted graph representation, or ``"No todo items."`` if empty.
"""

    @register_tool_category("todo")
    @tool
    async def todo_clear() -> str:
        """Clear all todos for this instance."""
        try:
            manager._todo_manager.clear(current_instance_id)
            await _emit_update(live_event_hub, current_instance_id, [])
            return "\U0001f5d1\ufe0f Todo graph cleared."
        except Exception as e:
            return f"ERROR: Failed to clear todo graph: {e}"

    todo_clear._full_doc_ = """\
Clear the entire todo graph for this instance.

After calling this, ``todo_view()`` will report ``"No todo items."``
until a new graph is created via ``todo_list_create()`` /
``todo_graph_create()``. Any pending edge mutations on a cleared graph
return ``ERROR:`` because the nodes no longer exist.
"""

    @register_tool_category("todo")
    @tool
    async def todo_graph_add_edge(from_id: str, to_id: str) -> str:
        """Add a dependency edge from_id -> to_id. Rejected if it creates a cycle.

        Args:
            from_id: Predecessor node ID.
            to_id: Successor node ID.
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

    todo_graph_add_edge._full_doc_ = """\
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
    async def todo_graph_remove_edge(from_id: str, to_id: str) -> str:
        """Remove a dependency edge from_id -> to_id.

        Args:
            from_id: Predecessor node ID.
            to_id: Successor node ID.
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

    todo_graph_remove_edge._full_doc_ = """\
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

    @register_tool_category("todo")
    @tool
    async def todo_graph_add_subtask(
        node_id: str,
        list: str | list[str] | None = None,
        text: str | list[str] | None = None,
    ) -> str:
        """Add binary checklist item(s) to a node. `list` may be a str or list[str]
        (atomic batch: all added or none). Sub-tasks are pending/done only.
        A JSON-encoded array string (e.g. '["a","b"]') is auto-parsed into a list.

        Args:
            node_id: Parent node ID.
            list: One description, or a list of descriptions (each <= 500 chars).
                A JSON array string is auto-parsed into a list.
            text: Deprecated alias for `list`. Kept so old agents passing
                `text=...` continue to work; new callers should use `list`.
        """
        try:
            # Promote legacy `text=` kwarg to the new `list` parameter.
            # NOTE: the parameter name `list` shadows the built-in
            # `list` type inside this function body, so we use
            # `list_value` as a local alias and avoid `list(...)`.
            if list is None and text is not None:
                list_value = text
            elif list is not None:
                list_value = list
            else:
                return (
                    "ERROR: Provide `list` (one sub-task description or a list "
                    "of descriptions)."
                )
            # If a JSON-encoded array string was passed (e.g. '["a","b"]'), parse it.
            # Fall back silently to plain-string handling on parse failure so a
            # legitimate string that happens to contain brackets isn't rejected.
            if (
                isinstance(list_value, str)
                and list_value.startswith("[")
                and list_value.endswith("]")
            ):
                try:
                    parsed = json.loads(list_value)
                    # ``list`` is shadowed by the parameter name above; use
                    # the module-level alias ``_list_type`` to reach the
                    # real built-in.
                    if isinstance(parsed, _list_type):
                        list_value = parsed
                except ValueError:
                    pass
            # Normalize ``list_value`` to a list so a single string and a list
            # of strings share one code path. The ``str`` check MUST come
            # before any iterable handling — ``str`` is iterable, so
            # treating a bare string as a sequence would split it into
            # characters.
            if isinstance(list_value, str):
                texts = [list_value]
            else:
                # list_value is already a list[str]; copy defensively for isolation.
                # Avoid `list(list_value)` because the parameter name shadows the built-in.
                texts = list_value[:]
            # Guard: empty list is a caller error, not an internal failure.
            if not texts:
                return (
                    "ERROR: Provide at least one sub-task description "
                    "(list cannot be empty)."
                )
            result = manager._todo_manager.add_subtasks(
                current_instance_id, node_id, texts
            )
            if result is None:
                # The manager raises ValueError for "max sub-tasks
                # exceeded" / "text too long" / "empty text", so this
                # None branch is ONLY reached when the instance or
                # node_id is unknown.
                return (
                    f"ERROR: Node '{node_id}' not found in instance "
                    f"'{current_instance_id}'."
                )
            todos = result["todos"]
            added_ids = result["added_ids"]
            await _emit_update(live_event_hub, current_instance_id, todos)
            body = _format_graph(todos, verbose=False)
            if len(added_ids) == 1:
                head = (
                    f"Added sub-task '{added_ids[0]}' to node "
                    f"'{node_id}'."
                )
            else:
                ids_str = ", ".join(added_ids)
                head = (
                    f"Added {len(added_ids)} sub-tasks to node "
                    f"'{node_id}': {ids_str}."
                )
            return f"{head}\n\n{body}"
        except Exception as e:
            return f"ERROR: Failed to add sub-task: {e}"

    todo_graph_add_subtask._full_doc_ = """\
Add one or more sub-tasks (checklist items) to a todo node.

Sub-tasks are STRICTLY BINARY — each item is either ``"pending"`` or
``"done"``. They do not participate in the graph structure (no
``next_ids``) and exist purely to track fine-grained acceptance
criteria under a parent node. Each ``list`` entry is required and
capped at :data:`MAX_SUBTASK_TEXT_LENGTH` (500 chars); the per-node
checklist is capped at :data:`MAX_SUBTASKS_PER_NODE` (20 items).

The ``list`` argument accepts EITHER a single string OR a list of
strings. Passing a list adds every item in one atomic batch — the
combined count (existing + new) is validated up-front, so a batch
that would exceed the per-node cap is rejected in full rather than
silently truncating. This is the recommended way to attach several
checklist items at once instead of issuing one call per item.

The new sub-tasks' ids are auto-generated (``s-`` + 8 hex chars) and
returned in the confirmation line so subsequent
``todo_graph_update_subtask`` / ``todo_graph_remove_subtask`` calls can target
them without re-listing the graph.

Args:
    node_id: The parent node's stable ID (e.g. ``"n-a1b2c3d4"``).
    list: A single sub-task description (str) OR a list of descriptions
        (``list[str]``). Each entry: non-empty, max 500 chars. A JSON array
        string (e.g. ``'["a","b"]'``) is auto-parsed into a list.
    text: Deprecated alias for ``list``. Provided so old agents passing
        ``text=...`` continue to work. Prefer ``list`` in new calls.

Behavior:

* Calls ``manager._todo_manager.add_subtasks`` which:
  - returns ``None`` when the instance or ``node_id`` is unknown;
  - raises :class:`ValueError` for an empty/non-list ``list``, an
    empty/too-long entry, or when the combined sub-task count would
    exceed the per-node cap.
* Emits a single ``todo_update`` SSE event on success (one event per
  batch, regardless of how many items were added).
* Renders the resulting graph with ``verbose=False`` (sub-tasks
  truncated at 5 per node with a ``+N more`` marker).

Returns:
    A confirmation line (listing the added sub-task id(s)) followed by
    the formatted graph on success. Returns ``ERROR: ...`` when the
    parent node is missing, an entry is empty/too long, or the per-node
    sub-task cap would be exceeded.
"""

    @register_tool_category("todo")
    @tool
    async def todo_graph_update_subtask(
        node_id: str,
        subtask_id: str,
        status: str,
        auto_complete: bool = False,
    ) -> str:
        """Set a sub-task pending or done. auto_complete=True marks the parent done
        once all its sub-tasks are done.

        Args:
            node_id: Parent node ID.
            subtask_id: Sub-task ID (e.g. "s-a1b2c3d4").
            status: pending | done only (no in_progress for sub-tasks).
            auto_complete: If True, auto-complete the parent when all sub-tasks done.
        """
        try:
            # Validate the sub-task status BEFORE delegating to the
            # manager. Sub-task statuses are strictly binary
            # (``pending`` / ``done``); without this gate the manager
            # rejects with the unhelpful "Node or sub-task not found"
            # error, which misleads the agent into thinking the
            # sub-task id is wrong when the real problem is the
            # status value.
            if _normalize_subtask_status(status) is None:
                return (
                    f"ERROR: Invalid sub-task status '{status}'. "
                    f"Use 'pending' or 'done'."
                )
            result = manager._todo_manager.update_subtask(
                current_instance_id, node_id, subtask_id, status, auto_complete
            )
            if result is None:
                # Covers all rejection paths uniformly: missing instance,
                # missing parent node, missing sub-task, OR invalid status
                # (sub-task statuses are strictly binary — ``in_progress``
                # and its aliases are rejected by the manager).
                return (
                    f"ERROR: Node '{node_id}' or sub-task '{subtask_id}' "
                    f"not found"
                )
            todos = result["todos"]
            reminder = result.get("reminder", "")
            await _emit_update(live_event_hub, current_instance_id, todos)
            head = (
                f"Updated sub-task '{subtask_id}' status to '{status}'."
            )
            extra = ""
            if auto_complete:
                if result.get("auto_completed"):
                    extra = (
                        f"\nParent node '{node_id}' auto-completed "
                        f"(all sub-tasks done)."
                    )
                else:
                    # Count sub-tasks still pending on the parent.
                    # When pending_count == 0 the parent is already
                    # 'done' (the manager only sets auto_completed when
                    # the parent wasn't already done), so we report that
                    # explicitly instead of saying "0 sub-task(s) remain
                    # pending" — which would be confusing and wrong.
                    parent = next(
                        (n for n in todos if n["id"] == node_id), None
                    )
                    pending_count = 0
                    if parent is not None:
                        pending_count = sum(
                            1
                            for st in parent.get("subtasks", [])
                            if st.get("status") != "done"
                        )
                    if pending_count == 0:
                        extra = (
                            f"\nauto_complete requested — all sub-tasks "
                            f"are done, but parent node '{node_id}' is "
                            f"already 'done'."
                        )
                    else:
                        extra = (
                            f"\nauto_complete requested but {pending_count} "
                            f"sub-task(s) remain pending."
                        )
            return (
                f"{head}{extra}{reminder}\n\n"
                f"{_format_graph(todos, verbose=False)}"
            )
        except Exception as e:
            return f"ERROR: Failed to update sub-task: {e}"

    todo_graph_update_subtask._full_doc_ = """\
Update a sub-task's status (pending or done).

Sub-task statuses are STRICTLY BINARY — ``"pending"`` or ``"done"``
(plus their case-insensitive aliases like ``"completed"``). The
``"in_progress"`` state used on parent nodes is NOT supported on
sub-tasks; passing it (or any unrecognized value) is treated as
"not found" and returns ``ERROR:``.

Auto-completion policy:

* When ``auto_complete=True`` AND every sub-task on the parent node
  is ``"done"`` AND the parent's own status is not already ``"done"``,
  the parent node's status flips to ``"done"`` and the response
  includes a confirmation line.
* When ``auto_complete=True`` but the above conditions are not met
  (e.g. other sub-tasks are still pending, or the node has no
  sub-tasks at all), the response includes a pending-count note
  instead — the parent status is left untouched.
* The vacuous-truth guard in
  :meth:`TodoGraphManager.update_subtask` ensures a node with zero
  sub-tasks never auto-completes on this path.

Args:
    node_id: The parent node's stable ID (e.g. ``"n-a1b2c3d4"``).
    subtask_id: The sub-task's ID (e.g. ``"s-a1b2c3d4"``).
    status: New status. Must normalize to ``"pending"`` or
        ``"done"`` (case-insensitive aliases accepted). Any other
        value, including ``"in_progress"`` and its aliases, is
        rejected as ``ERROR:``.
    auto_complete: If ``True``, attempt to propagate completion to
        the parent node when all sub-tasks are done. Defaults to
        ``False``.

Returns:
    Formatted graph (with ``verbose=False``) plus the standard
    reminder string. On ``auto_complete=True``:
    * If the parent was auto-completed, an extra confirmation line
      reports the flip.
    * Otherwise, an extra note reports the remaining pending count.
    Returns ``ERROR: ...`` when the parent node, the sub-task, or the
    status value cannot be resolved.
"""

    @register_tool_category("todo")
    @tool
    async def todo_graph_remove_subtask(node_id: str, subtask_id: str) -> str:
        """Remove a sub-task from a node.

        Args:
            node_id: Parent node ID.
            subtask_id: Sub-task ID to remove.
        """
        try:
            result = manager._todo_manager.remove_subtask(
                current_instance_id, node_id, subtask_id
            )
            if result is None:
                return (
                    f"ERROR: Node '{node_id}' or sub-task '{subtask_id}' "
                    f"not found"
                )
            todos = result["todos"]
            await _emit_update(live_event_hub, current_instance_id, todos)
            return (
                f"Removed sub-task '{subtask_id}' from node '{node_id}'.\n\n"
                f"{_format_graph(todos, verbose=False)}"
            )
        except Exception as e:
            return f"ERROR: Failed to remove sub-task: {e}"

    todo_graph_remove_subtask._full_doc_ = """\
Remove a sub-task from a todo node.

Treats missing instance, missing parent node, and missing sub-task
uniformly — any of those returns ``ERROR:`` with no mutation.

Args:
    node_id: The parent node's stable ID (e.g. ``"n-a1b2c3d4"``).
    subtask_id: The sub-task's ID to remove (e.g. ``"s-a1b2c3d4"``).

Returns:
    A confirmation line followed by the formatted graph (with
    ``verbose=False``) on success. Returns ``ERROR: ...`` when the
    parent node or sub-task cannot be resolved.
"""

    return [
        todo_list_create,
        todo_list_update,
        todo_graph_create,
        todo_graph_update,
        todo_graph_add_edge,
        todo_graph_remove_edge,
        todo_graph_add_subtask,
        todo_graph_update_subtask,
        todo_graph_remove_subtask,
        todo_view,
        todo_clear,
    ]