# Phase 5: Per-Turn Freshness

## Objective
Ensure context is fresh each turn — not stale from spawn-time freeze. Since context is assembled inside `agent_node` each turn (per ADR-2), freshness is mostly automatic. This phase verifies there are no stale caches and documents the freshness guarantee.

## Coupling
- **Depends on**: Phase 3 (loose — needs context flowing through `agent_node`)
- **Coupling type**: loose
- **Shared files with other phases**: `instance_lifecycle.py` (shared with Phase 2)
- **Can parallel with**: Phase 4 (different files)

## Context
- Phase 3 completed: Context assembled inside `agent_node` each turn
- Current problem: System prompt was frozen at graph-compile time. Context is now per-turn (inside `agent_node`)
- Key benefit: mid-session changes to shared context files, KV metadata, or skills are reflected next turn

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Audit per-turn data freshness | Verify that `assemble_context_messages()` inside `agent_node` reads FRESH data each call:
  - `get_shared_context()` → reads filesystem `.md` files fresh? Or cached?
  - `shared_context_metadata_repo.get_all_as_dict()` → DB query, fresh
  - `skill_repo.get_auto_load_skills()` → DB query, fresh
  - `format_project_context()` → project repo, notes, history → DB queries, fresh | `daemon/services/context_injection.py`, `daemon/services/context_messages.py` |
| 2 | Verify `get_shared_context()` freshness | `_match_context_files()` reads `context_dir.glob("*.md")` with `st_mtime` sorting. Files read fresh each call. Verify no file-content cache. | `daemon/services/context_injection.py:252-365` |
| 3 | Verify spawn path no longer freezes context | In spawn path, context appenders ran at spawn and froze results (system_prompt closure capture). With Phase 2 gating, skipped when mode is `human_messages`. Verify no residual caching. | `daemon/services/instance_lifecycle.py:1268-1349` |
| 4 | Add freshness verification test | Write to `shared_context` KV mid-session → send next message → verify context reflects change. Write new `.md` file to context dir mid-session → verify next turn picks it up. | `tests/integration/test_context_freshness.py` (new) |
| 5 | Add skill bank freshness test | Add a new auto-load skill mid-session → send next message → verify context includes it. | `tests/integration/test_context_freshness.py` |
| 6 | Document freshness guarantee | Add docstring to `assemble_context_messages()` and `ContextSlot.assemble()` documenting that ALL data is read fresh each call inside `agent_node`. | `daemon/services/context_messages.py`, `daemon/graph.py` |

## Key Files
- `daemon/services/context_messages.py` — READ-ONLY: verify builders read fresh
- `daemon/services/context_injection.py` — READ-ONLY: verify `get_shared_context()` reads fresh
- `daemon/services/instance_lifecycle.py` — READ-ONLY: verify spawn path no longer caches context
- `tests/integration/test_context_freshness.py` — NEW

## Freshness Guarantee Matrix

| Data Source | Read Method | Fresh Each Turn? | Notes |
|-------------|-------------|------------------|-------|
| Project JSON | `project_repository.get()` | ✅ DB query | Fresh |
| Critical notes | `_fetch_critical_notes_safe()` | ✅ DB query | Fresh |
| Recent history | `project_store.get_recent_history()` | ✅ DB query | Fresh |
| Shared context KV | `metadata_repo.get_all_as_dict()` | ✅ DB query | Fresh |
| Shared context files | `get_shared_context()` → `glob("*.md")` | ✅ Filesystem read | Fresh (mtime-sorted) |
| Auto-load skills | `skill_repo.get_auto_load_skills()` | ✅ DB query | Fresh |
| Skill search (BM25) | `inject_skills()` | ✅ DB + compute | Fresh (pre-computed in messaging path) |
| Base system prompt | `load_and_cache_prompt()` | ⚠️ Cached | OK — persona doesn't change mid-session |

## Deliverables
- [ ] All context data sources verified as fresh per-turn
- [ ] No stale caches in context build path
- [ ] Freshness test: mid-session KV write reflected next turn
- [ ] Freshness test: mid-session file write reflected next turn
- [ ] Freshness test: mid-session skill add reflected next turn
- [ ] `assemble_context_messages()` and `ContextSlot` docstrings document freshness
