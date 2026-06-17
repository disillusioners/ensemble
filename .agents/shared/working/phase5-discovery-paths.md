# Phase 5 Discovery: Dual-Path Unification

**Project:** agents-ensemble
**Date:** 2026-06-17
**Branch context:** `feature/correlation-manager`
**Phase:** 5 — Dual-Path Unification (Phase 0 error-handler consolidation complete; remaining divergences enumerated)
**Status:** Discovery only — no files modified

---

## 1. Executive Summary

Phase 0 unified **error-handling side-effects** across the two message-processing dispatchers via `handle_message_processing_error()` in `daemon/services/message_processing_errors.py`. Phase 5 must close the remaining **structural divergences** between the WorkerPool path (`ProcessMessageProcessor`) and the JobQueue path (`MessageJobHandler`) so that a message processed by either dispatcher produces identical observable state and lifecycle transitions.

**Confirmed remaining divergences (14 mirroring points identified, with multiple documented gaps):**

| Category | Gap |
|----------|-----|
| Pre-flight | WorkerPool has **no** cross-dispatcher pre-check; JobQueue has 2 (sibling MESSAGE job + running task) |
| Pre-pickup | WorkerPool has **no** WAITING_CHILDREN/IDLE → RUNNING transition; JobQueue has `transition_status_if` |
| Skip-complete | WorkerPool **always** completes its task; JobQueue defers when CM reports unresolved correlations (Phase 4) |
| Pause/terminate | WorkerPool has only a log line + re-raise; JobQueue discriminates PAUSED (leave PROCESSING) vs other (CANCELLED) |
| Lease contention | WorkerPool uses inline jittered backoff via `requeue_task_with_backoff`; JobQueue uses extracted `_requeue_for_contention` with dispatch-bus wake-up |
| CancellationToken | WorkerPool takes it as a parameter (no map); JobQueue creates and stores CTS in `_active_tokens` keyed by `job_id` |
| Retry context | WorkerPool derives `is_retry = task.retry_count > 0 OR resume_mode`; JobQueue uses only `is_retry=resume_mode` (metadata) |

The shared error helper is **not idempotent** — both paths invoke it exactly once per failure inside their `except Exception` clauses, with no other inline error side-effects that bypass it (one nuance: JobQueue's `handle()` also calls `complete_job(CANCELLED)` directly in the cancel handlers, which is correct because cancellation is not an error).

---

## 2. Files Analyzed

| File | Lines | Purpose |
|------|-------|---------|
| `daemon/services/task_processor.py` | 501 | WorkerPool path: `ProcessMessageProcessor` (active), `SendReportProcessor` (stub), `CleanupProcessor` (stub), `TaskProcessor` (router) |
| `daemon/services/message_job_handler.py` | 582 | JobQueue path: `MessageJobHandler` (active), `_requeue_for_contention` (helper), `cancel_message_job` (CTS-driven cancel) |
| `daemon/services/message_processing_errors.py` | 319 | Shared helper: `handle_message_processing_error` + pure helpers `_truncate_error`, `_classify_error_type` |

Pre-loaded context consulted:
- `dual-path-message-processing-workerpool-jobqueue-processmessageprocessor-message_20260617_110515.md` (HIGH confidence, Phase 0 background)
- `child-reports-py-error-reporting-py-message-job-handler-waiting-children-cascade_20260617_054547.md` (HIGH confidence, WAITING_CHILDREN cascade)
- `job-feedback-observer-handle-correlation-complete-callback-event-publication_20260617_054843.md` (HIGH confidence, terminal-transition callback)

---

## 3. The 14 Mirroring Points

Line numbers reference the **current** state on `feature/correlation-manager` after Phase 0 (commit `6d195812`).

| # | Stage | WorkerPool (`task_processor.py`) | JobQueue (`message_job_handler.py`) | Divergence Notes |
|---|-------|----------------------------------|-------------------------------------|------------------|
| 1 | **Entry signature** | L86 `async def process(self, task, cancellation_token=None) -> dict` | L75 `async def handle(self, job) -> None` | WP returns dict (drives worker loop); JQ returns None (state machine). JQ does **not** accept a CTS — it creates one. |
| 2 | **Pre-flight: identifier validation** | L101-102 `if not task.message_id: raise ValueError(...)` | L89-95 `if not instance_id: complete_job(FAILED, "missing instance_id"); return` | JQ completes the job before bailing; WP raises (the worker logs and marks the task failed separately). |
| 3 | **Pre-flight: sibling-MESSAGE check** | **MISSING** | L101-110 `find_processing_message_jobs_by_instance` then `_requeue_for_contention` | Authoritative safety net is still the gate's `try_acquire`; JQ pre-check is a fast-path optimisation. |
| 4 | **Pre-flight: cross-dispatcher (running task)** | **MISSING** | L120-141 `task_repo.find_running_by_instance` then `_requeue_for_contention` | **Asymmetric** — WorkerPool has no symmetric guard. See §8. |
| 5 | **Pre-pickup status transition** | **MISSING** | L168-188 `transition_status_if(WAITING_CHILDREN\|IDLE → RUNNING)` + `stream_status_change` | Drives UI/observability for the self-continuation case (`docs/bugs/root-instance-premature-completion-on-pending-message.md` Finding 5.3). |
| 6 | **Retry context extraction** | L133 `is_retry = task.retry_count > 0 or resume_mode`; L157 `retry_count=task.retry_count` | L201 `resume_mode`; L210-216 `retry_count from job_metadata with fallback to job.retry_count`; L224 `is_retry=resume_mode` | **Minor divergence**: WP treats any `retry_count > 0` as retry; JQ treats only `resume_mode=True` as retry. **Phase 0 Bug #2 fixed** retry_count propagation on JQ side. |
| 7 | **CancellationToken management** | L31 `cancellation_token: CancellationToken \| None = None` (param, not stored) | L144-145 create `cts = CancellationTokenSource()`; store `self._active_tokens[job.job_id] = cts`; L273/L292/L505 cleanup; L572-574 use in `cancel_message_job` | JQ needs named CTS so `cancel_message_job()` can signal it; WP doesn't expose a cancel API for tasks. |
| 8 | **`_do_process` closure** | L150-161 | L218-229 | Identical shape; param sources differ (task fields vs job.job_metadata). |
| 9 | **`execution_gate.run()` call** | L163-169 `holder_id=f"task:{task.id}" holder_kind=TASK` | L248-254 `holder_id=f"message_job:{job.job_id}" holder_kind=MESSAGE_JOB` | Same gate; different holder identifiers. |
| 10 | **LeaseLostError handling** | L170-189 inline `requeue_task_with_backoff` + return | L283-293 `_requeue_for_contention` + return | WP inline; JQ extracted. Different backoff primitives (`task_repo.requeue_task_with_backoff` vs `_job_repo.atomic_transition`). |
| 11 | **LeaseContention handling** | L190-241 inline (throttled DEBUG per occurrence + 60s INFO summary) + `requeue_task_with_backoff` + return | L266-274 `_requeue_for_contention` + return | WP: jittered backoff, per-instance counters. JQ: atomic_transition + queue-lock release + dispatch-bus notify. **See §6.** |
| 12 | **Message completion (`_queue_repository.complete`)** | L245-246 bare | L301-311 wrapped in `try/except Exception` with warn-log | Same DB call; different error handling — WP trusts the call; JQ defends it. |
| 13 | **Task/Job completion** | L250-254 `task_repo.complete_task(task.id, {...})` | L447-451 `_job_service.complete_job(job_id, COMPLETED, result_summary=...)` | Different terminuses. **WP unconditionally completes; JQ may skip** (see next row). |
| 14 | **Skip-complete check (CM/waiting_for)** | **MISSING** | L362-444 (CM authoritative via `get_correlation_manager().get_pending_count(instance_id)`; graceful-degradation fallback to `instance.waiting_for`) + L433-442 `notify_watchers("in_progress", waiting_for=wf)` | WorkerPool path cannot defer because task completion is owned by the worker loop; the JobQueue path can leave PROCESSING and let `JobFeedbackObserver` complete via the CM `handle_correlation_complete` callback (Phase 2). |
| 15 | **Dispatch (internal_report resolution + dispatch_completed)** | L256-299 | L313-346 | Near-identical. JQ adds `dispatch_source.startswith("internal_")` validation (line 328) before dispatch; WP does not. |
| 16 | **Child completion check** | L301-313 | L348-360 | Both call `self._manager._process_child_completion_and_notify_parent(instance_id, message_id)` wrapped in `try/except`. |
| 17 | **Pause/terminate discrimination** | L321-327 `except OperationCancelledError: raise; except asyncio.CancelledError: log("paused"); raise` | L459-482 `except OperationCancelledError: complete_job(CANCELLED); except asyncio.CancelledError: check instance.status — if PAUSED return, else complete_job(CANCELLED) + raise` | **JQ is significantly more sophisticated.** WP cannot distinguish pause from terminate because it does not own the job row. |
| 18 | **Shared error handler call** | L340-346 `handle_message_processing_error(instance_manager=..., instance_id=..., error=..., message_id=..., task_id=...)` | L497-503 `handle_message_processing_error(instance_manager=..., instance_id=..., error=..., message_id=..., job_id=...)` | Same helper, mutually-exclusive identifier (`task_id` vs `job_id`). |
| 19 | **`finally` cleanup** | (none — no CTS to pop) | L504-505 `self._active_tokens.pop(job.job_id, None)` |  |

> The "14 mirroring points" naturally expand to **19 enumerated rows** above when pre-/post-/error stages are broken out at the granularity the phase plan needs. Rows 2, 6, 12, 15, 16, 19 are **already aligned**; rows 3, 4, 5, 14, 17 are **confirmed gaps** on the WorkerPool side; rows 7, 9, 10, 11, 13 are **structurally different but not "missing"** (different identifier conventions, different storage, different observability primitives).

---

## 4. `handle_message_processing_error()` Call Sites

### 4.1 WorkerPool (task_processor.py)

**Single call site** — L340-346, inside the bottom-most `except Exception` of `ProcessMessageProcessor.process()`:

```python
except Exception as e:
    logger.error(f"Failed to process message task {task.id}: {e}", exc_info=True)
    await handle_message_processing_error(
        instance_manager=self._manager,
        instance_id=task.instance_id,
        error=e,
        message_id=task.message_id,
        task_id=task.id,
    )
    raise
```

**No bypass blocks.** The WorkerPool path produces no inline error event / lifecycle publish / `_send_error_report` outside this call. Pre-Phase 0, those three side-effects were inline; Phase 0 replaced them with the helper. No `try/except` blocks between this catch and the raise.

### 4.2 JobQueue (message_job_handler.py)

**Single call site** — L497-503, inside the outer `except Exception` of `MessageJobHandler.handle()`:

```python
except Exception as e:
    logger.error(
        f"MessageJobHandler: error processing MESSAGE job {job.job_id[:8]}...: {e}",
        exc_info=True,
    )
    await handle_message_processing_error(
        instance_manager=self._manager,
        instance_id=instance_id,
        error=e,
        message_id=message_id,
        job_id=job.job_id,
    )
```

**No bypass blocks.** Two other `except` clauses (`OperationCancelledError` L459, `asyncio.CancelledError` L466) complete the job as `DemandState.CANCELLED` directly — this is **correct** because cancellation is not an error and must not generate error events, lifecycle `"error"` publishes, or `_send_error_report`.

### 4.3 Inline-error-handling bypasses inside the happy path (legitimate, not bug-bypasses)

These exist in both paths and are **intentional best-effort** rather than duplicated error reporting:

| Location | Code | Rationale |
|----------|------|-----------|
| WP L297-299 | `except Exception as e: logger.error(...); # Don't fail the task` inside `dispatch_completed` | Dispatch is best-effort; should not propagate as task failure. |
| WP L308-313 | `except Exception as e: logger.error(...); # Don't fail the task` inside child-completion check | Child-completion is best-effort; should not propagate as task failure. |
| JQ L304-311 | `try/except` around `_queue_repository.complete(message_id)` with warn-log | Defensive against transient DB error; not a reported error. |
| JQ L344-346 | `except Exception as e: logger.error(...); # Don't fail the task` inside `dispatch_completed` | Same as WP. |
| JQ L355-360 | `except Exception as e: logger.error(...); # Don't fail the job` inside child-completion check | Same as WP. |
| JQ L424-427, L442 | `except Exception` around CM/DB status probe + `notify_watchers` | Defensive; log warn and continue. |

None of these would invoke `handle_message_processing_error` if extracted — they are not error reports, they are resilience wrappers around best-effort side-effects. They do **not** violate the "single error-handler call per failure" invariant.

---

## 5. Pre / Core / Post Stages Around `_process_message_with_tracking`

### 5.1 WorkerPool (`ProcessMessageProcessor.process`)

```
[ENTRY]        L86  async def process(self, task, cancellation_token=None)
[PRE-FLIGHT]   L101-102  validate task.message_id (raise on miss)
               L109-135  fetch message via _message_repo (or fallback _task_repo.get_by_message)
                         extract content, source, images, message_metadata
                         derive: resume_mode, is_retry, silent
[CORE BUILD]   L150-161  _do_process closure wrapping _process_message_with_tracking
               L163-169  execution_gate.run(holder_id=f"task:{task.id}", holder_kind=TASK, work_fn=_do_process)
[GATE EARLY-EXIT]  L170-189  LeaseLostError → requeue_task_with_backoff → return {success:False, requeued:True}
                  L190-241  LeaseContention → requeue_task_with_backoff + throttled log → return {success:False, requeued:True}
[POST-HAPPY]   L242     result = gate_outcome
               L245-246 _queue_repository.complete(message_id)
               L250-254 task_repo.complete_task(task.id, {success:True, message_id})
               L256-299 resolve dispatch_source (internal_report → original_source)
                          dispatch_completed(instance_id, message_id, source, content)
               L301-313 _process_child_completion_and_notify_parent(instance_id, message_id)
               L315-319 return {success:True, content:result.content, message_id}
[ERROR/HAPPY-EX]  L321-322 OperationCancelledError → raise
                  L323-327 asyncio.CancelledError → log("paused") → raise
                  L328-348 Exception → handle_message_processing_error(task_id=...) → raise
```

**No skip-complete stage** — WorkerPool path unconditionally marks the task complete.

### 5.2 JobQueue (`MessageJobHandler.handle`)

```
[ENTRY]        L75  async def handle(self, job) -> None
[PRE-FLIGHT]   L89-95   validate job.instance_id (complete_job(FAILED); return on miss)
               L101-110 sibling MESSAGE check → _requeue_for_contention → return
               L120-141 cross-dispatcher (running task) check → _requeue_for_contention → return
               L144-145 create CTS, register in _active_tokens[job.job_id]
               L168-188 transition_status_if(WAITING_CHILDREN|IDLE → RUNNING) + stream_status_change
[CORE BUILD]   L198-216 extract message_id, source, images, resume_mode, silent, retry_count from job.job_metadata
               L218-229 _do_process closure wrapping _process_message_with_tracking
               L248-254 execution_gate.run(holder_id=f"message_job:{job.job_id}", holder_kind=MESSAGE_JOB, work_fn=_do_process)
[GATE EARLY-EXIT] L266-274 LeaseContention (success path) → _requeue_for_contention → return
                  L283-293 LeaseLostError → _requeue_for_contention → return
[POST-HAPPY]   L295     result = gate_outcome
               L301-311 _queue_repository.complete(message_id)
               L313-346 resolve dispatch_source (internal_report → original_source)
                          dispatch_completed(instance_id, message_id, source, content)
               L348-360 _process_child_completion_and_notify_parent(instance_id, message_id)
               L362-444 CM-first skip-complete check (CM.get_pending_count → if >0: notify_watchers("in_progress") + return)
                          Graceful-degradation fallback (instance.waiting_for)
               L447-451 _job_service.complete_job(job_id, COMPLETED, result_summary)
[ERROR/HAPPY-EX] L459-465 OperationCancelledError → complete_job(CANCELLED)
                  L466-482 asyncio.CancelledError → PAUSED: return / other: complete_job(CANCELLED) + raise
                  L483-503 Exception → handle_message_processing_error(job_id=...)
                  L504-505 finally: _active_tokens.pop(job.job_id, None)
```

**Skip-complete stage is JobQueue-only** — WorkerPool cannot defer because task completion is owned by the worker loop and is not cancellable mid-flight.

---

## 6. Lease Contention Handling — Path Divergence

| Aspect | WorkerPool (task_processor.py L190-241) | JobQueue (message_job_handler.py L266-274 + helper L507-555) |
|--------|----------------------------------------|--------------------------------------------------------------|
| **Backoff primitive** | `task_repo.requeue_task_with_backoff(task.id)` — random `next_retry_at` 0.5-2.0s | `_job_repo.atomic_transition(job_id, processing → pending)` — no jitter |
| **Status guards** | Conditional on `status='running'`; leaves completed/cancelled tasks alone | Conditional on the `atomic_transition` returning a row; no-op on race |
| **Queue lock release** | N/A (task table has no per-job lock) | `_lock_manager.release_queue_lock(project_id, queue_id, job_id)` (L538-541) |
| **Dispatch-bus wake-up** | N/A (worker polls continuously on its own) | `bus.notify_new_job(project_id)` (L547-555) — wakes JobProcessor poll bus immediately so the freshly-requeued PENDING job isn't stranded for `_poll_interval` (default 30s) |
| **Observability** | Per-occurrence DEBUG log + per-instance 60s-throttled INFO summary with count (`self._contention_counts`, `self._last_info_at`) | Single INFO log per requeue (L524-527) with reason string |
| **In-memory state cleanup** | None | `_active_tokens.pop(job.job_id, None)` (L273/L292) |
| **Helper extraction** | Inline | `_requeue_for_contention(job, reason)` (L507-555) — also called from the pre-flight paths (L107, L138) |
| **Reason provenance** | Only "lease contention" (holder_id/holder_kind) | Three reasons: "another MESSAGE job is processing", "a task is RUNNING for this instance", "lease lost mid-execution" |

**Implication for Phase 5:** If the unified pipeline needs back-pressure on a hot instance, it should pick **one** of these primitives. WorkerPool's jitter is the better mechanism for preventing busy-spin, but the JobQueue path's dispatch-bus wake-up is what keeps the system responsive without the `_poll_interval` latency. A unified pipeline needs both.

---

## 7. Pause / Terminate Discrimination

| Aspect | WorkerPool (task_processor.py L321-327) | JobQueue (message_job_handler.py L459-482) |
|--------|----------------------------------------|--------------------------------------------|
| **OperationCancelledError** | `raise` (bare) | `complete_job(CANCELLED, "Message processing cancelled")` |
| **asyncio.CancelledError — pause path** | `logger.info("Task {id} paused")` then `raise` (worker marks task accordingly) | `if instance.status == PAUSED: return` (leave PROCESSING — next poll will resume the same job) |
| **asyncio.CancelledError — terminate path** | Falls through the same `raise`; worker treats as a generic cancellation | `complete_job(CANCELLED, "...instance terminated")` then `raise` |
| **Discrimination input** | None — pause and terminate look identical | `instance.status == InstanceStatus.PAUSED.value` from the DB row |
| **Why WP cannot discriminate** | The worker pool's `cancel_message_job` equivalent doesn't exist; task cancellation just raises | The DB row's status is the authoritative pause/terminate discriminator |

**Implication for Phase 5:** A unified pipeline needs to source the pause/terminate discrimination from somewhere. Options:
- Lift the discrimination into the shared helper (requires passing `instance_id` and the current `InstanceStatus` to it on cancel)
- Keep discrimination at the path entry (WorkerPool needs the same DB-status lookup as JobQueue, but that adds latency to every WorkerPool cancel)

---

## 8. Pre-Flight Cross-Checks (Confirmed Gap)

**WorkerPool has no pre-flight checks** before calling `execution_gate.run()`. Confirmed by:
- Direct read of `task_processor.py` L86-169: from `process()` entry through `execution_gate.run()`, the only logic is task/message validation (L101-125), retry-context derivation (L131-135), and closure construction (L150-161). There is no `find_processing_message_jobs_by_instance` or `find_running_by_instance` equivalent.
- The cross-dispatcher safety is provided **solely** by the gate's `try_acquire` (authoritative safety net).

**JobQueue has two pre-flights** (L101-141):
1. `find_processing_message_jobs_by_instance` (L102) — sibling MESSAGE job from the JobQueue side
2. `find_running_by_instance` (L135) — WorkerPool task actively driving graph.astream for the same instance

Both are explicitly documented (L97-100, L112-119) as **fast-path optimisations** rather than safety nets. The gate's `try_acquire` is the authoritative safety net in both cases.

**Why WorkerPool doesn't need them in principle:**
- WorkerPool's claim loop is atomic (`task_repo.claim_pending_task` returns a row only after a successful `UPDATE...WHERE status='pending' RETURNING`), so two workers can't both claim the same task.
- WorkerPool has no concept of "another dispatcher polling for the same instance" — only the gate protects it.

**Why the asymmetry may matter for Phase 5:**
- A hot instance receiving many concurrent child reports via the JobQueue side already self-throttles via the per-queue lock + `_poll_interval`. The WorkerPool side, by contrast, can spin a task through `process()` → `execution_gate.run()` → `LeaseContention` → `requeue_task_with_backoff` → next poll in a tight loop until the lease holder releases. The jittered backoff (L227-233) is the mitigation.
- A unified pipeline should preserve both pre-flight checks on the JobQueue side **and** the jittered backoff on the WorkerPool side; collapsing them into one path would either (a) add DB lookups to the WorkerPool path (latency) or (b) remove the fast-path optimisation from the JobQueue path (`_poll_interval` latency).

---

## 9. Dependencies and Path-Specific Callbacks

### 9.1 WorkerPool `ProcessMessageProcessor`

| Dependency | Source | Used For |
|------------|--------|----------|
| `instance_manager` (stored as `self._manager`) | constructor arg | `_process_message_with_tracking`, `execution_gate.run`, `_process_child_completion_and_notify_parent`, `_instance_repository.get` (for `original_source`) |
| `task_repo: TaskRepository` | constructor arg | `get_by_message`, `complete_task`, `requeue_task_with_backoff` |
| `event_repo: EventRepository \| None` | constructor arg | (currently unused in this path — declared in signature but never referenced) |
| `message_repository` | constructor kwarg `message_repository=instance_manager._queue_repository` | `get(message_id)`, `complete(message_id)` |
| `source_dispatcher` | constructor kwarg | `dispatch_completed(...)` |
| `MainLoopBridge.run_async` | module-level | bridges async `_run` from worker thread to event loop (L497) |

**Path-specific state:**
- `self._contention_counts: dict[str, int]` (L83) — per-instance contention counter for throttled INFO logging
- `self._last_info_at: dict[str, float]` (L84) — last INFO-emit timestamp per instance for 60s throttle

**Path-specific callbacks (none externally registered):** The processor is invoked by the worker loop directly via `TaskProcessor.run_task` → `processor.process(...)`. There is no external observer/callback hook.

### 9.2 JobQueue `MessageJobHandler`

| Dependency | Source | Used For |
|------------|--------|----------|
| `manager` (stored as `self._manager`) | constructor arg | `_process_message_with_tracking`, `execution_gate.run`, `_process_child_completion_and_notify_parent`, `_instance_repository.{get, transition_status_if}`, `_queue_repository.complete`, `_live_hub.stream_status_change` |
| `job_queue_service` | constructor arg | `complete_job`, `notify_watchers` |
| `job_repository` | constructor arg | `find_processing_message_jobs_by_instance`, `find_running_by_instance` (via `manager._task_repo`), `atomic_transition`, `cancel_job`, `get` |
| `source_dispatcher` | constructor arg | `dispatch_completed(...)` |
| `CorrelationManager` | module-level `get_correlation_manager()` (lazy import L391-394) | `get_pending_count(instance_id)` for skip-complete decision |

**Path-specific state:**
- `self._active_tokens: dict[str, CancellationTokenSource]` (L55) — CTS keyed by `job_id`, used by `cancel_message_job()` (L572-574)
- **Side effect of construction:** `manager._job_queue_service = job_queue_service` (L73) — force-set so the shared error helper can find the service via the manager facade. Documented in the comment block L56-72.

**Path-specific callbacks (externally invokable):**
- `cancel_message_job(job_id: str)` (L557-582) — JobQueueService entry point for cancellation. Reads the job row; if PENDING → `cancel_job()`; if PROCESSING → signal the stored CTS (the running `handle()` will catch the cancellation and discriminate pause/terminate).

### 9.3 Shared `handle_message_processing_error`

| Dependency | Required | Optional | Used For |
|------------|----------|----------|----------|
| `instance_manager._event_bus` | yes (else fallback) | — | `create_error_event(...)` |
| `instance_manager._event_repo` | fallback only | — | `create_event(...)` if no event bus |
| `instance_manager._events_service` | documented in docstring (L195) | not actually referenced in code | (declared for completeness; the lifecycle publish goes through `_publish_instance_lifecycle_event`) |
| `instance_manager._publish_instance_lifecycle_event` | yes | — | `publish(instance_id, status="error", error=..., parent_id=...)` |
| `instance_manager._send_error_report` | yes | — | `send_report(instance_id, error, error_type, message_id)` |
| `instance_manager._instance_repository` | yes | — | `get(instance_id)` for parent_id |
| `instance_manager._job_queue_service` | when `job_id` provided | — | `complete_job(job_id, FAILED, error=...)` |

**Pure helpers** (L52-148, no I/O, no shared state):
- `_truncate_error(error, max_len=MAX_ERROR_LEN)` — strips HTML, length-bounds.
- `_classify_error_type(e)` — maps `openai.APIStatusError` (413→`payload_too_large`, 429→`rate_limit`, 5xx→`server_error`, …), `openai.APITimeoutError`→`timeout_exhausted`, `ContextLengthExceededError`→`context_length_exceeded`, `CircuitOpenError`→`circuit_breaker_open`, connection errors→`connection_error`, `KeyError`→`instance_not_found`, `ValueError`→`invalid_data`, `RuntimeError`→`runtime_error`, default `execution_error`.

---

## 10. Open Questions / Risks for Phase 5

1. **Skip-complete semantics on WorkerPool.** A unified pipeline cannot simply mirror JQ's CM-aware skip-complete into WP, because the WorkerPool does not own a "PROCESSING" job row to leave behind. The closest equivalent would be a "task stays RUNNING, will be re-polled by the worker" semantic, but the worker's `claim_pending_task` already returned this row. This needs design input.

2. **Pause/terminate discrimination on WorkerPool.** Same root cause as #1: WorkerPool has no job-row state to consult. Options:
   - Lift the discrimination into the gate (`LeaseHolderKind.TASK_PAUSED` distinct from `TASK_TERMINATED`).
   - Add a fast-path DB status lookup to WorkerPool's cancel handler.
   - Accept that WorkerPool's pause/terminate is coarse-grained and document the gap.

3. **`_requeue_for_contention` vs `requeue_task_with_backoff`.** These two primitives do not have identical semantics. A unified pipeline needs to pick a single backoff primitive, OR explicitly compose them (jitter + dispatch-bus wake-up). If composing, the `_dispatch_bus.notify_new_job` call needs to be reachable from WorkerPool (currently it lives on `JobQueueService`).

4. **Pre-pickup status transition.** WorkerPool does not currently emit a `RUNNING` status_change before driving graph.astream. For the self-continuation case (root resumes after WAITING_CHILDREN), the observer would see only the terminal transition. Whether this is observable by UI/observability needs a decision.

5. **`is_retry` semantics.** WP treats any `retry_count > 0` as retry; JQ only treats `resume_mode=True` as retry. The phase plan should decide which is canonical.

6. **Error helper idempotency.** Per the pre-loaded context, `handle_message_processing_error` is **not idempotent**. Both paths must guarantee exactly-once invocation per failure. The current call structure (single `except Exception` → call → raise) satisfies this, but Phase 5 refactoring must preserve the invariant.

7. **Dispatch source validation.** JQ has an extra guard at L328 (`if not dispatch_source or dispatch_source.startswith("internal_"): dispatch_source = None`). WP lacks this guard. Without it, a malformed internal-source string could dispatch to an internal channel. This is a **latent bug** on the WorkerPool side that Phase 5 should consider.

8. **`event_repo` declared but unused.** `ProcessMessageProcessor.__init__` accepts `event_repo` (L58) but never references `self._event_repo` (L73) anywhere in the body. This is dead surface area from the multi-processor design; harmless but worth a cleanup.

---

## 11. Appendix — Cross-Reference Index

| Concern | WorkerPool location | JobQueue location | Helper / shared |
|---------|---------------------|-------------------|-----------------|
| `execution_gate.run` | L163-169 | L248-254 | `daemon/services/execution_gate.py` |
| LeaseContention type check | L190 | L266 | `daemon/services/execution_gate.py:LeaseContention` |
| LeaseLostError type check | L170 | L283 | `daemon/services/execution_gate.py:LeaseLostError` |
| `transition_status_if` | (n/a) | L169-174 | `daemon/repositories/instance/...` |
| `stream_status_change` | (n/a) | L177-179 | `live_hub` |
| `_process_message_with_tracking` | L151 | L219 | `daemon/manager.py` |
| `_process_child_completion_and_notify_parent` | L305 | L352 | `daemon/manager.py` |
| `_send_error_report` | (via helper L289-302) | (via helper L289-302) | `daemon/manager.py` |
| `_publish_instance_lifecycle_event` | (via helper L262-286) | (via helper L262-286) | `daemon/manager.py` |
| `dispatch_completed` | L290 | L337 | `daemon/dispatcher.py:ResponseDispatcher` |
| `complete_job` | (n/a) | L90-94, L447-451, L461-465, L473-478, L578-582 | `daemon/services/job_queue_service.py:JobQueueService` |
| `complete_task` | L251-254 | (n/a) | `daemon/repositories/task/repository.py` |
| `requeue_task_with_backoff` | L182, L232 | (n/a) | `daemon/repositories/task/repository.py` |
| `atomic_transition` | (n/a) | L529 | `daemon/repositories/job/...` |
| `cancel_job` (PENDING→CANCELLED) | (n/a) | L569 | `daemon/repositories/job/...` |
| `notify_watchers` | (n/a) | L435-441 | `daemon/services/job_queue_service.py` |
| `get_correlation_manager` | (n/a) | L391-394 | `daemon/services/correlation_manager.py` |
| `cm.get_pending_count(instance_id)` | (n/a) | L399 | `daemon/services/correlation_manager.py` |
| `OperationCancelledError` | L321 | L459 | `daemon/cancellation.py` |
| `CancellationTokenSource` | (uses param) | L144 | `daemon/cancellation.py` |

---

## 12. Summary Recommendation

Phase 5 unifies **18 distinct stages** around the central `_process_message_with_tracking` call (the "14 mirroring points" enumerated in §3, expanded to the granularity needed for code review). Of these:

- **6 stages already aligned** (§3 rows 2, 6, 12, 15, 16, 19) — no Phase 5 work needed
- **5 confirmed gaps on the WorkerPool side** (rows 3, 4, 5, 14, 17) — require architectural decisions per §10
- **7 stages are structurally different but not missing** (rows 1, 7, 8, 9, 10, 11, 13, 18) — different identifier conventions and storage primitives; Phase 5 should standardise them

The shared error handler is correctly invoked exactly once per failure on both paths with no inline bypasses; Phase 0 already closed that gap. Phase 5's risk is concentrated in the **pause/terminate discrimination** (§7) and **skip-complete semantics** (§5.2 / §10.1) — both of which require the WorkerPool side to gain DB-state awareness it currently lacks.
