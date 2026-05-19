# Phase 2: MCP Layer Integration

## Objective
Wire the warm-up pool into the existing MCP connection management layer. Add a `transfer_session()` method to `McpConnectionManager` and modify `McpService.preload_mcp_tools()` to check the pool first, falling back to cold-start when empty.

## Coupling
- **Depends on**: Phase 1 (McpWarmupPool)
- **Coupling type**: tight — imports `McpWarmupPool` and calls `acquire()`, modifies `connection_manager._connections` dict directly
- **Shared files with other phases**: `daemon/mcp/connection_manager.py`, `daemon/services/mcp_service.py`
- **Shared APIs/interfaces**: `transfer_session()` on connection_manager, pool-aware logic in mcp_service
- **Why this coupling**: Phase 2 directly uses the pool class from Phase 1 and modifies existing MCP modules

## Context
Phase 1 delivered `McpWarmupPool` with `register_server()`, `warmup()`, `acquire()`, and `drain()` methods. This phase modifies the existing MCP layer to use the pool while preserving backward compatibility.

### Key Principle: Additive Changes Only
- **Add** new methods to existing classes (don't modify existing method signatures)
- **Modify** `preload_mcp_tools` to try pool first, then fall back to existing flow
- Existing tests should pass unchanged because the pool returns `None` when not initialized

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `transfer_session()` to `McpConnectionManager` | New method: `transfer_session(instance_id, server_name, session, stream_cm)`. Registers an externally-created session into `_connections[instance_id][server_name]` and `_stream_contexts[instance_id][server_name]`. Uses `self._lock` for thread safety. This is the ownership transfer point. | `daemon/mcp/connection_manager.py` |
| 2 | Add liveness probe in pool→transfer path | After `pool.acquire()` returns a `PooledConnection`, perform a quick MCP protocol ping (2-3s timeout) before transferring to connection_manager. If ping fails, close the stale connection, log warning, and add server to the cold-start fallback list. This catches the race condition where a process dies between pool health check and transfer. | `daemon/services/mcp_service.py` |
| 3 | Modify `McpService.preload_mcp_tools()` to try pool first | Before calling `conn_mgr.connect_instance()`, check if the pool has pre-warmed connections for each built-in STDIO server. For servers with pooled connections: call `pool.acquire(server_name)`, **liveness probe**, then `conn_mgr.transfer_session()`, use pre-discovered tools. For servers without pooled connections (or if pool is empty / probe failed): use existing `connect_instance` flow. | `daemon/services/mcp_service.py` |
| 4 | Separate STDIO vs non-STDIO server handling in `preload_mcp_tools()` | Split the server list into: (a) built-in STDIO servers → try pool first, (b) all other servers → use existing flow. This ensures user-defined SSE/streamable-http servers are unaffected. | `daemon/services/mcp_service.py` |
| 5 | Add pool reference to `McpService.__init__()` | Store `self._warmup_pool: McpWarmupPool | None = None`. Provide `set_warmup_pool(pool)` method. This allows the pool to be injected after initialization (chicken-and-egg: pool needs config, service needs pool). | `daemon/services/mcp_service.py` |
| 6 | Add `is_builtin_stdio(server) -> bool` helper | Checks if a server is a built-in STDIO server (by matching name against `BuiltinServerRegistry` and checking transport type). Used in `preload_mcp_tools` to decide pool vs cold-start. | `daemon/services/mcp_service.py` or `daemon/mcp/builtin_servers/` |

## Key Files
- `daemon/mcp/connection_manager.py` — Add `transfer_session()` method
- `daemon/services/mcp_service.py` — Modify `preload_mcp_tools()`, add pool reference
- `daemon/mcp/builtin_servers/__init__.py` — May need `is_builtin_stdio()` helper

## Constraints
- **Do NOT modify** `connect_instance()` — existing method stays as-is for fallback
- **Do NOT modify** method signatures of existing public methods — only add new ones
- **Do NOT change** how `_connections` / `_stream_contexts` / `_tools_cache` are structured
- The pool must be optional — code works identically when pool is not configured

## Implementation Notes

### `transfer_session()` Implementation
```python
async def transfer_session(
    self,
    instance_id: str,
    server_name: str,
    session: ClientSession,
    stream_cm: Any,
) -> None:
    """Transfer an externally-managed session into this manager's tracking."""
    async with self._lock:
        if instance_id not in self._connections:
            self._connections[instance_id] = {}
        if instance_id not in self._stream_contexts:
            self._stream_contexts[instance_id] = {}
        self._connections[instance_id][server_name] = session
        self._stream_contexts[instance_id][server_name] = stream_cm
    logger.debug(f"Transferred pooled session for '{server_name}' to instance {instance_id[:8]}")
```

### Modified `preload_mcp_tools()` Flow (with liveness probe)
```python
async def preload_mcp_tools(self, instance_id: str) -> None:
    # ... (existing lock logic) ...
    servers = self._manager._mcp_server_repository.list_mcp_servers(is_active=True)
    if not servers:
        self._tools_cache[instance_id] = []
        return

    conn_mgr = get_mcp_connection_manager()
    pool = self._warmup_pool

    # Split servers into pooled vs cold-start
    pooled_servers = []
    cold_servers = []
    for server in servers:
        if pool and self._is_builtin_stdio(server):
            pooled_servers.append(server)
        else:
            cold_servers.append(server)

    tools = []

    # Handle pooled servers (from warm-up pool)
    for server in pooled_servers:
        conn = await pool.acquire(server.name)
        if conn:
            # Liveness probe: verify connection is still alive before transfer
            alive = await self._probe_connection(conn)
            if alive:
                # Transfer ownership to connection manager
                await conn_mgr.transfer_session(
                    instance_id, server.name, conn.session, conn.stream_cm
                )
                tools.extend(conn.tools)  # Pre-discovered tools!
                continue
            else:
                # Stale connection — close it, fall back to cold-start
                logger.warning(f"Stale pooled connection for '{server.name}', falling back")
                try:
                    await conn.session.close()
                    await conn.stream_cm.__aexit__(None, None, None)
                except Exception:
                    pass
        # Pool empty or stale — fall back to cold start for this server
        cold_servers.append(server)

    # Handle cold-start servers (existing flow)
    if cold_servers:
        await conn_mgr.connect_instance(instance_id, cold_servers)
        results = await asyncio.gather(
            *[self._discover_server_tools(instance_id, s) for s in cold_servers],
            return_exceptions=True,
        )
        for server, result in zip(cold_servers, results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to discover tools from '{server.name}': {result}")
            else:
                tools.extend(result)

    self._tools_cache[instance_id] = tools

async def _probe_connection(self, conn: PooledConnection, timeout: float = 3.0) -> bool:
    """Quick liveness probe — MCP protocol ping with short timeout."""
    try:
        await asyncio.wait_for(
            conn.session.send_ping(),
            timeout=timeout,
        )
        return True
    except Exception:
        return False
```

### `is_builtin_stdio()` Helper
```python
def _is_builtin_stdio(self, server: McpServer) -> bool:
    """Check if a server is a built-in STDIO server."""
    from daemon.mcp.builtin_servers import get_registry
    registry = get_registry()
    definition = registry.get_by_name(server.name)
    if definition is None:
        return False
    config = definition.get_base_config()
    return config.get("transport") == "stdio"
```

## Deliverables
- [ ] `transfer_session()` method added to `McpConnectionManager`
- [ ] `_probe_connection()` liveness probe method added to `McpService`
- [ ] `McpService` modified to use pool when available (with probe-then-transfer)
- [ ] `set_warmup_pool()` method on `McpService`
- [ ] `_is_builtin_stdio()` helper method
- [ ] Graceful degradation: pool miss → stale probe fail → cold-start fallback
- [ ] No changes to existing public API signatures
