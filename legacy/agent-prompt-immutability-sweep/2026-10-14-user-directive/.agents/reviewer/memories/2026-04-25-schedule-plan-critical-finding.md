# Schedule Feature Plan Review — Critical Finding

## Date: 2026-04-25

## Key Finding: Execution Recording Already Works

The plan claims `record_execution_start/complete` are "never called" by the adapter. This is **INCORRECT**.

### Evidence
- `daemon/sources/registry.py:300-314` defines `execution_callback` which wraps `_safe_sync_callback`
- `_safe_sync_callback` (lines 278-298) calls `repo.record_execution_start()` and `repo.record_execution_complete()` based on status
- The adapter calls `self._execution_callback()` 15+ times throughout `scheduler.py`
- The callback is injected at adapter creation time (line 334 in registry.py)

### Impact on Plan
- Phase 3 Task 3.1 (add recording to _execute_run) would create DUPLICATE records
- The root cause for last_run_at=None is in the API layer, not the adapter layer
- Both GET /schedules (line 60-68) and PUT /schedules/{id} (line 149) don't read execution records
- GET /schedules doesn't even pass last_run_at to ScheduleInfo constructor
- PUT /schedules/{id} explicitly hardcodes last_run_at=None at line 149
