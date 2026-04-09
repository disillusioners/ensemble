# Phase 4 Critical Path Gap — Last-Event-ID Reconnection Test Missing

**Date:** 2026-04-09  
**Phase:** Phase 4 — SSE Events Migration

## Issue Found

The critical path test specification required:
> Verify cursor-based reconnection works (`Last-Event-ID` header)

However, while cursor delivery tests exist, **no explicit Last-Event-ID header handling test was found**.

## Tests Found

### Cursor Delivery Tests (EXIST)
- `test_get_events_since_with_cursor` — Tests cursor-based event retrieval
- `test_cursor_advances_correctly` — Tests cursor advancement

### Missing Test
- `test_last_event_id_reconnection` — SSE Last-Event-ID header handling for reconnection scenarios

## Recommendation

Add a test for SSE reconnection with Last-Event-ID header:
```python
def test_last_event_id_reconnection():
    """Verify SSE clients can reconnect with Last-Event-ID and receive missed events."""
    # 1. Create EventBus
    # 2. Subscribe client, get some events
    # 3. Simulate disconnect
    # 4. Reconnect with Last-Event-ID header
    # 5. Verify only events after cursor are received
```

## Status

**Gap documented — Not blocking**  
All 34 Phase 4 tests pass. This is a coverage gap, not a functional failure.
