# Test Packs

## Summary
- Total: 14 packs
- Unit: 11 | Integration: 1 | Mock: 2

## Unit Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| core_unit_test | test/packs/core_unit_test.sh | Core daemon (agents, config, loader, manager, models, tools, persistence, queue, registry, telegram) + tool filter | 2 min | 2026-04-23 | ✅ PASS (611 passed, Phase 3 no regression) |
| sources_unit_test | test/packs/sources_unit_test.sh | Sources subsystem (circuit breaker, dispatcher, mapper, persistence, rate limiter, registry) | 2 min | 2026-04-23 | ✅ PASS (137 passed, Phase 3 no regression) |
| compaction_unit_test | test/packs/compaction_unit_test.sh | Compaction, find_near_instance, graph retry, idle timeout, LLM error classifier, response validation | 2 min | 2026-04-23 | ✅ PASS (171 passed, Phase 3 no regression) |
| api_unit_test | test/packs/api_unit_test.sh | API endpoints, scheduler adapter, spawn instance | 2 min | 2026-04-23 | ✅ PASS (148 passed, 8 skipped, Phase 3 quick fixes applied) |
| vision_unit_test | tests/unit/test_vision.py | Vision backend pipeline (validation, multimodal construction, serialization, DB storage) | 2 min | 2026-04-23 | ✅ PASS (45 tests, Phase 3 no regression) |
| job_queue_unit_test | test/packs/job_queue_unit_test.sh | Job queue full suite + Phase 1-3 + Phase 2 observer/recovery/cancellation/atomic/state-machine + Phase 2 feedback + Phase 4 event dispatch/idempotent enqueue verify tests + DLQ retry + replay-all + project_id injection + soft delete (77 tests across 3 files) + 42 tool pack tests | 2 min | 2026-04-23 | ✅ PASS (948 passed, 19 skipped, Phase 3 no regression) |
| frontend_unit_test | frontend/jest.config.js | Angular frontend full suite (models, services, SSE, components, message-input image upload, api.service) | 2 min | 2026-04-23 | ✅ PASS (278 passed, Phase 3 no regression) |
| worker_notification_test | tests/test_worker_notification.py | Worker notification mechanism, race conditions, lifecycle integration (real threads) | 2 min | 2026-04-23 | ✅ PASS (14 passed, Phase 3 no regression) |
| models_split_unit_test | tests/unit/test_models_split.py | Phase 2 models split: backward compat, __all__ completeness, cross-module refs, instantiation, HealthResponse, Pydantic behavior | 2 min | 2026-04-23 | ✅ PASS (30 passed, Phase 3 no regression) |
| message_service_unit_test | tests/unit/test_message_service.py | MessageService, UnifiedMessage, ToolCallInfo (SSE message unification) | 2 min | 2026-04-23 | ✅ PASS (16 passed) |
| api_router_extraction_test | tests/unit/test_api_router_extraction.py | Phase 3 router extraction: route registration, app.state, backward compat, _validate_instance_mode, _get_manager DI, router structure, API size | 2 min | 2026-04-23 | ✅ PASS (47 passed, NEW) |

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
