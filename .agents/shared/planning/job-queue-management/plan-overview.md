# Plan Overview: Named Per-Project Job Queues

## Objective
Extend the job_queue system to support **named, per-project job queues** with start/stop controls, system-predefined queues (`system_fifo_queue`, `system_parallel_queue`), and user-defined custom queues — enabling fine-grained control over job processing within each project.

> **Note:** Since we're not actively using the job system right now, we will **TRUNCATE all existing job data** before migration. This significantly simplifies the migration and removes backward-compatibility requirements for project-less jobs.

## Scope Assessment
**MEDIUM** — Multi-module change spanning database schema, backend, and frontend. Estimated 1-2 days across 3 phases. (Previously LARGE with 5 phases)

## Context
- **Project:** agents-ensemble
- **Working Directory:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Requested by:** Leader
- **Current state:** Single global queue with per-project serialization (1 job active per project via `JobLockManager`). Project has `job_queue_paused` boolean flag.
- **Migration strategy:** TRUNCATE all existing jobs — we are not using the job system right now, so no existing data needs preservation.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | DB Schema & Migration | `job_queues` table, `queue_id` column, TRUNCATE existing jobs, auto-provision system queues | None | — | 2h |
| 2 | Backend Core Services | Queue repository, atomic lock manager, queue-aware service & processor | Phase 1 | tight | 4h |
| 3 | API + Frontend + Integration | REST endpoints with IDOR protection, Angular UI, end-to-end testing | Phase 2 | tight | 5h |

### Coupling Assessment

| From → To | Coupling | Reasoning |
|-----------|----------|-----------|
| Phase 1 → Phase 2 | **tight** | Phase 2 imports models and uses schema defined in Phase 1 |
| Phase 2 → Phase 3 | **tight** | Phase 3 exposes Phase 2 services via API and frontend |

### Scheduling Recommendation
- All phases must run sequentially (tight coupling)
- Phase 3 is the largest — can be split into API + Frontend if needed during execution

## Architecture Decisions

### AD-1: Queue Table Design
**Decision:** Separate `job_queues` table with FK to `projects` + `queue_name_lower` column for case-insensitive uniqueness.

**Rationale:** Clean normalization. `queue_name_lower` provides DB-level case-insensitive uniqueness (W1 fix).

### AD-2: System Queue Auto-Provisioning
**Decision:** Hooked at the **router/manager layer** via `BackgroundTasks` after project creation — NOT in the repository (W2 fix).

**Rationale:** Repository is synchronous; injecting async service would be a layering violation. Router-level hook is clean.

### AD-3: Parallel Queue Concurrency — Atomic Lock Manager
**Decision:** `acquire_queue_lock()` performs capacity check **inside** `asyncio.Lock` — no separate `can_acquire()` call. Eliminates TOCTOU races (C5 fix).

**Rationale:** Single atomic operation under lock prevents race between capacity check and lock acquisition.

### AD-4: Migration Strategy — TRUNCATE
**Decision:** Since we're not using the job system, we TRUNCATE the `JOB_QUEUE_ITEMS` table before adding the `queue_id` column. This eliminates all migration complexity.

**Rationale:** No existing job data to preserve = no migration edge cases = clean schema change.

### AD-5: Per-Queue Pause State — Two-Level Model
**Decision:** Queue `is_paused` + project `job_queue_paused` as master override. Checked in `_process_loop()`: queue-level first, then master.

**Rationale:** Backward compatible. Existing pause/resume API works as master override. Per-queue pause is additive.

### AD-6: Job Submission — project_id Required
**Decision:** Since we're starting fresh, `project_id` is required when submitting a job. No backward-compatibility shims needed for project-less jobs.

**Rationale:** Simplifies the system. Clean slate = clean design.

### AD-7: Queue Deletion — Protected, Atomic, PENDING-Only
**Decision:** Only PENDING jobs are reassigned. PROCESSING jobs cause `409 Conflict`. Atomic conditional SQL UPDATE prevents race (W4).

**Rationale:** PROCESSING jobs must complete naturally. Conditional UPDATE is atomic.

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| TOCTOU race in lock acquisition | Critical | Medium | Capacity check inside `asyncio.Lock` — atomic (C5 fix) |
| IDOR on queue endpoints | Critical | Medium | Every endpoint validates `queue.project_id == path_id` → 404 (W3 fix) |
| Queue deletion race (PENDING→PROCESSING) | Medium | Medium | Atomic conditional UPDATE only reassigns PENDING (W4 fix) |
| Non-atomic `start_job()` | Medium | Medium | Single-session atomic update in Phase 2 (W5 fix) |
| `acquire_sync()` bypassing `asyncio.Lock` | Medium | Medium | Removed in Phase 2 (W6 fix) |

## Success Criteria
- [ ] `job_queues` table created with `queue_name_lower`, CHECK constraint, proper indexes
- [ ] All existing jobs TRUNCATED (clean migration)
- [ ] Lock manager operates on real queue IDs only (C3 fix)
- [ ] `acquire_queue_lock()` is atomic — no TOCTOU race (C5 fix)
- [ ] `start_job()` uses single-session atomic update (W5 fix)
- [ ] Lock manager is fully async — no `acquire_sync()` bypass (W6 fix)
- [ ] Queue deletion: 409 if PROCESSING jobs, only PENDING jobs reassigned (C4 fix)
- [ ] Queue deletion: atomic conditional UPDATE (W4 fix)
- [ ] Auto-provisioning at router layer, not repository (W2 fix)
- [ ] All queue endpoints validate project ownership → 404 (W3 fix)
- [ ] Case-insensitive queue name uniqueness via `queue_name_lower` (W1 fix)
- [ ] FIFO concurrency_limit=1 enforced at DB and app level (S1, S2 fix)
- [ ] Frontend: mat-select only, no ng-zorro (S5 fix)
- [ ] System queues cannot be deleted (403)
- [ ] Reserved queue names rejected (400)
- [ ] Project creation auto-provisions system queues

## Tracking
- Created: 2026-04-08
- Last Updated: 2026-04-09 (major simplification — TRUNCATE instead of migrate)
- Status: draft — ready for review
