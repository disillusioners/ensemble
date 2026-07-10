# Phase 1: Backend Data Model + DAG Service

## Objective

Replace `TodoItem` (flat, index-keyed) with `TodoNode` (graph, ID-keyed with adjacency lists). Implement `TodoGraphManager` as a drop-in replacement for `TodoManager` — same public method names, same threading pattern, same return-dict shape (augmented with `id`, `next_ids`, and preserved `index` fields), plus new graph primitives (`add_node`, `add_edge`, `remove_edge`) and DAG validation (cycle detection via Kahn's algorithm).

**Phase 1 also freezes the SSE payload schema** — the exact dict shape that `get_all()` returns is the contract Phases 2, 3, and 4 build against. Once Phase 1 defines `_to_dict()`, the SSE payload shape is frozen and downstream phases can start in parallel.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/services/todo_manager.py` (Phase 2 tools import from it; Phase 3 router calls it; Phase 4 frontend consumes its SSE payload shape)
- **Shared APIs/interfaces**: `TodoGraphManager` public method signatures are the contract Phases 2-4 build against. **The `_to_dict()` output shape (the SSE payload schema) is frozen as a Phase 1 deliverable.**
- **Why this coupling**: The data model is the foundation — every other phase calls `TodoGraphManager` methods or consumes its output shape. Interface must be stable before downstream phases start. By freezing the SSE schema in Phase 1, Phase 2 (tools) and Phase 3 (API) can proceed in parallel without coupling to each other.

## Context

- Current `TodoManager` at `daemon/services/todo_manager.py` (260 lines)
- Instantiated at `daemon/manager.py:716` as `self._todo_manager = TodoManager()`
- Cleaned up at `daemon/services/instance_lifecycle.py:836-840` via `clear(instance_id)`
- `_normalize_status()` and `_STATUS_ALIASES` dict (16 aliases) must be preserved unchanged
- `MAX_COMMENT_LENGTH = 1000` must be preserved
- `threading.Lock` pattern must be preserved (sync, not asyncio)

## Design: New Data Model

### TodoNode Dataclass

```python
@dataclass
class TodoNode:
    """A single node in the todo DAG.

    Replaces TodoItem. Identity is the string ``id`` (not positional index).
    Edges are stored as ``next_ids`` — a list of node IDs that follow this node.
    A node with empty ``next_ids`` is a terminal/sink node.

    The ``index`` field is preserved for backward compatibility — it is derived
    from insertion order and included in serialized output so existing consumers
    that reference ``item["index"]`` continue to work.
    """
    id: str               # Unique within the instance's graph (prefixed "n-" + hex)
    text: str             # Human-readable description (immutable after creation)
    status: str           # "pending" | "in_progress" | "done"
    comment: str = ""     # User annotation side-channel (max 1000 chars)
    next_ids: list[str] = field(default_factory=list)  # Adjacency list (successors)
    index: int = 0        # Insertion-order position (backward compat, derived)
```

> **W2 fix**: The dataclass uses `field(default_factory=list)` for `next_ids` (correct Python pattern for mutable defaults). However, method signatures that accept `next_ids` as a parameter (e.g., `add_node`) use `next_ids: list[str] | None = None` and resolve to `next_ids or []` inside the method body — never a bare `[]` default.

### TodoGraphManager Class

```python
class TodoGraphManager:
    """In-memory, per-instance todo DAG manager.

    Replaces TodoManager. Each instance_id owns a graph stored as
    ``dict[str, TodoNode]`` (node_id → node). The manager enforces DAG
    validity (no cycles) on all structural mutations.

    Thread Safety:
        Same pattern as TodoManager — single threading.Lock guarding
        all state mutations and snapshot reads.
    """

    MAX_NODES = 200  # Safety valve per instance

    def __init__(self) -> None:
        self._instance_graphs: dict[str, dict[str, TodoNode]] = {}
        self._lock = threading.Lock()
```

### Method Signatures

| Method | Signature | Returns | Backward Compatible? |
|--------|-----------|---------|---------------------|
| `create` | `(instance_id, items: list[str])` | `list[dict]` | ✅ Yes — auto-converts flat list to linear chain |
| `create_graph` | `(instance_id, nodes: list[dict], edges: list[dict])` | `list[dict]` | New method for graph input |
| `update` | `(instance_id, node_id: str, status: str)` | `dict \| None` | ⚠️ Changed — `node_id` replaces `index` (but tools provide backward-compat shim) |
| `update_by_index` | `(instance_id, index: int, status: str)` | `dict \| None` | ✅ New — backward-compat shim that resolves index → node_id |
| `set_comment` | `(instance_id, node_id: str, comment: str)` | `dict` | ⚠️ Changed — `node_id` replaces `index` |
| `set_comment_by_index` | `(instance_id, index: int, comment: str)` | `dict` | ✅ New — backward-compat shim |
| `get_all` | `(instance_id)` | `list[dict]` | ✅ Yes — returns list of node dicts (includes `id`, `next_ids`, AND `index`) |
| `get_graph` | `(instance_id)` | `dict` | New — returns `{"nodes": [...], "edges": [...]}` structured form |
| `clear` | `(instance_id)` | `None` | ✅ Yes — identical behavior |
| `add_node` | `(instance_id, text: str, next_ids: list[str] \| None = None)` | `dict` | New — add single node to existing graph |
| `add_edge` | `(instance_id, from_id: str, to_id: str)` | `dict \| None` | New — add directed edge, validates no cycle |
| `remove_edge` | `(instance_id, from_id: str, to_id: str)` | `dict \| None` | New — remove directed edge |
| `remove_node` | `(instance_id, node_id: str)` | `dict \| None` | New — remove node and all its edges |

> **C2 fix**: `create_graph` accepts `edges: list[dict]` where each dict is `{"from": "node_id_a", "to": "node_id_b"}`. This matches the Phase 2 tool signature and the `get_graph()` output shape. No `list[tuple]` anywhere.

### DAG Validation

**Cycle detection** via Kahn's algorithm (topological sort):

> **C1 fix**: The original plan had `queue.append(nid)` (appending the parent instead of the child whose in-degree dropped to 0) and used `list.pop(0)` (O(n) per pop). Fixed to `queue.append(next_id)` and `collections.deque.popleft()` (O(1) per pop).

```python
from collections import deque

def _has_cycle(self, nodes: dict[str, TodoNode]) -> bool:
    """Detect cycles using Kahn's algorithm. O(V+E).

    Returns True if the graph contains a cycle (not a valid DAG).
    """
    in_degree = {nid: 0 for nid in nodes}
    for node in nodes.values():
        for next_id in node.next_ids:
            if next_id in in_degree:
                in_degree[next_id] += 1
    # Start with all zero-in-degree nodes (roots)
    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        nid = queue.popleft()  # O(1) — was list.pop(0) which is O(n)
        visited += 1
        for next_id in nodes[nid].next_ids:
            if next_id in in_degree:
                in_degree[next_id] -= 1
                if in_degree[next_id] == 0:
                    queue.append(next_id)  # FIXED: was queue.append(nid)
    return visited != len(nodes)
```

**Validation rules** (enforced on `create_graph`, `add_node`, `add_edge`, AND `create`):
1. All `next_ids` references must point to existing nodes (no dangling edges)
2. Adding an edge must not create a cycle
3. Node IDs must be unique within the instance's graph
4. Max nodes per instance: 200 (safety valve) — **enforced on BOTH `create()` and `create_graph()`** (W4 fix)

### Flat List → Linear Chain Auto-Conversion

> **W4 fix**: `MAX_NODES` guard applied to the flat-list `create()` path as well, not just `create_graph()`.

```python
def create(self, instance_id: str, items: list[str]) -> list[dict]:
    """Backward-compatible create: flat list → linear chain DAG.

    ["A", "B", "C"] becomes:
        n-xxxxxx (A) → n-yyyyyy (B) → n-zzzzzz (C)
    with next_ids = ["n-yyyyyy"], ["n-zzzzzz"], [] respectively.

    Each node also gets an ``index`` field (0, 1, 2) for backward compat.
    """
    if len(items) > self.MAX_NODES:
        raise ValueError(
            f"Cannot create {len(items)} todo nodes: exceeds maximum of "
            f"{self.MAX_NODES}. Use todo_clear() to reset first."
        )
    node_ids = [self._generate_id() for _ in items]
    nodes = {}
    for i, text in enumerate(items):
        next_ids = [node_ids[i+1]] if i + 1 < len(items) else []
        nodes[node_ids[i]] = TodoNode(
            id=node_ids[i], text=text, status="pending",
            comment="", next_ids=next_ids, index=i
        )
    with self._lock:
        self._instance_graphs[instance_id] = nodes
        return [self._to_dict(node) for node in nodes.values()]
```

### Reminder Logic (Graph-Aware)

The `update()` method's reminder must handle branching AND preserve the comment-fence prompt-injection protection from the current `TodoManager.update()` (lines 183-186).

**Comment-fence pattern (PRESERVED from current implementation)**: When the updated node is marked `"done"` AND has a non-empty `comment`, the reminder is prefixed with:

```
User commented:
---
{comment}
---
```

The `---` fences visually separate untrusted user-supplied comment text from the rest of the system-formatted reminder, making prompt injection attempts embedded in the comment obvious to the agent. This is a **security-critical pattern** — it must not be lost in the graph refactor.

```python
def _compute_reminder(
    self,
    nodes: dict[str, TodoNode],
    updated_node_id: str,
    updated_status: str,
) -> str:
    """Compute reminder for graph structure.

    Preserves the comment-fence prompt-injection protection: when the
    updated node is marked "done" with a non-empty comment, the reminder
    is prefixed with fenced comment text before the graph-state reminder.

    Logic:
    1. Compute the "base reminder" based on graph state:
       a. Find all "ready" pending nodes — pending nodes whose ALL
          predecessors are done.
       b. If ready nodes exist: "⏭️ Next: {text1}, {text2}, ..." (list all ready)
       c. If no ready nodes but pending nodes exist: "⏳ Waiting: {count} blocked items"
       d. If no pending nodes: "All items completed! ✅"
    2. If updated_status == "done" AND the updated node has a non-empty
       comment, prefix the base reminder with the fenced comment:
           "User commented:\\n---\\n{comment}\\n---\\n" + base_reminder
    3. Otherwise, return the base reminder unchanged.

    The comment-fence prefix is ONLY applied when:
      - The status transition is to "done" (not "in_progress" or "pending")
      - The comment is non-empty (empty string → no prefix)
    This matches the current TodoManager.update() behavior exactly.
    """
    # Step 1: Compute base reminder from graph state
    updated_node = nodes[updated_node_id]

    # Build reverse adjacency (predecessor map) for "ready" computation
    predecessors: dict[str, list[str]] = {nid: [] for nid in nodes}
    for node in nodes.values():
        for next_id in node.next_ids:
            if next_id in predecessors:
                predecessors[next_id].append(node.id)

    ready_nodes = [
        node for node in nodes.values()
        if node.status == "pending"
        and all(
            nodes[pred_id].status == "done"
            for pred_id in predecessors[node.id]
        )
    ]

    if ready_nodes:
        texts = ", ".join(n.text for n in ready_nodes)
        base_reminder = f"\n\n⏭️ Next: {texts}"
    elif any(n.status == "pending" for n in nodes.values()):
        blocked_count = sum(1 for n in nodes.values() if n.status == "pending")
        base_reminder = f"\n\n⏳ Waiting: {blocked_count} blocked items"
    else:
        base_reminder = "\n\nAll items completed! ✅"

    # Step 2: Apply comment-fence prefix (prompt-injection protection)
    if updated_status == "done" and updated_node.comment:
        return f"User commented:\n---\n{updated_node.comment}\n---\n" + base_reminder

    # Step 3: Return base reminder without comment prefix
    return base_reminder
```

**The `update()` method calls `_compute_reminder()` like this:**

```python
def update(self, instance_id: str, node_id: str, status: str) -> dict | None:
    normalized = _normalize_status(status)
    if normalized is None:
        return None
    with self._lock:
        nodes = self._instance_graphs.get(instance_id)
        if nodes is None or node_id not in nodes:
            return None
        nodes[node_id].status = normalized
        snapshot = [self._to_dict(n) for n in nodes.values()]
        reminder = self._compute_reminder(nodes, node_id, normalized)
        return {"todos": snapshot, "reminder": reminder}
```

### Node ID Generation

> **C3 fix**: Node IDs are prefixed with `n-` to guarantee they are never all-numeric. This prevents collision with the `node_id.isdigit()` backward-compat path in the API comment endpoint, which would route numeric strings to `set_comment_by_index()`.
>
> **Implementation note (all-numeric user-supplied IDs)**: When `create_graph()` accepts user-supplied node IDs (via the `nodes` parameter), a user could pass all-numeric IDs like `"123"`. If such a node is later referenced via the API comment endpoint (`POST /todos/{node_id}/comment`), the `node_id.isdigit()` check would incorrectly route it to `set_comment_by_index()` instead of `set_comment()`. **Mitigation**: In `create_graph()`, if a user-supplied ID is all-numeric, either (a) reject it with a `ValueError` asking for a non-numeric ID, or (b) auto-prefix it with `n-` and update all edge references accordingly. Option (a) is simpler and recommended — fail fast with a clear error message. This only affects `create_graph()` (user-supplied IDs); `create()` and `add_node()` always use `_generate_id()` which produces `n-`-prefixed IDs.

```python
import uuid

def _generate_id(self) -> str:
    """Generate a short unique node ID.

    Uses 'n-' prefix + UUID4 hex truncated to 8 chars.
    The 'n-' prefix guarantees the ID is never all-numeric, preventing
    collision with the API's numeric-index backward-compat path
    (node_id.isdigit() → set_comment_by_index).

    Collision probability for <200 nodes is negligible
    (8 hex chars = 4 billion possible suffixes).
    """
    return f"n-{uuid.uuid4().hex[:8]}"
```

### _to_dict Serialization (SSE Payload Schema — FROZEN)

> **C4 fix**: The `index` field is PRESERVED in the serialized output alongside the new `id` and `next_ids` fields. This is critical for backward compatibility — the frontend's `track item.index` and `item.index` references, and existing test assertions on `set(item.keys()) == {"index", "text", "status", "comment"}`, must not break. The payload is augmented, not replaced.

> **This is the frozen SSE payload schema** (W10 fix). Once this shape is defined in Phase 1, Phases 2, 3, and 4 can build against it in parallel without cross-phase coupling.

```python
@staticmethod
def _to_dict(node: TodoNode) -> dict:
    """Serialize a TodoNode to a plain dict for JSON transport.

    FROZEN SCHEMA — this is the SSE payload shape that Phases 2, 3, and 4
    build against. Do NOT change without coordinating across all phases.

    Output shape (backward compatible — index preserved, id + next_ids added):
        {
            "id": "n-a1b2c3d4",        # NEW: stable node identity
            "index": 0,                 # PRESERVED: insertion-order position
            "text": "Setup DB",
            "status": "pending",
            "comment": "",
            "next_ids": ["n-e5f6g7h8"]  # NEW: adjacency list (successors)
        }
    """
    return {
        "id": node.id,
        "index": node.index,           # PRESERVED for backward compat
        "text": node.text,
        "status": node.status,
        "comment": node.comment,
        "next_ids": list(node.next_ids),  # Copy to prevent mutation
    }
```

### get_graph Method (Structured Graph Output)

```python
def get_graph(self, instance_id: str) -> dict:
    """Return the graph as {nodes: [...], edges: [...]} structure.

    Edges are derived from next_ids adjacency lists:
        [{"from": "n-abc", "to": "n-def"}, ...]
    """
    with self._lock:
        nodes = self._instance_graphs.get(instance_id, {})
        node_dicts = [self._to_dict(n) for n in nodes.values()]
        edges = []
        for node in nodes.values():
            for next_id in node.next_ids:
                edges.append({"from": node.id, "to": next_id})
        return {"nodes": node_dicts, "edges": edges}
```

### Frozen SSE Payload Schema (Phase 1 Deliverable)

> **W10 fix**: The SSE payload schema is frozen here in Phase 1 so that Phase 2 (tools) and Phase 3 (API) have no coupling to each other — both depend only on Phase 1's frozen schema.

The `todo_update` SSE event payload is:
```json
{
  "instance_id": "inst-123",
  "event_type": "todo_update",
  "todos": [
    {
      "id": "n-a1b2c3d4",
      "index": 0,
      "text": "Setup DB",
      "status": "pending",
      "comment": "",
      "next_ids": ["n-e5f6g7h8"]
    }
  ]
}
```

**Key invariants** (frozen, do not change without cross-phase coordination):
1. `todos` is always a `list[dict]` — never restructured to `{"nodes": [...]}`
2. Each dict has 6 keys: `id`, `index`, `text`, `status`, `comment`, `next_ids`
3. `index` is always present (int, derived from insertion order)
4. `id` is always present (str, prefixed with `n-`)
5. `next_ids` is always present (list[str], may be empty)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `TodoNode` dataclass | Replace `TodoItem`. Fields: `id`, `text`, `status`, `comment`, `next_ids` (via `field(default_factory=list)`), `index` (int, for backward compat). | `daemon/services/todo_manager.py` |
| 2 | Implement `TodoGraphManager` class | Replace `TodoManager`. Storage: `dict[str, dict[str, TodoNode]]`. All methods under `threading.Lock`. Preserve `_normalize_status`, `_STATUS_ALIASES`, `MAX_COMMENT_LENGTH`. Add `MAX_NODES = 200` class constant. | `daemon/services/todo_manager.py` |
| 3 | Implement `create()` (backward-compat) | Accept `list[str]`, auto-convert to linear chain. Generate `n-`-prefixed node IDs. Assign `index` per insertion order. Enforce `MAX_NODES` guard (W4). Return list of dicts with `id`, `index`, `next_ids` added. | `daemon/services/todo_manager.py` |
| 4 | Implement `create_graph()` | Accept `nodes: list[dict]` (each `{id, text, next_ids?}`) + `edges: list[dict]` (each `{"from": str, "to": str}`). Validate DAG (no cycles, no dangling refs). Enforce `MAX_NODES` guard. Assign `index` per insertion order. | `daemon/services/todo_manager.py` |
| 5 | Implement `update()` by node_id | Accept `node_id: str`. Normalize status. Find "ready" pending nodes for reminder. Return `{"todos": [...], "reminder": str}`. | `daemon/services/todo_manager.py` |
| 6 | Implement `update_by_index()` shim | Resolve `index` → Nth node in insertion order. Delegate to `update()`. | `daemon/services/todo_manager.py` |
| 7 | Implement `set_comment()` by node_id | Same as before but keyed by `node_id` instead of `index`. Preserve `MAX_COMMENT_LENGTH` guard. | `daemon/services/todo_manager.py` |
| 8 | Implement `set_comment_by_index()` shim | Resolve `index` → node_id. Delegate to `set_comment()`. | `daemon/services/todo_manager.py` |
| 9 | Implement `get_all()` | Return list of node dicts (ordered by insertion). Each dict includes `id`, `index`, `text`, `status`, `comment`, `next_ids` (6 keys — frozen schema). | `daemon/services/todo_manager.py` |
| 10 | Implement `get_graph()` | Return `{"nodes": [...], "edges": [...]}` structured form. | `daemon/services/todo_manager.py` |
| 11 | Implement `clear()` | `pop(instance_id, None)` — identical to current. | `daemon/services/todo_manager.py` |
| 12 | Implement `add_node()` | Add single node to existing graph. `next_ids: list[str] \| None = None` (W2 — resolve to `next_ids or []`). Validate `next_ids` refs. Enforce `MAX_NODES` guard. Return node dict. | `daemon/services/todo_manager.py` |
| 13 | Implement `add_edge()` | Add directed edge `from_id → to_id`. Validate both nodes exist. Run cycle check. Return updated graph or `None` on failure. | `daemon/services/todo_manager.py` |
| 14 | Implement `remove_edge()` | Remove edge. Clean up `next_ids` on source node. Return updated graph or `None`. | `daemon/services/todo_manager.py` |
| 15 | Implement `remove_node()` | Remove node + all edges referencing it (both as source and target). Return removed node dict or `None`. | `daemon/services/todo_manager.py` |
| 16 | Implement `_has_cycle()` | Kahn's algorithm with `collections.deque.popleft()` (C1 fix: append `next_id` not `nid`). O(V+E). Called by `create_graph` and `add_edge`. | `daemon/services/todo_manager.py` |
| 17 | Implement `_compute_reminder()` | Graph-aware reminder: find "ready" pending nodes (all predecessors done). Handle branching (multiple ready nodes). **PRESERVE comment-fence prompt-injection protection**: when updated node is "done" with non-empty comment, prefix reminder with `"User commented:\\n---\\n{comment}\\n---\\n"` + base reminder. | `daemon/services/todo_manager.py` |
| 18 | Implement `_generate_id()` | `"n-" + uuid.uuid4().hex[:8]` — prefixed to prevent numeric collision (C3 fix). | `daemon/services/todo_manager.py` |
| 19 | Keep `TodoManager` as alias | `TodoManager = TodoGraphManager` for backward compat with import sites. | `daemon/services/todo_manager.py` |
| 20 | Update `instance_lifecycle.py` cleanup | `clear()` call at line 836 stays the same (method signature unchanged). No change needed — just verify. | `daemon/services/instance_lifecycle.py` |
| 21 | Freeze SSE payload schema | Define and document the `_to_dict()` output shape as the frozen contract. Add a comment block in the code marking it as frozen. (W10 fix — this decouples Phase 2 from Phase 3.) | `daemon/services/todo_manager.py` |

## Key Files

- `daemon/services/todo_manager.py` — **PRIMARY** — all new code goes here. Replace `TodoItem` with `TodoNode`, replace `TodoManager` with `TodoGraphManager`, add alias `TodoManager = TodoGraphManager`.
- `daemon/manager.py:716` — verify `self._todo_manager = TodoManager()` still works (it will, via alias)
- `daemon/services/instance_lifecycle.py:836` — verify `clear()` call still works (it will, signature unchanged)

## Constraints

- **No DB persistence** — in-memory only, same as current
- **Thread safety** — `threading.Lock`, not `asyncio.Lock`
- **Status normalization** — preserve all 16 aliases unchanged
- **Comment max length** — 1000 chars, enforced at service layer
- **Max nodes** — 200 per instance (safety valve, enforced on `create()` AND `create_graph()` AND `add_node()`)
- **Node ID format** — `"n-" + uuid.uuid4().hex[:8]` (prefixed to prevent numeric collision with API backward-compat path)
- **Backward compatibility** — `create(instance_id, list[str])` must still work
- **Backward compatibility** — `_to_dict()` output includes `index` field (augmented, not replaced)
- **DAG validity** — no cycles allowed; all edge refs must resolve
- **Edge format** — `list[dict]` with `{"from": str, "to": str}` keys everywhere (C2 fix — no `list[tuple]`)
- **SSE schema frozen** — `_to_dict()` output shape is the contract for Phases 2, 3, 4 (W10 fix)
- **Python 3.12+** — use `str | None` syntax, `field(default_factory=list)` for dataclass fields, `list[str] | None = None` for method params (W2 fix)

## Deliverables

- [ ] `TodoNode` dataclass defined with `id`, `text`, `status`, `comment`, `next_ids`, `index`
- [ ] `TodoGraphManager` class with all 14 methods implemented
- [ ] `_has_cycle()` using Kahn's algorithm with `deque.popleft()` and correct child enqueueing
- [ ] `_compute_reminder()` handling branching (multiple ready nodes) AND comment-fence prompt-injection protection (fenced comment prefix when done + non-empty comment)
- [ ] `_generate_id()` returns `n-`-prefixed IDs (never all-numeric)
- [ ] `create()` auto-converts flat list to linear chain, enforces `MAX_NODES`
- [ ] `create_graph()` accepts `edges: list[dict]` (not `list[tuple]`), validates DAG structure
- [ ] `_to_dict()` includes `index` field (backward compat — augment, not replace)
- [ ] SSE payload schema frozen and documented (6 keys: `id`, `index`, `text`, `status`, `comment`, `next_ids`)
- [ ] `add_node()`, `add_edge()`, `remove_edge()`, `remove_node()` graph primitives
- [ ] `MAX_NODES` guard on `create()`, `create_graph()`, AND `add_node()`
- [ ] `TodoManager = TodoGraphManager` alias for backward compat
- [ ] All existing `TodoManager` import sites still work
- [ ] Thread safety verified (lock on every mutation + snapshot read)
