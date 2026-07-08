# Plan Overview: OpenSpace MCP Integration

## Objective
Integrate OpenSpace as a builtin MCP server in agents-ensemble using the sub-agent executor pattern, with dual-transport support (STDIO default, HTTP/SSE optional via ENV flag). Agents gain skill search, task delegation, skill repair, and skill upload capabilities through 4 MCP tools.

## Scope Assessment
**MEDIUM** — 4 new/modified files in the builtin server layer, 1 timeout mechanism enhancement, 1 innate skill prompt, and documentation. No DB schema changes. No changes to existing MCP infrastructure. All work fits within the existing `BuiltinServerDefinition` ABC pattern.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Branch: `feature/openspace-mcp-integration`
- OpenSpace source: `.inspiration-projects/OpenSpace-main/`
- OpenSpace MCP server: `python3 -m openspace.mcp_server`

## Architecture Summary

```
OpenSpaceServerDefinition (new)
├── get_base_config() → STDIO base (command + args only)
├── build_config() → dual transport via ENV check (called by bootstrap + warmup pool)
│   ├── ENS_OPENSPACE_REMOTE_URL set?  → streamable-http transport
│   └── ENS_OPENSPACE_REMOTE_URL empty? → stdio transport (python3 -m openspace.mcp_server)
├── get_config_schema() → user-configurable fields (model, max_iterations, etc.)
└── tool_call_timeout override → 900s (OpenSpace execute_task is long-running)
```

Existing infrastructure handles the rest automatically:
- `_bootstrap_builtin_servers()` creates/updates DB record
- `_init_warmup_pool()` registers STDIO servers for pooling
- HTTP/SSE servers use cold discovery (no warmup)
- `_apply_tool_filter()` handles per-agent tool access
- `MCP_DISABLE_BUILT_IN_OPENSPACE` disables via ENV (existing pattern)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | OpenSpaceServerDefinition | Create the dual-transport builtin server definition + register it | None | — (root) | 2-3h |
| 2 | Timeout & Dependency Mgmt | Per-server timeout override mechanism + dependency docs | Phase 1 | loose | 1-2h |
| 3 | Prompt & Agent Integration | Innate skill + tool filter docs + agent config | Phase 1 | loose | 1h |
| 4 | Optional Utilities *(deferred)* | Transport auto-detect, stdout safety wrapper, dep auto-install | None | independent | 2-3h |

### Phase Scheduling

**Core integration (Phases 1-3): ~4-6 hours total.**
- Phase 1 first (sequential — everything depends on it)
- Phases 2 and 3 in parallel (loose coupling, different files)

**Phase 4 is optional/deferred** — it's a pre-existing plan from a prior session covering quality-of-life utilities (transport auto-detection, stdout safety wrapper, MCP dependency auto-installer). Not required for OpenSpace integration to function. See `phase4-plan.md` for details.

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **loose** | Phase 2 modifies `McpWarmupPool` and `manager.py` for per-server timeout. Depends on Phase 1's definition existing, but touches different files. |
| 1 → 3 | **loose** | Phase 3 adds a prompt file and meta.json documentation. Only needs to know the tool names from Phase 1. |
| 2 → 3 | **independent** | Different files entirely (warmup_pool.py vs skill.md). No shared code. |

**Recommendation:** Phase 1 first (sequential), then Phases 2 and 3 can be done in parallel.

## Key Design Decisions

### D1: ENV-Based Transport Selection
- `ENS_OPENSPACE_REMOTE_URL` — if set, use `streamable-http` transport pointing to this URL
- If empty/unset, use `stdio` transport with `python3 -m openspace.mcp_server`
- This is a **config-time** decision (evaluated at `build_config()` time, stored in DB)

### D2: Per-Server Timeout via Definition Property
- Add `tool_call_timeout` as an optional property on `BuiltinServerDefinition` (default: None = use pool default)
- `OpenSpaceServerDefinition` returns `900` (15 minutes) — covers 20 iterations × 120s worst case
- `_init_warmup_pool()` reads this property and passes it to `register_server()`

### D3: Credential Injection via Explicit `os.environ` Read
- OpenSpace needs `OPENSPACE_LLM_API_KEY`, `OPENSPACE_API_KEY`, `OPENSPACE_MODEL`
- **MCP SDK does NOT inherit full `os.environ`** — `stdio_client` uses a 6-var POSIX whitelist only (`HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`). Credentials are **absent** unless explicitly injected into `config["env"]`
- `build_config()` override reads credential vars from `os.environ` and injects into `config["env"]`
- Schema keys use `OPENSPACE_` prefix (e.g., `openspace_model`) so base class uppercasing produces correct env var names (`OPENSPACE_MODEL`)
- For HTTP/SSE mode: credentials configured on the remote OpenSpace instance directly
- Credentials resolved from OpenSpace's 3-tier system (OPENSPACE_LLM_API_KEY > provider-native > host config)

### D4: Dependency Management — User Responsibility
- OpenSpace is NOT bundled with ensemble
- Document `pip install openspace-ai` in the innate skill and README
- Graceful failure: if `python3 -m openspace.mcp_server` fails to start, the MCP bootstrap logs an error and continues

### D5: Warmup Pool Handling
- STDIO mode: registers with warmup pool (pre-warmed connection, same as webfetch/context7)
- HTTP/SSE mode: cold discovery (existing behavior for non-STDIO servers)
- **Fix required:** `_init_warmup_pool()` at `daemon/manager.py:1033` must call `build_config({})` instead of `get_base_config()` to honor resolved transport (prevents zombie STDIO subprocess in HTTP mode — see Task 7 in Phase 1)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **`get_base_config()` vs `build_config()` divergence** | **high** | `_init_warmup_pool()` calls `get_base_config()` which always returns STDIO. In HTTP mode, this spawns a zombie subprocess. Fix: change line 1033 to `build_config({})` (Task 7, Phase 1). |
| OpenSpace not installed → subprocess fails | med | Graceful failure in bootstrap (existing per-server try/except). Log clear error pointing to install docs. |
| 120s timeout kills `execute_task` calls | **high** | Phase 2 adds per-server timeout override (900s for OpenSpace) |
| OpenSpace `OPENSPACE_LLM_API_KEY` not injected into subprocess env | **high** | MCP SDK uses 6-var POSIX whitelist, NOT full `os.environ`. `build_config()` must explicitly inject credentials into `config["env"]`. Without this, `execute_task` fails silently. |
| LiteLLM version conflict with ensemble deps | low | OpenSpace runs in its own subprocess (STDIO mode). No shared process space. Remote mode has zero dependency overlap. |
| STDERR pipe deadlock (Windows) | low | OpenSpace already handles this with `_MCPSafeStdout`. Linux/macOS unaffected. |
| Concurrent `execute_task` calls exhaust pool_size=1 | med | Cold-start fallback spawns extra OpenSpace subprocess (extra LLM tokens). Document; recommend `pool_size` increase or remote mode for high concurrency. |
| Eager schema warmup delay for slow OpenSpace startup | low | `eager_warm_schemas()` may delay if OpenSpace subprocess startup is slow. First `preload_mcp_tools()` falls through to cold discovery. Acceptable for initial integration. |

## Success Criteria
- [ ] `MCP_DISABLE_BUILT_IN_OPENSPACE=true` prevents server creation (existing pattern)
- [ ] Default (no ENV): OpenSpace runs as STDIO subprocess, registers with warmup pool
- [ ] `ENS_OPENSPACE_REMOTE_URL` set: OpenSpace connects via streamable-http
- [ ] Tool timeout for OpenSpace is 900s (not 120s default)
- [ ] Agents with OpenSpace tools can call `mcp_openspace_execute_task`, `mcp_openspace_search_skills`, etc.
- [ ] Agents without OpenSpace in their tool filter don't see the tools
- [ ] Innate skill prompt explains how/when to use OpenSpace tools
- [ ] Existing webfetch/context7 servers unaffected
- [ ] Bootstrap is idempotent and fault-tolerant

## Tracking
- Created: 2026-07-08
- Last Updated: 2026-07-08
- Status: draft
