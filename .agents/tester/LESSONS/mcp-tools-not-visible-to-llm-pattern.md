# Lesson: MCP Tools Not Visible to LLM — Root Cause Pattern

**Date:** 2026-05-20
**Branch:** fix/mcp-tools-not-available-to-llm

## Root Cause
Two related bugs:
1. **`resolve_tool_filter()` called without `all_tool_names`** — When the "mcp" tool category was expanded, it had no MCP tool names to expand into. The function was called in loader, help tool, and instance tool without passing the MCP names.
2. **Cache key mismatch** — `_get_system_prompt_tokens()` looked up cache without MCP names in key → returned 0 for instances with MCP tools → cache miss → regenerated prompt (but still without MCP tools).

## Pattern to Watch
When adding a new parameter that must be threaded through multiple function calls:
- Trace ALL call sites of the affected function
- Check cache key computation includes the new parameter
- Verify the new parameter appears in the system prompt output
- Test that API responses expose the new data

## Bonus Fix
The same root cause also fixed 2 pre-existing failures in `test_gaia_agent.py` — those tests were failing because `resolve_tool_filter()` couldn't expand the "mcp" category, which was the exact same bug.

## Verification Method
E2E test approach was effective:
1. Direct API call to check `mcp_tool_names` field (PRIMARY verification)
2. Send message to LLM asking about tools (SECONDARY verification)
3. Both confirm the fix works end-to-end
