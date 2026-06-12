# MCP Lazy Init E2E Debug — Three Issues Fixed

**Date:** 2026-06-12
**Commit:** fafb119

## Issues Found & Fixed

### 1. context7 tools invisible (dict args_schema)
- **Root cause:** `warmup_pool.py:get_cached_tool_schemas()` called `.schema()` on `args_schema` which can be a plain dict from langchain_mcp_adapters (not always a Pydantic model). Error caught by mcp_service.py silently dropped the entire server.
- **Fix:** Added `_extract_input_schema()` helper that handles None/dict/Pydantic/unexpected shapes.
- **Key insight:** `langchain_mcp_adapters` can return args_schema as dict OR Pydantic model depending on the MCP server implementation. Always handle both.

### 2. Disabled webfetch still warmed up
- **Root cause:** `_init_warmup_pool` only checked `existing.is_active=False` but bootstrap doesn't create DB records for env-disabled servers (`existing is None`). So the skip check never matched.
- **Fix:** Added explicit `is_builtin_disabled(name)` guard before pool registration.
- **Key insight:** There are THREE states for a builtin server: (1) enabled with DB record, (2) disabled with inactive DB record, (3) env-disabled with NO DB record. All three must be handled.

### 3. Instance creation timing
- After fixes: ~174ms, acceptable. Was slow before because context7 schemas failed and caused retries/fallbacks.

## Quick Fix Assessment
Both fixes were quick-fix eligible: < 20 lines, single file, obvious root cause, low risk.
