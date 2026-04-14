# Phase 2: EventBus Rewrite

---

## Goals

1. Add `broadcast_checkpoint_event()` method
2. Remove old streaming event methods and constants
3. Simplify `broadcast_sync()` to `completed`-only routing

---

## 1. Add `broadcast_checkpoint_event()` Method

```python
async def broadcast_checkpoint_event(
    self,
    instance_id: str,
    messages: list,  # list[BaseMessage] from LangGraph state
    checkpoint_id: str,
    tool_outputs: dict | None = None,  # tool_call_id -> output content
) -> None:
    """Broadcast a checkpoint event containing full message state.
    
    This replaces all individual streaming events (content_chunk, thinking,
    tool_call, tool_complete) and lifecycle events (message_received,
    message_completed, processing_started, etc.).
    
    Args:
        instance_id: The instance this checkpoint belongs to.
        messages: Full list of messages from LangGraph channel_values.
        checkpoint_id: Checkpoint ID from LangGraph state.
        tool_outputs: Map of tool_call_id -> output content for embedding
                      in tool_calls[].output.
    """
    from daemon.utils import serialize_message  # lazy import
    
    # Build tool_outputs from ToolMessages if not provided
    if tool_outputs is None:
        tool_outputs = {}
        for msg in messages:
            if hasattr(msg, 'tool_call_id'):
                tool_outputs[msg.tool_call_id] = msg.content
    
    serialized = [serialize_message(msg, tool_outputs) for msg in messages]
    
    # Skip empty checkpoints — LangGraph nodes may complete without new messages
    # (conditional edges, routing nodes). Emitting an empty messages[] would wipe
    # the frontend's message list.
    if not serialized:
        return
    
    event = {
        "instance_id": instance_id,
        "event_type": "checkpoint",
        "event_id": checkpoint_id,
        "messages": serialized,
        "checkpoint_id": checkpoint_id,
    }
    
    queue = self.get_streaming_queue(instance_id)
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning(f"Queue full for instance {instance_id}, dropping checkpoint")
    
    queue.put_nowait(event)
    self.notify(instance_id)
    await self._broadcast_to_global(instance_id, "checkpoint", data=event)
```

> **Note**: `_broadcast_to_global()` is called for checkpoint events. The `ResponseDispatcher` filters for `event_type=="completed"`, so checkpoint events are silently ignored. This is acceptable overhead (~1 queue put per checkpoint).

---

## 2. Code to DELETE from event_bus.py

| Lines | What | Why |
|-------|------|-----|
| 22 | `STREAMING_EVENT_TYPES` constant | No more streaming events |
| 25-28 | `LEGACY_EVENT_MAP` | No more legacy compat |
| 82-94 | `create_message_received_event()` | Replaced by checkpoint |
| 96-106 | `create_processing_started_event()` | Replaced by checkpoint |
| 108-120 | `create_processing_completed_event()` | Replaced by checkpoint |
| 122-134 | `create_processing_failed_event()` | Replaced by checkpoint |
| 136-146 | `create_child_completed_event()` | Replaced by checkpoint |
| 148-159 | `create_child_failed_event()` | Replaced by checkpoint |
| 161-169 | `create_instance_completed_event()` | DEAD CODE — zero callers confirmed |
| 171-181 | `create_error_event()` | Keep minimal version |
| 198-200 | Legacy event type mapping | No more legacy |
| 225-280 | `broadcast_streaming_event()` | Replaced by checkpoint |
| 442-463 | `_next_streaming_id()` | No more streaming IDs |
| 469-478 | `cleanup_old()` | Events no longer in DB |
| 551-552 | Legacy handling in `broadcast_sync()` | No more legacy |

---

## 3. What to KEEP from event_bus.py

| Lines | What | Why |
|-------|------|-----|
| 31-76 | `__init__()` | Still need queues, notifications |
| 183-219 | `create_event()` | Keep for error events only |
| 286-311 | `get_notification()`, `notify()` | SSE endpoint needs these |
| 317-357 | `get_streaming_queue()`, `get_streaming_events()` | Reuse for checkpoint queue |
| 363-436 | `subscribe_all()`, `unsubscribe_all()`, `_broadcast_to_global()` | Keep for global subscribers |
| 440 | `broadcast_sync()` | **Simplify to `completed`-only routing** |
| 480-516 | `cleanup_instance()`, `shutdown()` | Still needed |

---

## 4. Simplify `broadcast_sync()`

Rewrite `broadcast_sync()` to check `event.get("event_type") == "completed"`, forward to dispatcher only. Delete streaming queue logic:

```python
def broadcast_sync(self, event: dict) -> None:
    """Route completed events to dispatcher (for external sources like Telegram/Discord)."""
    if event.get("event_type") == "completed":
        self._dispatcher.handle_event(event)
```

---

## Verification

```bash
# Verify no more old event method references
grep -rn "broadcast_streaming_event\|create_processing_started_event\|create_child_completed_event" daemon/ --include="*.py"

# Verify broadcast_checkpoint_event is callable
grep -rn "broadcast_checkpoint_event" daemon/ --include="*.py"
```
