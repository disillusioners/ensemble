# Plan Overview: MCP Server Exposing Explore & Experience KB Tools

## Objective
Create a new MCP (Model Context Protocol) server within the agents-ensemble project that exposes `explore()` and `experience()` knowledge base tools (as `ensemble_kb_explore` and `ensemble_kb_experience`) to external agent systems via SSE and StreamableHTTP transports, integrated into the existing FastAPI application.

## Scope Assessment
**MEDIUM** — This is a new subsystem but the scope is bounded:
- ~4 new files, ~2 modified files
- No database schema changes
- Dependencies already installed (`mcp` SDK, `sse-starlette`)
- Knowledge tools exist and work; we need to adapt them for external consumption
- Two transport mechanisms (SSE + StreamableHTTP) add moderate complexity
- The main design challenge is **dependency injection**: getting `InstanceManager` into the MCP tool context without the agent instance closure pattern

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: `feature/mcp-kb-tools` (already created)

## Key Architecture Decisions

### AD-1: Tool Implementation — Dedicated MCP Functions (NOT factory reuse)
The existing `create_knowledge_tools()` factory creates LangChain `@tool` functions that capture `manager` and `current_instance_id` via closures for agent instance context. For MCP, we need a different pattern:

- **External callers don't have instance context** — they provide `project_id` explicitly (mandatory)
- **No agent instance parent** — MCP tools run outside the agent spawn hierarchy
- **Direct access to infrastructure** — MCP tools call the same enqueue helpers and `invoke_agent_and_wait()` that the internal tools use

**Decision**: Create dedicated MCP tool functions that call the existing module-level helpers (`_enqueue_experience_job`, `_enqueue_kb_update_job`, `_parse_should_update_kb`, `_generate_*_idempotency_key`) from `daemon.tools.knowledge_tools` directly. These helpers are module-level functions with explicit parameters — no closure coupling. This reuses proven logic without modifying working code.

### AD-2: Dependency Access — Module-Level Setter Pattern
The MCP server runs within the FastAPI app. Access `InstanceManager` via a module-level setter, matching the project's existing DI convention (e.g., `set_dead_letter_service()` in `daemon/routers/dlq.py`).

**Decision**: Use `set_kb_mcp_manager(manager)` called during lifespan startup.

### AD-3: Dual Transport — Same FastMCP Instance
A single `FastMCP` instance can serve both SSE and StreamableHTTP by creating separate Starlette sub-apps from it. Both mount under the main FastAPI app at different paths.

**Decision**: Single `FastMCP` instance → `sse_app()` mounted at `/api/mcp/kb/sse`, `streamable_http_app()` mounted at `/api/mcp/kb`. StreamableHTTP uses `stateless_http=True, json_response=True` for simplicity. Session manager initialized eagerly in `create_kb_mcp_server()` by calling `mcp.streamable_http_app()` during factory.

### AD-4: Mount Path & Ordering
Mount under `/api/mcp/kb/` — distinct from the existing `/api/mcp-servers` (which is internal management).

**Decision**: `/api/mcp/kb` for StreamableHTTP, `/api/mcp/kb/sse` for SSE. **CRITICAL**: `app.mount()` calls MUST be placed BEFORE the catch-all SPA route `@app.get("/{path:path}")` at line 520 of `daemon/api.py`. Starlette checks routes before mounts, so mounts placed after catch-all routes will never match.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | MCP KB Server Module | Create FastMCP server with dual-transport tools | None | — | 2-3h |
| 2 | Integration & Wiring | Mount into FastAPI app, lifespan setup, tests | Phase 1 | tight | 1-2h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **tight** | Phase 2 imports and mounts the exact module created in Phase 1. Same files touched (`api.py`), same DI pattern. |

**Recommendation**: Execute sequentially. Phase 2 is small enough to follow immediately.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `invoke_agent_and_wait()` deadlocks when called from MCP context (semaphore exhaustion) | high | Only `ensemble_kb_explore` uses `invoke_agent_and_wait()` — `ensemble_kb_experience` uses `_enqueue_experience_job()` (no semaphore). Explore calls are naturally bounded by MCP transport. Monitor semaphore usage. |
| StreamableHTTP session manager lifespan conflicts with FastAPI's lifespan | medium | Eagerly initialize session manager in `create_kb_mcp_server()` by calling `mcp.streamable_http_app()`. Use `stateless_http=True` which minimizes session management. Nest `session_manager.run()` in app lifespan. |
| RAG not enabled (`is_rag_enabled()` returns False) | medium | Return clear error: "Knowledge base is not enabled. Configure RAG to use this tool." Don't silently fail. |
| Catch-all SPA route swallowing MCP mount paths | medium | `app.mount()` calls placed BEFORE `@app.get("/{path:path}")` in `create_app()`. Document ordering constraint with inline comment. |
| External callers flooding with requests | low | No rate limiting in v1. MCP transport itself provides natural throttling. Document as future enhancement. |
| `project_id` validation — caller provides invalid/unknown project_id | low | Underlying enqueue/explore handle this gracefully (no results / queue not found). |

## Success Criteria
- [ ] `ensemble_kb_explore` tool callable via StreamableHTTP transport
- [ ] `ensemble_kb_experience` tool callable via StreamableHTTP transport
- [ ] `ensemble_kb_explore` includes full post-processing (KB update check + conditional enqueue + heading strip)
- [ ] `ensemble_kb_experience` uses `_enqueue_experience_job()` pattern (NOT `invoke_agent_and_wait()`)
- [ ] SSE transport also works for both tools
- [ ] `project_id` is mandatory — tools error clearly when missing
- [ ] `mode` parameter validated against allowed values: `local`, `global`, `hybrid`, `naive`
- [ ] Tools return meaningful errors when RAG is not enabled
- [ ] Mounted under `/api/mcp/kb` (StreamableHTTP) and `/api/mcp/kb/sse` (SSE)
- [ ] Mount calls placed before catch-all SPA route
- [ ] No changes to existing agent-internal explore/experience functionality
- [ ] Unit tests for tool logic (mocked infrastructure)
- [ ] Integration test verifying endpoint accessibility

## Files Summary

### New Files
| File | Purpose |
|------|---------|
| `daemon/mcp/kb_server.py` | FastMCP server definition with `ensemble_kb_explore` and `ensemble_kb_experience` tools |
| `tests/unit/test_mcp_kb_server.py` | Unit tests for MCP KB tools |

### Modified Files
| File | Change |
|------|--------|
| `daemon/api.py` | Import and mount MCP KB server (both transports) BEFORE catch-all SPA route; add StreamableHTTP session manager to lifespan |
| `daemon/mcp/__init__.py` | Export KB server setup function |

### Import Only (NOT Modified)
| File | What's Imported |
|------|----------------|
| `daemon/tools/knowledge_tools.py` | `_enqueue_experience_job`, `_enqueue_kb_update_job`, `_parse_should_update_kb`, `_SHOULD_UPDATE_KB_PATTERN`, `_generate_idempotency_key`, `_generate_experience_idempotency_key` |

## Tracking
- Created: 2025-05-29
- Last Updated: 2025-05-29
- Status: draft (revised — 5 fixes applied)
