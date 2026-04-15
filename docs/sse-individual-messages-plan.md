# Plan: Change SSE from Bulk to Individual Message Events

## Overview

Transition SSE from emitting **batch checkpoint events** (all messages at once) to **individual message events** (one message at a time). Frontend will append messages to the list with deduplication by `message_id`.

## Current Architecture

```
Backend: broadcast_checkpoint_event(messages=[ALL_MESSAGES])
    ↓
SSE: Event with full array
    ↓
Frontend: messages.set(mappedMessages) — replaces entire list
```

## Target Architecture

```
Backend: broadcast_message_event(message=SINGLE_MESSAGE)
    ↓
SSE: Event with one message
    ↓
Frontend: messages.update(list => [...list, msg]) — append with dedup
```

---

## Phase 1: Backend Changes

### 1.1 EventBus — Add `broadcast_message_event()`

**File:** `daemon/services/event_bus.py`

```python
async def broadcast_message_event(
    self,
    instance_id: str,
    message: dict,
    event_type: str = "message",  # "user_message" | "assistant_message" | "thinking" | "tool_call"
    checkpoint_id: str | None = None,
) -> None:
    """Broadcast a single message event.

    Args:
        instance_id: The instance this message belongs to.
        message: Pre-serialized message dict (MUST include tool_outputs baked in).
        event_type: Type of message event for frontend routing.
        checkpoint_id: Optional checkpoint ID for ordering.
    """
    event: dict[str, Any] = {
        "instance_id": instance_id,
        "event_type": event_type,
        "event_id": message.get("message_id", ""),
        "message": message,  # Single message dict
        "checkpoint_id": checkpoint_id,
    }

    queue = self.get_streaming_queue(instance_id)
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.error(f"Queue full for {instance_id}, dropping message")  # ERROR not WARNING

    self.notify(instance_id)
    
    # CRITICAL: Also broadcast to global subscribers (ResponseDispatcher, etc.)
    await self._broadcast_to_global(instance_id, event_type, data=event)
```

### 1.2 Manager — Emit Individual Messages

**File:** `daemon/manager.py`

**Helper function:**
```python
def _get_message_event_type(msg: dict) -> str:
    """Determine event type based on message content."""
    if msg.get("role") == "user":
        return "user_message"
    if msg.get("tool_calls"):
        return "tool_call"
    if msg.get("thinking") or msg.get("thinking_extracted"):
        return "thinking"
    return "assistant_message"
```

**⚠️ CRITICAL: Add diffing logic to track NEW messages only**

The current code re-serializes ALL messages every checkpoint. We need to track which messages are truly new vs updated:

```python
# Around line 1084-1086, add tracking set:
all_state_messages: list = []
event_index = 0
seen_message_ids: set[str] = set()  # NEW: Track seen IDs for diffing
```

**Change streaming loop (around lines 1099-1142):**

```python
if mode == "updates":
    any_new = False
    for node_name, node_data in data.items():
        node_messages = node_data.get("messages", [])
        if node_messages:
            any_new = True
            # Build index for replacement tracking
            msg_index = {m.id: i for i, m in enumerate(all_state_messages) if hasattr(m, 'id')}
            
            for m in node_messages:
                msg_id = getattr(m, 'id', None)
                
                if msg_id and msg_id in msg_index:
                    # Updated message — replace in place (don't re-emit)
                    all_state_messages[msg_index[msg_id]] = m
                else:
                    # NEW message — add and emit individually
                    all_state_messages.append(m)
                    if msg_id:
                        seen_message_ids.add(msg_id)
                    
                    # Skip ToolMessages — they get baked into tool_calls
                    if isinstance(m, ToolMessage):
                        continue
                    
                    # Serialize the NEW message only
                    msg_serialized = serialize_message(m, tool_outputs)
                    msg_serialized["instance_id"] = instance_id
                    
                    # Emit individually
                    event_type = _get_message_event_type(msg_serialized)
                    await self._event_bus.broadcast_message_event(
                        instance_id=instance_id,
                        message=msg_serialized,
                        event_type=event_type,
                        checkpoint_id=f"seq_{event_index}",
                    )

    if not any_new:
        continue

    event_index += 1
```

**Change final-state safety net (around lines 1160-1189):**

```python
# After streaming loop, emit any remaining NEW messages from final state
# (some messages may not have been emitted during streaming)
for msg in final_messages:
    if isinstance(msg, ToolMessage):
        continue  # Skip ToolMessages
    
    msg_id = getattr(msg, 'id', None)
    if msg_id and msg_id in seen_message_ids:
        continue  # Already emitted during streaming
    
    msg_serialized = serialize_message(msg, final_tool_outputs)
    msg_serialized["instance_id"] = instance_id
    
    event_type = _get_message_event_type(msg_serialized)
    await self._event_bus.broadcast_message_event(
        instance_id=instance_id,
        message=msg_serialized,
        event_type=event_type,
        checkpoint_id=f"{final_sequence_id}_final",
    )
```

### 1.3 SSE Endpoint — Dynamic Payload Based on Event Type

**File:** `daemon/api.py` (around lines 857-868)

**⚠️ CRITICAL: Branch on event_type since payload shape differs**

```python
# Drain checkpoint events from queue
events = await event_bus.get_streaming_events(instance_id)
for event in events:
    # Build payload based on event type
    # Individual message events have "message" (singular)
    # Checkpoint events have "messages" (plural)
    if event["event_type"] == "checkpoint":
        payload = {
            "instance_id": event["instance_id"],
            "messages": event["messages"],  # Array
            "checkpoint_id": event.get("checkpoint_id", ""),
        }
    else:
        payload = {
            "instance_id": event["instance_id"],
            "message": event["message"],  # Single message
            "checkpoint_id": event.get("checkpoint_id", ""),
        }
    
    yield {
        "event": event["event_type"],
        "id": event.get("event_id", ""),
        "data": json.dumps(payload),
    }
```

### 1.4 Keep Checkpoint for Initial/Reconnect Load

For backward compatibility and reconciliation on reconnect:

```python
# After streaming loop completes, emit final checkpoint snapshot
# This helps frontend reconcile state on reconnect
await self._event_bus.broadcast_checkpoint_event(
    instance_id=instance_id,
    messages=final_serialized,
    checkpoint_id=f"{final_sequence_id}_complete",
)
```

---

## Phase 2: Frontend Changes

### 2.1 Add New Event Types

**File:** `frontend/src/app/models/index.ts`

```typescript
// NEW: Individual message event types
export type MessageEventType = 
  | 'user_message' 
  | 'assistant_message' 
  | 'thinking' 
  | 'tool_call'
  | 'checkpoint'     // Keep for initial load / reconnect
  | 'connected'
  | 'error'
  | 'keepalive';

export interface SSEMessageEvent {
  type: MessageEventType;
  data: {
    instance_id: string;
    message?: Message;        // For individual events
    messages?: Message[];      // For checkpoint events
    checkpoint_id?: string;
  };
}
```

### 2.2 Update SSE Service — Append with Deduplication

**File:** `frontend/src/app/services/sse.service.ts`

```typescript
/**
 * Append or update a message in the list with deduplication by message_id.
 */
private upsertMessage(message: Message): void {
  this.messages.update(msgs => {
    const existsIndex = msgs.findIndex(m => m.message_id === message.message_id);
    if (existsIndex >= 0) {
      // Update existing (replace with latest version)
      const updated = [...msgs];
      updated[existsIndex] = message;
      return updated;
    }
    // Append new message (maintain insertion order)
    return [...msgs, message];
  });
}

/**
 * Map raw SSE message data to Message type.
 */
private mapToMessage(data: any): Message {
  return {
    message_id: data.message_id,
    role: data.role,
    content: data.content || '',
    thinking: data.thinking || null,
    thinking_extracted: data.thinking_extracted || null,
    tool_calls: data.tool_calls || null,
    created_at: data.created_at || new Date().toISOString(),
    instance_id: data.instance_id,
  };
}
```

**⚠️ CRITICAL: Use upsertMessage in checkpoint handler too (avoid flicker)**

```typescript
// Individual message events
eventSource.addEventListener('user_message', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      const message = this.mapToMessage(data.message);
      this.upsertMessage(message);
      this.events.update(evts => [...evts, { type: 'user_message', data }]);
    } catch (err) {
      console.error('[SSE] Failed to parse user_message:', err);
    }
  });
});

eventSource.addEventListener('assistant_message', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      const message = this.mapToMessage(data.message);
      this.upsertMessage(message);
      this.events.update(evts => [...evts, { type: 'assistant_message', data }]);
    } catch (err) {
      console.error('[SSE] Failed to parse assistant_message:', err);
    }
  });
});

eventSource.addEventListener('thinking', (e: MessageEvent) => {
  // Same pattern — upsertMessage handles updates to thinking content
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      const message = this.mapToMessage(data.message);
      this.upsertMessage(message);
    } catch (err) {
      console.error('[SSE] Failed to parse thinking:', err);
    }
  });
});

eventSource.addEventListener('tool_call', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      const message = this.mapToMessage(data.message);
      this.upsertMessage(message);
    } catch (err) {
      console.error('[SSE] Failed to parse tool_call:', err);
    }
  });
});

// ⚠️ CRITICAL: Checkpoint handler uses upsertMessage (not set) to avoid flicker
// This reconciles state without replacing the entire list
eventSource.addEventListener('checkpoint', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      if (data.messages && Array.isArray(data.messages)) {
        // Use upsertMessage per message to avoid flicker with concurrent individual events
        for (const msg of data.messages) {
          this.upsertMessage(this.mapToMessage(msg));
        }
      }
    } catch (err) {
      console.error('[SSE] Failed to parse checkpoint:', err);
    }
  });
});
```

### 2.3 Chat Component — Simplified Effect

**File:** `frontend/src/app/pages/chat/chat.component.ts`

```typescript
// SIMPLIFY: No longer need to convert — SSE already returns Message
effect(() => {
  const sseMessages = this.sseService.messages();
  if (sseMessages.length === 0) return;
  
  // Messages already deduplicated and typed from SSE service
  this.messages.set(sseMessages);
  this.isSending.set(false);
});
```

### 2.4 Temp Message Reconciliation

**⚠️ IMPORTANT: Handle optimistic user messages**

Frontend creates temp messages with `temp-${Date.now()}` IDs before sending. When backend echoes the real message, we get duplicates.

**Option A: Skip SSE user_message if content matches existing temp**

```typescript
eventSource.addEventListener('user_message', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      const message = this.mapToMessage(data.message);
      
      // Check if we have a temp message with same content — replace it
      const tempIndex = this.messages().findIndex(m => 
        m.message_id.startsWith('temp-') && m.content === message.content
      );
      
      if (tempIndex >= 0) {
        // Replace temp message with real one
        this.messages.update(msgs => {
          const updated = [...msgs];
          updated[tempIndex] = message;
          return updated;
        });
      } else {
        this.upsertMessage(message);
      }
    } catch (err) {
      console.error('[SSE] Failed to parse user_message:', err);
    }
  });
});
```

**Option B: Clear temp messages before SSE connects**

In `loadMessages()` after getting history from REST API, the temp message will be replaced when checkpoint arrives.

---

## Phase 3: Migration & Testing

### 3.1 Testing Strategy

1. **Unit tests for EventBus:** Verify individual message events are queued correctly + global broadcast
2. **Integration tests:** Verify streaming produces individual events with diffing
3. **Frontend tests:** Verify message append/dedup logic + temp reconciliation
4. **E2E test:** Send message → verify SSE delivers individual events → verify UI updates

### 3.2 Backward Compatibility

- Keep `checkpoint` event for reconnect/reconciliation
- New `message` events are additive
- No breaking changes to existing REST API (`GET /instances/{id}/messages`)

---

## Summary of Changes

| Component | File | Change |
|-----------|------|--------|
| EventBus | `daemon/services/event_bus.py` | Add `broadcast_message_event()` + global broadcast |
| Manager | `daemon/manager.py` | Add diffing logic, emit per-message, skip ToolMessages |
| API | `daemon/api.py` | Dynamic payload based on `event_type` |
| Models | `frontend/src/app/models/index.ts` | Add `MessageEventType` |
| SSE Service | `frontend/src/app/services/sse.service.ts` | Add `upsertMessage()`, use upsert in checkpoint, temp reconciliation |
| Chat | `frontend/src/app/pages/chat/chat.component.ts` | Simplified effect |

---

## Verification Checklist

### Backend
- [ ] `broadcast_message_event()` queues individual messages
- [ ] `_broadcast_to_global()` is called for global subscribers
- [ ] Streaming loop diffs messages (only NEW ones emitted)
- [ ] Updated messages are NOT re-emitted
- [ ] `ToolMessage` objects are skipped
- [ ] SSE endpoint branches on `event_type` for payload shape
- [ ] Final checkpoint still emitted for reconciliation

### Frontend
- [ ] `upsertMessage()` correctly appends/updates by `message_id`
- [ ] Checkpoint handler uses `upsertMessage()` (not `set()`)
- [ ] Temp message reconciliation works
- [ ] Individual events don't flicker UI
- [ ] All event type handlers work

### Integration
- [ ] Send message → SSE delivers individual events → UI updates
- [ ] Reconnect → checkpoint reconciles state
- [ ] Tool calls + results appear correctly
- [ ] Thinking updates appear correctly

---

## Council Review Notes (v2)

Revisions from council review:

| Priority | Issue | Fix Applied |
|----------|-------|-------------|
| P0 | `new_messages` doesn't exist | Added `seen_message_ids` set + diffing logic |
| P0 | SSE endpoint hardcodes `event["messages"]` | Added branching on `event_type` |
| P1 | Missing `_broadcast_to_global()` | Added to `broadcast_message_event()` |
| P1 | Temp message reconciliation | Added Option A in SSE service |
| P1 | ToolMessage filtering | Explicitly skipped in emission loop |
| P2 | Checkpoint `.set()` vs `.upsert()` flicker | Using upsert in checkpoint handler |
| P2 | tool_outputs in serialized message | Documented as requirement |
