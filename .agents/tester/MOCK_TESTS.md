# Job Queue Tests — Mock Tests Inventory

## Phase 1: Schema & Migration (COMPLETE)
- Models: QueueType, JobQueue, JobItem queue_id
- Migration: table creation, seeding, constraints, idempotency
- Schemas: CreateRequest, UpdateRequest, Response validation

## Phase 2: Backend Core Services (COMPLETE)
- JobQueueRepository: CRUD, atomic operations, job counting, reassignment
- JobQueueMgmtService: auto-provision, CRUD with IDOR, queue deletion rules
- JobLockManager: per-queue atomic locking, concurrency limits
- JobProcessor: per-queue polling, two-level pause (queue + project level)
- JobQueueService: queue-aware enqueue with system queue fallback
- JobRepository: list_pending_by_queue, start_job_atomic, delete_by_project

---

## Updating MOCK_TESTS.md

Update when mock tests are added/modified.
