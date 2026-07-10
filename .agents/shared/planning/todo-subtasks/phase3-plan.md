# Phase 3: API Endpoints

## Objective
Add 3 new REST API endpoints for sub-task CRUD operations, with Pydantic request models and SSE emission on all mutations. Update existing endpoint docstrings to reflect the 7-key schema.

## Coupling
- **Depends on**: Phase 1 (manager methods must exist)
- **Coupling type**: tight — endpoints call `manager._todo_manager.add_subtask()`, `update_subtask()`, `remove_subtask()`
- **Shared files with other phases**: `daemon/routers/instances.py` (shared with existing todo endpoints)
- **Shared APIs/interfaces**: Manager method signatures from Phase 1
- **Why this coupling**: API endpoints are thin HTTP wrappers around manager methods

## Context
- Existing todo endpoints in `daemon/routers/instances.py` (lines 396-703):
  - `GET /{instance_id}/todos` — returns array of node dicts
  - `GET /{instance_id}/todos/graph` — returns `{nodes, edges}`
  - `POST /{instance_id}/todos/edges` — add edge
  - `DELETE /{instance_id}/todos/edges` — remove edge
  - `POST /{instance_id}/todos/{node_id}/comment` — set comment
- All endpoints use `_check_instance_exists()` helper for 404 guard
- SSE emission pattern: call `live_hub.stream_todo_update(instance_id, manager._todo_manager.get_all(instance_id))`
- Pydantic models: `TodoCommentRequest`, `TodoEdgeRequest` already exist
- Route ordering matters: literal segments (`/edges`) must be declared before catch-all (`/{node_id}`)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `TodoSubtaskRequest` Pydantic model | `{"text": str}` — for creating a sub-task. Validate text is non-empty, max 500 chars. | `daemon/routers/instances.py` |
| 2 | Add `TodoSubtaskUpdateRequest` Pydantic model | `{"status": str, "auto_complete": bool = False}` — for updating a sub-task. | `daemon/routers/instances.py` |
| 3 | Implement `POST /{instance_id}/todos/{node_id}/subtasks` | Create a sub-task on the specified node. Body: `TodoSubtaskRequest`. Returns `{"todos": [...], "reminder": str}` (uniform shape per D12). Emits SSE. 404 if instance/node not found. 400 if max sub-tasks exceeded. | `daemon/routers/instances.py` |
| 4 | Implement `PATCH /{instance_id}/todos/{node_id}/subtasks/{subtask_id}` | Update a sub-task's status. Body: `TodoSubtaskUpdateRequest`. Returns `{"todos": [...], "reminder": str, "auto_completed": bool}`. Emits SSE. 404 if instance/node/sub-task not found. 400 if invalid status. | `daemon/routers/instances.py` |
| 5 | Implement `DELETE /{instance_id}/todos/{node_id}/subtasks/{subtask_id}` | Remove a sub-task. Returns `{"todos": [...], "reminder": str}` (uniform shape). Emits SSE. 404 if instance/node/sub-task not found. | `daemon/routers/instances.py` |
| 6 | Update `GET /{instance_id}/todos` docstring | Document the 7-key schema (add `subtasks` field description). | `daemon/routers/instances.py` |
| 7 | Update `GET /{instance_id}/todos/graph` docstring | Note that nodes now include `subtasks` in the 7-key schema. | `daemon/routers/instances.py` |
| 8 | Update `LiveEventHub.stream_todo_update()` docstring | Document the 7-key payload (add `subtasks` key description). This is one of the 6 frozen-contract docstring locations. | `daemon/services/live_event_hub.py` |
| 9 | Ensure route ordering | Declare `/subtasks` routes before the `/{node_id}/comment` catch-all. The `/subtasks` and `/subtasks/{subtask_id}` literal segments must not be captured by `{node_id}`. | `daemon/routers/instances.py` |
| 10 | Audit and update frozen-contract docstrings | Update the 2 API-layer docstring locations that assert "exactly six keys": `instances.py:428-436` (get_instance_todos) and `instances.py:458-463` (get_instance_todo_graph). Evolve to "frozen seven keys (subtasks added)." The other 4 locations are in `todo_manager.py` and `live_event_hub.py` (handled in Phase 1). | `daemon/routers/instances.py` |

## Key Files
- `daemon/routers/instances.py` — 703 lines currently, estimated +150-200 lines
- `daemon/services/live_event_hub.py` — docstring update only (~10 lines changed)

## Endpoint Specifications

### POST `/{instance_id}/todos/{node_id}/subtasks`

```
Request Body: {"text": "Create schema"}
Response 200: {"todos": [...], "reminder": "..."}
  (todos = full 7-key node array; reminder = graph-aware reminder string)
Response 404: Instance or node not found
Response 400: Max sub-tasks exceeded, empty text
```

### PATCH `/{instance_id}/todos/{node_id}/subtasks/{subtask_id}`

```
Request Body: {"status": "done", "auto_complete": true}
Response 200: {"todos": [...], "reminder": "...", "auto_completed": true}
  (auto_completed = true only when parent was auto-marked done)
Response 404: Instance, node, or sub-task not found
Response 400: Invalid status (e.g., "in_progress")
```

### DELETE `/{instance_id}/todos/{node_id}/subtasks/{subtask_id}`

```
Response 200: {"todos": [...], "reminder": "..."}
Response 404: Instance, node, or sub-task not found
```

## Route Ordering

Current route order (critical for FastAPI matching):
```
1. GET  /{instance_id}/todos                    ← literal
2. GET  /{instance_id}/todos/graph              ← literal
3. POST /{instance_id}/todos/edges              ← literal (before {node_id})
4. DELETE /{instance_id}/todos/edges            ← literal (before {node_id})
5. POST /{instance_id}/todos/{node_id}/comment  ← catch-all
```

New routes must be inserted **before** route 5 (the `{node_id}` catch-all):
```
1. GET    /{instance_id}/todos
2. GET    /{instance_id}/todos/graph
3. POST   /{instance_id}/todos/edges
4. DELETE /{instance_id}/todos/edges
5. POST   /{instance_id}/todos/{node_id}/subtasks          ← NEW (before comment catch-all)
6. PATCH  /{instance_id}/todos/{node_id}/subtasks/{subtask_id}  ← NEW
7. DELETE /{instance_id}/todos/{node_id}/subtasks/{subtask_id}  ← NEW
8. POST   /{instance_id}/todos/{node_id}/comment            ← catch-all (last)
```

**Note:** `POST /{instance_id}/todos/{node_id}/subtasks` has two path params (`{node_id}` and literal `subtasks`). FastAPI matches literal segments before path params, so `subtasks` won't be captured by `{node_id}` as long as the route is declared before the comment catch-all. The `{subtask_id}` segment in routes 6-7 is a path param that FastAPI handles natively.

## SSE Emission Pattern

All 3 new endpoints follow the existing pattern:
```python
live_hub = getattr(request.app.state, "live_hub", None)
if live_hub is not None:
    try:
        await live_hub.stream_todo_update(
            instance_id,
            manager._todo_manager.get_all(instance_id),
        )
    except Exception as e:
        logger.warning(f"todo SSE emission failed for subtask operation on {instance_id}: {e}")
```

## Constraints
- All endpoints must call `_check_instance_exists()` first (404 guard)
- SSE emission is best-effort (never blocks the write)
- `PATCH` method is preferred over `PUT` for partial update (status only)
- Pydantic models must validate input (non-empty text, valid status)
- Error responses must use `ErrorResponse` with `ErrorCodes` (matching existing pattern)
- `auto_complete` defaults to `False` in the Pydantic model
- **All 3 endpoints return `{"todos": [...], "reminder": str}`** — uniform shape per D12. PATCH additionally includes `"auto_completed": bool`. No bare node dict responses.

## Deliverables
- [ ] `TodoSubtaskRequest` and `TodoSubtaskUpdateRequest` Pydantic models
- [ ] 3 new endpoints implemented with correct route ordering
- [ ] SSE emission on all mutations
- [ ] Docstrings updated (7-key schema)
- [ ] `LiveEventHub.stream_todo_update()` docstring updated
- [ ] All existing API tests pass after updating 1 schema-key-set assertion (`test_todo_api.py:158-160`)
- [ ] New API tests (~12-15 tests)
