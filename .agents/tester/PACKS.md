# Test Packs

## Summary
- Total: 16 packs
- Unit: 13 | Integration: 1 | Mock: 2

## Unit Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| core_unit_test | test/packs/core_unit_test.sh | Core daemon (agents, config, loader, manager, models, tools, persistence, queue, registry, telegram) + tool filter | 2 min | 2026-04-23 | ✅ PASS (611 passed, Phase 6 no regression) |
| sources_unit_test | test/packs/sources_unit_test.sh | Sources subsystem (circuit breaker, dispatcher, mapper, persistence, rate limiter, registry) | 2 min | 2026-04-23 | ✅ PASS (137 passed, Phase 6 no regression) |
| compaction_unit_test | test/packs/compaction_unit_test.sh | Compaction, find_near_instance, graph retry, idle timeout, LLM error classifier, response validation | 2 min | 2026-04-23 | ✅ PASS (171 passed, Phase 6 no regression) |
| api_unit_test | test/packs/api_unit_test.sh | API endpoints, scheduler adapter, spawn instance | 2 min | 2026-04-23 | ✅ PASS (148 passed, 8 skipped, Phase 6 no regression) |
| vision_unit_test | tests/unit/test_vision.py | Vision backend pipeline (validation, multimodal construction, serialization, DB storage) | 2 min | 2026-04-23 | ✅ PASS (45 tests, Phase 6 no regression) |
| job_queue_unit_test | test/packs/job_queue_unit_test.sh | Job queue full suite + Phase 1-5 + DLQ retry + replay-all + project_id injection + soft delete + 42 tool pack tests | 2 min | 2026-04-23 | ✅ PASS (948 passed, 19 skipped, Phase 6 no regression) |
| frontend_unit_test | frontend/jest.config.js | Angular frontend full suite (models, services, SSE, components, message-input image upload, api.service) | 2 min | 2026-04-23 | ✅ PASS (278 passed, Phase 6 no regression) |
| worker_notification_test | tests/test_worker_notification.py | Worker notification mechanism, race conditions, lifecycle integration (real threads) | 2 min | 2026-04-23 | ✅ PASS (14 passed, Phase 6 no regression) |
| models_split_unit_test | tests/unit/test_models_split.py | Phase 2 models split: backward compat, __all__ completeness, cross-module refs, instantiation, HealthResponse, Pydantic behavior | 2 min | 2026-04-23 | ✅ PASS (30 passed, Phase 6 no regression) |
| message_service_unit_test | tests/unit/test_message_service.py | MessageService, UnifiedMessage, ToolCallInfo (SSE message unification) | 2 min | 2026-04-23 | ⚠️ FILE NOT FOUND (stale entry) |
| api_router_extraction_test | tests/unit/test_api_router_extraction.py | Phase 3 router extraction: route registration, app.state, backward compat, _validate_instance_mode, _get_manager DI, router structure, API size | 2 min | 2026-04-23 | ✅ PASS (47 passed, Phase 6 no regression) |
| phase5_jobs_router_test | tests/unit/test_phase5_jobs_router.py | Phase 5 jobs router split: route registration, _release_job_lock scenarios, backward compat, service dependency, sub-router structure | 2 min | 2026-04-23 | ✅ PASS (34 passed, Phase 6 no regression) |
| phase4_manager_decomposition_test | tests/unit/test_phase4_manager_decomposition.py | Phase 4 manager decomposition: facade delegation, module-level functions, inner classes, service DI, fuzzy matching, cancellation service, title generation, circular imports | 2 min | 2026-04-23 | ✅ PASS (73 passed, Phase 6 no regression) |

## Integration Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| integration_test | test/packs/integration_test.sh | All integration tests (require OPENAI_API_KEY, auto-skip without) | 5 min | 2026-04-23 | ❌ FAIL (17 passed, 4 failed — PRE-EXISTING, not Phase 6) |

## Mock Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| mock_message_service_test | tests/mock_message_service.py | MessageService SSE critical paths (emit, error isolation, duplicate prevention, edge cases) | 2 min | 2026-04-23 | ⚠️ FILE NOT FOUND (stale entry) |
| mock_job_queue_test | test/packs/mock_job_queue_test.sh | Mock job queue API test | 5 min | 2026-04-23 | ❌ FAIL (48 fixture errors — PRE-EXISTING, not Phase 6) |

---

## Updating PACKS.md

Update after each test run:
- **Last Run**: timestamp
- **Status**: PASS/FAIL/TIMEOUT
- Add new entry for new packs
- Mark deprecated packs as DEPRECATED
