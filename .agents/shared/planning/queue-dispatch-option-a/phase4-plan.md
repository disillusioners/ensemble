# Phase 4: Filters & Safety — Remove All Message Exclusions

## Objective

Complete the circuit: remove ALL `job_type != "message"` filters so message JobItems flow through JobProcessor dispatch, cancel, and cleanup paths. Also remove the PostgreSQL trigger exemption, the startup migration that destroys in-flight message jobs, and the now-redundant WorkerPool mirror activation.

## Coupling

- **Depends on**: Phase 3 (new producer must be live — removing filters before the new producer would double-dispatch via the old Task path)
- **Coupling type**: **tight**
- **Shared files with other phases**: `repository.py` (5 filters), `manager.py` (trigger + startup migration), `worker_pool.py` (activation)
- **Shared APIs/interfaces**: Repository query signatures (callers unchanged — only the `.where` clauses change)
- **Why this coupling**: This phase is the "flip the switch" moment. It must land AFTER Phase 3's producer is producing authoritative QUEUED JobItems. Landing it before would expose stale mirror JobItems to double-dispatch.

## Context

- **Previous phases completed**: Phase 1 (enqueue callable) + Phase 2 (spawn reuses) + Phase 3 (producer rewritten)
- **Key decisions**:
  - **All 5 filters removed in one commit** — they must be removed together. Removing only one would make message dispatch depend on which JobProcessor query runs.
  - **PG trigger conjunct removed** — message jobs now require a `job_locks` row for ACTIVE state, same as task jobs.
  - **Startup migration removed** — the unconditional cancel of in-flight message jobs is no longer appropriate (those are now legitimate jobs).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Remove filter in `list_pending_by_project` | Delete line 921: `.where(JobItem.job_type != "message")`. Update docstring at 905-908. | `daemon/repositories/job_queue/repository.py:898-926` |
| 2 | Remove filter in `list_all_pending` | Delete line 945: `.where(JobItem.job_type != "message")`. Update docstring at 933. | `daemon/repositories/job_queue/repository.py:928-950` |
| 3 | Remove filter in `list_pending_by_queue` | Delete line 1022: `.where(JobItem.job_type != "message")`. Update docstring at 1006-1009. | `daemon/repositories/job_queue/repository.py:1001-1027` |
| 4 | Remove filter in `cancel_all_queued_jobs` | Delete line 2315: `.where(JobItem.job_type != "message")`. Update docstring at 2297-2301. | `daemon/repositories/job_queue/repository.py:2288-2324` |
| 5 | Remove filter in `find_active_jobs` | Delete line 2355: `.where(JobItem.job_type != "message")`. Update docstring at 2336. | `daemon/repositories/job_queue/repository.py:2326-2359` |
| 6 | Remove PG trigger exemption | In `manager.py:3355`, the trigger function `job_queue_items_active_lock_guard` has `IF NEW.admission_state = 'active' AND NEW.job_type != 'message' THEN`. Remove the `AND NEW.job_type != 'message'` conjunct so it becomes `IF NEW.admission_state = 'active' THEN`. This is a `CREATE OR REPLACE FUNCTION` statement — provide a migration that runs it. | `daemon/manager.py:3355` |
| 7 | Remove/gate startup migration | Lines 620-636 call `self._migrate_cancel_inflight_message_jobitems()` unconditionally on startup. Either (a) remove the call entirely, or (b) gate it behind a version check (only run if upgrading from pre-Option-A). The migration method at 3848-3932 (with `.where(JobItem.job_type == "message")` at 3891) cancels ALL message JobItems — this would destroy legitimate jobs. | `daemon/manager.py:620-636, 3848-3932` |
| 8 | Remove WorkerPool mirror activation | Lines 267-292 and 363+ in `worker_pool.py` contain `_activate_message_jobitem_async` which eagerly flips mirror JobItems `queued→active`. Under the standard path, activation happens via `start_job_atomic_with_lock`. Remove the activation call at 291-292 and the helper method at 363+. | `daemon/services/worker_pool.py:267-292, 363+` |

## Key Files

- `daemon/repositories/job_queue/repository.py` — 5 filters (921, 945, 1022, 2315, 2355)
- `daemon/manager.py` — PG trigger (3355), startup migration (620-636, 3848-3932)
- `daemon/services/worker_pool.py` — mirror activation (267-292, 363+)

## Secondary Review Items (no change expected, but verify)

| Item | File:Line | Action |
|------|-----------|--------|
| Instance cleanup skip | `instance_lifecycle.py:1790` | `if remaining_job.job_type == "message": continue` — review whether this skip is still correct. If message jobs are now authoritative, skipping them in cleanup may be wrong. |
| Message-scoped UPDATE | `instance_lifecycle.py:3732-3768` | SQL UPDATE scoped to `job_type='message'` — review whether this should now apply to all jobs or stay message-specific. |
| Removed child-report guards | `child_reports.py:900-905, 1413-1418` | Comments reference removed `_has_no_active_message_job`. Under real active message jobs, review parent status transitions. Add behavioral tests. |
| Task repository D13 comments | `task/repository.py:630-660, 1390-1410` | Comments referencing removed `j.job_type = 'message'` predicates — verify the actual queries are still correct (no stale filters). |
| Orphan reaper | `repository.py:2393-2402` | Already handles both job types (`job_type != 'message'` NOT filtered here) — no change needed. |

## PG Trigger Migration

The trigger change (Task 6) requires careful handling per the 🔴 critical constraint: **PostgreSQL is the PRIMARY dev/test DB** and **`_ensure_postgres_columns()` must be used for ALL new columns on existing tables**. For a `CREATE OR REPLACE FUNCTION`, the migration is:

```sql
-- Option A: Remove message exemption from active_lock_guard trigger
CREATE OR REPLACE FUNCTION job_queue_items_active_lock_guard() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.admission_state = 'active' THEN  -- removed: AND NEW.job_type != 'message'
    IF NOT EXISTS (SELECT 1 FROM job_locks WHERE instance_id = NEW.instance_id) THEN
      RAISE EXCEPTION 'admission_state=active requires a job_locks row (instance_id=%)', NEW.instance_id
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

This statement is in `manager.py:3355` within `_ensure_postgres_columns` (or the equivalent trigger setup function). It runs on every startup via `CREATE OR REPLACE`, so it's idempotent. No separate `.sql` migration file needed (those NO-OP on PostgreSQL per the 🔴 constraint).

**Rollback**: To revert, restore the `AND NEW.job_type != 'message'` conjunct. Document this in the migration.

## Constraints

- **Ordering is critical**: Tasks 1-5 (filters) + Task 6 (trigger) + Task 7 (startup) + Task 8 (WorkerPool) must ALL land in the same commit/PR. Removing filters without the trigger fix would cause PG constraint violations. Removing the startup migration without the producer fix (Phase 3) would leave in-flight jobs orphaned.
- **No partial filter removal**: All 5 `.where(JobItem.job_type != "message")` clauses must be removed together.
- **Test against PostgreSQL**: The trigger change CANNOT be validated on SQLite (SQLite has no equivalent trigger). Run the full PG test suite.

## Deliverables

- [ ] All 5 `job_type != "message"` filters removed from repository queries
- [ ] PG trigger no longer exempts message jobs from the active-lock requirement
- [ ] Startup migration removed or version-gated (no in-flight message job destruction)
- [ ] WorkerPool mirror activation removed
- [ ] Integration test: message JobItem appears in `list_pending_by_queue` results
- [ ] Integration test: message job admission acquires a `job_locks` row (PG trigger passes)
- [ ] Integration test: restart with in-flight message jobs → jobs survive

## Notes

- This is the "flip the switch" phase. After this, the full circuit is live: producer (Phase 3) → enqueue (Phase 1) → spawn-reuse (Phase 2) → **dispatch via JobProcessor (this phase removes the filter barrier)**.
- The transitional state from Phase 3 (messages queued but filtered out) is resolved here atomically.
- **Highest risk in this phase**: the PG trigger change. If message jobs don't acquire locks correctly, every message admission will abort with a constraint violation. Verify `start_job_atomic_with_lock` runs for messages (it has no `job_type` filter — confirmed by exploration).
