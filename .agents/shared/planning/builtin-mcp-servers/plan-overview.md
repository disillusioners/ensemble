# Plan Overview: Built-in MCP Servers

## Objective
Add a built-in MCP server framework to agents-ensemble that ships pre-configured MCP servers (starting with `webfetch`) alongside user-created servers. Built-in servers are auto-seeded on startup, cannot be deleted by users, and provide dynamic configuration schemas that the frontend renders as structured forms (replacing the raw JSON textarea for built-in servers).

## Scope Assessment
**LARGE** — Multiple modules across backend and frontend:
- DB migration + model changes (including schema versioning)
- New Pydantic models for configuration schemas
- New API endpoints for fetching built-in server templates/schemas
- Repository changes for built-in server filtering
- Manager bootstrap logic for auto-seeding built-in servers
- Frontend: dynamic schema-driven forms, built-in badges, delete protection
- Concrete `webfetch` built-in server implementation (`mcp-server-fetch` via `uvx`)

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Framework**: Python FastAPI backend + Angular 21 frontend with Material UI
- **Database**: SQLite via SQLModel

## Architecture Summary (from exploration)

### Current MCP Architecture
| Layer | File | Role |
|-------|------|------|
| **DB Model** | `daemon/repositories/mcp_server/models.py` | `McpServer` SQLModel (id, name, description, config, is_active) |
| **Repository** | `daemon/repositories/mcp_server/repository.py` | CRUD operations, `list_mcp_servers(is_active=None)` |
| **Config** | `daemon/mcp/config.py` | `McpStdioConfig`, `McpSseConfig`, `McpStreamableHttpConfig` (discriminated union) |
| **Connection Mgr** | `daemon/mcp/connection_manager.py` | Manages MCP client sessions per instance |
| **Service** | `daemon/services/mcp_service.py` | Tool discovery, caching, lifecycle |
| **API Models** | `daemon/models/mcp_server.py` | `McpServerCreate`, `McpServerUpdate`, `McpServerInfo` |
| **Router** | `daemon/routers/mcp_servers.py` | 5 REST endpoints at `/api/mcp-servers` |
| **Frontend List** | `frontend/src/app/components/mcp-server-list/` | Grid of server cards with edit/delete |
| **Frontend Dialog** | `frontend/src/app/components/mcp-server-dialog/` | Raw JSON textarea for config |
| **Frontend Service** | `frontend/src/app/services/mcp-server.service.ts` | HTTP calls to backend |
| **Frontend Types** | `frontend/src/app/models/index.ts` | `McpServer` interface |

### Key Architecture Constraints (from review)
| Constraint | Rationale |
|-----------|-----------|
| **Repository is pure DB layer** — no registry/definition imports | Layering: repo must not depend on higher-level modules |
| **Router/Manager orchestrates** via registry | Business logic lives above the repository |
| **`build_config` + `parse_config` are on `BuiltinServerDefinition`** | Lossy generation requires server-specific reverse mapping |
| **Frontend signals are `readonly` (public)** | Components access service signals directly, matching existing pattern |

---

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | **Backend: Built-in MCP Framework** | DB migration, model changes, repository extensions, API protection, built-in server registry with config generation + reverse-mapping, validation helper, bootstrap logic, tests | None | — | 5-6h |
| 2 | **Frontend: Built-in Server UI** | Dynamic config schema forms, built-in badges, delete protection, schema-driven dialog, reset with state sync, tests | Phase 1 | tight | 4-5h |
| 3 | **Built-in Server: WebFetch** | Implement the `webfetch` built-in server definition using `mcp-server-fetch` (Python/uvx) with config schema, build_config, and parse_config | Phase 1 | loose | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Reason |
|------------|----------|--------|
| 1 → 2 | **tight** | Frontend needs `is_builtin`, `config_schema`, `initial_values` in responses and new endpoints from Phase 1 |
| 1 → 3 | **loose** | WebFetch only needs the `BuiltinServerDefinition` abstract class; it's self-contained |

### Parallelism Opportunity
- **Phase 2 and Phase 3 can run in parallel** after Phase 1 completes.

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Breaking existing MCP server config format | high | low | Additive-only changes: new fields have defaults, no removals |
| Built-in server seed conflicts with user-created server of same name | medium | low | User server wins; warning logged; frontend shows actionable conflict resolution |
| Dynamic schema rendering complexity | medium | medium | Start simple: text, number, boolean, select types; expand later |
| WebFetch server requires system dependencies | low | medium | Uses `uvx` (Python) to run `mcp-server-fetch`; document `uv` requirement |
| Migration conflicts with parallel development | low | low | Use timestamp-based migration naming; test on fresh DB |
| Single built-in server bootstrap failure | medium | low | Each server seeded independently; failure logged but doesn't block startup |

---

## Success Criteria
- [ ] Built-in servers appear in the MCP server list with a distinct "Built-in" badge
- [ ] Users cannot delete built-in servers (button disabled + API returns 403)
- [ ] Users can configure built-in servers via dynamic form fields (not raw JSON)
- [ ] `webfetch` built-in server is auto-seeded on daemon startup with default config
- [ ] Configured built-in servers work identically to user-created servers for agents
- [ ] Existing user-created MCP servers continue to work without any changes
- [ ] DB migration applies cleanly and is reversible
- [ ] Bootstrap is fault-tolerant: single server failure doesn't block daemon startup
- [ ] Schema versioning detects and updates stale built-in server definitions
- [ ] Users can reset built-in server config to defaults
- [ ] Editing a built-in server pre-fills the form with current values (reverse-mapped from stored config)
- [ ] Repository layer has no dependency on the registry or built-in server definitions

---

## Tracking
- **Created**: 2026-05-17
- **Last Updated**: 2026-05-17
- **Status**: revised (v3 — final)
