# Bug Investigation — Instance 06f500af Stuck in `waiting_children`

**Date:** 2026-06-26
**Severity:** High — leader instances can become permanently stranded
**Status:** Root cause identified, fix proposed (not yet applied)
**Related:** `ebcbabd9` (parent-stuck-waiting-children-orphan-error-report), `5d28fda7` (notify parent on child task failure)

---

## TL;DR

Instance `06f500af-c60e-4d2a-b5c0-6464fc81aa85` (leader) is stuck in
`status=waiting_children` because the `DependencyBus` has **one orphan PENDING
watcher** (`29dc99be-3823-4aad-8858-3d207b59aab3`) keyed on
`source_task_id=4464`. That task was force-cancelled by `StaleTaskRecovery`
and a retry (task 4466) was scheduled, but **no code path informed the bus**
that task 4464 reached a terminal event — so the watcher stays PENDING
forever and `count_pending_for_target(06f500af) > 0`.

This is a **distinct bug** from the previous fix in `ebcbabd9` (which
addressed three other `waiting_children` causes — orphan error reports,
graph-timeout-vs-natural-completion race, and idempotency no-op).

---

## Database evidence (PostgreSQL `ensemble_prod`)

### Instance state

```
06f500af-c60e-4d2a-b5c0-6464fc81aa85 | status=waiting_children | version=13
updated_at = 2026-06-26T08:34:12 UTC   (last touch 14h ago)
```

All 8 children are `status=completed`:

```
c94bbf24 (explorer)   completed
ce7c1904 (explorer)   completed
3e69ec94 (giter)      completed
ce27f76c (developer)  completed    <-- the relevant child
8fc2d122 (explorer)   completed
ec75db01 (reviewer)   completed
d94f276d (tidier)     completed
3eec6a8c (reviewer)   completed
```

### The orphan watcher

```
watch_id        = 29dc99be-3823-4aad-8858-3d207b59aab3
source_task_id  = 4464
target_instance = 06f500af-c60e-4d2a-b5c0-6464fc81aa85
state           = PENDING            <-- NEVER fired
created_at      = 2026-06-26T09:32:20 UTC
fired_at        = NULL
enqueued_at     = NULL
```

All other 10 watchers for `06f500af` are `state=FIRED` with both
`fired_at` and `enqueued_at` populated. Only this one is stuck.

### Task 4464 timeline

```
16:32:20  task 4464 created (process_message, instance=ce27f76c,
          message=c85c73b4, worker-3)
16:32:20  task 4464 → RUNNING (worker-3 picked it up)
          ... ce27f76c makes 50+ LLM calls; worker-3 stops heartbeating ...
17:59:42  StaleTaskRecovery Step 2: requested cancel for stale task 4464
          (worker-3 not heartbeating; threshold=10min)
19:05:34  StaleTaskRecovery Step 4+5: FORCE-CANCELLED 4464 +
          scheduled retry 4466 (attempt 1)
          <<<  NO bus.emit_terminal(4464) call here >>>
19:07:07  Retry task 4466 claimed (worker-3 again)
20:04:22  Retry task 4466 → COMPLETED → message c85c73b4 → COMPLETED
          _process_child_completion_and_notify_parent(ce27f76c, c85c73b4)
          → _emit_terminal_via_bus(task_id=4466, status='completed')
          <<<  Bus only has watcher for 4464, not 4466 — no-op >>>
20:18:38  Leader 06f500af completes final report message but is STILL stuck
          because bus.count_pending_for_target(06f500af) returns 1
          (the orphan watcher 29dc99be)
```

---

## Code-path analysis

### Who registered the watcher

When the leader (06f500af) sent a message to `ce27f76c` via
`send_message` at 16:32:20, the `send_message` tool registered a
`DependencyWatcher` keyed on `source_task_id=4464, target=06f500af`
with the FollowUp payload "after task 4464 finishes, enqueue a
PROCESS_REPORT message on the leader's queue so it knows ce27f76c
is done."

### Who should fire it (the missing path)

The bus's `emit_terminal(task_id)` is the **sole** mechanism for
firing PENDING watchers. It is called from exactly two places in the
codebase:

1. `child_reports._emit_terminal_via_bus(task_id=...)` — fired from
   `_dispatch_post_commit_side_effects` for `regular_child_completed`
   (normal completion path via `MessageJobHandler`). This calls
   `emit_terminal` with `status="completed"` and the **child's task
   id** resolved from the just-completed `message_id` via
   `_task_repo.get_by_message(message_id)`. This works for the
   happy path because the watcher is keyed on the same task id that
   ran.

2. `error_reporting._send_error_report` → calls
   `child_reports._emit_terminal_via_bus(task_id=..., status="error")`
   in the failure path. Same lookup by message_id. Fires
   `emit_terminal` with `status="error"`. Mirrors the success path.

### Why neither fires for cancelled-task-then-retry

The `force_cancel_and_schedule_retry` path in `StaleTaskRecovery`
deliberately cancels task 4464 and creates a new task 4466 for the
same message. There is **no `emit_terminal` call anywhere** on this
path:

```python
# daemon/services/stale_task_recovery.py:183-227
if current.status == TaskStatus.RUNNING.value:
    # Task still running after grace period — force cancel + retry atomically
    retry_task = self._task_repo.force_cancel_and_schedule_retry(
        task_id=task.id,
        max_retries=self._max_retries,
        reason=f"Stale task force-cancelled (>{self._threshold_minutes}min)",
        ...
    )
    if retry_task:
        # logs + bookkeeping — NO bus interaction
        ...
    else:
        # Max retries exceeded — permanent fail
        ...
        if self._on_task_permanently_failed:
            # Only fires on PERMANENT failure, NOT on retry-scheduled
            self._on_task_permanently_failed(...)
```

When the retry task 4466 eventually completes successfully:

- `_process_child_completion_and_notify_parent(ce27f76c, c85c73b4)` runs
- It looks up `_task_repo.get_by_message(c85c73b4)` → returns **task
  4466** (because the retry row replaces 4464 as the active row for
  this message in the task repository, or because 4464 is now
  terminal/cancelled and 4466 is the current one)
- It calls `_emit_terminal_via_bus(task_id=4466, status="completed")`
- The bus finds **zero** watchers for `source_task_id=4466` (the
  watcher is registered against 4464)
- No FollowUp is enqueued
- The watcher `29dc99be` remains PENDING forever

### The bug class — caller responsibility gap

The `DependencyBus` design documents itself as "SOLE completion
authority for parent→child correlation". The bus's `watch()` API is
keyed on `source_task_id` (a low-level worker task id). Higher-level
flows (cancelled-then-retry, message-id replacement, restart recovery)
all need to translate "this message id was retired; its replacement is
task N" into a bus emit — but that translation is missing.

There are three concrete code paths that need attention:

1. **`StaleTaskRecovery.recover_stale_tasks` — the retry-scheduled
   branch (lines 183-227).** When `force_cancel_and_schedule_retry`
   succeeds, it should fire `bus.emit_terminal(task_id)` with
   `status="cancelled"` to release the parent watchers **OR** cancel
   them via `bus.cancel_for_source(task_id)` so they don't strand
   the parent.

2. **`WorkerPool._handle_cancellation` — the timeout-then-scheduled-
   retry path (lines 547-579).** Same gap as #1. The retry path
   (after the grace-window poll) schedules a retry via
   `schedule_retry(...)` and never notifies the bus.

3. **Retry-on-restart path (`StaleTaskRecovery.recover_on_startup`,
   lines 318-356).** Same gap. Crash-recovery force-cancels and
   schedules a retry, but the bus watcher for the original task id
   is orphaned.

(The **permanent-failure** path in both places — where
`fail_task` is called instead of `schedule_retry` — is already
covered by the existing `_on_task_permanently_failed` callback
(commit `5d28fda7`), which routes to `_send_error_report` →
`bus.emit_terminal(status="error")`. The retry-scheduled path is
the one that's missing.)

---

## Impact

Every parent that has spawned a child task whose worker stops
heartbeating will end up in `waiting_children` forever, **even when
the retry succeeds and the child delivers a normal completion
report.** The leader's UI will show "waiting_children" indefinitely,
and the `count_pending_for_target` check will keep deferring
completion until the dangling watcher is manually cleared.

In production, this is user-visible: the leader never reaches
`COMPLETED`, no `task_done` lifecycle event fires, the
`CompletionRegistry` never gets signaled, and any
`invoke_agent_and_wait()` caller on the leader hangs.

The previous fix in `ebcbabd9` closed three other paths but did not
touch the cancel-and-retry path. This bug will continue to occur
roughly whenever the `StaleTaskRecovery` threshold (10min) is
exceeded — i.e., whenever any task runs longer than 10min without
heartbeating. Long LLM calls with `agentic` model and large message
contexts hit this regularly.

---

## Proposed fix

Two layers:

### 1. Code fix — `StaleTaskRecovery` and `WorkerPool`

Whenever a task is force-cancelled and a retry is scheduled, the bus
must be informed. Cleanest option: cancel the watchers for the
cancelled `source_task_id` so the parent sees them as resolved (no
FollowUp to enqueue — the retry will register fresh watchers if
needed, **OR** we can simply `emit_terminal(status="cancelled")` so
the bus fires its own FollowUp, which would be a duplicate of what
the retry will eventually produce).

The right design is:
- `bus.cancel_for_source(task_id)` — new method that transitions
  all PENDING watchers for `task_id` to `CANCELLED` (matches the
  existing `cancel_for_target` pattern but scans by `source_task_id`).
  The retry will register new watchers for itself; the cancelled
  ones no longer block.

Apply this in:
- `daemon/services/stale_task_recovery.py:recover_stale_tasks` —
  after `force_cancel_and_schedule_retry` succeeds (line ~200)
- `daemon/services/stale_task_recovery.py:recover_stale_tasks` —
  after `schedule_retry` succeeds (line ~244)
- `daemon/services/stale_task_recovery.py:recover_on_startup` —
  after `force_cancel_and_schedule_retry` (line ~327)
- `daemon/services/worker_pool.py:_handle_cancellation` — after
  `schedule_retry` (line ~555), but only if we are NOT going through
  `_handle_task_failure` or the permanent-fail path (those already
  call `_send_error_report` which fires the bus)

### 2. One-shot recovery — unblock `06f500af` right now

Until the code fix ships, run this SQL on production Postgres:

```sql
-- Mark the orphan watcher as cancelled so the leader can complete
UPDATE dependency_watchers
SET state = 'CANCELLED',
    fired_at = NOW()
WHERE watch_id = '29dc99be-3823-4aad-8858-3d207b59aab3';

-- The leader's next MESSAGE completion will now see count_pending_for_target == 0
-- and transition to COMPLETED. If there are no further messages,
-- trigger the transition manually:
UPDATE instances
SET status = 'completed',
    updated_at = NOW(),
    version = version + 1
WHERE instance_id = '06f500af-c60e-4d2a-b5c0-6464fc81aa85';
```

### 3. Add a startup sweep (defense in depth)

Add a one-shot sweeper in `DependencyBus.start()` (or
`StaleTaskRecovery.start()`) that finds `PENDING` watchers whose
`source_task_id` no longer corresponds to an active task
(status NOT IN ('running', 'pending', 'paused')) and transitions
them to `CANCELLED`. This protects against future code paths that
forget to inform the bus.

---

## Test plan

1. Unit test: `StaleTaskRecovery.recover_stale_tasks` after
   `force_cancel_and_schedule_retry` → bus `cancel_for_source` is
   invoked with the cancelled task id → watcher row is CANCELLED in
   the DB.
2. Unit test: Worker `_handle_cancellation` timeout-with-retry →
   bus `cancel_for_source` is invoked.
3. Integration: simulate a stale worker (no heartbeat for 11 min),
   force recovery, then assert the parent does NOT stay in
   `waiting_children` after the retry's completion report.
4. Regression: `tests/unit/test_pause_flow_redesign.py::
   test_pause_does_not_cancel_bus_watchers` — the proposed fix must
   NOT touch the pause path (paused tasks leave their watchers PENDING
   so resume can re-fire them).

---

## Open questions

- Should `cancel_for_source` use the bus's `transition_state` guard
  (PENDING-only) or also touch FIRED rows? Answer: PENDING-only —
  matches the existing `cancel_for_target` contract.
- Does the retry path register a fresh bus watcher for the new task
  id? If yes, then `cancel_for_source` is safe (we just release the
  old one). Need to verify `send_message` registers a fresh watcher
  when the retry runs.
