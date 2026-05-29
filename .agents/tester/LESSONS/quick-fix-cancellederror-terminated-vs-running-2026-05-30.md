# Quick Fix: CancelledError TERMINATED vs RUNNING Status

**Date**: 2026-05-30
**Branch**: fix/job-cancel-on-terminate
**Commit**: bcf9c3e

## Problem
The initial fix (`50191de` + `0e84202`) completed ALL non-PAUSED CancelledError cases as CANCELLED. This broke the existing test `test_message_job_handler_shutdown_propagates_cancelled_error` which expects RUNNING instances to propagate CancelledError without completing the job.

## Root Cause
Three distinct CancelledError scenarios need different handling:
1. **PAUSED** → job stays PROCESSING (for resume) — already correct
2. **TERMINATED** → complete as CANCELLED, then re-raise — new behavior
3. **RUNNING/other** → propagate CancelledError (failure handler deals with it) — must not change

## Fix
Changed `else` branch to `elif instance.status == TERMINATED` and added new `else` for RUNNING/other:

```python
elif instance and instance.status == InstanceStatus.TERMINATED.value:
    # Terminated — complete job as CANCELLED before re-raising
    try:
        await self._job_service.complete_job(...)
    except Exception:
        logger.warning(...)
    raise
else:
    # Other states — shutdown, propagate CancelledError
    logger.info(...)
    raise
```

## Lesson
When adding a new status-specific handler, check that the existing `else` branch serves multiple states. Split carefully to avoid changing behavior for states that don't need the new logic.
