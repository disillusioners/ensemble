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

## Test Results (Latest: 2026-04-11 Phase 6 Config & Wiring — FINAL)

### feature/message-queue-redesign branch
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
**Phase 6 Config & Wiring — FINAL TESTING COMPLETE**

### Status: ✅ READY — FEATURE COMPLETE

**Latest:** 1704 tests pass, 290 MQ tests pass, ensure.sh validated, config loads correctly
**Phase 6:** Config & Wiring — 10 E2E tests covering full timeout/retry flow
**All 6 phases verified:** Schema → Worker Pool → Message Flow → Events → StaleTaskRecovery → Config & Wiring

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
