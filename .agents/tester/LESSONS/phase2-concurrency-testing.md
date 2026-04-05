# Phase 2 Concurrency Testing Lessons

## Quick Fix: get_queue_stats() Return Type
- **Date**: 2026-04-05
- **Commit**: aa75121
- **Issue**: `get_queue_stats()` returned raw dict but callers expected `QueueStats` dataclass
- **Root Cause**: Return type mismatch between repository layer and manager
- **Pattern**: Always check return type when refactoring data access layers
- **Files**: daemon/manager.py, tests/integration/test_message_queue_e2e.py

## Quick Fix: Deprecated asyncio.get_event_loop() in Python 3.14
- **Date**: 2026-04-05
- **Commit**: 734c32b
- **Issue**: `asyncio.get_event_loop()` raises RuntimeError in Python 3.14 when called from thread without event loop
- **Root Cause**: Deprecated API used in test mock LLM callback; fails when LangGraph runs in thread pool executor
- **Fix**: Replace with `asyncio.get_running_loop()` + exception handling
- **Pre-existing**: This was NOT introduced by Phase 2 — existed before
- **Files**: tests/integration/test_message_queue_e2e.py

## Phase 2 Architecture Pattern: Semaphore + Timeout Nesting
- Correct pattern: `async with semaphore:` wrapping `async with asyncio.timeout():`
- This ensures semaphore is held during the full LLM call including timeout
- Buffer flush must be in `finally` block OUTSIDE the timeout context to preserve partial results
- Error handling order matters: `TimeoutError` handler must come before generic `Exception`

## Phase 2 Architecture Pattern: Fire-and-Forget with Error Visibility
- Use `run_coroutine_threadsafe()` for fire-and-forget from sync context
- Always add `add_done_callback()` with error logger — otherwise failures are silently swallowed
- Module-level `_log_future_error()` helper is clean pattern for this
