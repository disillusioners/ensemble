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

## Test Results (Latest: 2026-04-05 Post-Completion)
- **1108+ unit/functional/integration tests pass** on feature/concurrency-model-fixes branch
- 8 pre-existing failures (instructive error tests — feature never implemented on this branch)
- 2 collection errors (missing `croniter` dependency)
- 14 skipped (integration tests requiring mock LLM server)
- **0 NEW failures** — Phase 2 concurrency changes confirmed clean
- dev.sh validated and working (ensure.md: PASS)
- See `.agents/tester/RESULTS/2026-04-05-phase2-post-completion-validation.md` for full details
- See `.agents/tester/RESULTS/2026-04-05-phase2-concurrency-fixes.md` for Phase 2 details

## Current Focus
**FINAL VALIDATION COMPLETE for feature/concurrency-model-fixes (all P1-P4)**

### Status: 🟡 CONDITIONAL PASS — 2 fixable regressions

**Branch:** 18 commits, head `881673f`
- **Core tests:** 1045/1055 PASS (8 pre-existing failures in instructive_errors)
- **Job queue tests:** 59/60 FAIL — **REGRESSION** from `5dcc584` (asyncio.to_thread + SQLite in-memory)
- **Scheduler API tests:** 2/3 FAIL — **REGRESSION** (source_registry null guard missing)
- **Import validation:** 7/8 PASS (1 stale `create_app` reference)
- **dev.sh smoke test:** ✅ PASS — clean start, graceful shutdown

### Before Merge: Fix Required
1. Job queue test fixtures need `StaticPool` for SQLite threading (59 tests)
2. Scheduler API needs null guard for `source_registry` (2 tests)

See `.agents/tester/RESULTS/2026-04-06-final-validation-P1-P4.md` for full details.
