# Task Queue Feature Design Document

## Overview

This document describes the Task Queue feature for agents-ensemble. The feature ensures that only one session can modify a project's files at a time by implementing a per-project task queue with the following characteristics:

- **Lock by project_id** - Trust-based locking, no filesystem enforcement
- **Per-project serialization** - Only one task per project can run at a time
- **Priority-based scheduling** - Higher priority tasks execute first (1-10 scale)
- **Crash recovery** - Tasks persisted in SQLite for durability
- **Seamless scheduler integration** - Optional project-based queuing

---

## Implementation Status

**Sprint 1: COMPLETE** ✅ (2026-03-16)

### Components

| Component | Status | Description |
|-----------|--------|-------------|
| TaskRepository | ✅ Complete | SQLite persistence with CRUD operations |
| TaskLockManager | ✅ Complete | Per-project lock management with waiters |
| TaskQueueService | ✅ Complete | Core enqueue/dequeue/cancel logic |
| API Endpoints (POST/GET) | ✅ Complete | Submit and query tasks |
| TaskProcessor | ⏳ Pending | Background worker for queued tasks |
| DELETE /tasks/{id} | ⏳ Pending | Cancel/abort tasks |
| SSE /tasks/{id}/events | ⏳ Pending | Real-time task updates |
| SessionManager Integration | ⏳ Pending | Enhanced terminate_session() |
| Scheduler Integration | ⏳ Pending | project_id routing |

### Sprint 1 Commits

| Commit | Description | Lines |
|--------|-------------|-------|
| `1350a64` | Foundation: schema, models, repository | 561 |
| `4c1a24a` | Lock Management: TaskLockManager | 401 |
| `b4d7ff3` | Core Service: TaskQueueService | 284 |
| `4639a675` | Basic API: POST/GET endpoints | 431 |

### Files Created

```
daemon/repositories/task_queue/
├── __init__.py
├── models.py           # TaskQueueItem, TaskStatus
└── repository.py       # TaskRepository (SQLite)

daemon/services/
├── __init__.py
├── task_lock_manager.py    # Per-project lock management
└── task_queue_service.py   # Core queue operations

daemon/routers/
├── __init__.py
├── schemas.py          # Pydantic request/response models
└── tasks.py            # FastAPI router for /api/tasks
```

### What's Working

- **POST /api/tasks** - Submit tasks (immediate or queued based on lock)
- **GET /api/tasks/{task_id}** - Query task status and results
- **GET /api/tasks** - List tasks with filters (status, project_id, limit)
- Priority-based queue ordering (1-10 scale)
- Per-project lock management with waiter queues
- Crash recovery via SQLite persistence

### Sprint 2 Roadmap

- Background TaskProcessor worker
- DELETE /api/tasks/{task_id} - Cancel pending/running tasks
- GET /api/tasks/{task_id}/events - SSE endpoint
- SessionManager.terminate_session() integration
- Scheduler project_id routing
- Cascade terminate to children

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
│                          TASK QUEUE SERVICE                                      │
│                                                                                  │
│  ┌─────────────────────┐    ┌──────────────────────────────────────────────┐ │
│  │  TaskQueueService   │    │           TaskLockManager                      │ │
│  │                     │    │                                              │ │
│  │  • enqueue()       │    │  • acquire_lock(project_id) → task_id        │ │
│  │  • dequeue()       │    │  • release_lock(project_id)                  │ │
│  │  • get_task()      │    │  • get_locked_project(session_id)              │ │
│  │  • cancel_task()   │    │  • wait_for_lock(project_id, timeout)         │ │
│  │  • list_tasks()   │    │                                              │ │
│  └─────────┬───────────┘    └──────────────────────────────────────────────┘ │
│            │                                                                     │
│            │                                                                     │
│  ┌─────────▼───────────┐    ┌──────────────────────────────────────────────┐ │
│  │  TaskRepository      │    │           ProjectLockRegistry                │ │
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
│  │  SessionManager     │◄───│  InputMessageQueue  │───►│  SchedulerAdapter │ │
│  │  (daemon/manager.py)│    │  (daemon/queue.py)  │    │  (scheduler.py)   │ │
│  │                     │    │                     │    │                   │ │
│  │  • spawn_session() │    │  • enqueue()        │    │  • _emit_message()│ │
│  │  • send_message()  │    │  • dequeue()        │    │  • project_id    │ │
│  │  • terminate_      │    │  • watchdog         │    │    (optional)     │ │
│  │    session() ←NEW  │    │                     │    │                   │ │
│  └─────────────────────┘    └─────────────────────┘    └───────────────────┘ │
│                                                                                  │
│  ┌─────────────────────┐    ┌─────────────────────┐                           │
│  │  EventBroadcaster  │    │  SessionRepository  │                           │
│  │  (daemon/events.py)│    │  (session/models)   │                           │
│  │                     │    │                      │                           │
│  │  • broadcast()     │    │  • create()          │                           │
│  │  • event_to_sse() │    │  • update_status()   │                           │
│  └─────────────────────┘    │  • terminate_session │                           │
│                             └─────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Models

### TaskQueueItem (SQLModel)

Location: `daemon/repositories/task_queue/models.py`

```python
class TaskQueueItem(SQLModel, table=True):
    """Task queue item - persisted for crash recovery."""
    __tablename__ = "task_queue_items"

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
    status: str = Field(default=TaskStatus.PENDING.value, index=True)
    
    # Timing
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # Result (filled on completion)
    session_id: Optional[str] = Field(default=None, index=True)
    error_message: Optional[str] = None
    result_summary: Optional[str] = None
    
    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    
    # Cancellation
    cancelled_at: Optional[str] = None


class TaskStatus(str, Enum):
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
    session_id: str
    locked_at: datetime
```

### Relationships

```
TaskQueueItem
├── project_id (FK to Project.project_id, optional)
├── session_id (FK to Session.session_id, optional)
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
    "agent_dir": "/agents/coder",
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
    "session_id": "session-uuid",
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
    "message": "Task queued, waiting for project lock"
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
    "session_id": "session-uuid",
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
data: {"task_id": "task-uuid", "status": "completed", "session_id": "session-uuid", "result_summary": "..."}

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

**Response (aborted running - terminates session):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "task_id": "task-uuid",
    "status": "cancelled",
    "message": "Task aborted, session terminated with children"
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
        │ • spawn_session()     │               │ • LockManager.acquire()   │
        │ • Return 200 +        │               └────────────┬──────────────┘
        │   session_id          │                                │
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
                      │ Spawn session:            │
                      │ SessionManager.spawn_     │
                      │   session()               │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │ Update task with          │
                      │ session_id                │
                      │                           │
                      │ Send message to session   │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │ Wait for session         │
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
                      │  TaskLockManager.release │
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
                    │                                │ Terminate session:        │
                    │                                │ SessionManager.terminate_ │
                    │                                │   session() ENHANCED      │
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
        # Route through task queue
        task_payload["project_id"] = self._config.project_id
        await task_queue_service.enqueue(**task_payload)
    else:
        # Immediate execution (existing behavior)
        incoming = IncomingMessage(...)
        await self._emit_message(incoming)
```

---

### 2. SessionManager Integration

Location: `daemon/manager.py`

**Enhancement:** Add task_queue_service dependency and enhance terminate_session().

```python
# daemon/manager.py

class SessionManager:
    def __init__(self, ...):
        # Existing initialization
        ...
        
        # NEW: Task queue service
        self._task_queue_service: TaskQueueService | None = None
    
    def set_task_queue_service(self, service: TaskQueueService):
        """Set task queue service for integration."""
        self._task_queue_service = service
    
    def terminate_session(self, session_id: str) -> bool:
        """Terminate session with full cleanup."""
        
        # 1. NEW: Cancel any active requests for this session
        active_requests = self._request_registry.get_active_for_session(session_id)
        for msg_id in active_requests:
            self._request_registry.cancel(msg_id, CancellationReason.MANUAL)
        
        # 2. NEW: Clean up queue messages for this session
        if self._task_queue_service:
            self._task_queue_service.cancel_tasks_by_session(session_id)
        
        # 3. NEW: Terminate child sessions (cascade)
        children = self._session_repository.get_children(session_id)
        for child_id in children:
            self.terminate_session(child_id)
        
        # 4. Existing logic
        self._processing.discard(session_id)
        self.broadcaster.cleanup_session(session_id)
        
        if session_id in self.sessions:
            del self.sessions[session_id]
        
        self._session_repository.update_status(session_id, "terminated")
        
        # 5. NEW: Release project lock if this session held one
        if self._task_queue_service:
            self._task_queue_service.release_lock_by_session(session_id)
        
        return True
```

---

### 3. InputMessageQueue Integration

Location: `daemon/queue.py`

The Task Queue is orthogonal to the existing InputMessageQueue:

- **InputMessageQueue**: Per-session message queuing (what to send to a session)
- **TaskQueue**: Per-project task queuing (which session can run)

They work at different layers:
1. TaskQueue decides which task (session) can proceed
2. Once session is running, InputMessageQueue handles its message stream

---

### 4. EventBroadcaster Integration

Location: `daemon/events.py`

**Enhancement:** Task events should integrate with existing event system.

```python
# Task events mirror session events for consistency

TASK_EVENT_TYPES = [
    "task_queued",      # Task added to queue
    "task_started",    # Task started processing
    "task_progress",   # Progress updates
    "task_completed",  # Task finished successfully
    "task_failed",     # Task failed
    "task_cancelled",  # Task was cancelled
]
```

---

## Enhancement: terminate_session() for Cascade to Children

### Current Implementation Gap

Current `terminate_session()` in `daemon/manager.py:1548-1572`:
- ❌ Does NOT cancel in-flight requests
- ❌ Does NOT clean up queue messages
- ❌ Does NOT terminate child sessions

### Enhanced Implementation

```python
# daemon/manager.py - Enhanced terminate_session()

def terminate_session(
    self, 
    session_id: str, 
    *,
    cancel_requests: bool = True,
    cleanup_queue: bool = True,
    cascade_children: bool = True,
    reason: str = CancellationReason.MANUAL.value
) -> bool:
    """
    Terminate a session with full cleanup.
    
    Args:
        session_id: The session to terminate.
        cancel_requests: Cancel in-flight requests for this session.
        cleanup_queue: Remove pending messages from queue.
        cascade_children: Also terminate child sessions.
        reason: Cancellation reason for tracking.
    
    Returns:
        True if terminated, False if not found.
    """
    # Early validation
    if session_id not in self.sessions:
        return False
    
    # 1. Cancel in-flight requests
    if cancel_requests:
        active = self._request_registry.get_active_for_session(session_id)
        for msg_id in active:
            self._request_registry.cancel(msg_id, reason)
    
    # 2. Clean up queue messages
    if cleanup_queue and hasattr(self, '_task_queue_service'):
        self._task_queue_service.cancel_tasks_by_session(session_id)
    
    # 3. Cascade to children FIRST (they hold resources too)
    if cascade_children:
        children = self._session_repository.get_children(session_id)
        for child_id in children:
            self.terminate_session(
                child_id,
                cancel_requests=cancel_requests,
                cleanup_queue=cleanup_queue,
                cascade_children=True,
                reason=reason
            )
    
    # 4. Stop processing this session
    self._processing.discard(session_id)
    
    # 5. Clean up event broadcaster
    self.broadcaster.cleanup_session(session_id)
    
    # 6. Remove from memory
    del self.sessions[session_id]
    
    # 7. Update database status
    self._session_repository.update_status(session_id, "terminated")
    
    # 8. Release project lock (if task queue is active)
    if hasattr(self, '_task_queue_service') and self._task_queue_service:
        self._task_queue_service.release_lock_by_session(session_id)
    
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
    TASK_CANCELLED = "task_cancelled"  # Task queue cancellation (NEW)
    CHILD_CASCADE = "child_cascade"    # Cascaded from parent termination (NEW)
```

---

## Component Specifications

### TaskQueueService

Location: `daemon/services/task_queue_service.py`

```python
class TaskQueueService:
    """Manages task queuing with per-project locking."""
    
    def __init__(
        self,
        repository: TaskRepository,
        session_manager: SessionManager,
        event_broadcaster: EventBroadcaster
    ):
        self._repository = repository
        self._session_manager = session_manager
        self._broadcaster = event_broadcaster
        self._lock_manager = TaskLockManager(repository)
        self._processor: TaskProcessor | None = None
    
    # ========== Public API ==========
    
    async def enqueue(
        self,
        agent_dir: str,
        message: str,
        source: str = "api",
        project_id: str | None = None,
        priority: int = 5
    ) -> TaskQueueItem:
        """
        Submit a task for processing.
        
        If project_id is None or no lock contention, executes immediately.
        Otherwise, queues for later processing.
        
        Returns task with status and (if immediate) session_id.
        """
        pass
    
    async def get_task(self, task_id: str) -> TaskQueueItem | None:
        """Get task by ID."""
        pass
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or abort a running task."""
        pass
    
    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        project_id: str | None = None,
        limit: int = 50
    ) -> list[TaskQueueItem]:
        """List tasks with optional filters."""
        pass
    
    # ========== Lock Management ==========
    
    def acquire_lock(self, project_id: str, task_id: str) -> bool:
        """Acquire lock for project. Returns True if acquired."""
        pass
    
    def release_lock(self, project_id: str) -> None:
        """Release lock for project."""
        pass
    
    def release_lock_by_session(self, session_id: str) -> None:
        """Release any lock held by a session."""
        pass
    
    def get_locked_project(self, session_id: str) -> str | None:
        """Get project_id if session holds a lock."""
        pass
    
    # ========== Internal / Background ==========
    
    async def start_processor(self) -> None:
        """Start background task processor."""
        pass
    
    async def stop_processor(self) -> None:
        """Stop background task processor."""
        pass
```

### TaskLockManager

Location: `daemon/services/task_lock_manager.py`

```python
@dataclass
class LockInfo:
    """Information about a held lock."""
    task_id: str
    project_id: str
    session_id: str
    locked_at: datetime


class TaskLockManager:
    """Manages per-project locks for task execution."""
    
    def __init__(self, repository: TaskRepository):
        self._repository = repository
        self._locks: dict[str, LockInfo] = {}  # project_id -> LockInfo
        self._waiters: dict[str, asyncio.Queue[tuple[str, asyncio.Event]]] = {}
    
    def acquire(self, project_id: str, task_id: str, session_id: str) -> bool:
        """
        Try to acquire lock for project.
        
        Args:
            project_id: The project to lock
            task_id: The task acquiring the lock
            session_id: The session running the task
            
        Returns:
            True if lock acquired, False if already held
        """
        if project_id in self._locks:
            return False
        
        self._locks[project_id] = LockInfo(
            task_id=task_id,
            project_id=project_id,
            session_id=session_id,
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
    
    def release_by_session(self, session_id: str) -> list[str]:
        """Release any locks held by a session. Returns released project_ids."""
        released = []
        for project_id, info in list(self._locks.items()):
            if info.session_id == session_id:
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

### TaskProcessor (Background Worker)

Location: `daemon/services/task_processor.py`

```python
class TaskProcessor:
    """Background worker that processes queued tasks."""
    
    def __init__(
        self,
        task_queue_service: TaskQueueService,
        session_manager: SessionManager
    ):
        self._task_queue = task_queue_service
        self._session_manager = session_manager
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
            projects_with_pending = await self._task_queue.get_projects_with_pending_tasks()
            
            for project_id in projects_with_pending:
                await self._process_project(project_id)
            
            # Brief sleep to prevent tight loop
            await asyncio.sleep(0.5)
    
    async def _process_project(self, project_id: str) -> None:
        """Process next task for a project."""
        # Get next task (highest priority, then FIFO)
        task = await self._task_queue._get_next_task(project_id)
        if not task:
            return
        
        # Try to acquire lock
        if not await self._task_queue._try_start_task(task):
            return  # Lock not acquired, skip for now
        
        try:
            # Execute task
            await self._execute_task(task)
        finally:
            # Release lock and process next
            await self._task_queue._complete_task(task)
    
    async def _execute_task(self, task: TaskQueueItem) -> None:
        """Execute a single task."""
        # Spawn session
        session_id = self._session_manager.spawn_session(
            agent_dir=task.agent_dir,
            session_id=None  # Auto-generate
        )
        
        # Update task with session
        task.session_id = session_id
        await self._task_queue._repository.update(task)
        
        # Send message to session
        await self._session_manager.send_message(
            session_id=session_id,
            message=task.message,
            source=task.source
        )
        
        # Wait for completion (via events)
        await self._wait_for_session_completion(session_id, task.task_id)
```

---

## Error Handling

### Lock Acquisition Failure

If lock cannot be acquired (timeout or max waiters reached):
- Return 202 Accepted with queue position
- Task remains in PENDING status
- Processor will retry on lock release

### Session Spawn Failure

If session cannot be created:
- Update task status to FAILED
- Set error_message with reason
- Release any lock held
- Notify via events

### Session Completion with Children

Per decision: When session completes (success/fail/error), terminate all child sessions as safety measure.

```python
async def _on_session_completed(self, session_id: str, task_id: str) -> None:
    """Handle session completion."""
    # Terminate all children as safety measure
    children = self._session_repository.get_children(session_id)
    for child_id in children:
        self._session_manager.terminate_session(child_id)
    
    # Release project lock
    self._lock_manager.release_by_session(session_id)
    
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
SELECT * FROM task_queue_items
WHERE project_id = ?
AND status = 'pending'
AND NOT EXISTS (
    SELECT 1 FROM task_dependencies
    WHERE task_id = task_queue_items.task_id
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

1. **TaskQueueService**:
   - test_enqueue_immediate_no_project
   - test_enqueue_immediate_no_lock_contention
   - test_enqueue_queued_with_contention
   - test_priority_ordering
   - test_fifo_same_priority
   - test_cancel_pending
   - test_cancel_running

2. **TaskLockManager**:
   - test_acquire_release
   - test_double_acquire_fails
   - test_release_wrong_task_fails
   - test_waiter_notification
   - test_release_by_session

3. **TaskProcessor**:
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
   - Cancel running → session terminated → children terminated

4. **Crash recovery**:
   - Simulate crash mid-task → task marked failed on restart
   - Orphaned locks cleared on startup

---

## Migration Notes

### Database Schema

New table needed:

```sql
CREATE TABLE task_queue_items (
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
    session_id TEXT,
    error_message TEXT,
    result_summary TEXT,
    metadata TEXT DEFAULT '{}',
    cancelled_at TEXT
);

CREATE INDEX idx_task_queue_project ON task_queue_items(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_task_queue_status ON task_queue_items(status);
CREATE INDEX idx_task_queue_session ON task_queue_items(session_id);
```

### Configuration

Add to `daemon/config.py`:

```python
class Config:
    # Task Queue settings
    task_queue_enabled: bool = True
    task_queue_max_waiters_per_project: int = 100
    task_queue_lock_timeout_seconds: int = 300
    task_queue_processor_interval_seconds: float = 0.5
```

### Backward Compatibility

- If task_queue service is not configured, fall back to immediate execution
- Scheduler with project_id but no queue → warning log, execute immediately
- Existing sessions without task tracking → unaffected

---

## Implementation Checklist

### Sprint 1 ✅ COMPLETE

- [x] Create database table schema
- [x] Implement TaskRepository
- [x] Implement TaskLockManager
- [x] Implement TaskQueueService
- [x] Add API endpoints (POST/GET /tasks)

### Sprint 2 ⏳ PENDING

- [ ] Implement TaskProcessor background worker
- [ ] Add DELETE /tasks/{task_id} endpoint
- [ ] Add SSE /tasks/{task_id}/events endpoint
- [ ] Integrate with SessionManager.terminate_session()
- [ ] Integrate with SchedulerAdapter
- [ ] Add configuration options
- [ ] Write unit tests
- [ ] Write integration tests
- [ ] Update documentation

---

*Document Version: 1.1*  
*Last Updated: 2026-03-16*
