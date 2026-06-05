# Terminate/Pause Latency — Mock Path Correctness

**Date:** 2026-06-05
**PR:** `feature/terminate-pause-latency` (commit `6aa5023`)
**Related plan:** `docs/plans/terminate-pause-latency.md` §4.3

## The trap

The plan explicitly warns (§4.3) that mocking `notify_all()` on the dispatch bus requires the **correct attribute path**. There are two paths that look correct but only one works:

| Path | Works? | Why |
|------|--------|-----|
| `manager._dispatch_bus` | ❌ NO | `InstanceManager` has no `_dispatch_bus` attribute |
| `manager._job_queue_mgmt_service._dispatch_bus` | ✅ YES | Set at `daemon/api.py:210` |

A naive test using `hasattr(self._manager, '_dispatch_bus')` would always return `False`, so the wakeup call would silently never fire. The test would pass (no crash) while production code silently fails to wake the JobProcessor — the exact latency bug RC2 this fix is meant to close.

## The correct mock pattern

```python
# Test fixture builds the manager mock correctly:
manager._job_queue_mgmt_service = MagicMock()
manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
manager._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()

# Production code uses defensive getattr chain:
mgmt = getattr(self._manager, '_job_queue_mgmt_service', None)
bus = getattr(mgmt, '_dispatch_bus', None) if mgmt is not None else None
if bus is not None:
    bus.notify_all()
```

## Verification rules for any future dispatch-bus test

1. **Assert the call, not just attribute presence** — use `bus.notify_all.assert_called_once()`, not `hasattr(manager, '_dispatch_bus')`.
2. **Use `MagicMock` not `AsyncMock`** — `notify_all()` is synchronous in production.
3. **Test both the present and missing-bus paths** — the missing-bus test must NOT raise.
4. **Match production's `getattr` chain** — don't bypass the defensive checks by setting the attribute directly on the wrong level.

## Why this matters

The `notify_all()` call is a latency optimization (closes RC2). It shortens the JobProcessor's poll-wait from 30s to sub-millisecond when an instance is terminated. If the test silently passes with the wrong mock path, the latency regression returns invisibly — there are no error logs, just a 30s user-visible delay on DELETE.

## See also
- Plan: `docs/plans/terminate-pause-latency.md` §4.3
- Production: `daemon/services/instance_lifecycle.py:605-609`
- Test: `tests/services/test_instance_lifecycle_terminate.py` tests 1, 2, 3, 6, 7
