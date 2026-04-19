# Job Soft Delete Testing — 2026-04-19

## Feature
Soft delete capability for jobs: `deleted_at` column, all execution paths exclude deleted jobs.

## Key Testing Patterns
- **Repository tests**: Create jobs with various statuses, soft-delete them, verify each execution-path method excludes them
- **Scheduler safety tests**: Create PENDING job → soft-delete → verify `list_all_pending()`, `list_pending_by_project()`, `list_pending_by_queue()` all exclude it
- **API tests**: DELETE on terminal = soft delete, DELETE on active = cancel, POST /restore works
- **Idempotency**: `soft_delete()` called twice returns same job, no error
- **get() intentional pass-through**: `get()` does NOT filter deleted jobs (needed for restore/display)

## Test Files
- `tests/job_queue/test_soft_delete.py` — 34 BE tests (repository + API + scheduler safety)
- FE tests added to existing spec files — 35 FE tests

## Gotchas
- Existing `delete()` was renamed to `hard_delete()` — existing tests needed updating
- `hard_delete_completed()` also renamed — integration tests needed fixing
- All 9 execution-path methods must have `WHERE deleted_at IS NULL` — critical for scheduler safety
- `get()` and `atomic_transition()` intentionally do NOT filter deleted jobs

## Commits
- Implementation: `2cc8998` → `34cf89e` → `740efbf` → `4421c02` → `ae2b4f6`
- Tests: `9185a08` (FE) → `b767425` → `bf18230` → `e1f45ba` → `45b4bae` (consolidated BE)
