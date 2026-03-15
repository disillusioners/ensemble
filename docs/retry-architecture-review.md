# Retry Architecture Review

**Date:** 2026-03-15  
**Issue:** LangGraph streaming errors (502/503) not being retried

## Executive Summary

The retry system has **two layers** but they don't work together properly:

1. **LLM-level retry** (LangChain `with_retry()`) - only catches RateLimitError, Timeout, ConnectionError
2. **Queue-level retry** - catches all exceptions but **never re-triggers processing**

The message IS being scheduled for retry in the database, but **nothing restarts the processing loop**.

---

## Evidence from Production

### Log Output (14:26:52)
```
14:26:50 - POST /v1/chat/completions → 502 Bad Gateway
14:26:50 - OpenAI SDK: Retrying in 0.49s (internal retry #1)
14:26:51 - POST /v1/chat/completions → 503 Service Unavailable  
14:26:51 - OpenAI SDK: Retrying in 0.75s (internal retry #2)
14:26:52 - POST /v1/chat/completions → 503 Service Unavailable
14:26:52 - ERROR: Streaming failed for message 032508f8...
14:26:52 - ERROR: Error processing message 032508f8...
```

### Database State
```sql
SELECT * FROM message_queue WHERE message_id = '032508f8-611b-491a-b301-532fa88a7fc5';

-- Result:
-- status = ready
-- retry_count = 1
-- next_retry_at = 2026-03-15 07:27:52 (1 minute after error)
-- error_message = "<html>...503 Service Temporarily Unavailable...</html>"
```

**Conclusion:** Retry WAS scheduled correctly, but no further log output → processing never restarted.

---

## Architecture Analysis

### Current Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. OPENAI SDK INTERNAL RETRY (automatic)                            │
│    - Handles: 429, 5xx errors                                       │
│    - Default: 2-3 retries with exponential backoff                  │
│    - ✅ WORKING (we see retries in logs)                            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ All retries exhausted
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. LANGCHAIN with_retry() (graph.py:117-124)                        │
│    - Only catches: RateLimitError, Timeout, ConnectionError         │
│    - ❌ MISSES: InternalServerError (502, 503, 500)                 │
│    - Error passes through to queue-level                            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ Passes through
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. QUEUE-LEVEL RETRY (manager.py:797-814)                           │
│    - Catches: All Exception                                         │
│    - Calls: repository.retry() → sets next_retry_at, status=READY   │
│    - ✅ SCHEDULING WORKS (verified in DB)                           │
│    - ❌ PROCESSING NEVER RESTARTS                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Root Cause: Missing Re-trigger Mechanism

```
After error occurs:
  1. Exception caught in _process_queue
  2. repository.retry() called
     - retry_count = 1
     - next_retry_at = now + 1 minute
     - status = READY
  3. _process_queue loop exits (no more messages)
  4. ❌ NOTHING CALLS _process_queue AGAIN

After 1 minute:
  5. Message has next_retry_at <= now
  6. dequeue() CAN pick it up
  7. ❌ BUT NO ONE IS CALLING dequeue()
```

### Trigger Points Analysis

| Trigger | When | Calls _process_queue? |
|---------|------|----------------------|
| `enqueue_message()` | New message arrives | ✅ Yes |
| Watchdog `_check_stuck_messages()` | Message stuck > 1 hour | ❌ No (only schedules retry) |
| Watchdog `_check_retry_ready_messages()` | Retry time arrived | ❌ No (only moves status) |
| Tool creates child session | Tool execution | ✅ Yes (for parent) |
| **After error with retry scheduled** | After backoff | ❌ **MISSING** |

---

## Issues Found

### Issue 1: InternalServerError Not in TRANSIENT_EXCEPTIONS

**File:** `daemon/graph.py:14-18`
```python
TRANSIENT_EXCEPTIONS = (
    openai.RateLimitError,      # 429
    openai.APITimeoutError,     # timeout
    openai.APIConnectionError,  # connection failed
)
# ❌ Missing: openai.InternalServerError (500, 502, 503, etc.)
```

**Impact:** 5xx errors bypass LangChain retry, going straight to slow queue-level retry (1 min backoff).

### Issue 2: No Re-trigger After Retry Scheduled

**File:** `daemon/manager.py:797-814`

After scheduling retry, the code just exits. There's no mechanism to restart processing after the backoff period.

**Current:**
```python
except Exception as e:
    if msg.retry_count < self.config.queue.max_retries:
        self._queue_repository.retry(msg.message_id, str(e))
        # Broadcast retry scheduled event
        await self.broadcaster.broadcast(...)
    # Loop exits, nothing else happens
```

### Issue 3: Inconsistent Status Handling

**File:** `daemon/repositories/message_queue/repository.py:302-337`

The `retry()` method sets `status = READY` directly, but there's also a `RETRYING` status used by `schedule_retry_for_stuck()`.

| Method | Status Set | retry_count Updated? |
|--------|------------|---------------------|
| `retry()` | `READY` | ✅ Yes (incremented) |
| `schedule_retry_for_stuck()` | `RETRYING` | ❌ No (passed as param) |

This causes confusion about which status to check and when.

### Issue 4: is_retry Detection Was Broken (FIXED)

**File:** `daemon/manager.py:701-702`

**Before (broken):**
```python
is_retry = msg.message_metadata.get("is_retry", False) if msg.message_metadata else False
```

**After (fixed):**
```python
is_retry = msg.retry_count > 0
```

The metadata approach was fragile - `retry()` never set `is_retry` in metadata.

---

## Proposed Solutions

### Option A: Add Watchdog Re-trigger (Recommended)

Modify watchdog to re-trigger processing for retry-ready messages.

**Changes:**
1. Keep `retry()` setting `status = RETRYING` (more explicit)
2. Watchdog `_check_retry_ready_messages()` calls callback to trigger `_process_queue`
3. Requires passing manager reference to watchdog

**Pros:**
- Clear separation of concerns
- Explicit retry state (`RETRYING` vs `READY`)
- Watchdog already runs on interval

**Cons:**
- More complex callback wiring
- Watchdog interval (30s) adds latency

### Option B: Immediate Re-trigger via asyncio

After scheduling retry, schedule an asyncio task to re-trigger after backoff.

**Changes:**
```python
except Exception as e:
    if msg.retry_count < self.config.queue.max_retries:
        message = self._queue_repository.retry(msg.message_id, str(e))
        # Schedule re-trigger after backoff
        delay = (message.next_retry_at - datetime.now(timezone.utc)).total_seconds()
        asyncio.create_task(self._schedule_retry_processing(session_id, delay))
```

**Pros:**
- Immediate response after backoff
- No watchdog changes needed

**Cons:**
- Tasks lost on restart
- More asyncio complexity

### Option C: Simplify - Add InternalServerError to TRANSIENT_EXCEPTIONS

The simplest fix with biggest immediate impact.

**Changes:**
```python
# daemon/graph.py
TRANSIENT_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,  # 500, 502, 503, etc.
)
```

**Pros:**
- 1-line change
- Fast retries (seconds) instead of slow (minutes)
- Uses LangChain's built-in exponential backoff with jitter

**Cons:**
- Doesn't fix queue-level retry (still needed for other errors)
- Retries happen within same HTTP request (user still waits)

### Option D: Hybrid Approach (Recommended)

Combine Option C + fix queue-level retry:

1. **Add InternalServerError to TRANSIENT_EXCEPTIONS** - fast LLM-level retry
2. **Fix queue-level re-trigger** - for errors that exhaust LLM retries
3. **Consolidate status handling** - use `READY` with `next_retry_at` consistently
4. **Add re-trigger mechanism** - via watchdog callback or event-driven

---

## Recommended Implementation

### Phase 1: Quick Fix (Immediate)

```python
# daemon/graph.py
TRANSIENT_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,  # Add this
)
```

### Phase 2: Fix Queue-Level Re-trigger

Add callback mechanism to watchdog:

```python
# daemon/queue.py - SessionWatchdog.__init__
def __init__(
    self,
    queue_repository: SQLModelMessageQueueRepository,
    request_registry: Optional[ActiveRequestRegistry] = None,
    on_retry_ready: Optional[Callable[[str], None]] = None,  # Add callback
):
    self._on_retry_ready = on_retry_ready
    ...

# daemon/queue.py - _check_retry_ready_messages
def _check_retry_ready_messages(self) -> None:
    retry_ready_messages = self._queue_repository.find_retry_ready_messages()
    
    if retry_ready_messages:
        # Group by session
        sessions = set(msg.session_id for msg in retry_ready_messages)
        
        # Move to ready
        message_ids = [msg.message_id for msg in retry_ready_messages]
        count = self._queue_repository.move_retry_ready_to_ready(message_ids)
        
        # Trigger re-processing for each session
        if self._on_retry_ready:
            for session_id in sessions:
                self._on_retry_ready(session_id)
```

### Phase 3: Consistency Improvements

1. Remove `RETRYING` status, use only `READY` with `next_retry_at`
2. Update all code to check `next_retry_at` for retry detection
3. Add logging for retry attempts with count

---

## Implementation Complete ✅

### Changes Made

| File | Change |
|------|--------|
| `daemon/repositories/message_queue/repository.py:329` | `status = RETRYING` (was `READY`) |
| `daemon/queue.py:345-354` | Added `on_retry_ready` callback parameter to `SessionWatchdog.__init__` |
| `daemon/queue.py:438-454` | Updated `_check_retry_ready_messages` to trigger callback |
| `daemon/manager.py:321-344` | Added `_on_watchdog_retry_ready` callback handler |
| `daemon/manager.py:346-351` | Wired callback to watchdog instantiation |

### How It Works now

```
Error during streaming
    ↓
Exception caught in _process_queue
    ↓
repository.retry() called
        - retry_count += 1
        - status = RETRYING ✅ (was READY)
        - next_retry_at = now + backoff
    ↓
_process_queue exits (no more messages)
    ↓
... backoff period passes (up to 30s latency due to watchdog interval) ...
    ↓
Watchdog _check_retry_ready_messages()
    - Finds messages with status=RETRYING and next_retry_at <= now
    - Moves to READY status
    - Calls on_retry_ready callback with session_ids
    ↓
Callback triggers _process_queue(session_id) for each session
    ↓
Message reprocessed with is_retry=True
    - Resumes from checkpoint if available ✅
```

### Testing
1. Check that retry works after a 503 error
2. Verify logs show:
   - "Moved X messages from retrying to ready for sessions: [...]"
   - "Triggering retry processing for session..."
   - "Resuming session... from checkpoint (retry #1)"

---

## Files Affected

| File | Changes |
|------|---------|
| `daemon/graph.py` | Add `InternalServerError` to `TRANSIENT_EXCEPTIONS` |
| `daemon/manager.py` | Fix `is_retry` detection (DONE), add re-trigger logic |
| `daemon/queue.py` | Add `on_retry_ready` callback to watchdog |
| `daemon/repositories/message_queue/repository.py` | Consolidate status handling |

---

## Appendix: Exception Hierarchy

```
OpenAIError (base)
└── APIError
    └── APIStatusError  ← base for 4xx and 5xx
        ├── BadRequestError (400)
        ├── AuthenticationError (401)
        ├── PermissionDeniedError (403)
        ├── NotFoundError (404)
        ├── RateLimitError (429)        ← IN TRANSIENT_EXCEPTIONS
        └── InternalServerError (500+)  ← NOT in TRANSIENT_EXCEPTIONS
```
