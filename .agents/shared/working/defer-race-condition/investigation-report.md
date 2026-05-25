# Investigation Report: Defer Job Race Condition in Job Queue

**Date:** 2026-05-25  
**Status:** Root Cause Identified — Investigation Only (No Fixes)  
**Severity:** HIGH

---

## Executive Summary

**A deferred job can start executing while non-defer work is still active because `JobFeedbackObserver` bypasses the defer queue idle check when triggering the next job after a completion event.**

The correct idle check exists in `JobProcessor._process_next_job()` but is never reached when the observer handles job chaining directly. This is the primary root cause.

A secondary contributing factor is that `_get_next_job()` doesn't respect queue type ordering — it picks ANY highest-priority pending job.

---

## Bug #1 (PRIMARY): JobFeedbackObserver Bypasses Defer Idle Check

### Location
- **File:** `daemon/services/job_feedback_observer.py:329-337`

### The Bypass

When a job completes, the observer triggers the next job:

```python
# job_feedback_observer.py:329-337
if job.project_id:
    next_job = await self._job_queue_service._get_next_job(job.project_id)  # ← NO defer check
    if next_job is None:
        return
    started_job = await self._job_queue_service.start_job(next_job.job_id)  # ← Starts immediately
```

**No defer idle check is performed.** The observer picks the next job by priority and starts it regardless of whether it's from a defer queue.

### The CORRECT Check (That's Bypassed)

The `JobProcessor` has the proper defer idle check at `daemon/services/job_processor.py:192-205`:

```python
if queue.queue_type == "defer" and pending:
    non_defer_active = await asyncio.to_thread(
        self._queue_service._repository.count_active_jobs_in_non_defer_queues, queue.project_id
    )
    if non_defer_active > 0:
        continue  # Skip defer queue — project has active non-defer work
```

**This check is NEVER reached when the observer handles job chaining.** The observer calls `_get_next_job()` directly, completely bypassing the queue iteration and defer logic.

### Why This Causes the Reported Bug

**Exact scenario:**
1. A MESSAGE job (child agent communication) is PROCESSING on the PARALLEL queue
2. Another MESSAGE job completes → `JobFeedbackObserver` fires
3. Observer calls `_get_next_job(project_id)` → picks the **highest priority** pending job
4. If a DEFER job has higher priority than other pending FIFO/PARALLEL jobs → it gets picked
5. Observer calls `start_job()` → defer job starts immediately
6. **DEFER job is now running while the MESSAGE job is still PROCESSING** — violates defer semantics

---

## Bug #2 (CONTRIBUTING): `_get_next_job()` Ignores Queue Type

### Location
- **File:** `daemon/services/job_queue_service.py:783-811`
- **Repository method:** `daemon/repositories/job_queue/repository.py:245-263`

### The Problem

```python
# job_queue_service.py:804-808
elif project_id:
    pending = await asyncio.to_thread(
        self._repository.list_pending_by_project, project_id
    )
    return pending[0] if pending else None  # ← Returns ANY highest-priority job
```

```python
# repository.py:255-263
stmt = (
    select(JobItem)
    .where(JobItem.project_id == project_id)
    .where(JobItem.status == JobStatus.PENDING.value)
    .where(JobItem.deleted_at.is_(None))
    .order_by(col(JobItem.priority).desc(), JobItem.created_at.asc())  # ← No queue_type filter
)
```

**`list_pending_by_project()` returns ALL pending jobs ordered by priority + timestamp with ZERO queue type awareness.** A defer job with `priority=10` would be returned before a FIFO job with `priority=5`.

This means even if the observer DID check queue type, `_get_next_job()` wouldn't help — it doesn't expose queue information in its return value in a way that enables filtering.

---

## Bug #3 (EDGE CASE): `waiting_for` Not Set Without `send_message`

### Location
- **File:** `daemon/services/instance_lifecycle.py:315-317`

### The Gap

```python
# NOTE: waiting_for is NOT incremented here
# Only send_message to a child increments waiting_for
```

When a parent spawns a child via `spawn_instance()`, `waiting_for` is NOT incremented. It's only incremented when `send_message()` is called with a child as the target (instance.py:480-496).

**Impact:** If a parent spawns a child but communicates through a mechanism OTHER than `send_message()` (or the child self-starts), `waiting_for` stays at 0. The parent's MESSAGE job would complete immediately, potentially triggering the defer race through the observer.

This is less common but creates a latent vulnerability.

---

## How the Defer Mechanism Works (Correct Flow)

### Design Intent

The defer queue is supposed to ONLY dequeue jobs when ALL non-defer queues (FIFO + PARALLEL) are idle:

```
JobProcessor._process_next_job()
  → iterates queues in order
  → for each DEFER queue: count_active_jobs_in_non_defer_queues()
  → if count > 0: skip (don't dequeue from defer)
  → if count == 0: safe to dequeue
```

### Re-check Triggers

Deferred jobs are re-checked via:
1. **Event-driven:** `dispatch_bus.notify_new_job()` called from `enqueue()` and `trigger_next_job()`
2. **Polling fallback:** Every 30 seconds (`_poll_interval`)

### Active Job Definition

An "active" job = status IN (`PENDING`, `PROCESSING`) AND queue_type != `defer` AND deleted_at IS NULL.

---

## Race Condition Timing Diagram

```
                    CORRECT PATH (JobProcessor)              OBSERVER PATH (Bypass)
                    ──────────────────────────               ──────────────────────────
T0: Job-A completes (non-defer)              │  T0: Job-A completes (non-defer)
T1: EventBus publishes event                 │  T1: Observer picks up event
T2: JobProcessor polls/triggered             │  T2: Observer calls _get_next_job(project_id)
T3: Iterates queues in order                │  T3: Returns defer job (highest priority!)
T4: FIFO queue → picks next FIFO job ✓      │  T4: Observer calls start_job(defer_job)
T5: PARALLEL queue → picks next ✓           │  T5: DEFER JOB STARTS while work active ✗
T6: DEFER queue → idle check (count > 0?)   │  
T7: count=1 → SKIP defer ✓                  │  ← NO IDLE CHECK PERFORMED
```

---

## Code Paths Summary

| Path | Code | Defer Check? | Bug? |
|------|------|-------------|------|
| JobProcessor polling | `job_processor.py:192-205` | ✅ YES | No |
| JobFeedbackObserver | `job_feedback_observer.py:329-337` | ❌ NO | **YES** |
| `_get_next_job()` | `job_queue_service.py:783-811` | ❌ NO | **YES** |
| `list_pending_by_project()` | `repository.py:245-263` | ❌ NO | **YES** |

---

## Files Involved

| File | Role |
|------|------|
| `daemon/services/job_feedback_observer.py` | **BUG #1**: Bypasses defer check at line 331 |
| `daemon/services/job_queue_service.py` | **BUG #2**: `_get_next_job()` at lines 783-811 |
| `daemon/repositories/job_queue/repository.py` | **BUG #2**: `list_pending_by_project()` at lines 245-263 |
| `daemon/services/job_processor.py` | **CORRECT**: Defer idle check at lines 192-205 |
| `daemon/services/instance_lifecycle.py` | **BUG #3**: `waiting_for` not set at spawn (line 315) |
| `daemon/tools/instance.py` | `waiting_for` increment only in `send_message()` (line 480) |
| `daemon/services/child_reports.py` | Atomic completion flow (lines 708-742) |
| `daemon/services/message_job_handler.py` | `skip_complete` logic (lines 139-160) |

---

## Root Cause Summary

**Primary:** `JobFeedbackObserver._process_event()` calls `_get_next_job(project_id)` without performing the defer queue idle check. It picks and starts ANY pending job, including defer jobs, regardless of whether non-defer work is active.

**Contributing:** `_get_next_job()` and `list_pending_by_project()` have no queue type awareness — they pick jobs purely by priority and timestamp, with no defer-specific filtering.

**Edge case:** `waiting_for` is only incremented by `send_message()`, not `spawn_instance()`, creating a gap where a parent's job could complete before child work finishes.
