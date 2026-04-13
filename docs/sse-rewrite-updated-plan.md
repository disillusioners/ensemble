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

### 5. Double `CHILD_COMPLETED` Event Creation (BUG)
- `_create_completion_events()` (manager.py:1850) creates a raw `Event(kind=CHILD_COMPLETED)` in the DB
- Then `_process_child_completion_and_notify_parent()` (manager.py:1943) calls `event_bus.create_child_completed_event()` which creates **another** Event row for the same thing
- After rewrite: only checkpoint events are used — this duplication is eliminated

### 6. Duplicate SSE `error` Listener (BUG)
- `sse.service.ts` registers **two** `addEventListener('error', ...)` handlers (lines 300–333 and 418–452)
- They handle different envelope formats, indicating inconsistent backend error formatting
- After rewrite: single event model eliminates this

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

## Implementation Details

### 1. Backend: `daemon/services/event_bus.py`

#### 1.1 Add `broadcast_checkpoint_event()` method

Add new method on the `EventBus` class:

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
    
    self.notify(instance_id)
    # Notify global subscribers (ResponseDispatcher, etc.)
    await self._broadcast_to_global(instance_id, "checkpoint", data=event)
```

#### 1.2 Add `serialize_message()` module-level function

Add at module level in `event_bus.py` (not in the `EventBus` class — so `persistence.py` can reuse it):

```python
def serialize_message(msg, tool_outputs: dict | None = None) -> dict:
    """Serialize a LangChain message to dict matching REST API format.
    
    Must handle all 5 thinking extraction paths:
      1. additional_kwargs.get("reasoning_content")
      2. additional_kwargs.get("thinking")  
      3. msg.reasoning_content attribute
      4. msg.thinking attribute (Claude models)
      5. msg.content as list with type="reasoning" blocks
    
    Args:
        msg: LangChain BaseMessage (HumanMessage, AIMessage, ToolMessage, etc.)
        tool_outputs: Optional map of tool_call_id -> output content.
    
    Returns:
        Dict with id, role, content, thinking, tool_calls, created_at.
    """
    from daemon.utils import parse_think_tags  # lazy import to avoid circular dep
    
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    role = role_map.get(msg.type, msg.type)
    content = getattr(msg, 'content', '') or ''
    
    # Thinking extraction (5 paths)
    thinking = None
    if hasattr(msg, 'additional_kwargs'):
        kwargs = msg.additional_kwargs or {}
        thinking = kwargs.get("reasoning_content") or kwargs.get("thinking")
    if not thinking and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
        thinking = msg.reasoning_content
    if not thinking and hasattr(msg, 'thinking') and msg.thinking:
        thinking = msg.thinking
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
        # LangGraph msg.id can be None — use uuid fallback to prevent runtime errors
        "id": getattr(msg, 'id', None) or str(uuid.uuid4()),
        "role": role,
        "content": content_str,
        "thinking": thinking,
        "thinking_extracted": thinking_extracted,
        "tool_calls": tool_calls,
        "created_at": None,  # Filled from checkpoint timestamps in persistence.py; None in SSE path
    }
```

**Notes**:
- **`msg.id` can be `None`**: LangGraph message IDs can be `None` for certain message types or providers. The `getattr(..., 'id', None) or str(uuid.uuid4())` fallback is required.
- **`created_at`**: In the SSE streaming path, `created_at` will be `None` because checkpointing does not include timestamps. Timestamps are only populated when loading from the REST API via checkpoint history.

#### 1.3 Code to DELETE from event_bus.py

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
| 161-169 | `create_instance_completed_event()` | Replaced by checkpoint |
| 171-181 | `create_error_event()` | Keep minimal version |
| 198-200 | Legacy event type mapping | No more legacy |
| 225-280 | `broadcast_streaming_event()` | Replaced by checkpoint |
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
| 522-558 | `broadcast_sync()` | Audit first — delete if no callers; otherwise simplify |

---

### 2. Backend: `daemon/manager.py` — `_process_message_with_tracking()`

**Location**: Lines **991–1479** (method signature at 991, body 1001–1479)

#### 2.1 Remove ALL streaming event broadcasts

Delete these blocks from `_process_message_with_tracking()`:

| Lines | What |
|-------|------|
| 1054-1057 | `accumulated_assistant_content`, `current_assistant_msg_id` variables |
| 1060 | `all_tool_calls = []` |
| 1061 | `tool_call_map = {}` |
| 1064-1085 | Content buffer variables and adaptive batching settings |
| 1185-1193 | `thinking` event broadcast (updates mode) |
| 1209-1221 | `tool_call` event broadcast (updates mode) |
| 1250-1258 | `tool_complete` event broadcast (updates mode) |
| 1265-1339 | Content chunk buffering + thinking buffering (messages mode) |
| 1336-1347 | `content_chunk` event broadcast |
| 1380-1391 | Content/thinking flush broadcasts in `finally` block |

#### 2.2 Add checkpoint broadcast after each node update

Replace the streaming loop (around line 1156 where `graph.astream()` is called) with:

```python
all_state_messages: list = []

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
                    tool_outputs=tool_outputs,
                )
                break  # Only emit once per update
```

> **Behavior change**: `stream_mode=["updates"]` drops token-level streaming entirely. Users will see no output until a node completes. This is an accepted regression per the project's long-running task focus.

**Important**: We accumulate messages from the `astream` output directly — **no `graph.aget()` call** inside the loop.

#### 2.3 Remove post-streaming logic after `finally` block (lines 1407–1479)

The code after line 1406 (`finally` block) handles:
- Finalizing the assistant message
- Calling `self._message_service.on_assistant_message_completed()` (line ~1410)
- Calling `self._send_error_report()` on failure

**What to do with this section**:
- The call to `on_assistant_message_completed()` (DB write) must be replaced with inline DB persistence (see Section 3)
- The `finally` block should still be kept for its primary purpose: ensuring `is_processing = False` and cleanup
- Delete the streaming event broadcasts from within it (covered by 2.1 above)
- `_send_error_report()` call site must be updated (see Section 3.3)

#### 2.4 Remove `compute_message_id()` imports and usage

| Line | What |
|------|------|
| 21 | `from .persistence import compute_message_id` — remove |
| 919 | `message_id = compute_message_id(instance_id, "user", message)` — replace with `str(uuid.uuid4())` |
| 1057 | `current_assistant_msg_id = compute_message_id(...)` — remove entirely |
| 1272-1274 | `current_assistant_msg_id = compute_message_id(...)` — remove entirely |

**For user messages in `enqueue_message()`**: Use `str(uuid.uuid4())` instead of `compute_message_id()`. The LangGraph ID will be assigned when the message enters the graph.

#### 2.5 `message_received` in `enqueue_message()`

The `MESSAGE_RECEIVED` Event row in `enqueue_message()` (lines 958-975) stays for audit/analytics. No change needed.

---

### 3. Backend: `daemon/manager.py` — completion/report handling

#### 3.1 Remove `MessageService.on_assistant_message_completed()` calls

**The actual call site is in `task_processor.py`, NOT `manager.py`**:

Find and remove:
- `task_processor.py:171`: `await self._message_service.on_assistant_message_completed(...)` — migrate DB write inline here
- `self._event_bus.create_processing_completed_event()` — called by MessageService
- `self._event_bus.create_message_received_event()` — for child reports

Child completion reports should still enqueue messages to parent instances, but the SSE emission happens via checkpoint events.

#### 3.2 Simplify child completion flow

Current flow in `_process_child_completion_and_notify_parent()` (lines 1861–1948):
1. Build report message
2. Create `MessageQueue` + `Task` entries
3. Call `_create_completion_events()` ← **Keep** (raw Event rows for audit)
4. Call `self._message_service.on_child_completion_report()` ← **Remove** DB write (moved inline)
5. Call `self._event_bus.create_child_completed_event()` ← **Remove** (BUG: creates duplicate CHILD_COMPLETED; checkpoint replaces it)

After changes:
1. Build report message
2. Create `MessageQueue` + `Task` entries
3. Insert message record to DB directly ← **NEW** (moved from `on_child_completion_report()`)
4. `_create_completion_events()` — keep for audit log

The parent instance will emit a checkpoint event when it processes the report message.

#### 3.3 Update `_send_error_report()` (lines 1950–2066)

Remove the call to `self._message_service.on_child_error_report()` from `_send_error_report()`. The error report message is already persisted to the DB — the parent will pick it up on its next checkpoint.

Also remove `self._event_bus.create_child_failed_event()` at line 2046.

#### 3.4 `ResponseDispatcher` integration

The `ResponseDispatcher` (`daemon/sources/dispatcher.py:62,178`) subscribes to ALL events via `subscribe_all()` and filters for `event_type == "completed"` to route agent responses back to external sources (Telegram, Discord, etc.).

**After the rewrite**: Keep emitting a lightweight `completed` event specifically for the dispatcher. This preserves the dispatcher's expected payload structure `{source, content, message_type}` without polluting the SSE stream:

```python
# In task_processor.py:ProcessMessageProcessor.process(), after message completes:
# Use _broadcast_to_global() directly — broadcast_sync() uses asyncio.run_coroutine_threadsafe()
# which is for synchronous callers, not async contexts.
await self._event_bus._broadcast_to_global(
    instance_id=instance_id,
    event_type="completed",
    data={
        "source": source,
        "content": final_message_content,
        "message_type": "final",
        "instance_id": instance_id,
    }
)
```

Update `ResponseDispatcher._handle_global_event()` to skip `checkpoint` events:
```python
if event_type == 'checkpoint':
    return  # Dispatcher only handles 'completed' events
```

The SSE stream only has `connected`/`checkpoint`/`error`/`keepalive`. The dispatcher receives `completed` via `_broadcast_to_global()`.

#### 3.5 `_create_completion_events()` (lines 1807–1859)

**Keep this method.** The Event table is used for audit/analytics. These raw inserts are the audit log, separate from the SSE stream.

After the rewrite, verify this method is still called from:
- `process_and_complete()` — for `INSTANCE_COMPLETED`
- `_process_child_completion_and_notify_parent()` — for `CHILD_COMPLETED`

After removing the duplicate `create_child_completed_event()` call, `CHILD_COMPLETED` will only be written once (via this method) — fixing the existing bug.

#### 3.6 Audit `process_and_complete()` before implementation

`process_and_complete()` is the task completion entry point. Audit before deleting `create_instance_completed_event()`:
```bash
grep -n "create_instance_completed_event\|create_agent_message_delta\|create_processing_completed_event\|broadcast_streaming_event" daemon/manager.py
```

Delete or redirect all EventBus calls in this method.

#### 3.7 Audit all `create_*_event()` call sites

Before implementing Step 1, audit all callers:
```bash
grep -rn "create_instance_completed_event\|instance_completed\|INSTANCE_COMPLETED\|create_processing_started_event\|create_processing_completed_event\|create_processing_failed_event\|create_child_completed_event\|create_child_failed_event" daemon/ --include="*.py"
```

Map every call site, then either update callers or delete them.

#### 3.8 Error handler scope

The exception handler in `_process_message_with_tracking()` (around line 1374) calls `create_error_event()`. Keep `create_error_event()` but clarify: the error handler remains for **graph execution errors**, not streaming errors. Update the exception scope post-rewrite.

---

### 4. Backend: `daemon/api.py` — SSE endpoint

**Location**: `stream_events()` at lines **822–951**; `format_sse_event()` at lines **954–1024**

#### 4.1 Rewrite `stream_events()` (lines 822-951)

> **Reconnection support**: The current SSE endpoint supports `Last-Event-ID` header for cursor-based reconnection (`api.py:862-880`). The simplified implementation below drops reconnection support. Document as accepted regression, or implement cursor-based seek using checkpoint sequence numbers if needed later.

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

#### 4.2 Delete `format_sse_event()` (lines 954-1024)

No longer needed. Events are formatted inline in `stream_events()`.

#### 4.3 Remove merge logic

The current code merges DB events and streaming events (lines 922-936). This entire merge is deleted — only checkpoint events from the queue are used.

---

### 5. Backend: `daemon/persistence.py` — REST API messages

**Location**: Lines 74-212

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

Extract the timestamp tracking logic from current `get_instance_messages()` (lines 104-128) into a helper function.

---

### 6. Backend: `daemon/message_models.py`

**What to delete**:

| Lines | What | Why |
|-------|------|-----|
| 27-34 | `SSEEventPayload` | No more SSE event payloads |
| 37-42 | `SSEEventDelta` | No more delta types |
| 45-51 | `SSEEventStatus` | No more status events |
| 54-89 | `UnifiedMessage` | No more unified message format |

**What to keep**: `MessageRole` enum and `ToolCallInfo`

---

### 7. Backend: `daemon/services/message_service.py`

> **Deletion is NOT straightforward.** `MessageService` has two concerns:
> 1. **SSE broadcast** (replaced by checkpoint events) — can be removed
> 2. **DB persistence** (inserts message records) — **must be preserved**

**DB persistence logic that must be moved before deletion:**

| Method | Lines | What it does | Where to move |
|--------|-------|-------------|---------------|
| `on_assistant_message_completed()` | 55–111 | Inserts assistant message record + tool message records | `task_processor.py:ProcessMessageProcessor.process()` (line 171) |
| `on_child_completion_report()` | 113–136 | Inserts child report message record to parent's queue | `manager.py:_process_child_completion_and_notify_parent()` |
| `on_child_error_report()` | 138–163 | Inserts child error report message record | Remove call from `_send_error_report()` (Section 3.3) |

**Deletion plan:**
1. Move DB write from `on_assistant_message_completed()` into `task_processor.py:171`
2. Move DB write from `on_child_completion_report()` into `_process_child_completion_and_notify_parent()`
3. Remove call to `on_child_error_report()` from `_send_error_report()` (Section 3.3)
4. Delete `message_service.py` entirely
5. Remove `MessageService` instantiation and all `self._message_service` call sites from `manager.py` AND `task_processor.py`

**Also audit `task_processor.py:129-140`**: The fallback when `_event_bus` is None uses `_event_repo.create_event()` directly. If `_event_bus` is always set in production, delete this path. If it can fire, update to use the new `completed` event approach.

---

### 8. Backend: `daemon/repositories/event/`

**Decision**: Keep the Event table and repository for now. It's used for:
- Error event tracking (still useful)
- Potential future use (analytics, audit log)

But remove all lifecycle event creation except `error`.

---

### 9. Frontend: `frontend/src/app/services/sse.service.ts`

**Rewrite entirely**.

#### Current signals to keep

```typescript
isStreaming = signal(false);
events = signal<SSEEvent[]>([]);  // Keep for debugging
latestError = signal<...>(null);   // Keep
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

### 10. Frontend: `frontend/src/app/pages/chat/chat.component.ts`

#### 10.1 Delete the main delta-processing effect (lines 80–349)

The 270-line effect that handles `processing_started`, `message_received`, `content_chunk`, `thinking`, `tool_call`, `tool_complete`, `processing_completed`, `message_completed` — **all deleted**.

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

#### 10.3 Delete title update effect (lines 362–376)

Title updates come from the instance API, not SSE.

#### 10.4 Delete error handling effect (lines 379–387)

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

#### 10.5 Evaluate fallback `isSending` reset effect (lines 352–359)

After the rewrite, `isSending` is reset when the first checkpoint arrives (Section 10.2). This may be redundant.

#### 10.6 Delete `message_id`-based lookup logic (line 99)

No merging needed — `messages` signal is replaced entirely on each checkpoint.

#### 10.7 Delete HTTP message merge logic (lines 511–528)

SSE messages ARE the source of truth. On connect, initial state comes from first checkpoint event. No merge needed.

---

### 11. Frontend: `frontend/src/app/models/index.ts`

#### 11.1 Update interfaces

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

interface MessageResponse {
  id: string;                              // was message_id — matches LangGraph
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  instance_id?: string;
}
```

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

| File | Line | Current | New |
|------|------|---------|-----|
| `manager.py` | 21 | `from .persistence import compute_message_id` | Delete import |
| `manager.py` | 919 | `compute_message_id(instance_id, "user", message)` | `str(uuid.uuid4())` |
| `manager.py` | 1057 | `current_assistant_msg_id = compute_message_id(...)` | Delete entire line |
| `manager.py` | 1272-1274 | `current_assistant_msg_id = compute_message_id(...)` | Delete |
| `message_service.py` | — | File deleted | — |
| `persistence.py` | 23-37 | `def compute_message_id(...)` | Delete function |
| `persistence.py` | ~193 | `compute_message_id(instance_id, role, content)` | Use `msg.id` directly |

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
| `daemon/manager.py` | Remove streaming, add checkpoints, migrate MessageService | ~300 deleted, ~100 added |
| `daemon/api.py` | Rewrite SSE endpoint | ~200 deleted, ~60 added |
| `daemon/persistence.py` | Use LangGraph IDs, shared serialization | ~100 deleted, ~50 added |
| `daemon/task_processor.py` | Remove EventBus lifecycle calls | TBD — audit first |

### Backend — Files to Delete

| File | Reason |
|------|--------|
| `daemon/services/message_service.py` | DB persistence logic moved inline; SSE broadcast replaced by checkpoints. |

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
| `frontend/src/app/pages/chat/chat.component.ts` | Remove delta effects, remove `message_id` lookups | ~300 deleted, ~30 added |
| `frontend/src/app/models/index.ts` | Delete SSE types, update Message (`message_id` → `id`) | ~100 deleted, ~20 added |
| `frontend/src/app/pages/chat/chat-interface.component.ts` | Update `trackBy` function | ~1 line |
| `frontend/src/app/services/sse.service.spec.ts` | Update test mocks | ~1 line |

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
| Tests for `EventBus` convenience methods | Delete (`create_processing_started_event`, `create_child_completed_event`, etc.) |
| Tests for `parse_think_tags` in manager context | Move to `daemon/utils.py` |

### New Unit Tests Needed

1. `serialize_message()` — verify all message types serialize correctly (HumanMessage, AIMessage, ToolMessage, SystemMessage)
2. `broadcast_checkpoint_event()` — verify queue delivery with tool_outputs
3. `get_instance_messages()` — verify LangGraph IDs are used and match between SSE and REST API
4. SSE endpoint (`/instances/{id}/events`) — verify only `connected`, `checkpoint`, `error`, `keepalive` events are emitted
5. `serialize_message()` with `tool_outputs` map — verify tool outputs are embedded in assistant message's `tool_calls[]` field
6. `broadcast_checkpoint_event()` — concurrent emissions don't cause race conditions
7. **`serialize_message()` with `msg.id = None`** — verify fallback to `uuid.uuid4()` works
8. **`serialize_message()` with all thinking formats** — verify all 5 extraction paths
9. **`ResponseDispatcher` routing after rewrite** — verify external sources still receive responses via `_broadcast_to_global()` calls

### Integration Tests Needed

1. Send message → verify SSE delivers checkpoint with LangGraph IDs
2. Send message → verify GET /messages returns same IDs as SSE
3. Tool call → verify checkpoint includes tool_calls with outputs
4. Multi-turn → verify messages accumulate correctly across checkpoints
5. Reconnect → verify initial checkpoint is sent on new SSE connection
6. Child agent completes → verify parent receives checkpoint with child's report message
7. Error during processing → verify SSE emits `error` event
8. **Multi-turn message ID consistency** — Send msg1 → verify checkpoint → send msg2 → verify BOTH messages have consistent IDs → refresh → verify REST API matches SSE
9. **Backend restart → SSE reconnect** — Verify client reconnects and receives current state
10. **External source routing (Telegram/Discord)** — Verify ResponseDispatcher still routes responses

### Paths NOT Previously Covered by Tests

| Path | Why |
|------|-----|
| `_create_completion_events()` | Raw Event creation for audit log — ensure it still runs after rewrite |
| `_send_error_report()` after `on_child_error_report()` removal | Error reports must still be persisted |
| `TaskProcessor` + EventBus integration | Remove lifecycle events from task processing path |
| `_process_child_completion_and_notify_parent()` after duplicate removal | CHILD_COMPLETED should only be written once |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Circular import: `event_bus.py` → `manager.py` for `parse_think_tags` | **Step 0**: Move `parse_think_tags` + `_THINK_PATTERN` to `daemon/utils.py` before any other changes |
| LangGraph `msg.id` might be `None` for some message types | `getattr(msg, 'id', None) or str(uuid.uuid4())` fallback in `serialize_message()` |
| Thinking extraction has 5 provider-specific paths | Port all 5 paths to `serialize_message()` |
| `tool_outputs` map needs ToolMessages that are excluded from output | Build map before filtering, pass to `serialize_message()` |
| Frontend field rename `message_id` → `id` breaks all references | Search entire frontend for `.message_id` and update all references |
| No real-time feedback during LLM inference | Acceptable for long-running task focus. |
| Large message list on each checkpoint | Acceptable for now. Add diff mode later if needed. |
| `created_at` is `None` during SSE streaming | Accept regression. REST API populates after reload. |
| `TaskProcessor` calls EventBus lifecycle methods | Audit and remove in Step 3 before deleting `create_*_event` methods |
| `ResponseDispatcher` loses event stream (external sources silent) | Keep lightweight `completed` event via `_broadcast_to_global()` for dispatcher |
| `broadcast_sync()` still calls deleted `broadcast_streaming_event()` | Remove streaming branch, add checkpoint routing. Audit for callers first — delete if unused. |
| `Last-Event-ID` reconnection support silently dropped | Document as regression or implement cursor-based seek later |
| `send_message()` inconsistency with new system | Document as SSE-invisible. `ainvoke()` bypasses SSE. |
| `on_assistant_message_completed()` call is in `task_processor.py`, not `manager.py` | Update call site reference to `task_processor.py:171`. Migrate DB logic there. |
| `_broadcast_to_global()` signature: positional arg maps to `event_id`, not `data` | Use `data=event` keyword arg |
| `create_child_failed_event()` call in `_send_error_report()` not in plan | Add to removal list at `manager.py:2046` |
| Empty checkpoint wipes frontend messages | Skip emission in `broadcast_checkpoint_event()` when `serialized` is empty |
| `broadcast_sync()` wrong for async contexts | Use `_broadcast_to_global()` directly from async code |

---

## Implementation Order

> Steps 1 and 2 are swapped. `persistence.py` just needs import updates and can be done before adding `serialize_message()` to event_bus.py.

### **Step 0 (PREREQUISITE)**: Create `daemon/utils.py`

> **Circular import fix.** `parse_think_tags` and `_THINK_PATTERN` live in `manager.py`, but `event_bus.py` and `persistence.py` both need to use them.

```bash
touch daemon/utils.py
```

Move from `daemon/manager.py`:
- `_THINK_PATTERN` (compiled regex, **line 249**) → `daemon/utils.py`
- `parse_think_tags()` (**lines 252–272**, 21 lines) → `daemon/utils.py`

Update imports:
- `daemon/manager.py`: `from .utils import parse_think_tags`
- `daemon/persistence.py`: `from daemon.utils import parse_think_tags` (replace line 166 import)
- `daemon/services/event_bus.py`: `from daemon.utils import parse_think_tags` (in `serialize_message()`)

---

### **Step 1**: `daemon/persistence.py` — Update imports, remove `compute_message_id()`

Steps:
1. Update `parse_think_tags` import to `from daemon.utils import parse_think_tags`
2. Rewrite `get_instance_messages()` to use `serialize_message()` and LangGraph `msg.id`
3. Remove `compute_message_id()` function
4. Add `_collect_timestamps()` helper if not already extracted

> `serialize_message` will be imported from `daemon.services.event_bus` — do this after Step 2 completes.

---

### **Step 2**: `daemon/services/event_bus.py` — Add `serialize_message()` + `broadcast_checkpoint_event()`, remove old methods

Steps:
1. Add `serialize_message()` at module level
2. Add `broadcast_checkpoint_event()` method on EventBus class
3. Delete the old methods (see Section 1.3 table)
4. Delete `STREAMING_EVENT_TYPES`, `LEGACY_EVENT_MAP`
5. Delete `_next_streaming_id()`, `cleanup_old()`
6. Audit `broadcast_sync()` — if no callers found, delete it entirely. If callers exist, simplify (remove streaming branch, keep `completed` event routing for dispatcher).

> After this step, update `persistence.py` to import `serialize_message` from `daemon.services.event_bus`.

---

### **Step 3**: `daemon/manager.py` + `daemon/task_processor.py` — Full migration

> This is the largest and most complex step. All `MessageService` call sites must be migrated before deleting the file.

**Manager.py sub-steps:**
1. Update `parse_think_tags` import to `from .utils import parse_think_tags`
2. Import `serialize_message` from `daemon.services.event_bus`
3. In `enqueue_message()`: replace `compute_message_id()` with `str(uuid.uuid4())`
4. In `_process_message_with_tracking()`:
   - Remove streaming broadcast code blocks (Section 2.1)
   - Replace streaming loop with checkpoint broadcast (Section 2.2)
   - Remove remaining `compute_message_id()` usage
5. In `_process_child_completion_and_notify_parent()`:
   - Remove call to `self._event_bus.create_child_completed_event()` (BUG fix — was duplicate)
   - Migrate DB write from `on_child_completion_report()` inline
6. In `_send_error_report()`:
   - Remove call to `self._message_service.on_child_error_report()`
   - Remove `self._event_bus.create_child_failed_event()` at line 2046
7. Remove `MessageService` instantiation and `self._message_service` declaration from `manager.py`

**task_processor.py sub-steps:**
8. At line 171: Replace `await self._message_service.on_assistant_message_completed(...)` with inline DB write
9. Audit fallback `_event_repo` path at lines 129-140 — clean up orphaned event creation
10. Remove `MessageService` instantiation and `self._message_service` declaration from `task_processor.py`

---

### **Step 4**: Delete `daemon/services/message_service.py`

Only after Step 3 is complete. Verify no remaining references:
```bash
grep -r "MessageService\|on_assistant_message_completed\|on_child_completion_report\|on_child_error_report\|on_user_message_stored" daemon/ --include="*.py"
```

---

### **Step 4.5**: Audit `send_message()` call sites

`send_message()` (`manager.py:751-867`) uses `ainvoke()` which produces no streaming events. Document as SSE-invisible if it's used for programmatic/API calls only:
```bash
grep -rn "send_message" daemon/ --include="*.py"
```

---

### **Step 5**: `daemon/api.py` — Rewrite SSE endpoint

Rewrite `stream_events()` using the pattern in Section 4.1. Delete `format_sse_event()`.

---

### **Step 6**: `daemon/message_models.py` — Clean up

Delete: `SSEEventPayload`, `SSEEventDelta`, `SSEEventStatus`, `UnifiedMessage`
Keep: `MessageRole`, `ToolCallInfo`

---

### **Step 7**: Frontend: `frontend/src/app/models/index.ts` — Update interfaces

1. Change `Message.message_id` → `Message.id`
2. Change `MessageResponse.message_id` → `MessageResponse.id`
3. Delete SSE-specific types (Section 11.2)
4. Add simplified `SSEEvent` type (Section 11.3)

---

### **Step 8**: Frontend: `frontend/src/app/services/sse.service.ts` — Rewrite

Full rewrite per Section 9. Remove all delta handling. Add `messages` signal.

---

### **Step 9**: Frontend: `frontend/src/app/pages/chat/chat.component.ts` — Simplify

1. Delete delta-processing effect (lines 80–349)
2. Replace with checkpoint effect (Section 10.2)
3. Delete title update effect (lines 362–376) and error handling effect (lines 379–387)
4. Evaluate fallback `isSending` reset effect (lines 352–359)
5. Remove `message_id`-based lookups and HTTP merge logic
6. Update `trackBy` to use `message.id`

---

### **Step 10**: Frontend: Update remaining `message_id` references

| File | Change |
|------|--------|
| `chat-interface.component.ts` | `trackBy`: `message.message_id` → `message.id` |
| `sse.service.spec.ts` | Any mock fixture `message_id` fields → `id` |

```bash
grep -r "message_id" frontend/src --include="*.ts" -l
```

---

### **Step 11**: Update tests

See Testing Plan section above.

---

## Pre-Implementation Verification

Run these before starting to scope the impact:

```bash
# 1. daemon/utils.py doesn't exist yet
ls daemon/utils.py

# 2. Frontend message_id references
grep -r "message_id" frontend/src --include="*.ts" -l | wc -l

# 3. parse_think_tags() call sites
grep -rn "parse_think_tags" daemon/ --include="*.py"

# 4. TaskProcessor EventBus usage — find ALL create_*_event calls
grep -rn "create_processing_started_event\|create_processing_completed_event\|create_processing_failed_event\|create_error_event\|create_instance_completed_event\|create_child_completed_event\|create_child_failed_event" daemon/ --include="*.py"

# 5. MessageService call sites
grep -rn "self._message_service\|on_assistant_message_completed\|on_child_completion_report\|on_child_error_report" daemon/ --include="*.py"

# 6. send_message() call sites
grep -rn "send_message" daemon/ --include="*.py"

# 7. ResponseDispatcher integration
grep -rn "_broadcast_to_global\|subscribe_all" daemon/sources/

# 8. broadcast_sync() callers — delete if none
grep -rn "broadcast_sync" daemon/ --include="*.py"

# 9. _send_error_report() second EventBus call
grep -n "create_child_failed_event" daemon/manager.py

# 10. _create_completion_events() call sites
grep -rn "_create_completion_events" daemon/ --include="*.py"

# 11. process_and_complete() EventBus usage
grep -n "create_instance_completed_event\|broadcast_streaming_event" daemon/manager.py
```

---

## Accepted Regressions

The following behavior changes are intentional and accepted:

| Regression | Rationale |
|-------------|-----------|
| No real-time token streaming during LLM inference | Project focuses on long-running tasks; correctness over real-time feedback |
| `created_at` is `None` during SSE streaming | Timestamps only populated when loading from REST API after completion |
| `Last-Event-ID` reconnection support dropped | Simplifies SSE endpoint; can be re-added with checkpoint sequence numbers |
| `send_message()` bypasses SSE entirely | Used for programmatic/API calls, not user-facing streaming |
| Large message list sent on each checkpoint | Acceptable for current scale; diff mode can be added later |
| Some `EventKind` enum values become dead code | Doesn't break anything; can clean up later |
