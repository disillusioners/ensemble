# Background Queue Type — Implementation Map & Testing Notes

## Date: 2026-07-13
## Branch: feature/background-all-projects-queue

## Architecture

The BACKGROUND queue type is a new addition (alongside FIFO, PARALLEL, DEFER) that processes
tasks only when ALL projects in the system are idle (no active non-deferred, non-background tasks).

### Key Difference from DEFER
- **DEFER**: Checks own project only — processes when `count_active_jobs_in_non_defer_queues(project_id) == 0`
- **BACKGROUND**: Checks ALL projects — processes when no active non-deferred/non-background tasks exist ANYWHERE

### Implementation Pattern (Atomic SQL Guard)
The background idle gate is folded INTO the atomic `claim_pending_task()` SQL, not a Python pre-check:

```sql
NOT (task.is_background = TRUE AND EXISTS(
    SELECT 1 FROM task t2
    WHERE t2.status = 'running'
    AND t2.is_deferred = false
    AND t2.is_background = false
    -- NOTE: no project_id filter — checks ALL projects
))
```

This mirrors the defer gate pattern but with the critical asymmetry of no project_id scoping.

## Components

| Component | Location | Notes |
|-----------|----------|-------|
| QueueType enum | `job_queue/models.py:164-169` | `BACKGROUND = "background"` |
| CheckConstraint | `job_queue/models.py:187` | Enforces `concurrency_limit=1` for defer+background |
| Model validator | `job_queue/models.py:215-229` | Broadened from defer-only to cover background too |
| Task.is_background | `task/models.py:167` | Bool column, defaults False |
| claim_pending_task | `task/repository.py:367` | Atomic SQL gate (cross-project) |
| has_active_non_background_work | `task/repository.py:1517` | project_id param accepted but ignored |
| auto_provision | `job_queue_mgmt_service.py:55-154` | 5 system queues incl. system_background_queue |
| enqueue_message | InstanceMessagingService:1073 | Stamps is_background |
| JobProcessor | lines 733,793,929 | Derives is_background from queue_type |

## Testing Discrepancies Found

1. **Pydantic model_validator vs SQLModel**: The `enforce_defer_concurrency_limit` validator does NOT
   fire on `SQLModel(table=True)` instantiation in this codebase. The DB `CheckConstraint` is the
   actual runtime enforcement layer (surfaces as `IntegrityError`). Tests must accept either
   `ValueError` or `IntegrityError`.

2. **SQLite boolean semantics**: SQLite returns `0/1` not Python `True/False` for boolean columns.
   Always cast through `bool()` in assertions for backend invariance.

3. **TaskRepository.create() API gap**: Does not accept `is_background`/`is_deferred` parameters.
   Tests insert via raw SQL, consistent with existing defer-gate test patterns in
   `tests/message_queue_redesign/test_task_repository.py`.

## Test File
- `tests/job_queue/test_background_queue.py` — 13 tests, all PASS
- Commit: `7613ded0db0b8a5435f169d2ef97d404cfe12612`
