# Lesson: Observer Re-Spawns Existing Instance → UniqueViolation → DLQ
**Date:** 2026-07-26
**Surfaced during:** FIFO concurrency fix validation (Part B) on commit `67eb16b1`
**Severity:** Pre-existing bug, orthogonal to the FIFO fix — but causes false job-failure/DLQ when a queued message job eventually starts
**Status:** NOT FIXED — recommended follow-up (architecture-level, out of scope for the FIFO fix)

## Problem

When a message job sits in a FIFO queue (correctly blocked by `concurrency_limit`) and later gets dispatched after the prior job releases its slot, the `job_feedback_observer` **re-attempts to spawn the instance** that was created at enqueue time. The instance already exists in the DB, so the spawn `INSERT INTO instances` raises:

```
(psycopg.errors.UniqueViolation) duplicate key value violates unique constraint "instances_pkey"
Key (instance_id)=(95cfe765...) already exists.
```

Consequence: the job is reported `FAILED` and lands in the **DLQ** with `reason=MANUAL`, `admission_state=dead` — **even though the message itself executed correctly** (the instance responded "DONE").

## Why this surfaced now (and not before)

This is a **pre-existing** bug, NOT caused by the FIFO fix (`67eb16b1` only touches `daemon/repositories/task/repository.py`). It surfaced during the FIFO validation only because the fix *correctly* serialized two messages — so Job B's dispatch happened *later* (after Job A released the slot), which triggered the observer's re-spawn attempt for B. Before the FIFO fix, both jobs would have run immediately (concurrency bypassed), so this observer path was not exercised for message jobs.

## Root cause hypothesis

The `job_feedback_observer` spawn path assumes the instance does not yet exist when a job starts. For message-type jobs under Option B, the instance is created at **enqueue time** (`enqueue_message_job`), not at dispatch time. So when the observer's spawn logic runs on dispatch, it tries to `INSERT` a row that already exists.

## Recommended fix (for a separate follow-up — NOT quick-fix eligible)

Guard the observer's spawn with an **existence check** before `INSERT INTO instances`. If the instance already exists (message-type job, instance created at enqueue), skip the spawn and proceed directly to `notify_work()` / task routing.

Likely location: `job_feedback_observer.py` (the spawn path triggered around job start).

## Reproduction

1. Create a FIFO queue with `concurrency_limit=1`.
2. Send 2 messages to 2 different IDLE instances in that queue (short prompts).
3. Observe: while Job A runs, Job B is correctly blocked (the FIFO fix works).
4. When Job A finishes and Job B starts → Job B lands in DLQ with `reason=MANUAL` despite the instance responding correctly.

## Key distinction (for future debugging)

- The **message execution succeeds** at the instance level (instance status → running → completed, correct response emitted).
- The **job-level tracking fails** (job → DLQ → `admission_state=dead`) because the observer's spawn-INSERT throws.
- So the symptom is a **false job-failure**, not a real execution failure. Any DLQ triage should cross-check instance-level success before treating these as real failures.
