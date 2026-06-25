# Key Design Decisions

## Decision 1: First-Class PAUSED State for Jobs and Tasks

**Decision**: Add `PAUSED` as a first-class state in both `JobStatus` and `TaskStatus` enums.

**Rationale**: The current hack keeps jobs in PROCESSING during pause, which makes it impossible to distinguish "actually processing" from "paused but job still says PROCESSING". This ambiguity causes:
- Crash recovery to treat paused jobs as active
- Job tracking to lose state
- Race conditions on resume

**Alternative Considered**: Keep the hack (instance-level PAUSED only) but add a `paused_at` timestamp to jobs. Rejected because it doesn't solve the core ambiguity — the job is still in PROCESSING and can be claimed/completed by any finalize path.

**Trade-off**: Adds a new state to the state machine, requiring all state-transition guards to account for it. This is a wider change surface but fundamentally correct.

> **⚠️ Enforcement gate** (approver B1): The authoritative enforcement gate for job transitions is `daemon/services/job_state_machine.py` — the `TRANSITIONS` dict (lines 20-41) and `validate_transition()` (line 76). `JobRepository.atomic_transition()` at `repository.py:664` calls `validate_transition()` before any UPDATE. The PAUSED transition pairs MUST be added to this dict, or every pause/resume throws `InvalidTransitionError`: `(PROCESSING, PAUSED)`, `(PAUSED, PROCESSING)`, `(PAUSED, CANCELLED)`.

---

## Decision 2: Bus Watchers Preserved During Pause (NOT Cancelled) — with Growth Mitigation

**Decision**: During pause, preserve DependencyBus watchers instead of cancelling them. Add a compaction hook to prevent unbounded growth during long pauses with partial-tree scenarios.

**Rationale**: The current code cancels bus watchers via `_cancel_bus_watchers_for()` in `pause_instance_cascade()` (instance_lifecycle.py:1052). This was done to prevent late child reports from delivering FollowUps to a paused parent. However, this causes desynchronization on resume because:
1. Resume does NOT re-register bus watchers
2. Child reports that arrived during pause are lost
3. The parent doesn't know about completed children on resume

By keeping the job in PAUSED state, we can safely preserve bus watchers because:
1. The `_process_event` finalize path checks bus pending count before finalizing
2. PROCESS_REPORT tasks are blocked by the pause gate in `claim_pending_task` (instance status = PAUSED)
3. When resumed, pending PROCESS_REPORT tasks are claimed and processed normally

### Unbounded Growth Mitigation (addresses reviewer C3)

**Problem**: In partial-tree pauses (parent paused, children still running), children continue completing. Each completion transitions a `dependency_watchers` row PENDING → FIRED and creates a PENDING `PROCESS_REPORT` task. Over hours/days, FIRED watcher rows and unclaimed PENDING PROCESS_REPORT tasks accumulate without cleanup.

**Solution**: Add a compaction hook `_compact_fired_watchers_for_paused(instance_id)` that runs:
1. **On resume** (before `notify_work()`): Deletes FIRED watcher rows for the paused instance that have already been superseded by completed PROCESS_REPORT tasks. This is safe because the report data is already in the `message_queue` table.
2. **Periodically** (optional, future enhancement): A background sweeper that compacts FIRED rows for long-paused instances.

The compaction is safe because:
- FIRED rows are already resolved — they only exist for backward-compat bookkeeping
- The PROCESS_REPORT tasks carry the actual report data in the message_queue
- On resume, the PROCESS_REPORT tasks are the real driver of finalization, not the watcher rows

**Alternative Considered**: Cancel bus watchers but re-register on resume. Rejected because it's complex to reconstruct watcher state and the watchers contain task_id references that may no longer be valid.

---

## Decision 3: Resume Uses `_process_event` Single Finalize Path — with Deterministic Finalize Trigger

**Decision**: Eliminate the direct `complete_job()` call in `resume_processing_job()` (manager.py:2898). Instead, after the graph turn completes, **explicitly trigger the observer's finalize path** via a deterministic finalize-or-defer call that routes through `_process_event`'s transactional bus gate.

**Rationale**: The root cause of the premature completion bug is that `resume_processing_job()` directly calls `complete_job()` after a non-transactional bus check. This bypasses the single finalize path (`_process_event`) which has proper transactional bus gating inside `_finalize_job_db_sync`.

### Critical: Why We CANNOT Just "Let the Lifecycle Event Fire" (reviewer C1)

`_process_event` (job_feedback_observer.py:763) **only fires finalize logic for `status IN (COMPLETED, ERROR)`**. If the resumed graph turn is a **no-op** (all children already reported during pause, checkpoint just re-enters and exits without producing a terminal status), NO lifecycle event fires → `_process_event` never finalizes → **job stays PROCESSING forever**. This would reintroduce the exact zombie state the redesign eliminates.

Additionally, the hard `RuntimeError` at `manager.py:2885` ("DependencyBus is None") lived in the code being removed. The observer's `_process_event` at lines 788-792 **silently `pass`es** when bus is None — no equivalent safety check. We must carry forward this hard-fail safety guarantee.

### The Deterministic Finalize Trigger (Chosen Approach)

After the graph turn in `_resume_processing_background`, **explicitly invoke the observer's finalize-or-defer logic**:

```python
# After the graph turn completes (or no-ops), trigger deterministic finalize.
# This replaces the old direct complete_job() call.
await self._job_feedback_observer._process_resume_finalize(
    instance_id=instance_id,
    job_id=old_job_id,
    result_summary=result.content if result else None,
)
```

Where `_process_resume_finalize` is a new method on the observer that:
1. **Validates the DependencyBus is not None** (carries forward the A9 hard-error from manager.py:2885)
2. Calls the SAME transactional finalize path as `_process_event`:
   - Checks `bus.count_pending_for_target(instance_id)` inside `_finalize_job_db_sync`'s WriteGuardSession
   - If pending > 0: emits `_emit_in_progress` and returns (defer — children still completing)
   - If pending == 0: calls `_finalize_job(COMPLETED)` (finalize via single transaction)
3. Handles both cases: graph turn produced a result AND graph turn was a no-op

This approach **(c)** from the reviewer's options — explicitly calling the finalize logic after the graph turn — is chosen because:
- It guarantees finalization regardless of whether a lifecycle event fires
- It routes through the SAME transactional bus gate as `_process_event`
- It carries forward the hard-error safety check
- It eliminates the TOCTOU race (bus check + finalize in one transaction)

### New Flow
1. Resume transitions job PAUSED → PROCESSING
2. Execution re-enters LangGraph from checkpoint (may produce result OR no-op)
3. After graph turn: **explicitly call** `_process_resume_finalize()` on the observer
4. The observer checks bus pending **inside a transaction** and either defers or finalizes

**Trade-off**: This adds a new method to the observer (`_process_resume_finalize`). It's slightly more coupled than "just let events fire," but it's deterministic and correct.

---

## Decision 4: Task Status PAUSED → PENDING on Resume (Not RUNNING)

**Decision**: When resuming, transition paused tasks from `PAUSED → PENDING` (not directly to RUNNING). Let the WorkerPool re-claim them through `claim_pending_task`.

**Rationale**: Directly setting tasks to RUNNING would bypass the atomic claim mechanism in `claim_pending_task`, which has critical guards:
1. Per-instance serialization (only one RUNNING task per instance)
2. Pause gate (skip paused instances)
3. Cross-system job coordination

By going PAUSED → PENDING, we reuse the safe claim path. The WorkerPool's `notify_work()` (already called in resume) ensures immediate re-claim.

**Alternative Considered**: Directly set to RUNNING and re-spawn the graph task. Rejected because it duplicates the claim logic and reintroduces race conditions.

---

## Decision 5: Migration Strategy — Additive Enum + Backward Compat

**Decision**: The database migration is purely additive — adding the PAUSED enum value. No data transformation of existing rows needed.

**Rationale**: 
- **PostgreSQL**: `JobStatus` and `TaskStatus` are stored as VARCHAR/TEXT columns with app-level validation, not native PG enums. Adding a new value requires no DDL change to the column type.
- **SQLite**: No enum constraints at DB level.
- **Existing PROCESSING jobs**: Remain valid. The crash recovery in Phase 6 will detect PROCESSING jobs on paused instances and transition them to PAUSED.

**Alternative Considered**: Native PG enum type (`CREATE TYPE job_status AS ENUM(...)`). Rejected because the project uses VARCHAR with app-level validation, and changing to native enums would break the dual-driver strategy.

---

## Decision 6: Pause Does NOT Release Job Locks

**Decision**: Consistent with current behavior, pausing a job does NOT release job locks.

**Rationale**: 
1. The job is not cancelled — it's paused and will resume
2. Releasing the lock would allow other jobs to claim the same instance slot
3. On resume, the lock is still held, ensuring no concurrent execution

**Note**: This matches the existing comment in `instance_lifecycle.py:1036-1040`.

---

## Decision 7: Cascade Semantics — Downward Only

**Decision**: Pause/resume cascades downward only (parent → all children), matching existing `get_tree_ids()` traversal.

**Rationale**: The current cascade behavior is already correct for instances. We extend it to jobs/tasks:
- **Pausing parent**: All descendant instances → PAUSED, their jobs → PAUSED, their tasks → PAUSED
- **Pausing child**: Only that child's instance/job/task → PAUSED
- **Resuming parent**: All descendant instances → RUNNING, their jobs → PROCESSING, their tasks → PENDING
- **Resuming child**: Only that child → RUNNING, job → PROCESSING, task → PENDING

**Key clarification**: Resuming a child does NOT resume the parent. The parent remains paused. This matches existing behavior.

---

## Decision 8: Crash Recovery for PAUSED Jobs

**Decision**: On startup, job recovery detects PROCESSING jobs on PAUSED instances and transitions them to PAUSED.

**Rationale**: If a crash occurs while a job is PROCESSING and its instance is PAUSED (the old hack), the restart should reconcile: set the job to PAUSED to match reality. This is a one-time migration during the upgrade.

**Implementation**: In **`daemon/services/job_recovery_service.py:132`** (`JobRecoveryService.recover_on_startup`), add logic at line 132-143 where PAUSED instances currently fall into the "alive" branch (job stays PROCESSING). Add handling to transition PROCESSING → PAUSED for jobs on PAUSED instances during crash recovery.

```python
# In job_recovery_service.py recover_on_startup, line ~132:
# If instance.status == PAUSED and job.status == PROCESSING:
#   Transition job to PAUSED
```

> **⚠️ Corrected location** (reviewer C2): The crash recovery for JOBS is in `job_recovery_service.py:96-156`, NOT in `daemon/api.py:672-803`. The `api.py` code is bus watcher recovery (FIRED-but-unsent watchers), not job recovery.

**Bus Watcher Recovery** (separate concern, reviewer C4): The bus watcher recovery at `api.py:743-760` calls `_get_processing_job_for_instance` which only returns PROCESSING jobs. For PAUSED instances, this lookup returns None → watchers get stamped as "processed" at line 760 → **silently dropped**. This must be fixed: bus watcher recovery must explicitly SKIP PAUSED-instance jobs (leave watchers for resume) rather than stamping them as processed. See Phase 6 Task 8.

This handles the backward-compat case of existing in-flight jobs during upgrade.

---

## Decision 9: `claim_pending_task` Guard Updates

**Decision**: Update the per-instance serialization guard in `claim_pending_task` to exclude PAUSED tasks from blocking.

**Rationale**: Current guard: `AND instance_id NOT IN (SELECT instance_id FROM task WHERE status = :status_running_guard)`. With PAUSED tasks, a PAUSED task should not block a sibling task from being claimed (since the PAUSED task is not actively running). 

**However**: During pause, the instance is PAUSED, so the pause gate (Guard 2) already blocks ALL tasks for that instance. The serialization guard only matters when the instance is RUNNING. So the impact is minimal — PAUSED tasks won't block siblings because paused instances are already filtered.

**Conclusion**: The serialization guard may need `AND status NOT IN ('running', 'paused')` adjustment, but verify during implementation if any edge case arises.

---

## Decision 10: PAUSED → CANCELLED Transition (reviewer W3)

**Decision**: PAUSED jobs CAN be cancelled directly (user terminates a paused instance). Cancelling a PAUSED job:
1. Transitions job `PAUSED → CANCELLED`
2. **Releases job locks** (unlike pause which preserves them)
3. **Cancels preserved bus watchers** via `_cancel_bus_watchers_for()` (restores the old behavior specifically for the terminate path)
4. Transitions tasks `PAUSED → CANCELLED` for the instance subtree

**Rationale**: When a user terminates a paused instance (via `terminate_instance` at `instance_lifecycle.py:922`), the job must move to a terminal state. The key difference from pause:
- **Pause** preserves locks and watchers (job will resume)
- **Cancel/Terminate** releases locks and cancels watchers (job is dead)

**State Transition Rules**:
| From | To | Trigger |
|------|-----|---------|
| `PAUSED` | `CANCELLED` | User terminates paused instance |
| `PAUSED` | `PROCESSING` | User resumes |
| `PAUSED` | `FAILED` | (not supported — must resume first, then fail) |

**Implementation in `terminate_instance`** (`instance_lifecycle.py:922`): Add a branch that handles PAUSED jobs/tasks. Currently `terminate_instance` assumes jobs are PROCESSING. Extend it to:
- Find PAUSED jobs for the instance → transition to CANCELLED
- Find PAUSED tasks → transition to CANCELLED
- Cancel bus watchers (restore `_cancel_bus_watchers_for()` call for terminate path only)
- Release job locks

**Tests**: The 64 cancellation tests (test_cancellation.py + test_cancellation_cascade.py) must verify that PAUSED jobs can be cancelled and that bus watchers are properly cleaned up.
