# SSE System Rewrite Plan

> **Note**: This project is **not in production**. No migration, no backward compatibility,
> no feature flags. All changes are applied directly. Old code is deleted, not deprecated.

---

## Overview

**Goal**: Rewrite SSE system so that messages delivered via SSE are identical to messages from the REST API, using LangGraph's native message IDs.

**Key Principles**:
1. LangGraph's `msg.id` is the source of truth — no more `compute_message_id()`
2. SSE delivers checkpoint snapshots after each node completes
3. Frontend replaces entire message list on each checkpoint event
4. Correctness over real-time feedback (project focuses on long-running tasks)

---

## Current State Problems

### 1. Duplicate `message_received` (Audit Gap)
- Written to DB inside `enqueue_message()` transaction (`session.add(event)` at line 968) with `kind=MESSAGE_RECEIVED`
- **Not broadcast via SSE** — no call to `EventBus.create_message_received_event()` in `enqueue_message()`
- `message_service.py:on_child_completion_report()` emits `message_received` via SSE separately (for child report delivery to parent)
- The DB write is for audit/analytics only — not part of the real-time stream
- After this rewrite, child report delivery uses checkpoint events instead (parent emits when it processes the report)

### 2. Wrong `message_id` on Streaming Events
- `tool_call`, `content_chunk`, `thinking` events use the **user's** message_id
- They should use the **assistant's** message_id
- Root cause: `manager.py:_process_message_with_tracking()` passes `message_id` param (user's) to all `broadcast_streaming_event()` calls

### 3. Inconsistent ID Computation
- `compute_message_id()` uses `(instance_id, role, content[:100])` — a deterministic hash
- For user messages: computed once at queue time (`manager.py:919`)
- For assistant messages: **ID changes** as content accumulates during streaming (`manager.py:1272`)
- REST API (`persistence.py:193`) uses final content → IDs **never match** streaming

### 4. Complex Event System
- **10 event types** in `EventKind` enum (`repositories/event/models.py:13-23`)
- **4 streaming event types** (`event_bus.py:22`)
- **14+ event types handled** in frontend `sse.service.ts` (lines 85-452)
- Frontend `models/index.ts` defines ~20 types for SSE alone (lines 91-186)
- Frontend `sse.service.ts` is **537 lines** for event handling

---

## Proposed Architecture

### 3 Event Types Only

| Event | Payload | When |
|-------|---------|------|
| `connected` | `{instance_id}` | Client connects |
| `checkpoint` | `{instance_id, messages[], checkpoint_id}` | After each LangGraph node completes |
| `error` | `{error, details}` | Unrecoverable failure |
| `keepalive` | `{}` | Every 30s timeout |

**Removed entirely**: `content_chunk`, `thinking`, `tool_call`, `tool_complete`, `message_received`, `message_completed`, `processing_started`, `processing_completed`, `processing_failed`, `child_completed`, `child_failed`, `instance_completed`, `title_updated`, `cancelled`, `message_queued`, `completed`, `status_changed`

### Message Format (Identical in SSE and REST API)

```json
{
  "id": "msg-uuid-from-langgraph",
  "role": "assistant",
  "content": "Hello!",
  "thinking": null,
  "thinking_extracted": null,
  "tool_calls": null,
  "created_at": "2026-04-13T15:30:34.050055+00:00"
}
```

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  LangGraph astream(stream_mode=["updates"])                     │
│                                                                  │
│  agent node completes ──► emit checkpoint event                 │
│  tools node completes ──► emit checkpoint event                 │
│  agent node completes ──► emit checkpoint event                 │
│  ...                                                             │
│                                                                  │
│  Each checkpoint event contains ALL messages from state,        │
│  with LangGraph's msg.id as the message identity.               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Frontend                                                       │
│                                                                  │
│  On "checkpoint" event:                                         │
│    this.messages.set(normalize(event.messages))                 │
│                                                                  │
│  No delta merging, no message_id tracking, no accumulation.     │
│  Just replace the list.                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Detail

### 1. Backend: `daemon/services/event_bus.py` (558 lines)

**What to change**:

#### 1.1 Add `broadcast_checkpoint_event()` method

Add new method after line 280:

```python
async def broadcast_checkpoint_event(
    self,
    instance_id: str,
    messages: list,  # list[BaseMessage] from LangGraph state
    checkpoint_id: str,
) -> None:
    """Broadcast a checkpoint event containing full message state.
    
    This replaces all individual streaming events (content_chunk, thinking,
    tool_call, tool_complete) and lifecycle events (message_received,
    message_completed, processing_started, etc.).
    
    Args:
        instance_id: The instance this checkpoint belongs to.
        messages: Full list of messages from LangGraph channel_values.
        checkpoint_id: Checkpoint ID from LangGraph state.
    """
    serialized = [_serialize_message(msg) for msg in messages]
    
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
    
    self.notify(instance_id)
```

#### 1.2 Add `_serialize_message()` module-level function (NEW)

> ⚠️ This function does not currently exist. It is being **added** as a new module-level helper.

Add at module level (not in EventBus class — so `persistence.py` can reuse it):

```python
def serialize_message(msg, tool_outputs: dict | None = None) -> dict:
    """Serialize a LangChain message to dict matching REST API format.
    
    Must handle all 4 thinking extraction paths:
      1. additional_kwargs.get("reasoning_content")
      2. additional_kwargs.get("thinking")  
      3. msg.reasoning_content attribute
      4. msg.content as list with type="reasoning" blocks
    
    Args:
        msg: LangChain BaseMessage (HumanMessage, AIMessage, ToolMessage, etc.)
        tool_outputs: Optional map of tool_call_id -> output content.
    
    Returns:
        Dict with id, role, content, thinking, tool_calls, created_at.
    """
    from daemon.manager import parse_think_tags
    
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    role = role_map.get(msg.type, msg.type)
    content = getattr(msg, 'content', '') or ''
    
    # Thinking extraction (4 paths, matching manager.py:1276-1302)
    thinking = None
    if hasattr(msg, 'additional_kwargs'):
        kwargs = msg.additional_kwargs or {}
        thinking = kwargs.get("reasoning_content") or kwargs.get("thinking")
    if not thinking and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
        thinking = msg.reasoning_content
    if not thinking and isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "reasoning":
                    thinking = block.get("reasoning") or block.get("summary_text", "")
                    break
    
    # Parse <think/> tags from content
    content_str = content if isinstance(content, str) else str(content)
    content_str, thinking_extracted = parse_think_tags(content_str)
    
    # Tool calls for AIMessage
    tool_calls = None
    if hasattr(msg, 'tool_calls') and msg.tool_calls:
        tool_outputs = tool_outputs or {}
        tool_calls = []
        for tc in msg.tool_calls:
            if isinstance(tc, dict):
                tc_id = tc.get("id", "")
                tool_calls.append({
                    "id": tc_id,
                    "name": tc.get("name", ""),
                    "arguments": tc.get("args", {}),
                    "output": tool_outputs.get(tc_id),
                })
            else:
                tc_id = getattr(tc, "id", "")
                tool_calls.append({
                    "id": tc_id,
                    "name": getattr(tc, "name", ""),
                    "arguments": getattr(tc, "args", {}),
                    "output": tool_outputs.get(tc_id),
                })
    
    return {
        "id": msg.id,                          # LangGraph's ID
        "role": role,
        "content": content_str,
        "thinking": thinking,
        "thinking_extracted": thinking_extracted,
        "tool_calls": tool_calls,
        "created_at": None,                    # Filled from checkpoint timestamps
    }
```

#### 1.3 Code to DELETE from event_bus.py

| Lines | What | Why |
|-------|------|-----|
| 22 | `STREAMING_EVENT_TYPES` constant | No more streaming events |
| 25-28 | `LEGACY_EVENT_MAP` | No more legacy compat |
| 82-106 | `create_message_received_event()` | Replaced by checkpoint |
| 108-134 | `create_processing_started_event()` | Replaced by checkpoint |
| 122-134 | `create_processing_completed_event()` | Replaced by checkpoint |
| 136-159 | `create_child_completed_event()`, `create_child_failed_event()` | Replaced by checkpoint |
| 161-169 | `create_instance_completed_event()` | Replaced by checkpoint |
| 171-181 | `create_error_event()` | Keep minimal version |
| 225-280 | `broadcast_streaming_event()` | Replaced by checkpoint |
| 198-200 | Legacy event type mapping | No more legacy |
| 442-463 | `_next_streaming_id()` | No more streaming IDs |
| 469-478 | `cleanup_old()` | Events no longer in DB |
| 551-552 | Legacy handling in `broadcast_sync()` | No more legacy |

#### 1.4 What to KEEP from event_bus.py

| Lines | What | Why |
|-------|------|-----|
| 31-76 | `__init__()` | Still need queues, notifications |
| 183-219 | `create_event()` | Keep for error events only |
| 286-311 | `get_notification()`, `notify()` | SSE endpoint needs these |
| 317-357 | `get_streaming_queue()`, `get_streaming_events()` | Reuse for checkpoint queue |
| 363-436 | `subscribe_all()`, `unsubscribe_all()`, `_broadcast_to_global()` | Keep for global subscribers |
| 480-516 | `cleanup_instance()`, `shutdown()` | Still needed |
| 522-558 | `broadcast_sync()` | Keep but simplify |

---

### 2. Backend: `daemon/manager.py` — `_process_message_with_tracking()`

**Location**: Lines 1001-1406

**What to change**:

#### 2.1 Remove ALL streaming event broadcasts

Delete these blocks from `_process_message_with_tracking()`:

| Lines | What |
|-------|------|
| 1054-1057 | `accumulated_assistant_content`, `current_assistant_msg_id` variables |
| 1060 | `all_tool_calls = []` |
| 1061 | `tool_call_map = {}` |
| 1064-1085 | Content buffer variables and adaptive batching settings |
| 1188-1198 | `thinking` event broadcast (updates mode) |
| 1200-1226 | `tool_call` event broadcast (updates mode) |
| 1228-1263 | `tool_complete` event broadcast (updates mode) |
| 1265-1339 | Content chunk buffering + thinking buffering (messages mode) |
| 1330-1345 | `content_chunk` event broadcast |
| 1382-1403 | Final content/thinking flush broadcasts |

#### 2.2 Add checkpoint broadcast after each node update

Replace the removed code with a single checkpoint emit in the `updates` mode handler:

```python
async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
    if isinstance(event, tuple):
        mode, data = event
    else:
        mode = "updates"
        data = event
    
    if mode == "updates":
        # Check if any node produced new messages
        for node_name, node_data in data.items():
            node_messages = node_data.get("messages", [])
            if node_messages:
                # Accumulate messages from stream (avoids redundant graph.aget() call)
                all_state_messages.extend(node_messages)
                
                # Build tool_outputs map from ToolMessages
                tool_outputs = {}
                for m in all_state_messages:
                    if hasattr(m, 'tool_call_id'):
                        tool_outputs[m.tool_call_id] = m.content
                
                # Serialize all messages using shared helper
                serialized = [
                    serialize_message(m, tool_outputs) 
                    for m in all_state_messages
                    if m.type != "tool"  # Skip raw ToolMessages
                ]
                
                # Get checkpoint ID from latest message
                checkpoint_id = getattr(node_messages[-1], 'id', str(uuid.uuid4()))
                
                await self._event_bus.broadcast_checkpoint_event(
                    instance_id=instance_id,
                    messages=serialized,
                    checkpoint_id=checkpoint_id,
                )
                break  # Only emit once per update
```

**Important**: We accumulate messages from the `astream` output directly — **no `graph.aget()` call** inside the loop. This avoids the redundant DB read that the council flagged.

#### 2.3 Remove duplicate `message_received` in `enqueue_message()`

Already fixed (lines 984-991 removed). Verify the fix is in place.

#### 2.4 Remove `compute_message_id()` imports and usage

| Line | What |
|------|------|
| 21 | `from .persistence import compute_message_id` — remove |
| 919 | `message_id = compute_message_id(instance_id, "user", message)` — replace with `str(uuid.uuid4())` |
| 1057 | `current_assistant_msg_id = compute_message_id(...)` — remove entirely |
| 1272-1274 | `current_assistant_msg_id = compute_message_id(...)` — remove entirely |

**For user messages in `enqueue_message()`**: Use `str(uuid.uuid4())` instead of `compute_message_id()`. The LangGraph ID will be assigned when the message enters the graph.

---

### 3. Backend: `daemon/manager.py` — completion/report handling

**What to change**:

#### 3.1 Remove `MessageService.on_assistant_message_completed()` calls

The `message_completed` and `processing_completed` events are no longer needed.
Checkpoint events replace them.

Find and remove calls to:
- `self._message_service.on_assistant_message_completed()`
- `self._event_bus.create_processing_completed_event()`
- `self._event_bus.create_message_received_event()` (for child reports)

Child completion reports should still enqueue messages to parent instances,
but the SSE emission happens via checkpoint events, not explicit lifecycle events.

#### 3.2 Simplify child completion flow

Current flow in `_process_child_completion_and_notify_parent()` (line 1861):
1. Build report message
2. Create `MessageQueue` + `Task` entries
3. Call `self._message_service.on_child_completion_report()` ← **REMOVE** (DB write moved directly into this method)
4. Call `create_child_completed_event()` ← **REMOVE** (replaced by parent's checkpoint)

After changes:
1. Build report message
2. Create `MessageQueue` + `Task` entries
3. Insert message record to DB directly ← NEW (moved from `on_child_completion_report()`)

The parent instance will emit a checkpoint event when it processes the report message.

---

### 4. Backend: `daemon/api.py` — SSE endpoint

**Location**: Lines 822-1024

**What to change**:

#### 4.1 Rewrite `stream_events()` (lines 822-951)

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
        current_messages = await manager.get_messages(instance_id)
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

#### 4.2 Delete `format_sse_event()` (lines 954-1024)

No longer needed. Events are formatted inline in `stream_events()`.

#### 4.3 Remove merge logic

The current code merges DB events and streaming events (lines 922-936).
This entire merge is deleted — only checkpoint events from the queue are used.

---

### 5. Backend: `daemon/persistence.py` — REST API messages

**Location**: Lines 74-212

**What to change**:

#### 5.1 Rewrite `get_instance_messages()` to use LangGraph IDs

```python
async def get_instance_messages(
    checkpointer: AsyncSqliteSaver,
    instance_id: str,
) -> list[dict[str, Any]]:
    """Get message history from LangGraph checkpoints using native msg.id."""
    from daemon.services.event_bus import serialize_message
    
    config = {"configurable": {"thread_id": instance_id}}
    state = await checkpointer.aget(config)
    if not state:
        return []
    
    messages = state.get("channel_values", {}).get("messages", [])
    if not messages:
        return []
    
    # Collect timestamps from checkpoint history
    msg_timestamps = await _collect_timestamps(checkpointer, config, messages)
    
    # Build tool_outputs map from ToolMessages
    tool_outputs = {}
    for msg in messages:
        if hasattr(msg, 'tool_call_id'):
            tool_outputs[msg.tool_call_id] = msg.content
    
    result = []
    for msg in messages:
        if msg.type == "tool":
            continue  # ToolMessages included in AIMessage's tool_calls
        
        serialized = serialize_message(msg, tool_outputs)
        serialized["instance_id"] = instance_id
        serialized["created_at"] = msg_timestamps.get(msg.id)
        result.append(serialized)
    
    return result
```

#### 5.2 Remove `compute_message_id()` function (lines 23-37)

No longer used anywhere. Delete entirely.

#### 5.3 Keep `get_checkpointer()` (lines 40-71)

Unchanged.

#### 5.4 Keep `_collect_timestamps()` helper

Extract the timestamp tracking logic from current `get_instance_messages()` (lines 104-128)
into a helper function. It still needs to iterate checkpoint history to find when each
message first appeared.

---

### 6. Backend: `daemon/message_models.py` (91 lines)

**What to delete**:

| Lines | What | Why |
|-------|------|-----|
| 27-34 | `SSEEventPayload` | No more SSE event payloads |
| 37-42 | `SSEEventDelta` | No more delta types |
| 45-51 | `SSEEventStatus` | No more status events |
| 54-89 | `UnifiedMessage` | No more unified message format |

**What to keep**:

| Lines | What | Why |
|-------|------|-----|
| 13-17 | `MessageRole` enum | Still useful for type safety |
| 20-24 | `ToolCallInfo` | Still used for tool call structure |

---

### 7. Backend: `daemon/services/message_service.py` (163 lines)

> ⚠️ **Deletion is NOT straightforward.** `MessageService` has two concerns:
> 1. **SSE broadcast** (replaced by checkpoint events) — can be removed
> 2. **DB persistence** (inserts message records) — **must be preserved**

**DB persistence logic that must be moved before deletion:**

| Method | Lines | What it does | Where to move |
|--------|-------|-------------|---------------|
| `on_user_message_stored()` | 29–53 | Inserts message record to DB after user message queued | `manager.py:enqueue_message()` |
| `on_assistant_message_completed()` | 55–111 | Inserts assistant message record + tool message records | `manager.py:_process_message_with_tracking()` |

**Deletion plan:**
1. Move DB write from `on_user_message_stored()` into `enqueue_message()` (before or after existing DB ops)
2. Move DB writes from `on_assistant_message_completed()` into `_process_message_with_tracking()` (after assistant content is finalized)
3. Delete `message_service.py` entirely
4. Remove `MessageService` instantiation and `self._message_service` from `manager.py`

**Child report handling** (via `on_child_completion_report()`) — still needs DB write to persist the report message to the parent's message queue. This should remain in `manager.py:_process_child_completion_and_notify_parent()` directly.

---

### 8. Backend: `daemon/repositories/event/` (models.py + repository.py)

**Decision**: Keep the Event table and repository for now. It's used for:
- Error event tracking (still useful)
- Potential future use (analytics, audit log)

But remove all lifecycle event creation except `error`.

---

### 9. Frontend: `frontend/src/app/services/sse.service.ts` (537 lines → ~80 lines)

**Rewrite entirely**.

#### Current signals to keep

```typescript
isStreaming = signal(false);
events = signal<SSEEvent[]>([]);           // Keep for debugging
latestError = signal<...>(null);           // Keep
```

#### Signals to remove

```typescript
statusUpdates = signal<Map<string, string>>(new Map());   // DELETE
titleUpdates = signal<...>(null);                          // DELETE
messageDeltas = signal<MessageDelta[]>([]);                // DELETE — replaced by checkpoint
```

#### New signal

```typescript
messages = signal<Message[]>([]);  // Replaces messageDeltas
```

#### New simplified event handling

```typescript
private handleEvent(event: MessageEvent) {
  const data = JSON.parse(event.data);
  
  switch (event.type) {
    case 'connected':
      this.events.update(e => [...e, { type: 'connected', data }]);
      break;
      
    case 'checkpoint':
      this.isStreaming.set(true);
      this.messages.set(
        data.messages.map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content || '',
          thinking: m.thinking || null,
          thinking_extracted: m.thinking_extracted || null,
          tool_calls: m.tool_calls || null,
          created_at: m.created_at || new Date().toISOString(),
        }))
      );
      break;
      
    case 'error':
      this.isStreaming.set(false);
      this.latestError.set(data);
      break;
      
    case 'keepalive':
      break;
  }
}
```

#### Methods to DELETE

| Lines | Method | Why |
|-------|--------|-----|
| 29-52 | `isValidInstanceEvent()`, `emitDelta()` | No more deltas |
| 70-472 | `connectInternal()` (all event listeners) | Replaced by single handler |
| 474-508 | `handleCompletedEvent()` | No more completion events |

---

### 10. Frontend: `frontend/src/app/pages/chat/chat.component.ts` (717 lines)

**What to change**:

#### 10.1 Delete the main delta-processing effect (lines 80-349)

The 270-line effect that handles `processing_started`, `message_received`,
`content_chunk`, `thinking`, `tool_call`, `tool_complete`, `processing_completed`,
`message_completed` — **all deleted**.

#### 10.2 Replace with simple checkpoint effect

```typescript
effect(() => {
  const messages = this.sseService.messages();
  if (messages.length > 0) {
    this.messages.set(messages.map(m => this.toViewModel(m)));
    this.isSending.set(false);
  }
});
```

#### 10.3 Delete title update effect (lines 351-359)

Title updates come from the instance API, not SSE.

#### 10.4 Delete error handling effect (lines 362-376)

Replace with simpler error handler:

```typescript
effect(() => {
  const error = this.sseService.latestError();
  if (error) {
    this.isSending.set(false);
    // Show error in UI
  }
});
```

#### 10.5 Delete `message_id`-based lookup logic (line 99)

Current: `let msgIndex = updated.findIndex(m => m.message_id === delta.message_id)`
New: No merging needed — `messages` signal is replaced entirely on each checkpoint.

#### 10.6 Delete HTTP message merge logic (lines 512-528)

Current: `existingMap.get(httpMsg.message_id)` merges SSE messages with HTTP messages.
New: SSE messages ARE the source of truth. On connect, initial state comes from
first checkpoint event. No merge needed.

---

### 11. Frontend: `frontend/src/app/models/index.ts` (269 lines)

**What to change**:

#### 11.1 Update `Message` interface (lines 24-36)

```typescript
interface Message {
  id: string;                              // LangGraph's msg.id (was message_id)
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls?: ToolCall[] | null;
  created_at?: string;
}
```

Key change: `message_id` → `id` (matching LangGraph's field name).

#### 11.2 Delete SSE-specific types

| Lines | What | Why |
|-------|------|-----|
| 91-106 | `EventType` union (14 types) | Only `connected`, `checkpoint`, `error`, `keepalive` |
| 108-114 | `SSEEventEnvelope` | No more envelopes |
| 117-123 | `SSEEventEnvelope` | No more envelopes |
| 125-130 | `SSEDelta` | No more deltas |
| 132-138 | `SSEStatus` | No more status events |
| 142-151 | `MessageDeltaType` union (9 types) | No more deltas |
| 154-164 | `CanonicalMessage` | No more canonical messages |
| 166-186 | `MessageDelta` | No more deltas |

#### 11.3 Simplified SSE types

```typescript
type SseEventType = 'connected' | 'checkpoint' | 'error' | 'keepalive';

interface SSEEvent {
  type: SseEventType;
  data: Record<string, unknown>;
}
```

#### 11.4 Keep unchanged

| Lines | What |
|-------|------|
| 2-13 | `InstanceStatus`, `InstanceInfo` |
| 15-21 | `InstanceListResponse` |
| 38-43 | `ToolCall` (unchanged) |
| 45-57 | `MessageCreate`, `MessageResponse` |
| 67-76 | Agent types |
| 192-202 | Source types |

---

### 12. Backend: Remove `compute_message_id()` usage everywhere

Search all files for `compute_message_id` and replace:

| File | Line | Current | New |
|------|------|---------|-----|
| `manager.py` | 21 | `from .persistence import compute_message_id` | Delete import |
| `manager.py` | 919 | `compute_message_id(instance_id, "user", message)` | `str(uuid.uuid4())` |
| `manager.py` | 1057 | `compute_message_id(instance_id, "assistant", "")` | Delete |
| `manager.py` | 1272-1274 | `compute_message_id(instance_id, "assistant", accumulated_assistant_content)` | Delete |
| `message_service.py` | 14 | `from daemon.persistence import compute_message_id` | File deleted |
| `message_service.py` | 70 | `compute_message_id(instance_id, "assistant", content)` | File deleted |
| `persistence.py` | 23-37 | `def compute_message_id(...)` | Delete function |
| `persistence.py` | 193 | `compute_message_id(instance_id, role, content)` | Use `msg.id` |

---

### 13. Backend: What to keep unchanged

| File/Section | Why |
|-------------|-----|
| `daemon/graph.py` | Graph structure unchanged |
| `daemon/repositories/task/` | Task queue unchanged |
| `daemon/repositories/message_queue/` | Message queue unchanged |
| `daemon/sources/` | Message adapters unchanged |
| `daemon/tools/` | Agent tools unchanged |
| `daemon/loader.py` | Agent loading unchanged |
| `daemon/config.py` | Config unchanged |

---

## File Change Summary

### Backend — Files to Modify

| File | Action | Approx Lines Changed |
|------|--------|---------------------|
| `daemon/services/event_bus.py` | Major rewrite | ~400 deleted, ~100 added |
| `daemon/manager.py` | Remove streaming, add checkpoints | ~300 deleted, ~50 added |
| `daemon/api.py` | Rewrite SSE endpoint | ~200 deleted, ~60 added |
| `daemon/persistence.py` | Use LangGraph IDs, shared serialization | ~100 deleted, ~50 added |

### Backend — Files to Delete

| File | Reason |
|------|--------|
| `daemon/services/message_service.py` | DB persistence logic moved to `manager.py`; SSE broadcast replaced by checkpoints. File is now empty/unused. |

### Backend — Files to Clean Up

| File | Action |
|------|--------|
| `daemon/message_models.py` | Delete SSE-specific models, keep ToolCallInfo |
| `daemon/repositories/event/models.py` | Keep EventKind enum (simplify later) |
| `daemon/repositories/event/repository.py` | Keep (still used for error events) |

### Frontend — Files to Modify

| File | Action | Approx Lines Changed |
|------|--------|---------------------|
| `frontend/src/app/services/sse.service.ts` | Full rewrite | ~450 deleted, ~80 added |
| `frontend/src/app/pages/chat/chat.component.ts` | Remove delta effects | ~300 deleted, ~30 added |
| `frontend/src/app/models/index.ts` | Delete SSE types, update Message | ~100 deleted, ~20 added |

---

## Testing Plan

### Unit Tests to Update

| Test File | What |
|-----------|------|
| `tests/unit/test_*` | Remove tests for deleted streaming events |
| Tests for `compute_message_id` | Delete (function removed) |
| Tests for `MessageService` | Delete (file removed) |
| Tests for `EventBus.broadcast_streaming_event` | Replace with `broadcast_checkpoint_event` |
| Tests for `format_sse_event` | Delete (function removed) |

### New Unit Tests Needed

1. `serialize_message()` — verify all message types serialize correctly
2. `broadcast_checkpoint_event()` — verify queue delivery
3. `get_instance_messages()` — verify LangGraph IDs are used
4. SSE endpoint — verify only `checkpoint` events are emitted
5. `serialize_message()` with `tool_outputs` map — verify tool outputs are embedded in assistant message's `tool_calls[]` field

### Integration Tests Needed

1. Send message → verify SSE delivers checkpoint with LangGraph IDs
2. Send message → verify GET /messages returns same IDs as SSE
3. Tool call → verify checkpoint includes both AIMessage and ToolMessage
4. Multi-turn → verify messages accumulate correctly across checkpoints
5. Reconnect → verify initial checkpoint is sent

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| LangGraph `msg.id` might be `None` for some message types | Add assertion/log in `serialize_message()`, fallback to `str(uuid.uuid4())` |
| Thinking extraction has 4 provider-specific paths | Port all 4 paths to `serialize_message()` |
| `tool_outputs` map needs ToolMessages that are excluded from output | Build map before filtering, pass to `serialize_message()` |
| Frontend field rename `message_id` → `id` breaks all references | Search entire frontend for `.message_id` and update |
| No real-time feedback during LLM inference (30-60s) | Acceptable for long-running task focus. Can add `status` events later. |
| Large message list on each checkpoint | Acceptable for now. Add diff mode later if needed. |

---

## Implementation Order

1. **`daemon/services/event_bus.py`** — Add `serialize_message()` + `broadcast_checkpoint_event()`, remove old methods
2. **`daemon/persistence.py`** — Use `serialize_message()`, remove `compute_message_id()`
3. **`daemon/manager.py`** — Move DB writes from `message_service.py` into `enqueue_message()` and `_process_message_with_tracking()`; replace streaming with checkpoints; remove `compute_message_id` usage
4. **`daemon/api.py`** — Rewrite SSE endpoint
5. **`daemon/message_models.py`** — Clean up
6. **Delete `daemon/services/message_service.py`** — Only after DB writes have been moved to `manager.py`
7. **`frontend/src/app/models/index.ts`** — Update interfaces (`message_id` → `id`)
8. **`frontend/src/app/services/sse.service.ts`** — Rewrite
9. **`frontend/src/app/pages/chat/chat.component.ts`** — Simplify (remove delta effects, remove `message_id` lookups)
10. **Update tests**

---

## Pre-Implementation Verification

Run these before starting to scope the impact:

1. **Frontend `message_id` references** — Count all files that need updating:

   ```bash
   grep -r "message_id" frontend/src --include="*.ts" -l | wc -l
   grep -r "\.message_id" frontend/src --include="*.ts" -c
   ```

2. **`parse_think_tags()` circular import risk** — `event_bus.py` cannot import from `manager.py` (EventBus is injected into manager, so a circular dependency exists). If `parse_think_tags()` is only in `manager.py`, move it to a shared utility module (e.g., `daemon/utils.py`) before `serialize_message()` tries to use it.

3. **Thinking extraction provider coverage** — Confirm all 4 extraction paths are needed for your LLM providers. If any path is unused, document which providers need which paths and remove dead paths.

4. **`on_child_completion_report()` DB write scope** — Verify exactly what this method writes to DB (message record, task record, etc.) so the replacement in `manager.py` is complete.
