# MCP Runtime Integration — Test Report

**Date**: 2026-05-17
**Branch**: `feature/mcp-runtime-integration`
**Commit**: `d195b5a`

## Summary

| Category | Total | Passed | Failed | Skipped |
|----------|-------|--------|--------|---------|
| MCP Tests (existing) | 145 | 145 | 0 | 0 |
| MCP Tests (new runtime integration) | 16 | 16 | 0 | 0 |
| **MCP Total** | **161** | **161** | **0** | **0** |
| Unit Tests (main groups) | 2,362 | 2,362 | 0 | 19 |
| dev.sh (ensure.md) | 1 | 1 | 0 | 0 |

## MCP Test Breakdown

### Existing MCP Tests (145 tests, all PASS)

| File | Tests | Status |
|------|-------|--------|
| `tests/unit/test_mcp_config.py` | 15 | ✅ PASS |
| `tests/unit/test_mcp_connection_manager.py` | 17 | ✅ PASS |
| `tests/unit/test_mcp_service.py` | 15 | ✅ PASS |
| `tests/unit/test_mcp_tool_filter.py` | 22 | ✅ PASS |
| `tests/unit/test_mcp_concurrent.py` | 8 | ✅ PASS |
| `tests/unit/test_mcp_server_crud.py` | 55 | ✅ PASS |
| `tests/integration/test_mcp_lifecycle.py` | 13 | ✅ PASS |

### New Runtime Integration Tests (16 tests, all PASS)

| File | Tests | Status |
|------|-------|--------|
| `tests/unit/test_mcp_runtime_integration.py` | 16 | ✅ PASS |

**New Test Details:**

#### TestFullFlowMcpToolsInjected (3 tests)
- `test_preload_discovers_and_caches_tools` — MCP tools discovered and cached after preload
- `test_multiple_servers_aggregated_tools` — Tools from multiple servers aggregated correctly
- `test_lifecycle_cleanup_clears_cache` — Cache cleared after close_connections

#### TestResilienceValidInvalidServers (3 tests)
- `test_valid_server_works_invalid_ignored` — Invalid server doesn't break valid ones
- `test_all_servers_fail_caches_empty_no_crash` — All servers failing = empty cache, no crash
- `test_load_mcp_tools_exception_handled` — Exception in tool loading handled gracefully

#### TestRestorePathMcpPreloaded (2 tests)
- `test_preload_skipped_if_instance_in_memory` — No re-preload for active instances
- `test_preload_on_restored_instance` — Restored instance gets MCP tools preloaded

#### TestEdgeCases (5 tests)
- `test_zero_mcp_servers_returns_empty_list` — No servers = empty tool list
- `test_server_raises_exception_graceful_fallback` — Exception = graceful fallback
- `test_tool_filter_mcp_in_deny_excludes_tools` — MCP in deny list excludes MCP tools
- `test_get_mcp_tools_unknown_instance_returns_empty` — Unknown instance = empty list
- `test_concurrent_preload_same_instance` — Concurrent preload doesn't race

#### TestLifecycleCleanup (3 tests)
- `test_close_connections_idempotent` — Close is safe to call multiple times
- `test_close_all_connections_clears_everything` — close_all cleans up everything
- `test_cache_isolation_between_instances` — Instances don't share caches

## ensure.md Validation

✅ **dev.sh runs 30s without crash**
- Daemon started successfully
- All subsystems initialized (compaction, migrations, worker pool, job recovery, etc.)
- Graceful shutdown completed cleanly
- No errors in startup logs

## Issues Found & Fixed

### Issue 1: Test mocks using old `spawn_instance` method
- **Root cause**: MCP integration changed `spawn_instance` → `spawn_instance_with_mcp` in code, but 114 tests still mocked the old method
- **Fix**: Updated all affected tests to mock `spawn_instance_with_mcp`
- **Files**: test_api.py, test_manager.py, test_progressive_dispatch.py, test_scheduler_api.py, test_sources_registry.py, test_sources_mapper.py, test_job_processor*.py, test_completion_registry.py, test_knowledge_tools.py, test_migration_api_comprehensive.py
- **Commit**: `d195b5a`

### Issue 2: Mock instance metadata missing `project_id` attribute
- **Root cause**: `_get_project_id()` accesses `instance_meta.project_id` (model attribute), but mock only set `instance_metadata` dict
- **Fix**: Added `mock_instance_meta.project_id = VALUE` alongside `mock_instance_meta.instance_metadata = {...}`
- **Files**: tests/unit/tools/test_knowledge_tools.py
- **Commit**: `d195b5a`

### Issue 3: Migration file naming conflict
- **Root cause**: Two migration files shared the same `_000001_` prefix
- **Fix**: Renamed `paused_at` migration to `_000002_`
- **Files**: daemon/migrations/versions/
- **Commit**: `d195b5a`

### Issue 4: Missing runtime import for McpService
- **Root cause**: `McpService` import was inside `TYPE_CHECKING` block only (type-checking, not runtime)
- **Fix**: Added runtime import inside `__init__` method
- **Files**: daemon/manager.py
- **Commit**: `d195b5a`

### Issue 5: sources/mapper still using old spawn_instance
- **Root cause**: Code change was missing in mapper.py
- **Fix**: Changed `self.manager.spawn_instance` → `await self.manager.spawn_instance_with_mcp`
- **Files**: daemon/sources/mapper.py
- **Commit**: `d195b5a`

## Overall Status

✅ **READY** — All 161 MCP tests pass, all unit tests pass, dev.sh runs without crash
