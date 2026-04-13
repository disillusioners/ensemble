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

### 1. Critical Issues (must fix before implementing)

> ⚠️ **Added from code review.** The following issues were identified by comparing the plan against the actual codebase.

#### C1. ResponseDispatcher will break — Section 3.3a fix is incomplete

The dispatcher (`dispatcher.py:178`) filters for `event_type == "completed"` and expects fields `data.get("source")` and `data.get("content")` — fields that come from `UnifiedMessage.to_dict()` in the current `on_assistant_message_completed()` → `create_event()` path.

Checkpoint events have **completely different payload structure** — `{instance_id, messages[], checkpoint_id}`. The dispatcher's `_handle_event()` will never extract `source` or `content` from a checkpoint event.

**Required**: Keep emitting a lightweight `completed` lifecycle event (via `create_event()` → `_broadcast_to_global()`) specifically for the dispatcher, alongside checkpoints for SSE:

```python
# In manager.py post-streaming section, after all messages are processed:
await self._event_bus.broadcast_sync(
    instance_id=instance_id,
    event_type="completed",
    data={
        "source": source,  # from existing context
        "content": final_message_content,
        "message_type": "final",
        "instance_id": instance_id,
    }
)
```

This is a **minimal** `completed` event — just enough for the dispatcher. All SSE-specific fields (`UnifiedMessage`, deltas, etc.) are removed.

#### C2. `on_assistant_message_completed()` call is in `task_processor.py`, NOT `manager.py`

The plan's Section 3.1 says *"in post-streaming logic (~line 1410)"* but **there is no such call in `manager.py`**. The actual call site is `task_processor.py:171`:

```python
await self._message_service.on_assistant_message_completed(...)
```

The `ProcessMessageProcessor` calls it after `_process_message_with_tracking()` returns.

**Action**: Update Section 3.1 and Section 7 to reference `task_processor.py:171` instead. The DB persistence logic in `on_assistant_message_completed()` must be moved into `task_processor.py:ProcessMessageProcessor.process()`. After migration, `task_processor.py:171` becomes an inline DB write.

#### C3. `_broadcast_to_global()` signature mismatch in Section 3.3a

The plan proposes:
```python
await self._broadcast_to_global(instance_id, "checkpoint", event)
```

But the actual signature is:
```python
async def _broadcast_to_global(self, instance_id, event_type, event_id=None, message_id=None, delta=None, data=None)
```

Passing `event` (a dict) as the third positional argument maps to `event_id`, not `data`. This silently produces events with `event_id=<dict>` and `data=None`.

**Fix**: Change to:
```python
await self._broadcast_to_global(instance_id, "checkpoint", data=event)
```

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

#### 1.2 Add `serialize_message()` module-level function (NEW)

> ⚠️ This function does not currently exist. It is being **added** as a new module-level helper.

> ⚠️ **Circular import hazard**: `parse_think_tags` lives in `manager.py`, but `event_bus.py` already imports `manager.py`. A direct top-level import would be circular. See **Step 0** (Implementation Order) for the correct fix: move `parse_think_tags` and `_THINK_PATTERN` to `daemon/utils.py` **before** implementing this function.

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
    
    # Thinking extraction (5 paths, matching manager.py patterns)
    thinking = None
    if hasattr(msg, 'additional_kwargs'):
        kwargs = msg.additional_kwargs or {}
        thinking = kwargs.get("reasoning_content") or kwargs.get("thinking")
    if not thinking and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
        thinking = msg.reasoning_content
    # Direct thinking attribute (Claude models)
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
        "id": getattr(msg, 'id', None) or str(uuid.uuid4()),  # Fallback needed — LangGraph msg.id can be None
        "role": role,
        "content": content_str,
        "thinking": thinking,
        "thinking_extracted": thinking_extracted,
        "tool_calls": tool_calls,
        "created_at": None,                    # Filled from checkpoint timestamps in persistence.py; None in SSE path (frontend shows no timestamp during streaming)
    }
```

> ⚠️ **`msg.id` can be `None`**: LangGraph message IDs can be `None` for certain message types or providers. The `getattr(..., 'id', None) or str(uuid.uuid4())` fallback is required to avoid runtime errors. The plan's `broadcast_checkpoint_event()` example at line 349 also has this fallback, but it belongs in `serialize_message()` as the authoritative location.

> ⚠️ **`created_at` note**: In the SSE streaming path (checkpoint events), `created_at` will be `None` because checkpointing does not include timestamps. Timestamps are only populated when loading from the REST API via checkpoint history. Frontend should display no timestamp (or a placeholder) during active streaming. This is an accepted regression for correctness.

#### 1.3 Code to DELETE from event_bus.py

| Lines | What | Why |
|-------|------|-----|
| 22 | `STREAMING_EVENT_TYPES` constant | No more streaming events |
| 25-28 | `LEGACY_EVENT_MAP` | No more legacy compat |
| 82-94 | `create_message_received_event()` | Replaced by checkpoint |
| 96-106 | `create_processing_started_event()` | Replaced by checkpoint |
| 108-120 | `create_processing_completed_event()` | Replaced by checkpoint |
| 122-134 | `create_processing_failed_event()` | Replaced by checkpoint (MISSING from original plan) |
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
| 522-558 | `broadcast_sync()` | Keep but simplify — **must remove call to deleted `broadcast_streaming_event()` and add checkpoint event routing for message events**. Currently maps event types to `EventKind` and routes streaming events. After streaming events are removed, `broadcast_sync()` will attempt to call the deleted `broadcast_streaming_event()`. Concrete changes: remove streaming event branch, keep lifecycle event routing (INSTANCE_STARTED, etc.), add checkpoint event routing for message events. |

---

### 2. Backend: `daemon/manager.py` — `_process_message_with_tracking()`

**Location**: Lines **991–1479** (method signature at 991, body 1001–1479)
> ⚠️ The plan previously said "1001–1406" — this was wrong. The `finally` block ends at line 1406, but post-streaming logic continues to line 1479. All the following changes span the full method range.

**What to change**:

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
all_state_messages: list = []  # M7: Must initialize before loop

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

> **M8 — Behavior change**: `stream_mode=["updates"]` drops token-level streaming entirely. Users will see no output until a node completes (30-60+ seconds). This is an accepted regression per the project's long-running task focus.

**Important**: We accumulate messages from the `astream` output directly — **no `graph.aget()` call** inside the loop. This avoids the redundant DB read that the council flagged.

#### 2.3 Remove post-streaming logic after `finally` block (lines 1407–1479)

> ⚠️ This section was missing from the original plan. It must also be addressed.

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

#### 2.5 Duplicate `message_received` in `enqueue_message()` — plan incorrectly marked as fixed

Section 2.5 says *"Already fixed (lines 984-991 removed)."* But the actual code at lines 958-975 still creates `Event(kind=MESSAGE_RECEIVED)` via `session.add(event)`. Lines 984-991 are `return AsyncMessageResult(...)` — nothing was removed.

**Decision**: The `MESSAGE_RECEIVED` Event row stays for audit/analytics. No change needed in `enqueue_message()`. The plan's claim of this being "already fixed" was incorrect — no action required.

---

### 3. Backend: `daemon/manager.py` — completion/report handling

**What to change**:

#### 3.1 Remove `MessageService.on_assistant_message_completed()` calls

The `message_completed` and `processing_completed` events are no longer needed.
Checkpoint events replace them.

**The actual call site is in `task_processor.py`, NOT `manager.py`** (C2):

Find and remove:
- `task_processor.py:171`: `await self._message_service.on_assistant_message_completed(...)` — migrate DB write inline here
- `self._message_service.on_assistant_message_completed()` — in post-streaming logic (~line 1410) — **does not exist**, ignore
- `self._event_bus.create_processing_completed_event()` — called by MessageService
- `self._event_bus.create_message_received_event()` — for child reports

Child completion reports should still enqueue messages to parent instances,
but the SSE emission happens via checkpoint events, not explicit lifecycle events.

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

> ⚠️ **MISSING from original plan.** This method calls `self._message_service.on_child_error_report()`. After `message_service.py` is deleted, this call site must be updated.

`on_child_error_report()` emits a `message_received` event via EventBus. Since checkpoint events replace all lifecycle events, the SSE notification will happen automatically when the parent processes the error report message.

**Action**: Remove the call to `self._message_service.on_child_error_report()` from `_send_error_report()`. The error report message is already persisted to the DB — the parent will pick it up on its next checkpoint.

#### 3.3a Update `broadcast_checkpoint_event()` to notify global subscribers

> ⚠️ **CRITICAL — Missing from original plan.** The `ResponseDispatcher` (`daemon/sources/dispatcher.py:62,178`) subscribes to ALL events via `subscribe_all()` and filters for `event_type == "completed"` to route agent responses back to external sources (Telegram, Discord, etc.). The plan removes the `completed` event type entirely but provides **no replacement signal**.

After the rewrite, ALL external source routing will silently stop working.

**Required fix** — In `event_bus.py`, `broadcast_checkpoint_event()` must call `_broadcast_to_global()` alongside `notify()`:

```python
async def broadcast_checkpoint_event(self, instance_id: str, ...):
    # ... existing queue.put_nowait(event) ...
    self.notify(instance_id)
    # ADD THIS: Notify global subscribers (ResponseDispatcher, etc.)
    # ⚠️ C3: Must use data= keyword arg — positional arg maps to event_id
    await self._broadcast_to_global(instance_id, "checkpoint", data=event)
```

Also update `ResponseDispatcher._handle_event()` to filter for the new completion signal:

```python
# Before: if event_type == "completed":
# After:  if event_type in ("checkpoint", "message_completed") and event_data.get("reason") == "message_complete"
```

Alternatively, emit a dedicated `completed` event via `broadcast_sync()` at the end of processing to signal the dispatcher without duplicating the complex event creation logic.

**Recommended approach (C1)**: Keep emitting a lightweight `completed` event specifically for the dispatcher (not SSE). This preserves the dispatcher's expected payload structure `{source, content, message_type}` without polluting the SSE stream:

```python
# In manager.py or task_processor.py, after all processing completes:
await self._event_bus.broadcast_sync(
    instance_id=instance_id,
    event_type="completed",
    data={
        "source": source,  # from existing context
        "content": final_message_content,
        "message_type": "final",
        "instance_id": instance_id,
    }
)
```

The SSE stream only has `connected`/`checkpoint`/`error`/`keepalive`. The dispatcher receives `completed` via `_broadcast_to_global()`.

#### M2. Also remove `create_child_failed_event()` in `_send_error_report()` (line 2046)

The plan covers removing `on_child_error_report()` (line 2034) but misses the separate `create_child_failed_event()` call at line 2046. Add to removal list:
- `manager.py:2046`: `self._event_bus.create_child_failed_event(...)` — remove

#### 3.4 `TaskProcessor` usage of EventBus

> ⚠️ **MISSING from original plan.** `TaskProcessor` (created at manager.py:518) likely calls EventBus methods.

Search for all EventBus convenience method calls in `TaskProcessor` and related files:
```bash
grep -n "create_processing_started_event\|create_processing_completed_event\|create_processing_failed_event\|create_error_event" daemon/
```

**All of these call sites must be removed.** `TaskProcessor` should no longer emit lifecycle events — checkpoints replace them.

> ⚠️ **Also in `task_processor.py:218-226`**: The exception handler calls `create_error_event()`. This is **not** listed in the plan's removal table. If only the explicitly listed methods are removed, `create_error_event` will remain as dead code pointing to a method that may no longer exist. Add it to the removal list and specify what replaces it.

#### 3.5 `_create_completion_events()` (lines 1807–1859)

> ⚠️ **MISSING from original plan.** This method creates raw `Event` rows (not via EventBus) for `INSTANCE_COMPLETED` and `CHILD_COMPLETED`.

**Decision**: Keep this method. The Event table is used for audit/analytics (per Section 8). These raw inserts are the audit log, separate from the SSE stream.

After the rewrite, verify this method is still called from:
- `process_and_complete()` — for `INSTANCE_COMPLETED`
- `_process_child_completion_and_notify_parent()` — for `CHILD_COMPLETED`

> **Note**: After removing the duplicate `create_child_completed_event()` call in `_process_child_completion_and_notify_parent()`, `CHILD_COMPLETED` will only be written once (via this method) — fixing the existing bug.

---

### 4. Backend: `daemon/api.py` — SSE endpoint

**Location**: `stream_events()` at lines **822–951**; `format_sse_event()` at lines **954–1024** (separate functions)

**What to change**:

#### 4.1 Rewrite `stream_events()` (lines 822-951)

> ⚠️ **Reconnection support must be explicitly addressed.** The current SSE endpoint supports `Last-Event-ID` header for cursor-based reconnection (`api.py:862-880`). The simplified `stream_events()` below does not mention this feature.
>
> **Decision required**: Either (A) intentionally drop reconnection support (document as accepted regression), or (B) preserve it using checkpoint sequence numbers as cursors. If (B), the implementation must read `Last-Event-ID` from the request headers and seek to the appropriate checkpoint on reconnect.

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

> ⚠️ **`on_user_message_stored()` is dead code** — it is never called from production code. `enqueue_message()` creates the `MESSAGE_RECEIVED` Event directly via the raw model (manager.py:968). No migration needed for this method.

**DB persistence logic that must be moved before deletion:**

| Method | Lines | What it does | Where to move |
|--------|-------|-------------|---------------|
| `on_assistant_message_completed()` | 55–111 | Inserts assistant message record + tool message records | `task_processor.py:ProcessMessageProcessor.process()` (line 171 — actual call site, not manager.py) |
| `on_child_completion_report()` | 113–136 | Inserts child report message record to parent's queue | `manager.py:_process_child_completion_and_notify_parent()` |
| `on_child_error_report()` | 138–163 | Inserts child error report message record | `manager.py:_send_error_report()` (Section 3.3) |

**Deletion plan:**
1. Move DB write from `on_assistant_message_completed()` into `task_processor.py:171` (C2)
2. Move DB write from `on_child_completion_report()` into `_process_child_completion_and_notify_parent()`
3. Remove call to `on_child_error_report()` from `_send_error_report()` (Section 3.3)
4. Delete `message_service.py` entirely
5. Remove `MessageService` instantiation and all `self._message_service` call sites from `manager.py` AND `task_processor.py`

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

#### 10.1 Delete the main delta-processing effect (lines 80–349)

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

#### 10.3 Delete title update effect (lines 362–376)

> ⚠️ Line numbers corrected from plan's "351–359".
Title updates come from the instance API, not SSE.

#### 10.4 Delete error handling effect (lines 379–387)

> ⚠️ Line numbers corrected from plan's "362–376" (these were conflated with title effect).

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

#### 10.5 Fallback `isSending` reset effect (lines 352–359)

> ⚠️ **MISSING from original plan.** Small effect between the delta effect and title effect that resets `isSending` if streaming stopped but the flag is still true.

After the rewrite, determine if this is still needed. In the checkpoint-based model, `isSending` is reset when the first checkpoint arrives (Section 10.2), so this may be redundant.

#### 10.6 Delete `message_id`-based lookup logic (line 99)

Current: `let msgIndex = updated.findIndex(m => m.message_id === delta.message_id)`
New: No merging needed — `messages` signal is replaced entirely on each checkpoint.

#### 10.7 Delete HTTP message merge logic (lines 511–528)

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
| `manager.py` | 1057 | `current_assistant_msg_id = compute_message_id(...)` | Delete entire line |
| `manager.py` | 1272-1274 | `current_assistant_msg_id = compute_message_id(instance_id, "assistant", accumulated_assistant_content)` | Delete |
| `message_service.py` | 14 | `from daemon.persistence import compute_message_id` | File deleted |
| `message_service.py` | 70 | `compute_message_id(instance_id, "assistant", content)` | File deleted |
| `persistence.py` | 23-37 | `def compute_message_id(...)` | Delete function |
| `persistence.py` | ~193 | `compute_message_id(instance_id, role, content)` | Use `msg.id` directly |

> ⚠️ `persistence.py` also imports `parse_think_tags` from `manager.py` at line 166. After Step 0 moves `parse_think_tags` to `daemon/utils.py`, update this import too.

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
| `daemon/` (TaskProcessor audit) | Remove EventBus lifecycle calls in `TaskProcessor` | TBD — audit first |

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
| Tests for `parse_think_tags` in manager context | Move to `daemon/utils.py` (or keep if imported from utils) |

### New Unit Tests Needed

1. `serialize_message()` — verify all message types serialize correctly (HumanMessage, AIMessage, ToolMessage, SystemMessage)
2. `broadcast_checkpoint_event()` — verify queue delivery with tool_outputs
3. `get_instance_messages()` — verify LangGraph IDs are used and match between SSE and REST API
4. SSE endpoint (`/instances/{id}/events`) — verify only `connected`, `checkpoint`, `error`, `keepalive` events are emitted
5. `serialize_message()` with `tool_outputs` map — verify tool outputs are embedded in assistant message's `tool_calls[]` field
6. `broadcast_checkpoint_event()` — concurrent emissions don't cause race conditions in queue/notify
7. **`serialize_message()` with `msg.id = None`** — verify fallback to `uuid.uuid4()` works (HIGH — prevents runtime errors)
8. **`serialize_message()` with all thinking formats** — verify all 5 extraction paths including direct `msg.thinking` attribute
9. **`ResponseDispatcher` routing after rewrite** — verify external sources (Telegram, Discord) still receive responses via `_broadcast_to_global()` calls

### Integration Tests Needed

1. Send message → verify SSE delivers checkpoint with LangGraph IDs
2. Send message → verify GET /messages returns same IDs as SSE
3. Tool call → verify checkpoint includes tool_calls with outputs
4. Multi-turn → verify messages accumulate correctly across checkpoints
5. Reconnect → verify initial checkpoint is sent on new SSE connection
6. Child agent completes → verify parent receives checkpoint with child's report message (not a separate `child_completed` event)
7. Error during processing → verify SSE emits `error` event, not `processing_failed`
8. **Multi-turn message ID consistency** — Send msg1 → verify checkpoint → send msg2 → verify BOTH messages have consistent IDs → refresh → verify REST API matches SSE
9. **Backend restart → SSE reconnect** — Verify client reconnects and receives current state from checkpoint after backend crash
10. **External source routing (Telegram/Discord)** — Verify ResponseDispatcher receives checkpoint events and routes responses correctly

### Paths NOT Previously Covered by Tests

> ⚠️ **MISSING from original plan.** These code paths were not adequately tested and should be added:

| Path | Why |
|------|-----|
| `_create_completion_events()` | Raw Event creation for audit log — ensure it still runs after rewrite |
| `_send_error_report()` after `on_child_error_report()` removal | Error reports must still be persisted |
| `TaskProcessor` + EventBus integration | Remove lifecycle events from task processing path |
| `_process_child_completion_and_notify_parent()` after duplicate removal | CHILD_COMPLETED should only be written once (fixing the existing bug) |

### 2. Missing Details (should add to plan)

> ⚠️ **Added from code review.** The following items were missing or incomplete.

#### M1. `enqueue_message()` still creates `MESSAGE_RECEIVED` Event row — plan incorrectly marked as fixed

Section 2.5 says *"Already fixed (lines 984-991 removed)."* But the actual code at lines 958-975 still creates `Event(kind=MESSAGE_RECEIVED)` and calls `session.add(event)`. Lines 984-991 are `return AsyncMessageResult(...)` — nothing was removed.

**Decision**: The `MESSAGE_RECEIVED` Event row stays — it is the audit/analytics entry. No change needed in `enqueue_message()`. The plan's Section 2.5 should be updated to reflect this.

#### M2. `_send_error_report()` also calls `create_child_failed_event()` at line 2046

Section 3.3 covers removing `on_child_error_report()` (line 2034) but misses `create_child_failed_event()` at line 2046. This EventBus call will break when `create_child_failed_event()` is deleted per Section 1.3.

**Action**: Add to removal list: `_send_error_report()` line 2046 — remove `self._event_bus.create_child_failed_event()` call.

#### M3. `ProcessMessageProcessor` has direct `_event_repo` fallback path

`task_processor.py:129-140` has a fallback when `_event_bus` is None — it uses `_event_repo.create_event()` directly. After removing lifecycle events, this path also needs cleanup or it creates orphaned events.

**Action**: Audit `task_processor.py:129-140` in Step 3.4. If `_event_bus` is always set in production, this path is dead code — delete it. If it can fire, update to use the new `completed` event approach (Section C1 above).

#### M4. No plan for `EventKind` enum cleanup

`EventKind` has 9 values. After the rewrite, only `ERROR`, `INSTANCE_COMPLETED`, `CHILD_COMPLETED`, and `MESSAGE_RECEIVED` (audit) are still written. The others (`PROCESSING_STARTED`, `PROCESSING_COMPLETED`, `PROCESSING_FAILED`, `CHILD_FAILED`, `MESSAGE_COMPLETED`) become dead enum values.

**Decision**: Keep all `EventKind` values for now (they don't break anything). Document which are still actively written vs. deprecated in a comment.

#### M5. `send_message()` (manager.py:751) uses `graph.ainvoke()` — bypasses SSE entirely

`send_message()` uses `ainvoke()` which produces **no streaming events at all**. If used for agent-to-agent communication (via `tools/instance.py:267`), those messages have no SSE output after the rewrite.

**Decision required**: Either (A) add checkpoint emission to `send_message()`, or (B) document it as SSE-invisible. Option B is recommended since `send_message()` is used for programmatic/API calls, not user-facing streaming.

#### M6. `broadcast_sync()` has no known callers

`event_bus.py:522` — I found zero call sites for `broadcast_sync()` outside its definition. The plan says to "simplify" it, but if it's truly unused, it should be deleted entirely.

**Action**: Search before implementing Step 1. If no callers found, delete `broadcast_sync()` entirely.

#### M7. `all_state_messages` variable not initialized in Section 2.2

The plan's code uses `all_state_messages.extend(node_messages)` but never declares `all_state_messages = []` before the streaming loop. This produces a `NameError` at runtime.

**Fix**: Add before the streaming loop:
```python
all_state_messages: list = []
```

#### M8. `stream_mode=["updates"]` drops token-level streaming explicitly

The plan replaces the `"messages"` stream mode with `"updates"`. This means **no streaming tokens during inference** — only full messages after each node completes. This is intentional per the plan's principles ("correctness over real-time feedback") but should be **explicitly documented** as a behavior change.

Add to Section 2.2:
> **Behavior change**: `stream_mode=["updates"]` drops token-level streaming entirely. Users will see no output until a node completes. This is an accepted regression per the project's long-running task focus.

#### M9. `manager.get_messages()` doesn't exist in Section 4.1

The proposed SSE endpoint calls `manager.get_messages(instance_id)` but `InstanceManager` has no such method. The correct approach is:

```python
# Get checkpointer from instance
instance = manager.get_instance(instance_id)
checkpointer = instance.checkpointer  # or however the checkpointer is accessed
current_messages = await persistence.get_instance_messages(checkpointer, instance_id)
```

**Action**: Audit `InstanceManager` to determine how to access the checkpointer for a running instance, or add a `get_messages()` wrapper method.

#### M10. `create_processing_started_event()` in `task_processor.py:125` needs a replacement

The plan removes this EventBus call but doesn't specify what replaces it. Currently, the frontend relies on `processing_started` to set `isStreaming = true`. After the rewrite, the first `checkpoint` event implicitly signals processing started, but there's a gap: from user submission to first checkpoint could be 30-60+ seconds.

**Decision**: No replacement needed — the `checkpoint` event implicitly signals processing started. Frontend `isStreaming = true` is set when first `checkpoint` arrives (Section 10.2). If the frontend needs immediate visual feedback, add a `processing_started` SSE event **only** (not the full lifecycle event system). Simpler fix: set `isStreaming = true` on `send_message()` call, `false` on first checkpoint.

#### M11. Line numbers for `parse_think_tags` in Step 0

Step 0 says `parse_think_tags()` is at "~line 252". It is actually lines **252–272** (21 lines including `return content, None`). `_THINK_PATTERN` is at line **249**.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Circular import: `event_bus.py` → `manager.py` for `parse_think_tags` | **Step 0**: Move `parse_think_tags` + `_THINK_PATTERN` to `daemon/utils.py` before any other changes |
| LangGraph `msg.id` might be `None` for some message types | **Added to plan**: `getattr(msg, 'id', None) or str(uuid.uuid4())` fallback in `serialize_message()` |
| Thinking extraction has 4 provider-specific paths | **Updated**: Port all 5 paths to `serialize_message()` including `msg.thinking` direct attribute (missing from original plan) |
| `tool_outputs` map needs ToolMessages that are excluded from output | Build map before filtering, pass to `serialize_message()` |
| Frontend field rename `message_id` → `id` breaks all references | Search entire frontend for `.message_id` and update all 72+ references across 5 files |
| No real-time feedback during LLM inference (30-60s) | Acceptable for long-running task focus. Documented explicitly (Section M8). |
| Large message list on each checkpoint | Acceptable for now. Add diff mode later if needed. |
| `created_at` is `None` during SSE streaming | Accept regression. Frontend shows no timestamp during streaming; REST API populates after reload. |
| `TaskProcessor` calls EventBus lifecycle methods | Must audit and remove in Step 3.4 before deleting `create_*_event` methods. Include `create_error_event` — missing from original removal list. |
| Post-streaming logic in `_process_message_with_tracking()` (lines 1407–1479) was missing from plan | Now documented in Section 2.3. DB writes from `on_assistant_message_completed()` must be migrated inline. |
| `ResponseDispatcher` loses event stream (external sources silent) | **C1**: Keep lightweight `completed` event via `broadcast_sync()` for dispatcher. Do NOT route checkpoint events to dispatcher — wrong payload structure. |
| `broadcast_sync()` still calls deleted `broadcast_streaming_event()` | **Added to Section 1.4**: Remove streaming branch, add checkpoint routing. |
| `Last-Event-ID` reconnection support silently dropped | **Added to Section 4.1**: Explicit decision required — document as regression or implement cursor-based seek. |
| `send_message()` inconsistency with new system | **M5**: Document as SSE-invisible (recommended). `ainvoke()` bypasses SSE. |
| `on_assistant_message_completed()` call is in `task_processor.py`, not `manager.py` | **C2**: Update Sections 3.1 and 7 to reference `task_processor.py:171`. Migrate DB logic there. |
| `_broadcast_to_global()` signature mismatch in Section 3.3a | **C3**: Use `data=event` keyword arg, not positional. Positional maps to `event_id`, producing `event_id=<dict>` silently. |
| `create_child_failed_event()` call in `_send_error_report()` not in plan | **M2**: Add to removal list at `manager.py:2046`. |
| `all_state_messages` variable not initialized in Section 2.2 | **M7**: Add `all_state_messages: list = []` before streaming loop. |
| `manager.get_messages()` doesn't exist in Section 4.1 | **M9**: Use `persistence.get_instance_messages()` with checkpointer from instance. |

---

## Implementation Order

### **Step 0 (PREREQUISITE)**: Create `daemon/utils.py`

> ⚠️ **Circular import fix.** `parse_think_tags` and `_THINK_PATTERN` live in `manager.py`, but `event_bus.py` and `persistence.py` both need to use them. Moving them to a shared utils module breaks the circular dependency.

```bash
touch daemon/utils.py
```

Move from `daemon/manager.py`:
- `_THINK_PATTERN` (compiled regex, **line 249**) → `daemon/utils.py`
- `parse_think_tags()` (**lines 252–272**, 21 lines) → `daemon/utils.py`

Update imports:
- `daemon/manager.py`: `from .utils import parse_think_tags` (keep using it; it still works)
- `daemon/persistence.py`: `from daemon.utils import parse_think_tags` (replace line 166 import)
- `daemon/services/event_bus.py`: `from daemon.utils import parse_think_tags` (in `serialize_message()`)

---

### 1. **`daemon/services/event_bus.py`** — Add `serialize_message()` + `broadcast_checkpoint_event()`, remove old methods

Steps:
1. Add `serialize_message()` at module level (imports `parse_think_tags` from `daemon.utils`)
2. Add `broadcast_checkpoint_event()` method on EventBus class
3. Delete the old methods (see Section 1.3 table)
4. Delete `STREAMING_EVENT_TYPES`, `LEGACY_EVENT_MAP`
5. Delete `_next_streaming_id()`, `cleanup_old()`
6. **M6**: Audit `broadcast_sync()` — if no callers found, delete it entirely instead of maintaining. If callers exist, simplify (remove streaming branch, keep `completed` event routing for dispatcher).
7. Keep `broadcast_sync()` with `completed` event routing if dispatcher integration is needed (C1)

### 2. **`daemon/persistence.py`** — Use `serialize_message()`, remove `compute_message_id()`

Steps:
1. Update `parse_think_tags` import to `from daemon.utils import parse_think_tags`
2. Rewrite `get_instance_messages()` to use `serialize_message()` and LangGraph `msg.id`
3. Remove `compute_message_id()` function
4. Add `_collect_timestamps()` helper if not already extracted

### 3. **`daemon/manager.py` + `task_processor.py`** — Full migration (streaming → checkpoints + MessageService removal)

> ⚠️ This is the largest and most complex step. All `MessageService` call sites must be migrated before deleting the file.

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
   - Remove call to `self._message_service.on_child_error_report()` (Section 3.3)
   - **M2**: Also remove `self._event_bus.create_child_failed_event()` at line 2046
7. Remove `MessageService` instantiation and `self._message_service` declaration from `manager.py`

**task_processor.py sub-steps (C2 — this is where `on_assistant_message_completed()` is actually called):**
8. At line 171: Replace `await self._message_service.on_assistant_message_completed(...)` with inline DB write (migrate from `message_service.py:55-111`)
9. **M3**: Audit fallback `_event_repo` path at lines 129-140 — clean up orphaned event creation
10. Remove `MessageService` instantiation and `self._message_service` declaration from `task_processor.py`

### 4. **Delete `daemon/services/message_service.py`** — Only after Step 3 is complete

Verify no remaining references:
```bash
grep -r "MessageService\|on_assistant_message_completed\|on_child_completion_report\|on_child_error_report\|on_user_message_stored" daemon/ --include="*.py"
```

### 4.5 Audit `send_message()` call sites

> ⚠️ **MISSING from original plan.** `send_message()` (`manager.py:751-867`) is a synchronous-style method that also extracts thinking and builds tool_calls. If it's still called anywhere, it will have inconsistent behavior with the new system.

```bash
grep -rn "send_message" daemon/ --include="*.py"
```

Either update `send_message()` to use the new serialization approach, or document it as deprecated if superseded by the queue path.

### 5. **`daemon/api.py`** — Rewrite SSE endpoint

Rewrite `stream_events()` (lines 822–951) using the pattern in Section 4.1.
Delete `format_sse_event()` (lines 954–1024).

### 6. **`daemon/message_models.py`** — Clean up

Delete: `SSEEventPayload`, `SSEEventDelta`, `SSEEventStatus`, `UnifiedMessage`
Keep: `MessageRole`, `ToolCallInfo`

### 7. **Frontend: `frontend/src/app/models/index.ts`** — Update interfaces

1. Change `Message.message_id` → `Message.id`
2. Delete SSE-specific types (Section 11.2)
3. Add simplified `SSEEvent` type (Section 11.3)

### 8. **Frontend: `frontend/src/app/services/sse.service.ts`** — Rewrite

Full rewrite per Section 9. Remove all delta handling. Add `messages` signal.

### 9. **Frontend: `frontend/src/app/pages/chat/chat.component.ts`** — Simplify

1. Delete delta-processing effect (lines 80–349)
2. Replace with checkpoint effect (Section 10.2)
3. Delete title update effect (lines 362–376) and error handling effect (lines 379–387)
4. Evaluate fallback `isSending` reset effect (lines 352–359) — may be redundant
5. Remove `message_id`-based lookups and HTTP merge logic
6. Update `trackBy` to use `message.id` if needed

### 10. **Frontend: Update remaining `message_id` references**

| File | Change |
|------|--------|
| `chat-interface.component.ts` | `trackBy`: `message.message_id` → `message.id` |
| `sse.service.spec.ts` | Any mock fixture `message_id` fields → `id` |

Run to verify all removed:
```bash
grep -r "message_id" frontend/src --include="*.ts" -l
```

### 11. **Update tests**

See Testing Plan section above.

---

## Pre-Implementation Verification

Run these before starting to scope the impact:

1. **`daemon/utils.py` doesn't exist yet** — Confirm: `ls daemon/utils.py` should return "No such file". If it exists, Step 0 becomes unnecessary.

2. **Frontend `message_id` references** — Count all files that need updating:

   ```bash
   grep -r "message_id" frontend/src --include="*.ts" -l | wc -l
   grep -r "\.message_id" frontend/src --include="*.ts" -c
   ```

3. **`parse_think_tags()` call sites** — After moving to utils, update all call sites:
   ```bash
   grep -rn "parse_think_tags" daemon/ --include="*.py"
   ```

4. **`TaskProcessor` EventBus usage** — Find and audit all EventBus lifecycle event calls:
   ```bash
   grep -rn "create_processing_started_event\|create_processing_completed_event\|create_processing_failed_event\|create_error_event" daemon/ --include="*.py"
   ```
   **Include `create_error_event`** — the plan's original removal list was missing this.

5. **`MessageService` call sites** — Verify all call sites are addressed before deletion:
   ```bash
   grep -rn "self._message_service\|on_assistant_message_completed\|on_child_completion_report\|on_child_error_report" daemon/ --include="*.py"
   ```

6. **`send_message()` call sites** — Audit whether it needs updating for the new serialization:
   ```bash
   grep -rn "send_message" daemon/ --include="*.py"
   ```

7. **`ResponseDispatcher` integration** — Verify the dispatcher will still receive events after checkpoint rewrite:
   ```bash
   grep -rn "_broadcast_to_global\|subscribe_all\|ResponseDispatcher" daemon/sources/
   ```
   `broadcast_checkpoint_event()` must call `_broadcast_to_global()` or external sources (Telegram, Discord) will silently stop routing.

8. **`broadcast_sync()` callers** — M6: Verify if any code calls `broadcast_sync()`. If none, delete it entirely:
    ```bash
    grep -rn "broadcast_sync" daemon/ --include="*.py"
    ```

9. **`task_processor.py` audit** — C2: Find all EventBus calls in task_processor.py (not just manager.py):
    ```bash
    grep -n "create_processing_started_event\|create_processing_completed_event\|create_processing_failed_event\|create_error_event\|_event_repo\|MessageService" daemon/task_processor.py
    ```
    This is the actual location of `on_assistant_message_completed()` call (line 171).

10. **`_send_error_report()` second EventBus call** — M2: Verify `create_child_failed_event()` at line 2046:
    ```bash
    grep -n "create_child_failed_event" daemon/manager.py
    ```

6. **Thinking extraction provider coverage** — Confirm all 5 extraction paths are needed for your LLM providers. If any path is unused, document which providers need which paths and remove dead paths.

7. **`_create_completion_events()` call sites** — Verify this method is still called after the rewrite:
   ```bash
   grep -rn "_create_completion_events" daemon/ --include="*.py"
   ```
