# MCP Tools Warmup Pool Fix

## Date: 2026-05-21

## Problem
MCP tools (context7, webfetch) were not visible to LLM instances despite being correctly configured. Instance metadata showed tool names but tool_help() returned nothing in the MCP category.

## Root Causes (TWO bugs)

### Bug 1: Warmup pool didn't adapt MCP tools with prefix
- File: `daemon/mcp/warmup_pool.py:222-224`
- The `_create_pooled_connection()` method called `load_mcp_tools()` directly without `adapt_mcp_tools()`
- Cold-start path correctly used `adapt_mcp_tools()` but pooled path bypassed it
- Result: tools had raw names like "fetch" instead of "mcp_webfetch_fetch"
- Fix: Added `tools = adapt_mcp_tools(server_name, tools)` after load

### Bug 2: MCP tools not scanned into tool metadata registry
- File: `daemon/tools/instance.py:645-664`
- In `create_instance_tools()`, the order was: create help tool → scan → add MCP tools
- MCP tools were added AFTER `scan_tools_for_full_docs()`, so they never got registered in `_tool_metadata`
- `tool_help()` reads from `_tool_metadata` via `list_tools_by_category()` — it never saw MCP tools
- Fix: Reorder to: add MCP tools → create help tool → scan all tools → apply filter

## Key Learning
The tool visibility chain: `_tool_metadata` registry → `list_tools_by_category()` → `tool_help()` → system prompt. If tools aren't in `_tool_metadata`, they're invisible to the LLM even if they're bound via `bind_tools()`.

## Commit: 73be23f
