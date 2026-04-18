# Test Packs

## Summary
- Total: 11 packs
- Unit: 8 | Integration: 1 | Mock: 2

## Unit Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| core_unit_test | test/packs/core_unit_test.sh | Core daemon (agents, config, loader, manager, models, tools, persistence, queue, registry, telegram) + tool filter | 2 min | 2026-04-18 | ✅ PASS (no regressions from merge-access-memory-self) |
| sources_unit_test | test/packs/sources_unit_test.sh | Sources subsystem (circuit breaker, dispatcher, mapper, persistence, rate limiter, registry) | 2 min | 2026-04-07 | ✅ PASS (111 passed) |
| compaction_unit_test | test/packs/compaction_unit_test.sh | Compaction, find_near_instance, graph retry, idle timeout, LLM error classifier, response validation | 2 min | 2026-04-07 | ✅ PASS (177 passed) |
| job_queue_unit_test | test/packs/job_queue_unit_test.sh | Job queue full suite + Phase 1-3 + Phase 2 observer/recovery/cancellation/atomic/state-machine + Phase 2 feedback + Phase 4 event dispatch/idempotent enqueue verify tests + DLQ retry + replay-all | 2 min | 2026-04-18 | ✅ PASS (857+ passed, includes 19 new DLQ retry tests) |
| frontend_unit_test | frontend/jest.config.js | Angular frontend job queue (model, services, SSE, components) + Phase 3 queue service/model + DLQ model/service tests — Jest | 2 min | 2026-04-18 | ✅ PASS (232 passed, includes 16 new DLQ tests) |
| worker_notification_test | tests/test_worker_notification.py | Worker notification mechanism, race conditions, lifecycle integration (real threads) | 2 min | 2026-04-11 | ✅ PASS (13 passed, 3× verified no flakiness) |
| message_service_unit_test | tests/unit/test_message_service.py | MessageService, UnifiedMessage, ToolCallInfo (SSE message unification) | 2 min | 2026-04-12 | ✅ PASS (16 passed) |

## Integration Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| integration_test | test/packs/integration_test.sh | All integration tests (require OPENAI_API_KEY, auto-skip without) | 5 min | 2026-04-07 | ❌ FAIL (56 passed, 6 failed, 7 skipped) |

## Mock Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| mock_message_service_test | tests/mock_message_service.py | MessageService SSE critical paths (emit, error isolation, duplicate prevention, edge cases) | 2 min | 2026-04-12 | ✅ PASS (24 passed) |
| mock_job_queue_test | test/packs/mock_job_queue_test.sh | Mock job queue API test | 5 min | 2026-04-07 | ❌ FAIL (147 passed, 1 failed, 2 skipped) |

---

## Updating PACKS.md

Update after each test run:
- **Last Run**: timestamp
- **Status**: PASS/FAIL/TIMEOUT
- Add new entry for new packs
- Mark deprecated packs as DEPRECATED
rk deprecated packs as DEPRECATED
