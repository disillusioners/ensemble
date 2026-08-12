# Quick Fixes Applied During Job Concurrency Fix E2E (2026-08-12)

Branch: `fix/job-concurrency-and-watchover-job-loss`

## Fix 1: Tool Registration Count Drift
- **Commit:** `a1715bac`
- **File:** `tests/job_queue/test_jober_watch_integration.py:686`
- **Root cause:** Commit `0a558dbe` (feat: add job visibility tools for Ari orchestrator) added 4 new tools (job_messages, job_tree, job_progress, job_inject) but the `test_tool_registration` assertion was not updated. Expected 17, actual 21.
- **Fix:** Updated assertion 17→21. Test-code only, 3 lines.
- **Verification:** Re-ran full job_queue pack (1518 passed, 0 failed).

## Fix 2: Title Generation Dual-Dispatch Assertions
- **Commit:** `8c71b862`
- **File:** `tests/unit/services/test_title_generation_trigger.py` (+37/-26)
- **Root cause:** Commit `a0fa7c1e` (2026-07-30, feat: initiative_message) added `_maybe_store_initiative_message` as a second `run_async_no_wait` dispatch in `_maybe_trigger_title_generation`. Tests still asserted `called_once()`. Pre-existing since initiative_message feature landed.
- **Fix:** Relaxed 7 assertions to `assert_called()` + 1 count to `== 2`. Left 2 assertions as `assert_called_once()` (they test the direct `_trigger_title_generation` method which legitimately has 1 dispatch). Test-code only.
- **Verification:** Re-ran c2_core_regression pack (40 failed → all pre-existing, 0 new).

## Fix 3: PostgreSQL Test Fixture Alignment
- **Commit:** `93478ed2`
- **Files:** `tests/postgres/test_nuclear_cleanup_zombie_pg.py`, `tests/postgres/test_report_lane_phase2_pg.py`
- **Root cause (a):** `_create_job` helper created `admission_state=active` JobItems without matching `JobLock` rows, hitting PG DEFERRABLE trigger `trg_job_queue_items_active_lock_guard`.
- **Root cause (b):** `test_pg_process_message_blocked_by_cross_system_guard` didn't insert a sibling Task with matching `work_id` for the post-self-deadlock-fix cross-system guard semantics.
- **Fix:** (a) JobLock row seeding in `_create_job`; (b) PAUSED sibling Task insertion mirroring canonical SQLite test pattern. ~74 lines, test-code only.
- **Verification:** Re-ran PG pack (77 passed, 0 failed).
