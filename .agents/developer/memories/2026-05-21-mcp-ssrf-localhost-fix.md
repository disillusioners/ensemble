# MCP SSRF Validation Fix — Localhost Allowed by Default

## Date: 2026-05-21

## Problem
MCP server config validation blocked localhost/loopback/private IPs by default (`MCP_ALLOW_LOCAL` defaulted to `"false"`). This prevented users from adding local MCP servers (e.g., `http://localhost:4123/v1/mcp/...`) without setting an environment variable.

## Root Cause
SSRF validation was baked into Pydantic `@field_validator` on `McpSseConfig` and `McpStreamableHttpConfig` models. The validators ran on ALL operations (create, update, test-connection). Default was to block localhost.

## Fix
Changed default from `"false"` to `"true"` — localhost/loopback/private IPs are now ALLOWED by default. Only link-local (169.254.x.x, cloud metadata) and reserved IPs remain always blocked. Users wanting strict mode can set `MCP_ALLOW_LOCAL=false`.

## Files Changed
- `daemon/mcp/config.py` — default changed, error messages updated
- `tests/unit/test_mcp_config.py` — tests updated for new defaults + strict mode tests added
- `tests/unit/test_mcp_test_connection.py` — tests updated for new defaults + strict mode tests added
- `tests/unit/conftest.py` — added `strict_local` fixture

## Commit
`258b801` on `feature/fix-mcp-localhost-block`

## Key Lesson
SSRF protection for MCP servers should be developer-friendly by default — localhost is the primary use case. Cloud metadata (169.254.169.254) is the real danger, not localhost.
