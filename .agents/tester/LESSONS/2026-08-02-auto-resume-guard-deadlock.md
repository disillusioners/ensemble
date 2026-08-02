# Auto-Resume Guard Deadlock — Root Cause & Fix

**Date:** 2026-08-02
**Related commits:** `5a7bc33b` (original fix), `338a72b0` (self-deadlock fix that hardened the guard)
**Severity:** Important (🟠) — blocks PAUSED auto-resume end-to-end

## Problem

The `5a7bc33b` fix for PAUSED auto-resume message loss correctly added a fallback: when `resume_processing_job` returns `None`, the router falls through to `enqueue_message_job`. This fixes the message-loss bug (messages are no longer silently dropped), but introduces a NEW deadlock: the enqueued job is never claimed by any worker.

## Root Cause

`enqueue_message_job` creates a JobItem that stays in `admission_state='queued'`. The cross-system guard (hardened by `338a72b0`) has two conditions that interact to create a permanent deadlock:

### Deadlock Mechanism

```
Instance state: PAUSED (but idle — no suspended turn exists)

Step 1: User sends message → resume_processing_job returns None
Step 2: Fallback fires → enqueue_message_job creates:
        - Task #2 (new, PENDING, work_id=MESSAGE_JobItem.job_id)
        - MESSAGE JobItem (admission_state='queued')

DB state after fallback:
  Task #1 (original, PENDING, no JobItem)  ← from initial spawn
  Task #2 (new, PENDING, linked to JobItem)
  MESSAGE JobItem (queued)

Guard evaluation for Task #1 (cross-system guard):
  EXISTS(JobItem queued + Task linked to JobItem with status PENDING) → TRUE → BLOCKED

Guard evaluation for Task #2 (queue-awareness guard):
  NOT EXISTS(JobItem for task.work_id with admission_state='queued') → FALSE → BLOCKED

Result: BOTH tasks permanently blocked. Workers retry indefinitely.
```

### Why `enqueue_message` Works Instead

`enqueue_message` (not `enqueue_message_job`):
- Creates Task directly — **NO JobItem** created
- Notifies worker pool directly (line 1548-1549 in instance_messaging.py)
- Queue-awareness guard passes (no linked JobItem → NOT EXISTS is TRUE)
- Cross-system guard passes (no queued JobItem → subquery returns nothing)
- Both tasks become claimable immediately

## Fix

Changed `daemon/routers/messages.py` PAUSED branch fallback:
- **Before:** `await manager.enqueue_message_job(...)` with `source="api_resume_fallback"`
- **After:** `await manager.enqueue_message(...)` with `source="api_resume_fallback"`

Also updated `tests/unit/test_paused_auto_resume_fallback.py` mock from `enqueue_message_job` to `enqueue_message`.

## Why Unit Tests Missed This

The unit tests in `test_paused_auto_resume_fallback.py` mock the manager surface (`resume_processing_job`, `enqueue_message_job`). They verify the fallback CALL fires, but never exercise the real worker pool claim path. The deadlock is invisible to mocked tests — only a daemon-level E2E test catches it.

**Lesson:** Auto-resume/routing fixes need at least one daemon E2E test that verifies the enqueued message is actually CLAIMED and PROCESSED by a worker, not just enqueued.

## Timeline

1. `cced02cc` — removed `cascade_resume` fallback → message loss (PAUSED messages silently dropped)
2. `5a7bc33b` — added `enqueue_message_job` fallback → message no longer lost, but guard deadlock
3. **This fix** — changed to `enqueue_message` → message delivered AND claimable
