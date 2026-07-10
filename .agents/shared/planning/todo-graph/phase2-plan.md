# Phase 2: Agent Tools (Backward Compatible)

## Objective

Update the 4 existing todo tools (`todo_create`, `todo_update`, `todo_list`, `todo_clear`) to work with the new `TodoGraphManager` while accepting both flat-list input (backward compat) and graph-structure input (new capability). Add 2 new tools (`todo_add_edge`, `todo_remove_edge`) for dynamic graph manipulation. Update `_format_list` to render graph structure in text with merge-node handling. Update SSE emission to include graph data. Update agent skill documentation and sweep agent definition files for todo tool references.

## Coupling

- **Depends on**: Phase 1 (TodoGraphManager + frozen SSE payload schema)
- **Coupling type**: **tight** — tools call `TodoGraphManager` methods directly; signatures depend on manager API
- **Shared files with other phases**: `daemon/tools/todo_tools.py` (Phase 3 router delegates to same manager; Phase 4 frontend consumes SSE payload shape defined in Phase 1)
- **Shared APIs/interfaces**: Tool function signatures are the LLM-facing contract; `_emit_update` helper shape
- **Why this coupling**: Tools are the primary consumer of `TodoGraphManager`. The `create_todo_tools` factory closure captures `manager._todo_manager` — the method names and return shapes must match Phase 1's implementation exactly. **No coupling to Phase 3** — the SSE payload shape is frozen in Phase 1 (W10 fix).

## Context

- Current `daemon/tools/todo_tools.py` (209 lines)
- Factory pattern: `create_todo_tools(manager, current_instance_id, live_event_hub)` returns `[todo_create, todo_update, todo_list, todo_clear]`
- All tools are `@register_tool_category("todo") @tool async def` inside the closure
- `_format_list(todos: list[dict])` renders `[idx] ○ text` per line
- `_emit_update(live_event_hub, current_instance_id, todos)` — best-effort SSE, swallows exceptions
- `_STATUS_ICONS = {"pending": "○", "in_progress": "◐", "done": "●"}`
- SSE calls `live_event_hub.stream_todo_update(instance_id, todos)` where `todos` is `list[dict]`
- Agent skill doc: `agents/_prompt_system/innate-skills/todo/skill.md` (16 lines) — documents 4-tool inventory
- Agent definition files with todo references:
  - `agents/ari/soul.md:31` — references `todo_create`
  - `agents/ari/rule.md:55` — references `todo_create`
  - `agents/ari/workflow.md:21,30,50,238,261,291,294,295` — references `todo_create()`, "todo list", "update todos"

## Design: Tool Signatures

### todo_create (Overloaded — Backward Compatible)

```python
@tool
async def todo_create(
    items: list[str] | None = None,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> str:
    """Create or replace the instance's todo list.

    Two modes:
    1. Flat list (backward compatible): todo_create(items=["A", "B", "C"])
       Creates a linear chain: A → B → C
    2. Graph structure (new): todo_create(
        nodes=[{"id": "n1", "text": "Setup DB"}, {"id": "n2", "text": "Build API"}],
        edges=[{"from": "n1", "to": "n2"}]
    )

    At least one of `items` or `nodes` must be provided.
    If `items` is provided, `nodes` and `edges` are ignored.
    """
```

**Implementation logic:**
1. If `items` is not None → call `manager._todo_manager.create(current_instance_id, items)` (backward compat path)
2. If `items` is None and `nodes` is not None → call `manager._todo_manager.create_graph(current_instance_id, nodes, edges or [])`
3. If neither provided → return error
4. Emit SSE with result (frozen payload shape from Phase 1)
5. Return formatted string

> **C2 fix**: `edges` parameter is `list[dict]` where each dict is `{"from": "node_id_a", "to": "node_id_b"}`. This matches Phase 1's `create_graph()` signature. No `list[tuple]` anywhere.

### todo_update (Overloaded — Backward Compatible)

> **C5 fix**: The current tool signature is `todo_update(index: int, status: str)`. Agents call it positionally: `todo_update(0, "done")`. The revised signature MUST preserve `index` as the first positional parameter. `status` stays second. `node_id` is added as an optional third parameter.

```python
@tool
async def todo_update(
    index: int | None = None,
    status: str = "",
    node_id: str | None = None,
) -> str:
    """Update the status of a single todo item.

    Identify the node by either:
    - index: Zero-based position in the list (backward compatible, first param)
    - node_id: The node's string ID (new, preferred for graph workflows)

    If both are provided, node_id takes precedence.
    If neither is provided, returns an error.

    Args:
        index: Zero-based position of the item to update (backward compat).
        status: New status — one of "pending", "in_progress", "done".
        node_id: Node ID string (e.g., "n-a1b2c3d4"). Takes precedence over index.
    """
```

**Implementation logic:**
1. If `node_id` is not None → call `manager._todo_manager.update(current_instance_id, node_id, status)`
2. Elif `index` is not None → call `manager._todo_manager.update_by_index(current_instance_id, index, status)`
3. If both None → return error
4. If result is None → return ERROR string
5. Emit SSE, return formatted string with reminder

**Backward compatibility verification**:
- `todo_update(0, "done")` → `index=0, status="done", node_id=None` → works ✅
- `todo_update(index=0, status="done")` → same → works ✅
- `todo_update(node_id="n-abc123", status="done")` → `index=None, status="done", node_id="n-abc123"` → works ✅
- `todo_update(status="done", node_id="n-abc123")` → works ✅

### todo_list (Enhanced — Graph Display)

```python
@tool
async def todo_list() -> str:
    """List the instance's current todo items as a graph.

    For linear chains: displays as before ([0] ○ text)
    For branching graphs: displays with indentation showing branches
    Merge nodes (diamonds) are annotated with (merged)
    """
```

### _format_graph Helper (W6 fix — Merge Node Handling)

> **W6 fix**: The DFS-based formatter now tracks visited nodes. When a node is encountered that was already visited (merge point in a diamond), it is annotated with `(merged)` instead of being re-rendered. This prevents infinite loops on diamonds and gives the agent a clear picture of the graph topology.

```python
def _format_graph(todos: list[dict]) -> str:
    """Format todo nodes as a text-based graph.

    For linear chains (each node has ≤1 successor and ≤1 predecessor):
        [0] ○ Setup DB
        [1] ◐ Build API
        [2] ● Write tests

    For branching graphs:
        [0] ○ Setup DB
          └→ [1] ◐ Build API
               └→ [2] ● Write tests
               └→ [3] ○ Write docs
          └→ [4] ○ Deploy

    For merge nodes (diamonds):
        [0] ○ Setup
          └→ [1] ○ Task A
               └→ [2] ○ Merge point
          └→ [3] ○ Task B
               └→ [2] (merged)

    Falls back to simple list format if graph is purely linear.

    Algorithm:
    1. Build adjacency list from next_ids
    2. Find root nodes (no predecessors)
    3. DFS from each root, tracking visited: set[str]
    4. When encountering an already-visited node, render as "(merged)"
    5. Use └→ prefix for child nodes, indent by depth
    6. Show [index] (insertion order) + status icon + text
    """
```

### todo_clear (Unchanged)

```python
@tool
async def todo_clear() -> str:
    """Clear the entire todo list for this instance."""
    # Identical to current implementation
```

### todo_add_edge (New Tool)

```python
@tool
async def todo_add_edge(from_id: str, to_id: str) -> str:
    """Add a directed edge between two todo nodes.

    Creates a dependency: the `to_id` node becomes a successor of `from_id`.
    The edge must not create a cycle.

    Args:
        from_id: ID of the predecessor node.
        to_id: ID of the successor node.

    Returns:
        Formatted string showing the updated graph, or ERROR if the edge
        would create a cycle or either node doesn't exist.
    """
```

### todo_remove_edge (New Tool)

```python
@tool
async def todo_remove_edge(from_id: str, to_id: str) -> str:
    """Remove a directed edge between two todo nodes.

    Args:
        from_id: ID of the predecessor node.
        to_id: ID of the successor node.

    Returns:
        Formatted string showing the updated graph, or ERROR if the edge
        doesn't exist.
    """
```

### _emit_update (No Code Change — Frozen Schema)

```python
async def _emit_update(
    live_event_hub: "LiveEventHub | None",
    current_instance_id: str,
    todos: list[dict],
) -> None:
    """Best-effort SSE emission — never raises.

    The todos list follows the frozen SSE payload schema from Phase 1:
    each dict has {id, index, text, status, comment, next_ids}.
    The LiveEventHub.stream_todo_update passes this through unchanged.
    """
    # Identical implementation — the data shape flows through
```

### Tool Count Change

Factory now returns **6 tools** instead of 4:
```python
return [todo_create, todo_update, todo_list, todo_clear, todo_add_edge, todo_remove_edge]
```

**IMPORTANT**: This changes the tool list length. Any code that asserts `len(tools) == 4` must be updated. The `_build_tools()` test helper returns all tools — tests checking `tools[0]` through `tools[3]` still work; new tools are `tools[4]` and `tools[5]`.

## Agent Documentation Updates (C10 fix)

### skill.md Update

`agents/_prompt_system/innate-skills/todo/skill.md` (16 lines) currently documents 4 tools. Must be updated to:

```markdown
# Todo Skill

Track multi-step workflows with a todo list or task graph. Use these tools to plan, track progress, and mark items complete.

## Tool Inventory

| Tool | Purpose |
|------|---------|
| `todo_create(items)` | Create/replace the full todo list (flat list, backward compatible) |
| `todo_create(nodes, edges)` | Create a task graph with branches and dependencies (new) |
| `todo_update(index, status)` | Update item status by index (backward compatible) |
| `todo_update(node_id, status)` | Update item status by node ID (new) |
| `todo_list()` | View current todo graph |
| `todo_clear()` | Clear all items |
| `todo_add_edge(from_id, to_id)` | Add a dependency edge between nodes (new) |
| `todo_remove_edge(from_id, to_id)` | Remove a dependency edge (new) |

## Behavioral Hint

When you complete a task, mark it `done` via `todo_update`. The system will remind you of the next ready item(s) — nodes whose predecessors are all done. Keep your todo list current throughout multi-step work — it helps you track progress and avoid skipping steps.
```

### Ari Agent File Sweep

The following files reference `todo_create` or todo tools and should be reviewed for accuracy:

| File | Line(s) | Reference | Action |
|------|---------|-----------|--------|
| `agents/ari/soul.md` | 31 | `I track progress with todo_create` | No change needed — `todo_create` still exists |
| `agents/ari/rule.md` | 55 | `If multi-step → track with todo_create` | No change needed |
| `agents/ari/workflow.md` | 21,294 | `todo_create()` | No change needed — signature backward compatible |
| `agents/ari/workflow.md` | 30,295 | `update todos` | No change needed — `todo_update` still exists |
| `agents/ari/workflow.md` | 50,238,261,291 | "todo list" references | No change needed — `todo_list` still exists |

**Conclusion**: Ari files require NO changes — all references use backward-compatible call patterns (`todo_create(items=...)` and `todo_update(index, status)`). The tool names and flat-list signatures are preserved.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `_STATUS_ICONS` | No change needed — same 3 statuses. | `daemon/tools/todo_tools.py` |
| 2 | Implement `_format_graph()` | Replace `_format_list()`. DFS from root nodes with `visited: set[str]` for merge handling (W6). Indent by depth, show `└→` for children, `(merged)` for already-visited nodes. Fall back to flat list if linear. | `daemon/tools/todo_tools.py` |
| 3 | Update `todo_create` signature | Accept `items: list[str] \| None`, `nodes: list[dict] \| None`, `edges: list[dict] \| None` (C2 — edges are dicts with `from`/`to` keys). Route to `create()` or `create_graph()`. | `daemon/tools/todo_tools.py` |
| 4 | Update `todo_update` signature | Accept `index: int \| None = None` (first, backward compat), `status: str = ""` (second), `node_id: str \| None = None` (third, new). Route to `update()` or `update_by_index()`. (C5 fix — preserves positional compat.) | `daemon/tools/todo_tools.py` |
| 5 | Update `todo_list` to use `_format_graph` | Call `get_all()`, pass to `_format_graph()`. Header changes to "Current todo graph:" | `daemon/tools/todo_tools.py` |
| 6 | Verify `todo_clear` still works | No changes needed — calls `clear()` which is unchanged. | `daemon/tools/todo_tools.py` |
| 7 | Implement `todo_add_edge` tool | Call `manager._todo_manager.add_edge()`. Return formatted graph or ERROR. Emit SSE. | `daemon/tools/todo_tools.py` |
| 8 | Implement `todo_remove_edge` tool | Call `manager._todo_manager.remove_edge()`. Return formatted graph or ERROR. Emit SSE. | `daemon/tools/todo_tools.py` |
| 9 | Verify `_emit_update` | No code change — data follows frozen Phase 1 schema. Verify dict shape includes `id`, `index`, `next_ids`. | `daemon/tools/todo_tools.py` |
| 10 | Update factory return list | Return 6 tools instead of 4. Update `_full_doc_` for all tools. | `daemon/tools/todo_tools.py` |
| 11 | Update `CATEGORY_DOC` | Document the new graph capability and 2 new tools. | `daemon/tools/todo_tools.py` |
| 12 | Verify tool registration | Ensure `todo_add_edge` and `todo_remove_edge` have `@register_tool_category("todo")`. Verify they are NOT tagged `"instance"`. | `daemon/tools/todo_tools.py` |
| 13 | Update `skill.md` | Update `agents/_prompt_system/innate-skills/todo/skill.md` to document all 6 tools + graph mode. (C10 fix.) | `agents/_prompt_system/innate-skills/todo/skill.md` |
| 14 | Sweep Ari agent files | Verify `agents/ari/{soul,rule,workflow}.md` references are backward-compatible. No changes needed — all use flat-list patterns. Document the sweep result. (C10 fix.) | `agents/ari/soul.md`, `agents/ari/rule.md`, `agents/ari/workflow.md` |

## Key Files

- `daemon/tools/todo_tools.py` — **PRIMARY** — all tool changes
- `daemon/services/todo_manager.py` — Phase 1 provides the manager methods called by tools
- `daemon/services/live_event_hub.py` — `stream_todo_update()` receives the frozen dict shape (no code change needed)
- `agents/_prompt_system/innate-skills/todo/skill.md` — **C10 fix** — update tool inventory table
- `agents/ari/soul.md` — C10 sweep (verify, no changes needed)
- `agents/ari/rule.md` — C10 sweep (verify, no changes needed)
- `agents/ari/workflow.md` — C10 sweep (verify, no changes needed)

## Constraints

- **Backward compatibility**: `todo_create(items=["A","B"])` must still work (agents don't need to change)
- **Backward compatibility**: `todo_update(0, "done")` positional call must still work (C5 fix — `index` stays first param)
- **Backward compatibility**: `todo_update(index=0, status="done")` keyword call must still work
- **Tool names preserved**: `todo_create`, `todo_update`, `todo_list`, `todo_clear` keep their names
- **New tool names**: `todo_add_edge`, `todo_remove_edge` — descriptive, follow `todo_` prefix convention
- **Category tag**: All 6 tools tagged `"todo"`, never `"instance"`
- **SSE emission**: Every mutating tool emits `todo_update` SSE (best-effort, swallows exceptions). Payload follows frozen Phase 1 schema.
- **Error handling**: Tools return `ERROR: ...` strings, never raise exceptions
- **`_full_doc_`**: Every tool must have extended documentation for LLM context
- **Edge format**: `edges` parameter is always `list[dict]` with `{"from": str, "to": str}` (C2 fix)
- **No coupling to Phase 3**: SSE schema is frozen in Phase 1; tools don't depend on API endpoint changes (W10 fix)

## Deliverables

- [ ] `todo_create` accepts both flat list and graph structure input (edges as `list[dict]`)
- [ ] `todo_update` preserves positional compat: `todo_update(0, "done")` still works (C5)
- [ ] `todo_update` accepts `node_id` as optional third parameter
- [ ] `todo_list` renders graph structure with branching + merge node visualization (W6)
- [ ] `todo_clear` unchanged and working
- [ ] `todo_add_edge` tool implemented and registered
- [ ] `todo_remove_edge` tool implemented and registered
- [ ] `_format_graph()` renders DAG as text with `└→` indentation and `(merged)` annotation for diamonds
- [ ] All 6 tools tagged `"todo"` category, never `"instance"`
- [ ] SSE emission works for all mutating tools (frozen payload schema)
- [ ] Factory returns 6 tools
- [ ] All `_full_doc_` attributes updated
- [ ] `skill.md` updated with 6-tool inventory (C10)
- [ ] Ari agent files swept and verified backward-compatible (C10)
