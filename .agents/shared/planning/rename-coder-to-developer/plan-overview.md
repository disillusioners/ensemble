# Plan Overview: Rename "coder" Agent to "developer" (Rev. 2)

> **Revision 2**: Incorporates Reviewer feedback — 3 criticals, 5 warnings, 4 suggestions all addressed.

## Objective
Rename the `coder` agent to `developer` across the entire agents-ensemble codebase: directory, meta.json, all Python source references, test files, frontend, agent prompt files, documentation, and a DB data migration for backward compatibility.

## Scope Assessment
**LARGE** — 1,300+ string references across 8 layers. No new features, but the breadth of mechanical changes + dual-engine DB migration makes this a multi-phase effort requiring careful sequencing.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Primary DB: PostgreSQL (SQLite also supported, dual-driver)
- Agent discovery is filesystem-based: `daemon/registry.py` scans `agents/` dir, reads `meta.json`, uses `id` field as canonical agent_id

## Reference Census (Corrected — Rev. 2)

| Layer | Files | Lines | Notes |
|-------|-------|-------|-------|
| Agent definition (`agents/coder/`) | 1 dir, 8 files | ~70 refs | meta.json + soul.md + rule.md + workflow.md |
| Python daemon (`daemon/`) | **20** files | 64 refs | Mostly docstring examples + registry *(was 15, corrected)* |
| Python tests (`tests/`) | 107 files | 1,038 refs | Mix of agent_id="coder" + natural language |
| **`test/packs/`** | **1** file | 3 refs | `stop_resume_spawn_e2e_test.py` *(newly added)* |
| Frontend (`frontend/src/`) | **9** files | 58 refs | 3 runtime + 6 test/spec *(was 13, corrected)* |
| Other agent prompts | 12 files | ~40 refs | leader, planner, jober, _mother, _prompt_system |
| Markdown docs (root) | 4 files | ~10 refs | README, ROADMAP, PLAN, base-plan |
| **Markdown docs (`docs/`)** | **25** files | ~150 refs | api-reference, agents, architecture, bugs, features *(newly added)* |
| Scripts | 3 files | ~12 refs | migrate_agent_id.py, migrate_memory_to_rag.py, e2e_pause_resume_test.py |
| `.agents/shared/planning/` | 6+ files | ~30 refs | Historical planning docs (informational only) |

### Daemon File List (20 files — corrected from 15)

| File | Refs | Change Type |
|------|------|-------------|
| `daemon/models/instance.py` | 5 | Field descriptions + JSON examples |
| `daemon/models/agent.py` | 4 | JSON examples |
| `daemon/models/mapping.py` | 6 | Field descriptions + JSON examples |
| `daemon/models/source.py` | 2 | JSON examples |
| `daemon/routers/schemas.py` | 8 | Field descriptions + JSON examples |
| `daemon/routers/dlq.py` | 4 | JSON examples |
| `daemon/tools/instance.py` | 4 | Docstrings + Field descriptions |
| `daemon/tools/inner_soul.py` | 1 | Docstring |
| `daemon/tools/agent_mother.py` | 2 | Docstrings |
| `daemon/tools/job_queue.py` | 4 | Docstrings + example |
| `daemon/registry.py` | 8 | Docstrings + examples |
| `daemon/services/child_reports.py` | 2 | Docstrings |
| `daemon/services/notification_broadcaster.py` | 1 | Docstring |
| `daemon/services/job_queue_service.py` | 1 | Docstring |
| `daemon/services/instance_lifecycle.py` | 1 | Docstring |
| `daemon/loader.py` | 4 | Docstrings |
| `daemon/manager.py` | 3 | Docstrings |
| `daemon/repositories/instance/repository.py` | 2 | Docstrings |
| `daemon/repositories/job_queue/repository.py` | 1 | Docstring |
| `daemon/repositories/factory.py` | 1 | Comment example |

> Note: `daemon/migrations/data_migrator.py` excluded — false positive ("encoder"/"Postgres encoder").

### DB Tables with `agent_id` / `agent_dir` Columns (corrected — Rev. 2)

| Table | Model Class | Columns | PK |
|-------|-------------|---------|-----|
| `instances` | `Instance` (`instance/models.py:47`) | `agent_id`, `agent_dir` | `instance_id` |
| `instance_mappings` | `InstanceMapping` (`source/models.py:87`) | `agent_id`, `agent_dir` | (mapping) |
| `job_queue_items` | `JobItem` (`job_queue/models.py:114`) | `agent_id`, `agent_dir` | `job_id` |
| `dead_letter_items` | `DeadLetterItem` (`job_queue/models.py:316`) | `agent_id`, `agent_dir` | (id) |
| `projects` | `Project` (`project/models.py:190`) | `creator_agent_id` | `id` |
| `jobqueue` | (legacy table name) | `agent_id`, `agent_dir` | `job_id` |

> **Removed**: `task_queue_items` — does NOT exist in the codebase (0 grep matches).
> **Added**: `dead_letter_items` — was missed in Rev. 1.
> **Note**: `projects` uses `creator_agent_id` (different column name, but may contain "coder").

### Key Architecture Insights (unchanged)

1. **Agent discovery is dynamic** — `registry.py:discover()` scans the `agents/` directory and reads `meta.json`. Renaming directory + updating `id` is sufficient for runtime.
2. **KB_AGENT_IDS does NOT include "coder"** — no change needed.
3. **agent_id is stored in DB** — 6 tables have agent_id-related columns (see above).
4. **No hardcoded agent routing in code** — leader prompt references are natural language.
5. **LangGraph checkpoints do NOT store agent_id** — `SessionState` only contains messages + `compacted_at`. No checkpoint migration needed. (Verified S2.)

---

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Core Agent Definition | Rename directory + meta.json + coder's own prompt files | None | — (root) | 1h |
| 2 | Python Daemon Source | Update all 20 `daemon/` source files | Phase 1 | tight | 2h |
| 3 | DB Migration & Backward Compat | Dual-engine migration + registry alias for all methods | Phase 2 | tight | 2.5h |
| 4 | Agent Prompt Updates | Update leader, planner, jober, _mother, _prompt_system prompts | Phase 1 | loose | 1.5h |
| 5 | Test Suite Updates | Update all 107 test files + `test/packs/` + migration tests | Phase 1, 2, 3 | tight | 3.5h |
| 6 | Frontend + Docs + Scripts + `.agents/` | Update frontend, 29 doc files, scripts, active `.agents/` files | Phase 1 | loose | 2.5h |

**Total Estimated Time: 13h**

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | tight | Daemon source references `agents/coder/` paths in examples |
| 2 → 3 | tight | DB migration + registry alias need daemon changes complete |
| 1 → 4 | loose | Agent prompt files are independent |
| 1+2+3 → 5 | tight | Tests exercise daemon + migration, must match new agent_id |
| 1 → 6 | loose | Frontend/docs/scripts reference agent_id independently |

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 5
    │           │
    │           ├──→ Phase 4 (parallel)
    └──────→ Phase 6 (parallel)
```

---

## Risks & Mitigations (corrected — Rev. 2)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Existing DB rows with `agent_id="coder"` break after rename | **HIGH** | Phase 3: Registry alias + dual-engine DB migration (PostgreSQL via `_ensure_postgres_columns()`, SQLite via `run_migrations()`) |
| **PostgreSQL migration silently no-ops** (C1) | **CRITICAL** | **DO NOT** use `factory.py:run_migrations()` — it returns early for non-SQLite. Add migration SQL to `manager.py:_ensure_postgres_columns()` instead. |
| `dead_letter_items` table missed in migration | **HIGH** | Phase 3 corrected to include all 6 tables |
| E2E tests use natural language ("spawn a coder") | **MEDIUM** | Update E2E prompts; keep agent description similar |
| External API consumers using `agent_id="coder"` | **MEDIUM** | Phase 3: Alias in `resolve_pure_id()`, `resolve_path_to_id()`, AND `exists()` |
| Frontend color map has hardcoded `'coder'` | **LOW** | Phase 6: Add `'developer'` entry |
| Other agents' prompts reference "coder" by name | **MEDIUM** | Phase 4: Update all prompts |
| `.agents/` active files reference "coder" (73 files) | **LOW** | Phase 6: Update active files (rules, soul, memories); leave historical RESULTS/LESSONS |

---

## Success Criteria

- [ ] `agents/developer/meta.json` has `id: "developer"`
- [ ] `agents/coder/` directory no longer exists
- [ ] All 20 `daemon/` Python source files use "developer" in examples/docstrings
- [ ] `registry.resolve_pure_id("coder")` → `"developer"` (alias)
- [ ] `registry.resolve_path_to_id("./agents/coder")` → `"developer"` (alias)
- [ ] `registry.exists("coder")` → `True` (alias)
- [ ] DB migration runs on **both** PostgreSQL and SQLite
- [ ] Migration covers all 6 tables: instances, instance_mappings, job_queue_items, dead_letter_items, projects, jobqueue(legacy)
- [ ] All 107 test files + `test/packs/stop_resume_spawn_e2e_test.py` pass
- [ ] Migration tests (insert coder → migrate → verify developer, idempotency, dual-engine)
- [ ] Frontend displays "Developer" agent
- [ ] All agent prompts reference "developer"
- [ ] `docs/` files updated (25 files)
- [ ] `grep -rn "coder" daemon/ --include="*.py" | grep -v encoder | grep -v tiktoken` returns 0

## Tracking
- Created: 2026-06-25
- Last Updated: 2026-06-25 (Rev. 2 — Reviewer feedback)
- Status: draft
