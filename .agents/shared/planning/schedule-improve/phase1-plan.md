# Phase 1: Data Layer Foundation

## Objective
Establish the data layer foundation by adding missing database indexes, creating an execution status enum, fixing the run counter race condition with atomic SQL, and standardizing timezone handling across the repository layer.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (foundation)
- **Shared files with other phases**: `models.py` (enum), `repository.py` (atomic update, timezone fix)
- **Shared APIs**: `ScheduleExecution` model, `record_execution_start/complete`, `increment_scheduler_run_counter`, `get_latest_execution`
- **Why**: Phases 2–3 depend on the enum, atomic counter, and indexes established here

## Context
**Issues addressed**: #2 (race condition), #3 (missing indexes), #5 (no enum), #9 (timezone inconsistency)

**Current state**:
- `ScheduleExecution.status` is `str` — no validation, arbitrary values possible
- `triggered_at` has no index — `get_latest_execution()` does full table scan
- No composite index on `(schedule_id, status)` — `get_running_executions()` unoptimized
- `increment_scheduler_run_counter()` at `repository.py:97-126` uses non-atomic read-modify-write
- Repository uses `datetime.utcnow()` (deprecated in Python 3.12), scheduler uses `datetime.now(tz)`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1.1 | Create `ExecutionStatus` enum | Enum in `models.py` with values: `triggered`, `completed`, `failed`, `skipped`, `queued`. Add `is_valid(status)` class method. Keep `ScheduleExecution.status` as `str` in DB for backward compatibility. | `daemon/repositories/source/models.py` |
| 1.2 | Add `triggered_at` index | Add `index=True` to `ScheduleExecution.triggered_at` field. Create migration for existing DBs. | `daemon/repositories/source/models.py:125` |
| 1.3 | Add composite index `(schedule_id, status)` | Add `__table_args__` with composite index for `get_running_executions()` optimization. | `daemon/repositories/source/models.py` |
| 1.4 | Fix run counter race condition | Replace non-atomic read-modify-write at `repository.py:97-126` with atomic SQL: `UPDATE source_configs SET config = JSON_SET(config, '$._run_counter', COALESCE(CAST(JSON_EXTRACT(config, '$._run_counter') AS INTEGER), 0) + 1) WHERE source_id = ?`. Return incremented value via `JSON_EXTRACT` in same statement or re-select. | `daemon/repositories/source/repository.py:97-126` |
| 1.5 | Standardize timezone to UTC | Replace all `datetime.utcnow()` calls with `datetime.now(timezone.utc)` in models.py and repository.py. This affects: `created_at`, `updated_at`, `triggered_at`, `completed_at` defaults and all manual timestamp assignments. | `daemon/repositories/source/models.py`, `daemon/repositories/source/repository.py` |
| 1.6 | Add status validation to repository | Validate `status` parameter in `record_execution_start()` and `record_execution_complete()` using `ExecutionStatus.is_valid()`. Raise `ValueError` for invalid values. | `daemon/repositories/source/repository.py:460-512` |
| 1.7 | Add unit tests for data layer changes | Test enum validation, atomic counter (including concurrent access), new indexes verified via `EXPLAIN QUERY PLAN`, status validation rejection. | `tests/test_scheduler_adapter.py` (or new test file) |

## Key Files
- `daemon/repositories/source/models.py` — SQLModel tables with enum and indexes
- `daemon/repositories/source/repository.py` — Repository methods with atomic updates and timezone fix
- `daemon/migrations/` — New migration file for index additions

## Constraints
- Must maintain backward compatibility (string status in DB column)
- Migration must be non-destructive (ADD INDEX only)
- **SQLite migration note**: DOWN migrations for index additions are `DROP INDEX IF EXISTS` only. Schema migrations in this project are effectively one-way — plan accordingly and test UP migration on a copy of production data.
- Atomic counter must handle case where `_run_counter` key doesn't exist yet in JSON
- All existing repository tests must pass

## Deliverables
- [ ] `ExecutionStatus` enum with validation method
- [ ] Index on `triggered_at` field
- [ ] Composite index on `(schedule_id, status)`
- [ ] `increment_scheduler_run_counter()` uses atomic SQL
- [ ] All timestamps use `datetime.now(timezone.utc)`
- [ ] Status validation in repository methods
- [ ] All existing tests pass
- [ ] New tests for atomic counter and enum validation
