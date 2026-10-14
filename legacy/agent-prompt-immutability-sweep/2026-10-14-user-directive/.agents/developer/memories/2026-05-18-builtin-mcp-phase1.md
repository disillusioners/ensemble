# Phase 1: Built-in MCP Server Backend Framework — Implementation Notes

## Date: 2026-05-18

## What Was Built
Phase 1 of built-in MCP server support for agents-ensemble project. Full backend framework including:
- DB migration (3 new columns on mcp_servers)
- SQLModel updates
- Pydantic API models (ConfigSchemaField, BuiltinServerTemplate, etc.)
- BuiltinServerDefinition ABC with generic build_config/parse_config
- Registry singleton
- Validation helper
- Repository extensions (pure DB layer)
- Router: 403 protection + 3 new endpoints
- Manager bootstrap with schema drift detection
- 57 new tests

## Key Architecture Decisions
- **Repository = pure DB layer** — absolutely NO imports from daemon.mcp.builtin_servers
- **Router/Manager = orchestration** — calls both registry and repo
- **build_config/parse_config on ABC** — generic algorithms, subclasses can override
- **`from __future__ import annotations`** needed in mcp_server.py for forward references

## Critical Bugs Caught in Review
1. **Forward reference crash**: McpServerInfo referenced ConfigSchemaField before it was defined → fixed with `from __future__ import annotations`
2. **Route shadowing**: `/builtin-templates` defined after `/{server_id}` → FastAPI matched "builtin-templates" as server_id → fixed by reordering routes (static paths first)
3. **Naming collision**: McpConfigValidationError exists in both MCP SDK and our validation module → aliased as BuiltinConfigValidationError
4. **Error response inconsistency**: 403 responses used inline dicts instead of ErrorResponse → standardized

## Implementation Stats
- 20 files changed, +2649/-3 lines
- 57 new tests (all passing)
- 55 existing tests preserved (no regressions)
- Commit: fac6e0c
