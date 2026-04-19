# Plan Overview: Job Soft Delete

## Objective
Add soft delete capability to the job system: jobs are marked with a `deleted_at` timestamp instead of being hard-deleted. The FE gets a delete button and a "Show Deleted" filter toggle. The scheduler and all job execution paths must never pick up soft-deleted jobs.

## Scope Assessment
**MEDIUM** — Changes span BE (migration + model + repository + API + service) and FE (service + model + components), but the changes are well-contained. The most critical aspect is ensuring no execution path picks up deleted jobs.

## Context
- Project: agents-ensemble
- Working Directory: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- Database: SQLite (via SQLModel/SQLAlchemy)
- Frontend: Angular with Material UI
- Migration system: Custom SQL-file based runner (`daemon/migrations/`)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | BE: Database & Model Layer | Add `deleted_at` column to `job_queue_items`, update `JobItem` model | None | — | 1h |
| 2 | BE: Repository Layer | Update `JobRepository` to exclude deleted jobs in execution queries, add soft-delete method, conditionally include in listing | Phase 1 | tight | 1.5h |
| 3 | BE: API & Service Layer | Add soft-delete endpoint, add `include_deleted` param to list endpoint, update schemas | Phase 2 | tight | 1h |
| 4 | FE: Service & Model Layer | Add `deleted_at` to Job model, add `softDelete()` method to `JobService`, update filters | Phase 3 | loose | 0.5h |
| 5 | FE: UI Components | Add delete button to job-card, "Show Deleted" checkbox to filter bar, handle deleted state display | Phase 4 | tight | 1h |

### Coupling Assessment

| Coupling | Meaning | Scheduling |
|----------|---------|------------|
| Phase 1 → Phase 2: **tight** | Phase 2 reads the new column from Phase 1's model | Must run sequential |
| Phase 2 → Phase 3: **tight** | Phase 3 calls repository methods from Phase 2 | Must run sequential |
| Phase 3 → Phase 4: **loose** | Phase 4 only needs API contract, not implementation | Can pipeline |
| Phase 4 → Phase 5: **tight** | Phase 5 uses service methods from Phase 4 | Must run sequential |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Scheduler picks up deleted jobs** | **CRITICAL** — deleted jobs would be executed | Audit ALL repository methods called by scheduler/processor/recovery/retry-engine. Add `WHERE deleted_at IS NULL` to every execution-path query. Add integration test. |
| Hard-delete code paths still exist | Medium — existing `delete()` and `delete_completed()` hard-delete rows | Keep hard-delete methods but rename for clarity. Soft-delete is the new default. |
| Missing query exclusion causes deleted job to appear in FE | Low | Default `list()` excludes deleted. Only `include_deleted=True` includes them. |
| Idempotency key matches a deleted job | Low — deleted job could block new job creation | `find_by_idempotency_key` should exclude deleted jobs |
| Frontend shows stale deleted state | Low — user deletes but card still shows | Optimistic UI update on delete success |

## Success Criteria
- [ ] `deleted_at` column added to `job_queue_items` table via migration
- [ ] All scheduler/processor/execution paths exclude deleted jobs (`WHERE deleted_at IS NULL`)
- [ ] `DELETE /api/jobs/{job_id}` sets `deleted_at` instead of removing the row
- [ ] `GET /api/jobs?include_deleted=true` returns both deleted and non-deleted jobs
- [ ] `GET /api/jobs` (default) returns only non-deleted jobs
- [ ] Job card has a delete button (only for terminal-status jobs)
- [ ] Filter bar has "Show Deleted" checkbox
- [ ] Deleted jobs show with visual distinction (strikethrough, grayed out)
- [ ] Integration test: deleted PENDING job is never picked up by scheduler
- [ ] Existing hard-delete paths updated/removed

## Tracking
- Created: 2026-04-19
- Last Updated: 2026-04-19
- Status: draft
