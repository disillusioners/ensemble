# Test Report: Phase 2 — Type Registry + DevOps Infra Tools

**Date:** 2026-06-15  
**Branch:** `feature/infra-info`  
**Commits tested:** `353c236` (Phase 2 base), `3d3f8b4` (bootstrap tests), `4b4dcd4` (tool tests), `4a8a4dc` (migration fix)  
**Sessions:** `infra-regression`, `infra-startup`, `infra-tools`

---

## Summary

| Category | Result | Details |
|----------|--------|---------|
| Existing Test Suite (Regression) | ✅ PASS | ~8,525 tests passed, 0 regressions from infra code |
| Type Registry Seeding | ✅ PASS | 10/10 bootstrap tests pass |
| 9 Infra Tools | ✅ PASS | 47/47 tool-layer tests pass |
| Project Isolation | ✅ PASS | Cross-project access blocked via all 6 project-scoped tools |
| Error Handling | ✅ PASS | Nonexistent IDs, cross-project, invalid types handled |
| Startup Integration | ✅ PASS | `_bootstrap_infra_types()` runs on daemon init |
| ensure.md (dev.sh) | ✅ PASS | Server ran 30s without crash, 9 types bootstrapped |
| Quick Fixes Applied | 1 | Migration SQL comment semicolon bug |
| **Overall Verdict** | ✅ **PASS** | |

---

## 1. Existing Test Suite Regression Check

**Verdict: PASS — No regressions from Phase 2 infra code**

| Group | Passed | Failed | Skipped |
|-------|--------|--------|---------|
| tests/repositories/infra/ | 130 | 0 | 0 |
| tests/repositories/ (all) | 130 | 0 | 0 |
| tests/unit/test_devops_agent.py | 62 | 0 | 0 |
| tests/unit/ (rest) | 3071 | 1 (pre-existing) | — |
| tests/opencode/ | 469 | 0 | 0 |
| tests/job_queue/ | 1230 | 0 | 19 |
| tests/tools/ + tests/api/ + tests/services/ + tests/migration/ | 441 | 0 | 0 |
| tests/integration/ | 1290 | 3 (pre-existing) | — |
| tests/e2e/ | 0 | 0 | 14 (need real env) |
| tests/test_*.py (top-level) | 1818 | 9 (pre-existing) | — |

### 13 Pre-Existing Failures (NOT infra regressions)
All verified by git stash + re-run on pre-infra commit:
1. **test_spawn_limit_edge_cases.py** (9 tests) — Missing `lease_heartbeat_interval_seconds` in mock_config (execution-gate feature gap)
2. **test_message_queue_e2e.py** (3 tests) — Mock LLM server not invoked
3. **test_gaia_agent.py** (1 test) — Stale assertion: gaia meta.json now includes `context` tool

---

## 2. Type Registry Seeding

**Verdict: PASS — 10/10 tests**

New file: `tests/unit/test_infra_bootstrap.py` (commit `3d3f8b4`)

| Test | Status |
|------|--------|
| `test_bootstrap_infra_types_called_in_init` | ✅ PASS |
| `test_bootstrap_call_positioned_correctly` | ✅ PASS |
| `test_seeds_all_nine_types_on_fresh_db` | ✅ PASS |
| `test_definition_count_matches_expectation` | ✅ PASS |
| `test_idempotent_runs_twice_no_duplicates` | ✅ PASS |
| `test_idempotent_keeps_same_row_ids` | ✅ PASS |
| `test_handles_repository_error_gracefully` | ✅ PASS |
| `test_logs_seeded_count_on_fresh_db` | ✅ PASS |
| `test_logs_only_debug_when_already_seeded` | ✅ PASS |
| `test_schemas_match_infra_type_definitions` | ✅ PASS |

Key findings:
- All 9 default types created on fresh DB
- Idempotent: second run returns same row IDs, no duplicates
- Error-tolerant: repository RuntimeError caught and logged, daemon doesn't crash
- Correct log levels: INFO on first seed, DEBUG on subsequent

---

## 3. Infra Tools (Tool Layer)

**Verdict: PASS — 47/47 tests**

New file: `tests/unit/test_infra_tools.py` (1301 lines, commit `4b4dcd4`)

| Test Class | Tests | Tool(s) |
|------------|-------|---------|
| TestInfraAssetCreateTool | 5 | infra_asset_create |
| TestInfraAssetGetTool | 3 | infra_asset_get |
| TestInfraAssetListTool | 7 | infra_asset_list |
| TestInfraAssetSearchTool | 5 | infra_asset_search |
| TestInfraAssetUpdateTool | 7 | infra_asset_update |
| TestInfraAssetDeleteTool | 4 | infra_asset_delete |
| TestInfraTypeRegisterTool | 3 | infra_type_register |
| TestInfraTypeListTool | 3 | infra_type_list |
| TestInfraHistoryGetTool | 6 | infra_history_get |
| TestProjectIsolation | 1 | Cross-cutting isolation |
| TestInfraToolsFactory | 3 | Factory returns 9 tools |

Key findings:
- Audit fields (`created_by`/`updated_by`/`deleted_by`) correctly auto-populated from instance_id
- Project isolation enforced across all 6 project-scoped tools (get, list, search, update, delete, history)
- Factory returns exactly 9 tools with correct names and `infra` category tags
- `infra_asset_update` uses replace-not-merge semantics for attributes column
- `infra_asset_list` defaults to unparented-only (top-level view)
- History fallback works for deleted assets via `snapshot->>'id'`

---

## 4. Quick Fixes Applied

### Bug: Migration SQL Comment Semicolon (commit `4a8a4dc`)
- **Root cause:** A `;` inside a SQL comment in `daemon/migrations/versions/20260616_000001_create_infra_asset_storage_tables.sql` (line 90) broke the migration runner's `split(";")` logic
- **Impact:** Corrupted `CREATE TABLE infra_asset_history` statement → 60 test failures
- **Fix:** Changed comment from `reconstructable; the FK` to `reconstructable. The FK` (1 line)
- **Verification:** All 60 failures resolved after fix

---

## 5. ensure.md Validation

**Verdict: PASS**

```
timeout 30 bash dev.sh  # exit code 124 = GOOD (ran 30s)
```

Log highlights:
- `Bootstrapped infra asset types: 9 total, 0 new, 9 updated`
- `Application startup complete.`
- Zero ERROR/WARNING/EXCEPTION lines in 30s log
- All subsystems started: WorkerPool, JobProcessor, JobFeedbackObserver, SourceRegistry, MCP warm-up pool

---

## Overall Status

- **Regression Tests**: ✅ PASS (0 infra-caused regressions)
- **Type Registry**: ✅ PASS (10/10)
- **Infra Tools**: ✅ PASS (47/47)
- **Project Isolation**: ✅ PASS
- **Error Handling**: ✅ PASS
- **Startup Integration**: ✅ PASS
- **ensure.md**: ✅ PASS
- **Testing Complete**: ✅ **READY**
