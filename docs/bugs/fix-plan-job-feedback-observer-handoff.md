# Fix Plan: JobFeedbackObserver cross-instance handoff crash

**Bug doc**: `docs/bugs/job-feedback-observer-cross-instance-handoff.md`
**Date**: 2026-06-10
**Approach**: Option B (refactored) — shared dispatch logic, zero-delay handoff preserved

---

## Strategy

Extract a shared `dispatch_started_job()` method so both the observer (quick path) and the poller share one implementation of "what happens after PENDING→PROCESSING transition". The observer's handoff block gets corrected scoping and a message-concurrency pre-check, but the heavy routing logic lives in one place.

---

## Step 1: Add `dispatch_started_job()` to `JobQueueService`

**File**: `daemon/services/job_queue_service.py`

Add a new async method. This is the single source of truth for post-`start_job()` logic:

```python
async def dispatch_started_job(self, started_job: JobItem) -> None:
    """Route a started job: message→MessageJobHandler, task→spawn+enqueue.

    Called by both JobFeedbackObserver and JobProcessor after a successful
    start_job() transition. Contains all routing and error handling that
    previously was duplicated between the two callers.

    Args:
        started_job: JobItem already in PROCESSING state with instance_id set.
    """
    if getattr(started_job, 'job_type', 'task') == "message":
        if self._message_job_handler is None:
            # Misconfiguration: mark FAILED so the job doesn't sit in PROCESSING forever
            logger.error(
                f"dispatch_started_job: MESSAGE job {started_job.job_id[:8]}... "
                "received but MessageJobHandler is not configured; failing job"
            )
            await self.complete_job(
                started_job.job_id,
                demand_state=DemandState.FAILED,
                error="MessageJobHandler not configured",
            )
            return
        await self._message_job_handler.handle(started_job)
        return

    # task job: spawn instance + enqueue message
    instance_id = started_job.instance_id
    try:
        instance_id = await self._instance_manager.spawn_instance_with_mcp(
            agent_id=started_job.agent_id,
            instance_id=instance_id,
            project_id=started_job.project_id,
        )
    except Exception as e:
        logger.error(f"dispatch_started_job: failed to spawn instance for job {started_job.job_id[:8]}...: {e}")
        await self.complete_job(
            started_job.job_id, demand_state=DemandState.FAILED, error=str(e)
        )
        return

    try:
        await self._instance_manager.enqueue_message(
            instance_id=instance_id,
            message=started_job.message,
            source=started_job.source,
        )
    except Exception as e:
        logger.error(f"dispatch_started_job: failed to enqueue message for job {started_job.job_id[:8]}...: {e}")
        await self.complete_job(
            started_job.job_id, demand_state=DemandState.FAILED, error=str(e)
        )
        return

    logger.info(
        f"dispatch_started_job: queued job {started_job.job_id[:8]}... "
        f"for instance {instance_id[:8]}..."
    )
```

**Pre-check helper** (recommended — avoids the 5-line duplication in both callers):

```python
async def is_instance_busy_with_message(self, job: JobItem) -> bool:
    """Check if a MESSAGE job's target instance already has another MESSAGE
    job processing. Used as a pre-check before start_job() to avoid
    unnecessary lock acquisition and to leave busy-target jobs PENDING.

    Must be called BEFORE start_job(). At that point, no MESSAGE job for
    `job.instance_id` is yet in PROCESSING for *this* job, so any active
    message job found here is by definition "another" job.

    Args:
        job: Pending JobItem to check.

    Returns:
        True if the target instance already has a MESSAGE job processing.
    """
    if getattr(job, 'job_type', 'task') != "message" or not job.instance_id:
        return False
    active = await asyncio.to_thread(
        self._repository.find_processing_message_jobs_by_instance,
        job.instance_id,
    )
    return bool(active)
```

---

## Step 2: Rewrite observer handoff block

**File**: `daemon/services/job_feedback_observer.py`
**Lines to replace**: 335–391 (the entire `try` block after lock release)

Replace with:

```python
        # Trigger the next pending job in the same queue — zero-delay handoff.
        # Uses shared dispatch_started_job() for correct routing and error handling.
        try:
            if not job.queue_id:
                return  # No queue scope — let the poller handle it

            next_job = await self._job_queue_service._get_next_job(queue_id=job.queue_id)
            if next_job is None:
                return

            # Pre-check: skip if another MESSAGE is already processing for the target instance
            if await self._job_queue_service.is_instance_busy_with_message(next_job):
                return  # Leave PENDING — poller will pick it up when instance is free

            started_job = await self._job_queue_service.start_job(next_job.job_id)
            if started_job is None:
                return

            await self._job_queue_service.dispatch_started_job(started_job)
            logger.info(
                f"Observer: triggered next job {started_job.job_id[:8]}... "
                f"for queue {job.queue_id[:8]}..."
            )
        except Exception as e:
            logger.warning(
                f"Failed to trigger next job for queue {job.queue_id[:8]}...: {e}"
            )
```

**Key changes**:
- `_get_next_job(project_id)` → `_get_next_job(queue_id=job.queue_id)` — scoped to the same queue, not the whole project
- Unconditional `spawn_instance_with_mcp` → `dispatch_started_job()` — shared routing handles both message and task jobs
- Added `is_instance_busy_with_message()` pre-check before `start_job()`
- Added `queue_id` guard — if the completed job has no queue, bail out and let the poller handle it
- Log line uses `queue_id` not `project_id` (matches the new scope)

---

## Step 3: Simplify poller to use shared dispatch

**File**: `daemon/services/job_processor.py`
**Lines to replace**: 523–570 (the `job_type` routing + spawn + enqueue + success log block inside `_process_next_job`)

Replace with:

```python
                    # Route via shared dispatch (message→handler, task→spawn)
                    await self._queue_service.dispatch_started_job(started_job)
                    continue
```

The pre-check at lines 493–505 can optionally be changed to use the helper:

```python
                if await self._queue_service.is_instance_busy_with_message(job):
                    continue
```

But this is cosmetic — the inline version works fine too.

**Note on `asyncio.CancelledError` handling**: the original poller code at lines 528–535 had explicit `CancelledError` handling that `return`ed (not `continue`d) out of `_process_next_job`. With the shared dispatch, `CancelledError` from `MessageJobHandler.handle()` will bubble up past `dispatch_started_job` and out of `_process_next_job` (since the outer `except Exception` at line 571 doesn't catch `BaseException`). The end behavior is the same: cancellation propagates correctly. No regression.

---

## Step 4: Ensure `_message_job_handler` is set on `JobQueueService`

**File**: `daemon/services/job_processor.py`, method `setup_message_job_handler()` (line 88)

The existing code already sets `self._queue_service._message_job_handler` at line 100. Verify this runs before the observer can fire. If not, the observer should guard against `None` in `dispatch_started_job` (already handled with the `if self._message_job_handler is not None` check).

---

## Step 5: Verify no regressions and add a regression test

Run the existing test suite first:

```bash
pytest tests/ -v
pytest tests/job_queue/ -v
```

Specifically check:
- **Message job routing**: a `message`-type job should go through `MessageJobHandler.handle()`, not `spawn_instance_with_mcp`
- **Task job spawning**: a `task`-type job should still spawn a new instance
- **Busy-instance skip**: if another MESSAGE is processing for the same instance, the observer should leave the job PENDING
- **Queue-scoped lookup**: observer only picks up jobs from the same queue as the completed job, not arbitrary jobs across the project

**Add a new regression test** that reproduces the original bug:

```python
# tests/integration/test_observer_handoff_cross_instance.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_observer_does_not_crash_on_cross_instance_handoff():
    """Regression: when instance A's job completes and a pending MESSAGE job
    targets instance B (busy), the observer must not call spawn_instance for B.

    Original bug: observer called _get_next_job(project_id) which returned
    the B-bound message job, then spawned it → UniqueViolation on instances_pkey.
    """
    # Setup: project P with two instances A (just completed) and B (busy)
    # - A has JobItem in PROCESSING, transitioning to COMPLETED
    # - B has a PENDING MESSAGE job in the same project
    # - Queue ID is shared
    ...
    # After A's lifecycle:completed event flows through the observer:
    # - A's job → COMPLETED ✓
    # - Observer handoff → _get_next_job(queue_id=...) finds the B-bound message job
    # - Pre-check: is_instance_busy_with_message returns True (B is busy)
    # - Observer returns without spawning → no crash
    # - Poller picks up the message job later, SKIPs it, message stays PENDING
    ...
    assert no_unique_violation_raised
    assert b_message_job.status == "pending"
```

This test should fail without the fix and pass with it.

---

## Summary of files to change

| File | Change |
|---|---|
| `daemon/services/job_queue_service.py` | Add `dispatch_started_job()` and `is_instance_busy_with_message()` methods |
| `daemon/services/job_feedback_observer.py` | Replace handoff block (lines 335–391) — queue-scoped lookup, pre-check, shared dispatch |
| `daemon/services/job_processor.py` | Replace lines 523–570 with `dispatch_started_job()` call; optionally use `is_instance_busy_with_message()` at lines 493–505 |
| `tests/integration/test_observer_handoff_cross_instance.py` | New regression test that fails on the old code, passes on the new code |

No changes to `MessageJobHandler`, `InstanceManager`, or repository layer.

---

## Risks and notes

- **`_get_next_job(queue_id=...)`**: The completed job's `queue_id` must be set. Verify that all jobs that go through the observer have `queue_id` populated. If `queue_id` is `None` on the completed job, the observer bails out (safe — poller handles it).
- **Orphan recovery untouched**: The poller's orphan-detection logic (lines 238–479) is poller-specific and should not be affected by this change.
- **Pre-check timing**: `is_instance_busy_with_message()` has a TOCTOU window between the check and `start_job()`. This is the same window the current poller code has (lines 493–505 vs 512). The `MessageJobHandler.handle()` has its own safety-net check (lines 66–97) that back-transitions PROCESSING→PENDING if a race occurs. So the defense-in-depth is maintained.
- **Architectural note**: after this fix, the observer is no longer purely a "completion notifier" — it also performs dispatch (previously only the poller did). The shared `dispatch_started_job()` keeps the two paths from drifting again. If a third caller is added in the future, it should also go through `dispatch_started_job()`.
- **Misconfiguration safety**: if `MessageJobHandler` is not configured (e.g., setup_message_job_handler wasn't called), `dispatch_started_job` will now mark MESSAGE jobs FAILED instead of leaving them in PROCESSING. This is a strict improvement over silent loss.

---

## Review: CancelledError propagation (Kilo, 2026-06-10)

### Verdict: Step 3's "No regression" note is incorrect — must fix before implementing

### The problem

`MessageJobHandler.handle()` re-raises `asyncio.CancelledError` at `message_job_handler.py:254` after completing the job as CANCELLED. This is by design — it signals the caller that the operation was interrupted.

**Current poller code** (`job_processor.py:528-535`) catches this explicitly:

```python
try:
    await self._message_job_handler.handle(started_job)
except asyncio.CancelledError:
    ...
    return  # Graceful: exits _process_next_job, _process_loop continues
```

**Proposed plan** replaces lines 523–570 with a bare call:

```python
await self._queue_service.dispatch_started_job(started_job)
continue
```

`CancelledError` is `BaseException`, not `Exception`, so it passes through:
- `dispatch_started_job` — no handler
- Poller's `except Exception` at line 571 — doesn't catch it
- `_process_loop`'s `except asyncio.CancelledError: raise` at line 172 — **kills the entire processing task**

A single message cancellation would stop ALL job processing. This is a regression.

**Observer path** is worse: `CancelledError` passes through `except Exception` at line 388, propagates to `_event_loop`, hits `except asyncio.CancelledError: self._running = False; break` at line 191 — **stops the observer entirely**.

### Required fix: add CancelledError handling in both callers

Do NOT handle it in `dispatch_started_job` — that method should stay a simple router. Different callers need different behavior.

**Poller (Step 3)** — replace the proposed two-liner with:

```python
                    try:
                        await self._queue_service.dispatch_started_job(started_job)
                    except asyncio.CancelledError:
                        logger.info(
                            f"CancelledError during dispatch of job {started_job.job_id[:8]}..., "
                            "returning from cycle"
                        )
                        return
                    continue
```

**Observer (Step 2)** — add to the handoff block:

```python
        try:
            if not job.queue_id:
                return

            next_job = await self._job_queue_service._get_next_job(queue_id=job.queue_id)
            if next_job is None:
                return

            if await self._job_queue_service.is_instance_busy_with_message(next_job):
                return

            started_job = await self._job_queue_service.start_job(next_job.job_id)
            if started_job is None:
                return

            await self._job_queue_service.dispatch_started_job(started_job)
            logger.info(...)
        except asyncio.CancelledError:
            return  # Don't crash the observer — let event loop continue
        except Exception as e:
            logger.warning(...)
```

### Other review items — all confirmed correct

| Item | Status |
|---|---|
| Line ranges (335–391 observer, 523–570 poller) match current code | ✓ |
| `_get_next_job(queue_id=...)` signature exists at `job_queue_service.py:799` | ✓ |
| `is_instance_busy_with_message` rename (was `should_skip_message_job`) — better name | ✓ |
| None-guard on `_message_job_handler` now fails job explicitly — strict improvement | ✓ |
| Queue-scoped lookup is correct for the observer handoff | ✓ |
| TOCTOU risk analysis correct — defense-in-depth via `MessageJobHandler.handle()` lines 66–97 | ✓ |
| Orphan recovery logic (lines 238–479) untouched | ✓ |
| Regression test skeleton is reasonable | ✓ |

### Minor nit: Step 4 wording

Line 187 says "already handled with the `if self._message_job_handler is not None` check" but the updated `dispatch_started_job` uses the inverted pattern (`is None → fail job`). Should read:

> "already handled — `dispatch_started_job` fails MESSAGE jobs if handler is None"
