# Plan Overview: LLM Retry Hardening

## Objective
Make agents resilient to transient LLM failures during long-running tasks by expanding retry coverage, adding response validation, handling context overflow gracefully via reactive compaction, and validating multi-turn checkpoint resume.

## Scope Assessment
**SMALL-MEDIUM** — 4 targeted improvements across ~3 files (graph.py, manager.py) plus 1 new test file. Each change is localized. Estimated 1-2 days of focused work.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Architecture**: API → manager.enqueue_message() → _process_queue() → graph.astream() → ThinkingChatOpenAI → llmproxy → upstream LLMs
- **Retry layers**: LLM-level (`with_retry` in graph.py) → Queue-level (manager.py catches all exceptions)
- **llmproxy** handles upstream fallback/race retries; client mostly sees "everything failed" errors
- **Compaction**: `daemon/compaction.py` fully implemented. Proactive compaction runs before each message (80% threshold). Reactive compaction (on `context_length_exceeded` error) is the gap this plan fills.

## What's IN Scope

| # | Item | Files | Type |
|---|------|-------|------|
| 1 | Expand TRANSIENT_EXCEPTIONS for LLM-level retry | `graph.py` | Improvement |
| 2 | Response validation — retry on bad responses | `graph.py` | New feature |
| 3 | Context window exceeded — reactive compaction + retry, graceful permanent failure | `graph.py`, `manager.py` | Bug fix + New feature |
| 4 | Multi-turn graph resume validation test | `tests/integration/test_checkpoint_resume.py` | New test |

## What's OUT of Scope (Explicitly)

| Item | Why |
|------|-----|
| Backoff/jitter tuning | User said keep `wait_exponential_jitter=True` as-is |
| Fallback provider support | llmproxy handles this |
| Dead config wiring (`llm_retry_delay_seconds`, `llm_retry_exponential_base`) | Separate concern, not blocking |
| `checkpoint_interval` wiring | Separate concern |
| Modifying `daemon/compaction.py` | Compaction module is complete and merged. We only USE its API. |
| Proactive compaction changes | Already working correctly via `_maybe_compact_context()` |
| Tool idempotency in retries | Known risk, explicitly out of scope |
| Circuit breaker changes | Works fine as-is for this scope |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Response validation building blocks | Create `validate_llm_response()` and `LLMResponseValidationError` | None | — | 1-2h |
| 2 | Error classifier + expanded exceptions + reactive compaction | LLMErrorClassifier with exception reclassification, response validation, and reactive compaction for context overflow recovery | Phase 1 (calls `validate_llm_response()`); `daemon/compaction.py` (uses `ContextCompactor` API) | tight | 4-5h |
| 3 | Multi-turn resume validation test | Prove checkpoint resume works correctly under failure | Phase 2 (uses expanded retry) | loose | 2-3h |

### Coupling Assessment

| From → To | Coupling | Rationale |
|-----------|----------|-----------|
| Phase 1 → Phase 2 | **tight** | Phase 2's `LLMErrorClassifier` directly calls `validate_llm_response()` defined in Phase 1. Phase 2 cannot compile without Phase 1's code. |
| Phase 2 → Phase 3 | **loose** | Phase 3 test uses the retry mechanism from Phase 2, but only as infrastructure. Test can mock the retry independently if Phase 2 isn't landed yet. |

**Scheduling**: **Strictly sequential**. Phase 1 must land first (provides `validate_llm_response()` and `LLMResponseValidationError`). Phase 2 integrates them into the classifier, adds reactive compaction to agent_node, and wires up the retry chain. Phase 3 tests the result.

### Why the ordering changed from original plan
Originally Phase 1 (retry exceptions) and Phase 2 (response validation) were independent. Reviewer found that response validation MUST run inside the `with_retry` scope to actually trigger retries — if it runs in `agent_node` (outside `with_retry`), the `LLMResponseValidationError` bubbles up to LangGraph which does NOT retry. The only place it can run is inside `Phase 1`'s `LLMErrorClassifier`. This creates a tight dependency: Phase 1 provides the validation function, Phase 2's classifier calls it.

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| `BadRequestError` is subclass of `APIStatusError` — wrong except order | Context overflow NEVER detected (dead code) — **FOUND IN REVIEW** | Was High (now fixed) | Except clauses MUST order `BadRequestError` BEFORE `APIStatusError` (subclass before parent). Enforced in code + tested. |
| Response validation runs outside `with_retry` scope | `LLMResponseValidationError` never retried — **FOUND IN REVIEW** | Was High (now fixed) | Validation runs INSIDE `LLMErrorClassifier`, not in `agent_node`. Classifier is wrapped by `with_retry`. |
| `APIStatusError` match too broad — retries non-transient 4xx | Wastes retry budget on permanent errors | Medium | Classifier checks status code before re-raising as `TransientAPIError`. Non-retryable codes (400, 401, 403) pass through unmodified. |
| Response validation rejects valid responses | Agent gets stuck retrying a good response | Low | Fail-open design: if validation is uncertain, log warning and proceed. Only retry on clearly bad signals. |
| Shared retry budget between transient errors and validation errors | Single transient + repeated validation failures exhaust budget faster | Medium | Intentional design (see ADR-006). Default 3 retries covers any combination. Budget is configurable via `llm_max_retries`. |
| Reactive compaction adds latency on context overflow | User waits longer for first response after overflow | Low | One-time cost: compaction (1 summarization LLM call) + retry. Better than 5 wasted retries. Logged for observability. |
| Reactive compaction summarization itself fails | Compaction falls back to truncation (acceptable) | Low | `ContextCompactor` already has fallback: summarization → chunked summarization → truncation → emergency truncation. Truncation always works. |
| Graph reference late-binding fails | Reactive compaction can't update state | Low | `graph_ref[0]` is set immediately after `graph.compile()`. If None, re-raise without compaction. Graceful degradation. |
| `compacted_at` dedup skips reactive compaction | Context overflow error repeats after proactive compaction already ran | Medium | Reactive compaction bypasses dedup: pass current `compacted_at` to `CompactionContext.last_compacted_at` — if compactor skips due to dedup, re-raise. The `_is_recently_compacted` check uses a cooldown window; if proactive just ran, reactive won't re-compact. Acceptable: if proactive didn't help, reactive won't either (same data). |
| Resume test is flaky with real LLM | Test instability in CI | Medium | Use mock LLM for resume test, not real API. Only integration test patterns, not full e2e. |
| `ConnectionResetError` / `BrokenPipeError` not wrapped in OpenAI exceptions | Retry doesn't catch raw socket errors | Medium | Add `(ConnectionResetError, BrokenPipeError)` directly to TRANSIENT_EXCEPTIONS tuple alongside OpenAI exceptions. |

## Success Criteria
- [ ] LLM-level retry catches 429, 500, 502, 503, 504 status errors from llmproxy
- [ ] LLM-level retry catches connection resets / broken pipes from proxy restarts
- [ ] Malformed JSON / empty responses / truncated (`finish_reason=length`) responses trigger retry
- [ ] `context_length_exceeded` is detected, triggers reactive compaction, and retries the LLM call
- [ ] If reactive compaction succeeds, the agent continues normally (no error visible to user)
- [ ] If reactive compaction fails or retry still exceeds context, fails immediately (no queue retry) with clear error report to parent
- [ ] Compactor-unavailable scenarios degrade gracefully: re-raise `ContextLengthExceededError` without compaction
- [ ] Response validation fails open (logs warning, proceeds) when uncertain
- [ ] Except clauses ordered correctly: `BadRequestError` before `APIStatusError`
- [ ] Response validation runs inside `with_retry` scope (inside classifier)
- [ ] Multi-turn resume test passes: failure at step 3 resumes from step 3 (not step 1)
- [ ] All existing tests continue to pass
- [ ] No new dependencies added
- [ ] `daemon/compaction.py` is NOT modified (used as-is)

## Tracking
- Created: 2025-04-02
- Last Updated: 2025-04-04
- Status: draft
