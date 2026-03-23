# Scheduler Architecture Fix Plan

Generated from Oracle Architecture Review

## Overview

This document outlines all fixes needed to address the issues identified in the scheduler architecture review. Issues are organized by priority.

---

## P0 - Critical (Must Fix)

### 1. Add ScheduleExecution Cleanup on Source Delete
**File:** `daemon/repositories/source/repository.py`
**Location:** `delete_source_config()` method (lines 153-180)

**Problem:** When a scheduler source is deleted, `ScheduleExecution` records are not deleted, causing orphaned rows.

**Fix:**
```python
# Add after SessionMapping and ProcessedMessage deletion:
session.exec(
    sql_delete(ScheduleExecution).where(ScheduleExecution.schedule_id == source_id)
)
```

---

### 2. Auto-Disable One-Time Schedulers After Execution
**File:** `daemon/sources/adapters/scheduler.py`
**Location:** `_run_schedule()` method (lines 364-367)

**Problem:** One-time schedulers exit their loop after execution but the source remains `enabled=True` with `status=RUNNING` in database.

**Fix:**
1. Add a callback to the scheduler adapter to update source status after one-time execution completes
2. The callback should:
   - Set `enabled=False` in `source_configs`
   - Set `status=STOPPED` in `source_configs`
3. Register this callback during adapter creation (similar to `execution_callback`)

```python
# In SchedulerAdapter.__init__():
self._on_complete_callback = None  # New callback for completion

# After one-time execution in _run_schedule():
if self._on_complete_callback:
    await self._on_complete_callback(self.source_id, completed=True)
```

---

## P1 - High Priority

### 3. Fix Scheduler Creation to Auto-Start
**File:** `daemon/api.py`
**Location:** `create_source()` endpoint (lines 830-838)

**Problem:** Schedulers are forced `enabled=False` on creation, so the auto-start logic (lines 866-870) never triggers.

**Fix:** Two options - choose one:

**Option A (Recommended):** Allow `enabled=True` for schedulers and auto-start them:
```python
# In create_source(), remove the scheduler enabled check:
# Remove lines 830-838

# The existing auto-start logic (lines 865-870) will then work:
if source.enabled:
    await manager.source_registry.start_adapter(source.source_id)
```

**Option B:** Add dedicated `POST /schedules` endpoint that creates and starts scheduler:
- Create scheduler source via repository
- Immediately call `start_adapter()`
- **Note:** Lines 1025-1039 for `delete_source()` endpoint (2 lines off at start)

---

### 4. Unregister Adapter on Source Delete
**File:** `daemon/api.py`
**Location:** `delete_source()` endpoint (lines 1027-1039)

**Problem:** Deleting a source from DB doesn't stop/unregister the running adapter.

**Fix:**
```python
async def delete_source(source_id: str):
    # Get source to check type first
    existing = manager._source_repository.get_source_config(source_id)
    if not existing:
        raise HTTPException(...)
    
    # Stop and unregister adapter if running
    try:
        adapter = manager.source_registry.get(source_id)
        if adapter:
            await manager.source_registry.stop_adapter(source_id)
            manager.source_registry.unregister(source_id)
    except Exception as e:
        logger.warning(f"Failed to stop adapter during delete {source_id}: {e}")
    
    # Delete from database
    result = manager._source_repository.delete_source_config(source_id)
    ...
```

---

### 5. Unregister Should Stop Adapter First
**File:** `daemon/sources/registry.py`
**Location:** `unregister()` method (lines 67-90)

**Problem:** `unregister()` cancels supervisor task but doesn't stop the adapter, causing resource leaks.

**Fix:**
```python
def unregister(self, source_id: str) -> bool:
    """Unregister an adapter."""
    if source_id not in self._adapters:
        logger.warning(f"Adapter not found for unregistration: {source_id}")
        return False
    
    adapter = self._adapters[source_id]
    
    # Stop the adapter first if running
    if self._running.get(source_id, False):
        try:
            # Note: Can't await in sync method, use fire-and-forget or make async
            asyncio.create_task(adapter.stop())
        except Exception as e:
            logger.warning(f"Error stopping adapter during unregister: {e}")
    
    # Cancel supervisor task
    if source_id in self._supervisor_tasks:
        task = self._supervisor_tasks.pop(source_id)
        task.cancel()
        logger.debug(f"Cancelled supervisor task for: {source_id}")
    
    # Remove adapter and running state
    del self._adapters[source_id]
    self._running.pop(source_id, None)
    
    logger.info(f"Unregistered adapter: {source_id}")
    return True
```

**Note:** This requires making `unregister()` async or using `asyncio.create_task()` for fire-and-forget stop.

---

## P1 - High Priority

### 3. Fix Start/Stop Responses Missing Required `status` Field
**File:** `daemon/api.py`
**Location:** `start_schedule()` (lines 1495-1499) and `stop_schedule()` (lines 1539-1543)

**Problem:** `SourceActionResponse` model (`models.py:563-578`) has `status` as a **required** field, but both endpoints omit it. This causes a **Pydantic ValidationError** on every successful response—the API returns invalid JSON.

**Fix:**
```python
# In start_schedule():
adapter = manager.source_registry.get(schedule_id)
status = adapter.status if adapter else None
return SourceActionResponse(
    source_id=schedule_id,
    status=status,  # ADD THIS - required field!
    success=True,
    message=f"Scheduler {schedule_id} started successfully"
)

# In stop_schedule():
return SourceActionResponse(
    source_id=schedule_id,
    status=SourceStatus.STOPPED,  # ADD THIS - required field!
    success=True,
    message=f"Scheduler {schedule_id} stopped successfully"
)
```

---

### 7. Make Execution Callback Async-Safe
**File:** `daemon/sources/registry.py`
**Location:** `_create_adapter_from_config()` (lines 251-273)

**Problem:** `execution_callback` is sync but called in async context without thread pool.

**Fix:** Option A - Run in thread pool:
```python
def execution_callback(execution_id: str, schedule_id: str, status: str, 
                       error: str | None = None, triggered_at: datetime | None = None):
    """Sync callback - run in thread pool."""
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _sync_execution_callback, 
                         execution_id, schedule_id, status, error, triggered_at)
```

**Fix:** Option B - Make callback async throughout:
```python
async def execution_callback(...):
    await source_repo.record_execution_start(...)
```

---

### 8. Clean `_running` on Supervisor Exit
**File:** `daemon/sources/registry.py`
**Location:** `_run_adapter_safe()` method (cleanup section at lines 518-519)

**Problem:** When `_run_adapter_safe()` exits (either normally or via exception), stale entries remain in `_running` dict if adapter crashes. The `_running` dict is only cleaned in `stop_adapter()`, not when the adapter exits on its own.

**Fix:**
```python
# Current code at lines 518-519:
logger.info(f"Supervisor exiting for adapter: {source_id}")

# Should add _running cleanup:
self._running.pop(source_id, None)  # Add this line
self._supervisor_tasks.pop(source_id, None)
logger.info(f"Supervisor exiting for adapter: {source_id}")
```

---

## P3 - Low Priority

### 9. Inconsistent Error Response Format
**File:** `daemon/api.py`
**Location:** `create_source()` scheduler enabled check (lines 830-838)

**Problem:** Uses plain dict instead of `ErrorResponse.model_dump()`.

**Fix:**
```python
raise HTTPException(
    status_code=400,
    detail=ErrorResponse(
        code=ErrorCodes.SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED,
        message="Scheduler sources manage their own lifecycle and cannot be enabled on creation."
    ).model_dump()
)
```

---

### 10. Missing Cron/Interval Conflict Validation
**File:** `daemon/sources/adapters/scheduler.py`
**Location:** `_parse_schedule_config()` method (lines 137-189)

**Problem:** If both `schedule` and `interval_seconds` are provided, only `schedule` is used silently.

**Fix:**
```python
if self._schedule and self._interval_seconds is not None:
    logger.warning(
        f"Both cron and interval specified for {self.source_id}. "
        f"Using cron, ignoring interval_seconds={self._interval_seconds}"
    )
```

---

### 11. Missing Next-Run-Time in API Response
**File:** `daemon/api.py` or models
**Location:** `ScheduleInfo` response model

**Problem:** Frontend can't display next scheduled run without calculating it client-side.

**Fix:**
Add `next_run_at: datetime | None` field to `ScheduleInfo` response. Calculate in endpoint:
```python
# In get_schedule() endpoint:
schedule_info = ScheduleInfo(...)
if schedule.status == "running":
    schedule_info.next_run_at = scheduler_adapter.get_next_run_time()
```

---

## Implementation Order

| Order | Priority | Task | Files |
|-------|----------|------|-------|
| 1 | P0 | ScheduleExecution cleanup on delete | `repository.py` |
| 2 | P0 | Auto-disable one-time schedulers | `scheduler.py`, `registry.py` |
| 3 | P0 | Fix status field in start/stop responses (Pydantic validation!) | `api.py` |
| 4 | P1 | Fix scheduler creation auto-start | `api.py` |
| 5 | P1 | Unregister adapter on delete | `api.py` |
| 6 | P1 | Unregister stops adapter first | `registry.py` |
| 7 | P2 | Async-safe execution callback | `registry.py` |
| 8 | P2 | Clean _running on exit | `registry.py` |
| 9 | P3 | Consistent error format | `api.py` |
| 10 | P3 | Cron/interval validation | `scheduler.py` |
| 11 | P3 | Next-run-time in response | `api.py/models` |

---

## Verification Summary (2026-03-23)

This document was verified against the actual codebase. Results:

### Line Number Accuracy
| Location | Document | Actual | Status |
|----------|----------|--------|--------|
| `delete_source_config()` | 153-180 | 153-180 | ✅ Accurate |
| `_run_schedule()` | 364-367 | 364-367 | ✅ Accurate |
| `_parse_schedule_config()` | 137-189 | 137-189 | ✅ Accurate |
| Scheduler enabled check | 830-838 | 830-838 | ✅ Accurate |
| Auto-start logic | 866-870 | 865-870 | ⚠️ 1 line off |
| `delete_source()` endpoint | 1027-1039 | 1025-1039 | ⚠️ 2 lines off at start |
| `unregister()` method | 67-90 | 67-90 | ✅ Accurate |
| `_create_adapter_from_config()` | 251-273 | 251-273 | ✅ Accurate |
| `_run_adapter_safe()` cleanup | Around 488 | 518-519 | ❌ No finally block |

### Bug Verification Status
| Issue | Verified | Notes |
|-------|----------|-------|
| P0 #1 ScheduleExecution cleanup | ✅ | Bug confirmed |
| P0 #2 Auto-disable one-time | ✅ | Bug confirmed |
| P1 #3 Scheduler auto-start | ✅ | Bug confirmed |
| P1 #4 Unregister on delete | ✅ | Bug confirmed |
| P1 #5 Unregister stops adapter | ✅ | Bug confirmed |
| P2 #6 Status field missing | ✅ | **Critical!** Causes Pydantic ValidationError |
| P2 #7 Sync callback | ✅ | Bug confirmed |
| P2 #8 _running cleanup | ✅ | Bug exists, but no finally block |
| P3 #9 Error format | ✅ | Bug confirmed |
| P3 #10 Cron/interval | ✅ | Bug confirmed |

**Overall Accuracy: ~85%** - All bugs verified, some descriptions clarified.

---

## Testing Checklist

After implementing fixes, verify:

- [ ] Delete scheduler → ScheduleExecution records removed
- [ ] Create one-time scheduler → executes once → auto-disabled
- [ ] Create scheduler (enabled) → adapter starts immediately
- [ ] Delete running source → adapter stops and unregisters
- [ ] Unregister running adapter → adapter stops cleanly
- [ ] Start/stop scheduler → status returned correctly
- [ ] Many executions → no blocking on DB writes
- [ ] Restart daemon → scheduler state consistent with DB
