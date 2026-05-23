# Plan Overview: Rename Critical Experience → Critical Notes

## Objective
Rename `critical_experience` → `critical_notes` across the entire codebase (DB, models, API, tools, agent definitions, tests, docs) and change the mechanism so only the leader agent has manual access — removing all experiencer agent integration.

## Scope Assessment
**LARGE** — ~269 code occurrences of `critical_experience`, ~90 of `CriticalExperience`, 82 tests across 6 test files, 5 tool names, DB migration, 2 agent definitions, shared prompt system, API schemas, tester agent documentation. Touches database layer, business logic, API surface, agent config, documentation, and tests simultaneously.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Current data flow**: Experiencer observes pattern → `project_ce_add()` → `tools/critical_experience.py` → `projects.critical_experience` JSON column → `format_project_context()` → injected as "### ⚡ Critical Experience"
- **Target data flow**: Leader agent (manual, user-driven) → `project_cn_add()` → `tools/critical_notes.py` → `projects.critical_notes` JSON column → `format_project_context()` → injected as "### ⚡ Critical Notes"

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Core Layer | Rename models, enums, tool implementation, registry; `git mv` tool file | None | — | 1.5h |
| 2 | API & Service Layer | Update router schemas, API endpoints, manager formatting | Phase 1 | tight | 1h |
| 3 | Agent Definitions | Update leader meta.json, strip experiencer (2 rule sections), rename shared prompt; `git mv` prompt file | Phase 1 | loose | 1h |
| 4 | Tests | Update all 82 tests across 6 files; `git mv` all test files | Phase 1, 2 | loose | 1.5h |
| 5 | Migration & Cleanup | DB migration with idempotency, tester docs update, explicit docstring fix, final grep sweep | Phase 1-4 | loose | 0.5h |

### Coupling Assessment

| From → To | Coupling | Reason |
|-----------|----------|--------|
| Phase 1 → Phase 2 | **tight** | Phase 2 imports models, calls tools defined in Phase 1. Same Python module namespace. |
| Phase 1 → Phase 3 | **loose** | Agent definitions only reference tool names (strings in JSON/markdown). Phase 1 defines the new names, Phase 3 just updates strings. |
| Phase 1,2 → Phase 4 | **loose** | Tests import from Phase 1 modules and hit Phase 2 API. But tests are consumers, not producers. |
| Phase 1-4 → Phase 5 | **loose** | Migration only needs to know old→new column name. Cleanup is purely mechanical. |

**Scheduling recommendation**: Phase 1 must complete first. Phases 2 and 3 can run in parallel after Phase 1. Phase 4 can start after Phase 1 but needs Phase 2 for API tests. Phase 5 runs last.

```
Phase 1 (Core Layer)
    ├──→ Phase 2 (API & Service) ──→ Phase 4 (Tests) ──→ Phase 5 (Migration & Cleanup)
    └──→ Phase 3 (Agent Defs)   ──────────────────────↗
```

### File Rename Ownership

Each phase owns its own `git mv` — no conditional "if not done" renames in Phase 5.

| Phase | File Rename | Command |
|-------|-------------|---------|
| 1 | Tool implementation | `git mv daemon/tools/critical_experience.py daemon/tools/critical_notes.py` |
| 3 | Shared prompt | `git mv agents/_prompt_system/critical-experience.md agents/_prompt_system/critical-notes.md` |
| 4 | Test files (6 total) | `git mv` for each test file (see Phase 4 rename map) |
| 5 | None | Verification-only, no file renames |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Migration data loss | HIGH | Migration preserves data via `ALTER TABLE RENAME COLUMN`. Test migration up+down. |
| Stale imports after rename | MED | Phase 5 includes global grep sweep for all old names. |
| Experiencer workflow breaks | MED | Phase 3 completely removes CE references from experiencer — no partial state. Both `rule.md` CE sections are removed. |
| Test breakage from name changes | MED | Phase 4 dedicated to test updates across all 6 test files. Run full suite after. |
| Shared prompt references missed | LOW | Grep for `critical.experience`, `CriticalExperience`, `project_ce_` patterns in Phase 5. |
| Tester agent docs go stale | MED | Phase 5 explicitly updates `.agents/tester/LESSONS/` and `.agents/tester/PACKS.md`. |
| "critical" false positives in grep sweep | LOW | Phase 5 verifies experiencer isolation. `rule.md` line 134 priority word "critical" is NOT a CE reference. |

## Success Criteria
- [ ] `grep -ri "critical_experience\|CriticalExperience\|project_ce_" daemon/ agents/ tests/` returns zero results (except migration SQL files)
- [ ] `grep -ri "critical.experience" daemon/ agents/ tests/` returns zero results (except migration files)
- [ ] All 82 tests pass with new names
- [ ] Experiencer agent has zero references to critical notes tools
- [ ] Leader agent has `critical_notes` tool access
- [ ] DB migration renames column and preserves data
- [ ] `format_project_context()` renders "### ⚡ Critical Notes"
- [ ] File `daemon/tools/critical_experience.py` no longer exists (renamed to `critical_notes.py`)
- [ ] `daemon/tools/project_history.py` docstring updated ("critical experience" → "critical notes")
- [ ] `.agents/tester/LESSONS/critical-experience-testing-patterns.md` and `.agents/tester/PACKS.md` updated

## Tracking
- Created: 2025-07-13
- Last Updated: 2025-07-13
- Status: draft
