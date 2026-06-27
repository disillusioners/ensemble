# Job-as-Queue-Proxy Phase 3 Testing — Findings & Patterns

**Date:** 2026-06-27
**Branch:** `feature/job-as-queue-proxy` (commits `f2acdd4c`, `1fcb99a1`, `b750eb72`)

## Key Findings

### 1. Phase 3 is a Clean Cutover — No Bugs Found
Unlike Phase 2 (which had the server_default bug), Phase 3's query migration was implementationally correct from the start. All 10 migrated queries use correct `admission_state` predicates. No production code fixes were needed.

### 2. Critical `IN ('queued', 'active')` Invariant Preserved (C2 + C3)
Two critical sites that MUST use `IN ('queued', 'active')` (NOT just `'active'`):

**C2 — FIFO Priority (count_active_jobs_by_project, count_active_jobs_in_non_defer_queues):**
- These count toward concurrency limits and defer-idle-gate checks
- If only `'active'` were used, queued jobs wouldn't count → defer-idle-gate would falsely report "no active jobs" → FIFO priority broken

**C3 — Race-Delete Protection (_ACTIVE_JOB_IDS_SUBQUERY in lock_repository.py):**
- This subquery drives `clear_stale_job_locks()` — deletes locks for non-active jobs
- If only `'active'` were used, during PENDING→PROCESSING transition the lock could be deleted (job is briefly in 'queued' state) → race condition

**Pattern to remember:** Any query used for active-job counting or stale-lock sweep MUST include BOTH `'queued'` and `'active'` admission states.

### 3. Query Predicate Mapping (status → admission_state)
| Old status filter | New admission_state filter | Rationale |
|-------------------|--------------------------|-----------|
| status IN (PENDING, PROCESSING) | admission_state IN (queued, active) | Active jobs (incl. queued waiting) |
| status = PROCESSING | admission_state = 'active' | Currently processing only |
| status = PENDING | admission_state = 'queued' | Waiting to dequeue |
| status IN (PENDING,PROCESSING,FAILED,PAUSED) | admission_state IN (queued, active) | Narrowed — FAILED → done excluded |

### 4. find_jobs_by_instance Narrowing
The original `find_jobs_by_instance` searched for (PENDING+PROCESSING+FAILED+PAUSED). The new query narrows to (queued+active), dropping FAILED (now `done`). This is correct because callers use this to find in-flight jobs, and FAILED jobs are terminal — no caller needs them here.

### 5. find_processing_jobs Now Includes PAUSED
Under the old system, `find_processing_jobs` searched for `status=PROCESSING` only. Under admission_state, `status=PROCESSING` → `active` and `status=PAUSED` → `active` (lock still held). So PAUSED jobs now appear in `find_processing_jobs`. This is correct — paused jobs still hold locks and are in-flight.

## Testing Strategy Used
3 parallel sessions:
1. **Existing suite** — broad regression (SQLite + PG)
2. **Query migration** — C2/C3 + semantic equivalence tests (SQLite)
3. **Regression flows** — full lifecycle regression (SQLite)
Plus 1 verification session.

All completed within ~7 minutes total wall time. 0 production bugs found.
