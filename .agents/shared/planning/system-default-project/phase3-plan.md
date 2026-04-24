# Phase 3: Migration & Service Layer Cleanup

## Objective

**[B2 FIX — CRITICAL]** First, run a SQL migration to backfill all existing `project_id=NULL` rows to the system default project — ensuring no data is lost when orphan-handling code is removed. Then, remove all defensive code paths that exist solely to handle `project_id=None` jobs, since Phase 2 guarantees normalization prevents `None` from reaching the service layer. The migration + cleanup together make the code simpler, faster, and more correct.

---

## Coupling

- **Depends on**: Phase 2 (Normalization)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/services/job_processor.py`, `daemon/services/dispatch_event_bus.py`, `daemon/services/dead_letter_service.py`, `daemon/services/job_queue_service.py`
- **Shared APIs/interfaces**: None (removal only, no new APIs)
- **Why this coupling**: Phase 3's migration and removal of `None`-project fallbacks is only safe after Phase 2's normalization is live at every input boundary AND at the `enqueue()` chokepoint.

## Context

### Previous Phase Completed

Phase 2 delivered:
- `normalize_project_id()` utility in `daemon/services/project_normalizer.py`
- **Canonical normalization in `enqueue()`** — the single chokepoint covering all callers (HTTP, tools, retry, internal)
- Pydantic validator in `JobCreateRequest` converting `None`/`""` → system project ID (defense-in-depth)
- Normalization in `jobs_crud.py`, `job_queue.py`, `instance_lifecycle.py`, `tools/instance.py` (defense-in-depth)
- Test proving `retry_job()` normalizes orphan jobs via `enqueue()` chokepoint

### Current State — Orphan-Handling Code Paths

| Location | Lines | Behavior |
|----------|-------|----------|
| `job_processor.py` | 304–332 | **C5 fallback**: iterates `list_all_pending`, filters `project_id is None`, processes one at a time without queue assignment |
| `dispatch_event_bus.py` | 62–69 | Always sets `_global_event` (for `project_id=None` catch-all wakeup) alongside any project-specific event |
| `job_queue_service.py` | 299–313 | When `project_id` is `None` and `queue_id` is `None`, `resolved_queue_id` stays `None` — job created without queue assignment |
| `dead_letter_service.py` | 119, 190 | `project_id=job.project_id or ""` — converts `None` to empty string to satisfy NOT NULL |
| `retry_scheduler.py` | 181 | **Known bug**: silently drops jobs with `project_id=None` — no retry attempted |

### Target State

- All existing `project_id=NULL` rows backfilled to system project ID via migration.
- `job_processor.py` — no orphan block, no `project_id is None` check.
- `dispatch_event_bus.py` — no `_global_event`, no `None` handling in `notify_new_job()`.
- `job_queue_service.py` — `enqueue()` has `assert project_id is not None` after normalization (defense-in-depth). The old `elif project_id is None` branch removed.
- `dead_letter_service.py` — no `or ""` fallback; `project_id` passed through as-is. Assertion added to catch normalization gaps.
- `job_retry_engine.py` — type signature fixed (`str = None` → `str | None = None`).

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **3.0** | **🚨 [B2] Create SQL migration: backfill existing `project_id=NULL` rows** | Create a new migration file in `daemon/migrations/versions/`. The migration must: (1) Run AFTER system_default_project bootstrap — use `op.execute` to SELECT the system project ID by name, or hardcode the deterministic UUID. (2) `UPDATE job_queue_items SET project_id = <system_default_project_id> WHERE project_id IS NULL`. (3) `UPDATE dead_letter_items SET project_id = <system_default_project_id> WHERE project_id IS NULL OR project_id = ''`. (4) Add verification step: raise an error if `SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL` > 0 after the UPDATE. (5) Set `queue_id` on any newly-normalized jobs that have `queue_id IS NULL` by assigning them to `system_fifo_queue`. | `daemon/migrations/versions/` (new file) |
| 3.1 | Remove C5 orphan fallback block | Delete lines 304–332 entirely. Before deleting, search for any remaining callers of `list_all_pending` with `None` project filtering. Verify `JobRecoveryService` is also clean (it iterates by instance, not by project). | `daemon/services/job_processor.py` |
| 3.2 | Simplify `DispatchEventBus.notify_new_job()` | Remove `_global_event` field and `_get_global_event()` method. In `notify_new_job()`, remove the always-set global event. Keep only the per-project event path. Update `notify_all()` to iterate only `_events.values()`. Update `wait_for_job()`: if `project_id=None` is passed, fall back to polling (no event) rather than a global event. | `daemon/services/dispatch_event_bus.py` |
| **3.3** | **🚨 [B2] Add `assert project_id is not None` in `enqueue()` after normalization** | **[R2 FIX]** After the `normalize_project_id()` call in `enqueue()` (added in Phase 2 task 2.2), add: `assert project_id is not None, "enqueue() project_id is None after normalization — this should never happen"`. This replaces the old defensive `elif project_id is None` branch with a hard assertion. Remove the old branch (it was kept with a comment in the original plan, but the assertion is stronger). | `daemon/services/job_queue_service.py` |
| 3.4 | Remove `or ""` conversion in `DeadLetterService` | Lines 119 and 190: change `project_id=job.project_id or ""` to `project_id=job.project_id`. Same for `queue_id=job.queue_id or ""`. Add an assertion at the top of both methods: `assert job.project_id is not None, "DeadLetterService.move_to_dlq requires normalized project_id"`. This turns silent data corruption into a loud assertion failure. | `daemon/services/dead_letter_service.py` |
| **3.5** | **🚨 [R4] Fix type signature in `job_retry_engine.py:261`** | Change `project_id: str = None` → `project_id: str \| None = None`. This is a latent type error that could cause issues with strict type checking. | `daemon/services/job_retry_engine.py` |
| 3.6 | Audit for remaining `project_id is None` checks | Search for any remaining `project_id is None` or `if project_id` checks in service/repository code that handle orphan jobs. Document findings and clean up any that remain. | All `daemon/services/`, `daemon/repositories/` |
| **3.7** | **🚨 [R3] Verify `retry_scheduler.py:181` post-migration** | Document as a known current bug: `retry_scheduler.py:181` silently drops jobs with `project_id=None`. After migration, no `NULL` rows exist, so this code path is unreachable. Add a comment: `# [R3] Post-migration: project_id is never None, this branch is unreachable`. Do not change the behavior — just document it. | `daemon/services/retry_scheduler.py` |
| 3.8 | Integration test: orphan job goes to system project | Submit a job with `project_id=None` via `POST /jobs`, verify the DB row has `project_id = SYSTEM_DEFAULT_PROJECT_ID` and `queue_id` is assigned to `system_fifo_queue`. | `tests/integration/` |
| 3.9 | Integration test: DLQ receives normalized project_id | Simulate a job failure and DLQ move; verify `DeadLetterItem.project_id` equals the system project ID (not `""` or `None`). | `tests/integration/` |
| **3.10** | **🚨 [B2] Migration verification test** | Add a test that: (1) Inserts a job with `project_id=NULL`, (2) Runs the migration, (3) Asserts `SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL` returns 0, (4) Asserts the job's `project_id` now equals `SYSTEM_DEFAULT_PROJECT_ID`. | `tests/integration/test_migration.py` (new) |
| 3.11 | Run full test suite | All tests pass. Any failure in the orphan paths confirms a normalization gap. | `pytest tests/ -v` |

---

## Key Files

- `daemon/migrations/versions/` — **🚨 New migration file — backfill NULL project_ids** (B2 fix)
- `daemon/services/job_processor.py` — Remove C5 fallback block (lines 304–332)
- `daemon/services/dispatch_event_bus.py` — Remove `_global_event`, simplify `notify_new_job()` and `notify_all()`
- `daemon/services/job_queue_service.py` — **🚨 Replace `None` branch with `assert`** (R2 fix)
- `daemon/services/dead_letter_service.py` — Remove `or ""` conversions, add assertions
- `daemon/services/job_retry_engine.py` — **🚨 Fix type signature** (R4 fix)
- `daemon/services/retry_scheduler.py` — **🚨 Document known bug as unreachable** (R3 fix)
- `tests/integration/test_migration.py` — **🚨 New file — migration verification** (B2 fix)

---

## Constraints

1. **🚨 Migration BEFORE C5 removal (B2).** Task 3.0 (migration) MUST complete and be verified before task 3.1 (C5 removal). The migration is the safety net — it backfills existing orphan rows so they can be processed by the normal (non-fallback) path.
2. **Incremental verification.** Remove each code path and run the test suite before moving to the next. Do not remove multiple paths in a single untested commit.
3. **Assert, don't silently ignore.** After removing `or ""` in `DeadLetterService` and the `None` branch in `enqueue()`, add assertions that fire if `None` reaches this code. This ensures that if Phase 2 normalization ever has a gap, it is caught immediately.
4. **`DispatchEventBus` polling fallback.** When `project_id=None` is passed to `wait_for_job()` after Phase 3, the method must degrade gracefully to a pure polling behavior (wait for `timeout`, return `False`). Do not raise an error — `JobProcessor._process_loop()` passes `project_id=None` in some call paths.
5. **Migration idempotency.** The migration must be safe to run multiple times. If no `NULL` rows exist, the UPDATE affects 0 rows and the verification passes.

---

## Deliverables

- [ ] **🚨 SQL migration file backfilling `project_id=NULL` rows** (B2 fix)
- [ ] **🚨 Migration verification: 0 rows with `project_id IS NULL` after migration** (B2 fix)
- [ ] C5 orphan fallback removed from `job_processor.py`
- [ ] `_global_event` removed from `dispatch_event_bus.py`; per-project event-only path retained
- [ ] **🚨 `assert project_id is not None` in `enqueue()` replacing old `None` branch** (R2 fix)
- [ ] `or ""` conversions removed from `dead_letter_service.py`; assertions added
- [ ] **🚨 `job_retry_engine.py:261` type signature fixed** (R4 fix)
- [ ] **🚨 `retry_scheduler.py:181` documented as unreachable post-migration** (R3 fix)
- [ ] Remaining `project_id is None` checks audited and documented
- [ ] Integration test: job with null project_id lands in system project
- [ ] Integration test: DLQ item has system project ID (not empty string)
- [ ] **🚨 Migration verification integration test** (B2 fix)
- [ ] All existing tests pass (`pytest tests/ -v`)
