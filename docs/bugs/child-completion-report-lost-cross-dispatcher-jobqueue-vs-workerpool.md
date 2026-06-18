# Bug: Giter Completion Report Lost from Parent LLM Context — Cross-Dispatcher Race (JobQueue ↔ WorkerPool)

> **✅ Resolved (2026-06-14).** The cross-dispatcher checkpoint corruption race — the exact bug the `ExecutionGate` was built to eliminate — is now resolved. The gate is the single chokepoint for `graph.astream` on both dispatch paths. For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md).

**Date:** 2026-06-14
**Severity:** High (silent — no error, parent's LLM produces incorrect final response)
**Status:** Confirmed (root cause identified, fix not implemented)
**Variant of:** `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md` (worker-pool-only race, 2026-06-06)
**This bug covers the *cross-dispatcher* race — the same defect class, but between two independent dispatch systems rather than two tasks within one dispatch system.**

---

## Summary

The parent instance `dbaeec51-e8bf-4f95-8fcf-44555815c83e` produced a final response that referenced a child whose completion report had already been delivered to the queue. The giter's report (`7fac201d-1b5b-4ea9-9d19-4d79a49f66f8`) is in the database with `status=completed`, the corresponding `task` row is `completed`, and the report's content is present in a LangGraph checkpoint write — **but the parent's *final* LLM call did not have the giter's report in its message context**.

This time the race is **not** between two workers in the WorkerPool (the prior bug). It is between the two **independent dispatch systems** that both end up calling `graph.astream` for the same `thread_id`:

| Dispatcher | Path | DB table | Triggered by |
|---|---|---|---|
| `MessageJobHandler` | `enqueue_message_via_jq` → JobQueue → `MessageJobHandler.handle` → `_process_message_with_tracking` | `job_queue_items` | API user messages (routers/messages.py:119) |
| `ProcessMessageProcessor` (via WorkerPool) | `enqueue_message` → Task row → `notify_work` → worker claims → `_process_message_with_tracking` | `task` | Child completion reports (child_reports.py:367-374), tool-invocation results |

Each dispatcher has a **partial** concurrency guard that only sees its own table. Neither sees the other, so the parent's `thread_id` can be in `graph.astream` twice simultaneously — once via JobQueue, once via WorkerPool.

The architecture doc `docs/architecture/unified-dispatch-architecture.md` calls this out as the *known target* of the Execution Gate proposal:

> | **Execution** | *How* is a unit of work run safely against one langgraph thread? | **Execution Gate** (new, single component) | **Split across WorkerPool dispatch *and* MessageJobHandler** |
> | --- | --- | --- | --- |
>
> "**No dual dispatchers touching one langgraph thread.** The checkpoint race is structurally impossible, not guarded against."

---

## Concrete Evidence — Prod Run 2026-06-14

### Parent instance: `dbaeec51-e8bf-4f95-8fcf-44555815c83e`

| Time (UTC+7) | Event |
|---|---|
| 12:52:46 | API user message → `enqueue_message_via_jq` → `job_type=message` job created in `job_queue_items` for `dbaeec51`. **MessageJobHandler** picks it up. |
| 12:52:47 | Parent's first LLM turn (2 messages) |
| 12:53:13 | Tool call `spawn_instance` (coder `215b0bab`) |
| 12:53:13 | Tool call `spawn_instance` (giter `70f8d93a`) — both children attached to parent |
| 12:53:14 | Parent's LLM turn (5 messages) |
| 12:53:26 | Tool call `send_message` to coder → `waiting_for: 0→1` |
| 12:53:26 | Tool call `send_message` to giter → `waiting_for: 1→2` |
| 12:53:26 | Parent's LLM turn (8 messages) — coder and giter in flight |
| 12:53:36 | Tool call `explore` → tool-invoked explorer `b64b6374` (synchronous via `invoke_agent_and_wait`) |
| 12:54:14 | Parent's LLM turn (10 messages) |
| 12:54:23 | Tool call `explore` → tool-invoked explorer `843d9df8` |
| **12:54:25** | **Giter's final LLM response: "All done. Here's the summary…"** |
| 12:54:25 | `MessageJobHandler` (parent's JobQueue worker) finishes parent's current LLM turn |
| 12:54:25 | `_process_child_completion_and_notify_parent(70f8d93a)` fires — idempotency check passes |
| 12:54:26 | `child_reports._create_completion_report` runs: creates `MessageQueue` row + **`Task` row** (priority 0, `status=pending`) — **WorkerPool path** |
| 12:54:26 | `waiting_for` decremented: 2→1 |
| 12:54:26 | `worker_pool.notify_work()` → wakes a worker |
| 12:54:41 | Coder's last child finishes |
| 12:54:42 | `MessageJobHandler` (parent's JobQueue worker) is invoked again, this time for the **coder's completion report** path. **But the coder's report also goes through the WorkerPool path** because all `child_reports` use `enqueue_message` (Task table), not `enqueue_message_via_jq` (JobQueue table). |
| 12:54:42 | Coder's report task also created. |
| 12:54:41 | Parent's LLM turn (12 messages) |

### Database state — `message_queue` (parent `dbaeec51`)

```
              message_id              | source                                          | status    | type
--------------------------------------+-------------------------------------------------+-----------+-------------------
 99bf78a4-...                         | api                                             | completed | human
 7fac201d-1b5b-4ea9-9d19-4d79a49f66f8 | internal_report:70f8d93a:b710056d (GITER)       | completed | completion_report
 6d9a6dfc-03cf-4d97-90df-30c8c8f00aad | internal_report:215b0bab:48f15410 (CODER)       | completed | completion_report
```

All three messages: `status=completed`. From the DB's perspective nothing is missing.

### LangGraph checkpoint state — final (step=18, `1f167b80-7852-666c-8012-58e680e923fe`)

The 20-message final state contains the **coder's** report (msg[18] = "Coder agent (name=explorer-experience-arch…")) **but NOT the giter's report**. Full reconstruction of the 20-msg state at the final LLM call:

| # | Type | Content |
|---|---|---|
| 0 | human | Original user prompt |
| 1 | ai | "Looking at this request…" |
| 2 | tool | "Successfully spawned instance: 70f8d93a" |
| 3 | tool | "Successfully spawned instance: 215b0bab" |
| 4 | ai | (tool-call turn) |
| 5 | tool | "Message queued and sent to 70f8d93a" |
| 6 | tool | "Message queued and sent to 215b0bab" |
| 7 | ai | (tool-call turn) |
| 8 | tool | "Explorer agent (name=explore-experience tool…)" |
| 9 | ai | (tool-call turn) |
| 10 | tool | "Explorer agent (name=explore-how experience tool…)" |
| 11 | ai | "Good, I now have a solid understanding…" |
| 12 | tool | "Explorer agent (name=explore-experience tool…)" |
| 13 | ai | "Now I have a thorough understanding…" |
| 14 | tool | "Explorer agent (name=explore-context preloaded…)" |
| 15 | ai | "I now have a thorough understanding…" |
| 16 | tool | "Explorer agent (name=explore-context preloaded…)" |
| 17 | ai | "I have thorough understanding now from the explore…" |
| **18** | **human** | **"Coder agent (name=explorer-experience-arch, id=215b0bab…)"** ← CODER report |
| 19 | ai | "Excellent — the coder's report is extremely thorough…" |

**There is no "Giter agent" HumanMessage anywhere in the final 20-msg state.**

### Proof of concurrent writes (lost-update evidence)

Many checkpoint blobs exist with `checkpoint_id=1f167b80-7852-666c-8012-58e680e923fe, step=18, source=loop`. They have wildly different message counts and different giter/coder presence:

```
msgs | giter_present | alldone_present | coder_present
-----+---------------+-----------------+---------------
  1  |       -       |        -        |       -
  2  |       -       |        -        |       -
  4  |       -       |        -        |       -
  5  |       -       |        -        |       -
  7  |       -       |        -        |       -
  8  |       -       |        -        |       -     <- parent only
  9  |       -       |        -        |       -
 10  |       -       |        -        |       -
 11  |    TRUE       |     TRUE        |       -     <- giter report DID land here
 12  |    TRUE       |     TRUE        |       -
 13  |    TRUE       |     TRUE        |       -
 14  |    TRUE       |     TRUE        |       -
 15  |    TRUE       |     TRUE        |       -
 16  |    TRUE       |     TRUE        |       -
 11  |       -       |        -        |       -     <- alternate write without giter
 12  |       -       |        -        |       -
 13  |       -       |        -        |       -
 14  |       -       |        -        |       -
 15  |       -       |        -        |       -
 16  |       -       |        -        |       -
 17  |       -       |        -        |       -
 18  |       -       |        -        |       -
 19  |       -       |        -        |    TRUE     <- coder's report lands without giter
 20  |       -       |        -        |    TRUE     <- final state — giter LOST
```

Two parallel checkpoint-write streams were racing: one that had the giter report (and was building on it, eventually being shadowed), and one that didn't (the winner, which is what the final 20-msg blob shows). The giter's report was written to the channel at least once but the write that *survived as the "current state"* did not include it.

This is the same lost-update pattern documented in the 2026-06-06 bug, but the concurrency came from **two different dispatch systems** rather than two workers in the same system.

---

## Root Cause

### Primary cause: no cross-dispatcher serialization

The daemon has **two independent dispatch systems** that both eventually call `graph.astream(graph_input, config, ...)` for the same `thread_id`:

1. **JobQueue path** (`daemon/services/message_job_handler.py` + `daemon/services/job_processor.py`)
   - Used for: API user messages (`daemon/routers/messages.py:119` calls `enqueue_message_via_jq`)
   - DB record: `job_queue_items.job_type='message'`
   - Concurrency guard: `MessageJobHandler.handle` checks `find_processing_message_jobs_by_instance` (`message_job_handler.py:67-69`) — only sees other `job_queue_items` rows, not `task` rows.

2. **WorkerPool / TaskProcessor path** (`daemon/services/worker_pool.py` + `daemon/services/task_processor.py`)
   - Used for: child completion reports (`daemon/services/child_reports.py:367-374` calls `enqueue_message`), tool-invocation results, internal-error reports
   - DB record: `task.task_type='process_message'`
   - Concurrency guard: NONE. `claim_pending_task` (`daemon/repositories/task/repository.py:116-161`) is a pure FIFO on `status='pending'`, no per-instance filter. `ProcessMessageProcessor.process` does not check either.

In this run, the parent's first LLM turn was in flight via **JobQueue** (started 12:52:46, `job_type=message`). At 12:54:26, the giter's completion report enqueued a **Task** (WorkerPool path) for the same `instance_id`. The worker pool woke up, claimed the task, and entered `graph.astream` for the same `thread_id` while the JobQueue worker's previous `graph.astream` was still committing writes. Two parallel writers on the same LangGraph thread → lost update on the `messages` channel.

### Secondary cause: `_create_completion_report` and `_send_error_report` use the wrong path

`daemon/services/child_reports.py:323-376` (`_create_completion_report`):

```python
report_message = MessageQueue(
    message_id=report_message_id,
    instance_id=instance.parent_id,
    content=last_content,
    source=f"internal_report:{instance.instance_id}:{completed_message_id}",
    type=MessageType.COMPLETION_REPORT.value,
    status=MessageStatus.READY.value,
    priority=0,  # System priority
    enqueued_at=datetime.now(timezone.utc),
)
session.add(report_message)

# Create task for parent to process the report
report_task = Task(
    task_type=TaskType.PROCESS_MESSAGE.value,
    instance_id=instance.parent_id,
    message_id=report_message_id,
    status=TaskStatus.PENDING.value,
    created_at=datetime.now(timezone.utc),
)
session.add(report_task)
```

This always uses the **Task table** (WorkerPool). The parent is then processed by `ProcessMessageProcessor` (a worker thread) and not by `MessageJobHandler`. So the parent has two different execution contexts active:

- The original user-message job in `job_queue_items` (MessageJobHandler is the only thing that ever touches this)
- The completion-report task in `task` (ProcessMessageProcessor is the only thing that touches this)

These two don't talk to each other.

If `_create_completion_report` were instead to use `enqueue_message_via_jq` (creating a JobQueue job), the existing per-instance check in `MessageJobHandler` would at least serialize against other MessageJobHandler invocations. But it would still race with the original user-message job if that was still in flight — unless the JobQueue layer also added a "thread-busy" lease check.

### The langgraph-level mechanism

The Postgres checkpointer keys each write by `(thread_id, checkpoint_ns, task_id, channel)`. When two concurrent `graph.astream` calls (one via JobQueue, one via WorkerPool) both load the same base state and both produce writes, the writes are *append-only by message ID via the `add_messages` reducer*. In theory this is safe: a HumanMessage with id=`7fac201d-…` should be added exactly once, regardless of how many writers emit it.

In practice, the issue is that **each writer is reading from a *different* `parent_checkpoint_id` and producing a *new* `checkpoint_id` for its own write**. The reducer merges channel values by message ID, but the channel read for an LLM call happens at a different checkpoint base than the channel read for another LLM call. The LLM at the final call (message 19) reads from a base that didn't include the giter's report — the giter's report was successfully *written* to the channel at one point, but a subsequent write replaced the channel state with a snapshot that did not include it (because the coder-report processing overwrote the channel value with a snapshot from a different base).

This is the same shape as the 2026-06-06 bug, but the concurrency source is *external* (cross-dispatcher) rather than *internal* (two workers of the same dispatcher).

---

## Why This Differs From the 2026-06-06 Bug

| Aspect | 2026-06-06 bug | 2026-06-14 bug (this one) |
|---|---|---|
| Concurrency source | Two workers in the same WorkerPool claimed two `task` rows for the same instance | Two **different** dispatchers (JobQueue + WorkerPool) processed work for the same instance |
| `claim_pending_task` guard | None — but a per-instance check would have fixed it | None on either side — a guard on one side wouldn't fix the other |
| `MessageJobHandler` check | N/A (that case was all in WorkerPool) | Already present (`message_job_handler.py:67-69`) but only checks `job_queue_items`, not `task` |
| Path of giter report | Task table (WorkerPool) — the same path as the parent's main message in that run | Task table (WorkerPool) — but the parent's main message was on the **JobQueue path** in this run |
| Fix | Add per-instance lock in worker pool | **Cannot be fixed by a per-system lock** — needs a system-wide Execution Gate (per `docs/architecture/unified-dispatch-architecture.md`) |

---

## Why the Giter Report Specifically Was Lost (and Not the Coder Report)

Both reports are equally vulnerable. What differs is timing:

- Giter finished at **12:54:25** — the parent was still in its first long LLM turn (tool-invoked explorer `b64b6374` and `843d9df8` were in flight, all via synchronous `invoke_agent_and_wait` from the parent's tool calls). The giter's report task got claimed and started while the parent's other LLM stream was still emitting writes.
- Coder finished at **12:54:42** — the parent's previous stream had long since committed. The coder's report task was claimed after the parent's state was stable. The coder's report was the *last* write, so it was the one that ended up in the final 20-msg state.

If the giter had been slower (finished after the coder), the giter's report would have been the survivor and the coder's would have been lost. The bug is symmetric; giter was unlucky in this run.

---

## Why Existing Defenses Don't Catch It

| Defense | Present? | Why it doesn't help here |
|---|---|---|
| `MessageJobHandler` per-instance check (`message_job_handler.py:67`) | ✅ | Only checks `job_queue_items`. The giter's report task is in the `task` table — invisible to this check. |
| `claim_pending_task` instance filter | ❌ | Pure FIFO on `status='pending'`, no per-instance check. |
| `ProcessMessageProcessor` per-instance check | ❌ | None. Doesn't even check the `job_queue_items` table either. |
| `WriteGuardSession` | ✅ | Per-session write serialization, but not relevant to cross-process/cross-thread races. |
| LangGraph `add_messages` reducer (append by id) | ⚠️ Partial | Correctly deduplicates within a single writer's chain of writes, but two writers building from different base checkpoints can each produce a `checkpoint_id` that "wins" for some downstream reader. |
| `compacted_at` reactive compaction | ✅ | Not relevant; context was well under 180k tokens. |

---

## Recommended Fixes (do not apply yet — investigation only)

### Fix A (proper, per architecture doc): Execution Gate

Implement the **Execution Gate** as described in `docs/architecture/unified-dispatch-architecture.md`:

> **Execution Gate (new single component)** — The **only** caller of `graph.astream` for an instance thread. Guarantees a **single in-flight writer per `thread_id`** via a durable, crash-safe, multi-process-safe check (DB-backed, not in-memory locks lost on restart).

Both `MessageJobHandler` and `ProcessMessageProcessor` would *enqueue* a unit of work, but the **Gate** would be the only one that invokes `graph.astream`. It would maintain a single lease table (e.g., `graph_thread_leases(thread_id PRIMARY KEY, owner_id, acquired_at, expires_at)`) with `INSERT … ON CONFLICT DO NOTHING` semantics to claim a lease and a heartbeat to keep it alive.

This structurally eliminates the race. It's also non-trivial — the unified doc itself notes it's an architectural refactor.

### Fix B (minimal, interim): mutual-awareness between the two dispatchers

In the short term, make each dispatcher aware of the other's in-flight work for the same `thread_id`:

- `MessageJobHandler.handle` (`message_job_handler.py:67-69`): also check `task` table for `status='running'` rows on the same `instance_id`. If found, back-transition the current MESSAGE job to PENDING and release the queue lock.
- `ProcessMessageProcessor.process` (`task_processor.py:158`): also check `job_queue_items` table for `status='processing'` MESSAGE jobs on the same `instance_id`. If found, abort the task and re-queue.

Caveat: this is a stop-gap, not a real fix. The "PENDING and try again later" semantics can starve (the second writer keeps being deferred). It's better than losing writes, but the proper fix is the Gate.

### Fix C (alternative): route everything through one dispatcher

Force `_create_completion_report` to use `enqueue_message_via_jq` (JobQueue) instead of `enqueue_message` (Task). This puts *all* parent-bound work in the same `job_queue_items` table, where `MessageJobHandler`'s per-instance check covers it. The catch: the parent's *original* user-message job is already in `job_queue_items`, but the new report job is now in a *different* queue (or same queue, depending on routing) — so the per-instance check still works *within* JobQueue.

This still doesn't protect against a *user-message* job running concurrently with a *completion-report* job for the same instance, because the existing `MessageJobHandler` check is per-instance within JobQueue, but it does fix the *cross-dispatcher* race. The remaining race would be the same problem already addressed (or not) by the JobQueue's per-queue concurrency limit + the per-instance check.

### Fix D (defense in depth): checkpoint-version-based detection

After running `graph.astream`, read the resulting `parent_checkpoint_id` and the prior `parent_checkpoint_id` of the new state. If they imply that the messages channel shrank (a previous report message is no longer present), log a warning and re-issue the LLM call. This would *detect* the race but not prevent it.

---

## Files Involved

| File | Role |
|---|---|
| `daemon/services/message_job_handler.py:44-97` | JobQueue dispatcher (`MessageJobHandler.handle`) — partial per-instance check on `job_queue_items` only |
| `daemon/services/task_processor.py:158-298` | WorkerPool dispatcher (`ProcessMessageProcessor.process`) — no per-instance check |
| `daemon/services/worker_pool.py:88-127` | `Worker.run` — claims tasks without per-instance filter |
| `daemon/repositories/task/repository.py:116-161` | `claim_pending_task` — FIFO claim, no instance guard |
| `daemon/services/child_reports.py:323-376` | `_create_completion_report` — uses Task path (WorkerPool), not JobQueue |
| `daemon/services/error_reporting.py` | `_send_error_report` — likely uses the same Task path; needs audit |
| `daemon/services/instance_messaging.py:841-1100` | `_process_message_with_tracking` + `graph.astream` call site |
| `daemon/routers/messages.py:119` | API messages use `enqueue_message_via_jq` (JobQueue path) |
| `daemon/utils.py:520-595` | `invoke_agent_and_wait` for tool-invocation child instances |
| `daemon/loader.py` and `agents/` | Agent definitions (giter is at `agents/giter/`) |
| `docs/architecture/unified-dispatch-architecture.md` | Target architecture that calls this out by name |

---

## Reproduction Recipe

1. Start the daemon (prod or dev) with default 4 workers.
2. From a user, send a long-running request to a parent agent (e.g., `leader`).
3. The parent should, in one LLM turn:
   - `send_message` to two children (e.g., `giter` and `coder`)
   - Also call `explore` or another tool that triggers `invoke_agent_and_wait` (so the parent is mid-stream for ≥10s)
4. Have one child (giter) finish quickly (≤30s) so its completion-report task lands in `task` while the parent is still mid-stream.
5. Force the giter's report task to be claimed by a worker before the parent's main MessageJobHandler work commits.
6. After both parent and giter work complete, query the parent instance's LangGraph checkpoints:
   ```sql
   SELECT checkpoint_id, metadata FROM checkpoints
   WHERE thread_id = '<parent_instance_id>'
   ORDER BY checkpoint_id DESC LIMIT 5;
   ```
7. Decode the messages channel of the latest checkpoint. The giter's report HumanMessage should be present in *some* intermediate checkpoint but **missing from the final one**.

### Deterministic variant

Add a `await asyncio.sleep(60)` in `MessageJobHandler.handle` after `start_job` succeeds but before `_process_message_with_tracking` is called. This guarantees the parent's JobQueue job is still in flight when the giter's report task is created. Then verify the parent's LangGraph state for the lost-update.

---

## Related Bugs

- `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md` (2026-06-06) — same defect class, but only within WorkerPool. This file is the **cross-dispatcher** variant.
- `docs/bugs/parent-instance-premature-completion-on-fast-child.md` — separate, different bug (state-machine).
- `docs/bugs/job-completed-when-parent-agent-not-done.log` — same area (job feedback timing).
- `docs/architecture/unified-dispatch-architecture.md` — design doc that names this exact problem as the target of the Execution Gate.

---

## Related Recent Changes (verified unrelated)

The two commits on `main` immediately preceding the prod run (12:52–12:55) were:

- `4ea8e5f` (12:39:14) — `feat(chat): live context-usage indicator in chat header` — added `_emit_context_usage()` SSE telemetry inside the in-loop `graph.astream` event handler.
- `ec1a108` (12:48:58) — `fix(chat): address review on context-usage emission` — removed a redundant pre-emit `graph.aget_state()` call, added `_last_context_usage` cache cleanup at task-pop sites, cancelled a leaked snapshot task.

Files touched: `daemon/manager.py`, `daemon/routers/messages.py`, `daemon/routers/projects.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/instance_messaging.py`, `daemon/services/live_event_hub.py`, frontend, and tests.

**None of these files contain dispatch logic.** No change to `worker_pool.py`, `task_processor.py`, `message_job_handler.py`, `child_reports.py`, or the JobQueue claim logic. The new telemetry is a one-way SSE emission that reads from `all_state_messages` (already accumulated in memory) and never writes back to the graph or DB. The pre-emit `aget_state` removal is a small *simplification* (fewer round-trips), not a behavior change that could affect the race.

**Conclusion: these commits did not make the bug worse, nor did they introduce it.** The bug is pre-existing — it's the same shape as the 2026-06-06 worker-pool-only variant, but the concurrency source is between two dispatch systems rather than within one.

---

## Investigation Trail

- 2026-06-14 12:55 — User noticed the log showed "giter report not reach parent" while the DB had the report marked `completed`.
- 2026-06-14 13:00 — Queried `message_queue` for parent `dbaeec51`. Found 3 rows: human, giter, coder — all `completed`.
- 2026-06-14 13:05 — Decoded parent's LangGraph checkpoint blobs. Found 20 distinct blob versions for the same `checkpoint_id=1f167b80-…` with message counts 1, 2, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20. Identified that blobs 11–16 had `giter=True` and blobs 19–20 had `coder=True, giter=False`. Concluded the giter's report was lost.
- 2026-06-14 13:10 — Reconstructed the 20-msg final state. Confirmed giter's "All done" content was *not* in the LLM's input context.
- 2026-06-14 13:15 — Identified that the parent's first message used `enqueue_message_via_jq` (JobQueue), while the giter's report used `enqueue_message` (Task/WorkerPool). These two dispatchers both call `_process_message_with_tracking` → `graph.astream` for the same `thread_id` and neither checks the other.
- 2026-06-14 13:20 — Searched for prior bug reports. Found `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md` (2026-06-06) which documents a worker-pool-only variant. This is the cross-dispatcher variant.
- 2026-06-14 13:25 — Re-read `docs/architecture/unified-dispatch-architecture.md`. Confirmed the Execution Gate is the proper target fix.
