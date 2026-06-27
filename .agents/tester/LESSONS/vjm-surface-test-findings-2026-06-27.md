# Virtual Job Management Surface — Test Findings (2026-06-27)

## Feature
Branch: `feature/virtual-job-management-surface` (commits d058314f → 3d3613e4 + fe3dee2a)
4 phases: P1 (work_id foundation), P2 (watcher rewire + tool routing + SSE), P3 (defer queue on task layer), P4 (unified Work board UI)

## Test Results Summary
- **2300 tests total**: 2262 passed, 1 pre-existing flaky, 43 skipped (PG-env)
- **0 VJMS-caused failures**
- Contract preserved across all job orchestration paths

## Key Findings

### 1. Pre-existing Flaky Test (NOT VJMS)
- **Test**: `test_atomic_retry_concurrent_calls_only_one_succeeds` in `tests/job_queue/test_job_retry_engine.py:575`
- **Root cause**: SQLite+threading.Barrier(2) race condition — both UPDATEs see 0 rows under contention
- **Behavior**: Passes in isolation (28/28 when run alone), fails ~60% when run after other test files
- **Pre-dates VJMS**: Last modified in commits 12122f93, d589d360 (pre-VJMS branch)
- **Recommendation**: Consider moving to PostgreSQL-only test or adding retry logic

### 2. Frontend Coverage Gap (Non-blocking)
- Phase 4 frontend additions (work.model.ts, work.service.ts, jobs view mode toggle) have **NO dedicated spec files**
- Existing `jobs.component.spec.ts` (1189L) covers legacy features only
- `work.model.spec.ts`, `work.service.spec.ts`, and Phase 4 view mode tests should be added as follow-up
- Pattern already established: job.model.spec.ts (525L), job.service.spec.ts (564L)

### 3. Environment Issue (Not Code)
- `SSL_CERT_FILE` env var pointed to stale tmp path, causing RAG auto-test to fail with `RAGRequiredError`
- Workaround: export correct certifi path before `./dev.sh`
- Not a VJMS code bug, but worth documenting for daemon startup troubleshooting

### 4. Pre-existing `/health` Route Shadowing
- `/health` returns Angular index.html instead of JSON
- Root cause: SPA catch-all `@app.get("/{path:path}")` at `daemon/api.py:1140` shadows the api_router's `/health` route
- `/api/health` still works correctly
- Pre-existing, NOT introduced by VJMS

### 5. Defer Gate Working Correctly
- `claim_pending_task` respects defer gate (7/7 tests pass)
- Explicit test: non-deferred task on running instance claimed BEFORE deferred task on paused instance
- Defer gate is project-scoped (no cross-project leak)

### 6. WorkResolver Architecture Soundness
- Task-first lookup with job fallback works correctly
- `job_list` returns true UNION of jobs and tasks
- `job_get` on task work_id returns WorkRecord with `kind="turn"`
- `job_cancel` on task work_id sets `cancel_requested` cooperatively (not atomic)
- No double-notify on concurrent terminal callers

## Test Packs Created
- `vjm_resolver_unit_test` — 63 tests
- `vjm_router_unit_test` — 19 tests
- `vjm_resume_gate_unit_test` — 9 tests
- `vjm_task_repository_unit_test` — 59 tests (incl. 7 defer gate tests)
- `vjm_migration_unit_test` — 3 SQLite + 5 PG-skipped
- `vjm_job_queue_contract_test` — 1288/1289 (1 pre-existing flaky)
- `vjm_frontend_unit_test` — 799 tests + build
- `vjm_smoke_e2e_test` — 6/6 browser automation checks
