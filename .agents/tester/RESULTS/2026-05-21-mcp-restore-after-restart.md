# Test Results: MCP Tools on Restored Instances After Daemon Restart

**Date**: 2026-05-21
**Branch**: fix/mcp-tools-not-available-to-llm
**Commits**: `43e208b` (main fix), `e36d76e` (quick fix: docstring indentation)
**File Changed**: `daemon/manager.py`

## Summary

| Category | Result |
|----------|--------|
| E2E Restore Test | ✅ PASS (16/16 checks) |
| MCP Unit Tests | ✅ PASS (224/224) |
| Core Unit Tests | ✅ PASS (642/653 — 11 pre-existing failures unrelated) |
| ensure.md | ✅ PASS (dev.sh stable 41s+, no crashes) |
| Quick Fixes | 1 (docstring indentation in `ensure_mcp_preloaded()`) |
| **Overall** | ✅ **READY** |

## E2E Restore Test — The Critical Test

**Script**: `tests/e2e/test_mcp_tools_restore.py`

### Phase 1 — Initial Start
- ✅ Daemon started, healthy after warmup
- ✅ Instance created: `4f832637-3497-47dc-9a70-caa3deed77c0`
- ✅ MCP tools in API: `mcp_context7_resolve-library-id`, `mcp_context7_query-docs`, `mcp_webfetch_fetch`
- ✅ LLM response mentions MCP tools (context7, webfetch)

### Phase 2 — Restart
- ✅ Daemon stopped gracefully (SIGTERM)
- ✅ Port 8079 freed
- ✅ Daemon restarted and healthy again

### Phase 3 — Restore Verification (THE KEY TEST)
- ✅ Same instance_id used: `4f832637-3497-47dc-9a70-caa3deed77c0`
- ✅ MCP tools STILL present via API: same 3 tools
- ✅ LLM response STILL mentions MCP tools after restart
- ✅ Cleanup completed

### Root Cause Analysis
The original `ensure_mcp_preloaded()` had a simple early-return when the instance was already in memory:
```python
if instance_id in self.instances:
    return  # Skip — instance already loaded
```

This meant that instances restored from checkpoint (which don't have MCP tools cached) would never get their MCP tools re-preloaded on daemon restart.

The fix changes this to check if cached MCP tools actually exist:
```python
if instance_id in self.instances:
    if self._mcp_service:
        cached = self._mcp_service.get_mcp_tools(instance_id)
        if cached:
            return  # Has tools — truly no need to preload
    else:
        return  # No MCP service — nothing to preload
```

## Unit Test Results

| Pack | Total | Passed | Status |
|------|-------|--------|--------|
| mcp_warmup_pool_unit_test | 40 | 40 | ✅ PASS |
| mcp_connection_manager_unit_test | 19 | 19 | ✅ PASS |
| mcp_service_pool_unit_test | 25 | 25 | ✅ PASS |
| mcp_runtime_integration_test | 16 | 16 | ✅ PASS |
| context7_unit_test | 25 | 25 | ✅ PASS |
| mcp_server_crud_unit_test | 55 | 55 | ✅ PASS |
| gaia_agent_unit_test | 44 | 44 | ✅ PASS |
| core_unit_test | 653 | 642 | ⚠️ 11 pre-existing (langgraph import) |

### Pre-existing Failures (Unrelated)
11 failures in `tests/test_persistence.py::TestGetInstanceMessages` — `ModuleNotFoundError: No module named 'langgraph.checkpoint.base'`. This is a langgraph version compatibility issue, not related to this commit.

## Quick Fixes Applied
- `daemon/manager.py:1033-1044` — Fixed docstring indentation in `ensure_mcp_preloaded()` (commit `e36d76e`)

## ensure.md Validation
- dev.sh ran for 41+ seconds without crashing
- Health endpoint returned healthy throughout
- All startup components initialized correctly (MCP warmup pool, job processor, notification broadcaster)
- Port 8079 cleaned up properly after test

## Test Script Created
- `tests/e2e/test_mcp_tools_restore.py` — Full E2E test for MCP tools restoration after daemon restart
- Can be re-run for regression testing: `python tests/e2e/test_mcp_tools_restore.py`
