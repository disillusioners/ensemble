# Plan Overview: Code Quality Refactoring (No Logic Changes)

## Objective
Restructure the agents-ensemble Python codebase for improved maintainability, readability, and organization. Split monolithic files, eliminate code duplication, introduce consistent patterns, and extract constants — all while preserving exact existing behavior.

## Scope Assessment
**LARGE** — Multiple modules, 6 phases, ~1.5–2 weeks of careful refactoring. No logic changes but high surface area across 8+ files with 20+ test files depending on them.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Stack**: Python, FastAPI, LangGraph, Pydantic, SQLite
- **Key Constraint**: Tests must pass identically before and after every phase

## Current State Summary

### File Sizes (lines)
| File | Lines | Issue |
|------|-------|-------|
| `daemon/manager.py` | 2985 | God class — 49 methods, 5 module-level functions, 2 callback handler classes, 2 dataclasses |
| `daemon/api.py` | 2114 | Monolith — 33 endpoints, 8 groups, 8 globals |
| `daemon/services/job_queue_service.py` | 1144 | Duplicated lock release logic (lines 603–614 vs 836–843) |
| `daemon/routers/jobs.py` | 891 | Imports `validate_agent_id` from `daemon.api` (line 166) — cross-module dependency |
| `daemon/models.py` | 737 | Mixed concerns across 8+ model groups |
| `daemon/utils.py` | 204 | **Already exists** with 5 functions — must APPEND, not create |
| `daemon/compaction.py` | 948 | (Large but focused — not in scope) |

### Key Quality Issues
1. **Monolithic files**: `api.py` (2114 lines) and `manager.py` (2985 lines)
2. **Code duplication**: datetime parsing (32 occurrences), HTTPException patterns, lock release logic (with subtle differences)
3. **Global state**: 8 module-level globals in `api.py`; `app.state.live_hub` already partially in use (lines 341, 370–371, 972)
4. **Magic numbers**: ~150 hard-coded numeric literals across the codebase
5. **Type inconsistency**: `Optional[T]` used in 326+ locations (especially `routers/schemas.py` with 38), 1 `Union[]` usage
6. **Cross-module import fragility**: `validate_agent_id` defined in `api.py` but imported by `routers/jobs.py` and tests
7. **Module-level functions in manager.py** imported by tests: `_build_message_content`, `extract_project_keywords`, `format_project_context`, `_get_message_event_type`, `_compute_message_content_hash`

### Pre-existing Patterns to Respect
- `app.state.live_hub` is already set during startup (line 341) and used in SSE endpoint (line 972) and shutdown (lines 370–371)
- `daemon/utils.py` already exists with: `parse_think_tags`, `_extract_timestamp`, `serialize_message`, `get_next_sequence`, `compute_message_id`
- Inner classes in `manager.py`: `ActivityCallbackHandler` (lines 168–217), `CancellationCallbackHandler` (lines 220–252), `MessageResult` (lines 296–302), `AsyncMessageResult` (lines 305–310)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | **Constants & Utilities** | Extract magic numbers, relocate `validate_agent_id` to utils, APPEND helpers to existing utils.py | None | — | 3–4h |
| 2 | **Models Split** | Split `models.py` into domain-specific model modules | Phase 1 | loose | 2–3h |
| 3 | **API Router Extraction** | Split `api.py` into router modules, migrate globals to `app.state` (coexist with existing `live_hub`) | Phase 1 (uses utils) | loose | 4–5h |
| 5 | **Jobs Router Cleanup** | Split `jobs.py` router + deduplicate lock logic in job_queue_service | Phase 1 (uses constants) + Phase 3 (validate_agent_id relocated) | tight | 2–3h |
| 4 | **Manager Decomposition** | Split `InstanceManager` into focused service classes; handle module-level functions and inner classes | Phase 1 (magic numbers done) + Phase 2 (model paths) | loose | 5–6h |
| 6 | **Type Consistency & Final Polish** | Normalize type annotations across 326+ `Optional[T]` and 1 `Union[]`; final cleanup | Phases 1–5 | loose | 2–3h |

### Required Execution Order (SEQUENTIAL — no parallelization)

```
Phase 1 → Phase 2 → Phase 3 → Phase 5 → Phase 4 → Phase 6
```

**Why this order and NO parallelization:**
1. **Phase 1 first** — Creates foundation constants and relocates `validate_agent_id` to `utils.py`; both Phase 3 and Phase 5 depend on this
2. **Phase 2 after Phase 1** — Model split uses constants from Phase 1; Phase 4 (manager) needs new model paths
3. **Phase 3 after Phase 2** — Router extraction is independent of models but follows logical ordering; Phase 5 depends on Phase 3 completing
4. **Phase 5 after Phase 3 (NOT parallel)** — `daemon/routers/jobs.py:166` imports `validate_agent_id` from `daemon.api`. Phase 3 splits `api.py`, breaking this import. Phase 1 already relocated `validate_agent_id` to `utils.py` with re-export from `api.py`, but Phase 3 must finish so the import source is stable
5. **Phase 4 after Phase 1** — Phase 1 replaces magic numbers in `manager.py`; Phase 4 restructures the entire class. Doing Phase 4 first would create merge conflicts with Phase 1
6. **Phase 5 after Phase 1** — `job_queue_service.py` modified by both Phase 1 (constants) and Phase 5 (lock dedup); cannot overlap
7. **Phase 6 last** — Touches all files for type annotation normalization

### Coupling Assessment

| Transition | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **loose** | Phase 2 imports constants; different files |
| Phase 1 → Phase 3 | **loose** | Phase 3 imports utilities; different files |
| Phase 2 → Phase 3 | **independent** | Different files entirely |
| Phase 3 → Phase 5 | **tight** | Phase 5 imports from `daemon.api` which Phase 3 restructures |
| Phase 1 → Phase 5 | **tight** | Same file (`job_queue_service.py`) modified by both |
| Phase 1 → Phase 4 | **tight** | Same file (`manager.py`) modified by both |
| Phase 2 → Phase 4 | **loose** | Phase 4 uses new model paths from Phase 2 |
| Phase 5 → Phase 4 | **independent** | Different files |
| Phase 4 → Phase 6 | **loose** | Phase 6 only changes type annotations in Phase 4's files |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Import path changes break tests | **high** | Use re-exports for backward compatibility; run full test suite after each phase |
| Behavior change during manager split | **high** | Keep `InstanceManager` as facade; add integration test snapshot before starting |
| Circular imports from module splitting | **medium** | Dependency order: constants → utils → models → services → routers |
| `app.state` migration breaks startup | **medium** | Coexist with existing `app.state.live_hub`; migrate one global at a time |
| Lock release dedup changes behavior | **medium** | The two lock patterns have **subtle differences** (`release_by_instance` vs `release`) — preserve both code paths |
| Missing test coverage for routers | **medium** | Create characterization tests before refactoring routers |
| `validate_agent_id` relocation breaks consumers | **medium** | Add re-export in `api.py`; update all import sites explicitly |
| Phase 3 breaks Phase 5's import of `validate_agent_id` | **high** | Phase 1 relocates function FIRST; Phase 3 can safely split api.py |
| Manager module-level functions break during Phase 4 | **high** | Keep in `manager.py` alongside facade, or move to `utils.py` with re-exports |

## Testing Strategy

### Pre-flight Validation (Before Starting)
1. Run full test suite, record pass/fail count as baseline
2. Run `python -c "from daemon.api import app; print('OK')"` — verify app loads
3. Record all current import paths used by tests: `grep -r "from daemon\." tests/`
4. Create git branch for the entire refactoring
5. Create git tag `refactor-pre-phase1` as rollback point

### Per-Phase Verification
1. **Before starting phase**: Create git tag `refactor-pre-phaseN`
2. **After completing phase**: Run full test suite, compare to baseline — must be identical
3. **If tests break**: `git diff refactor-pre-phaseN` to identify changes; revert if needed
4. **No new test failures allowed**

### Rollback Procedure (Per Phase)
```bash
# If Phase N breaks tests:
git stash  # or commit current work
git checkout refactor-pre-phaseN -- .  # restore to pre-phase state
# Investigate, fix, and retry
```

### Smoke Test (After Each Phase)
```bash
# App loads
python -c "from daemon.api import create_app; app = create_app(); print(f'Routes: {len(app.routes)}')"

# Models load
python -c "from daemon.models import *; print('Models OK')"

# Manager loads
python -c "from daemon.manager import InstanceManager; print('Manager OK')"
```

## Success Criteria
- [ ] All tests pass identically (same count, same results)
- [ ] No file exceeds 600 lines (except `manager.py` facade, max 400)
- [ ] All magic numbers extracted to named constants
- [ ] Zero code duplication for datetime parsing, HTTPException creation
- [ ] `api.py` fully migrated to routers with no inline endpoints
- [ ] `InstanceManager` decomposed into focused classes; module-level functions preserved
- [ ] `models.py` split by domain concern
- [ ] `Optional[T]` normalized to `T | None` across codebase
- [ ] `validate_agent_id` lives in `utils.py` (not `api.py`)
- [ ] `app.state` used consistently for all globals (coexisting with `live_hub`)

## Tracking
- Created: 2025-04-23
- Last Updated: 2025-04-23
- Status: draft
