# Bug: Pause During a Report Turn Orphans the Original Message JobItem → Resume Deadlock

**Date:** 2026-08-01
**Status:** Unfixed (production incident; awaiting team review)
**Severity:** High (instance permanently stuck after resume; requires manual DB intervention)
**Affected versions:** All versions shipping with the Phase 5 pause/resume redesign + D13 message-JobItem elimination
**Affected instances:** Any root instance that is paused **after** its original `process_message` turn has completed naturally (instance reached `WAITING_CHILDREN`) **while** a child-completion `process_report` turn is in flight on the parent.

**Discovery:** Production incident 2026-08-01 01:09–10:29 UTC. Leader instance `691c6638-f3db-49d5-a768-8dc6c957dee0` paused (via `ask_questions` answer gate) while a `process_report` task was mid-flight. This produced **two compounding symptoms**, both fixed manually:

1. **Bug A (01:09–01:24):** Resume never progressed — the user's answer was enqueued as Task `13192` but stayed `pending` forever (orphaned `active` JobItem deadlock).
2. **Bug B (10:25–10:29):** After Bug A's unstick the instance completed its work but got stuck at `waiting_children` because two `completion_report` `message_queue` rows were orphaned at `status='processing'` (their `process_report` Tasks were cancelled by the pause cascade but the message rows were never reset/completed).

---

## Summary

When pause fires during a `process_report` turn (rather than during the original `process_message` turn), the resume path misroutes the instance to the **child branch** because `find_paused_or_running_by_instance` finds no `PAUSED`/`RUNNING`/`CANCELLED` `process_message` task. The resume then enqueues the *answer* as a fresh `process_message` Task with no JobItem (cascade-resume rule). But the **original turn's JobItem is still `active`** (the instance never went terminal, so `_finalize_job` was never called), and the cross-system claim guard (`claim_pending_task`) blocks the new Task because the only release carve-outs require a matching `pending`/`running` Task (the old one is `completed`) or a `queued` orphan (the old one is `active`). Result: permanent deadlock — the answer Task stays `pending` forever.

Two independent gaps compound into the user-visible symptom:

1. **Routing gap** — `resume_processing_job` decides root-vs-child purely on the presence of a `PAUSED`/`RUNNING`/`CANCELLED` `process_message` Task, which misses the "report turn in flight + original message job still active" state. All existing pause/resume tests pause **during** the message turn, so this branch was never exercised.
2. **Guard gap** — The orphan-exclusion carve-out in `claim_pending_task` only releases `queued` JobItems. An `active` JobItem whose backing Task is terminal (`completed`/`cancelled`/`failed`) is an equally orphaned mirror but blocks forever. The cross-system guard's "matching Task must be `pending`/`running`" carve-out (the unified-dispatcher admission carve-out) doesn't fire because the backing Task is `completed`.

### Important clarification: NOT FIFO-specific

The deadlock is **not** caused by the FIFO queue's `concurrency_limit=1`. The cross-system guard blocks on *any* `active` JobItem for the instance regardless of slot count. The same lingering-active-JobItem would block a new answer Task on a parallel queue (concurrency 3–5) too. FIFO makes the symptom visible immediately (100% slot pressure), but the guard itself is queue-agnostic. A pause-during-report on a parallel queue would deadlock the same way — it just happens that production ran on FIFO.

---

## Production Evidence

Root instance: `691c6638-f3db-49d5-a768-8dc6c957dee0` (leader, project `83da04de-a410-4fb5-9e92-251a99d28a52`, FIFO queue `sys-fifo-83da04de-...`)

### DB state at discovery

| Table | Row | State |
|-------|-----|-------|
| `instances` | root `691c6638...` | `status=running`, `paused_at=NULL` (resume had fired at 01:18:14) |
| `task` | `13178` (`process_message`, msg `7990d597`) | `completed` at 00:54:17 — **original turn finished naturally** |
| `task` | `13191` (`process_report`, msg `b1799520`) | `cancelled` at 01:18:14 — pause cascade cancelled it |
| `task` | `13192` (`process_message`, msg `3319bf33`) | `pending`, `worker_id=NULL` — **the answer, never claimed** |
| `job_queue_items` | `4a629435` (msg `7990d597`) | `admission_state='active'`, `deleted_at=NULL` — **orphaned active mirror** |
| `job_locks` | `1fc9f56f` | lock slot held for job `4a629435` — **stale FIFO lock** |
| `instance_execution_leases` | — | empty (no lease held) |

### Task timeline

| Task | Type | msg_id | Status | Created | Started | Completed |
|------|------|--------|--------|---------|---------|-----------|
| `13178` | `process_message` | `7990d597` | `completed` | 00:48:27 | 00:48:27 | 00:54:17 |
| `13191` | `process_report` | `b1799520` | `cancelled` | 01:08:52 | 01:08:52 | 01:18:14 |
| `13192` | `process_message` | `3319bf33` | `pending`→`running` (post-unstick) | 01:18:14 | 01:24:17 | 01:25:13 |

### Log timeline

| Time | Event |
|------|-------|
| `00:54:17` | Task 13178 (`process_message`) **completes naturally**. Instance transitions to `WAITING_CHILDREN`. The original message's JobItem `4a629435` is left `active` (correct — slot must be held while children resolve). |
| `01:08:52` | Leader invokes `ask_questions` tool mid-graph (Task 13191, `process_report`). The graph hits the answer gate. |
| `01:09:07` | Streaming error: `'NoneType' object has no attribute 'get'` on message `b1799520`. The cascade triggers `pause_instance(691c6638...)`. |
| `01:09:07` | Pause cascade cancels Task 13191 (`process_report`) — it was the **only** in-flight task; Task 13178 was already `completed`. |
| `01:18:14` | User answers the question. Resume cascade fires: `_resume_cascade_db_sync` lifts pause, wakes WorkerPool. `resume_processing_job` → `find_paused_or_running_by_instance` finds **no** `PAUSED`/`RUNNING`/`CANCELLED` `process_message` task (13178 is `completed`, 13191 was `process_report`) → takes the **child branch**. |
| `01:18:14` | Child branch enqueues the answer as Task 13192 (`process_message`, no JobItem, `source="cascade_resume"`). Log: `Child instance 691c6638... enqueued via WorkerPool: message_id=3319bf33`. |
| `01:18:14 → 01:24:17` | WorkerPool's `claim_pending_task` returns `None` on every 3s safety-net wakeup. JobItem `4a629435` (`active`, msg `7990d597`) blocks Task 13192 via the cross-system guard. The carve-out needs a matching `pending`/`running` Task for msg `7990d597`, but 13178 is `completed` → carve-out doesn't fire. The orphan-exclusion only covers `queued` JobItems, not `active` ones → also doesn't fire. **Permanent deadlock.** |
| `01:24:17` | Manual DB unstick: mark JobItem `4a629435` → `done`, release lock `1fc9f56f`. Next claim picks up Task 13192 within 6s. Instance resumes. |

---

## Root Causes

### Routing Gap — `resume_processing_job` misroutes to the child branch

**Location:** `daemon/manager.py:4844-4912` (`resume_processing_job`) + `daemon/repositories/task/repository.py:171-244` (`find_paused_or_running_by_instance`)

The root-vs-child routing decision filters on `task_type = PROCESS_MESSAGE` and `status IN (PAUSED, RUNNING, CANCELLED)`. This correctly identifies a root instance **that was paused during its message turn** — but it fails for the sequence:

1. Original `process_message` task completes naturally → instance → `WAITING_CHILDREN`.
2. A child-completion `process_report` turn starts on the parent.
3. Pause fires mid-report.

At pause time, the only in-flight task is a `process_report`, which the routing primitive excludes by design ("a paused report task is irrelevant to whether the root instance has an in-flight graph turn to resume" — `repository.py:201-204`). The resume path then finds no `process_message` task → concludes "child instance" → enqueues the answer as a fresh message with no JobItem.

This is the correct behavior **only** when the instance genuinely *never had* an in-flight message turn. When the instance's message turn already completed and the JobItem is still `active` (waiting for children), routing to the child branch:
- creates a Task with no backing JobItem (the cascade-resume rule at `manager.py:4886-4893`), and
- leaves the original turn's JobItem orphaned, because nothing in the child branch calls `_finalize_job` / `_process_resume_finalize`.

**Why tests missed it:** every pause/resume test (`tests/e2e/test_e2e_workflows.py:1561 test_pause_after_spawn_then_resume`, `tests/unit/test_pause_resume_root.py`, `tests/unit/test_cascade_pause_resume.py`, `tests/integration/test_cold_resume_ttl.py`) pauses during the **message turn** (the leader is mid-`process_message` when `_pause_instance` fires). The "pause during report turn" sequence was never exercised.

### Guard Gap — orphan-exclusion carve-out misses terminal-backed `active` JobItems

**Location:** `daemon/repositories/task/repository.py:646-765` (`claim_pending_task` cross-system guard)

The cross-system guard blocks any `process_message` Task whose instance has an `active`/`queued` JobItem while `instance.status != WAITING_CHILDREN`. Two carve-outs release the block:

1. **Unified-dispatcher admission carve-out** (`repository.py:718-741`): exclude JobItems that have a matching Task for the same `message_id` in `pending`/`running`. The rationale: the dispatcher has taken ownership. But when the backing Task is terminal (`completed`), the carve-out doesn't fire — even though a `completed` Task is no longer driving `graph.astream`, so admitting a new Task would not race.

2. **Orphan-exclusion** (`repository.py:757-763`, FIFO concurrency fix 2026-07-26): exclude `queued` JobItems that have *no* matching Task at all (truly orphaned mirrors). But this only covers `queued` orphans, not `active` ones.

An `active` JobItem whose backing Task is terminal is an orphaned mirror with the same semantics as the `queued` orphan — it cannot be coordinating in-flight work — but it permanently blocks the instance. This is the gap that turned the routing mistake into a permanent deadlock.

### Why the JobItem is `active` in the first place (this is correct by design)

The JobItem staying `active` through `WAITING_CHILDREN` is **intended** — releasing the slot before children resolve would let a second message race the graph thread (`MessageJobHandler` defers job completion; `JobFeedbackObserver` completes the job when the instance finally completes). See `daemon/services/job_feedback_observer.py:727-728` and `:2779-2784`. The bug is not the `active` JobItem itself; it is that the resume path leaves it orphaned (`_finalize_job` never runs) **and** the guard doesn't recognize the orphan.

### Contributing factor — streaming error aborted the observer chain

The `01:09:07` streaming error (`'NoneType' object has no attribute 'get'` in `daemon.services.instance_messaging`) is the proximate trigger. It aborted the in-flight report turn and forced the pause cascade. If the report turn had completed normally, the instance would have finalized via `JobFeedbackObserver` and the JobItem would have transitioned `active → done`. The pause/resume redesign does not appear to have a recovery path for "streaming blew up mid-report, leaving the original message JobItem orphaned."

---

## Bug B — Orphaned `processing` `message_queue` rows block the final COMPLETED transition

**Location:** `daemon/services/instance_lifecycle.py:3030` (`_pause_cascade_db_sync`) + `daemon/services/instance_lifecycle.py:3293` (`_resume_cascade_db_sync`) vs `daemon/services/child_reports.py:1459-1519` (root-completion `pending_count` guard)

### What happened

After Bug A's manual unstick (01:24), the instance resumed and continued working normally — the leader processed the user's answer, spawned more children, and consumed completion reports through `04:02`. But at the final report (Task `13285`, completed `04:02:26`) the `.completed` transition never fired:

```
5253:04:02:26 - daemon.services.child_reports - INFO - Instance 691c6638... parent_id=None, status=waiting_children
5254:04:02:26 - daemon.services.child_reports - INFO - Instance 691c6638... has 2 pending messages, status=WAITING_CHILDREN (deprecated)
```

The "has 2 pending messages" log line is the root-completion guard at `child_reports.py:1509-1512`. The guard counts `message_queue` rows with `status IN ('ready','processing','retrying')` (excluding the just-completed message). The count was **2** because two `completion_report` rows were orphaned at `status='processing'` with `processing_task_id=NULL`:

| `message_queue.message_id` | type | status | backing Task | Task status | orphaned since |
|----------------------------|------|--------|--------------|-------------|----------------|
| `b1799520-ee0d-4d46-...` | `completion_report` | `processing` | `13191` (`process_report`) | `cancelled` @ 01:18:14 | pause cascade #1 |
| `202e63a4-c03e-489c-...` | `completion_report` | `processing` | `13219` (`process_report`) | `cancelled` @ 02:41:53 | pause cascade #2 |

Both Task rows were cancelled by pause cascades (the same `ask_questions`-triggered pause at 01:09, plus a second pause around 02:36). The `processing_task_id` pointer was cleared to `NULL` (so no worker re-claims them), but the `message_queue.status` field was **never** reset to `ready` (for re-arming) or `completed`.

### Root cause

`_pause_cascade_db_sync` (`instance_lifecycle.py:3108-3190`) performs exactly two UPDATEs, both on `instances` (→ PAUSED) and `task` (RUNNING → PAUSED). It does **not** touch `message_queue`. `_resume_cascade_db_sync` (`instance_lifecycle.py:3360-3412`) performs two UPDATEs on `instances` (→ RUNNING) and `task` (PAUSED → CANCELLED). It also does **not** touch `message_queue`.

So when a `process_report` Task is RUNNING (driving the parent graph with a child's `completion_report` message in `processing` state) and pause fires:

1. Pause cascade: Task `RUNNING → PAUSED`. `message_queue` row stays `processing` (untouched).
2. Resume cascade: Task `PAUSED → CANCELLED`. `message_queue` row **still** stays `processing`.

The `processing_task_id` ends up `NULL` (cleared somewhere in the cancel/cascade path — observed in the DB), but `status` is never reconciled. The row is now a **permanent orphan**: no Task will ever drive it (the Task is `cancelled` and non-claimable), but the `processing` status makes the root-completion guard count it forever.

The cumulative `pending_count` guard at `child_reports.py:1459-1512` then keeps the instance at `WAITING_CHILDREN` on every subsequent report delivery — even after all children resolve and the bus is clean. There is no recovery sweep that resets orphaned `processing` `message_queue` rows whose backing Task is terminal.

### Why this is the same bug family as Bug A

Both stem from the **pause/resume cascade not fully cleaning up the in-flight report turn's state**:

- Bug A: the report turn's `job_queue_items` mirror is orphaned at `admission_state='active'`.
- Bug B: the report turn's `message_queue` row is orphaned at `status='processing'`.

The cascade's two UPDATEs (`instances` + `task`) are sufficient for the `process_message`-turn pause (the message turn's own `message_queue` row is owned and cleaned by `resume_processing_job`'s stale-message cleanup at `manager.py:4928-5011`). But for a `process_report`-turn pause, no equivalent cleanup exists for the orphaned `completion_report` message row.

### Proposed fix (Option D — extends the cascade)

Add a third UPDATE to `_resume_cascade_db_sync` (and/or `_pause_cascade_db_sync`) that reconciles `message_queue` rows whose backing Task was just cancelled:

```sql
-- For each cancelled task in the resume cascade, reset its
-- in-flight message_queue row so it does not linger as a
-- permanent `processing` orphan. Two viable semantics:
--   (a) re-arm to `ready` so the report is re-delivered, OR
--   (b) mark `completed` so the (now-stale) report is dropped.
UPDATE message_queue
   SET status = 'completed',
       completed_at = :now,
       processing_task_id = NULL,
       last_activity_at = :now
 WHERE instance_id IN (:tree_ids)
   AND status IN ('processing', 'retrying')
   AND processing_task_id IS NULL
   AND NOT EXISTS (
       SELECT 1 FROM task t
       WHERE t.message_id = message_queue.message_id
         AND t.status IN ('pending', 'running', 'paused')
   )
```

The `NOT EXISTS` guard ensures we only finalize messages whose backing Task is terminal (cancelled/completed/failed) — never one that might still be re-armed by a later resume. Semantics (a) vs (b) is a design decision for the team: re-arming (a) preserves the child report but risks re-delivering stale content after the parent already moved on; finalizing (b) drops the report content but matches what was observed in production (the parent continued fine without these two reports).

**Alternative / defense-in-depth (Option E):** broaden the `pending_count` guard at `child_reports.py:1459-1469` to exclude `processing`/`retrying` rows whose backing Task is terminal — mirroring the orphan-exclusion carve-out proposed for Bug A. This makes the completion guard robust to orphaned message rows under any failure mode, not just the pause cascade.

**Test coverage to add:**
- E2E / integration: pause during a `process_report` turn (root parent receiving a child completion), then resume → verify no orphaned `processing` `message_queue` rows remain, and the instance reaches `COMPLETED`.
- Unit: `_resume_cascade_db_sync` reconciles `message_queue` rows whose task was cancelled (Option D).
- Unit: `child_reports` root-completion guard excludes `processing` rows with terminal backing Tasks (Option E).

---

## Proposed Solution

This needs team review. Two options, not mutually exclusive. The reviewer's preferred fix depth determines the scope.

### Option A — Hardening only the guard (minimal blast radius, fixes the deadlock)

**Scope:** `daemon/repositories/task/repository.py:757-763` and the mirror at `:1488-1497` (`has_pending_tasks_blocked_by_busy_instance`).

Extend the orphan-exclusion to cover `active` JobItems whose backing Task is terminal:

```sql
-- Current (queued-only):
AND NOT (
    j.admission_state = :status_queued_admission
    AND NOT EXISTS (
        SELECT 1 FROM task _orphan_check
        WHERE _orphan_check.message_id = {_orphan_json_extract}
    )
)
```

```sql
-- Proposed (queued-orphan OR active-orphan-with-terminal-task):
AND NOT (
    j.admission_state IN (:status_queued_admission, :status_active_admission)
    AND NOT EXISTS (
        SELECT 1 FROM task _orphan_check
        WHERE _orphan_check.message_id = {_orphan_json_extract}
          AND _orphan_check.status IN (
              :status_pending, :status_running,
              :status_paused   -- exclude: pause cascade owns this, resume will re-arm
          )
    )
)
```

Inlining the `IN (pending, running, paused)` filter into the orphan check subsumes the unified-dispatcher carve-out (carve-out #1) for the `active` case: an `active` JobItem with only a `completed`/`cancelled`/`failed` Task is an orphan.

**Pros:** Fixes the deadlock for *any* future code path that orphans an `active` JobItem, not just the report-turn-pause case. Minimal blast radius — only the claim/busy-probe predicates change.
**Cons:** Does not address the routing gap — the resume path still takes the child branch, creating a Task with no JobItem. The orphaned `active` JobItem lingers as `active` until the orphan-exclusion reaps it on each claim. The JobItem row is never explicitly `done`-transitioned or slot-released by the resume path itself (only the `JobFeedbackObserver` orphan sweep / next-instance-finalize would clean it). For FIFO this means the slot is freed by a later sweep, not by resume — acceptable but not clean.

**Test coverage to add:**
- Unit: `claim_pending_task` admits a `process_message` Task when the instance has an `active` JobItem whose backing Task is `completed`.
- Unit: `claim_pending_task` still blocks when the backing Task is `running` (no regression of the unified-dispatcher protection).
- Unit: `claim_pending_task` still blocks when the backing Task is `pending` (the message hasn't been claimed yet — the JobItem must stay).
- Unit: `has_pending_tasks_blocked_by_busy_instance` mirrors the same predicate (the two MUST agree, per the P1/F11 invariant in the docstring).

### Option B — Fix the routing gap (correct but larger scope)

**Scope:** `daemon/manager.py:4844-4912` (`resume_processing_job`) + `daemon/repositories/task/repository.py:171` (`find_paused_or_running_by_instance` or a new primitive).

Add a primitive that recognizes the "report-turn-in-flight-with-active-message-JobItem" state, so resume takes the **root branch** and drives `_process_resume_finalize` to clean up the original JobItem:

- New repo method, e.g. `find_paused_or_running_or_active_job_by_instance`: returns the still-`active` JobItem's `work_id` when (a) no `PAUSED`/`RUNNING`/`CANCELLED` `process_message` Task exists AND (b) the instance's most-recent `process_message` Task is terminal AND (c) an `active` JobItem exists for that task's `message_id`.
- `resume_processing_job`: when the new primitive returns a job, take the root branch (checkpoint resume + `_process_resume_finalize` to transition the orphaned JobItem `active → done` + release the slot).

**Pros:** Resumes the instance cleanly — the JobItem is explicitly finalized, slot released, no reliance on a later sweep. Removes the Task-with-no-JobItem artifact from the resume output.
**Cons:** Mutates the resume routing — a hotter path. Needs careful review of the root-vs-child contract (the child branch is deliberately JobItem-free for cascade-resume traffic). Risk of double-finalize races with `JobFeedbackObserver`.

### Option C — Reactive: streaming-error recovery (addresses the proximate trigger)

**Scope:** `daemon/services/instance_messaging.py` (the streaming `NoneType` failure site) + a resume-cleanup hook.

Investigate why the streaming error left the original message JobItem orphaned. Even if the graph turn errors out, the instance should still finalize via `_finalize_job` so the JobItem transitions to `done`. This may be the cleaner long-term fix — but it requires reproducing/diagnosing the `NoneType: .get` streaming failure, which is out of scope for the pause/resume routing gap.

### Recommendation

**Option A is the minimal, safe fix for the deadlock** and should ship first regardless of the other options. It makes the guard robust to orphaned `active` JobItems under any failure mode, not just this one.

**Option B is the correct structural fix** for the routing gap and should follow A once the team is comfortable with the resume-routing change. The unit regression test for B (pause during a `process_report` turn) is what was missing from the suite and is the test the team should sign off on first.

**Option C is a separate investigation** — it does not block A or B but explains *why* the JobItem was orphaned in this specific incident. Worth a follow-up ticket.

---

## Manual Remediation (applied to production 2026-08-01)

### Bug A unstick — 01:24 (orphaned `active` JobItem)

```sql
BEGIN;
UPDATE job_queue_items
   SET admission_state = 'done',
       terminal_reason = 'manual-unstick: orphaned active mirror; backing task completed but admission_state never transitioned'
 WHERE job_id = '4a629435-33e5-49e4-9018-382425c21de6'
   AND admission_state = 'active';
DELETE FROM job_locks
 WHERE lock_id = '1fc9f56f-cc53-4282-b14b-4f879f8d16db';
COMMIT;
```

The WorkerPool's 3-second safety-net wakeup claimed Task `13192` within 6s of the commit; instance resumed and continued.

### Bug B unstick — 10:27 (orphaned `processing` `message_queue` rows)

```sql
BEGIN;
UPDATE message_queue
   SET status = 'completed',
       completed_at = NOW(),
       last_activity_at = NOW(),
       processing_task_id = NULL,
       error_message = COALESCE(error_message,'') || 'manual-unstick: orphaned processing row; backing task cancelled by pause/resume cascade (status never reset)'
 WHERE instance_id = '691c6638-f3db-49d5-a768-8dc6c957dee0'
   AND status IN ('processing','retrying')
   AND processing_task_id IS NULL;
-- 2 rows updated (b1799520, 202e63a4)
COMMIT;
```

This cleared the `pending_count=2` guard, making the instance eligible for `COMPLETED`. Because no pending task / bus watcher / JobItem remained to re-fire the completion check (the JobItem was already `done` from Bug A's unstick, and no natural child-completion event would arrive), the instance status was then transitioned directly:

```sql
BEGIN;
UPDATE instances
   SET status = 'completed',
       updated_at = NOW()::text,
       last_activity_at = NOW(),
       version = COALESCE(version, 1) + 1
 WHERE instance_id = '691c6638-f3db-49d5-a768-8dc6c957dee0'
   AND status = 'waiting_children';
COMMIT;
```

The direct status write mirrors `_finalize_instance_db_sync`'s transition. The COMPLETED side effects skipped by the direct write (SSE `status_change`, `CompletionRegistry.complete`) are display-only and resolve on the next UI poll / interaction. A future fix should add a "reevaluate completion" sweep or API so operators don't have to write to `instances.status` directly when all completion conditions are met but no trigger event fires.

---

## Files Touched (Option A — Bug A guard hardening)

- `daemon/repositories/task/repository.py:757-763` (claim path orphan-exclusion — broaden to `active`)
- `daemon/repositories/task/repository.py:1488-1497` (busy-instance probe — mirror the broadened predicate, per the P1/F11 invariant)
- New tests under `tests/unit/` exercising the broadened carve-out

## Files Touched (Option B — Bug A routing fix, follow-up)

- `daemon/repositories/task/repository.py:171` (new or extended primitive)
- `daemon/manager.py:4844-4912` (`resume_processing_job` routing)
- New E2E test `pause_during_report_turn_then_resume` under `tests/e2e/`

## Files Touched (Option D — Bug B cascade reconciliation)

- `daemon/services/instance_lifecycle.py:3293` (`_resume_cascade_db_sync` — add `message_queue` reconciliation UPDATE)
- Possibly `daemon/services/instance_lifecycle.py:3030` (`_pause_cascade_db_sync` — same, if re-arm semantics chosen)
- Test: pause/resume during a `process_report` turn leaves no orphaned `processing` `message_queue` rows

## Files Touched (Option E — Bug B completion-guard hardening, defense-in-depth)

- `daemon/services/child_reports.py:1459-1519` (root-completion `pending_count` guard — exclude `processing`/`retrying` rows whose backing Task is terminal)
- Mirror at `daemon/services/child_reports.py:862-922` (legacy `_update_parent_on_child_complete` path) and `daemon/services/error_reporting.py:269-324` (error-report path)
- Unit tests for the broadened exclusion

---

## Related

- `docs/bugs/parent-stuck-waiting-children-orphan-error-report.md` (same project, same kind of orphan-cascade failure, different trigger)
- `docs/plans/report-lane-decoupling.md` (the report-lane decoupling that introduced `task_type != process_message` guard scoping)
- The FIFO concurrency fix (2026-07-26) — added the `queued`-only orphan-exclusion that Option A extends
- The 2026-06-22 premature-completion fixes (C2 inline bus gate, pending-tasks guard at `child_reports.py:1284-1306`) — the guard that Bug B's orphaned rows end up blocking
