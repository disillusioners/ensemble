# Phase 3 Implementation — Service Layer Cleanup & Migration

## Key Learnings

1. **Migration BEFORE removal**: The migration (Task 3.0) MUST run before removing fallback code (Task 3.1+). This is critical — without backfilling existing NULL rows first, removing the fallback would leave orphan jobs permanently unprocessable.

2. **Deterministic UUID in SQL**: Hardcoding `71931ae0-0f25-5fbf-853b-2a78cc978d7e` in the migration (computed from `uuid5(NAMESPACE_DNS, "__system_default__")`) is correct because:
   - It matches what `ensure_system_default_project()` computes in Python
   - SQL migrations can't call Python's uuid5
   - It's stable across all environments

3. **Dual-binding fixture issue (recurrence)**: When `normalize_project_id()` does `from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID`, it captures `None` at import time. Test fixtures MUST patch BOTH `daemon.constants.SYSTEM_DEFAULT_PROJECT_ID` AND `daemon.services.project_normalizer.SYSTEM_DEFAULT_PROJECT_ID`. This was the same issue from Phase 2.

4. **Test cascading from enqueue() normalization**: Adding normalization to `enqueue()` means ALL tests that call enqueue() need `SYSTEM_DEFAULT_PROJECT_ID` set. This cascaded to 120+ tests in `tests/job_queue/`. The fix: autouse fixture in conftest.py. Lesson: When modifying a core service method, prepare for widespread test impact.

5. **DispatchEventBus graceful degradation**: Removing `_global_event` required `wait_for_job(project_id=None)` to degrade gracefully to polling (sleep timeout, return False) rather than crashing. The JobProcessor passes `project_id=None` in some paths.

6. **Dead letter `or ""` was a latent bug**: The `project_id=job.project_id or ""` in DeadLetterService silently corrupted data by storing empty strings instead of real project_ids. The assertion replacement (`assert job.project_id is not None`) turns silent corruption into a loud crash.

7. **Queue ID for orphan jobs**: Migration must also assign `queue_id` (not just `project_id`) to orphaned jobs, because the normal processing path requires both. System FIFO queue ID follows pattern `sys-fifo-{project_id}`.

## Architecture Changes
- Removed: C5 orphan fallback (28 lines), _global_event field+method, `or ""` DLQ hacks
- Added: Migration file, assertions in enqueue/DLQ, documentation comments for unreachable code
- Test infrastructure: autouse fixtures in conftest.py for SYSTEM_DEFAULT_PROJECT_ID

## Files Changed (25 files, +2257/-183)
- daemon/migrations/versions/20260424_000001_backfill_null_project_ids.sql (new)
- daemon/services/job_processor.py (C5 removed)
- daemon/services/dispatch_event_bus.py (global event removed)
- daemon/services/job_queue_service.py (assert added)
- daemon/services/dead_letter_service.py (or "" removed, asserts added)
- daemon/services/job_retry_engine.py (type fix)
- daemon/services/retry_scheduler.py (doc comment)
- tests/job_queue/ (6 test files updated for normalized behavior)
- tests/integration/test_migration.py (new, 16 tests)
- tests/integration/test_dlq_project_normalization.py (new, 6 tests)
