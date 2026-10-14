# Phase 3 Implementation — Migration & Service Layer Cleanup

## Key Learnings

1. **Migration-first ordering is critical**: Task 3.0 (migration) MUST complete before 3.1 (C5 fallback removal). Without the migration backfill, removing the fallback would leave existing orphan jobs unprocessable. The migration is the safety net.

2. **Deterministic UUIDs enable safe SQL migrations**: Because Phase 1 used `uuid.uuid5(NAMESPACE_DNS, "__system_default__")` to generate the system project ID, we can hardcode `71931ae0-0f25-5fbf-853b-2a78cc978d7e` in the migration without needing a SELECT lookup.

3. **Test fixture cascade**: Adding normalization to `enqueue()` caused 120+ pre-existing test failures because those tests didn't set `SYSTEM_DEFAULT_PROJECT_ID`. The fix required updating conftest.py in `tests/job_queue/` AND updating individual test assertions that expected `project_id is None`.

4. **Dual-binding issue persists in fixtures**: `normalize_project_id()` imports `SYSTEM_DEFAULT_PROJECT_ID` at module level, creating a local binding. Test fixtures must patch BOTH `constants.SYSTEM_DEFAULT_PROJECT_ID` AND `project_normalizer.SYSTEM_DEFAULT_PROJECT_ID`.

5. **Assert-after-normalize pattern**: Adding `assert project_id is not None` immediately after `normalize_project_id()` is a strong defense-in-depth. If it fires, it means the normalization function has a bug — not a missing call site.

6. **Graceful degradation for event bus**: When removing `_global_event`, `wait_for_job(project_id=None)` must degrade to polling (sleep + return False) rather than raising an error, because `JobProcessor._process_loop()` may pass None in edge cases.

7. **DeadLetterService `or ""` was silent corruption**: The `project_id=job.project_id or ""` pattern silently converted None to empty string. Replacing with assertions turns this into a loud failure if normalization is ever bypassed.

## Files Changed (25 files, +2257/-183)
- daemon/migrations/versions/20260424_000001_backfill_null_project_ids.sql (new)
- daemon/services/job_processor.py (C5 removal)
- daemon/services/dispatch_event_bus.py (_global_event removal)
- daemon/services/job_queue_service.py (assert after normalize)
- daemon/services/dead_letter_service.py (or "" removal + assertions)
- daemon/services/job_retry_engine.py (type fix)
- daemon/services/retry_scheduler.py (documentation)
- 7 test files updated for fixture/assertion changes
- 3 new test files (migration, DLQ, integration)
