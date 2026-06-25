# Unify JobQueue and WorkerPool into a Single Dispatcher

> Status: **Proposed sketch** (not yet implemented)
> Owner: TBD
> Target: replace the dual-dispatcher architecture with a single dispatcher, and downgrade the Execution Gate to an internal lock of that dispatcher.
> Related: `docs/architecture/unified-dispatch-architecture.md` (target doc), `docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md` (the bug that motivated the gate), `daemon/services/execution_gate.py` (current surgical fix).

## 1. Why this plan exists

Today the daemon has **two physical dispatchers** that both end up calling `graph.astream(thread_id=instance_id)`:

| Dispatcher | Driven by | Table | Polled by | Entry points |
|---|---|---|---|---|
| **JobQueue path** | `JobProcessor._process_loop` → `MessageJobHandler.handle` | `job_queue_items` (`job_type='message'`) | async poll loop, `JobLockManager` per-queue lock | `enqueue_message_via_jq` (HTTP API; also `JobFeedbackObserver` for cross-instance handoff) |
| **WorkerPool path** | `WorkerPool.Worker.run` → `ProcessMessageProcessor.process` | `task` (`task_type='process_message'`) | threaded worker pool with `MainLoopBridge` to async LLM | `enqueue_message` (agent `send_message` tool, child completion reports, error reports, sources, scheduler) |

They share **no state machine, no lock, no pre-flight** — except for the two-line cross-table check in `MessageJobHandler` (`find_running_by_instance` on the `task` table) and the Execution Gate, which was added on 2026-06-14 as a DB-backed per-instance lease specifically to keep them from racing on the langgraph checkpoint.

The gate is doing real work today and we should keep it, but the honest description of the current architecture is:

> We built a mutex to keep two unrelated dispatchers from running the same code on the same instance.

The right description is:

> There is one dispatcher, and an in-process lock inside it is enough to make the gate unnecessary.

This plan describes how to get from the former to the latter.

## 2. Goals and non-goals

**Goals**

1. **One dispatch path for message work.** All "I want `graph.astream` to run for instance X" requests go through the same code path, the same table, the same poller, the same state machine.
2. **One row type.** Pick `Task` (the more general row; `child_reports` already chose it for completion reports) and stop writing `JobItem` rows for new work.
3. **One enqueue function.** `manager.enqueue_message(...)` is the only entry. `enqueue_message_via_jq` becomes either an alias or a tag on the same row.
4. **Pre-flight and lease collapse to the gate.** With one dispatcher, the "is anyone else running for this instance?" check is a single `instance_execution_leases` SELECT. The cross-table scan in `MessageJobHandler` and the in-process fast path both go away.
5. **The dependency model becomes one primitive.** The `waiting_for` counter + child-completion-report-as-separate-Task pattern collapses into "watchers on the child's work unit" — when all watchers fire, the parent gets a single resume work unit.
6. **No regression in scheduling features.** Whatever the JobQueue gives us today (priority, per-queue concurrency, dead-letter, retries, soft-delete, observers, feedback) must still work after unification. The scheduler vocabulary stays, the storage changes.

**Non-goals (explicit)**

- We are **not** rewriting `_process_message_with_tracking` or the langgraph execution core.
- We are **not** changing the HTTP API contract, SSE behavior, or the LLM streaming pipeline.
- We are **not** deleting the `job_queue_items` table on day one. The Job system continues to own the scheduling vocabulary (queues, priorities, concurrency limits, dead-letter). The migration is: every "MESSAGE" job becomes a `Task` row, and the Job system becomes the *frontend* of the unified dispatcher (a "scheduling" row that, when admitted, writes a `Task` row).
- We are **not** removing the Execution Gate. It gets downgraded to an in-process asyncio lock (a couple of lines) for the same code path. The DB-backed lease becomes an internal optimization, not a cross-dispatcher safety net.

## 3. Target architecture (single paragraph)

**One dispatch path:** HTTP / agent-tool / child-report / error-report / source / scheduler all call `manager.enqueue_message(instance_id, message, source, priority, metadata)`. That function writes one `MessageQueue` row + one `Task` row in a single transaction, computes a unified scheduling decision (priority, defer-when-idle, FIFO-per-instance), and notifies a single poller. The poller drains `Task` rows, hands them to a single `MessageTaskProcessor`, which acquires a per-instance in-process lease (the downgraded Execution Gate) and calls `_process_message_with_tracking`. Completion of a work unit emits a terminal event to the Dependency Bus, which fires watchers (including the parent's "resume when my last child finishes" watcher) and may schedule follow-up work.

## 4. End-state diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  Entry points                                                       │
│    HTTP message     agent send_message     sources                  │
│    child completion error report          scheduler                │
└────────────┬────────────────────────────────────────────────────────┘
             │ manager.enqueue_message(...)
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Unified Enqueue Facade                                             │
│    - 1 transaction: MessageQueue row + Task row                     │
│    - Tags: source, priority, defer_idle, watch_for                  │
│    - Same code path for every caller                                │
└────────────┬────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Scheduling Layer (Job system, repurposed)                          │
│    - Owns queues, FIFO-per-instance, priority, defer                │
│    - Owns per-queue concurrency limits, dead-letter, retries        │
│    - Decides *when* a Task may run; hands the Task to the            │
│      dispatcher when admitted                                       │
└────────────┬────────────────────────────────────────────────────────┘
             │ admits one Task
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Single Dispatcher (WorkerPool, owns the Task table)                │
│    - claim_pending_task() with FIFO + per-instance + lease-aware     │
│    - delegates to MessageTaskProcessor.process(task)                 │
└────────────┬────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Execution Gate (in-process asyncio lock, NOT DB)                  │
│    - per-instance, re-entrant for same holder, fast                 │
│    - replaces the DB-backed lease from commit 6c11c2a                │
└────────────┬────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  _process_message_with_tracking → graph.astream (unchanged)         │
└────────────┬────────────────────────────────────────────────────────┘
             │ terminal event
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Dependency Bus (NEW)                                               │
│    - Watchers: "I want to run when this Task finishes"               │
│    - Parent-waits-for-children: a parent watcher on each child      │
│      Task; when all fire, the parent Task is enqueued               │
│    - Replaces waiting_for counter, _create_completion_report Task   │
│    - Replaces cross-instance handoff in JobFeedbackObserver          │
└─────────────────────────────────────────────────────────────────────┘
```

## 5. Detailed changes

### 5.1 Pick the winner: `Task` is the dispatch row

Why `Task` and not `JobItem`:

- `child_reports._create_completion_report` and `error_reporting._send_error_report` already use `Task` (WorkerPool path). Migrating these to `JobItem` would be the more invasive change.
- `Task` is simpler — no queue, no `JobLockManager`, no per-queue concurrency, no `DemandState` enum. The Job system has all of those *as features we want to keep*; we'll layer them on top of `Task` in §5.2.
- `JobItem` is project-scoped (`project_id` + `queue_id`). Message work is instance-scoped. The two scopes don't compose cleanly; the unified row needs to be instance-scoped.
- `Task` already has a `message_id` foreign key into `MessageQueue`; this is the right shape (the message content lives in `MessageQueue`, the dispatch row references it).

**Migration:** for any caller currently using `enqueue_message_via_jq`, switch to `enqueue_message` (which is the same `Task` path). The `JobItem` table stops receiving `job_type='message'` rows. Other `job_type`s (custom user-defined jobs not from the agent message protocol) continue to use the Job system unchanged.

### 5.2 Repurpose the Job system as the scheduling vocabulary

The Job system is **not** deleted. It stays as the owner of: queues (FIFO, parallel, defer), per-queue concurrency, priority, pause/resume, dead-letter, retries, soft-delete, observers, feedback. What changes is what it does when it "admits" a work unit:

- **Today:** "admit" = `MessageJobHandler.handle` calls `_process_message_with_tracking` (via the gate).
- **Tomorrow:** "admit" = the Job scheduler, on admitting a `JobItem` of `job_type='message'`, writes a `Task` row pointing at the same `message_id`, and notifies the WorkerPool. The Job system is now the *scheduling* layer for message work; the WorkerPool is the *execution* layer.

In other words, the existing `JobFeedbackObserver` cross-instance handoff (which already does `manager.enqueue_message` to bounce a work unit into the other instance's `task` table) becomes the *only* way message work enters the Task table. The Job system is the metered front door; the Task table is the back-pressure-aware execution queue.

Concretely:
- `JobFeedbackObserver` (in `daemon/services/job_feedback_observer.py`) already calls `manager.enqueue_message` at lines 372, 425, 465, 555. Today that's one of several call sites. After unification it's the **only** call site for message work entering the Task table. Every other call site collapses into either the Job enqueue path (which then routes through `JobFeedbackObserver`) or a direct `Task` insert (for non-job, non-message work like background jobs).
- `enqueue_message_via_jq` (HTTP API entry) and `enqueue_message` (the WorkerPool-flavored enqueue) become **the same function**. The signature is unchanged. The implementation is: write `MessageQueue` + write `JobItem` (with `job_type='message'`, queue priority from request) + let the Job scheduler admit it. The `JobFeedbackObserver` then writes the `Task` and notifies the WorkerPool.
- The `Task` table is no longer enqueued-to by anyone except `JobFeedbackObserver`. This is the structural invariant that makes "one dispatcher" true.

**For non-message work** (background computation, scheduled jobs, the `job` LangChain tool for the `jober` agent), the Job system is the entire system — there is no `Task` row, no Execution Gate, no `graph.astream`. That code path is already separate and stays separate.

### 5.3 Single poller: collapse `JobProcessor` and `WorkerPool` for message work

Today there are two pollers:

- `JobProcessor._process_loop` (asyncio, polls `job_queue_items`).
- `WorkerPool.Worker.run` (threaded, polls `task` via `MainLoopBridge` to call into the asyncio event loop for the LLM streaming call).

After unification, the `JobProcessor` is the *admission* loop; the `WorkerPool` is the *execution* loop. The flow is:

1. `JobProcessor` admits a Job → calls into `JobFeedbackObserver.enqueue_message_for_instance(...)` → writes a `Task` row → `worker_pool.notify_work()` (already exists).
2. `WorkerPool.Worker.run` claims the `Task` (FIFO, per-instance, with `claim_pending_task` and the existing `requeue_task_with_backoff`).
3. `WorkerPool` calls `MessageTaskProcessor.process(task)` → gate → `_process_message_with_tracking` → terminal event.

There is no race between admission and execution because admission is into the same `Task` table that execution reads from. The "is anyone else running this instance?" check is now a single `instance_execution_leases` SELECT inside the WorkerPool's claim step, *or* the in-process asyncio lock from §5.4.

`JobProcessor`'s per-queue `JobLockManager` is preserved. The async `JobProcessor` poll loop is preserved. The threaded `WorkerPool` with `MainLoopBridge` is preserved. The threaded model stays because it has worked well in production for LLM streaming; the unification is about the *table* and the *entry point*, not the threading model.

### 5.4 Downgrade the Execution Gate to an in-process asyncio lock

The DB-backed `instance_execution_leases` table and the cross-process lease acquire/heartbeat/recover machinery in `daemon/services/execution_gate.py` exist for one reason: to keep `MessageJobHandler` and `ProcessMessageProcessor` from running `graph.astream` on the same instance concurrently. After §5.1–§5.3, only one of those two dispatchers exists. The race is structurally impossible.

The execution gate can shrink to:

```python
# daemon/services/execution_gate.py (after unification)
class ExecutionGateService:
    """Per-instance in-process asyncio lock around graph.astream.

    With the unified single dispatcher, no cross-process coordination
    is needed (one poller, one Task table, one execution path). The
    in-process lock prevents two workers in the same process from
    double-driving the same langgraph thread, which was the original
    bug class.

    The DB-backed lease (commit 6c11c2a) is retired. If a multi-node
    deployment ever needs cross-process serialization, it is
    re-introduced as a thin wrapper around this in-process lock, not
    as a separate code path.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, instance_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(instance_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[instance_id] = lock
            return lock

    async def run(self, instance_id, holder_id, holder_kind, work_fn):
        lock = self._lock_for(instance_id)
        async with lock:
            return await work_fn()
```

That's it. ~40 lines. The `recover_stale_leases` startup call, the heartbeat task, `LeaseContention`, `LeaseLostError`, the `instance_execution_leases` table, the lease migration — all deleted. The migration `20260614_000002_create_instance_execution_leases.sql` is dropped in the unification migration.

**Why this is safe:** with one dispatcher, the only way two `graph.astream` calls for the same instance can run is if two workers in the same process both claim the same `Task` row. They can't — the `claim_pending_task` SQL is `UPDATE ... WHERE id=:id AND status='pending' RETURNING *`, atomic across processes and threads. The WorkerPool's existing per-thread serialization on `Worker.run` already prevents this. The asyncio lock is belt-and-braces.

**Why we keep the gate as a class at all:** the test fixtures and the `gate.run(...)` call sites in the unified `MessageTaskProcessor` don't need to change. The semantics stay "acquire per-instance, run, release." The implementation collapses to one asyncio.Lock per instance. If a future requirement (e.g. multi-node deployment, langgraph remote runtime) reintroduces a cross-process serialization need, the gate class is the right place to put it back — but as a single class, not as a band-aid around two dispatchers.

### 5.5 Collapse `MessageJobHandler` and `ProcessMessageProcessor` into one

After §5.1–§5.3, there is only one processor: `MessageTaskProcessor` (rename of `ProcessMessageProcessor`). It:

1. Receives a `Task` row.
2. Loads the referenced `MessageQueue` row.
3. Calls `gate.run(instance_id, holder_id=f"task:{task.id}", work_fn=_do_process)`.
4. On success: emits the terminal event to the Dependency Bus.
5. On failure: applies retry policy (the existing `requeue_task_with_backoff`) and emits a failed event.

`MessageJobHandler.handle` is deleted. The 438 lines of pause/terminate discrimination in that handler move into `MessageTaskProcessor.process` (they apply to any message work, not just JobQueue-sourced work; today the duplication is hidden because the Job path goes through `MessageJobHandler` and the WorkerPool path goes through `ProcessMessageProcessor` with a slightly different cancellation flow). The Job-side per-queue lock (the `JobLockManager` and `release_queue_lock` calls in `_requeue_for_contention`) is preserved at the admission layer in `JobFeedbackObserver`, not duplicated into the dispatcher.

The cross-dispatcher pre-flight in `MessageJobHandler` (`find_processing_message_jobs_by_instance` and `find_running_by_instance`) is deleted. The Job side and the Task side are now the same side.

### 5.6 Dependency Bus: collapse `waiting_for` + child-report-as-Task

The parent's "I am waiting for N children" model is currently:

1. Parent calls `send_message(child_id, ...)` → `_messaging_service.enqueue_message` → writes `MessageQueue` + `Task` for the child.
2. `daemon/services/instance_lifecycle.py` increments parent's `waiting_for` counter.
3. Child's `ProcessMessageProcessor` finishes → calls `manager._process_child_completion_and_notify_parent` → `child_reports._create_completion_report` writes a `MessageQueue` (type=`COMPLETION_REPORT`) + `Task` for the parent, AND `child_reports._update_parent_on_child_complete` decrements `waiting_for` and transitions the parent to RUNNING if it hits 0.
4. Parent's `ProcessMessageProcessor` picks up the completion report `Task` and resumes the langgraph thread.

This is three mechanisms (counter, completion-report message, completion-report task) doing what one primitive can do. The Dependency Bus collapses them:

```python
# daemon/services/dependency_bus.py (NEW)
class DependencyBus:
    """One mechanism for "A waits for B".

    A watcher subscribes a follow-up work unit to a source work unit's
    terminal event. When the source emits its terminal event, the
    follow-up is enqueued.

    Replaces:
      - waiting_for counter
      - child-completion-report-as-separate-MessageQueue-and-Task
      - JobFeedbackObserver cross-instance handoff
    """

    async def watch(self, source_task_id: str, follow_up: FollowUp) -> None: ...
    async def emit_terminal(self, task_id: str, outcome: Outcome) -> None: ...
    async def pending_watchers(self, source_task_id: str) -> list[FollowUp]: ...
```

The parent-waits-for-children pattern becomes:

```python
# In spawn-instance / send-message: register a watcher, not increment a counter
await dependency_bus.watch(
    source_task_id=child_task.id,
    follow_up=FollowUp(
        target_instance_id=parent_id,
        message=completion_report_content,  # pre-built
        metadata={"kind": "child_completion", "child_id": child_id},
    ),
)
# When the child's Task finishes, DependencyBus enqueues the parent's
# follow-up Task automatically. No counter, no separate completion-
# report-as-a-different-message, no decrement.
```

The `waiting_for` column on `Instance` is dropped (migration). The `instance_hierarchy` junction table is also dropped in the same migration — the parent→child relationship is recoverable from "parent's spawn-instance call recorded child as a watcher source" or, more simply, from the `parent_id` column on `Instance` (which is already there). The denormalized `children` JSON cache on `Instance` is dropped.

`_create_completion_report` in `child_reports.py` is deleted. The completion report content (the "Developer agent (id=xxx) has done …" string) is built *once* at `watch()` time and stored on the `FollowUp` record. `_should_send_completion_report` (the idempotency check) is preserved as a check on the `FollowUp` table (one row per `source_task_id`).

`JobFeedbackObserver` is significantly simplified. The "instance handoff" case (where a child instance completing in this daemon triggers a Task in another daemon, identified by `parent_id`'s node identity) is the only place that needs cross-instance coordination. The Dependency Bus emits a "follow-up needs to run in another node" event, and a small relay service does the bounce.

### 5.7 The `MessageResult` / SSE pipeline is untouched

`_process_message_with_tracking` (manager.py:1814, `InstanceMessagingService._process_message_with_tracking` instance_messaging.py:841), the SSE streaming, the tool-call extraction, the thinking-block parsing, the `source_dispatcher.dispatch_completed` plumbing — all of it stays. The unification changes only the dispatch *surround*: enqueue, admit, claim, execute, observe completion.

## 6. The migration: incremental steps

The unification is not a single big-bang commit. It's a sequence of small, individually-reversible steps. Each step leaves the system in a working state. The Execution Gate (already shipped) is the safety belt that makes the migration low-risk.

**Step 0 (already done, 2026-06-14):** Execution Gate is the single chokepoint for `graph.astream` across both dispatchers. Without this, the migration is unsafe.

**Step 1: alias, don't fork.** Make `enqueue_message_via_jq` a thin wrapper that calls `enqueue_message` (or vice versa). Both functions still exist and behave identically. No behavior change. Just to prove the code paths are equivalent and the call sites are consistent.

**Step 2: route all JobQueue admission through `JobFeedbackObserver`.** Today, when a JobQueue MESSAGE job is admitted, the *execution* goes through `MessageJobHandler`. The dispatch into the Task table already happens for the cross-instance handoff path; extend `JobFeedbackObserver` to also handle the local-admission path. The Job system's `JobProcessor` no longer needs the `MessageJobHandler`; it just admits the job and lets the observer write the `Task`.

**Step 3: rename and dedupe the processor.** Rename `ProcessMessageProcessor` → `MessageTaskProcessor`. Move the Job-side pause/terminate discrimination logic from `MessageJobHandler.handle` into `MessageTaskProcessor.process`. The cross-dispatcher pre-flight checks in `MessageJobHandler` (the two SQL scans) are removed (no longer needed; the in-process gate is the only safety net now that there is one dispatcher).

**Step 4: delete `MessageJobHandler`.** Step 3 made it dead code. Delete the file.

**Step 5: collapse the Execution Gate.** Replace the DB-backed lease with the asyncio.Lock implementation in §5.4. Drop the `instance_execution_leases` table migration. Delete `recover_stale_leases`, `LeaseContention`, `LeaseLostError`, `_lease_heartbeat_loop`, the heartbeat escalation logic, the `LeaseHolderKind` enum. Update `MessageTaskProcessor.process` to call the new minimal gate. ~700 lines of `execution_gate.py` shrink to ~40.

**Step 6: Dependency Bus, behind a feature flag.** Introduce `daemon/services/dependency_bus.py`. Build the parent-waits-for-children flow on the new bus, *behind a feature flag*. The old `waiting_for` + completion-report-as-Task path still works in parallel. Turn the flag on in dev, run for a week, turn off the old path, delete the old code.

**Step 7: drop the legacy columns.** Migration drops `Instance.waiting_for`, `Instance.children` (denormalized cache), `instance_hierarchy`. The `MessageType.COMPLETION_REPORT` and `source.startswith("internal_report:")` plumbing in `_process_message_with_tracking` is gone — completion reports are no longer a special message type; they are a regular `Task` with `kind="child_completion"` metadata.

**Step 8: retire `JobItem` for message work.** The Job system is the scheduling layer; the `JobItem` table continues to exist for other job types but no `job_type='message'` rows are written. After a few weeks of clean logs (no message jobs), drop the `job_type='message'` handling and the MessageQueue reference from `MessageJobHandler`-shaped code (which was already deleted in step 4 — so this step is just deleting the `MESSAGE` constant and the `process_message_job` handler dispatch in `JobProcessor`).

## 7. What we delete, what we keep, what we rename

**Delete** (after their final replacement is in production):

- `daemon/services/message_job_handler.py` (438 lines)
- `daemon/services/job_processor.py`'s message-specific dispatch logic (~150 of its lines; the polling loop, lock manager, and queue ops stay)
- `daemon/services/child_reports.py` (`_create_completion_report` and `_update_parent_on_child_complete`; the per-instance idempotency check moves to the Dependency Bus)
- `daemon/services/error_reporting.py` `_send_error_report`'s "create a Task" path (the error becomes a `FollowUp` on the parent's previous Task)
- `daemon/services/job_queue_service.py`'s MESSAGE-specific helper methods (`complete_job` for MESSAGE jobs, `cancel_message_job`); the Job system keeps the rest
- `daemon/repositories/execution_lease/` (entire directory)
- The `instance_execution_leases` table and its migration
- The `waiting_for` column, the `children` column, the `instance_hierarchy` table
- The `MessageType.COMPLETION_REPORT` and `MessageType.ERROR_REPORT` enum values (and their handling in `enqueue_message`'s `if source.startswith("internal_report:")` branch)
- The `LeaseHolderKind` enum, `LeaseContention`, `LeaseLostError` (all replaced by the in-process asyncio.Lock)
- The `find_processing_message_jobs_by_instance` and `find_running_by_instance` repository methods (cross-dispatcher pre-flight, no longer needed)

**Keep unchanged** (do not touch in this work):

- `daemon/manager.py` (except the dispatch-surround helpers around lines 1741–1955)
- `daemon/services/instance_messaging.py` (except `enqueue_message` and `enqueue_message_via_jq` collapse to one)
- `daemon/services/worker_pool.py` (the threaded pool, `MainLoopBridge`, the claim-pending logic — all stay)
- `daemon/services/job_queue_service.py` (the Job system: queues, priorities, concurrency, dead-letter, observers, feedback — all stay; just stops processing `job_type='message'` directly)
- `daemon/services/job_feedback_observer.py` (extended to handle local admission in step 2, but its core behavior is unchanged)
- `daemon/services/event_bus.py`, `daemon/services/dispatch_event_bus.py`, `daemon/services/event_publisher.py` (unchanged)
- `daemon/services/completion_registry.py` (unchanged; it's the `invoke_agent_and_wait` plumbing, orthogonal to dispatch)
- `_process_message_with_tracking` (unchanged)
- The HTTP API, SSE behavior, LLM streaming pipeline, all agent tools
- All tests that don't specifically test the dispatch glue

**Rename**:

- `ProcessMessageProcessor` → `MessageTaskProcessor` (and `task_processor.py` → `message_task_processor.py`)
- `enqueue_message_via_jq` → deprecated alias for `enqueue_message`
- `ExecutionGateService` stays (it's still the right name; its internals change)

## 8. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Step 2 (route JobQueue admission through JobFeedbackObserver) introduces a hot loop** — the observer is the cross-instance handoff path today, designed for rare events. If a hot instance receives a child report every second, the observer's polling and locking become a bottleneck. | Medium | Medium | Profile first. The observer's polling is event-driven (dispatch bus notify), not interval-driven, so it should scale. If not, add per-instance admission throttling (the same throttle the WorkerPool already has for `requeue_task_with_backoff`). |
| **Step 5 (collapse the gate) loses the cross-process safety net.** A future multi-node deployment will hit the same race we just fixed. | Low now, High later | High | The new `ExecutionGateService` keeps the same interface (`gate.run(instance_id, holder_id, holder_kind, work_fn)`). The in-process asyncio.Lock is the default. A future `MultiProcessExecutionGate` (subclass or strategy) can re-add the DB-backed lease without changing the call site. Document this in the gate's module docstring. |
| **Step 6 (Dependency Bus) duplicates or races with the legacy `waiting_for` path during the transition.** | Medium | Medium | Feature-flag the bus. Keep both paths running in parallel for at least one full sprint. Add a metric: "completion reports delivered via legacy vs Dependency Bus" and "waiting_for decrements" — both should hit zero before turning off the legacy path. |
| **`MessageJobHandler`'s pause/terminate discrimination is subtly different from `ProcessMessageProcessor`'s.** The two handlers have diverged over time; merging them might lose a case. | Medium | Medium | Before step 4, write a contract test: "given X status before processing, Y cancellation, the unified processor must end in Z state." Cover all the cases both handlers currently handle. Then refactor under the test. |
| **Other callers of `enqueue_message` outside the agent path** (e.g. scheduler, sources, `invoke_agent_and_wait`) silently get a different code path. | Low | Low | `enqueue_message` is the only public entry; its signature is unchanged. The implementation change is internal. Add a deprecation log line to `enqueue_message_via_jq` so any forgotten caller surfaces immediately. |
| **The `Task` table grows unboundedly with the new "every admission writes a Task" pattern.** | Low | Low | The existing `StaleTaskRecovery` and `DeadLetterService` already cover this. Step 8 retires the Job-side cleanup and leaves only the Task-side, which is already battle-tested. |

## 9. What the test plan looks like

The unification is mostly a refactor. The tests that matter:

- **End-to-end "hot instance" stress test.** Fire 100 child completions into one parent instance in parallel, from two different dispatch paths. Verify: (a) the parent's final LLM context contains all 100 reports, (b) no lease-related `LeaseContention` logs, (c) no dropped reports. This is the bug that motivated the gate; it's the test that proves the unification preserves the fix.
- **Pause/terminate discrimination test.** Snapshot the matrix from `MessageJobHandler.handle` and `ProcessMessageProcessor.process` of "starting state × cancel reason × expected terminal state × expected job/task status." Encode as a pytest parametrize. Run before step 4 to capture the contract; run after step 4 to verify the unified processor matches.
- **Dependency Bus watcher test.** "Parent waits for 3 children, all complete → parent's follow-up enqueued exactly once, even with duplicate child completions." This is the new property; the bus needs a test that proves the `waiting_for` double-decrement bug class (the one already fixed with the `CASE` clamp in `_update_parent_on_child_complete`) is gone in the new model.
- **Existing tests:** run unchanged. The dispatcher glue is the only code that's rewritten; everything downstream of `_process_message_with_tracking` is untouched.

## 10. How long this takes

Realistic estimate for a small team with a few weeks of context in this codebase:

- Step 1: 1 day (alias, no behavior change)
- Step 2: 1 week (route admission through `JobFeedbackObserver`, prove no regression)
- Step 3: 3 days (rename + dedupe processor; pause/terminate test)
- Step 4: 1 day (delete `MessageJobHandler`)
- Step 5: 2 days (collapse the gate; update `MessageTaskProcessor`; delete the lease table)
- Step 6: 1 week (Dependency Bus behind a feature flag; parent-waits-for-children on the bus)
- Step 7: 1 day (drop columns; migration)
- Step 8: 1 day (retire `job_type='message'`)

About 4 weeks of focused work, with the Execution Gate live as the safety net throughout. The gate is removed only at step 5, by which point the unified dispatcher has been running in production for two weeks (steps 1–4).

## 11. Open questions for the implementer

1. **Should `MessageQueue` and `Task` be merged into one table?** Today `MessageQueue` is the conversation log and `Task` is the dispatch row referencing a `MessageQueue` row. Merging would save a join but lose the ability to "log a message without scheduling it" (used by the `internal_*` sources for the watcher model). **Recommendation:** keep them separate. The Dependency Bus needs both: a `FollowUp` references a Task source and a MessageQueue target.
2. **Where does `stale_task_recovery` move to?** Today it's its own service (`daemon/services/stale_task_recovery.py`) that periodically fails-and-requeues stuck `Task` rows. After step 5, this is the only "scheduler" cron job in the WorkerPool side. **Recommendation:** keep it. It's correct and tested. Rename to `stale_message_recovery` for clarity.
3. **Should the in-process gate be a class attribute on `InstanceManager` or a module-level singleton?** A class attribute is testable (each test can construct a fresh one). A module-level singleton is faster (no per-test setup) but harder to reset. **Recommendation:** class attribute, following the existing `InstanceManager._execution_gate` pattern.
4. **The `parent_instance` denormalized cache (`Instance.children` JSON) was added in fix W6 because `instance_hierarchy` was being mutated mid-transaction.** If we drop `instance_hierarchy` (step 7), we still need a way to enumerate a parent's children cheaply. **Recommendation:** keep the `parent_id` column on `Instance` (it's already there) and compute children on demand with `SELECT * FROM instances WHERE parent_id = :pid`. This was the original design before the denormalized cache was added in W6; the race that motivated W6 is gone in the Dependency Bus model because there's no longer a counter being mutated under contention.
5. **Do we need a new `MIGRATION_PLAN.md` for the column/table drops?** Yes. Step 7's migration needs to handle the case where `waiting_for` is non-NULL on existing instances (set to 0), and the `instance_hierarchy` table needs a final dump for audit. The migration should be reversible (drop columns, recreate them, no data loss) and tested against a populated production database snapshot.

## 12. Why this is worth doing

The Execution Gate is a 5-commit, ~700-line, well-engineered safety belt. It works. It will continue to work. This plan is not "the gate is broken, fix it" — it's "the gate is a symptom; the underlying duplication is the disease." The cost of the duplication is:

- **Two pre-flight checks** in `MessageJobHandler` that scan the *other* dispatcher's table (one of which was added in commit `c1aae71` specifically to handle the bug).
- **Two different back-off strategies** on `LeaseContention` (one for the Job path, one for the Task path) that we have to keep in sync.
- **Two different pause/terminate discrimination flows** that have already diverged in subtle ways.
- **A `waiting_for` counter** with a non-trivial atomic-decrement race that needed its own fix (the `CASE` clamp in `_update_parent_on_child_complete`).
- **A completion report as a separate MessageQueue + Task**, when what we really want is "parent's langgraph thread should resume when the child is done."
- **Every new entry point** (sources, scheduler, future MCP integrations) has to pick a dispatch path, and a wrong pick is a silent data-loss bug.

The unification collapses all of that into: "write a Task, the WorkerPool picks it up, the gate (now a 40-line asyncio lock) serializes execution, the Dependency Bus handles 'wait for X'." The mental model fits on an index card. The Execution Gate becomes an implementation detail of the dispatcher instead of a cross-dispatcher safety belt. Future work (multi-node, langgraph remote runtime, new scheduling policies) has a single place to plug in instead of two.

The gate is still worth what we paid for it: it was the right thing to ship first because the bug was in production. But the right long-term shape is one dispatcher, not two dispatchers and a mutex.

## 13. Feature-preservation requirements

The plan above is silent on several user-visible features that the **current** code implements, but where the implementation is split across the two dispatch paths in inconsistent ways. The unification must not regress any of these. More importantly, in several cases unification is the **only** way to make them work correctly (they are buggy today because of the path split). This section enumerates the requirements and tells the implementer where each one needs to live in the unified architecture.

### 13.1 Pause / resume

**Current behavior (asymmetric, see the diff):**

- **HTTP `POST /instances/{id}/messages`** has an auto-resume branch (`daemon/routers/messages.py:63-115`): if the target instance is `PAUSED`, the router calls `manager.resume_instance_cascade(...)` *first*, then `manager.resume_processing_job(...)` for each resumed instance, and **skips the normal enqueue path entirely**. The message is injected directly via the resume path, not via the JobQueue.
- **HTTP `POST /projects/{id}/pause`** (`daemon/routers/projects.py:361-494`) sets `projects.job_queue_paused = true`. This flag is **only** consulted by the JobQueue admission path (`JobQueueService.start_job`, `daemon/services/job_queue_service.py:1041-1045`); it does nothing to the WorkerPool. PAUSED *project* ≠ PAUSED *instance*: a project is a container of queues, an instance is a running agent thread.
- **`enqueue_message`** (WorkerPool path) explicitly does NOT auto-resume PAUSED instances (`daemon/services/instance_messaging.py:782-784`): "PAUSED instances are NOT auto-resumed — only IDLE/WAITING_CHILDREN/COMPLETED transition." A new user message to a paused instance via the WorkerPool path just sits in the Task table until the user unpauses.

**The bugs this asymmetry creates today:**

- A Telegram user messaging a paused instance will not get a response and the message will not be processed when the user unpauses (the Task is created but the instance never transitions to RUNNING, so the WorkerPool claim predicate — `i.status NOT IN (paused, terminated)` in `daemon/repositories/task/repository.py:891-907` — keeps skipping it).
- The `resume_processing_job` path is HTTP-only. A paused instance revived by a child completion report (`child_reports._create_completion_report`) is also stuck, because completion reports go through `enqueue_message`, not the router.
- A project paused via the projects API silently leaks WorkPool messages: they get enqueued, the Task is claimed, but the instance runs anyway. The pause flag is unenforced for the WorkerPool.

**Target behavior in the unified dispatcher:**

- Pause/resume becomes **one mechanism** in the Unified Enqueue Facade, not three (router branch, `job_queue_paused` column, instance-status check in claim).
- The facade checks three things, in order, before admitting a `Task`:
  1. **Project-level pause** (the current `job_queue_paused` flag, generalized): if set, the Task is admitted to a "deferred" sub-queue and surfaces a `project_paused` event. Drain on resume. This subsumes today's "project pause" feature.
  2. **Instance-level pause** (the current `InstanceStatus.PAUSED`): if set, the Task is held; when the user calls `resume_instance`, the held Tasks are released in FIFO order. This subsumes today's "instance pause" feature.
  3. **Instance status transitions** (`IDLE/WAITING_CHILDREN/COMPLETED/TERMINATED/ERROR/FAILED`): the facade transitions to `RUNNING` and rebuilds the langgraph thread lazily (the existing "revive" path).
- The HTTP `resume_processing_job` path is replaced by the facade: the router just enqueues with `metadata={"resume": true}`, and the facade's "PAUSED" branch handles the rest. The auto-resume branch in `routers/messages.py:63-115` collapses to a single `enqueue_message` call.
- Sources (Telegram, Slack, scheduler) get the same pause/resume semantics as the HTTP API for free, because they all go through the same facade.
- **Concrete deliverable for step 2 of the migration (§6):** the facade's three checks live in a single function `UnifiedEnqueueFacade.enqueue(...)` and the tests in `tests/test_pause_resume_*.py` exercise all three entry points (HTTP, agent tool, source) against the same instance. The "Telegram user can't wake a paused instance" bug gets a regression test in the same PR.

### 13.2 Terminate

**Current behavior:**

- `manager.terminate_instance(instance_id)` (manager.py:2319) cascades to all children via `_lifecycle_service.terminate_instance` (`daemon/services/instance_lifecycle.py`).
- The dispatcher-side handling is the **pause/terminate discrimination in `MessageJobHandler.handle`** (`daemon/services/message_job_handler.py:327-350`): if the in-flight MESSAGE job is cancelled and the instance's new status is `PAUSED`, the job is **left in PROCESSING** so the resume path can pick it up; otherwise (TERMINATED, shutdown), the job is `CANCELLED` and the in-flight `graph.astream` is cancelled.
- The `ProcessMessageProcessor` (WorkerPool path) has a *different* pause/terminate discrimination that does not preserve the PROCESSING row for PAUSED — it just re-queues with backoff (`daemon/services/task_processor.py:409`). So a paused instance with a WorkerPool Task in flight has the Task re-queued forever (caught by the same claim predicate at line 891 that excludes PAUSED instances).

**Target behavior in the unified dispatcher:**

- The unified `MessageTaskProcessor` (renamed from `ProcessMessageProcessor` in step 3) takes the **JobQueue path's pause/preserve logic** as the canonical behavior: PAUSED → leave the Task in `running` (or transition to a new `paused_held` state) so resume picks it up; TERMINATED → `CANCELLED` and propagate the cancel to `graph.astream` via the in-process gate.
- The pause/terminate discrimination contract test (§9) must cover the matrix: {start status × cancel reason} → {terminal job/task status, terminal instance status, lease state, is the in-flight stream cancelled}. Today the two handlers cover a strict subset of this matrix; the test must list every cell both currently cover and assert the unified processor matches.
- Termination cascades are orthogonal to dispatch and stay in `InstanceLifecycleService`. The unified processor just needs to consult the new instance status at the right moment (currently: after the gate releases, before marking the Task terminal).
- **Concrete deliverable for step 3 of the migration (§6):** the pause/terminate test from §9 is the gating test for step 4 (deleting `MessageJobHandler`). The test must fail on `main` today (because the two paths produce different outcomes for at least one matrix cell) and pass after step 3.

### 13.3 Revive a terminated/completed/error instance on new message

**Current behavior (asymmetric):**

- **JobQueue path** (`daemon/services/job_queue_service.py:1010-1039`): on `start_job`, if the target instance is in a terminal status (`COMPLETED/TERMINATED/ERROR/FAILED`), it transitions the instance to `RUNNING` and streams a status change. Comment at line 1037: "Revived instances get graph + MCP rebuilt lazily via `get_instance()`."
- **WorkerPool path** (`daemon/services/instance_messaging.py:782-787`): only revives `IDLE/WAITING_CHILDREN/COMPLETED`. A `TERMINATED/ERROR/FAILED` instance **stays in the terminal state**; the Task is created and the WorkerPool claim predicate excludes it (same `i.status NOT IN (paused, terminated)` check at `daemon/repositories/task/repository.py:891-907`).

**The bugs this creates today:**

- An agent tool `send_message` to a TERMINATED child instance silently drops the message (the Task is created but the instance never runs because the claim predicate excludes it).
- A child completion report from a sibling that triggers `_create_completion_report` for a TERMINATED parent silently drops the report (same mechanism).
- The `message_repository` (the `MessageQueue` row) is marked `COMPLETED` by the side effects in `enqueue_message` even though the dispatch never actually ran (need to verify; this is a follow-up audit). If true, this is a silent data-loss bug.

**Target behavior in the unified dispatcher:**

- The Unified Enqueue Facade revives **all** terminal instance states, mirroring the JobQueue path: `COMPLETED/TERMINATED/ERROR/FAILED` → transition to `RUNNING`, rebuild graph + MCP lazily, then admit the Task. The asymmetric revival rules in `instance_messaging.py:783` and `job_queue_service.py:1020` are unified.
- Children of a cascade-terminated parent **remain terminated** (the existing comment at `job_queue_service.py:1038` is preserved as a feature, not a bug). Revival only applies to the targeted instance.
- **Concrete deliverable for step 1 of the migration (§6):** the `enqueue_message_via_jq` alias from step 1 uses the same revival rule for both code paths. The "send_message to terminated child" bug gets a regression test in the same PR. The test must fail on `main` (WorkerPool drops, JobQueue revives) and pass after step 1.

### 13.4 Sources (Slack, Telegram, scheduler)

**Current behavior:**

- All sources call `manager.enqueue_message(...)` (WorkerPool path) — see `daemon/sources/registry.py:822`, `daemon/sources/adapters/scheduler.py`, etc. The sources never call `enqueue_message_via_jq`.
- Therefore: sources never trigger the HTTP router's auto-resume branch (13.1), never honor `job_queue_paused` (13.1), and never revive `TERMINATED/ERROR/FAILED` instances (13.3).

**The bugs this creates today:**

- A user pauses a project, then sends a Telegram message: the message is processed (WorkPool is unenforced). The "pause" UI lies.
- A user pauses an instance via the instance-pause API, then sends a Telegram message to it: the message is enqueued, the instance is still paused, the Task is never claimed. The user sees nothing happen.
- A scheduled job fires for a TERMINATED child instance: the child stays terminated; the scheduler's task is silently dropped.
- A user terminates a long-running developer instance and then messages it from Telegram: the message is enqueued, the instance stays terminated, the message is lost. This is a real production scenario (user rage-quits, then re-engages).

**Target behavior in the unified dispatcher:**

- Sources call the same `enqueue_message` as the HTTP router and the agent tools. They inherit pause/resume and revival semantics automatically (§13.1, §13.3).
- Source-specific concerns (typing indicators, reply-to routing, source-formatted responses) stay in the adapter layer; they are not part of dispatch. The `source_dispatcher.dispatch_completed(...)` call site in `MessageTaskProcessor.process` (the post-success hook) is unchanged.
- The scheduler is a special case: it owns the trigger, not the dispatch. Its current call to `enqueue_message` is correct under the unified model; nothing changes for it.
- **Concrete deliverable for step 1 of the migration (§6):** the source registry continues to call `enqueue_message` and the unification removes the asymmetry. The "paused-project receives Telegram messages anyway" bug gets a regression test in the same PR.

### 13.5 Other features worth calling out

- **Project-level queue pause** (`projects.job_queue_paused`): currently a JobQueue-only flag. After unification, it's a property of the facade (§13.1).
- **`MessageType.COMPLETION_REPORT` and `MessageType.ERROR_REPORT`**: today these are special-cased in `enqueue_message` (`instance_messaging.py:725-736`) by sniffing the `source` prefix. After the Dependency Bus migration (§5.6, step 6), completion reports are no longer a separate message type; they're a `FollowUp` record. The special-casing in `enqueue_message` is deleted in step 6.
- **Per-source rate limiting and circuit breakers** (`daemon/sources/circuit_breaker.py`, `rate_limiter.py`): orthogonal to dispatch. Unchanged.
- **SSE streaming and the response dispatcher**: orthogonal to dispatch. Unchanged.
- **`invoke_agent_and_wait`** (the synchronous send-and-await pattern, used by `completion_registry.py`): currently goes through the JobQueue's `start_job` and the `JobFeedbackObserver` for the result. After unification, the synchronous waiter becomes a `watch()` call on the Dependency Bus: register a future that resolves when the Task emits its terminal event. This is a step-6 deliverable.
- **Cross-instance job handoff** (`JobFeedbackObserver`): the case where a child instance on this daemon triggers work in a parent instance on a *different* daemon. Stays as the only cross-node coordination point; the Dependency Bus emits "follow-up needs to run in another node" events to it. Step-6 deliverable.

### 13.6 The feature-preservation acceptance criteria for the unification

The unification is "done" when **all** of these are true:

1. `POST /instances/{id}/messages` to a PAUSED instance auto-resumes it (current HTTP behavior).
2. `enqueue_message` to a PAUSED instance holds the Task and processes it on resume (currently broken for non-HTTP entry points).
3. `POST /projects/{id}/pause` blocks HTTP, agent-tool, **and source** message enqueues (currently broken for sources and agent tools).
4. `enqueue_message` to a `TERMINATED/ERROR/FAILED` instance revives it (currently broken for non-HTTP entry points).
5. `terminate_instance` cancels the in-flight `graph.astream` cleanly and the dispatcher's pause/terminate discrimination is the same in both code paths (currently divergent).
6. A source (Telegram/Slack/scheduler) message to a paused project / paused instance / terminated child behaves identically to the equivalent HTTP message (currently broken).
7. All four current features above work in the unified dispatcher with **one** code path per feature (i.e. no router branch, no `job_queue_paused` column, no `source.startswith("internal_report:")` sniff, no `find_processing_message_jobs_by_instance` cross-dispatcher scan).

The deliverable for each migration step in §6 should be measured against this list. If a step doesn't move at least one bullet from "broken" to "fixed" (or at least "no more broken than before"), it's not done.
