# Quick Fix: Narrowed Internal Source Skip in Dispatcher

**Date**: 2026-04-17
**File**: `daemon/sources/dispatcher.py`
**Commit**: `21ad4e1`

## Issue
The dispatcher was using `source_id.startswith("internal_")` to decide which sources to skip during progressive dispatch. This incorrectly skipped `internal_agent:parent_id` sources, which are legitimate sources that should dispatch normally. Only `internal_report:` and `internal_error_report:` should be skipped because they represent internal communication that shouldn't go to external adapters.

This was the root cause of the child-parent source propagation bug — after a child agent reported back, the parent's subsequent messages with `internal_agent:` source were silently dropped.

## Fix
Changed both `dispatch_message()` and `dispatch_completed()`:
```python
# Before (too broad):
if source_id.startswith("internal_"):
    return

# After (correctly narrow):
if source_id in ("internal_report", "internal_error_report"):
    return
```

## Testing
- 7 new tests added to `tests/test_progressive_dispatch.py`
- Total 32 progressive dispatch tests all pass
- 704 existing tests pass (no regressions)

## Pattern
When filtering source IDs by prefix, be precise about which prefixes to match. Using `startswith("internal_")` was too broad — `internal_agent:` is a real dispatchable source, while only `internal_report:` and `internal_error_report:` are internal-only communication that should be skipped.
