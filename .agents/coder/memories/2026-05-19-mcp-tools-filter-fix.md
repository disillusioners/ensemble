# MCP Tools Fix — Tool Filter Exclusion Bug

## Problem
Spawned agent instances reported ZERO MCP tools available, even though MCP servers (like Context7) were configured and loaded at startup.

## Root Cause
**Chicken-and-egg problem in `_apply_tool_filter()`** (`daemon/tools/instance.py`):

1. Agent `meta.json` files have explicit `tools.allow` lists (e.g., `["bash", "filesystem", "time", ...]`)
2. MCP category expansion only works if `"mcp"` is already in the allow list
3. Since no agent had `"mcp"` in their allow list, MCP tools were always filtered out

## Fix (Commit: dde10d1)
- **`_apply_tool_filter()`**: After resolving tool filter, always add MCP tools to the allowed set (using `is_mcp_tool()` from `daemon.mcp.tool_adapter`)
- MCP tools can still be explicitly denied via `deny=["mcp"]` or individual tool names
- **`_load_mcp_tools()`**: Improved logging (info/debug/warning levels)
- **`mcp_service.preload_mcp_tools()`**: Better error context in logs

## Architecture Notes
- MCP tool flow: `preload_mcp_tools()` → `_tools_cache` → `get_mcp_tools()` → `_load_mcp_tools()` → `_apply_tool_filter()`
- Tool names follow `mcp_{slugified_server}_{tool_name}` pattern
- Context7 is a built-in MCP server (`npx -y @upstreamapi/context7-mcp`)
- `resolve_tool_filter()` has MCP category expansion but it only works if "mcp" is already in tool categories
