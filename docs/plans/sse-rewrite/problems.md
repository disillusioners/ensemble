# Current State Problems

## 1. Duplicate `message_received` (Audit Gap)

- Written to DB inside `enqueue_message()` transaction (`session.add(event)` at line 968) with `kind=MESSAGE_RECEIVED`
- **Not broadcast via SSE** — no call to `EventBus.create_message_received_event()` in `enqueue_message()`
- `message_service.py:on_child_completion_report()` emits `message_received` via SSE separately (for child report delivery to parent)
- The DB write is for audit/analytics only — not part of the real-time stream
- After this rewrite, child report delivery uses checkpoint events instead (parent emits when it processes the report)

## 2. Wrong `message_id` on Streaming Events

- `tool_call`, `content_chunk`, `thinking` events use the **user's** message_id
- They should use the **assistant's** message_id
- Root cause: `manager.py:_process_message_with_tracking()` passes `message_id` param (user's) to all `broadcast_streaming_event()` calls

## 3. Inconsistent ID Computation

- `compute_message_id()` uses `(instance_id, role, content[:100])` — a deterministic hash
- For user messages: computed once at queue time (`manager.py:919`)
- For assistant messages: **ID changes** as content accumulates during streaming (`manager.py:1272`)
- REST API (`persistence.py:193`) uses final content → IDs **never match** streaming

## 4. Complex Event System

- **10 event types** in `EventKind` enum (`repositories/event/models.py:13-23`)
- **4 streaming event types** (`event_bus.py:22`)
- **14+ event types handled** in frontend `sse.service.ts` (lines 85-452)
- Frontend `models/index.ts` defines ~20 types for SSE alone (lines 91-186)
- Frontend `sse.service.ts` is **537 lines** for event handling
- Plus: `task_processor.py` call sites for EventBus convenience methods not documented in PR boundary

## 5. Double `CHILD_COMPLETED` Event Creation (BUG)

- `_create_completion_events()` (manager.py:1850) creates a raw `Event(kind=CHILD_COMPLETED)` in the DB
- Then `_process_child_completion_and_notify_parent()` (manager.py:1943) calls `event_bus.create_child_completed_event()` which creates **another** Event row for the same thing
- After rewrite: only checkpoint events are used — this duplication is eliminated

## 6. Duplicate SSE `error` Listener (BUG)

- `sse.service.ts` registers **two** `addEventListener('error', ...)` handlers (lines 300–333 and 418–452)
- They handle different envelope formats, indicating inconsistent backend error formatting
- After rewrite: single event model eliminates this

## 7. Multi-Node Update Silent Message Loss (BUG)

- `manager.py` lines 1166–1171 only capture the **last** node's message
- If multiple nodes emit messages in the same step (possible in complex graphs), messages are **silently lost**
- Current code: `latest_msg = data["agent"]["messages"][-1]`
- This rewrite fixes it by accumulating ALL nodes' messages
