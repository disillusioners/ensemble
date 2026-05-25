# Phase 2: Ensure System Queues API Endpoint

## Objective

Add a `POST /api/projects/{project_id}/queues/ensure-system` endpoint that checks for missing system queues and creates them. This is the "repair" mechanism for projects that somehow lost their system queues.

## Coupling

- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: None (Phase 3 calls this API from frontend)
- **Shared APIs/interfaces**: `auto_provision_system_queues()` — same function used by project creation
- **Why this coupling**: Uses the same provisioning logic as project creation but exposed as an on-demand endpoint

## Context

- `auto_provision_system_queues()` in `daemon/services/job_queue_mgmt_service.py:55-139` is already idempotent (check-then-create)
- It's currently called at: (1) daemon startup for all projects, (2) project creation via BackgroundTasks
- The function already handles duplicates — calling it when all queues exist is a no-op
- `get_queue_mgmt_service()` DI pattern used in `daemon/routers/queues.py` and `daemon/routers/projects.py`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Add ensure_system_queues method to mgmt service** | Add `ensure_system_queues(project_id) -> EnsureSystemQueuesResult` that wraps `auto_provision_system_queues()` with a richer response: list existing queues, list created queues, total count. This separates the "ensure" intent from the raw "provision" function. | `daemon/services/job_queue_mgmt_service.py` |
| 2 | **Add response schema** | Create `EnsureSystemQueuesResponse` schema with fields: `project_id`, `existing_queues: list[str]`, `created_queues: list[str]`, `total_system_queues: int`. | `daemon/routers/schemas.py` |
| 3 | **Add POST endpoint to queues router** | Add `POST /projects/{project_id}/queues/ensure-system` to `daemon/routers/queues.py`. Call the new ensure method. Return the response schema. Validate project exists first. | `daemon/routers/queues.py` |
| 4 | **Test the endpoint** | Verify: all queues exist → returns existing list, created empty. Missing queues → creates them, returns created list. Invalid project → 404. | Manual or test file |

## Key Files

- `daemon/services/job_queue_mgmt_service.py:55-139` — `auto_provision_system_queues()` to wrap
- `daemon/routers/queues.py` — Add POST ensure endpoint
- `daemon/routers/schemas.py` — Add response schema

## API Contract

```
POST /api/projects/{project_id}/queues/ensure-system

Response 200:
{
  "project_id": "uuid",
  "existing_queues": ["system_fifo_queue", "system_parallel_queue"],
  "created_queues": ["system_kb_fifo_queue", "system_defer_queue"],
  "total_system_queues": 4
}

Response 404:
{
  "detail": {"error": "Project not found"}
}
```

## Constraints

- Reuse `auto_provision_system_queues()` — do NOT duplicate queue creation logic
- Endpoint should be idempotent (safe to call multiple times)
- Response must clearly distinguish existing vs created queues (useful for frontend feedback)
- Must validate project exists before attempting (return 404 if not)

## Deliverables

- [ ] `ensure_system_queues()` method on `JobQueueMgmtService`
- [ ] `EnsureSystemQueuesResponse` schema
- [ ] `POST /projects/{project_id}/queues/ensure-system` endpoint
- [ ] Proper error handling (404 for missing project)
