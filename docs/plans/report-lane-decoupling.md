# Plan: Report-Lane Decoupling (fix stuck child-report Tasks)

| Field | Value |
|---|---|
| **Status** | DRAFT — for discussion |
| **Supersedes** | Option A sketch in `logs/report-flow-before-after.mmd` (refines the guard change) |
| **Goal** | Child-report Tasks reach the parent graph independently; one finalize path; no cross-system guard entanglement for reports |
| **Scope** | MEDIUM — ~6 files, 1 SQL restructure, 1 new TaskType, behavior change on a hot path |
| **Risk** | Hot path (task claim, child completion) — requires pause/resume + crash-recovery test coverage |

---

## 1. Problem (recap)

A parent that spawns children ends its graph run early. Each child completion calls
`_create_completion_report`, which inserts a `MessageQueue` row + a `PROCESS_MESSAGE`
Task (fresh `message_id`, **no JobItem**). Two defects strand those Tasks PENDING:

1. **No `notify_work()`** — the worker is never woken (only the 3s poll eventually picks it up).
2. **Cross-system guard blocks them** (`task/repository.py:299`). The guard joins `job_queue_items`
   on `message_id` to decide whether a PROCESSING MESSAGE job is "actively driving the graph".
   A report Task's `message_id` matches no job, so the guard treats the parent's job as active
   and blocks the claim.

Meanwhile `_retrigger_parent_finalize` (child_reports.py:528) finalizes the parent's job
**directly** via `_finalize_job`, bypassing the message queue — so the report Tasks it just
created are orphaned. The two paths race; results are batched, not independent.

**Root architectural issue:** reports and user messages are *half-merged* — they share the Task
table + `_do_process` delivery (correct: both are real graph turns), but differ in admission
(reports have no JobItem). That asymmetry is what forces the cross-system guard to exist and
is the source of the weight.

---

## 2. Target Architecture: "Report Lane"

Keep delivery shared, decouple admission.

- **User messages** → `MessageQueue` → `JobItem` → dispatcher admits → `PROCESS_MESSAGE` Task.
  The cross-system guard continues to coordinate these (its original purpose).
- **Child reports** → `MessageQueue` → **`PROCESS_REPORT` Task** (new type, no JobItem, no
  dispatcher). The cross-system guard **does not apply** to report tasks by construction.
  Only the per-instance serialization guard (one RUNNING task per instance) applies — that is
  the only invariant reports need.

Cardinality unchanged: **1 JobItem per user request** (spans user turn + all report turns),
N report Tasks riding alongside. Reports never create or touch a JobItem.

Finalize path collapses to one: the parent's graph turn (user or report) emits a lifecycle
event → `_process_event` checks `bus.count_pending_for_target` → `>0` defer (`in_progress`),
`==0` `_finalize_job`. The direct finalize in `_retrigger_parent_finalize` is removed.

---

## 3. Phases

### Phase 1 — Bug fix (ship-first, minimal)

**1.1 Wake workers on report creation.**
`daemon/services/child_reports.py` — `_create_completion_report` / `_dispatch_post_commit_side_effects`
(`regular_child_completed` branch, ~L2243): after inserting the report Task, call
`worker_pool.notify_work()` (guard with `getattr(self._manager, "_worker_pool", None)`).
This alone unsticks the Tasks once 1.2 lands.

**1.2 New `PROCESS_REPORT` TaskType + processor.**
`daemon/repositories/task/models.py`: add `PROCESS_REPORT = "process_report"` to `TaskType`.
`daemon/services/task_processor.py`: register the report task type to the same
`ProcessMessageProcessor` pipeline (report delivery is identical: read `message_queue` row by
`message_id`, feed `message.content` to `_do_process`). Either alias the processor under the
new type, or subclass with report-specific logging. No behavior change in the pipeline.

`_create_completion_report` (child_reports.py:944) and the inline report creation
(child_reports.py:1902) set `task_type=TaskType.PROCESS_REPORT.value`.

**1.3 Restructure `claim_pending_task` — scope the cross-system guard to PROCESS_MESSAGE.**
`daemon/repositories/task/repository.py:254-316`. Move the job-coordination exclusion behind a
candidate-type condition so report tasks bypass it entirely. Sketch:

```sql
UPDATE task SET status = running, worker_id = :wid, started_at = :now, last_heartbeat_at = :now
WHERE id = (
  SELECT id FROM task
  WHERE status = 'pending'
    AND (next_retry_at IS NULL OR next_retry_at <= :now_str)
    -- per-instance serialization: applies to ALL task types (incl. reports)
    AND instance_id NOT IN (
        SELECT instance_id FROM task WHERE status = 'running'
    )
    -- cross-system guard: JOB COORDINATION ONLY — process_message tasks.
    -- report tasks are exempt: they have no JobItem to collide with.
    AND (
        task_type != 'process_message'
        OR instance_id NOT IN (
            SELECT j.instance_id FROM job_queue_items j
            LEFT JOIN instances i ON j.instance_id = i.instance_id
            WHERE j.status = 'processing'
              AND j.job_type = 'message'
              AND j.instance_id IS NOT NULL
              AND j.deleted_at IS NULL
              AND (i.status IS NULL OR i.status != 'waiting_children')
              AND NOT EXISTS (
                  SELECT 1 FROM task t
                  WHERE t.message_id = <json_extract(j.metadata,'$.message_id')>
                    AND t.status IN ('pending','running')
              )
        )
    )
  ORDER BY created_at ASC LIMIT 1
)
AND status = 'pending'
RETURNING *;
```

Net effect: report tasks are claimed as soon as no other task for the instance is RUNNING.
The `WAITING_CHILDREN` carve-out and the `message_id`-join carve-out now only ever govern
`process_message` tasks — their original intent. (The carve-outs can be simplified later; left
intact in Phase 1 to minimize behavior change.)

**1.4 Remove the direct finalize bypass.**
`daemon/services/child_reports.py` `_retrigger_parent_finalize` (L386-554): delete the
`observer._finalize_job(...)` call (L528) and the surrounding job-lookup. The method becomes a
no-op / is removed; `_emit_terminal_via_bus` keeps only the bus state transition (PENDING→FIRED)
and the crash-recovery stamp ordering. The bus is now a pure state machine.
`daemon/api.py` crash-recovery call site (L631) that invokes `_retrigger_parent_finalize`
directly must be re-routed: on restart, fired-but-unstamped rows are retried by stamping logic;
the natural finalize happens when the (re-claimed) report Task's turn ends.

**1.5 Single finalize path already exists — verify only.**
`daemon/services/job_feedback_observer.py:_process_event` (L703-735) already gates on
`bus.count_pending_for_target`: `>0` → `_emit_in_progress` + return; `==0` → `_finalize_job`.
No code change expected. **Verify:** the parent instance stays non-terminal (RUNNING) between
report turns while `bus_pending > 0`, so the next report Task can still claim. Check the
instance-status write site that follows the lifecycle event does not force COMPLETED when the
observer deferred.

### Phase 2 — Hardening & tests

**2.1** Independent-turn test: 2 children complete at different times → parent produces 2
distinct assistant turns (not one batched). Assert Task claim order and lifecycle events.
**2.2** Pause safety: pause cancels bus watchers → no new report Task created during pause
(report path runs in post-commit of child completion; if child completes during pause the Task
is created but not claimed until resume because instance is PAUSED — confirm the
per-instance/serialization guard or a `status != paused` clause holds; if reports are exempt
from the job guard they must still respect a PAUSED instance, add explicit check).
**2.3** Crash recovery: kill process after report Task PENDING, before claim → restart reclaims
and runs the turn; kill after bus fired but before stamp → restart retries, idempotent.
**2.4** Error propagation: a child that errors → report Task delivers the error; last report
turn finalizes parent as `error` (conservative any-error→error rule). Confirm the error status
is no longer threaded solely through the deleted `_retrigger_parent_finalize` — move it to the
report message content / metadata consumed by the turn.

### Phase 3 (optional, defer) — Drop the report MessageQueue row

Reports currently snapshot the child's last assistant message into `message_queue.content`,
then `ProcessMessageProcessor` reads it back by `message_id`. The content is already in the
child's thread. Phase 3 carries a `child_instance_id` (+ snapshot version) on the report Task
and fetches content at run time, removing the `MessageQueue` row for reports entirely. This
makes `message_queue` hold *only* external/user input. **Deferred** — it's a larger change to
`ProcessMessageProcessor`'s `message_repo.get(message_id)` contract and the content-snapshot
stability guarantee; not needed to fix the bug.

---

## 4. Risks / open questions

- **Pause interaction (2.2):** exempting reports from the job guard removes the
  `i.status != waiting_children` clause's reach over reports. Reports must still not run while
  the instance is PAUSED. Need an explicit `instance.status != 'paused'` (or reuse the
  per-instance RUNNING-task guard + a paused check). This is the one place the "exempt by
  construction" model needs a small explicit gate.
- **`_get_processing_job_for_instance` assumption:** still one PROCESSING job per instance.
  Unchanged by this plan (reports create no JobItem). Confirm no Phase-1 edit violates it.
- **api.py crash-recovery retrigger (1.4):** the direct `_retrigger_parent_finalize` call on
  restart is a belt-and-suspenders path for fired-but-unfinalized parents. With the bypass
  removed, restart must rely on (a) report Tasks being reclaimed, or (b) the lifecycle event
  on the next turn. A stuck parent with no pending report Task and a fired bus but unfinalized
  job needs a recovery sweep — design a minimal "finalize parents with 0 pending watchers and
  a PROCESSING job" reconciliation on startup.
- **Error status threading (2.4):** currently `_retrigger_parent_finalize` carried
  `terminal_status`/`had_parent_error`. Removing it means error info must ride the report
  message. Ensure the conservative any-error→error semantics survive.

---

## 5. Out of scope

- Splitting `message_queue` into a separate report table (rejected — reintroduces a two-claim-loop
  coordination problem; the per-instance serialization invariant needs one task table).
- Multiple JobItems per request (rejected — breaks one-job-per-instance invariant).
