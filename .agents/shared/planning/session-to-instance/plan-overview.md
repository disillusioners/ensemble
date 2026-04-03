# Plan Overview: Session → Instance Rename Refactor

## Objective
Rename the agent "session" concept to "instance" across the entire agents-ensemble codebase. This is a definition/naming change only — no logic changes. Agent definitions (markdown/json) are like classes; when spawned they become instances (currently called sessions).

## Scope Assessment
**HUGE** — ~70+ files, ~5000+ occurrences across 7 layers (Python backend, DB/repository, tools, frontend, config/SQL, agent definitions, tests). This is a pure rename refactor with no behavioral changes, but the sheer surface area and cross-cutting nature make it high-risk for breakage if done piecemeal without careful ordering.

## Context
- **Project**: agents-ensemble
- **Working Directory**: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- **Requested by**: Leader

## Critical Exclusions (DO NOT rename)
| Keep As-Is | Reason |
|------------|--------|
| `opencode_skill` session concept | External tool, separate concept — see Phase 9 for detailed examples |
| SQLAlchemy `db_session`, `SQLModelSession` | ORM session, NOT agent session |
| `with Session(engine) as db_session` | SQLAlchemy Session class |
| HTTP/web server sessions (auth) | Unrelated concept |

**Rule of thumb**: Only rename "session" when it refers to an agent execution instance (a running agent spawned from an agent definition). This includes `session_id` values that flow through the job system (the job queue generates a UUID that is passed to `spawn_session` and becomes the agent's ID — it's the same concept).

## Phase Index

| Phase | Name | Objective | Dependencies | Est. Time |
|-------|------|-----------|-------------|-----------|
| 1 | **Foundation: Models & Pydantic** | Rename all type/class names in daemon/models.py, all repository models (session, source, project, job_queue, message_queue) | None | 1-2h |
| 2 | **Repository Layer** | Rename repository directory, classes, methods, factory, and re-exports (including message_queue repo) | Phase 1 | 1-2h |
| 3a | **Core Daemon** | Rename manager.py, graph.py, persistence.py, config.py, request_registry.py | Phase 2 | 2-3h |
| 3b | **Infrastructure: Events & Queue** | Rename events.py, queue.py (SSE event routing, message queuing, circuit breaker) | Phase 3a | 1h |
| 3c | **Services: Job Processing** | Rename job_processor.py, job_lock_manager.py, job_queue_service.py | Phase 3a | 1h |
| 4 | **Sources Layer** | Rename mapper.py, registry.py, scheduler adapter | Phase 3a | 1-2h |
| 5 | **Tools Layer** | Rename tools/session.py→instance.py, all tool references | Phase 3a | 1-2h |
| 6 | **API Layer** | Rename all routes, SSE events, error codes in daemon/api.py + daemon/routers/ | Phase 3a-3c, 4, 5 | 1-2h |
| 7 | **SQL Migration & Config** | Create new migration for table/column renames, update config.yaml | Phase 2, 3a | 1h |
| 8 | **Frontend** | Rename TypeScript models, API service, components, pages | Phase 6 | 2-3h |
| 9 | **Agent Definitions & Docs** | Update all markdown agent files, AGENTS.md, design docs | Phase 5 | 1h |
| 10 | **Tests** | Rename test files, classes, functions, fixtures, assertions | Phases 1-9 | 2-3h |

## Dependency Graph

```
Phase 1 (Models & Pydantic)
    ↓
Phase 2 (Repository Layer)
    ↓
Phase 3a (Core Daemon)
    ↓         ↓           ↓
Phase 3b   Phase 3c    Phase 4    Phase 5
(Events/   (Services)  (Sources)  (Tools)
 Queue)
    ↓         ↓           ↓         ↓
    ┌─────────┴───────────┴─────────┘
                ↓
         Phase 6 (API + Routers)
                ↓
Phase 7 (SQL Migration) ←─ Also depends on Phase 2, 3a
                ↓
Phase 8 (Frontend) ←─ Depends on Phase 6
                ↓
Phase 9 (Docs) ←─ Can run after Phase 5 (parallel with 6-8)
Phase 10 (Tests) ←─ Depends on everything
```

**Parallelization opportunities**:
- Phases 3b, 3c, 4, 5 can all run in parallel (each depends only on Phase 3a)
- Phase 9 can run in parallel with Phases 6-8
- Maximum parallelism: 4 sessions at the widest point

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Breaking API contract** | HIGH | Medium | Phase 6 must update ALL routes atomically. Frontend (Phase 8) must follow immediately. |
| **DB migration fails on existing data** | HIGH | Low | Create new migration (don't modify initial). Test with existing DB. Include rollback. |
| **Missed renames cause import errors** | HIGH | Medium | Each phase ends with `grep` verification. Final phase does full-codebase grep. |
| **False positives (ORM sessions)** | MEDIUM | Medium | Explicit exclusion list in every phase plan. Coder must verify each match. |
| **Circular import breakage** | MEDIUM | Low | Follow dependency order strictly. Models first, consumers last. |
| **Test suite breaks mid-refactor** | MEDIUM | High | Tests are last phase. Accept broken tests until Phase 10. |
| **Large files (manager.py 2100 lines)** | MEDIUM | Medium | Phase 3a is scoped to single file. Use find-and-replace carefully. |
| **Job system session_id is same value as agent ID** | MEDIUM | Low | Clarified: job queue UUID flows through to spawn_session → must be renamed consistently. Phase 3c handles this. |
| **Routers layer missed in API rename** | HIGH | Medium | Phase 6 explicitly includes daemon/routers/ (jobs.py, projects.py, schemas.py). |

## Verification Strategy

### Per-Phase Verification
Each phase must pass these checks before marking complete:

1. **Import check**: `python -c "from daemon.X import Y"` for all renamed exports
2. **Grep verification**: `grep -rn "old_name" --include="*.py" daemon/` returns 0 hits (excluding exclusions)
3. **No false renames**: `grep -rn "db_session\|SQLModelSession\|opencode_skill"` unchanged

### Final Verification (after Phase 10)
1. **Full grep**: Zero occurrences of old session names (excluding exclusions)
2. **Python imports**: `python -c "import daemon.api; import daemon.manager; import daemon.tools"`
3. **Test suite**: `pytest tests/` passes
4. **Frontend build**: `ng build` succeeds
5. **Migration test**: Apply migration to test DB, verify schema

## Success Criteria
- [ ] All "session" references renamed to "instance" (excluding exclusion list)
- [ ] Zero import errors across daemon package
- [ ] All API routes use `/instances/` instead of `/sessions/`
- [ ] DB tables use `instances`, `instance_hierarchy`, `instance_mappings`
- [ ] Frontend builds without errors
- [ ] All tests pass
- [ ] New SQL migration created (initial migration unchanged)
- [ ] Config keys use `instance` naming

## Tracking
- Created: 2026-04-02
- Last Updated: 2026-04-02 (rev2 — reviewer feedback incorporated)
- Status: draft
