# Review Report: Job System Improvements Plan v4

**Verdict: 🔴 Needs Work — 2 blocking issues must be resolved before implementation**

**Reviewed:** 6 plan files + 1 investigation document  
**Sessions:** review-phase2, review-phases13, review-cross-cut (3 parallel)  
**Date:** 2025-04-18  

---

## Summary

**13 issues: 4 Critical, 5 Warnings, 4 Suggestions**

The plan's architectural philosophy is sound — "observe don't duplicate" (ADR-009) is the right call, the state machine design is well-structured, and the phased approach with clear coupling assessments is good. However, there are **two blocking critical issues** in Phase 2 that would cause the implementation to fail silently:

1. **INSTANCE_COMPLETED only fires for child instances** — not for top-level job instances
2. **The observer can't detect error/termination events** — missing filters and missing event types

---

## 🔴 Critical (Must Fix Before Implementation)

### C1. INSTANCE_COMPLETED only fires for child instances — Phase 2's entire feedback mechanism fails

**Area:** Phase 2 / EventBus integration  
**Files:** `phase2-plan.md:83-95,112`, `plan-overview.md:127`, `manager.py:1630-1718`

**Issue:** The plan assumes `INSTANCE_COMPLETED` fires for ALL instances. In reality, `_create_completion_events()` (manager.py:1630) is called ONLY from `_process_child_completion_and_notify_parent()`, which returns early for top-level instances:

```python
# manager.py:1716
if instance.parent_id is None:
    logger.debug(f"Instance {instance_id[:8]}... has no parent, skipping completion check")
    return
```

Job instances are top-level (`parent_id=None`). **They never publish INSTANCE_COMPLETED.** The observer would never receive events for the majority of jobs.

**Impact:** All top-level job instances stay PROCESSING forever — the exact problem Phase 2 is supposed to fix.

**Fix:** Choose one:
- **(Recommended)** Add `INSTANCE_COMPLETED` publishing for top-level instances when their message queue drains or their processing completes
- **(Alternative)** Wire the existing but unused `_complete_job_for_instance()` (manager.py:562) into the completion path for top-level instances

---

### C2. No event for instance error/termination — observer missing critical filters

**Area:** Phase 2 / EventBus integration  
**Files:** `phase2-plan.md:84-85,227-230`, `event_bus.py:317-343`, `event/models.py:13-23`

**Issue:** The observer only filters for `INSTANCE_COMPLETED`, but:
- `INSTANCE_ERROR` doesn't exist in EventKind (only `ERROR` exists)
- `INSTANCE_TERMINATED` doesn't exist (shown in plan's cancellation diagram but never published)
- `terminate_instance()` doesn't publish any EventBus event at all
- `ERROR` events ARE published for task failures but the observer doesn't watch for them

**Impact:** Error and termination scenarios bypass the observer entirely. Jobs in error states stay PROCESSING.

**Fix:**
1. Observer must also filter for `event_type == "error"` → `fail_job()`
2. Either add termination event to EventBus OR document that `terminate_instance()` handles job completion internally (which it already does via `complete_job_sync()`)
3. Update plan's cancellation diagram to reflect reality

---

### C3. EventBus event dict uses `event_type`, not `kind` — plan has wrong filter field

**Area:** Phase 2 / EventBus integration  
**Files:** `phase2-plan.md:95`, `event_bus.py:335-338`

**Issue:** Plan says: `filter by kind == EventKind.INSTANCE_COMPLETED.value`. But `subscribe_all()` returns dicts from `_broadcast_to_global()` which uses `event_type`, not `kind`:

```python
event = {"instance_id": instance_id, "event_type": event_type}
```

**Impact:** Observer's filter would never match — wrong field name.

**Fix:** Filter by `event["event_type"] == EventKind.INSTANCE_COMPLETED.value`

---

### C4. `last_heartbeat_at` on JobItem is an orphaned field

**Area:** Phase 1 / Field consistency  
**Files:** `phase1-plan.md:88`, `phase2-plan.md:262-266`

**Issue:** Phase 1 Task 3.1 adds `last_heartbeat_at` to JobItem "Activated By Phase 2". But Phase 2 removed job-level heartbeat entirely. The field will be created in the migration but never written to by any phase.

**Impact:** Dead column in the database. Confusing for implementers.

**Fix:** Remove `last_heartbeat_at` from Phase 1's JobItem field list. Add it to Phase 2's "What Was Removed" table explicitly. (Lock heartbeat on `JobLock` is separate and should be kept.)

---

## 🟡 Warnings (Should Fix)

### W1. Investigation §7.2 contradicts ADR-009

**Area:** Cross-plan consistency  
**Files:** `tasks-job-investigation.md:554-631`

Investigation recommends `JobTimeoutMonitor` and `JobHeartbeat`, but ADR-009 explicitly rejects both. The redundancy analysis table (§6) also says "Jobs need timeout/recovery/retry" which contradicts v4 decisions.

**Fix:** Add supersession notice to investigation §7.2 and §6: *"Superseded by ADR-009 — see decisions.md for current architecture."*

---

### W2. `cancel_instance()` doesn't exist — only `cancel_instance_requests()`

**Area:** Phase 2 / Cancellation cascade  
**Files:** `phase2-plan.md:198`, `manager.py:2160`

Phase 2 Task 4.2 says "Verify that manager.py has a `cancel_instance()` method." It doesn't — only `cancel_instance_requests()` exists, which cancels HTTP requests but doesn't set task cancellation flags.

**Fix:** Promote to formal sub-task: implement `InstanceManager.cancel_instance()` that cascades cancellation to the WorkerPool.

---

### W3. No observer health monitoring or restart

**Area:** Phase 2 / Failure modes  
**Files:** `phase2-plan.md:80-87`

If the observer's async loop dies, jobs stay PROCESSING until daemon restart. No health check, no restart mechanism documented.

**Fix:** Wrap observer loop in try/except with restart. Add health logging if no events processed in N minutes.

---

### W4. `cleanup_instance()` race with observer

**Area:** Phase 2 / Failure modes  
**Files:** `manager.py:2200`, `phase2-plan.md:248-254`

`terminate_instance()` calls `cleanup_instance()` (removes event queue) then completes the job inline. The observer may try to process a late event for an already-terminated instance.

**Fix:** Document this race. Observer should catch `InvalidTransitionError` silently (debug log, not warning). `atomic_transition()` naturally handles this via rowcount=0.

---

### W5. No `PENDING → FAILED` transition for validation errors

**Area:** Phase 1 / State machine  
**Files:** `phase1-plan.md:40-52`

No path from PENDING to FAILED exists. If a job fails validation before being picked up, there's no way to mark it FAILED.

**Fix:** Add `(PENDING, FAILED): "reject"` to the transition table as a placeholder for pre-processing failures.

---

## 🟢 Suggestions (Consider)

| # | Area | File | Issue |
|---|------|------|-------|
| S1 | ADR-004 note | `decisions.md:74` | Note says EventBus consumers "don't interact" — but Phase 2's observer IS a job-level consumer of task-level EventBus. Update note. |
| S2 | Architecture diagram | `plan-overview.md:129` | Arrow `IM -.->|terminate_instance| JF` is misleading — terminate_instance is called ON instance, not sent TO observer. |
| S3 | Lock vs Job heartbeat | `phase1-plan.md:95` | Clarify: "Job-level heartbeat on JobItem removed. Lock heartbeat on JobLock is separate and retained." |
| S4 | Startup recovery timeout | `phase2-plan.md:143-185` | Consider configurable timeout (60s) for startup recovery to prevent blocking daemon startup with many PROCESSING jobs. |

---

## Positive Assessment

These aspects are well-designed:
- ✅ **"Observe-don't-duplicate" philosophy** (ADR-009) — correct architectural decision
- ✅ **State machine with atomic transitions** — single-statement SQL + rowcount verification
- ✅ **Phased approach** — coupling assessments are accurate (tight Phase 1→2, loose Phase 1→3/4)
- ✅ **Single migration** (ADR-005) — pragmatic for SQLite
- ✅ **Dead-letter queue design** (ADR-003) — separate table is the right call
- ✅ **Auto-retry same job** (ADR-007) — preserves job_id for traceability
- ✅ **Phase 3 FAILED→DLQ path** — verified correct without TIMED_OUT
- ✅ **TIMED_OUT removal** — fully clean across all plan files
- ✅ **EventBus as integration point** — right concept, wrong assumptions about what's published

---

## Recommended Path Forward

1. **Resolve C1 and C2 first** — these are architectural blockers
2. **Wire `_complete_job_for_instance()` or add top-level instance events** — this is the core implementation change needed
3. **Clean up C3, C4** — straightforward fixes during implementation
4. **Address W1-W5** — important for robustness but not blocking
5. **Investigation document** — add supersession notices pointing to ADR-009

The plan is **close to ready**. The core architecture (observe vs duplicate, state machine, DLQ, retry) is solid. The gap is in Phase 2's assumptions about what the EventBus currently publishes. Once the event publishing reality is aligned with the plan's expectations, this is implementable.
