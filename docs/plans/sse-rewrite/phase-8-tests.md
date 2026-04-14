# Phase 8: Tests & Polish

---

## Goals

1. Update tests for removed/changed functionality
2. Add new tests for checkpoint-based system
3. Verify end-to-end functionality

---

## 1. Tests to DELETE

| Test File | What |
|-----------|------|
| Tests for `compute_message_id` | Delete (function removed) |
| Tests for `MessageService` | Delete (file removed) |
| Tests for `EventBus.broadcast_streaming_event` | Replace with `broadcast_checkpoint_event` |
| Tests for `format_sse_event` | Delete (function removed) |
| Tests for `EventBus` convenience methods | Delete (`create_processing_started_event`, `create_child_completed_event`, etc.) |
| Tests for `parse_think_tags` in manager context | Move to utils test |

---

## 2. New Unit Tests Needed

### 2.1 `serialize_message()` — all message types

Verify HumanMessage, AIMessage, ToolMessage, SystemMessage serialize correctly.

### 2.2 `serialize_message()` — thinking extraction (5 paths)

Verify all 5 extraction paths work:
1. `additional_kwargs.get("reasoning_content")`
2. `additional_kwargs.get("thinking")`
3. `msg.reasoning_content` attribute
4. `msg.thinking` attribute
5. `msg.content` as list with `type="reasoning"` blocks

### 2.3 `serialize_message()` with `msg.id = None`

Verify `_stable_message_id()` fallback produces consistent IDs across calls:
```python
# Call twice with same msg → same ID
id1 = serialize_message(msg)
id2 = serialize_message(msg)
assert id1["id"] == id2["id"]
```

### 2.4 `_stable_message_id()` determinism

- Same (role, content, tool_call_id) → same ID
- Different content → different ID
- Boundary cases: empty content, unicode, 200-char content limit

### 2.5 `broadcast_checkpoint_event()` — queue delivery

Verify messages are queued correctly with tool_outputs.

### 2.6 `broadcast_checkpoint_event()` — empty messages

Verify skip (no emission) when serialized list is empty.

### 2.7 `broadcast_checkpoint_event()` — concurrent emissions

Verify no race conditions.

### 2.8 `get_instance_messages()` — LangGraph IDs

Verify LangGraph IDs are used and match between SSE and REST API.

### 2.9 SSE endpoint events

Verify only `connected`, `checkpoint`, `error`, `keepalive` events are emitted.

### 2.10 `serialize_message()` with `tool_outputs` map

Verify tool outputs are embedded in assistant message's `tool_calls[]` field.

### 2.11 `ResponseDispatcher` routing after rewrite

Verify external sources still receive responses via `_broadcast_to_global()` calls.

### 2.12 `ResponseDispatcher` routing

Verify `completed` event includes `source` field.

### 2.13 `MessageService` deletion verification

Confirm all methods have zero callers before deleting file.

### 2.14 `_process_message_with_tracking()` `MessageResult` completeness

Verify returned `MessageResult` has all fields after refactoring.

### 2.15 `send_message()` still works

Test ainvoke path after `compute_message_id()` removal.

### 2.16 `broadcast_sync()` callers audit

Verify zero production callers before deletion.

### 2.17 Multi-node update event

Test when LangGraph emits `{"agent": {...}, "tools": {...}}` to ensure no messages are dropped.

### 2.18 Frontend disconnect/reconnect

SSE connects to empty instance → receives only `connected` → sends message → receives checkpoint.

### 2.19 Checkpoint serialization perf

50 messages with tool_calls < 100ms.

---

## 3. Verification Commands

```bash
# Run all tests
pytest tests/ -v

# Run specific test categories
pytest tests/unit/ -v
pytest tests/integration/ -v

# Verify frontend builds
cd frontend && npm run build

# Manual SSE test
# 1. Start backend: ./dev.sh
# 2. Start frontend: cd frontend && npm start
# 3. Open browser devtools, check SSE events
```

---

## 4. Final Smoke Tests

```bash
# 1. Verify SSE connects
curl -N http://localhost:8079/instances/test/events

# 2. Verify messages match between SSE and REST
# (Use browser devtools or curl to compare)

# 3. Verify error handling
# (Send invalid message, check error event)

# 4. Verify keepalive
# (Wait 30s, verify keepalive event)
```
