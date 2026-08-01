# Lesson: Stale E2E Assertion vs C2 Cancelled Status Fix (Inc 4 Cluster 2)

**Date:** 2026-08-01
**Branch:** `latest`
**Commit:** `b5d816a5`

## Root Cause

The Inc 4 C2 code review fix intentionally added `CANCELLED` to `find_paused_or_cancellable_turn`'s status filter (to prevent instance stranding after resume cascade). The docstring at `repository.py:336` documents: "CANCELLED is included as the resume cascade's consumed 'resumed' marker."

The E2E test `test_full_chain_claim_process_pause_resume_answer_complete` at line 499 asserted `find_paused_or_cancellable_turn(iid) is None` after ResumeTurn consumed the handle. This was correct for the **old** selector but stale after C2 — the selector now correctly returns the CANCELLED task.

The second test in the same file (`test_full_chain_no_deadlock_at_each_phase` at line 640-645) already accepted the CANCELLED post-resume status, confirming this is the intended behavior.

## Fix

Updated the assertion to expect the CANCELLED task:
```python
post_resume_turn = task_repo.find_paused_or_cancellable_turn(iid)
assert post_resume_turn is not None
assert post_resume_turn.work_id == work_id
assert post_resume_turn.status == TaskStatus.CANCELLED.value
```

## Pattern: Code Review Fix → Update ALL Tests

When a code review fix changes a selector's return contract (e.g., adding CANCELLED to a status filter), ALL tests that assert on that selector's output must be audited — not just the ones in the same test file.

**Checklist:**
```bash
# After changing a selector's filter/query:
grep -rn "find_paused_or_cancellable_turn\|find_suspended_turn_for_answer" tests/ --include="*.py"
# Verify all assertions match the new return contract
```

## DependencyBus Warning is Expected Noise

The `resume_cascade_db_sync: post-reconcile re-fire failed ... RuntimeError: DependencyBus is not initialized` warning is **expected E2E noise** — the test does not wire a DependencyBus singleton. The warning is caught and logged (`except Exception as refire_error: logger.warning(...)`) and does not affect test outcomes.
