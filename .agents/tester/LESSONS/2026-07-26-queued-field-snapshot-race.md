# Lesson: `queued` Field Snapshot Race — False Positive When Slot Available [RESOLVED]
**Date:** 2026-07-26
**Surfaced during:** Queued Message Feedback Feature validation (commits `0ecc91f7`+`dbe382dd`)
**Resolved by:** uncommitted race fix on `feature/queue-dispatch-option-b` — synchronous slot accounting
**Status:** ✅ **RESOLVED** (validated 2026-07-26, see RESULTS/2026-07-26-queued-feedback-race-fix-retest.md)

## Problem (before the fix)

The `MessageResponse.queued` field — which the chat UI uses to decide whether to show the `⏳ Queued: "..." — waiting for slot` indicator — was correct ONLY when a slot was genuinely unavailable. When a slot IS available, it returned a deterministic **false positive** (`true`).

| Actual slot state | API `queued` field (before fix) | Correct? |
|-------------------|-----------------------------|----------|
| Genuinely unavailable (slot full) | `true` | ✅ |
| Available (admitted within ~200ms) | `true` | ❌ false positive |

## Root Cause (before the fix)

In `daemon/routers/messages.py` `send_message`, the snapshot read `JobItem.admission_state` via `JobQueueService.get_job` **immediately** after `enqueue_message_job` returned. But:

1. `JobQueueService.enqueue` (`daemon/services/job_queue_service.py:787-808`) created the JobItem with `admission_state='queued'` (the DB default)
2. It then fired `dispatch_bus.notify_new_job()` — a **fire-and-forget asyncio Event signal**, not a synchronous admission
3. The actual `queued → active` admission happened **asynchronously** in the `JobProcessor`'s claim loop ~100-200ms later
4. The immediate `get_job` read raced the admission

## The Fix (synchronous slot accounting)

The `queued` field is no longer read from `admission_state` after enqueue. Instead, it's computed **synchronously** in `enqueue_message_job` via slot accounting: `active_count >= concurrency_limit`. This decouples the `queued` signal from the async admission lifecycle entirely — no race possible.

## Validation (2026-07-26, race fix)

Re-tested all 3 scenarios that define the `queued` field truth table. All PASS:

| Scenario | Actual slot state | Before fix | After fix | Correct? |
|----------|------------------|------------|-----------|----------|
| Available-slot (FIFO, 1 msg) | Available | `true` ❌ | `false` ✅ | ✅ FIXED |
| Full-queue msg1 (FIFO, 1st msg) | Available | `true` ❌ | `false` ✅ | ✅ FIXED |
| Full-queue msg2 (FIFO, 2nd msg) | Unavailable (full) | `true` ✅ | `true` ✅ | ✅ still correct |
| Parallel msg1 (concurrency=5) | Available | `true` ❌ | `false` ✅ | ✅ FIXED |
| Parallel msg2 (concurrency=5) | Available | `true` ❌ | `false` ✅ | ✅ FIXED |

E2E Release Gate: 4/4 PASS (no regressions). See `RESULTS/2026-07-26-queued-feedback-race-fix-retest.md`.

## Key takeaway

When a status field depends on an async state transition (like `admission_state` lifecycle), **never** read it immediately after triggering the transition — the snapshot will race the async completion. Instead, compute the answer synchronously from the source-of-truth data (here: queue slot accounting: `active_count >= concurrency_limit`).
