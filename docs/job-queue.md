# Job Queue & Scheduling Guide

This guide covers the job queue and scheduling system for agents-ensemble, providing reliable, persistent task execution with retry logic, dead letter handling, and scheduling capabilities.

## Table of Contents

1. [Overview](#overview)
2. [Creating Jobs](#creating-jobs)
3. [Job States & Transitions](#job-states--transitions)
4. [Queue Types](#queue-types)
5. [Idle Gate Semantics (Defer / Background)](#idle-gate-semantics-defer--background)
6. [System Queues](#system-queues)
7. [Dead Letter Queue (DLQ)](#dead-letter-queue-dlq)
8. [Scheduling](#scheduling)
9. [Job API Reference](#job-api-reference)
10. [Queue API Reference](#queue-api-reference)
11. [Schedule API Reference](#schedule-api-reference)

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

> The diagram below uses the **legacy API status vocabulary**. The stored authority is
> the four-value `admission_state` (`queued`/`active`/`done`/`dead`) — see
> [Job States & Transitions](#job-states--transitions) below and
> [`docs/job-task-system.md`](job-task-system.md).

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

> **Vocabulary note (2026-09-01).** The authoritative stored lifecycle is the four-value
> `AdmissionState` (`queued` / `active` / `done` / `dead`) — the sole write authority on
> `job_queue_items`. The six states below are the **legacy API-facing status strings**,
> derived for `JobResponse.status` (via `_derive_legacy_status`, discriminated by
> `terminal_reason` for `done` rows). API responses still speak the legacy vocabulary;
> write sites and DB reads speak `admission_state` only. For the full model — including
> the mission/mirror kind split and the linkage contract — see
> [`docs/job-task-system.md`](job-task-system.md), the canonical core-module reference.

### States

Stored authority (`admission_state`) vs the legacy status an API caller sees:

| `admission_state` (stored) | API legacy status | Description |
|-------|------------------|-------------|
| `queued` | `pending` | Job is in queue, awaiting dequeue |
| `active` | `processing` | Job is dequeued, lock held, instance spawned/awake (pause is an instance concern — a paused job stays `active`) |
| `done` (+ `terminal_reason`) | `completed` / `failed` / `cancelled` | Terminal, no retry pending; `terminal_reason` discriminates |
| `dead` | `dead_letter` | Dead-lettered (exhausted retries) |

### Valid Transitions

`VALID_TRANSITIONS` (`daemon/services/job_state_machine.py`), with the legacy name where
one exists:

| FROM | TO | Transition |
|------|----|------------|
| (none) | `queued` | create |
| `queued` | `active` | start |
| `queued` | `done` | cancel pending |
| `active` | `done` | complete / fail / cancel / abort (NO_RETRY) |
| `active` | `queued` | retry (RETRY, backoff scheduled) |
| `active` | `dead` | dead-letter (DEAD_LETTER) |
| `done` | `queued` | replay from done |
| `dead` | `queued` | replay from DLQ (the only `dead` exit) |
| `done` | `active` | orphan-race post-commit re-arm |

`DEAD` is terminal except for DLQ replay; corrections are additive (no path re-opens a
wrongly-`done` row). Every `admission_state` write must go through a registered
authority — see the census gate in [`docs/job-task-system.md`](job-task-system.md#7-the-census-gate-phase-0).

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

### BACKGROUND

- **Concurrency**: Always 1 (enforced)
- **Behavior**: Only processes when ALL projects in the system are idle (system-wide, not per-project)
- **Use case**: Lowest-priority maintenance / cleanup that must never compete with user work
- **System queue**: `system_background_queue`

```json
{
  "queue_name": "system_background_queue",
  "queue_type": "background",
  "concurrency_limit": 1,
  "description": "System background queue - only processes when ALL projects are idle",
  "is_system": true
}
```

> **DEFER vs BACKGROUND**: `defer` is gated on the owning project being idle. `background` is gated on every project system-wide being idle — use it only for true low-priority work that should yield to everything else.
>
> **How "idle" is decided.** "Idle" is determined by the durable job/`instance` lifecycle, not by transient `task` rows. See [Idle Gate Semantics (Defer / Background)](#idle-gate-semantics-defer--background) for the full mechanism.

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

- FIFO, DEFER, and BACKGROUND queues: `concurrency_limit` must be 1
- Cannot use reserved names: `system_fifo_queue`, `system_parallel_queue`, `system_kb_fifo_queue`, `system_defer_queue`, `system_background_queue`

---

## Idle Gate Semantics (Defer / Background)

`defer` and `background` queues are gated on whether the system is idle. This section explains the durable mechanism behind that gate — what "idle" means, where it is checked, and how the system behaves on failure.

### The Two-Mechanic Model

Two mechanisms cooperate to keep defer/background queues from running while work is in flight:

1. **Bus watcher lifecycle keeps `JobItem` active.** When a non-defer message job spawns a child, the `DependencyBus` registers a pending watcher. While the watcher is pending, the message job's `admission_state` stays `active`. The watcher is only released when the child reaches a terminal state, allowing the message job to finalize to `done`. This keeps the job "in flight" across the full message + children lifecycle, including the inter-turn gaps between `process_message` and the eventual `process_report` turns.

2. **Job predicates carry lifecycle state into idle checks.** The defer/background admission paths read the durable job / `instance` lifecycle through `JobRepository.has_active_non_deferred_work` / `has_active_non_background_work`; maintenance's `_is_idle` uses the system-wide non-deferred form as an idle-detection probe. The busy-set is mission-respecting: the gate blocks on **non-terminal instances of non-defer jobs**, not on active job rows as such. Concretely, a project is busy iff any non-deleted JobItem on a non-defer queue satisfies **either** the legacy clause — `admission_state` is `queued` / `active` **and** its instance is non-terminal (`running` / `waiting_children` / `paused`) — **or** the post-settle clause — `job_type='message'` with `admission_state='done'` (a Fix-B settled mirror) **and** a non-terminal instance. Because the job predicate follows the job → instance lineage, it still sees a parent during the `waiting_children` inter-turn gap even when no `task` row exists — and, since the post-settle widening (2026-09-03), also after Fix B has already settled the message mirror at T0. The background form preserves the same lifecycle coverage with system-wide scope.

   > **Asymmetry (defer vs background legacy clause).** The two predicate bodies (`daemon/repositories/job_queue/_idle_predicate_sql.py`) are NOT identical in their legacy clause: the **defer** body counts `admission_state = 'active'` only (a defer candidate's own row sits on the defer queue and is excluded by `queue_type NOT IN :excluded_queue_types`, so a `queued` defer row is structurally irrelevant), while the **background** body counts `admission_state IN ('queued','active')` (the background queue must starve on both rows because a `queued` non-background job with no instance yet would otherwise leak past the gate — see the Fix-2B deadlock carve-out below).

   > **I3 clarifying line.** A settled mirror of a non-terminal instance counts as **live** for the defer/background gate, terminal for everything else. The busy-set's truthmaker is `Instance.status` — instance liveness IS mission liveness — and the two predicate bodies are shared SQL constants (`daemon/repositories/job_queue/_idle_predicate_sql.py`) so all five gate/maintenance consumers cannot drift.

   > **Dialect note (hotfix 2026-09-04, PG `AmbiguousParameter`).** The defer body is split into two SQL forms — a project-scoped body with a plain `j.project_id = :project_id` equality (a STRING bind, no NULL trick) and a system-wide body with NO project parameter at all — so no NULL-typed bind ever reaches PG; the predicates themselves also fail-CLOSED on DB error so a transient failure holds the gate (returning True / BUSY) instead of silently releasing it.

### Belt-and-Suspenders Pattern

The two signals layer for safety, but each consumer has a distinct role:

- **Job predicate** is the primary definition of "idle" for admission. It scopes to job → instance lineage so that a `waiting_children` parent in one project never blocks unrelated defer work.
- **Task predicates** (`TaskRepository.has_active_non_deferred_work` / `has_active_non_background_work`) are fallback legs after the job predicate in the `job_processor` and `job_queue_service` admission gates. They also feed the maintenance idle probe and provide defense-in-depth for virtual / queue-less work — work that runs without a `JobItem` row but still holds an active `task`. The job predicate cannot see queue-less work; the task predicate can.
- The same task-level conditions are additionally represented inside `claim_pending_task` as an atomic SQL guard, rather than as a separate application-level pre-check.

The phrase "either predicate is active, so block" applies to admission gates only. There, the job predicate is checked first and an active result from either predicate prevents admission. The maintenance probe and the atomic claim guard have the separate semantics described below.

> **Asymmetry.** The FIFO lane's `waiting_children` carve-out (`daemon/repositories/task/repository.py:653`) is intentionally **not** mirrored here. The FIFO carve-out lets a `waiting_children` parent yield its FIFO slot to a *different* instance; the defer gate deliberately treats `waiting_children` as busy. See [Carve-out parity](#carve-out-parity) below.

### Gate Topology

The three consumers of the idle signals must not be conflated:

| Consumer | Live topology | Failure / result semantics |
|----------|---------------|----------------------------|
| **Admission gates** — `job_processor` Gate A (`_defer_idle_check`, `_background_idle_check`) and `job_queue_service` Gate B (the defer/background branches of `_select_next_eligible_job`) | `JobRepository` predicate first **OR** corresponding `TaskRepository` predicate as a fallback leg | **Fail-CLOSED.** An active result from either predicate blocks admission; predicate-call errors are treated as busy. |
| **Maintenance probe** — `maintenance._is_idle` | Uses `JobRepository.has_active_non_deferred_work(None)` and `TaskRepository.has_active_non_deferred_work(None)` as the same system-wide non-deferred probes, alongside its other checks | **Not an admission gate. Fail-OPEN / best effort.** An active result makes the probe report not idle, while an error is logged and the probe continues to the next check; the probe may ultimately report idle. |
| **Atomic claim guard** — `TaskRepository.claim_pending_task` | The task-level defer/background conditions are inlined in the atomic candidate-selection SQL | No separate application-level fail policy. The SQL guard participates in the claim statement itself. |

### Fail-CLOSED Behavior (Admission Gates Only)

The `job_processor` and `job_queue_service` admission gates wrap their job-predicate and task-predicate calls and **block on error**:

- If a transient database error occurs (lock contention, connection blip, schema mismatch), the gate reports "busy" rather than admitting.
- A failing predicate is treated as evidence that the durable lifecycle is unreadable, not as evidence that the project is idle.
- This is a deliberate safety posture: a momentary block is acceptable; a premature admission causes the original bug (defer running while non-defer work is in flight).

Concretely, `_defer_idle_check`, `_background_idle_check`, the defer branch of `_select_next_eligible_job`, and the background branch of `_select_next_eligible_job` use the unified **fail-CLOSED** admission policy. This policy applies only to those admission gates; it does not turn `maintenance._is_idle` into an admission gate and does not add an application-level policy around the atomic claim SQL.

> **Maintenance `_is_idle` is a probe, not an admission gate.** Each job/task predicate probe (`daemon/services/maintenance.py:_is_idle`), along with `list_all_pending` / `find_processing_jobs` and the request registry, is wrapped in `try/except`. On exception it logs a warning and continues rather than returning `False`; if every probe raises or reports no work, `_is_idle` returns `True` and the maintenance loop runs. This is **fail-OPEN at the probe layer** and is intentionally best-effort. An active predicate result can make maintenance skip a cycle, but it does not itself decide defer/background admission.

### Paused Instances Remain Busy

`paused` is treated as **suspended-but-occupying**, not as idle:

- A paused instance holds `admission_state='active'` on its non-defer job. The job predicate counts it as in-flight.
- The instance's `Task.status='paused'` also counts as blocking in the task predicate.
- This mirrors the existing "paused holds the lock" semantics and ensures that pausing a long-running parent does not accidentally admit defer work.

If you pause an instance and observe that defer jobs are still being admitted, that is the bug this invariant prevents. The pause MUST keep the queue lock, and defer / background MUST stay blocked until the instance resumes and reaches `idle`.

### Carve-out Parity

The FIFO lane has a `waiting_children` carve-out that lets a waiting parent yield its FIFO slot to a different instance. That carve-out is **about FIFO claiming** — it ensures one instance's `waiting_children` does not block *another* instance's FIFO task. It is **not** about defer/background idle semantics, and it intentionally does not apply there.

**Do not unify these.** The FIFO carve-out is per-instance FIFO claiming; the defer/background gate is global idle. Treating `waiting_children` as busy for defer (but yielding FIFO slots for FIFO) is the correct asymmetry. Unifying them will reintroduce the original 2026-07-23 incident.

### Historical Incident (2026-07-23)

**Project:** `83da04de`. **Time:** 2026-07-23 ~10:36 local (UTC+7). **Severity:** defer admitted during active non-defer work.

**What happened.** Instance `40f1be39` (leader) was driving a graph turn that spawned a long-running child (~1 hour, 09:59 → 11:00). While the child worked, the parent sat in `waiting_children` with no active `task` row for ~60 minutes. During that gap, defer job `be336411` was admitted at 10:36:22. The user paused `be336411` manually.

**Why the gate missed it.** `has_active_non_deferred_work` was task-granular (`status IN ('pending','running','paused') AND is_deferred=false`). A parent in `waiting_children` between turns has no active task row — even though its work is in flight. The same blind spot applied to the `background` queue, system-wide.

**What changed (two-phase fix).**

- **Phase 1** (commit `6e077ddd`, since removed as dead code) tried adding an instance-status gate. It was found to be unreachable: lifecycle events commit `instance.status` to terminal BEFORE publishing, so the gate always saw terminal state.
- **Phase 2** (commits `059d1ecc`, `5c83e519`) restored lifecycle-aware predicates on `JobRepository`: `has_active_non_deferred_work(project_id)` (project-scoped) and `has_active_non_background_work()` (system-wide). Both count jobs with `admission_state IN ('queued','active')` whose instance is non-terminal. The `DependencyBus` now keeps pending watchers through the inter-report gap, so the message job's `admission_state` stays `active` for the full message + children lifecycle.

**Verified by.** `tests/job_queue/test_defer_idle_gate_phase2.py` (predicate unit tests, incident reproduction, and gate composition), `tests/job_queue/test_defer_gate_post_settle_window.py` (post-settle window: settled-mirror busy clause, folding proof-test, shared-SQL-body drift guard, self-deadlock pin, PG/SQLite parity), and existing invariant coverage in `tests/job_queue/test_seam_invariants.py` (W-series: NULL `message_id` guard, defer-not-admitted during active virtual work, defer-completes after idle).

**See also.** `docs/plans/defer-queue-idle-gate.md` for the full root-cause analysis, architecture smell, and risk analysis (defer / background starvation, self-deadlock, lock retention).

---

## System Queues

System queues are auto-provisioned per project when first needed. They handle different job types:

| Queue Name | Type | Concurrency | Purpose |
|------------|------|-------------|---------|
| `system_fifo_queue` | FIFO | 1 | TASK jobs - serial execution |
| `system_parallel_queue` | PARALLEL | 5 | MESSAGE jobs - concurrent execution |
| `system_kb_fifo_queue` | FIFO | 1 | Knowledge base import jobs |
| `system_defer_queue` | DEFER | 1 | Project-idle tasks (per-project) |
| `system_background_queue` | BACKGROUND | 1 | System-wide low-priority tasks (all-projects idle) |

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
  "created_queues": ["system_kb_fifo_queue", "system_defer_queue", "system_background_queue"],
  "total_system_queues": 5
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
| `queue_type` | string | "fifo", "parallel", "defer", or "background" |
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
