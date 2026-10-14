# Phase 3 Implementation Experience — Fix `last_run_at` in Schedule API

## What was done:
- Fixed `last_run_at` in GET /schedules (list endpoint) — queries `get_latest_execution(schedule_id)` for each schedule
- Fixed `last_run_at` in PUT /schedules/{id} (single endpoint) — replaced hardcoded `None` with actual DB query
- Added `next_run_at` computation from adapter when available
- Added mock for `get_latest_execution` in test fixture
- Commit: `be048c5`

## Key Learnings:
1. **Adapter constraint violation caught by review**: Opencode accidentally modified `daemon/sources/adapters/scheduler.py` and `daemon/constants.py` (from Phase 5 work). The review caught this and we reverted only those files with `git checkout HEAD -- <file>`. Lesson: Always verify scope in review.

2. **`get_latest_execution()` already existed**: The repository already had this method, so no new repository methods were needed. Always check existing code before adding new helpers.

3. **`triggered_at` vs `completed_at`**: `last_run_at` must use `triggered_at` (when execution started, not when it completed). This is important for UX — users want to know when the last run was triggered.

4. **N+1 acceptable for schedules**: Schedule lists are typically small (< 50), so per-schedule query for latest execution is fine. No batch method needed.

5. **Graceful None handling**: For schedules created before execution recording existed, `get_latest_execution()` returns `None` and `last_run_at` stays `None` — no error, no crash.

## Files modified:
- `daemon/routers/schedules.py` — API endpoints with `last_run_at` fix
- `tests/test_scheduler_api.py` — Mock for `get_latest_execution` in test fixture

## Constraints respected:
- Did NOT modify `daemon/sources/adapters/scheduler.py` (reverted accidental changes)
- Did NOT add execution recording (already works via registry callback)
- API response shape backward compatible
