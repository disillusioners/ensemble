# Code Review Fixes for MCP Integration — Implementation Notes

## What was delivered
16 files changed, 344 insertions, 202 deletions.

### Critical fixes
- C-1: `ensure_mcp_preloaded()` + `spawn_instance_with_mcp()` in Manager — replaced 7 duplicated preload blocks
- C-2: Spawn failure cleanup — `spawn_instance_with_mcp()` catches exceptions and closes MCP connections
- C-3: Resource leak fix — `_stream_contexts` dict tracks stream context managers for proper cleanup

### Warning fixes
- W-3: Per-instance `_preload_locks` in McpService prevents concurrent preload races
- W-4/W-5: Eager lock init (`self._lock = asyncio.Lock()` in `__init__`) — no lazy race
- W-6: `asyncio.gather(return_exceptions=True)` for parallel session closing
- W-8: `McpConfigValidationError(ValueError)` custom exception
- W-11/W-12: Removed dead code in tool_adapter.py, fixed mcp_tools.py

### Key patterns
1. `spawn_instance_with_mcp()` combines preload + spawn + cleanup in one call
2. `ensure_mcp_preloaded()` is idempotent, safe for already-loaded instances, graceful no-op if no _mcp_service
3. Stream tracking uses `_stream_contexts[instance_id][server_name]` = context manager tuple
4. Per-instance preload lock uses two-phase: outer `_preload_lock` to access `_preload_locks` dict, inner per-instance lock for actual preload

### Commit
- Hash: `adbb185`
- Branch: `feature/mcp-runtime-integration`
- 16 files, +344 -202
