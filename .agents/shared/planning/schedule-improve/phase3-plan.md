# Phase 3: API Layer — Fix `last_run_at`

## Objective
Fix the `last_run_at` bug by updating the API endpoints to **read** existing execution records from the database. Execution recording already works via the registry callback chain — the API just needs to query it.

## Coupling
- **Depends on**: Phase 1 (needs indexes on `ScheduleExecution` for efficient queries)
- **Coupling type**: loose
- **Shared files with other phases**: `daemon/routers/schedules.py` only
- **Shared APIs**: `get_latest_execution()` (already exists in repository)
- **Why**: Only reads from DB — no changes to `scheduler.py` or the recording pipeline

## Context
**Issues addressed**: #1 (last_run_at always None)

**Root cause**: Execution recording already works via this callback chain:
```
SchedulerAdapter._execution_callback()
  → SourceRegistry.execution_callback()         [registry.py:300-314]
    → _safe_sync_callback()                      [registry.py:278-298]
      → repo.record_execution_start()            [registry.py:286-290]
      → repo.record_execution_complete()          [registry.py:292-296]
```
The `ScheduleExecution` table IS populated correctly. The bug is purely an **API read gap**:
- `GET /schedules` (line 39-69): never passes `last_run_at` to `ScheduleInfo`
- `PUT /schedules/{id}` (line 73-151): hardcodes `last_run_at=None` at line 149
- `GET /schedules/{id}/executions` (line 322-389): already works correctly

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 3.1 | Fix `GET /schedules` to include `last_run_at` | For each schedule in the list, call `source_repo.get_latest_execution(schedule_id)`. If found, set `last_run_at = execution.triggered_at` (ISO string from DB). If not found, keep `None`. **Avoid N+1** — consider adding `get_latest_executions(schedule_ids: list[str]) -> dict[str, ScheduleExecution | None]` batch method to repository, or accept the N+1 for now if schedule list is typically small. | `daemon/routers/schedules.py:39-69`, `daemon/repositories/source/repository.py` |
| 3.2 | Fix `PUT /schedules/{id}` to include `last_run_at` | Replace hardcoded `last_run_at=None` at line 149 with query to `get_latest_execution(schedule_id)`. Same logic as Task 3.1. | `daemon/routers/schedules.py:142-151` |
| 3.3 | Handle missing execution records gracefully | For schedules created before execution recording existed (or for schedules never triggered), `get_latest_execution()` returns `None`. API must return `last_run_at=None` — no error, no crash. | `daemon/routers/schedules.py` |
| 3.4 | Verify `GET /schedules/{id}/executions` works | This endpoint already exists and queries `ScheduleExecution` correctly. Verify it returns populated records (it should — recording already works). No code changes expected. | `daemon/routers/schedules.py:322-389` |
| 3.5 | Update API tests for `last_run_at` | Add tests verifying: `last_run_at` is populated after execution, returns `None` for new schedules, reflects the most recent execution, GET list includes `last_run_at` for each schedule. | `tests/test_scheduler_api.py` |

## Key Files
- `daemon/routers/schedules.py` — API endpoints with `last_run_at` fix
- `daemon/repositories/source/repository.py` — DB methods (`get_latest_execution` already exists)
- `tests/test_scheduler_api.py` — API tests

## Constraints
- **DO NOT add execution recording to the adapter** — recording already works via registry callback (registry.py:286-296). Adding it to the adapter would create duplicate records.
- Must handle missing execution records for pre-existing schedules (graceful `None`)
- Frontend already expects `last_run_at` — no frontend changes needed
- `triggered_at` is stored as UTC ISO string — return as-is in API
- Avoid N+1 query problem in GET list endpoint — consider batch query

## Deliverables

- [ ] `GET /schedules` returns `last_run_at` from latest execution
- [ ] `PUT /schedules/{id}` returns `last_run_at` from latest execution
- [ ] `GET /schedules/{id}/executions` verified to return populated records
- [ ] Graceful handling of missing execution records (`last_run_at=None`)
- [ ] All existing API tests pass
- [ ] New tests for `last_run_at` integration
