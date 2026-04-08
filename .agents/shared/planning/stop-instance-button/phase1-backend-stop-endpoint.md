# Phase 1: Backend — Stop Endpoint

## Objective
Add a `POST /instances/{instance_id}/stop` API endpoint that gracefully cancels the current in-flight request(s) for an instance without destroying it, allowing the instance to return to idle and accept new messages.

## Coupling
- **Depends on**: None
- **Coupling type**: N/A (root phase)
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: `POST /api/instances/{instance_id}/stop` → `{ stopped: bool, cancelled_requests: int }`
- **Why this coupling**: This is the foundation phase. Phase 2 consumes this API.

## Context
The backend already has a full cancellation infrastructure:
- `ActiveRequestRegistry.cancel_by_instance(instance_id)` cancels all active requests, but **hardcodes `CancellationReason.INSTANCE_TERMINATED`** on line 128 (the method has no `reason` parameter to forward)
- `CancellationToken` + `CancellationCallbackHandler` propagates cancellation to LLM/tool boundaries
- On cancellation, `_process_queue()` catches `OperationCancelledError` and broadcasts `cancelled` event via SSE
- The existing `DELETE /instances/{id}` is too aggressive — it destroys the instance entirely

**Key fixes needed**:
1. `cancel_by_instance()` must accept and forward a `reason` parameter (not hardcode)
2. A new **public** `InstanceManager.cancel_instance_requests()` method must wrap the registry call (no direct access to `_request_registry`)
3. `CancellationReason.USER_STOPPED` must be added

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `reason` parameter to `cancel_by_instance()` | Modify `ActiveRequestRegistry.cancel_by_instance()` to accept an optional `reason` parameter (defaulting to `CancellationReason.MANUAL` for backward compat). Forward it to the inner `self.cancel()` call instead of the hardcoded `INSTANCE_TERMINATED`. Return `int` (count of cancelled requests). | `daemon/request_registry.py` |
| 2 | Add public `cancel_instance_requests()` to manager | New **public** method on `InstanceManager` that calls `self._request_registry.cancel_by_instance(instance_id, reason)` and returns the count. This avoids exposing `_request_registry` directly to the API layer. | `daemon/manager.py` |
| 3 | Add `USER_STOPPED` cancellation reason | Add `USER_STOPPED = "user_stopped"` to `CancellationReason` enum so the `cancelled` SSE event carries the correct reason. | `daemon/cancellation.py` |
| 4 | Add `POST /instances/{instance_id}/stop` endpoint | Validate instance exists, call `manager.cancel_instance_requests(instance_id, USER_STOPPED)`, return result. Follow same pattern as `terminate_instance()` but lighter. | `daemon/api.py` (after line 663) |

## Key Files
- `daemon/request_registry.py` — Fix `cancel_by_instance()` to accept and forward `reason`
- `daemon/manager.py` — Add `cancel_instance_requests()` public method
- `daemon/cancellation.py` — Add `USER_STOPPED` reason
- `daemon/api.py` — Add the new endpoint

## Implementation Details

### Task 1: Fix `cancel_by_instance()` — CRITICAL

**Current code** (`request_registry.py:118-131`):
```python
def cancel_by_instance(self, instance_id: str) -> None:
    """Cancel all active requests for an instance."""
    with self._lock:
        message_ids = self._by_instance.get(instance_id, set()).copy()
    
    for message_id in message_ids:
        self.cancel(message_id, CancellationReason.INSTANCE_TERMINATED)  # ← HARDCODED
    
    if message_ids:
        logger.info(f"Cancelled {len(message_ids)} request(s) for instance {instance_id[:8]}...")
```

**Fixed code**:
```python
def cancel_by_instance(
    self,
    instance_id: str,
    reason: CancellationReason = CancellationReason.MANUAL
) -> int:
    """Cancel all active requests for an instance.
    
    Args:
        instance_id: The instance whose requests should be cancelled.
        reason: The cancellation reason. Defaults to MANUAL.
    
    Returns:
        Number of requests that were cancelled.
    """
    with self._lock:
        message_ids = self._by_instance.get(instance_id, set()).copy()
    
    count = 0
    for message_id in message_ids:
        if self.cancel(message_id, reason):  # ← Now forwarded
            count += 1
    
    if count > 0:
        logger.info(f"Cancelled {count} request(s) for instance {instance_id[:8]}...")
    
    return count
```

> **Note**: `self.cancel()` already returns `bool` (True if cancellation was signalled), so we use the return value to count only actually-cancelled requests.

### Task 2: Add `cancel_instance_requests()` to `InstanceManager`

In `manager.py`, add a new public method (near `terminate_instance` at ~line 2075):

```python
def cancel_instance_requests(
    self,
    instance_id: str,
    reason: CancellationReason = CancellationReason.MANUAL
) -> int:
    """Cancel all active requests for an instance without terminating it.
    
    This differs from terminate_instance() which fully destroys the instance.
    Here the instance remains alive and can receive new messages.
    
    Args:
        instance_id: The instance whose requests should be cancelled.
        reason: The cancellation reason.
    
    Returns:
        Number of requests that were cancelled.
    """
    return self._request_registry.cancel_by_instance(instance_id, reason)
```

> **Note**: The method also accepts `instance_id` values with no active requests — in that case it returns `0`, which is the correct and safe behavior.

### Task 3: Add `USER_STOPPED` reason

In `cancellation.py`, add to `CancellationReason` enum:

```python
class CancellationReason(Enum):
    TIMEOUT = "timeout"
    WATCHDOG_RETRY = "watchdog_retry"
    MANUAL = "manual"
    SHUTDOWN = "shutdown"
    SESSION_TERMINATED = "session_terminated"
    USER_STOPPED = "user_stopped"  # NEW: user pressed stop button in UI
```

### Task 4: `POST /instances/{instance_id}/stop` endpoint

In `api.py`, add after the existing `terminate_instance` endpoint (~line 663):

```python
@api_router.post("/instances/{instance_id}/stop")
async def stop_instance(instance_id: str):
    """Stop current processing for an instance without terminating it.
    
    Cancels all active requests for the instance. The instance remains alive
    and can receive new messages. Returns the count of cancelled requests.
    """
    # Validate instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )
    
    from .cancellation import CancellationReason  # Import here to avoid circular issues
    cancelled_count = manager.cancel_instance_requests(
        instance_id,
        reason=CancellationReason.USER_STOPPED
    )
    
    return {"stopped": True, "cancelled_requests": cancelled_count}
```

> **Note on returns when idle**: If there are no active requests for the instance, `cancel_instance_requests()` returns `0`. The response is still `{ stopped: true, cancelled_requests: 0 }` — this is safe and expected.

## Constraints
- Must NOT destroy the instance — only cancel in-flight work
- Must NOT cascade to child instances
- Must NOT update DB status to `terminated`
- Must NOT expose `_request_registry` directly to the API layer (use the public wrapper)
- Must broadcast `cancelled` SSE event with `reason: "user_stopped"` (already handled by `_process_queue()` in manager.py)
- Must be safe to call on an already-idle instance (return `cancelled_requests: 0`)

## Deliverables
- [ ] `POST /instances/{id}/stop` endpoint returns 200 with `{ stopped: true, cancelled_requests: N }`
- [ ] Returns 404 for non-existent instances
- [ ] Cancelled requests trigger `cancelled` SSE event with `reason: "user_stopped"`
- [ ] Instance remains alive and can process new messages after stop
- [ ] Calling on idle instance returns `{ stopped: true, cancelled_requests: 0 }`
- [ ] No regression: `DELETE /instances/{id}` still works as before (existing `cancel_by_instance()` calls from `terminate_instance()` continue to work via the default `reason` parameter)
