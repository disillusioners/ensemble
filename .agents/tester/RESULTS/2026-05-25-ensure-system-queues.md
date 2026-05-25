# Test Report: Ensure System Queues (Phase 2)
Date: 2026-05-25
Commit: eb4bcc1 (feature), 8ccf4cc (tests), a7c2851 (bug fix), 1ce9a04 (cascade fix)

## Summary
- **Unit Tests**: 9/9 PASS (0.25s)
- **Mock Tests**: 20/20 assertions PASS (all 7 scenarios)
- **ensure.md**: PASS — dev.sh stable, healthy, uptime ~4min
- **Quick Fixes**: 2 bugs found and fixed

## Bugs Found & Fixed

### Bug 1: Project Repository Not Initialized for Queues Router
- **File**: `daemon/api.py` (line 328-330)
- **Commit**: `a7c2851`
- **Issue**: The `ensure-system` endpoint in `queues.py` uses its own project repository dependency injection (`get_project_repository()`), but it was never initialized during app startup. This caused the endpoint to return `503 {"error": "Project repository not initialized"}`.
- **Fix**: Added initialization of the queues router's project repository alongside the existing projects router initialization.
- **Impact**: Without this fix, the endpoint would always return 503 in production.

### Bug 2: Project Delete Cascade — Orphaned Tables & Cleanup Order
- **Files**: `daemon/repositories/project/repository.py`, `daemon/routers/projects.py`, frontend template
- **Commit**: `1ce9a04`
- **Issue**: In-memory instance cleanup happened AFTER DB deletion (couldn't find instances). Missing cleanup for task, event, message_queue tables. CSS class mismatch in delete dialog.
- **Fix**: Reordered cleanup, added cascade deletes for orphaned tables, fixed CSS class.

## Unit Test Results (9/9 PASS)

### Service Layer Tests (`TestEnsureSystemQueuesService`) — 4/4 PASS
| Test | Description | Status |
|------|-------------|--------|
| `test_ensure_system_queues_partial_existing` | Some queues exist, some don't → correct tracking | ✅ |
| `test_ensure_system_queues_all_exist` | All 4 already exist → all in existing_queues, created empty | ✅ |
| `test_ensure_system_queues_none_exist` | No queues exist → all 4 created, existing empty | ✅ |
| `test_ensure_system_queues_idempotent` | Call twice, second time all in existing_queues | ✅ |

### API Endpoint Tests (`TestEnsureSystemQueuesAPI`) — 5/5 PASS
| Test | Description | Status |
|------|-------------|--------|
| `test_ensure_system_queues_200_ok` | Valid project, correct response structure | ✅ |
| `test_ensure_system_queues_404_non_existent_project` | Non-existent project returns 404 | ✅ |
| `test_ensure_system_queues_correct_queue_properties` | Each queue has correct name, type, concurrency | ✅ |
| `test_ensure_system_queues_idempotent_api` | Calling endpoint twice works correctly | ✅ |
| `test_ensure_system_queues_partial_existing_api` | Pre-created queues tracked correctly | ✅ |

## Mock Test Results (20/20 assertions PASS)

Script: `tests/mock_ensure_system_queues.py`

| Scenario | Status |
|----------|--------|
| Discover valid project_id | ✅ |
| Create fresh project | ✅ |
| Ensure system queues (fresh — all 4 created) | ✅ |
| Idempotency (all 4 now existing) | ✅ |
| Queue correctness (name, type, concurrency) | ✅ |
| 404 for non-existent project | ✅ |
| Cleanup (delete test project) | ✅ |

## ensure.md Validation
- **Status**: ✅ PASS
- Dev server running on port 8079, healthy
- Health: `{"status":"healthy","uptime_seconds":239.5,"version":"0.3.3"}`

## Documentation Updated
- [x] RESULTS/2026-05-25-ensure-system-queues.md — this report
- [x] LESSONS/ensure-system-queues-bugs.md — bugs found during testing

## Code Changes Summary
| File | Change | Commit |
|------|--------|--------|
| tests/job_queue/test_ensure_system_queues.py | Created: 9 unit tests | 8ccf4cc |
| tests/mock_ensure_system_queues.py | Created: mock test script | (test script) |
| daemon/api.py | Fixed: init project repo for queues router | a7c2851 |
| daemon/repositories/project/repository.py | Fixed: cascade delete cleanup | 1ce9a04 |
| daemon/routers/projects.py | Fixed: cleanup order | 1ce9a04 |
| frontend template | Fixed: CSS class mismatch | 1ce9a04 |

## Overall Status
- Unit Tests: ✅ PASS (9/9)
- Mock Tests: ✅ PASS (20/20)
- ensure.md: ✅ PASS
- **Testing Complete**: ✅ READY — All tests pass, bugs fixed, dev.sh stable
