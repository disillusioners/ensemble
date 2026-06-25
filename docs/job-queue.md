# Job Queue & Scheduling Guide

This guide covers the job queue and scheduling system for agents-ensemble, providing reliable, persistent task execution with retry logic, dead letter handling, and scheduling capabilities.

## Table of Contents

1. [Overview](#overview)
2. [Creating Jobs](#creating-jobs)
3. [Job States & Transitions](#job-states--transitions)
4. [Queue Types](#queue-types)
5. [System Queues](#system-queues)
6. [Dead Letter Queue (DLQ)](#dead-letter-queue-dlq)
7. [Scheduling](#scheduling)
8. [Job API Reference](#job-api-reference)
9. [Queue API Reference](#queue-api-reference)
10. [Schedule API Reference](#schedule-api-reference)

---

## Overview

The job queue provides persistent, reliable task execution with the following capabilities:

- **Persistent Jobs**: All jobs are stored in SQLite for crash recovery
- **Priority Scheduling**: Jobs are ordered by priority (1-10) then FIFO
- **Per-Queue Serialization**: Queue-level locking prevents concurrent execution beyond concurrency limits
- **Retry with Backoff**: Automatic retry with exponential backoff for failed jobs
- **Dead Letter Queue**: Failed jobs that exhaust retries are moved to DLQ for manual inspection
- **Idempotency**: Deduplication via idempotency keys with configurable TTL
- **Real-time Updates**: SSE streaming for job status changes

### Job Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         JOB LIFECYCLE                                   │
└────────────────────────────────────────────────────────────────────────┘

                            ┌──────────────┐
                            │    START     │
                            └──────┬───────┘
                                   │
                                   ▼
                         ┌─────────────────┐
                         │    PENDING     │◄────────────────┐
                         └────────┬────────┘                 │
                                  │                         │
                     ┌────────────┼────────────┐             │
                     │            │            │             │
                     ▼            │            ▼             │
            ┌────────────┐        │    ┌───────────┐        │
            │ CANCELLED  │        │    │PROCESSING │─────────┘
            └────────────┘        │    └─────┬─────┘  (requeue)
                     ▲            │          │
                     │            │    ┌─────┼─────┐
                     │            │    │     │     │
                     │            │    ▼     │     ▼
                     │            │ COMPLETED │   FAILED
                     │            │    │     │     │
                     │            │    │     │     ├────► DEAD_LETTER
                     │            │    │     │     │     (exhausted retries)
                     │            │    │     │     │
                     │            │    │     │     ▼
                     │            │    │     │ CANCELLED
                     │            │    │     │ (stop pending retries)
                     │            │    │     │
                     │            └────┴─────┘
                     │                 │
                     │                 ▼
                     │          (auto-retry or DLQ)
                     │                 │
                     └─────────────────┘
                     (manual retry)
```

---

## Creating Jobs

### POST /api/jobs

Submit a new job for processing.

**Request:**

```bash
curl -X POST http://localhost:8079/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "developer",
    "message": "Fix the login bug in auth.py",
    "project_id": "my-project-uuid",
    "priority": 7,
    "source": "api",
    "metadata": {
      "user_id": "user-123",
      "ticket_id": "TICKET-456"
    },
    "idempotency_key": "fix-login-2026-05-29"
  }'
```

**Response (201 Created - new job):**

```json
{
  "job_id": "job-abc123",
  "status": "pending",
  "priority": 7,
  "agent_id": "developer",
  "agent_dir": "/path/to/agents/developer",
  "project_id": "my-project-uuid",
  "queue_id": "queue-xyz",
  "instance_id": null,
  "created_at": "2026-05-29T10:00:00Z",
  "started_at": null,
  "completed_at": null,
  "result_summary": null,
  "error_message": null,
  "position": 1,
  "message": "Job queued for processing",
  "source": "api",
  "job_metadata": {
    "user_id": "user-123",
    "ticket_id": "TICKET-456"
  },
  "cancelled_at": null,
  "idempotency_key": "fix-login-2026-05-29",
  "dlq_reason": null,
  "retry_count": null,
  "moved_to_dlq_at": null,
  "deleted_at": null
}
```

### Request Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `agent_id` | string | Yes | - | Agent ID (e.g., "developer", "leader") |
| `message` | string | Yes | - | Job message/content for the agent |
| `project_id` | string | No | null | Project ID for job serialization |
| `queue_id` | string | No | auto | Queue ID to assign job to a specific queue |
| `priority` | integer | No | 5 | Priority 1-10 (higher = more urgent) |
| `source` | string | No | "api" | Source of job: "api", "telegram", "scheduler", "webhook" |
| `metadata` | object | No | {} | Optional metadata dictionary |
| `idempotency_key` | string | No | null | Key for deduplication (max 255 chars) |

### Idempotency

When `idempotency_key` is provided, the system:

1. Checks for existing job with same key
2. If found and non-terminal (pending/processing), returns existing job with HTTP 200
3. If found but terminal, creates new job (allows re-running)
4. TTL: 24 hours (configurable via `ENSEMBLE_JOB_SYSTEM_IDEMPOTENCY_KEY_TTL_HOURS`)

**Response (200 OK - idempotent return):**

```json
{
  "job_id": "job-existing",
  "status": "processing",
  ...
  "message": "Existing job returned (idempotent)"
}
```

---

## Job States & Transitions

### States

| State | Description |
|-------|-------------|
| `PENDING` | Job is queued, waiting to be processed |
| `PROCESSING` | Job is actively being executed |
| `COMPLETED` | Job finished successfully |
| `FAILED` | Job failed (may be retried) |
| `CANCELLED` | Job was cancelled (no retry) |
| `DEAD_LETTER` | Job exhausted retries, moved to DLQ |

### Valid Transitions

```
┌──────────────────────────────────────────────────────────────────┐
│                    STATE TRANSITION TABLE                         │
├──────────────────┬───────────────────────┬──────────────────────┤
│     FROM         │         TO            │   TRANSITION NAME    │
├──────────────────┼───────────────────────┼──────────────────────┤
│ (none)           │ PENDING               │ create               │
│ PENDING          │ PROCESSING            │ start                │
│ PENDING          │ CANCELLED             │ cancel               │
│ PROCESSING       │ COMPLETED             │ complete             │
│ PROCESSING       │ FAILED                │ fail                 │
│ PROCESSING       │ CANCELLED             │ abort                │
│ PROCESSING       │ PENDING               │ requeue              │
│ FAILED           │ PENDING               │ retry                │
│ FAILED           │ DEAD_LETTER           │ dead_letter          │
│ FAILED           │ CANCELLED             │ cancel_after_fail    │
│ DEAD_LETTER      │ PENDING               │ replay               │
└──────────────────┴───────────────────────┴──────────────────────┘
```

### Retry Behavior

When a job fails, the retry engine automatically:

1. **Checks retry eligibility**: Job status must be FAILED, max_retries not exceeded
2. **Calculates backoff**: Exponential backoff with jitter
3. **Schedules retry**: Transitions FAILED → PENDING at calculated time
4. **Moves to DLQ**: If retries exhausted

**Backoff Formula:**

```
delay = min(base × multiplier^retry_count + jitter, max_delay)
jitter = random(0, base × 0.5)
```

**Default Configuration:**

| Parameter | Default | Env Variable |
|-----------|---------|--------------|
| Base delay | 60 seconds | `ENSEMBLE_JOB_SYSTEM_RETRY_BACKOFF_BASE_SECONDS` |
| Max delay | 3600 seconds (1 hour) | `ENSEMBLE_JOB_SYSTEM_RETRY_BACKOFF_MAX_SECONDS` |
| Multiplier | 2.0 | `ENSEMBLE_JOB_SYSTEM_RETRY_BACKOFF_MULTIPLIER` |
| Default max retries | 3 | `ENSEMBLE_JOB_SYSTEM_DEFAULT_MAX_RETRIES` |

**Backoff Examples (with defaults):**

| Retry # | Base Calculation | Jitter Range | Total Delay |
|---------|------------------|--------------|-------------|
| 1 | 60 × 2⁰ = 60s | 0-30s | 60-90s |
| 2 | 60 × 2¹ = 120s | 0-30s | 120-150s |
| 3 | 60 × 2² = 240s | 0-30s | 240-270s |
| 4 | 60 × 2³ = 480s | 0-30s | 480-510s |
| 5 | 60 × 2⁴ = 960s | 0-30s | 960-990s (capped) |

---

## Queue Types

### FIFO (First In, First Out)

- **Concurrency**: Always 1 (enforced)
- **Use case**: Sequential tasks where order matters
- **System queue**: `system_fifo_queue`

```json
{
  "queue_name": "system_fifo_queue",
  "queue_type": "fifo",
  "concurrency_limit": 1,
  "is_system": true
}
```

### PARALLEL

- **Concurrency**: Configurable (1-20)
- **Use case**: Independent tasks that can run concurrently
- **System queue**: `system_parallel_queue`

```json
{
  "queue_name": "system_parallel_queue",
  "queue_type": "parallel",
  "concurrency_limit": 5,
  "is_system": true
}
```

### DEFER

- **Concurrency**: Always 1 (enforced)
- **Behavior**: Only processes when project has NO active work
- **Use case**: Background tasks that shouldn't interfere with user-facing work
- **System queue**: `system_defer_queue`

```json
{
  "queue_name": "system_defer_queue",
  "queue_type": "defer",
  "concurrency_limit": 1,
  "description": "System defer queue - only processes when project is idle",
  "is_system": true
}
```

### Creating Custom Queues

**POST /projects/{project_id}/queues**

```bash
curl -X POST http://localhost:8079/api/projects/my-project/queues \
  -H "Content-Type: application/json" \
  -d '{
    "queue_name": "high-priority",
    "queue_type": "parallel",
    "concurrency_limit": 3,
    "description": "High priority parallel processing"
  }'
```

**Constraints:**

- FIFO and DEFER queues: `concurrency_limit` must be 1
- Cannot use reserved names: `system_fifo_queue`, `system_parallel_queue`, `system_kb_fifo_queue`, `system_defer_queue`

---

## System Queues

System queues are auto-provisioned per project when first needed. They handle different job types:

| Queue Name | Type | Concurrency | Purpose |
|------------|------|-------------|---------|
| `system_fifo_queue` | FIFO | 1 | TASK jobs - serial execution |
| `system_parallel_queue` | PARALLEL | 5 | MESSAGE jobs - concurrent execution |
| `system_kb_fifo_queue` | FIFO | 1 | Knowledge base import jobs |
| `system_defer_queue` | DEFER | 1 | Background tasks (idle-only) |

### Ensuring System Queues

**POST /projects/{project_id}/queues/ensure-system**

```bash
curl -X POST http://localhost:8079/api/projects/my-project/queues/ensure-system
```

**Response:**

```json
{
  "project_id": "my-project",
  "existing_queues": ["system_fifo_queue", "system_parallel_queue"],
  "created_queues": ["system_kb_fifo_queue", "system_defer_queue"],
  "total_system_queues": 4
}
```

---

## Dead Letter Queue (DLQ)

The DLQ stores jobs that have exhausted their retry attempts.

### DLQ Reasons

| Reason | Description |
|--------|-------------|
| `MAX_RETRIES` | Job exceeded max retry attempts |
| `MANUAL` | Manually moved to DLQ |

### Viewing DLQ

**GET /projects/{project_id}/dlq**

```bash
curl "http://localhost:8079/api/projects/my-project/dlq?limit=20&offset=0"
```

**Response:**

```json
{
  "items": [
    {
      "dlq_id": "dlq-abc123",
      "job_id": "job-failed-456",
      "agent_id": "developer",
      "agent_dir": "/path/to/agents/developer",
      "message": "Process large dataset",
      "source": "api",
      "project_id": "my-project",
      "queue_id": "queue-xyz",
      "priority": 5,
      "error_message": "Connection timeout after 3 retries",
      "retry_count": 3,
      "failed_at": "2026-05-29T10:00:00",
      "moved_to_dlq_at": "2026-05-29T10:30:00",
      "reason": "MAX_RETRIES",
      "metadata": {}
    }
  ],
  "total": 1
}
```

### Get DLQ Item

**GET /projects/{project_id}/dlq/{dlq_id}**

Get details of a specific DLQ item.

```bash
curl "http://localhost:8079/api/projects/my-project/dlq/dlq-abc123"
```

**Response:**

```json
{
  "dlq_id": "dlq-abc123",
  "job_id": "job-failed-456",
  "agent_id": "developer",
  "agent_dir": "/path/to/agents/developer",
  "message": "Process large dataset",
  "source": "api",
  "project_id": "my-project",
  "queue_id": "queue-xyz",
  "priority": 5,
  "error_message": "Connection timeout after 3 retries",
  "retry_count": 3,
  "failed_at": "2026-05-29T10:00:00",
  "moved_to_dlq_at": "2026-05-29T10:30:00",
  "reason": "MAX_RETRIES",
  "metadata": {}
}
```

### Delete DLQ Item

**DELETE /projects/{project_id}/dlq/{dlq_id}**

Permanently delete a DLQ item. The original job remains in DEAD_LETTER status.

```bash
curl -X DELETE "http://localhost:8079/api/projects/my-project/dlq/dlq-abc123"
```

**Response:** `204 No Content`

### Replaying Individual Jobs

**POST /projects/{project_id}/dlq/{dlq_id}/replay**

```bash
curl -X POST http://localhost:8079/api/projects/my-project/dlq/dlq-abc123/replay
```

**Response:**

```json
{
  "job_id": "job-failed-456",
  "status": "pending",
  "message": "Job queued for replay"
}
```

This atomically:
1. Resets job status to PENDING
2. Resets retry_count to 0
3. Clears error_message, failed_at, started_at, completed_at, instance_id
4. Deletes the DLQ entry

### Replaying All DLQ Jobs

**POST /projects/{project_id}/dlq/replay-all**

```bash
curl -X POST "http://localhost:8079/api/projects/my-project/dlq/replay-all?limit=100"
```

**Response:**

```json
{
  "total": 150,
  "limit": 100,
  "replayed": 95,
  "failed": 3,
  "skipped": 52,
  "errors": [
    {"dlq_id": "dlq-1", "error": "Job not in dead_letter state"},
    {"dlq_id": "dlq-2", "error": "Job not found"}
  ]
}
```

### Cleanup/Purge

**DELETE /projects/{project_id}/dlq**

```bash
curl -X DELETE "http://localhost:8079/api/projects/my-project/dlq?max_age_days=30&reason=MAX_RETRIES"
```

**Response:**

```json
{
  "deleted_count": 5,
  "message": "Deleted 5 DLQ items"
}
```

---

## Scheduling

Schedules are created via the Sources API with `source_type: "scheduler"`.

### Creating a Schedule

**POST `/api/sources`**

```bash
curl -X POST http://localhost:8079/api/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "daily-standup",
    "source_type": "scheduler",
    "name": "Daily Standup",
    "enabled": true,
    "config": {
      "type": "cron",
      "schedule": "0 9 * * 1-5",
      "agent_id": "leader",
      "agent": "./agents/leader",
      "message": "Run daily standup report",
      "instance_mode": "reuse_instance",
      "max_concurrent": 1
    }
  }'
```

**Config Fields**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Schedule type: `cron`, `interval`, or `one_time` |
| `schedule` | string | Yes* | Cron expression (for `cron` type) |
| `interval_seconds` | integer | Yes* | Interval in seconds (for `interval` type) |
| `run_at` | string | Yes* | ISO 8601 datetime (for `one_time` type) |
| `agent` | string | Yes | Agent path (e.g., `./agents/leader`) |
| `agent_id` | string | No | Agent ID for reference |
| `message` | string | Yes | Message to send to the agent |
| `instance_mode` | string | No | `new_instance` (default) or `reuse_instance` |
| `max_concurrent` | integer | No | Max concurrent executions (default: 1) |
| `timezone` | string | No | Timezone (default: UTC) |
| `priority` | integer | No | Job priority 1-10 (default: 5) |

*Only required for the respective schedule type.

The scheduler adapter supports three schedule types:

### Cron Expressions

**Format:** Standard cron (5 fields)

| Field | Values | Wildcards |
|-------|--------|-----------|
| Minute | 0-59 | , - * / |
| Hour | 0-23 | , - * / |
| Day of Month | 1-31 | , - * / |
| Month | 1-12 | , - * / |
| Day of Week | 0-6 (Sun-Sat) | , - * / |

**Examples:**

| Expression | Description |
|------------|-------------|
| `0 9 * * 1-5` | Every weekday at 9 AM |
| `*/15 * * * *` | Every 15 minutes |
| `0 0 1 * *` | First day of every month at midnight |
| `30 17 * * *` | Every day at 5:30 PM |

### Interval Scheduling

**`interval_seconds`**: Run every N seconds

```json
{
  "config": {
    "type": "interval",
    "interval_seconds": 300,
    "agent": "./agents/leader",
    "message": "Check system health"
  }
}
```

### One-Time Scheduling

**`run_at`**: Run once at specific time (ISO 8601 format)

```json
{
  "config": {
    "type": "one_time",
    "run_at": "2026-06-01T10:00:00Z",
    "agent": "./agents/developer",
    "message": "Deploy to production"
  }
}
```

### Instance Modes

| Mode | Behavior |
|------|----------|
| `new_instance` (default) | Creates new instance per execution |
| `reuse_instance` | Reuses existing instance across executions (max_concurrent=1 enforced) |

**Note:** For `one_time` schedules, `new_instance` is always enforced.

### Managing Schedules

Schedules are managed via the Source API:

**List schedules:**

```bash
curl http://localhost:8079/api/schedules
```

**Update schedule:**

```bash
curl -X PUT http://localhost:8079/api/schedules/scheduler-123 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Morning Briefing",
    "config": {
      "interval_seconds": 3600
    }
  }'
```

**Trigger immediately:**

```bash
curl -X POST http://localhost:8079/api/schedules/scheduler-123/trigger
```

**Start/Stop scheduler:**

```bash
# Start
curl -X POST http://localhost:8079/api/schedules/scheduler-123/start

# Stop
curl -X POST http://localhost:8079/api/schedules/scheduler-123/stop
```

**Get execution history:**

```bash
curl "http://localhost:8079/api/schedules/scheduler-123/executions?limit=50&offset=0"
```

---

## Job API Reference

### Jobs Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/jobs` | Create a new job |
| `GET` | `/api/jobs/{job_id}` | Get job by ID |
| `GET` | `/api/jobs` | List jobs with filters |
| `DELETE` | `/api/jobs/{job_id}` | Cancel or soft-delete job |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a job |
| `POST` | `/api/jobs/{job_id}/restore` | Restore a soft-deleted job |
| `POST` | `/api/jobs/{job_id}/retry` | Retry a failed or DLQ job |
| `GET` | `/api/jobs/{job_id}/events` | SSE stream for job updates |

### Query Parameters for GET /api/jobs

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | null | Filter by status (comma-separated) |
| `project_id` | string | null | Filter by project ID |
| `queue_id` | string | null | Filter by queue ID |
| `limit` | integer | 50 | Max results (1-100) |
| `include_deleted` | boolean | false | Include soft-deleted jobs |

**Example:**

```bash
curl "http://localhost:8079/api/jobs?status=pending,processing&project_id=my-project&limit=20"
```

### Retry Endpoint

**POST /api/jobs/{job_id}/retry**

- **FAILED jobs**: Creates NEW job with same parameters (original stays FAILED)
- **DEAD_LETTER jobs**: Resets existing job to PENDING via DLQ replay

### SSE Streaming

**GET /api/jobs/{job_id}/events**

```bash
curl -N http://localhost:8079/api/jobs/job-abc123/events
```

**Events:**

```
event: connected
data: {"job_id": "job-abc123", "status": "pending", "instance_id": null, "queue_id": "queue-xyz"}

event: status_update
data: {"job_id": "job-abc123", "status": "processing", "instance_id": "inst-123", "previous_status": "pending", "queue_id": "queue-xyz"}

event: completed
data: {"job_id": "job-abc123", "status": "completed", "result_summary": "Task completed", "error_message": null, "queue_id": "queue-xyz"}
```

---

## Queue API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/projects/{project_id}/queues` | List all queues |
| `POST` | `/api/projects/{project_id}/queues` | Create a custom queue |
| `GET` | `/api/projects/{project_id}/queues/{queue_id}` | Get queue details |
| `PATCH` | `/api/projects/{project_id}/queues/{queue_id}` | Update queue settings |
| `DELETE` | `/api/projects/{project_id}/queues/{queue_id}` | Delete a queue |
| `POST` | `/api/projects/{project_id}/queues/ensure-system` | Ensure system queues exist |
| `POST` | `/api/projects/{project_id}/queues/{queue_id}/start` | Resume a paused queue |
| `POST` | `/api/projects/{project_id}/queues/{queue_id}/stop` | Pause a queue |

### Queue Fields

| Field | Type | Description |
|-------|------|-------------|
| `queue_id` | string | Unique queue identifier |
| `project_id` | string | Owning project ID |
| `queue_name` | string | Display name (unique per project) |
| `queue_type` | string | "fifo", "parallel", or "defer" |
| `concurrency_limit` | integer | Max concurrent jobs (1-20) |
| `is_system` | boolean | System queue (cannot delete) |
| `is_paused` | boolean | Queue is paused |
| `description` | string | Optional description |
| `active_jobs` | integer | Currently processing jobs |
| `pending_jobs` | integer | Queued jobs waiting |

### Pausing/Resuming Queues

```bash
# Pause queue
curl -X POST http://localhost:8079/api/projects/my-project/queues/queue-xyz/stop

# Resume queue
curl -X POST http://localhost:8079/api/projects/my-project/queues/queue-xyz/start
```

### Deleting Queues

```bash
curl -X DELETE http://localhost:8079/api/projects/my-project/queues/queue-xyz
```

**Behavior:**
- PENDING jobs are reassigned to system FIFO queue
- PROCESSING jobs block deletion (returns 409)
- System queues cannot be deleted (returns 403)

---

## Schedule API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/schedules` | List all schedules |
| `PUT` | `/api/schedules/{schedule_id}` | Update schedule configuration |
| `POST` | `/api/schedules/{schedule_id}/trigger` | Manually trigger execution |
| `POST` | `/api/schedules/{schedule_id}/start` | Start scheduler |
| `POST` | `/api/schedules/{schedule_id}/stop` | Stop scheduler |
| `GET` | `/api/schedules/{schedule_id}/executions` | Get execution history |

### Schedule Configuration

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name |
| `config.type` | string | "cron", "interval", "one_time" |
| `config.schedule` | string | Cron expression (for cron type) |
| `config.interval_seconds` | integer | Interval in seconds (for interval type) |
| `config.run_at` | string | ISO datetime (for one_time type) |
| `config.agent` | string | Agent path |
| `config.message` | string | Message content |
| `config.timezone` | string | Timezone (default: UTC) |
| `config.priority` | integer | Job priority (1-10) |
| `config.instance_mode` | string | "new_instance" or "reuse_instance" |
| `status` | string | "running", "stopped", "error" |

### Execution History

```bash
curl "http://localhost:8079/api/schedules/scheduler-123/executions?limit=100&offset=0"
```

**Response:**

```json
{
  "executions": [
    {
      "execution_id": "exec-abc123",
      "schedule_id": "scheduler-123",
      "triggered_at": "2026-05-29T09:00:00Z",
      "instance_id": "inst-xyz",
      "status": "completed",
      "error_message": null,
      "completed_at": "2026-05-29T09:05:00Z"
    }
  ],
  "total": 1
}
```

**Execution Statuses:**

| Status | Description |
|--------|-------------|
| `triggered` | Execution triggered |
| `queued` | Job queued (for scheduled triggers with project_id) |
| `completed` | Execution finished |
| `failed` | Execution failed |
| `skipped` | Skipped due to max concurrent or active instance |

---

## DLQ API Reference

### DLQ Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/projects/{project_id}/dlq` | List DLQ items with pagination |
| `GET` | `/api/projects/{project_id}/dlq/{dlq_id}` | Get a specific DLQ item |
| `DELETE` | `/api/projects/{project_id}/dlq/{dlq_id}` | Delete a single DLQ item |
| `POST` | `/api/projects/{project_id}/dlq/{dlq_id}/replay` | Replay a single DLQ item |
| `POST` | `/api/projects/{project_id}/dlq/replay-all` | Replay all DLQ items |
| `DELETE` | `/api/projects/{project_id}/dlq` | Bulk cleanup DLQ items |

### Query Parameters for GET /api/projects/{project_id}/dlq

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 50 | Max results (1-100) |
| `offset` | integer | 0 | Pagination offset |
| `queue_id` | string | null | Filter by queue ID |
| `reason` | string | null | Filter by reason (MAX_RETRIES, MANUAL) |

### Query Parameters for DELETE /api/projects/{project_id}/dlq

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_age_days` | integer | 30 | Delete items older than N days |
| `reason` | string | null | Filter by reason |

---

## Configuration

Environment variables for job system configuration:

| Variable | Default | Description |
|----------|---------|-------------|
| `ENSEMBLE_JOB_SYSTEM_DEFAULT_MAX_RETRIES` | 3 | Default max retry attempts |
| `ENSEMBLE_JOB_SYSTEM_RETRY_BACKOFF_BASE_SECONDS` | 60 | Base delay for backoff |
| `ENSEMBLE_JOB_SYSTEM_RETRY_BACKOFF_MAX_SECONDS` | 3600 | Maximum backoff delay |
| `ENSEMBLE_JOB_SYSTEM_RETRY_BACKOFF_MULTIPLIER` | 2.0 | Exponential multiplier |
| `ENSEMBLE_JOB_SYSTEM_DLQ_ENABLED` | true | Enable DLQ functionality |
| `ENSEMBLE_JOB_SYSTEM_IDEMPOTENCY_KEY_TTL_HOURS` | 24 | Idempotency key TTL |
| `ENSEMBLE_JOB_SYSTEM_JOB_RETRY_SCHEDULER_ENABLED` | (empty) | Enable retry scheduler background task |
