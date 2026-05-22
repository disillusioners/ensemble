# Plan Overview: Project History Feature

## Objective
Add a project history mechanism so agents (primarily leaders) can record structured history entries about project changes, milestones, and events — replacing the current ad-hoc practice of storing change logs in project metadata JSON strings.

## Scope Assessment
**LARGE** — This feature spans 4 distinct modules (data model + migration, repository, tools, injection), touches 6+ files, and requires careful coordination between layers. Estimated 1-2 days of focused implementation.

## Context
- Project: agents-ensemble
- Working Directory: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble

## Key Design Decisions

### 1. Storage: Separate `project_history` Table ✅
**Decision:** New SQLModel table `project_history` with foreign key to `projects` (`ON DELETE CASCADE`).
**Rationale:** History can grow unbounded, needs paging, needs search. A JSON column on `projects` would be wasteful and impractical for large histories. This follows standard relational design. CASCADE ensures clean project deletion without orphan history rows.

### 2. History Entry Schema
```python
class ProjectHistoryEntry(SQLModel, table=True):
    __tablename__ = "project_history"
    id: str                          # UUID
    project_id: str                  # FK → projects.project_id
    entry_type: str                  # "milestone" | "commit" | "phase" | "bugfix" | "deployment" | "note" | "config_change" | "other"
    summary: str                     # Brief description (max 300 chars)
    details: str | None              # Optional longer description
    recorded_by_agent: str | None    # agent_id of recorder
    recorded_by_instance: str | None # instance_id of recorder
    entry_metadata: dict | None      # JSON blob for arbitrary structured data
    created_at: datetime             # When recorded
```

### 3. Tools
- `project_history_add` — Add a history entry
- `project_history_list` — List with paging (offset/limit), optional type filter
- `project_history_search` — Full-text search on summary + details
- `project_history_delete` — Remove a specific entry (requires `project_id` + `entry_id`, validates ownership)

### 4. Injection Format
Formatted markdown section appended after Critical Experience in `format_project_context()`. Show most recent 10 entries (not 20 — keeps context compact while providing useful history).

### 5. Availability
All agents get these tools (like project tools), not restricted. Any agent can record history.

### 6. Migration
Single migration file adding the `project_history` table with index on `project_id` + `created_at`. FK uses `ON DELETE CASCADE` so project deletion automatically removes history. Proposed filename: `20260521_000001_add_project_history_table.sql` (verify no conflicts at implementation time).

### 7. Naming Convention
All uses of the metadata field are standardized to `entry_metadata` — model field, repository params, tool params, and API schema.

### 8. Testing
Each phase includes a testing section covering its deliverables (see phase plans).

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Data Layer | New SQLModel, repository methods, migration | None | — | 2h |
| 2 | Tool Layer | 4 project history tools with factory pattern | Phase 1 | tight | 2h |
| 3 | Integration Layer | Project injection, registry, instance tool loading | Phase 1, 2 | loose | 1.5h |
| 4 | API & Schema | Expose history via API endpoints, response schemas | Phase 1 | loose | 1h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 → 2 | **tight** | Tools directly import and call repository methods; same data model |
| 1 → 4 | **loose** | API schemas reference model fields but don't share implementation files |
| 2 → 3 | **loose** | Integration imports the tool factory function; needs interface only |
| 1 → 3 | **loose** | Injection calls repository to get recent history; needs repository interface |

**Parallelization:** Phases 2 and 4 can partially overlap (both depend on Phase 1, but not on each other). Phase 3 should wait for Phase 2 completion.

## Risks & Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| SQLite full-text search limitations | low | Use LIKE-based search with NULL guards (`coalesce`); can upgrade to FTS5 later |
| Context window bloat from history injection | medium | Limit to 10 recent entries, truncate summaries, make configurable |
| Unbounded history growth | low | History is per-project; agents can delete old entries; no auto-eviction needed |
| Breaking existing project context format | medium | Append new section after existing CE section; don't modify existing format |
| Migration compatibility | low | New table only; no ALTER to existing tables; fully backward-compatible |
| Project deletion with history | medium | FK uses ON DELETE CASCADE — deleting a project auto-removes all history entries |
| Any agent deleting another project's history | medium | `project_history_delete` requires both `project_id` and `entry_id`, validates ownership |

## Success Criteria
- [ ] Agents can add structured history entries to any project
- [ ] History entries appear in project context when injected
- [ ] Agents can list history with paging and filter by type
- [ ] Agents can search history by text
- [ ] Agents can delete specific history entries (with project ownership validation)
- [ ] Existing project tools and critical experience tools continue working unchanged
- [ ] Migration runs cleanly on existing databases (including UP and DOWN)
- [ ] Project deletion cascades to remove all associated history entries

## Tracking
- Created: 2025-05-19
- Last Updated: 2025-05-19 (review fixes applied)
- Status: draft
