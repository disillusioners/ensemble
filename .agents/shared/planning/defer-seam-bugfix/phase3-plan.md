# Phase 3: Reconciliation Infrastructure

**Closes:** F2, F5, F6, F8, F10, F12, F13, F14, F15  
**Category:** C (remaining reconciliation bugs) + B (F8 second defer gate)  
**PR:** PR 3 — reconciliation infrastructure, benefits from PR 1+2 test coverage

## Objective
Build the reconciliation infrastructure that catches and repairs drift states between the JobItems and Tasks tables. This includes: a periodic reconciler (F5/F10) that catches stuck-`processing` + never-claimed-`pending` states; retry `work_id` stability (F6) so watchers survive across retries; the second defer idle-gate fix (F8); stale PENDING task cancellation on retry (F12); and three observer hardening fixes (F13/F14/F15) that prevent premature finalization.

## Coupling
- **Depends on**: Phase 1 (shared idle predicate, `is_deferred` wiring, test infrastructure), Phase 2 (lock-release scoping for correct reconciler interventions, `terminal_reason` status semantics)
- **Coupling type**: tight — the reconciler must understand the lock-scoping semantics from Phase 2, and the observer hardening depends on the correct status semantics from Phase 2's F3 fix
- **Shared files with other phases**: `daemon/repositories/task/repository.py` (`schedule_retry` for F6 — also touched by Phase 1 for the cross-system guard), `daemon/services/job_feedback_observer.py` (F13/F14/F15 — not touched by other phases)
- **Shared APIs/interfaces**: The periodic reconciler uses the shared `has_active_non_deferred_work` predicate from Phase 1
- **Why this coupling**: The reconciler needs to know whether a lock release is scoped correctly (Phase 2) before deciding to force-cancel or retry. The observer hardening needs correct status semantics (Phase 2 F3) to avoid finalizing the wrong sibling.

## Context
- Previous phase completed: Phase 2 delivered lock-release scoping, `list_work` dedup, and status map fixes
- Key decisions: The reconciler should be conservative — only act on clear drift states. Observer hardening should prefer exact job ID resolution over freshest-by-created_at. Retry watcher migration keeps the exact-match contract unchanged.

---

## Tasks

### Task Group C4: Periodic reconciler (F5, F10)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `reconcile_drift_states` method to JobRecoveryService | New method that runs periodically (not just startup). Scans for drift states: (a) `active` JobItem + `pending` Task (P1-pattern deadlock), (b) `done` JobItem + `running` Task (F10 double-execution risk), (c) `active` JobItem + instance not `running`/`idle` for extended period. | `daemon/services/job_recovery_service.py` (new method) |
| 2 | Register the reconciler alongside StaleTaskRecovery's loop | **The reconciler MUST NOT use MaintenanceService._loop** — that loop runs on a 15-min interval and is gated on `_is_idle`, which (after Phase 1's fix) returns False during active work. The reconciler needs to run precisely DURING active work to catch drift. Instead, register it in the same loop/daemon as `StaleTaskRecovery.run_cycle()` — find where `StaleTaskRecovery` is scheduled (its loop runs independently of the `_is_idle` gate) and add `reconcile_drift_states` as an adjacent call. If StaleTaskRecovery is on the maintenance loop, the reconciler gets its own asyncio task with a 60s sleep interval. | `daemon/services/stale_task_recovery.py` or `daemon/daemon.py` (registration site), `daemon/services/job_recovery_service.py` |
| 3 | Reconciler action: stuck "processing" + "pending" Task (P1-pattern) | When `active` JobItem + `pending` Task with no heartbeat: stamp the `message_id` if missing (Phase 1 fix should prevent this, but reconciler is defense-in-depth). If the task is truly orphaned (instance dead), cancel the task and finalize the job as FAILED. | `daemon/services/job_recovery_service.py` |
| 4 | Reconciler action: `done` JobItem + `running` Task (F10) | When `done` JobItem + `running` Task: force-complete the Task (it's a zombie — the JobItem already finalized). Log at WARNING. Do NOT retry — the JobItem is terminal. Uses the new `force_complete_task` method (Task 5). | `daemon/services/job_recovery_service.py`, `daemon/services/stale_task_recovery.py` |
| 5 | Add `force_complete_task(task_id, reason)` to StaleTaskRecovery | StaleTaskRecovery currently has `force_cancel_and_schedule_retry` and `fail_task`, but no `force_complete_task`. Add a new method that transitions a Task from `running` → `completed` with a `reason` annotation in the result/error fields. This is used by the F10 reconciler action to clean up zombie tasks whose JobItem is already terminal. | `daemon/services/stale_task_recovery.py` (new method) |
| 6 | Test: reconciler catches P1-pattern deadlock | Seed: `active` JobItem + `pending` Task with NULL heartbeat → run reconciler → verify the drift is detected and corrected. | `tests/job_queue/test_seam_invariants.py` |
| 7 | Test: reconciler catches F10 done+running mismatch | Seed: `done` JobItem + `running` Task → run reconciler → verify the Task is force-completed (not cancelled/retried). | `tests/job_queue/test_seam_invariants.py` |

### Task Group C5: Retry watcher migration for `work_id` stability (F6)

> ⚠️ **CRITICAL — reworked from original plan.** The original Option (c) (derived handle `f"{parent_work_id}#retry:{retry_count}"` matchable by prefix) does NOT work because `notify_work_watchers` (`work_notifier.py:233`) does an **exact** `get_watchers_for_job(work_id)` match — no prefix matching. Additionally, the parent Task is NOT deleted before retry insert — only its status is set to `cancelled`. Reusing `parent.work_id` would trigger a UNIQUE constraint violation.

**New approach (Option 2b — watcher migration):** Inside `schedule_retry`'s transaction, after inserting the retry Task with its new `work_id`, migrate watcher rows from the parent's `work_id` to the child's `work_id`. This keeps the exact-match contract unchanged and preserves watcher continuity.

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 8 | Keep generating fresh `work_id` for retry Task (no change) | The retry Task at `task/repository.py:1316` generates `work_id = str(uuid.uuid4())`. This is correct — a UNIQUE constraint violation would occur if we reused the parent's `work_id` since the parent row is only `cancelled`, not deleted. **No change needed here.** | `daemon/repositories/task/repository.py:1294-1318` |
| 9 | Migrate watcher rows inside `schedule_retry`'s transaction | After the retry Task INSERT (line 1316) and before the transaction commits, execute: `UPDATE job_watchers SET job_id = :child_work_id WHERE job_id = :parent_work_id`. The `:parent_work_id` is `parent_row.work_id`; the `:child_work_id` is the newly generated UUID from line 1316. This atomically moves all watchers from the parent to the child, so `notify_work_watchers` (which does exact `get_watchers_for_job(work_id)`) will find them on the child's `work_id`. | `daemon/repositories/task/repository.py:1294-1318` (add UPDATE within same transaction) |
| 10 | Handle orphaned parent watchers safely | After the UPDATE, some watcher rows may have `job_id` values that don't match any current Task `work_id` (stale watchers from previous retries). These are cleaned up by the existing `reconcile_terminal_watches` mechanism at daemon restart. No additional cleanup needed in `schedule_retry`. | N/A (existing mechanism) |
| 11 | Test: watcher survives retry via migration | Seed: register a watcher via `watch_job(job_id)` → trigger task retry → verify the watcher row's `job_id` has been migrated to the child's `work_id` → verify `notify_work_watchers(child_work_id)` delivers the notification. | `tests/job_queue/test_seam_invariants.py` |

### Task Group C6: Stale PENDING task cancellation on retry (F12)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 12 | Cancel stale PENDING tasks BEFORE `start_job` on retry | In `job_retry_engine.py`'s caller of `atomic_retry` (around line 318): after the JobItem transitions `done → queued`, cancel any PENDING Task for the same `instance_id` **BEFORE** calling `start_job` (which spawns a fresh instance/Task). **Ordering is critical:** cancel FIRST, then `start_job`. If `start_job` runs first, the new Task and the stale PENDING Task can contest the same LangGraph checkpoint. | `daemon/services/job_retry_engine.py:318-336` |
| 13 | Add `cancel_pending_tasks_for_instance` repository method | New method on TaskRepository: `UPDATE task SET status = 'cancelled' WHERE instance_id = :instance_id AND status = 'pending'`. Returns count of cancelled rows. | `daemon/repositories/task/repository.py` (new method) |
| 14 | Test: stale PENDING task cancelled on retry | Seed: instance with an active JobItem + a leftover PENDING Task → trigger retry → verify the PENDING Task is cancelled BEFORE re-admission (before the new Task is created). | `tests/job_queue/test_seam_invariants.py` |

### Task Group B2: Second defer idle-gate (F8)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 15 | Verify `_select_next_eligible_job` uses shared predicate | Phase 1 Task 10 should have already updated this. Verify and test that the observer admission path (`job_feedback_observer.py:2670`) correctly uses the shared `has_active_non_deferred_work` predicate. If Phase 1 only updated the predicate but not this specific call site, update it now. | `daemon/services/job_queue_service.py:1750-1758`, `daemon/services/job_feedback_observer.py:2670` |
| 16 | Test: observer path respects defer idle-gate | Seed: active non-deferred Task + defer-queue JobItem → trigger `_select_next_eligible_job` via observer path → verify defer job is not selected. | `tests/job_queue/test_seam_invariants.py` |

### Task Group C7: Observer hardening (F13, F14, F15)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 17 | F13: Resolve exact job by ID, not freshest-by-created_at | `get_active_by_instance` (repo `:369-394`) returns the freshest ACTIVE JobItem by `created_at`. If two ACTIVE JobItems exist for one instance, the wrong one may be finalized. Fix: prefer resolving by exact `job_id` when available. The observer's `_get_processing_job_for_instance` (`:620-630`) should pass `job_id` through if known. | `daemon/services/job_feedback_observer.py:620-630`, `daemon/repositories/job_queue/repository.py:369-394` |
| 18 | F14: Bus gate counts non-bus-registered pending tasks | The premature-finalization gate (`:2258`, `:2387`) counts `dependency_watchers` rows only. A child Task whose `send_message` failed before `bus.watch` ran is invisible. Fix: also count pending `task` rows for the instance (using the task table as source of truth). | `daemon/services/job_feedback_observer.py:2258, 2387` |
| 19 | F15: Deferred finalize check guards against TOCTOU | The 5s `_deferred_finalize_check` re-queries `_get_processing_job_for_instance`. A `job_continue`/`watch_job` that created a new JobItem during the sleep window gets finalized prematurely. Fix: capture the `job_id` at scheduling time and verify it's still the same (or the only) active job before finalizing. | `daemon/services/job_feedback_observer.py:741-776, 1636-1795` |
| 20 | Test: F13 — exact job resolution | Seed: two ACTIVE JobItems for one instance → trigger finalize → verify the correct (newest by job_id, not created_at) is finalized. | `tests/job_queue/test_job_feedback_observer.py` |
| 21 | Test: F14 — bus gate sees pending tasks | Seed: instance with a pending Task not registered in dependency_watchers → verify the finalize gate does not prematurely finalize. | `tests/job_queue/test_job_feedback_observer.py` |
| 22 | Test: F15 — deferred check TOCTOU guard | Seed: deferred finalize scheduled → new JobItem created during delay → verify the old job is NOT prematurely finalized. | `tests/job_queue/test_deferred_finalize_check.py` |

### Task Group C8: F2 maintenance `_is_idle` (if not completed in Phase 1)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 23 | Verify `_is_idle` was updated in Phase 1 | Phase 1 Task 11 should have updated `_is_idle` to consult active work using `has_active_non_deferred_work(project_id=None)` and `admission_state IN ('queued', 'active')`. If it wasn't fully implemented, complete it now. | `daemon/services/maintenance.py:212-242` |

---

## Key Files
- `daemon/services/job_recovery_service.py` — new `reconcile_drift_states` method (F5/F10)
- `daemon/services/stale_task_recovery.py` — new `force_complete_task` method (F10), reconciler registration site
- `daemon/repositories/task/repository.py` — watcher migration in `schedule_retry` (F6), new `cancel_pending_tasks_for_instance` (F12)
- `daemon/services/job_retry_engine.py` — stale task cleanup ordering (F12)
- `daemon/services/job_feedback_observer.py` — exact job resolution (F13), bus gate (F14), TOCTOU guard (F15)
- `daemon/repositories/job_queue/repository.py` — `get_active_by_instance` (F13)
- `daemon/services/maintenance.py` — `_is_idle` (F2)
- `tests/job_queue/test_seam_invariants.py` — reconciler, retry, F12 tests
- `tests/job_queue/test_job_feedback_observer.py` — observer hardening tests

## Constraints
- The periodic reconciler must NOT cause false-positive force-cancels. Use conservative detection criteria and log-only mode for ambiguous cases.
- The reconciler MUST bypass the `_is_idle` gate — it needs to run precisely during active work. Do NOT register it on MaintenanceService._loop.
- The `force_complete_task` method must only be called when the JobItem is confirmed terminal (`done`). Never force-complete a Task whose JobItem is still `active`.
- The watcher migration (F6) must happen inside the same transaction as the retry Task INSERT — if the transaction rolls back, the migration must also roll back.
- The reconciler interval (60s) must be configurable, not hardcoded.
- Observer hardening changes must not break the existing `test_deferred_finalize_check.py` (4 tests) or `test_job_feedback_observer.py`.
- All changes must pass on both SQLite and PostgreSQL test suites.

## Deliverables
- [ ] Periodic reconciler runs every 60s (on StaleTaskRecovery's loop or own asyncio task, NOT maintenance._loop)
- [ ] Periodic reconciler catches P1-pattern deadlock (active JobItem + pending Task)
- [ ] Periodic reconciler catches F10 done+running mismatch and force-completes zombie tasks
- [ ] `force_complete_task(task_id, reason)` exists on StaleTaskRecovery
- [ ] Watcher rows are migrated from parent to child `work_id` inside `schedule_retry`'s transaction
- [ ] `watch_job` watcher survives across task retry (exact-match, no prefix matching needed)
- [ ] Stale PENDING tasks are cancelled BEFORE `start_job` on retry re-admission
- [ ] Second defer idle-gate (observer path) uses shared predicate
- [ ] Observer resolves exact job by ID, not freshest-by-created_at
- [ ] Bus gate counts pending tasks, not just dependency_watchers rows
- [ ] Deferred finalize check guards against TOCTOU
- [ ] `maintenance._is_idle` returns False during active work
- [ ] All existing tests pass (8000+ SQLite unit tests)
- [ ] PostgreSQL test suite passes (`tests/postgres/`)

## Implementation Notes

### Periodic reconciler — loop registration (critical)
The reconciler MUST NOT be registered on `MaintenanceService._loop`. That loop:
1. Runs on a 15-minute interval (too slow for drift detection)
2. Is gated on `_is_idle` — after Phase 1's fix, `_is_idle` returns False during active work, which is exactly when the reconciler needs to run

**Correct approach:** Register `reconcile_drift_states` alongside `StaleTaskRecovery.run_cycle()`. StaleTaskRecovery has its own loop that runs independently of `_is_idle` (it runs during active work to find stale tasks). If StaleTaskRecovery's loop is not a separate asyncio task, the reconciler gets its own `asyncio.create_task` with a 60s sleep interval. The reconciler is completely independent of the `_is_idle` gate.

```python
async def reconcile_drift_states(self) -> dict:
    """Periodic drift reconciliation. Runs every 60s, independent of _is_idle.
    
    Detects and repairs:
    1. active JobItem + pending Task with no heartbeat → P1-pattern deadlock
    2. done JobItem + running Task → F10 zombie task
    3. active JobItem + instance not running/idle for >5min → stuck processing
    """
```

### F6 — Watcher migration (Option 2b)
Inside `schedule_retry`'s existing transaction (the `with self.engine.begin() as conn:` block), after the retry Task INSERT returns `child_work_id`:

```python
# After retry Task INSERT (line 1316):
child_work_id = result.work_id

# Migrate watchers from parent to child (atomic, same transaction):
conn.execute(
    text("UPDATE job_watchers SET job_id = :child_work_id WHERE job_id = :parent_work_id"),
    {"child_work_id": child_work_id, "parent_work_id": parent_row.work_id},
)
```

This keeps `notify_work_watchers`'s exact-match contract (`get_watchers_for_job(work_id)`) unchanged. The watcher rows now point at the child's `work_id`, so the next notification will find them. The parent's `work_id` is freed (no watchers reference it).

**Why not reuse parent's `work_id`:** The parent Task is only `cancelled` (status set to `cancelled`), NOT deleted from the table. The `work_id` column has `UNIQUE=True` constraint. Inserting a new row with the same `work_id` would violate the constraint.

**Why not prefix matching:** `work_notifier.py:233` calls `get_watchers_for_job(work_id)` which does `WHERE job_id = :work_id` (exact match). Changing this to prefix matching would be a broader API change affecting all callers.

### F12 — Ordering (cancel THEN start_job)
The retry flow must execute in this exact order:
1. `atomic_retry(job_id, ...)` → JobItem transitions `done → queued`
2. `cancel_pending_tasks_for_instance(instance_id)` → stale PENDING tasks cancelled
3. `start_job(job_id)` → spawns fresh instance/Task

If step 3 runs before step 2, the new Task and the stale PENDING Task coexist for the same `instance_id`, and both can be claimed by the WorkerPool — contesting the same LangGraph checkpoint.

### Observer F13 fix — exact job resolution
The cleanest fix: `_get_processing_job_for_instance` should accept an optional `job_id` parameter. When the observer knows the `job_id` (from the event that triggered it), pass it through and query by exact ID instead of freshest-by-created_at. The `get_active_by_instance` repo method should also support an optional `job_id` filter.

### Observer F14 fix — bus gate expansion
The premature-finalization gate at `:2258` and `:2387` should also check:
```sql
SELECT COUNT(*) FROM task 
WHERE instance_id = :instance_id 
  AND status = 'pending'
```
If this count > 0 (pending tasks not registered in dependency_watchers), defer finalization.

### Observer F15 fix — TOCTOU guard
Capture `job_id` (or a snapshot of active job IDs) when scheduling `_deferred_finalize_check`. After the sleep, verify the same `job_id` is still the active job. If a new job was created during the sleep, skip finalization for the old job.
