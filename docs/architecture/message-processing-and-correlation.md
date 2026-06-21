> **Last updated: 2026-06-21.** Reflects the state after Phase D (Dependency Bus & Cleanup). The Dependency Bus is now the authoritative completion authority; the CorrelationManager is the rollback path.

# Message Processing and Correlation

This document describes the current, post-migration architecture for how the daemon processes messages and manages parent-child correlations. It is the authoritative reference for the system as it exists today, not a design proposal.

## 1. Overview

The daemon has **one physical dispatch path**: the WorkerPool (4 threads) backed by the `task` table. The JobQueue is now scheduling vocabulary only (priority, queue management, project scoping) — it no longer dispatches message work. All message work flows through a unified pipeline: `Task` → `MessageProcessingPipeline` → `DependencyBus` (completion authority).

- **WorkerPool** — sole execution path for all work (messages, tasks). Driven by `ProcessMessageProcessor` in `daemon/services/task_processor.py`.
- **JobQueue** — scheduling vocabulary only (Phase D-M8). No longer writes `JobItem` rows for message work; only `Task` rows are written. `MessageJobHandler` is deleted.

The architecture is organized as three layers: a path-agnostic `MessageProcessingPipeline`, a `DependencyBus` that is the authoritative parent-waits-for-children mechanism (DB-backed, survives restart), and an `ExecutionGate` that serializes access to `graph.astream` per instance.

## 2. The Three-Layer Architecture

```
┌───────────────────────────────────────────────────────────┐
│  MessageProcessingPipeline (shared, Phase 5)              │
│  Stage 1: GATE ACQUIRE  ← ExecutionGate lease             │
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
│  DependencyBus (authoritative — Phase D)                  │
│  ├─ DB-backed (dependency_watchers table)                │
│  ├─ Watches keyed by source_task_id                       │
│  ├─ Watcher state survives restart (no rebuild needed)   │
│  └─ On terminal: enqueue FollowUp task onto parent       │
│                                                           │
├───────────────────────────────────────────────────────────┤
│  ExecutionGate (per-instance asyncio.Lock)                │
│  ├─ Prevents dual-driver checkpoint corruption            │
│  ├─ Lock acquired BEFORE graph.astream                    │
│  └─ Released on success, failure, or cancellation         │
└───────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────┐
│  CorrelationManager — ROLLBACK PATH (shadow validation)   │
│  ├─ Disabled by default (use_dependency_bus=True)         │
│  ├─ Kept for: rollback, shadow validation, kill-switch   │
│  └─ In-memory _pending + per-parent asyncio.Lock          │
└───────────────────────────────────────────────────────────┘
```

### Layer 1 — MessageProcessingPipeline

`daemon/services/message_processing_pipeline.py` — `MessageProcessingPipeline`

The pipeline is the single shared execution layer. It takes a `ProcessingContext` (message metadata) and `PipelineCallbacks` and produces a `ProcessingResult`. The six stages are:

1. **GATE ACQUIRE** — wraps the work function in `execution_gate.run()`, acquiring the per-instance `asyncio.Lock` before any `graph.astream` call. The second caller for the same instance blocks on the event loop until the first releases.
2. **PROCESS** — calls `manager._process_message_with_tracking()`, which runs the LangGraph streaming logic.
3. **MARK COMPLETE** — calls `queue_repository.complete(message_id)` to set the message row to `COMPLETED`. Defensive (warn-log on failure; does not fail the message).
4. **DISPATCH** — resolves the dispatch source (`internal_report` → `original_source` from instance metadata) and calls `source_dispatcher.dispatch_completed()` to route the response externally (SSE, etc.). Skipped when no valid external source is resolved.
5. **CHILD CHECK** — calls `dependency_bus.emit_terminal(source_task_id, outcome)` (Phase D wiring). The bus resolves any registered watcher and enqueues a FollowUp task onto the parent. Structured log line `completion_delivery_path=bus` marks the active authority. Best-effort — does not fail the message on error.
6. **ERROR HANDLE** — catches any exception from stages 3–6 and calls `handle_message_processing_error()` (in `daemon/services/message_processing_errors.py`), which writes the error event to the DB, publishes the lifecycle event, sends the error report to the parent, and (when `job_id` is provided) marks the job as `FAILED`.

The unified `on_success` callback calls `TaskRepository.complete_task()` and then `dependency_bus.emit_terminal()` so the bus can fire any registered FollowUp. There is no JobQueue path-specific callback set any more — `MessageJobHandler` is deleted.

### Layer 2 — DependencyBus (authoritative)

`daemon/services/dependency_bus.py` — `DependencyBus`

The Dependency Bus is the **authoritative** mechanism for parent-child correlation in Phase D. It is enabled when `use_dependency_bus=True` (the default). It replaces the in-memory `CorrelationManager._pending` set as the source of truth for "is this parent waiting for any children?".

Key API:
- `watch(source_task_id, target_instance_id, follow_up_payload, metadata)` — called on `send_message` via `instance.py`. Writes a `dependency_watchers` row keyed by `source_task_id` with the pre-built FollowUp payload (the message that the parent should receive when the child finishes).
- `emit_terminal(source_task_id, outcome)` — called from the pipeline's child-check stage on `completion_report` or error. Resolves any registered watcher and enqueues a `Task` onto the parent with the FollowUp payload. Idempotent — multiple emits for the same source_task_id are safe.
- `cancel_for_target(target_instance_id)` — called on parent termination. Cancels all watchers whose `target_instance_id` matches, preventing orphan FollowUps.
- `rebuild_state()` — startup sweep. The `dependency_watchers` table is the source of truth; on startup, the bus loads any unfired PENDING watchers (those with `fired_at IS NULL`). Watcher state survives restart by construction.

> **Structured log metric:** every emit writes `completion_delivery_path=bus` (active authority) or `completion_delivery_path=cm` (rollback path) for observability. Operators can verify which authority is in effect per request.

> **Why DB-backed, not in-memory?** The Dependency Bus replaces the in-memory `CorrelationManager._pending` dict. The CM's `rebuild_from_db()` was a crash-recovery hack — it reconstructed state from `message_queue` rows on startup and was fragile under concurrent register-during-rebuild. The bus avoids this entirely: watcher state IS the DB row. Crash, restart, deploy — the bus re-loads `dependency_watchers` PENDING rows on startup and continues.

### Layer 3 — ExecutionGate

`daemon/services/execution_gate.py` — `ExecutionGateService`

The `ExecutionGate` is the **single chokepoint** for `graph.astream`. It owns a per-instance `asyncio.Lock` keyed by `instance_id`. The contract:

- Only one `gate.run` for a given instance is in flight at a time. The second caller blocks on the same event loop until the first releases the lock, then runs its `work_fn` (no `LeaseContention` return path — the lock IS the contention mechanism).
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
  ├─ Looks up watcher by source_task_id
  ├─ If watcher found: enqueue FollowUp Task onto parent
  ├─ If no watcher: no-op (root message or already-resolved)
  └─ Mark watcher FIRED (or CANCELLED if terminal=error/cancelled)
        ↓
  Worker picks next task
```

There is **no second path**. The JobQueue no longer dispatches message work; it owns scheduling vocabulary (priority, queue management, project scoping). `JobItem` rows for message work are not written; only `Task` rows are. `MessageJobHandler` is deleted.

## 4. How a Message Flows (API → Instance)

A concrete walkthrough from entry to finalization:

1. **Entry** — An HTTP API call lands in `enqueue_message()` (in `daemon/manager.py` or `daemon/services/instance_messaging.py`), or an internal agent tool triggers `enqueue_message()` (e.g., a child completion report from `_create_completion_report` in `child_reports.py`).
2. **Dependency registration** — When `use_dependency_bus=True` (default), `enqueue_message` calls `dependency_bus.watch(source_task_id, target_instance_id=parent_id, follow_up_payload, metadata)` to register the parent as a watcher on this child's terminal event. The watcher is keyed by `source_task_id` (the child's task ID). When the parent is the root (no parent_id), no watcher is registered.
3. **Queue** — `enqueue_message` writes a `MessageQueue` row (the message to process) and a `Task` row (status=PENDING) in the same transaction. The WorkerPool's notification signal wakes a worker thread.
4. **Gate** — `MessageProcessingPipeline.execute()` calls `execution_gate.run()`, acquiring the per-instance `asyncio.Lock`. If a second caller arrives while the lock is held, it blocks on the same event loop until the holder releases; there is no `LeaseContention` return path.
5. **Process** — `_process_message_with_tracking` calls `graph.astream`, streaming results back to the caller.
6. **Post-process** — The pipeline marks the message `COMPLETED`, resolves and dispatches the response (SSE, routing).
7. **Bus emit** — The pipeline calls `dependency_bus.emit_terminal(source_task_id, outcome)`. The bus looks up the watcher, marks it FIRED, and enqueues a FollowUp `Task` onto the parent instance. The FollowUp payload is the completion-report message that the parent's graph will receive on its next turn.
8. **Structured log** — Every emit writes `completion_delivery_path=bus` (active authority) or `completion_delivery_path=cm` (rollback path) for observability.

## 5. CorrelationManager in Depth (Rollback Path)

**Status after Phase D:** The `CorrelationManager` (CM) is **no longer authoritative**. When `use_dependency_bus=True` (the default), the bus is the source of truth and the CM is unused on the hot path. The CM class is retained as:

1. **Shadow validation** — `debug_completion_invariant=True` keeps the CM tracking in parallel and logs divergence between CM `pending` counts and bus `dependency_watchers` rows. This is an observability safety net for one more release.
2. **Rollback path** — `use_dependency_bus=False` falls back to the CM. The flag is a kill switch: if the bus is ever broken in production, flipping `use_dependency_bus=False` reverts to the proven in-memory CM path without code changes.

**Legacy interface (retained for shadow/rollback):**
- `_pending[parent_id]["child_id:message_id"]` — the in-memory tracking set, per-parent `asyncio.Lock` in `_locks[parent_id]`.
- `register_message_send(parent_id, child_id, message_id)` / `resolve_response(parent_id, child_id, message_id, had_error)` — pure set operations, no DB query in the hot path.
- `rebuild_from_db()` — reconstructs `_pending` from the `message_queue` table on startup.
- `completion_callback(parent_id, terminal_status)` — direct async callback to `JobFeedbackObserver`.

**The `(parent, child, message_id)` triple model.** Every `send_message` call is a parent waiting for a response from a child. The triple `(parent_id, child_id, message_id)` uniquely identifies one outstanding correlation. The CM stores these in `_pending[parent_id]`, keyed by `"child_id:message_id"`. Under the bus, the equivalent is `(source_task_id, target_instance_id, dependency_watchers.watch_id)` — keyed by `source_task_id` for direct O(1) lookup on terminal.

**Message-response pairs, NOT child lifecycle.** Both the CM and the bus track communication — they answer "is the response to this specific send_message still outstanding?" Neither tracks child existence or lifecycle. A child can be alive and running without either side tracking anything about it.

**Why the bus replaced the CM.** Three reasons drove the cutover:
1. **Crash safety by construction.** The CM is in-memory and required `rebuild_from_db()` on startup, which was fragile under concurrent register-during-rebuild. The bus is DB-backed; watcher state survives restart by definition.
2. **No TOCTOU window.** The CM's `is_complete()` check + completion callback could race with concurrent register/resolve calls. The bus's terminal-emit is idempotent and atomic at the row level.
3. **One source of truth.** With CM + `waiting_for` + bus, three sources of truth existed in parallel. Phase D reduced to one (bus) with CM as shadow.

**The `waiting_for` deprecation (ADR-011).** The `waiting_for` column on the `instance` table is **deprecated as control-flow**. It is retained as a dead-but-present column. Phase D's migration (`20260621_000002_drop_legacy_completion_columns.sql`) drops `waiting_for`, `children`, and `instance_hierarchy`; this migration is IRREVERSIBLE and **not auto-applied** — operators must run it manually after 2+ weeks of clean bus operation.

**Error handling (`had_error` flag).** When `emit_terminal(source_task_id, outcome=ERROR)` is called, the bus marks the watcher CANCELLED instead of FIRED, and the FollowUp task receives the error report payload. This enables conservative error propagation through the same unified mechanism.

## 6. ExecutionGate in Depth

**What it prevents: dual-driver checkpoint corruption.** Before the gate existed, two dispatchers (WorkerPool and JobQueue) could call `graph.astream` for the same instance concurrently. Each call would read the same LangGraph checkpoint version, append its own message via `add_messages`, and try to write a new version. The write-side lost-update race caused one of the appended messages to disappear from the final checkpoint. This was the root cause of the bug documented in `docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md`. Phase D removed the dual-driver model entirely (only WorkerPool dispatches now), but the gate is retained because concurrent `graph.astream` calls can still occur via the resume path or duplicate enqueues.

**Per-instance `asyncio.Lock` (C12 collapse).** The gate is a per-instance `asyncio.Lock` stored in `ExecutionGateService._locks[instance_id]`. The lock is acquired on the main event loop by every caller — WorkerPool threads (via `MainLoopBridge.run_async`) and the resume path all funnel their `gate.run` calls onto the same loop, so a single `asyncio.Lock` per instance is sufficient to serialize concurrent `graph.astream` calls for that instance.

The previous DB-backed implementation (`instance_execution_leases` table, `ExecutionLeaseRepository`, `recover_stale_leases` startup sweep, in-flight `LeaseContention` / `LeaseLostError` escalation) has been collapsed. The migration file `20260614_000002_create_instance_execution_leases.sql` is retained as part of released history but the table is no longer written at runtime.

> **Why `asyncio.Lock` is the correct primitive.** Every `gate.run` body acquires the lock on the main loop, so the lock's `lock.locked()` / `lock.locked()` semantics (single-owner per instance, no re-entrance) line up exactly with the dispatch model. There is no need for cross-process coordination: the WorkerPool's `ThreadPoolExecutor` calls are bridged to the main loop before they touch the gate. A coarse single-process lock (not per-instance) would false-serialize unrelated instances; a coarse `dict[instance_id, threading.Lock]` would require thread-safe dict access; an `asyncio.Lock` per instance keeps the lookup and the acquire on the same event loop with no extra synchronization.

> **Note on the `LeaseHolderKind` enum:** it is preserved as a deprecated stub in `daemon/services/execution_gate.py` (along with `LeaseContention` and `LeaseLostError`) so the dispatchers' existing call sites continue to work. The asyncio.Lock gate ignores `holder_kind` entirely — the value is kept for documentation/diagnostics only (log lines, SSE payloads). Under the new gate, `LeaseContention` is never returned and `LeaseLostError` is never raised; the dispatcher branches that handle them are harmless dead code that will be removed in a future cleanup pass.

**Lock lifecycle.** Acquire (via `async with` on the per-instance `asyncio.Lock`) → hold for duration of `_process_message_with_tracking` → release unconditionally on exit (success, exception, or task cancellation). On contention, the second caller blocks on the same event loop until the holder releases; the second caller's `work_fn` then runs *after* the first caller's. There is no `LeaseContention` return path and no mid-flight `LeaseLostError` escalation.

**Required on ALL paths, including resume (Race #5).** The `resume_processing_job` path previously bypassed the gate entirely. This created a window where a resumed instance could run `graph.astream` concurrently with a new message if the previous run had not yet released the lease. Race #5 is eliminated: `resume_processing_job` now calls `gate.run()` with 3-attempt bounded retry + exponential backoff, identical to the forward path. The retry/return semantics inside the resume path are preserved unchanged; only the underlying lock primitive is different (asyncio.Lock vs DB-backed lease).

## 7. Unified Path — No More Dual Dispatch

The single WorkerPool path makes the entire "shared vs path-specific" framing obsolete. There is one execution layer (MessageProcessingPipeline), one completion authority (DependencyBus, default ON), and one chokepoint (ExecutionGate). Every message — HTTP API, child completion report, error report, scheduler — flows through the same pipeline.

> **Removed in Phase D (D11-D13):** the MESSAGE-dispatch branch in `JobProcessor` and `MessageJobHandler` itself. The JobQueue is now scheduling vocabulary only — it owns priority, queue management, and project scoping for `Task` rows, but no longer owns a separate `JobItem` lifecycle for messages. `MessageJobHandler` is deleted.

Pause-vs-terminate discrimination, which used to live in `MessageJobHandler.handle()` (Section 7 of the pre-Phase-D doc), has moved: instance pause is now a **pre-check before `start_job`** in `JobProcessor`. If the target instance is `PAUSED`, the job is left `PENDING`; only `RUNNING` instances are admitted. The MESSAGE handler no longer exists to discriminate mid-flight.

Cancellation in the new path: when a parent is terminated, `dependency_bus.cancel_for_target(parent_id)` is called to cancel all watchers targeting that instance, preventing orphan FollowUps from being enqueued onto a dead parent.

## 8. The Deadlock Fix

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

Every site that calls a SQLAlchemy/SQLModel repository method now goes through `asyncio.to_thread()`. The exception is read-only operations on in-memory state (CM's `_pending` dict on the rollback path, lock dictionaries), which do not block on I/O.

## 9. Resolved Race Conditions

| Race | Phase | Resolution |
|------|-------|------------|
| Race #1 — JobFeedbackObserver `waiting_for` snapshot vs child completion | Phase 2 | CM callback (`handle_correlation_complete`) is the sole terminal-transition path; no TOCTOU window between snapshot read and transition |
| Race #3 — `SELECT COUNT(*)` TOCTOU in cascade decision logic | Phase 3 | CM uses pure in-memory `_pending` set operations; no DB query in the completion hot path |
| Race #5 — ExecutionGate bypass on `resume_processing_job` | Phase 0 | `resume_processing_job` now wrapped in `gate.run()` with bounded retry + backoff; gate is authoritative |
| Cross-dispatcher checkpoint corruption | ExecutionGate | Per-instance `asyncio.Lock` prevents concurrent `graph.astream` calls (was DB-backed lease pre-C12); Phase D removed the second dispatcher entirely |
| Sync/async deadlock | `asyncio.to_thread` wrapping | All sync DB calls offloaded to thread pool |
| **Double-decrement bug (bus vs in-flight `register`)** | **Phase D (D9)** | DependencyBus terminal emit is idempotent on `source_task_id`; backpressure enforced via `dependency_watchers` row lock + `cancel_for_target` on parent termination |

## 10. Related Docs

- [`docs/architecture.md`](../architecture.md) — top-level architecture overview (includes Completion Architecture summary, Phase D)
- [`docs/architecture/concurrency-model.md`](concurrency-model.md) — concurrency model and threading strategy
- [`docs/features/job-queue.md`](../features/job-queue.md) — JobQueue feature documentation (now scheduling-vocabulary-only post-Phase-D)
- [`docs/job-queue.md`](../job-queue.md) — job queue operational guide
- [`docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md`](../bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md) — root cause of the cross-dispatcher bug that motivated ExecutionGate; closed at Phase C-M5 with the single-dispatcher unification, reinforced at Phase D
- [`docs/architecture/unified-dispatch-architecture.md`](unified-dispatch-architecture.md) — target dispatch architecture (proposed state); this doc covers the **current** implemented state post-Phase-D
- [`docs/architecture/completion-authority.md`](completion-authority.md) — authoritative source for "is this parent complete?": three mechanisms (DependencyBus, CM rollback path, `USE_LEGACY_WAITING_FOR_CASCADE` kill switch)
- [`docs/plans/decouple-execution-plan.md`](../plans/decouple-execution-plan.md) — original decouple execution plan
- [`docs/plans/decouple-job-task-message-correlation.md`](../plans/decouple-job-task-message-correlation.md) — follow-up plan covering Phase D (Dependency Bus) and the unified architecture
- [`docs/plans/unified-dispatcher.md`](../plans/unified-dispatcher.md) — migration plan for dispatcher unification
