# Review Report: Job System Improvements v5 — Codebase Mismatch Fixes

**Verdict: ✅ Approved with Notes**

6 findings: 0 critical, 3 warnings, 3 suggestions

---

## Scope

All 6 plan files in `.agents/shared/planning/job-system-improvements/`:
- `plan-overview.md` — Updated architecture, state machine, phase index
- `phase1-plan.md` — Foundation (state machine, persistent locks, new fields)
- `phase2-plan.md` — Major rewrite v5: event publishing, observer, cancellation cascade, recovery, wiring
- `phase3-plan.md` — Resilience: DLQ + Auto-Retry
- `phase4-plan.md` — Performance: Event-Driven Dispatch + Idempotency
- `decisions.md` — ADR-001 through ADR-012

## Sessions Used

| Session | Target | Focus |
|---------|--------|-------|
| `ensemble review-manager` | InstanceManager (manager.py), JobQueueService | C1-C3 verification, ADR-011 cancellation cascade, hook points |
| `ensemble review-events` | EventBus, EventKind enum, complete_job_sync | Event structure, race condition analysis, dead code verification |

---

## Critical Fixes Verification

### C1: `INSTANCE_COMPLETED` never fires for top-level instances ✅ CONFIRMED

**Verdict: Plan fix is CORRECT**

**Codebase evidence:** `_process_child_completion_and_notify_parent()` at `manager.py:1715-1718`:
```python
if instance.parent_id is None:
    logger.debug(f"Instance {instance_id[:8]}... has no parent, skipping completion check")
    return  # ← Job instances hit this and NO event is published
```

The early return happens BEFORE `_create_completion_events()` (line 1739). Top-level instances (job instances always have `parent_id=None`) never emit `INSTANCE_COMPLETED`.

**Plan fix:** Add `_publish_instance_lifecycle_event()` as a new method, hook into the early return path to publish an event before returning. This is NEW functionality, not observation of existing behavior.

**Assessment:** The fix is sound. The new method creates a parallel event path for top-level instances without modifying the existing child completion flow.

---

### C2: `cancel_instance()` doesn't exist ✅ CONFIRMED

**Verdict: Plan fix is CORRECT**

**Codebase evidence:** No `cancel_instance()` method exists. Only `cancel_instance_requests()` at `manager.py:2160-2167`, which only cancels active LLM requests via the request registry — it does NOT stop tasks, cascade to children, or terminate instances.

**Plan fix:** Use existing `terminate_instance()` instead of creating a new method. The cancellation cascade is:
1. `cancel_job()` → `terminate_instance(instance_id)` → marks job FAILED
2. `atomic_transition(FAILED → CANCELLED)` → corrects to CANCELLED status

**Assessment:** Sound approach. Leverages existing proven cleanup path. The double transition (FAILED→CANCELLED) is safe via `atomic_transition()` with `WHERE status='FAILED'`.

---

### C2-NEW: Observer can't detect errors ✅ CONFIRMED

**Verdict: Plan fix is CORRECT**

**Codebase evidence:** `terminate_instance()` at `manager.py:2169-2241` does NOT call `_broadcast_to_global()` or any EventBus publish method. The only EventBus interaction is `_event_bus.cleanup_instance()` (line 2200) which removes listeners — it does not publish events.

**Plan fix:** Hook `_publish_instance_lifecycle_event()` into `terminate_instance()` AFTER the existing cleanup and job completion code. Events cover all terminal states: `completed`, `terminated`, `error`.

**Assessment:** Correct. The hook point in `terminate_instance()` is after line 2231 (`complete_job_sync`) and before return, ensuring the job is already marked FAILED before the event is published.

---

### C3: `_complete_job_for_instance()` is dead code ✅ CONFIRMED

**Verdict: Plan fix is CORRECT**

**Codebase evidence:** Method defined at `manager.py:562-598` but grep for `_complete_job_for_instance` in `/daemon` returns ZERO production callers. Only test files reference it.

The only actual job completion path is `terminate_instance()` at line 2231: `complete_job_sync(success=False)` — which always marks FAILED. **There is no successful completion path.**

**Plan fix:** The `JobFeedbackObserver` IS the new primary completion mechanism. It receives instance lifecycle events and calls `complete_job(success=True)` for successful completions.

**Assessment:** The observer approach is well-designed. It fills the gap without requiring changes to `_complete_job_for_instance()`. The dead code can be removed in a later cleanup.

---

### C3-NEW: Wrong filter field name ✅ CONFIRMED

**Verdict: Plan fix is CORRECT**

**Codebase evidence:** `_broadcast_to_global()` at `event_bus.py:335-343` constructs events with `"event_type"` key:
```python
event = {"instance_id": instance_id, "event_type": event_type, ...}
```

**Plan fix:** Observer filters on `event["event_type"] == "instance_lifecycle"` — NOT `kind`.

**Assessment:** Correct. ADR-012 documents this explicitly.

---

## Findings

### 🟡 Warning 1: Race Condition Description is Misleading

**Area:** `plan-overview.md` lines 189-192, Race Condition Handling section

**Issue:** The plan states:
> "`terminate_instance()` always wins because it runs **synchronously on the main thread** and completes before the observer processes the event from its async queue."

This is misleading. `terminate_instance()` is actually `async def` (line 2169), not synchronous. And `_broadcast_to_global()` is `async def` with `put_nowait()` (non-blocking).

**Why it still works correctly:**
1. `complete_job_sync()` is truly synchronous (`def`, not `async def`) — the DB write completes atomically without yielding
2. The event is published AFTER `complete_job_sync()` finishes (correct ordering per plan diagrams)
3. By the time the observer processes the event, the job is already in terminal state
4. `atomic_transition()` with `rowcount=0` handles any remaining race gracefully

**Recommendation:** Update the race condition description to accurately reflect the async nature:
> "`complete_job_sync()` is synchronous and completes atomically before `_broadcast_to_global()` queues the event. The observer's `atomic_transition()` always finds the job already in terminal state."

---

### 🟡 Warning 2: Cancellation Cascade — Observer May Fire Between FAILED and CANCELLED

**Area:** `phase2-plan.md` Task 4, `decisions.md` ADR-011

**Issue:** The cancellation cascade does:
1. `terminate_instance()` → marks job FAILED + publishes lifecycle event (Phase 2 addition)
2. `atomic_transition(FAILED → CANCELLED)` → corrects status

Between step 1 and step 2, the observer may pick up the `terminated` event and see the job as FAILED. Per the observer's logic, it skips `terminated` events ("handled by terminate_instance"). **But if the event is `terminated` and observer skips it, this is fine.**

However, there's a subtlety: the lifecycle event published from `terminate_instance()` has `status: "terminated"`. The observer's filter correctly skips these. But if the event were to arrive as `status: "error"` (from an error path), the observer would try `atomic_transition(PROCESSING → FAILED)` which would get `rowcount=0` (job already FAILED) — also fine.

**Assessment:** The design handles this correctly because:
- `terminated` events → observer skips
- Any other event → `atomic_transition()` returns `rowcount=0` → skip
- The double transition (FAILED→CANCELLED) always succeeds because it checks `WHERE status='FAILED'`

**Recommendation:** Add a brief note in the plan that the brief FAILED→CANCELLED window is safe because the observer skips `terminated` events and `atomic_transition()` handles concurrent access.

---

### 🟡 Warning 3: ADR-010 vs ADR-011 EventKind Naming Inconsistency

**Area:** `decisions.md` ADR-010 (line 188) vs `phase2-plan.md` Task 1 (line 67)

**Issue:** Two different EventKind approaches are mentioned:
- **ADR-010 (line 188):** "`INSTANCE_LIFECYCLE` EventKind" with a `status` field
- **Phase2-plan Task 1.1 (line 67):** "Alternative: add specific kinds `INSTANCE_TERMINATED`, `INSTANCE_ERROR` alongside existing `INSTANCE_COMPLETED`"
- **Plan-overview (line 130):** Mentions "MISSING: `INSTANCE_TERMINATED`, `INSTANCE_ERROR`"

ADR-010 (line 194) explicitly chose `INSTANCE_LIFECYCLE` with a `status` field OVER separate kinds. But the plan-overview still references the separate kinds as "MISSING".

**Recommendation:** Make the plan-overview consistent with ADR-010's decision. Remove the "MISSING: INSTANCE_TERMINATED, INSTANCE_ERROR" note and clarify that a single `INSTANCE_LIFECYCLE` event with `status` field is used instead.

---

### 🟢 Suggestion 1: Hook Point Line Numbers Need Updating

**Area:** `decisions.md` ADR-011, `phase2-plan.md` ADR-011 reference

**Issue:** Plan references approximate line numbers that are slightly off:
| Plan Says | Actual Line | Code |
|-----------|-------------|------|
| ~1505 | **1520** | `instance.status = InstanceStatus.COMPLETED.value` |
| ~1600 | **1615** | `parent.status = InstanceStatus.COMPLETED.value` |
| ~2197 | **2212** | `_instance_repository.update_status(instance_id, "terminated")` |

**Recommendation:** Update line number references. Minor issue since these are approximate and will be verified during implementation.

---

### 🟢 Suggestion 2: ADR-005 Title Says "Revised" but Scope Doesn't Reflect v5 Changes

**Area:** `decisions.md` ADR-005 (line 67)

**Issue:** ADR-005 title says "(Revised v3)" but the content references v5 removals. The "(Revised v3)" tag is stale.

**Recommendation:** Update to "(Revised v5)" for accuracy.

---

### 🟢 Suggestion 3: Dead Code Cleanup Not Mentioned

**Area:** `phase2-plan.md`, overall plan

**Issue:** `_complete_job_for_instance()` at `manager.py:562-598` is confirmed dead code. The plan introduces the `JobFeedbackObserver` as the new primary completion mechanism but doesn't mention removing or deprecating the dead code.

**Recommendation:** Add a cleanup task (can be Phase 2 or a follow-up) to remove `_complete_job_for_instance()` and any related dead code paths.

---

## ADR Soundness Assessment

| ADR | Topic | Sound? | Notes |
|-----|-------|--------|-------|
| ADR-001 | Custom State Machine | ✅ | Correct for 6 states / 8 transitions |
| ADR-002 | DB-Backed Locks | ✅ | SQLite WAL handles this volume |
| ADR-003 | Separate DLQ Table | ✅ | Clean separation |
| ADR-004 | In-Process Event Bus | ✅ | asyncio.Event is appropriate |
| ADR-005 | All Fields Phase 1 | ✅ | Single migration, simpler |
| ADR-006 | Exponential Backoff | ✅ | Standard pattern |
| ADR-007 | In-Place Retry | ✅ | Preserves traceability |
| ADR-008 | Atomic Transitions | ✅ | Core safety mechanism |
| ADR-009 | No Duplicate Timeout | ✅ | Correct delegation to tasks |
| **ADR-010** | **Instance Lifecycle Events** | ✅ | **New — verified against codebase** |
| **ADR-011** | **Cancellation Cascade** | ✅ | **New — verified against codebase** |
| **ADR-012** | **Event Field Name** | ✅ | **New — verified against codebase** |

---

## Race Condition Assessment

The plan identifies three concurrent actors that can complete the same job:

| Actor | Trigger | Resolution |
|-------|---------|------------|
| `terminate_instance()` | User cancel/error | `complete_job_sync()` runs synchronously |
| `JobFeedbackObserver` | Instance completes naturally | `atomic_transition()` with rowcount check |
| `JobRecoveryService` | Startup recovery | `atomic_transition()` with rowcount check |

**Assessment:** The `atomic_transition()` pattern with `rowcount=0` → skip is the correct resolution. The first writer wins, others gracefully skip. The plan-overview's race condition section (lines 199-210) is functionally correct despite the misleading "synchronous" description (see Warning 1).

---

## Summary

| Category | Count |
|----------|-------|
| 🔴 Critical | 0 |
| 🟡 Warning | 3 |
| 🟢 Suggestion | 3 |

### Verdict: ✅ Approved with Notes

All 5 critical codebase mismatch fixes (C1, C2, C2-NEW, C3, C3-NEW) are **confirmed correct** against the actual codebase. The architectural design is sound — the observer pattern fills the completion gap, the cancellation cascade leverages existing infrastructure, and the race condition handling via `atomic_transition()` is robust.

The 3 warnings are accuracy/clarity issues in documentation, not architectural problems. None block implementation.

### Implementation Readiness

The plan is ready for implementation. Recommended before starting:
1. Fix the EventKind naming inconsistency (Warning 3) — affects Phase 2 Task 1
2. Correct the race condition description (Warning 1) — affects developer understanding
3. Add the brief FAILED→CANCELLED window note (Warning 2) — affects completeness
