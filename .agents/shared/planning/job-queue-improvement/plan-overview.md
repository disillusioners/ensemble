# Plan Overview: Job Queue Improvement

## Objective
Fix the critical job completion callback mechanism (jobs stay PROCESSING forever), resolve API schema mismatches between backend and frontend, clean up frontend dead code, and add comprehensive testing for the job queue feature.

## Scope Assessment
**LARGE** — Cross-cutting changes spanning:
- Backend: 6 files modified across services, manager, router, and schema layers
- Frontend: 4+ files modified across models, services, and components
- Testing: New test files for both backend (JobProcessor, API routes) and frontend (service/component tests)
- Critical bug fix requiring careful integration with existing instance lifecycle

**Justification**: The completion callback fix touches the core message processing loop in manager.py (~2500 LOC), requires coordination with JobQueueService, and must handle multiple edge cases (success, failure, cancellation, crash recovery). Combined with schema fixes and testing, this is a multi-day effort.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Requested by: Leader
- Key insight: The `_job_queue_service` is already wired into InstanceManager (set via `set_job_queue_service()`), but is never called from the instance completion path

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend: Job Completion Callback | Wire instance completion → job status updates | None | — | 3-4h |
| 2 | Backend: API Schema & Route Fixes | Add missing fields to JobResponse, consolidate inline responses | None | independent | 1-2h |
| 3 | Backend: Testing | Tests for JobProcessor, completion callback, API routes | Phase 1, Phase 2 | tight | 2-3h |
| 4 | Frontend: Schema Alignment & Cleanup | Fix type mismatches, remove dead code | Phase 2 | loose | 1-2h |
| 5 | Frontend: Testing | Service tests, component tests for job features | Phase 4 | loose | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 ↔ 2 | **independent** | Different files (manager.py/job_queue_service.py vs schemas.py/router). No shared code. |
| 1 ↔ 3 | **tight** | Phase 3 tests the completion callback code written in Phase 1. Same functions tested. |
| 2 ↔ 3 | **loose** | Phase 3 tests API routes, needs schema from Phase 2 but only the interface. |
| 2 ↔ 4 | **loose** | Frontend depends on backend API contract (schema). Phase 2 defines it, Phase 4 aligns to it. |
| 4 ↔ 5 | **loose** | Phase 5 tests the frontend code from Phase 4, but can start with existing code if needed. |

### Parallelization Opportunity
- **Phase 1 and Phase 2 can run in parallel** (independent — different backend files)
- **Phase 4 can start as soon as Phase 2 completes** (needs final API contract)
- **Phase 3 must wait for Phase 1** (tests the callback implementation)
- **Phase 5 can overlap with Phase 3** (different codebase — frontend vs backend tests)

### Recommended Execution Order
```
Phase 1 ──┐
           ├──→ Phase 3 (backend tests)
Phase 2 ──┤
           └──→ Phase 4 ──→ Phase 5 (frontend tests)
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Completion callback introduces deadlocks with lock manager | High | Medium | Use non-blocking async calls; add timeout to `complete_job()`; test with concurrent jobs per project |
| Race condition between `_process_queue` completion and `terminate_instance` | High | Medium | Both paths check current job status before updating; `complete_job()` already handles `ValueError` for already-completed jobs (idempotent) |
| Schema changes break existing API consumers | Medium | Low | All new fields are optional with defaults; existing fields unchanged |
| Frontend test infrastructure missing entirely | Medium | High | Scope Phase 5 to service tests first (simpler setup); component tests are stretch goal |
| Instance crash leaves orphaned PROCESSING jobs (existing limitation, not introduced by this change) | Medium | High | Document as known limitation; add orphan detection in Phase 3 as stretch goal |
| `lock_manager.release()` (async) vs `lock_manager.release_sync()` inconsistency in JobQueueService | Low | Low | Both methods exist and work; document pattern in decisions.md; no change needed now |

## Success Criteria
- [ ] Jobs transition to COMPLETED when instance finishes successfully
- [ ] Jobs transition to FAILED when instance errors out (max retries, crash)
- [ ] Jobs transition to FAILED when instance is terminated manually
- [ ] Lock is released on job completion AND failure (already works — verified in `_fail_job()` and `_complete_job()`)
- [ ] Next pending job is triggered after current job completes for the same project
- [ ] Premature `trigger_next_job()` call removed from `job_processor.py` (was no-op when lock held)
- [ ] `get_job_by_instance()` public method added to `JobQueueService` (no private field access)
- [ ] `complete_job()` accepts `result_summary` parameter (no hardcoded string)
- [ ] Frontend `Job.source`, `Job.job_metadata`, `Job.cancelled_at` fields populated from API
- [ ] No duplicate methods in `project.service.ts`
- [ ] Unused `currentObserver` field and `Observer<T>` interface removed from SSE service
- [ ] Backend tests cover completion callback (success, failure, termination paths)
- [ ] Frontend service tests for job.service.ts and job-sse.service.ts

## Tracking
- Created: 2026-04-07
- Last Updated: 2026-04-07
- Status: draft
