# Phase 4: Migrate SSE Events

## Objective

Replace the in-memory `EventBroadcaster` (per-instance `asyncio.Queue` + ring buffer) with a database-backed event system that reads from the `event` table and delivers via SSE. Events survive restart, and no client loses events during reconnection.

## Coupling

- **Depends on**: Phase 1 (event table schema)
- **Coupling type**: loose (only needs event table schema, not worker pool)
- **Shared files with other phases**: `daemon/repositories/event/`
- **Shared APIs/interfaces**: `EventRepository.get_events_since()`, `EventRepository.cleanup_old()` <!-- FIX: C2 -->
- **Why this coupling**: SSE migration only needs the event table to exist. It can run in parallel with Phases 2-3.

**Important**: This phase can execute **in parallel** with Phases 2 and 3, since it only depends on Phase 1.

## Context

### What Exists Today

| Component | Location | Problem |
|-----------|----------|---------|
| EventBroadcaster | `daemon/events.py` | Per-instance asyncio.Queue + ring buffer, lost on restart |
| SSE endpoint | `daemon/api.py:808-933` | Reads from asyncio.Queue, in-memory only |
| Event dataclass | `daemon/events.py` | Not persisted to DB |
| ResponseDispatcher | `daemon/dispatcher.py` | Subscribes to all events for routing replies |

### Current Event Types

| Type | Priority | Source |
|------|----------|--------|
| `error` | CRITICAL | Processing failure |
| `completed` | CRITICAL | Message processed |
| `status_changed` | HIGH | Processing started, retry |
| `message_queued` | HIGH | New message |
| `tool_call` | NORMAL | Tool execution |
| `tool_complete` | NORMAL | Tool finished |
| `content_chunk` | NORMAL | LLM streaming |
| `thinking` | LOW | Reasoning content |
| `keepalive` | LOW | Connection ping |
| `connected` | LOW | SSE established |
| `title_updated` | NORMAL | Instance title |
| `cancelled` | HIGH | Message cancelled |
| `error_report` | HIGH | Child failed |
| `retry_scheduled` | HIGH | Retry scheduled |

### New Event Table Types (from Phase 1)

| Type | Purpose |
|------|---------|
| `message_received` | New message enqueued |
| `processing_started` | Worker started processing |
| `processing_completed` | Processing done |
| `processing_failed` | Processing failed |
| `child_completed` | Child done |
| `child_failed` | Child failed |
| `instance_completed` | Instance done |
| `error` | Error event |

### Mapping: Old Events → New Events

| Old Event | New Event | Notes |
|-----------|-----------|-------|
| `message_queued` | `message_received` | Direct mapping |
| `status_changed` (processing) | `processing_started` | More specific |
| `completed` | `processing_completed` | Direct mapping |
| `error` | `processing_failed` | More specific |
| `content_chunk` | `content_chunk` | Keep as-is (streaming) |
| `thinking` | `thinking` | Keep as-is |
| `tool_call` | `tool_call` | Keep as-is |
| `tool_complete` | `tool_complete` | Keep as-is |
| `cancelled` | `cancelled` | Keep as-is |
| `error_report` | `child_failed` | More specific |
| `title_updated` | `title_updated` | Keep as-is |
| `connected` | (no DB event) | Connection-level only |
| `keepalive` | (no DB event) | Connection-level only |
| `retry_scheduled` | `retry_scheduled` | Keep as-is |

**Important**: Streaming events (`content_chunk`, `thinking`, `tool_call`, `tool_complete`) are NOT stored in the event table — they're too frequent. They continue to use the in-memory notification pattern for real-time delivery, but we add a **notification mechanism** to wake up SSE listeners.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create DBEventBroadcaster | New broadcaster that writes events to DB + notifies SSE via lightweight in-memory signal | `daemon/db_event_broadcaster.py` (new) |
| 2 | Create notification mechanism | Lightweight in-memory signal (asyncio.Event or threading.Condition) to wake SSE on new events | `daemon/db_event_broadcaster.py` (modify) |
| 3 | Update SSE endpoint | Read from event table instead of asyncio.Queue | `daemon/api.py` (modify) |
| 4 | Update ResponseDispatcher | Subscribe to DB events instead of in-memory broadcaster | `daemon/dispatcher.py` (modify) |
| 5 | Add event types to models | Add all event types to Event model (including streaming types that skip DB) | `daemon/repositories/event/models.py` (modify) |
| 6 | Implement reconnection | Use event table for reconnection instead of ring buffer | `daemon/api.py` (modify) |
| 7 | Hybrid streaming delivery | Keep in-memory for content_chunk/thinking, DB for lifecycle events | `daemon/db_event_broadcaster.py` (modify) |
| 8 | Write unit tests | Test event persistence, delivery, reconnection | `tests/message_queue_redesign/test_db_event_broadcaster.py` (new) |
| 9 | Write integration tests | Test SSE delivery with DB events | `tests/message_queue_redesign/test_sse_events.py` (new) |

## Key Files

### New Files

| File | Purpose |
|------|---------|
| `daemon/db_event_broadcaster.py` | DB-backed event broadcaster with in-memory notification |
| `tests/message_queue_redesign/test_db_event_broadcaster.py` | Broadcaster unit tests |
| `tests/message_queue_redesign/test_sse_events.py` | SSE integration tests |

### Modified Files

| File | Changes |
|------|---------|
| `daemon/api.py` | SSE endpoint reads from event table |
| `daemon/dispatcher.py` | Subscribe to DB events |
| `daemon/repositories/event/models.py` | Add all event types |
| `daemon/manager.py` | Use DBEventBroadcaster instead of EventBroadcaster |

## Constraints

1. **Streaming latency**: `content_chunk` events must have <50ms latency (can't wait for DB round-trip)
2. **No lost events**: All lifecycle events must survive restart
3. **Multi-client safe**: Multiple SSE clients (e.g., browser tabs) for the same instance must each receive all events — cursor-based delivery via `Last-Event-ID` ensures this <!-- FIX: C2 -->
4. **Backward compatible**: SSE clients should not need changes (same event format)
5. **Event ordering**: Events must be delivered in creation order per instance

## Design: Hybrid Event Delivery

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    DBEventBroadcaster                         │
│                                                               │
│  Lifecycle events (persisted):                              │
│    message_received, processing_started/completed,           │
│    child_completed, instance_completed, error                │
│    → INSERT INTO event → notify SSE listeners                │
│                                                               │
│  Streaming events (in-memory only):                         │
│    content_chunk, thinking, tool_call, tool_complete         │
│    → asyncio.Event signal → SSE picks up immediately         │
│                                                               │
│  ┌──────────────────────┐  ┌──────────────────────────┐     │
│  │  _db_path            │  │  _streaming_channels     │     │
│  │  (all lifecycle)     │  │  dict[inst_id → Queue]   │     │
│  │  EventRepository     │  │  (content_chunk etc)     │     │
│  └──────────────────────┘  └──────────────────────────┘     │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  _notifications: dict[inst_id → asyncio.Event]       │   │
│  │  Lightweight signal to wake SSE poll loop             │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Event Flow

```
Manager produces event:
    ↓
    Is it a streaming event? (content_chunk, thinking, tool_call, tool_complete)
    ├── YES → Put in in-memory Queue → notify SSE listener
    └── NO  → INSERT INTO event table → notify SSE listener
    ↓
SSE listener wakes up:
    ↓
    1. Read streaming events from in-memory Queue (if any)
    2. Read lifecycle events from DB (WHERE delivered = FALSE)
    3. Merge by timestamp, yield to SSE client
    4. Mark DB events as delivered
```

<!-- FIX: C2 — cursor-based delivery, no delivered boolean. FIX: W7 — explicit ordering algorithm. -->
### SSE Poll Loop (New)

```python
async def instance_events(request, instance_id: str):
    broadcaster = app.state.db_event_broadcaster
    event_repo = app.state.event_repository
    
    # Each client tracks its own cursor position
    # Reconnection: replay missed events from DB using Last-Event-ID
    last_event_id = int(request.headers.get("Last-Event-ID", 0))
    if last_event_id > 0:
        missed_events = event_repo.get_events_since(instance_id, last_event_id)
        for event in missed_events:
            yield format_sse_event(event)
            last_event_id = max(last_event_id, event.id)
    
    # Main poll loop
    notification = broadcaster.get_notification(instance_id)
    
    while True:
        # Wait for notification or timeout
        try:
            await asyncio.wait_for(notification.wait(), timeout=30)
            notification.clear()
        except asyncio.TimeoutError:
            yield format_sse_keepalive()
            continue
        
        # Read lifecycle events from DB using cursor (not boolean flag)
        db_events = event_repo.get_events_since(instance_id, last_event_id)
        
        # Read from in-memory streaming queue
        streaming_events = broadcaster.get_streaming_events(instance_id)
        
        # Merge using explicit ordering algorithm:
        # 1. DB events are ordered by auto-increment id (monotonic)
        # 2. Streaming events have no DB id — assign temporary negative ids
        # 3. Interleave by created_at timestamp, streaming events first if same timestamp
        all_events = []
        for e in db_events:
            all_events.append(("db", e.created_at, e))
        for e in streaming_events:
            all_events.append(("streaming", e.created_at, e))
        all_events.sort(key=lambda x: (x[1], 0 if x[0] == "streaming" else 1))
        
        for kind, _, event in all_events:
            yield format_sse_event(event)
            if kind == "db":
                last_event_id = max(last_event_id, event.id)
        
        # No mark_delivered needed — each client tracks its own cursor
```

<!-- FIX: C2 — cursor-based delivery, each client tracks its own position -->
### Reconnection (New)

Instead of in-memory ring buffer:
1. Client sends `Last-Event-ID` header with the id of the last event it received
2. Server queries: `SELECT * FROM event WHERE instance_id = ? AND id > ? ORDER BY id ASC`
3. Replays missed events
4. Continues with normal poll loop from that cursor position

**Events never lost** — they persist in DB until cleaned up by `cleanup_old()` (configurable, default 24 hours). Multiple clients can independently read all events because there's no per-client `delivered` flag.

## Testing Strategy

### Unit Tests

| Test | Scenario |
|------|----------|
| `test_lifecycle_event_persisted` | Lifecycle events written to event table |
| `test_streaming_event_not_persisted` | content_chunk NOT written to DB |
| `test_notification_mechanism` | SSE listener wakes on new event |
| `test_cursor_based_delivery` | Client reads events after its cursor position <!-- FIX: C2 --> |
| `test_event_ordering` | Events delivered in creation order per instance |

### Integration Tests

| Test | Scenario |
|------|----------|
| `test_sse_receives_lifecycle_events` | SSE client gets lifecycle events from DB |
| `test_sse_receives_streaming_events` | SSE client gets streaming events from memory |
| `test_sse_reconnection` | Client reconnects and gets missed events |
| `test_events_survive_restart` | Events available after app restart |
| `test_multiple_sse_clients` | Multiple clients for same instance each get all events independently (cursor-based) <!-- FIX: C2 --> |

## Deliverables

- [ ] `daemon/db_event_broadcaster.py` — hybrid event delivery (cursor-based, no delivered boolean) <!-- FIX: C2 -->
- [ ] SSE endpoint reads from event table with cursor-based positioning
- [ ] ResponseDispatcher uses new event source
- [ ] Reconnection via event table cursor (no more ring buffer)
- [ ] Streaming events via in-memory notification (no latency regression)
- [ ] Explicit merge/ordering algorithm for DB + streaming events <!-- FIX: W7 -->
- [ ] Periodic event cleanup (configurable TTL, default 24h) <!-- FIX: C2 -->
- [ ] Unit tests for DBEventBroadcaster
- [ ] Integration tests for SSE delivery (including multi-client)
- [ ] All existing SSE tests pass (no regression)
