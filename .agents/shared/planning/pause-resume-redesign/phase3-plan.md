# Phase 3: Resume Flow Redesign

## Objective

Rewrite the resume flow to cleanly transition jobs from `PAUSED → PROCESSING` and tasks from `PAUSED → PENDING`, then re-enter LangGraph from checkpoint. **Eliminate the premature completion bug** by replacing the direct `complete_job()` call with a **deterministic finalize trigger** that routes through the observer's transactional bus gate.

## Coupling

- **Depends on**: Phase 2 (Pause Flow Redesign)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/manager.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/job_feedback_observer.py`
- **Shared APIs/interfaces**: `resume_processing_job()`, `resume_instance_cascade()`, `_process_event()`, new `_process_resume_finalize()`
- **Why this coupling**: Resume must reverse what pause does (Phase 2). The state transitions must be symmetric.

## Context

- Phase 2 implemented the new pause flow (job → PAUSED, task → PAUSED, bus watchers preserved with compaction hook)
- The current `resume_processing_job()` at `manager.py:2589` has the **critical premature completion bug** at line 2898
- The bug: `complete_job(COMPLETED)` is called whenever bus reports 0 pending, but bus check and job transition are NOT atomic (TOCTOU race)
- **Critical gap (reviewer C1)**: Simply removing `complete_job()` and "letting `_process_event` handle it" is NOT sufficient — `_process_event` only fires finalize for `status IN (COMPLETED, ERROR)` (line 763). If the graph turn is a no-op, NO lifecycle event fires → **job stays PROCESSING forever**.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Transition job PAUSED → PROCESSING on resume | In `resume_instance_cascade()` or its DB sync helper, add logic to transition paused jobs back to PROCESSING. Use `atomic_transition` with `status = 'paused'` guard (only transition if currently PAUSED). | `daemon/services/instance_lifecycle.py:1056-1180` |
| 2 | Transition task PAUSED → PENDING on resume | In the same resume flow, transition paused tasks to PENDING so the WorkerPool can re-claim them. Do NOT set to RUNNING directly. | `daemon/services/instance_lifecycle.py:1056-1180` |
| 3 | Update `_resume_cascade_db_sync` | Extend the batched UPDATE that sets instance status to RUNNING to also transition jobs (PAUSED → PROCESSING) and tasks (PAUSED → PENDING) in the same WriteGuardSession. | `daemon/services/instance_lifecycle.py` (`_resume_cascade_db_sync`) |
| 4 | **Implement `_process_resume_finalize()` on the observer** (C1 fix) | Add a new method `_process_resume_finalize(instance_id, job_id, result_summary)` to `JobFeedbackObserver`. This is the **deterministic finalize trigger** that replaces the old direct `complete_job()`. It: (1) validates DependencyBus is not None (carries forward A9 hard-error from manager.py:2885), (2) calls the SAME transactional finalize path as `_process_event` — checks bus pending inside `_finalize_job_db_sync`'s WriteGuardSession, defers if >0 or finalizes if ==0. | `daemon/services/job_feedback_observer.py` (new method, near `_process_event` at line 698) |
| 5 | **Call `_process_resume_finalize()` after graph turn** (C1 fix) | In `_resume_processing_background()` (manager.py:2749), after the graph turn completes (whether it produced a result OR was a no-op), explicitly call `await self._job_feedback_observer._process_resume_finalize(instance_id, job_id, result_summary)`. This guarantees finalization regardless of whether a lifecycle event fires. REMOVE the old direct `complete_job()` call (manager.py:2898) and TOCTOU bus check (manager.py:2870). | `daemon/manager.py:2749-2905` |
| 6 | **Remove `complete_task()` block** (W2 fix) | The resume path at `manager.py:2944-2991` calls `complete_task()` on the original paused task. With the new PAUSED → PENDING → re-claim lifecycle, this block is **incorrect** — it would complete a PENDING task that should be naturally re-claimed by the WorkerPool. Remove or repurpose this block. | `daemon/manager.py:2944-2991` |
| 7 | Ensure `_process_event` handles resume-triggered events | After resume, when the graph turn produces a non-no-op result, the instance also emits a lifecycle event. Verify `_process_event` (job_feedback_observer.py:698) picks it up. Both `_process_event` (via lifecycle event) and `_process_resume_finalize` (via explicit call) check the bus gate — the `_finalize_job_db_sync` atomic transition `WHERE status = 'processing'` ensures only one wins. | `daemon/services/job_feedback_observer.py:698-828` |
| 8 | Handle child completion during pause (delayed processing) | If a child completed while the parent was paused, a PROCESS_REPORT task was created but blocked. On resume, this task becomes claimable. Verify the task is claimed and processed. Run bus watcher compaction hook (Phase 2 Task 4) BEFORE `notify_work()` to clean FIRED rows. | `daemon/services/child_reports.py`, `daemon/repositories/task/repository.py` |
| 9 | Remove zombie job cleanup (no longer needed) | The current code cleans stale PROCESSING jobs before creating new ones. With PAUSED state, there are no stale PROCESSING jobs — the job was PAUSED, not left PROCESSING. Remove or simplify the zombie cleanup code. | `daemon/manager.py:2669-2747` |
| 10 | Update `WorkerPool.notify_work()` timing | The resume flow already calls `worker_pool.notify_work()`. Verify this fires AFTER the task status is set to PENDING AND after bus watcher compaction, so workers see the updated state. | `daemon/services/instance_lifecycle.py:1173` |
| 11 | Write tests for new resume flow | Test that resume transitions job PAUSED → PROCESSING. Test that task goes PAUSED → PENDING → RUNNING (via claim). Test that no premature completion occurs. Test that no-op resume does NOT leave job stuck PROCESSING (C1 regression test). Test that delayed child reports are processed after resume. | `tests/unit/test_resume_flow_redesign.py` (new) |

## Key Files

- `daemon/manager.py` — `resume_processing_job()` (line 2589), `_resume_processing_background()` (line 2749), the premature completion bug (lines 2858-2905), `complete_task()` block (lines 2944-2991)
- `daemon/services/instance_lifecycle.py` — `resume_instance_cascade()` (line 1056), `_resume_cascade_db_sync`
- `daemon/services/job_feedback_observer.py` — `_process_event` (line 698), `_get_processing_job_for_instance` (line 518), `_finalize_job` (line 860), `_finalize_job_db_sync` (line 1641), new `_process_resume_finalize()` method
- `daemon/services/instance_messaging.py` — `_process_message_with_tracking` (line 1014)
- `daemon/routers/instances.py` — Resume endpoint (line 255-261)
- `daemon/services/task_processor.py` — PROCESS_REPORT processor (line 513)

## Constraints

- The resume flow must be atomic — all status transitions in one transaction
- The TOCTOU race must be eliminated — no non-transactional bus check before finalize
- **The no-op resume must NOT leave the job stuck PROCESSING** (C1) — the deterministic finalize trigger handles this
- The resume must not create new jobs — reuse the existing PAUSED job
- The resume must re-enter LangGraph from checkpoint (existing behavior, keep it)
- Task transition PAUSED → PENDING (NOT → RUNNING) to reuse safe claim mechanism
- The `_process_resume_finalize` must carry forward the A9 hard-error for `bus is None`

## Deliverables

- [ ] Resume transitions job PAUSED → PROCESSING atomically
- [ ] Resume transitions task PAUSED → PENDING atomically
- [ ] `_process_resume_finalize()` method implemented on observer (C1)
- [ ] `_process_resume_finalize()` called after every graph turn including no-ops (C1)
- [ ] A9 hard-error for `bus is None` carried forward (C1)
- [ ] Direct `complete_job()` call removed from resume path (TOCTOU eliminated)
- [ ] `complete_task()` block at lines 2944-2991 removed/repurposed (W2)
- [ ] No premature completion on resume
- [ ] No-op resume does NOT leave job stuck PROCESSING (C1 regression test)
- [ ] No zombie jobs on resume
- [ ] Delayed child reports (PROCESS_REPORT) are processed after resume
- [ ] Resume re-enters LangGraph from checkpoint correctly
- [ ] Bus watcher compaction runs before notify_work on resume
- [ ] Unit tests for new resume flow passing

## Critical Code Section: The Bug to Fix

### Current (BUGGY) — `manager.py:2858-2905`
```python
# TOCTOU: Non-atomic bus check
bus = get_dependency_bus()
if bus is not None:
    pending = await bus.count_pending_for_target(instance_id)  # <-- RACE WINDOW
    if pending > 0:
        skip_complete = True

if skip_complete:
    return

# Premature completion
await self._job_queue_service.complete_job(
    old_job_id,
    DemandState.COMPLETED,  # <-- BUG: completes even if child report racing
    result_summary=result.content if result else None,
)
```

### New (FIXED) — Deterministic Finalize Trigger (approach (c) from reviewer C1)
```python
# After the graph turn completes (or no-ops), trigger deterministic finalize.
# This replaces the old direct complete_job() call.
# _process_resume_finalize routes through the SAME transactional bus gate
# as _process_event, eliminating the TOCTOU race.
await self._job_feedback_observer._process_resume_finalize(
    instance_id=instance_id,
    job_id=old_job_id,
    result_summary=result.content if result else None,
)
```

Where `_process_resume_finalize` does:
```python
async def _process_resume_finalize(self, instance_id, job_id, result_summary=None):
    bus = get_dependency_bus()
    if bus is None:
        raise RuntimeError("DependencyBus is None during resume finalize — invalid state")  # A9 hard-error
    
    # Reuse the SAME transactional finalize path as _process_event
    job = await self._get_processing_job_for_instance(instance_id)
    if job is None:
        return  # Job already finalized by a racing _process_event — that's fine
    
    bus_pending = await bus.count_pending_for_target(instance_id)
    if bus_pending > 0:
        await self._emit_in_progress(job, instance_id)
        return  # Defer — children still completing
    
    # REUSE _finalize_job — do NOT reimplement finalize logic (approver note)
    await self._finalize_job(job, instance_id, "completed", error=None, result_summary=result_summary)
```

> **Approver note**: `_process_resume_finalize` MUST reuse `_finalize_job` rather than reimplementing it. The method above calls `self._finalize_job()` which delegates to `_finalize_job_db_sync` — the single transactional finalize path. This ensures all finalize logic (bus gate, lock release, SSE, `_trigger_next_job`) runs identically whether triggered by a lifecycle event or by resume.

The key insight: `_finalize_job_db_sync` uses `WHERE status = 'processing'` in its atomic transition, so if both `_process_event` (via lifecycle event) and `_process_resume_finalize` (via explicit call) try to finalize, only one wins. No double-finalize.

## Notes

- This is the highest-risk phase because it eliminates the core bug AND introduces a new finalize path
- The `_resume_processing_background` function will be significantly simplified
- The `is_retry=True` checkpoint resume mechanism stays the same
- The `ExecutionGate.run()` serialization stays the same
- The main changes: (1) remove old finalize code, (2) add `_process_resume_finalize()` call, (3) remove `complete_task()` block
- **Idempotency**: The `_finalize_job_db_sync` atomic transition (`WHERE status = 'processing'`) guarantees that the explicit call and any lifecycle event can't double-finalize. Only the first one to execute wins.
