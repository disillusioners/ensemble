# Phase 1 Concurrency Fixes Review - 2026-04-05

## Context
Reviewed 3 commits on `feature/concurrency-model-fixes` branch implementing P1 critical concurrency fixes.

## Key Findings

### Critical Issues Found
1. **Waiter notification gap** (commit 8902288): `release_by_instance_sync()` does NOT notify waiters. If an instance is terminated while holding a project lock, queued jobs waiting via `wait_for_lock()` will hang indefinitely.
2. **Incomplete to_thread wrapping** (commit b54f3d1): Only wrapped `_queue_repository` calls, missed 6 `_instance_repository` and `_project_repository` calls in the same `_process_queue()` method.
3. **Test breakage** (commit dd67d80): `bash` tool changed from sync to async, but `tests/test_tools.py` uses sync `bash.invoke()` which will raise `NotImplementedError`.

### Architecture Pattern
- `terminate_instance()` is sync but needs async lock release — the sync fallback is pragmatic but creates tech debt
- Alternative: `asyncio.create_task()` to schedule async cleanup from sync context
- SQLite operations are fast (<1ms) so missed to_thread calls are low practical impact

### Deprecation Tracking
- `daemon/graph.py` lines 211, 254 still use deprecated `get_event_loop()`
- Should be changed to `get_running_loop()` for consistency
