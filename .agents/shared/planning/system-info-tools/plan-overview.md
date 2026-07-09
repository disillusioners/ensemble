# Plan Overview: `system_*` Info Tools for Gaia Agent

## Objective
Add a read-only `system` tool category (`daemon/tools/system.py`) that lets
Gaia (and other agents like devops) inspect the current ensemble runtime
state — environment variables, resolved configuration, and system health —
so Gaia can act as a "self local DevOps" during setup and troubleshooting.

## Scope Assessment
**Medium.** The work touches:
- **2 new files**: `daemon/tools/system.py`, `tests/unit/tools/test_system_tools.py`
- **2 modified files**: `daemon/tools/instance.py` (1 import + 1 factory call),
  `agents/gaia/meta.json` (add `'system'` to `tools.allow`, remove `'context'`)
- **3 read-only tools** behind a factory with secret-masking logic

No DB schema changes, no migrations, no new repositories.

## Context
- **Project**: agents-ensemble (`ens`)
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: `feature/system-info-tools-gaia` (from latest)
- **Version**: `0.8.1`

### Key Design Decisions

1. **Config access via factory injection** — The factory
   `create_system_tools(manager, current_instance_id)` reads
   `manager.config` (the `Config` / `DaemonConfig` singleton) and
   `manager.ensemble_config` (the `EnsembleConfig`). This matches the
   `create_db_tools` / `create_infra_tools` pattern exactly. Direct import
   of config objects is avoided because the manager holds the **resolved**
   config at runtime.

2. **Secret masking** — Sensitive values (`api_key`, `password`,
   `OPENAI_API_KEY`, `POSTGRES_PASSWORD`) are masked as `****` (last-4
   reveal: `****abcd`). A curated allowlist of safe-to-show env var
   prefixes (`ENSEMBLE_`, `OPENAI_` model-related, `POSTGRES_*` connection
   host/port/db, `RAG_IS_REQUIRED`, `MCP_*`, `LIGHTRAG_*`) is used.

3. **Read-only enforcement** — All three tools return strings; none
   accept parameters that mutate state. No `write` / `set` / `update`
   verbs in the tool names.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | system.py Tool Module | Create `daemon/tools/system.py` with 3 read-only tools, factory, and masking helper | None | — | 2–3h |
| 2 | Wiring + Gaia Config + Tests | Wire into `create_instance_tools()`, update Gaia `meta.json`, write tests | Phase 1 | tight | 1.5–2h |

### Coupling Assessment

| Pair | Coupling | Reason |
|------|----------|--------|
| Phase 1 → Phase 2 | **tight** | Phase 2 imports `create_system_tools` from `daemon/tools/system.py` (the exact function + tool names Phase 1 creates). Phase 2 tests call Phase 1's factory directly. Must run sequential. |

Phases 1 and 2 **must run sequentially**. Phase 2 cannot start until Phase 1
is reviewed and merged (it calls the factory function by name, references
tool names for category assertions, and depends on the masking helper API).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Secret leak in env/config output | **high** | Curated allowlist + explicit mask list; unit tests assert secrets never appear in plaintext |
| Config object shape mismatch (Pydantic `.model_dump()` includes fields not expected) | medium | Use `.model_dump()` with `exclude` for known-sensitive fields; test with real `Config()` defaults |
| `manager.config` is `None` in some test paths | low | Factory defensively handles `None` config (returns "config not loaded" message) |
| Gaia `meta.json` test breakage from removing `'context'` | low | Phase 2 updates the assertion in `test_gaia_agent.py` to match the new allow list |

## Success Criteria
- [ ] `daemon/tools/system.py` exports `create_system_tools()` returning 3 tools
- [ ] All 3 tools registered under `"system"` category via `@register_tool_category("system")`
- [ ] No secret value ever appears in plaintext in any tool output
- [ ] `create_instance_tools()` includes system tools for all instances
- [ ] Gaia `meta.json` `tools.allow` includes `'system'` and excludes `'context'`
- [ ] `test_gaia_agent.py` tool filter assertion passes
- [ ] `tests/unit/tools/test_system_tools.py` — all tests pass
- [ ] Full test suite — 0 regressions (run against PostgreSQL)

## Tracking
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: draft
