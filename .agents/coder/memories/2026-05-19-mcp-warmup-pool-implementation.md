# MCP STDIO Server Warm-Up Pool Implementation

## Date: 2026-05-19

## Summary
Implemented a pre-warmed connection pool for built-in STDIO MCP servers (Context7, WebFetch) that eliminates the 5-15s cold-start delay on agent instance spawn. All 4 phases completed on branch `feature/mcp-server-pool`.

## Architecture: Transfer Ownership Model
- Pool pre-creates sessions (subprocess + handshake + tool discovery)
- On acquire, ownership transfers to connection_manager (same `_connections`/`_stream_contexts` dicts)
- Pool replenishes asynchronously after each acquire
- Non-blocking acquire with cold-start fallback

## Key Files Created/Modified
| File | Phase | Change |
|------|-------|--------|
| `daemon/mcp/warmup_pool.py` | 1 | NEW - McpWarmupPool class, PooledConnection dataclass, singleton |
| `daemon/mcp/connection_manager.py` | 2 | Added transfer_session() method |
| `daemon/services/mcp_service.py` | 2 | Pool-aware preload, liveness probe, _is_builtin_stdio helper |
| `daemon/config.py` | 3 | Added McpPoolConfig (enabled by default) |
| `daemon/manager.py` | 3 | Startup warmup (non-blocking), shutdown drain (15s timeout) |
| `tests/unit/test_mcp_warmup_pool.py` | 4 | NEW - 22 unit tests |
| `tests/unit/test_mcp_connection_manager.py` | 4 | Added 2 transfer_session tests |
| `tests/unit/test_mcp_service.py` | 4 | Added 10 pool-aware preload tests |
| `tests/unit/test_builtin_mcp_servers.py` | 4 | Fixed mock config for mcp_pool compat |

## Key Implementation Patterns
1. **BaseException catch** in `_create_pooled_connection` — catches CancelledError too, prevents orphaned processes
2. **Tracked replenish tasks** — `set[asyncio.Task]` with `add_done_callback(discard)` for auto-cleanup
3. **Semaphore(2)** — caps concurrent replenish across all servers
4. **Per-server lock in health check** — prevents queue starvation during check (acquire waits for lock, not empty queue)
5. **Liveness probe before transfer** — MCP ping with 3s timeout, falls back to cold-start on failure
6. **30s timeout** on `_create_pooled_connection` — matches connection_manager pattern
7. **Pool enabled by default** — opt-out via `mcp_pool.enabled: false`

## API References (verified correct)
- MCP ping: `session.send_ping()` (NOT `send_request`)
- Registry: `get_registry()` from `daemon/mcp/builtin_servers/`
- Registry iteration: `registry.get_all()` returns list (NOT dict, NO `.items()`)
- Config: `McpPoolConfig` with Pydantic BaseSettings pattern

## Commits
- Phase 1: Pool infrastructure
- Phase 2: MCP layer integration (23f687b)
- Phase 3: Daemon lifecycle & config (c1e504d)
- Phase 4: Testing (d047d81)

## Test Results
All 289 MCP-related tests pass with zero failures.
