# Bug: Leader Marked COMPLETED While Tester Child Still Running (Dependency Watcher Prematurely Cancelled)

**Date:** 2026-08-02
**Severity:** High
**Status:** Confirmed — root cause identified (investigation only — no code changes)
**Affected Component:** `daemon/repositories/task/repository.py` (`reconcile_turn_mirror`), `daemon/services/dependency_bus.py`, `daemon/services/child_reports.py`
**Environment:** Production (`ensemble_prod` PostgreSQL), backend on port 8088 / `logs/prod_run.log`

> **Update (2026-08-02):** Root cause refined. The earlier draft attributed the stray
> `CANCELLED` transition to an external process calling `cancel_for_target`. DB/code
> forensics later isolated the actual writer: a **raw bulk SQL** inside
> `reconcile_turn_mirror` (`daemon/repositories/task/repository.py:692-709`) that runs on
> **every** child-task completion and cancels watcher rows whose **parent** is momentarily
> idle (`waiting_children` has zero `pending/running/paused` tasks by design). See the
> "Root Cause (Definitive)" section. The earlier "external process" hypothesis is
> **superseded**.

---

## TL;DR

`reconcile_turn_mirror` (`daemon/repositories/task/repository.py:692-709`) runs on every
child-task completion and runs a bulk `UPDATE dependency_watchers SET state='CANCELLED'`
guarded by **parent-task liveness** (`NOT EXISTS task WHERE target_instance_id = parent AND
status IN ('pending','running','paused')`). A parent that is `waiting_children` has
**zero** such tasks **by design** — it is idle, waiting for the child's report. So the guard
fires on every idle-waiting parent and cancels the **live** child's watcher. When the child
finally reports, `count_pending_for_target(parent) == 0` and the root gate in
`child_reports.py` marks the parent COMPLETED even though the child is still running.

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

## Root Cause (Definitive)

### The smoking gun — `reconcile_turn_mirror` raw SQL

`daemon/repositories/task/repository.py`, method `reconcile_turn_mirror`, lines **692-709**
(called on every task completion via `complete_task` at line 1558):

```sql
UPDATE dependency_watchers
SET state = 'CANCELLED'              -- no fired_at set → stays NULL
WHERE source_task_id = CAST(:task_id AS TEXT)
  AND state = 'PENDING'
  AND :terminal                        -- the completing task is terminal
  AND {snapshot_guard}                -- task row still in the snapshotted terminal status
  AND NOT EXISTS (
      SELECT 1 FROM task AS target_task
      WHERE target_task.instance_id = dependency_watchers.target_instance_id  -- the PARENT
        AND target_task.status IN ('pending', 'running', 'paused')
  )
```

**Signature match.** This SQL:
- sets `state = 'CANCELLED'` with **no `fired_at`** write → `fired_at` stays **NULL** (matches
  the observed `fired_null=t` on all three bug-window watchers);
- runs **silently** — it is a raw `text()` Core execute in the task repository, never going
  through the bus service layer, so neither the
  `bus cancel_for_target: target=..., cancelled=N` INFO line nor the
  `instance_lifecycle.{op}: cancelled N dependency watcher(s)` line is emitted (this is why a
  full grep of `prod_run.log` for `cancel_for|cancel.*watcher` returns **zero** matches).

### Why the guard is wrong

The `NOT EXISTS` subquery checks whether the **parent** (`target_instance_id`) has any
`pending`/`running`/`paused` task. The author's intent was "don't cancel if the parent is
mid-turn." But for a leader that has spawned children and is now **waiting for their reports**,
the correct status is `waiting_children` — and a `waiting_children` parent has **zero** tasks
in `pending`/`running`/`paused` by design (its tasks all completed when it deferred
completion). So the guard evaluates **TRUE** for *every* idle-waiting parent, and the
child's still-live watcher gets cancelled the instant the child's task completes.

The guard should be keyed on **child-instance** liveness, not parent-task liveness (see
"Suggestions" below).

### The exact reproduction in this incident

Leader `7a0c990c` task timeline (from PostgreSQL `task` table) shows a **~14-minute idle gap**:

| Task | Type | Completed |
|------|------|-----------|
| `14211` | process_report | **21:02:32** (last active before gap) |
| `14237` | process_report | 21:16:25 (next — after a child reported) |

Between 21:02:32 and 21:16:25 the leader was `status=waiting_children` with **zero**
`pending`/`running`/`paused` tasks. When each child's first task completed, the post-commit
`reconcile_turn_mirror(work_id)` ran and cancelled the still-live child's watcher:

| Child first-task completes | Watcher cancelled | Child instance still running until |
|---|---|---|
| reviewer `f3df1e82` task `14212` @ 21:03:56 | watcher `14212` (`64d72154`) → **CANCELLED**, `fired_at=NULL` | 21:16:25 |
| tester `69e1faa5` task `14213` @ 21:06:45 | watcher `14213` (`ed4ef233`) → **CANCELLED**, `fired_at=NULL` | 21:44:38 |

Both children were genuinely alive on their **later** sub-report tasks (the tester spawned
explorer `bfcea5bd` + worker `4f13a3a7` and continued LLM turns as late as `21:17:01`), but
their **first** task was done — which is all this SQL keys on. The third bug-window watcher
`14261` (`00c89128`, child `94196c60` developer, never terminal until 21:44:38) was likewise
cancelled when developer's first task completed.

### The two-factor root gate, and why it failed

A root (parent-leader) instance is allowed to transition to `COMPLETED` only when **both**
conditions hold (`daemon/services/child_reports.py:1338`–`1641`):

1. **Bus gate**: `bus.count_pending_for_target_sync(instance_id) == 0` — no PENDING
   `dependency_watchers` row for this parent.
2. **Own-queue gate**: no PENDING `message_queue` rows for this instance.

At 21:16:33 condition 1 was satisfied **incorrectly**: both the tester's (`14213`) and
reviewer's (`14212`) watchers had already been moved out of `PENDING` — to `CANCELLED` by the
`reconcile_turn_mirror` SQL above — so the count was 0 even though both children were still
running (the tester until 21:44:38, ~28 min later).

### Why the corrective emit did not save it

The bus ships a corrective path for multi-turn children —
`emit_terminal_for_child_instance(parent, child, outcome)` at `dependency_bus.py:721` — which
matches a watcher by `(target_instance_id, follow_up_payload.metadata.child_id)` instead of
task id. It is designed to fire the watcher the task-keyed `emit_terminal` missed.

In this incident it was a **no-op**: by the time the child's final report reached the parent,
the row was already `CANCELLED` (by `reconcile_turn_mirror`). The guarded `transition_state`
primitive (`WHERE state = 'PENDING'`) returned `rowcount == 0` and the fire was skipped, so
the FollowUp was never delivered through the bus. The reviewer's completion was instead
delivered through the **separate completion-report** path (task `14237` / message
`ebf06b58`), which bypasses the bus entirely — and that path, with the bus now empty,
tripped the root-completion gate.

### Why the earlier "external process" hypothesis was wrong

The earlier draft inferred an external cancel because:
- the bus's `cancel_for_target` logs nothing, yet rows are `CANCELLED`;
- watcher `14261` was observed flipping `PENDING → CANCELLED` between two read-only queries.

Both are fully explained by `reconcile_turn_mirror`:

1. It writes `dependency_watchers` directly via the task repository, **bypassing the bus
   service layer entirely** — so the bus's `cancel_for_target` / `cancel_for_source` INFO log
   is correctly absent. The cancel is "silent" only relative to the bus logger; the SQL itself
   is perfectly normal code that runs on every task completion.
2. The late flip of `14261` corresponds to developer `94196c60`'s first task completing at
   21:44:56 — which is when `reconcile_turn_mirror(work_id)` ran for that task and cancelled
   its watcher. Single in-process thread of causation; no external actor required.

A `ps aux` confirmed a single backend process (`./ensemble-prod`, PID 29388 — the same one
emitting `prod_run.log`) was the only writer. No second uvicorn/worker sharing the DB was
found.

### The contributing factor — task-id keying (secondary, not the trigger)

The bus keys a parent-→child watcher on the child's **first** `process_message` task id
(`daemon/services/dependency_bus/models.py` `DependencyWatcher.source_task_id`; registered at
`send_message` time in `daemon/tools/instance._send_message`). This is **correct for
single-turn children** (one task in → one terminal emit out) but **incomplete for multi-turn
children**: the child's first task terminates long before the child instance reaches its own
terminal graph turn.

This keying is a **latent fragility** that *widens the window* in which a cancel/sweep can
wrongly fire — but it is **not itself the trigger**. The trigger is the wrong guard in
`reconcile_turn_mirror`. Even if watchers were keyed on `(parent, child_instance)` instead of
the first task id, the `reconcile_turn_mirror` SQL (which guards on **parent** task liveness,
not child liveness) would still cancel them whenever the parent is momentarily idle. Put
differently: the keying determines *which watcher row* becomes vulnerable; the guard
determines *whether it actually gets cancelled*. The fix priority is the guard.

---

## Code Positions

| File | Line(s) | Role |
|------|---------|------|
| `daemon/repositories/task/repository.py` | **692-709** | ** THE BUG ** — `reconcile_turn_mirror` bulk-cancels watcher rows guarded on **parent** task liveness; fires on every idle `waiting_children` parent |
| `daemon/repositories/task/repository.py` | 1558 | post-commit `reconcile_turn_mirror(work_id)` call from `complete_task` (the trigger on every child-task completion) |
| `daemon/services/child_reports.py` | 1338-1461 | Root-completion bus gate — trusts `count_pending_for_target_sync == 0` |
| `daemon/services/child_reports.py` | 1339 | `all_children_done = bus.count_pending_for_target_sync(instance_id) == 0` |
| `daemon/services/child_reports.py` | 1417-1461 | Inline `DependencyWatcher` COUNT, then COMPLETED if 0 |
| `daemon/services/child_reports.py` | 1569-1573 | `no parent, no children, status=COMPLETED` fallthrough |
| `daemon/services/dependency_bus.py` | 462-549 | `watch()` — keys watcher on child's first task id (contributing factor, not trigger) |
| `daemon/services/dependency_bus.py` | 721-906 | `emit_terminal_for_child_instance` — corrective (parent, child)-keyed emit; no-ops if row already CANCELLED |
| `daemon/services/dependency_bus.py` | 1025-1098 | `cancel_for_target` — blanket-cancels all parent watchers; `fired_at=None` (investigated, ruled out as the trigger in this incident) |
| `daemon/services/dependency_bus.py` | 1611-1745 | `_sweep_orphan_watchers` — startup-only sweep (sets `fired_at=now`; rules it out via the `fired_at IS NULL` signature) |
| `daemon/services/instance_lifecycle.py` | 1547-1550 | `terminate_instance` calls `cancel_for_target(instance_id)` (investigated, ruled out — no terminate/pause of 7a0c990c logged in the bug window) |

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

Priority-ordered by how directly each addresses the confirmed trigger.

### A. (PRIMARY FIX) Re-key the `reconcile_turn_mirror` cancel guard on child-instance liveness

`daemon/repositories/task/repository.py:692-709`. The current guard's `NOT EXISTS` subquery
checks the **parent's** (`target_instance_id`) task liveness. Replace it with a check on the
**child instance** liveness, resolved through the watcher's
`follow_up_payload->'metadata'->>'child_id'`. A watcher must only be cancelled when the **child**
is already terminal — never while it is `running` / `waiting_children` / `paused`.

Conceptual replacement for the `NOT EXISTS (...)` clause:

```sql
AND NOT EXISTS (
    SELECT 1
    FROM instances ci
    WHERE ci.instance_id = (
        SELECT dw2.follow_up_payload->'metadata'->>'child_id'
        FROM dependency_watchers dw2
        WHERE dw2.watch_id = dependency_watchers.watch_id
    )
      AND ci.status NOT IN ('completed', 'error', 'cancelled')
)
```

i.e. the watcher survives as long as its child instance is non-terminal, regardless of
whether the parent is momentarily idle. This is the single change that would have prevented
this incident — it removes the wrong predicate that caused every `waiting_children` parent's
live-child watcher to be cancelled on the child's first-task completion.

### B. (DEFENSE-IN-DEPTH) Add a live-children cross-check to the root-completion gate

`daemon/services/child_reports.py:1417-1461` currently trusts the bus count alone. Add a
redundant check against the `instances` table:

```sql
SELECT count(*) FROM instances
WHERE parent_id = :instance_id
  AND status NOT IN ('completed','error','cancelled')
```

A root with live children should never reach `COMPLETED`, even if the bus reports zero pending
watchers. This catches the bug class regardless of which cancel path (A, the orphan sweep,
`cancel_for_target`, or any future writer) silently fired.

### C. Log every `dependency_watchers` `PENDING → CANCELLED` transition authoritatively

The trigger that caused this bug wrote `state='CANCELLED'` via a raw `text()` Core execute in
the task repository and was therefore **invisible** to the bus service-layer logger. Add a
structured log (with `extra={watch_id, source_task_id, target_instance_id, child_id,
path='reconcile_turn_mirror'}`) on this SQL path (and any other raw writer) so future
silent cancels are observable and attributable. Without this, the only forensic signal is the
`fired_at IS NULL` row-state signature after the fact.

### D. (SECONDARY — reduces blast radius) Stop blanket-cancelling on parent terminate

`daemon/services/instance_lifecycle.py:1547-1550` calls `cancel_for_target(instance_id)`
unconditionally on every `terminate_instance`. For leaders that complete-and-reactivate
(`Reactivating terminal instance 7a0c990c...` appears ~11× in this run alone), this
blanket-cancels watchers tracking still-running children. Rule out the cause in this
incident (no terminate of `7a0c990c` was logged in the bug window), but it remains a latent
variant of the same bug class: before cancelling, intersect pending watchers with the set of
child instances still non-terminal and exclude those.

### E. (SECONDARY — latent fragility) Key multi-turn-child watchers on (parent, child)

The first-task-id keying is the **contributing factor**, not the trigger. It widens the
vulnerability window (between "child's first task done" and "child instance done") but would
not by itself have cancelled the row — fix A is what would have stopped it. If pursued,
options:

1. Re-key the watcher on `(target_instance_id, child_id)` and make `emit_terminal` resolve by
   child instance, not by task.
2. Or re-arm watchers on child task transition: when a child's task completes but the child
   instance is still non-terminal, re-register the pending watcher against the child's next
   active task.

### F. (SUPERSEDED) Confirm the external cancel source

> **Superseded by the definitive root cause (fix A above).** The earlier draft left an open
> question about an external process issuing `cancel_for_target`. Investigation closed it:
> the cancel came from the in-process `reconcile_turn_mirror` SQL, which bypasses the bus
> service-layer logger — hence the missing `cancel_for_target` INFO line. A `ps aux`
> confirmed a single backend process (`./ensemble-prod`, PID 29388 = the writer of
> `prod_run.log`) was the only writer to the DB. No external actor is required to explain
> the incident.

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

-- The leader's task timeline around the bug — note the 21:02:32 → 21:16:25 idle gap
-- (the leader is `waiting_children` with ZERO pending/running/paused tasks in that window,
--  which is exactly the predicate that lets reconcile_turn_mirror's cancel guard fire).
SELECT id, task_type, status, started_at, completed_at
FROM task
WHERE instance_id = '7a0c990c-7cbf-4a98-b083-53e41618acc0'
  AND id BETWEEN 14211 AND 14237
ORDER BY id;

-- Confirm single-writer: only one backend process should be touching the DB.
-- On the prod host:  ps aux | grep -E 'uvicorn|ensemble-prod' | grep -v grep
```
