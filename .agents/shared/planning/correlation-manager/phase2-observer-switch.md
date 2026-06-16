# Phase 2: JobFeedbackObserver Migration

## Objective
Migrate `JobFeedbackObserver` from reading the `waiting_for` counter to subscribing to CorrelationManager's `correlation.complete` events. This eliminates Race #1 (HIGH severity) — the TOCTOU window between reading `waiting_for` and acting on it.

## Coupling
- **Depends on**: Phase 0 (gate fix), Phase 1 (CorrelationManager exists)
- **Coupling type**: loose
- **Shared files with other phases**: `job_feedback_observer.py` (Phase 3 unifies cascade logic)
- **Shared APIs/interfaces**: `CorrelationManager` direct callback, EventBus subscribe_all (inbound)
- **Why this coupling**: Phase 2 consumes CM's callback; only needs the callback interface contract

## Context
- Current `job_feedback_observer.py:255-302` reads `waiting_for` via `await asyncio.to_thread(instance_repository.get, instance_id)` — a slow, race-prone read
- The window between read and `atomic_transition` includes `await _get_last_assistant_message_raw` (LLM fetch from checkpointer) — long await
- CorrelationManager fires `correlation.complete` callback only when all message-responses are resolved — no snapshot needed, no TOCTOU

### Race #1 Detail

```
T1: Child completes → lifecycle event published
T2: JobFeedbackObserver._process_event() picks up event
T3:   job = await get_job_by_instance(instance_id)         ← still PROCESSING
T4:   wf = await instance_repository.get(instance_id)      ← snapshot: wf=0
T5:   result_summary = await _get_last_assistant_message_raw()  ← SLOW (LLM/checkpointer)
       ─────── TOCTOU WINDOW ───────
       T5a: Another child completes
       T5b: child_reports cascade fires
       T5c: New lifecycle event published
       T5d: Parent now has pending messages
T6:   atomic_transition(job, PROCESSING → COMPLETED)      ← wrong! pending messages exist
```

After Phase 2, the observer no longer reads `waiting_for` at T4. Instead, CorrelationManager fires its callback only when ALL message responses are resolved — eliminating the snapshot race entirely.

## Event Delivery: Direct Callback (Not EventBus Queue)

### Why Not EventBus for `correlation.complete` (Fix C2 + C3)

| Issue | EventBus Limitation | Impact |
|-------|---------------------|--------|
| C2: DB persistence | `create_event()` ALWAYS writes to DB (`event_bus.py:174-181`) | Unnecessary DB writes for ephemeral state |
| C3: Queue overflow | `put_nowait()` silently drops on full (`event_bus.py:347-351`) | Dropped `correlation.complete` = parent stuck forever |

### Solution: Callback Registration

The observer registers itself as the `completion_callback` with CorrelationManager:

```python
# In daemon initialization (manager.py)
self._correlation_manager = CorrelationManager(
    instance_repository=self._instance_repository,
    message_queue_repository=self._message_queue_repository,
    completion_callback=self._job_feedback_observer.handle_correlation_complete,
)
```

The callback is invoked within CorrelationManager's per-parent Lock, so:
- No race between concurrent completions for the same parent
- No queue overflow (direct function call)
- No DB persistence (ephemeral state)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Implement `handle_correlation_complete` on JobFeedbackObserver | New method: receives `(parent_id, terminal_status)`, runs the job completion logic (atomic_transition + notify_watchers) | `daemon/services/job_feedback_observer.py` |
| 2 | Register observer as CM callback | Wire `observer.handle_correlation_complete` as `CorrelationManager.completion_callback` | `daemon/manager.py` |
| 3 | Remove `waiting_for > 0` guard (lines 255-286) | The guard is redundant — CM callback only fires when all responses resolved | `daemon/services/job_feedback_observer.py` |
| 4 | Keep `instance_lifecycle` handler for `in_progress` notifications | Single child completion still fires lifecycle event — use for progress display, NOT for job completion | `daemon/services/job_feedback_observer.py` |
| 5 | Keep `job.status != PROCESSING` check in callback | Idempotency guard — if job already terminal, don't re-process | `daemon/services/job_feedback_observer.py` |
| 6 | Handle late message arrival edge case | Design behavior for: CM fires `correlation.complete`, then a new `send_message` arrives | `daemon/services/job_feedback_observer.py` |
| 7 | Add test: callback triggers job completion | Test that CM callback transitions job PROCESSING → COMPLETED | `tests/test_observer_correlation.py` (new) |
| 8 | Add test: partial completion does NOT trigger | Test that resolving 1 of 2 responses does NOT fire callback | `tests/test_observer_correlation.py` |
| 9 | Add test: Race #1 scenario | Test the exact TOCTOU: child A completes, slow LLM fetch, child B completes during fetch → verify observer does NOT complete until CM fires | `tests/test_observer_race1.py` (new) |
| 10 | Add test: late message arrival | Test that `send_message` after `correlation.complete` re-registers in CM and prevents premature job completion | `tests/test_observer_late_msg.py` (new) |

## Key Design Decisions

### 1. Direct Callback, Not EventBus Subscription (Fix C2 + C3)
**Decision**: `correlation.complete` delivered via direct async callback registered with CorrelationManager.
**Rationale**:
- EventBus `create_event()` always persists to DB — no ephemeral mode (C2)
- EventBus `put_nowait()` silently drops on queue full (C3) — a dropped `correlation.complete` = parent stuck in PROCESSING forever
- Direct callback has no persistence overhead, no overflow risk, preserves ordering within Lock
- Observer still subscribes to EventBus for *inbound* `instance_lifecycle` events (for `in_progress` notifications)

### 2. Job Completion Decision Uses Correlation Callback, Not Lifecycle Event
**Decision**: The `atomic_transition` call now fires in `handle_correlation_complete` callback, not in the `instance_lifecycle` handler.
**Rationale**:
- CM callback fires only when ALL message responses are resolved — authoritative signal
- The `instance_lifecycle` handler is retained ONLY for `in_progress` notifications (progress display)
- Eliminates the Race #1 TOCTOU window entirely — no snapshot to go stale

### 3. `in_progress` Notification Still Fires on Child Completion
**Decision**: When any child message completes but other responses remain pending, emit `in_progress` to show progress.
**Rationale**:
- User-facing observability: "child 2 of 3 responded, 1 still pending"
- This is fired by the `instance_lifecycle` handler, not the CM callback
- Provides the same UX as the current `waiting_for` deferral log

### 4. Late Message Arrival Handling
**Decision**: If a new `send_message` arrives AFTER CM fires `correlation.complete`, the `register_message_send` call re-adds the parent to CM's tracking. If the job has already transitioned to terminal, the message is processed normally (instance revival logic handles it).
**Rationale**:
- Scenario: Parent's children all complete → CM fires callback → job transitions to COMPLETED. Then agent sends a new message to another child → `register_message_send` adds a new pending entry.
- This is correct behavior: the parent was complete, then new work arrived. The instance revival logic in `enqueue_message` (instance_messaging.py:773-783) handles reviving from COMPLETED → RUNNING.
- CM tracks the new correlation normally. When the new child responds, CM fires callback again — but the job is already in a new PROCESSING cycle.

### 5. `job.status != PROCESSING` Check Remains
**Decision**: Lines 245-250 still check `job.status == PROCESSING` before proceeding.
**Rationale**:
- Idempotency: if job already terminal (COMPLETED, FAILED), don't re-process
- CM may fire callback multiple times if rebuilt — guard prevents duplicate transitions
- Standard "already-processed" pattern

## Refactored Observer Logic

### Before (lines 204-337)
```python
async def _process_event(self, event: dict) -> None:
    # ... filter for instance_lifecycle ...
    if status in ("completed", "error"):
        # Race #1 window starts here
        instance_meta = await asyncio.to_thread(self._instance_manager._instance_repository.get, instance_id)
        wf = getattr(instance_meta, "waiting_for", None) or 0
        if wf > 0:
            # Defer: emit in_progress
            ...
            return
        # Race #1 window: LLM fetch between read and atomic_transition
        result_summary = await self._instance_manager._get_last_assistant_message_raw(instance_id)
        self._job_repo.atomic_transition(job_id, PROCESSING, COMPLETED, ...)
        await self._job_queue_service.notify_watchers(job_id, "completed")
```

### After
```python
class JobFeedbackObserver:

    async def handle_correlation_complete(
        self, parent_id: str, terminal_status: str
    ) -> None:
        """Called by CorrelationManager when ALL message responses are resolved.

        This replaces the waiting_for snapshot check. No TOCTOU window:
        CM only calls this when pending count reaches 0 within its Lock.
        """
        job = await self._job_queue_service.get_job_by_instance(parent_id)
        if job is None:
            logger.debug(f"CM callback: no job for parent {parent_id[:8]}...")
            return
        if job.status != JobStatus.PROCESSING.value:
            logger.debug(f"CM callback: job {job.job_id[:8]}... already {job.status}")
            return

        now = datetime.now(timezone.utc).isoformat()

        if terminal_status == "completed":
            result_summary = await self._instance_manager._get_last_assistant_message_raw(parent_id)
            if not result_summary:
                result_summary = "Job completed (no agent response captured)"
            self._job_repo.atomic_transition(
                job_id=job.job_id, from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.COMPLETED.value, completed_at=now,
                result_summary=result_summary,
            )
            await self._job_queue_service.notify_watchers(job.job_id, "completed")
        elif terminal_status == "error":
            self._job_repo.atomic_transition(
                job_id=job.job_id, from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.FAILED.value, completed_at=now,
            )
            await self._job_queue_service.notify_watchers(job.job_id, "failed")

        # Lock release + next job trigger (unchanged)
        self._lock_repo.release_by_instance(parent_id)
        if job.project_id:
            next_job = await self._job_queue_service._get_next_job(job.project_id)
            if next_job:
                ...

    async def _process_event(self, event: dict) -> None:
        """Handle inbound lifecycle events — used for in_progress notifications ONLY.

        Job completion is now handled by handle_correlation_complete (CM callback).
        This handler only fires progress notifications, NOT terminal transitions.
        """
        if event.get("event_type") != "instance_lifecycle":
            return
        data = event.get("data") or {}
        status = data.get("status")
        instance_id = data.get("instance_id")

        if status not in ("completed", "error"):
            return

        job = await self._job_queue_service.get_job_by_instance(instance_id)
        if job is None or job.status != JobStatus.PROCESSING.value:
            return

        # Single child completed — emit in_progress notification
        # (CM callback handles the actual terminal transition when all responses resolve)
        cm_pending = self._correlation_manager.get_pending_count(instance_id)
        if cm_pending > 0:
            try:
                progress_text = await self._instance_manager._get_last_assistant_message_raw(instance_id)
                await self._job_queue_service.notify_watchers(
                    job.job_id, status="in_progress",
                    progress=progress_text, waiting_for=cm_pending,
                )
            except Exception as e:
                logger.warning(f"Observer: failed to emit in_progress notification: {e}")
```

## Key Files

| File | Purpose |
|------|---------|
| `daemon/services/job_feedback_observer.py:204-427` | `_process_event` — refactor to in_progress-only; add `handle_correlation_complete` |
| `daemon/services/job_feedback_observer.py:255-286` | `waiting_for > 0` guard — remove |
| `daemon/services/correlation_manager.py` | CM callback registration target (from Phase 1) |
| `daemon/repositories/job_queue/repository.py:398-457` | `atomic_transition` — unchanged, still called by callback |
| `daemon/manager.py` | Wire CM callback to observer |

## Constraints
- Must not break the `in_progress` notification behavior
- Must remain idempotent (multiple CM callbacks for same parent)
- CM callback runs within per-parent Lock — must not block for long (LLM fetch is the longest operation)
- Both WorkerPool and JobQueue paths trigger `register_message_send` / `resolve_response` in CM
- LangGraph checkpointer calls in `_get_last_assistant_message_raw` are unavoidable; they happen at a safer point (within CM Lock, no TOCTOU)
- **Constraint (N4):** The `completion_callback` invoked by `resolve_response` executes while holding the per-parent `asyncio.Lock`. The callback MUST NOT call any CorrelationManager method for the same `parent_id` — this would deadlock (the Lock is already held by the caller). If the callback needs to trigger further CM operations (e.g., `register_message_send` for a cascading parent), it must schedule them as a separate task via `asyncio.create_task()` that runs after the lock is released.

## Verification Strategy

1. **Unit test — callback triggers completion**: Mock CM calling `handle_correlation_complete`; verify observer calls `atomic_transition(PROCESSING → COMPLETED)`
2. **Unit test — partial completion does NOT trigger**: Resolve 1 of 2 responses; verify CM does NOT fire callback; verify `in_progress` notification fires instead
3. **Race #1 regression test**: Simulate exact race — child A completes, slow LLM fetch starts, child B completes during fetch; verify observer does NOT call `atomic_transition` until CM fires callback
4. **In-progress notification test**: Verify `in_progress` notification still fires for single child completion via lifecycle event
5. **Idempotency test**: CM fires callback twice for same parent; verify `atomic_transition` called only once (second hits `job.status != PROCESSING` guard)
6. **Late message arrival test**: CM fires callback, job completes; then `send_message` re-registers in CM; verify new correlation cycle starts
7. **No-deadlock test (Fix N4)**: Verify that `handle_correlation_complete` does NOT call any CM method for the same parent_id; if cascade operations are needed, they are scheduled via `asyncio.create_task` (test that a task is created, not called inline)
8. **Existing test suite**: All tests that depend on observer behavior must pass unchanged

## Rollback Plan

1. Restore old `_process_event` logic from git (the `waiting_for` guard returns)
2. Remove `handle_correlation_complete` method
3. Unregister CM callback
4. Keep CorrelationManager running in shadow mode (revert to Phase 1 only)

The rollback is **safe** because:
- CorrelationManager still exists (no removal needed)
- Callback registration is a single line to remove
- No DB schema changes
- Observer behavior returns to reading `waiting_for` (back to Race #1, but stable)

## Deliverables
- [ ] `handle_correlation_complete` method implemented on JobFeedbackObserver
- [ ] Observer registered as CM `completion_callback`
- [ ] `_process_event` refactored to in_progress-only notifications
- [ ] `waiting_for > 0` guard removed from lifecycle handler
- [ ] Late message arrival handled (CM re-registers on new send_message)
- [ ] Race #1 regression test added
- [ ] Late message arrival test added
- [ ] All existing tests pass
