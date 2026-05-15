# Test Packs

## Summary
- Total: 25 packs
- Unit: 22 | Integration: 1 | Mock: 2

## Unit Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| core_unit_test | test/packs/core_unit_test.sh | Core daemon (agents, config, loader, manager, models, tools, persistence, queue, registry, telegram) + tool filter | 2 min | 2026-05-15 | ✅ PASS (653 passed, stop-cascade no regression) |
| sources_unit_test | test/packs/sources_unit_test.sh | Sources subsystem (circuit breaker, dispatcher, mapper, persistence, rate limiter, registry) | 2 min | 2026-04-24 | ✅ PASS (137 passed, system_default_project no regression) |
| compaction_unit_test | test/packs/compaction_unit_test.sh | Compaction, find_near_instance, graph retry, idle timeout, LLM error classifier, response validation | 2 min | 2026-04-25 | ✅ PASS (fuzzy-match branch, find_near_instance: 26/26, no regression) |
| api_unit_test | test/packs/api_unit_test.sh | API endpoints, scheduler adapter, spawn instance | 2 min | 2026-05-15 | ✅ PASS (200 passed, 8 skipped, stop-cascade no regression) |
| vision_unit_test | tests/unit/test_vision.py | Vision backend pipeline (validation, multimodal construction, serialization, DB storage) | 2 min | 2026-04-23 | ✅ PASS (45 tests, Phase 6 no regression) |
| job_queue_unit_test | test/packs/job_queue_unit_test.sh | Job queue full suite + Phase 1-5 + DLQ retry + replay-all + project_id injection + soft delete + 42 tool pack tests | 2 min | 2026-04-29 | ✅ PASS (991 passed, 19 skipped, kb-fifo-queue no regression) |
| jober_watch_integration_test | tests/job_queue/test_jober_watch_integration.py | Phase 3 jober watch: 7 terminal paths, 13 edge cases, notification format, tool registration, agent definition, crash recovery | 2 min | 2026-04-24 | ✅ PASS (38 passed, 0 failed, 2 benign bugs found) |
| frontend_unit_test | frontend/jest.config.js | Angular frontend full suite (models, services, SSE, components, message-input image upload, api.service) | 2 min | 2026-04-23 | ✅ PASS (278 passed, Phase 6 no regression) |
| worker_notification_test | tests/test_worker_notification.py | Worker notification mechanism, race conditions, lifecycle integration (real threads) | 2 min | 2026-04-23 | ✅ PASS (14 passed, Phase 6 no regression) |
| models_split_unit_test | tests/unit/test_models_split.py | Phase 2 models split: backward compat, __all__ completeness, cross-module refs, instantiation, HealthResponse, Pydantic behavior | 2 min | 2026-04-23 | ✅ PASS (30 passed, Phase 6 no regression) |
| message_service_unit_test | tests/unit/test_message_service.py | MessageService, UnifiedMessage, ToolCallInfo (SSE message unification) | 2 min | 2026-04-23 | ⚠️ FILE NOT FOUND (stale entry) |
| api_router_extraction_test | tests/unit/test_api_router_extraction.py | Phase 3 router extraction: route registration, app.state, backward compat, _validate_instance_mode, _get_manager DI, router structure, API size | 2 min | 2026-04-23 | ✅ PASS (47 passed, Phase 6 no regression) |
| phase5_jobs_router_test | tests/unit/test_phase5_jobs_router.py | Phase 5 jobs router split: route registration, _release_job_lock scenarios, backward compat, service dependency, sub-router structure | 2 min | 2026-04-23 | ✅ PASS (34 passed, Phase 6 no regression) |
| phase4_manager_decomposition_test | tests/unit/test_phase4_manager_decomposition.py | Phase 4 manager decomposition: facade delegation, module-level functions, inner classes, service DI, fuzzy matching, cancellation service, title generation, circular imports | 2 min | 2026-04-23 | ✅ PASS (73 passed, Phase 6 no regression) |
| rag_completion_registry_test | tests/unit/services/test_completion_registry.py | CompletionRegistry: register/complete/wait_for, buffered completions, timeout, thread safety, stale cleanup, invoke_agent_and_wait integration, semaphore deadlock prevention | 2 min | 2026-04-26 | ✅ PASS (feature/rag-knowledge-toolset, 0 failures) |
| rag_client_test | tests/unit/rag/test_client.py | RAG HTTP client: config, headers, schemas, all API methods (insert/query/graph/entity/relation/docs/status), error handling, connection retry, is_rag_enabled edge cases | 2 min | 2026-05-07 | ✅ PASS (45 tests, feature/kb-disable-when-no-lightrag, +2 edge case tests) |
| rag_tools_test | tests/unit/tools/test_rag_tools.py | 16 RAG tools: factory pattern, graceful disable, defensive attribute access, mock client, output formatting, error handling | 2 min | 2026-05-04 | ✅ PASS (25 tests, fix/rag-tools-5-bugs, +2 get_entity tests) |
| rag_workspace_scoping_test | tests/unit/rag/test_workspace_scoping.py | LightRAG workspace scoping: project name resolution, _sanitize_workspace, fallback to project_id, edge cases | 2 min | 2026-05-04 | ✅ PASS (25 tests, fix/rag-search-workspace-mismatch, no regression) |
| llm_model_override_test | tests/unit/test_llm_config_override.py | Per-agent LLM model override: _build_llm_config, registry llm_model parsing, spawn_instance integration | 2 min | 2026-04-27 | ✅ PASS (feature/agent-llm-model, 9 tests, 0 failures) |
| title_generation_trigger_test | tests/unit/services/test_title_generation_trigger.py | Title generation trigger: 3 completion paths, non-blocking, idempotency, edge cases, fire-and-forget | 2 min | 2026-05-01 | ✅ PASS (13 tests, 0 failures, fix/instance-list-title) |
| reasoning_content_roundtrip_test | tests/unit/test_reasoning_content_roundtrip.py | reasoning_content passback: _get_request_payload preserves reasoning in AIMessages, empty strings, mixed types, tool messages | 2 min | 2026-05-04 | ✅ PASS (8 tests, 0 failures) |
| reasoning_content_edge_cases_test | tests/unit/test_reasoning_content_edge_cases.py | reasoning_content edge cases: SystemMessage, multi-turn, human-only, reasoning alternate key gap | 2 min | 2026-05-04 | ✅ PASS (6 tests, 0 failures) |
| reasoning_content_fallback_test | tests/unit/test_reasoning_content_fallback.py | reasoning_content fallback chain: empty string preserved, reasoning key fallback, response_metadata fallback, streaming fallbacks, non-string logging safety | 2 min | 2026-05-05 | ✅ PASS (7 tests, 0 failures, fix/reasoning-content-bugs) |

## Integration Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| integration_test | test/packs/integration_test.sh | All integration tests (require OPENAI_API_KEY, auto-skip without) | 5 min | 2026-04-24 | ❌ FAIL (37 passed, 6 failed — PRE-EXISTING, not system_default_project) |

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
