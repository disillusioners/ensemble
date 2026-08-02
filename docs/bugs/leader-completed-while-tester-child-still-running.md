# Bug: Leader Marked COMPLETED While Tester Child Still Running (Dependency Watcher Prematurely Cancelled)

**Date:** 2026-08-02
**Severity:** High
**Status:** Confirmed (investigation only — no code changes)
**Affected Component:** `daemon/services/dependency_bus.py`, `daemon/services/child_reports.py`, `daemon/services/instance_lifecycle.py`
**Environment:** Production (`ensemble_prod` PostgreSQL), backend on port 8088 / `logs/prod_run.log`

---

## Summary

Leader instance `7a0c990c-7cbf-4a98-b083-53e41618acc0` (agent `leader`, project `83da04de-...`, FIFO queue)
was marked **COMPLETED at 21:16:33** while a direct child instance, tester
`69e1faa5-e7b5-46dd-89ba-a4788333aa80` (`test-skill-search-interval`), was still running.
The tester did not actually finish until **21:44:38** — roughly 28 minutes later.
Job `f62a7fff...` finalized as `completed` at 21:16:34 with `instance_was_terminal=True`.

The root cause is a dependency-watcher tracking failure for **multi-turn children**: the
parent-→child completion watcher is keyed on the child's **first** `process_message` task id,
which becomes terminal long before the child instance reaches its own terminal graph turn.
A later cancellation path (`cancel_for_target` / orphan sweep) flipped that watcher to
`CANCELLED`, so `count_pending_for_target(leader) == 0` at root-completion time, even though
a live child was still working.

---

## Symptom (Log + DB Evidence)

### Timeline (UTC = local −7h; log timestamps are local)

```
21:02:07  spawn tester  69e1faa5  (parent=7a0c990c)  name=test-skill-search-interval
21:02:07  spawn reviewer f3df1e82 (parent=7a0c990c)  name=review-skill-search-interval
21:02:23  task 14213 (tester msg a346b6bb) started on 69e1faa5
21:06:45  task 14213 COMPLETED                                  ← watcher source_task becomes terminal

21:16:25  reviewer f3df1e82 reports completion to parent 7a0c990c
21:16:25  "Bus-active: skipping inline cascade for parent 7a0c990c — bus callback owns completion"
21:16:25  task 14237 (report ebf06b58) claimed for 7a0c990c
21:16:26  [LLM][7a0c990c] Invoking LLM with 268 messages
21:16:32  LLM response (review summary) emitted
21:16:33  _process_child_completion_and_notify_parent(7a0c990c, ebf06b58)
21:16:33  Instance 7a0c990c... parent_id=None, status=waiting_children
21:16:33  Instance 7a0c990c... no parent, skipping notification
21:16:33  Instance 7a0c990c... completed (no parent, no children), status=COMPLETED   ← BUG
21:16:34  Observer: finalized job f62a7fff status=completed (instance_was_terminal=True)

21:17:01  [LLM][69e1faa5] STILL RUNNING (tester continues after parent is "completed")
...
21:44:38  tester 69e1faa5 finally reaches COMPLETED
```

### DB state (PostgreSQL `dependency_watchers`, target = `7a0c990c`)

Only the watchers relevant to the bug window are shown.

| watch_id (prefix) | source_task_id | child_id (in payload) | state | fired_at | created_at |
|-------------------|----------------|-----------------------|-------|----------|-----------|
| `64d72154` | 14212 | f3df1e82 (reviewer) | **CANCELLED** | NULL | 21:02:23 |
| `ed4ef233` | 14213 | 69e1faa5 (tester)   | **CANCELLED** | NULL | 21:02:23 |
| `00c89128` | 14261 | 94196c60 (developer, still running) | **CANCELLED** | NULL | 21:44:56 |

Key observations:
- The tester watcher `14213` is **CANCELLED with `fired_at = NULL`**, even though the tester
  was still alive when the leader completed.
- Its backing task `14213` reached `completed` at 21:06:45 with `cancel_requested=f`,
  `retry_count=0` (`task` table) — i.e. it terminated **normally**, not via cancel/retry.
- `fired_at IS NULL` is the distinguishing signature: it rules out `_sweep_orphan_watchers`
  (which always sets `fired_at = now`) and points exclusively to `cancel_for_target` /
  `cancel_for_source` (both pass `fired_at=None`, see `dependency_bus.py:1069` / `:1168`).

### Instance table confirms the gap

```
7a0c990c | leader   |          | completed | updated 14:44:38.778959   (UTC)  ← 21:16:33 local
69e1faa5 | tester   | 7a0c990c | completed | updated 14:44:38.767302   (UTC)  ← 21:44:38 local
```

The leader and tester share a near-identical `updated_at` (the moment the tester's report
finally landed on the already-terminal leader and got re-flushed), but the leader's own
status flopped to `completed` ~28 minutes earlier, while the tester still had live LLM turns
(see `21:17:01 [LLM][69e1faa5] Invoking LLM`).

---

## Root Cause

### The two-factor root gate, and why it failed

A root (parent-leader) instance is allowed to transition to `COMPLETED` only when **both**
conditions hold (`daemon/services/child_reports.py:1338`–`1641`):

1. **Bus gate**: `bus.count_pending_for_target_sync(instance_id) == 0` — no PENDING
   `dependency_watchers` row for this parent.
2. **Own-queue gate**: no PENDING `message_queue` rows for this instance.

At 21:16:33 condition 1 was satisfied **incorrectly**: the tester's watcher
(`source_task_id=14213`) had already been moved out of `PENDING` — to `CANCELLED` — so the
count was 0 even though the tester was still running.

### Why the watcher was cancelled while the child was still alive

The dependency bus keys a parent-→child watcher on the child's **first** `process_message`
task id (the one registered at `send_message` time when the parent spawned the child):

```
daemon/repositories/dependency_bus/models.py  (DependencyWatcher.source_task_id)
daemon/services/dependency_bus.py:721         (emit_terminal_for_child_instance docstring)
```

This keying is **correct for single-turn children** (one task in → one terminal emit out)
but **incorrect for multi-turn children** such as the tester, which on this run:

1. Received its first message as task `14213` (started 21:02:23).
2. Spawned its own sub-children (e.g. explorer `bfcea5bd`, worker `4f13a3a7`) and processed
   their completion reports.
3. Reached its true terminal graph turn on **later** tasks (sub-report tasks), not on
   `14213`.

Task `14213` therefore went terminal at 21:06:45, while the child instance stayed alive until
21:44:38. The watcher keyed on `14213` was now backed by a "gone" task. Two production code
paths treat that as an orphan and cancel the watcher:

- **`cancel_for_target(instance_id)`** — called from `terminate_instance`
  (`daemon/services/instance_lifecycle.py:1550`). On ANY termination of the **parent**
  (`7a0c990c`), this blanket-cancels **all** PENDING watchers for that parent — including
  the ones tracking still-live children. The leadership model here repeatedly completes and
  reactivates its instances (`Reactivating terminal instance 7a0c990c...` appears ~11 times
  in the log), so any one of those termination cycles can cancel a live child's watcher.
- **`_sweep_orphan_watchers`** (`daemon/services/dependency_bus.py:1611`) — the startup
  sweep uses `source_task_id NOT IN (SELECT id FROM task WHERE status IN
  ('running','pending','paused'))`. For multi-turn children whose first task already
  completed, this predicate misclassifies the watcher as an orphan and would cancel it.

Because all three bug-window watchers (14212 / 14213 / 14261) share the same `target_instance_id`
(`7a0c990c`), a single `cancel_for_target(7a0c990c)` cancelled them together.

### Why the corrective emit did not save it

The bus ships a corrective path for exactly this multi-turn gap —
`emit_terminal_for_child_instance(parent, child, outcome)` at `dependency_bus.py:721` — which
matches a watcher by `(target_instance_id, follow_up_payload.metadata.child_id)` instead of
task id. It is designed to fire the watcher the task-keyed `emit_terminal` missed.

In this incident it was a **no-op**: by the time the child's final report reached the parent,
the row was already `CANCELLED`. The guarded `transition_state` primitive
(`WHERE state = 'PENDING'`) returned `rowcount == 0` and the fire was skipped, so the FollowUp
was never delivered through the bus. The reviewer's completion was instead delivered through
the **separate completion-report** path (task `14237` / message `ebf06b58`), which bypasses the
bus entirely — and that path, with the bus now empty, tripped the root-completion gate.

### Why no cancel log line exists in `prod_run.log`

Both `cancel_for_target` and `cancel_for_source` emit an **INFO** line when they actually
transition a row (`bus cancel_for_target: target=..., cancelled=N, cache_purged=N`). A full
grep of `logs/prod_run.log` for `cancel_for_target|cancel_for_source|cancel.*watcher`
returns **zero** matches, yet the rows are confirmed `CANCELLED` in PostgreSQL. Likewise
tasks `14212 / 14213 / 14237` show `cancel_requested=f, retry_count=0`, ruling out the
cancel-and-retry path.

Combined with the fact that watcher `14261` (child `94196c60`, a developer still `running`)
was observed transitioning from `PENDING` → `CANCELLED` between two successive read-only
queries (~21:44+), this means **`cancel_for_target(7a0c990c)` is being invoked by a process
that is not captured in `logs/prod_run.log`** — i.e. an external worker / the production
backend on port 8088 / an admin cleanup path sharing the same Postgres DB. All of the
leader's own LLM turns and spawns ARE in this log; only the cancellation side is not.

> **Open question for the fix investigation:** identify which process/endpoint issues
> `cancel_for_target(7a0c990c)` while the leader still has live children. Most likely
> candidates are the port-8088 production backend and any admin/terminate endpoint that
> calls `terminate_instance` on a parent that has merely gone momentarily terminal and is
> slated for reactivation.

---

## Code Positions

| File | Line(s) | Role |
|------|---------|------|
| `daemon/services/child_reports.py` | 1338–1461 | Root-completion bus gate — trusts `count_pending_for_target_sync == 0` |
| `daemon/services/child_reports.py` | 1339 | `all_children_done = bus.count_pending_for_target_sync(instance_id) == 0` |
| `daemon/services/child_reports.py` | 1417–1461 | Inline `DependencyWatcher` COUNT, then COMPLETED if 0 |
| `daemon/services/child_reports.py` | 1569–1573 | `no parent, no children, status=COMPLETED` fallthrough |
| `daemon/services/dependency_bus.py` | 462–549 | `watch()` — keys watcher on child's first task id |
| `daemon/services/dependency_bus.py` | 721–906 | `emit_terminal_for_child_instance` — corrective (parent, child)-keyed emit; no-ops if row already CANCELLED |
| `daemon/services/dependency_bus.py` | 1025–1098 | `cancel_for_target` — blanket-cancels all parent watchers; `fired_at=None` |
| `daemon/services/dependency_bus.py` | 1611–1745 | `_sweep_orphan_watchers` — startup sweep, misclassifies multi-turn child watchers as orphans |
| `daemon/services/instance_lifecycle.py` | 1547–1550 | `terminate_instance` calls `cancel_for_target(instance_id)` — wrong scope when parent is merely reactivatable |

---

## Question 2: Was It Caused by the Parallel Leader `f5716f6e`?

**No.** `f5716f6e-9b79-4bcf-a761-dba8dbb8d76d` (leader, project `…`, parallel queue) ran
concurrently and was completing its own children around the same time (e.g. bus
`emit_terminal: task_id=14219, fired=1` at 21:15:48), but it is **not** the cause:

1. **No watcher mis-routing.** The bug-window watchers all correctly target `7a0c990c`
   (`follow_up_payload.metadata.child_id` ∈ {`f3df1e82`, `69e1faa5`, `94196c60`}; target =
   `7a0c990c`). None are routed to `f5716f6e`.
2. **Bus isolation.** `dependency_watchers` are partitioned by `target_instance_id`, and the
   bus uses per-parent locks (`_get_parent_lock`). Any `emit_terminal` / `cancel_for_target`
   issued by `f5716f6e` only touches rows whose `target_instance_id = f5716f6e`; it cannot
   touch `7a0c990c`'s rows.
3. **`f5716f6e` behaved correctly.** At 21:17:34 it logged
   `completed message but waiting for 1 children (bus=True), deferring completion` — its
   completion logic held correctly and did not bleed across to `7a0c990c`.

The two leaders share only the process and the singleton bus with per-parent locks; no
cross-instance corruption was found. The premature completion is **internal to `7a0c990c`'s
own watcher lifecycle** — the parallel run of `f5716f6e` is coincidental, not causal.

---

## Impact

- The leader went terminal 28 minutes before its tester child finished. Any state expecting
  the parent to be alive across the child's lifetime (e.g. watching for the tester's final
  verdict, driving follow-up spawns off the tester result) was operating against a
  "completed" parent.
- The leader's COMPLETED status forced the worker pool / observer to finalize job `f62a7fff`
  as completed with `instance_was_terminal=True`, even though logical work was outstanding.
- The tester's eventual completion report landed on an already-terminal parent (it required
  reactivation at 21:17:22 for a related message — `Reactivating terminal instance cb54ac85`),
  a fragile path that depends on reactivation working perfectly.

---

## Suggestions (Investigation Only — Not Implemented)

### A. Stop blanket-cancelling live children's watchers on parent terminate

`instance_lifecycle.py:1547–1550` calls `cancel_for_target(instance_id)` unconditionally on
every `terminate_instance`. For a leadership pattern where a parent is momentarily terminal
and later reactivated, this cancels watchers tracking children that are **still running**.

Propose: only cancel watchers whose **child instance** is also terminal. Concretely, before
`cancel_for_target(parent)`, intersect pending watchers with the set of child instances still
in a non-terminal status; exclude those whose child is `running` / `waiting_children` /
`paused`. This keeps the original orphan-cleanup goal (a truly-dead parent should not receive
a late FollowUp) without destroying correlation data for live children.

### B. Key multi-turn-child watchers on (parent, child), not the first task id

The root defect is that a watcher's lifetime is bound to the child's **first** `process_message`
task, which for multi-turn children terminates early. Options:

1. **Re-key the watcher** on `(target_instance_id, child_id)` (the same pair
   `emit_terminal_for_child_instance` already uses), and make `emit_terminal` resolve by
   child instance, not by task.
2. Or **re-arm watchers on child task transition**: when a child's task completes but the
   child instance is still non-terminal, re-register the pending watcher against the child's
   next active task instead of leaving it backed by a "gone" task.

### C. Harden the orphan sweep for multi-turn children

`_sweep_orphan_watchers` (`dependency_bus.py:1611`) classifies a watcher as an orphan when
its `source_task_id` is not in `('running','pending','paused')`. For multi-turn children the
first task legitimately leaves that set while the child instance is still alive. Join the
sweep predicate against the **child instance** status
(`instances.status NOT IN (terminal set)`) via the watcher's
`follow_up_payload.metadata.child_id`, so a watcher whose child instance is still live is
never cancelled.

### D. Add a live-children cross-check to the root-completion gate

`child_reports.py:1417–1461` currently trusts the bus count alone. Add a redundant check
against the `instances` table:

```sql
SELECT count(*) FROM instances
WHERE parent_id = :instance_id
  AND status NOT IN ('completed','error','cancelled')
```

A root with live children should never reach `COMPLETED`, even if the bus reports zero
pending watchers. This is the defense-in-depth that would have prevented this incident
regardless of which watcher-cancel path silently fired.

### E. Log every watcher state transition authoritatively

There is **no log line** for the `CANCELLED` transition that caused this bug. Both
`cancel_for_target` and `cancel_for_source` only log when `count > 0`, and even then only at
INFO — easy to lose across worker processes. Add a structured log (with `extra={watch_id,
source_task_id, target_instance_id, child_id, path}`) on **every** `transition_state` →
`CANCELLED` so external-process cancels are observable and attributable.

### F. Confirm the external cancel source

Identify which process issues `cancel_for_target(7a0c990c)` while the leader still has live
children. It is NOT in `logs/prod_run.log`. Most likely candidates: the port-8088 production
backend, an admin/terminate endpoint, or a cleanup hook that calls `terminate_instance` on a
momentarily-terminal parent. Either route its logs into `prod_run.log` or have the bus emit a
CRITICAL-level line when it cancels a watcher whose child instance is still non-terminal.

---

## Reproduction / Verification Queries (read-only)

```sql
-- Watchers for the prematurely-completed leader, bug window
SELECT substring(source_task_id,1,12) AS src_task, state, created_at, fired_at,
       follow_up_payload->'metadata'->>'child_id' AS child_id
FROM dependency_watchers
WHERE target_instance_id = '7a0c990c-7cbf-4a98-b083-53e41618acc0'
ORDER BY created_at;

-- Backing task state for those watchers
SELECT id, task_type, substring(instance_id,1,8) AS inst, status,
       cancel_requested, retry_count, created_at, completed_at
FROM task
WHERE id IN (14212, 14213, 14237, 14261)
ORDER BY id;

-- Instance statuses at the bug moment
SELECT instance_id, agent_id, parent_id, status, created_at, updated_at
FROM instances
WHERE instance_id IN ('7a0c990c-7cbf-4a98-b083-53e41618acc0',
                      '69e1faa5-e7b5-46dd-89ba-a4788333aa80',
                      'f3df1e82-ee01-483e-993c-14ff94aa472a');
```
