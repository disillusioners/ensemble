# Phase 1 MCP Runtime Integration — Implementation Notes

## What was delivered
- `pyproject.toml`: Added `mcp>=1.0.0` and `langchain-mcp-adapters>=0.1.0`
- `daemon/mcp/__init__.py`: Public API re-exports
- `daemon/mcp/config.py`: Pydantic config models for stdio/SSE/streamable-http with discriminated union + `validate_mcp_server_config()`
- `daemon/mcp/connection_manager.py`: `McpConnectionManager` with lazy asyncio.Lock, parallel connections via asyncio.gather(), per-server 5s timeout, module-level singleton via `get_mcp_connection_manager()`
- `daemon/mcp/tool_adapter.py`: `mcp_tool_name()`, `is_mcp_tool()`, `adapt_mcp_tools()`, `_slugify()` utilities
- `daemon/routers/mcp_servers.py`: Config validation at create/update endpoints with HTTP 422 on invalid config

## Key findings during implementation
1. **MCP SDK `streamablehttp_client` returns 3-tuple** (read, write, get_session_id_callback) — NOT 2-tuple like `sse_client`. Review caught this bug.
2. **MCP SDK `sse_client` returns 2-tuple** — just (read, write)
3. **MCP SDK `stdio_client`** uses `StdioServerParameters` and returns context manager
4. All 55 MCP CRUD tests pass after updates (tests now use valid transport configs)
5. Pre-existing test failure in `test_inner_soul_remember` (migration conflict) — unrelated

## Commit
- Hash: `ba8caea`
- Branch: `feature/mcp-runtime-integration`
