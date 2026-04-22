# Phase 5: Jobs Router Cleanup & Lock Logic Dedup

## Objective
Split the `daemon/routers/jobs.py` router (891 lines) into focused sub-routers and eliminate the duplicated lock release logic in `daemon/services/job_queue_service.py`. 

> **⚠️ This phase MUST run after Phase 3** — `jobs.py` line 166 previously imported `validate_agent_id` from `daemon.api`. Phase 1 relocated it to `daemon.utils` and Phase 3 splits `api.py`. Both must complete first.
> 
> **⚠️ This phase MUST run after Phase 1** — `job_queue_service.py` is modified by Phase 1 (constant extraction) and Phase 5 (lock dedup). Cannot overlap.

## Coupling
- **Depends on**: Phase 1 (constants, utilities) + Phase 3 (api.py split complete)
- **Coupling type**: tight with both Phase 1 and Phase 3
- **Shared files with other phases**: `daemon/services/job_queue_service.py` (also modified by Phase 1 — constants already applied)
- **Shared APIs/interfaces**: Job router paths preserved

## Pre-flight Validation
```bash
git tag refactor-pre-phase5

# Verify validate_agent_id import is already updated from Phase 1
grep "validate_agent_id" daemon/routers/jobs.py
# Expected: "from daemon.utils import validate_agent_id" (NOT from daemon.api)

# Verify constants are already applied to job_queue_service.py
grep "from daemon.constants import" daemon/services/job_queue_service.py

# Record job endpoint paths
grep -n "@.*\." daemon/routers/jobs.py | head -20
```

## Rollback Procedure
```bash
git checkout refactor-pre-phase5 -- daemon/routers/jobs.py daemon/services/job_queue_service.py
rm -f daemon/routers/jobs_crud.py daemon/routers/jobs_management.py daemon/routers/jobs_streaming.py
# Re-run tests
```

## Context
- Phase 1 completed: constants applied to `job_queue_service.py`, `validate_agent_id` moved to utils
- Phase 3 completed: `api.py` fully split into routers, no impact on jobs router
- `jobs.py` has 8 endpoints in 3 sub-groups: CRUD (lines 131–401), Management (lines 403–739), Streaming (lines 741–883)
- `job_queue_service.py` has duplicated lock release at lines 603–614 and 836–843 with **subtle differences**

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Split `jobs.py` CRUD endpoints | Extract `POST /`, `GET /{job_id}`, `GET /` into `daemon/routers/jobs_crud.py` | `daemon/routers/jobs_crud.py` (new) |
| 2 | Split `jobs.py` Management endpoints | Extract `DELETE /{job_id}`, `POST /{job_id}/cancel`, `POST /{job_id}/restore`, `POST /{job_id}/retry` into `daemon/routers/jobs_management.py` | `daemon/routers/jobs_management.py` (new) |
| 3 | Split `jobs.py` Streaming endpoint | Extract `GET /{job_id}/events` SSE endpoint into `daemon/routers/jobs_streaming.py` | `daemon/routers/jobs_streaming.py` (new) |
| 4 | Replace `daemon/routers/jobs.py` with aggregator | Thin router that includes the 3 sub-routers | `daemon/routers/jobs.py` |
| 5 | Extract lock release helper in `job_queue_service.py` | Create `_release_job_lock()` method that handles both code paths (see detailed notes for the subtle differences) | `daemon/services/job_queue_service.py` |
| 6 | Replace duplicated lock release calls | Replace code at lines 603–614 and 836–843 with calls to the new helper | `daemon/services/job_queue_service.py` |
| 7 | Use HTTPException helpers | Replace verbose exception construction in new job sub-routers | `daemon/routers/jobs_*.py` |

## Key Files
- `daemon/routers/jobs.py` — Becomes aggregator (~30 lines)
- `daemon/routers/jobs_crud.py` (new) — ~270 lines
- `daemon/routers/jobs_management.py` (new) — ~340 lines
- `daemon/routers/jobs_streaming.py` (new) — ~140 lines
- `daemon/services/job_queue_service.py` — Deduplicate lock logic

## Constraints
- Job API paths and responses must remain identical
- SSE streaming behavior must not change
- Lock release behavior must be **exactly preserved** — the two code paths have different fallback calls
- Tests in `tests/job_queue/` must pass without changes

## Detailed Implementation Notes

### Lock Release Dedup — IMPORTANT: Subtle Differences

The two lock release patterns are **NOT identical**:

**Pattern A** (`_complete_job`, lines 603–614):
```python
if job.queue_id and job.project_id:
    await self._lock_manager.release_queue_lock(
        job.project_id, job.queue_id, job.job_id
    )
elif job.project_id:
    # Backward compatibility: project without queue - release by instance
    if job.instance_id:
        await self._lock_manager.release_by_instance(job.instance_id)
```

**Pattern B** (`complete_job`, lines 836–843):
```python
if job.queue_id and job.project_id:
    await self._lock_manager.release_queue_lock(
        job.project_id, job.queue_id, job_id
    )
elif job.project_id:
    # Backward compatibility: project without queue
    await self._lock_manager.release(job.project_id, job_id)
```

**Key differences**:
1. Pattern A passes `job.job_id`; Pattern B passes local `job_id` (may or may not be the same)
2. Pattern A's fallback: `release_by_instance(job.instance_id)` (checks `if job.instance_id`)
3. Pattern B's fallback: `release(job.project_id, job_id)` (no instance check)

**Extracted helper must support both code paths**:
```python
async def _release_job_lock(
    self,
    *,
    project_id: str | None,
    queue_id: str | None,
    job_id: str,
    instance_id: str | None = None,
    fallback_mode: str = "by_instance",  # "by_instance" or "by_project"
) -> None:
    """Safely release a job's queue lock with backward-compatible fallback.
    
    Args:
        fallback_mode: "by_instance" uses release_by_instance (from _complete_job),
                       "by_project" uses release (from complete_job).
    """
    if queue_id and project_id:
        await self._lock_manager.release_queue_lock(
            project_id, queue_id, job_id
        )
    elif project_id:
        if fallback_mode == "by_instance" and instance_id:
            await self._lock_manager.release_by_instance(instance_id)
        else:
            await self._lock_manager.release(project_id, job_id)
```

> **Note**: The implementer MUST verify the exact method signatures on `_lock_manager` before implementing. The pattern above is based on exploration but should be validated against the actual `LockManager` class.

### Jobs Router Aggregation Pattern
```python
"""Jobs router — aggregates sub-routers."""
from fastapi import APIRouter
from daemon.routers.jobs_crud import router as crud_router
from daemon.routers.jobs_management import router as mgmt_router
from daemon.routers.jobs_streaming import router as stream_router

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
router.include_router(crud_router)
router.include_router(mgmt_router)
router.include_router(stream_router)
```

### Handling `validate_agent_id` Import (line 166)
Phase 1 already changed this to:
```python
from daemon.utils import validate_agent_id
```
No further action needed. If Phase 1's import is an inline import (inside a function), consider moving it to the top of the file in the appropriate sub-router.

## Deliverables
- [ ] `jobs.py` split into 3 sub-routers + aggregator
- [ ] Lock release duplication eliminated with helper that preserves both code paths
- [ ] All HTTPException patterns replaced with helpers
- [ ] All existing job API paths and responses preserved
- [ ] Full test suite passes (especially `tests/job_queue/`)
