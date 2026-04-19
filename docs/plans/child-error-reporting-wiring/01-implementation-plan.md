# Implementation Plan

## Phase 0: Rewrite `_send_error_report()` Completely

**Critical — must be done before any wiring.** The method is missing 4 operations that the success path (`_process_child_completion_and_notify_parent`) already does. Without these, the parent will still be stuck even if wired correctly.

### 0.1 — Atomic transaction with all state mutations

Wrap ALL state changes in a single `Session` transaction (match the success path pattern):

```python
async def _send_error_report(...):
    # Step 1: Idempotent reads OUTSIDE transaction
    meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
    if not meta or not meta.parent_id:
        return
    
    # Step 2: Atomic DB transaction
    with Session(self._engine) as session:
        # a) Set child instance status = ERROR
        # b) Fail associated message
        # c) Decrement parent.waiting_for (floor at 0)
        # d) DELETE FROM instance_hierarchy WHERE child_id = ?
        # e) CASCADE: if parent.waiting_for == 0 AND parent.status == WAITING_CHILDREN
        #    → Check pending messages, transition to RUNNING or COMPLETED
        session.commit()
    
    # Step 3: Enqueue error report (outside transaction)
    # Step 4: Broadcast SSE (with _live_hub null guard)
```

### 0.2 — Delete from `instance_hierarchy`

The success path removes the parent-child link when a child completes. The error path must do the same — otherwise hierarchy records leak forever, confusing future queries.

### 0.3 — Cascade parent status transition

After decrementing `waiting_for`, if it reaches 0 AND parent is `WAITING_CHILDREN`, transition parent to `RUNNING` (or `COMPLETED` if no pending messages). **Without this, the parent still gets stuck** even with the counter correct.

Reference: adapt cascade logic from `_update_parent_on_child_complete()` (manager.py ~1668).

### 0.4 — `_live_hub` null guard

Line 1958 calls `self._live_hub.stream_lifecycle()` unconditionally. During early startup or shutdown, `_live_hub` may not be available.

```python
if self._live_hub:
    await self._live_hub.stream_lifecycle(...)
```

### 0.5 — Fail the associated message

Set `message.status = FAILED` in the same transaction (currently the message stays in PROCESSING).

---

## Phase 1: Wire into `ProcessMessageProcessor.process()` (Primary Path)

**Rationale**: This is the natural async layer. It covers ALL processing-phase errors (Categories A, B, C1, C5). Use `self._manager._event_bus` directly.

### 1.1 — Use manager's event bus, fix null bug

**File**: `daemon/services/task_processor.py`, line ~216

```python
# Phase 4 fix: use manager's event bus, not self._event_bus (never initialized)
if self._manager._event_bus:
    await self._manager._event_bus.create_error_event(...)
elif self._event_repo:
    ...
```

### 1.2 — Add `_send_error_report()` call

```python
except Exception as e:
    error_msg = _truncate_error(str(e))
    logger.error(...)

    # Create error event
    if self._manager._event_bus:
        await self._manager._event_bus.create_error_event(...)
    elif self._event_repo:
        await asyncio.to_thread(self._event_repo.create_event, ...)

    # Publish instance lifecycle event
    if hasattr(self._manager, '_publish_instance_lifecycle_event'):
        ...

    # Send error report to parent (Phase 1 wiring)
    if hasattr(self._manager, '_send_error_report'):
        try:
            await self._manager._send_error_report(
                instance_id=task.instance_id,
                error=error_msg,
                error_type=_classify_error_type(e),
                message_id=task.message_id,
            )
        except Exception as report_err:
            logger.warning(f"Failed to send error report to parent: {report_err}")

    raise
```

### 1.3 — Add error type classifier helper

```python
def _classify_error_type(e: Exception) -> str:
    if isinstance(e, openai.APIStatusError):
        if e.status_code == 413:   return "payload_too_large"
        if e.status_code in (401, 403): return "authentication_error"
        if e.status_code == 404:  return "endpoint_not_found"
        return f"api_error_{e.status_code}"
    if isinstance(e, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return "timeout_exhausted"
    if isinstance(e, ContextLengthExceededError):
        return "context_length_exceeded"
    if isinstance(e, (openai.APIConnectionError, ConnectionResetError, BrokenPipeError)):
        return "connection_error_exhausted"
    if isinstance(e, openai.BadRequestError):
        return "bad_request"
    return "execution_error"
```

### 1.4 — Phase 2 dedup note (CRITICAL)

**TaskProcessor reports. Worker does NOT report for the same failure.**

The exception from `process()` propagates to `Worker._handle_task_failure()`, which would also try to call `_send_error_report()`. To avoid double-reporting:

- Check if task is already FAILED before reporting in Worker (Phase 2)
- The existing queue-check dedup in `_send_error_report()` is the safety net
- **DO NOT wire `_send_error_report()` in `Worker._handle_task_failure()` for the normal flow** — only for pre-processing errors (ValueError, RuntimeError) that bypass TaskProcessor entirely

---

## Files Modified Summary

| File | Changes |
|------|---------|
| `daemon/manager.py` | **Rewrite** `_send_error_report()`: atomic DB transaction, instance hierarchy delete, parent cascade, `_live_hub` null guard, message fail |
| `daemon/services/task_processor.py` | Add `_send_error_report()` call in exception handler; add `_classify_error_type()` helper; use `self._manager._event_bus` (fix Phase 4) |
| `daemon/services/worker_pool.py` | Add `_send_error_report()` calls in `_handle_cancellation()` (timeout+retries, non-timeout) via `MainLoopBridge`; **do NOT** call in `_handle_task_failure()` for normal flow (dedup) |
| `daemon/services/stale_task_recovery.py` | Add `on_task_permanently_failed` callback; invoke after each `fail_task()` |
| `daemon/manager.py` (init) | Wire callback to `StaleTaskRecovery` constructor with explicit `MainLoopBridge` bridge |

## Open Questions (Council Recommendations)

1. **Parent cascade on error**: When last child fails (`waiting_for` reaches 0), should parent transition to `RUNNING` (to process remaining work) or `ERROR` (signal propagation)? Success path uses `RUNNING`. Align with success path for now.

2. **Error report message format**: The report is a plain text message enqueued to the parent's queue. The parent LLM sees it as a regular message. This is intentional — the parent's agent soul/rule guides how it responds to errors. No special message type needed.

---

## Verification Plan

See `02-verification.md`.
