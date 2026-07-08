# OpenSpace MCP Phase 2 Timeout — Testing Lessons

## Date: 2026-07-08

### Feature Overview
Phase 2 adds per-server tool_call_timeout override for OpenSpace MCP (900s instead of default 120s). The implementation spans warmup_pool.py, mcp_service.py, manager.py, openspace.py, and base.py across both STDIO and HTTP/SSE transport paths.

### Key Findings

#### 1. timeout=0 Sentinel Pattern (CRITICAL)
The implementation correctly handles `0` as a sentinel meaning "disable timeout wrapping" — NOT "use default". This is enforced via `is not None` checks at all 7 gating sites:
- warmup_pool.py (2 sites)
- tool_adapter.py (3 sites)
- mcp_service.py (2 sites)

A truthy check (`if tool_call_timeout:`) would silently replace 0 with the default 120s, which is WRONG. The test `test_register_server_with_zero_timeout_preserved` explicitly guards against this regression.

#### 2. Per-Server Timeout Architecture
- **Storage**: `_tool_call_timeouts: dict[str, int]` in warmup pool
- **Lookup**: `dict.get(server_name, default)` — returns stored 0, falls back to pool default for missing keys
- **Resolution in cold path**: `_get_per_server_timeout()` looks up builtin definition via `getattr()` (resilient to subclasses without the property)
- **Both paths consistent**: STDIO and HTTP/SSE both use `is not None` checks

#### 3. Backward Compatibility Verified
- Base class `tool_call_timeout` property returns None by default
- Only OpenSpace overrides (900s); webfetch and context7 inherit None → 120s default
- `register_server()` signature adds `tool_call_timeout=None` as optional param — no breaking change

#### 4. Full Suite Execution Challenge
The full 8657-test non-integration suite takes 25+ minutes, exceeding the 10-minute opencode session window. Strategy: use targeted test runs (MCP + job_queue + core daemon) instead of full suite for Phase-scoped verification. The 484 MCP tests run in ~58 seconds.

#### 5. Pre-Existing SQLite Concurrency Failures
Two concurrency tests fail intermittently under SQLite when run as part of the full suite, but pass in isolation:
- `test_concurrent_terminal_writes_only_one_succeeds` (SQLite InterfaceError)
- `test_atomic_retry_concurrent_calls_only_one_succeeds` (flaky under load)

These are NOT related to Phase 2 and are documented as known SQLite threading limitations.

### Test Coverage
- 484 MCP tests pass (12 test files covering warmup pool, tool timeout, builtin servers, OpenSpace, context7, webfetch, mcp_service, server CRUD, runtime integration, stdio timeout, lazy init, skill loading)
- 148 core daemon tests pass (manager, loader, config)
- 1328 job_queue tests pass (1 flaky pre-existing)
- Total verified: ~2000 tests, zero MCP regressions

### Architecture Pattern Worth Remembering
The per-server timeout pattern is clean and extensible:
1. Define timeout as a `@property` on the builtin server definition
2. Warmup pool stores it in a per-server dict during `register_server()`
3. Cold path looks it up via `_get_per_server_timeout()` helper
4. Both paths use `is not None` to preserve the 0 sentinel

This pattern could be reused for other per-server configuration overrides (e.g., pool size, retry count).
