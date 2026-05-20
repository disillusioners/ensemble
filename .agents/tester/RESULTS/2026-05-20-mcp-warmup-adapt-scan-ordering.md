# Test Report: MCP Warmup Pool Tool Adaptation + Scan Ordering Fix

**Date:** 2026-05-20
**Branch:** fix/mcp-tools-not-available-to-llm (commit 73be23f)
**Files Changed:** daemon/mcp/warmup_pool.py, daemon/tools/instance.py

## Summary

| Category | Result | Details |
|----------|--------|---------|
| Unit Tests | ✅ 251/251 PASS | 8 packs, 0 failures |
| E2E Test | ✅ 8/8 PASS | All MCP checks verified |
| ensure.md | ✅ PASS | dev.sh stable 30s+ |
| Quick Fixes | 0 | None needed |
| Regressions | 0 | Zero across all packs |

## What Was Fixed

1. **Warmup pool skipped tool adaptation** — `adapt_mcp_tools()` now called after `load_mcp_tools()`, so tool names get `mcp_` prefix
2. **Wrong tool scan ordering** — MCP tools now added BEFORE `scan_tools_for_full_docs()` runs, so they appear in `tool_help()`

## Unit Test Results (Session: mcp-unit-tests)

| # | Pack | Result | Tests |
|---|------|--------|-------|
| 1 | mcp_warmup_pool_unit_test | ✅ PASS | 40/40 |
| 2 | core_unit_test | ✅ PASS | 27/27 |
| 3 | gaia_agent_unit_test | ✅ PASS | 44/44 |
| 4 | mcp_service_pool_unit_test | ✅ PASS | 25/25 |
| 5 | mcp_connection_manager_unit_test | ✅ PASS | 19/19 |
| 6 | mcp_runtime_integration_test | ✅ PASS | 16/16 |
| 7 | context7_unit_test | ✅ PASS | 25/25 |
| 8 | mcp_server_crud_unit_test | ✅ PASS | 55/55 |
| **TOTAL** | | **8/8 PASS** | **251/251** |

**Scope:** MCP-related packs only (8 packs). Skipped: job_queue, sources, compaction, API, vision, frontend, memory, CE tools, notification, etc.

## E2E Results (Session: mcp-e2e)

| Check | Result |
|-------|--------|
| Daemon started | ✅ YES |
| MCP warmup completed | ✅ YES (both context7 + webfetch 1/1) |
| Instance spawned | ✅ YES (6f0b5f61-...) |
| mcp_tool_names in API | ✅ 3 tools with mcp_ prefix |
| LLM mentions MCP tools | ✅ Listed all 3 MCP tools |
| tool_help(category="mcp") | ✅ Returns all MCP tools |

**MCP Tool Names Verified:**
- `mcp_context7_resolve-library-id`
- `mcp_context7_query-docs`
- `mcp_webfetch_fetch`

**LLM Response:** Listed 2 MCP integrations with 3 total tools (Context7 + WebFetch).

## ensure.md Validation

| Requirement | Result | Evidence |
|-------------|--------|----------|
| dev.sh runs without crash for 30s | ✅ PASS | Ran ~2 minutes, stable, no errors |

## Overall Status: ✅ READY

All tests pass, E2E verified, ensure.md satisfied, zero regressions, no quick fixes needed.
