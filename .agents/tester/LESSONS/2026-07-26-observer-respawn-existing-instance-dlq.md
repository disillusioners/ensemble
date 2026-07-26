# Lesson: Observer Re-Spawns Existing Instance → UniqueViolation → DLQ [RESOLVED]
**Date:** 2026-07-26
**Surfaced during:** FIFO concurrency fix validation (Part B) on commit `67eb16b1`
**Resolved by:** commit `b6d4953f` — `fix: prevent observer re-spawn UniqueViolation for message jobs`
**Status:** ✅ **RESOLVED** (validated 2026-07-26, see RESULTS/2026-07-26-observer-respawn-fix-validation.md)

## Problem (before the fix)

When a message job sat in a FIFO queue (correctly blocked by `concurrency_limit`) and later got dispatched after the prior job released its slot, the `job_feedback_observer` **re-attempted to spawn the instance** that was created at enqueue time. The instance already existed in the DB, so the spawn `INSERT INTO instances` raised:

```
(psycopg.errors.UniqueViolation) duplicate key value violates unique constraint "instances_pkey"
Key (instance_id)=(95cfe765...) already exists.
```

Consequence: the job was reported `FAILED` and landed in the **DLQ** with `reason=MANUAL`, `admission_state=dead` — **even though the message itself executed correctly** (the instance responded "DONE").

## The fix (commit `b6d4953f`)

The observer now detects message-type jobs and skips the spawn (the instance already exists, created at enqueue time). Instead it wakes the worker pool for the pre-existing Task directly:

```
Observer (message branch): woke worker pool for pre-existing Task on job 31ad7fc3...
/ instance 6a37a176... (no spawn; instance already exists for this message job)
```

No `spawn_instance_with_mcp`, no `enqueue_message`, no `complete_job(FAILED)`. The JobItem transitions cleanly: `queued` → `active` → `done`.

## Validation (2026-07-26, commit `b6d4953f`)

Reproduced the exact scenario (FIFO `concurrency_limit=1`, 2 messages to 2 IDLE instances). All symptoms eliminated:

| Pattern | Previous run (67eb16b1) | After fix (b6d4953f) |
|---------|------------------------|----------------------|
| `failed to spawn instance` | appeared | **gone** ✅ |
| `duplicate key value violates unique constraint` | appeared | **gone** ✅ |
| `UniqueViolation` | appeared | **gone** ✅ |
| `released 0 lock(s)` (scenario jobs) | appeared | **gone** ✅ (now `released 1 lock(s)`) |
| Job B in DLQ | yes (false `FAILED`) | **no** ✅ |
| Job B `admission_state` stuck at `queued` | yes | **no** ✅ (now `active` → `done`) |

E2E Release Gate: 4/4 PASS (no regressions). See `RESULTS/2026-07-26-observer-respawn-fix-validation.md`.

## Key distinction (still relevant for DLQ triage)

- The **message execution succeeds** at the instance level (correct response emitted).
- The **job-level tracking** is now also correct (job → `done`, not DLQ).
- Any DLQ triage should still cross-check instance-level success before treating entries as real failures — but the specific false-DLQ-from-observer-respawn path is now closed.
