# Plan Overview: Replace Go opencode_skill with Native Python Tools (REVISION 3)

## Objective
Replace the external Go binary `opencode_skill` (TCP daemon + CLI client at port 44111) with 8 native Python tools running inside the ensemble daemon that call the OpenCode HTTP API directly, eliminating the TCP daemon layer entirely.

## Scope Assessment
**LARGE** — Multi-module implementation. The Go→Python port itself is **complete in working code** (3,093 lines in `daemon/opencode/`); the remaining work is **integration wiring** (manager hook, tools, factory, skill prompt, tests).

## Status — REVISION 3

This plan was revised after approver review (3 blockers found). All blockers are now fixed in both production code and plan documents.

- **Production code**: ✅ Done (state machine, client, repository, server, constants, state helpers)
- **Blocker 1** (table pollution): ✅ Fixed — `__table__.create()` replaces `SQLModel.metadata.create_all()`
- **Blocker 2** (wrong migration target): ✅ Fixed — Migration file deleted; table created at engine-factory time
- **Blocker 3** (wrong method names): ✅ Fixed — `initialize()` and `shutdown()` used in plan
- **Wiring**: ⏳ Required (manager init, factory integration, skill prompt, tools)
- **Tests**: ⏳ Required

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: `feature/opencode-native-tools`
- **Go source**: `.inspiration-projects/opencode_skill_src/` (reference only)
- **OpenCode HTTP API**: `http://127.0.0.1:4095`, Basic Auth `opencode/opencode`, header `x-opencode-directory`

## Production Code Already Implemented

| File | Lines | Purpose |
|------|-------|---------|
| `daemon/opencode/__init__.py` | 129 | Public API exports |
| `daemon/opencode/constants.py` | 79 | All Go config constants |
| `daemon/opencode/state.py` | 211 | `SessionState` enum + `_derive_state_from_finish`, `get_message_finish`, `has_message_error`, `strip_message_bloat` |
| `daemon/opencode/client.py` | 481 | `OpenCodeClient` (all 8 HTTP methods) + Pydantic DTOs with camelCase aliases |
| `daemon/opencode/repository.py` | 319 | `OpenCodeSessionRecord` SQLModel + `OpenCodeSessionRepository` (CRUD + dialect-aware + index on `id`); uses `__table__.create()` |
| `daemon/opencode/session_manager.py` | 998 | `OpenCodeSessionManager` (state machine, worker pattern, optimistic BUSY, abort, resume, sync, poll) |
| `daemon/opencode/registry.py` | 491 | `OpenCodeSessionRegistry` (create_new with abort-old-then-delete, abort_session with 3s settle, recover_from_registry, handle_start_work) + `get_session_record()` delegate |
| `daemon/opencode/server.py` | 385 | `external_opencode_send_message` dispatcher with BUSY bypass + `start-work` agent lock + agent-lock override |

**No migration file** — table is created at engine-factory time via `__table__.create(engine, checkfirst=True)`.

## Blocker Resolutions (Revision 3)

### Blocker 1: Table pollution — ✅ FIXED
**Problem**: `SQLModel.metadata.create_all(engine)` on the dedicated engine creates ALL 22+ tables.
**Fix**: `daemon/opencode/repository.py:311-319` — `create_opencode_session_repository()` now uses:
```python
OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
```
Creates only `opencode_sessions` + its index, nothing else.

### Blocker 2: Migration targets wrong DB — ✅ FIXED
**Problem**: Migration SQL file would be run on the main ensemble engine, polluting `instances.db`.
**Fix**: Deleted `daemon/migrations/versions/20260606_000002_create_opencode_sessions_table.sql`. Table creation happens at engine-factory time (Blocker 1 fix). No migration needed.

### Blocker 3: Nonexistent method names — ✅ FIXED
**Problem**: Phase 3 plan referenced `InstanceManager.start()` and `InstanceManager.stop()` which don't exist.
**Fix**: Phase 3 plan now uses `InstanceManager.initialize()` (manager.py:972) for recovery and `InstanceManager.shutdown()` (manager.py:2563) for cleanup.

## Important Issues (Documented for Implementation)

| ID | Issue | Resolution |
|----|-------|------------|
| 4 | Tools access private `registry._repository` | `OpenCodeSessionRegistry` has public `get_session_record(project, session_name)` delegate; tools use it instead of `_repository.get()` |
| 5 | `wait_any` polls sequentially | Phase 2 plan updated: use `asyncio.gather()` for parallel status checks |
| 6 | Tests mock wrong httpx method | Phase 5 plan updated: tests patch `self._client.request()` (production code uses unified `.request()`) |
| 7 | Deprecated `asyncio.get_event_loop().time()` | Phase 2 plan updated: use `asyncio.get_running_loop().time()` |

## Phase Index (Remaining Work)

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Production code port | ✅ DONE | None | — | — |
| 2 | Tool definitions + factory | Create 8 LangChain tools wrapping `daemon/opencode/server.py` | Phase 1 | tight | 1.5h |
| 3 | Manager wiring | Wire `OpenCodeSessionRegistry` into `daemon/manager.py` with separate engine | Phase 1 | tight | 1h |
| 4 | Skill prompt rewrite | Update `skill.md` for native tools | Phase 2 | loose | 0.5h |
| 5 | Tests | Unit + integration tests for the production code | Phase 1 | independent | 2h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|-----------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 imports `external_opencode_send_message`, `OpenCodeRequest`, `OpenCodeResponse`, `OpenCodeSessionRegistry` from `daemon/opencode/` |
| 1 → 3 | **tight** | Phase 3 wires `OpenCodeSessionRepository` + `OpenCodeSessionRegistry` into `daemon/manager.py` |
| 2 → 4 | **loose** | Phase 4 only needs tool function signatures |
| 1 → 5 | **independent** | Tests can be written from public APIs |

**Scheduling**:
- Phases 2 + 3 are independent of each other (different files) → **can run in parallel**
- Phase 4 can start as soon as Phase 2 tool signatures are stable
- Phase 5 can start in parallel with Phases 2/3

## Success Criteria
- [x] All 8 Go binary commands ported to Python (production code complete)
- [x] Table creation uses `__table__.create()` (no table pollution)
- [x] No migration file (table created at engine-factory time)
- [ ] 8 LangChain tools defined and registered in `_tool_registry.CATEGORY_MODULES`
- [ ] `daemon/manager.py` initializes `OpenCodeSessionRegistry` with separate engine at startup
- [ ] `recover_from_registry()` called in `initialize()` (not `start()`)
- [ ] `registry.shutdown()` called in `shutdown()` (not `stop()`)
- [ ] All tools work in agent context (factory/closure pattern)
- [ ] `skill.md` updated to document 8 native tools
- [ ] Unit tests for: state helpers, client, repository, session manager, registry
- [ ] Integration test for end-to-end workflow
- [ ] No imports of `opencode_skill` Go binary remain in skill.md

## Tracking
- Created: 2026-06-06
- Last Updated: 2026-06-06 (REVISION 3 — blockers fixed)
- Status: in_progress (production code done + blockers fixed; integration + tests pending)
