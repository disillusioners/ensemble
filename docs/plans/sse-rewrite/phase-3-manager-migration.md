# Phase 3: Manager Migration

> **Largest and most complex step.** `MessageService` call sites must be deleted before deleting the file.

---

## Goals

1. Remove ALL streaming event broadcasts from `_process_message_with_tracking()`
2. Add checkpoint broadcast after each node update
3. Remove `compute_message_id()` usage
4. Remove `MessageService` instantiation and calls
5. Simplify child completion and error reporting

---

## 1. `daemon/manager.py` — `_process_message_with_tracking()`

**Location**: Lines **991–1479**

### 1.1 Remove ALL streaming event broadcasts

Delete these blocks:

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

### 1.2 Add checkpoint broadcast after each node update

Replace the streaming loop (around line 1156) with:

```python
# NOTE: all_state_messages is reset per-call to prevent unbounded growth.
# Initial state comes from get_instance_messages() on SSE connect.
all_state_messages: list = []
event_index = 0  # Sequence counter for checkpoint_id

async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
    if isinstance(event, tuple):
        mode, data = event
    else:
        mode = "updates"
        data = event
    
    if mode == "updates":
        # Accumulate messages from ALL nodes — do NOT break early.
        # LangGraph may emit updates from multiple nodes in a single event
        # (e.g., parallel execution). Dropping subsequent nodes was a bug.
        any_new = False
        for node_name, node_data in data.items():
            node_messages = node_data.get("messages", [])
            if node_messages:
                any_new = True
                # Key by msg.id to handle modifications
                msg_index = {m.id: i for i, m in enumerate(all_state_messages) if hasattr(m, 'id')}
                for m in node_messages:
                    if hasattr(m, 'id') and m.id in msg_index:
                        all_state_messages[msg_index[m.id]] = m  # Replace existing
                    else:
                        all_state_messages.append(m)
        
        if not any_new:
            continue
        
        # Build tool_outputs from ALL messages (including ToolMessages)
        tool_outputs = {}
        for m in all_state_messages:
            if hasattr(m, 'tool_call_id'):
                tc_id = getattr(m, 'tool_call_id', '')
                if tc_id:
                    content = getattr(m, 'content', '') or ''
                    tool_outputs[tc_id] = str(content) if not isinstance(content, str) else content
        
        # Serialize all messages (full state for frontend replacement)
        from langchain_core.messages import ToolMessage
        serialized = [
            serialize_message(m, tool_outputs) 
            for m in all_state_messages
            if not isinstance(m, ToolMessage)
        ]
        
        # Build a simple sequence number from event index (avoids depending on config)
        # NOTE: LangGraph's config does NOT contain checkpoint_id after streaming.
        # checkpoint_id is only available in the checkpoint metadata, not in the
        # runtime config passed to astream(). We use an incrementing sequence instead.
        sequence_id = f"seq_{event_index}"
        event_index += 1

        await self._event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=serialized,  # list[dict] — pre-serialized, not BaseMessage objects
            checkpoint_id=sequence_id,
            tool_outputs=tool_outputs,
        )
```

> **CRITICAL**: Do NOT add a `break` inside the node loop. Accumulate from ALL nodes before emitting.

### 1.3 Simplified post-streaming code

After removing streaming broadcasts, the post-loop section simplifies to:

```python
# After the streaming for-loop completes (or raises)
try:
    # Broadcast final completion event for ResponseDispatcher
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
finally:
    self._active_instances.discard(instance_id)
```

**Post-loop safety net**: Keep `graph.aget_state()` as a fallback after the streaming loop.

### 1.6 Final-state safety net after streaming loop

After the `async for event in graph.astream(...)` loop completes, add a final checkpoint
to guard against accumulation bugs where `all_state_messages` diverges from actual
LangGraph state:

```python
# After the streaming for-loop completes
# Emit final authoritative checkpoint from LangGraph state
try:
    final_state = await graph.aget_state(config)
    if final_state and final_state.values:
        final_messages = final_state.values.get("messages", [])
        # Rebuild serialized messages (same logic as in-loop)
        final_tool_outputs = {}
        for m in final_messages:
            if hasattr(m, 'tool_call_id'):
                tc_id = getattr(m, 'tool_call_id', '')
                if tc_id:
                    content = getattr(m, 'content', '') or ''
                    final_tool_outputs[tc_id] = str(content) if not isinstance(content, str) else content
        
        final_serialized = [
            serialize_message(m, final_tool_outputs)
            for m in final_messages
            if not isinstance(m, ToolMessage)
        ]
        
        # Use a special final checkpoint_id
        final_sequence_id = f"seq_{event_index}_final"
        
        await self._event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=final_serialized,  # list[dict] — pre-serialized, not BaseMessage objects
            checkpoint_id=final_sequence_id,
            tool_outputs=final_tool_outputs,
        )
except Exception as e:
    logger.warning(f"Final state fetch failed for {instance_id}: {e}")
```

> **Note**: `messages` passed to `broadcast_checkpoint_event()` must be pre-serialized
> `list[dict]` (the output of `serialize_message()`), NOT `list[BaseMessage]`.

### 1.7 Remove `compute_message_id()` imports and usage

| Line | What |
|------|------|
| 21 | `from .persistence import compute_message_id` — remove |
| 919 | `message_id = compute_message_id(instance_id, "user", message)` — replace with `str(uuid.uuid4())` |
| 1057 | `current_assistant_msg_id = compute_message_id(...)` — remove entirely |
| 1272-1274 | `current_assistant_msg_id = compute_message_id(...)` — remove entirely |

### 1.8 Remove `MessageService` instantiation

Delete:
- `MessageService` instantiation
- `self._message_service` declaration

---

## 2. `daemon/task_processor.py` — Remove MessageService Calls

### 2.1 At line 171

Replace:
```python
await self._message_service.on_assistant_message_completed(...)
```

With:
```python
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

### 2.2 DELETE dead code

- The `elif self._event_repo:` fallback branch at lines 129-140 — **dead code**

### 2.3 Remove `MessageService` instantiation

Delete `MessageService` instantiation and `self._message_service` declaration.

---

## 3. `_process_child_completion_and_notify_parent()`

Remove:
- `self._message_service.on_child_completion_report()` call
- `self._event_bus.create_child_completed_event()` call (BUG: duplicate)

**Keep**:
- `_create_completion_events()` — for audit log

---

## 4. `_send_error_report()`

Remove:
- `self._message_service.on_child_error_report()` call
- `self._event_bus.create_child_failed_event()` call at line 2046

---

## 5. Verification

```bash
# Verify no more MessageService references
grep -rn "self._message_service\|on_assistant_message_completed\|on_child_completion_report\|on_child_error_report" daemon/ --include="*.py"

# Verify no more compute_message_id in manager
grep -rn "compute_message_id" daemon/manager.py

# Verify no more old event broadcasts in manager
grep -rn "broadcast_streaming_event" daemon/manager.py
```
