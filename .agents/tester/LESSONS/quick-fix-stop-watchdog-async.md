# Quick Fix: _stop_watchdog async mismatch

**Date:** 2026-04-05
**Commit:** b1c3c65
**Phase:** Phase 4 — Graceful shutdown + SSE heartbeat
**Session:** phase4-integration

## Issue
During integration testing, the daemon crashed during graceful shutdown. The `_stop_watchdog()` method in `daemon/manager.py:2448` was defined as a synchronous method but was being awaited in the shutdown sequence at `manager.py:2388`.

## Root Cause
The shutdown sequence in `manager.py` uses a list of awaitable steps:
```python
steps = [
    ("stop_sources", self.stop_sources(timeout=grace_period)),
    ("cancel_active_requests", self._cancel_all_active_requests()),
    ("wait_inflight", self._wait_for_inflight(grace_period)),
    ("cancel_consumers", self._cancel_consumers()),
    ("stop_watchdog", self._stop_watchdog()),      # <-- awaited but sync
    ("shutdown_broadcaster", self.broadcaster.shutdown()),
]
```

Each step is awaited with `await coro`, but `_stop_watchdog()` was a regular `def` returning `None` instead of a coroutine.

## Fix
Changed method signature:
```python
# Before
def _stop_watchdog(self) -> None:

# After  
async def _stop_watchdog(self) -> None:
```

## Verification
- Daemon starts and runs for 30 seconds without errors
- Graceful shutdown completes cleanly with all 7 steps executing
- All 36 manager tests pass
- 73/74 SSE/shutdown tests pass (1 pre-existing flaky test)

## Lesson
When building an ordered shutdown sequence with awaitable steps, ensure ALL methods in the sequence are `async def`. A sync method in an await chain will either raise a TypeError or silently fail.
