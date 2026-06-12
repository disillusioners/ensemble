# Phase 2: Connection Resolution + Cleanup

## Objective

Validate that the lazy session resolution via `connect_instance()` works correctly for all transport types, handle cleanup flows for lazily-initialized sessions, and remove dead code from the old eager preload path.

## Coupling

- **Depends on**: Phase 1 (schema cache + lazy tool factory + `McpSessionProvider`)
- **Coupling type**: tight — Phase 2 validates and completes the runtime behavior that Phase 1 defines
- **Shared files with other phases**: `daemon/services/mcp_service.py` (cleanup), `daemon/mcp/connection_manager.py` (no changes needed)
- **Shared APIs/interfaces**: `McpConnectionManager.connect_instance()`, `McpConnectionManager.get_session()`, `McpConnectionManager.close_instance()`
- **Why this coupling**: Phase 1 defines the lazy coroutine that calls `connect_instance()` — Phase 2 ensures the full lifecycle (create → use → cleanup) works end-to-end

## Context

Phase 1 creates lazy tools whose coroutines defer session creation to `_McpSessionProviderImpl.get_session()`. That method uses `conn_mgr.connect_instance(instance_id, [server])` for cold start, which already handles all 3 transports (stdio subprocess, SSE, streamable-http). This phase focuses on cleanup and removing dead code.

### Why no new `connection_manager.py` methods are needed (C2 fix)

The original plan proposed `get_or_create_session()` and `_create_and_store_session()` on `McpConnectionManager`. These are **not needed** because:

1. `connect_instance(instance_id, [server])` already does everything: transport creation via `_create_session()`, session initialization via `_open_and_track_session()`, storage in `_connections[instance_id][server.name]`, and error handling.

2. The double-check-locking in `_build_lazy_coroutine._get_session()` prevents duplicate calls. Two concurrent tools for the same server serialize on the shared `asyncio.Lock`; the second finds the session already in the shared cache dict.

3. `conn_mgr.get_session(instance_id, server_name)` provides the fast-path lookup.

**No changes to `connection_manager.py`** — it already has all the APIs we need.

## Tasks

### Task 1: Validate all-transport lazy resolution

**No code changes** — this is a validation task.

The `_McpSessionProviderImpl.get_session()` calls `conn_mgr.connect_instance(instance_id, [server])`. This method dispatches to:
- `_create_stdio_session()` for `McpStdioConfig` — spawns subprocess via `TaskScopedStdioClient`
- `_create_sse_session()` for `McpSseConfig` — HTTP connection via `TaskScopedContextManager(sse_client)`
- `_create_streamable_http_session()` for `McpStreamableHttpConfig` — HTTP via `TaskScopedContextManager(streamablehttp_client)`

All three are invoked within `_open_and_track_session()` → `_create_session()`, which `connect_instance()` calls via `_create_and_track_session()` → `_open_and_track_session()`. The flow is unchanged from the existing code.

**Validate by testing:**
- STDIO: non-built-in user STDIO server (cold path, not pooled)
- SSE: HTTP SSE server
- StreamableHTTP: HTTP streamable server
- All should connect on first tool call and work correctly

---

### Task 2: Update cleanup flow

**File:** `daemon/services/mcp_service.py`

The existing `close_connections()` already handles closing sessions via `conn_mgr.close_instance()`. For lazy connections, sessions are stored in `McpConnectionManager._connections` once created (by `connect_instance()`), so `close_instance()` finds and closes them. The closure-level `shared_session_cache` dict is just a pointer optimization — `connection_manager` is the authoritative store.

Update `close_connections()` to also clear the per-instance session state:

```python
async def close_connections(self, instance_id: str) -> None:
    self._tools_cache.pop(instance_id, None)
    self._session_caches.pop(instance_id, None)  # Clear shared session caches/locks
    async with self._preload_lock:
        self._preload_locks.pop(instance_id, None)
    try:
        await get_mcp_connection_manager().close_instance(instance_id)
    except Exception as e:
        logger.warning(f"Error closing MCP connections for {instance_id[:8]}: {e}")
```

**Key insight:** `close_instance()` works even if no lazy connections were ever created (no-op case — the `_connections[instance_id]` dict simply doesn't exist).

---

### Task 3: Remove dead code from old eager preload path

**File:** `daemon/services/mcp_service.py`

The old `preload_mcp_tools()` contained several methods and logic paths that are no longer needed:

**Remove:**
- `_probe_connection()` method — was used for 3s liveness probing on pooled acquire. Not needed since we never eagerly connect anymore.
- The pooled-server branch in the old `preload_mcp_tools()` that called `pool.acquire()` + `_probe_connection()` + `conn_mgr.transfer_session()` — replaced by the lazy path in `_McpSessionProviderImpl`.
- The cold-server branch that called `conn_mgr.connect_instance()` + `_discover_server_tools()` — replaced by `get_schemas_for_server()` (one-time discovery) + lazy tools.
- `_discover_server_tools()` method — replaced by `_discover_schemas_cold()` (schema-only, no session binding).

**Keep:**
- `ensure_mcp_preloaded()` — unchanged, still the guard function
- `get_mcp_tools()` — unchanged, reads from `_tools_cache`
- `close_connections()` — updated in Task 2
- `close_all_connections()` — unchanged

---

### Task 4: Verify restore/recovery path (W8)

**File:** `daemon/services/mcp_service.py`, `daemon/manager.py`

When an instance is restored (e.g., after daemon restart from SQLite checkpoint), the flow is:
1. `_restore_instance()` rebuilds the instance from persisted state
2. `ensure_mcp_preloaded()` is called during restore

With lazy tools, the restored instance gets lazy tools with fresh (empty) session caches. This is correct — the old sessions are gone after restart. The first tool call after restore will create new connections.

**Verify in tests (Phase 3):**
- Instance with lazy tools → simulate restart → restore instance → first tool call creates new connection
- Ensure no stale session references remain after restore

No code changes needed for this — the behavior falls out naturally from the lazy design.

---

### Task 5: Document behavioral changes

**File:** Code comments and/or `docs/`

Add comments documenting:

1. **Pool size=1 implication (W5):** When the first instance takes the pooled connection, subsequent instances fall back to cold start. This is acceptable — the warmup pool is an optimization, not a requirement. Operators can increase pool size via `MCP_POOL_SERVERS` env var.

2. **STDIO timeout behavior (W3):** `connect_instance(per_server_timeout=15.0)` is overridden by STDIO's internal default of 30s (`STDIO_DEFAULT_TIMEOUT`) unless the server config specifies its own `timeout` field. The 15s parameter applies to SSE/HTTP transports.

3. **Concurrency guard (W7):** The double-check-locking pattern in `_build_lazy_coroutine._get_session()` is the sole concurrency guard. Two concurrent tool calls to the same server serialize on the shared `asyncio.Lock`; the second finds the session already cached.

```python
# In _build_lazy_coroutine:
# CONCURRENCY GUARD (W7): Double-check locking ensures exactly one connection
# per instance+server even under concurrent first calls.
# 1. Fast path: check cache without lock (no contention after first call)
# 2. Slow path: acquire shared lock, re-check cache, create if still missing
```

## Key Files

- `daemon/services/mcp_service.py` — Updated `close_connections()`, removed dead code (`_probe_connection`, `_discover_server_tools`, old preload branches)
- `daemon/mcp/connection_manager.py` — **No changes** (reuses existing `connect_instance()`, `get_session()`, `close_instance()`)

## Constraints

- Must handle concurrent first-calls to different tools on the same server (shared lock + double-check pattern)
- `close_instance()` must work even if no lazy connections were ever created (no-op case)
- All 3 transports must work via the existing `connect_instance()` path (no new transport logic)

## Deliverables

- [ ] Updated `close_connections()` clearing `_session_caches`
- [ ] Dead code removed: `_probe_connection()`, `_discover_server_tools()`, old preload branches
- [ ] Behavioral documentation comments (W3, W5, W7)
- [ ] Restore/recovery verified via tests (Phase 3)
