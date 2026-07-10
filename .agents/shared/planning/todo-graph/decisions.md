# Architecture Decisions: Todo Graph (DAG) Transformation

## Decision Record

> **Revision note (2026-07-10)**: ADRs updated to reflect review fixes C1-C12 and W2-W14. Changed ADRs are marked with [REVISED]. New ADRs added for issues that required architectural decisions.

### ADR-1: Node Identity — String ID Replaces Integer Index

**Status**: Decided ✅ (approved, no changes)  
**Date**: 2026-07-10

**Context**: The current `TodoItem` uses `index: int` as identity. A DAG requires stable node identity independent of position — nodes can have multiple predecessors and successors, so positional index is meaningless.

**Decision**: Use `id: str` (prefixed `"n-" + UUID4 hex[:8]`) as node identity. The `index` field is PRESERVED in the dataclass and serialized output for backward compatibility.

**Rationale**:
- UUID4 hex[:8] gives 4 billion possible suffixes — collision probability negligible for <200 nodes
- `n-` prefix guarantees IDs are never all-numeric (C3 fix — prevents collision with API's numeric-index backward-compat path)
- 8 chars is short enough to display in tool output (`[n-a1b2c3d4]`)
- String IDs work in URL paths without type coercion

**Consequences**:
- All consumers (tools, API, frontend) gain `id` as primary identity
- `index` preserved alongside `id` for backward compatibility (C4 fix)
- Backward-compat shims (`update_by_index`, `set_comment_by_index`) bridge the gap

---

### ADR-2: Adjacency List Storage (next_ids) — Not Edge List [REVISED]

**Status**: Decided (revised — W2 fix)  
**Date**: 2026-07-10

**Context**: A DAG can be stored as (a) an adjacency list per node (`next_ids: list[str]`) or (b) a separate edge list (`edges: [{from, to}]`).

**Decision**: Store edges as `next_ids: list[str]` on each `TodoNode`. The edge list is derived on-demand by `get_graph()`.

**Revision (W2 fix)**: The `TodoNode` dataclass uses `field(default_factory=list)` for `next_ids` (correct Python pattern). Method signatures that accept `next_ids` as a parameter (e.g., `add_node`) use `next_ids: list[str] | None = None` and resolve to `next_ids or []` inside the method body — never a bare `[]` default.

**Rationale**:
- Adjacency list is the natural representation for DFS/BFS/cycle detection
- Single data structure (dict of nodes) — no need to sync two collections
- Serialization is simpler — each node dict is self-contained
- `get_all()` returns a list of node dicts, each carrying its own edges — backward compatible

**Consequences**:
- Adding an edge requires updating `node.next_ids` (O(1))
- Removing an edge requires filtering `node.next_ids` (O(E_per_node))
- Edge format in `create_graph()` and tool params is `list[dict]` with `{"from": str, "to": str}` keys (C2 fix — no `list[tuple]`)

---

### ADR-3: Backward Compatibility Strategy — Overloaded Tool Signatures [REVISED]

**Status**: Decided (revised — C5 fix)  
**Date**: 2026-07-10

**Context**: Existing agents use `todo_create(items=["A","B"])` and `todo_update(0, "done")` (positional). Changing these signatures breaks all deployed agents.

**Decision**: Use Python's optional parameters to overload tool signatures.

**Revision (C5 fix)**: `todo_update` preserves `index` as the FIRST positional parameter:
```python
async def todo_update(
    index: int | None = None,  # FIRST — preserves todo_update(0, "done")
    status: str = "",           # SECOND — preserves positional compat
    node_id: str | None = None, # THIRD — new, optional
) -> str
```
This ensures `todo_update(0, "done")` still maps to `index=0, status="done"`.

**Rationale**:
- Agents don't need prompt changes — `items=["A","B"]` and `todo_update(0, "done")` still work
- New graph-aware agents can use `node_id="n-xxx"` parameter
- The `_by_index` shims resolve index → node_id internally

**Consequences**:
- Two code paths in `todo_create` and `todo_update` (must test both)
- `todo_create` uses `items=None` as discriminator (flat-list vs graph)
- `todo_update` uses `node_id` precedence over `index`

---

### ADR-4: DAG Validation — Kahn's Algorithm for Cycle Detection [REVISED]

**Status**: Decided (revised — C1 fix)  
**Date**: 2026-07-10

**Context**: The graph must be a valid DAG (no cycles). Cycle detection must run on `create_graph()` and `add_edge()`.

**Decision**: Use Kahn's algorithm (topological sort) with `collections.deque`.

**Revision (C1 fix)**: The original plan had two bugs:
1. `queue.append(nid)` — appended the parent instead of the child whose in-degree dropped to 0. Fixed to `queue.append(next_id)`.
2. `list.pop(0)` — O(n) per pop. Fixed to `collections.deque.popleft()` — O(1).

```python
from collections import deque

queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
while queue:
    nid = queue.popleft()  # O(1)
    visited += 1
    for next_id in nodes[nid].next_ids:
        if next_id in in_degree:
            in_degree[next_id] -= 1
            if in_degree[next_id] == 0:
                queue.append(next_id)  # FIXED: was queue.append(nid)
```

**Rationale**: O(V+E) time, simple to implement, also produces topological order for layout.

---

### ADR-5: Reminder Logic — "Ready" Nodes (All Predecessors Done)

**Status**: Decided ✅ (approved, no changes)  
**Date**: 2026-07-10

**Context**: The current reminder shows "Next: {first_pending_text}". In a DAG, "next" is ambiguous.

**Decision**: A node is "ready" if it is `pending` AND all its predecessors are `done`. The reminder shows all ready nodes.

**Reminder format**:
- Single ready node: `"⏭️ Next: {text}"`
- Multiple ready nodes: `"⏭️ Ready: {text1}, {text2}, ..."`
- No ready nodes but pending exist: `"⏳ {N} items blocked"`
- No pending nodes: `"All items completed! ✅"`

---

### ADR-6: Frontend Rendering — Custom SVG, No Graph Library [REVISED]

**Status**: Decided (revised — W12, W13 fixes)  
**Date**: 2026-07-10

**Context**: The frontend must render a DAG. Options: (a) graph layout library, (b) Mermaid, (c) custom SVG.

**Decision**: Custom SVG rendering with a simple layered layout algorithm. No new npm dependencies.

**Revision (W12 fix)**: Deleted dead code `startY = (containerWidth - totalHeight) / 2` which incorrectly mixed X width with Y height. Replaced with `yCursor = 0` that increments per node within each layer.

**Revision (W13 fix)**: Container width is NOT hardcoded to 600px. Instead, a `ResizeObserver` tracks the actual container element width and updates a signal. The layout uses the signal's value.

**Revision (C11 fix)**: All template-referenced constants (`NODE_WIDTH`, `NODE_HEIGHT`, `LAYER_GAP_X`) are declared as class members. `graphWidth()` and `graphHeight()` are computed signals.

**Rationale**:
- Typical graphs are 5-20 nodes — no need for a heavy layout engine
- `foreignObject` lets us put HTML (buttons, text) inside SVG nodes — full control
- Linear chains fall back to current flat list rendering — zero visual regression

---

### ADR-7: SSE Payload — Augmented Dicts, Not Restructured [REVISED]

**Status**: Decided (revised — C4 fix, W10 fix)  
**Date**: 2026-07-10

**Context**: The SSE `todo_update` event currently sends `{"todos": [{index, text, status, comment}]}`. The new payload must include graph structure.

**Decision**: Augment each node dict with `id` and `next_ids` fields. PRESERVE `index` field. Keep the payload as `{"todos": [...]}`.

**Revision (C4 fix)**: The `index` field is PRESERVED in the serialized output. It is NOT removed. The payload is augmented (6 keys: `id`, `index`, `text`, `status`, `comment`, `next_ids`), not replaced. Old frontend code that reads `item.index` and `track item.index` continues to work without changes.

**Revision (W10 fix)**: The SSE payload schema is FROZEN as a Phase 1 deliverable. Once `_to_dict()` is defined in Phase 1, the schema is locked. Phases 2, 3, and 4 build against the frozen schema — no cross-phase coupling between Phase 2 (tools) and Phase 3 (API).

**Rationale**:
- Backward compatible — old frontend code ignores new fields, `index` preserved
- No SSE event type change — stays `"todo_update"`
- `LiveEventHub.stream_todo_update()` needs zero code changes
- Frozen schema eliminates Phase 2↔3 coupling

---

### ADR-8: Keep `_todo_manager` Attribute Name

**Status**: Decided ✅ (approved, no changes)  
**Date**: 2026-07-10

**Decision**: Keep the attribute name `_todo_manager`. `TodoManager = TodoGraphManager` alias preserves all imports.

---

### ADR-9: Tool Count Change — 4 → 6 Tools

**Status**: Decided ✅ (approved, no changes)  
**Date**: 2026-07-10

**Decision**: Adding `todo_add_edge` and `todo_remove_edge` increases the tool count from 4 to 6. Test assertions updated to expect 6 tools.

---

### ADR-10: Max Nodes Guard — 200 Per Instance [REVISED]

**Status**: Decided (revised — W4 fix)  
**Date**: 2026-07-10

**Decision**: Enforce a maximum of 200 nodes per instance graph.

**Revision (W4 fix)**: The guard is enforced on ALL node-creation paths: `create()` (flat list), `create_graph()` (graph structure), AND `add_node()` (incremental). The original plan only applied the guard to `create_graph()`.

---

### ADR-11: Node ID Prefix — `n-` Prevents Numeric Collision [NEW — C3 fix]

**Status**: Decided (new)  
**Date**: 2026-07-10

**Context**: Node IDs are `uuid.uuid4().hex[:8]`. 8-char hex can be all digits ~1/438 of the time. With 200 nodes per instance, P(at least one all-numeric ID) ≈ 36%. The API comment endpoint uses `node_id.isdigit()` to distinguish between numeric indices (backward compat) and string node IDs. An all-numeric node ID would incorrectly route to `set_comment_by_index()`.

**Decision**: All generated node IDs are prefixed with `n-` (e.g., `n-a1b2c3d4`). This guarantees they are never all-numeric.

**Rationale**:
- `n-` prefix is short, human-readable, and clearly non-numeric
- Eliminates the `isdigit()` collision risk entirely
- No impact on URL paths or tool output readability

---

### ADR-12: TypeScript Interface — `TodoNode` with Preserved `index` [NEW — C4/C6 fix]

**Status**: Decided (new)  
**Date**: 2026-07-10

**Context**: The frontend `TodoItem` interface has `index: number`. The new `TodoNode` needs `id: string` and `next_ids: string[]`. Making `TodoItem extends TodoNode` would cause TS compile failures because `TodoItem` has `index` but `TodoNode` originally didn't.

**Decision**: `TodoNode` includes `index: number` as a required field (it's always present in the frozen SSE payload). `TodoItem` becomes a simple type alias: `type TodoItem = TodoNode`. No inheritance, no compile issues.

**Rationale**:
- `index` is preserved in the SSE payload (C4 fix), so it's always available
- Including `index` in `TodoNode` means all existing `item.index` references work
- `type TodoItem = TodoNode` is the simplest backward-compatible alias
- No `extends` chain means no TS structural typing issues

---

### ADR-13: Text Graph Rendering — Merge Node Annotation [NEW — W6 fix]

**Status**: Decided (new)  
**Date**: 2026-07-10

**Context**: The `_format_graph()` text renderer uses DFS from root nodes. In a diamond pattern (A→B, A→C, B→D, C→D), node D would be visited twice — once from B's subtree and once from C's subtree. Without tracking, this causes infinite loops or duplicate rendering.

**Decision**: The DFS tracks `visited: set[str]`. When encountering an already-visited node (merge point), it is annotated with `(merged)` instead of being re-rendered. This gives the agent a clear picture of the graph topology without duplication.

**Example output**:
```
[0] ○ Setup
  └→ [1] ○ Task A
       └→ [2] ○ Merge point
  └→ [3] ○ Task B
       └→ [2] (merged)
```

---

### ADR-14: SSE Schema Freeze — Phase 1 Deliverable [NEW — W10 fix]

**Status**: Decided (new)  
**Date**: 2026-07-10

**Context**: Phase 2 (tools) emits SSE events. Phase 3 (API) also emits SSE events. If the SSE payload schema is defined in Phase 3, then Phase 2 has a tight coupling to Phase 3 — tools can't be tested without the API payload shape being finalized.

**Decision**: The SSE payload schema is FROZEN as a Phase 1 deliverable. The `_to_dict()` method in `TodoGraphManager` defines the exact 6-key dict shape (`id`, `index`, `text`, `status`, `comment`, `next_ids`). Once Phase 1 is complete, this shape is locked. Phases 2, 3, and 4 all build against the frozen schema.

**Rationale**:
- Eliminates Phase 2↔3 coupling — both depend only on Phase 1
- Enables maximum parallelization (Phases 2, 3, 4 can all start after Phase 1)
- The schema is simple (6 keys) and unlikely to change
- Any future schema change requires cross-phase coordination (documented in code)
