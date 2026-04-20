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

## Test Results (Latest: 2026-04-20 Vision Frontend Phase 2)

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
