# Job-Task-Pause-Resume Architecture

> **Note (2026-06-24):** Updated for the post-cleanup architecture. The Dependency Bus is the sole completion authority; the CorrelationManager is deleted. `MessageJobHandler` is deleted. Pause-vs-terminate discrimination happens as a pre-check in `JobProcessor.start_job`, not inside a MESSAGE handler. Parent pause / terminate cancels bus watchers via `dependency_bus.cancel_for_target(parent_id)`. The `waiting_for` and `children` columns have been dropped from the SQLModel. For the current message-processing architecture, see [`docs/architecture/message-processing-and-correlation.md`](message-processing-and-correlation.md).

## 1. Overview

### What is the Feature?

The Job-Task-Pause-Resume feature enables users to pause running agent instances and resume them later, with full state recovery via LangGraph checkpointing. It supports tree-aware operations that cascade pause/resume to all child instances in the hierarchy.

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Instance** | An agent instance (e.g., "developer", "leader") running in the system. Has a status lifecycle: `idle` → `running` ↔ `paused` → `completed`. |
| **Job** | A unit of work in the JobQueue system (`daemon/repositories/job_queue/`). Jobs wrap agent tasks with persistence for crash recovery. |
| **Task** | A unit of work in the WorkerPool system (`daemon/services/worker_pool.py`). Tasks are database-backed and processed by worker threads. |
| **Message** | A user input or system notification to an instance. Messages are persisted in `message_queue` table. |
| **Checkpoint** | LangGraph's state snapshot via `AsyncSqliteSaver`. Used for pause/resume — stores conversation history and node state. |
| **Cascade Pause/Resume** | Pausing/resuming an instance affects its entire tree (parent + all descendants). |

### Why Cascade?

Agent instances can spawn child instances via tools. A pause without cascade would leave children running, potentially causing deadlocks (parent waiting for children that continue executing). The cascade design ensures:

1. **No Deadlocks**: Parent and children are paused together
2. **Consistent State**: All tree members share the same lifecycle
3. **Clean Resume**: Children can resume from their checkpoints independently

---

## 2. Architecture Components

### 2.1 InstanceManager (`daemon/manager.py`)

The central orchestrator for all agent instances.

**Key Responsibilities:**
- Manages instance lifecycle (spawn, terminate, pause, resume)
- Coordinates with services and repositories
- Maintains in-memory graph cache

**Key Methods:**
- `spawn_instance()` — Creates new instance with graph
- `terminate_instance()` — Cascades termination to children
- `pause_instance_cascade()` — Delegates to InstanceLifecycleService
- `resume_instance_cascade()` — Delegates to InstanceLifecycleService
- `get_instance()` — Returns graph (lazy-loads from DB)

**Relationships:**
- Delegates lifecycle to `InstanceLifecycleService`
- Delegates messaging to `InstanceMessagingService`
- Uses `JobQueueService` for job management
- Uses `WorkerPool` for task processing

**Key Method: `resume_processing_job(instance_id, message, silent, images)`**

This is the dual-path resume architecture that handles the difference between root and child instances:

**Root Instance Path:**
1. Finds existing PROCESSING MESSAGE job for the instance
2. Cleans stale message entries: PROCESSING/RETRYING → COMPLETED; **PENDING messages are preserved for post-resume delivery** (not completed)
3. Schedules background task via `_resume_processing_background()`
4. Uses checkpoint resume with optional message injection

**Child Instance Path:**
1. No JobQueue job exists (children use WorkerPool directly)
2. If `silent=True`: skips enqueue (child will resume via parent's send_message)
3. If `silent=False`: enqueues message via WorkerPool with `resume_mode=True` metadata

**Key Parameters:**
- `message`: Resume message text (injected via graph_input for root, direct enqueue for child)
- `silent`: If True, resume from checkpoint without appending new message
- `images`: Optional base64 images for multimodal content

**Returns:** `dict` with `{instance_id, job_id, message_id, status}`

The `status` field has 4 possible values:
- `resuming` — instance has a checkpoint, `_resume_processing_background()` is running (root path)
- `queued` — instance enqueued via WorkerPool (child path, non-silent)
- `silent_resume` — instance resumed silently with no checkpoint, no message (child path, silent=True)
- `already_resuming` — instance is already being resumed (deduplication guard)

---

### 2.2 JobQueue (`daemon/repositories/job_queue/`)

Database-backed job persistence with crash recovery.

#### Files

| File | Purpose |
|------|---------|
| `models.py` | `JobItem`, `JobQueue`, `JobLock`, `DeadLetterItem` SQLModel tables |
| `repository.py` | `JobRepository` — CRUD + atomic state transitions |
| `queue_repository.py` | `JobQueueRepository` — Queue metadata CRUD |
| `lock_repository.py` | `LockRepository` — Per-queue locking |

#### Key Classes

**JobItem** — The job queue item (full model):
```python
class JobItem(SQLModel, table=True):
    # Primary identification
    job_id: str                    # Primary key (UUID)

    # Job content
    agent_id: str                  # Agent to run
    agent_dir: str                 # Path to agent files
    message: str                   # Job content
    source: str                    # "api" | "telegram" | "scheduler" | "webhook"

    # Project queuing
    project_id: str | None        # Project ID for job serialization
    queue_id: str | None          # Queue ID for job routing

    # Scheduling
    priority: int                  # 1-10 (1=lowest, 10=highest)
    status: str                   # pending | processing | completed | failed | cancelled | dead_letter

    # Timing
    created_at: str                # ISO timestamp
    started_at: str | None        # When processing started
    completed_at: str | None      # When processing completed

    # Result
    instance_id: str | None        # Set when job starts processing
    error_message: str | None     # Error details on failure
    result_summary: str | None     # Summary on completion

    # Metadata
    job_metadata: dict             # JSON: {message_id, resume_mode, silent, ...}

    # Cancellation
    cancelled_at: str | None       # When cancelled

    # Soft delete
    deleted_at: str | None         # Soft delete timestamp

    # Job type
    job_type: str                  # Phase D (D11): "message" branch removed; JobQueue owns only scheduling vocabulary for non-message work (scheduler, webhook, project-rooted tasks)

    # Retry handling
    retry_count: int               # Number of retries (default: 0)
    max_retries: int | None       # Max retries allowed
    idempotency_key: str | None   # For deduplication
    failed_at: str | None          # When job failed
    next_retry_at: str | None      # When to retry next
```

**JobRepository** — Key methods:
- `atomic_transition(job_id, from_status, to_status, **extra)` — Atomically transitions job status
- `start_job_atomic(job_id, instance_id)` — PENDING → PROCESSING (atomic)
- `start_job(job_id, instance_id)` — PENDING → PROCESSING (non-atomic)
- `complete_job(job_id, result_summary)` — PROCESSING → COMPLETED
- `fail_job(job_id, error_message)` — PROCESSING → FAILED
- `cancel_job(job_id)` — PENDING/PROCESSING → CANCELLED
- `find_jobs_by_instance(instance_id, job_type)` — Find all active jobs for an instance (used in termination cleanup)
- `find_processing_message_jobs_by_instance(instance_id)` — DB-level concurrency gate

**JobQueue** — Named queue for per-project isolation:
```python
class JobQueue(SQLModel, table=True):
    queue_id: str
    project_id: str
    queue_type: str       # "fifo" | "parallel" | "defer"
    concurrency_limit: int # Max concurrent jobs (1-20)
```

---

### 2.3 TaskProcessor / WorkerPool (`daemon/services/`)

#### TaskProcessor (`task_processor.py`)

Routes tasks to type-specific processors and provides thread-safe execution.

**Key Methods:**
- `claim_task(worker_id)` — Atomically claims next pending task from DB
- `run_task(task, cancellation_token)` — Runs task via MainLoopBridge

**Processors:**
| Processor | Purpose |
|-----------|---------|
| `ProcessMessageProcessor` | Handles `process_message` tasks — calls `_process_message_with_tracking()` |
| `SendReportProcessor` | Sends completion reports (not implemented) |
| `CleanupProcessor` | Handles cleanup tasks (not implemented) |

#### WorkerPool (`worker_pool.py`)

Notification-driven worker thread pool.

**Key Components:**
- **Worker threads** — Stateless, claim tasks from DB, process via TaskProcessor
- **Notification coordination** — `threading.Condition` wakes workers when work arrives
- **Timeout monitoring** — `TimeoutMonitor` enforces task timeouts

**Key Flow:**
```
Worker.run():
  1. claim_task() → get pending task from DB
  2. If no task → wait_for_work() (sleep until notified)
  3. _process_with_timeout(task) → run via MainLoopBridge
  4. Update task status (complete/fail/cancel)
  5. Loop
```

**Cancellation Handling:**
- `concurrent.futures.CancelledError` → Task stays RUNNING (pause scenario)
- `OperationCancelledError` → Task marked as cancelled (user/shutdown)

---

### 2.4 MessageJobHandler — REMOVED in Phase D (D12)

> **Removed 2026-06-21 in Phase D (D12).** `daemon/services/message_job_handler.py` is deleted. Message work no longer has a separate dispatch branch in `JobProcessor` (D11) and the JobQueue is now scheduling vocabulary only — it owns priority, queue management, and project scoping for `Task` rows, but no longer owns a `JobItem` lifecycle for messages. Pause-vs-terminate discrimination has moved to a pre-check in `JobProcessor.start_job` (see §2.5 below). For current architecture, see [`docs/architecture/message-processing-and-correlation.md`](message-processing-and-correlation.md).

**Historical behavior (retained for archival reference):**

Handles MESSAGE-type jobs from JobQueue.

**Key Behavior:**
- Reads `instance_id` from `JobItem.instance_id` column
- Creates `CancellationTokenSource` for cancel support
- Checks DB for concurrent MESSAGE jobs (concurrency gate)

**Key Methods:**
- `handle(job)` — Processes MESSAGE job
- `cancel_message_job(job_id)` — Cancels PENDING/PROCESSING job

**Pause Handling (historical):**
```python
except asyncio.CancelledError:
    instance = repo.get(instance_id)
    if instance.status == InstanceStatus.PAUSED:
        # Leave PROCESSING for resume
        return
    else:
        # Shutdown/other — re-raise
        raise
```

---

### 2.5 InstanceLifecycleService (`daemon/services/instance_lifecycle.py`)

Manages instance lifecycle operations.

**Key Methods:**

`pause_instance_cascade(instance_id)`:
1. Find tree root via `repo.get_tree_root_id()`
2. Get all node IDs via `repo.get_tree_ids()`
3. For each node:
   - Cancel active requests (`_request_registry.cancel_by_instance`)
   - Cancel graph task (`_graph_tasks.pop().cancel()`)
   - Update status to PAUSED, set `paused_at`
4. Emit status_change SSE events
5. **Cancel bus watchers for paused parents**: `dependency_bus.cancel_for_target(instance_id)` is called to cancel any in-flight FollowUps targeting the paused instance, so resumed work starts from a clean slate.

`resume_instance_cascade(instance_id)`:
1. Find tree root and all node IDs
2. For each node:
   - Update status to RUNNING, clear `paused_at`
3. Emit status_change SSE events

**Pause pre-check in `JobProcessor.start_job`:**

Before admitting a job, `JobProcessor.start_job` checks the target instance's status:
- If `RUNNING` — admit (atomic transition `PENDING → PROCESSING`, hand to WorkerPool).
- If `PAUSED` — leave the job `PENDING`; do not call `_process_message_with_tracking`. The job will be picked up when the instance is resumed.
- If `IDLE`/`WAITING_CHILDREN`/`COMPLETED`/`TERMINATED`/`ERROR` — admit (instance state-machine handles the rest).

This replaces the historical `MessageJobHandler.handle()` pause-vs-terminate discrimination. The discrimination no longer happens mid-flight inside a MESSAGE handler; it is a pre-check at admission.

---

### 2.6 InstanceMessagingService (`daemon/services/instance_messaging.py`)

Handles message sending and processing.

**Key Methods:**

`enqueue_message(instance_id, message, source, metadata)`:
1. Insert message into `message_queue` table
2. Create `Task` for worker pool
3. Update instance status (IDLE/WAITING_CHILDREN → RUNNING)
4. Emit `MESSAGE_RECEIVED` event
5. Notify worker pool (`_worker_pool.notify_work()`)

`_process_message_with_tracking(instance_id, message, message_id, ...)`:
1. Get graph from manager (lazy-loads from DB)
2. Create callbacks (activity tracking, cancellation)
3. On retry (`is_retry=True`):
   - Check for checkpoint
   - Use `graph_input` with `HumanMessage` instead of `aupdate_state`
4. Stream through graph (`graph.astream()`)
5. Emit SSE events for each message
6. Return `MessageResult`

**Checkpoint Resume Pattern:**
```python
if is_retry:
    has_ckpt = await self._has_checkpoint(instance_id)
    if has_ckpt:
        # Use graph_input instead of aupdate_state
        # LangGraph's add_messages reducer appends to checkpoint
        content = _build_message_content(message, images)
        if content and not silent:
            graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
        else:
            graph_input = None  # Silent resume
    else:
        graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
```

---

### 2.7 ChildReportsService (`daemon/services/child_reports.py`)

Handles child instance completion reports to parents.

**Key Method:**
`_process_child_completion_and_notify_parent(instance_id, completed_message_id)`

**Flow:**
1. Check idempotency (no duplicate reports for same message)
2. If tool invocation: skip parent notification
3. Create completion report message for parent
4. **DependencyBus wiring** — `dependency_bus.emit_terminal(source_task_id=child_task_id, outcome=COMPLETED)` is the authoritative terminal-emit. The bus atomically transitions the watcher `PENDING → FIRED` and enqueues a FollowUp `Task` onto the parent instance with the pre-built completion-report payload.
5. Delete from `instance_hierarchy` junction table (the canonical working set)

> **Idempotency Note:** `dependency_bus.emit_terminal()` is idempotent on `source_task_id`. Multiple emits for the same source are safe; the second emit is a no-op (the watcher is already FIRED). This eliminates the historical "double-decrement" bug class where CM `is_complete()` could race with concurrent register/resolve.

**Instance Hierarchy Deletion:**
- Child is removed from `instance_hierarchy` junction table via `DELETE FROM instance_hierarchy WHERE child_id = :id`
- The instance record in `instances` table is NOT deleted (soft state change only)

**Status Transition Logic:**
- When there are still pending children → stays in `WAITING_CHILDREN`
- Parent waits for its own message processing to complete before marking job done
- When parent completes its message, status check keeps it in `WAITING_CHILDREN`, cascade marks it `COMPLETED`

**Idempotency Key:**
```python
source=f"internal_report:{instance_id}:{completed_message_id}"
# Each completion generates unique report per message
```

---

### 2.8 JobFeedbackObserver (`daemon/services/job_feedback_observer.py`)

Subscribes to EventBus and propagates instance lifecycle events to job completion.

**Key Behavior:**
1. Subscribes to `instance_lifecycle` events via EventBus
2. On `completed` status:
   - `atomic_transition(PROCESSING → COMPLETED)`
   - Notify watchers
   - Release locks
   - Trigger next pending job
3. On `error` status:
   - `atomic_transition(PROCESSING → FAILED)`
   - Notify watchers
   - Release locks

---

### 2.9 Graph (`daemon/graph.py`)

LangGraph state machine for agent execution.

**Graph Structure:**
```
StateGraph(SessionState)
├── agent (LLM + tools)
├── tools (ToolNode)
└── nudge (injects continue prompt)
```

**State Schema:**
```python
class SessionState(MessagesState):
    compacted_at: str | None  # Last compaction timestamp
```

**Checkpoints:**
- Uses `AsyncSqliteSaver` checkpointer
- `configurable.thread_id` = instance_id
- Stores all message history in checkpoints

---

### 2.10 API Endpoints

#### Instances Router (`daemon/routers/instances.py`)

| Endpoint | Method | Handler |
|----------|--------|---------|
| `/instances/{id}/pause` | POST | `pause_instance()` |
| `/instances/{id}/resume` | POST | `resume_instance()` |

**Pause Flow:**
```python
result = await manager.pause_instance_cascade(instance_id)
return {
    "paused": True,
    "paused_ids": result["paused_ids"],
    "skipped_ids": result["skipped_ids"],
}
```

**Resume Flow:**
```python
result = await manager.resume_instance_cascade(instance_id)
# For each resumed instance, call resume_processing_job()
# Target instance: message="user's message", silent=False
# Children: message="resume", silent=True
```

#### Messages Router (`daemon/routers/messages.py`)

| Endpoint | Method | Handler |
|----------|--------|---------|
| `/instances/{id}/messages` | POST | `send_message()` |

**Auto-Resume on Paused Instance:**
```python
if instance_info.get("status") == InstanceStatus.PAUSED.value:
    # Skip normal enqueue
    resume_result = await manager.resume_instance_cascade(instance_id)
    # Resume processing jobs...
    return {"auto_resumed": True, "resume_info": {...}}
```

---

## 3. Data Flow Diagrams

### Normal Message Flow

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant API
    participant WorkerPool
    participant TaskProcessor
    participant Bus as DependencyBus
    participant Graph
    participant InstanceMessaging

    User->>API: POST /instances/{id}/messages
    API->>WorkerPool: enqueue_message()
    Note over WorkerPool: Writes MessageQueue row<br/>(status=READY)<br/>+ Task row (status=PENDING)
    API->>Bus: watch(source_task_id, target=parent_id, payload)
    Note over Bus: dependency_watchers row<br/>registered
    WorkerPool->>TaskProcessor: claim_task()
    TaskProcessor->>InstanceMessaging: _process_message_with_tracking()
    InstanceMessaging->>Graph: graph.astream(graph_input)
    Graph->>Graph: LLM + Tools execution
    loop Streaming
        Graph->>User: SSE events
    end
    Graph-->>InstanceMessaging: MessageResult
    InstanceMessaging-->>TaskProcessor: MessageResult
    TaskProcessor->>Bus: emit_terminal(source_task_id, COMPLETED)
    Bus->>WorkerPool: enqueue FollowUp Task onto parent
    Note over Bus: watcher FIRED,<br/>completion_delivery_path=bus
```

### Pause Cascade Flow

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant API
    participant InstanceLifecycle
    participant Repository
    participant WorkerPool
    participant Bus as DependencyBus

    User->>API: POST /instances/{id}/pause
    API->>InstanceLifecycle: pause_instance_cascade(id)
    InstanceLifecycle->>Repository: get_tree_root_id(id)
    InstanceLifecycle->>Repository: get_tree_ids(root)
    loop For each node in tree
        InstanceLifecycle->>InstanceLifecycle: _pause_single(node_id)
        InstanceLifecycle->>Repository: cancel_by_instance()
        Note over InstanceLifecycle: Cancels LLM requests
        InstanceLifecycle->>WorkerPool: _graph_tasks.pop().cancel()
        Note over WorkerPool: asyncio.CancelledError
        InstanceLifecycle->>Repository: Update status=PAUSED, paused_at=now
        InstanceLifecycle->>Bus: cancel_for_target(node_id)
        Note over Bus: cancels watchers whose<br/>target=node_id (orphaned FollowUps)
        InstanceLifecycle->>User: Emit SSE status_change
    end
    API-->>User: {paused_ids: [...], skipped_ids: [...]}
```

> **Note:** The historical `MessageJobHandler` pause-vs-terminate discrimination (mid-flight `asyncio.CancelledError` catch) is gone. The MESSAGE handler itself is deleted. `JobProcessor.start_job` now performs the pause check as a pre-check before admitting a job (see §2.5). The `waiting_for` reset that used to be part of the pause cascade has been removed because the column itself has been dropped from the schema — the bus is the source of truth for pending children.

### Resume Flow (Root Instance)

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant API
    participant InstanceLifecycle
    participant Repository
    participant Manager
    participant Graph
    participant Bus as DependencyBus

    User->>API: POST /instances/{id}/resume
    API->>InstanceLifecycle: resume_instance_cascade(id)
    InstanceLifecycle->>Repository: get_tree_root_id(id)
    InstanceLifecycle->>Repository: get_tree_ids(root)
    loop For each node in tree
        InstanceLifecycle->>Repository: Update status=RUNNING, paused_at=null
        InstanceLifecycle->>User: Emit SSE status_change
    end
    API->>Manager: resume_processing_job(target_id, message, silent=False)
    Note over Manager: For target instance:<br/>- Find PROCESSING job<br/>- Clean stale messages (PROCESSING/RETRYING)<br/>- Preserve PENDING messages
    Manager->>Manager: _resume_processing_background() [asyncio.create_task]
    Manager->>Manager: _process_message_with_tracking(is_retry=True)
    Manager->>Graph: ainvoke(graph_input) [checkpoint resume + message injection]
    Graph->>Graph: Resume from checkpoint + inject message
    Graph-->>Manager: MessageResult
    Manager->>Bus: emit_terminal(source_task_id, COMPLETED)
    Note over Bus: watcher FIRED, FollowUp enqueued
    API-->>User: {resumed_ids: [...], resume_results: {...}}
```

### Resume Flow (Child Instance)

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant API
    participant InstanceLifecycle
    participant Repository
    participant Manager
    participant WorkerPool
    participant TaskProcessor
    participant Graph

    User->>API: POST /instances/{id}/resume
    Note over API: id is a child instance
    API->>InstanceLifecycle: resume_instance_cascade(id)
    InstanceLifecycle->>Repository: get_tree_root_id(id)
    InstanceLifecycle->>Repository: get_ancestor_ids(id)
    InstanceLifecycle->>Repository: get_tree_ids(root)
    loop For each node in tree
        InstanceLifecycle->>Repository: Update status=RUNNING
        InstanceLifecycle->>User: Emit SSE status_change
    end
    API->>Manager: resume_processing_job(child_id, message="resume", silent=True)
    alt Child has checkpoint
        Manager->>WorkerPool: Enqueue task via normal flow
        WorkerPool->>TaskProcessor: claim_task()
        TaskProcessor->>Graph: _process_message_with_tracking(is_retry=True, silent=True)
        Note over Graph: graph_input = None (silent resume)
        Graph->>Graph: Pure checkpoint resume
        Graph-->>TaskProcessor: MessageResult
    else Child is idle (no checkpoint, no active job)
        Manager->>Manager: No action needed
        Note over Manager: Parent's send_message will handle the child
    end
```

### Send Message to Paused Instance

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant API
    participant InstanceLifecycle
    participant Repository
    participant Manager
    participant WorkerPool
    participant Graph

    User->>API: POST /instances/{id}/messages
    API->>Repository: get_instance_info(id)
    alt Instance is PAUSED
        API->>InstanceLifecycle: resume_instance_cascade(id)
        InstanceLifecycle->>Repository: Update all nodes to RUNNING
        API->>Manager: resume_processing_job(id, message, silent=False)
        Note over Manager: Auto-injects user's message
        Manager->>WorkerPool: Enqueue task
        WorkerPool->>Graph: _process_message_with_tracking(message)
        Graph->>Graph: Resume + inject user message
        Graph-->>WorkerPool: MessageResult
        WorkerPool-->>API: result
        API-->>User: {auto_resumed: True, resume_info: {...}}
    else Instance is NOT PAUSED
        API->>Manager: enqueue_message()
        Note over Manager: Normal queue flow<br/>(Task row written)
        API-->>User: {message_id: "..."}
    end
```

> **Phase D change:** `MessageJobHandler.handle(job)` is deleted. There is no mid-flight pause-vs-terminate discrimination branch to draw here — `JobProcessor.start_job` performs the pause pre-check before admitting the job (see §2.5).

---

## 4. Key Data Models

### 4.1 Job (JobQueue)

```python
class JobItem(SQLModel, table=True):
    """Job queue item - persisted for crash recovery."""
    job_id: str                    # Primary key (UUID)
    agent_id: str                  # Agent to run
    message: str                   # Job content
    status: str                    # pending | processing | completed | failed | cancelled | dead_letter
    instance_id: str | None       # Set when job starts processing
    job_type: str                  # "task" (serial) | "message" (parallel)
    job_metadata: dict              # JSON: {message_id, resume_mode, silent, ...}
    priority: int                  # 1-10 (10=highest)
    created_at: str                 # ISO timestamp
    started_at: str | None         # When processing started
    completed_at: str | None        # When processing completed
```

**Job Status Transitions:**

> **Phase D note (D11):** `JobItem` rows for `job_type='message'` are no longer written. The JobQueue now owns only scheduling vocabulary for `Task` rows. `MessageJobHandler` is deleted. The `JobItem` lifecycle below applies to non-message work (scheduler, webhook, project-rooted tasks); message work flows entirely through the WorkerPool/Task lifecycle (§4.2).
```
┌─────────┐
│ pending │
└────┬────┘
     │ start_job() (post Phase D: pause pre-check; PAUSED instance leaves job PENDING)
     ▼
┌───────────┐
│ processing│◄──────────┐
└─────┬─────┘           │
      │                 │
      │ ├─► completed   │ complete_job()
      │                 │
      │ ├─► failed      │ fail_job() / max_retries
      │                 │
      │ ├─► cancelled   │ cancel_job()
      │                 │
      └───────────────┘ cancel_job() (shutdown)
```

### 4.2 Task (WorkerPool)

```python
class Task(SQLModel, table=True):
    """Worker pool task - database-backed."""
    id: int | None               # Auto-increment primary key
    task_type: str               # process_message | send_report | cleanup
    instance_id: str             # Target instance
    message_id: str | None       # Associated message
    status: str                  # pending | running | completed | failed | cancelled
    worker_id: str | None        # Assigned worker
    retry_count: int             # Number of retries
    created_at: datetime         # When task was created
    started_at: datetime | None  # When processing started
    completed_at: datetime | None # When processing completed
```

**Task Status Transitions:**
```
┌─────────┐
│ pending │◄── Created by enqueue_message()
└────┬────┘
     │ claim_pending_task()
     ▼
┌────────┐
│ running│◄── Worker picked up task
└────┬───┘
     │
     ├──► completed  (success)
     ├──► failed     (error)
     └──► cancelled  (pause/shutdown)
```

### 4.3 Instance

```python
class Instance(SQLModel, table=True):
    """Agent instance."""
    instance_id: str              # Primary key (UUID)
    agent_id: str                 # Agent type (e.g., "developer")
    agent_dir: str                # Path to agent files
    parent_id: str | None         # Parent instance ID
    status: str                   # idle | running | paused | completed | error | terminated | waiting_children
    paused_at: str | None         # ISO timestamp when paused
    created_at: str               # ISO timestamp
    updated_at: str               # ISO timestamp
    instance_metadata: dict       # JSON: project_id, mcp_tool_names, etc.
```

> **Note:** The legacy `waiting_for` (pending-children count) and `children` (denormalized JSON cache) columns have been dropped from this model. Parent-child correlation is owned by the **DependencyBus** (`dependency_watchers` table) and the canonical working set is the `instance_hierarchy` junction table. See [`docs/architecture/completion-authority.md`](completion-authority.md) and [`docs/architecture/message-processing-and-correlation.md`](message-processing-and-correlation.md).

**Instance Status Lifecycle:**
```mermaid
stateDiagram-v2
    [*] --> idle: spawn_instance()
    idle --> running: First message
    running --> paused: User pauses
    paused --> running: User resumes
    running --> waiting_children: All messages done, waiting for children (bus says pending)
    waiting_children --> running: Child completion report (enqueue_message)
    waiting_children --> completed: Last child done (bus says no pending)

    running --> error: Unhandled exception
    error --> terminated: Auto-cleanup

    running --> terminated: terminate_instance()
    paused --> terminated: terminate_instance()
    completed --> [*]
    terminated --> [*]
```

**Instance Status Lifecycle:**
```mermaid
stateDiagram-v2
    [*] --> idle: spawn_instance()
    idle --> running: First message
    running --> paused: User pauses
    paused --> running: User resumes
    running --> waiting_children: All messages done, waiting for children
    waiting_children --> running: Child completion report
    running --> completed: All work done
    waiting_children --> completed: All children done
    running --> error: Error occurred
    running --> terminated: User terminates
    paused --> terminated: User terminates
    completed --> [*]
    terminated --> [*]
    error --> terminated: Auto-cleanup
```

### 4.4 MessageQueue

```python
class MessageQueue(SQLModel, table=True):
    """Queued message for an instance."""
    message_id: str               # Primary key (UUID)
    instance_id: str              # Target instance
    content: str                  # Message content
    type: str                     # human | agent | system | completion_report | error_report
    source: str                   # "api" | "telegram:user:123" | "internal_report:child_id:msg_id"
    status: str                   # pending | ready | processing | retrying | completed | failed
    message_metadata: dict        # JSON: {resume_mode, silent, ...}
    enqueued_at: datetime         # When queued
    completed_at: datetime | None # When processed
```

**Message Status Transitions:**
```
┌────────┐
│ pending│ (for retry support)
└───┬────┘
    │ enqueue_message()
    ▼
┌───────┐
│ ready │
└───┬───┘
    │ Worker picks up
    ▼
┌────────────┐
│ processing │
└─────┬──────┘
      │
      ├──► completed  (success)
      ├──► retrying  (transient error)
      └──► failed     (permanent error)
```

### 4.5 Checkpoint (LangGraph)

LangGraph's `AsyncSqliteSaver` stores checkpoints with:

```python
# Config used for all graph operations
config = {
    "configurable": {
        "thread_id": instance_id  # Same as instance_id
    },
    "recursion_limit": 1000
}

# Checkpoint contains:
{
    "channel_values": {
        "messages": [...],      # Conversation history
        "compacted_at": "..."  # Last compaction timestamp
    },
    "channel_versions": {...}, # Version tracking
    "next_nodes": {...}       # Pending node queue
}
```

---

## 5. State Machines

### Job State Machine

```mermaid
stateDiagram-v2
    [*] --> pending: enqueue()
    pending --> processing: (start)
    processing --> completed: complete_job()
    processing --> failed: fail_job() / max_retries
    processing --> cancelled: cancel_job()
    processing --> cancelled: shutdown
    pending --> cancelled: cancel_job()
    processing --> pending: (requeue)
    failed --> pending: retry_job()
    failed --> dead_letter: max_retries_exceeded
    failed --> cancelled: cancel_after_fail
    dead_letter --> pending: replay
    cancelled --> [*]
    completed --> [*]
    dead_letter --> [*]
```

Note: Valid transitions per `daemon/services/job_state_machine.py`:
- `pending → processing` (start)
- `pending → cancelled` (cancel)
- `processing → completed` (complete)
- `processing → failed` (fail)
- `processing → cancelled` (abort)
- `processing → pending` (requeue)
- `failed → pending` (retry)
- `failed → dead_letter` (dead_letter)
- `failed → cancelled` (cancel_after_fail)
- `dead_letter → pending` (replay) — dead_letter is replayable, not strictly terminal

### Instance Status Lifecycle

```mermaid
stateDiagram-v2
    [*] --> idle: spawn_instance()

    idle --> running: enqueue_message()

    running --> paused: pause_instance_cascade()
    paused --> running: resume_instance_cascade()

    running --> completed: All messages done + DependencyBus says no pending children
    running --> waiting_children: DependencyBus has pending children + no own-queue messages

    Note over running,waiting_children: Completion decisions are made by the DependencyBus — `dependency_bus.count_pending_for_target_sync(instance_id)` is the source of truth for "are children still running?"

    waiting_children --> running: Child completion report (enqueue_message)
    waiting_children --> completed: Last child done (bus pending count → 0)

    running --> error: Unhandled exception
    error --> terminated: Auto-cleanup

    running --> terminated: terminate_instance()
    paused --> terminated: terminate_instance()
    completed --> [*]
    terminated --> [*]
```

### Message Queue State Machine

```mermaid
stateDiagram-v2
    [*] --> pending: (retry support)
    pending --> ready: Enqueue
    
    ready --> processing: Worker claim
    
    processing --> completed: Success
    processing --> retrying: Transient error
    processing --> failed: Permanent error
    processing --> ready: Pause (back to queue)
    
    retrying --> processing: Retry delay elapsed
    retrying --> failed: Max retries exceeded
```

---

## 6. Pause/Resume Design Decisions

### 6.1 Why Cascade?

**Problem:** Agent instances can spawn child instances. A parent may have outstanding FollowUps enqueued from in-flight children.

**Without Cascade:**
1. Parent pauses → Parent status = PAUSED
2. Children continue running → Send completion reports
3. Parent can't process reports → Deadlock

**With Cascade:**
1. Parent pauses → All children pause
2. Bus watchers targeting any node in the tree are cancelled via `dependency_bus.cancel_for_target(node_id)`, so no FollowUps are enqueued onto paused instances
3. Resume → All children resume together; new `send_message` calls re-register watchers on the bus
4. No deadlock possible

> The historical implementation also reset a `waiting_for` column on pause/resume. That column has been dropped; the DependencyBus is now the sole source of truth for pending-children state.

---

### 6.2 Why Root Uses Direct Resume vs Child Uses Normal Queue?

**Root Instance:**
- Uses the existing `PROCESSING` task (not a new enqueue)
- The `_resume_processing_job()` method schedules `_resume_processing_background()` as an asyncio task
- `_resume_processing_background()` calls `_process_message_with_tracking()` **directly** (there is no MESSAGE handler to route through post-Phase-D)
- Checkpoint resume with message injection via `graph_input` (LangGraph `add_messages` reducer appends the message to checkpoint state)
- Task is completed by `_resume_processing_background()` after processing finishes

**Child Instance:**
- No separate `JobItem` exists (children use WorkerPool `Task` directly)
- When `silent=True` (cascade resume): skips enqueue, child resumes via parent's `send_message`
- When `silent=False`: enqueues message via `enqueue_message()` → WorkerPool task claiming path
- Uses normal `ProcessMessageProcessor` → `_process_message_with_tracking()` flow

**Rationale:**
- Root is user-facing → checkpoint resume preserves exact conversation state
- Child is background → normal async WorkerPool processing is sufficient
- Different code paths prevent interference and respect the WorkerPool as the single execution layer

---

### 6.3 Why Silent Flag?

**Purpose:** Skip message injection during cascade resume for non-target nodes.

```python
# In resume_instance_cascade()
for node_id in tree_ids:
    is_target = node_id == target_id
    await resume_processing_job(
        node_id,
        message=message_text if is_target else "resume",
        silent=not is_target,  # Children get silent=True
    )
```

**Why?**
- Target gets user's message injected
- Children only need checkpoint resume (no new message)
- Silent flag prevents duplicate/incorrect message injection

---

### 6.4 Why graph_input Instead of aupdate_state?

**The Problem with aupdate_state:**
```python
# DON'T use this for resume:
await graph.aupdate_state(
    config,
    {"messages": [HumanMessage(content=message)]},
    as_node="agent"
)
# This clears checkpoint's next=() queue!
# astream(None) returns instantly without running the graph.
```

**The Solution:**
```python
# DO use this for resume:
if has_checkpoint:
    graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
else:
    graph_input = {"messages": [...]}

async for event in graph.astream(graph_input, config):
    # Works correctly!
```

**Why It Works:**
- LangGraph's `add_messages` reducer appends to existing messages
- Checkpoint state is preserved
- Graph runs from checkpoint + new message

---

### 6.5 Why the Dependency Bus Cancellation Path?

**Purpose:** Prevent orphan FollowUp tasks from being enqueued onto a parent that is being paused, resumed, or terminated.

```python
# In InstanceLifecycleService.pause_instance_cascade()
loop for node in tree_ids:
    repo.update(node, status=PAUSED, paused_at=now)
    await dependency_bus.cancel_for_target(node_id)
```

**Why?**
- A `send_message` may have registered a watcher (`dependency_watchers` row) on a child task whose terminal will enqueue a FollowUp onto the parent.
- If the parent is paused mid-flight, the FollowUp must NOT be enqueued — it would land on a paused instance and either wedge the queue or be processed after resume, producing a stale completion report.
- Canceling the watcher at pause-time makes the eventual `emit_terminal(source_task_id, outcome=...)` a no-op: the bus sees no matching watcher and emits nothing.

**Symmetry for terminate:**
```python
# In InstanceLifecycleService.terminate_instance_cascade()
await dependency_bus.cancel_for_target(instance_id)
# Then proceed with the historical cascade termination logic.
```

**Why the bus?** The bus `cancel_for_target` clears `dependency_watchers` rows — durable, restart-safe, and idempotent. It is the single source of truth for in-flight parent-child coupling; cancellation must go through it.

---

## 7. Edge Cases & Gotchas

### 7.1 Pause During Tool Call

**Scenario:** User pauses while agent is executing a tool.

**What Happens:**
1. Pause cancels graph task (`asyncio.CancelledError`)
2. Checkpoint may be from previous step (not mid-tool)
3. Resume starts from last checkpointed state

**Result:**
- Some tool effects may be lost (if tool partially executed)
- Agent re-executes from checkpoint
- This is acceptable — tools should be idempotent

**Mitigation:**
- Checkpoint after each tool execution
- Tools should be designed idempotently

---

### 7.2 Resume Message Injection via add_messages Reducer

**Scenario:** Resume with user's message.

**Mechanism:**
```python
# LangGraph's add_messages reducer:
def add_messages(existing, new):
    return existing + new  # Append, not replace

# So this works:
graph_input = {"messages": [HumanMessage(content=user_msg)]}
# Existing checkpoint messages + new message
```

**Edge Case:** Multiple rapid resume calls.
- Each call appends another HumanMessage
- May cause duplicate processing

**Mitigation:** Use `silent=True` for non-target instances

---

### 7.3 Stale Message Cleanup

**Scenario:** Message stuck in PROCESSING after pause.

**Cleanup Logic:**
```python
# In resume_processing_job()
if message.status in (MessageStatus.PROCESSING, MessageStatus.RETRYING):
    await self._queue_repository.complete(message.message_id)
# PENDING messages are preserved for post-resume delivery!
```

**Why Preserve PENDING?**
- `PROCESSING`/`RETRYING` = stale from crash/pause (incomplete, needs cleanup)
- `PENDING` = legitimate queue position that was interrupted
- The system expects PENDING messages to be processed **after** the resume message is handled
- This supports "post-resume delivery" where pending user inputs are queued until the resume completes

---

### 7.4 Idempotency in Child Completion Reports

**Scenario:** Child completes, sends report to parent.

**Idempotency Key:**
```python
source = f"internal_report:{instance_id}:{completed_message_id}"
# Example: internal_report:abc123:def456

# Check before creating:
existing = session.exec(
    select(MessageQueue)
    .where(MessageQueue.source == source)
    .where(MessageQueue.status.in_([READY, PROCESSING, COMPLETED]))
).first()

if existing:
    return  # Skip duplicate
```

**Why Per-Message?**
- Allows multiple completions from same child
- Each completion generates unique report
- Prevents duplicate reports on retry

---

### 7.5 Fire-and-Forget API Design

**Scenario:** User calls pause/resume.

**Design:** API returns immediately, processing happens async.

```python
# In pause_instance()
result = await pause_instance_cascade(instance_id)
return {
    "paused": True,
    "paused_ids": result["paused_ids"],
    # Processing continues in background
}
```

**Trade-offs:**
- Fast response time
- No SSE needed for pause/resume
- Status can be checked via GET /instances/{id}

---

### 7.6 Concurrent Resume Deduplication

**Scenario:** User calls resume twice rapidly.

**Handling:**
- Each resume creates new graph task
- First task runs, second may fail/cancel
- Database state is consistent

**Prevention:**
- UI should disable resume button during operation
- API could add idempotency key (not implemented)

---

### 7.7 Daemon Restart with Paused Instances

**Scenario:** Daemon restarts (crash, deployment, manual restart) with paused instances in the database.

**What Happens:**
- Paused instances remain in `status=PAUSED` in the database
- Their in-memory graphs are lost (released on restart)
- No background task automatically resumes them

**Operational Implication:**
- The operator must manually resume paused instances via the API (`POST /instances/{id}/resume`)
- This is intentional — the system does not auto-resume on restart to avoid unexpected agent behavior after downtime
- Paused instances can remain in this state indefinitely until manually resumed

**Mitigation:**
- Monitor for long-paused instances in production
- Consider adding a startup warning or health check for paused instances

---

## 8. API Reference

### POST /api/instances/{id}/pause

Pause an instance and cascade to all children.

**Request:**
```
POST /api/instances/{instance_id}/pause
```

**Response (200):**
```json
{
  "paused": true,
  "paused_ids": ["instance-1", "child-1", "child-2"],
  "skipped_ids": []
}
```

**Errors:**
- `404 Not Found` — Instance not found

---

### POST /api/instances/{id}/resume

Resume a paused instance and cascade to all children.

**Request:**
```json
POST /api/instances/{instance_id}/resume
{
  "message": "continue with the next step"
}
```

**Response (200):**
```json
{
  "resumed": true,
  "resumed_ids": ["instance-1", "child-1", "child-2"],
  "skipped_ids": [],
  "target_id": "instance-1",
  "resume_results": {
    "instance-1": {"status": "processing"},
    "child-1": {"status": "processing"},
    "child-2": {"status": "no_active_job"}
  }
}
```

**Notes:**
- `message` is optional (defaults to "resume")
- Target instance gets user's message
- Children resume silently from checkpoint

**Errors:**
- `404 Not Found` — Instance not found

---

### POST /api/instances/{id}/messages (Auto-Resume)

Send message to instance (auto-resumes if paused).

**Request:**
```json
POST /api/instances/{instance_id}/messages
{
  "content": "Please continue with the task",
  "images": ["base64..."]
}
```

**Response (200, Paused Instance):**
```json
{
  "message_id": null,
  "role": "user",
  "content": "Please continue with the task",
  "thinking": null,
  "thinking_extracted": null,
  "tool_calls": null,
  "images": null,
  "auto_resumed": true,
  "resume_info": {
    "resumed": true,
    "resumed_ids": ["instance-1", "child-1"],
    "skipped_ids": [],
    "target_id": "instance-1",
    "resume_results": {
      "instance-1": {"status": "processing"}
    }
  }
}
```

**Response (200, Normal):**
```json
{
  "message_id": "msg-123",
  "role": "assistant",
  "content": "",
  "auto_resumed": false,
  "resume_info": null
}
```

**Errors:**
- `404 Not Found` — Instance not found
- `400 Bad Request` — Images but no vision model configured

---

## Appendix: File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `daemon/manager.py` | 2334 | Central orchestrator |
| `daemon/repositories/job_queue/models.py` | 286 | JobItem, JobQueue, JobLock models |
| `daemon/repositories/job_queue/repository.py` | 742 | JobRepository CRUD |
| `daemon/repositories/task/models.py` | 103 | Task model |
| `daemon/services/task_processor.py` | 505 | Task routing |
| `daemon/services/worker_pool.py` | 488 | Worker thread pool |
| `daemon/services/dependency_bus.py` | — | Sole completion authority (DB-backed); `watch` / `emit_terminal` / `cancel_for_target` API |
| `daemon/repositories/dependency_bus/` | — | `dependency_watchers` table + repository (WriteGuardSession pattern) |
| `daemon/services/instance_lifecycle.py` | 835 | Lifecycle operations; pause-time `dependency_bus.cancel_for_target()` |
| `daemon/services/instance_messaging.py` | 1288 | Message handling; unified `enqueue_message(... dispatch_path=...)` |
| `daemon/services/child_reports.py` | 816 | Child completion reports; calls `dependency_bus.emit_terminal()` |
| `daemon/services/job_feedback_observer.py` | 378 | Job completion observer |
| `daemon/services/job_state_machine.py` | 114 | Job state machine |
| `daemon/services/job_processor.py` | — | Pause pre-check added to `start_job`; MESSAGE-dispatch branch removed |
| `daemon/services/execution_gate.py` | 205 | Per-instance `asyncio.Lock` gate; no Lease stubs |
| `daemon/graph.py` | 618 | LangGraph definition |
| `daemon/routers/instances.py` | 323 | Instance API endpoints |
| `daemon/routers/messages.py` | 252 | Message API endpoints |
| `daemon/repositories/instance/models.py` | 95 | Instance model (`waiting_for` and `children` columns dropped) |
| `daemon/repositories/message_queue/models.py` | 101 | MessageQueue model |
| `daemon/services/job_queue_service.py` | 1445 | Job queue service (scheduling vocabulary only for `Task` rows) |
