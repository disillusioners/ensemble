# Phase 2: Backend — API Schema & Route Fixes

## Objective
Add the missing fields (`source`, `job_metadata`, `cancelled_at`) to the `JobResponse` schema and update the route mapping function to populate them from `JobItem`, so the frontend receives complete job data.

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

**Backend `JobItem` model** already stores all these fields:
- `source`: stored as `str` (line in models.py)
- `job_metadata`: stored as `dict[str, Any]`
- `cancelled_at`: stored as `Optional[str]`

**Backend `agent_dir`** is in `JobResponse` but NOT in frontend `Job` interface — this is backend-only and doesn't need to be added to frontend (it's an implementation detail).

### Root Cause
The `_job_to_response()` mapping function in `jobs.py:63-84` simply doesn't include these fields. The `JobResponse` Pydantic model doesn't declare them.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add missing fields to `JobResponse` schema | Add `source`, `job_metadata`, `cancelled_at` as optional fields | `daemon/routers/schemas.py:43-80` |
| 2 | Update `_job_to_response()` mapping | Map the new fields from `JobItem` to `JobResponse` | `daemon/routers/jobs.py:63-84` |
| 3 | Update inline response constructions | Fix `create_job` endpoint's inline `JobResponse` construction to include new fields | `daemon/routers/jobs.py:151-188` |
| 4 | Add `message` field from `JobItem.message` | The `message` field should carry the original job message content, not transient status messages | `daemon/routers/jobs.py:63-84` |

## Detailed Implementation

### Task 1: Update `JobResponse` Schema

**File**: `daemon/routers/schemas.py`

**Add after line 59** (after the `message` field):
```python
source: Optional[str] = Field(default=None, description="Source of the job (api, telegram, scheduler)")
job_metadata: Optional[dict[str, Any]] = Field(default=None, description="Job metadata dictionary")
cancelled_at: Optional[str] = Field(default=None, description="Timestamp when job was cancelled")
```

All three fields are `Optional` with `None` default — backward compatible.

### Task 2: Update `_job_to_response()` Mapping

**File**: `daemon/routers/jobs.py` — `_job_to_response()` function (lines 63-84)

**Current mapping**:
```python
def _job_to_response(job, position=None, message=None):
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
        message=message,
    )
```

**Updated mapping** — add 3 new fields:
```python
def _job_to_response(job, position=None, message=None):
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
        message=message or job.message,  # Fall back to job content
        source=job.source,                # NEW
        job_metadata=job.job_metadata,    # NEW
        cancelled_at=job.cancelled_at,    # NEW
    )
```

**Note on `message` field**: The current code uses `message` for transient status messages ("Job queued, waiting for project lock"). The frontend expects it to be the original job content. The fix: if no explicit status message is provided, fall back to `job.message` (the original content). When a status message is passed (e.g., "Job cancelled successfully"), use that instead.

### Task 3: Update Inline Responses

**File**: `daemon/routers/jobs.py` — `create_job()` endpoint (lines 151-188)

Both the 200 and 202 response paths construct `JobResponse` inline. Add the new fields:

```python
# Line 153-164 (200 response)
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
    message=job.message,             # CHANGED: use job content
    source=job.source,               # NEW
    job_metadata=job.job_metadata,   # NEW
    cancelled_at=job.cancelled_at,   # NEW
)

# Line 174-184 (202 response)
response = JobResponse(
    job_id=job.job_id,
    status=job.status,
    priority=job.priority,
    agent_id=job.agent_id,
    agent_dir=job.agent_dir,
    project_id=job.project_id,
    created_at=job.created_at,
    position=position,
    message=job.message,             # CHANGED: use job content
    source=job.source,               # NEW
    job_metadata=job.job_metadata,   # NEW
    cancelled_at=job.cancelled_at,   # NEW
)
```

### Task 4: Semantic Fix for `message` Field

The `message` field in `JobResponse` currently has **dual semantics**:
- Sometimes: original job content (what the user submitted)
- Sometimes: transient status message ("Job queued", "Job cancelled")

**Decision**: Keep the current behavior but make the fallback explicit:
- When `_job_to_response()` is called with explicit `message` parameter → use that (status message)
- When called without `message` → use `job.message` (original content)
- Frontend can distinguish by context (status messages are short and descriptive)

This is a pragmatic fix. A more thorough approach would be to add a separate `content` field for the original message, but that's a bigger change and can be done later.

## Key Files
- `daemon/routers/schemas.py` — Add 3 fields to `JobResponse` (lines 43-80)
- `daemon/routers/jobs.py` — Update `_job_to_response()` and inline responses (lines 63-188)

## Constraints
- All new fields must be `Optional` with `None` default — backward compatible
- Existing API consumers must not break — additive change only
- `agent_dir` stays in backend response (not added to frontend) — it's implementation info
- `JobItem` model already has all fields stored — no DB changes needed

## Deliverables
- [ ] `JobResponse` schema has `source`, `job_metadata`, `cancelled_at` fields
- [ ] `_job_to_response()` maps all new fields
- [ ] `create_job()` endpoint returns new fields
- [ ] `message` field falls back to original job content when no status message
- [ ] All 6 API endpoints return complete job data
- [ ] No breaking changes to existing API contract
