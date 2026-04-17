# Job System Deep Review — agents-ensemble

> **Review Date:** April 2026  
> **Scope:** Core job queue modules, persistence layer, API surface, background workers, and crash recovery  
> **Status:** Complete

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Job Lifecycle & State Machine](#2-job-lifecycle--state-machine)
3. [Job Queue & Scheduling](#3-job-queue--scheduling)
4. [Job Types](#4-job-types)
5. [Error Handling & Recovery](#5-error-handling--recovery)
6. [API Surface](#6-api-surface)
7. [Relationship to Sessions & Agents](#7-relationship-to-sessions--agents)
8. [Pause Mechanism](#8-pause-mechanism)
9. [Code Quality Observations](#9-code-quality-observations)
10. [Configuration](#10-configuration)
11. [Summary & Ratings](#11-summary--ratings)

---

## 1. Architecture Overview

The job system is a multi-layered queue built on SQLite persistence with a polling-based background worker. It provides job lifecycle management, concurrency control, pause/resume, and crash recovery — all decoupled from the agent execution layer via `InstanceManager`.

### Core Modules

| File | Purpose |
|------|---------|
| `daemon/queue.py` | Core queue types and constants (legacy, deprecated) |
| `daemon/services/job_queue_service.py` | Main job queue operations (enqueue, cancel, retry) |
| `daemon/services/job_queue_mgmt_service.py` | Queue CRUD operations (create, delete, pause) |
| `daemon/services/job_processor.py` | Background worker that polls and processes jobs |
| `daemon/services/job_lock_manager.py` | In-memory lock management for concurrency control |
| `daemon/repositories/job_queue/repository.py` | Job persistence layer (SQLModel) |
| `daemon/repositories/job_queue/queue_repository.py` | Queue metadata persistence |
| `daemon/repositories/job_queue/models.py` | SQLModel table definitions |
| `daemon/routers/jobs.py` | FastAPI endpoints for job operations |
| `daemon/routers/queues.py` | FastAPI endpoints for queue management |
| `daemon/services/stale_task_recovery.py` | Crash recovery and stale task detection |

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CREATION (via API)                                │
│                                                                             │
│  POST /api/jobs → JobQueueService.enqueue()                                │
│    → validates agent                                                        │
│    → creates JobItem with status=PENDING                                   │
│    → persists to job_queue_items table                                     │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                          PROCESSING (background)                            │
│                                                                             │
│  JobProcessor._process_loop() [polls every 2s]                             │
│    → checks project/queue pause                                            │
│    → gets next pending job (priority desc, created_at asc)                │
│    → JobQueueService.start_job()                                           │
│    → atomically transitions PENDING→PROCESSING                             │
│    → acquires per-queue lock                                              │
│    → spawns instance via InstanceManager                                   │
│    → enqueues message                                                      │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                            COMPLETION                                       │
│                                                                             │
│  Instance completes                                                        │
│    → InstanceManager._complete_job_for_instance()                         │
│    → looks up job by instance_id                                           │
│    → transitions to COMPLETED or FAILED                                    │
│    → releases lock                                                         │
│    → triggers next job                                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Job Lifecycle & State Machine

### States

States are defined as an enum in `models.py`:

| State | Meaning |
|-------|---------|
| `PENDING` | Job queued, waiting for processing |
| `PROCESSING` | Job currently being executed |
| `COMPLETED` | Job finished successfully |
| `FAILED` | Job failed with error |
| `CANCELLED` | Job was cancelled |

### State Transitions

| From | To | Trigger | Method |
|------|-----|---------|--------|
| *(new)* | `PENDING` | Job created | `JobRepository.create()` |
| `PENDING` | `PROCESSING` | Worker picks up | `start_job()` / `start_job_atomic()` |
| `PROCESSING` | `COMPLETED` | Instance succeeds | `complete_job()` |
| `PROCESSING` | `FAILED` | Instance fails | `fail_job()` |
| `PENDING` | `CANCELLED` | User cancels | `cancel_job()` |
| `PROCESSING` | `CANCELLED` | User aborts | `cancel_job()` (releases lock) |
| `FAILED` | `PENDING` | Retry creates NEW job | `retry_job()` |

> **Note:** The system uses ad-hoc state validation (each method checks current state) rather than a formal state machine. This makes it easy to miss validation when adding new methods.

### Lifecycle Paths

```
Happy path:
  PENDING → PROCESSING → COMPLETED

Failure path:
  PENDING → PROCESSING → FAILED

Retry path:
  PENDING → PROCESSING → FAILED → (retry) → PENDING (NEW JOB) → ...

Cancel path:
  PENDING → CANCELLED
  PENDING → PROCESSING → CANCELLED
```

---

## 3. Job Queue & Scheduling

### Queue Architecture

Two-layer system:

**Layer 1 — Named Queues** (`job_queues` table)

Per-project isolation with configurable concurrency:

| Queue Type | Concurrency | Behavior |
|------------|-------------|----------|
| `fifo` | `concurrency_limit=1` | Strict ordering, one job at a time |
| `parallel` | `concurrency_limit=1-20` | Concurrent execution within limit |

**Layer 2 — System Queues**

Auto-provisioned per project:

| Queue | Purpose | Default Concurrency |
|-------|---------|---------------------|
| `system_fifo_queue` | Default FIFO | 1 |
| `system_parallel_queue` | Default parallel | 5 |

### Persistence

- **Database:** `data/instances.db`
- **ORM:** SQLModel / SQLAlchemy
- **Tables:** `job_queue_items`, `job_queues`

### Concurrency Control

```
┌─────────────────────────────────────────┐
│     DATABASE LEVEL (primary safety)     │
│                                         │
│  Atomic state transitions via           │
│  start_job_atomic() — UPDATE with       │
│  WHERE status = 'PENDING' ensures       │
│  single worker picks up each job        │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│      IN-MEMORY LEVEL (queue capacity)    │
│                                         │
│  JobLockManager tracks (project_id,     │
│  queue_id) → list of LockInfo           │
│  Prevents exceeding concurrency_limit   │
│  ⚠ LOST ON RESTART — no persistence    │
└─────────────────────────────────────────┘
```

### Priority & Ordering

- **Range:** 1–10 (default: 5)
- **Ordering:** `priority DESC, created_at ASC`

### Dispatching

Polling-based every **2 seconds** (`poll_interval`). Before processing each job, the worker checks:
1. Project-level pause state
2. Queue-level pause state

---

## 4. Job Types

Jobs are classified by the **`source`** field — a plain string, not an enum.

| Source | Origin | Routing Behavior |
|--------|--------|-----------------|
| `api` | REST API | Routes as `HUMAN` message |
| `telegram` | Telegram adapter | Routes as `HUMAN` message |
| `scheduler` | Scheduler adapter | Routes as `HUMAN` message |
| `webhook` | Webhook | Routes as `HUMAN` message |
| `internal_report:` | Completion report | Routes as `COMPLETION_REPORT` |
| `internal_error_report:` | Error report | Routes as `COMPLETION_REPORT` |
| `internal_agent:` | Agent-to-agent message | Routes as `AGENT` message |

All job types follow the same processing pipeline. The `source` field only affects message type routing in `InstanceManager`.

> **Adding new types is low friction** — just add a new source string prefix.

---

## 5. Error Handling & Recovery

### Failure Handling

```
Instance failure detected
  → logged
  → Job marked FAILED with error message
  → Lock released (if held)
  → Next job triggered
```

### Retry Logic

| Feature | Status |
|---------|--------|
| Manual retry via API | ✅ `POST /api/jobs/{job_id}/retry` |
| Automatic retry | ❌ Not implemented |
| Max retries | ❌ Not implemented |
| Exponential backoff | ❌ Not implemented |
| Dead-letter queue | ❌ Not implemented |

**Current retry behavior:** `retry_job()` creates an entirely **new job** with the same parameters. No automatic retry mechanism exists.

### Crash Recovery — StaleTaskRecovery

Located in `daemon/services/stale_task_recovery.py`. Monitors the **tasks table** (not `job_queue_items`).

**5-Step Recovery Sequence:**

```
1. Find stale RUNNING tasks
       ↓
2. Request cancellation
       ↓
3. Grace period (10s)
       ↓
4. Force cancel
       ↓
5. Schedule retry
```

**Startup Recovery Phases:**

| Phase | Targets | Action |
|-------|---------|--------|
| Phase A | Stale `RUNNING` tasks | Cancel + retry |
| Phase B | Orphaned `CANCELLED` tasks | Cleanup |

### Critical Gaps

> ⚠️ **Jobs in `PROCESSING` are NOT automatically recovered.** There is no timeout mechanism at the job level — a job can remain in `PROCESSING` indefinitely if the worker crashes or the instance hangs.

> ⚠️ **In-memory locks are lost on restart.** This can cause queue capacity to be temporarily misreported until locks are naturally released.

---

## 6. API Surface

### Job Endpoints (`daemon/routers/jobs.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /api/jobs` | `create_job` | Create a new job (returns 202 Accepted) |
| `GET /api/jobs/{job_id}` | `get_job` | Get job details |
| `GET /api/jobs` | `list_jobs` | List jobs with filters (status, project_id, queue_id, limit) |
| `DELETE /api/jobs/{job_id}` | `cancel_job` | Cancel a job |
| `POST /api/jobs/{job_id}/retry` | `retry_job` | Retry a failed job |
| `GET /api/jobs/{job_id}/events` | `stream_job_events` | SSE stream for real-time job updates |

### Queue Endpoints (`daemon/routers/queues.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /projects/{id}/queues` | `list_queues` | List queues with job counts |
| `POST /projects/{id}/queues` | `create_queue` | Create a custom queue |
| `GET /projects/{id}/queues/{qid}` | `get_queue` | Get queue details |
| `PATCH /projects/{id}/queues/{qid}` | `update_queue` | Update queue fields |
| `DELETE /projects/{id}/queues/{qid}` | `delete_queue` | Delete a queue |
| `POST /projects/{id}/queues/{qid}/start` | `start_queue` | Resume a paused queue |
| `POST /projects/{id}/queues/{qid}/stop` | `stop_queue` | Pause a queue |

### Project Pause Endpoints (`daemon/routers/projects.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /projects/{id}/pause-queue` | `pause_queue` | Master pause — stops ALL queues for project |
| `POST /projects/{id}/resume-queue` | `resume_queue` | Resume all queues for project |
| `PATCH /projects/{id}/queue-status` | `set_queue_status` | Set paused state directly |

### ⚠️ Security Note

> **No authentication on any endpoints.** All endpoints are publicly accessible.

---

## 7. Relationship to Sessions & Agents

### Key Distinction

> **Jobs are NOT sessions.** Jobs submit work that results in agent instances, but they are independent concepts.

### How Jobs Connect to Instances

```
Job (job_queue_items)
    │
    ├── instance_id ────────────────────────────→ Agent Instance (instances table)
    │                                                      │
    │                                                      ├── graph execution
    │                                                      ├── message handling
    │                                                      └── completion triggers job update
    │
    └── source field ──→ message type routing
                            │
                            ├── api / telegram / scheduler / webhook → HUMAN
                            ├── internal_report: / internal_error_report: → COMPLETION_REPORT
                            └── internal_agent: → AGENT
```

### Communication Flow

```
Job created
    → JobProcessor picks up job
    → InstanceManager.spawn_instance() creates agent instance
    → Message enqueued to instance
    → Instance processes work
    → Instance completes → InstanceManager._complete_job_for_instance()
    → Job transitions to COMPLETED or FAILED
```

Jobs do **not** directly communicate with sessions. The instance acts as the intermediary.

---

## 8. Pause Mechanism

Two-level pause architecture:

```
Level 1 — Project-level (master override)
┌────────────────────────────────────────────────┐
│  projects.job_queue_paused                      │
│  Stops ALL queues for the entire project        │
│  Checked FIRST in _process_next_job()          │
└────────────────────────────────────────────────┘
                    │
                    ▼ (if not paused)
Level 2 — Queue-level (granular control)
┌────────────────────────────────────────────────┐
│  job_queues.is_paused                           │
│  Stops only this specific queue                 │
│  Checked SECOND in _process_next_job()         │
└────────────────────────────────────────────────┘
```

### Behavior Summary

| Pause Level | Effect on PENDING jobs | Effect on PROCESSING jobs |
|-------------|------------------------|---------------------------|
| Project pause | Stay pending, not picked up | Complete normally |
| Queue pause | Stay pending, not picked up | Complete normally |

### Triggers

| Action | Endpoint |
|--------|----------|
| Pause project | `POST /projects/{id}/pause-queue` |
| Resume project | `POST /projects/{id}/resume-queue` |
| Pause queue | `POST /projects/{id}/queues/{qid}/stop` |
| Resume queue | `POST /projects/{id}/queues/{qid}/start` |

---

## 9. Code Quality Observations

### Anti-Patterns

| # | Issue | Location | Risk |
|---|-------|----------|------|
| 1 | **Ad-hoc state machine** — no formal state machine, easy to miss validation in new methods | `models.py`, all service methods | High |
| 2 | **In-memory locks without persistence** — locks lost on restart, can orphan PROCESSING jobs | `job_lock_manager.py` | High |
| 3 | **Dual sync/async methods** — sync versions don't work properly (`trigger_next_job_sync` logs warnings) | `job_processor.py` | Medium |
| 4 | **Direct repository access** — `job_processor.py` accesses `_repository` directly, bypassing service layer | `job_processor.py:process_next_job()` | Medium |

### Potential Bugs

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | **Orphaned locks on exception** — if event loop not running, lock never released, queue capacity permanently reduced | `job_lock_manager.py` | High |
| 2 | **Concurrent queue iteration** — no transaction spanning multiple queues, race between checking and starting | `job_processor.py` | Medium |
| 3 | **No idempotency for enqueue** — same message submitted twice creates duplicate jobs | `job_queue_service.py` | Medium |
| 4 | **Silent exception swallowing** — inner exception at line 184-191 silently passed | `job_processor.py:184-191` | Medium |

### Missing Features

| Feature | Impact |
|---------|--------|
| Job timeout (jobs can run forever) | High |
| Dead-letter queue | High |
| Automatic retry with backoff | Medium |
| Authentication / authorization | High |
| Rate limiting | Medium |
| Input validation (message length, etc.) | Medium |

### Hardcoded Non-Configurable Values

| Value | Location | Default |
|-------|----------|---------|
| JobProcessor poll interval | `daemon/api.py:223` | 2.0s |
| WorkerPool num_workers | `daemon/api.py:172` | 4 |
| SSE keepalive | `daemon/routers/jobs.py:588` | 5s |
| Default task timeout | `worker_pool.py:19` | 300.0s |
| Stale task threshold | `stale_task_recovery.py:15` | 15 min |

---

## 10. Configuration

### config.yaml Structure

```yaml
queue:
  discard_on_startup: false          # Dev helper
  llm_retry_transient_attempts: 10   # Retries for transient LLM errors
  llm_retry_timeout_attempts: 3      # Retries for timeout errors

services:
  task_timeout_minutes: 60            # Task execution timeout
  max_task_retries: 3                 # Max automatic retries (not used)
  task_retry_backoff_base: 60         # Backoff base in seconds
  task_retry_backoff_max: 3600        # Max backoff in seconds
  stale_task_cancel_grace_seconds: 10 # Grace period before force cancel
  graph_timeout_minutes: 55           # Graph execution timeout
  worker_poll_interval: 0.5           # Worker pool poll interval
  stale_task_recovery_interval: 60     # Recovery check interval

limits:
  max_instances: 100                  # Max concurrent instances
  max_children_per_instance: 50       # Max child instances per parent
  instance_timeout_minutes: 60        # Instance idle timeout
  llm_concurrency: 10                 # Max concurrent LLM calls
```

### Environment Variable Overrides

| Prefix | Config Section |
|--------|---------------|
| `OPENAI_` | LLMConfig |
| `DAEMON_` | DaemonConfig |
| `LIMITS_` | LimitsConfig |
| `QUEUE_` | QueueConfig |
| `SERVICES_` | ServicesConfig |

### Queue-Level Config (Database)

| Field | Type | Default | Range |
|-------|------|---------|-------|
| `concurrency_limit` | int | 1 | 1–20 |
| `queue_type` | string | `fifo` | `fifo` / `parallel` |
| `is_paused` | bool | `false` | — |

---

## 11. Summary & Ratings

### Overall Assessment

| Dimension | Rating | Notes |
|-----------|--------|-------|
| **Correctness** | ⭐⭐⭐☆☆ | State machine is ad-hoc; orphaned locks possible; silent exception swallowing |
| **Reliability** | ⭐⭐☆☆☆ | No job timeout; no automatic recovery for PROCESSING jobs; in-memory locks fragile |
| **Scalability** | ⭐⭐⭐☆☆ | SQLite is a bottleneck; polling model; no horizontal scaling story |
| **Observability** | ⭐⭐⭐☆☆ | SSE streaming exists; limited metrics; no structured logging |
| **Security** | ⭐☆☆☆☆ | No authentication on any endpoint; no input validation |
| **Extensibility** | ⭐⭐⭐⭐☆ | Low friction for new job types; modular architecture |
| **Operability** | ⭐⭐⭐☆☆ | Pause/resume works; no dead-letter queue; manual retry only |

### Key Strengths

- Clean separation between queue, service, and repository layers
- Two-level pause mechanism (project + queue)
- SSE streaming for real-time updates
- Low-friction job type system via source field
- Crash recovery for stale tasks (within its scope)

### Key Risks

- **PROCESSING jobs are not recoverable** — worker crash leaves jobs stuck
- **In-memory locks are ephemeral** — restart causes capacity misreporting
- **No job timeout** — jobs can run indefinitely
- **No authentication** — all endpoints are public
- **Polling model** — 2s latency floor, SQLite contention at scale

### Recommendations (Priority Order)

| Priority | Recommendation |
|----------|----------------|
| 🔴 P0 | Add job timeout mechanism with configurable max duration |
| 🔴 P0 | Persist in-memory locks to database or implement lock cleanup on startup |
| 🔴 P0 | Add authentication to all API endpoints |
| 🟠 P1 | Implement dead-letter queue for failed jobs |
| 🟠 P1 | Add automatic recovery for PROCESSING jobs (heartbeat + timeout) |
| 🟠 P1 | Replace polling with event-driven job dispatch |
| 🟡 P2 | Add formal state machine (e.g., `transitions` library) |
| 🟡 P2 | Implement idempotency key for job enqueue |
| 🟡 P2 | Add input validation (message size, field length limits) |
| 🟢 P3 | Expose configurable polling intervals via config.yaml |
| 🟢 P3 | Add structured metrics / Prometheus endpoint |

---

*Document generated from code review of `daemon/services/`, `daemon/repositories/job_queue/`, `daemon/routers/jobs.py`, `daemon/routers/queues.py`, and related modules.*
