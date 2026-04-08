# Phase 3: API + Frontend + Integration

## Objective
Expose the new queue management capabilities via REST API endpoints, update existing job endpoints to accept queue parameters, build the frontend UI, and validate end-to-end integration.

## Coupling
- **Depends on**: Phase 2 (services must exist)
- **Coupling type**: tight
- **Shared files with other phases**:
  - `daemon/routers/jobs.py` — modified
  - `daemon/routers/schemas.py` — modified
  - `daemon/routers/projects.py` — modified
  - `frontend/src/app/` — Angular components
- **Shared APIs/interfaces**: REST API contracts, Angular services
- **Why this coupling**: Phase 3 wraps Phase 2 services into API and UI

## Context
- Phase 2 provides `JobQueueMgmtService` (queue management) and updated `JobQueueService` (job operations)
- Current API router: `daemon/routers/jobs.py` with prefix `/api/jobs`
- Current project router: `daemon/routers/projects.py` with pause/resume queue endpoints
- SSE endpoint already exists at `GET /api/jobs/{job_id}/events`
- Angular 21 standalone components with signals, Angular Material (not ng-zorro)
- Existing JobsComponent at `/jobs` route with job list, detail drawer, create dialog

## Part A: API Layer

### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create queue management router** | New router file `daemon/routers/queues.py` with prefix `/api/projects/{project_id}/queues`. All endpoints **must** validate `queue.project_id == path_project_id` — return `404 Not Found` (never `403`) to avoid IDOR leakage (W3 fix). Endpoints: `GET /` (list), `POST /` (create), `GET /{queue_id}` (get), `PATCH /{queue_id}` (update), `DELETE /{queue_id}` (delete), `POST /{queue_id}/start`, `POST /{queue_id}/stop`, `GET /{queue_id}/stats` | `daemon/routers/queues.py` (new) |
| 2 | **Register queue router in app** | Mount the new router in `daemon/api.py`. Ensure the router prefix correctly extracts `project_id` for IDOR validation. | `daemon/api.py` |
| 3 | **Update `JobCreateRequest` schema** | Add optional `queue_name: Optional[str] = None` field. | `daemon/routers/schemas.py` |
| 4 | **Update `JobResponse` schema** | Add `queue_id: Optional[str]`, `queue_name: Optional[str]` fields. | `daemon/routers/schemas.py` |
| 5 | **Update `POST /api/jobs` endpoint** | Accept `queue_name` in request body. Resolve queue: (a) `project_id` set, no queue → `system_fifo_queue`, (b) `project_id` set, queue specified → resolve. Return `404` if queue not found, `409` if queue is paused. | `daemon/routers/jobs.py` |
| 6 | **Update `GET /api/jobs` endpoint** | Add optional query param `queue_id: Optional[str] = None`. Pass to service layer for filtering. | `daemon/routers/jobs.py` |
| 7 | **Update `GET /api/jobs/{id}/events` SSE** | Include `queue_id` and `queue_name` in SSE event payloads. Add `queue_updated` event type. | `daemon/routers/jobs.py` |
| 8 | **Add queue SSE endpoint** | `GET /api/projects/{project_id}/queues/events` — SSE stream for queue-level events. | `daemon/routers/queues.py` |
| 9 | **Update project creation endpoint** | Ensure auto-provisioning is triggered after project creation (via `BackgroundTasks`). | `daemon/routers/projects.py` |

### API Endpoint Reference

#### Queue Management (`/api/projects/{project_id}/queues`)

| Method | Path | Request | Response | Status | Notes |
|--------|------|---------|----------|--------|-------|
| GET | `/` | — | `list[JobQueueResponse]` | 200 | List all queues. **W3:** validate project_id. |
| POST | `/` | `JobQueueCreateRequest` | `JobQueueResponse` | 201 | Create. Reserved names → 400. |
| GET | `/{queue_id}` | — | `JobQueueResponse` | 200 | **W3:** ownership check → 404. |
| PATCH | `/{queue_id}` | `JobQueueUpdateRequest` | `JobQueueResponse` | 200 | **W3:** ownership → 404. FIFO validation → 400. |
| DELETE | `/{queue_id}` | — | `{deleted: true}` | 200 | **W3:** ownership → 404. System → 403. PROCESSING → 409. |
| POST | `/{queue_id}/start` | — | `JobQueueResponse` | 200 | Resume. **W3:** ownership → 404. |
| POST | `/{queue_id}/stop` | — | `JobQueueResponse` | 200 | Pause. **W3:** ownership → 404. |
| GET | `/{queue_id}/stats` | — | `QueueStatsResponse` | 200 | **W3:** ownership → 404. |
| GET | `/events` | — | SSE stream | 200 | **W3:** validate project_id. |

### Request/Response Schemas

```python
class JobQueueCreateRequest(BaseModel):
    queue_name: str = Field(min_length=1, max_length=100)
    queue_type: str = Field(default="fifo", pattern="^(fifo|parallel)$")
    concurrency_limit: int = Field(default=1, ge=1, le=20)
    description: Optional[str] = None

    @validator('queue_name')
    def normalize_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("queue_name cannot be empty")
        reserved = {"system_fifo_queue", "system_parallel_queue"}
        if v.lower() in reserved:
            raise ValueError(f"Queue name '{v}' is reserved")
        return v

class JobQueueResponse(BaseModel):
    queue_id: str
    project_id: str
    queue_name: str
    queue_type: str
    concurrency_limit: int
    is_paused: bool
    is_system: bool
    description: Optional[str]
    created_at: str
    updated_at: str
    active_jobs: int = 0
    pending_jobs: int = 0
```

## Part B: Frontend

### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create TypeScript interfaces** | `JobQueue`, `JobQueueCreateRequest`, `JobQueueUpdateRequest`, `QueueStatsResponse` | `frontend/src/app/models/job-queue.model.ts` (new) |
| 2 | **Create `QueueService`** | Angular injectable: `listQueues()`, `createQueue()`, `getQueue()`, `updateQueue()`, `deleteQueue()`, `startQueue()`, `stopQueue()`, `getQueueStats()` | `frontend/src/app/services/queue.service.ts` (new) |
| 3 | **Create `QueueSseService`** | SSE for queue-level events, emits signals | `frontend/src/app/services/queue-sse.service.ts` (new) |
| 4 | **Create `QueueListComponent`** | Queue panel. "Select a project to view queues" placeholder when none selected. Name, type badge, status, counts, start/stop, delete (disabled for system queues) | `frontend/src/app/components/queue-list/` (new) |
| 5 | **Create `QueueCreateDialogComponent`** | MatDialog. `queue_name` (required, reserved-name validation), `queue_type` (mat-select), `concurrency_limit` (shown only for Parallel), `description` (optional). Reactive forms. | `frontend/src/app/components/queue-create-dialog/` (new) |
| 6 | **Update `JobsComponent`** | QueueListComponent as side panel. Queue filter dropdown. Master pause tooltip. | `frontend/src/app/pages/jobs/` |
| 7 | **Update `JobCardComponent`** | Queue badge (mat-chip). | `frontend/src/app/components/job-card/` |
| 8 | **Update `JobCreateDialogComponent`** | Queue selector (mat-select, NOT ng-zorro). Default `system_fifo_queue`. | `frontend/src/app/components/job-create-dialog/` |
| 9 | **Update `JobService`** | `queue_id` param to `listJobs()`, update `Job` interface, `createJob()` accepts `queue_name` | `frontend/src/app/services/job.service.ts` |

### UI Layout Design

```
┌──────────────────────────────────────────────────────────────┐
│ Jobs                                           [SSE] [+New] │
├──────────────────────┬───────────────────────────────────────┤
│ PROJECT: [dropdown]  │  Filter: [status] [source] [queue ▼] │
│ ■ system_fifo_queue  │  ┌──────────────────────────────────┐ │
│   ● Running (2/5)    │  │ Job Card (queue badge shown)     │ │
│ ■ system_parallel    │  │ Job Card                         │ │
│   ● Running (1/3)    │  │ Job Card                         │ │
│ ■ my-custom-queue    │  └──────────────────────────────────┘ │
│   ○ Paused (0/2)     │                        [Detail Drawer]│
│ [+ Add Queue]        │                                       │
│ Master: [▶/⏸]        │                                       │
└──────────────────────┴───────────────────────────────────────┘
```

## Part C: Integration Testing

### Tasks

| # | Task | Details |
|---|------|---------|
| 1 | **Validate migration** | Run migration on test database. Verify: (a) `job_queues` table created, (b) System queues seeded for all projects, (c) All job data cleared |
| 2 | **Test queue CRUD** | Create, read, update, delete custom queues. System queue protection (403). Reserved names (400). |
| 3 | **Test queue operations** | Start/stop queues. Pause affects job processing. Resume re-enables. |
| 4 | **Test job submission with queue** | Submit job with queue name. Verify queue assignment. |
| 5 | **Test parallel queue concurrency** | Create parallel queue with concurrency=3. Submit 5 jobs. Verify 3 process concurrently. |
| 6 | **Test queue deletion** | Delete queue with only PENDING jobs → reassigned. With PROCESSING jobs → 409 Conflict. |
| 7 | **Test auto-provisioning** | Create new project. Verify `system_fifo_queue` and `system_parallel_queue` exist. |
| 8 | **Test IDOR protection (W3)** | Access queue with wrong project_id in path → 404 (never 403). |
| 9 | **Test frontend integration** | Queue panel loads, create queue works, start/stop works, job filtering works. |
| 10 | **Test edge cases** | (a) Submit to paused queue → 409, (b) Submit to non-existent queue → 404, (c) Case collision ("My Queue" vs "my queue") → rejected (W1), (d) Two queues, one paused → only running processes |

## Key Files

### Backend (new/modified)
- `daemon/routers/queues.py` — **NEW**: Queue management endpoints with IDOR validation (W3)
- `daemon/routers/jobs.py` — **MODIFY**: queue params
- `daemon/routers/schemas.py` — **MODIFY**: queue schemas
- `daemon/routers/projects.py` — **MODIFY**: auto-provisioning
- `daemon/api.py` — **MODIFY**: register new queue router

### Frontend (new/modified)
- `frontend/src/app/models/job-queue.model.ts` — **NEW**: TypeScript interfaces
- `frontend/src/app/services/queue.service.ts` — **NEW**: Queue REST API service
- `frontend/src/app/services/queue-sse.service.ts` — **NEW**: Queue SSE service
- `frontend/src/app/components/queue-list/` — **NEW**: Queue list panel
- `frontend/src/app/components/queue-create-dialog/` — **NEW**: Create queue dialog
- `frontend/src/app/pages/jobs/` — **MODIFY**: Queue panel, queue filter
- `frontend/src/app/components/job-card/` — **MODIFY**: Queue badge
- `frontend/src/app/components/job-create-dialog/` — **MODIFY**: Queue selector
- `frontend/src/app/services/job.service.ts` — **MODIFY**: Queue params

## Constraints
- **W3 (IDOR):** Every queue endpoint that takes `queue_id` in the path must validate `queue.project_id == path_project_id`. Return `404` (never `403`).
- All new endpoints follow existing error response format: `{"detail": "message"}`
- `DELETE` returns `409 Conflict` if PROCESSING jobs exist in the queue
- **S5:** Use ONLY Angular Material — no ng-zorro-antd. Queue selector uses `mat-select`.
- SSE reconnection follows existing `JobSseService` pattern
- Tests should work against a real SQLite database (in-memory or temp file)

## Deliverables
### API
- [ ] Queue management router with all 9 endpoints
- [ ] **W3:** All queue endpoints validate project ownership → 404
- [ ] Router registered in FastAPI app
- [ ] `JobCreateRequest` accepts `queue_name` with normalization
- [ ] `JobResponse` includes `queue_id`, `queue_name`
- [ ] `GET /api/jobs` supports `queue_id` filter
- [ ] SSE events include queue information
- [ ] `DELETE` returns 409 when PROCESSING jobs exist
- [ ] OpenAPI docs document two-level pause behavior

### Frontend
- [ ] TypeScript interfaces for all queue-related types
- [ ] `QueueService` with all API methods
- [ ] `QueueSseService` with real-time queue event handling
- [ ] `QueueListComponent` with project validation
- [ ] `QueueCreateDialogComponent` with form validation
- [ ] JobsComponent updated with queue sidebar panel and queue filter
- [ ] JobCardComponent showing queue badge
- [ ] JobCreateDialogComponent with mat-select queue selector (**S5**)
- [ ] Responsive layout for mobile

### Integration
- [ ] Migration validated: system queues seeded
- [ ] System queue protection tested (403 on delete, 400 on reserved name)
- [ ] Parallel queue concurrency tested (N concurrent jobs)
- [ ] Queue deletion: 409 for PROCESSING, atomic reassign for PENDING
- [ ] Auto-provisioning at router layer verified (W2)
- [ ] IDOR protection: all endpoints return 404 for wrong project (W3)
- [ ] Case-insensitive queue name uniqueness (W1)
