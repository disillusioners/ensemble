# Phase 1: CompletionRegistry Implementation

## Summary
Implemented core infrastructure for synchronous agent invoke-and-wait pattern.
Commit: 781f650

## Key Architecture Decisions

### Thread Safety
- `threading.Lock` for all dict access (not asyncio.Lock — complete() can be called from worker threads)
- `asyncio.Event` for async wait (not threading.Event — caller is on event loop)
- `call_soon_threadsafe(event.set)` for cross-thread event notification

### Deadlock Prevention
- `asyncio.Semaphore(WORKER_POOL_SIZE - 1)` caps concurrent invoke_agent_and_wait calls
- Semaphore is acquired on event loop (yields properly), blocking in async sense only
- Ensures at least 1 worker thread stays free for agent-as-tool instances

### Buffered Completions
- `complete()` before `register()` stores result in `_buffered` dict
- `register()` checks `_buffered` and immediately sets event if pre-completed
- Handles tiny race window between spawn and register

### Signal Points (Hook Points)
- **child_reports.py**: Signal A (line 559) root instance completing, Signal B (line 604) child completing after commit
- **error_reporting.py**: Signal after Session block exits (line 245), before Step 4 enqueue

### File Locations
- `daemon/services/completion_registry.py` — NEW, core registry
- `daemon/utils.py` — invoke_agent_and_wait() at end of file
- `daemon/manager.py` — registry init (line 512), event loop (line 537), cleanup task (line 542), pool size (line 695)

## Gotchas
- `_try_terminate_orphan()` uses `asyncio.ensure_future()` (fire-and-forget) — acceptable for best-effort cleanup
- `cleanup_stale()` buffered safety valve: clears ALL buffered entries if >100
- error_reporting signal is OUTSIDE the `with Session` block (after auto-commit on block exit)
