# Agents Ensemble — Tester README

## Project Overview
Persistent multi-agent daemon built with LangGraph. Agents defined by markdown files with HTTP API, OpenAI-compatible LLM support, session hierarchy for agent spawning/communication, and SQLite checkpoints for crash recovery.

## Test Framework
- **pytest** with `tests/conftest.py` that mocks langgraph modules
- Integration tests under `tests/integration/` (require OPENAI_API_KEY for real LLM calls)
- Unit tests at `tests/test_*.py` and `tests/unit/`
- **conftest.py** pre-populates `sys.modules` with langgraph mocks — all unit tests use these

## Key Test Patterns
- `conftest.py` pre-populates `sys.modules` with langgraph mocks — all unit tests use these
- Tools tested by creating tool then calling `.invoke({"param": value})`
- Filesystem tests use `tmp_path` fixture
- Cache tests use `time.sleep(0.1)` between mtime changes
- Config tests use YAML fixtures and env var manipulation

## Test Structure
```
tests/
├── conftest.py              # Unit test fixtures (mocks langgraph)
├── test_*.py                # Unit tests (top-level)
├── unit/                    # Unit tests (subdirectory, no __init__.py needed)
├── integration/
│   ├── conftest.py          # Integration fixtures (real config, no langgraph mocks)
│   └── test_*.py            # Integration tests (skip without OPENAI_API_KEY)
├── job_queue/               # Job queue tests
├── message_queue_redesign/  # Message queue redesign tests (Phase 1-3)
│   ├── conftest.py          # MQ test fixtures (in-memory SQLite, test repos)
│   ├── test_event_repository.py   # Event repository tests
│   ├── test_stale_task_recovery.py # Stale task recovery tests
│   ├── test_task_repository.py    # Task repository + atomic claim tests
│   └── test_worker_pool.py        # Worker pool lifecycle tests
└── mock_*.py                # Mock test scripts
```

## Compaction Testing
- `daemon/compaction.py` — Full compaction engine
- `daemon/graph.py` — `SessionState(MessagesState)` with `compacted_at`
- `daemon/manager.py` — `_maybe_compact_context()` integration
- `daemon/config.py` — `CompactionConfig`
- `daemon/loader.py` — `estimate_messages_tokens()` (uses tiktoken)

### Key Types for Testing
- `CompactionContext` — Input container for compaction
- `CompactionResult` — Output with replacement_messages, tokens, type
- `MessageGroup` — Atomic group (single or tool_sequence)
- `SessionState(MessagesState)` — LangGraph state with `compacted_at`

### Important Function Signatures
- `identify_boundary_groups(messages: list[BaseMessage]) -> list[MessageGroup]`
- `select_compactable_groups(groups, recent_window, min_window, context_window, system_prompt_tokens, estimate_fn, config_threshold) -> (compactable, preserved, actual_window)`
- `emergency_truncate(messages, max_tokens, estimate_fn, max_tool_response_chars, max_human_message_chars) -> list[BaseMessage]`
- `_truncate_batch_to_fit(batch_groups, max_tokens, tokenizer_fn, max_tool_response_chars) -> list[MessageGroup]`
- `get_model_context_limit(model_name, config=None) -> int`
- `ContextCompactor._build_replacement_messages(compactable_groups, preserved_groups, summary) -> list[BaseMessage]`
- `ContextCompactor._is_recently_compacted(last_compacted_at) -> bool`
- `ContextCompactor.compact_state(context) -> CompactionResult | None`
- `ContextCompactor._merge_summaries(partial_summaries, context) -> SystemMessage`
- `ContextCompactor._call_summarization_llm(prompt, context) -> str`

## Test Results (Latest: 2026-05-15 Send/Stop Button Toggle E2E)

### Send/Stop Button Toggle E2E (main branch)
- **7 E2E tests passed**, 0 failed (Playwright browser automation)
- **Critical discovery**: `isStreaming` signal means "SSE connected", NOT "actively streaming response"
- **Behavior documented**: Stop button shows on page load (SSE connects immediately), stays after clicking stop
- **Send button only appears when SSE disconnects** (error, navigation away, manual disconnect)
- **Visual checks passed**: Stop button has proper square icon, correct dimensions, red color
- **Angular probe technique**: Used `window.ng.getComponent()` to manually disconnect SSE in tests
- **Test file**: `frontend/e2e/send-stop-button.spec.ts`
- See `.agents/tester/RESULTS/2026-05-15-send-stop-button-toggle.md` for full report

### Send/Stop Button Toggle Status: ✅ READY (behavior documented, UX concern noted)

### Stop Instance with Child Cascade (main branch)
- **901 tests passed**, 0 failed (8 skipped)
- **14 stop-cascade tests** — ALL PASS (11 unit + 2 API + 1 delegation)
- **Mock accuracy verified** — All test mocks match real service/repository interfaces
- **Integration testing** — Daemon spun up, API endpoint tested end-to-end:
  - Stop parent with children → all cascade to idle ✅
  - Stop non-existent instance → 404 ✅
  - Already-idle → graceful no-op ✅
  - Response format correct ✅
- **Edge cases verified in real code**: circular refs, exceptions during child stop, depth limit, resumability
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean implementation
- **Minor findings** (non-blocking): mutual circular ref not tested, no try/except around update_status in real code
- See `.agents/tester/RESULTS/2026-05-15-stop-instance-cascade.md` for full report

### Stop Instance with Child Cascade Status: ✅ READY

### KB Tools Conditional Disabling (branch feature/kb-disable-when-no-lightrag)
- **1,029 tests passed**, 0 failed (2 pre-existing unrelated failures)
- **110 new feature tests** (61 loader + 49 knowledge tools) — ALL PASS
- **~15 gap coverage tests added** — tool list verification, cache toggle, H1 stripping, edge cases
- **6/6 test scenarios validated**: Tool availability, Prompt assembly, Cache behavior, Per-agent files, Edge cases, Backward compat
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **1 quick fix applied** — test assertion fix for H1 stripping test (commit e4a2fbd)
- **Minor finding**: Whitespace-only LIGHTRAG_HOST treated as enabled (documented, not blocking)
- See `.agents/tester/RESULTS/2026-05-07-kb-disable-when-no-lightrag.md` for full report

### KB Tools Conditional Disabling Status: ✅ READY

### Reasoning Content Fallback Bug Fixes (branch fix/reasoning-content-bugs)
- **21 reasoning tests passed** (8 roundtrip + 6 edge cases + 7 fallback) — ALL PASS
- **7 new tests** covering all 4 bug fixes: fallback chain, empty string preservation, streaming reasoning key, logging safety
- **0 regressions** in existing tests
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean implementation
- See `.agents/tester/RESULTS/2026-05-05-reasoning-content-fallback.md` for full report

### Reasoning Content Fallback Bug Fixes Status: ✅ READY

### RAG Tools 5 Bug Fixes (branch fix/rag-tools-5-bugs)
- **93 RAG tests passed** (43 client + 25 workspace scoping + 25 tools) — ALL PASS
- **5/5 bugs validated**: updated_name forwarding, rag_get_entity tool, docs fixes, delete endpoint
- **1000+ full suite tests passed** (3 pre-existing failures unrelated to RAG)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **1 quick fix applied** — added rag_get_entity unit tests + fixed tool count 15→16 (commit 98ce3cb)
- See `.agents/tester/RESULTS/2026-05-04-rag-tools-5-bug-fixes.md` for full report

### RAG Tools 5 Bug Fixes Status: ✅ READY

### RAG Search Workspace Mismatch Fix
- **68 RAG tests passed** (43 client + 25 workspace scoping) — ALL PASS, includes 2 new header behavior tests
- **9 integration checks passed** — workspace defaults, header behavior, request overrides
- **4 edge case checks passed** — whitespace-only, tabs, leading/trailing spaces
- **3306/3308 full suite tests passed** (2 pre-existing failures in test_invoked_as_tool.py, unrelated)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **1 quick fix applied** — strip whitespace from workspace param in _request() (commit fe1e826)
- See `.agents/tester/RESULTS/2026-05-04-rag-workspace-mismatch-fix.md` for full report

### RAG Search Workspace Mismatch Status: ✅ READY

### Reasoning Content Passback Fix
- **14 reasoning tests passed** (8 roundtrip + 6 edge cases) — ALL PASS
- **520+ full unit tests passed**, 1 pre-existing failure (unrelated: jober watch)
- **0 regressions** in existing tests
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **Known gap documented**: `additional_kwargs["reasoning"]` alternate key not injected (low risk)
- **0 quick fixes needed** — Clean fix, no issues
- See `.agents/tester/RESULTS/2026-05-04-reasoning-content-passback.md` for full report

### Reasoning Content Passback Status: ✅ READY

### LightRAG Workspace Scoping (refactor: use project name for LIGHTRAG-WORKSPACE)
- **117 RAG tests passed** (workspace scoping 24 + rag_tools 23 + rag_client 42 + completion_registry 28)
- **24 new tests** — ALL PASS (sanitize_workspace, get_project_workspace, edge cases, integration)
- **0 regressions** in existing RAG tests
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean refactor, no issues
- See `.agents/tester/RESULTS/2026-05-02-rag-workspace-scoping.md` for full report

### LightRAG Workspace Scoping Status: ✅ READY

### Instance Title Generation Trigger Fix (branch fix/instance-list-title)
- **117 existing tests passed** — no regressions (2 pre-existing failures in knowledge_tools async mocking, unrelated)
- **13 new tests** — ALL PASS (trigger method, 3 completion paths, non-blocking, idempotency, edge cases, fire-and-forget)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean fix, no issues
- See `.agents/tester/RESULTS/2026-05-01-title-generation-trigger.md` for full report

### Title Generation Trigger Status: ✅ READY

### Experiencer Fire-and-Forget Feature
- **47 knowledge_tools tests passed**, 0 failed — ALL PASS
- **991 job_queue + 47 API tests** — no regressions
- **5/5 verification points passed** — fire-and-forget, queue routing, edge cases, idempotency keys
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean feature, no issues
- See `.agents/tester/RESULTS/2026-04-29-experiencer-kb-queue.md` for full report

### Experiencer Fire-and-Forget Status: ✅ READY

### KB-FIFO Queue Feature
- **1,418 tests passed**, 0 failed, 27 skipped — ALL PASS (no regressions)
- **3 test packs**: job_queue (991), core (624), api (193) — all pass
- **New system_kb_fifo_queue** — auto-provisioning, reserved name, KB job routing, FIFO properties verified
- **Quick fix**: Pre-existing API test modernization (commit 3326259)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- See `.agents/tester/RESULTS/2026-04-29-kb-fifo-queue.md` for full report

### KB-FIFO Queue Status: ✅ READY

### Per-Agent LLM Model Override Feature
- **3,205 tests passed**, 0 failed, 27 skipped — ALL PASS (no regressions)
- **9 new tests** — Registry llm_model parsing (3), _build_llm_config (4), spawn_instance integration (2)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean feature, no issues
- See `.agents/tester/RESULTS/2026-04-27-agent-llm-model-override.md` for full report

### Per-Agent LLM Model Override Status: ✅ READY

### RAG Knowledge Toolset Feature
- **3,097 tests passed**, 0 failed, 176 skipped — ALL PASS
- **177 RAG-specific tests** — CompletionRegistry, RAG client, 15 RAG tools, knowledge tools, inner_soul redirect
- **Agent definitions verified** — Explorer (rag+filesystem), Experiencer (rag), all others (knowledge) ✅
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean feature, no issues
- See `.agents/tester/RESULTS/2026-04-26-rag-knowledge-toolset.md` for full report

### RAG Feature Status: ✅ READY

### Phase 3: Jober Agent Watch System Integration & Testing
- **38 Phase 3 tests** — ALL PASS (0 failed)
- **986 job_queue tests** — ALL PASS (19 skipped, 0 failed) — no regressions
- **120 tools/registry/loader tests** — ALL PASS
- **2 benign bugs found** — duplicate `add_watch()` calls in `watch_job` and `watch_jobs` tools
- **dev.sh validated** — runs for 30 seconds without crash
- See `.agents/tester/RESULTS/2026-04-24-phase3-jober-watch-integration.md` for full report

### Phase 3 Status: ✅ READY

### Code Quality Refactoring Phase 5 — Jobs Router Cleanup & Lock Deduplication
- **2,185 backend tests** — ALL PASS (27 skipped, 0 failed) — no regressions
- **73 new Phase 4 tests** — ALL PASS (facade delegation, module-level functions, inner classes, service DI, fuzzy matching, cancellation service, title generation, circular imports)
- **278 frontend tests** — ALL PASS (0 failed)
- **dev.sh validated** — Server runs cleanly for 30 seconds with decomposed manager
- **2 minor test fixes** — test attribute check approach + AsyncMessageResult field
- See `.agents/tester/RESULTS/2026-04-23-phase4-manager-decomposition.md` for full report

### Phase 4 Status: ✅ READY

### Code Quality Refactoring Phase 5 — Jobs Router Cleanup & Lock Deduplication
- **2,327 backend tests** — ALL PASS (27 skipped, 0 failed) — no regressions
- **34 new Phase 5 tests** — ALL PASS (route registration, _release_job_lock scenarios, backward compat, service dependency, sub-router structure)
- **278 frontend tests** — ALL PASS (0 failed)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **0 quick fixes needed** — Clean refactoring, no issues
- See `.agents/tester/RESULTS/2026-04-23-phase5-jobs-router-cleanup.md` for full report

### Phase 5 Status: ✅ READY

### Code Quality Refactoring Phase 3 — API Router Extraction
- **2,151 backend tests** — ALL PASS (27 skipped, 0 failed) — no regressions
- **47 new Phase 3 tests** — ALL PASS (route registration, app.state, backward compat, _validate_instance_mode, _get_manager DI, router structure, API size)
- **278 frontend tests** — ALL PASS (0 failed)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **Live API validation** — All 12 endpoint groups respond correctly
- **2 quick fix commits** — Missing Any import + test fixture updates for app.state migration
- See `.agents/tester/RESULTS/2026-04-23-phase3-api-router-extraction.md` for full report

### Phase 3 Status: ✅ READY
- **1,968 backend tests** — ALL PASS (19 skipped, 0 failed) — no regressions
- **30 new Phase 2 tests** — ALL PASS (backward compat, __all__, cross-module refs, instantiation, HealthResponse, Pydantic behavior)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **0 quick fixes needed** — Clean split, no issues
- See `.agents/tester/RESULTS/2026-04-23-phase2-models-split.md` for full report

### Phase 2 Status: ✅ READY

### Code Quality Refactoring Phase 1 — Constants & Utilities Foundation
- **1,359 backend tests** — ALL PASS (19 skipped, 0 failed) — no regressions
- **68 new Phase 1 tests** — ALL PASS (constants, utils, backward compat, HTTP helpers, service dependency)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **0 quick fixes needed** — Clean refactoring, no issues
- See `.agents/tester/RESULTS/2026-04-23-phase1-constants-utilities.md` for full report

### Phase 1 Status: ✅ READY

### Vision Frontend Phase 2 — Image Upload UI (commits f4a3a93 + 6bdae97)
- **278 frontend tests** — ALL PASS (0 failed)
- **2,074 backend tests** — ALL PASS (0 failed, 27 skipped) — no regressions
- **Angular build** — SUCCESS (no compilation errors)
- **Web automation** — PASS (6/7 full, 1 partial due to instance state, not UI bug)
  - Chat input renders ✅ | Attach button (📎) present ✅ | Textarea ✅
  - Drag-drop zone ✅ | Image preview thumbnails ✅ | Remove button ✅
- **2 backend quick fixes** — project_list assertion + FIFO order in pending queries
- **dev.sh validated** — Server runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-20-vision-frontend-phase2.md` for full report

### Vision Frontend Phase 2 Status: ✅ READY

### Backend Vision Pipeline Phase 1 (commits 8ec692c + 650eef5)
- **45 vision unit tests** — ALL PASS (37 original + 8 edge-case additions)
- **All test packs pass** — No regressions from vision changes
- **2 quick fixes applied** — test_api.py images=None assertion + stale test file references
- **Tool binding verified** — Tools work without vision model configured
- **Text-only backward compatibility** — No regression
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-20-vision-backend-pipeline.md` for full report

### Backend Vision Pipeline Status: ✅ READY

### Internal Source Log Level Fix (commit 611ddcb)
- **12 new dispatcher tests** — Internal source log levels (dispatch_completed + dispatch_message paths) — ALL PASS
- **2515 total tests pass** (22 skipped, 0 failed) — no regressions
- **1 quick fix applied** — Updated version assertion in test_api.py ("0.1.0" → "0.1.1")
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-19-internal-source-log-level.md` for full report

### Internal Source Log Level Fix Status: ✅ READY

### Job Soft Delete Feature (branch feature/job-soft-delete)
- **34 new BE tests** — Repository (13) + API (11) + Scheduler safety (8) + Integration (2) — ALL PASS
- **35 new FE tests** — Model (7) + Service (11) + Component (17) — ALL PASS
- **953 job_queue tests pass** (14 skipped, 0 failed) — no regressions
- **267 FE tests pass** (10 suites, 0 failed) — no regressions
- **2 quick fixes** — Updated test files for renamed `hard_delete` methods
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- **CRITICAL**: All 9 execution-path methods verified to exclude soft-deleted jobs
- See `.agents/tester/RESULTS/2026-04-19-job-soft-delete.md` for full report

### Job Soft Delete Status: ✅ READY FOR MERGE

### Job Processor project_id injection (branch feature/job-autoinject-project-id)
- **8 new unit tests** — ALL PASS (project_id propagation, edge cases, no regressions)
- **865 job_queue tests pass** (14 skipped, 0 failed) — no regressions
- **ensure.md validated** — dev.sh ran clean for 30 seconds
- See `.agents/tester/RESULTS/2026-04-18-job-processor-project-id.md` for full report

### Job Processor project_id injection Status: ✅ READY FOR MERGE

### Merge access_memory into self (branch feature/merge-access-memory-self)
- **2407 non-integration tests pass** (0 failed, 22 skipped) — no regressions
- **Integration verification: 4/4 checks PASS** — self category has both tools, ToolFilter resolves correctly, startup validation works
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- **1 pre-existing integration test failure** (test_instance_title_generation_e2e — unrelated to branch)
- See `.agents/tester/RESULTS/2026-04-18-merge-access-memory-self.md` for full report

### Merge access_memory into self Status: ✅ READY FOR MERGE

### Per-Agent Tool Control Feature (branch feature/per-agent-tools, commits 5de34b0, 10fd317)
- **35 new tool filter tests** — ALL PASS
- **2410 total tests pass** (0 failed, 22 skipped) — no regressions
- **Integration validation**: All imports, category counts, smoke tests PASS
- **Edge cases**: All 5 verified (backward compat, deny-wins, category expansion, _mother)
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-19-per-agent-tool-control.md` for full report

### Per-Agent Tool Control Status: ✅ READY FOR MERGE

### DLQ Retry Feature (commits 4b2f5c2, 8decef9)
- **19 new backend tests** — Retry DEAD_LETTER job (9) + Bulk replay-all (10) — ALL PASS
- **16 new frontend tests** — DeadLetterItem model (7) + DLQ service methods (9) — ALL PASS
- **2362 total backend tests** (2340 passed, 22 skipped, 0 failed) — no regressions
- **232 total frontend tests** (10 suites, all pass) — no regressions
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-18-dlq-retry-feature.md` for full report

### DLQ Retry Feature Status: ✅ READY FOR MERGE

### Child-Parent Source Propagation Fix (commit 21ad4e1)
- **7 new tests added** to `tests/test_progressive_dispatch.py` — all PASS
- **32 total progressive dispatch tests** — ALL PASS
- **704 existing tests pass** (125 sources + 579 core) — no regressions
- **1 quick fix applied**: Narrowed `startswith("internal_")` to exact match on `internal_report`/`internal_error_report` only
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-17-child-parent-source-propagation.md` for full report

### Child-Parent Source Propagation Status: ✅ READY FOR MERGE

### Progressive Message Delivery — Initial (commit 388d64c)
- **17 new progressive dispatch tests** — ALL PASS (dispatcher routing, skip rules, dedup, cleanup, error handling, manager streaming)
- **704 existing tests pass** (125 sources + 579 core) — no regressions
- **1 quick fix applied**: Added try/except around adapter.send() in dispatch_message() for error resilience
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-17-progressive-message-delivery.md` for full report

### Progressive Message Delivery Status: ✅ READY FOR MERGE

### feature/sse-message-unification branch (commit 7f39b28)
- **1787 tests pass** (22 skipped, 0 failed) excluding integration
- **16 new message_service unit tests** — MessageService, UnifiedMessage, ToolCallInfo
- **24 new mock tests** — SSE critical paths (emit, error isolation, duplicate prevention, edge cases)
- **197 frontend tests pass** — no regressions
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- **4 quick fixes applied** (commit 7f39b28): async mock fixes, status count update
- See `.agents/tester/RESULTS/2026-04-12-sse-message-unification.md` for full report

### SSE Message Unification Status: ✅ READY FOR MERGE

### feature/worker-pool-followup branch (commit 3c396b8)
- **1789 tests pass** (22 skipped, 0 failed) excluding integration
- **13 notification tests pass × 3 runs** — flakiness check, all deterministic
- **5 new integration tests** — real Worker threads with threading.Event coordination
- **Spurious wakeup defense verified** — while loop + monotonic elapsed tracking works
- **Stop event check verified** — fast shutdown in wait_for_work()
- **ensure.sh validated** — Server starts and runs cleanly for 30 seconds
- **No regressions** from base branch
- See `.agents/tester/RESULTS/2026-04-11-worker-pool-followup.md` for full report

### Worker Pool Followup Status: ✅ READY FOR MERGE

### feature/worker-pool-optimization branch (previous)
- **1749 tests pass** (22 skipped, 0 failed) excluding integration
- **31 notification tests pass** — 8 original + 23 new edge case tests ALL pass
- **ensure.sh validated** — Server starts and runs cleanly for 30 seconds
- **Notification mechanism verified**: notify_work() → wait_for_work(), 3s safety-net timeout, metrics tracking
- **Edge cases verified**: rapid notifications, callback exceptions, shutdown, schedule_retry integration
- No regressions from base branch
- 1 quick fix applied: integration tests updated to use _event_bus API
- See `.agents/tester/RESULTS/2026-04-11-worker-pool-optimization.md` for full report

### Worker Pool Optimization Status: ✅ READY FOR MERGE (followup tested)

### Previous Results (feature/message-queue-redesign branch)
- **1704 tests pass** (22 skipped, 0 failed, 0 errors) excluding integration
- **290 message_queue_redesign tests pass** — Phase 1-6 redesign tests ALL pass
- **ensure.sh validated** — Server starts and runs cleanly for 30 seconds
- **Config loads correctly** — timeout=15.0min, retries=3, backoff=60s/3600s, grace=10s
- **All E2E critical paths verified**: timeout→retry→complete, max retries→permanent failure, exponential backoff
- No regressions, no quick fixes needed
- See `.agents/tester/RESULTS/2026-04-11-phase6-config-wiring-final.md` for full report

### Phase 6 Config & Wiring Status: ✅ READY — FEATURE COMPLETE

### Previous Results
- Phase 5: 1689 tests pass ✅ (22 skipped, 0 failed), 275 MQ tests pass
- Phase 4: 1623 tests pass ✅ (22 skipped, 0 failed), 132 MQ tests pass
- **34 new tests added** for Phase 4 (test_event_bus.py: DB-backed EventBus, cursor-based SSE)
- dev.sh validated and working (ensure.md: PASS)
- **Critical path gap**: Missing Last-Event-ID header/reconnection test (3/4 covered)
- See `.agents/tester/RESULTS/2026-04-09-phase4-sse-events-tests.md` for full report

### feature/job-queue-management branch (previous)
- **1492 tests pass** (22 skipped, 0 failed) excluding integration
- **402 job_queue tests pass** (14 skipped, 0 failed) — all Phase 1+2+3 tests pass
- **35 queue router API tests pass** — Phase 3 queue CRUD, IDOR, start/stop endpoints
- **197 frontend tests pass** (10 test suites) — including new queue service/model tests
- dev.sh validated and working (ensure.md: PASS)
- Review fix commit `98a6e7a` — all 7 fixes verified, no regressions, no test updates needed
- Integration tests have pre-existing failures (require OPENAI_API_KEY) — not Phase issues
- See `.agents/tester/RESULTS/2026-04-08-phase3-post-review-retest.md` for re-test details
- See `.agents/tester/RESULTS/2026-04-08-phase3-api-frontend-integration.md` for original Phase 3 details

## Frontend Tests (Angular 21)

- **Framework:** Jest with `jest-preset-angular`
- **Config:** `frontend/jest.config.js` + `frontend/setup-jest.ts`
- **Run:** `cd frontend && npx jest` (or `npm test`)
- **Execution time:** ~2.5s for all tests
- **Test helpers:** `frontend/src/app/testing/job-test-helpers.ts`

### Frontend Test Files
| File | Scope |
|------|-------|
| `frontend/src/app/models/job.model.spec.ts` | Job model types, helper functions (isTerminalStatus, getStatusColor, getPriorityColor) |
| `frontend/src/app/models/job-queue.model.spec.ts` | Queue model types, helper functions (getQueueStatusColor, getQueueTypeIcon, etc.) |
| `frontend/src/app/services/job.service.spec.ts` | HTTP calls (list, get, create, cancel, retry) |
| `frontend/src/app/services/job-sse.service.spec.ts` | SSE connection, events, reconnection |
| `frontend/src/app/services/queue.service.spec.ts` | Queue HTTP calls (list, create, get, update, delete, start, stop) |
| `frontend/src/app/pages/jobs/jobs.component.spec.ts` | Filters, job actions, drawer, project pause |
| `frontend/src/app/components/job-detail-drawer/job-detail-drawer.component.spec.ts` | Computed properties, template rendering |

## Current Focus
**Internal Source Log Level Fix — TESTING COMPLETE**

### Status: ✅ READY

**Latest:** 12 new tests pass (internal source log levels), 2515 total tests pass, dev.sh validated
**Key verified:** Internal sources (internal_*) → DEBUG, non-internal → ERROR, edge cases covered
**Commit:** `611ddcb`
**See RESULTS/2026-04-19-internal-source-log-level.md for full report**

### Previous Focus: Job Soft Delete Feature — TESTING COMPLETE

### Status: ✅ READY FOR MERGE

**Latest:** 34 BE tests pass (repository + API + scheduler safety), 35 FE tests pass (model + service + component), dev.sh validated
**Branch:** feature/job-soft-delete
**Commits:** `2cc8998` → `34cf89e` → `740efbf` → `4421c02` → `ae2b4f6` (implementation) + `9185a08` → `45b4bae` (tests)
**Key verified:** All 9 execution-path methods exclude deleted jobs, soft_delete() idempotent, API soft-deletes terminal / cancels active, restore works, scheduler never picks up deleted PENDING jobs
**See RESULTS/2026-04-19-job-soft-delete.md for full report**

### Previous Phase: Phase 2 — Task↔Job Feedback Loop — COMPLETE
**799 job_queue tests pass (14 skipped, 0 failed), 1138 core tests pass (8 skipped, 0 failed), dev.sh validated**

### Phase 6 Test File
- **test_timeout_retry_e2e.py** (10 tests): Config flow, timeout→retry→complete, max retries→permanent failure, exponential backoff, multiple timeouts→success, default config, env var overrides, stale recovery config threshold, real repo integration

### Phase 1-6 Test Files (13 test modules, 290 tests)
- **test_event_bus.py** (34 tests): Phase 4 — DB-backed EventBus, cursor-based SSE
- **test_event_repository.py** (18 tests): Event logging, message linking
- **test_message_flow.py** (23 tests): Phase 3 — enqueue_message_v2, completion checks, idempotency
- **test_stale_recovery_v2.py** (24 tests): Phase 5 — 5-step recovery protocol, graceful/force
- **test_stale_task_recovery.py** (19 tests): Phase 3 — Stale task detection and reset
- **test_task_repository.py** (25 tests): Phase 1-3 — Task CRUD, atomic claim, retry chain
- **test_task_retry_models.py** (28 tests): Phase 5 — Retry policy models, exponential backoff
- **test_task_retry_repository.py** (31 tests): Phase 5 — Retry scheduling, retry_scheduled guard
- **test_timeout_monitor.py** (18 tests): Phase 5 — Timeout detection, grace period
- **test_timeout_retry_e2e.py** (10 tests): Phase 6 — E2E config flow, timeout/retry chains
- **test_worker_pool.py** (13 tests): Phase 2 — Worker pool lifecycle
- **test_worker_timeout.py** (27 tests): Phase 5 — Worker timeout handling

**Branch:** feature/message-queue-redesign
- **Phase 6 (FINAL):** 1704 tests passed ✅ (290 in message_queue_redesign/, 10 new Phase 6 E2E tests)
- **Phase 5:** 1689 tests passed ✅ (275 in message_queue_redesign/)
- **Phase 4:** 1623 tests passed ✅ (132 in message_queue_redesign/, 34 new Phase 4 tests)
- **Phase 3:** 1581 tests passed ✅ (89 in message_queue_redesign/, 21 new tests)
