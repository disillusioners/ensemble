# Performance Bug: `external_opencode_wait_for_result` ignores fast-path result, blocks 30s

**Date:** 2026-06-12
**Status:** Identified, not yet fixed
**Severity:** Medium — adds 0–30s latency (mean ~15s) to every wait call
**Affected:** `daemon/tools/external_opencode.py:509-546`, `daemon/opencode/session_manager.py`

## Summary

The OpenCode session manager's sync API has a **fast path** that flips state to `IDLE` immediately when the OpenCode server returns a response. However, the `external_opencode_wait_for_result` tool polls with a blind `asyncio.sleep(30)`, so it does NOT observe the fast-path result until the next 30s tick — adding up to 30 seconds of unnecessary latency per wait call.

## The Original Design Goal

From the design intent (mirrored from the Go binary's `manager.go`):

> We want to receive the result as soon as the sync `send_prompt` API returns. Polling is only a backup because the network can't be fully trusted.

This goal is **partially preserved** in the Python code: the sync result IS used to update `_latest_response` and flip state to `IDLE` immediately. But the wait loop in `external_opencode.py` is blind to this state change and must wait for the next 30s tick to see it.

## Root Cause

### Code path for the fast path (still works)

```
_run_worker()  [session_manager.py:893-1027]
    ↓
await self._client.send_prompt(...)  ← blocks until OpenCode returns (e.g. T=45s)
    ↓
result = ...
    ↓
self._worker_done_queue.put_nowait(_WorkerResult(result, error))
    ↓
_handle_worker_done()  [line 1029-1099]
    ↓
self._latest_response = {"result": strip_message_bloat(res.result)}  [line 1076]
self._state = SessionState.IDLE  [line 1082]
    ↓
_on_state_change callback → persists to DB
```

✅ This path is intact: at T=45s, state is `IDLE` and `_latest_response` holds the result.

### Code path for the wait (broken)

```python
# daemon/tools/external_opencode.py:509-546
async def external_opencode_wait_for_result(
    project: str,
    session_name: str,
    timeout: int = 600,
) -> str:
    """Block until an opencode session completes."""
    registry = _get_registry()
    record = await registry.get_session_record(project, session_name)
    if record is None:
        return f"[ERROR] Session '{session_name}' not found in project '{project}'"
    session_id = record.get("id", "")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_resp = None
    while loop.time() < deadline:
        req = OpenCodeRequest(action="GET_STATUS", session_id=session_id)
        resp = await _send(req)
        if resp.status == "ok":
            last_resp = resp
            data = resp.data or {}
            state = data.get("state", "UNKNOWN")
            if state == "IDLE":
                return f"[COMPLETED] Session completed.\n{_format_response(resp)}"
            if state == "WAITING_FOR_INPUT":
                questions = data.get("questions", [])
                return (
                    "[WAITING_FOR_INPUT] Session needs input. "
                    ...
                )
        await asyncio.sleep(POLL_INTERVAL_S)  # ← BLIND 30s SLEEP
    return _format_timeout(last_resp, timeout)
```

❌ The blind `asyncio.sleep(POLL_INTERVAL_S)` at line 544 means even if state flips to `IDLE` immediately after a `GET_STATUS` call, the loop won't notice until the next 30s tick.

## Timeline Example (T=0: wait starts, T=45: sync returns)

```
T=0     wait_for_result() called
T=0+ε   GET_STATUS → state=BUSY (worker still running)
T=0+ε   asyncio.sleep(30)  ← sleeping
T=30    wake, GET_STATUS → state=BUSY
T=30    asyncio.sleep(30)  ← sleeping
T=45    ← send_prompt returns HERE
        _run_worker completes
        _handle_worker_done runs
        _state: BUSY → IDLE  ← state flipped
        _latest_response updated
        BUT: no one is listening!
T=60    wait loop wakes, GET_STATUS → state=IDLE ✓
        returns "[COMPLETED]"
```

**Latency: T=45 (sync return) → T=60 (wait return) = 15s wasted.**

## Latency Distribution

The delay between the sync API returning and the wait loop observing the state change is uniformly distributed from 0 to `POLL_INTERVAL_S` (30s):

| Sync return time relative to last sleep | Additional wait time |
|------------------------------------------|----------------------|
| Right after `GET_STATUS` returned (just slept) | Up to 30s |
| Right before `GET_STATUS` is about to fire | ~0s |

**Mean added latency: ~15s per wait call.**

For a typical agent run with multiple `wait_for_result` calls, this can add up to minutes of wall-clock time per session.

## Why `sync_state_with_open_code` Doesn't Help

`GET_STATUS` calls `manager.sync_state_with_open_code()` (server.py:275), which:
1. Fetches the latest message via `GET /session/{id}/message?limit=1`
2. Derives state from the message's `step-finish.reason` field
3. Also probes `GET /question` for pending questions
4. **Overwrites** `_latest_response` with the polled message

This call itself is fast (HTTP roundtrip, no waiting on the agent), but it's bounded by the wait loop's `asyncio.sleep(30)` cadence.

## The Fix

Replace the blind `asyncio.sleep(30)` with an event-based wait that fires immediately when state transitions to `IDLE` or `WAITING_FOR_INPUT`.

### Option A: Add a state-change event to the manager

```python
# In OpenCodeSessionManager.__init__
self._state_changed: asyncio.Event = asyncio.Event()

# In _handle_worker_done, after state mutation under lock
async with self._lock:
    # ... existing state mutations ...
    self._state = SessionState.IDLE
    # ... etc ...
    # Signal waiters
asyncio.get_event_loop().call_soon_threadsafe(self._state_changed.set)
# (in async context, just call self._state_changed.set() — no need for call_soon_threadsafe)
self._state_changed.set()
```

But `asyncio.Event.set()` is a sync method — it's safe to call from within async code. The event loop schedules the wakeup.

### Option B: Use the existing `_worker_done_queue`

The manager already has `_worker_done_queue` (size=1, drop-oldest). A waiter could subscribe to it:

```python
async def wait_for_result_fast():
    while True:
        try:
            res = await asyncio.wait_for(
                manager._worker_done_queue.get(),
                timeout=1.0,  # safety recheck
            )
            # Worker done — state should now be IDLE/WAITING_FOR_INPUT
            return manager.get_snapshot()
        except asyncio.TimeoutError:
            # Periodic recheck (e.g. abort detection)
            continue
```

### Option C: Short polling loop (simple, no architectural change)

```python
# Instead of asyncio.sleep(POLL_INTERVAL_S), use a short tick
while loop.time() < deadline:
    snapshot = manager.get_snapshot()  # local, no HTTP
    state = snapshot["state"]
    if state in ("IDLE", "WAITING_FOR_INPUT"):
        return ...
    await asyncio.sleep(0.1)  # 100ms — fast response, low CPU
```

This is the simplest fix. The in-memory `get_snapshot()` is a sync read of Python attributes — effectively free. 100ms tick = max 100ms latency, mean ~50ms.

**Trade-off**: 100ms tick vs 30s tick = 300x more CPU during waits. But waits are short-lived, and the tick can yield via `asyncio.sleep` so it doesn't block the event loop.

## Recommended Fix: Option C (simple) + Option A (proper)

1. **Quick fix (Option C)**: Change `asyncio.sleep(POLL_INTERVAL_S)` to `asyncio.sleep(0.1)` in the wait loop. Read state directly from the in-memory `manager.get_snapshot()` instead of going through HTTP. ~10 lines of code, no architectural changes.

2. **Proper fix (Option A)**: Add a state-change event to the manager. Waiters subscribe and get notified immediately when state transitions. Eliminates polling entirely. ~30 lines of code across session_manager.py and external_opencode.py.

## Why This Matters

For a coder agent doing iterative work (e.g. "write code → wait → test → wait → fix → wait"), each `wait_for_result` call adds an average of 15s of dead time. A session with 10 iterations loses 2.5 minutes to polling latency alone. For interactive use, this is noticeably slow.

The original design explicitly intended to avoid this — the sync API returns the result, the wait should observe it immediately. Polling was supposed to be a backup, not the primary mechanism.

## Files Involved

- `daemon/tools/external_opencode.py:509-546` — `external_opencode_wait_for_result` (also `wait_any` at line 567)
- `daemon/opencode/session_manager.py:893-1027` — `_run_worker` (fast path, still works)
- `daemon/opencode/session_manager.py:1029-1099` — `_handle_worker_done` (sets state to IDLE)
- `daemon/opencode/server.py:275` — `GET_STATUS` calls `sync_state_with_open_code`
- `daemon/opencode/session_manager.py:629-779` — `sync_state_with_open_code`
