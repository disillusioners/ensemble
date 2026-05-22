# Test Packs

## Summary
- Total: 58 packs
- Unit: 49 | Integration: 1 | Mock: 2 | E2E: 6

## Unit Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| core_unit_test | test/packs/core_unit_test.sh | Core daemon (agents, config, loader, manager, models, tools, persistence, queue, registry, telegram) + tool filter | 2 min | 2026-05-22 | ✅ PASS (4433/4433, feature/fix-mcp-cold-load, 0 failures) |
| sources_unit_test | test/packs/sources_unit_test.sh | Sources subsystem (circuit breaker, dispatcher, mapper, persistence, rate limiter, registry) | 2 min | 2026-04-24 | ✅ PASS (137 passed, system_default_project no regression) |
| compaction_unit_test | test/packs/compaction_unit_test.sh | Compaction, find_near_instance, graph retry, idle timeout, LLM error classifier, response validation | 2 min | 2026-04-25 | ✅ PASS (fuzzy-match branch, find_near_instance: 26/26, no regression) |
| api_unit_test | test/packs/api_unit_test.sh | API endpoints, scheduler adapter, spawn instance | 2 min | 2026-05-19 | ✅ PASS (feature/builtin-mcp-servers all phases, no regression) |
| vision_unit_test | tests/unit/test_vision.py | Vision backend pipeline (validation, multimodal construction, serialization, DB storage) | 2 min | 2026-04-23 | ✅ PASS (45 tests, Phase 6 no regression) |
| job_queue_unit_test | test/packs/job_queue_unit_test.sh | Job queue full suite + Phase 1-5 + DLQ retry + replay-all + project_id injection + soft delete + 42 tool pack tests | 2 min | 2026-04-29 | ✅ PASS (991 passed, 19 skipped, kb-fifo-queue no regression) |
| jober_watch_integration_test | tests/job_queue/test_jober_watch_integration.py | Phase 3 jober watch: 7 terminal paths, 13 edge cases, notification format, tool registration, agent definition, crash recovery | 2 min | 2026-04-24 | ✅ PASS (38 passed, 0 failed, 2 benign bugs found) |
| frontend_unit_test | frontend/jest.config.js | Angular frontend full suite (models, services, SSE, components, message-input image upload, api.service, mcp-server CRUD, dialog template pills + JSON editor, **test connection button + SSRF**, **notification.service WAV/audio unlock/cleanup**) | 2 min | 2026-05-23 | ✅ PASS (616/616, feature/notification-sound: +39 notification tests) |
| worker_notification_test | tests/test_worker_notification.py | Worker notification mechanism, race conditions, lifecycle integration (real threads) | 2 min | 2026-04-23 | ✅ PASS (14 passed, Phase 6 no regression) |
| models_split_unit_test | tests/unit/test_models_split.py | Phase 2 models split: backward compat, __all__ completeness, cross-module refs, instantiation, HealthResponse, Pydantic behavior | 2 min | 2026-04-23 | ✅ PASS (30 passed, Phase 6 no regression) |
| message_service_unit_test | tests/unit/test_message_service.py | MessageService, UnifiedMessage, ToolCallInfo (SSE message unification) | 2 min | 2026-04-23 | ⚠️ FILE NOT FOUND (stale entry) |
| api_router_extraction_test | tests/unit/test_api_router_extraction.py | Phase 3 router extraction: route registration, app.state, backward compat, _validate_instance_mode, _get_manager DI, router structure, API size | 2 min | 2026-04-23 | ✅ PASS (47 passed, Phase 6 no regression) |
| phase5_jobs_router_test | tests/unit/test_phase5_jobs_router.py | Phase 5 jobs router split: route registration, _release_job_lock scenarios, backward compat, service dependency, sub-router structure | 2 min | 2026-04-23 | ✅ PASS (34 passed, Phase 6 no regression) |
| phase4_manager_decomposition_test | tests/unit/test_phase4_manager_decomposition.py | Phase 4 manager decomposition: facade delegation, module-level functions, inner classes, service DI, fuzzy matching, cancellation service, title generation, circular imports | 2 min | 2026-04-23 | ✅ PASS (73 passed, Phase 6 no regression) |
| rag_completion_registry_test | tests/unit/services/test_completion_registry.py | CompletionRegistry: register/complete/wait_for, buffered completions, timeout, thread safety, stale cleanup, invoke_agent_and_wait integration, semaphore deadlock prevention | 2 min | 2026-04-26 | ✅ PASS (feature/rag-knowledge-toolset, 0 failures) |
| rag_config_auto_test | tests/unit/rag/test_config.py | RAG auto-test on startup: auto_test_rag(), disable_rag(), enable_rag(), from_env() resilience, auth failure, timeout, connection refused, invalid LIGHTRAG_TIMEOUT | 2 min | 2026-05-22 | ✅ PASS (27/27, RAG auto-test feature, 0 failures) |
| rag_client_test | tests/unit/rag/test_client.py | RAG HTTP client: config, headers, schemas, all API methods (insert/query/graph/entity/relation/docs/status), error handling, connection retry, is_rag_enabled edge cases | 2 min | 2026-05-22 | ✅ PASS (46 tests, RAG auto-test regression, no regressions) |
| rag_tools_test | tests/unit/tools/test_rag_tools.py | 16 RAG tools: factory pattern, graceful disable, defensive attribute access, mock client, output formatting, error handling | 2 min | 2026-05-22 | ✅ PASS (25 tests, RAG auto-test regression, no regressions) |
| rag_workspace_scoping_test | tests/unit/rag/test_workspace_scoping.py | LightRAG workspace scoping: project name resolution, _sanitize_workspace, fallback to project_id, edge cases | 2 min | 2026-05-22 | ✅ PASS (24 tests, RAG auto-test regression, no regressions) |
| llm_model_override_test | tests/unit/test_llm_config_override.py | Per-agent LLM model override: _build_llm_config, registry llm_model parsing, spawn_instance integration | 2 min | 2026-04-27 | ✅ PASS (feature/agent-llm-model, 9 tests, 0 failures) |
| title_generation_trigger_test | tests/unit/services/test_title_generation_trigger.py | Title generation trigger: 3 completion paths, non-blocking, idempotency, edge cases, fire-and-forget, enqueue/send_message triggers | 2 min | 2026-05-18 | ✅ PASS (26 tests: 13 existing + 13 new timing triggers, fix/title-generation-timing) |
| reasoning_content_roundtrip_test | tests/unit/test_reasoning_content_roundtrip.py | reasoning_content passback: _get_request_payload preserves reasoning in AIMessages, empty strings, mixed types, tool messages | 2 min | 2026-05-04 | ✅ PASS (8 tests, 0 failures) |
| reasoning_content_edge_cases_test | tests/unit/test_reasoning_content_edge_cases.py | reasoning_content edge cases: SystemMessage, multi-turn, human-only, reasoning alternate key gap | 2 min | 2026-05-04 | ✅ PASS (6 tests, 0 failures) |
| reasoning_content_fallback_test | tests/unit/test_reasoning_content_fallback.py | reasoning_content fallback chain: empty string preserved, reasoning key fallback, response_metadata fallback, streaming fallbacks, non-string logging safety | 2 min | 2026-05-05 | ✅ PASS (7 tests, 0 failures, fix/reasoning-content-bugs) |
| windows_path_workdir_test | tests/unit/test_filesystem_workdir.py | Windows path compatibility: _normed_contains, _is_within_workdir, symlink escape, empty TEMP/TMP bypass, mocked Windows behavior | 2 min | 2026-05-16 | ✅ PASS (21 tests, 0 failures, feature/windows-path-fix) |
| mcp_server_crud_unit_test | tests/unit/test_mcp_server_crud.py | MCP Server CRUD backend: models, schemas, repository, router, integration | 2 min | 2026-05-19 | ✅ PASS (55 tests, feature/builtin-mcp-servers all phases, no regression) |
| mcp_runtime_integration_test | tests/unit/test_mcp_runtime_integration.py | MCP runtime integration: full flow, resilience, restore, edge cases, lifecycle cleanup | 2 min | 2026-05-20 | ✅ PASS (16 tests, fix/mcp-stdio-connection-init) |
| context7_unit_test | tests/unit/test_context7_builtin.py | Context7 built-in MCP server: properties, base config, config schema, build config, parse config, registry, bootstrap, npx unavailability | 2 min | 2026-05-20 | ✅ PASS (25 tests, fix/mcp-stdio-connection-init) |
| mcp_warmup_pool_unit_test | tests/unit/test_mcp_warmup_pool.py | MCP warm-up pool: lifecycle, acquire, replenish, health check, drain, liveness probe, exception logging, CancelledError handling, BaseException propagation, **retry logic** (3 attempts, backoff, timeout, log levels) | 2 min | 2026-05-20 | ✅ PASS (40 tests, fix/mcp-stdio-connection-init, ManagedClientSession verified) |
| mcp_connection_manager_unit_test | tests/unit/test_mcp_connection_manager.py | MCP connection manager: transfer_session(), pool integration | 2 min | 2026-05-20 | ✅ PASS (19 tests, fix/mcp-stdio-connection-init, ManagedClientSession verified) |
| mcp_service_pool_unit_test | tests/unit/test_mcp_service.py | MCP service pool-aware: preload, liveness probe, graceful degradation | 2 min | 2026-05-20 | ✅ PASS (25 tests, fix/mcp-stdio-connection-init) |
| gaia_agent_unit_test | tests/unit/test_gaia_agent.py | Gaia agent: meta.json validation, registry discovery, agent loading, tool filtering, script accessibility, full pipeline | 2 min | 2026-05-20 | ✅ PASS (44/44, fix/mcp-tools-not-available-to-llm FIXED 2 pre-existing failures) |
| memory_redirect_unit_test | tests/unit/tools/test_inner_soul_redirect.py | Phase 1 bug fixes: target="memories", honest error messages, classification fallback, RAG redirect | 2 min | 2026-05-19 | ✅ PASS (85 tests, feature/unified-memory-architecture) |
| memory_compound_unit_test | tests/unit/tools/test_inner_soul_compound.py | Phase 2 compound requests: AND splitting, semicolons, sentence boundaries, per-part classification | 2 min | 2026-05-19 | ✅ PASS (48 tests, feature/unified-memory-architecture) |
| memory_compaction_unit_test | tests/unit/tools/test_inner_soul_compaction.py | Phase 3 compaction: file locking, atomic writes, deduplication, structure preservation | 2 min | 2026-05-19 | ✅ PASS (42 tests, feature/unified-memory-architecture) |
| memory_archive_unit_test | tests/unit/tools/test_archive_lifecycle.py | Phase 4 archive: path validation, traversal protection, symlinks, auto-archive, rate limiting | 2 min | 2026-05-19 | ✅ PASS (29 tests, feature/unified-memory-architecture) |
| memory_integration_test | tests/test_memory_integration.py | Integration: full lifecycle, compound requests, concurrent writes, RAG redirect, edge cases, regression | 2 min | 2026-05-19 | ✅ PASS (28 tests, feature/unified-memory-architecture) |
| memory_edge_cases_test | tests/unit/tools/test_memory_edge_cases.py | Edge cases: path traversal, symlinks, rate limiting, compaction boundaries, concurrent writes, collision, unicode | 2 min | 2026-05-19 | ✅ PASS (48 tests, feature/unified-memory-architecture) |
| ce_tools_unit_test | tests/unit/tools/test_critical_experience.py | Critical Experience tools: add (validation, categories, priorities), merge (keyword overlap, shorter summary), eviction (priority order, max capacity), list, remove | 2 min | 2026-05-20 | ✅ PASS (36 tests, feature/critical-experience Phase 5) |
| ce_injection_unit_test | tests/unit/test_critical_experience_injection.py | format_project_context injection: CE section formatting, priority icons, deduplication, non-dict skip, reference handling | 2 min | 2026-05-20 | ✅ PASS (14 tests, feature/critical-experience Phase 5) |
| ce_schema_unit_test | tests/unit/test_critical_experience_schema.py | CriticalExperience model validation, Project integration, migration file (UP/DOWN, default []) | 2 min | 2026-05-20 | ✅ PASS (20 tests, feature/critical-experience Phase 5) |
| ce_api_unit_test | tests/unit/test_critical_experience_api.py | Projects API: GET /projects/{id} and GET /projects include critical_experience in response | 2 min | 2026-05-20 | ✅ PASS (13 tests, feature/critical-experience Phase 5) |
| mcp_test_connection_unit_test | tests/unit/test_mcp_test_connection.py | MCP test connection: SSRF validation (42→68), endpoint logic (11), helper function (5) | 2 min | 2026-05-21 | ✅ PASS (68/68, feature/fix-mcp-localhost-block, SSRF localhost default=true verified) |
| mcp_disable_flags_unit_test | tests/unit/test_builtin_mcp_servers.py | MCP disable flags: is_builtin_disabled helper, bootstrap disable/enable, API protection, config validation, registry, model schema (12 classes, 74 tests) | 2 min | 2026-05-22 | ✅ PASS (74/74, feature/mcp-disable-flags, no regressions) |
| mcp_cold_load_race_unit_test | tests/unit/test_mcp_cold_load_race.py | MCP cold-load race condition: preload before restore, hot path no preload, graceful degradation, async delegation | 2 min | 2026-05-22 | ✅ PASS (6/6, feature/fix-mcp-cold-load) |
| project_history_tools_unit_test | tests/unit/test_project_history_tools.py | Project history agent tools: add (validation, truncation, special chars), list (clamping, filter, pagination), search (format, special chars), delete (ownership), constants | 2 min | 2026-05-22 | ✅ PASS (38/38, project_history feature, no regressions) |
| project_history_injection_unit_test | tests/unit/test_project_history_injection.py | Project history context injection: emoji icons, section rendering, limit 10, ordering, error handling, CE+history coexistence, ProjectResponse serialization | 2 min | 2026-05-22 | ✅ PASS (28/28, project_history feature, no regressions) |

## Integration Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| integration_test | test/packs/integration_test.sh | All integration tests (require OPENAI_API_KEY, auto-skip without) | 5 min | 2026-04-24 | ❌ FAIL (37 passed, 6 failed — PRE-EXISTING, not system_default_project) |

## Mock Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| mock_message_service_test | tests/mock_message_service.py | MessageService SSE critical paths (emit, error isolation, duplicate prevention, edge cases) | 2 min | 2026-04-23 | ⚠️ FILE NOT FOUND (stale entry) |
| mock_job_queue_test | test/packs/mock_job_queue_test.sh | Mock job queue API test | 5 min | 2026-04-23 | ❌ FAIL (48 fixture errors — PRE-EXISTING, not Phase 6) |

## E2E Test Packs

| Pack | Location | Scope | Timeout | Last Run | Status |
|------|----------|-------|---------|----------|--------|
| send_stop_button_e2e_test | frontend/e2e/send-stop-button.spec.ts | SSE real-time status: send/stop button timing, SSE streaming, direct navigation (6 tests) | 5 min | 2026-05-15 | ✅ PASS (6/6 passed, Stop button fix verified, 114ms direct navigation) |
| stop_resume_spawn_e2e_test | test/packs/stop_resume_spawn_e2e_test.py | Stop→Resume→Spawn Instance: verify spawn_instance works after stop/resume, multiple cycles, no "no running event loop" | 5 min | 2026-05-15 | ✅ PASS (async def fix verified, spawn works after resume, 2 cycles, no errors) |
| pause_ttl_cold_resume_e2e_test | test/packs/pause_ttl_cold_resume_e2e_test.py | Pause TTL + Cold Resume: pause→paused_at set→daemon restart→cold resume→completed, status transitions | 5 min | 2026-05-16 | ✅ PASS (9/9 steps, cold resume from checkpoint verified) |
| project_tabs_e2e_test | frontend/e2e/project-tabs.spec.ts | Project tabs: default state, add/switch/close tab, persistence, menu filtering | 5 min | 2026-04-23 | ✅ PASS |
| mcp_tools_e2e_test | tests/e2e/test_mcp_tools.py | MCP tools visible to LLM: API returns MCP tool names, LLM response mentions MCP tools, daemon health check | 5 min | 2026-05-22 | ✅ PASS (8/8 checks, feature/fix-mcp-cold-load) |
| mcp_tools_restore_e2e_test | tests/e2e/test_mcp_tools_restore.py | MCP tools on restored instances: create instance → verify MCP → restart daemon → re-verify MCP on same instance | 5 min | 2026-05-22 | ✅ PASS (16/16 checks, feature/fix-mcp-cold-load) |

| notification_broadcaster_unit_test | tests/unit/test_notification_broadcaster.py | NotificationBroadcaster: connection management, broadcasting, queue-full, dead connection cleanup, singleton | 2 min | 2026-05-20 | ✅ PASS (17/17, notification system) |
| notification_sse_endpoint_test | tests/unit/test_notification_sse_endpoint.py | SSE endpoint integration: queue management, multi-client broadcast, root completion flow, event structure, JSON format, heartbeat | 2 min | 2026-05-20 | ✅ PASS (11/11, notification system) |
| notification_lifecycle_hook_test | tests/unit/test_notification_lifecycle_hook.py | Lifecycle hook: root instance notification, child exclusion, payload correctness, edge cases, EventBus integration | 2 min | 2026-05-20 | ✅ PASS (15/15, notification system) |

---

## Updating PACKS.md

Update after each test run:
- **Last Run**: timestamp
- **Status**: PASS/FAIL/TIMEOUT
- Add new entry for new packs
- Mark deprecated packs as DEPRECATED
