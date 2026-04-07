# Test Packs

## Summary
- Total: 7 packs
- Unit: 5 | Integration: 1 | Mock: 1

## Unit Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| core_unit_test | test/packs/core_unit_test.sh | Core daemon (agents, config, loader, manager, models, tools, persistence, queue, registry, telegram) | 2 min | 2026-04-07 | ✅ PASS (644 passed) |
| api_unit_test | test/packs/api_unit_test.sh | API endpoints, scheduler adapter/API/instance-mode, spawn validation/instructive errors | 2 min | 2026-04-07 | ❌ FAIL (147 passed, 8 failed) |
| sources_unit_test | test/packs/sources_unit_test.sh | Sources subsystem (circuit breaker, dispatcher, mapper, persistence, rate limiter, registry) | 2 min | 2026-04-07 | ✅ PASS (111 passed) |
| compaction_unit_test | test/packs/compaction_unit_test.sh | Compaction, find_near_instance, graph retry, idle timeout, LLM error classifier, response validation | 2 min | 2026-04-07 | ✅ PASS (177 passed) |
| job_queue_unit_test | test/packs/job_queue_unit_test.sh | Job queue (task lock manager, queue integration, repository, service) | 2 min | 2026-04-07 | ❌ FAIL (147 passed, 1 failed, 2 skipped) |

## Integration Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| integration_test | test/packs/integration_test.sh | All integration tests (require OPENAI_API_KEY, auto-skip without) | 5 min | 2026-04-07 | ❌ FAIL (56 passed, 6 failed, 7 skipped) |

## Mock Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| mock_job_queue_test | test/packs/mock_job_queue_test.sh | Mock job queue API test | 5 min | 2026-04-07 | ❌ FAIL (147 passed, 1 failed, 2 skipped) |

---

## Updating PACKS.md

Update after each test run:
- **Last Run**: timestamp
- **Status**: PASS/FAIL/TIMEOUT
- Add new entry for new packs
- Mark deprecated packs as DEPRECATED
