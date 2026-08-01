# Increment 1 Plan: Turn-Reconciler Named Transitions Migration

Date: 2026-08-01  
Author: plan-creation worker  
Status: Revised v4 (Approver Iteration 002) — Fixing SQL correctness defects in §4 mirrors #3/#4/#5/#6/#8

> **§ REVISION HISTORY.**
> - **v2 (Council Review 2026-08-01):** Addressed 2 critical findings
>   (C1, C2), 4 warnings (W1–W4), and 1 line-citation cleanup (R1).
>   See §13 for the consolidated v2 change log.
> - **v3 (Approver Review 2026-08-01):** Removed the v2 fast-path
>   probe (§4.1) because it only inspected 2 of 8 mirrors; added the
>   WAITING_CHILDREN exception (D13) to the `job_queue_items`
>   reconciliation SQL in Inc 1 so Inc 2 can rely on it; documented
>   the claim-time reconciliation ordering rationale (§5.2); added
>   arbitrary-corruption commands to the Hypothesis state machine
>   (§7). See §13 for the v3 change log and §14 for a summary of
>   what changed vs. v2.
> - **v4 (Approver Iteration 002, 2026-08-01):** Surgical SQL-correctness
>   fixes in §4 mirror table specifications only — no scope or design
>   changes. Issues 1, 2, and 3 (below) were found by the Approver in
>   iteration 2. See §13 for the v4 change log.

## 1. Objective

Implement the first, additive increment of the Turn-Reconciler Named Transitions migration. This increment introduces `TaskRepository.reconcile_turn_mirror(work_id)` as a single-transaction, idempotent, SQL-based reconciliation routine; makes the PostgreSQL-only admission/lock invariant visible on SQLite and other non-PostgreSQL development paths; and establishes a Hypothesis state-machine/property-test harness covering the complete eight-table turn mirror.

The routine initially runs alongside existing guards and application-layer follow-up logic, except that the explicitly identified Resume-cascade UPDATE 4 block is replaced by the reconciler call. Existing behavior must remain unchanged for the existing 404-test baseline while the new routine closes the known orphan paths and provides the foundation for later named-transition migration increments.

## 2. Scope

### In Scope

- Add `TaskRepository.reconcile_turn_mirror(work_id)` in `daemon/repositories/task/repository.py`.
- Use `Task.work_id` as the authoritative correlation axis; use `task.message_id`, `task.id`, and `task.instance_id` only for the explicitly defined secondary relationships.
- Implement one-transaction reconciliation with one initial Task-status snapshot and guarded SQL writes.
- Cover all eight mirror tables:
  `task`, `job_queue_items`, `message_queue`, `job_locks`, `dependency_watchers`, `report_injections`, `instances`, and `job_watchers`.
  The `job_queue_items` handler has a documented exception for
  `instances.status = 'waiting_children'` (see D13, §4 mirror
  table #2, §5 call-site ordering note); the other seven handlers
  treat every terminal Task uniformly.
- Add the Python-side equivalent of the PostgreSQL active-admission/JobLock invariant, raising `InvalidTransitionError` on mismatch.
- Keep PostgreSQL constraint triggers as defense-in-depth.
- Integrate the routine at the six verified call sites:
  `claim_pending_task`, `_resume_cascade_db_sync`, `_pause_cascade_db_sync`, `_finalize_job_db_sync`, `StaleTaskRecovery.recover_stale_tasks`, and `reconcile_drift_states`.
- Replace only Resume UPDATE 4 (`instance_lifecycle.py:3664-4032`) with the reconciler call. Keep `message_queue/predicates.py` and `_post_reconcile_completion_refire` in place.
- Add focused unit/integration tests and Hypothesis tests in `tests/property/test_turn_state_machine.py`.
- Add the directed end-to-end regression scenario in `tests/e2e/test_pause_during_report_turn_then_resume.py`.
- Preserve dual-driver support and avoid SQLite-only SQL such as `rowid`.

> **§ REVISION NOTE (Council C2).** Removed bullet
> "Remove the status-drift warning at `work_resolver.py:692-709`".
> Verified at `daemon/services/work_resolver.py:1082-1098` that the
> F10 status-drift warning was already removed in
> "Phase 4 partial collapse (2026-07-06)"; lines 692-709 now
> contain the docstring of the `_resolve_completion_time` helper
> (a completely unrelated function). There is no longer anything
> to remove. The reconciler is still the authoritative consistency
> mechanism, but there is no obsolete warning left to clean up.

### Out of Scope

- Migrating every existing hand-written guard or cascade update to named transition methods; that belongs to later increments.
- Removing all existing guards, PostgreSQL triggers, or application-layer follow-up behavior.
- Forcing `instances.status` updates from the reconciler. Instance state is tree-scoped and must be verify-and-flag only in this increment.
- Implementing the periodic drift-correction behavior that consumes instance drift flags/logs; the reconciler only records the inconsistency.
- Changing the public job primitive, queue topology, or message dispatch semantics.
- Adding schema migrations or new columns. If implementation discovers that a column is required, stop and obtain a separate migration decision; any future new column must use `_ensure_postgres_columns()` because `.sql` migrations no-op on PostgreSQL.
- Re-introducing the F10 status-drift warning or any equivalent drift-warning diagnostic in `work_resolver.py`.
- Performance tuning beyond transaction correctness and bounded property-test execution.

> **§ REVISION NOTE (Council C2).** Added an explicit out-of-scope
> item forbidding reintroduction of the F10 status-drift warning.
> See §9 success criterion #7 for the corresponding negative
> assertion.

## 3. Exact Files and Functions Touched

### Production files

1. `daemon/repositories/task/repository.py`
   - Add `TaskRepository.reconcile_turn_mirror(work_id)`.
   - Reuse the repository's existing transaction/dialect/error/logging conventions.
   - Add any narrowly scoped private SQL helpers only if they remain inside the repository abstraction.
2. `daemon/services/instance_lifecycle.py`
   - Integrate after the RUNNING→PAUSED UPDATE 2 in `_pause_cascade_db_sync` (verified at lines `3039-3210`).
   - Replace Resume UPDATE 4 (lines `3664-4032`) in `_resume_cascade_db_sync` (function spans `3474-4130`) with one reconciler call, preserving surrounding transaction/order and existing follow-up behavior.
3. `daemon/services/job_feedback_observer.py`
   - Call after Step 1 in `_finalize_job_db_sync` (verified at lines `2761-3421`), before Step 2.
4. `daemon/services/stale_task_recovery.py`
   - Call after `force_cancel_and_schedule_retry` in `StaleTaskRecovery.recover_stale_tasks` (verified at lines `168-385`; the leading underscore in earlier drafts was a transcription error — the actual method is `recover_stale_tasks`).
5. `daemon/services/job_recovery_service.py`
   - Call at the top of `reconcile_drift_states` (verified at lines `488-1090`), after its bail-early check.

> **§ REVISION NOTE (Council C2 / R1).** Removed the entire
> `work_resolver.py` production-file touchpoint (previously item 7
> in this list). The F10 status-drift warning is already gone
> (see C2 in §13). Line-citation cleanup: `_pause_cascade_db_sync`
> verified at `instance_lifecycle.py:3039-3210`,
> `_resume_cascade_db_sync` at `3474-4130`,
> `_finalize_job_db_sync` at `job_feedback_observer.py:2761-3421`,
> `recover_stale_tasks` at `stale_task_recovery.py:168-385` (note:
> no leading underscore — the original plan's `_recover_stale_tasks`
> was stale).

### Test files

- `tests/property/test_turn_state_machine.py`: Hypothesis RuleBasedStateMachine/model and all-eight-table invariant checks.
- `tests/e2e/test_pause_during_report_turn_then_resume.py`: directed pause-during-`process_report` → resume → answer scenario.
- Existing task-repository, lifecycle, recovery, queue, and PostgreSQL trigger test modules: add focused regression cases where the existing test organization requires them.

## 4. `reconcile_turn_mirror(work_id)` Routine

### Contract

- Input is a `work_id` UUID/string identifying the authoritative `task` row.
- Return a small deterministic result suitable for logging/tests, such as `{work_id, found, snapshot_status, updated_counts, drift_flags}`; do not expose driver-specific row-count details as the API contract unless existing repository conventions require it.
- Execute all work in one database transaction.
- Read `task.status`, `task.id`, `task.message_id`, and `task.instance_id` once at transaction start. Treat this as the snapshot.
- Every write is guarded by the snapshot status and the same `work_id`/secondary key. If a concurrent transition changes the Task before a write, the guarded operation affects zero rows; log the race and return without applying a stale snapshot.
- No Python-side read-then-write branching for reconciliation decisions. Python may bind the snapshot and execute SQL statements; SQL `CASE`, `EXISTS`, and guarded predicates determine row changes.
- Re-running the routine with the same state produces no additional semantic changes (idempotent).
- A missing Task is handled as an orphan: mark the corresponding JobItem done with `terminal_reason='orphaned_no_task'` and release its lock; do not fabricate a Task or message lifecycle.

> **§ REVISION NOTE v3 (Approver Review — Issue 1, BLOCKING).** The
> v2 fast-path probe (§4.1) was REMOVED in v3 because it only
> inspected 2 of 8 mirrors (`job_queue_items.admission_state` and
> `job_locks`), allowing orphans in the other 6 to persist whenever
> the admission/lock pair happened to be consistent. The reconciler
> ALWAYS runs all 8 handlers in Inc 1; the per-table guarded `WHERE`
> clauses already provide per-table early-exit at near-zero cost (an
> `UPDATE` whose `WHERE` filters to zero rows is one of the cheapest
> operations a relational engine performs). Adding a higher-level
> skip that fires before all handlers reintroduces the exact bug
> class this migration is designed to kill. The result-shape
> contract drops `fast_path_skipped` accordingly; the per-handler
> counters in `updated_counts` remain the only "did anything change"
> signal.

### Common status classification

Bind a terminal predicate for `completed`, `cancelled`, and `failed`; bind an in-flight predicate for `pending`, `running`, and `paused` according to the existing lifecycle contract. Do not invent additional Task statuses. The discriminator used for terminal JobItem rows must be stable and map Task status to the existing terminal-reason vocabulary (for example, completed/cancelled/failed), with tests asserting the exact project convention.

### SQL-level operations by table

The implementation should use parameterized SQL and dialect-compatible constructs. The following is pseudocode expressing required semantics; adapt syntax to the repository's existing SQLAlchemy/text or query-builder conventions.

#### 1. `task` — authority

```sql
SELECT id, status, message_id, instance_id
FROM task
WHERE work_id = :work_id
FOR UPDATE;
```

The status snapshot is the sole authority for this invocation. The `FOR UPDATE`/equivalent must follow existing SQLite/PostgreSQL compatibility conventions; if SQLite cannot lock rows, the guarded predicates remain mandatory.

#### 2. `job_queue_items`

Link with `job_id = :work_id`.

```sql
UPDATE job_queue_items
SET admission_state = CASE
      WHEN :terminal
           AND NOT EXISTS (
               SELECT 1 FROM instances i
               WHERE i.instance_id = :task_instance_id
                 AND i.status = 'waiting_children'
           )
      THEN 'done'
      ELSE 'active'
    END,
    terminal_reason = CASE
      WHEN :terminal
           AND NOT EXISTS (
               SELECT 1 FROM instances i
               WHERE i.instance_id = :task_instance_id
                 AND i.status = 'waiting_children'
           )
      THEN :terminal_reason
      ELSE terminal_reason
    END,
    failed_at = CASE
      WHEN :task_status IN ('failed', 'cancelled')
           AND NOT EXISTS (
               SELECT 1 FROM instances i
               WHERE i.instance_id = :task_instance_id
                 AND i.status = 'waiting_children'
           )
      THEN COALESCE(failed_at, CURRENT_TIMESTAMP)
      ELSE failed_at
    END
WHERE job_id = :work_id
  AND (:task_exists = false OR EXISTS (
      SELECT 1 FROM task WHERE work_id = :work_id AND status = :snapshot_status
  ));
```

For no Task, set `admission_state='done'`, `terminal_reason='orphaned_no_task'`, and release the associated lock. For a terminal Task, preserve existing terminal timestamps/reasons where idempotency requires it; do not regress a completed item. For in-flight Tasks, set/retain `active` as the project contract requires.

> **§ REVISION NOTE v3 (Approver Review — Issue 2, D13).** Added
> the `WAITING_CHILDREN` exception to the `job_queue_items`
> reconciliation SQL in Inc 1 (not deferred to Inc 2). When
> `instances.status = 'waiting_children'`, the JobItem MUST remain
> `active` even if the backing Task is terminal — the JobItem is an
> intentional semaphore for child-completion correlation (see D13 in
> decisions.md and the corresponding exception documented in the
> Inc 2 plan). The exception is implemented as three correlated
> `NOT EXISTS` clauses against the `instances` table (one each for
> `admission_state`, `terminal_reason`, and `failed_at`); all three
> must agree so a partial transition cannot leak. This means Inc 2
> can rely on Inc 1's reconciler correctly preserving the active
> JobItem for `waiting_children` instances, and the Inc 2 carve-out
> (which deletes the exception under named-transition refactor) is
> the only place that ever transitions a `waiting_children` JobItem
> to `done`. See §8 for the paired unit tests.

#### 3. `job_locks`

Link with `job_id = :work_id`.

```sql
DELETE FROM job_locks
WHERE job_id = :work_id
  AND :terminal
  AND (:task_exists = false OR EXISTS (SELECT 1 FROM task WHERE work_id = :work_id AND status = :snapshot_status));
```

> **§ REVISION NOTE v4 (Approver Iteration 002).** Mirror #3 used a bare `EXISTS` guard without the `(:task_exists = false OR ...)` wrapper. When the Task row is missing entirely, `EXISTS` returns FALSE and the DELETE silently no-ops, allowing the `job_locks` row to survive as an orphan — recreating the exact bug class this migration is designed to kill. Fixed to match mirror #2's pattern.

Leave locks untouched for in-flight Tasks. Missing-Task cleanup also deletes the lock. The implementation must not delete another transition's lock after the Task snapshot has changed.

#### 4. `message_queue`

Link with `message_id = :task_message_id` only after obtaining the Task's message ID; `message_id` is not the primary correlation axis.

```sql
UPDATE message_queue
SET status = 'completed',
    processing_task_id = NULL,
    completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
WHERE message_id = :message_id
  AND :terminal
  AND (:task_exists = false OR EXISTS (SELECT 1 FROM task WHERE work_id = :work_id AND status = :snapshot_status));
```

> **§ REVISION NOTE v4 (Approver Iteration 002).** Mirror #4 used a bare `EXISTS` guard without the `(:task_exists = false OR ...)` wrapper. When the Task row is missing entirely, `EXISTS` returns FALSE and the UPDATE silently no-ops, leaving the `message_queue` row stuck in its old status — recreating the exact bug class this migration prevents. Fixed to match mirror #2's pattern.

> **§ REVISION NOTE (Council W1).** Clarified the
> `processing_task_id` semantics:
>
> - `processing_task_id` is **dead code** in production. No
>   producer in `daemon/` populates it for any message type
>   (verified at `daemon/repositories/message_queue/predicates.py:113-148`,
>   which states: "Production reality:
>   `message_queue.processing_task_id` is [unused]; the actual
>   link key is `message_id` only").
> - The `message_id` column is the authoritative link key for
>   reconciling message_queue rows to Task lifecycle.
> - The `processing_task_id = NULL` write above is **defensive**:
>   it clears any stale reference even though the column is
>   dead code, so that future debugging is not confused by
>   leftover values from earlier code paths. Do NOT remove
>   this defensive write, and do NOT assume it has any
>   production semantic meaning.
> - A future follow-up may physically drop the column in a
>   separate migration; that is explicitly out of scope for
>   Increment 1.

Leave message rows untouched for in-flight Tasks because the processor owns `processing`. Ensure the update closes the pause-cascade omission without changing retry/dead semantics outside the terminal reconciliation contract.

#### 5. `dependency_watchers` (Council C1 — semantics corrected)

Link source rows with `source_task_id = :task_id`. The original
plan referenced a non-existent column `target_task_id`; the
DependencyWatcher schema
(`daemon/repositories/dependency_bus/models.py`) uses
`source_task_id` (child task) + `target_instance_id` (parent
INSTANCE). The corrected reconciliation semantics are
**instance-scoped, not task-scoped**: a pending watcher is
cancelled only when its source Task is terminal AND the target
INSTANCE has no remaining in-flight tasks. The corrected SQL:

```sql
UPDATE dependency_watchers AS w
SET state = 'CANCELLED'
WHERE w.source_task_id = :task_id
  AND w.state = 'PENDING'
  AND (:task_exists = false OR EXISTS (SELECT 1 FROM task WHERE work_id = :work_id AND status = :snapshot_status))
  AND NOT EXISTS (
      SELECT 1 FROM task AS t
      WHERE t.instance_id = w.target_instance_id
        AND t.status IN ('pending', 'running', 'paused')
  );
```

> **§ REVISION NOTE v4 (Approver Iteration 002).** Mirror #5 used a bare `EXISTS` guard without the `(:task_exists = false OR ...)` wrapper. When the source Task row is missing entirely, `EXISTS` returns FALSE and the UPDATE silently no-ops, leaving a `PENDING` watcher orphaned in `dependency_watchers`. Fixed to match mirror #2's pattern. Note: the second `NOT EXISTS` (target-instance-no-in-flight-tasks) is the D10 instance-scoped predicate and is intentionally untouched — only the Task-snapshot guard needed the missing-Task fix.

Rationale:

- A parent INSTANCE registers a watcher against a CHILD
  task id (`source_task_id`) and is itself identified by
  `target_instance_id`.
- The watcher should remain `PENDING` as long as the parent
  instance has any in-flight work (because at least one of those
  in-flight tasks may itself be the eventual FollowUp target or
  may depend on this child).
- The watcher is eligible for `CANCELLED` only when the source
  child task is terminal AND the target parent instance is
  fully drained (no `pending`/`running`/`paused` tasks for that
  instance).
- This is the existing semantics used by the DependencyBus
  cancellation-scan path; the reconciler must mirror it, not
  invent a new task-level predicate.

> **§ REVISION NOTE (Council C1 — critical).** Replaced the
> non-existent `w.target_task_id` reference with
> `w.target_instance_id` and rewrote the `EXISTS` subquery from
> "target task is terminal" to "target instance has NO in-flight
> tasks" (instance-scoped semantics). See §7 for the corresponding
> D10 invariant update and §8 for the unit-test description.

#### 6. `report_injections`

Link through `report_message_id` to the relevant `message_queue.message_id` and reconcile the injection to the terminal state matching the report Task lifecycle. Pending/processing injection rows cannot remain orphaned after the backing Task is terminal or gone; already-terminal rows must be preserved.

```sql
UPDATE report_injections
SET state = 'TASK_DELIVERED'
WHERE report_message_id = :task_message_id
  AND state = 'PENDING'
  AND (:task_exists = false OR EXISTS (SELECT 1 FROM task WHERE work_id = :work_id AND status = :snapshot_status));
```

> **§ REVISION NOTE v4 (Approver Iteration 002).** Mirror #6 was a TODO with no concrete SQL ("Define the exact terminal state mapping"). Replaced with the deployed implementation's SQL (verified at `daemon/repositories/task/repository.py:677-694`): `state = 'TASK_DELIVERED'` is the terminal state for a report injection whose backing Task has completed or is gone. The guard uses the corrected `(:task_exists = false OR ...)` pattern consistent with the Issue-1 fixes for mirrors #3/#4/#5.

#### 7. `instances` — soft reconciliation only

Link with `instance_id = :task_instance_id`. Do not update `instances.status` merely because one Task is terminal.

```sql
SELECT 1
FROM task
WHERE instance_id = :instance_id
  AND status NOT IN ('completed', 'cancelled', 'failed')
LIMIT 1;
```

If no in-flight task remains and the instance is still `running`, emit a structured drift log/flag using existing logging conventions. This result is diagnostic input for a later periodic correction; it is not a forced state transition.

#### 8. `job_watchers`

Link with semantic `job_id = :work_id`. Watchers are deleted only when the Task row is completely gone (truly dangling orphan). A terminal Task may have a retry child with migrated watchers; deleting watchers on terminal would break retry correlation, so `terminal` status alone is not the trigger — only a missing Task row is.

```sql
DELETE FROM job_watchers
WHERE job_id = :work_id
  AND NOT EXISTS (SELECT 1 FROM task WHERE work_id = :work_id);
```

> **§ REVISION NOTE v4 (Approver Iteration 002).** Mirror #8 was prose only ("delete only dangling watcher subscriptions"). Replaced with the deployed implementation's SQL (verified at `daemon/repositories/task/repository.py:728-738`). **CRITICAL SEMANTIC NOTE:** this is deliberately DIFFERENT from every other mirror. It uses `NOT EXISTS` (Task row gone entirely) — NOT `terminal` status and NOT `(:task_exists = false OR EXISTS(snapshot))`. Rationale: a terminal Task may have a retry child with migrated watchers; deleting watchers on terminal would break retry correlation. Watchers are only deleted when the Task row is completely gone. Tests must assert that a terminal-but-present Task does NOT delete its `job_watchers` row.

### Invariant check within the transaction

After the guarded reconciliation statements, execute a Python-side-equivalent invariant query for the relevant JobItem:

```sql
SELECT
  (jqi.admission_state = 'active') AS is_active,
  EXISTS (SELECT 1 FROM job_locks jl WHERE jl.job_id = jqi.job_id) AS has_lock
FROM job_queue_items jqi
WHERE jqi.job_id = :work_id;
```

If `is_active != has_lock`, raise the existing `InvalidTransitionError` (import from `daemon.services.job_state_machine`; do not create a duplicate error type). Roll back the transaction and include `work_id`, admission state, and lock presence in the diagnostic message. Keep the PostgreSQL constraint trigger enabled; this check is an intentionally redundant defense so SQLite exposes the same invariant failure during development and tests.

## 5. Six Call-Site Integrations

Each integration must document transaction ownership and avoid nested commits. Where the caller already owns a transaction, invoke the repository routine through the same connection/session or provide the repository's established transaction-aware form.

| Site | Integration | Before state | After state / ordering requirement |
|---|---|---|---|
| Claim: `claim_pending_task` (`task/repository.py:493-992`) | Invoke after the existing `with engine.begin()` commits and immediately before return. | Task has just been admitted/claimed; JobItem, lock, and message mirrors may have been changed by claim SQL. | Reconciler sees committed Task snapshot and normalizes mirrors; claim result/return behavior is unchanged. |
| Resume: `_resume_cascade_db_sync` (`instance_lifecycle.py:3474-4130`) | Replace UPDATE 4 (`:3664-4032`) with a reconciler call; preserve surrounding cascade and `_post_reconcile_completion_refire` (`instance_lifecycle.py:3302`). | Resume has transitioned the relevant Task/tree state and legacy block would perform dialect-specific mirror updates. | One reconciler owns that block's mirror normalization; no duplicate UPDATE 4 remains; later application follow-up still runs. |
| Pause: `_pause_cascade_db_sync` (`instance_lifecycle.py:3039-3210`) | Invoke after UPDATE 2 (RUNNING→PAUSED). | Task is paused; current path may leave `message_queue` and other mirrors stale. | Reconciler performs the same additive normalization and closes Bug B structurally; existing guards remain. |
| Finalize: `_finalize_job_db_sync` (`job_feedback_observer.py:2761-3421`) | Invoke after Step 1 job transition and before Step 2. | Finalization has selected/committed the Task transition but subsequent feedback work has not started. | All terminal mirrors are reconciled before Step 2; a concurrent transition is detected by guarded row counts. |
| Timeout: `StaleTaskRecovery.recover_stale_tasks` (`stale_task_recovery.py:168-385`) | Invoke after `force_cancel_and_schedule_retry` (defined at `task/repository.py:2454`). | Stale Task has been force-cancelled/retry-scheduled; lock/admission/message mirrors may lag. | Cancel/retry-related terminal mirror state is normalized without changing retry scheduling ownership. |
| Periodic sweep: `reconcile_drift_states` (`job_recovery_service.py:488-1090`) | Invoke at the top after the existing bail-early check. | Recovery sweep has not yet applied its other drift handling. | Reconciler is the first consistency pass; existing recovery logic remains available for follow-up and soft instance drift. |

For every call site add tests proving the call is made exactly in the stated ordering and that existing return values/exceptions remain compatible.

> **§ REVISION NOTE (Council R1).** Refreshed all six call-site
> line citations against the current codebase. The most material
> correction: the stale-task recovery method is named
> `recover_stale_tasks` (no leading underscore) and spans lines
> `168-385` in `stale_task_recovery.py`; the previous plan's
> `_recover_stale_tasks` at `165-277` was both misnamed and
> misranged.

### 5.1 Exception Propagation Per Call Site (Council W2)

The reconciler can raise `InvalidTransitionError` from the §4.5
invariant check. The contract for each call site is fixed; do NOT
let the exception propagate uniformly — different sites have
different abort-versus-continue semantics.

| Call site | On `InvalidTransitionError` | Rationale |
|---|---|---|
| Claim (`claim_pending_task`) | **CATCH, log, return success.** | A mirror desync is a reconciliation failure, not a claim failure. Blocking the claim would deadlock the system, because the claim is the only path that can eventually retry the reconciliation. The reconciler's correction already ran; the exception is diagnostic. Log at WARNING with `work_id`, `admission_state`, `has_lock`. |
| Resume cascade (`_resume_cascade_db_sync`) | **Let it propagate.** | The cascade runs inside a `WriteGuardSession`. An invariant violation means the transition is structurally invalid; the cascade must abort so the caller sees the failure and can roll back the surrounding resume transaction. Do NOT catch. |
| Pause cascade (`_pause_cascade_db_sync`) | **Let it propagate.** | Same rationale as Resume: cascade-internal integrity failure must surface to the cascade caller. |
| Finalize (`_finalize_job_db_sync`) | **CATCH, log, proceed with finalization.** | The job is completing; blocking it would orphan the JobItem in an in-flight state. Log at WARNING and continue — the next call site (or a follow-up sweep) will catch the desync. |
| Timeout (`StaleTaskRecovery.recover_stale_tasks`) | **CATCH, log, proceed with force-cancel path.** | The stale task is being recovered. Blocking recovery would leave the task stuck (worker is presumed dead). Log at WARNING and continue. |
| Periodic sweep (`reconcile_drift_states`) | **CATCH, log, continue sweep to the next work_id.** | The sweep is best-effort by design; one work_id's failure must not abort the whole sweep. The bail-early at the top of the function remains the only global short-circuit. |

> **§ REVISION NOTE (Council W2).** Added §5.1 — the
> exception-propagation contract per call site. Each call site has
> a documented catch/propagate decision and rationale. Reviewers
> should verify the catch sites emit a structured WARNING log with
> `work_id` + invariant fields so desyncs are diagnosable in
> production telemetry.

### 5.2 Claim-time reconciliation ordering — why the guard runs first (Approver Issue 3)

**VERIFIED IN CODEBASE.** The cross-system guard runs INSIDE the
claim SQL at `repository.py:1060-1180` (single-statement
`UPDATE task SET status='running' WHERE id = (SELECT id FROM task
WHERE status='pending' AND ... AND instance_id NOT IN (cross-system
guard subquery))`). The reconciler is called AFTER the
`with engine.begin()` transaction commits (§5, call site 2a —
"Claim: `claim_pending_task`").

If an orphaned JobItem blocks the claim, the inner `SELECT` returns
no rows, the claim returns `None`, and the reconciler NEVER FIRES
on that blocked attempt. The orphan persists until the next claim
attempt OR another reconciler call site corrects it.

**Decision (Option b): the reconciler at claim time runs AFTER the
guard and CANNOT unblock a blocked claim.** This is acceptable and
is by design, because:

1. The periodic sweep (`reconcile_drift_states`, call site 2f)
   runs every N seconds and corrects orphans for ALL active
   JobItems whose backing Task is terminal.
2. The reconciler at resume/pause/finalize/timeout (call sites
   2b–2e) fires on EVERY transition — these are the moments
   orphans are created. An orphan introduced by one of these
   cascades is corrected by the same cascade's reconciler call
   within the same transaction.
3. By the time a claim query runs, the orphan has already been
   corrected by one of:
   - the cascade that created it (resume/pause/finalize/timeout
     reconciler), OR
   - the periodic sweep (within N seconds of the orphan's
     creation).

**Evidence chain.** Orphan created → cascade's reconciler call
fixes it immediately → if the cascade didn't fire (crash), periodic
sweep catches it within N seconds → claim sees clean state.

**Narrow window (acceptable).** Between orphan creation (crash)
and the next periodic sweep, a claim could see the orphan and
block. This is acceptable because:
- The claim will retry on the next `notify_work` cycle.
- The periodic sweep interval (N seconds) is short.
- The claim blocking is NOT a deadlock — it is a temporary stall
  that self-resolves when the periodic sweep runs.

**Hard dependency on Inc 2.** Inc 2's carve-out deletion (which
removes the WAITING_CHILDREN exception under named-transition
refactor, see Issue 2 / D13) is ONLY safe because the reconciler
runs on ALL other paths (resume, pause, finalize, timeout,
periodic sweep). If ANY path skips the reconciler, orphans
could survive to claim time and block indefinitely. Implementation
must therefore verify, as part of the Inc 1 exit gate, that each
of the six call sites still invokes `reconcile_turn_mirror` after
this revision lands. See §12 step 5 for the integration
verification and §9 success criterion #11 for the
exception-propagation coverage.

**No changes to claim-time ordering.** The reconciler continues
to run AFTER the claim transaction commits at call site 2a
(Claim). The §5.1 catch-and-log contract for Claim is the correct
post-claim behavior because, by the time the reconciler runs, the
claim has already succeeded and the reconciler is normalizing the
mirrors for a Task that was just admitted. If the claim is
blocked by an orphan, the reconciler is a no-op (zero rows
affected) and the periodic sweep / next cascade's reconciler will
correct the orphan.

> **§ REVISION NOTE v3 (Approver Review — Issue 3, BLOCKING).**
> New §5.2 documents the claim-time reconciliation ordering
> rationale. The reconciler at call site 2a (Claim) runs AFTER the
> cross-system guard and CANNOT unblock a blocked claim; this is
> Option (b) and is acceptable because (i) every transition
> cascade runs the reconciler immediately, (ii) the periodic
> sweep corrects orphans within N seconds, and (iii) any
> crash-induced orphan self-resolves on the next claim attempt
> after the sweep. The narrow window between orphan creation
> and the next sweep is documented as a temporary stall, not a
> deadlock. The Inc 2 carve-out deletion is conditional on this
> ordering remaining correct — any future code that introduces a
> new claim path or skips the reconciler on an existing path
> MUST be flagged in code review as a violation of this
> contract.

## 6. Phase 5 — PostgreSQL-Only Invariant Visibility

1. Identify the canonical PostgreSQL trigger definition and the repository's `InvalidTransitionError` import path (`daemon.services.job_state_machine.InvalidTransitionError`).
2. Implement the invariant query/check inside `reconcile_turn_mirror`, after mirror updates but before commit.
3. Raise `InvalidTransitionError` for both mismatch directions (`active` without lock and lock without active), unless the schema explicitly permits a documented transitional exception. Do not silently repair a mismatch in the checker; the reconciler's SQL must make the normal state consistent, and the exception must reveal races/bugs.
4. Add PostgreSQL tests proving trigger enforcement remains active.
5. Add SQLite/non-PostgreSQL tests proving the Python check raises the same error for the same invalid fixture.
6. Verify no status-drift warning has been reintroduced in `work_resolver.py`. The F10 drift warning was removed in the 2026-07-06 Phase 4 collapse (see `work_resolver.py:1082-1098`); the reconciler must not re-introduce it. The CI assertion is a static check that `work_resolver.py` contains no drift-warning code (no `drift`, no `F10`, no `status-drift` substring in any commit after this increment's baseline tag).
7. Record in test comments that PostgreSQL is the primary test database and that SQLite coverage exists specifically to ensure trigger-equivalent visibility, not as permission to use SQLite-only SQL.

> **§ REVISION NOTE (Council C2).** Replaced the original item 6
> (which asked to remove a warning at `work_resolver.py:692-709`
> that was already gone) with a negative assertion: a static CI
> guard that the warning is not reintroduced.

## 7. Phase 9 — Property Tests

### Model and commands

Create a Hypothesis `RuleBasedStateMachine` in `tests/property/test_turn_state_machine.py` with one or more generated turns/instances and a model state per turn:

`not_created → pending → running → paused → completed/cancelled/failed`, including retry paths back to `pending` where the production lifecycle permits them.

Commands:

- `BEGIN_TURN`
- `CLAIM_TURN`
- `SUSPEND_TURN`
- `RESUME_TURN`
- `COMPLETE_TURN`
- `ABORT_TURN`
- `RETRY_TURN`
- `CORRUPT_MIRROR(mirror_table, corruption_type)` — NEW in v3
  (Approver Issue 5). Injects an inconsistency into a SINGLE
  mirror table while leaving the other seven consistent. The
  corruption represents a partial-crash scenario where the
  transaction that produced the inconsistency did not commit
  cleanly. The state machine's invariant suite (§7 invariants)
  must then pass after `reconcile_turn_mirror(work_id)` runs,
  proving the reconciler corrects the corruption.

Each lifecycle command must call the real repository/service
transition where practical, then re-run
`reconcile_turn_mirror(work_id)`. If a command intentionally
creates a race/invalid transition, assert the canonical exception
rather than weakening the model.

> **§ REVISION NOTE v3 (Approver Review — Issue 5, BLOCKING).**
> Added `CORRUPT_MIRROR(mirror_table, corruption_type)` as a
> first-class command in the Hypothesis state machine. The v2
> state machine executed valid lifecycle commands then re-ran
> reconciliation; that path cannot detect a single-table orphan
> in a mirror the lifecycle commands never corrupt. The new
> command injects arbitrary inconsistency into one of the 8
> mirror tables, which exercises the reconciler's per-handler
> guards and proves that ALL 8 handlers run on every invocation
> (would have caught Issue #1 / v2 fast-path skipping). See
> directed fuzz scenarios below for the specific corruption
> seeds.

### Invariants after every transition

1. **No double-admit:** no `instance_id` has more than one `running` Task.
2. **No orphan mirrors:** for every terminal Task, verify all eight mirror tables. This must include:
   - JobItem is terminal/done or absent with the documented orphan reason.
   - Message row is terminal/completed or absent, with processing ownership cleared when required.
   - Job lock is absent.
   - **D10 (Council C1 update).** Eligible dependency watchers are `CANCELLED` only when their source Task is terminal AND the target parent INSTANCE has no remaining in-flight tasks. Watchers whose target instance still has `pending`/`running`/`paused` tasks must remain `PENDING`. Test fixtures must cover both branches: (a) target instance drained → watcher `CANCELLED`; (b) target instance still in-flight → watcher remains `PENDING`.
   - Report injections are terminal/reconciled or absent as specified.
   - Instance row is checked for soft drift without asserting a forced per-turn status.
   - Job watcher subscriptions are cleaned when dangling.
   The test should maintain an explicit `MIRROR_TABLES = 8` coverage registry and fail if a table-specific assertion is removed or omitted.
3. **No permanent deadlock:** every pending Task whose instance is not paused/terminated is claimable by `claim_pending_task` within a bounded number of attempts.
4. **Mirror consistency:** `job_queue_items.admission_state='active'` iff an in-flight Task with matching `work_id` exists and a corresponding `job_locks` row exists.

> **§ REVISION NOTE (Council C1).** Invariant #2's dependency-watcher
> sub-assertion (D10) now specifies instance-scoped semantics. The
> previous "target task is also terminal" wording was a stale
> transcription of the (non-existent) `target_task_id` schema. Test
> fixtures must cover both branches explicitly.

### Directed fuzz scenario

Add a deterministic/targeted Hypothesis example for:

`BEGIN_TURN → CLAIM_TURN → process_report turn → SUSPEND_TURN during report processing → RESUME_TURN → answer arrives → COMPLETE_TURN`.

Use a fixed regression seed/example once the failing sequence is reproduced. Assert no stale `message_queue.processing_task_id`, no active JobItem without a lock, no dangling report injection, no dangling job watcher, and successful answer delivery after resume.

#### Corruption scenarios (Approver Issue 5)

In addition to the lifecycle sequence above, the state machine
MUST include directed fuzz examples that inject arbitrary
single-table corruption, simulating partial-crash scenarios. Each
scenario follows the same shape:

```
[setup a consistent Task + JobItem + message_queue + lock + ...]
CORRUPT_MIRROR(<table>, <corruption_type>)
[assert invariant violation is present in the corrupted table only]
reconcile_turn_mirror(work_id)
[assert all 8 mirror tables are consistent — not just the corrupted one]
```

The required directed scenarios:

1. **Admission/lock consistent, `message_queue` stale.** Setup:
   Task in terminal status, JobItem `admission_state='done'`,
   `job_locks` row absent (consistent), but `message_queue.status='processing'`.
   Run reconciler. Assert `message_queue` is corrected to
   `status='completed'` with `processing_task_id = NULL`. **This
   scenario would have caught v2 Issue #1**: a fast-path probe
   that inspects only `job_queue_items` + `job_locks` would skip
   the sweep and the `message_queue` orphan would persist.
2. **Admission consistent, `dependency_watchers` stale.** Setup:
   Task terminal, JobItem done, `dependency_watchers` row with
   `state='PENDING'` where the source Task is terminal AND the
   target INSTANCE has no remaining in-flight tasks. Run
   reconciler. Assert watcher is cancelled.
3. **`dependency_watchers` stale but target instance still
   in-flight.** Setup: as #2 but target instance has at least one
   in-flight Task. Run reconciler. Assert watcher remains
   `PENDING`. (Mirrors the D10 paired unit tests in §8.)
4. **Single-table corruption for each of the 8 tables.** For each
   of `task`, `job_queue_items`, `message_queue`, `job_locks`,
   `dependency_watchers`, `report_injections`, `instances`,
   `job_watchers`: inject the "wrong" state while all others are
   correct. Run reconciler. Assert ONLY the targeted table is
   corrected, and the others are unchanged. This proves each
   handler's `WHERE` clause is correctly scoped to its own table
   and that no handler has a write that touches another mirror.
5. **Multi-table corruption.** Inject inconsistency into 3+ tables
   simultaneously. Run reconciler. Assert all corrected tables
   are consistent and the untouched tables remain unchanged.
6. **`waiting_children` JobItem exception (D13).** Setup:
   terminal Task + active JobItem + `instances.status='waiting_children'`.
   Run reconciler. Assert JobItem stays `active`. (Cross-validates
   the §4 mirror #2 unit tests with the property harness.)

**Invariant after corruption + reconcile (NEW in v3).** After
ANY `CORRUPT_MIRROR` command followed by
`reconcile_turn_mirror(work_id)`, the test MUST assert that ALL 8
mirror tables are consistent — not just the table the corruption
targeted. This invariant is the direct regression test for Issue #1
and any future regression that reintroduces a fast-path skip
based on a subset of mirrors.

#### Deterministic test hooks (Council W3)

The directed scenario MUST be reproducible without timing-based
flakiness. Implementation must satisfy these requirements:

1. **No `time.sleep`-based pacing.** The scenario fires the pause
   at a deterministic program point, not after a wall-clock delay.
2. **Use existing test infrastructure hooks.** The existing test
   harness already provides barriers/hooks for several lifecycle
   boundaries. Use whichever hook fires at (or just before) the
   report-processing boundary to inject the pause synchronously.
3. **If no existing hook covers the exact "pause fires during
   `process_report`" boundary**, add a test-specific hook in
   `process_report` (or its closest test seam) that calls a
   registered test callback at the report-processing boundary. The
   hook must be guarded by a test-only environment flag
   (`ENSEMBLE_TEST_PAUSE_HOOK=1`) so production behavior is
   unchanged.
4. **Document the hook contract** in the test file's module
   docstring: which boundary the hook fires at, how the test
   registers the callback, and how the test unregisters it on
   teardown to avoid leaking into other tests.
5. **The same hook must be usable from the property test and from
   the E2E test**, so a fix in the directed scenario can be
   cross-validated between the two suites.

> **§ REVISION NOTE (Council W3).** Added the deterministic-test-hook
> contract for the directed pause-during-report scenario. Timing
> sleeps are explicitly forbidden; a test-only hook on the
> report-processing boundary is the canonical mechanism.

## 8. Test Strategy

### Baseline protection

- Run all 404 existing tests before changes and record the command/database configuration.
- Run the same suite after each logical integration group and at final completion.
- Run against PostgreSQL as the primary environment. Run the focused SQLite suite to validate portable SQL and Python-side invariant visibility.
- Confirm no SQLite-only syntax (`rowid`, SQLite-specific timestamp behavior, or incompatible locking syntax) is introduced.

### New focused coverage

- Repository unit tests for each of the eight table rules, including terminal, in-flight, missing-Task, and idempotent rerun cases.
- **Dependency-watcher unit tests (Council C1).** Two paired test cases must be added under
  `tests/repositories/task/test_dependency_watcher_reconcile.py` (or the existing dependency-watcher test module):
  1. Insert a watcher with `source_task_id` = terminal Task and `target_instance_id` = an instance that still has an in-flight Task (any of `pending`/`running`/`paused`). Call `reconcile_turn_mirror`. Assert watcher remains `PENDING`.
  2. Insert a watcher with `source_task_id` = terminal Task and `target_instance_id` = an instance with NO in-flight Tasks. Call `reconcile_turn_mirror`. Assert watcher transitions to `CANCELLED`.
  Both tests must run on PostgreSQL and SQLite.
- Concurrency/race tests that mutate Task status between snapshot and guarded update and assert zero stale writes plus diagnostic logging.
- Invariant tests for active JobItem/JobLock mismatch on both PostgreSQL and SQLite.
- **WAITING_CHILDREN JobItem exception tests (Approver Issue 2, D13).** Two paired test cases must be added under `tests/repositories/task/test_job_queue_items_reconcile.py` (or the existing `job_queue_items` test module):
  1. Insert a Task in terminal status (`completed`/`cancelled`/`failed`) + an active JobItem with `admission_state='active'` + an `instances` row whose `status = 'waiting_children'` and `instance_id` matches `task.instance_id`. Call `reconcile_turn_mirror`. Assert JobItem `admission_state` remains `active` (NOT transitioned to `done`), `terminal_reason` is unchanged, and `failed_at` is unchanged.
  2. Insert a Task in terminal status + an active JobItem + an `instances` row whose `status = 'running'` and `instance_id` matches `task.instance_id`. Call `reconcile_turn_mirror`. Assert JobItem `admission_state` transitions to `done` with the correct `terminal_reason`.
  Both tests must run on PostgreSQL AND SQLite. The Postgres trigger must not fire on the `waiting_children` case (verify with a test that asserts no `InvalidTransitionError` is raised — the WAITING_CHILDREN exception is a documented carve-out from the active/lock invariant because the JobItem is intentionally retained).
- **Arbitrary-corruption property tests (Approver Issue 5).** Covered in §7; the directed fuzz scenario in §7 includes corruption injection.
- One ordering/integration test per call site, including proof that Resume no longer executes the old UPDATE 4 block.
- Soft-reconciliation tests proving a running instance is logged/flagged but not overwritten.
- Dependency watcher tests distinguishing terminal and in-flight targets (as above; supersedes the previous one-line mention).
- Report injection and job watcher orphan cleanup tests.
- Hypothesis state-machine tests with bounded examples and reproducible seed/example for the directed pause/report/resume sequence.
- E2E answer-delivery regression test at `tests/e2e/test_pause_during_report_turn_then_resume.py`.

### Verification commands

Use the repository's standard PostgreSQL test command and targeted commands for repository, lifecycle/recovery, property, and E2E tests. The final verification record must include the exact commands, database backend, total tests passed, and any intentionally skipped tests. A passing result requires the existing 404-test baseline plus all new tests.

## 9. Success Criteria

| # | Criterion | Measurement | Threshold |
|---|---|---|---|
| 1 | Reconciler exists at the required repository location and is callable by authoritative `work_id`. | Static inspection plus repository tests. | Method accepts `work_id`, uses Task as authority, and has no message-id-only path. |
| 2 | All eight mirror tables are covered. | Code review checklist and property-test coverage registry. | Eight explicit table handlers/assertions; tests fail if any handler is omitted. |
| 3 | Reconciliation is atomic and idempotent. | Transaction and repeated-call tests. | One transaction per invocation; second identical call causes no semantic changes. |
| 4 | Concurrent Task transition cannot cause stale mirror writes. | Race test with status change between snapshot and guarded SQL. | Guarded row count is zero, stale write is not applied, and invocation logs/returns race. |
| 5 | Six call sites are integrated in the required order. | Focused integration tests and source inspection. | All six call sites pass; Resume UPDATE 4 is removed/replaced; existing follow-up paths remain. |
| 6 | Admission/lock invariant is visible on all supported test backends. | PostgreSQL trigger test and SQLite Python-check test. | Same invalid state raises `InvalidTransitionError` on both backends. |
| 7 | **Negative assertion (Council C2):** No status-drift warning has been reintroduced in `work_resolver.py`. | Static CI check (grep for `drift`, `F10`, `status-drift` against `work_resolver.py`) plus test fixture that scans the file at suite start. | The static check passes; the file contains no drift-warning code; the F10 comment block at `work_resolver.py:1082-1098` continues to read "gone — The previous code logged when a dropped turn's status disagreed with the shadowing JobItem's status." |
| 8 | Property harness validates lifecycle invariants. | Hypothesis state-machine run. | Every generated transition re-runs reconciliation and all four invariants pass, including all eight-table orphan coverage. |
| 9 | Directed pause/report/resume regression is fixed. | E2E test using deterministic hooks. | Answer arrives after resume with no stale processing owner, lock/admission mismatch, report injection orphan, or dangling watcher; the test is reproducible without `time.sleep`. |
| 10 | Regression baseline remains green. | Full existing suite on PostgreSQL, plus focused portability suite. | All 404 existing tests pass; no new test is left failing or xfailed without documented reason. |
| 11 | **Exception propagation is per-site (Council W2).** | Per-call-site tests with a forced `InvalidTransitionError`. | Claim/Finalize/Timeout/Sweep catch-and-log; Resume/Pause propagate. Verified by per-site unit tests with a mocked invariant failure. |
| 12 | **WAITING_CHILDREN JobItem exception (Approver Issue 2, D13).** | Two paired unit tests covering the `job_queue_items` exception. | (a) Terminal Task + active JobItem + `instances.status='waiting_children'` → reconciler leaves JobItem `active`; (b) Terminal Task + active JobItem + `instances.status='running'` → reconciler transitions JobItem to `done`. Both tests run on PostgreSQL and SQLite. |
| 13 | **Property tests inject arbitrary corruption (Approver Issue 5).** | Hypothesis state machine with `CORRUPT_MIRROR` commands. | After any corruption seed runs the reconciler, all 8 mirror tables are consistent — not only the table the corruption targeted. The "admission consistent + message_queue stale" scenario is a directed example that would have caught Issue #1. |

> **§ REVISION NOTE v3 (Approver Review).** Criterion #12 was
> REMOVED in v3 (fast-path probe deleted in Issue 1); renumbered
> success criteria: former #11 became the entry above; new #12
> documents the WAITING_CHILDREN exception (Issue 2); new #13
> documents the property-test corruption coverage (Issue 5).
>
> **§ REVISION NOTE (Council C2 / W2 / W4).** Criterion #7 was
> rewritten as a negative assertion (no drift warning
> re-introduction) instead of the original positive removal
> criterion. Added criteria #11 and #12 for the W2 exception
> propagation and W4 fast-path probe contracts.

## 10. Rollback Plan

This increment is additive and can be reverted cleanly:

1. Revert the reconciler method and its repository tests.
2. Restore the Resume UPDATE 4 block exactly as it was before the increment.
3. Remove the six call-site invocations.
4. Remove the Python-side invariant tests, Hypothesis state machine, and directed E2E test if they depend exclusively on the reverted method.
5. Run the 404-test baseline on PostgreSQL to confirm restoration.

No schema rollback is expected because Increment 1 adds no columns or migrations. If implementation unexpectedly requires schema changes, do not apply them as part of this plan; split them into a separately approved migration.

> **§ REVISION NOTE v3 (Approver Review — Issue 1).** Rollback
> item 3 no longer references the §4.1 fast-path probe because the
> probe was deleted in v3 (Issue 1); there is nothing extra to
> roll back on that axis.
>
> **§ REVISION NOTE (Council C2).** Removed the original rollback
> item 3 ("restore the status-drift warning only if rollback
> validation requires the old diagnostic behavior") because the
> warning has been gone since 2026-07-06 — there is nothing to
> restore. Added explicit removal of the §4.1 fast-path probe as
> part of rollback, since it is a behavior change that needs to
> come out with the reconciler.

## 11. Dependencies

None. Increment 1 is intended to ship first and supplies the reconciler, invariant visibility, and property-test harness required by later named-transition increments. It depends only on existing repository transaction helpers, lifecycle status constants, queue schemas, PostgreSQL trigger definitions, and the canonical `InvalidTransitionError` already present in the codebase.

## 12. Implementation Sequencing and Exit Gate

1. Inspect existing repository transaction/dialect helpers, table schemas, status constants, trigger definitions, and error imports.
2. Add the repository routine (no fast-path probe — always runs all 8 handlers) and focused table-rule tests without changing call sites.
3. Add the Python invariant check and backend-specific tests.
4. Verify (do not "remove") that no drift-warning exists in `work_resolver.py`; add the static CI guard from §9 criterion #7.
5. Integrate the six call sites with the §5.1 exception-propagation contract, replacing Resume UPDATE 4 while retaining the two explicitly coexisting point-fixes.
6. Add the property state machine (with the D10 instance-scoped watcher invariant and the D13 WAITING_CHILDREN JobItem exception) and directed E2E test (with deterministic hooks).
7. Run full PostgreSQL baseline and focused SQLite portability tests.

Increment 1 is ready for review only when the reconciler's eight-table coverage is explicit, the WAITING_CHILDREN exception is correctly preserved (Issue 2 / D13), the exception propagation contract is verified at every call site (§5.1), the claim-time ordering rationale is documented (§5.2 — Issue 3), the directed pause/report/resume path passes via deterministic hooks and survives arbitrary-corruption injection (Issue 5), the negative CI guard against drift-warning re-introduction is green, and all 404 existing tests remain green.

> **§ REVISION NOTE v3 (Approver Review).** Steps 2 and the exit
> gate were rewritten in v3:
> - Step 2 makes explicit that there is no fast-path probe; the
>   reconciler always runs all 8 handlers (Issue 1).
> - Step 6 calls out the WAITING_CHILDREN JobItem exception as a
>   required invariant of the state machine (Issue 2 / D13).
> - Exit gate adds the §5.2 claim-time ordering rationale and the
>   arbitrary-corruption property-test coverage (Issues 3 and 5).
>
> **§ REVISION NOTE (Council C2 / W3 / W4).** Step 4 rewritten
> from "Remove the resolver warning" to the C2 negative assertion.
> Steps 2, 5, and 6 updated to reflect the W4 probe, W2
> exception contract, and W3 deterministic hooks.

## 13. Revision Change Log

### v2 (Council Review 2026-08-01)

| ID | Severity | Section | Fix |
|---|---|---|---|
| **C1** | Critical | §4 mirror table #5 (dependency_watchers) | Replaced non-existent `target_task_id` with `target_instance_id`. Rewrote `EXISTS` subquery from "target task is terminal" to "target instance has NO in-flight tasks" (instance-scoped semantics, matching DependencyBus cancellation-scan). Updated §7 D10 invariant. Added paired unit-test cases in §8. |
| **C2** | Critical | §2 In Scope, §3 Files Touched, §6 item 6, §9 criterion #7, §10 rollback item 3, §12 step 4 | The F10 status-drift warning was removed in Phase 4 partial collapse (2026-07-06) — verified at `work_resolver.py:1082-1098`; lines 692-709 now contain `_resolve_completion_time`. Removed every "delete status-drift warning" reference; replaced with a negative CI assertion that the warning is not re-introduced. |
| **W1** | Warning | §4 mirror table #3 (message_queue) | Documented that `processing_task_id` is dead code (verified at `predicates.py:113-148`) and `message_id` is the only link key. The `processing_task_id = NULL` write is defensive cleanup. |
| **W2** | Warning | New §5.1 | Added the per-call-site exception-propagation contract: Claim/Finalize/Timeout/Sweep catch-and-log; Resume/Pause propagate. Rationales documented. New success criterion #11. |
| **W3** | Warning | §7 directed fuzz scenario | Added deterministic-test-hook contract: no `time.sleep`; existing hooks preferred; if no hook covers the `process_report` boundary, add a test-only hook guarded by `ENSEMBLE_TEST_PAUSE_HOOK`. Success criterion #9 updated to require reproducibility without timing. |
| **W4** | Warning | §4 contract | Added §4.1 conditional fast-path probe (single `EXISTS`-based SELECT that checks admission/lock invariant). Returns `updated_counts = {}` and `fast_path_skipped = True` when consistent. New success criterion #12. |
| **R1** | Cleanup | §3 Files Touched, §5 call-site table | Refreshed line citations: `_pause_cascade_db_sync` 3039-3210, `_resume_cascade_db_sync` 3474-4130, `_finalize_job_db_sync` 2761-3421, `reconcile_drift_states` 488-1090. **Material correction:** the stale-task recovery method is `recover_stale_tasks` (no leading underscore) at lines 168-385 in `stale_task_recovery.py`; the previous `_recover_stale_tasks` at 165-277 was both misnamed and misranged. |

### v3 (Approver Review 2026-08-01)

| ID | Severity | Section | Fix |
|---|---|---|---|
| **Issue 1** | BLOCKING | §4 contract; §4.1 (deleted); §8 "Fast-path probe tests" (deleted); §9 criterion #12 (deleted, renumbered); §10 rollback item 3; §12 step 2 + exit gate; §13 v3 entry | REMOVED the v2 fast-path probe entirely. It only inspected 2 of 8 mirrors (`job_queue_items.admission_state` and `job_locks`), allowing orphans in the other 6 to persist whenever the admission/lock pair happened to be consistent. Reconciler always runs all 8 handlers in v3; per-table `WHERE` clauses provide the early-exit at near-zero cost. Removed `fast_path_skipped` from result shape, deleted §4.1 + the dedicated unit test, deleted success criterion #12, updated §10 rollback and §12 sequencing. |
| **Issue 2** | BLOCKING | §2 In Scope mirror-table summary; §4 mirror table #2 (`job_queue_items` SQL); §8 new "WAITING_CHILDREN JobItem exception tests"; §9 new criterion #12 (renumbered) | Added the WAITING_CHILDREN exception (D13) to Inc 1's `job_queue_items` reconciliation SQL in three correlated `NOT EXISTS` clauses. When `instances.status = 'waiting_children'`, the JobItem remains `active` even if the Task is terminal — the JobItem is an intentional semaphore for child-completion correlation. Added two paired unit tests (waiting_children keeps active; running transitions to done) on PostgreSQL AND SQLite. Renumbered success criteria: new #12 documents the exception; new #13 documents Issue 5. |
| **Issue 3** | BLOCKING | New §5.2; §9 (no criterion changes — no criterion implied claim-time reconciler unblocks); §12 exit gate | Added §5.2 "Claim-time reconciliation ordering — why the guard runs first" documenting that the reconciler at call site 2a runs AFTER the cross-system guard and CANNOT unblock a blocked claim (Option b). Acceptable because: every transition cascade runs the reconciler immediately, the periodic sweep corrects orphans within N seconds, and any crash-induced orphan self-resolves. The narrow crash-to-sweep window is documented as a temporary stall, not a deadlock. The Inc 2 carve-out deletion (D13) is conditional on this ordering remaining correct. |
| **Issue 5** | BLOCKING | §7 commands list; §7 directed fuzz scenario + new "Corruption scenarios" subsection; §8 "Arbitrary-corruption property tests" bullet; §9 new criterion #13 | Added `CORRUPT_MIRROR(mirror_table, corruption_type)` command to the Hypothesis state machine. Added 6 directed corruption scenarios: admission-consistent + message_queue stale (the scenario that would have caught Issue #1), admission-consistent + dependency_watchers stale (target drained), dependency_watchers stale but target in-flight (mirrors D10), single-table corruption for each of the 8 tables, multi-table corruption (3+ tables), waiting_children JobItem exception. Added invariant: after ANY `CORRUPT_MIRROR` + reconciler, ALL 8 mirror tables must be consistent. |

---

**Increment 1 is approved for implementation once this v4 revision is acknowledged.** All v2 C1/C2 fixes, W1–W4 warnings, and R1 cleanup are applied. The v3 review fixes Issues 1, 2, 3, and 5; the property test suite now generates the failure modes the reconciler must catch; the WAITING_CHILDREN exception is defined HERE so Inc 2 can rely on it; the claim-time ordering rationale is explicit so future code review can catch regressions. **Issue 4** (the missing item from the reviewer's numbered list) was not addressed because the reviewer's dispatch did not specify a fourth blocking issue — it listed Issues 1, 2, 3, and 5 only. If a fourth issue surfaces, it can be folded into a v3.1 cycle.

### v4 (Approver Iteration 002, 2026-08-01) — §4 SQL correctness fixes

Surgical SQL-correctness fixes only. No scope changes, no design changes, no test-strategy changes. The Approver found three line-level SQL defects in the §4 mirror table specifications; each is fixed in place with a `§ REVISION NOTE v4 (Approver Iteration 002)` marker next to it.

| ID | Severity | Section | Fix |
|---|---|---|---|
| **v4.1** | Blocking (SQL correctness) | §4 mirror table #3 (`job_locks` DELETE guard), mirror #4 (`message_queue` UPDATE guard), mirror #5 (`dependency_watchers` UPDATE guard) | Mirrors #3, #4, and #5 used bare `EXISTS(SELECT 1 FROM task WHERE work_id = :work_id AND status = :snapshot_status)` guards WITHOUT the `(:task_exists = false OR ...)` wrapper that mirror #2 correctly uses. When the Task row is MISSING entirely, `EXISTS` returns FALSE → the DELETE/UPDATE silently no-ops → the lock / queue-row / watcher SURVIVES as an orphan. This is the exact bug class the reconciler is designed to kill. Fixed all three mirrors to mirror #2's pattern. The second `NOT EXISTS` in mirror #5 (D10 instance-scoped predicate) is intentionally untouched. |
| **v4.2** | Blocking (TODO → concrete SQL) | §4 mirror table #6 (`report_injections`) | Was a prose TODO ("Define the exact terminal state mapping"). Replaced with the deployed implementation's SQL (`repository.py:677-694`): `SET state = 'TASK_DELIVERED' WHERE report_message_id = :task_message_id AND state = 'PENDING'` plus the corrected `(:task_exists = false OR ...)` guard consistent with the v4.1 fixes. |
| **v4.3** | Blocking (TODO → concrete SQL + semantic divergence) | §4 mirror table #8 (`job_watchers`) | Was prose only ("delete only dangling watcher subscriptions"). Replaced with the deployed implementation's SQL (`repository.py:728-738`): `DELETE FROM job_watchers WHERE job_id = :work_id AND NOT EXISTS (SELECT 1 FROM task WHERE work_id = :work_id)`. **Semantically distinct from every other mirror:** uses `NOT EXISTS` (Task row entirely gone) — NOT `terminal` status — because a terminal Task may have a retry child with migrated watchers, and deleting watchers on terminal would break retry correlation. Tests must assert that a terminal-but-present Task does NOT delete its `job_watchers` row. |

**v4 summary — what changed vs v3:**

- §4 mirrors #3, #4, #5, #6, #8 — SQL correctness fixes only.
- All v3 design decisions (WAITING_CHILDREN, §4.1 deletion, claim-time ordering, arbitrary-corruption property tests, six call-site integration, §5.1 exception propagation) are unchanged.
- No new success criteria added in v4; the existing v3 criteria #1, #8, #13 already cover the v4 invariants (completeness of all 8 mirror tables; arbitrary-corruption property harness would have caught the v4.1 bug).
- No new call-site changes; the mirror-SQL fixes are isolated to §4 specifications.
