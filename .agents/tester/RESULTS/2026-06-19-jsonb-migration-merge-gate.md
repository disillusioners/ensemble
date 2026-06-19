# Test Report: JSON→JSONB Migration Branch — Merge Gate
Date: 2026-06-19T19:00Z
Branch: feature/jsonb-migration
Commits tested: f850bf3c (Phase 1), 6f94a584 (Phase 2), 89443707 (Phase 3), 351f5622 (conftest fix)
Session IDs: jsonb-git-verify, sqlite-regression, pg-concurrency-tests, pg-startup-migration

## Summary
| Task | Result |
|------|--------|
| SQLite Suite (regression) | ✅ PASS — 0 NEW failures (28 pre-existing) |
| PG Concurrency Suite | ✅ PASS — 46/46 tests pass |
| PG Startup Verification | ✅ WORKS — clean start, jsonb_set verified |
| Migration Verification | ✅ VERIFIED — 0 json, 26 jsonb columns, idempotent |
| Quick Fixes Applied | 1 (conftest import registration) |
| **Overall** | **✅ MERGE READY** |

## SQLite Suite (Regression Check)
- **Total collected**: 8,101 | **Ran**: 8,086 (15 undecorated integration tests excluded as hung)
- **Passed**: 7,999 | **Failed**: 31 | **Errors**: 0 | **Skipped**: 50 | **xfailed**: 6
- **NEW failures (JSONB-related)**: **0**
- **Pre-existing failures**: 28 (non-integration) + 3 (integration, out of scope)
- **Baseline**: `latest` has ~46 known failures; this branch has 28 → below baseline floor
- **Verdict**: PASS — no new regressions from JSONB migration

### Pre-existing Failure Categories (28 non-integration)
- test_config (1) — config drift (assert 500 == 300)
- test_innate_skills_refactoring (3) — pre-existing mock pattern
- test_memory_integration (1) — classification label drift
- test_project_store (2) + test_project_store_sqlmodel (2) — fixture bugs
- test_spawn_limit_edge_cases (9) — pre-existing mock pattern
- test_constants (1), test_context_key (1), test_api_router_extraction (1) — config drift
- test_llm_config_override (2) — pre-existing mock pattern
- test_stale_recovery_v2 (3) + test_timeout_retry_e2e (1) — pre-existing
- test_rag/test_config (1) — pre-existing

## PG Concurrency Suite
- **tests/postgres/** (31 concurrency + 7 smoke): 38/38 PASS
  - test_concurrent_enqueue (5) — unique constraint race
  - test_concurrent_jsonb_updates (5) — jsonb key writes, no lost updates
  - test_concurrent_lock_claims (6) — slot loop capacity ordering
  - test_concurrent_status_transitions (10) — atomic WHERE guard + EPQ re-evaluation
  - test_optimistic_locking (5) — version_id_col guard
  - test_smoke (7) — engine, event repo, truncate, session factory, two-connections
- **tests/migration/test_jsonb_migration.py** (8): 8/8 PASS
  - TestFreshPGSchemaIsJSONB (2), TestEnsurePostgresColumnsConvertsJSONtoJSONB (3), TestSQLiteRegression (3)
- **Total**: 46/46 PASS

## PG Startup Verification
- Daemon started on port 18079 against `ensemble_dev` (PostgreSQL)
- Engine created cleanly: `Creating PostgreSQL engine: localhost:5432/ensemble_dev`
- **Zero `CannotCoerce` errors** in stderr
- **Zero JSONB-related errors**
- Health endpoint returned 200 OK (status: healthy, version 0.6.10)
- jsonb_set verified end-to-end: metadata column updated via `jsonb_set` (original_source field merged)
- Clean shutdown confirmed

## Migration Verification
- **json columns**: 0 ✅ (expected 0)
- **jsonb columns**: 26 ✅ (expected ~26)
- All 17 DO-block-targeted columns confirmed jsonb
- **Idempotency**: DO block `WHERE data_type='json'` filter matches 0 rows on restart → true no-op, no errors

## Quick Fix Applied
- **File**: tests/postgres/conftest.py (+20 lines)
- **Root cause**: conftest didn't import SQLModel classes → `create_all` produced empty schema → `_pg_truncate_tables` fixture failed with UndefinedTable
- **Fix**: Added 12 model imports to register all 27 tables before create_all
- **Commit**: 351f5622 — "test: register SQLModel classes in PG conftest before create_all"
- **Verification**: PG tests re-run after fix → 46/46 PASS

## Notes
- PG tests require `--override-ini="addopts="` because default addopts excludes postgres marker
- System Python 3.14 lacks psycopg; use `.venv/bin/python`
- 15 integration tests lack `@pytest.mark.integration` decoration (pre-existing issue)
