# MCP STDIO Connection Timeout Fix

## Problem
Both built-in MCP servers (Context7 via npx, WebFetch via uvx) started successfully but the STDIO connection manager timed out before completing the MCP handshake. The 5-second timeout was wrapping BOTH subprocess spawn (npx/uvx package resolution + download on cold start) AND the JSON-RPC handshake.

## Root Cause
- `per_server_timeout` was hardcoded to `5.0` seconds
- STDIO transport needs: subprocess spawn (8-15s cold start for npx, 5-10s for uvx) + JSON-RPC handshake (<1s)
- 5s was only enough for warm starts, never for cold starts

## Fix Applied
- Added `STDIO_DEFAULT_TIMEOUT = 30.0` constant for STDIO transport
- Increased default `per_server_timeout` from 5.0 to 15.0
- Made timeout configurable per-server via `McpStdioConfig.timeout` field
- SSE/Streamable HTTP transports still use 5s (unchanged)
- Improved error logging with command details and suggestions

## Files Changed
- `daemon/mcp/config.py` — Added optional `timeout` field to `McpStdioConfig`
- `daemon/mcp/connection_manager.py` — Transport-specific timeouts, improved logging

## Branch: `fix/mcp-stdio-timeout` from `latest`
## Commit: `f77c939`

## Architecture Note
The connection flow: `preload_mcp_tools()` → `connect_instance()` → `_create_session()` → `_create_stdio_session()` → `mcp.stdio_client()` (SDK) → subprocess spawn → `ClientSession.initialize()` → JSON-RPC handshake. The timeout wraps the entire flow, so it needs to account for subprocess spawn time.
