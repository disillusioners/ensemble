# Phase 1: Reconciliation Code Fix

## Objective

Add a Task reconciliation step to the finalization path so that when a JobItem transitions to terminal state (`done`/`dead`), the linked Task (via `work_id`) is reconciled to `cancelled` if it is still in a non-terminal, non-running state (`paused` or `pending`). Pause-first crash recovery is preserved — reconciliation runs **only** when the JobItem is **truly** terminal (after the existing pending-tasks gate passes).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add SYNC `TaskRepository.reconcile_terminal_task(self, work_id: str) -> int` method to `daemon/repositories/task/repository.py` (suggested placement: after line 2126, alongside `has_active_non_background_work`). **Constructor signature:** `TaskRepository.__init__(self, engine: Engine)` — NOT a session. All repo methods use sync `with self.engine.begin() as conn:` | none | Method returns count of updated rows (0 or 1 in normal operation); guards with `WHERE status IN ('paused', 'pending')` **AND `EXISTS` JobItem terminal check** (self-contained — call site does NOT need to pre-check); logs each reconciliation at INFO level |
| 2 | Add `TASK_RECONCILIATION_BEST_EFFORT` config flag (default `True`) in `daemon/services/job_feedback_observer.py` or via `daemon/config.py` (whichever pattern fits the codebase) | none | Flag accessible via settings; documented as a kill-switch for the reconciliation step |
| 3 | Add Step 4 to `_finalize_job_db_sync` (`daemon/services/job_feedback_observer.py:2802`) — **POST-COMMIT, after `reconcile_turn_mirror` block at line 3469**. Step 4 opens its own `engine.begin()` block, mirroring the existing `reconcile_turn_mirror` pattern (lines 3456-3469). Steps 1-3 (JobItem UPDATE ~3105-3202 → Instance UPDATE → job_locks DELETE) stay in-session; Step 4 is in a separate transaction | Tasks 1-2 | After Steps 1-3 commit and the post-commit `reconcile_turn_mirror` block completes, Step 4 runs reconciliation; wrapped in try/except with `logger.warning`; uses `asyncio.to_thread(self._task_repo.reconcile_terminal_task, job_id)` since `reconcile_terminal_task` is SYNC |
| 4 | Add unit test: `paused` task + `done` JobItem → Task transitions to `cancelled` | Tasks 1-3 | Test passes; assert task.status == CANCELLED after `_finalize_job_db_sync` call |
| 5 | Add unit test: `running` task + `done` JobItem → Task NOT touched | Tasks 1-3 | Test passes; assert task.status == RUNNING after finalization (regression guard) |
| 6 | Add unit test: `paused` task with NON-terminal JobItem (`active`) → Task NOT reconciled | Tasks 1-3 | Test passes; assert task.status == PAUSED (proves the gate preserves pause-first crash recovery — the `AND EXISTS` JobItem terminal subquery blocks reconciliation when JobItem is still active) |
| 7 | **Verification**: Confirm `_resume_cascade_db_sync` handles `InvalidTransitionError`. Read `daemon/services/instance_lifecycle.py` — find the resume cascade DB sync method. Verify it catches `InvalidTransitionError` (or equivalent) when transitioning `PAUSED → RUNNING` and logs at DEBUG. If not caught, the race in C6 window (b) would cause an unhandled exception. Document the finding. The race severity stays Low ONLY if this catch path is confirmed | Tasks 1-6 | Document whether `_resume_cascade_db_sync` catches `InvalidTransitionError`; if yes, race severity remains Low; if no, escalate to Medium and add a follow-up task |

## Coupling

- **Tight with:** Phase 2 (Defensive Idle-Gate). Both operate on the same Task↔JobItem linkage and depend on the same predicate semantics. If Phase 1 is reverted, Phase 2 still works as defense-in-depth. If Phase 2 is reverted, Phase 1 still fixes the root cause.
- **Loose with:** Phase 3 (Data Migration). Phase 3 catches pre-existing data that Phase 1 will fix going forward. Phase 3 is **not** a dependency for Phase 1 — they can ship independently.
- **Independent of:** Instance lifecycle pause/resume code paths. This code runs only at finalization time, not at pause time.

## Detailed Implementation Guidance

### Task 1: New Repository Method (SYNC)

File: `daemon/repositories/task/repository.py` (add near line 2126, after `has_active_non_background_work`)

**Important (C1, W2):** `TaskRepository.__init__` takes `engine: Engine`, NOT a session. All repo methods are SYNC using `with self.engine.begin() as conn:`. Service-layer callers wrap in `asyncio.to_thread(...)`.

```python
def reconcile_terminal_task(self, work_id: str) -> int:
    """Reconcile an orphaned Task to terminal status when its linked
    JobItem is already terminal. Best-effort — caller wraps in try/except.

    Self-contained: the WHERE clause verifies the JobItem is terminal
    (admission_state IN 'done','dead' AND deleted_at IS NULL) before
    cancelling the Task. The call site does NOT need to pre-check.

    Guards against touching running tasks or already-terminal tasks:
    - Running tasks are excluded by the `status IN ('paused','pending')` guard.
    - Already-terminal tasks (completed/failed/cancelled) need no transition.
    - The AND EXISTS subquery ensures we only reconcile when the linked
      JobItem is truly terminal — prevents accidental cancellation of
      Tasks whose JobItem is still active/queued (pause-first crash recovery).

    Returns the count of updated rows (0 or 1 in normal operation).
    """
    from sqlalchemy import text
    with self.engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE task SET status = :status_cancelled,
                            updated_at = CURRENT_TIMESTAMP
            WHERE work_id = :work_id
              AND status IN (:status_paused, :status_pending)
              AND EXISTS (SELECT 1 FROM job_queue_items ji
                          WHERE ji.job_id = task.work_id
                            AND ji.admission_state IN (:qi_done, :qi_dead)
                            AND ji.deleted_at IS NULL)
        """), {
            "status_cancelled": TaskStatus.CANCELLED.value,
            "work_id": work_id,
            "status_paused": TaskStatus.PAUSED.value,
            "status_pending": TaskStatus.PENDING.value,
            "qi_done": AdmissionState.DONE.value,
            "qi_dead": AdmissionState.DEAD.value,
        })
        count = result.rowcount
    if count > 0:
        logger.info(
            "task.reconciled_to_cancelled",
            work_id=work_id,
            count=count,
        )
    return count
```

**Notes:**
- Method is **SYNC** (`def`, not `async def`). Wrapped by caller via `asyncio.to_thread`.
- Uses raw `text(...)` SQL with bound parameters (consistent with other repo methods like `has_active_non_background_work`).
- `CURRENT_TIMESTAMP` is used (not `func.now()`) for dual-driver portability — both SQLite and PostgreSQL support it natively.
- The `AND EXISTS` JobItem terminal subquery (C4) makes the method self-contained. The call site does not need to pre-check the JobItem state.

### Task 2: Configuration Flag

File: `daemon/services/job_feedback_observer.py` (or `daemon/config.py` — follow codebase pattern)

```python
TASK_RECONCILIATION_BEST_EFFORT: bool = True  # default ON
```

This allows operators to disable reconciliation via env var or config if unexpected behavior is observed post-deploy.

### Task 3: New Step 4 in `_finalize_job_db_sync` (POST-COMMIT)

File: `daemon/services/job_feedback_observer.py:2802`

**Placement (C3):** Insert AFTER line 3469 — i.e., after the existing post-commit `reconcile_turn_mirror` block (lines 3456-3469). Step 4 is **POST-COMMIT** and opens its own `engine.begin()` block, mirroring `reconcile_turn_mirror`. It does NOT share the caller's `WriteGuardSession`. If Step 4 fails, Steps 1-3 are already committed and remain durable.

```python
# Step 4 (POST-COMMIT): Reconcile orphaned Task to terminal.
# Opens its own engine.begin() — does NOT share the caller's
# WriteGuardSession. Best-effort: failure is logged, not fatal.
# Mirrors the post-commit reconcile_turn_mirror pattern at lines 3456-3469.
# Per docs/plans/task-job-reconciliation/phase1-plan.md.
task_repo = getattr(self._instance_manager, "_task_repo", None)
if task_repo is not None and hasattr(task_repo, "reconcile_terminal_task"):
    try:
        await asyncio.to_thread(
            task_repo.reconcile_terminal_task, job_id
        )
    except Exception as exc:  # noqa: BLE001  (NOT BaseException — see pause-cancellederror fix)
        logger.warning(
            "Step 4 reconcile_terminal_task failed for work_id=%s: %s",
            job_id, exc,
        )
```

### Why Step 4 in `_finalize_job_db_sync` (not `_finalize_terminal`)?

`_finalize_job_db_sync` is the authoritative DB-sync boundary. Specifically:

1. **It's already the 3-step ordered pattern**: job_queue_items UPDATE → instances UPDATE → job_locks DELETE. Steps 1-3 share the in-session transaction (with the existing pending-tasks gate ~lines 3213-3241 and bus gate as guard rails).
2. **Step 4 is POST-COMMIT** (after line 3469, after `reconcile_turn_mirror`): Step 4 opens its own `engine.begin()` block, mirroring `reconcile_turn_mirror` at lines 3456-3469. This is critical — if Step 4 ran inside the same `WriteGuardSession` as Steps 1-3 and failed pre-commit, it would roll back the JobItem finalization we just completed. POST-COMMIT placement keeps Step 4 in a separate failure domain: Steps 1-3 stay durable; Step 4's failure is logged, not propagated.
3. **F14 pending-tasks gate rationale (C5)**: The F14 gate (`_finalize_job_db_sync`) defers finalization while PENDING tasks exist (checking `status == 'pending'`); PAUSED tasks do NOT block finalization. Reconciliation of PAUSED tasks therefore runs unconditionally — the JobItem is already terminal (done/dead) and the Task is merely orphaned state, not in-flight work. The Step 4 `AND EXISTS` JobItem terminal subquery is what actually guarantees pause-first crash recovery — F14 only blocks finalization while PENDING work exists; the JobItem-terminal check in the UPDATE prevents cancelling a paused Task whose JobItem is still ACTIVE (e.g., a Task paused mid-flight whose parent hasn't finalized yet).
4. **It mirrors `reconcile_turn_mirror` post-commit pattern**: Lines 3456-3469 already implement this exact pattern (open its own `engine.begin()`, catch `InvalidTransitionError`, log at WARNING). Step 4 follows the same shape — reviewable against an existing precedent in the same function.
5. **`_finalize_terminal` would be the wrong place**: `_finalize_terminal` is the async entry point but delegates the actual DB writes to `_finalize_job_db_sync`. Adding reconciliation to `_finalize_terminal` would create a **second** DB-write path outside the existing transaction — a regression risk. The post-commit `reconcile_turn_mirror` block exists in `_finalize_job_db_sync` for the same architectural reason.

### Why `cancelled` (not `failed`)?

The Task did not fail on its own — its JobItem was externally finalized (e.g., via timeout, retry exhaustion, operator cancellation, deferral, or simply the parent finalizing while a child Task was paused). `cancelled` semantically captures "stopped without normal completion"; `failed` implies a Task-side error.

This matches the `CancellationReason` discriminator pattern in `daemon/services/task_processor.py` (per pause-first crash recovery convention noted in `daemon/services/instance_lifecycle.py`).

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | Reconciliation accidentally cancels a `running` task | High | `WHERE status IN ('paused', 'pending')` guard in the SQL; explicit test in Task 5 |
| 2 | Task UPDATE failure blocks JobItem finalization | High | try/except with `logger.warning`; do NOT propagate; do NOT wrap in transaction with Steps 1-3 (best-effort = separate failure domain) |
| 3 | Step 4 race with concurrent Task state change (paused→running via resume) | Low | The reconciliation runs AFTER the JobItem is already terminal; concurrent resume would mean the JobItem was finalized while the Task was still being processed — a separate pause-resume bug, out of scope |
| 4 | `CURRENT_TIMESTAMP` semantic differs between SQLite and PostgreSQL | Low | Both drivers support `CURRENT_TIMESTAMP` returning the current timestamp; semantically equivalent; used throughout Phase 1 Task 1 and Phase 3 for dual-driver portability |
| 5 | Reconciliation target status `CANCELLED` confuses downstream metrics that count `failed` tasks | Low | `CANCELLED` is a legitimate terminal status per `TaskStatus` enum (line 51); existing metrics must already distinguish terminal states; verify in code review |
| 6 | Step 4 reconciliation↔resume race (C6): (a) Step 4 fires while `PAUSED` + resume not yet committed → Task silently CANCELLED; (b) Step 4 fires first (`PAUSED → CANCELLED`) then resume cascade tries `PAUSED → RUNNING` → `InvalidTransitionError` | Low | Low | Race requires exact ordering between post-commit Step 4 and resume cascade. Window (b) requires confirmation that `_resume_cascade_db_sync` catches `InvalidTransitionError` and logs at DEBUG. See verification Task 7 — race severity stays Low ONLY if this catch path is confirmed. The `AND EXISTS` JobItem terminal subquery in `reconcile_terminal_task` already excludes `ACTIVE` JobItems, so window (a) only triggers if the JobItem transitioned to terminal between gate-check and Step 4 (extremely narrow). |

## Exit Criterion

All 7 tasks complete. Unit tests pass on both SQLite and PostgreSQL. Integration test confirms end-to-end:
- Stub `_finalize_job_db_sync` with a paused Task + active JobItem that becomes done.
- After finalization, Task status is `cancelled`.
- No regression in existing finalization tests (job_queue_items UPDATE, instances UPDATE, job_locks DELETE).
- Pause-first crash recovery test passes: pausing an instance with active JobItem leaves Task in `paused` (not cancelled).
- Step 4 is verified to be POST-COMMIT (after line 3469, after `reconcile_turn_mirror`), opens its own `engine.begin()`, and uses `asyncio.to_thread(self._task_repo.reconcile_terminal_task, job_id)`.
- Task 7 verification confirms `_resume_cascade_db_sync` catches `InvalidTransitionError` (C6 race severity remains Low).
