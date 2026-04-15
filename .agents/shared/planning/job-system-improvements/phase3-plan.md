# Phase 3: Resilience — Dead-Letter Queue & Automatic Retry

## Objective

Implement a dead-letter queue (DLQ) for jobs that fail permanently and an automatic retry engine with exponential backoff for transient failures. Jobs that exhaust retries land in the DLQ for manual inspection/replay instead of piling up in FAILED state.

**Auto-retry is an internal-only mechanism** that transitions the same job in-place (FAILED→PENDING). The existing manual `POST /api/jobs/{job_id}/retry` API remains unchanged — it creates a new job with a new `job_id`. See ADR-007 for full rationale.

## Coupling

- **Depends on**: Phase 1 (State Machine, `retry_count`, `max_retries`, `failed_at`, `next_retry_at` fields), Phase 2 (TIMED_OUT state)
- **Coupling type**: moderate
- **Shared files with other phases**: `models.py` (adds DEAD_LETTER status, uses fields from Phase 1), `job_state_machine.py` (adds DEAD_LETTER transitions), `job_queue_service.py` (adds retry/DLQ methods)
- **Why this coupling**: DLQ adds a new state and a new table. Retry uses `retry_count`/`max_retries`/`next_retry_at` fields from Phase 1. TIMED_OUT exit paths (TIMED_OUT→PENDING, TIMED_OUT→DEAD_LETTER) require that Phase 2 has already deployed the TIMED_OUT state — without Phase 3, TIMED_OUT is terminal.

## Context

Phase 1 added `retry_count`, `max_retries`, `failed_at`, `next_retry_at` fields to JobItem and `default_max_retries` to JobQueue. Phase 2 added the TIMED_OUT state and timeout infrastructure. This phase activates those fields with automatic retry logic and adds a DLQ table for jobs that can't be retried.

## Tasks

### Task 1: Dead-Letter Queue Model & Repository

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 1.1 | Create `DeadLetterItem` model | New SQLModel: `dlq_id` (PK), `job_id` (unique, not FK — original job row stays in `job_queue_items`), `original_job_data` (JSON blob), `error_message`, `retry_count`, `failed_at`, `moved_to_dlq_at`, `reason` (enum: MAX_RETRIES, MAX_TIMEOUTS, MANUAL). | `daemon/repositories/job_queue/models.py` |
| 1.2 | Create `DeadLetterRepository` | CRUD for DLQ items: `enqueue()`, `get()`, `list()`, `delete()`, `cleanup_by_age()`. | `daemon/repositories/job_queue/dead_letter_repository.py` (NEW) |
| 1.3 | Add DLQ state to state machine | Add transitions: `(FAILED, DEAD_LETTER)`, `(TIMED_OUT, DEAD_LETTER)`, `(DEAD_LETTER, PENDING)` (replay). | `daemon/services/job_state_machine.py` |
| 1.4 | Implement `move_to_dlq()` — **single transaction** | Called when retries exhausted. In a **single SQLite session**: (1) `INSERT INTO dead_letter_items` (copy of job data), (2) `UPDATE job_queue_items SET status='DEAD_LETTER' WHERE job_id=? AND status='FAILED'` with rowcount check, (3) `session.commit()`. If rowcount=0 (concurrent modification), rollback and skip. | `daemon/services/dead_letter_service.py` (NEW) |
| 1.5 | Add migration | New `dead_letter_items` table in `migrations/versions/`. | `daemon/migrations/versions/` |

**Dead-letter item schema:**

```python
class DeadLetterItem(SQLModel, table=True):
    __tablename__ = "dead_letter_items"
    dlq_id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    job_id: str = Field(index=True, unique=True)  # Original job ID (not FK)
    agent_id: str
    agent_dir: str
    message: str
    source: str
    project_id: str = Field(index=True)
    queue_id: str = Field(index=True)
    priority: int
    error_message: str
    retry_count: int = Field(default=0)
    failed_at: str
    moved_to_dlq_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    reason: str  # MAX_RETRIES, MAX_TIMEOUTS, MANUAL
    metadata: Optional[dict] = Field(default=None, sa_column=Column(Text))
```

> **Convention note:** Using `str` type with ISO-format datetimes to match existing `JobItem` convention. Avoids deprecated `datetime.utcnow()`.

> **Note on job_id:** Not a foreign key — the original job row in `job_queue_items` is updated to `status=DEAD_LETTER` (it stays in the main table). The DLQ row is a **copy** of the relevant fields plus DLQ-specific metadata. This avoids cross-table FK constraints while keeping the main table as the source of truth for current status.

> **Issue 4 fix:** `move_to_dlq()` wraps both operations (INSERT into `dead_letter_items` + UPDATE `job_queue_items` status) in a **single SQLite session transaction**. Since both tables are in the same SQLite database, this guarantees atomicity — either both happen or neither. A crash mid-operation triggers SQLite's automatic rollback.

### Task 2: Automatic Retry Engine

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 2.1 | Create `JobRetryEngine` service | Core retry logic: calculate backoff, increment retry_count, transition to PENDING. | `daemon/services/job_retry_engine.py` (NEW) |
| 2.2 | Implement exponential backoff | Formula: `delay = min(base_seconds * 2^retry_count + jitter, max_seconds)`. Default: base=60s, max=3600s (1 hour). Set `next_retry_at = failed_at + delay`. | `daemon/services/job_retry_engine.py` (NEW) |
| 2.3 | Add `should_retry()` logic | Returns True if: `retry_count < max_retries`. Uses `max_retries` from job → queue default → `JobSystemConfig.default_max_retries`. If all are None, no auto-retry (hard cap at 100 to prevent runaway). | `daemon/services/job_retry_engine.py` (NEW) |
| 2.4 | Retry via **single atomic transaction** | When job fails, `maybe_retry()` executes in a **single SQLite session**: (1) `atomic_transition(FAILED → PENDING, retry_count+=1, next_retry_at=calculated)` if retries remain; or (2) `move_to_dlq()` in same transaction if exhausted. All-or-nothing — if any step fails, rollback to FAILED. | `daemon/services/job_retry_engine.py` (NEW) |
| 2.5 | `find_retryable_jobs()` method | Repository query for: `status = 'FAILED' AND next_retry_at IS NOT NULL AND next_retry_at <= datetime('now')`. | `daemon/repositories/job_queue/repository.py` |
| 2.6 | Integrate into completion path | When instance fails, `JobQueueService.complete_job(success=False)` atomically transitions to FAILED, then calls `JobRetryEngine.maybe_retry()` which either transitions to PENDING or moves to DLQ — both within a single continuation of the same transaction scope. | `daemon/services/job_queue_service.py` |

> **Issue 3 fix:** The original plan had auto-retry as multi-step (mark FAILED, then separately update to PENDING). Under a crash between steps, the job could be FAILED with retry_count not incremented, or retry_count incremented but still FAILED. Now the entire retry decision and transition happens in a **single atomic operation**: `atomic_transition(FAILED → PENDING, retry_count=new_val, next_retry_at=calculated)`. If the process crashes before commit, SQLite rolls back and the job stays FAILED — safe for the next restart/retry cycle.

**Retry flow diagram (single-transaction):**

```mermaid
sequenceDiagram
    participant IM as InstanceManager
    participant JQS as JobQueueService
    participant RE as JobRetryEngine
    participant Repo as JobRepository
    participant DLQ as DeadLetterService
    
    IM->>JQS: complete_job(job_id, success=False, error=...)
    
    rect rgb(200, 230, 200)
        Note over JQS,Repo: Single SQLite session/transaction
        JQS->>Repo: atomic_transition(PROCESSING → FAILED, error, failed_at)
        Note over Repo: UPDATE WHERE status='PROCESSING'<br/>rowcount check
        
        JQS->>RE: maybe_retry(job_id)
        RE->>Repo: get(job_id) — read current state
        RE->>RE: should_retry()?
        
        alt Has retries remaining (retry_count < max_retries)
            RE->>Repo: atomic_transition(FAILED → PENDING,<br/>retry_count+=1, next_retry_at=calculated)
            Note over Repo: UPDATE WHERE status='FAILED'<br/>rowcount check
            Note over Repo: Same job_id, back in queue
        else Retries exhausted
            RE->>DLQ: move_to_dlq(job_id, reason)
            Note over DLQ,Repo: See move_to_dlq flow below
        end
        
        Note over JQS,Repo: session.commit() — all or nothing
    end
    
    JQS-->>IM: job updated
```

**`move_to_dlq()` flow (single-transaction, within same session):**

```mermaid
sequenceDiagram
    participant DLQ as DeadLetterService
    participant Session as SQLite Session
    participant JI as job_queue_items
    participant DLI as dead_letter_items
    
    DLQ->>Session: begin (if not already in transaction)
    DLQ->>JI: INSERT INTO dead_letter_items (copy of job data)
    DLQ->>DLI: UPDATE job_queue_items SET status='DEAD_LETTER' WHERE job_id=? AND status='FAILED'
    Note over DLI: atomic — WHERE status check + rowcount verification
    DLQ->>Session: commit
    Note over Session: Both tables updated atomically
    
    alt rowcount = 0 (job no longer FAILED)
        DLQ->>Session: rollback
        Note over DLQ: Concurrent modification — skip
    end
```

### Task 3: Retry Scheduler

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 3.1 | Create `RetryScheduler` | Background async loop that periodically checks `find_retryable_jobs()`. Default interval: 60s. | `daemon/services/retry_scheduler.py` (NEW) |
| 3.2 | Trigger job processor | On finding retryable jobs, call `trigger_next_job(project_id)` to wake up the processor. | `daemon/services/retry_scheduler.py` (NEW) |
| 3.3 | Wire into lifecycle | Start RetryScheduler alongside JobProcessor in api.py. | `daemon/api.py` |

### Task 4: DLQ API Endpoints

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 4.1 | List DLQ items | `GET /projects/{id}/dlq` with filters: project_id, queue_id, reason, date range, limit. | `daemon/routers/dlq.py` (NEW) |
| 4.2 | Get DLQ item detail | `GET /projects/{id}/dlq/{dlq_id}` with full job data. | `daemon/routers/dlq.py` (NEW) |
| 4.3 | Replay DLQ item | `POST /projects/{id}/dlq/{dlq_id}/replay` — atomically: (1) update job status DEAD_LETTER→PENDING, reset `retry_count=0`, clear `next_retry_at`, (2) delete DLQ item, (3) trigger next job. | `daemon/routers/dlq.py` (NEW) |
| 4.4 | Delete DLQ item | `DELETE /projects/{id}/dlq/{dlq_id}` — permanent removal of DLQ record only (job stays DEAD_LETTER). | `daemon/routers/dlq.py` (NEW) |
| 4.5 | DLQ cleanup | `DELETE /projects/{id}/dlq` — bulk delete by age or reason. | `daemon/routers/dlq.py` (NEW) |

> **W5 fix:** DLQ replay atomicity is now specified. `DeadLetterRepository.replay()` wraps both operations in a single SQLite transaction:
> ```python
> def replay(self, session: Session, dlq_id: str) -> JobItem:
>     try:
>         dlq_item = session.get(DeadLetterItem, dlq_id)
>         job = session.get(JobItem, dlq_item.job_id)
>         job.status = JobStatus.PENDING
>         job.retry_count = 0
>         job.next_retry_at = None
>         session.delete(dlq_item)
>         session.commit()
>         return job
>     except Exception:
>         session.rollback()
>         raise
> ```

## Key Files

| File | Role |
|------|------|
| `daemon/repositories/job_queue/models.py` | `DeadLetterItem` model, DEAD_LETTER status |
| `daemon/repositories/job_queue/dead_letter_repository.py` (NEW) | DLQ persistence layer with atomic replay |
| `daemon/services/job_state_machine.py` | DEAD_LETTER transitions |
| `daemon/services/job_retry_engine.py` (NEW) | Backoff calculation, retry decision |
| `daemon/services/dead_letter_service.py` (NEW) | Move-to-DLQ logic |
| `daemon/services/retry_scheduler.py` (NEW) | Background retry scheduling |
| `daemon/services/job_queue_service.py` | Integrates retry on job completion |
| `daemon/routers/dlq.py` (NEW) | DLQ API endpoints |
| `daemon/api.py` | Wire RetryScheduler |

## Constraints

- **Backoff must be configurable.** Base, max, and multiplier should be tunable via `JobSystemConfig`.
- **DLQ replay must reset retry_count.** Replayed jobs start fresh with retry_count=0.
- **DLQ bulk cleanup must be safe.** Only delete by explicit criteria (age, reason), never all items.
- **No infinite retry loops.** Hard cap at 100 retries even if `max_retries = None`.
- **Manual retry API unchanged.** `POST /api/jobs/{job_id}/retry` still creates a new job.
- **Auto-retry is internal-only.** Same job_id, in-place transition, invisible to API consumers.
- **All multi-step operations are single-transaction.** Auto-retry (FAILED→PENDING + retry_count increment + next_retry_at) is one atomic UPDATE. `move_to_dlq()` (INSERT DLQ + UPDATE status) is one session commit. No partial states on crash.
- **All state transitions use `atomic_transition()`.** The WHERE status=? + rowcount check pattern applies to every transition in this phase.

## Deliverables

- [ ] `DeadLetterItem` model and `DeadLetterRepository` (with atomic replay)
- [ ] DEAD_LETTER state in state machine (including TIMED_OUT→DEAD_LETTER exit)
- [ ] `JobRetryEngine` with exponential backoff (in-place transitions)
- [ ] `RetryScheduler` background service
- [ ] DLQ API endpoints (list, get, replay, delete, cleanup)
- [ ] `move_to_dlq()` called when retries exhausted
- [ ] TIMED_OUT jobs now have exit paths (auto-retry or DLQ)
- [ ] Configurable backoff via `JobSystemConfig`
- [ ] Tests for retry logic and DLQ operations
