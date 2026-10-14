# Phase 2: Backend Core Services for Named Per-Project Job Queues

## What Was Implemented
- **JobQueueRepository** (`daemon/repositories/job_queue/queue_repository.py`) — CRUD for job_queues table
- **JobQueueMgmtService** (`daemon/services/job_queue_mgmt_service.py`) — Queue management service
- **JobLockManager rework** — Per-queue locking with atomic `acquire_queue_lock()`
- **JobQueueService extension** — Queue-aware enqueue, start, complete
- **JobProcessor rework** — Per-queue polling with two-level pause check
- **Auto-provisioning hook** — System queues auto-created on startup in api.py lifespan

## Key Design Decisions
- Per-queue locking: `(project_id, queue_id) → list[LockInfo]`
- Atomic lock acquisition: capacity check + acquire under asyncio.Lock (C5 fix)
- Atomic job start: `start_job_atomic()` single-session PENDING→PROCESSING (W5 fix)
- Atomic job reassignment: `reassign_pending_jobs_atomic()` uses SQLAlchemy Core UPDATE (W4 fix)
- Auto-provisioning at api.py lifespan, not repository (W2 fix)
- Two-level pause: queue `is_paused` → project `job_queue_paused` master override

## Bug Fixes Addressed
- C3: No phantom queue names, only real queue IDs
- C5: Atomic lock acquisition under asyncio.Lock
- W2: Auto-provisioning at router/manager layer
- W4: Atomic SQL UPDATE for job reassignment
- W5: Single-session atomic PENDING→PROCESSING
- W6: All lock operations async, no acquire_sync

## Commit
- cce7976 (14 files, +1616/-428)

## Notes
- Phase 3 will add API endpoints and frontend
- Tests pass: 233 passed, 14 skipped
- Backward compat maintained for complete_job_sync etc.
