# Plan Overview: `system_default_project` for the Job System

## Objective

Introduce a reserved system project (`__system_default__`) that acts as the implicit home for all jobs submitted without an explicit `project_id`. This eliminates the orphan-job problem (where `JobItem.project_id` is `NULL`, causing `DeadLetterItem.project_id` NOT NULL constraint violations), removes defensive workarounds scattered across the service layer, and makes the job system's default routing behavior explicit and auditable.

---

## Scope Assessment

**LARGE** — spans all job-system layers: constants, repositories, services, routers, tool interface, and API.

### In Scope

- New `__system_default__` project and two system queues (`system_fifo_queue`, `system_parallel_queue`) created idempotently at daemon startup.
- `project_id` normalization at the canonical chokepoint (`enqueue()`) plus defense-in-depth at every input boundary — `None` and `""` are converted to the system project's `project_id` before any persistence or routing logic runs.
- Removal of all `None`-project code paths from the service layer (`job_processor.py` C5 fallback, `job_queue_service.py` queue-skip, `dispatch_event_bus.py` global catch-all, `dead_letter_service.py` None→"" hack).
- SQL migration to backfill existing `project_id=NULL` rows before C5 fallback removal.
- `is_system` flag on `ProjectResponse` and `exclude_system` query param on `GET /projects` to semi-hide the system project from regular listings.

### Out of Scope

- Schema changes to `JobItem.project_id` (remains `str | None` in DB — the field is not NOT NULL-constrained).
- Changes to `JobLock`, `DeadLetterItem`, `JobQueue` model definitions (their `project_id` columns already have NOT NULL constraints).
- Changes to any non-job-system code.

### Verified Safe (No Change Needed)

- `daemon/sources/adapters/scheduler.py:705` — only routes through `enqueue()` when `self._project_id` is set. No change needed.

---

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Related Bug**: `DeadLetterItem.project_id` is NOT NULL but `JobItem.project_id` is nullable → crash when orphan jobs exhaust retries

---

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | System Project & Queue Bootstrap | Create `__system_default__` project and 2 queues at startup | None | — | 2h |
| 2 | Normalization (Chokepoint + Boundaries) | Normalize `None`/`""` → system project ID in `enqueue()` and at all input boundaries | Phase 1 | tight | 3h |
| 3 | Migration & Service Layer Cleanup | Backfill existing NULL rows, then remove all orphan code paths | Phase 2 | tight | 4h |
| 4 | API & Visibility | Semi-hide system project from listings, add `is_system` flag | Phase 1 | independent | 1.5h |

### Coupling Assessment

| Phase Pair | Coupling | Meaning |
|------------|----------|---------|
| 1 → 2 | **tight** | Phase 2 reads `SYSTEM_DEFAULT_PROJECT_ID` that Phase 1 populates at startup. Cannot run before Phase 1. |
| 2 → 3 | **tight** | Phase 3 removes orphan fallbacks that are only safe to remove after Phase 2 guarantees no `None` reaches services. Must be sequential. |
| 1 → 4 | **independent** | Phase 4 only reads `SYSTEM_DEFAULT_PROJECT_NAME` from constants — a plain string that exists from Phase 1's first commit. Can run in parallel with Phase 2/3. |
| 3 → 4 | **independent** | Phase 4 is purely API presentation. No shared files or imports with Phase 3. |

### Scheduling

```
Phase 1 ──→ Phase 2 ──→ Phase 3
   │
   └──────→ Phase 4  (can start after Phase 1, run parallel with 2/3)
```

---

## Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Internal callers bypass boundary normalization** | High | Medium | **B1 FIX**: Primary normalization lives inside `enqueue()` itself — the single chokepoint ALL callers pass through. Boundary normalization is defense-in-depth, not the sole line of defense. |
| 2 | **Data loss on existing orphan jobs when C5 fallback removed** | High | High | **B2 FIX**: Explicit SQL migration task (Phase 3 task 3.0) backfills `project_id=NULL` rows before C5 fallback removal. Migration runs with verification. |
| 3 | **Dead letter items with empty string** | Medium | Medium | Phase 3 adds assertion in `DeadLetterService.move_to_dlq()` that raises if `project_id` is `None`, surfacing normalization gaps as loud errors rather than silent corruption. |
| 4 | **Startup ordering** | Low | Low | Place `ensure_system_default_project()` in `api.py` lifespan after repos, before services. Test with daemon restart. |
| 5 | **Circular imports from constants** | Low | Low | `constants.py` only gets a plain string. Runtime resolution stays in the repository layer. No imports from `repositories/` or `services/` into `constants.py`. |
| 6 | **`retry_scheduler.py:181` silently drops None jobs** | Low | Medium | Known current bug documented in Phase 3 context. Post-migration verification confirms no `NULL` rows remain, making this unreachable. |

---

## Success Criteria

1. [ ] A project named `__system_default__` exists in the database immediately after the first daemon startup.
2. [ ] Two queues (`system_fifo_queue`, `system_parallel_queue`) exist under the system project after startup.
3. [ ] Submitting a job via `POST /jobs` with `{"project_id": null}` or `{"project_id": ""}` results in a DB row with `project_id` set to the system project's ID (not `NULL`).
4. [ ] Submitting a job via the `job_create` tool with `project_id=None` behaves identically.
5. [ ] Retrying an orphan job (with `project_id=NULL` in DB) produces a new job with the system project's ID — verified via `enqueue()` normalization.
6. [ ] `SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL` returns **0** after migration.
7. [ ] `DeadLetterService` contains no `or ""` conversion for `project_id`.
8. [ ] `job_processor.py` contains no `project_id is None` check or orphan-handling block (no C5 fallback).
9. [ ] `dispatch_event_bus.py` no longer sets a global event when `project_id=None`.
10. [ ] `GET /projects` returns all projects including `__system_default__` by default.
11. [ ] `GET /projects?exclude_system=true` excludes `__system_default__`.
12. [ ] `ProjectResponse` includes `is_system: bool` field.
13. [ ] All existing tests pass; new unit tests cover normalization at each boundary.

---

## Review History

| Date | Reviewer | Result | Key Changes |
|------|----------|--------|-------------|
| 2026-04-24 | Approver | **2 blocking issues** | B1: Move normalization into `enqueue()` as chokepoint. B2: Add migration task before C5 removal. |
| 2026-04-24 | Reviewer | **6 improvements** | R1: instance_lifecycle bypass. R2: assert in enqueue(). R3: retry_scheduler bug. R4: type signature fix. R5: migration success criteria. R6: scheduler adapter verified safe. |
| 2026-04-24 | Planner | **Revised** | All blocking issues and improvements incorporated. |

---

## Tracking

- **Created**: 2026-04-24
- **Last Updated**: 2026-04-24 (rev 2 — feedback incorporated)
- **Status**: draft
