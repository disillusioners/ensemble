# Phase 2 MCP Service Layer — Implementation Notes

## What was delivered
- `daemon/services/mcp_service.py`: McpService class with async preload, sync cache read, connection lifecycle
- `daemon/manager.py`: McpService wired in __init__ with TYPE_CHECKING import, shutdown in proper sequence
- `daemon/tools/_tool_registry.py`: "mcp" category added to CATEGORY_MODULES
- `daemon/tools/mcp_tools.py`: Category metadata stub (CATEGORY_NAME, CATEGORY_DOC)
- `daemon/tools/instance.py`: resolve_tool_filter() with all_tool_names param for dynamic MCP tool filtering
- `tests/test_tool_filter.py`: 12 new MCP filtering tests

## Key findings during implementation
1. Services use `from daemon.mcp import ...` (absolute imports), not relative `from ..mcp import`
2. Manager services initialized in __init__ after internal state, stored as instance attributes
3. Shutdown sequence in Manager uses ordered steps; MCP shutdown placed after event bus shutdown, before cleanup
4. Tool filter: `resolve_tool_filter()` got new `all_tool_names` param with None default for backward compatibility
5. MCP category expansion uses prefix matching (tools starting with "mcp_" and containing ≥2 underscores)

## Commit
- Hash: `3ec7884`
- Branch: `feature/mcp-runtime-integration`
- 6 files changed, 418 insertions
