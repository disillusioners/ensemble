# Phase 2: SSE Integration

## Objective
Add a `stream_todo_update()` method to LiveEventHub and wire SSE emission into the todo tools so that every todo list change (create/update/clear) broadcasts a `todo_update` SSE event with the full todo list to all connected frontend clients for that instance.

## Coupling
- **Depends on**: Phase 1 (TodoManager + tools)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/tools/todo_tools.py` (modified to add SSE emit calls)
- **Shared APIs/interfaces**: `LiveEventHub._stream_to_connections()` (existing), new `stream_todo_update()` method
- **Why this coupling**: The todo tools created in Phase 1 must call `stream_todo_update()` after every state mutation. This requires modifying the same tool functions Phase 1 created. Same codepath — must be sequential.

## Context
- Per-instance SSE endpoint at `GET /instances/{instance_id}/events` (`daemon/routers/messages.py:281`)
- **Key insight**: The SSE event name is taken directly from `event["event_type"]` at `messages.py:342`. No router changes needed — a new `event_type: "todo_update"` is automatically streamed.
- LiveEventHub is accessible from tools via `manager._live_hub`
- Existing pattern: `stream_message()`, `stream_tool_result()`, `stream_status_change()` — all follow the same shape: build event dict, call `_stream_to_connections()`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `stream_todo_update()` to LiveEventHub | New async method following existing pattern. Event payload: `{instance_id, event_type: "todo_update", todos: [{index, text, status}, ...]}`. Call `_stream_to_connections()`. | `daemon/services/live_event_hub.py` |
| 2 | Wire SSE emission into todo tools | After each mutation in `todo_tools.py` (create/update/clear), call `await manager._live_hub.stream_todo_update(current_instance_id, todos)`. Wrap in `try/except` so SSE failure doesn't break the tool. | `daemon/tools/todo_tools.py` (MODIFY) |
| 3 | Add SSE integration tests | Test that todo operations emit SSE events. Mock LiveEventHub, verify `stream_todo_update` called with correct payload on each mutation. | `tests/test_todo_sse.py` (NEW) |

## Key Files
- `daemon/services/live_event_hub.py` — New `stream_todo_update()` method
- `daemon/tools/todo_tools.py` — SSE emit calls in all mutation tools
- `tests/test_todo_sse.py` (NEW) — SSE integration tests

## Detailed Design

### `stream_todo_update()` Method

```python
async def stream_todo_update(
    self,
    instance_id: str,
    todos: list[dict[str, Any]],
) -> None:
    """Stream a todo_update event with the full todo list (replacement semantics).

    Args:
        instance_id: The instance the todo list belongs to.
        todos: Full list of todo items [{index, text, status}, ...].
    """
    event: dict[str, Any] = {
        "instance_id": instance_id,
        "event_type": "todo_update",
        "todos": todos,
    }
    await self._stream_to_connections(instance_id, event)
```

### SSE Emit in Tools (pattern)

```python
# In todo_tools.py, after each mutation:
try:
    todos = await manager._todo_manager.get_list(current_instance_id)
    await manager._live_hub.stream_todo_update(current_instance_id, todos)
except Exception:
    pass  # SSE failure should not block todo operation
```

### SSE Event Payload (received by frontend)

```json
{
  "instance_id": "abc123",
  "event_type": "todo_update",
  "todos": [
    {"index": 0, "text": "Set up project structure", "status": "done"},
    {"index": 1, "text": "Implement auth module", "status": "in_progress"},
    {"index": 2, "text": "Write tests", "status": "pending"}
  ]
}
```

### Why No Router Changes Needed

The per-instance SSE endpoint (`messages.py:281-353`) streams events generically:
```python
yield {
    "event": event["event_type"],  # ← "todo_update" becomes the SSE event name
    "data": json.dumps(event),
}
```
Any event put on the instance's queue with `event_type: "todo_update"` is automatically delivered to the frontend as an SSE event named `todo_update`. No notification router changes required.

## Constraints
- SSE emission is best-effort: wrap in `try/except`, never block tool execution
- `stream_todo_update` must be called AFTER the TodoManager mutation succeeds
- Event uses replacement semantics (full list every time, not diffs)
- Event must include `instance_id` for frontend routing

## Deliverables
- [ ] `stream_todo_update()` method in LiveEventHub
- [ ] SSE emission wired into all 3 mutation tools (create, update, clear)
- [ ] SSE integration tests passing
