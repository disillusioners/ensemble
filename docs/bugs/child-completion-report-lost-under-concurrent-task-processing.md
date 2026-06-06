# Bug: Child Completion Report Lost from Parent LLM Context Under Concurrent Task Processing

**Date:** 2026-06-06
**Severity:** High (silent — no error, just wrong LLM output)
**Status:** Confirmed (root cause identified, fix not implemented)
**Affected Component:** `daemon/services/worker_pool.py` + `daemon/repositories/task/repository.py` (no per-instance serialization)
**Symptom also observable in:** `daemon/services/child_reports.py` (lost update on `waiting_for`)

---

## Summary

When a parent instance has multiple `process_message` tasks in its queue and two tasks run **concurrently** for the same parent (e.g., the original human-message task and a child's completion-report task), the parent's langgraph checkpoint state can become inconsistent: messages that are correctly stored in `checkpoint_writes` are not visible to a later LLM call on the same thread.

In the observed production case, the parent (4f5cc5fd) produced the final response:

> *"Excellent exploration report. Now I'm waiting for the giter to confirm the branch creation before starting the planning workflow..."*

…even though the giter's completion report had been written to the channel **35 minutes earlier** and was acknowledged by an intermediate LLM turn (*"Good, git is set up. Now waiting for the exploration report from the coder."*). The LLM at the final call did not see the giter's report.

The bug is **rare** because it requires:
1. A parent to have ≥2 pending `process_message` tasks at once
2. Both tasks to be claimed by **different** workers before the first finishes
3. The LLM call of the second task to fire while the first is still mid-stream

In normal operation, the parent's `process_message` task is in flight while its child's report is enqueued; the report task is usually processed **after** the parent's main task completes (resuming from checkpoint), so concurrency is avoided. The race fires when timing lines up so the worker pool claims both back-to-back.

---

## Symptoms (Production Evidence)

### User-observed
- Parent's final assistant response references a child whose completion report has already been delivered to its queue.
- From the caller's perspective, the parent appears "stuck waiting" for a child that already finished.
- No error in logs; the message is in the DB, the task is in the `task` table with `status=completed`.

### Concrete timeline from `4f5cc5fd-99d7-4133-9eca-2de08f951ace`

| Time (UTC+7) | Event |
|---|---|
| 12:52:30 | Task 108 created (human message `d963568b`), worker-1 starts |
| 12:52:44–12:53:21 | 3 tool invocations complete synchronously (explorers, `invoked_as_tool=true`) |
| 12:53:26 | `send_message` to 69b6a850 (coder) → `waiting_for=0→1` |
| 12:53:40 | `send_message` to e349b270 (giter) → `waiting_for=1→2` |
| 12:54:22 | Tool invocation 12d2b495 starts |
| 12:54:24 | Giter completes → `waiting_for=2→1`, **report enqueued** as message `bacf6f31`, task 119 created |
| 12:54:24–12:57:06 | 12d2b495 tool invocation still running |
| **12:56:12** | **worker-2 claims task 119 (giter's report) while worker-1 is still on task 108** |
| 12:56:12 | Parent's LLM invoked with 2 messages (task 119, model=agentic) |
| 12:56:12 | Parent's LLM invoked with 16 messages (task 119, model=agentic) |
| 12:56:16 | Task 119 calls `explore` tool — giter report is in context here |
| 12:57:06 | 12d2b495 tool invocation completes |
| 12:57:26 | Task 119 completes (giter's report processed) |
| 12:57:38 | Task 108 finally completes (parent's original human message) |
| 13:29:04 | Coder completes → `waiting_for=1→0`, report enqueued as `2c312178`, task 122 created |
| 13:29:04 | worker-3 claims task 122, LLM invoked |
| 13:29:29 | **Final LLM response written to checkpoint:** *"Excellent exploration report. Now I'm waiting for the giter to confirm the branch creation…"* |
| 13:29:29 | Parent transitions to `COMPLETED`, waiting_for=0 |

**Key overlap:** task 108 (12:52:30–12:57:38) and task 119 (12:56:12–12:57:26) ran in **parallel for 1m14s** on different workers, both targeting the same `thread_id='4f5cc5fd-...'`.

---

## Database Evidence

### Message queue (parent 4f5cc5fd)

| message_id | source | type | status | enqueued | completed |
|---|---|---|---|---|---|
| `d963568b` | api | human | completed | 12:52:30 | 12:57:38 |
| `bacf6f31` | `internal_report:e349b270:096d1355` | completion_report | **completed** | 12:54:24 | 12:57:26 |
| `2c312178` | `internal_report:69b6a850:1d456d46` | completion_report | completed | 13:29:04 | 13:29:29 |

Both child reports were created, both reached `status=completed`, both `task` rows are `status=completed`. From the DB's perspective nothing is missing.

### Checkpoint writes (`messages` channel)

The giter's report write exists at:
- `checkpoint_id=1f1616c6-ea09-6fc2-800e-4a39bd8e84df`
- `task_id=73060abd-60ca-d72a-60cb-705c51bcd094`
- `channel='messages'`, `idx=0`

### Full reconstructed message history (24 messages)

| # | Type | ID | Notes |
|---|---|---|---|
| 0 | human | `d963568b` | Original user request |
| 1 | ai | — | "I'll start by exploring…" |
| 2–4 | tool | — | 3 explorer tool-invocation results |
| 5 | ai | — | "Good context. Now I need to understand…" |
| 6 | tool | `a5514f96` | spawn 69b6a850 (coder) |
| 7 | ai | — | (empty) |
| 8 | tool | `df48ce40` | "Message queued and sent to 69b6a850" |
| 9 | ai | — | "Now let me also get the git flow started…" |
| 10 | tool | `9890dbab` | spawn e349b270 (giter) |
| 11 | ai | — | (empty) |
| 12 | tool | — | "Message queued and sent to e349b270" |
| 13 | ai | — | "Waiting for the git setup and codebase exploration…" |
| 14 | tool | — | Explorer 12d2b495 result |
| **15** | **human** | **`bacf6f31`** | **GITER REPORT (e349b270)** ← in channel |
| 16 | ai | — | **"Good, git is set up. Now waiting for the exploration report from the coder."** ← ack |
| 17 | tool | `238c5498` | Explorer 5284edd0 result |
| 18 | ai | — | "Good. Now I have a good understanding…" |
| 19 | tool | `c922dce9` | Explorer d2c29828 result |
| 20 | ai | — | "Good context. The current opencode skill uses…" |
| 21 | ai | — | "I'll wait for the coder and giter completion reports…" |
| 22 | human | `2c312178` | Coder report |
| 23 | ai | — | **"Excellent exploration report. Now I'm waiting for the giter…"** ← LOST CONTEXT |

Message 15 (giter's report) is present in the channel, but the final LLM call (producing message 23) didn't see it.

### Worker assignment in `task` table

| task_id | message_id | started_at | completed_at | worker_id |
|---|---|---|---|---|
| 108 | `d963568b` | 12:52:30 | 12:57:38 | **worker-1** |
| 119 | `bacf6f31` | 12:56:12 | 12:57:26 | **worker-2** |
| 122 | `2c312178` | 13:29:04 | 13:29:29 | **worker-3** |

**Three different workers** processed tasks for the same instance, with task 108 and 119 overlapping.

---

## Root Cause

### Primary cause: no per-instance serialization in the worker pool

`daemon/repositories/task/repository.py:116-161` — `claim_pending_task`:

```sql
UPDATE task
SET status = 'running', worker_id = ?, started_at = ?
WHERE id = (
    SELECT id FROM task
    WHERE status = 'pending'
    AND (next_retry_at IS NULL OR next_retry_at <= now)
    ORDER BY created_at ASC
    LIMIT 1
)
RETURNING *
```

The claim query filters **only by status and retry time**. It does **not** check whether another `RUNNING` task already exists for the same `instance_id`. Therefore, two workers can simultaneously claim two different pending tasks targeting the same instance.

`daemon/services/worker_pool.py:88-127` — `Worker.run` then immediately processes the claimed task by calling `self._task_processor.run_task(task, ...)` which ultimately invokes `graph.astream(graph_input, config, ...)` (`daemon/services/instance_messaging.py:938`) for the same `thread_id`.

### Secondary cause: the job-level concurrency check is bypassed

`daemon/services/message_job_handler.py:66-97` has a check that prevents concurrent `MESSAGE` job processing per instance:

```python
active = await asyncio.to_thread(
    self._job_repo.find_processing_message_jobs_by_instance, instance_id
)
if active_other:
    # Back-transition to PENDING so it's picked up next poll cycle
    ...
```

This check operates on the **`job_queue_items` table** (the legacy JobQueue). The worker pool, however, dispatches from the **`task` table** (the redesigned MessageQueue). The two are independent:

- `job_queue_items` — legacy JobQueue, one row per `MESSAGE` job
- `task` — redesigned MessageQueue, one row per `process_message` task

The `MessageJobHandler.handle()` concurrency check **does not apply to the worker pool's `task`-table dispatch**. A safety net that exists for one system is missing for the other.

### The race in practice

When two workers (worker-1 on task 108, worker-2 on task 119) both invoke `graph.astream` for `thread_id=4f5cc5fd-...`:

1. **worker-1** enters `astream` and calls `aget_state` → reconstructs state from base checkpoint + all prior writes (messages 0–14). At this point in real time, the giter's report write (message 15) may or may not have committed yet.
2. **worker-2** enters `astream` and calls `aget_state` → reads the same checkpoint.
3. **worker-1**'s `agent_node` runs, calls LLM, emits AIMessage → `put_writes` with `parent_checkpoint_id` = whatever was read in step 1.
4. **worker-2**'s `agent_node` runs, calls LLM (in some cases with a **state that does not include the giter's report** because worker-2's `aget_state` raced ahead of worker-1's write that *would* have made the giter report visible) → emits AIMessage → `put_writes`.

The langgraph Postgres checkpointer uses `parent_checkpoint_id` + `task_id` to scope writes, but the **channel state at read time is what each LLM call sees**. When two readers load the same `parent_checkpoint_id` and then both write, the order in which their writes are applied (and the order in which a *subsequent* `aget_state` reads them) depends on commit ordering in Postgres.

The key failure mode is: **worker-1's reads see message 15, but worker-2's reads do not, because the write committing message 15 was issued by worker-1's processing of the giter's report — i.e., the message 15 write is performed by the *child reports service* (in `child_reports.py`) on a different connection than worker-2's `aget_state`**. If worker-2's `aget_state` runs *before* the giter's report write commits, worker-2 builds its LLM context without it.

In the observed case, the giter's report was visible to worker-2 at one LLM call (acknowledged at message 16) but was **not visible to a later LLM call** (message 23). This is the deeper race: the LLM call at message 16 succeeded because worker-2's `aget_state` happened after the giter report's commit. But by message 23, the parent had run many more graph steps (messages 17–22), and the LLM context was reconstructed from a checkpoint_id that worker-1 had already moved past, **shadowing** the giter's report channel write.

This is a variant of the "lost update" problem, but at the **langgraph channel / checkpoint_writes** level rather than at the SQL row level: a write that should be append-only (via `add_messages` reducer keyed by message ID) is effectively "shadowed" by a sibling write that rebuilt the channel from a different checkpoint base.

### Related race #1 (also observed in the same data) — Lost update on `parent.waiting_for`

`daemon/services/child_reports.py:402-410`:

```python
parent = session.get(Instance, instance.parent_id)
old_waiting = parent.waiting_for or 0
parent.waiting_for = max(0, old_waiting - 1)
```

This is a non-atomic read-modify-write. If two children complete in the same window, both threads can read the same `waiting_for` value and both write `value-1`, leaving `waiting_for` stuck one too high. In this run, the children completed 35 minutes apart, so this race did not fire — but the code path is the same as `error_reporting.py:184-186`.

A symmetric lost-update is also possible in `daemon/tools/instance.py:488-493` (the increment side of `waiting_for`).

---

## Why the System Sometimes Works

The bug is timing-sensitive. The `claim_pending_task` ordering (`ORDER BY created_at ASC`) means worker-1 typically takes the **older** task (task 108) first. Worker-1's processing of task 108 includes the parent's first LLM turn, which spawns children and pauses. While paused, **no new checkpoint write is committed by worker-1**. When the giter's report arrives (`bacf6f31` at 12:54:24), a *new* task (119) is created, and worker-2 can claim it.

If worker-2's processing of task 119 happens **after** worker-1 has committed more checkpoint writes, the state is consistent. If it happens during a window where worker-1 has just read but not yet committed, the giter's report may be lost from worker-2's view.

The coder's report arriving 35 minutes later (13:29:04) means the parent's task 108 had long since completed and the state was stable. That LLM call (message 23) ran on worker-3 with no concurrent contention — but its state reconstruction was based on the *intermediate* checkpoint written by worker-1/worker-2, which had the giter's report shadowed.

---

## Impact

- **Silent**: no error, no exception, no log warning. The parent completes successfully with `status=completed`, but its final response is wrong because it forgot a child's result.
- **Cascade impact**: any downstream agent that consumed the parent's final response as authoritative will make a wrong decision.
- **Job-queue feedback**: `JobFeedbackObserver` correctly marks the parent's job as `completed` based on the lifecycle event — there's no signal that the parent's output was incomplete.
- **Hard to reproduce**: requires two workers, two tasks, and specific commit-ordering timing. The user's log shows it happened **once in this run** (giter's report lost), but the parent's task 108 itself completed normally.

---

## Why Existing Defenses Don't Catch It

| Defense | Present? | Why it doesn't help |
|---|---|---|
| Per-instance `asyncio.Lock` in worker | ❌ | `Worker` is a `threading.Thread`; it doesn't know which instance the task targets until it claims one. |
| `claim_pending_task` filters by `instance_id` | ❌ | Pure FIFO on `status='pending'`, no per-instance check. |
| Job-level concurrency check in `message_job_handler.py:67` | ⚠️ Partial | Checks `job_queue_items`, not the `task` table that the worker pool uses. |
| LangGraph `add_messages` reducer | ⚠️ Partial | Append-by-ID works correctly *within a single writer*, but two writers building from different snapshot bases can produce non-merging channel states. |
| `WriteGuardSession` | ✅ | Serializes writes per session, but does not serialize **read-modify-write** sequences across sessions. |
| `compacted_at` reactive compaction | ✅ | Doesn't fire here (context well under 180k tokens). |

---

## Recommended Fixes (do not apply yet — investigation only)

### Fix A (primary): per-instance serialization in the worker pool

Add an in-process `dict[instance_id, threading.Lock]` (or `asyncio.Lock` if we move to a single event loop) in the `Worker` class:

```python
# In Worker.run or via WorkerPool helper
def _process_with_instance_lock(self, task, instance_id):
    with self._worker_pool._instance_locks.setdefault(instance_id, threading.Lock()):
        return self._process_with_timeout(task)
```

Acquire the lock **after** claim but **before** `run_task`. This ensures only one worker processes any task for a given instance at a time.

### Fix B (alternative): filter at claim time

Modify `claim_pending_task` to skip pending tasks whose `instance_id` already has a `RUNNING` task. Pseudo-SQL:

```sql
UPDATE task
SET status = 'running', ...
WHERE id = (
    SELECT id FROM task
    WHERE status = 'pending'
    AND (next_retry_at IS NULL OR next_retry_at <= now)
    AND instance_id NOT IN (
        SELECT DISTINCT instance_id FROM task WHERE status = 'running'
    )
    ORDER BY created_at ASC
    LIMIT 1
)
RETURNING *
```

Note: this is a `SELECT ... FOR UPDATE SKIP LOCKED` candidate for Postgres. SQLite would need a different approach (transaction with the lock check).

### Fix C (belt-and-braces): apply the same fix to `error_reporting.py:184-186` and `tools/instance.py:488-493`

Use an atomic update instead of read-modify-write:

```python
session.execute(
    text("UPDATE instances SET waiting_for = MAX(0, waiting_for - 1) WHERE instance_id = :pid"),
    {"pid": parent_id}
)
session.execute(
    text("UPDATE instances SET waiting_for = waiting_for + 1 WHERE instance_id = :pid"),
    {"pid": parent_id}
)
```

This eliminates the lost-update race on the counter even if Fix A or B isn't applied.

### Fix D (defense in depth): re-apply `message_job_handler`'s concurrency check at the task level

The `message_job_handler` already has the right idea (line 66-97) but operates on the wrong table. Either:
- Move the check into the worker pool's claim logic, OR
- Add a `claim_pending_message_task` that joins against `task` + a `running-task-per-instance` check.

---

## Files Involved

| File | Role |
|---|---|
| `daemon/services/worker_pool.py:88-127` | `Worker.run` — claims and processes tasks without per-instance lock |
| `daemon/services/worker_pool.py:147-203` | `_process_with_timeout` — entry point for task execution |
| `daemon/repositories/task/repository.py:116-161` | `claim_pending_task` — FIFO claim, no instance guard |
| `daemon/services/instance_messaging.py:938` | `graph.astream` — concurrent invocations target same `thread_id` |
| `daemon/services/message_job_handler.py:66-97` | Job-level concurrency check (bypassed for task-table dispatch) |
| `daemon/services/child_reports.py:402-410` | Lost-update site for `parent.waiting_for` decrement |
| `daemon/services/error_reporting.py:184-186` | Symmetric lost-update site in error-report path |
| `daemon/tools/instance.py:488-493` | Symmetric lost-update site for `waiting_for` increment (in `send_message`) |

---

## Reproduction Recipe (for future verification)

1. Start daemon with multiple workers (default: 4).
2. From a parent, in one LLM turn: call `send_message` to two children (giter + coder) and call `explore` for a third (tool-invocation).
3. Have the giter complete quickly (< 30s) so the report task is enqueued while the parent is still mid-stream.
4. Force the giter's completion `process_message` task to be claimed **before** the parent's main task finishes.
5. After both tasks complete, query the parent instance's `task` table — should see two `completed` rows from different `worker_id`s with overlapping `started_at`/`completed_at`.
6. Decode the parent's `checkpoint_writes` and reconstruct the messages channel. The final LLM response (last `ai` message) should reference a child whose `human` report is in the channel but **was not in the LLM's input context**.

To force step 4 deterministically: artificially sleep 60s in the giter agent's tool execution so the parent's first LLM turn is still in flight when the giter's report task is created. Then verify the same `thread_id` is being processed by two workers simultaneously via `SELECT * FROM task WHERE instance_id = ? AND status IN ('running','completed') ORDER BY started_at`.

---

## Related Bugs (not the same root cause, but worth noting)

- `docs/bugs/parent-instance-premature-completion-on-fast-child.md` — related but different: parent's status is set to `COMPLETED` before its LLM finishes. That one was a state-machine bug; this one is a langgraph channel-state race.
- `docs/bugs/job-completed-when-parent-agent-not-done.log` — same area (job feedback timing).
- The `waiting_for` lost update in this file is independent of the main Race #2 and is a simpler bug to fix.

---

## Investigation Trail

- 2026-06-06 12:54 — User noticed parent final response said "waiting for giter" while giter had already reported.
- 2026-06-06 14:00 — Queried postgres directly. Found: report in queue ✓, task completed ✓, channel value present ✓, but LLM context at final call did not include it ✗.
- 2026-06-06 14:05 — Reconstructed full 24-message channel history from `checkpoint_writes`. Confirmed message 15 (giter's report) is in the channel.
- 2026-06-06 14:07 — Identified that tasks 108 and 119 ran in parallel on worker-1 and worker-2 (1m14s overlap).
- 2026-06-06 14:09 — Concluded Race #2 (no per-instance serialization in worker pool) is the primary cause; Race #1 (lost update on `waiting_for`) is a separate, simpler bug in the same area.
