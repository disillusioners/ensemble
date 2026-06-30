# Phase 2: Worst Drift Bugs — Lock Scoping + Dedup + Status Map

**Closes:** F1, F3, F4, F7  
**Category:** C (partial — the worst drift bugs)  
**PR:** PR 2 — each focused fix with clear before/after

## Objective
Fix the three worst drift bugs: (1) `_finalize_terminal` releases locks across ALL queues by `instance_id` instead of scoping to the job's own `(project_id, queue_id)`, causing sibling lock deletion and over-admission; (2) `list_work` deduplicates Task turns by `instance_id` instead of `work_id`/`message_id`, causing standalone message turns to silently vanish; and (3) the lossy `_JOB_CANONICAL_TO_ADMISSION` status map collapses `completed/failed/cancelled → done`, breaking `/api/work` status filtering.

## Coupling
- **Depends on**: Phase 1
- **Coupling type**: **HARD dependency** — F1's dedup by `(instance_id, message_id)` is non-functional without Phase 1's `message_id` stamping (Tasks 1–2). Without stamped `message_id` on JobItems, the dedup cannot match Task turns to their driving JobItem, and the old behavior (dedup by `instance_id` only) would be required as a fallback. F3 also benefits from Phase 1's `terminal_reason` awareness but is independently functional.
- **Shared files with other phases**: `daemon/services/job_queue_service.py` (`_finalize_terminal` — Phase 1 touches `_select_next_eligible_job` in the same file but different method)
- **Shared APIs/interfaces**: None new. Phase 3's periodic reconciler will rely on the correct lock-scoping established here.
- **Why this coupling**: Phase 2's F1 dedup fix requires `metadata.message_id` to be stamped on JobItems (Phase 1 Task 1–2). The test suite from Phase 1 Category D validates lock release scoping. Phase 3's reconciler must understand the scoping semantics established here.

## Context
- Previous phase completed: Phase 1 delivered the join-key stamping, shared idle predicate, `is_deferred` wiring, and SQLite invariant tests
- Key decisions: Lock release must be scoped per-queue, not per-instance. Dedup must use `message_id`, not `instance_id`. Status filtering must consult `terminal_reason` with NULL fallback.

---

## Tasks

### Task Group C1: Lock-release scoping (F4, F7)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Replace `release_by_instance` with scoped release in `_finalize_terminal` | The `finally` block at `job_queue_service.py:1426-1435` calls `release_by_instance(canonical_instance_id)`. Change to `release_by_job(project_id, queue_id, job_id)`. The `_finalize_terminal` method already has access to `job_id` (parameter), and `project_id`/`queue_id` can be fetched from the JobItem before the `finally` block. | `daemon/services/job_queue_service.py:1420-1435` |
| 2 | Handle the `_dispatch_skipped=True` path | When `dispatch_skipped` is True (job was queued, never held a lock), do NOT release any lock. The current code releases unconditionally. Add a guard: `if not dispatch_skipped and canonical_instance_id:`. | `daemon/services/job_queue_service.py:1420-1435` |
| 3 | Fetch `(project_id, queue_id)` from JobItem before `finally` | In the `_finalize_terminal` body, before entering the `finally` block, read the JobItem's `project_id` and `queue_id` so they're available for scoped lock release. Handle the case where the JobItem was already deleted (fallback to `release_by_instance` as safety net with WARNING log). | `daemon/services/job_queue_service.py:1153-1443` |
| 4 | Update the F4/F7 invariant test | The Phase 1 test (Task 18) seeds JobA (active, lock) + JobB (queued). After the lock-scoping fix, `cancel_job(JobB)` must NOT delete JobA's lock. Verify the test passes. | `tests/job_queue/test_seam_invariants.py` |

### Task Group C2: `list_work` dedup by `message_id` (F1)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 5 | Change dedup key from `instance_id` to `message_id` | Current dedup (`work_resolver.py:945-981`) drops ALL Task turns for an instance that has a JobItem. Change: a JobItem should only suppress the Task turn whose `message_id` matches `job_queue_items.metadata.message_id`. After Phase 1, the `message_id` is stamped — so the dedup can use it. **Note:** This task has a HARD dependency on Phase 1 Tasks 1–2 (`message_id` stamping). Without stamped `message_id`, this dedup is non-functional. | `daemon/services/work_resolver.py:945-981` |
| 6 | Build dedup index by `(instance_id, message_id)` | Instead of `job_status_by_instance_id = {r.instance_id: r.status for r in records if r.kind == "job"}`, build a set of `(instance_id, message_id)` tuples for JobItem records. A Task turn is suppressed only if its `(instance_id, message_id)` matches an existing JobItem entry. Task turns with `message_id=None` are never suppressed (they can't match). | `daemon/services/work_resolver.py:945-981` |
| 7 | Add `message_id` to WorkRecord for jobs | The WorkRecord for a JobItem must carry `message_id` (from `metadata.message_id`) so the dedup can match. Check if WorkRecord already has this field; if not, populate it in `_job_to_record`. | `daemon/services/work_resolver.py` (WorkRecord model, `_job_to_record`) |
| 8 | Test: `list_work` shows standalone task turns | Seed: job_create spawns instance I (JobItem J + Task T1) → POST /messages on I creates Task T2 (no JobItem). Verify `list_work(instance_id=I)` returns BOTH T1 and T2 (T1 may be suppressed by J, but T2 must appear). | `tests/unit/services/test_work_resolver.py` |

### Task Group C3: Fix lossy status map (F3)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9 | Consult `terminal_reason` on the filter path | `_JOB_CANONICAL_TO_ADMISSION` maps `completed/failed/cancelled → {done}`. The filter `list_work(status="failed")` translates to `admission_state IN ('done')` — which returns ALL terminal jobs. Fix: after fetching by `admission_state='done'`, post-filter by `terminal_reason`. | `daemon/services/work_resolver.py:783-837`, `859-989` |
| 10 | Add `terminal_reason` to the SQL filter with NULL fallback | Extend the JobItem SELECT to filter: `WHERE admission_state = 'done' AND (terminal_reason = :canonical_status OR terminal_reason IS NULL)` when the canonical status maps to `done`. The `OR terminal_reason IS NULL` clause handles databases upgraded before Phase 7c that have NULL `terminal_reason` on existing `done`-state rows. For these NULL rows, fall back to the existing `_ADMISSION_TO_LEGACY_STATUS` map (`done → completed`) to determine canonical status. **Example:** filtering `status="failed"` returns rows where `terminal_reason = 'failed'` (exact match) — NULL `terminal_reason` rows are NOT returned for `failed` (they default to `completed` semantics, not `failed`). | `daemon/services/work_resolver.py:859-989` (JobItem SELECT) |
| 11 | Handle the `paused` → `active` ambiguity | `paused` maps to `admission_state='active'` (same as `processing`). A filter on `"paused"` must also filter `terminal_reason IS NULL` or `terminal_reason != 'paused'` — check how pause state is represented. If paused jobs have a distinct marker, use it. If not, the `paused` filter may need to match `admission_state='active'` and then check for pause-specific metadata. | `daemon/services/work_resolver.py:339-347` |
| 12 | Backfill migration for NULL `terminal_reason` on done-state rows | Add a one-time migration (via `_ensure_postgres_columns` pattern for PostgreSQL, inline for SQLite) that sets `terminal_reason` on existing `done`-state rows based on their `error_message`: if `error_message IS NOT NULL AND error_message != ''`, set `terminal_reason = 'failed'`; otherwise set `terminal_reason = 'completed'`. This eliminates the NULL-fallback ambiguity for future queries. The `OR terminal_reason IS NULL` clause in Task 10 remains as defense-in-depth for rows missed by the migration. | `daemon/manager.py` (migration section), or `daemon/services/work_resolver.py` (on-demand backfill) |
| 13 | Test: `/api/work?status=failed` returns only failed jobs | Seed: completed job + failed job + cancelled job. Filter by `status="failed"` → only the failed job. Filter by `status="completed"` → only the completed job. Filter by `status="cancelled"` → only the cancelled job. Also test: a `done`-state job with NULL `terminal_reason` should appear under `completed` (not `failed`/`cancelled`). | `tests/unit/services/test_work_resolver.py` |

---

## Key Files
- `daemon/services/job_queue_service.py` — `_finalize_terminal` lock release (F4/F7)
- `daemon/repositories/job_queue/lock_repository.py` — `release_by_job` (already exists, lines 84-108), `release_by_instance` (lines 110-128)
- `daemon/services/work_resolver.py` — dedup logic (F1), status map (F3), WorkRecord model
- `daemon/manager.py` — backfill migration for NULL `terminal_reason` (F3)
- `tests/job_queue/test_seam_invariants.py` — F4/F7 invariant test
- `tests/unit/services/test_work_resolver.py` — F1 and F3 tests

## Constraints
- `release_by_job` already exists at `lock_repository.py:84-108` — use it, don't create a new method
- The `_finalize_terminal` method is called from many paths (Phase 4 audit found every call site) — the lock-release change must be safe for all callers
- The dedup change must not break the "report tasks are never deduped" contract (comment at `work_resolver.py:932-936`)
- The status-filter change must maintain backward compatibility with callers that pass raw `admission_state` strings
- F1 dedup change requires Phase 1's `message_id` stamping to be complete — do not attempt F1 without Phase 1 landed

## Deliverables
- [ ] `_finalize_terminal` releases locks scoped to `(project_id, queue_id, job_id)`, not `instance_id`-wide
- [ ] `_dispatch_skipped` path does not release any lock
- [ ] `list_work` dedup suppresses only the driving task (by `message_id`), not all turns on the instance
- [ ] `/api/work?status=failed` returns only failed jobs (not completed/cancelled)
- [ ] `/api/work?status=completed` returns only completed jobs (not failed/cancelled)
- [ ] NULL `terminal_reason` rows fall back to `_ADMISSION_TO_LEGACY_STATUS` (`done → completed`)
- [ ] Backfill migration sets `terminal_reason` on existing `done`-state rows
- [ ] All existing tests pass

## Implementation Notes

### Lock-release scoping — job_id availability
The `_finalize_terminal` signature already accepts `job_id: str | None`. When `job_id` is None (virtual job finalize), fall back to `release_by_instance` — virtual jobs have no per-queue lock. When `job_id` is provided, use `release_by_job`.

### `release_by_job` parameters
`release_by_job(project_id, queue_id, job_id)` — the `project_id` and `queue_id` must be available. If the JobItem is fetched inside `_finalize_terminal`, extract these before the `finally`. If the JobItem is deleted/missing, fall back to `release_by_instance` with a WARNING log.

### Status map — `terminal_reason` values + NULL fallback
The `terminal_reason` column was added in Phase 7c as a discriminator for `done`-state jobs. Values are: `completed`, `failed`, `cancelled` (matching the canonical status vocabulary).

Databases upgraded before Phase 7c have NULL `terminal_reason` on existing `done`-state rows. The filter logic handles this:
- `WHERE admission_state = 'done' AND (terminal_reason = :canonical_status OR (terminal_reason IS NULL AND :canonical_status = 'completed'))`
- This means NULL rows are treated as `completed` (per `_ADMISSION_TO_LEGACY_STATUS: done → completed`), and are NOT returned for `failed`/`cancelled` filters.
- The backfill migration (Task 12) eliminates these NULLs over time.

### WorkRecord `message_id` field
Check the WorkRecord dataclass/model. If it doesn't have `message_id`, add it. For JobItem records, populate from `metadata.message_id`. For Task records, populate from `task.message_id`. The dedup uses this field to match.
