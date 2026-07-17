# C2 Deferred Pause Fix — Quick Fix & Testing Lessons

**Date**: 2026-07-17
**Commit**: 557ec294 (C2 fix), cae11e6f (test stub fix), 4ad66982 (edge case tests)

## Quick Fix: _ManagerStub Missing Attribute

### What happened
The C2 fix added `self._deferred_question_pause: set[str] = set()` to `InstanceManager.__init__` and `self._deferred_question_pause.discard(instance_id)` to `_cleanup_instance_state`. The test stub `_ManagerStub` in `tests/test_question_untested_paths.py` mirrored the old manager's attributes but not the new `_deferred_question_pause` set.

### Root cause
When adding a new instance attribute to InstanceManager that's used in `_cleanup_instance_state`, ALL test stubs that simulate cleanup must be updated. The `_ManagerStub` pattern mirrors specific attributes — it needs to be kept in sync.

### Fix
1 line: `self._deferred_question_pause: set[str] = set()` in `_ManagerStub.__init__`
Commit: `cae11e6f`

### Lesson
When a production class gains a new attribute that's accessed in cleanup/state-management code paths, grep for all stub/mock classes that simulate those code paths. Pattern: `grep -rn "_cleanup_instance_state\|_ManagerStub" tests/`

---

## Testing Pattern: Order-of-Operations Verification via Side-Effect Snapshot

The C2 invariant test `test_send_message_current_task_is_popped_before_cascade_runs` uses an elegant pattern for verifying execution order:

```python
graph_tasks_at_cascade_time: dict | None = None

async def _capture_then_cascade(_instance_id: str) -> dict:
    nonlocal graph_tasks_at_cascade_time
    graph_tasks_at_cascade_time = dict(manager._graph_tasks)  # snapshot at exact moment
    return {"status": "paused"}

manager.pause_instance_cascade = AsyncMock(side_effect=_capture_then_cascade)
```

This captures the state **at the exact instant** the method is called, proving ordering rather than just co-occurrence. This pattern is reusable for any ordering invariant.

---

## Pre-Existing Migration Bug (NOT C2-related)

### SQLite incompatibility
`20260714_000001_widen_job_queue_type_constraint.sql` uses `ALTER TABLE ... DROP CONSTRAINT IF EXISTS` which fails on SQLite 3.43.2. This causes 38 failures in `test_manager.py` whenever `InstanceManager` is instantiated.

### PG compatibility
The migration is valid PG SQL. Verified by running it directly against PostgreSQL 14.22.

### Test infrastructure limitation
`test_manager.py` fixture hardcodes `db_path=":memory:"` — there's no way to inject a PG URL without changing the fixture. This should be addressed in a follow-up.

### Recommendation
Either: (a) rewrite the migration using SQLite's table-rebuild pattern (12-step procedure), or (b) add dialect detection to the migration runner, or (c) update test_manager.py fixture to accept a configurable DB URL.
