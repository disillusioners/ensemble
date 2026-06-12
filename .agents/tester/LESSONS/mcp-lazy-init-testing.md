# MCP Lazy Init Testing Lessons

**Date:** 2026-06-12
**Branch:** feature/mcp-lazy-init
**Commits:** 574a2e3, 6722318, 2e893e7

## Test Architecture

The lazy init feature is tested across 3 files with clear separation:

1. **test_mcp_lazy_init.py** (22 tests) — Building blocks in `tool_adapter.py`:
   - Factory (create_lazy_mcp_tools): names, descriptions, empty schemas
   - Lazy coroutine (_build_lazy_coroutine): session resolution, reuse, timeout, errors
   - Double-check locking (concurrent → 1 session)
   - Shared session cache (N tools → 1 session)
   - isError propagation (ToolException)
   - Lifecycle hooks (close connections)

2. **test_mcp_service.py** (39 tests) — Service-level logic:
   - Schema cache: hit/miss/invalidate/concurrent double-check
   - _McpSessionProviderImpl: pool acquire, cold fallback, transfer_session failure
   - Preload rewrite: no connections during preload

3. **test_mcp_lifecycle.py** (13 tests) — Integration:
   - Spawn with MCP tools, verify lazy injection
   - Restore after terminate
   - Unreachable server resilience
   - Cleanup closes connections

## Key Patterns

### Conftest Mock Pop + Reimport Pattern
Tests need real `tool_adapter.py` but conftest mocks `daemon.mcp.*`. Solution: pop mock from `sys.modules`, reimport real module, test, then restore mock in teardown.

### Schema Cache vs Session Cache
Two separate caches:
- **Schema cache** (module-level on McpService): `get_schemas_for_server()` — populated during preload, no connections
- **Session cache** (per-instance dict): `_session_caches[instance_id][server_name]` — populated on first tool call, real connections

### Double-Check Locking Pattern
`_build_lazy_coroutine` uses `asyncio.Lock` per server + shared dict. Lock ensures exactly 1 connection per server even under concurrent first-calls. Tests verify with 3+ concurrent coroutines.

## Coverage Gaps (Non-Blocking)

1. No timing assertion for preload <500ms with warm cache (structural only)
2. No restore-after-cleanup test verifying schema cache reuse
3. No provider-level cold-start failure propagation test (only session-level)
4. No transport-specific tests (STDIO vs SSE vs HTTP all go through same connect_instance)

These are non-blocking because the feature works correctly — they would strengthen the test surface for regression protection.

## Pre-existing Failures (4)

Unrelated to MCP lazy init:
1. test_gaia_agent.py: agent config `allow` list includes "context" but test doesn't expect it
2-4. test_innate_skills_refactoring.py: skill file restructured, header format changed
