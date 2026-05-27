# Test Report: Project-Aware Instance URL Routing
Date: 2026-05-27
Feature: Frontend URLs changed from `/instances/:instanceId` to `/projects/:projectId/instances/:instanceId`
Commit: c66075b

## Summary
- **Overall Status**: ✅ READY
- **Unit Tests**: 800/800 PASS (77 new routing tests added)
- **Browser E2E**: 4/4 PASS
- **ensure.md**: PASS (dev.sh stable 30s)
- **Quick Fixes**: 1 commit (86f46eb — variable scoping in home.component.spec.ts)
- **Regressions**: 0

## Unit Test Results (Session: routing-tests)

### Test Files Created/Extended
| File | Status | Tests Added |
|------|--------|-------------|
| `app.routes.spec.ts` | New | 17 |
| `home.component.spec.ts` | New | 32 |
| `chat.component.spec.ts` | New | 15 |
| `instances.component.spec.ts` | New | 13 |
| `jobs.component.spec.ts` | Extended | 10 |
| `instance-list.component.spec.ts` | Extended | 18 |

### Test Coverage by Scenario
| Scenario | Tests | Files |
|----------|-------|-------|
| 1. New route resolves correctly | 17 | app.routes.spec.ts |
| 2. Backward compatibility redirect | 3 | app.routes.spec.ts |
| 3. Navigation with specific project | 20 | home, chat, instances, jobs, instance-list |
| 4. Navigation from "All" tab | 20 | home, chat, instances, jobs, instance-list |
| 5. All 10 navigation points correct URLs | 10 | home(6), chat(1), instances(1), jobs(1), instance-list(1) |

### Suite Results
```
Test Suites: 22 passed, 22 total
Tests:       800 passed, 800 total (was 723, +77 new)
Time:        17.212s
```

## Browser E2E Results (Session: browser-e2e)

| # | Test | Result | Evidence |
|---|------|--------|----------|
| 1 | Navigate to instances page | ✅ PASS | URL: `/instances` |
| 2 | Instance URL structure | ✅ PASS | URL: `/projects/all/instances/<id>` |
| 3 | Back-navigation | ✅ PASS | Returns to `/instances` correctly |
| 4 | Old URL redirect | ✅ PASS | `/instances/<id>` → `/projects/all/instances/<id>` |

### URL Observations
- **List view**: `/instances` (simplified, no project prefix for "all")
- **Instance detail**: `/projects/all/instances/<id>` (project-aware format)
- **Redirect**: Old-style URLs redirect correctly to new format

## ensure.md Validation (Session: ensure-md)
- **dev.sh**: ✅ PASS — exit code 124 (timeout = running fine for 30s)
- Server started cleanly: RAG, MCP, workers, job recovery all OK
- Port 8079 cleaned up after test

## Quick Fixes Applied
| Session | Fix | Commit |
|---------|-----|--------|
| routing-tests | Fixed 3 variable scoping issues in home.component.spec.ts | 86f46eb |

## Documentation Updated
- [x] RESULTS/2026-05-27-project-aware-url-routing.md — full test report
- [x] PACKS.md — frontend_unit_test updated (800 tests, new status)
