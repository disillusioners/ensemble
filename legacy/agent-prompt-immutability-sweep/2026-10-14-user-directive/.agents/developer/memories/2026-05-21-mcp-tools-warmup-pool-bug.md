# MCP Tools Warmup Pool Bug Fix

## Date: 2026-05-21

## Problem
MCP tools (context7, webfetch) were not available to the LLM despite being registered in instance metadata. The LLM reported "No MCP tools are currently connected" and tool_help() showed no MCP category.

## Root Causes (Two Bugs)

### Bug 1: Warmup Pool Missing Tool Adaptation
**File:** `daemon/mcp/warmup_pool.py:222-224`
- The warmup pool called `load_mcp_tools()` directly WITHOUT applying `adapt_mcp_tools()`
- This meant pooled tools had raw names like "resolve-library-id" instead of "mcp_context7_resolve-library-id"
- Cold-start path correctly applied adaptation, but pooled path skipped it

**Fix:** Added `tools = adapt_mcp_tools(server_name, tools)` after `load_mcp_tools()` call

### Bug 2: Tool Scan Ordering in create_instance_tools()
**File:** `daemon/tools/instance.py:645-664`
- MCP tools were added to the tools list AFTER `scan_tools_for_full_docs()` ran
- This meant MCP tools never got registered in `_tool_metadata`
- `list_tools_by_category()` (used by tool_help) reads from `_tool_metadata`

**Fix:** Reordered to: Add MCP tools → Create help tool → Scan ALL tools → Apply filter

## Key Architecture Insight
- MCP tools have TWO paths to the LLM: (1) bind_tools() for LLM invocation, (2) _tool_metadata registry for tool_help() listings
- Both paths must be consistent for MCP tools to be both usable AND visible
- The warmup pool and cold-start paths must apply identical transformations

## Verification
- Fresh instance correctly shows `mcp_tool_names: ["mcp_context7_resolve-library-id", "mcp_context7_query-docs", "mcp_webfetch_fetch"]`
- tool_help(category="mcp") lists all 3 MCP tools
- LLM correctly reports "16 tools (3 MCP + 13 built-in)"
- All 461 unit tests pass

## Commit
73be23f fix: MCP tools not available to LLM - warmup pool missing adapt + tool scan ordering
