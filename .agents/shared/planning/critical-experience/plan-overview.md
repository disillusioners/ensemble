# Plan Overview: Critical Experience for Project Model

## Objective
Add a `critical_experience` field to the Project model — a structured list of concise, high-value knowledge entries (max 30, max 200 chars each) that all agents see when project info is injected. The Experiencer agent decides what goes to RAG vs what is critical enough to write to this field, and the Leader agent gets access for user-initiated management.

## Scope Assessment
**LARGE** — This feature touches 5 distinct areas: database schema/migration, new tool module with merge/eviction logic, Experiencer agent workflow changes, project context injection, and integration testing. Involves ~10 files across 3 subsystems with non-trivial merge/eviction logic.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: `feature/critical-experience`

## Key Architecture Facts (from Exploration)

### Project Model
- `daemon/repositories/project/models.py` — SQLModel `Project` class (line 60-136)
- JSON columns use `sa_column=Column(JSON)` pattern
- Serialization via `to_dict()` method (line 117-136)
- **Latent bug (defensive fix)**: `repository.py:140` calls `project.to_data()` instead of `project.to_dict()` — defensive fix for a code path that only triggers if `_enrich_project()` returns None on a non-None project

### DB Migration
- `daemon/migrations/` — file-based migration system using `MigrationRunner`
- Migration files live in `daemon/migrations/versions/` with naming: `YYYYMMDD_NNNNNN_description.sql`
- Files contain `-- UP` / `-- DOWN` SQL sections; discovered and applied automatically on startup
- Tracking via `schema_migrations` table (version, name, applied_at, execution_time_ms, checksum)
- Most recent migration: `20260517_000001_add_builtin_fields_to_mcp_servers.sql`

### Tool System
- `daemon/tools/_tool_registry.py` — `@register_tool_category("category")` decorator
- `daemon/tools/instance.py:create_instance_tools()` — assembles all tools, then filters by agent's `meta.json` `tools.allow/deny`
- Tool categories: `bash`, `filesystem`, `time`, `instance`, `self`, `project`, `job`, `rag`, `knowledge`, `mcp`, `help`
- Agent access control via `meta.json` tools config: `{"allow": ["category1", "category2"]}`

### Experiencer Agent
- `agents/experiencer/meta.json` — `tools.allow: ["rag", "help", "time", "mcp"]`
- 8-phase workflow in `agents/experiencer/workflow.md`
- Rule: "Never Query RAG for Retrieval" — only inserts

### Leader Agent
- `agents/leader/meta.json` — `tools.allow: ["time", "instance", "self", "project", "help", "knowledge", "mcp"]`
- Already has "project" category access

### Injection
- `daemon/manager.py:format_project_context()` (line 156-175) — calls `project.to_dict()`, returns JSON string
- Injected everywhere project info appears

### Shared Prompts
- `agents/_prompt_system/` — contains `knowledge.md`, `project-experience.md`

---

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Schema & Migration | Add `CriticalExperience` model, `critical_experience` column to Project, fix `to_data()` bug | None | — | 1h |
| 2 | Tool Module | Create `daemon/tools/critical_experience.py` with 3 tools (add/list/remove) + merge/eviction logic | Phase 1 | tight | 2h |
| 3 | Experiencer Agent | Update experiencer workflow, routing logic, and prompt system for critical experience handling | Phase 2 | loose | 1.5h |
| 4 | Project Injection | Update `format_project_context()` and APIs to include `critical_experience` in output | Phase 1 | independent | 0.5h |
| 5 | Testing & Integration | End-to-end validation of full flow | All phases | tight | 1h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|-----------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 tools operate on the `critical_experience` column defined in Phase 1 |
| 1 → 4 | **independent** | Phase 4 reads the new field via `to_dict()` — different files, no shared implementation |
| 2 → 3 | **loose** | Phase 3 references tool names/interfaces defined in Phase 2, but doesn't import Phase 2 code |
| 2 → 4 | **independent** | Different files, different concerns |
| All → 5 | **tight** | Integration testing requires all implementations complete |

### Scheduling Recommendation

```
Phase 1 ──┬──► Phase 2 ──► Phase 3 ──┐
           └──► Phase 4 ──────────────┼──► Phase 5
```

- **Phase 1** first (foundation)
- **Phase 2 + Phase 4** can run in parallel after Phase 1
- **Phase 3** after Phase 2 (needs tool definitions)
- **Phase 5** after all

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Merge logic false positives (merging unrelated entries) | medium | Use strict matching: same category + keyword overlap threshold; include examples in tool docs |
| Token budget: 30 entries × 200 chars = ~6K chars in injection | low | Already within reasonable limits; summary field is capped at 200 chars |
| Experiencer over-routing to critical_experience | medium | Clear criteria in routing logic; require "actionable + project-specific" test |
| Migration on large databases | low | SQLite ALTER TABLE is fast; JSON column with default `[]` |
| Breaking existing APIs | low | New field is additive; default `[]` means no behavioral change for existing projects |

## Success Criteria
- [ ] `critical_experience` field exists on Project model with proper schema
- [ ] DB migration adds column with default `[]`, preserves existing data
- [ ] `to_data()` latent bug fixed in `repository.py:140` (defensive fix — code path unlikely in practice)
- [ ] 3 tools (`project_ce_add`, `project_ce_list`, `project_ce_remove`) work correctly
- [ ] Merge logic combines similar entries, eviction removes oldest lowest-priority when full
- [ ] Only Experiencer and Leader agents have access to critical experience tools
- [ ] `format_project_context()` includes `critical_experience` in output
- [ ] Experiencer agent correctly routes knowledge to RAG vs critical_experience
- [ ] Full end-to-end flow works: input → experiencer → critical_experience → injection

## Tracking
- Created: 2026-05-19
- Last Updated: 2026-05-19
- Status: draft
