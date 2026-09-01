# Messaging System Architecture

> **Note (2026-06-18):** The two-path description below reflects the architecture BEFORE the CorrelationManager migration unified message processing. The two physical paths (JobQueue, WorkerPool) still exist, but they now share a `MessageProcessingPipeline`, a `CorrelationManager` for parent-child correlation, and an `ExecutionGate` that serializes `graph.astream`. For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md).

## Overview

The system implements a **multi-layer queue architecture** with two distinct paths for handling requests:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL SOURCES                                  │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│    │    API       │  │   Telegram   │  │  Scheduler   │  │   Webhook    │   │
│    │  /api/jobs   │  │   Adapter    │  │   Adapter    │  │   Adapter    │   │
│    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
└───────────┼────────────────┼────────────────┼────────────────┼────────────┘
            │                │                │                │
            ▼                ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TWO ENTRY POINTS                                    │
│                                                                             │
│  ┌─────────────────────────────────┐    ┌─────────────────────────────────┐ │
│  │      PATH 1: Job Queue          │    │      PATH 2: Direct Message     │ │
│  │                                 │    │                                 │ │
│  │  POST /api/jobs                │    │  POST /api/instances/{id}/msgs │ │
│  │         ↓                      │    │         ↓                      │ │
│  │  JobQueueService.enqueue()     │    │  manager.enqueue_message()      │ │
│  │         ↓                      │    │         ↓                      │ │
│  │  JobItem (queued)              │    │  MessageQueue + Task (atomic)   │ │
│  │         ↓                      │    │         ↓                      │ │
│  │  JobProcessor (poll 2s)        │    │  WorkerPool (poll 0.5s)        │ │
│  │         ↓                      │    │         ↓                      │ │
│  │  spawn instance                │    │  process_message()              │ │
│  │         ↓                      │    │         ↓                      │ │
│  │  enqueue_message()             │    │  LangGraph execution            │ │
│  └─────────────────────────────────┘    └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

> **Both paths now converge** through the shared `MessageProcessingPipeline` (6 stages: gate acquire → process → mark complete → dispatch → child check → error handle) and the `ExecutionGate` per-instance lease. They also share the `CorrelationManager` for parent-child correlation. See [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md) for the unified view.

---

## Path 1: Job Queue (Background Jobs)

Used for **background jobs** with priorities, project serialization, and queue management.

> Job behavior splits into two dispatch cases — first-job (mission, spawns the instance)
> and message-job (mirror receipt on an existing instance). See
> [`docs/job-task-system.md`](job-task-system.md) for the canonical model, the
> `admission_state` lifecycle, and the `work_id` linkage contract.

### Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         JOB QUEUE FLOW                                      │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐
   │  POST       │
   │ /api/jobs   │
   └──────┬──────┘
          │
          ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                     JobQueueService.enqueue()                        │
   │  • Creates JobItem (admission_state='queued')                              │
   │  • Assigns to queue based on project_id                            │
   │  • Sets priority (1-10, 10=highest)                                │
   └────────────────────────────┬────────────────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                     job_queue_items TABLE                             │
   │  ┌─────────┬──────────┬─────────┬──────────┬─────────────────────┐ │
   │  │ job_id  │  status  │ priority│ queue_id │ instance_id         │ │
   │  ├─────────┼──────────┼─────────┼──────────┼─────────────────────┤ │
   │  │ job-001 │ queued   │   8     │ proj-q1  │ null                │ │
   │  │ job-002 │ queued   │   5     │ proj-q1  │ null                │ │
   │  └─────────┴──────────┴─────────┴──────────┴─────────────────────┘ │
   └────────────────────────────┬────────────────────────────────────────┘
                                │
          JobProcessor polls every 2 seconds
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                     JobProcessor._process_loop()                      │
   │                                                                      │
   │  For each project (skip if job_queue_paused):                        │
   │    For each queue (skip if is_paused):                             │
   │      Get next pending job (ordered by priority DESC, created ASC)   │
   │      Acquire per-queue lock                                         │
   │      Update job status → PROCESSING                                  │
   │      Spawn instance                                                 │
   │      Call enqueue_message()                                          │
   └────────────────────────────┬────────────────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                     InstanceManager.enqueue_message()                 │
   │  (Same atomic transaction as Path 2)                                 │
   └────────────────────────────┬────────────────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                     WorkerPool processes task                        │
   │  → See "Message Processing" section below                           │
   └─────────────────────────────────────────────────────────────────────┘
```

### Key Features

| Feature | Description |
|---------|-------------|
| **Per-project serialization** | Jobs for same `project_id` execute one-at-a-time |
| **Per-queue concurrency** | FIFO queues: limit=1, Parallel queues: limit=5 |
| **Priority ordering** | Jobs sorted by `priority` DESC, then `created_at` ASC |
| **Two-level pause** | Project-level (`job_queue_paused`) + Queue-level (`is_paused`) |

---

## Path 2: Direct Message (Real-time)

Used for **real-time messages** to existing instances, bypassing job queue.

> Although this path bypasses the JobQueue, it still flows through the same shared `MessageProcessingPipeline` and `ExecutionGate` as Path 1 once the WorkerPool picks up the task — see [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md).

### Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      DIRECT MESSAGE FLOW                                    │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────┐
   │  POST                   │
   │ /api/instances/{id}/   │
   │     messages            │
   └───────────┬─────────────┘
               │
               ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │               manager.enqueue_message() — ATOMIC TRANSACTION            │
   │                                                                        │
   │   ┌───────────────────────────────────────────────────────────────┐    │
   │   │                      session.begin()                          │    │
   │   │                                                                │    │
   │   │   1. INSERT message_queue                                      │    │
   │   │      └── MessageQueue(status=READY)                           │    │
   │   │                                                                │    │
   │   │   2. INSERT task                                              │    │
   │   │      └── Task(status=PENDING, type=process_message)          │    │
   │   │                                                                │    │
   │   │   3. UPDATE instance                                          │    │
   │   │      └── Instance(IDLE → RUNNING)                            │    │
   │   │                                                                │    │
   │   │   4. INSERT event                                             │    │
   │   │      └── Event(kind=MESSAGE_RECEIVED)                        │    │
   │   │                                                                │    │
   │   │   5. COMMIT (all-or-nothing)                                  │    │
   │   └───────────────────────────────────────────────────────────────┘    │
   │                                                                        │
   └───────────────────────────────────────────────────────────────────────┘
               │
               │ Returns immediately (non-blocking)
               │ AsyncMessageResult(message_id, status="queued")
               ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                    WorkerPool picks up task                            │
   │                                                                        │
   │   Worker-0: claim_task() → UPDATE ... RETURNING *                     │
   │   Worker-1: claim_task() → None (no tasks)                            │
   │   Worker-2: claim_task() → None (no tasks)                            │
   │   Worker-3: claim_task() → None (no tasks)                           │
   │                                                                        │
   └───────────────────────────────────────────────────────────────────────┘
```

### Atomic Transaction Details

```python
with Session(self._engine) as session:
    # 1. Message
    db_message = MessageQueue(
        message_id=message_id,
        instance_id=instance_id,
        content=message,
        status=MessageStatus.READY.value,  # ← Ready to process
        ...
    )
    session.add(db_message)
    
    # 2. Task
    task = Task(
        task_type=TaskType.PROCESS_MESSAGE.value,
        instance_id=instance_id,
        message_id=message_id,
        status=TaskStatus.PENDING.value,  # ← Worker will pick up
        ...
    )
    session.add(task)
    
    # 3. Instance status
    instance.status = InstanceStatus.RUNNING.value
    
    # 4. Event
    event = Event(kind=EventKind.MESSAGE_RECEIVED.value, ...)
    session.add(event)
    
    session.commit()  # ← All-or-nothing
```

---

## Message Processing (Worker Pool)

> **Current state:** Both the WorkerPool and the JobQueue drive messages through the shared `MessageProcessingPipeline` (`daemon/services/message_processing_pipeline.py`) under the `ExecutionGate` (`daemon/services/execution_gate.py`). The pipeline is identical across paths; only the injected `PipelineCallbacks` differ (retry semantics, contention backoff, completion-side-effects). See [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         WORKER POOL ARCHITECTURE                            │
└─────────────────────────────────────────────────────────────────────────────┘

                              ┌─────────────────────────────────┐
                              │         WorkerPool               │
                              │                                 │
                              │  ┌───────────────────────────┐  │
                              │  │  Worker-0 (thread)        │  │
                              │  │  while not stopped:        │  │
                              │  │    task = claim_task()    │  │
                              │  │    if task: process()      │  │
                              │  │    else: sleep(0.5s)      │  │
                              │  └───────────────────────────┘  │
                              │  ┌───────────────────────────┐  │
                              │  │  Worker-1 (thread)        │  │
                              │  │  ...                      │  │
                              │  └───────────────────────────┘  │
                              │  ┌───────────────────────────┐  │
                              │  │  Worker-2 (thread)        │  │
                              │  │  ...                      │  │
                              │  └───────────────────────────┘  │
                              │  ┌───────────────────────────┐  │
                              │  │  Worker-3 (thread)        │  │
                              │  │  ...                      │  │
                              │  └───────────────────────────┘  │
                              └────────────────┬────────────────┘
                                               │
                                               │ MainLoopBridge.run_async()
                                               ▼
                              ┌─────────────────────────────────┐
                              │      Main Event Loop (async)    │
                              │                                 │
                              │   TaskProcessor.run_task()      │
                              │           │                      │
                              │           ▼                      │
                              │   ┌───────────────────────────┐ │
                              │   │   Route by task_type:     │ │
                              │   │                           │ │
                              │   │   • process_message  ─────┼──▶ ProcessMessageProcessor
                              │   │   • send_report      ─────┼──▶ SendReportProcessor
                              │   │   • cleanup          ─────┼──▶ CleanupProcessor
                              │   │                           │ │
                              │   └───────────────────────────┘ │
                              └─────────────────────────────────┘
```

### Task Claiming (Atomic)

```sql
UPDATE task
SET status = 'running',
    worker_id = 'worker-0',
    started_at = NOW()
WHERE id = (
    SELECT id FROM task
    WHERE status = 'pending'
      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
    ORDER BY created_at ASC
    LIMIT 1
)
RETURNING *
```

- **Only one worker** can claim each task (DB-level locking)
- Tasks with `next_retry_at` in the future are skipped
- FIFO ordering by `created_at`

### Task Routing

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TASK PROCESSING FLOW                                │
└─────────────────────────────────────────────────────────────────────────────┘

   claim_task()                      ┌─────────────────────┐
          │                          │   TaskProcessor     │
          ▼                          │                     │
   ┌─────────────┐                   │  claim_task()       │
   │   Task      │                   │       │             │
   │ task_type:  │                   │       ▼             │
   │ "process_   │                   │  route by type      │
   │  message"   │                   │       │             │
   │ message_id: │                   │       ▼             │
   │ "msg-001"   │                   │  ┌─────────────┐    │
   └──────┬──────┘                   │  │   Router    │    │
          │                          │  └──────┬──────┘    │
          ▼                          │         │            │
   ┌─────────────┐                   │    ┌────┴────┐      │
   │ Process     │                   │    │         │      │
   │ Message     │                   │    ▼         ▼      │
   │ Processor   │                   │ process_   send_    │
   │             │                   │ message    report    │
   │ 1. Get msg  │                   │   ▲         ▲        │
   │ 2. Call     │                   │   │         │      │
   │    LangGraph│                   │   └─────────┘      │
   │ 3. Check    │                   │   (delegates)      │
   │    children │                   │                     │
   └──────┬──────┘                   └─────────────────────┘
          │
          ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │              manager._process_message_with_tracking()                  │
   │                                                                        │
   │   1. Create checkpoint                                                │
   │   2. Execute LangGraph (resume from checkpoint if retry)             │
   │   3. Save new checkpoint                                              │
   │   4. Return result                                                    │
   │                                                                        │
   └───────────────────────────────────────────────────────────────────────┘
```

---

## Retry & Timeout Handling

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         RETRY MECHANISM                                    │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────────────────┐
                    │         Task Fails              │
                    │    (timeout or error)           │
                    └──────────────┬───────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────┐
                    │    Check retry_count < max      │
                    │         (default: 3)             │
                    └──────────────┬───────────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     │                           │
                     ▼                           ▼
            ┌────────────────┐          ┌─────────────────┐
            │     YES       │          │      NO         │
            │  Schedule     │          │   Fail          │
            │  retry        │          │   permanently   │
            └───────┬────────┘          └─────────────────┘
                    │
                    │ Exponential backoff
                    │  attempt 1: wait 1 min
                    │  attempt 2: wait 2 min
                    │  attempt 3: wait 4 min
                    │  attempt 4: wait 8 min
                    │  ...
                    │  max: 60 min
                    │
                    ▼
            ┌──────────────────────────────────────┐
            │  UPDATE task                         │
            │    SET status = 'pending',           │
            │        retry_count = 2,             │
            │        next_retry_at = NOW() + 2min  │
            │  WHERE id = ?                        │
            └──────────────────────────────────────┘
                    │
                    │ Worker picks up again
                    ▼
            ┌──────────────────────────────────────┐
            │  On retry: resume from checkpoint    │
            │  (don't re-send original message)   │
            └──────────────────────────────────────┘
```

---

## Database Schema

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATABASE TABLES                                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────┐
│        job_queues              │
├────────────────────────────────┤
│ queue_id          PK           │
│ project_id                    │
│ queue_name                     │
│ queue_type    (fifo/parallel)  │
│ concurrency_limit (1-20)       │
│ is_paused     (bool)          │
│ is_system     (bool)          │
└────────────────────────────────┘
            │
            │ 1:N
            ▼
┌────────────────────────────────┐
│      job_queue_items           │
├────────────────────────────────┤
│ job_id           PK            │
│ queue_id        FK ──────────┐│
│ agent_id                      │
│ agent_dir                     │
│ message                       │
│ source                        │
│ project_id                    │
│ priority      (1-10)          │
│ status                       │
│ instance_id                   │
│ result_summary                │
│ error_message                 │
└────────────────────────────────┘

┌────────────────────────────────┐
│       message_queue            │
├────────────────────────────────┤
│ message_id       PK            │
│ instance_id                    │
│ content                       │
│ type      (human/agent/system) │
│ source                       │
│ status                       │
│ priority                     │
│ retry_count                  │
│ processing_task_id ───────────┼──┐
└────────────────────────────────┘  │
            │                        │
            │ N:1                     │
            ▼                        │
┌────────────────────────────────┐  │
│           task                 │  │
├────────────────────────────────┤  │
│ id              PK (auto)      │◀─┘
│ task_type                      │
│ instance_id                    │
│ message_id                     │
│ status                        │
│ worker_id                     │
│ retry_count                   │
│ next_retry_at                 │
│ created_at                    │
│ started_at                    │
│ completed_at                  │
│ error                         │
│ cancel_requested (bool)        │
│ retry_scheduled (bool)        │
└────────────────────────────────┘
```

---

## Event Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EVENT SYSTEM                                         │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
   │   Message    │      │   Worker     │      │   LangGraph  │
   │   Enqueued   │      │   Claims     │      │   Executes   │
   └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
          │                      │                      │
          ▼                      ▼                      ▼
   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
   │ MESSAGE_     │      │ PROCESSING_  │      │ PROCESSING_  │
   │ RECEIVED     │      │ STARTED      │      │ COMPLETED    │
   └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
          │                      │                      │
          │                      │                      │
          ▼                      ▼                      ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                          EventBus                                     │
   │                                                                       │
   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
   │  │ SSE Stream  │  │  Database   │  │  Webhook    │                  │
   │  │ (real-time) │  │  (persist)  │  │  (external) │                  │
   │  └─────────────┘  └─────────────┘  └─────────────┘                  │
   │                                                                       │
   └───────────────────────────────────────────────────────────────────────┘
```

---

## Configuration

| Component | Default | Config Key |
|-----------|---------|------------|
| Worker count | 4 | `manager.setup_worker_pool(num_workers=4)` |
| Task poll interval | 0.5s | `WorkerPool(poll_interval=0.5)` |
| Job poll interval | 2.0s | `JobProcessor(poll_interval=2.0)` |
| Task timeout | 45 min | `Worker(timeout_minutes=45.0)` |
| Max retries | 3 | `Worker(max_retries=3)` |
| Backoff base | 60s | `Worker(retry_backoff_base=60)` |
| Backoff max | 3600s | `Worker(retry_backoff_max=3600)` |

---

## Key Files

### Per-path
| File | Purpose |
|------|---------|
| `daemon/manager.py:806` | `enqueue_message()` - atomic message+task creation |
| `daemon/services/worker_pool.py` | Worker threads + pool management |
| `daemon/services/task_processor.py` | Task routing to type-specific processors |
| `daemon/services/job_queue_service.py` | Job queue operations |
| `daemon/services/job_processor.py` | Background job polling |
| `daemon/repositories/task/repository.py` | Task CRUD + atomic claiming |
| `daemon/repositories/message_queue/repository.py` | Message queue operations |
| `daemon/repositories/job_queue/repository.py` | Job queue operations |

### Shared message-processing infrastructure (current architecture)
| File | Purpose |
|------|---------|
| `daemon/services/message_processing_pipeline.py` | 6-stage shared pipeline (gate → process → mark → dispatch → child-check → error-handle) |
| `daemon/services/correlation_manager.py` | Authoritative parent-child correlation; `(parent, child, message_id)` triples |
| `daemon/services/execution_gate.py` | DB-backed per-instance lease serializing `graph.astream` |
| `daemon/services/message_processing_errors.py` | Shared error side-effects |

> The "Per-path" files above describe the dispatch surfaces that still exist. The "Shared message-processing infrastructure" is what both paths now go through. See [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md).
