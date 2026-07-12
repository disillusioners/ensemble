# Plan Overview: Shared Context Metadata KV System

## Objective

Add a batch CRUD agent tool for key-value metadata stored per `context_key`, and inject that metadata into ALL agent types' system prompts (not just explorer). This enables the leader to set shared context (e.g. `project_change_scope = BIG`) that every team member automatically sees at the top of their system prompt.

## Scope Assessment

**Scope: LARGE** — spans 4 distinct modules: DB storage layer, agent tool system, system-prompt injection chain, and tests. Each module has established patterns that must be followed precisely (dual DB support, tool registration, post-processing chain). Estimated 1-2 days of developer work.

| Factor | Detail |
|--------|--------|
| Files created | ~8 new files |
| Files modified | ~6 existing files |
| DB tables | 1 new table (`shared_context_metadata`) |
| New repository | 1 (`SharedContextMetadataRepository`) |
| New agent tool | 1 (`shared_context_metadata` with batch operations) |
| Injection points | 2 (spawn + restore in `instance_lifecycle.py`) |
| Test files | 3 (storage, tool, injection) |

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch**: create feature branch `feature/shared-context-metadata` from `latest`

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│  Leader Agent                                                │
│  └─ calls shared_context_metadata tool (batch set/delete)   │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  Storage Layer                                               │
│  SharedContextMetadataRepository                             │
│  Table: shared_context_metadata (context_key, key, value)    │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  Injection Layer (instance_lifecycle.py)                     │
│  append_shared_context_metadata()                            │
│  → fetches all KV pairs for context_key                      │
│  → formats into "# Shared Context" section                   │
│  → adds "---" separator after content                        │
│  Chain: append_context_key → append_shared_context_metadata  │
│         → append_current_time → append_user_language         │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  ALL Agent System Prompts (spawn + restore)                  │
│  ## Context Key                                              │
│  CONTEXT_KEY: {root_id}                                      │
│                                                              │
│  # Shared Context                                            │
│  context_key: {context_key}                                  │
│  ## Metadata KV                                              │
│  {"project_change_scope": "BIG", "decision": "..."}          │
│  ---                                                         │
│  ## Current Time                                             │
│  ## User Language Preference                                 │
└─────────────────────────────────────────────────────────────┘
```

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Storage Layer | New DB table + repository + factory wiring + migration | None | — | 3-4h |
| 2 | Agent Tool | New `shared_context_metadata` tool with batch CRUD + registration | Phase 1 | tight (imports repository) | 2-3h |
| 3 | Injection Layer | `append_shared_context_metadata()` in post-processing chain | Phase 1 | tight (imports repository) | 2-3h |
| 4 | Tests | Storage, tool, and injection tests | Phases 1-3 | tight (tests all layers) | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 tool imports `SharedContextMetadataRepository` from Phase 1 |
| 1 → 3 | **tight** | Phase 3 injection calls `SharedContextMetadataRepository.list_records()` from Phase 1 |
| 2 → 3 | **independent** | Tool and injection are separate code paths; both depend on Phase 1 but not on each other |
| 3 → 4 | **tight** | Tests require all three layers to be complete |
| 1 → 4 | **tight** | Storage tests require the repository from Phase 1 |

**Scheduling**: Phase 1 must complete first. Phases 2 and 3 can be developed in parallel (both depend only on Phase 1). Phase 4 must come last.

```
Phase 1 (Storage)
    ├── Phase 2 (Agent Tool)     ← can parallel with Phase 3
    ├── Phase 3 (Injection)      ← can parallel with Phase 2
    └── Phase 4 (Tests)          ← after 1, 2, 3
```

## Key Design Decisions

See `decisions.md` for full details. Summary:

1. **New domain `shared_context/`** — separate from `project/` domain (context_key-scoped, not project-scoped)
2. **Tool category `context_metadata`** — separate from `context` (file-based context); add to leader's `tools.allow`
3. **Injection position: after `append_context_key()`, before `append_current_time()`** — metadata adjacent to context key
4. **Batch operations in one tool call** — single `shared_context_metadata` tool accepts `operations` array
5. **No project-level coupling** — metadata writes don't mutate parent project's `updated_at` (unlike `project_metadata`)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Metadata injection adds latency to every agent spawn | medium | Metadata table is small (few KV pairs per context_key); indexed by context_key; query is sub-millisecond |
| Tool not available to leader due to `tools.allow` filtering | high | Explicitly add `"context_metadata"` to leader's `meta.json` `tools.allow` |
| SQLite/PostgreSQL upsert dialect differences | medium | Follow established `_get_dialect_insert()` pattern from `project/repository.py` |
| Post-processing chain runs on EVERY spawn — must not crash | high | Wrap metadata fetch in try/except; on error, skip injection (graceful degradation) |
| Reserved column name `metadata` silently renamed by SQLAlchemy | low | Use `meta_key`/`meta_value` naming (matching `project_metadata_records` pattern) |
| Prompt cache invalidation — metadata changes shouldn't invalidate cache | low | Injection is post-cache (same as `append_context_key`); cache is keyed by agent_id + MCP tools, not metadata |

## Success Criteria

- [ ] `shared_context_metadata` table exists with unique constraint on `(context_key, meta_key)`
- [ ] `SharedContextMetadataRepository` supports batch upsert, delete, and list operations
- [ ] Repository works on both SQLite and PostgreSQL
- [ ] `shared_context_metadata` agent tool accepts batch operations (set/delete/list)
- [ ] Tool is available to leader agent
- [ ] `append_shared_context_metadata()` injects KV pairs into system prompt for ALL agent types
- [ ] Injection appears after `## Context Key` and before `## Current Time` in system prompt
- [ ] A `---` separator is added after the shared context content
- [ ] Empty metadata (no KV pairs) does not inject any section (no empty header)
- [ ] All tests pass on both SQLite and PostgreSQL
- [ ] No regressions in existing tests

## Tracking

- Created: 2026-07-12
- Last Updated: 2026-07-12
- Status: draft
