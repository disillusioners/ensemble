# Plan Overview: Integrate Context7 as Built-in MCP Server

## Objective
Add Context7 (`@upstreamapi/context7-mcp`) as a built-in MCP server that ships with the ensemble by default, requires zero user configuration, and is automatically available to all agent instances at runtime.

## Scope Assessment
**MEDIUM** — The project already has a complete built-in MCP server framework (`BuiltinServerDefinition` ABC, `BuiltinServerRegistry`, bootstrap in `InstanceManager.__init__`, DB seeding, tests). Adding Context7 follows the exact same pattern as WebFetch. It involves:
- 1 new file (`context7.py` definition)
- 2 modified files (registry `__init__.py` to register, possibly docs)
- 1 new test file
- No architecture changes needed

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Existing Pattern**: `daemon/mcp/builtin_servers/webfetch.py` is the reference implementation
- **Registry**: `daemon/mcp/builtin_servers/__init__.py` — singleton registry, auto-registers on import
- **Bootstrap**: `daemon/manager.py:540-605` — `_bootstrap_builtin_servers()` creates DB entries on startup
- **Tool Naming**: `mcp_{slugified_server}_{tool_name}` → Context7 tools become `mcp_context7_{tool}`

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Context7 Server Definition | Create `Context7ServerDefinition` class following existing patterns | None | — | 1h |
| 2 | Registry Integration & npx Handling | Register Context7, add npx availability check, graceful degradation | Phase 1 | tight | 0.5h |
| 3 | Testing | Unit tests for definition, config, registry integration, and npx fallback | Phase 2 | tight | 1h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|-----------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 imports the class from Phase 1's file and registers it in `__init__.py` |
| 2 → 3 | **tight** | Tests verify the registration and npx handling from Phase 2 |

All phases are tightly coupled — this is a single coherent module addition. They should be executed sequentially.

## Key Design Decisions

### DEC-001: npx Dependency Handling
**Decision**: Add a runtime check for `npx` availability at connection time, not at bootstrap time.

**Rationale**: The built-in server bootstrap happens at daemon startup and is fault-tolerant (per-server try/except). The DB entry should always be created regardless of npx availability. If npx isn't available, the MCP connection will fail gracefully when an instance tries to use it — this is already handled by the existing error handling in `McpConnectionManager` and `McpService._discover_server_tools()`.

**No new error-handling code needed** — the existing architecture handles stdio command failures gracefully:
- Connection manager catches subprocess failures
- Service layer logs errors and continues with other servers
- Instance still gets other MCP tools

### DEC-002: No Configurable Fields
**Decision**: Context7 has no user-configurable options — empty config schema.

**Rationale**: `@upstreamapi/context7-mcp` takes no CLI arguments or env vars. It's `npx -y @upstreamapi/context7-mcp` — that's it. The `get_config_schema()` returns `[]` and `build_config({})` returns the base config directly.

### DEC-003: Server Name
**Decision**: Use `"context7"` as the server name.

**Rationale**: Clean, matches the product name, slugifies to `context7` for tool naming (`mcp_context7_resolve_library_name`, etc.).

### DEC-004: User Disable/Override
**Decision**: Users can deactivate the built-in Context7 server via the existing API (set `is_active=False`), matching the existing pattern for WebFetch.

**Rationale**: The built-in server framework already supports this. Users can toggle `is_active` through the MCP server management API. If a user creates a custom server named `context7`, the bootstrap skips it with a warning (existing behavior at `manager.py:594-599`).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| npx not installed on host | low | Existing connection error handling; server entry created but tools unavailable. `connect_instance` catches `FileNotFoundError` via `asyncio.gather(return_exceptions=True)`, logs error, continues with other servers |
| Old npm (< 5.2) doesn't support `npx` | low | `npx` shipped with npm 5.2+ (2017). Document Node.js version requirement. The `-y` flag requires npm 7+; on older npm, `npx` works but prompts interactively (hangs in stdio). Log clear error on connection failure |
| Old Node.js — Context7-mcp may require Node 18+ | med | Package `@upstreamapi/context7-mcp` may use ES modules or Node 18+ APIs. If it fails to start, `stdio_client()` raises subprocess error → caught by existing error handling. Document Node 18+ recommendation |
| No internet — first `npx -y` download fails after timeout | med | First run downloads `@upstreamapi/context7-mcp` from npm registry. Without internet, subprocess hangs until `per_server_timeout` (5s default), then `asyncio.TimeoutError` is raised → caught and logged. Silent degradation may confuse users. Consider adding startup log: "Context7: first run requires internet to download npm package" |
| npm package name changes | low | Centralized in `get_base_config()`, easy to update. Schema version bump triggers drift detection |
| Context7 server startup slow (npm download) | low | First run downloads package; subsequent runs use cache. `npx -y` auto-confirms. Non-blocking — doesn't delay daemon startup (only delays per-instance MCP preload) |
| Conflicts with user-configured Context7 | low | Bootstrap detects name collision, logs warning, skips. User's config takes priority |

## Success Criteria
- [ ] `Context7ServerDefinition` class exists in `daemon/mcp/builtin_servers/context7.py`
- [ ] Context7 is registered in `BuiltinServerRegistry` on module import
- [ ] Daemon startup creates Context7 DB entry via `_bootstrap_builtin_servers()`
- [ ] Context7 tools appear as `mcp_context7_*` when npx is available
- [ ] If npx is unavailable, daemon starts normally, Context7 tools are empty, error is logged
- [ ] Users can deactivate Context7 via existing API (`is_active=False`)
- [ ] All existing tests pass
- [ ] New unit tests cover: definition, config, schema, registry, bootstrap, parse_config roundtrip, npx unavailability

## Tracking
- Created: 2026-05-17
- Last Updated: 2026-05-17
- Status: draft
