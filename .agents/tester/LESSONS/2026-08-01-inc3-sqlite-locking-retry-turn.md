# LESSON: Inc 3 SQLite Locking Deadlock in RetryTurn → reconcile_turn_mirror

**Date:** 2026-08-01
**Commit:** `07761955` — `fix: reconcile retry mirrors in caller transaction`
**Severity:** 🟠 Important (caused 2 NEW test failures, fixed immediately)
**Increment:** Inc 3 (Named Transitions)

## Root Cause

`RetryTurn` (the named transition wrapping `schedule_retry`) runs inside a caller-owned transaction (`engine.begin()`). During its execution it called `reconcile_turn_mirror(work_id)`, which opened a **second** `engine.begin()` transaction.

On SQLite (single-writer lock model), the nested connection contended with the outer write lock, causing:
```
sqlite3.OperationalError: database is locked
```

## Symptoms

Two tests failed on initial run of `tests/message_queue_redesign/`:
- `test_task_retry_repository.py:477` — `test_schedule_retry_concurrent_calls_create_at_most_one_child`
- `test_task_retry_repository.py:902` — `test_force_cancel_concurrent_calls_create_at_most_one_child`

Both are concurrency tests that stress the retry path under parallel calls — exactly the scenario where SQLite's single-writer lock amplifies nested-transaction contention.

## Fix

`reconcile_turn_mirror()` now accepts an optional `connection` parameter. `RetryTurn` passes its active transaction (session) into the parent and child mirror reconciliation, eliminating the nested `engine.begin()`.

**Scope:** 16 lines across 2 production files. No architecture change.

**Before:**
```python
# Inside RetryTurn.run(session):
self._task_repo.reconcile_turn_mirror(parent_work_id)   # opens NEW engine.begin() — DEADLOCK
self._task_repo.reconcile_turn_mirror(child_work_id)    # same
```

**After:**
```python
# Inside RetryTurn.run(session):
self._task_repo.reconcile_turn_mirror(parent_work_id, connection=session)   # reuses caller's tx
self._task_repo.reconcile_turn_mirror(child_work_id, connection=session)    # same
```

## Why This Matters for Future Increments

1. **Pattern:** Any transition that calls `reconcile_turn_mirror` inside an existing transaction MUST pass the session. The reconciler's default `engine.begin()` is only safe when called standalone (e.g., from `claim_pending_task` or the periodic sweep).

2. **Inc 4 risk:** `RESUME_TURN` and `SUSPEND_TURN` also run inside cascade transactions. If Inc 4 adds reconciliation calls inside these transitions, the same pattern applies.

3. **PostgreSQL vs SQLite:** PostgreSQL has MVCC (multi-version concurrency) so nested transactions use savepoints and don't deadlock. This bug was SQLite-specific — a reminder that SQLite's single-writer model exposes transaction nesting bugs that PG hides.

4. **Test coverage:** The concurrency retry tests (`test_task_retry_repository.py`) are the canary for this class — they're the only tests that stress `RetryTurn` under concurrent SQLite writes.
