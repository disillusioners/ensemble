# Job Queue Implementation Guide

> **Historical artifact (2026-03-16):** This is the original Sprint 1 delivery log. The system has since been significantly extended and refactored. For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md) and [`docs/features/job-queue.md`](job-queue.md).

> **Sprint 1 Complete** - Last updated: 2026-03-16

## Sprint 1 Summary

Sprint 1 delivered the foundational Job Queue infrastructure for agents-ensemble. The implementation ensures that only one instance can modify a project's files at a time through per-project locking with priority-based queuing.

### What Was Built

- **Job Persistence Layer** - SQLite-backed repository with full CRUD operations
- **Lock Management** - Per-project mutex with waiter queue support
- **Core Service** - JobQueueService with enqueue/dequeue/cancel/list operations
- **Basic API** - POST and GET endpoints for task submission and status polling

### Commits

| Commit | Description | Files Changed |
|--------|-------------|---------------|
| `1350a64` | Foundation: schema, models, repository | 561 lines |
| `4c1a24a` | Lock Management: JobLockManager | 401 lines |
| `b4d7ff3` | Core Service: JobQueueService | 284 lines |
| `4639a675` | Basic API: POST/GET endpoints | 431 lines |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENTS                                      │
│         (API Clients, Telegram, Webhooks, Scheduler)                │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ HTTP
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      API LAYER (daemon/routers/)                    │
│                                                                          │
│   POST /api/tasks              - Submit task (enqueue or immediate)  │
│   GET  /api/tasks/{task_id}   - Poll task status/result             │
│   GET  /api/tasks             - List tasks with filters              │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    JOB QUEUE SERVICE                                │
│                                                                          │
│  ┌─────────────────────┐     ┌──────────────────────────────────────┐  │
│  │  JobQueueService   │────▶│        JobLockManager               │  │
│  │                     │     │                                      │  │
│  │  • enqueue()        │     │  • acquire_lock(project_id)         │  │
│  │  • get_task()       │     │  • release_lock(project_id)        │  │
│  │  • list_tasks()     │     │  • wait_for_lock(project_id)        │  │
│  └─────────┬───────────┘     └──────────────────────────────────────┘  │
│            │                                                         │
│            ▼                                                         │
│  ┌─────────────────────┐     ┌──────────────────────────────────────┐  │
│  │  JobRepository     │     │        ProjectLockRegistry           │  │
│  │  (SQLite)           │     │        (In-memory)                  │  │
│  │                     │     │                                      │  │
│  │  • create()         │     │  _locks: dict[str, LockInfo]        │  │
│  │  • update()         │     │  _waiters: dict[str, Queue]         │  │
│  │  • get()            │     │                                      │  │
│  │  • list()           │     │                                      │  │
│  └─────────────────────┘     └──────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Task Submission** (POST /api/tasks)
   - Validate request
   - If no project_id → spawn instance immediately
   - If project_id → acquire lock
      - Lock free → spawn instance immediately
     - Lock held → queue task, return 202

2. **Task Processing**
   - Lock acquired → instance spawns
   - Instance processes → updates task status
   - Instance completes → release lock → process next

3. **Task Status** (GET /api/tasks/{id})
   - Query SQLite for task state
   - Return status + result/error

---

## API Usage Examples

### Submit a Task (Immediate Execution)

```bash
curl -X POST http://localhost:8079/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "agent_dir": "agents/coder",
    "message": "Fix the login bug in auth.py"
  }'
```

**Response (200):**
```json
{
  "task_id": "task-uuid",
  "status": "processing",
  "instance_id": "instance-uuid",
  "message": "Task started immediately"
}
```

### Submit a Task (Queued)

```bash
curl -X POST http://localhost:8079/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "agent_dir": "agents/coder",
    "message": "Add new feature",
    "project_id": "project-uuid",
    "priority": 8
  }'
```

**Response (202):**
```json
{
  "task_id": "task-uuid",
  "status": "pending",
  "position": 2,
  "message": "Job queued, waiting for project lock"
}
```

### Get Task Status

```bash
curl http://localhost:8079/api/tasks/task-uuid
```

**Response (200):**
```json
{
  "task_id": "task-uuid",
  "status": "completed",
  "instance_id": "instance-uuid",
  "created_at": "2026-03-16T10:00:00Z",
  "started_at": "2026-03-16T10:00:01Z",
  "completed_at": "2026-03-16T10:05:00Z",
  "result_summary": "Fixed login bug - added token refresh logic"
}
```

### List Tasks

```bash
curl "http://localhost:8079/api/tasks?status=pending&project_id=project-uuid&limit=20"
```

**Response (200):**
```json
{
  "tasks": [
    {
      "task_id": "task-uuid-1",
      "status": "pending",
      "priority": 8,
      "project_id": "project-uuid",
      "created_at": "2026-03-16T10:00:00Z"
    }
  ],
  "total": 1
}
```

---

## Current Limitations

**[Shipped]** — see the current architecture docs.

The following features are planned for Sprint 2:

| Feature | Description | Status |
|---------|-------------|--------|
| DELETE /tasks/{id} | Cancel pending or abort running tasks | ⏳ Pending |
| SSE /tasks/{id}/events | Real-time task progress via Server-Sent Events | ⏳ Pending |
| TaskProcessor | Background worker that processes queued tasks | ⏳ Pending |
| InstanceManager Integration | Enhanced terminate_instance() with cascade | ⏳ Pending |
| Scheduler Integration | Route scheduled jobs through job queue | ⏳ Pending |

### Workaround for Missing DELETE

Currently, running tasks cannot be cancelled via API. To stop a task:
1. Terminate the instance directly: `DELETE /api/instances/{instance_id}`
2. The task will be marked as failed automatically

### Workaround for Missing SSE

Poll `GET /api/tasks/{task_id}` for status updates. Task transitions through:
- `pending` → `processing` → `completed` (or `failed`, `cancelled`)

---

## Sprint 2 Roadmap

**[Shipped]** — see the current architecture docs.

### Background TaskProcessor

The TaskProcessor is a background worker that continuously monitors queued tasks and processes them when locks become available.

```
┌─────────────────────────────────────────────────────────────────┐
│                    TaskProcessor (Background)                   │
│                                                                  │
│  while running:                                                 │
│    1. Get projects with pending tasks                          │
│    2. For each project:                                        │
│       a. Get next task (highest priority, FIFO)                │
│       b. Try to acquire lock                                   │
│       c. If acquired:                                          │
│          - Spawn instance                                        │
│          - Wait for completion                                  │
│          - Release lock                                         │
│          - Process next task                                    │
└─────────────────────────────────────────────────────────────────┘
```

### DELETE Endpoint

Cancel pending tasks or abort running tasks:

```bash
# Cancel pending task
curl -X DELETE http://localhost:8079/api/tasks/task-uuid
# Response: { "task_id": "uuid", "status": "cancelled" }

# Abort running task (terminates instance)
curl -X DELETE http://localhost:8079/api/tasks/task-uuid
# Response: { "task_id": "uuid", "status": "cancelled", "message": "Task aborted" }
```

### SSE Endpoint

Subscribe to real-time task updates:

```bash
curl -N http://localhost:8079/api/tasks/task-uuid/events \
  -H "Accept: text/event-stream"
```

Events:
- `connected` - Initial connection
- `content_chunk` - Stream output chunks
- `completed` - Task finished
- `failed` - Task failed
- `cancelled` - Task cancelled

---

## File Reference

| File | Purpose |
|------|---------|
| `daemon/repositories/job_queue/models.py` | SQLModel definitions |
| `daemon/repositories/job_queue/repository.py` | Database operations |
| `daemon/services/job_lock_manager.py` | Per-project locking |
| `daemon/services/job_queue_service.py` | Core queue logic |
| `daemon/routers/jobs.py` | API endpoints |
| `daemon/routers/schemas.py` | Request/response models |
| `docs/features/job-queue.md` | Full design document |

---

## Related Documentation

- [Job Queue Design Document](./job-queue.md) - Complete feature specification
