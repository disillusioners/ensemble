# Quick Fix: MCP KB Server Init Order & E2E Test API Update

**Date**: 2026-05-29
**Branch**: feature/mcp-kb-tools

## Issue 1: KB MCP Server Initialization Order
- **Symptom**: E2E tests fail — MCP endpoints not accessible
- **Root Cause**: `create_kb_mcp_server()` was called during lifespan startup, but `app.mount()` calls for SSE/StreamableHTTP needed the MCP instance during `create_app()`. Starlette's mount registration happens at app creation time.
- **Fix**: Moved `create_kb_mcp_server()` call from lifespan to `create_app()`, before mount calls.
- **Commit**: `4b9dd65`

## Issue 2: E2E conftest.py needed MCP SDK unloading
- **Symptom**: E2E tests couldn't import MCP SDK
- **Root Cause**: Root `conftest.py` mocks `mcp` module for unit tests. E2E tests need the real SDK.
- **Fix**: Added `pytest_configure` hook in `tests/e2e/conftest.py` to unload mocked MCP modules.
- **Commit**: `4b9dd65`

## Issue 3: Deprecated MCP SDK API
- **Symptom**: Test used deprecated import
- **Root Cause**: `streamablehttp_client` was deprecated in newer MCP SDK versions
- **Fix**: Updated to `streamable_http_client`
- **Commit**: `79b565e`
