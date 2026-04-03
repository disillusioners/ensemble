# Bug: Async Coroutine Not Awaited in `terminate_instance`

**Date:** 2026-03-31  
**Status:** Investigated (pending fix)  
**Severity:** Medium (silent failure - locks not released on session termination)

---

## Summary

`terminate_instance()` in `daemon/manager.py` calls `release_lock_by_session()` without awaiting it, causing the coroutine to never execute.

## Root Cause

| Location | Type |
|----------|------|
| `daemon/manager.py:1865` | Sync function calling async function without await |

**Code:**
```python
# Line 1814: terminate_instance is NOT async
def terminate_instance(self, session_id: str) -> bool:
    ...
    # Line 1865: release_lock_by_session IS async but not awaited
    released_projects = self._job_queue_service.release_lock_by_session(session_id)
```

```python
# job_queue_service.py:455
async def release_lock_by_session(self, session_id: str) -> list[str]:
    ...
```

## Symptoms

```
RuntimeWarning: coroutine 'JobQueueService.release_lock_by_session' was never awaited
daemon.manager - WARNING - Failed to release locks for session 771c13b2...: object of type 'coroutine' has no len()
```

## Impact

1. **Project locks are not released** when sessions terminate
2. Deadlocks possible if locked projects cannot be reused
3. Silent failure - no error thrown, but locks accumulate

## Fix Options

### Option 1: Make `terminate_instance` async (Recommended)
```python
async def terminate_instance(self, session_id: str) -> bool:
    ...
    released_projects = await self._job_queue_service.release_lock_by_session(session_id)
```

### Option 2: Create sync wrapper in JobQueueService
```python
def release_lock_by_session_sync(self, session_id: str) -> list[str]:
    return asyncio.run(self._job_queue_service.release_lock_by_session(session_id))
```

### Option 3: Fire-and-forget with create_task
```python
import asyncio
asyncio.create_task(self._job_queue_service.release_lock_by_session(session_id))
```

## Notes

- `terminate_instance` is called from sync context in `daemon/tools/session.py:132`
- Option 1 requires updating all call sites to await
- Option 2 adds complexity but minimizes changes
- Option 3 is simplest but loses error handling
