# E2E Debug Report: MCP Lazy Init — Three Issues

**Date:** 2026-06-12
**Session:** mcp-lazy-e2e-debug (ses_1451acd88ffeKZ3HXNHnB01bPM)
**Commit:** fafb119 `fix(mcp): handle dict args_schema in warmup pool + skip disabled servers`

---

## Summary

All 3 issues diagnosed. 2 fixed (quick-fix). 1 was a non-issue after fixes applied.

| Issue | Severity | Status | Root Cause |
|-------|----------|--------|------------|
| context7 tools missing | CRITICAL | ✅ FIXED | `warmup_pool.py` calls `.schema()` on dict args_schema |
| webfetch warmup despite disabled | MEDIUM | ✅ FIXED | `_init_warmup_pool` doesn't check env-disabled servers |
| First instance slow | LOW | ✅ RESOLVED | 174ms after fixes — was 258ms before |

---

## Issue 1: context7 tools not appearing (CRITICAL) — FIXED

### Root Cause

**File:** `daemon/mcp/warmup_pool.py:328`

The `get_cached_tool_schemas()` method unconditionally called `tool.args_schema.schema()` to extract input schemas. When a server (context7) advertises schemas as plain dicts through `langchain_mcp_adapters`, that call raises:
```
AttributeError: 'dict' object has no attribute 'schema'
```
The error was caught by `mcp_service.py` which logged `"Schema lookup failed for 'context7'"` and **silently skipped the entire server's tools**. This caused context7's 3 tools to become invisible.

### Fix

Added `_extract_input_schema()` helper in `warmup_pool.py` that normalizes:
- `None` → `{}`
- `dict` → pass through (it IS the schema already)
- Pydantic model → call `.schema()`
- Unexpected shapes → fallback to `{}`

### Verification

Before fix:
```
Lazy-loaded 1 MCP tool schemas from 3 server(s)
Loaded 1 MCP tools: ['mcp_zai_web_search_web_search_prime']
```

After fix:
```json
"mcp_tool_names": [
    "mcp_zai_web_reader_webReader",
    "mcp_zai_web_search_web_search_prime",
    "mcp_context7_resolve-library-id",
    "mcp_context7_query-docs"
]
```
**4 MCP tools loaded** including both context7 tools.

---

## Issue 2: Disabled webfetch still gets warmup — FIXED

### Root Cause

**File:** `daemon/manager.py:_init_warmup_pool`

The `_init_warmup_pool` method only checked `existing.is_active=False` to skip a server. But the bootstrap code deliberately does NOT create a DB record for env-disabled servers (so `existing is None`), so the check never matched. The disabled server was still registered for pooling, which spawned an npx subprocess at startup.

### Fix

Added explicit `is_builtin_disabled(name)` guard at the top of the loop in `_init_warmup_pool`. Now logs: `"MCP server '{name}' disabled (env var), skipping warmup pool registration"`.

### Verification

Before fix:
```
Warmed up pool for 'webfetch' (1/1 connections)
```

After fix: No webfetch warmup line at all; only context7 is warmed.

---

## Issue 3: First instance creation timing — RESOLVED

### Result

- **After fixes:** 174ms instance creation time
- **Before fixes:** ~258ms
- **MCP tool count:** 4 tools loaded correctly

The warmup pool is working correctly after fixes. The remaining latency is normal instance-graph construction, not MCP-related.

---

## Tests Added (9 new tests)

### `tests/unit/test_mcp_warmup_pool.py`
- `test_handles_dict_args_schema` — the exact bug scenario
- `test_handles_pydantic_args_schema` — backward-compat
- `test_dict_args_schema_does_not_break_others` — mixed batch
- `TestExtractInputSchema` — 5 unit tests (None/dict/Pydantic/raising/unexpected)

### `tests/unit/test_builtin_mcp_servers.py`
- `test_warmup_pool_skips_disabled_servers` — disabled skipped, enabled registered
- `test_warmup_pool_registers_enabled_servers` — positive case

---

## Files Modified

| File | Change |
|------|--------|
| `daemon/mcp/warmup_pool.py` | New `_extract_input_schema` helper + use it in `get_cached_tool_schemas` |
| `daemon/manager.py` | `is_builtin_disabled` guard in `_init_warmup_pool` |
| `tests/unit/test_mcp_warmup_pool.py` | 7 new tests |
| `tests/unit/test_builtin_mcp_servers.py` | 2 new tests |

**Total:** 4 files changed, 255 insertions(+), 3 deletions(-)

---

## Post-Fix Verification (Live)

- ✅ Dev server running on port 8079
- ✅ Instance creation creates 4 MCP tools (was 1)
- ✅ context7 tools visible: `resolve-library-id`, `query-docs`
- ✅ No `Schema lookup failed` warnings
- ✅ No webfetch warmup (disabled server properly skipped)
- ✅ Instance creation time: ~174ms (acceptable)

---

## Action Items

None — all issues resolved and committed.
