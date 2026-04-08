# Architecture Decisions: Named Per-Project Job Queues

## AD-1: Queue Table Design
**Decision:** Separate `job_queues` table with FK to `projects` + `queue_name_lower` column for case-insensitive uniqueness + CHECK constraint on `queue_type`.
**Rationale:** Clean normalization. Efficient queries. DB-level case-insensitive uniqueness (W1). DB-level type validation (S1).

## AD-2: System Queue Auto-Provisioning — Router Layer Hook
**Decision:** System queues are auto-created via `BackgroundTasks` in the project creation endpoint at the router layer, NOT in the repository (W2 fix).
**Rationale:** Repository stays synchronous and unaware of queues. Router triggers async provisioning after project is persisted.

## AD-3: Parallel Queue Concurrency — Atomic Lock Manager
**Decision:** `concurrency_limit` field on queue. `acquire_queue_lock()` performs capacity check **inside** `asyncio.Lock` — no separate `can_acquire()` method. All lock operations are async; `acquire_sync()` removed (W6).
**Rationale:** Single atomic operation under lock prevents TOCTOU race (C5). Removing `acquire_sync()` eliminates the W6 bypass.

## AD-4: Migration Strategy — TRUNCATE (Simplified)
**Decision:** Since we're not using the job system right now, we DELETE all existing jobs before adding the `queue_id` column. No complex migration logic needed.
**Rationale:** No existing job data to preserve = no migration edge cases = clean schema change. Removed: C1 (idempotent INSERT), C2 (PROCESSING guard), C6 (backward compat for project-less jobs).

## AD-5: Per-Queue vs Master Pause — Two-Level Model
**Decision:** Queue `is_paused` + project `job_queue_paused` as master override. `_process_loop()` checks queue-level first, then master override.
**Rationale:** Backward compatible. Existing pause/resume API works as master override. Per-queue is additive.

## AD-6: Job Submission — project_id Required
**Decision:** Since we're starting fresh with a clean database, `project_id` is required when submitting a job. No backward-compatibility shims needed.
**Rationale:** Clean slate design. No complex C6 legacy handling.

## AD-7: Queue Deletion — Protected, Atomic, PENDING-Only
**Decision:** PROCESSING jobs return `409 Conflict` and block deletion. Only PENDING jobs are reassigned to `system_fifo_queue` via atomic conditional SQL UPDATE (W4).
**Rationale:** PROCESSING jobs must complete naturally. Atomic UPDATE prevents race. 409 response is clear to user.

## AD-8: IDOR Protection — 404 Not 403
**Decision:** All queue endpoints validate `queue.project_id == path_project_id`. On mismatch, return `404 Not Found` (never `403`) to prevent queue existence leakage (W3).
**Rationale:** 404 is indistinguishable from "queue doesn't exist" — no information leakage.

## AD-9: Frontend Layout — Queue Panel in Jobs Page (Angular Material Only)
**Decision:** Queue list as collapsible sidebar panel within existing Jobs page. Angular Material only — no ng-zorro (S5).
**Rationale:** Contextual queue management. Consistent component library.
