# Queue-Level Retry Bug

**Status**: BUG CONFIRMED

**Date**: 2026-04-08

**Severity**: High — Child instances fail permanently on transient LLM errors

---

## Summary

When a child instance (spawned by a leader) encounters repeated LLM errors (e.g., 502 Bad Gateway), the LLM-level retry works correctly, but the queue-level retry **never triggers**. This causes child instances to fail permanently instead of retrying.

---

## Observed Behavior

| Timeline | Event |
|----------|-------|
| 18:24:15 | LLM invoked with `max_retries=7` |
| 18:25:52 | First LLM response received |
| 18:27:43–18:52:53 | ~15x 502/503 errors from `llm.daoduc.org` |
| 18:52:53 | LLM retries exhausted: "All retries exhausted after 7 attempts" |
| 18:52:53 | "Error processing message ..." logged |
| 18:52:53 | **No queue-level retry logged** |
| 18:52:53 | Task ended — **message never re-queued** |

### Key Evidence from Log (`docs/investigation/retry-check.log`)

- **Message ID**: `9c4797a2-aec1-4181-9691-f762d331ae3f`
- **Child Instance**: `7e182995-bb1d-4a87-b14e-dd971ec4a30c` (spawned by leader)

**Expected logs that are MISSING:**
- ❌ `"Retry check: msg.message_id=..., retry_count=X, max_retries=Y"`
- ❌ `"Retry scheduled: ..."` 
- ❌ `"status_changed"` event with `"status": "retrying"`

The code path at `manager.py:1104-1117` was **never reached**.

---

## Two-Level Retry Architecture

### Level 1: LLM-Level Retry ✅ WORKING

- **Location**: `daemon/graph.py:376-381`
- **Config**: `config.yaml:46` — `llm_max_retries: 7`
- **Mechanism**: `RunnableRetry` with `wait_exponential_jitter=True`
- **Status**: Working correctly

### Level 2: Queue-Level Retry ❌ BROKEN

- **Location**: `daemon/manager.py:1104-1117`
- **Config**: `config.yaml:41` — `max_retries: 5`
- **Mechanism**: Calls `self._queue_repository.retry()` which sets `next_retry_at` with exponential backoff
- **Status**: Code never executed

---

## Code Flow Analysis

### Expected Flow

```
_process_queue() [manager.py:1006]
  └── _process_message_with_tracking() [manager.py:1264]
        └── async for event in graph.astream()
              └── Exception (TransientAPIError)
                    └── caught at manager.py:1100
                          └── if msg.retry_count < self.config.queue.max_retries:  ← NEVER REACHED
                                └── await asyncio.to_thread(self._queue_repository.retry, ...)
                                      └── broadcast status_changed with "retrying"
```

### Actual Flow

```
Exception raised at manager.py:1100
  └── logger.error(f"Error processing message ...")  ← This is the last log
  └── ??? (no more logs until task ended)
```

---

## Root Cause Analysis

The exception handler at `manager.py:1100-1143` is supposed to:
1. Log the error
2. Check `if msg.retry_count < self.config.queue.max_retries`
3. Call `self._queue_repository.retry()`
4. Broadcast retry event

**Step 2-4 never execute.** The task ends immediately after the error log.

### Possible Causes

1. **Exception during exception handling**: Something inside the try block throws before reaching retry logic
2. **asyncio.to_thread failure**: The `asyncio.to_thread(self._queue_repository.retry, ...)` call fails silently
3. **Missing await on to_thread**: Potential issue with how `asyncio.to_thread` is used in this context
4. **Race condition**: Task being cancelled between error log and retry check

---

## Configuration

```yaml
# config.yaml
queue:
  max_retries: 5        # Queue-level retries (NOT working)
  llm_max_retries: 7    # LLM-level retries (working)
```

---

## Reproduction Steps

1. Leader instance spawns a child instance
2. Child instance processes a message
3. LLM returns 502 errors repeatedly
4. LLM retries 7 times, all fail
5. Exception propagates to `_process_queue`
6. Error logged, task ends
7. **Bug**: Message not re-queued, child instance dead

---

## Impact

- Child instances fail permanently on any transient LLM error
- Leader cannot rely on child instances for retry-capable tasks
- Users must manually retry or restart failed child instances
- Breaks the expected resilience of the two-level retry system

---

## Next Steps

1. **Add debug logging** to pinpoint exact failure location
2. **Verify `asyncio.to_thread` behavior** in this context
3. **Check if task cancellation** occurs between error and retry
4. **Fix the bug** once root cause identified

---

## Related Files

| File | Line | Description |
|------|------|-------------|
| `daemon/manager.py` | 1100-1143 | Exception handler (broken) |
| `daemon/manager.py` | 1104 | Retry condition check (never reached) |
| `daemon/manager.py` | 1106 | `queue_repository.retry()` call (never called) |
| `daemon/manager.py` | 1264 | `_process_message_with_tracking()` where error originates |
| `daemon/graph.py` | 376-381 | LLM retry config (working) |
| `daemon/llm_error_classifier.py` | 99 | `TransientAPIError` raise (working) |
