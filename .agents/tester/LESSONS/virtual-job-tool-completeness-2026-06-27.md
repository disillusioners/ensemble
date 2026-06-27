# Virtual Job Tool Completeness — Testing Notes (2026-06-27)

## Feature
Commit `f2514738` on `feature/virtual-job-tool-completeness`. Follow-up to VJM Surface adding root_only filtering, tool routing consistency, race guards, frontend All Work view.

## Key Insight: kill switch scope
The `use_virtual_job_resolver` kill switch (`USE_VIRTUAL_JOB_RESOLVER` env / `job_service.use_virtual_job_resolver` flag) controls **TOOL routing** (`job_list`, `job_get`, `job_continue`, `job_retry`, `job_delete`, `job_restore`, `job_cancel`, `watch_job(s)`), NOT the `WorkResolverService.list_work()` method itself.

`list_work()` is a NEW API that always goes through the resolver — it has no legacy fallback. This is by design. When verifying "kill switch" scenarios, check the **tools** in `daemon/tools/job_queue.py`, not `work_resolver.py`.

Tests covering the kill switch on tools:
- `test_job_retry_job_kind_falls_through`
- `test_job_retry_resolver_returns_none_falls_through`
(Both in `tests/test_job_queue_tools.py`, both PASS)

## Frontend All Work View Wiring (verified via Playwright)
- `jobs.component.ts:474` — hard-codes `root_only: false` in `loadWorks()` for the "All Work" view contract (shows every row incl. child-instance turns + reports)
- `work.service.ts:51-78` — serializes `root_only` as literal `true`/`false` query param (FastAPI bool coercion safe)
- Toggle behavior: "Queues" radio → `GET /api/jobs` (legacy); "All Work" radio → `GET /api/work?root_only=false`
- Kind chip helpers: `getKindLabel`, `getKindColor`, `getKindIcon` in `work.model.ts` (Job=blue, Turn=green, Report=purple)

## Pre-existing Flakes (NOT this feature)
| Test | File:Line | Cause |
|------|-----------|-------|
| `test_concurrent_terminal_writes_only_one_succeeds` | `test_job_repository_atomic_transition.py:359` | SQLite+threading.Barrier race |
| `test_concurrent_start_only_one_succeeds` | `test_job_repository_atomic_transition.py:525` | SQLite+threading.Barrier race |
| `test_ensure_dev_sh_still_works` | `test_jober_watch_integration.py:772` | `[Errno 48] Address already in use` (port timing) |
| `test_atomic_retry_concurrent_calls_only_one_succeeds` | `test_job_retry_engine.py:575` | SQLite+threading.Barrier race (documented prior) |

All pass in isolation. Recommend moving SQLite+threading atomic tests to PostgreSQL-only marker.

## Smoke Test Gotcha
`lsof -i :8079` can show stale PIDs (process exited but socket lingered). Always verify with `ps -p <PID>` AND a live `curl http://localhost:8079/health` before assuming the daemon is up. The frontend dev server (:4199) proxies `/api` → :8079, so UI checks surface "Failed to load work" when backend is down — this is correct graceful behavior, not a bug.

## Test Counts (commit f2514738)
- WorkResolver unit: 67 tests
- WorkRouter unit: 20 tests
- Job Queue Tools unit: 69 tests (NEW file, 552 lines added)
- Job Queue contract: 1327 tests (1286 pass + 3 pre-existing flakes + 38 skipped)
- Frontend unit: 823 tests (23 suites)
- Frontend build: SUCCESS (1.36 MB bundle, 3 budget warnings — pre-existing)
