# Phase 2: BE — Repository Layer

## Objective
Update `JobRepository` to (1) exclude soft-deleted jobs from all execution-path queries, (2) add a `soft_delete()` method, and (3) support conditional inclusion of deleted rows in listing methods.

## Coupling
- **Depends on**: Phase 1 (model must have `deleted_at` field)
- **Coupling type**: tight — reads `JobItem.deleted_at` directly
- **Shared files with other phases**: `daemon/repositories/job_queue/repository.py`
- **Shared APIs/interfaces**: All repository methods used by services
- **Why this coupling**: Repository is the single data-access layer. All queries flow through it.

## Context
This is the **most critical phase** for system integrity. The scheduler, processor, retry engine, and recovery service all read from this repository. If any query fails to exclude `deleted_at IS NOT NULL`, a deleted job could be executed.

### Execution Path Analysis (MUST exclude deleted jobs)

| Method | Called By | Current Behavior | Required Change |
|--------|-----------|------------------|-----------------|
| `get()` | API get_job, service get_job, recovery, processor | Returns any job by ID | **Keep as-is** — single-ID fetch used for status checks on known jobs |
| `get_by_instance()` | Service (feedback observer, completion) | Finds job by instance_id | Add `WHERE deleted_at IS NULL` |
| `find_by_idempotency_key()` | Service enqueue (dedup) | Finds job by key | Add `WHERE deleted_at IS NULL` — deleted jobs shouldn't block new ones |
| `list()` | API list_jobs, service list_jobs | Lists with filters | **Conditional** — default exclude deleted, `include_deleted=True` includes them |
| `list_pending_by_project()` | Service `_get_next_job`, `trigger_next_job_sync` | Finds pending for project | Add `WHERE deleted_at IS NULL` ⚠️ CRITICAL |
| `list_all_pending()` | Service `get_next_pending_job`, processor `_process_next_job` | Finds all pending | Add `WHERE deleted_at IS NULL` ⚠️ CRITICAL |
| `find_processing_jobs()` | Recovery service | Finds orphaned processing jobs | Add `WHERE deleted_at IS NULL` — no point recovering deleted jobs |
| `list_pending_by_queue()` | Service `_get_next_job`, processor `_process_next_job` | Finds pending for queue | Add `WHERE deleted_at IS NULL` ⚠️ CRITICAL |
| `list_by_queue()` | Processor (orphan detection) | Lists by queue with filters | Add `WHERE deleted_at IS NULL` ⚠️ CRITICAL |
| `find_retryable_jobs()` | Retry engine | Finds retryable failed jobs | Add `WHERE deleted_at IS NULL` — deleted jobs shouldn't be retried |
| `atomic_transition()` | Service (all state transitions) | Transitions job status | **Keep as-is** — operates on specific job_id, transition validation is enough |

### New Methods Needed

| Method | Purpose |
|--------|---------|
| `soft_delete(job_id)` | Sets `deleted_at = now()` on a job. Returns updated job or None. |
| `restore(job_id)` | Sets `deleted_at = None`. Returns updated job or None. (Future-proofing) |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `_exclude_deleted()` helper | Private method that returns a `WHERE deleted_at IS NULL` clause condition. Can be applied as `.where(JobItem.deleted_at.is_(None))` | `daemon/repositories/job_queue/repository.py` |
| 2 | Update `list_pending_by_project()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 3 | Update `list_all_pending()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 4 | Update `find_processing_jobs()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 5 | Update `list_pending_by_queue()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 6 | Update `list_by_queue()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 7 | Update `find_retryable_jobs()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 8 | Update `get_by_instance()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 9 | Update `find_by_idempotency_key()` | Add `.where(JobItem.deleted_at.is_(None))` to query | `daemon/repositories/job_queue/repository.py` |
| 10 | Update `list()` — add `include_deleted` param | Add `include_deleted: bool = False` parameter. When False (default), add `.where(JobItem.deleted_at.is_(None))` to both count and list queries. When True, include all. | `daemon/repositories/job_queue/repository.py` |
| 11 | Add `soft_delete()` method | New method: fetch job, set `deleted_at = datetime.utcnow().isoformat()`, commit, return updated job. If job not found, return None. If already deleted, return as-is (idempotent). | `daemon/repositories/job_queue/repository.py` |
| 12 | Update existing `delete()` method | Rename to `hard_delete()` (or add deprecation note). Keep for admin/cleanup use cases but it should not be called by the API. | `daemon/repositories/job_queue/repository.py` |
| 13 | Add tests for soft-delete exclusion | Test that `list_all_pending()`, `list_pending_by_queue()`, `list_pending_by_project()`, `find_retryable_jobs()`, `find_processing_jobs()` all exclude deleted jobs. | `daemon/repositories/job_queue/test_soft_delete.py` (new) or add to existing tests |

## Key Files
- `daemon/repositories/job_queue/repository.py` — Main repository (13 methods to update + 1 new method)

## Constraints
- **SQLite compatibility**: `WHERE deleted_at IS NULL` works correctly with SQLite. Indexed column for performance.
- **No ORM-level default filtering**: SQLModel doesn't have Django-style default manager filtering. We must add the WHERE clause to each method manually.
- **Do NOT modify `get()` or `atomic_transition()`**: These operate on specific job IDs and should be able to fetch deleted jobs (e.g., for the API to return a deleted job's details, or to restore it).

## Detailed Implementation Reference

### `_exclude_deleted` pattern
```python
# Each query method adds this condition:
.where(JobItem.deleted_at.is_(None))
```

### `soft_delete` method
```python
def soft_delete(self, job_id: str) -> Optional[JobItem]:
    """Soft-delete a job by setting deleted_at timestamp.
    
    Idempotent - if already deleted, returns the job as-is.
    
    Args:
        job_id: Job identifier.
        
    Returns:
        Updated JobItem if found, None otherwise.
    """
    with SQLModelSession(self.engine) as db_session:
        job = db_session.get(JobItem, job_id)
        if job is None:
            return None
        if job.deleted_at is not None:
            return job  # Already deleted, idempotent
        job.deleted_at = datetime.utcnow().isoformat()
        db_session.commit()
        db_session.refresh(job)
        return job
```

### Updated `list()` signature
```python
def list(
    self,
    statuses: Optional[list[str]] = None,
    project_id: Optional[str] = None,
    queue_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    include_deleted: bool = False,  # NEW
) -> tuple[list[JobItem], int]:
```

## Deliverables
- [ ] All execution-path methods exclude deleted jobs
- [ ] `soft_delete()` method implemented
- [ ] `list()` supports `include_deleted` parameter
- [ ] Existing `delete()` renamed to `hard_delete()` with deprecation note
- [ ] Tests verify deleted jobs are excluded from all execution queries
- [ ] No import errors after renaming `delete()` → `hard_delete()`
