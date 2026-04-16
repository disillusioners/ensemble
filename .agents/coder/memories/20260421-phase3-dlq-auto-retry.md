# Phase 3 Implementation Experience — Dead-Letter Queue & Auto-Retry

## Date: 2026-04-21

## Key Learnings

### 1. DeadLetterService Atomicity
- `move_to_dlq_standalone()` and `replay_from_dlq()` MUST use a single SQLModelSession context for both the job transition and the DLQ item operation
- The pattern: create session → do both operations → single commit
- Without this, partial failures leave orphaned records (job in DEAD_LETTER with no DLQ entry, or DLQ entry for a PENDING job)

### 2. DLQ Cleanup Project Filtering
- When implementing bulk cleanup endpoints, ALWAYS filter by project_id even when other filters (like `reason`) are absent
- The repo's `cleanup_by_age()` method may not have project filtering — the router must handle this

### 3. Retry Engine Integration
- The retry engine integrates into `job_queue_service.py`'s `complete_job()` method — after transitioning to FAILED, call `maybe_retry()`
- `maybe_retry()` decides atomically: retry (FAILED→PENDING with backoff) or DLQ (FAILED→DEAD_LETTER)
- The fallback chain for max_retries: job.max_retries → queue.default_max_retries → config.default_max_retries → default(3), hard cap at 100

### 4. Retry Scheduler Pattern
- Background async service using `asyncio.create_task()` for the loop
- Uses `asyncio.to_thread()` for sync DB operations
- Started/stopped in api.py lifespan alongside other services
- Shutdown order matters: stop RetryScheduler FIRST before observer/processor

### 5. State Machine Transitions
- Phase 1 already had FAILED→DEAD_LETTER and DEAD_LETTER→PENDING transitions
- FAILED→PENDING (for auto-retry) also existed as "retry" action

## Architecture
- DeadLetterItem is a SEPARATE table (ADR-003) — keeps main job table clean
- Auto-retry transitions same job in-place (ADR-007) — manual retry creates new job
- All state transitions use atomic_transition() pattern (ADR-008)

## Files Created
- `daemon/repositories/job_queue/dead_letter_repository.py`
- `daemon/services/dead_letter_service.py`
- `daemon/services/job_retry_engine.py`
- `daemon/services/retry_scheduler.py`
- `daemon/routers/dlq.py`
- `daemon/migrations/versions/20260421_000001_add_dead_letter_queue.sql`
- Tests: test_retry_engine.py, test_retry_scheduler.py, test_dead_letter_service.py

## Commit
- `f1db6dc` — feat(job-system): Phase 3 - dead-letter queue, auto-retry, retry scheduler, DLQ API
