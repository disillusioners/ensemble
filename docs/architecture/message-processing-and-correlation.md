> **Last updated: 2026-06-18.** Reflects the state after the CorrelationManager migration (6 phases) + deadlock fix + resume gate fix.

# Message Processing and Correlation

This document describes the current, post-migration architecture for how the daemon processes messages and manages parent-child correlations. It is the authoritative reference for the system as it exists today, not a design proposal.

## 1. Overview

The daemon has two physical dispatch paths that handle incoming messages — HTTP API messages and internal agent-to-agent messages — against an instance's LangGraph thread:

- **WorkerPool path** — driven by `ProcessMessageProcessor` in `daemon/services/task_processor.py`, backed by a `ThreadPoolExecutor` polling the `task` table. Triggered by `enqueue_message()` (e.g., child completion reports from `_create_completion_report` in `child_reports.py`).
- **JobQueue path** — driven by `MessageJobHandler` in `daemon/services/message_job_handler.py`, backed by an async poll loop over `job_queue_items`. Triggered by `enqueue_message_via_jq()` (the HTTP API entry point and some internal paths).

Both paths converge through shared infrastructure. The architecture is organized as three layers: a path-agnostic `MessageProcessingPipeline` (Phase 5), a `CorrelationManager` that tracks parent-child message-response pairs, and an `ExecutionGate` that serializes access to `graph.astream` per instance.

## 2. The Three-Layer Architecture

```
┌───────────────────────────────────────────────────────────┐
│  MessageProcessingPipeline (shared, Phase 5)              │
│  Stage 1: GATE ACQUIRE  ← ExecutionGate lease             │
│  Stage 2: PROCESS        ← _process_message_with_tracking │
│  Stage 3: MARK COMPLETE  ← message status → COMPLETED     │
│  Stage 4: DISPATCH       ← dispatch_completed (SSE, etc.) │
│  Stage 5: CHILD CHECK    ← CM resolve_response            │
│  Stage 6: ERROR HANDLE   ← shared error side-effects      │
│                                                           │
│  Strategy/callbacks for path-specific behavior:           │
│  ├─ WorkerPool: jittered backoff, complete_task           │
│  └─ JobQueue: CM deferral, atomic contention, job complete│
│                                                           │
├───────────────────────────────────────────────────────────┤
│  CorrelationManager (authoritative for correlation)       │
│  ├─ Tracks (parent, child, message_id) triples            │
│  ├─ Per-parent asyncio.Lock                              │
│  ├─ Pure set operations (no DB query in completion path)  │
│  └─ Direct async callback → observer finalizes job        │
│                                                           │
├───────────────────────────────────────────────────────────┤
│  ExecutionGate (DB-backed lease per instance)              │
│  ├─ Prevents dual-driver checkpoint corruption             │
│  ├─ Lease acquired BEFORE graph.astream                    │
│  ├─ Released on success, failure, or LeaseLostError        │
│  └─ Both paths (WorkerPool + JobQueue) go through it       │
└───────────────────────────────────────────────────────────┘
```

### Layer 1 — MessageProcessingPipeline

`daemon/services/message_processing_pipeline.py` — `MessageProcessingPipeline`

Phase 5 of the CorrelationManager migration consolidated six shared stages that both dispatchers were duplicating. The pipeline takes a `ProcessingContext` (message metadata) and `PipelineCallbacks` (path-specific behaviour) and produces a `ProcessingResult`. The six stages are:

1. **GATE ACQUIRE** — wraps the work function in `execution_gate.run()`, acquiring a per-instance lease before any `graph.astream` call.
2. **PROCESS** — calls `manager._process_message_with_tracking()`, which runs the LangGraph streaming logic.
3. **MARK COMPLETE** — calls `queue_repository.complete(message_id)` to set the message row to `COMPLETED`. Defensive (warn-log on failure; does not fail the message).
4. **DISPATCH** — resolves the dispatch source (`internal_report` → `original_source` from instance metadata) and calls `source_dispatcher.dispatch_completed()` to route the response externally (SSE, etc.). Skipped when no valid external source is resolved.
5. **CHILD CHECK** — calls `manager._process_child_completion_and_notify_parent()` (CM `resolve_response`), which decrements the CM's pending count for this parent. Best-effort — does not fail the message on error.
6. **ERROR HANDLE** — catches any exception from stages 3–6 and calls `handle_message_processing_error()` (in `daemon/services/message_processing_errors.py`), which writes the error event to the DB, publishes the lifecycle event, sends the error report to the parent, and (when `job_id` is provided) marks the job as `FAILED`.

Path-specific behaviour is injected via `PipelineCallbacks`:
- **WorkerPool**: `on_success` → `TaskRepository.complete_task`; `on_contention` → jittered backoff + `requeue_task_with_backoff`; `on_cancel` → log + re-raise.
- **JobQueue**: `on_success` → `JobQueueService.complete_job(COMPLETED)` (gated by CM deferral check); `on_contention` → atomic transition `PROCESSING→PENDING` + dispatch bus wake-up; `on_cancel` → pause-vs-terminate discrimination (see Section 7).

### Layer 2 — CorrelationManager

`daemon/services/correlation_manager.py` — `CorrelationManager`

The CM tracks `(parent_id, child_id, message_id)` triples in an in-memory dict `_pending[parent_id]`. A per-parent `asyncio.Lock` serializes all register, resolve, and cleanup operations per parent. It is the **authoritative** mechanism for parent-child correlation.

Key API:
- `register_message_send(parent_id, child_id, message_id)` — called on `send_message` via the `notify_corr_register` hook site.
- `resolve_response(parent_id, child_id, message_id, had_error)` — called on `completion_report` or error via `notify_corr_resolve`. Pure set operations — no DB query in the hot path.
- `rebuild_from_db()` — reconstructs `_pending` state from the `message_queue` table for crash recovery.
- `completion_callback(parent_id, terminal_status)` — the direct async callback registered at construction. It is invoked **after** the per-parent lock is released (W1 fix), so it is safe for the callback to re-enter CM for the same parent without deadlocking.

Hook sites (`notify_corr_register` / `notify_corr_resolve`) are wired in `child_reports.py` (for `completion_report`) and `error_reporting.py` (for error reports). Shadow mode is safe: all hook sites check `get_correlation_manager()` and return early if `None`.

### Layer 3 — ExecutionGate

`daemon/services/execution_gate.py` — `ExecutionGateService`

The `ExecutionGate` is the **single chokepoint** for `graph.astream`. It owns a DB-backed per-instance lease against the `instance_execution_leases` table. The contract:

- Only one dispatcher holds the lease for a given instance at a time.
- Acquisition is atomic (`INSERT ... ON CONFLICT DO NOTHING`).
- The lease is held for the entire duration of `_process_message_with_tracking`. Release is conditional on `holder_id` matching — a stale loser cannot evict a fresh winner's lease.
- A background heartbeat keeps the lease alive for long-running `graph.astream` calls.
- On contention, the caller receives a `LeaseContention` signal and backs off. If the lease is revoked mid-flight (e.g., by `recover_stale_leases` on another node), `LeaseLostError` is raised and the caller re-queues.

Both `ProcessMessageProcessor` and `MessageJobHandler` wrap their `_process_message_with_tracking` call in `gate.run()`. The gate is also required on the resume path (Race #5 fix — previously `resume_processing_job` bypassed the gate; now wrapped in `gate.run()` with 3-attempt bounded retry + exponential backoff).

## 3. Where the Weight Lives

```
WorkerPool (LIGHT)                           JobQueue (HEAVY)
═════════════════                            ══════════════════
                                             
Child message arrives                         Parent message arrives
       ↓                                            ↓
task_processor.process()                     message_job_handler.handle()
       ↓                                            ↓
  ┌─────────────────────────┐                ┌─────────────────────────┐
  │  MessageProcessingPipeline (SHARED)        │  MessageProcessingPipeline (SHARED)
  │  Stage 1: gate.run()                      │  Stage 1: gate.run()
  │  Stage 2: process message                 │  Stage 2: process message
  │  Stage 3: mark complete                   │  Stage 3: mark complete
  │  Stage 4: dispatch                        │  Stage 4: dispatch
  │  Stage 5: child check (CM resolve)        │  Stage 5: child check (CM resolve)
  │  Stage 6: error handling                  │  Stage 6: error handling
  └─────────────────────────┘                └─────────────────────────┘
       ↓                                            ↓
  on_success:                                on_success:
  complete_task() ← SIMPLE                     CM deferral check ←
       ↓                                        Is parent waiting for children?
  Worker picks next task                            ↓ YES
                                                Leave job PROCESSING
                                                Return (don't complete job)
                                                     ↓
                                                (Later: CM fires callback)
                                                     ↓
                                          ═════════════════════════════════
                                          JobFeedbackObserver (HEAVIEST)
                                          ═════════════════════════════════
                                          handle_correlation_complete()
                                                     ↓
                                          _get_processing_job_for_instance()
                                                     ↓
                                          _finalize_job()
                                            ├─ get_pending_count() re-check (C1)
                                            ├─ atomic_transition(PROCESSING→COMPLETED)
                                            ├─ notify_watchers()
                                            ├─ _finalize_instance()
                                            │    ├─ instance.status update
                                            │    ├─ SSE event
                                            │    ├─ CompletionRegistry
                                            │    └─ lifecycle event
                                            └─ _trigger_next_job()
```

The WorkerPool is "light" because child messages complete simply: after the pipeline runs, `on_success` calls `TaskRepository.complete_task()` and the worker thread picks the next task from the queue. There is no waiting-for-children concept.

The JobQueue is "heavy" because parent messages may need to **wait** for their children. If the CM deferral check (`get_correlation_manager()` + `get_pending_count()`) finds pending child correlations, `on_success` leaves the job in `PROCESSING` and returns early. The job completion is **deferred** to the CM callback: `JobFeedbackObserver.handle_correlation_complete()` → `_finalize_job()`, which does the full atomic transition, watcher notifications, instance finalization (status, SSE, CompletionRegistry, lifecycle event), and the `_trigger_next_job()` cascade.

## 4. How a Message Flows (API → Instance)

A concrete walkthrough from entry to finalization:

1. **Entry** — An HTTP API call lands in `enqueue_message_via_jq()` (in `daemon/manager.py` or `daemon/services/instance_messaging.py`), or an internal agent tool triggers `enqueue_message()` (e.g., a child completion report from `_create_completion_report` in `child_reports.py`).
2. **Queue** — Both paths write a `MessageQueue` row (the message to process) and a dispatch row (`JobItem` for JobQueue; `Task` for WorkerPool). The dispatcher picks it up from its respective poll loop.
3. **Gate** — `MessageProcessingPipeline.execute()` calls `execution_gate.run()`, acquiring a per-instance lease. If contention occurs, the path-specific `on_contention` callback re-queues the work.
4. **Process** — `_process_message_with_tracking` calls `graph.astream`, streaming results back to the caller.
5. **Post-process** — The pipeline marks the message `COMPLETED`, resolves and dispatches the response (SSE, routing), and checks child completion via the CM.
6. **Correlation resolution** — If the message is a child completion report, `notify_corr_resolve()` calls `CM.resolve_response()`, decrementing the parent's pending count. If the count reaches zero, the CM fires its `completion_callback`.
7. **Job finalization** (JobQueue path only) — If a parent still has pending children, the job stays `PROCESSING` and the pipeline returns. When the last child completes, `JobFeedbackObserver.handle_correlation_complete()` calls `_finalize_job()`, which:
   - Re-checks the pending count (C1 consistency guard).
   - Performs an atomic `PROCESSING→COMPLETED` transition.
   - Calls `notify_watchers()`.
   - Calls `_finalize_instance()` (instance status, SSE, CompletionRegistry, lifecycle event).
   - Calls `_trigger_next_job()`.

## 5. CorrelationManager in Depth

**The (parent, child, message_id) triple model.** Every `send_message` call is a parent waiting for a response from a child. The triple `(parent_id, child_id, message_id)` uniquely identifies one outstanding correlation. The CM stores these in `_pending[parent_id]`, keyed by `"child_id:message_id"`.

**Message-response pairs, NOT child lifecycle.** The CM tracks communication — it answers "is the response to this specific send_message still outstanding?" It does **not** track child existence or lifecycle (that is handled elsewhere). This distinction matters: a child can be alive and running without the CM tracking anything about it.

**Per-parent lock serialization.** Each parent has its own `asyncio.Lock` in `_locks[parent_id]`. All CM operations for that parent — `register_message_send`, `resolve_response`, and cleanup — acquire this lock. This prevents race conditions between concurrent `notify_corr_register` and `notify_corr_resolve` calls for the same parent.

**Why direct async callback, not EventBus.** The CM's `completion_callback` is a direct async callable passed at construction, not an `EventBus.publish()`. Direct invocation avoids the queue overflow risk and DB persistence overhead of the EventBus, and keeps the callback on the critical path without an extra hop.

**Crash recovery via `rebuild_from_db()`.** On startup (or when CM is initialized), `rebuild_from_db()` queries the `message_queue` table for `(child_id, message_id)` pairs where the parent has a non-zero `waiting_for` count. It reconstructs the `_pending` state in memory. The `waiting_for` column is the **only** source of truth for this reconstruction.

**The `waiting_for` deprecation (ADR-011).** The `waiting_for` column on the `instance` table is **deprecated as control-flow**. It is retained as a rebuild-only cache (the source for `rebuild_from_db()`). The CM is authoritative for all runtime decisions. No code should read `waiting_for` to decide whether a parent is done; use `CM.get_pending_count()` or `CM.is_complete()` instead.

**Error handling (`had_error` flag).** When `resolve_response()` is called with `had_error=True`, the CM sets `had_error=True` on the parent's `ParentCorrelation`. When all children have resolved, the `completion_callback` receives the terminal status derived from this flag: `"error"` if any response had an error, `"completed"` otherwise. This enables conservative error propagation.

## 6. ExecutionGate in Depth

**What it prevents: dual-driver checkpoint corruption.** Before the gate existed, both dispatchers could call `graph.astream` for the same instance concurrently. Each call would read the same LangGraph checkpoint version, append its own message via `add_messages`, and try to write a new version. The write-side lost-update race caused one of the appended messages to disappear from the final checkpoint. This was the root cause of the bug documented in `docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md`.

**The DB-backed lease (`instance_execution_leases` table).** The lease row carries `instance_id`, `holder_id`, `holder_kind` (`"task"` or `"message_job"`), `acquired_at`, and `heartbeat_at`. The heartbeat is written by a background task spawned inside `_execute_under_lease`; `recover_stale_leases` uses `COALESCE(heartbeat_at, acquired_at) < :cutoff` to detect crashed holders.

**Lease lifecycle.** Acquire → hold for duration of `_process_message_with_tracking` → release on success. On contention (`LeaseContention`), the caller re-queues. On mid-flight revocation (`LeaseLostError`), the caller's work is cancelled and it re-queues with backoff.

**Required on ALL paths, including resume (Race #5).** The `resume_processing_job` path previously bypassed the gate entirely. This created a window where a resumed instance could run `graph.astream` concurrently with a new message if the previous run had not yet released the lease. Race #5 is eliminated: `resume_processing_job` now calls `gate.run()` with 3-attempt bounded retry + exponential backoff, identical to the forward path.

## 7. What's Shared vs Path-Specific

Everything in the 6-stage pipeline, the CM, and the ExecutionGate is **shared** — both paths produce identical observable behaviour for gate acquisition, message completion, dispatch, child checking, and error side-effects.

The following five differences are **intentional path-specific behaviour**, not bugs. They reflect the fundamentally different dispatch models of the two paths (worker threads vs async job queue):

| # | Divergence | WorkerPool | JobQueue |
|---|---|---|---|
| 1 | **Retry-context derivation** | `retry_count > 0 OR resume_mode` | `resume_mode` only |
| 2 | **Pre-flight sibling checks** | Relies on gate's `try_acquire` | Cross-dispatcher checks before acquiring gate |
| 3 | **Pause/terminate discrimination** | Log "task paused" + re-raise | `PAUSED` instance → leave `PROCESSING`; others → `CANCELLED` |
| 4 | **Lease contention handling** | Jittered backoff via `requeue_task_with_backoff` | Extracted `_requeue_for_contention` + dispatch bus wake-up |
| 5 | **CancellationToken ownership** | Passed as parameter from `TaskProcessor.run_task` | Stored in `_active_tokens[job_id]` (keyed by `job_id`) |

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

Every site that calls a SQLAlchemy/SQLModel repository method now goes through `asyncio.to_thread()`. The exception is read-only operations on in-memory state (CM's `_pending` dict, lock dictionaries), which do not block on I/O.

## 9. Resolved Race Conditions

| Race | Phase | Resolution |
|------|-------|------------|
| Race #1 — JobFeedbackObserver `waiting_for` snapshot vs child completion | Phase 2 | CM callback (`handle_correlation_complete`) is the sole terminal-transition path; no TOCTOU window between snapshot read and transition |
| Race #3 — `SELECT COUNT(*)` TOCTOU in cascade decision logic | Phase 3 | CM uses pure in-memory `_pending` set operations; no DB query in the completion hot path |
| Race #5 — ExecutionGate bypass on `resume_processing_job` | Phase 0 | `resume_processing_job` now wrapped in `gate.run()` with bounded retry + backoff; gate is authoritative |
| Cross-dispatcher checkpoint corruption | ExecutionGate | Per-instance lease prevents concurrent `graph.astream` calls |
| Sync/async deadlock | `asyncio.to_thread` wrapping | All sync DB calls offloaded to thread pool |

## 10. Related Docs

- [`docs/architecture.md`](../architecture.md) — top-level architecture overview
- [`docs/architecture/concurrency-model.md`](concurrency-model.md) — concurrency model and threading strategy
- [`docs/features/job-queue.md`](../features/job-queue.md) — JobQueue feature documentation
- [`docs/job-queue.md`](../job-queue.md) — job queue operational guide
- [`docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md`](../bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md) — root cause of the cross-dispatcher bug that motivated ExecutionGate
- [`docs/architecture/unified-dispatch-architecture.md`](unified-dispatch-architecture.md) — target dispatch architecture (proposed state); this doc covers the **current** implemented state
- [`docs/plans/unified-dispatcher.md`](../plans/unified-dispatcher.md) — migration plan for dispatcher unification
