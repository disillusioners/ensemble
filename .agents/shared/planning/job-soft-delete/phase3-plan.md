# Phase 3: BE — API & Service Layer

## Objective
Add the soft-delete endpoint (`DELETE /api/jobs/{job_id}` soft-delete behavior) and `include_deleted` query parameter to the job listing endpoint. Update Pydantic schemas to include `deleted_at`.

## Coupling
- **Depends on**: Phase 2 (repository must have `soft_delete()` and updated `list()`)
- **Coupling type**: tight — calls repository methods from Phase 2
- **Shared files with other phases**: `daemon/routers/jobs.py`, `daemon/routers/schemas.py`, `daemon/services/job_queue_service.py`
- **Shared APIs/interfaces**: HTTP API contract (used by FE in Phase 4)
- **Why this coupling**: API layer calls repository directly; must use new method signatures.

## Context
- Currently `DELETE /api/jobs/{job_id}` calls `cancel_job()` — it cancels PENDING/PROCESSING jobs.
- The requirement is for soft delete, which is different from cancellation.
- **Design decision**: We need a SEPARATE endpoint or overloaded behavior for soft-delete.

### API Design Decision

**Recommended approach**: Replace the current `DELETE /{job_id}` (cancel) with soft-delete behavior, and keep cancellation as `POST /{job_id}/cancel`.

However, this would be a **breaking change** for existing API consumers. The safer approach:

| Approach | Pros | Cons |
|----------|------|------|
| **A: `DELETE /{job_id}` = soft delete for terminal jobs, cancel for active** | Intuitive REST semantics | Complex branching logic |
| **B: New `POST /{job_id}/delete` endpoint** | Non-breaking, clear separation | Not RESTful |
| **C: `DELETE /{job_id}` = soft delete always (rename cancel to POST)** | Clean REST semantics | Breaking change |

**Recommended: Approach A** — `DELETE /{job_id}` performs soft-delete on terminal jobs and cancel on active jobs. This is the most intuitive UX and preserves backward compatibility (canceling active jobs still works via DELETE).

**Alternative for cleaner API**: Also add `POST /{job_id}/cancel` as an explicit cancel endpoint, and document that `DELETE` should be used for soft-delete of completed/failed/cancelled jobs.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `JobResponse` schema | Add `deleted_at: Optional[str] = Field(default=None, description="Timestamp when job was soft-deleted")` | `daemon/routers/schemas.py` |
| 2 | Update `_job_to_response()` helper | Add `deleted_at=job.deleted_at` to the response mapping | `daemon/routers/jobs.py` |
| 3 | Update `list_jobs` endpoint | Add `include_deleted: bool = False` query parameter. Pass to `service.list_jobs()`. | `daemon/routers/jobs.py` |
| 4 | Update `JobQueueService.list_jobs()` | Add `include_deleted` parameter, pass to `repository.list()` | `daemon/services/job_queue_service.py` |
| 5 | Add `soft_delete_job()` to service | New method: calls `repository.soft_delete()`, returns updated job | `daemon/services/job_queue_service.py` |
| 6 | Update `DELETE /{job_id}` endpoint | Change behavior: if job is in terminal state → soft delete. If PENDING/PROCESSING → cancel (existing behavior). Return appropriate response. | `daemon/routers/jobs.py` |
| 7 | Add `POST /{job_id}/cancel` endpoint | Explicit cancel endpoint for API consumers who want clear cancel semantics (not delete). This is a thin wrapper that calls existing cancel logic. | `daemon/routers/jobs.py` |
| 8 | Add `POST /{job_id}/restore` endpoint | Restore a soft-deleted job by clearing `deleted_at`. Useful for undo functionality. Calls `repository.restore()`. | `daemon/routers/jobs.py` |
| 9 | Update `JobFilters` in frontend model (prep for Phase 4) | N/A — done in Phase 4 | — |

## Key Files
- `daemon/routers/jobs.py` — API endpoints (update DELETE, add include_deleted param, add restore endpoint)
- `daemon/routers/schemas.py` — Pydantic schemas (add deleted_at to JobResponse)
- `daemon/services/job_queue_service.py` — Service layer (add soft_delete_job, update list_jobs)

## Constraints
- Backward compatibility: existing `DELETE /{job_id}` for canceling PENDING/PROCESSING jobs must still work
- The `get_job` endpoint should return deleted jobs (so FE can display them when include_deleted=true)
- Terminal statuses for soft-delete: `completed`, `failed`, `cancelled`, `dead_letter`
- Cannot soft-delete a PENDING or PROCESSING job — those should be cancelled instead

## Detailed Implementation Reference

### Updated `DELETE /{job_id}` behavior

```python
@router.delete("/{job_id}")
async def delete_or_cancel_job(job_id: str, service = Depends(...)):
    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(404, ...)
    
    if job.deleted_at is not None:
        raise HTTPException(400, "Job is already deleted")
    
    if job.status in TERMINAL_STATUSES:
        # Soft delete for terminal jobs
        deleted_job = await service.soft_delete_job(job_id)
        return _job_to_response(deleted_job, message="Job deleted")
    else:
        # Cancel for active jobs (backward compat)
        success = await service.cancel_job(job_id)
        if not success:
            raise HTTPException(400, ...)
        updated_job = await service.get_job(job_id)
        return _job_to_response(updated_job, message="Job cancelled")
```

### Updated `list_jobs` endpoint

```python
@router.get("")
async def list_jobs(
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    queue_id: Optional[str] = None,
    limit: int = 50,
    include_deleted: bool = False,  # NEW
    service = Depends(...),
):
    # ... existing validation ...
    jobs = await service.list_jobs(
        statuses=statuses,
        project_id=project_id,
        limit=limit,
        queue_id=queue_id,
        include_deleted=include_deleted,  # NEW
    )
```

### `soft_delete_job` service method

```python
async def soft_delete_job(self, job_id: str) -> Optional[JobItem]:
    """Soft-delete a job by setting deleted_at timestamp."""
    return await asyncio.to_thread(self._repository.soft_delete, job_id)
```

## Deliverables
- [ ] `JobResponse` includes `deleted_at` field
- [ ] `DELETE /{job_id}` soft-deletes terminal jobs, cancels active jobs
- [ ] `GET /api/jobs?include_deleted=true` returns deleted jobs
- [ ] `GET /api/jobs` (default) excludes deleted jobs
- [ ] `POST /{job_id}/cancel` explicit cancel endpoint added
- [ ] `POST /{job_id}/restore` restore endpoint added
- [ ] All endpoint responses include `deleted_at` when applicable
