# Plan Overview: LLM Request Resilience

## Objective
Hardening the LLM call chain so agents never fail mid-task due to transient failures. This covers server errors, rate limits, timeouts, proxy restarts, malformed responses, network issues, and provides configurable fallback paths — all without breaking existing configurations.

## Scope Assessment
**LARGE** — Multiple files across daemon core (graph.py, manager.py, config.py, queue.py), new response validation module, new fallback provider support. Affects the critical LLM call chain used by every agent interaction. Estimated 3-4 coder-days.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Requested by**: Leader
- **Current state**: Two-level retry exists (LLM + queue) but has critical gaps in error coverage, no response validation, no fallback support, and several dead config values

## Current Architecture Summary

### Call Chain
```
API Request → manager.enqueue_message() → _process_queue() → _process_message_with_tracking()
→ graph.astream() → llm_with_tools.invoke() → ThinkingChatOpenAI → ChatOpenAI → OpenAI API
```

### Two-Level Retry (Current)
1. **LLM level** (LangChain `with_retry()`): 3 attempts, exponential+jitter, only catches `RateLimitError`, `APITimeoutError`, `APIConnectionError`
2. **Queue level** (repository.py): Exponential backoff 1→2→4→8→16→60 min, max 5 retries, checkpoint resume

### What's Broken
| Gap | Severity | Current Behavior |
|-----|----------|-----------------|
| `APIStatusError` not retried | CRITICAL | 500/502/503 errors fail immediately |
| No response validation | CRITICAL | Malformed/truncated responses silently accepted |
| `request_timeout=660s` | HIGH | 11-minute hang before retry |
| Dead retry config | HIGH | `llm_retry_delay_seconds`, `llm_retry_exponential_base` defined but unused |
| No fallback provider | HIGH | Single point of failure |
| No streaming timeout | MEDIUM | Stream can hang indefinitely within 660s |
| Partial streaming lost | MEDIUM | Must re-run entire LLM call on failure. **Bug**: buffered content not flushed on error — silently lost. |
| Generic exception catch | LOW | Loses error type specificity |

---

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Retry Config & Error Coverage | Fix dead config, expand transient errors, reduce timeout, add jitter, verify multi-turn checkpoint resume | None | — | 5h |
| 2 | Response Validation & Safe Streaming | Validate LLM responses, add per-chunk streaming timeouts, flush buffers on failure | Phase 1 | loose | 5h |
| 3 | Fallback Provider Support | Add configurable backup LLM provider with safe failover (no mid-stream corruption) | Phase 1 | loose | 5h |
| 4 | Observability & Circuit Breaker Cleanup | Structured retry logging, metrics, unify circuit breaker, wire config | Phase 1, 2 | loose | 3h |

### Coupling Assessment

| Phase Pair | Coupling | Justification |
|------------|----------|---------------|
| 1 → 2 | **loose** | Phase 2 depends on Phase 1's error types and config values, but doesn't share implementation files. Phase 2 adds validation on top of Phase 1's retry expansion. |
| 1 → 3 | **loose** | Phase 3 depends on Phase 1's config structure for fallback config fields, but implementation is independent (new code path). |
| 1 → 4 | **loose** | Phase 4 consumes retry events from Phase 1 for logging/metrics, doesn't modify retry logic. |
| 2 → 4 | **loose** | Phase 4 adds observability for Phase 2's validation events. |
| 3 → 4 | **loose** | Phase 4 adds observability for Phase 3's fallback events. |

**Parallelism opportunity**: Phases 2, 3, and 4 can all start once Phase 1 is complete. Phases 2 and 3 are fully independent of each other. Phase 4 can be done last since it's additive observability.

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Adding `APIStatusError` causes retries on non-transient errors (e.g., 401 auth) | HIGH | Filter `APIStatusError` by status code — only retry 429, 500, 502, 503, 529. Never retry 4xx (except 429). |
| Response validation rejects valid but unusual responses | MEDIUM | Validation should be conservative — only reject clearly malformed (empty content, missing tool calls when tools expected). Log warnings, don't fail. |
| Fallback provider increases latency on failover | MEDIUM | Fallback only activates after primary fails all retries. Add `fallback_timeout` config to cap total time before switching. |
| Config changes break existing deployments | HIGH | All new config fields have defaults matching current behavior. Existing YAML/env configs continue working unchanged. |
| `with_retry()` custom wait not supported by LangChain | MEDIUM | If LangChain doesn't support custom initial_delay, implement retry loop manually or use tenacity directly. |
| Reduced `request_timeout` kills legitimate long requests | MEDIUM | Set sensible default (120s) but make it configurable. Add separate `streaming_timeout` for active streams. |
| Fallback mid-stream causes output corruption | HIGH | **Prevented by design** — `FallbackLLM.astream()` tracks `chunks_yielded` flag. Never falls back after partial delivery. Error propagates to queue retry + checkpoint resume instead. |
| Per-chunk timeout incorrectly measures total time | HIGH | **Prevented by design** — uses `asyncio.wait_for()` on individual `__anext__()` calls, NOT `asyncio.timeout()` wrapping the iterator. |
| Multi-turn checkpoint resume doesn't work as expected | HIGH | Explicit verification task in Phase 1. If broken, document limitation and add `resume_from_checkpoint` config flag. |
| Streaming buffer content lost on failure | MEDIUM | **Fixed** — flush `content_buffer` and `thinking_buffer` as partial SSE events before re-raising exceptions. |

---

## Success Criteria
- [ ] `APIStatusError` with 429/500/502/503/529 is retried at LLM level
- [ ] `request_timeout` defaults to 120s (configurable), down from 660s
- [ ] All config values (`llm_retry_delay_seconds`, `llm_retry_exponential_base`, `circuit_breaker_*`) are wired to actual behavior
- [ ] Malformed/empty LLM responses are detected and retried
- [ ] Streaming timeout prevents indefinite hangs (per-chunk, not per-stream)
- [ ] Fallback provider can be configured and activates automatically (pre-chunk only, no mid-stream corruption)
- [ ] Streaming buffers flushed on failure (partial content preserved for observability)
- [ ] Multi-turn graph checkpoint resume verified and documented
- [ ] All retry events are logged with structured context (attempt, error type, delay)
- [ ] Existing configs continue working without modification (backward compatible)
- [ ] Tests cover all new retry paths

---

## Failure Scenario Coverage

| Scenario | Before | After |
|----------|--------|-------|
| Provider proxy restart (502) | ❌ Fails immediately | ✅ Retried at LLM level |
| Rate limit (429) | ✅ Retried | ✅ Retried (improved jitter) |
| Server error (500/503) | ❌ Fails immediately | ✅ Retried at LLM level |
| Model overloaded (529) | ❌ Fails immediately | ✅ Retried at LLM level |
| Network timeout | ✅ Retried (660s wait) | ✅ Retried (120s, configurable) |
| Connection refused | ✅ Retried | ✅ Retried |
| Auth failure (401/403) | ❌ Fails (correct) | ❌ Fails immediately (no retry) |
| Context window exceeded (400) | ❌ Fails (correct) | ❌ Fails immediately (no retry) |
| Malformed response | ❌ Silent corruption | ✅ Detected + retried |
| Empty response | ❌ Silent failure | ✅ Detected + retried |
| Truncated streaming response | ❌ Silent data loss | ✅ Detected + retried |
| Stream hangs mid-response | ❌ 11-min hang | ✅ Per-chunk streaming timeout (30s default) |
| Stream fails after partial output | ❌ Partial content silently lost | ✅ Buffer flushed as partial event + queue retry from checkpoint |
| Primary provider down (before stream) | ❌ Total failure | ✅ Fallback provider activated |
| Primary provider down (mid-stream) | ❌ Total failure | ✅ Error propagated to queue retry + checkpoint resume (no fallback — prevents corruption) |
| Queue retry after LLM retries exhausted | ✅ Works | ✅ Works (unchanged) |
| Checkpoint resume (single LLM call) | ✅ Works | ✅ Works (unchanged) |
| Multi-turn graph resume (LLM→tool→LLM→tool→FAIL) | ⚠️ Assumed works, never verified | ✅ Verified with test — resumes from last completed node |

---

## Tracking
- Created: 2026-04-02
- Last Updated: 2026-04-02 (reviewer fixes: streaming corruption, timeout impl, multi-turn resume, buffer flush)
- Status: draft
