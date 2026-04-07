# Phase 2: Backend — API Schema & Route Fixes

## Objective
Add the missing fields (`source`, `job_metadata`, `cancelled_at`) to the `JobResponse` schema, update the route mapping to populate them, and consolidate inline `JobResponse` constructions to use the existing `_job_to_response()` helper.

## Coupling
- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: `daemon/routers/schemas.py` (schema definition), `daemon/routers/jobs.py` (mapping logic)
- **Shared APIs/interfaces**: `JobResponse` schema — frontend (Phase 4) will align to this
- **Why independent**: Schema changes are additive (new optional fields). No existing fields change. No code in other phases touches these files.

## Context

### Current State

**Frontend `Job` interface** expects these fields that backend doesn't return:
| Field | Frontend Type | Backend Status |
|-------|--------------|----------------|
| `source` | `JobSource` (string enum) | ❌ Not in `JobResponse` |
| `job_metadata` | `Record<string, any>` | ❌ Not in `JobResponse` |
| `cancelled_at` | `string \| null` | ❌ Not in `JobResponse` |

**Backend `JobItem` model** already stores all these fields — no DB changes needed.

**Backend `agent_dir`** is in `JobResponse` but NOT in frontend `Job` interface — this is backend-only and doesn't need to be added to frontend (it's an implementation detail).

### Root Causes
1. The `JobResponse` Pydantic model doesn't declare these fields
2. The `_job_to_response()` mapping function doesn't include them
3. The `create_job()` endpoint constructs `JobResponse` inline (missing many fields), instead of using `_job_to_response()`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add missing fields to `JobResponse` schema | Add `source`, `job_metadata`, `cancelled_at` as optional fields | `daemon/routers/schemas.py` `JobResponse` |
| 2 | Update `_job_to_response()` mapping | Map the new fields from `JobItem` to `JobResponse`, fix `message` fallback | `daemon/routers/jobs.py` `_job_to_response()` |
| 3 | Replace inline `JobResponse` in `create_job()` with `_job_to_response()` | Both the 200 and 202 response paths construct incomplete `JobResponse` inline — refactor to use the shared helper | `daemon/routers/jobs.py` `create_job()` |
| 4 | Verify all 6 endpoints return complete data | Ensure list, get, cancel, retry, and SSE endpoints all use `_job_to_response()` | `daemon/routers/jobs.py` |

## Detailed Implementation

### Task 1: Update `JobResponse` Schema

**File**: `daemon/routers/schemas.py` — `JobResponse` class

**Add after the `message` field**:
```python
source: Optional[str] = Field(default=None, description="Source of the job (api, telegram, scheduler)")
job_metadata: Optional[dict[str, Any]] = Field(default=None, description="Job metadata dictionary")
cancelled_at: Optional[str] = Field(default=None, description="Timestamp when job was cancelled")
```

All three fields are `Optional` with `None` default — backward compatible. No existing fields changed.

### Task 2: Update `_job_to_response()` Mapping

**File**: `daemon/routers/jobs.py` — `_job_to_response()` function

**Current mapping** (missing `source`, `job_metadata`, `cancelled_at`, and `message` fallback is incomplete):
```python
def _job_to_response(job, position=None, message=None):
    return JobResponse(
        ...
        message=message,
    )
```

**Updated mapping**:
```python
def _job_to_response(job, position=None, message=None):
    """Convert JobItem to JobResponse."""
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        priority=job.priority,
        agent_id=job.agent_id,
        agent_dir=job.agent_dir,
        project_id=job.project_id,
        instance_id=job.instance_id,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result_summary=job.result_summary,
        error_message=job.error_message,
        position=position,
        message=message or job.message,  # Fall back to original job content
        source=job.source,                # NEW
        job_metadata=job.job_metadata,    # NEW
        cancelled_at=job.cancelled_at,    # NEW
    )
```

**Note on `message` field semantics**: When an explicit status message is passed (e.g., "Job cancelled successfully"), it's used. Otherwise, fall back to `job.message` (the original job content). This preserves the dual-purpose behavior while ensuring the field is always populated.

### Task 3: Replace Inline `JobResponse` in `create_job()` with `_job_to_response()`

**File**: `daemon/routers/jobs.py` — `create_job()` endpoint

**Problem**: The 200 response path (lines 153-164) and 202 response path (lines 174-184) construct `JobResponse` inline, missing many fields: `source`, `result_summary`, `error_message`, `completed_at`, `cancelled_at`, `job_metadata`. Instead of adding fields piecemeal, both should use the existing `_job_to_response()` helper which already handles full field mapping.

**Before** (200 path):
```python
if job.status == JobStatus.PROCESSING.value:
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        priority=job.priority,
        agent_id=job.agent_id,
        agent_dir=job.agent_dir,
        project_id=job.project_id,
        instance_id=job.instance_id,
        created_at=job.created_at,
        started_at=job.started_at,
        message="Job started immediately",
    )
```

**After** (200 path):
```python
if job.status == JobStatus.PROCESSING.value:
    # Job started immediately - return 200
    return _job_to_response(job, message="Job started immediately")
```

**Before** (202 path):
```python
else:
    position = None
    if job.project_id:
        try:
            position = await service._get_queue_position(job.job_id, job.project_id)
        except Exception:
            pass
    
    response = JobResponse(
        job_id=job.job_id,
        status=job.status,
        priority=job.priority,
        agent_id=job.agent_id,
        agent_dir=job.agent_dir,
        project_id=job.project_id,
        created_at=job.created_at,
        position=position,
        message="Job queued, waiting for project lock",
    )
    return JSONResponse(
        status_code=202,
        content=response.model_dump()
    )
```

**After** (202 path):
```python
else:
    # Job is pending (queued) - return 202
    position = None
    if job.project_id:
        try:
            position = await service._get_queue_position(job.job_id, job.project_id)
        except Exception:
            pass
    
    response = _job_to_response(
        job,
        position=position,
        message="Job queued, waiting for project lock",
    )
    return JSONResponse(
        status_code=202,
        content=response.model_dump()
    )
```

**Benefits**: Both paths now return ALL fields consistently (via the shared helper). Future field additions only need to update `_job_to_response()` in one place.

### Task 4: Verify All Endpoints Return Complete Data

Quick audit of the remaining endpoints:
- `get_job()` — already uses `_job_to_response()` ✅
- `list_jobs()` — already uses `_job_to_response()` ✅
- `cancel_job()` — already uses `_job_to_response()` ✅
- `retry_job()` — already uses `_job_to_response()` ✅
- `stream_job_events()` — SSE returns JSON directly, not JobResponse — OK as-is ✅

All endpoints already use the helper except `create_job()` (fixed in Task 3). No other changes needed.

## Key Files
- `daemon/routers/schemas.py` — Add 3 fields to `JobResponse`
- `daemon/routers/jobs.py` — Update `_job_to_response()`, refactor `create_job()` to use it

## Constraints
- All new fields must be `Optional` with `None` default — backward compatible
- Existing API consumers must not break — additive change only
- `agent_dir` stays in backend response (not added to frontend) — it's implementation info
- `JobItem` model already has all fields stored — no DB changes needed
- Use function/method names as primary references (line numbers are approximate)

## Deliverables
- [ ] `JobResponse` schema has `source`, `job_metadata`, `cancelled_at` fields
- [ ] `_job_to_response()` maps all new fields
- [ ] `create_job()` uses `_job_to_response()` instead of inline construction (both 200 and 202 paths)
- [ ] `message` field falls back to original job content when no status message provided
- [ ] All 6 API endpoints return complete job data via the shared helper
- [ ] No breaking changes to existing API contract
