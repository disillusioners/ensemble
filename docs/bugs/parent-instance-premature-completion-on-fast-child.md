# Bug: Parent Instance Prematurely Marked COMPLETED When Child Completes Quickly

> **✅ Resolved (2026-06).** Resolved by the `CorrelationManager` migration (Phase 2: JobFeedbackObserver migrated to CM callback, eliminating the waiting_for snapshot race). For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md).

**Date:** 2026-06-01
**Severity:** High
**Status:** Confirmed
**Affected Component:** `daemon/services/child_reports.py`

---

## Summary

When a child instance completes very quickly (before the parent's LLM finishes its turn), the parent instance is incorrectly marked as `COMPLETED` even when there are pending messages in the queue. This causes the parent's last assistant message to be lost from checkpoint data.

---

## Symptom (Log Evidence)

```
22:54:11 - daemon.services.child_reports - INFO - Instance cd7a9cd8... parent_id=None, waiting_for=0, status=waiting_children
22:54:11 - daemon.services.child_reports - WARNING - Instance cd7a9cd8 has pending_count=1 but waiting_for=0 — proceeding to COMPLETED (not waiting_children)
22:54:11 - daemon.services.child_reports - INFO - Instance cd7a9cd8... no parent, skipping notification
22:54:11 - daemon.services.child_reports - INFO - Instance cd7a9cd8... completed (no parent, no children), status=COMPLETED
22:54:12 - daemon.graph - INFO - [LLM] Response: Developer said hello back! 👋
```

The warning `"Instance cd7a9cd8 has pending_count=1 but waiting_for=0 — proceeding to COMPLETED"` indicates that despite having a pending message, the instance was marked COMPLETED.

---

## Root Cause

### Race Condition Flow

1. **Parent LLM generates response** and calls tools (`spawn_instance`, `send_message`)
2. **LLM output is partially generated** (e.g., "Waiting for the developer...")
3. **Child completes very quickly** and sends completion report to parent
4. **`_process_child_completion_and_notify_parent`** is called
5. **Parent is marked COMPLETED** even though:
   - Parent's LLM is still running
   - There's a pending completion report message in the queue
6. **Parent's complete response** ("Waiting for the developer...") is lost from checkpoint

### Code Position

**File:** `daemon/services/child_reports.py`
**Lines:** 651-693

### Current Code (Buggy)

```python
elif pending_count > 0 and instance.waiting_for == 0:
    logger.warning(
        "Instance %s has pending_count=%d but waiting_for=0 — "
        "proceeding to COMPLETED (not waiting_children)",
        instance_id[:8], pending_count
    )

# No children, no pending messages - safe to complete
logger.info(f"Instance {instance_id[:8]}... no parent, skipping notification")

# No children, no pending messages - safe to complete
logger.info(f"Instance {instance_id[:8]}... completed (no parent, no children), status=COMPLETED")

# Update instance status to COMPLETED in DB
instance.status = InstanceStatus.COMPLETED.value
instance.updated_at = datetime.now(timezone.utc).isoformat()
instance.last_activity_at = datetime.now(timezone.utc)
instance.version = (instance.version or 1) + 1

session.commit()
```

**Problem:** The `elif` block at line 651 does NOT return or transition to `WAITING_CHILDREN`. It falls through to mark the instance as `COMPLETED` even though `pending_count > 0`.

### Comparison with Correct Handling

Lines 635-650 correctly handle a similar case:

```python
if instance.waiting_for > 0 and pending_count > 0:
    # Has explicit children to wait for
    instance.status = InstanceStatus.WAITING_CHILDREN.value
    session.commit()
    logger.info(
        f"Instance {instance_id[:8]}... waiting_for={instance.waiting_for}, pending={pending_count}, "
        f"status=WAITING_CHILDREN"
    )
    logger.info(f"Instance {instance_id[:8]}... has pending messages, deferring notification")
    # Emit status_change SSE event
    if self._manager._live_hub:
        try:
            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
        except Exception as e:
            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
    return  # <-- Correctly returns instead of falling through
```

---

## Expected Behavior

When `pending_count > 0`, the instance should NOT be marked COMPLETED. Instead, it should:
1. Transition to `WAITING_CHILDREN` status (if not already), OR
2. Return early to wait for pending messages to be processed

---

## Proposed Fix

### Option 1: Add Early Return (Minimal Change)

```python
elif pending_count > 0 and instance.waiting_for == 0:
    logger.warning(
        "Instance %s has pending_count=%d but waiting_for=0 — "
        "deferring completion to wait for pending messages",
        instance_id[:8], pending_count
    )
    instance.status = InstanceStatus.WAITING_CHILDREN.value
    session.commit()
    if self._manager._live_hub:
        try:
            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
        except Exception as e:
            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
    return  # FIX: Add return to wait for pending messages
```

### Option 2: Use Same Pattern as Lines 635-650 (More Consistent)

Replace lines 651-656 with the same pattern used at lines 635-650:

```python
elif pending_count > 0 and instance.waiting_for == 0:
    logger.warning(
        "Instance %s has pending_count=%d but waiting_for=0 — "
        "deferring completion to wait for pending messages",
        instance_id[:8], pending_count
    )
    instance.status = InstanceStatus.WAITING_CHILDREN.value
    session.commit()
    logger.info(
        f"Instance {instance_id[:8]}... waiting_for={instance.waiting_for}, pending={pending_count}, "
        f"status=WAITING_CHILDREN"
    )
    logger.info(f"Instance {instance_id[:8]}... has pending messages, deferring notification")
    if self._manager._live_hub:
        try:
            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
        except Exception as e:
            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
    return
```

---

## Impact

- **Parent's final response is lost** from checkpoint data
- **result_summary in job feedback** may be incorrect or empty
- **State inconsistency** between instance status and actual queue state
- **Potential duplicate message processing** if pending message is later processed

---

## Related Files

| File | Role |
|------|------|
| `daemon/services/child_reports.py` | Main bug location (line 651-693) |
| `daemon/services/task_processor.py` | Calls `_process_child_completion_and_notify_parent` after message processing |
| `daemon/services/instance_messaging.py` | Runs LangGraph and saves checkpoints |
| `daemon/services/job_feedback_observer.py` | Observes lifecycle events and fetches result_summary from checkpoint |
| `daemon/persistence.py` | `get_instance_messages()` retrieves checkpoint data |

---

## Test Case

The existing test `test_leader_spawns_developer_and_receives_report` in `tests/integration/test_completion_report.py` should be enhanced to verify:
1. Parent's complete response is captured in checkpoint
2. `result_summary` in job feedback is correct
3. Instance is not marked COMPLETED while there are pending messages
