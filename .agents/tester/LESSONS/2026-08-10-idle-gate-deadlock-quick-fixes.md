# Quick Fixes: Idle-Gate Deadlock Fix (2026-08-10)

## Fix 1: Cross-System Guard Test Alignment
**Commit:** `1c65fe76`
**File:** `tests/test_report_lane_phase2.py`
**Pack:** concurrency_atomic_unit_test

### Problem
`test_process_message_blocked_by_cross_system_guard` failed because it manually aligned `Task.work_id == JobItem.job_id` to simulate the cross-system guard blocking a claim. After commit `338a72b0` ("cross-system guard self-deadlock — exclude candidate task from in-flight check"), the guard now excludes the candidate's own row, so this setup no longer triggers the intended block.

### Root Cause
Test was ported from `tests/message_queue_redesign/test_task_repository.py` during `fix/idle-gate-deadlock` development but missed the self-deadlock-fix exclusion that landed ~1 day later. NOT a regression from the idle-gate fix itself.

### Fix
Insert a **PAUSED** sibling Task whose `work_id` matches the active JobItem's `job_id`. PAUSED is in the cross-system guard's in-flight set (`pending/running/paused`) but NOT in the per-instance guard's RUNNING-only set, properly isolating the cross-system guard. The candidate's own `work_id` is no longer manually aligned.

### Pattern Lesson
When testing cross-system guards post-self-deadlock fix, use a sibling task in a PAUSED state to avoid self-exclusion. The same pattern exists in `tests/message_queue_redesign/test_task_repository.py::test_claim_still_blocks_genuine_other_inflight_work`.

---

## Fix 2: SQLite Boolean Coercion in E2E Tests
**Commit:** `f60ddfd6`
**File:** `tests/job_queue/test_idle_gate_e2e_integration.py`
**Pack:** idle_gate_e2e_integration_test

### Problem
8/14 E2E tests failed with `assert 1 is True` / `assert 0 is False`.

### Root Cause
SQLite stores BOOLEAN columns as INTEGER (0/1). Python `is True` / `is False` are identity-strict checks: `1 is True` evaluates to `False` in Python because `1` is not the `True` singleton. The E2E tests use raw SQL `SELECT` to fetch task rows (deliberately going through the DB layer to verify column bindings), so the values come back as integers.

### Fix
Added `bool()` coercion right after every raw SQL row unpack:
```python
# Before (broken):
is_deferred, is_background = row
# After (fixed):
is_deferred, is_background = bool(is_deferred), bool(is_background)
```

### Pattern Lesson
When writing E2E integration tests that fetch rows via raw SQL on SQLite, always coerce boolean columns with `bool()`. Production repository code already coerces internally, which is why the companion unit tests (`test_idle_gate_deadlock_fix.py`) that use repository-layer access patterns don't hit this trap. This is a test-boundary concern, not a production bug.
