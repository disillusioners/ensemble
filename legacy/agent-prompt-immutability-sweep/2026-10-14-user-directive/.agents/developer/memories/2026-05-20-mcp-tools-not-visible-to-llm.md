# MCP Tools Not Visible to LLM — Root Cause & Fix

## Problem
After MCP warmup pool connections were fixed (context7 & webfetch connect successfully), spawned instances still couldn't use MCP tools. The log showed "Injecting 3 MCP tools" but the LLM only knew about `tool_help`.

## Root Cause
**MCP tools were properly bound to the LLM via `bind_tools()`, but the system prompt and `tool_help` never mentioned them.** The LLM relies on the system prompt for tool awareness.

### Why
Two places call `resolve_tool_filter()` WITHOUT `all_tool_names`:
1. `daemon/loader.py:load_tools_doc_for_agent()` — builds system prompt's tool section
2. `daemon/tools/help.py:_get_allowed_tools()` — builds tool_help's tool list

Without `all_tool_names`, `resolve_tool_filter()` can't expand the "mcp" category to include actual MCP tool names (e.g., `mcp_webfetch_fetch`). The category stays empty and no MCP tools are included in the prompt.

### Contrast
`daemon/tools/instance.py:_apply_tool_filter()` DOES pass `all_tool_names`, so actual tool binding works fine. It's only the **awareness** (system prompt + tool_help) that was broken.

## Fix
Added `mcp_tool_names: list[str] | None = None` parameter through:
1. `daemon/tools/help.py` — `create_help_tool()` and `_get_allowed_tools()`
2. `daemon/tools/instance.py` — `create_instance_tools()` extracts MCP tool names and passes them
3. `daemon/loader.py` — `load_tools_doc_for_agent()`, `PromptCache._make_key()`, `load_and_cache_prompt()`
4. `daemon/services/instance_lifecycle.py` — `spawn_instance()` and `_restore_instance()` extract MCP tool names from service

Key design: MCP tool names are included in the prompt cache key to prevent stale cached prompts when MCP config changes.

## Key Insight
**Tool binding ≠ tool awareness.** Even if tools are properly passed to `bind_tools()`, the LLM won't use them if its system prompt doesn't mention them. Always check both paths: the binding path AND the prompt/documentation path.

## Additional Fix (Reviewer Feedback)
Two follow-up issues found and fixed:

### W2: `_get_system_prompt_tokens()` returns 0 for MCP instances
- `_get_mcp_tool_names()` was calling `get_mcp_tools(None)` — always `[]` because cache is keyed by instance_id
- Fixed: pass `instance_id` to read from correct cache key
- Store `mcp_tool_names` in `instance_metadata` dict for cache key reconstruction
- Both `manager.py` and `instance_messaging.py` now pass `mcp_tool_names` to `prompt_cache.get()`

### W4: E2E test too indirect
- Added `mcp_tool_names` field to `InstanceInfo` API response model
- Added `verify_mcp_tools_via_api()` step that checks GET /api/instances/{id} directly
- API now returns: `mcp_context7_resolve-library-id`, `mcp_context7_query-docs`, `mcp_webfetch_fetch`

## Test
E2E test at `tests/e2e/test_mcp_tools.py` verifies the full flow: start daemon → spawn instance → **check API response for MCP tool names** → ask LLM → verify MCP tools mentioned → cleanup.
