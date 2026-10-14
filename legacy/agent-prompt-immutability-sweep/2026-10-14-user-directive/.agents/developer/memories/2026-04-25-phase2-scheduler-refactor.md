# Phase 2 Scheduler Core Refactor — Implementation Experience

## Date: 2026-04-25

## Key Learnings

1. **Silent session failures**: Opencode sessions CAN report success without actually making changes. The first implementation session for Phase 2 claimed all changes were done, tests passed, etc. — but verification showed the file was completely unchanged. Always verify with a separate session after implementation.

2. **Double-callback bugs in refactoring**: When extracting helper methods that call callbacks (like `_execution_callback`), it's easy to introduce double-callback bugs. The pattern: helper calls callback on failure, then caller ALSO calls callback on failure. Must trace ALL code paths for callbacks after extraction.

3. **Semaphore lifecycle in async code**: When extracting semaphore acquire/release into helpers, trace every path:
   - Happy path: acquire → execute → release in finally
   - Timeout path: acquire fails → callback called → return (no release needed)
   - Skip path (reuse_instance): acquire → check → release → return (before execute)
   Each path must acquire/release exactly once.

4. **Review findings need verification too**: The first reviewer claimed a double-release bug that didn't exist (wrong function names, misunderstood control flow). A second verification session debunked it. But the second reviewer found a REAL double-callback bug. Don't blindly trust reviewers either.

## Architecture Notes

- `scheduler.py` reduced from 987 → ~855 lines (after final fixes)
- `_emit_scheduled_message()`: 37 lines (was 254)
- `_execute_trigger()`: ~40 lines (was 158)
- New helpers: `_acquire_execution_slot()`, `_execute_run()`, `_route_via_job_queue()`, `_execute_immediate()`
- Constants in `daemon/constants.py` with `SCHEDULER_` prefix
- Commit: `1cddc4e`
