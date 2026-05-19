# MCP SDK Mocking in conftest.py

**Date**: 2026-05-19
**Feature**: MCP STDIO Server Warm-Up Pool
**Commit**: `d602430`

## Problem
Unit tests for MCP pool feature failed because the `mcp` SDK package is not installed in the test environment. Tests importing `daemon.mcp.warmup_pool` or `daemon.mcp.connection_manager` would fail with `ModuleNotFoundError` for `mcp`, `mcp.client.stdio`, etc.

## Solution
Added comprehensive MCP SDK mocks to `tests/conftest.py`:
- `mcp`, `mcp.client`, `mcp.client.sse`, `mcp.client.streamable_http`
- `mcp.client.stdio`, `mcp.server`, `mcp.server.stdio`, `mcp.types`
- `langchain_mcp_adapters.tools`
- Early mock injection into `sys.modules` before test collection

## Key Takeaway
When adding new features that import external SDKs, the conftest.py mock infrastructure must be updated to include those SDKs. This is a recurring pattern — check conftest.py first when tests fail with import errors.

## Also Fixed
- `mock_config` fixture needs `mcp_pool` attribute (new config section from pool feature)
- `adapt_mcp_tools` must be patched at its import location (`daemon.services.mcp_service`), not its source location
