# OpenCode Session Manager Memory Leak

**Date:** 2026-06-11
**Status:** Workaround Applied (lazy loading disabled startup recovery)
**Affected:** daemon/opencode/registry.py, daemon/manager.py

## Summary

`OpenCodeSessionManager` instances accumulate in memory and are never cleaned up. Each manager runs a persistent background loop, holding resources (asyncio task, httpx client, queues). On daemon startup, ALL persisted sessions are loaded into memory via `recover_from_registry()`, causing memory bloat proportional to total historical sessions.

## Root Cause

### 1. Startup Recovery Loads All Sessions

At `daemon/manager.py:1075`, on every daemon startup:

```python
recovered = await self._opencode_registry.recover_from_registry()
```

This calls `registry.recover_from_registry()` which loads **every row** from `opencode_sessions` table into memory, regardless of whether the session is active or abandoned.

Each `OpenCodeSessionManager` holds:
- An `asyncio.Task` running `_run_loop()` (runs forever until explicitly stopped)
- An `httpx.AsyncClient` connection pool
- Two `asyncio.Queue` objects (input, worker_done)
- State data (`_questions`, `_latest_response`)
- A 30-second polling timer that wakes up on every active manager

### 2. Managers Never Removed After Abort

`abort_session()` (registry.py:222-285) resets the manager state but does NOT:
- Call `manager.stop()` to terminate the background loop
- Remove the manager from `self._managers` dict

```python
# registry.py:272-275
manager = await self.get_manager(session_id)
if manager is not None:
    await manager.abort_task()
    # ❌ Manager still in self._managers, loop still running
```

### 3. create_new() Doesn't Remove Old Manager

When creating a new session with an existing `(project, session_name)`:
- Old session is deleted from DB (line 171)
- But old manager remains in `self._managers` dict

```python
# registry.py:170-174
if existing is not None:
    self._repository.delete(project, session_name)
    # ❌ Old manager still in memory
```

## Impact

- **Memory**: O(all historical sessions) instead of O(active sessions)
- **CPU**: 30-second poll wake-ups for every historical session
- **Connections**: httpx connection pools held by abandoned sessions
- **On restart**: All abandoned sessions reloaded into memory

---

## CRITICAL: Unnecessary 30-Second Polling

Even after fixing the memory leak, the 30-second polling loop is fundamentally wasteful.

### What Happens Every 30 Seconds (Per Loaded Session)

Each `OpenCodeSessionManager` runs this in `_run_loop()` (session_manager.py:724-794):

```python
async def _run_loop(self) -> None:
    while True:
        # Wait for stop/input/worker_done events
        done, _ = await asyncio.wait({stop_task, input_task, worker_done_task}, ...)

        # ... process events ...

        # EVERY iteration: sleep 30s then poll
        await asyncio.sleep(POLL_INTERVAL_S)  # 30 seconds
        await self._poll_questions()  # ← HTTP call + SQLite write
```

### What `_poll_questions()` Does (session_manager.py:1034-1078)

```python
async def _poll_questions(self) -> None:
    # HTTP GET to OpenCode server
    all_questions = await self._client.get_questions()  # GET /question

    # Filter to this session
    session_questions = [q for q in all_questions if q.session_id == self.session_id]

    # Update in-memory state
    async with self._lock:
        self._questions = [...]

    # Persist to SQLite DB
    if state_to_save is not None:
        await self._persist_state()  # SQLite write
```

### Each Manager Has Its Own HTTP Client

Each manager creates its own `OpenCodeClient` with its own `httpx.AsyncClient` connection pool (client.py:234-238):

```python
self._client: httpx.AsyncClient = httpx.AsyncClient(
    base_url=self.base_url,  # http://localhost:8088
    timeout=timeout_s,        # 1 hour timeout
)
```

### The Problem

**HTTP polling happens every 30 seconds for EVERY loaded session, regardless of activity:**

- Session is abandoned/inactive → still polls
- Session from weeks ago → still polls
- User hasn't touched that project in days → still polls
- Agent is NOT waiting for input → still polls

With 50 loaded sessions: **50 HTTP requests + 50 SQLite writes every 30 seconds**, doing nothing useful.

### The Fix: Poll Only When Waiting

Polling should ONLY happen when an agent is actively waiting for user input (i.e., `wait` or `wait_any` command was called). Until then, the session should be truly idle — no loop, no polling, no resources consumed.

**Polling triggers:**
- `WAIT` / `WAIT_ANY` command sent to OpenCode
- Agent enters `WAITING_FOR_INPUT` state

**Polling stops:**
- When input arrives and is processed
- When `WAITING_FOR_INPUT` transitions back to `BUSY` or `IDLE`

This means the 30-second polling loop should be removed entirely and replaced with event-driven behavior — only poll when the agent has explicitly asked OpenCode to wait for something.

## Workaround Applied

Disabled `recover_from_registry()` call at startup:

```python
# daemon/manager.py:1070-1078
# DISABLED: loading all sessions on startup causes memory bloat.
# Sessions are now loaded lazily on-demand via load_session_into_memory().
# Uncomment below to re-enable recovery on startup.
# try:
#     recovered = await self._opencode_registry.recover_from_registry()
#     logger.info(f"Recovered {recovered} opencode session(s) from registry")
# except Exception as exc:
#     logger.warning(f"Failed to recover opencode sessions: {exc}")
```

Sessions are now loaded on-demand when a request arrives for a session not yet in memory (server.py:287 `load_session_into_memory()`).

## Recommended Fix

### Option A: Clean up managers in abort_session()

```python
# In abort_session(), after abort_task():
manager = await self.get_manager(session_id)
if manager is not None:
    await manager.abort_task()
    await manager.stop()  # Signal loop to exit
    async with self._managers_lock:
        self._managers.pop(session_id, None)  # Remove from dict
```

### Option B: Add delete_session() method

Create explicit `delete_session(project, session_name)` that:
1. Stops and removes manager from memory
2. Best-effort remote abort
3. Deletes from DB

### Option C: Lazy load only active sessions

Add an `is_active` flag to session records. Only load sessions where `is_active=True` on startup. Mark sessions inactive on `abort_session()`.

## Files Involved

- `daemon/opencode/registry.py` — `_managers` dict, `abort_session()`, `create_new()`
- `daemon/opencode/session_manager.py` — `OpenCodeSessionManager` class, `_run_loop()`
- `daemon/manager.py:1070-1078` — startup recovery call
- `daemon/opencode/server.py:287` — on-demand load path
