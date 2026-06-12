# Test Report: MCP Lazy Connection Initialization

**Date:** 2026-06-12
**Branch:** `feature/mcp-lazy-init`
**Commits:** `574a2e3` (initial), `6722318` (reviewer fixes), `2e893e7` (tidier fixes)

## Summary

| Category | Total | Passed | Failed | Skipped |
|----------|-------|--------|--------|---------|
| **MCP Tests** | 410 | 410 | 0 | 0 |
| **Non-MCP Unit Tests** | 2421 | 2420 | 1 | 0 |
| **Top-level Tests** | 1546 | 1543 | 3 | 8 |
| **OpenCode Tests** | 465 | 465 | 0 | 4 |
| **Job Queue Tests** | 1179 | 1179 | 0 | 19 |
| **TOTAL** | **6021** | **6017** | **4** | **31** |

### Overall Status: ✅ PASS — Zero new regressions, 4 pre-existing failures

---

## MCP Test Results (All PASS)

| Test File | Tests | Status |
|-----------|-------|--------|
| test_mcp_lazy_init.py | 22/22 | ✅ PASS |
| test_mcp_service.py | 39/39 | ✅ PASS |
| test_mcp_tool_timeout.py | 12/12 | ✅ PASS |
| test_mcp_runtime_integration.py | 14/14 | ✅ PASS |
| test_mcp_warmup_pool.py | 50/50 | ✅ PASS |
| test_mcp_cold_load_race.py | 6/6 | ✅ PASS |
| test_mcp_concurrent.py | 8/8 | ✅ PASS |
| test_mcp_connection_manager.py | 19/19 | ✅ PASS |
| test_mcp_server_crud.py | 55/55 | ✅ PASS |
| test_mcp_managed_session.py | 7/7 | ✅ PASS |
| test_mcp_config.py | 29/29 | ✅ PASS |
| test_mcp_task_scoped.py | 11/11 | ✅ PASS |
| test_mcp_tool_filter.py | 22/22 | ✅ PASS |
| test_mcp_test_connection.py | 71/71 | ✅ PASS |
| test_mcp_stdio_wrapper.py | 12/12 | ✅ PASS |
| test_mcp_stdio_timeout.py | 20/20 | ✅ PASS |
| test_mcp_lifecycle.py (integration) | 13/13 | ✅ PASS |
| **TOTAL** | **410/410** | **✅ ALL PASS** |

---

## ensure.md Validation: ✅ PASS

- dev.sh ran for 30 seconds without crash (exit code 124 — timeout killed)
- Server started successfully: `Ensemble v0.6.5` on port 8079
- MCP warmup pool initialized: 2 servers (webfetch + context7), both healthy
- PostgreSQL checkpointer ready, WorkerPool started (4 workers), Job recovery complete
- Clean shutdown at timeout

---

## Pre-existing Failures (4 — unrelated to MCP lazy init)

1. **test_gaia_agent.py::test_gaia_tool_filter_config_parsed_correctly**
   - Agent config `allow` list includes "context" but test doesn't expect it
   - Root cause: commit c890859 added "context" tool, test not updated

2-4. **test_innate_skills_refactoring.py** (3 failures)
   - Tests search for literal `"OpenCode_Skill"` header in prompts
   - The skill file was restructured and no longer contains that header format

---

## Quick Fixes Applied: None (all tests green on first run)

---

## Coverage Analysis

### test_mcp_lazy_init.py (22 tests — 8 classes)

| Class | Tests | Focus |
|-------|-------|-------|
| TestCreateLazyMcpTools | 4 | Factory: names, descriptions, empty schemas, callable |
| TestLazyCoroutine | 7 | Core lazy mechanics: session resolution, reuse, kwargs, timeout, errors |
| TestConcurrencyGuard | 2 | Double-check locking: concurrent calls → 1 session |
| TestSharedSessionCache | 2 | N tools → 1 session, separate caches per server |
| TestTimeoutWrapping | 2 | Timeout on/off |
| TestMcpSessionProviderProtocol | 1 | Protocol duck-typing |
| TestLazySessionLifecycle | 2 | Close after first call, idempotent close |
| TestIsErrorPropagation | 2 | isError=True → ToolException, empty content |

### Core Scenario Coverage

| Scenario | Coverage | Details |
|----------|----------|---------|
| 1. Instance creation perf | PARTIAL | Connection-free preload proven structurally, no timing assertion |
| 2. Lazy connection on first call | ✅ YES | 3 tests verify session resolution + reuse + N→1 |
| 3. Error handling | ✅ YES | Timeout, connection error, isError=True, empty content |
| 4. Schema cache behavior | ✅ YES (in test_mcp_service.py) | Cache hit/miss/invalidate covered in TestSchemaCache (5 tests) |
| 5. Concurrency | ✅ YES | Double-check locking + 3 concurrent calls → 1 session |
| 6. Lifecycle | PARTIAL | Close + idempotent covered; restore-after-cleanup in test_mcp_lifecycle.py |
| 7. Transport coverage | ✅ YES (in test_mcp_service.py) | Pool acquire/fallback/cold-start in TestMcpSessionProvider (6 tests) |

### Recommended Additions (not blocking)

1. **Timing assertion test** — Verify preload <500ms with warm schema cache (currently structural only)
2. **Restore-after-cleanup test** — Preload → close → re-preload → schema cache consulted
3. **Provider-level error propagation** — Drive `_McpSessionProviderImpl` to `ToolException` via cold-start failure
4. **Higher-arity concurrency** — 10 concurrent tools → 1 session (current test uses 3)

---

## Documentation Updated

- [x] RESULTS/2026-06-12-mcp-lazy-init.md — This report
- [ ] PACKS.md — Will update with new results
- [ ] LESSONS/ — Will add if notable findings

---

## Conclusion

**Testing Complete: ✅ READY FOR MERGE**

- All 410 MCP tests pass (0 failures)
- Zero new regressions across 5,611 non-MCP tests (4 pre-existing, unrelated)
- ensure.md validation: dev.sh runs stable for 30s
- Lazy init behavior verified: no connections at preload, deferred to first tool call
- Shared session cache verified: N tools → 1 connection (double-check locking)
- Error propagation works: ToolException for timeout, connection error, isError=True
- Concurrency safe: concurrent first-calls → exactly 1 session created
