# Quick Fix: Progressive Dispatch Error Handling

**Date**: 2026-04-17
**File**: `daemon/sources/dispatcher.py`
**Commit**: `388d64c`

## Issue
The `dispatch_message()` method called `await adapter.send(outgoing)` without error handling. If an external source adapter (e.g., Telegram) raised an exception during progressive dispatch, it would propagate up and potentially crash the entire agent execution.

## Fix
Wrapped the adapter call in try/except within `dispatch_message()`:
```python
try:
    await adapter.send(outgoing)
except Exception as e:
    logger.warning(f"Progressive dispatch failed for source {source}: {e}")
    return  # Don't track as sent
```

## Testing
- 17 new tests added in `tests/test_progressive_dispatch.py`
- Test #10 specifically verifies that adapter exceptions don't break execution
- All 704 existing tests still pass (no regressions)

## Pattern
When adding progressive/real-time dispatch to any source adapter, always wrap the send call in error handling since the adapter's health is unpredictable.
