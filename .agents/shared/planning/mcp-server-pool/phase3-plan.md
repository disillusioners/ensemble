# Phase 3: Daemon Lifecycle, Configuration & Health Monitoring

## Objective
Wire the warm-up pool into the daemon startup and shutdown lifecycle, add pool configuration to the daemon config schema, and implement the background health monitoring loop that keeps pool connections alive.

## Coupling
- **Depends on**: Phase 2 (pool integrated into MCP layer)
- **Coupling type**: tight — calls `pool.warmup()` and `pool.drain()` at daemon lifecycle points, reads config that Phase 2's code uses
- **Shared files with other phases**: `daemon/manager.py`, `daemon/api.py` (lifespan), daemon config
- **Shared APIs/interfaces**: `McpWarmupPool.warmup()`, `.drain()`, `.start_health_check()`
- **Why this coupling**: Must integrate with daemon startup/shutdown hooks which call pool methods

## Context
Phase 2 added pool integration to the MCP layer. This phase connects the pool to the daemon lifecycle so it:
1. Warms up during daemon startup (after MCP services are initialized)
2. Drains gracefully during daemon shutdown
3. Runs periodic health checks to detect dead connections
4. Reads pool configuration from the daemon's YAML config

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add pool configuration to daemon config schema | Add `mcp_pool` section to `daemon/config.py` (or equivalent config model): `enabled: bool = True`, `default_pool_size: int = 1`, `servers: dict[str, int] = {}` (per-server override), `health_check_interval: int = 60` (seconds), `health_check_timeout: int = 5` (seconds per connection). | `daemon/config.py` |
| 2 | Initialize pool in `InstanceManager.__init__()` or lifespan startup | After `_bootstrap_builtin_servers()` and `_mcp_service` initialization: create pool, register built-in STDIO servers with configs, call `pool.warmup()`. Inject pool into mcp_service via `set_warmup_pool(pool)`. Run warmup as async task (don't block startup). | `daemon/manager.py` |
| 3 | Start health check loop after warmup | After pool warmup completes, start `pool.start_health_check(interval=cfg.health_check_interval)`. This runs as a background `asyncio.Task`. | `daemon/manager.py` |
| 4 | Drain pool during daemon shutdown | In `manager.shutdown()`, before `_mcp_service.close_all_connections()`: call `pool.drain()`. This terminates all pooled subprocesses cleanly. After drain, proceed with existing shutdown. | `daemon/manager.py` |
| 5 | Add `start_health_check()` method to pool | Thin wrapper: creates `asyncio.create_task(self._health_check_loop(interval))`. Stores task reference for cancellation in `drain()`. | `daemon/mcp/warmup_pool.py` |
| 6 | Enhance `drain()` to cancel health check task | In `drain()`: cancel `_health_task` if running, await cancellation with timeout. Then proceed with existing drain logic. | `daemon/mcp/warmup_pool.py` |
| 7 | Add pool status logging at startup | After warmup completes, log pool status via `pool.get_status()`. E.g., "MCP warm-up pool ready: context7=1, webfetch=1". Log warnings for any servers that failed to warm up. | `daemon/manager.py` |
| 8 | Handle pool warmup failure gracefully | If warmup fails for all servers, log error but don't crash daemon. Pool stays empty, instances fall back to cold-start (existing behavior). This is non-fatal. | `daemon/manager.py` |

## Key Files
- `daemon/manager.py` — Startup/shutdown integration
- `daemon/mcp/warmup_pool.py` — `start_health_check()`, enhanced `drain()`
- `daemon/config.py` — Pool configuration schema

## Constraints
- Pool warmup must NOT block daemon startup — run as background task
- Pool warmup failure must NOT prevent daemon from starting
- Pool drain must have a timeout (e.g., 10s) — don't hang shutdown
- Configuration must be optional — defaults work out of the box
- The `mcp_pool` config section should be under the existing daemon config structure

## Implementation Notes

### Config Schema Addition
```python
# In daemon/config.py (or wherever daemon config is defined)
class McpPoolConfig:
    enabled: bool = True
    default_pool_size: int = 1
    servers: dict[str, int] = {}  # server_name → pool_size override
    health_check_interval: int = 60  # seconds
    health_check_timeout: int = 5    # seconds per connection

# Merged into existing DaemonConfig (or equivalent):
# mcp_pool: McpPoolConfig = McpPoolConfig()
```

### Startup Integration (in manager.py)
```python
async def _init_warmup_pool(self) -> None:
    """Initialize and warm up the MCP connection pool."""
    if not self._config.mcp_pool.enabled:
        logger.info("MCP warm-up pool disabled by config")
        return

    pool = get_mcp_warmup_pool()
    registry = get_registry()

    for definition in registry.get_all():
        name = definition.name
        config_dict = definition.get_base_config()
        if config_dict.get("transport") != "stdio":
            continue
        pool_size = self._config.mcp_pool.servers.get(
            name, self._config.mcp_pool.default_pool_size
        )
        stdio_config = McpStdioConfig(**config_dict)
        pool.register_server(name, stdio_config, pool_size=pool_size)

    # Warm up in background — don't block startup
    async def warmup_and_report():
        await pool.warmup()
        status = pool.get_status()
        logger.info(f"MCP warm-up pool ready: {status}")
        pool.start_health_check(self._config.mcp_pool.health_check_interval)

    self._warmup_task = asyncio.create_task(warmup_and_report())
    self._mcp_service.set_warmup_pool(pool)
```

### Shutdown Integration (in manager.py)
```python
# In shutdown() method, BEFORE _mcp_service.close_all_connections():
pool = get_mcp_warmup_pool()
try:
    await asyncio.wait_for(pool.drain(), timeout=15.0)
except asyncio.TimeoutError:
    logger.warning("MCP pool drain timed out, forcing cleanup")
except Exception as e:
    logger.warning(f"Error draining MCP pool: {e}")
```

### Health Check Integration
```python
# In warmup_pool.py
def start_health_check(self, interval: int = 60) -> None:
    """Start the periodic health check loop."""
    if self._health_task and not self._health_task.done():
        return
    self._health_task = asyncio.create_task(self._health_check_loop(interval))

async def _health_check_loop(self, interval: int) -> None:
    while self._running:
        try:
            await asyncio.sleep(interval)
            await self.health_check()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"MCP pool health check error: {e}")
```

## Deliverables
- [ ] Pool config schema added to daemon config
- [ ] Pool initialized and warmed up at daemon startup
- [ ] Pool drained at daemon shutdown (before existing MCP cleanup)
- [ ] Health check loop starts after warmup
- [ ] Graceful startup/shutdown — failures are logged, not fatal
- [ ] Pool status logged at startup
