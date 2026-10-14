# Test Packs Created (2026-04-07)

## What
Created 7 timeout-enforced test pack scripts in `test/packs/` and registered them in PACKS.md.

## Packs
| Pack | Files Covered | Timeout |
|------|---------------|---------|
| core_unit_test | 20 test files (agents, config, loader, manager, models, tools, etc.) | 120s |
| api_unit_test | 6 test files (API, scheduler, spawn validation) | 120s |
| sources_unit_test | 6 test files (circuit breaker, dispatcher, mapper, etc.) | 120s |
| compaction_unit_test | 6 test files (unit/ subdirectory) | 120s |
| job_queue_unit_test | 4 test files (job_queue/ subdirectory) | 120s |
| integration_test | 13 test files (integration/ subdirectory) | 300s |
| mock_job_queue_test | 1 mock test script | 300s |

## Pattern
All scripts use `timeout Ns pytest ... 2>&1` with exit code handling:
- 0 = PASS
- 124 = TIMEOUT
- other = FAIL

## Note
Integration tests require OPENAI_API_KEY (auto-skip without it).
