# Job Queue Defer Contract Drift (pre-existing, surfaced during authz smoke sweep)

**Date:** 2026-07-25
**Found during:** Tool Authorization Auto-Derive test task (smoke sweep of `tests/job_queue/`)
**Feature branch:** `feature/tool-authz-auto-derive` @ `3b94ba85`
**Unrelated to the authz change** — this is a pre-existing contract drift.

## Root Cause
Commit `45c068f9` (2026-07-23, "fix: defer gate no longer blocks on queued jobs") intentionally
narrowed the SQL predicate `has_active_non_deferred_work()` from
`admission_state IN ('queued','active')` → `admission_state = 'active'` to break a
defer↔background deadlock.

That commit updated the canonical test in `tests/job_queue/test_defer_idle_gate_phase2.py`
but left **two sister tests** in `tests/job_queue/test_seam_invariants.py` stale:

1. `test_defer_blocked_by_non_defer_work_on_fifo_queue` — set `admission_state="queued"` but
   asserted the predicate returned `True`. After `45c068f9`, queued jobs no longer count as
   active non-defer work → returns `False`.
2. `test_queued_admission_state_blocks_defer_predicate` — same stale contract; name and
   assertion both contradicted the narrowed predicate.

## Verification (NOT an authz regression)
- `git blame` pinpointed `45c068f9` as the predicate-narrowing commit.
- Worker created a temporary worktree at `/tmp/authz-parent` checked out at the **parent**
  commit `393cfef5` (before the authz commit) and ran the same tests → **both failed
  identically**. Confirms pre-existing, not introduced by the authz refactor.
- Worktree cleaned up via `git worktree remove`.

## Fix Applied
Test-only changes in `tests/job_queue/test_seam_invariants.py` (commit `05a00fb3`):
- Test 1: `admission_state="queued"` → `"active"` (test now verifies what its name claims).
- Test 2: renamed `test_queued_admission_state_does_not_block_defer_predicate`, assertion
  flipped to `is False`, docstring citing the `45c068f9` contract + canonical reference
  (`test_queued_job_does_not_count_as_active` in `test_defer_idle_gate_phase2.py`).

## Lesson
When a production contract change intentionally narrows a predicate (especially to fix a
deadlock), **audit ALL tests referencing the changed field/predicate**, not just the
canonical one in the same file. Sister tests in other files (here, the "seam invariants"
file) can drift silently and surface as false failures in unrelated smoke sweeps.

A smoke sweep that surfaces pre-existing failures is valuable — it catches contract drift
that would otherwise accumulate. The key disambiguation technique: **worktree test against
the parent commit** to prove pre-existing vs. introduced.
