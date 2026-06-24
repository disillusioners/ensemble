# Plan: Report-Lane Decoupling (fix stuck child-report Tasks)

| Field | Value |
|---|---|
| **Status** | DRAFT — Risks 1 (pause) & 3 (error) covered; crash-recovery still open |
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
No code change expected for the happy path. **Verify:** the parent instance stays non-terminal
(RUNNING) between report turns while `bus_pending > 0`, so the next report Task can still claim.
Check the instance-status write site that follows the lifecycle event does not force COMPLETED
when the observer deferred.

**1.6 Pause gate in `claim_pending_task` (Risk 1 — covered).**
Today, pause protection for report Tasks is *accidental*: it falls out of the cross-system job
guard (instance `PAUSED` ≠ `waiting_children` → guard blocks the claim). Once 1.3 exempts reports
from that guard, the protection is lost. Make it **explicit and uniform** instead:

- `daemon/repositories/task/repository.py` `claim_pending_task` candidate `WHERE`: add an
  instance-status exclusion for **all** task types:
  ```sql
  AND instance_id NOT IN (
      SELECT instance_id FROM instances
      WHERE status IN (:paused, :terminated)
  )
  ```
  This mirrors `JobProcessor`'s jobqueue skip (job_processor.py:633) and the recovery exclusions
  (task/repository.py:684, 1084). Semantics: no new Task (user *or* report) is claimed for a
  PAUSED/TERMINATED instance. Pending report Tasks created while paused simply wait; on resume
  they are claimed in order.

- `daemon/services/instance_lifecycle.py` `resume_instance_cascade` (~L1056): on a successful
  resume, call `worker_pool.notify_work()` so workers immediately reconsider the now-runnable
  pending Tasks (the mmd's RA5 gap — resume never notified). Guard with
  `getattr(self._manager, "_worker_pool", None)`.

Net: pause behaves correctly for reports by construction (explicit gate) rather than as a
side-effect of the job guard. A child that completes *during* the parent's pause still creates
its report MessageQueue + Task (that insertion is unrelated to claim eligibility); the Task sits
PENDING until resume, then runs — exactly the desired pause-safe behavior.

**1.7 Error-status threading into the finalize path (Risk 3 — covered).**
The conservative "any child error → parent ERROR" rule currently lives in
`_retrigger_parent_finalize` via `bus.had_parent_error(instance_id)` + the child's `error` text.
Once 1.4 deletes that method, the rule must move to the single remaining finalize decision
point: `_process_event`.

- `daemon/services/dependency_bus.py`: the bus already keeps a sticky
  `_parent_errored: dict[str, bool]` (set in `emit_terminal` when a child emits
  `status="error"`, dependency_bus.py:527; read by `had_parent_error`, L950; cleared by
  `clear_parent_error`, L980). Add a parallel **`_parent_error_message: dict[str, str]`**
  capturing the last child `error` text (set in the same `emit_terminal` branch that flips the
  bool). Expose `parent_error_message(parent_id) -> str | None` and clear it in
  `clear_parent_error`.
- `daemon/services/job_feedback_observer.py` `_process_event`, in the `bus_pending == 0`
  finalize branch (just before `_finalize_job`, L735):
  ```python
  if bus is not None and bus.had_parent_error(instance_id):
      status = InstanceStatus.ERROR.value
      error = bus.parent_error_message(instance_id) or "child agent error"
  await self._finalize_job(job, instance_id, status, error=error)
  if bus is not None and bus.had_parent_error(instance_id):
      bus.clear_parent_error(instance_id)   # moved from _retrigger_parent_finalize
  ```
  The parent's own graph turn may have completed cleanly (lifecycle `status=COMPLETED`); we
  override to `ERROR` because a child errored — the conservative rule, now sourced from the bus
  (the authority) at the finalize point. The parent's response text is still delivered (the turn
  ran); only the *job* status reflects the child failure.
- Note: the erroring child still drives the bus via `error_reporting.py:538`
  (`_emit_terminal_via_bus(status="error", error=...)`) — unchanged. What changes is *where* the
  resulting error status is consumed (finalize path instead of the deleted retrigger).

### Phase 2 — Hardening & tests

**2.1** Independent-turn test: 2 children complete at different times → parent produces 2
distinct assistant turns (not one batched). Assert Task claim order and lifecycle events.
**2.2** Pause safety (Risk 1): pause the parent while children run → (a) no report Task is
claimed for the paused instance (explicit gate), (b) a child completing during pause creates
its report Task and it stays PENDING, (c) on resume `notify_work()` fires and the queued report
Task is claimed and processed. Assert instance never runs `graph.astream` while PAUSED.
**2.3** Crash recovery: kill process after report Task PENDING, before claim → restart reclaims
and runs the turn; kill after bus fired but before stamp → restart retries, idempotent.
**2.4** Error propagation (Risk 3): (a) one child errors, one completes → parent job finalizes
as `ERROR` with the child's error message, even though the parent's own report turns completed;
(b) all children succeed → parent finalizes `COMPLETED`; (c) `clear_parent_error` runs after
finalize so a revived instance doesn't inherit the sticky error flag.

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

- ~~**Pause interaction**~~ — **covered (1.6).** Reports were only pause-safe by accident (the
  job guard blocked them while `PAUSED`). Exempting reports from that guard would have removed
  the protection. 1.6 restores it explicitly and uniformly: `claim_pending_task` excludes
  PAUSED/TERMINATED instances for *all* task types, and `resume_instance_cascade` calls
  `notify_work()`. No special-case for reports.
- **Crash recovery (OPEN).** Removing the direct `_retrigger_parent_finalize` (1.4) removes a
  startup safety net for parents that are fired-but-unfinalized (bus watchers all FIRED,
  `count_pending == 0`, but the job still PROCESSING with no pending report Task to drive a
  turn). With the bypass gone, restart must reconcile these: a startup sweep that finalizes any
  PROCESSING message job whose instance has `bus.count_pending_for_target == 0`. Design needed
  — this is the one risk still open.
- ~~**Error status threading**~~ — **covered (1.7).** The any-error→error rule moved from the
  deleted retrigger into `_process_event`'s finalize branch, sourced from the bus's
  `had_parent_error` + a new `parent_error_message`. Sticky flag cleared after finalize.
- **`_get_processing_job_for_instance` assumption:** still one PROCESSING job per instance.
  Unchanged by this plan (reports create no JobItem). Confirm no Phase-1 edit violates it.

---

## 5. Out of scope

- Splitting `message_queue` into a separate report table (rejected — reintroduces a two-claim-loop
  coordination problem; the per-instance serialization invariant needs one task table).
- Multiple JobItems per request (rejected — breaks one-job-per-instance invariant).
