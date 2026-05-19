# Plan Overview: MCP STDIO Server Warm-Up Pool

## Objective
Implement a pre-warmed connection pool for built-in STDIO MCP servers (Context7, WebFetch) that eliminates the 5-15s cold-start delay on every agent instance spawn by keeping ready-to-use subprocess connections available at daemon startup.

## Scope Assessment
**LARGE** — New infrastructure module (warmup_pool.py), modifications to 3 existing modules (connection_manager.py, mcp_service.py, manager.py), daemon lifecycle integration, configuration schema, health monitoring, and comprehensive test coverage across unit and integration levels.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: `feature/mcp-server-pool` (already exists)

## Current Architecture (Explored)

### Connection Flow (Current — Cold Start)
```
spawn_instance_with_mcp()
  → ensure_mcp_preloaded(instance_id)
    → mcp_service.preload_mcp_tools(instance_id)
      → lists active MCP servers from DB
      → connection_manager.connect_instance(instance_id, servers)
        → For each STDIO server: _create_stdio_session()
          → stdio_client(server_params) → subprocess spawn
          → asyncio.timeout(30s) → wait for streams
          → ClientSession + session.initialize() → MCP handshake
      → _discover_server_tools() for each server → load_mcp_tools(session)
      → Cache tools in _tools_cache[instance_id]
  → spawn_instance() (sync) → reads from _tools_cache
```

### Key Files
| File | Role |
|------|------|
| `daemon/mcp/connection_manager.py` | Singleton `McpConnectionManager` — manages sessions per instance |
| `daemon/services/mcp_service.py` | `McpService` — preload orchestration, tool caching |
| `daemon/manager.py` | `ensure_mcp_preloaded()`, `spawn_instance_with_mcp()` |
| `daemon/mcp/builtin_servers/` | `BuiltinServerRegistry`, Context7, WebFetch definitions |
| `daemon/mcp/config.py` | `McpStdioConfig`, `McpSseConfig`, etc. |

### Key Data Structures
```python
# connection_manager.py
_connections: dict[str, dict[str, ClientSession]] = {}     # instance_id → server_name → session
_stream_contexts: dict[str, dict[str, Any]] = {}            # instance_id → server_name → stream_cm

# mcp_service.py
_tools_cache: dict[str, list[BaseTool]] = {}                # instance_id → tools
_preload_locks: dict[str, asyncio.Lock] = {}                # instance_id → lock
```

### Built-in STDIO Servers
| Server | Command | Cold Start Time |
|--------|---------|-----------------|
| context7 | `npx -y @upstash/context7-mcp` | 8-15s |
| webfetch | `uvx mcp-server-fetch` | 5-10s |

## Architecture Decision: Transfer Ownership Model

The pool will use a **transfer ownership** model:
1. Pool pre-creates sessions (subprocess + handshake + tool discovery)
2. When an instance needs a connection, pool transfers ownership to `connection_manager`
3. `connection_manager` tracks the session as if it created it (same `_connections` / `_stream_contexts` dicts)
4. Pool replenishes asynchronously

This minimizes changes to existing code — `connection_manager` and `mcp_service` see the same data structures they always have.

### Why Not Other Models?
- **Connection sharing**: STDIO is 1:1 — can't share a single subprocess between instances
- **Proxy/bridge**: Adds complexity, latency, and a single point of failure
- **Lazy init with caching**: Only caches npx/uvx package resolution, not the handshake

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Pool Infrastructure | `McpWarmupPool` class — lifecycle, acquire, replenish, health check | None | — | 3-4h |
| 2 | MCP Layer Integration | Wire pool into connection_manager + mcp_service, graceful degradation | Phase 1 | tight | 2-3h |
| 3 | Daemon Lifecycle & Configuration | Startup warm-up, shutdown drain, config schema, health monitoring loop | Phase 2 | tight | 2-3h |
| 4 | Testing | Unit tests for pool, integration tests for full flow, regression verification | Phases 1-3 | loose | 2-3h |

### Coupling Assessment

| From → To | Coupling | Reason |
|-----------|----------|--------|
| Phase 1 → Phase 2 | **tight** | Phase 2 imports `McpWarmupPool` and calls its methods directly |
| Phase 2 → Phase 3 | **tight** | Phase 3 calls `pool.warmup()` at startup, depends on config Phase 2 reads |
| Phase 3 → Phase 4 | **loose** | Tests depend on the interfaces/contracts, not implementation details |

### Phase Scheduling
- **Phases 1-3**: Must be sequential (tight coupling, each builds on the prior)
- **Phase 4**: Can start drafting test structure after Phase 1 is complete (loose coupling)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Pre-warmed subprocess dies before use | Medium — pool returns stale connection | Liveness probe (MCP ping, 3s timeout) before transfer; health checks evict dead connections; fallback to cold-start on failure |
| Resource overhead of idle processes | Low — 2-4 extra processes (~50-100MB total) | Configurable pool size (default 1 per server); `asyncio.Semaphore` caps concurrent replenish; document resource expectations |
| Race condition: acquire while replenishing | Medium — double-creation or missed creation | Per-server asyncio.Lock; `_replenish_semaphore` caps concurrent replenish; atomic queue snapshot in health check |
| Existing test breakage | High — tests mock connection_manager | Add `transfer_session` method (new) instead of modifying `connect_instance`; pool is injectable (set_warmup_pool) |
| Orphaned processes on warmup failure | Medium — leaked subprocesses | `_create_pooled_connection` wraps all work in try/except with `streams_cm.__aexit__()` cleanup |
| Orphaned processes during drain (mid-flight replenish) | Medium — replenish task creates subprocess just before drain | Track all replenish tasks in `set[asyncio.Task]`; cancel all in `drain()` before closing remaining connections |
| npx/uvx cache not populated in CI | Low — first warm-up still slow | Document CI setup; warm-up failure is non-fatal (graceful degradation) |
| Pool warm-up slow on daemon start | Low — daemon start takes 5-15s extra | Run warm-up async (don't block startup); pool becomes available when ready |
| Health check starves acquire calls | Medium — queue drained during check | Per-server lock: acquire() waits for lock instead of seeing empty queue; lock held briefly (1-2s) |

## Success Criteria
- [ ] Instance spawn with built-in STDIO servers completes in <1s (vs current 5-15s)
- [ ] Pool pre-warms at daemon startup, before any instance is spawned
- [ ] Pool replenishes after connection is acquired
- [ ] Graceful degradation: cold-start fallback when pool is empty
- [ ] Health checks detect and evict dead pre-warmed connections
- [ ] Clean shutdown terminates all pool processes
- [ ] All existing MCP tests pass without modification
- [ ] User-defined MCP servers (SSE, streamable-http) unaffected

## Tracking
- Created: 2026-05-19
- Last Updated: 2026-05-19 (rev2 — reviewer fixes C1, C2, W1, W3/W11, W4, S1)
- Status: draft
