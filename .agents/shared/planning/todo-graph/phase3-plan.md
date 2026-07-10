# Phase 3: API Endpoints + SSE Payload

## Objective

Update the 2 existing API endpoints to work with node IDs (while keeping backward-compatible index-based paths), add 2 new endpoints for edge management, and update the SSE `todo_update` event payload documentation to reflect the augmented graph structure (frozen in Phase 1). The SSE payload includes `id`, `index`, `text`, `status`, `comment`, and `next_ids` per node — `index` is preserved for backward compatibility.

## Coupling

- **Depends on**: Phase 1 (TodoGraphManager methods + frozen SSE payload schema)
- **Coupling type**: **loose** — API endpoints call manager methods but only need the interface contract, not the implementation
- **Shared files with other phases**: `daemon/routers/instances.py` (Phase 4 frontend calls these endpoints); `daemon/services/live_event_hub.py` (SSE payload shape consumed by frontend — frozen in Phase 1)
- **Shared APIs/interfaces**: HTTP endpoint paths + request/response JSON shapes; SSE event payload schema (frozen in Phase 1)
- **Why this coupling**: The API and SSE payload are the wire protocol the frontend consumes. Phase 4 can start once the payload schema is frozen in Phase 1. **No coupling to Phase 2** — the SSE schema is frozen in Phase 1, so Phase 3 doesn't depend on Phase 2's tool changes (W10 fix).

## Context

- Current endpoints in `daemon/routers/instances.py` (lines 375-499):
  - `GET /api/instances/{instance_id}/todos` — returns `list[dict]` (flat array)
  - `POST /api/instances/{instance_id}/todos/{index}/comment` — sets comment by index
- `TodoCommentRequest(BaseModel)` at line 31 — `{"comment": str}`
- `MAX_COMMENT_LENGTH = 1000` duplicated in router (line 28) and manager (line 26)
- `_check_instance_exists()` helper at line 379
- SSE emission: `live_hub.stream_todo_update(instance_id, todos)` — currently passes `list[dict]`
- `LiveEventHub.stream_todo_update()` at `live_event_hub.py:336-356` — creates event dict with `event_type: "todo_update"` and `todos` key
- Frontend SSE handler at `sse.service.ts:315-324` — parses `data.todos` and sets signal

## Design: Endpoint Changes

### 1. GET /api/instances/{instance_id}/todos (Enhanced — Augmented, Not Replaced)

**No URL change.** The endpoint returns a richer payload.

> **C4 fix**: The response is augmented, not replaced. The `index` field is PRESERVED alongside new `id` and `next_ids` fields. Old frontend code that reads `item.index` continues to work. Angular `track item.index` does not trigger DOM teardown.

**Response shape change (backward compatible — fields added, not removed):**
```json
// Before (flat list):
[
  {"index": 0, "text": "A", "status": "pending", "comment": ""},
  {"index": 1, "text": "B", "status": "pending", "comment": ""}
]

// After (graph — backward compatible, index PRESERVED, id + next_ids ADDED):
[
  {"id": "n-a1b2c3d4", "index": 0, "text": "A", "status": "pending", "comment": "", "next_ids": ["n-e5f6g7h8"]},
  {"id": "n-e5f6g7h8", "index": 1, "text": "B", "status": "pending", "comment": "", "next_ids": []}
]
```

**Key decision**: Keep returning a `list[dict]` (not `{nodes, edges}`) for this endpoint. The `next_ids` field in each node dict is sufficient to reconstruct edges. This is backward compatible — old clients ignore the new `id` and `next_ids` fields, and the `index` field they depend on is preserved.

**Alternative endpoint** (optional, for explicit graph structure):
```
GET /api/instances/{instance_id}/todos/graph
```
Returns:
```json
{
  "nodes": [
    {"id": "n-a1b2c3d4", "index": 0, "text": "A", "status": "pending", "comment": "", "next_ids": ["n-e5f6g7h8"]}
  ],
  "edges": [
    {"from": "n-a1b2c3d4", "to": "n-e5f6g7h8"}
  ]
}
```

### 2. POST /api/instances/{instance_id}/todos/{node_id}/comment (Updated)

**URL change**: `{index}` → `{node_id}` in the path. But keep backward compatibility:

```
# New (preferred):
POST /api/instances/{instance_id}/todos/{node_id}/comment

# Backward compatible (legacy):
POST /api/instances/{instance_id}/todos/{index}/comment
```

> **C3 fix**: Since all generated node IDs are prefixed with `n-` (e.g., `n-a1b2c3d4`), they are never all-numeric. The `node_id.isdigit()` check cleanly distinguishes between legacy numeric indices and new string node IDs. There is no collision risk.

**Implementation approach**: Use a single endpoint that accepts `node_id: str` in the path. If the value is numeric (all digits), treat it as an index and resolve via `set_comment_by_index()`. Otherwise, treat as a node ID and use `set_comment()`.

```python
@router.post("/{instance_id}/todos/{node_id}/comment")
async def set_todo_comment(
    instance_id: str,
    node_id: str,          # Was: index: int
    body: TodoCommentRequest,
    request: Request,
) -> dict:
    """Set a comment on a todo node.

    node_id can be:
    - A node ID string (e.g., "n-a1b2c3d4") — never all-numeric due to 'n-' prefix
    - A numeric index (e.g., "0") for backward compatibility

    Numeric values are resolved to the Nth node by insertion order.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    if len(body.comment) > MAX_COMMENT_LENGTH:
        raise HTTPException(status_code=400, ...)

    try:
        if node_id.isdigit():
            # Backward compat: treat as index
            updated = manager._todo_manager.set_comment_by_index(
                instance_id, int(node_id), body.comment
            )
        else:
            updated = manager._todo_manager.set_comment(
                instance_id, node_id, body.comment
            )
    except ValueError as e:
        raise HTTPException(status_code=404, ...)

    # SSE re-emit (unchanged pattern — frozen payload schema from Phase 1)
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:
            logger.warning(f"todo SSE emission failed: {e}")

    return updated
```

### 3. POST /api/instances/{instance_id}/todos/edges (New)

Add a directed edge between two nodes.

```python
class TodoEdgeRequest(BaseModel):
    """Request body for adding/removing a todo graph edge."""
    from_id: str = Field(description="ID of the predecessor node")
    to_id: str = Field(description="ID of the successor node")

@router.post("/{instance_id}/todos/edges")
async def add_todo_edge(
    instance_id: str,
    body: TodoEdgeRequest,
    request: Request,
) -> dict:
    """Add a directed edge between two todo nodes.

    Returns the updated graph structure. Returns 404 if either node
    doesn't exist. Returns 400 if the edge would create a cycle.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    result = manager._todo_manager.add_edge(instance_id, body.from_id, body.to_id)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Cannot add edge {body.from_id} → {body.to_id}: "
                        "nodes don't exist or edge would create a cycle",
            ).model_dump(),
        )

    # SSE re-emit
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:
            logger.warning(f"todo SSE emission failed: {e}")

    return result
```

### 4. DELETE /api/instances/{instance_id}/todos/edges (New)

Remove a directed edge.

```python
@router.delete("/{instance_id}/todos/edges")
async def remove_todo_edge(
    instance_id: str,
    body: TodoEdgeRequest,
    request: Request,
) -> dict:
    """Remove a directed edge between two todo nodes.

    Returns the updated graph structure. Returns 404 if the edge
    doesn't exist.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    result = manager._todo_manager.remove_edge(instance_id, body.from_id, body.to_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TODO_NOT_FOUND,
                message=f"Edge {body.from_id} → {body.to_id} not found",
            ).model_dump(),
        )

    # SSE re-emit
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:
            logger.warning(f"todo SSE emission failed: {e}")

    return result
```

## SSE Payload Changes

### LiveEventHub.stream_todo_update (Updated docstring, same code)

The method signature stays the same — it accepts `todos: list[dict]`. The dicts now contain `id`, `index`, and `next_ids` fields (frozen schema from Phase 1), but the method is agnostic to dict contents (it just serializes and pushes to queues).

**Updated docstring** to document the frozen schema:

```python
async def stream_todo_update(
    self,
    instance_id: str,
    todos: list[dict],
) -> None:
    """Stream todo update event to all active connections.

    Emitted whenever the todo graph changes so the frontend can
    re-render the graph without a full reload.

    Args:
        instance_id: The instance this todo update belongs to.
        todos: List of todo node dicts (frozen schema from Phase 1).
            Each dict has 6 keys:
            - id (str): Node identifier (prefixed "n-")
            - index (int): Insertion-order position (backward compat)
            - text (str): Description
            - status (str): "pending" | "in_progress" | "done"
            - comment (str): User annotation
            - next_ids (list[str]): Successor node IDs (adjacency list)
    """
    event: dict[str, Any] = {
        "instance_id": instance_id,
        "event_type": "todo_update",
        "todos": todos,
    }
    await self._stream_to_connections(instance_id, event)
```

**No code change** — just updated documentation. The data flows through transparently.

### Frontend SSE Event Shape (Frozen — matches Phase 1)

```json
{
  "instance_id": "inst-123",
  "event_type": "todo_update",
  "todos": [
    {
      "id": "n-a1b2c3d4",
      "index": 0,
      "text": "Setup database",
      "status": "done",
      "comment": "",
      "next_ids": ["n-e5f6g7h8"]
    },
    {
      "id": "n-e5f6g7h8",
      "index": 1,
      "text": "Build API",
      "status": "in_progress",
      "comment": "Use FastAPI",
      "next_ids": ["n-f9g0h1i2", "n-j3k4l5m6"]
    },
    {
      "id": "n-f9g0h1i2",
      "index": 2,
      "text": "Write tests",
      "status": "pending",
      "comment": "",
      "next_ids": []
    },
    {
      "id": "n-j3k4l5m6",
      "index": 3,
      "text": "Write docs",
      "status": "pending",
      "comment": "",
      "next_ids": []
    }
  ]
}
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `GET /{instance_id}/todos` response | No code change — `get_all()` now returns dicts with `id`, `index`, `next_ids` (frozen Phase 1 schema). Update docstring to document all 6 fields. | `daemon/routers/instances.py` |
| 2 | Add `GET /{instance_id}/todos/graph` endpoint (optional) | Returns `{"nodes": [...], "edges": [...]}` via `get_graph()`. Add as new route. | `daemon/routers/instances.py` |
| 3 | Update `POST /{instance_id}/todos/{node_id}/comment` | Change path param from `index: int` to `node_id: str`. Add numeric-index backward-compat detection (`node_id.isdigit()`). Route to `set_comment` or `set_comment_by_index`. | `daemon/routers/instances.py` |
| 4 | Add `TodoEdgeRequest` Pydantic model | `{"from_id": str, "to_id": str}` — request body for edge endpoints. | `daemon/routers/instances.py` |
| 5 | Implement `POST /{instance_id}/todos/edges` | Add edge endpoint. Calls `add_edge()`. 400 on cycle/error. SSE re-emit. | `daemon/routers/instances.py` |
| 6 | Implement `DELETE /{instance_id}/todos/edges` | Remove edge endpoint. Calls `remove_edge()`. 404 if edge missing. SSE re-emit. | `daemon/routers/instances.py` |
| 7 | Update `stream_todo_update()` docstring | Document frozen schema: 6 keys (`id`, `index`, `text`, `status`, `comment`, `next_ids`). No code change. | `daemon/services/live_event_hub.py` |
| 8 | Update `MAX_COMMENT_LENGTH` handling | Verify the duplicate constant in router (line 28) still aligns with manager. Consider importing from manager to avoid duplication. | `daemon/routers/instances.py` |
| 9 | Update error codes | Verify `ErrorCodes.TODO_NOT_FOUND` exists for node-not-found. Add `ErrorCodes.TODO_EDGE_INVALID` or reuse `INVALID_REQUEST` for cycle errors. | `daemon/routers/instances.py`, `daemon/models.py` |
| 10 | Verify `_check_instance_exists` still works | No change needed — it validates instance, not todo state. | `daemon/routers/instances.py` |

## Key Files

- `daemon/routers/instances.py` — **PRIMARY** — endpoint changes, new Pydantic model, new routes
- `daemon/services/live_event_hub.py` — docstring update only (no code change)
- `daemon/models.py` — verify/add error codes if needed
- `daemon/services/todo_manager.py` — Phase 1 provides the manager methods

## Constraints

- **Backward compatibility**: `GET /todos` still returns `list[dict]` (augmented with `id`, `next_ids`; `index` PRESERVED — C4 fix)
- **Backward compatibility**: `POST /todos/{index}/comment` still works (numeric path param auto-detected)
- **SSE event type**: Stays `"todo_update"` — no new event type needed
- **SSE payload key**: Stays `"todos"` — the list of node dicts (not renamed to `"nodes"`)
- **SSE payload schema**: Frozen in Phase 1 — 6 keys per dict (`id`, `index`, `text`, `status`, `comment`, `next_ids`)
- **Error shapes**: Use `ErrorResponse` with `ErrorCodes` — same pattern as current
- **HTTP status codes**: 404 for missing node/edge, 400 for cycle/invalid edge, 200 for success
- **Comment length**: 1000 chars max, enforced at HTTP boundary (400) + service layer (ValueError)
- **No coupling to Phase 2**: SSE schema frozen in Phase 1; API endpoints don't depend on tool changes (W10 fix)

## Deliverables

- [ ] `GET /todos` returns node dicts with `id`, `index`, `next_ids` fields (6 keys — C4: index preserved)
- [ ] `GET /todos/graph` endpoint returns structured `{nodes, edges}` (optional)
- [ ] `POST /todos/{node_id}/comment` accepts string node ID (and numeric index)
- [ ] `POST /todos/edges` adds directed edge with cycle validation
- [ ] `DELETE /todos/edges` removes directed edge
- [ ] `TodoEdgeRequest` Pydantic model defined
- [ ] SSE `todo_update` payload matches frozen Phase 1 schema (6 keys per node)
- [ ] `stream_todo_update()` docstring updated
- [ ] All endpoints emit SSE on successful mutation
- [ ] Error responses use `ErrorResponse` + `ErrorCodes` pattern
