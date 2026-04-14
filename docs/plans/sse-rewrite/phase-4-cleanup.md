# Phase 4: Cleanup — Delete MessageService & Rewrite API

---

## Goals

1. Delete `daemon/services/message_service.py` (after verifying zero callers)
2. Rewrite `stream_events()` in `daemon/api.py`
3. Clean up `daemon/message_models.py`

---

## 1. Delete `daemon/services/message_service.py`

**Before deleting**, verify no remaining references:

```bash
grep -rn "MessageService\|on_assistant_message_completed\|on_child_completion_report\|on_child_error_report\|on_user_message_stored" daemon/ --include="*.py"
```

If output is empty, delete the file.

---

## 2. Rewrite SSE Endpoint — `daemon/api.py`

**Location**: `stream_events()` at lines **822–951**; `format_sse_event()` at lines **954–1024**

### 2.1 Rewrite `stream_events()`

```python
@api_router.get("/instances/{instance_id}/events")
async def stream_events(instance_id: str, request: Request):
    """SSE stream delivering checkpoint events."""
    if manager.is_shutting_down:
        raise HTTPException(status_code=503, detail="Server is shutting down")
    
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Instance not found: {instance_id}")
    
    event_bus: EventBus = request.app.state.event_bus
    
    async def event_generator() -> AsyncGenerator[dict, None]:
        # 1. Connected event
        yield {
            "event": "connected",
            "data": json.dumps({"instance_id": instance_id}),
        }
        
        # 2. Send initial checkpoint (current state)
        instance = manager.get_instance(instance_id)
        checkpointer = await persistence.get_checkpointer(manager._db_path)
        current_messages = await persistence.get_instance_messages(checkpointer, instance_id)
        if current_messages:
            yield {
                "event": "checkpoint",
                "data": json.dumps({
                    "instance_id": instance_id,
                    "messages": current_messages,
                    "checkpoint_id": "initial",
                }),
            }
        
        # 3. Listen for new checkpoints
        notification = event_bus.get_notification(instance_id)
        
        while True:
            if await request.is_disconnected():
                break
            
            if manager.is_shutting_down:
                yield {"event": "error", "data": json.dumps({"error": "server_shutdown"})}
                break
            
            try:
                await asyncio.wait_for(notification.wait(), timeout=30)
                notification.clear()
            except asyncio.TimeoutError:
                yield {"event": "keepalive", "data": "{}"}
                continue
            
            # Drain checkpoint events from queue
            events = await event_bus.get_streaming_events(instance_id)
            for event in events:
                yield {
                    "event": event["event_type"],
                    "id": event.get("event_id", ""),
                    "data": json.dumps({
                        "instance_id": event["instance_id"],
                        "messages": event["messages"],
                        "checkpoint_id": event.get("checkpoint_id", ""),
                    }),
                }
    
    return EventSourceResponse(event_generator(), ping=30)
```

### 2.2 Delete `format_sse_event()`

No longer needed. Events are formatted inline.

### 2.3 Remove merge logic

Delete the current code that merges DB events and streaming events (lines 922-936).

---

## 3. Clean Up `daemon/message_models.py`

### Delete:

| Lines | What | Why |
|-------|------|-----|
| 27-34 | `SSEEventPayload` | No more SSE event payloads |
| 37-42 | `SSEEventDelta` | No more delta types |
| 45-51 | `SSEEventStatus` | No more status events |
| 54-89 | `UnifiedMessage` | No more unified message format |

### Keep:

- `MessageRole` enum
- `ToolCallInfo`

---

## Verification

```bash
# Verify message_service.py is deleted
ls daemon/services/message_service.py 2>/dev/null && echo "STILL EXISTS" || echo "DELETED"

# Verify no format_sse_event references
grep -rn "format_sse_event" daemon/ --include="*.py"

# Verify SSE endpoint works (manual test or integration test)
```
