## Test Report: MCP Stdio Connection Root Cause Fix
Date: 2026-05-20T18:01+07:00
Branch: fix/mcp-stdio-connection-init
Commits: 3981088, 9ef15d7

### Summary
- **MCP Unit Tests**: 147/147 PASSED ✅
- **E2E Startup**: PASS ✅
- **Regression Check**: 4,162 passed, 20 pre-existing failures (unrelated), 27 skipped
- **ensure.md**: ✅ dev.sh runs 30s without crash
- **Quick Fixes Applied**: 1 (conftest.py mock fix)

### Root Cause Fixed
`ClientSession` was created without entering its async context manager, so the internal receive loop never ran — requests were sent but responses never received. Fixed by introducing `ManagedClientSession` with explicit `start()`/`stop()` lifecycle.

### MCP Unit Test Results

| Test File | Total | Passed | Failed | Status |
|-----------|-------|--------|--------|--------|
| test_mcp_warmup_pool.py | 40 | 40 | 0 | ✅ PASS |
| test_mcp_connection_manager.py | 19 | 19 | 0 | ✅ PASS |
| test_mcp_service.py | 25 | 25 | 0 | ✅ PASS |
| test_mcp_runtime_integration.py | 16 | 16 | 0 | ✅ PASS |
| test_context7_builtin.py | 25 | 25 | 0 | ✅ PASS |
| test_mcp_tool_filter.py | 22 | 22 | 0 | ✅ PASS |
| **TOTAL** | **147** | **147** | **0** | **✅ PASS** |

### E2E Startup Verification (ensure.md)

#### Daemon Status
- Started: **yes**
- Ran for 30s: **yes** (no crash)

#### MCP Warmup Results
- context7: ✅ SUCCESS — `Warmed up pool for 'context7' (1/1 connections)`
- webfetch: ✅ SUCCESS — `Warmed up pool for 'webfetch' (1/1 connections)`

#### Health Checks
- context7: `healthy: True, available: 1`
- webfetch: `healthy: True, available: 1`

#### Errors Found
- **NONE** — No TimeoutError, no ManagedClientSession errors, no crashes

#### Log Excerpt
```
17:58:20 - daemon.manager - INFO - Bootstrapping 2 built-in MCP servers...
17:58:20 - daemon.manager - INFO - MCP warm-up pool initialized: 2 server(s) registered, warmup running in background
Context7 Documentation MCP Server v2.2.5 running on stdio
17:58:22 - daemon.mcp.warmup_pool - INFO - Warmed up pool for 'webfetch' (1/1 connections)
17:58:22 - daemon.mcp.warmup_pool - INFO - Warmed up pool for 'context7' (1/1 connections)
17:58:22 - daemon.mcp.warmup_pool - INFO - MCP warmup complete: 2 server(s) ready
17:58:22 - daemon.manager - INFO - MCP warm-up pool ready: {'webfetch': {'available': 1, 'pool_size': 1, 'healthy': True}, 'context7': {'available': 1, 'pool_size': 1, 'healthy': True}}
```

#### Cleanup
- Daemon stopped: yes
- Orphaned processes: 0

### Regression Check
- **4,162 passed** | 20 pre-existing failures | 27 skipped
- Pre-existing failures unrelated to MCP stdio fix:
  - test_jober_watch_integration.py (1)
  - test_persistence.py (12)
  - test_nudge_behavior.py (3)
  - test_webfetch_builtin.py (2)

### Quick Fixes Applied
- `07beba7` — test: fix is_mcp_tool mock to match actual implementation
  - Fixed `tests/conftest.py` mock to correctly check for underscore after `mcp_` prefix

### Files Changed
- `daemon/mcp/managed_session.py` (NEW) — ManagedClientSession with start()/stop() lifecycle
- `daemon/mcp/warmup_pool.py` — Uses ManagedClientSession
- `daemon/mcp/connection_manager.py` — Uses ManagedClientSession
- `tests/unit/test_mcp_warmup_pool.py` — Tests for warmup pool
- `tests/unit/test_mcp_connection_manager.py` — Tests for connection manager
- `tests/conftest.py` — Quick fix for is_mcp_tool mock

### Overall Status
- Unit Tests: ✅ PASS (147/147 MCP tests)
- E2E Startup: ✅ PASS (daemon runs 30s, both MCP servers warm up successfully)
- ensure.md: ✅ PASS
- **Testing Complete: ✅ READY**
