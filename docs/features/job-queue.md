# Job Queue Feature Design Document

> **Note (2026-06-18):** This doc predates the CorrelationManager migration. For the current message-processing architecture — including the unified `MessageProcessingPipeline`, `CorrelationManager`, and `ExecutionGate` — see [`docs/architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md). The Job Queue data model and API reference below remain accurate.

## Overview

This document describes the Job Queue feature for agents-ensemble. The feature ensures that only one instance can modify a project's files at a time by implementing a per-project job queue with the following characteristics:

- **Lock by project_id** - Trust-based locking, no filesystem enforcement
- **Per-project serialization** - Only one job per project can run at a time
- **Priority-based scheduling** - Higher priority jobs execute first (1-10 scale)
- **Crash recovery** - Jobs persisted in SQLite for durability
- **Seamless scheduler integration** - Optional project-based queuing

---

## Implementation Status

Sprint 1 shipped; the system has since been extended. See the canonical architecture doc for the current state.

### Sprint 1 Commits (historical)

| Commit | Description | Lines |
|--------|-------------|-------|
| `1350a64` | Foundation: schema, models, repository | 561 |
| `4c1a24a` | Lock Management: JobLockManager | 401 |
| `b4d7ff3` | Core Service: JobQueueService | 284 |
| `4639a675` | Basic API: POST/GET endpoints | 431 |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              EXTERNAL CLIENTS                                    │
│  (API Clients, Telegram, Webhooks, Scheduled Jobs)                              │
└────────────────────────────────────────────────────────────────────────┬────────┘
                                                                         │
                                                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              API LAYER (daemon/api.py)                           │
│  • POST /tasks                    - Submit task (enqueue or immediate)         │
│  • GET /tasks/{task_id}           - Poll task status/result                    │
│  • GET /tasks/{task_id}/events    - SSE subscription for task completion       │
│  • DELETE /tasks/{task_id}        - Cancel pending/abort running task          │
│  • GET /tasks                     - List tasks (optional filters)              │
└────────────────────────────────────────────────────────────────────────┬────────┘
                                                                         │
                                                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          JOB QUEUE SERVICE                                      │
│                                                                                  │
│  ┌─────────────────────┐    ┌──────────────────────────────────────────────┐ │
│  │  JobQueueService   │    │           JobLockManager                      │ │
│  │                     │    │                                              │ │
│  │  • enqueue()       │    │  • acquire_lock(project_id) → task_id        │ │
│  │  • dequeue()       │    │  • release_lock(project_id)                  │ │
│  │  • get_task()      │    │  • get_locked_project(instance_id)              │ │
│  │  • cancel_task()   │    │  • wait_for_lock(project_id, timeout)         │ │
│  │  • list_tasks()   │    │                                              │ │
│  └─────────┬───────────┘    └──────────────────────────────────────────────┘ │
│            │                                                                     │
│            │                                                                     │
│  ┌─────────▼───────────┐    ┌──────────────────────────────────────────────┐ │
│  │  JobRepository      │    │           ProjectLockRegistry                │ │
│  │  (SQLite)            │    │           (In-memory + SQLite)                 │ │
│  │                      │    │                                              │ │
│  │  • create()         │    │  _locks: dict[str, LockInfo]                  │ │
│  │  • update()         │    │  _waiters: dict[str, asyncio.Queue]           │ │
│  │  • get()            │    │                                              │ │
│  │  • list()          │    │                                              │ │
│  │  • delete()        │    │                                              │ │
│  └─────────────────────┘    └──────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┬────────┘
                                                                         │
                                │                                            │
                                │ Enqueue                                     │ Process
                                ▼                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          EXISTING COMPONENTS                                    │
│                                                                                  │
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌───────────────────┐ │
│  │  InstanceManager    │◄───│  InputMessageQueue  │───►│  SchedulerAdapter │ │
│  │  (daemon/manager.py)│    │  (daemon/queue.py)  │    │  (scheduler.py)   │ │
│  │                     │    │                     │    │                   │ │
│  │  • spawn_instance() │    │  • enqueue()        │    │  • _emit_message()│ │
│  │  • send_message()  │    │  • dequeue()        │    │  • project_id    │ │
│  │  • terminate_      │    │  • watchdog         │    │    (optional)     │ │
│  │    instance() ←NEW  │    │                     │    │                   │ │
│  └─────────────────────┘    └─────────────────────┘    └───────────────────┘ │
│                                                                                  │
│  ┌─────────────────────┐    ┌─────────────────────┐                           │
│  │  EventBroadcaster  │    │  InstanceRepository │                           │
│  │  (daemon/events.py)│    │  (instance/models)  │                           │
│  │                     │    │                      │                           │
│  │  • broadcast()     │    │  • create()          │                           │
│  │  • event_to_sse() │    │  • update_status()   │                           │
│  └─────────────────────┘    │  • terminate_instance │                           │
│                             └─────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Models

### JobItem (SQLModel)

Location: `daemon/repositories/job_queue/models.py`

```python
class JobItem(SQLModel, table=True):
    """Job queue item - persisted for crash recovery."""
    __tablename__ = "job_queue_items"

    # Primary identification
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    
    # Task content
    agent_dir: str
    message: str
    source: str = "api"  # "api", "telegram", "scheduler", "webhook"
    
    # Project queuing (None = skip queue, execute immediately)
    project_id: Optional[str] = Field(default=None, index=True)
    
    # Scheduling
    priority: int = Field(default=5, ge=1, le=10)  # 1=lowest, 10=highest
    status: str = Field(default=JobStatus.PENDING.value, index=True)
    
    # Timing
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # Result (filled on completion)
    instance_id: Optional[str] = Field(default=None, index=True)
    error_message: Optional[str] = None
    result_summary: Optional[str] = None
    
    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    
    # Cancellation
    cancelled_at: Optional[str] = None


class JobStatus(str, Enum):
    """Task status values."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskLockInfo(Pydantic BaseModel):
    """In-memory lock tracking."""
    task_id: str
    project_id: str
    instance_id: str
    locked_at: datetime
```

### Relationships

```
JobItem
├── project_id (FK to Project.project_id, optional)
├── instance_id (FK to Instance.instance_id, optional)
└── status (indexed for filtering)
```

---

## API Endpoints

### 1. POST /tasks

Submit a new task for processing.

**Request:**
```http
POST /api/tasks
Content-Type: application/json

{
    "agent_dir": "/agents/developer",
    "message": "Fix the login bug in auth.py",
    "project_id": "optional-project-uuid",  // Optional: if provided, goes to queue
    "priority": 7,                          // Optional: 1-10, default 5
    "source": "api"                         // Optional: default "api"
}
```

**Response (immediate execution - no project_id or no lock):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "processing",
    "instance_id": "instance-uuid",
    "message": "Task started immediately"
}
```

**Response (queued - has project_id and lock held by another):**
```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "pending",
    "position": 3,
    "message": "Job queued, waiting for project lock"
}
```

**Response (validation error):**
```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/json

{
    "error": "Validation Error",
    "details": [
        {"field": "priority", "message": "Must be between 1 and 10"}
    ]
}
```

---

### 2. GET /tasks/{task_id}

Poll task status and result.

**Request:**
```http
GET /api/tasks/task-uuid
```

**Response (pending):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "pending",
    "priority": 7,
    "created_at": "2025-03-15T10:00:00Z",
    "position": 2
}
```

**Response (completed):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "completed",
    "instance_id": "instance-uuid",
    "created_at": "2025-03-15T10:00:00Z",
    "started_at": "2025-03-15T10:00:01Z",
    "completed_at": "2025-03-15T10:05:00Z",
    "result_summary": "Fixed login bug - added token refresh logic"
}
```

**Response (failed):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "failed",
    "error_message": "Agent error: Authentication failed for user 'test'",
    "created_at": "2025-03-15T10:00:00Z",
    "started_at": "2025-03-15T10:00:01Z",
    "completed_at": "2025-03-15T10:00:05Z"
}
```

**Response (not found):**
```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{
    "error": "Task not found",
    "task_id": "invalid-uuid"
}
```

---

### 3. GET /tasks/{task_id}/events

Subscribe to task completion via SSE.

**Request:**
```http
GET /api/tasks/task-uuid/events
Accept: text/event-stream
```

**Response:**
```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Transfer-Encoding: chunked

event: connected
data: {"task_id": "task-uuid", "status": "processing"}

event: content_chunk
data: {"chunk": "Analyzing auth.py..."}

event: content_chunk
data: {"chunk": "Found issue at line 42..."}

event: completed
data: {"task_id": "task-uuid", "status": "completed", "instance_id": "instance-uuid", "result_summary": "..."}

event: keepalive
data: {}
```

---

### 4. DELETE /tasks/{task_id}

Cancel a pending task or abort a running task.

**Request:**
```http
DELETE /api/tasks/task-uuid
```

**Response (cancelled pending):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "cancelled",
    "message": "Task cancelled successfully"
}
```

**Response (aborted running - terminates instance):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "cancelled",
    "message": "Task aborted, instance terminated with children"
}
```

**Response (already completed):**
```http
HTTP/1.1 409 Conflict
Content-Type: application/json

{
    "error": "Cannot cancel",
    "message": "Task already completed"
}
```

---

### 5. GET /tasks

List tasks with optional filters.

**Request:**
```http
GET /api/tasks?status=pending&project_id=uuid&limit=20
```

**Response:**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "tasks": [
        {
            "task_id": "task-uuid-1",
            "status": "pending",
            "priority": 8,
            "project_id": "project-uuid",
            "created_at": "2025-03-15T10:00:00Z"
        },
        {
            "task_id": "task-uuid-2",
            "status": "pending",
            "priority": 5,
            "project_id": "project-uuid",
            "created_at": "2025-03-15T09:59:00Z"
        }
    ],
    "total": 2
}
```

---

## Flow Diagrams

### Enqueue Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            ENQUEUE FLOW                                         │
└────────────────────────────────────────────────────────────────────────┬────────┘
                                                                         │
                              ┌───────────────────────┐
                              │  POST /tasks          │
                              │  (API Layer)          │
                              └───────────┬───────────┘
                                          │
                                          ▼
                         ┌────────────────────────────────┐
                         │  Validate request             │
                         │  • agent_dir exists           │
                         │  • priority in 1-10           │
                         │  • project_id valid (if given) │
                         └───────────────┬────────────────┘
                                         │
                    ┌────────────────────┴────────────────────┐
                    │                                           │
                    ▼                                           ▼
        ┌───────────────────────┐               ┌───────────────────────────┐
        │ project_id is None     │               │ project_id is provided    │
        └───────────┬───────────┘               └────────────┬──────────────┘
                    │                                           │
                    ▼                                           ▼
        ┌───────────────────────┐               ┌───────────────────────────┐
        │ Execute immediately   │               │ Acquire project lock      │
        │ • spawn_instance()    │               │ • LockManager.acquire()   │
        │ • Return 200 +        │               └────────────┬──────────────┘
        │   instance_id          │                                │
        └───────────────────────┘                    ┌──────────┴──────────┐
                                                      │                     │
                                                      ▼                     ▼
                                          ┌───────────────────┐ ┌─────────────────────┐
                                          │ Lock acquired     │ │ Lock held by other  │
                                          │ (no waiters)      │ │ (wait for lock)     │
                                          └─────────┬─────────┘ └──────────┬──────────┘
                                                    │                     │
                                                    ▼                     ▼
                                        ┌───────────────────┐ ┌─────────────────────┐
                                        │ Create task       │ │ Create task         │
                                        │ status=processing│ │ status=pending      │
                                        │ Return 200        │ │ Return 202 + pos    │
                                        └───────────────────┘ └─────────────────────┘
```

### Dequeue & Process Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         DEQUEUE & PROCESS FLOW                                   │
└────────────────────────────────────────────────────────────────────────┬────────┘
                                                                         │
                              ┌───────────────────────┐
                              │  Task Processor       │  (Background task)
                              │  (Runs continuously)  │
                              └───────────┬───────────┘
                                          │
                                          ▼
                         ┌────────────────────────────────┐
                         │  For each project with queue   │
                         │  (per-project loop)            │
                         └───────────────┬────────────────┘
                                         │
                                         ▼
                         ┌────────────────────────────────┐
                         │  SELECT next task by:          │
                         │  1. Priority DESC (highest)   │
                         │  2. created_at ASC (FIFO)     │
                         │  WHERE project_id = ?         │
                         │  AND status = pending         │
                         │  LIMIT 1                       │
                         └───────────────┬────────────────┘
                                         │
                    ┌────────────────────┴────────────────────┐
                    │ No tasks found? Continue to next project│
                    └─────────────────────────────────────────┘
                                         │
                    ┌────────────────────┴────────────────────┐
                    │ Task found                               │
                    └─────────────┬───────────────────────────┘
                                  │
                                  ▼
                      ┌───────────────────────────┐
                      │ Update task:              │
                      │ status = processing       │
                      │ started_at = now()        │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │ Spawn instance:            │
                      │ InstanceManager.spawn_     │
                      │   instance()               │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │ Update task with          │
                      │ instance_id                │
                      │                           │
                      │ Send message to instance   │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │ Wait for instance         │
                      │ completion (SSE/events) │
                      └─────────────┬─────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
              ▼                     ▼                     ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │ Completed OK    │  │ Failed/Error    │  │ Cancelled       │
    │                 │  │                 │  │                 │
    │ • Update:       │  │ • Update:       │  │ • Update:       │
    │   status=       │  │   status=failed │  │   status=       │
    │   completed     │  │   error=msg     │  │   cancelled     │
    │ • Terminate     │  │ • Terminate     │  │ • Terminate     │
    │   children     │  │   children     │  │   children     │
    │ • Release lock │  │ • Release lock │  │ • Release lock │
    └─────────────────┘  └─────────────────┘  └─────────────────┘
              │                     │                     │
              └─────────────────────┴─────────────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │  Release project lock    │
                      │  JobLockManager.release │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │  Process next task for    │
                      │  this project (loop)     │
                      └───────────────────────────┘
```

### Cancellation Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            CANCELLATION FLOW                                     │
└────────────────────────────────────────────────────────────────────────┬────────┘
                                                                         │
                              ┌───────────────────────┐
                              │  DELETE /tasks/{id}   │
                              │  (API Layer)          │
                              └───────────┬───────────┘
                                          │
                                          ▼
                         ┌────────────────────────────────┐
                         │  Load task from repository     │
                         └───────────────┬────────────────┘
                                         │
                    ┌────────────────────┴────────────────────┐
                    │                                             │
                    ▼                                             ▼
        ┌───────────────────────┐               ┌───────────────────────────┐
        │ Task is PENDING      │               │ Task is PROCESSING        │
        └───────────┬───────────┘               └────────────┬──────────────┘
                    │                                            │
                    ▼                                            ▼
        ┌───────────────────────┐               ┌───────────────────────────┐
        │ Update:               │               │ Cancel in-flight request │
        │ status = cancelled    │               │ via RequestRegistry      │
        │ cancelled_at = now()  │               │                           │
        └───────────┬───────────┘               └────────────┬──────────────┘
                    │                                            │
                    │                                            ▼
                    │                                ┌───────────────────────────┐
                     │                                │ Terminate instance:        │
                     │                                │ InstanceManager.terminate_ │
                     │                                │   instance() ENHANCED      │
                    │                                │                           │
                    │                                │ → Cancel active requests  │
                    │                                │ → Delete queue messages   │
                    │                                │ → Terminate children      │
                    │                                └────────────┬──────────────┘
                    │                                           │
                    └────────────────────┬────────────────────┘
                                         │
                                         ▼
                         ┌────────────────────────────────┐
                         │  Release project lock          │
                         │  (if held by this task)       │
                         └───────────────┬────────────────┘
                                         │
                                         ▼
                         ┌────────────────────────────────┐
                         │  Trigger next task for project │
                         │  (if any pending)              │
                         └────────────────────────────────┘
```

---

## Integration Points

### 1. Scheduler Integration

Location: `daemon/sources/adapters/scheduler.py`

**Enhancement:** Add optional `project_id` field to schedule configuration.

**Schedule Config (new fields):**
```python
# daemon/sources/adapters/scheduler.py

@dataclass
class ScheduleConfig:
    source_id: str
    agent_dir: str
    message: str
    
    # Existing fields
    schedule_type: str
    cron: str | None = None
    interval_seconds: int | None = None
    run_at: str | None = None
    
    # NEW: Project-based queuing
    project_id: str | None = None  # If set, task goes through queue
    priority: int = 5  # Default priority
```

**Logic in `_emit_scheduled_message()`:**
```python
# In scheduler.py - _emit_scheduled_message() enhancement

async def _emit_scheduled_message(self):
    # Build task payload
    task_payload = {
        "agent_dir": self._agent_dir,
        "message": self._message_content,
        "source": f"scheduler:{self._source_id}",
        "priority": self._config.priority,
    }
    
    if self._config.project_id:
        # Route through job queue
        task_payload["project_id"] = self._config.project_id
        await job_queue_service.enqueue(**task_payload)
    else:
        # Immediate execution (existing behavior)
        incoming = IncomingMessage(...)
        await self._emit_message(incoming)
```

---

### 2. InstanceManager Integration

Location: `daemon/manager.py`

**Enhancement:** Add job_queue_service dependency and enhance terminate_instance().

```python
# daemon/manager.py

class InstanceManager:
    def __init__(self, ...):
        # Existing initialization
        ...
        
        # NEW: Job queue service
        self._job_queue_service: JobQueueService | None = None
    
    def set_job_queue_service(self, service: JobQueueService):
        """Set job queue service for integration."""
        self._job_queue_service = service
    
    def terminate_instance(self, instance_id: str) -> bool:
        """Terminate instance with full cleanup."""
        
        # 1. NEW: Cancel any active requests for this instance
        active_requests = self._request_registry.get_active_for_instance(instance_id)
        for msg_id in active_requests:
            self._request_registry.cancel(msg_id, CancellationReason.MANUAL)
        
        # 2. NEW: Clean up queue messages for this instance
        if self._job_queue_service:
            self._job_queue_service.cancel_tasks_by_instance(instance_id)
        
        # 3. NEW: Terminate child instances (cascade)
        children = self._instance_repository.get_children(instance_id)
        for child_id in children:
            self.terminate_instance(child_id)
        
        # 4. Existing logic
        self._processing.discard(instance_id)
        self.broadcaster.cleanup_instance(instance_id)
        
        if instance_id in self.instances:
            del self.instances[instance_id]
        
        self._instance_repository.update_status(instance_id, "terminated")
        
        # 5. NEW: Release project lock if this instance held one
        if self._job_queue_service:
            self._job_queue_service.release_lock_by_instance(instance_id)
        
        return True
```

---

### 3. InputMessageQueue Integration

Location: `daemon/queue.py`

The Job Queue is orthogonal to the existing InputMessageQueue:

- **InputMessageQueue**: Per-session message queuing (what to send to a session)
- **JobQueue**: Per-project task queuing (which instance can run)

They work at different layers:
1. JobQueue decides which task (instance) can proceed
2. Once instance is running, InputMessageQueue handles its message stream

---

### 4. EventBroadcaster Integration

Location: `daemon/events.py`

**Enhancement:** Task events should integrate with existing event system.

```python
# Task events mirror session events for consistency

TASK_EVENT_TYPES = [
    "job_queued",      # Task added to queue
    "task_started",    # Task started processing
    "task_progress",   # Progress updates
    "task_completed",  # Task finished successfully
    "task_failed",     # Task failed
    "task_cancelled",  # Task was cancelled
]
```

---

## Enhancement: terminate_instance() for Cascade to Children

### Current Implementation Gap

Current `terminate_instance()` in `daemon/manager.py:1548-1572`:
- ❌ Does NOT cancel in-flight requests
- ❌ Does NOT clean up queue messages
- ❌ Does NOT terminate child sessions

### Enhanced Implementation

```python
# daemon/manager.py - Enhanced terminate_instance()

def terminate_instance(
    self, 
    instance_id: str, 
    *,
    cancel_requests: bool = True,
    cleanup_queue: bool = True,
    cascade_children: bool = True,
    reason: str = CancellationReason.MANUAL.value
) -> bool:
    """
    Terminate an instance with full cleanup.
    
    Args:
        instance_id: The instance to terminate.
        cancel_requests: Cancel in-flight requests for this instance.
        cleanup_queue: Remove pending messages from queue.
        cascade_children: Also terminate child instances.
        reason: Cancellation reason for tracking.
    
    Returns:
        True if terminated, False if not found.
    """
    # Early validation
    if instance_id not in self.instances:
        return False
    
    # 1. Cancel in-flight requests
    if cancel_requests:
        active = self._request_registry.get_active_for_instance(instance_id)
        for msg_id in active:
            self._request_registry.cancel(msg_id, reason)
    
    # 2. Clean up queue messages
    if cleanup_queue and hasattr(self, '_job_queue_service'):
        self._job_queue_service.cancel_tasks_by_instance(instance_id)
    
    # 3. Cascade to children FIRST (they hold resources too)
    if cascade_children:
        children = self._instance_repository.get_children(instance_id)
        for child_id in children:
            self.terminate_instance(
                child_id,
                cancel_requests=cancel_requests,
                cleanup_queue=cleanup_queue,
                cascade_children=True,
                reason=reason
            )
    
    # 4. Stop processing this instance
    self._processing.discard(instance_id)
    
    # 5. Clean up event broadcaster
    self.broadcaster.cleanup_instance(instance_id)
    
    # 6. Remove from memory
    del self.instances[instance_id]
    
    # 7. Update database status
    self._instance_repository.update_status(instance_id, "terminated")
    
    # 8. Release project lock (if job queue is active)
    if hasattr(self, '_job_queue_service') and self._job_queue_service:
        self._job_queue_service.release_lock_by_instance(instance_id)
    
    return True
```

### CancellationReason Enum Update

Location: `daemon/cancellation.py`

```python
class CancellationReason(str, Enum):
    """Reasons for cancellation."""
    TIMEOUT = "timeout"              # Watchdog timeout
    WATCHDOG_RETRY = "watchdog_retry"  # Watchdog retry exhausted
    MANUAL = "manual"              # User手动取消
    SHUTDOWN = "shutdown"           # System shutdown
    JOB_CANCELLED = "job_cancelled"  # Job queue cancellation (NEW)
    CHILD_CASCADE = "child_cascade"    # Cascaded from parent termination (NEW)
```

---

## Component Specifications

### JobQueueService

Location: `daemon/services/job_queue_service.py`

```python
class JobQueueService:
    """Manages task queuing with per-project locking."""
    
    def __init__(
        self,
        repository: JobRepository,
        instance_manager: InstanceManager,
        event_broadcaster: EventBroadcaster
    ):
        self._repository = repository
        self._instance_manager = instance_manager
        self._broadcaster = event_broadcaster
        self._lock_manager = JobLockManager(repository)
        self._processor: JobProcessor | None = None
    
    # ========== Public API ==========
    
    async def enqueue(
        self,
        agent_dir: str,
        message: str,
        source: str = "api",
        project_id: str | None = None,
        priority: int = 5
    ) -> JobItem:
        """
        Submit a task for processing.
        
        If project_id is None or no lock contention, executes immediately.
        Otherwise, queues for later processing.
        
        Returns task with status and (if immediate) instance_id.
        """
        pass
    
    async def get_task(self, task_id: str) -> JobItem | None:
        """Get task by ID."""
        pass
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or abort a running task."""
        pass
    
    async def list_tasks(
        self,
        status: JobStatus | None = None,
        project_id: str | None = None,
        limit: int = 50
    ) -> list[JobItem]:
        """List tasks with optional filters."""
        pass
    
    # ========== Lock Management ==========
    
    def acquire_lock(self, project_id: str, task_id: str) -> bool:
        """Acquire lock for project. Returns True if acquired."""
        pass
    
    def release_lock(self, project_id: str) -> None:
        """Release lock for project."""
        pass
    
    def release_lock_by_instance(self, instance_id: str) -> None:
        """Release any lock held by an instance."""
        pass
    
    def get_locked_instance(self, instance_id: str) -> str | None:
        """Get project_id if instance holds a lock."""
        pass
    
    # ========== Internal / Background ==========
    
    async def start_processor(self) -> None:
        """Start background task processor."""
        pass
    
    async def stop_processor(self) -> None:
        """Stop background task processor."""
        pass
```

### JobLockManager

Location: `daemon/services/job_lock_manager.py`

```python
@dataclass
class LockInfo:
    """Information about a held lock."""
    task_id: str
    project_id: str
    instance_id: str
    locked_at: datetime


class JobLockManager:
    """Manages per-project locks for task execution."""
    
    def __init__(self, repository: JobRepository):
        self._repository = repository
        self._locks: dict[str, LockInfo] = {}  # project_id -> LockInfo
        self._waiters: dict[str, asyncio.Queue[tuple[str, asyncio.Event]]] = {}
    
    def acquire(self, project_id: str, task_id: str, instance_id: str) -> bool:
        """
        Try to acquire lock for project.
        
        Args:
            project_id: The project to lock
            task_id: The task acquiring the lock
            instance_id: The instance running the task
            
        Returns:
            True if lock acquired, False if already held
        """
        if project_id in self._locks:
            return False
        
        self._locks[project_id] = LockInfo(
            task_id=task_id,
            project_id=project_id,
            instance_id=instance_id,
            locked_at=datetime.utcnow()
        )
        return True
    
    def release(self, project_id: str, task_id: str) -> bool:
        """
        Release lock if held by specified task.
        
        Returns:
            True if released, False if not held by this task
        """
        if project_id not in self._locks:
            return False
        
        if self._locks[project_id].task_id != task_id:
            return False
        
        del self._locks[project_id]
        
        # Notify next waiter
        self._notify_waiter(project_id)
        return True
    
    def release_by_instance(self, instance_id: str) -> list[str]:
        """Release any locks held by an instance. Returns released project_ids."""
        released = []
        for project_id, info in list(self._locks.items()):
            if info.instance_id == instance_id:
                del self._locks[project_id]
                released.append(project_id)
                self._notify_waiter(project_id)
        return released
    
    def is_locked(self, project_id: str) -> bool:
        """Check if project is currently locked."""
        return project_id in self._locks
    
    def get_lock_info(self, project_id: str) -> LockInfo | None:
        """Get lock info for project."""
        return self._locks.get(project_id)
    
    def _notify_waiter(self, project_id: str) -> None:
        """Notify next waiting task that lock is available."""
        if project_id in self._waiters:
            try:
                _, event = self._waiters[project_id].get_nowait()
                event.set()
            except asyncio.QueueEmpty:
                del self._waiters[project_id]
```

### JobProcessor (Background Worker)

Location: `daemon/services/task_processor.py`

```python
class JobProcessor:
    """Background worker that processes queued tasks."""
    
    def __init__(
        self,
        job_queue_service: JobQueueService,
        instance_manager: InstanceManager
    ):
        self._job_queue = job_queue_service
        self._instance_manager = instance_manager
        self._running = False
        self._tasks: list[asyncio.Task] = []
    
    async def start(self) -> None:
        """Start the processor."""
        self._running = True
        # Start one worker per project (or limited pool)
        for _ in range(MAX_CONCURRENT_PROJECTS):
            task = asyncio.create_task(self._run_worker())
            self._tasks.append(task)
    
    async def stop(self) -> None:
        """Stop the processor."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
    
    async def _run_worker(self) -> None:
        """Worker loop - processes tasks continuously."""
        while self._running:
            # Get all projects with pending tasks
            projects_with_pending = await self._job_queue.get_projects_with_pending_tasks()
            
            for project_id in projects_with_pending:
                await self._process_project(project_id)
            
            # Brief sleep to prevent tight loop
            await asyncio.sleep(0.5)
    
    async def _process_project(self, project_id: str) -> None:
        """Process next task for a project."""
        # Get next task (highest priority, then FIFO)
        task = await self._job_queue._get_next_task(project_id)
        if not task:
            return
        
        # Try to acquire lock
        if not await self._job_queue._try_start_task(task):
            return  # Lock not acquired, skip for now
        
        try:
            # Execute task
            await self._execute_task(task)
        finally:
            # Release lock and process next
            await self._job_queue._complete_task(task)
    
    async def _execute_task(self, task: JobItem) -> None:
        """Execute a single task."""
        # Spawn instance
        instance_id = self._instance_manager.spawn_instance(
            agent_dir=task.agent_dir,
            instance_id=None  # Auto-generate
        )
        
        # Update task with instance
        task.instance_id = instance_id
        await self._job_queue._repository.update(task)
        
        # Send message to instance
        # wc-wake-report-integrity (T6b, D7 LOCKED 2026-08-30): the
        # legacy ``Manager.send_message`` was DELETED. The example now
        # uses ``enqueue_message`` (the durable wake path that ALL
        # surviving production traffic must cross).
        await self._instance_manager.enqueue_message(
            instance_id=instance_id,
            message=task.message,
            source=task.source
        )
        
        # Wait for completion (via events)
        await self._wait_for_instance_completion(instance_id, task.task_id)
```

---

## Error Handling

### Lock Acquisition Failure

If lock cannot be acquired (timeout or max waiters reached):
- Return 202 Accepted with queue position
- Task remains in PENDING status
- Processor will retry on lock release

### Session Spawn Failure

If instance cannot be created:
- Update task status to FAILED
- Set error_message with reason
- Release any lock held
- Notify via events

### Instance Completion with Children

Per decision: When instance completes (success/fail/error), terminate all child instances as safety measure.

```python
async def _on_instance_completed(self, instance_id: str, task_id: str) -> None:
    """Handle instance completion."""
    # Terminate all children as safety measure
    children = self._instance_repository.get_children(instance_id)
    for child_id in children:
        self._instance_manager.terminate_instance(child_id)
    
    # Release project lock
    self._lock_manager.release_by_instance(instance_id)
    
    # Trigger next task for project
    await self._trigger_next_task(task.project_id)
```

---

## Future Considerations

### Task Dependencies (Future)

Not in initial scope, but designed to be extensible:

```python
# Future extension
class TaskDependency(Pydantic BaseModel):
    """Task dependency for future implementation."""
    task_id: str
    depends_on: str  # task_id this depends on
    dependency_type: str  # "blocks" | "follows"

# Query for next task would become:
"""
SELECT * FROM job_queue_items
WHERE project_id = ?
AND status = 'pending'
AND NOT EXISTS (
    SELECT 1 FROM task_dependencies
    WHERE task_id = job_queue_items.task_id
    AND status != 'completed'
)
ORDER BY priority DESC, created_at ASC
LIMIT 1
"""
```

### Priority Adjustment (Future)

- Allow dynamic priority adjustment (bump priority of waiting tasks)
- Admin UI to reorder queue

### Distributed Locking (Future)

Current implementation is single-node. For multi-node:
- Replace in-memory locks with Redis/distributed lock
- SQLite → PostgreSQL for shared state

---

## Testing Strategy

### Unit Tests

1. **JobQueueService**:
   - test_enqueue_immediate_no_project
   - test_enqueue_immediate_no_lock_contention
   - test_enqueue_queued_with_contention
   - test_priority_ordering
   - test_fifo_same_priority
   - test_cancel_pending
   - test_cancel_running

2. **JobLockManager**:
   - test_acquire_release
   - test_double_acquire_fails
   - test_release_wrong_task_fails
   - test_waiter_notification
   - test_release_by_instance

3. **JobProcessor**:
   - test_processes_queued_task
   - test_respects_priority
   - test_calls_next_on_complete

### Integration Tests

1. **End-to-end flow**:
   - Submit task → process → complete → next task triggered

2. **Scheduler integration**:
   - Scheduled job with project_id → queued
   - Scheduled job without project_id → immediate

3. **Cancellation**:
   - Cancel pending → removed from queue
   - Cancel running → instance terminated → children terminated

4. **Crash recovery**:
   - Simulate crash mid-task → task marked failed on restart
   - Orphaned locks cleared on startup

---

## Migration Notes

### Database Schema

New table needed:

```sql
CREATE TABLE job_queue_items (
    task_id TEXT PRIMARY KEY,
    agent_dir TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    project_id TEXT,
    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    instance_id TEXT,
    error_message TEXT,
    result_summary TEXT,
    metadata TEXT DEFAULT '{}',
    cancelled_at TEXT
);

CREATE INDEX idx_job_queue_project ON job_queue_items(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_job_queue_status ON job_queue_items(status);
CREATE INDEX idx_job_queue_instance ON job_queue_items(instance_id);
```

### Configuration

Add to `daemon/config.py`:

```python
class Config:
    # Job Queue settings
    job_queue_enabled: bool = True
    job_queue_max_waiters_per_project: int = 100
    job_queue_lock_timeout_seconds: int = 300
    job_queue_processor_interval_seconds: float = 0.5
```

### Backward Compatibility

- If job_queue service is not configured, fall back to immediate execution
- Scheduler with project_id but no queue → warning log, execute immediately
- Existing instances without task tracking → unaffected

---

## Implementation Checklist

### Sprint 1 ✅ COMPLETE

- [x] Create database table schema
- [x] Implement JobRepository
- [x] Implement JobLockManager
- [x] Implement JobQueueService
- [x] Add API endpoints (POST/GET /tasks)

### Sprint 2 ✅ SHIPPED

These items shipped after Sprint 1; the queue has since been significantly extended with the unified message-processing pipeline. See [`docs/architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md) for the current architecture.

- [x] Implement JobProcessor background worker
- [x] Add DELETE /tasks/{task_id} endpoint
- [x] Add SSE /tasks/{task_id}/events endpoint
- [x] Integrate with InstanceManager.terminate_instance()
- [x] Integrate with SchedulerAdapter
- [x] Add configuration options
- [x] Write unit tests
- [x] Write integration tests
- [x] Update documentation

---

*Document Version: 1.1*  
*Last Updated: 2026-03-16*
