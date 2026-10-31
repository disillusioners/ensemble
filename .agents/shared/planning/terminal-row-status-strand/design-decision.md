# Terminal-Row Status Strand — Design Decision

Commission: implement the 9f0ed4f5 incident family fix (root instance row
stays `completed` while its child tree is live). Diagnosis is the BINDING
design; this document records the three touchpoints, the D2.7 override,
the mirror interplay, and disclosures.

## Premise (the 9f0ed4f5 write ledger)

- `10:09:33` — enqueue flip (`_prepare_enqueued_message`, instance_messaging.py
  ~:2569): terminal → RUNNING. (Untouched — early leg.)
- `10:35:01` — gate-passed closure: root stamped COMPLETED at a quiet
  moment (completion stamp in `_process_child_completion_db_sync`, root
  lane; pre-stamp recursive liveness eval returned not-live, OR fail-open).
- Afterwards the child tree went live again (a child resumed / a late
  report-delivery turn spawned a new descendant). Subsequent
  PROCESS_REPORT delivery turns drove the root's graph with NO status
  write at all — `task_processor.py:441-457` →
  `_process_message_with_tracking` instance_messaging.py:3787, which has
  ZERO status writes. The hold branch (child_reports.py:2866-2910)
  correctly skipped the COMPLETED re-stamp but returned with NO positive
  write. The idempotency guard (child_reports.py:3208-3222) structurally
  blocked the WAITING_CHILDREN writers (:3458/:3516/:3637) on a terminal
  row, so a re-entered completion drive could not re-arm it.

Result: root row COMPLETED while the child tree is live — stranded.

## Three touchpoints

### 1. TURN-START CHOKEPOINT (instance_messaging.py `_process_message_with_tracking`)

Terminal → RUNNING reactivation at the TOP of the function, before meta
parsing and before `get_instance`. Delegates to the
`manager._revive_terminal_instance` pattern (manager.py:10437-10521):
PAUSED-exempt (PAUSED is not in the terminal set), guarded row write
with explicit commit, version bump + `last_activity_at` refresh. This is a
ROW WRITE, not a revive decision: no revive budget consumed, F7
revivable-terminal semantics untouched. Best-effort fail-open (a failed
flip never blocks the turn).

Covers every lane that bypasses the enqueue-time flip — the
PROCESS_REPORT delivery Task (minted in the child's completion txn at
manager.py:11156/:11224; the claim gate at task/repository.py:3022-3023
excludes only PAUSED/TERMINATED, so a COMPLETED root's report Task IS
claimable), cascade-resume, and resume_processing_job. All converge on
this funnel (manager.py:10276-10278). The enqueue-time flip at ~:2569 is
untouched.

Disclosure (A-2): the chokepoint revive does NOT reset
`attestation_denied_count` or `completion_gate_escalated` — it mirrors
the internal-report carve-out at instance_messaging.py:2586-2605, where
only a fresh user-driven episode resets those counters. Consequence: a
revived escalated root now runs gate-bearing turns with the escalated
flag still set (the gate logic reads the flag; the revive does not clear
it).

### 2. HOLD BRANCH (child_reports.py pre-stamp recursive-liveness hold)

Where the hold currently does a bare `return` after
`_schedule_strand_heal` (child_reports.py:2866-2910), a FRESH re-read of
the row (the `instance_meta` read at the top of the drive can be seconds
stale) gates a positive WAITING_CHILDREN write: ONLY when the row reads
terminal. The membership set (hoisted to `_TERMINAL_REARM_STATUSES`,
A-6) is `(COMPLETED, ERROR, FAILED)` — TERMINATED is EXCLUDED (M-2):
the claim gate excludes PAUSED/TERMINATED for all task types
(task/repository.py:3006-3015), so a re-armed TERMINATED root would become
claimable — a NEW resurrection path that even touchpoint 1 (unreachable
for terminated rows) would never trigger, contrary to explicit operator
intent. Non-terminal rows (the historical `running` hold) keep the
4ebd7f2f1 no-churn semantics — NO write, no version bump.

The healer hand-off runs AFTER a successful re-arm with the LIVE posture
(`InstanceStatus.WAITING_CHILDREN.value`); a non-re-armed row keeps the
historical posture (`instance_meta.status`) — same call, same coalescing
as 4ebd7f2f1 (M-1). The write reuses `_fp4_l6_downgrade_to_waiting`
(child_reports.py:2290-2315), extended with a keyword-only
`allowed_statuses` (default COMPLETED-only, so the three existing L6 call
sites stay byte-identical).

Note (A-5): `_fp4_l6_downgrade_to_waiting` writes via a raw
`Session(self._manager._engine)` (child_reports.py:~2318), outside a
`WriteGuardSession` — this was already the L6 contract and is unchanged
here; this fix adds a hot-path caller but does not re-plumb the guard.

### 3. IDEMPOTENCY GUARD (child_reports.py `_process_child_completion_db_sync`)

The guard (child_reports.py:3208-3222) is terminal-aware: when the row
reads terminal (COMPLETED / ERROR — the guard's existing membership) AND
live non-ghost direct children exist (the same-tx COUNT used by the
:3516 writer), write WAITING_CHILDREN and return
`deferred_waiting_children` instead of `idempotency_skip`. All-terminal
trees keep the skip — a genuinely-completed row stays completed. The
recursive live-tree shape (depth-2+) for ROOT rows is owned by the
pre-stamp hold (touchpoint 2), which never reaches this guard on a live
tree; non-root mid-tree rows still return `idempotency_skip` on a
grandchild-only live tree, because the pre-stamp hold is root-gated
(A-4). COUNT failure fails to the skip (the guard is a dedup net, not
the completion decision — a transient DB error must not churn a
genuinely-completed row).

## D2.7-LOCKED override rationale (narrow)

`report_integrity_guard.py` declines `WAITING_CHILDREN` as a control-flow
signal (D2.7 LOCKED, 2026-08-30): the declared-waiting shape lives on
the durable report / watcher rows, not on the instance status column.
This fix NARROWLY OVERRIDES that decline at the two completion-writer
sites above, and ONLY as the row's visible liveness surface for live
trees ("the tree below me is working"). It is NEVER an input to the (b)
predicate, which stays status-blind, pure, and content-blind (D2.18). The
detect/notify tripwire (this module) keeps firing unchanged — it remains
the regression signal that the override's conditions are being hit. The
override is documented at report_integrity_guard.py (the "Why NOT
instances.status = WAITING_CHILDREN" paragraph) and here.

## Mirror interplay (INTENDED)

With the fix live, a root re-armed to a live status (running /
waiting_children) keeps the wrapping JobItem mirror from settling to
`done`: `_LIVE_INSTANCE_NOT_EXISTS_GUARD` (task/repository.py:338-355)
counts `waiting_children` / `paused` / `running` as live, so the
reconciler's `job_queue_items` handler does not stamp
`admission_state='done'` while the root is live. The mirror now tracks
the live root. This couples to the reconciler path (task/repository.py
:2236-2314). Pinned as INTENDED in tests/repositories/test_turn_reconciler.py
(`TestWaitingChildrenGuard.test_running_root_keeps_jobitem_active` and
`test_waiting_children_root_keeps_jobitem_active`).

## Disclosures

- Touchpoint 1 adds one DB read (`_instance_repository.get`) per
  dispatched turn. Acceptable — the contract mandates the turn-start
  chokepoint; the read is the only way to gate on the current row status.
- `_revive_terminal_instance` is shared by two lanes; its docstring now
  documents both. No signature change; no revive-budget consumption.
- The hold branch's fresh re-read is a second read on top of
  `instance_meta` (read at the top of the drive). The staleness window
  between them is small (content fetch in between); the re-read closes
  the 9f0ed4f5 race where the row was terminal at hold time but the top-of-drive
  read was stale.
- Touchpoint 3's COUNT is direct-children only (same as the :3516
  writer). The recursive (depth-2+) shape for ROOT rows is owned by the
  pre-stamp hold (touchpoint 2), which never reaches this guard on a live
  tree. A depth-2+-only live tree that somehow reaches the guard returns
  the skip — a residual, but the hold upstream is the primary net for
  that shape.
- 3 pre-existing failures in tests/repositories/test_turn_reconciler.py
  (test_missing_task_sets_admission_done_orphan_reason,
  test_missing_task_deletes_lock, test_hard_deleted_task_watcher_deleted)
  are present on the base commit and are unrelated to this change.
- Daemon-only: no agents/ files touched; prompt-artifact baseline unchanged.
- No merge / bump / tag / stage / push performed (giter owns v0.28.2
  finalize).
PY
echo written