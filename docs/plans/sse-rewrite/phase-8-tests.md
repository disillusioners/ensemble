# Phase 8: Tests & Polish

---

## Goals

1. Update tests for removed/changed functionality
2. Add new tests for checkpoint-based system
3. Verify end-to-end functionality

---

## 1. Tests to REWRITE or DELETE

| Test File | Action |
|-----------|--------|
| `tests/test_events.py` | Rewrite for new event types |
| `tests/integration/test_sse_streaming.py` | Rewrite for checkpoint model |
| `tests/integration/test_streaming_errors.py` | Rewrite for error handling |
| `tests/integration/test_streaming_performance.py` | Rewrite for checkpoint perf |
| Tests for `compute_message_id` | Delete (function removed) |
| Tests for `MessageService` | Delete (file removed) |
| Tests for `EventBus.broadcast_streaming_event` | Replace with `broadcast_checkpoint_event` |
| Tests for `EventBus` convenience methods | Delete (`create_processing_started_event`, `create_child_completed_event`, etc.) |

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

### 2.17 Multi-node update — NO message loss (BUGFIX)

Test when LangGraph emits `{"agent": {...}, "tools": {...}}` to ensure no messages are dropped. This fixes the bug documented in problems.md.

### 2.18 Frontend disconnect/reconnect

SSE connects to empty instance → receives only `connected` → sends message → receives checkpoint.

### 2.19 Checkpoint serialization perf

50 messages with tool_calls < 100ms.

### 2.20 Child instance SSE test

**Scenario**: Parent instance spawns a child instance, child completes with a report.

**Expected**: Parent's SSE stream should eventually receive a checkpoint event containing the child's report via the checkpoint mechanism.

**Verification**:
1. Create parent instance
2. Connect SSE client to parent
3. Send message that triggers child spawn
4. Wait for child to complete
5. Verify parent's SSE receives checkpoint with child's completed state

### 2.21 Concurrent SSE clients test

**Scenario**: Two SSE clients connect to the same instance simultaneously.

**Expected**: Both clients receive identical checkpoint events.

**Verification**:
1. Create instance
2. Connect SSE client A
3. Connect SSE client B
4. Send message to instance
5. Verify both clients receive same checkpoint sequence
6. Verify ordering is consistent between clients

### 2.22 Final-state safety net verification

**Scenario**: Streaming completes but SSE client may have missed intermediate checkpoints.

**Expected**: After streaming completes, a final checkpoint with authoritative state (from `aget_state()`) is emitted to ensure client has current state.

**Verification**:
1. Connect SSE client to instance
2. Send message that triggers streaming
3. After streaming ends, verify final checkpoint arrives
4. Verify final checkpoint contains complete state from `aget_state()`
5. Verify this final event is distinguishable (e.g., contains `is_final: true` or similar marker)

### 2.23 ResponseDispatcher integration test

**Scenario**: Verify `ResponseDispatcher` still receives `completed` events after EventBus rewrite.

**Expected**: External message sources (webhook, polling, etc.) continue to receive responses.

**Verification**:
1. Mock external source with callback
2. Send message that triggers agent completion
3. Verify ResponseDispatcher calls callback with completed event
4. Verify event contains correct message data

### 2.24 `broadcast_checkpoint_event()` — deduplication safety

**Scenario**: Same checkpoint is emitted multiple times (edge case from LangGraph retries).

**Expected**: Duplicate checkpoints are handled gracefully (either deduplicated or client handles idempotently).

**Verification**:
1. Mock LangGraph to emit same checkpoint twice
2. Verify SSE stream handles duplicates
3. Verify client (frontend) can handle duplicate checkpoint events

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

---

## 5. Critical E2E Test — Full Flow Verification

**This is the entire point of the SSE rewrite. Must pass before merge.**

### Test: User Message → SSE Checkpoint → Messages Match REST API

**Scenario**: Complete end-to-end flow verifying SSE and REST API consistency.

**Steps**:
1. Start backend server
2. Create new instance via REST API
3. Connect SSE client to instance
4. Send message via REST API POST
5. Wait for SSE `checkpoint` events to stream
6. Compare SSE messages with REST API `GET /instances/{id}/messages`
7. Verify they are identical (same messages, same order, same IDs)

**Expected**:
- SSE `checkpoint` events contain all messages in correct order
- REST API `/messages` endpoint returns same messages
- Message IDs match between SSE and REST
- No messages missing from either channel

**Test Code Structure**:
```python
@pytest.mark.asyncio
async def test_full_flow_sse_matches_rest_api():
    # 1. Create instance
    instance_id = await create_instance(project="test")
    
    # 2. Connect SSE client
    async with SSEClient(f"/instances/{instance_id}/events") as sse:
        sse_messages = []
        
        async def collect_events():
            async for event in sse:
                if event.type == "checkpoint":
                    sse_messages.extend(event.data.get("messages", []))
        
        # 3. Start collecting SSE events in background
        collect_task = asyncio.create_task(collect_events())
        
        # 4. Send message via REST
        response = await post_message(instance_id, "Hello, agent")
        assert response.status == 202
        
        # 5. Wait for completion (with timeout)
        await wait_for_completion(instance_id, timeout=60)
        
        # 6. Fetch REST API messages
        rest_messages = await get_instance_messages(instance_id)
        
        # 7. Cancel SSE collection
        collect_task.cancel()
        
        # 8. Verify they match
        assert len(sse_messages) == len(rest_messages)
        for i, (sse_msg, rest_msg) in enumerate(zip(sse_messages, rest_messages)):
            assert sse_msg["id"] == rest_msg["id"], f"Message {i} ID mismatch"
            assert sse_msg["content"] == rest_msg["content"], f"Message {i} content mismatch"
            assert sse_msg["type"] == rest_msg["type"], f"Message {i} type mismatch"
```

**Verification Checklist**:
- [ ] SSE receives `connected` event first
- [ ] SSE receives `checkpoint` event(s) with messages
- [ ] REST API `/messages` returns same messages
- [ ] Message IDs are consistent
- [ ] Message order is consistent
- [ ] Final state is consistent

**Manual Verification**:
```bash
# Terminal 1: Start server
./dev.sh

# Terminal 2: Connect SSE client
curl -N http://localhost:8079/instances/test-instance/events

# Terminal 3: Send message
curl -X POST http://localhost:8079/instances/test-instance/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello"}'

# Compare SSE output with REST API
curl http://localhost:8079/instances/test-instance/messages
```
