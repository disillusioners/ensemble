# Plan Overview: Native Skill Evolution System

## Objective
Build a native skill evolution system that replaces the external OpenSpace MCP integration. Skills are living entities stored in the ensemble DB that can be dynamically selected per task, injected into agent conversations, and self-improve over time based on real execution outcomes through a tiered cost-control evolution pipeline.

## Scope Assessment
**HUGE** — 6 new DB tables, 6 new repositories, 11 new agent tools (6 `dynamic-skill` + 5 `skill-evolution`), 1 new agent, injection system in the message processing pipeline, 4-tier evolution engine with A/B testing, new API endpoints, and new Angular frontend page. Multi-phase roadmap spanning multiple developer sessions.

## Context
- **Project:** agents-ensemble
- **Working Directory:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch:** `feature/skill-evolution` (already created from `latest`)
- **Project ID:** `83da04de-a410-4fb5-9e92-251a99d28a52`

## Key Architecture Findings (from codebase exploration)

### Repository Pattern
- Single SQLModel implementation per entity, dual-driver via SQLAlchemy dialects
- `daemon/repositories/factory.py` — factory functions with `create_<name>_repository(config, engine, create_tables)`
- `daemon/repositories/__init__.py` — models MUST be imported here for `SQLModel.metadata.create_all()`
- `daemon/repositories/infra/types.py` — `JSONBType` TypeDecorator (JSONB on PG, JSON on SQLite)
- All timestamps stored as ISO-8601 strings (not datetime objects)
- All repositories take `engine: Engine` as first positional argument

### Migration Pattern (CRITICAL)
- SQLite: `.sql` migration files in `daemon/migrations/versions/YYYYMMDD_NNNNNN_description.sql`
- PostgreSQL: `.sql` migrations are NO-OPs — MUST extend `_ensure_postgres_columns()` in `daemon/manager.py`
- Use `IF NOT EXISTS` for idempotency
- Use `server_default` for NOT NULL columns on PostgreSQL
- No semicolons in SQLite migration comments (runner splits naively)

### Tool Registration (5-step pattern)
1. Write innate skill doc: `agents/_prompt_system/innate-skills/<name>/skill.md`
2. Write tool module: `daemon/tools/<name>_tools.py` with `create_<name>_tools()` factory
3. Add to `CATEGORY_MODULES` in `daemon/tools/_tool_registry.py:184`
4. Add to `INNATE_SKILL_TOOL_CATEGORIES` in `daemon/tools/instance.py:52`
5. Wire factory into `create_instance_tools()` in `daemon/tools/instance.py:537`

### Skill Injection Hook Point
- **File:** `daemon/services/instance_messaging.py`
- **Function:** `_process_message_with_tracking()` (lines 1468–1969)
- **Hook point:** `graph_input` construction at lines 1691–1716
- **Precedent pattern:** Project-context injection at lines 1591–1690 (prepends to `message` string)
- **LangGraph execution:** `graph.astream(graph_input, config)` at line 1753

### Task Completion Hook (for metrics)
- **File:** `daemon/services/job_queue_service.py:1274`
- **Function:** `_finalize_terminal()` — single chokepoint for ALL terminal transitions
- **Terminal write:** `finalize_active_to_done()` at lines 1469–1474
- All paths (completed/failed/cancelled/dead_letter) flow through this

### Agent Spawning
- `spawn_instance()` in `daemon/services/instance_lifecycle.py:437`
- Agent files: `agents/<name>/meta.json` + `agents/<name>/soul.md`
- `AgentMetadata` Pydantic model in `daemon/registry.py:69` — needs `skill_injection: bool` field added

### Job Types
- No central enum/registry — `job_type` is free-form string on `JobItem`
- New job types (`skill_metric_scan`, `skill_analysis`, `skill_evolution`, `skill_capture`) need no central schema update

### LLM + Embedding Infrastructure
- LLM: `ThinkingChatOpenAI` (extends LangChain `ChatOpenAI`) in `daemon/graph.py:34`
- Config: `LLMConfig` in `daemon/config.py:77` — OpenAI-compatible APIs only
- **Runtime config:** `Config(BaseSettings)` in `daemon/config.py:473` — all env-var-backed settings live here (NOT `EnsembleConfig` which is DB-only)
- Embedding: **Delegated to external LightRAG service** — no in-process embedding exists
- **numpy is EXCLUDED** (see `ensemble.spec:92`) — all vector math must be pure Python
- **For skill embeddings:** Need a new `EmbeddingService` that calls the configured OpenAI-compatible embedding endpoint directly (NOT via LightRAG — skills need per-example embeddings stored in DB). Store embeddings as JSON arrays of floats in a JSONB column (not BYTEA, not pickle — numpy unavailable). Implement cosine similarity in pure Python using `math.sqrt()` and `sum()`.

### Frontend
- **Angular 21.2.5** (NOT React as stated in original context)
- Angular Material 21.2.5 for UI components
- Standalone components, Angular Router v21.1.0
- Jobs page at `frontend/src/app/pages/jobs/jobs.component.ts`
- Routing via Angular Router, services for API calls
- Navigation is inline in `frontend/src/app/app.html:11-16` (not a separate component)
- Existing pages use custom card components (`JobCardComponent`, `ScheduleCardComponent`) — not `MatTableModule`
- Use `styleUrl` (singular), not `styleUrls` — Angular 17+ syntax

### API Routes
- Routers in `daemon/routers/` (e.g., `jobs_crud.py`, `messages.py`)
- Registered via `daemon/api.py` factory: `api_router.include_router(...)`
- Dependency injection via module-level service globals with setter/getter functions
- Initialize dependencies in lifespan function

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Foundation | DB schema (6 tables), 6 repositories, config system, `skill_injection` field | None | — (root) | 4-6h |
| 2 | Skill CRUD + Search | Skill store, BM25 pre-filter, embedding service, LLM selector, 6 agent tools, innate skill doc | Phase 1 | tight (repos + models) | 6-8h |
| 3 | Injection System | Message interceptor hook, search pipeline on user messages, HumanMessage injection | Phase 2 | tight (skill store + search) | 4-5h |
| 4 | Metrics & Triggers | Tier 0 recorder, trigger engine, default triggers, `skill_feedback` tool, denormalized counters | Phase 1, 2 | loose (repos only) | 4-6h |
| 5 | Evolution Engine | Skill-keeper agent, Tier 2/3 evolution, CAPTURED flow, lineage, A/B testing | Phase 1, 4 | tight (triggers, metrics) | 8-10h |
| 6 | Innate Skill + Polish | Full `dynamic-skill` doc, API endpoints, Angular frontend, integration tests | Phase 1-5 | loose (consumes all APIs) | 6-8h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 imports repos + models from Phase 1 directly |
| 2 → 3 | **tight** | Phase 3 calls the search pipeline built in Phase 2 |
| 1 → 4 | **loose** | Phase 4 only uses Phase 1's repos for recording metrics |
| 2 → 4 | **loose** | Phase 4 references skill IDs from Phase 2's store |
| 4 → 5 | **tight** | Phase 5 consumes trigger results + metrics from Phase 4 |
| 1-5 → 6 | **loose** | Phase 6 builds UI + API on top of existing services |

### Parallelization Opportunities
- **Phase 4 can run in parallel with Phase 2-3** (depends only on Phase 1 repos)
- **Phase 6 API layer can start early** (needs only Phase 1-2 to exist for basic CRUD)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Embedding service needs in-process embeddings (not via LightRAG) | high | Build `EmbeddingService` calling OpenAI-compatible `/embeddings` endpoint directly. Store embeddings as JSON arrays of floats in JSONB column (numpy is EXCLUDED in `ensemble.spec`). Pure Python cosine similarity via `math.sqrt()` + `sum()`. Configurable model/provider. |
| PostgreSQL migration NO-OPs on .sql files | high | MUST extend `_ensure_postgres_columns()` for ALL 5 new tables + any column additions. Use `CREATE TABLE IF NOT EXISTS` for fresh PG. Use `TEXT PRIMARY KEY` (not UUID) for consistency with existing tables — generate UUIDs in Python. |
| Config placement: SkillEvolutionConfig must go in `Config(BaseSettings)` at `daemon/config.py:473` (NOT `EnsembleConfig` which is DB-only) | high | `SkillEvolutionConfig(BaseSettings)` in `daemon/config.py`, registered as `skill_evolution: SkillEvolutionConfig = Field(default_factory=SkillEvolutionConfig)` on `Config`. Access via `self._config.skill_evolution` everywhere. |
| Injection hook could break existing message processing | high | Follow exact precedent pattern (project-context injection at lines 1591-1690). Gate by `skill_injection=true` in agent meta.json. Comprehensive tests. Inject as `HumanMessage` (NOT assistant message — LangGraph system messages can only be in system prompt). |
| A/B testing complexity — both versions served randomly | medium | Use `ab_test_group` UUID on skills table. **Deterministic** variant selection via hash of `(instance_id + message_id)` — not `random.choice()`. Deactivation after N comparisons (configurable, default 10) + minimum difference threshold (`ab_min_difference=0.15` — if difference < threshold after N, extend by another N). |
| Skill-keeper agent spawning via job queue adds latency | medium | Skill evolution is async/background by design. Use `system_parallel_queue` (concurrency=5). Tier 0/1 are free (no LLM). **CRITICAL:** `JobQueueService.enqueue()` defaults `queue_id=None` → `system_fifo_queue` (concurrency=1). MUST explicitly resolve `system_parallel_queue` via `queue_repo.get_by_name()` and pass `queue_id`. Precedent: `instance_messaging.py:1332-1353`. |
| `skill_injection` field on `AgentMetadata` always `False` | high | Adding the Pydantic field is NOT enough — `AgentMetadata` is constructed with explicit kwargs at `registry.py:195-210`, and `extra="ignore"` silently drops unknown keys. MUST add `skill_injection=meta.get("skill_injection", False)` to the constructor kwargs. |
| `feedback_applied` / `feedback_note` columns missing from DDL | high | MUST include these columns in both SQLite and PostgreSQL DDL. Phases 3, 4, 5 all query `feedback_applied` for capture flow and A/B testing. Add `has_applied_for_instance()` to `SkillUsageRepository`. |
| Frontend is Angular, not React (corrected from original spec) | low | Plan uses Angular Material components, standalone components, Angular Router patterns |
| BM25 implementation from scratch | medium | Implement simple in-memory BM25 over skill name + description + content. Re-rank with embeddings. Keep it simple — no external search engine dependency. |
| `skill_injection` field on `AgentMetadata` breaks existing agents | low | Default `False`. Only agents with explicit `skill_injection: true` in meta.json are affected. Backward compatible. |

## Success Criteria
- [ ] 6 DB tables created with PostgreSQL + SQLite dual support
- [ ] 5 repositories following existing ensemble pattern
- [ ] `skill_injection: bool` field on `AgentMetadata` (default `False`)
- [ ] 6 agent tools registered (`skill_search`, `skill_list`, `skill_view`, `skill_create`, `skill_fix`, `skill_feedback`)
- [ ] `dynamic-skill` innate skill doc created
- [ ] Skill injection working: real user messages trigger search → HumanMessage with skills injected before user message
- [ ] Tier 0 metrics recording after task completion
- [ ] Tier 1 trigger engine with configurable rules
- [ ] Skill-keeper agent spawned via job queue for Tier 2/3 evolution
- [ ] A/B testing: both versions served, loser deactivated after N comparisons + min difference threshold
- [ ] API endpoints for CRUD + lineage + metrics + feedback
- [ ] Angular frontend "Skills" menu
- [ ] Integration tests: create → inject → metrics → evolve → A/B → resolve
- [ ] All existing tests still pass (0 regressions)

## Tracking
- Created: 2026-07-10
- Last Updated: 2026-07-10 (review fixes round 2 applied)
- Status: draft
