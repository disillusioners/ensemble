> **Last updated: 2026-06-24.** Reflects the post-cleanup architecture. The Dependency Bus is the sole completion authority; the CorrelationManager, the `USE_LEGACY_WAITING_FOR_CASCADE` kill switch, and the `USE_LEGACY_JOBQUEUE_DISPATCH` flag have all been removed. Message dispatch is unified into a single `enqueue_message()` function with a `dispatch_path` parameter. The `waiting_for` and `children` columns have been dropped from the SQLModel.

# Message Processing and Correlation

This document describes the current architecture for how the daemon processes messages and manages parent-child correlations. It is the authoritative reference for the system as it exists today, not a design proposal.

## 1. Overview

The daemon has **one physical dispatch path**: the WorkerPool (4 threads) backed by the `task` table. The JobQueue is now scheduling vocabulary only (priority, queue management, project scoping) — it no longer dispatches message work. All message work flows through a unified pipeline: `Task` → `MessageProcessingPipeline` → `DependencyBus` (sole completion authority).

- **WorkerPool** — sole execution path for all work (messages, tasks). Driven by `ProcessMessageProcessor` in `daemon/services/task_processor.py`.
- **JobQueue** — scheduling vocabulary only. No longer writes `JobItem` rows for message work; only `Task` rows are written. `MessageJobHandler` is deleted.

The architecture is organized as three layers: a path-agnostic `MessageProcessingPipeline`, a `DependencyBus` that is the sole parent-waits-for-children mechanism (DB-backed, survives restart), and an `ExecutionGate` that serializes access to `graph.astream` per instance.

## 2. The Three-Layer Architecture

```
┌───────────────────────────────────────────────────────────┐
│  MessageProcessingPipeline (shared)                       │
│  Stage 1: GATE ACQUIRE  ← ExecutionGate asyncio.Lock      │
│  Stage 2: PROCESS        ← _process_message_with_tracking │
│  Stage 3: MARK COMPLETE  ← message status → COMPLETED     │
│  Stage 4: DISPATCH       ← dispatch_completed (SSE, etc.) │
│  Stage 5: CHILD CHECK    ← DependencyBus emit_terminal    │
│  Stage 6: ERROR HANDLE   ← shared error side-effects      │
│                                                           │
│  Callbacks (unified):                                     │
│  └─ on_success: complete_task + emit_terminal to bus      │
│                                                           │
├───────────────────────────────────────────────────────────┤
│  DependencyBus (SOLE completion authority)                │
│  ├─ DB-backed (dependency_watchers table)                 │
│  ├─ Watches keyed by source_task_id                       │
│  ├─ Watcher state survives restart (no rebuild needed)    │
│  └─ On terminal: enqueue FollowUp task onto parent       │
│                                                           │
├───────────────────────────────────────────────────────────┤
│  ExecutionGate (per-instance asyncio.Lock)                │
│  ├─ Prevents dual-driver checkpoint corruption            │
│  ├─ Lock acquired BEFORE graph.astream                    │
│  └─ Released on success, failure, or cancellation         │
└───────────────────────────────────────────────────────────┘
```

### Layer 1 — MessageProcessingPipeline

`daemon/services/message_processing_pipeline.py` — `MessageProcessingPipeline`

The pipeline is the single shared execution layer. It takes a `ProcessingContext` (message metadata) and `PipelineCallbacks` and produces a `ProcessingResult`. The six stages are:

1. **GATE ACQUIRE** — wraps the work function in `execution_gate.run()`, acquiring the per-instance `asyncio.Lock` before any `graph.astream` call. The second caller for the same instance blocks on the event loop until the first releases.
2. **PROCESS** — calls `manager._process_message_with_tracking()`, which runs the LangGraph streaming logic.
3. **MARK COMPLETE** — calls `queue_repository.complete(message_id)` to set the message row to `COMPLETED`. Defensive (warn-log on failure; does not fail the message).
4. **DISPATCH** — resolves the dispatch source (`internal_report` → `original_source` from instance metadata) and calls `source_dispatcher.dispatch_completed()` to route the response externally (SSE, etc.). Skipped when no valid external source is resolved.
5. **CHILD CHECK** — calls `dependency_bus.emit_terminal(source_task_id, outcome)`. The bus resolves any registered watcher and enqueues a FollowUp task onto the parent. Idempotent — multiple emits for the same source_task_id are safe.
6. **ERROR HANDLE** — catches any exception from stages 3–6 and calls `handle_message_processing_error()` (in `daemon/services/message_processing_errors.py`), which writes the error event to the DB, publishes the lifecycle event, sends the error report to the parent, and (when `job_id` is provided) marks the job as `FAILED`.

The unified `on_success` callback calls `TaskRepository.complete_task()` and then `dependency_bus.emit_terminal()` so the bus can fire any registered FollowUp. There is no JobQueue path-specific callback any more — `MessageJobHandler` is deleted.

### Layer 2 — DependencyBus (sole completion authority)

`daemon/services/dependency_bus.py` — `DependencyBus`

The Dependency Bus is the **sole** mechanism for parent-child correlation. It replaces the older in-memory `CorrelationManager._pending` set (which was removed when CM was deleted) and the older `waiting_for` SQL counter (which was dropped from the schema).

Key API:
- `watch(source_task_id, target_instance_id, follow_up_payload, metadata)` — called on `send_message` via `instance.py`. Writes a `dependency_watchers` row keyed by `source_task_id` with the pre-built FollowUp payload.
- `emit_terminal(source_task_id, outcome)` — called from the pipeline's child-check stage on `completion_report` or error. Atomically transitions the watcher `PENDING → FIRED` and enqueues a `Task` onto the parent with the FollowUp payload. Idempotent — the guarded UPDATE (`WHERE state = 'PENDING'`) ensures only one emit wins the race.
- `cancel_for_target(target_instance_id)` — called on parent pause / terminate. Atomically transitions all matching watchers to `CANCELLED`.
- `start()` — startup sweep. Loads `PENDING` watchers into the in-memory cache and recovers any `FIRED`-but-not-enqueued rows from a prior crash.

> **Why DB-backed, not in-memory?** The Dependency Bus replaces the older in-memory `CorrelationManager._pending` dict. The CM's `rebuild_from_db()` was a crash-recovery hack — it reconstructed state from `message_queue` rows on startup and was fragile under concurrent register-during-rebuild. The bus avoids this entirely: watcher state IS the DB row. Crash, restart, deploy — the bus re-loads `dependency_watchers` PENDING rows on startup and continues.

See [`docs/architecture/completion-authority.md`](completion-authority.md) for the authoritative reference on how the bus works.

### Layer 3 — ExecutionGate

`daemon/services/execution_gate.py` — `ExecutionGateService`

The `ExecutionGate` is the **single chokepoint** for `graph.astream`. It owns a per-instance `asyncio.Lock` keyed by `instance_id`. The contract:

- Only one `gate.run` for a given instance is in flight at a time. The second caller blocks on the same event loop until the first releases the lock, then runs its `work_fn`.
- The lock is held for the entire duration of `_process_message_with_tracking`. Release is unconditional on exit (success, exception, or task cancellation) via the `async with` block.
- Distinct instances have distinct locks, so unrelated work_fns run in parallel — the gate does NOT false-serialize the world.
- Cross-process coordination is NOT supported — this gate is for single-process WorkerPool serialization. All gate callers (WorkerPool threads, the resume path) funnel their work onto the main event loop via `MainLoopBridge.run_async`, so a single `asyncio.Lock` per instance is sufficient to serialize concurrent `graph.astream` calls for that instance.

The WorkerPool wraps its `_process_message_with_tracking` call in `gate.run()`. The gate is also required on the resume path (Race #5 fix — previously `resume_processing_job` bypassed the gate; now wrapped in `gate.run()` with 3-attempt bounded retry + exponential backoff).

## 3. Where the Work Lives

```
WorkerPool (UNIFIED PATH)
═══════════════════════════

Any message arrives (HTTP API, child completion, error report, scheduler)
        ↓
enqueue_message() → Task row (status=PENDING)
        ↓
Worker claims task → ProcessMessageProcessor.run_task()
        ↓
  ┌─────────────────────────────────────────────────┐
  │  MessageProcessingPipeline (SHARED, single path)│
  │  Stage 1: gate.run()                            │
  │  Stage 2: process message                       │
  │  Stage 3: mark complete                         │
  │  Stage 4: dispatch                              │
  │  Stage 5: emit_terminal to DependencyBus        │
  │  Stage 6: error handling                        │
  └─────────────────────────────────────────────────┘
        ↓
  on_success:
  complete_task() + dependency_bus.emit_terminal()
        ↓
  DependencyBus (DB-backed)
  ├─ Looks up watcher by source_task_id (atomic UPDATE)
  ├─ If watcher found: enqueue FollowUp Task onto parent
  ├─ If no watcher: no-op (root message or already-resolved)
  └─ Mark watcher FIRED (or CANCELLED if cancelled by parent)
        ↓
  Worker picks next task
```

There is **no second path**. The JobQueue no longer dispatches message work; it owns scheduling vocabulary (priority, queue management, project scoping). `JobItem` rows for message work are not written; only `Task` rows are. `MessageJobHandler` is deleted.

## 4. How a Message Flows (API → Instance)

A concrete walkthrough from entry to finalization:

1. **Entry** — An HTTP API call lands in `enqueue_message()` (in `daemon/manager.py` or `daemon/services/instance_messaging.py`), or an internal agent tool triggers `enqueue_message()` (e.g., a child completion report from `_create_completion_report` in `child_reports.py`).
2. **Dependency registration** — `enqueue_message` calls `dependency_bus.watch(source_task_id, target_instance_id=parent_id, follow_up_payload, metadata)` to register the parent as a watcher on this child's terminal event. The watcher is keyed by `source_task_id` (the child's task ID). When the parent is the root (no parent_id), no watcher is registered.
3. **Queue** — `enqueue_message` writes a `MessageQueue` row (the message to process) and, for the default `dispatch_path="workerpool"` path, a `Task` row (status=PENDING) in the same transaction. For `dispatch_path="jobqueue"`, a JobQueue MESSAGE JobItem is enqueued (used by external entry points that need a `job_id` back). The WorkerPool's notification signal wakes a worker thread.
4. **Gate** — `MessageProcessingPipeline.execute()` calls `execution_gate.run()`, acquiring the per-instance `asyncio.Lock`. If a second caller arrives while the lock is held, it blocks on the same event loop until the holder releases.
5. **Process** — `_process_message_with_tracking` calls `graph.astream`, streaming results back to the caller.
6. **Post-process** — The pipeline marks the message `COMPLETED`, resolves and dispatches the response (SSE, routing).
7. **Bus emit** — The pipeline calls `dependency_bus.emit_terminal(source_task_id, outcome)`. The bus atomically transitions the watcher `PENDING → FIRED` and enqueues a FollowUp `Task` onto the parent instance. The FollowUp payload is the completion-report message that the parent's graph will receive on its next turn.

## 5. Unified Dispatch (single `enqueue_message()`)

Message dispatch is consolidated into a single function with a `dispatch_path` parameter:

```python
async def enqueue_message(
    instance_id: str,
    message: str,
    source: str = "api",
    priority: int = 1,
    images: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    dispatch_path: Literal["workerpool", "jobqueue"] = "workerpool",
) -> "AsyncMessageResult":
```

- **`dispatch_path="workerpool"` (default)** — writes a `Task` row and notifies the WorkerPool. Used by child-instance resumption and internal agent-to-agent comms where no JobQueue job exists.
- **`dispatch_path="jobqueue"`** — enqueues a MESSAGE JobItem via `JobQueueService` and returns its `job_id`. Used by external entry points (HTTP API, `job_continue` tool) that need a `job_id` back.

The legacy `enqueue_message_via_jq` function was removed and consolidated into this single function. There is no longer a separate code path for the two dispatch destinations.

## 6. DependencyBus in Depth

The Dependency Bus is the sole mechanism that answers "is this parent's correlation complete?" See [`docs/architecture/completion-authority.md`](completion-authority.md) for the full reference. Key points relevant to message processing:

**The `(source_task_id, target_instance_id)` model.** Every `send_message` call writes a `dependency_watchers` row keyed by `source_task_id` (the child's task ID) and `target_instance_id` (the parent). When the child's task reaches a terminal event, `emit_terminal(source_task_id, outcome)` looks up the row in O(1) via the unique index on `source_task_id`, atomically transitions it `PENDING → FIRED`, and returns the pre-built FollowUp payload to the caller (the message processor), which enqueues it as a `Task` on the parent.

**Message-response pairs, NOT child lifecycle.** The bus tracks communication — "is the response to this specific `send_message` still outstanding?" It does NOT track child existence or lifecycle. A child can be alive and running without the bus tracking anything about it (e.g., a child that hasn't received any `send_message` from its parent).

**Error handling.** When `emit_terminal(source_task_id, outcome=ERROR)` is called, the bus atomically transitions the watcher `PENDING → FIRED` (not `CANCELLED` — FIRED means the FollowUp was delivered) and the FollowUp task receives the error-report payload. Error propagation flows through the same unified mechanism as success.

**Cancellation.** When a parent is paused, resumed, or terminated, `dependency_bus.cancel_for_target(target_instance_id)` atomically transitions all matching watchers to `CANCELLED`. The eventual `emit_terminal` for a cancelled watcher is a no-op (the guarded UPDATE finds no PENDING row to fire).

**Generation counter.** The bus owns an in-memory per-parent generation counter used by `JobFeedbackObserver._finalize_job` for orphan-race re-arming. The counter is in-memory only (single-process); see [`docs/architecture/completion-authority.md`](completion-authority.md) §4 for the multi-process limitation.

## 7. ExecutionGate in Depth

**What it prevents: dual-driver checkpoint corruption.** Before the gate existed, two dispatchers (WorkerPool and JobQueue) could call `graph.astream` for the same instance concurrently. Each call would read the same LangGraph checkpoint version, append its own message via `add_messages`, and try to write a new version. The write-side lost-update race caused one of the appended messages to disappear from the final checkpoint. This was the root cause of the bug documented in `docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md`. The second dispatcher was removed; only WorkerPool dispatches now. The gate is retained because concurrent `graph.astream` calls can still occur via the resume path or duplicate enqueues.

**Per-instance `asyncio.Lock` (C12 collapse).** The gate is a per-instance `asyncio.Lock` stored in `ExecutionGateService._locks[instance_id]`. The lock is acquired on the main event loop by every caller — WorkerPool threads (via `MainLoopBridge.run_async`) and the resume path all funnel their `gate.run` calls onto the same loop, so a single `asyncio.Lock` per instance is sufficient to serialize concurrent `graph.astream` calls for that instance.

The previous DB-backed implementation (`instance_execution_leases` table, `ExecutionLeaseRepository`, `recover_stale_leases` startup sweep, in-flight `LeaseContention` / `LeaseLostError` escalation) has been fully collapsed. The migration file `20260614_000002_create_instance_execution_leases.sql` is retained as part of released history but the table is no longer written at runtime. The `LeaseContention`, `LeaseLostError`, `LeaseHolderKind`, and `LeaseContentionReason` classes have been deleted from `daemon/services/execution_gate.py`.

> **Why `asyncio.Lock` is the correct primitive.** Every `gate.run` body acquires the lock on the main loop, so the lock's single-owner-per-instance semantics line up exactly with the dispatch model. There is no need for cross-process coordination: the WorkerPool's `ThreadPoolExecutor` calls are bridged to the main loop before they touch the gate. A coarse single-process lock (not per-instance) would false-serialize unrelated instances; a coarse `dict[instance_id, threading.Lock]` would require thread-safe dict access; an `asyncio.Lock` per instance keeps the lookup and the acquire on the same event loop with no extra synchronization.

**Lock lifecycle.** Acquire (via `async with` on the per-instance `asyncio.Lock`) → hold for duration of `_process_message_with_tracking` → release unconditionally on exit (success, exception, or task cancellation). On contention, the second caller blocks on the same event loop until the holder releases; the second caller's `work_fn` then runs *after* the first caller's.

**Required on ALL paths, including resume (Race #5).** The `resume_processing_job` path previously bypassed the gate entirely. This created a window where a resumed instance could run `graph.astream` concurrently with a new message if the previous run had not yet released the lease. Race #5 is eliminated: `resume_processing_job` now calls `gate.run()` with 3-attempt bounded retry + exponential backoff, identical to the forward path.

## 8. Unified Path — No More Dual Dispatch

The single WorkerPool path makes the entire "shared vs path-specific" framing obsolete. There is one execution layer (MessageProcessingPipeline), one completion authority (DependencyBus), and one chokepoint (ExecutionGate). Every message — HTTP API, child completion report, error report, scheduler — flows through the same pipeline.

The MESSAGE-dispatch branch in `JobProcessor` and `MessageJobHandler` itself were removed. The JobQueue is now scheduling vocabulary only — it owns priority, queue management, and project scoping for `Task` rows, but no longer owns a separate `JobItem` lifecycle for messages. `MessageJobHandler` is deleted.

Pause-vs-terminate discrimination, which used to live in `MessageJobHandler.handle()`, has moved: instance pause is now a **pre-check before `start_job`** in `JobProcessor`. If the target instance is `PAUSED`, the job is left `PENDING`; only `RUNNING` instances are admitted. The MESSAGE handler no longer exists to discriminate mid-flight.

Cancellation in the new path: when a parent is terminated, `dependency_bus.cancel_for_target(parent_id)` is called to cancel all watchers targeting that instance, preventing orphan FollowUps from being enqueued onto a dead parent.

## 9. The Deadlock Fix

Synchronous SQLAlchemy DB calls were running directly on the asyncio event loop thread. Because SQLite (and by extension SQLAlchemy's synchronous driver) performs write-ahead logging (WAL) operations that can block the calling thread, and because the asyncio event loop cannot switch tasks while a thread is blocked in a synchronous call, the result was a **permanent event loop deadlock**: the loop could not service reads that needed to acquire a shared lock while a blocked synchronous write held an exclusive lock.

The fix: **all synchronous DB calls are now wrapped in `asyncio.to_thread()`**, which offloads the blocking call to a shared thread pool and yields control back to the event loop. This pattern appears 323+ times across the codebase:

```python
# Before (deadlock risk):
active_job = await self._job_repo.get_by_instance(instance_id)

# After (safe):
active_job = await asyncio.to_thread(
    self._job_repo.get_by_instance, instance_id
)
```

Every site that calls a SQLAlchemy/SQLModel repository method now goes through `asyncio.to_thread()`. The exception is read-only operations on in-memory state (the bus's `_pending` cache, lock dictionaries), which do not block on I/O.

## 10. Resolved Race Conditions

| Race | Phase | Resolution |
|------|-------|------------|
| Race #1 — JobFeedbackObserver `waiting_for` snapshot vs child completion | Phase 2 | Bus callback (`emit_terminal`) is the sole terminal-transition path; no TOCTOU window between snapshot read and transition |
| Race #3 — `SELECT COUNT(*)` TOCTOU in cascade decision logic | Phase 3 | Bus uses atomic `UPDATE ... WHERE state = 'PENDING'` (rowcount-guarded); no separate snapshot + cascade sequence |
| Race #5 — ExecutionGate bypass on `resume_processing_job` | Phase 0 | `resume_processing_job` now wrapped in `gate.run()` with bounded retry + backoff; gate is authoritative |
| Cross-dispatcher checkpoint corruption | ExecutionGate | Per-instance `asyncio.Lock` prevents concurrent `graph.astream` calls (was DB-backed lease pre-C12); the second dispatcher was removed |
| Sync/async deadlock | `asyncio.to_thread` wrapping | All sync DB calls offloaded to thread pool |
| **Double-decrement bug (bus vs in-flight `register`)** | **Phase D (D9)** | DependencyBus terminal emit is idempotent on `source_task_id`; backpressure enforced via `dependency_watchers` row lock + `cancel_for_target` on parent termination |

## 11. Related Docs

- [`docs/architecture.md`](../architecture.md) — top-level architecture overview (includes Completion Architecture summary)
- [`docs/architecture/concurrency-model.md`](concurrency-model.md) — concurrency model and threading strategy
- [`docs/architecture/execution-gate-threading-model.md`](execution-gate-threading-model.md) — detailed threading-model rationale for the ExecutionGate
- [`docs/features/job-queue.md`](../features/job-queue.md) — JobQueue feature documentation (now scheduling-vocabulary-only)
- [`docs/job-queue.md`](../job-queue.md) — job queue operational guide
- [`docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md`](../bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md) — root cause of the cross-dispatcher bug that motivated ExecutionGate; closed with the single-dispatcher unification
- [`docs/architecture/unified-dispatch-architecture.md`](unified-dispatch-architecture.md) — target dispatch architecture (proposed state); this doc covers the **current** implemented state
- [`docs/architecture/completion-authority.md`](completion-authority.md) — authoritative source for "is this parent complete?" (DependencyBus, sole authority)
- [`docs/plans/decouple-execution-plan.md`](../plans/decouple-execution-plan.md) — original decouple execution plan
- [`docs/plans/decouple-job-task-message-correlation.md`](../plans/decouple-job-task-message-correlation.md) — follow-up plan covering Phase D (Dependency Bus) and the unified architecture
- [`docs/plans/unified-dispatcher.md`](../plans/unified-dispatcher.md) — migration plan for dispatcher unification
