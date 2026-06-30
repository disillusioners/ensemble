# Defer Seam Bugfix — Testing Lessons

## Date: 2026-06-30
## Branch: feature/defer-seam-bugfix

---

## Test Coverage Already Excellent

The developer pre-wrote comprehensive tests for all 4 bugs (F4, F7, F1, F3). All existing tests pass without modification. No new tests needed to be written by the tester.

**Key test files:**
- `tests/job_queue/test_seam_invariants.py` — 21 tests covering F4/F7 lock scoping, recovery isolation, sync twin, retry-then-cancel
- `tests/unit/services/test_work_resolver.py` — 84 tests covering F1 dedup (5 tests) and F3 status filter (7 tests)
- `tests/job_queue/test_job_recovery_service.py` — 33 tests, 16 assertions for `_fail_orphaned_job` using `release_by_job`

---

## F4/F7 Architecture: 3-Path Lock Release Dispatch

`_finalize_terminal` implements a 3-path dispatch:
1. **Path 1 (`_dispatch_skipped=True`)** → No release (job never held a lock)
2. **Path 2 (full triple available)** → `release_by_job` (scoped)
3. **Path 3 (virtual/missing data)** → `release_by_instance` (fallback with WARNING)

Critical regression was also found and fixed in `_fail_orphaned_job` — same `release_by_instance` bug existed there. When changing lock patterns, check ALL call sites.

---

## F1 Dedup: (instance_id, message_id) Tuple Key

Dedup rule: When a Task (kind="turn") shares `(instance_id, message_id)` with ANY JobItem, the Task is dropped. Reports (kind="report") are never deduped. Tasks with `message_id=None` are never suppressed.

---

## F3 Status Filter: terminal_reason Column

Status filtering now consults `terminal_reason` column for done-state disambiguation:
- `status="failed"` → strict match on terminal_reason='failed'
- `status="completed"` → terminal_reason='completed' OR NULL (legacy hedge)
- `status="cancelled"` → strict match on terminal_reason='cancelled'

---

## Pre-existing Flake

`test_concurrent_start_only_one_succeeds` in `test_job_repository_atomic_transition.py` is a known flaky test — fails only in full-suite context, passes in isolation. Not related to defer-seam changes.
