# Architecture Decisions: LLM Retry Resilience

## Decision Log

### ADR-001: Status-Code-Aware Retry Predicate vs. Broad Exception Catch
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: `APIStatusError` covers all HTTP errors (4xx and 5xx). We need to retry server errors (500/502/503/529) but NOT client errors (401/403/400).  
**Decision**: Use a custom `retry_if_exception()` predicate that checks status codes, NOT just adding `APIStatusError` to the exception tuple.  
**Rationale**: Broad exception catch would retry auth failures and bad requests, wasting resources and potentially causing side effects (duplicate tool calls).  
**Consequences**: Slightly more complex retry logic, but safe and correct.

### ADR-002: Tenacity Direct vs. LangChain `with_retry()`
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: LangChain's `with_retry()` uses tenacity internally but doesn't expose all configuration options (custom initial delay, custom callbacks).  
**Decision**: Keep using `with_retry()` for now, but pass tenacity params explicitly. If `with_retry()` doesn't support needed params, bypass it and use tenacity's `@retry` decorator directly on the LLM's methods.  
**Rationale**: `with_retry()` is the established pattern in the codebase. Only bypass if technically necessary.  
**Consequences**: May need to wrap `ThinkingChatOpenAI` methods directly with tenacity. Monitor LangChain compatibility.

### ADR-003: Response Validation: Fail-Open vs. Fail-Closed
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: When validation can't determine if a response is valid (unusual format, unknown fields), should we reject (fail-closed) or accept (fail-open)?  
**Decision**: **Fail-open** — log a WARNING but proceed. Only reject clearly malformed responses (empty content + no tool calls, missing tool name, missing tool args).  
**Rationale**: Aggressive validation would cause false positives and unnecessary retries, wasting tokens and time. Better to let unusual responses through and let downstream logic handle them.  
**Consequences**: Some edge-case malformed responses may still slip through, but this is better than retrying valid responses.

### ADR-004: Fallback Provider: Opt-In vs. Automatic
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: Should fallback be enabled automatically when configured, or require an explicit flag?  
**Decision**: **Opt-in** with `fallback_enabled: bool = False`. Both the config must be present AND the flag must be true.  
**Rationale**: Automatic fallback on config presence could surprise operators (unexpected costs, different model capabilities). Explicit opt-in gives control.  
**Consequences**: Slightly more config to set up, but clear intent.

### ADR-005: Streaming Timeout Granularity
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: Should we have a total streaming timeout or a per-chunk timeout?  
**Decision**: **Per-chunk timeout** with configurable `streaming_chunk_timeout` (default 30s). No total streaming timeout — some agent tasks genuinely take minutes.  
**Rationale**: A per-chunk timeout catches hung connections (no data flowing) without killing legitimate long-running streams. Total timeout would be too aggressive for complex agent tasks.  
**Consequences**: A stream that produces 1 token every 29 seconds would never timeout. This is acceptable — it's still making progress.

### ADR-006: Circuit Breaker Unification Strategy
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: Two circuit breaker implementations exist: `InstanceCircuitBreaker` in `queue.py` (per-instance) and `CircuitBreaker` in `sources/circuit_breaker.py` (generic).  
**Decision**: Use `sources/circuit_breaker.py` as the canonical implementation, enhance it with per-instance tracking from `queue.py`, then replace `InstanceCircuitBreaker` with the enhanced version.  
**Rationale**: The generic implementation is cleaner and has proper state machine logic. Adding instance tracking is straightforward.  
**Consequences**: One less duplicate implementation to maintain. All circuit breaker config goes through the unified module.

### ADR-007: Observability: Stdlib Logging vs. External Metrics
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: Should we add Prometheus/OpenTelemetry metrics or keep it simple with structured logging?  
**Decision**: **Stdlib logging only** with structured JSON format. No external metrics dependencies.  
**Rationale**: The project doesn't currently use any metrics infrastructure. Adding dependencies would increase complexity. Structured logs can be ingested by any observability tool later.  
**Consequences**: Metrics are text-based, not queryable in real-time. Operators need log aggregation tools for dashboards. This is acceptable for now.

### ADR-008: Error Classification at Queue Level
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: Currently all exceptions at queue level trigger retry with same backoff. Should permanent errors (401, context exceeded) be treated differently?  
**Decision**: **Yes** — classify errors as transient (retry) or permanent (fail immediately). Permanent errors: `AuthenticationError` (401/403), `BadRequestError` (400, including context window exceeded), `PermissionDeniedError` (403).  
**Rationale**: Retrying a 401 auth error is pointless and wastes 5 retry slots. Better to fail fast with a clear error message.  
**Consequences**: Some errors that might be transient (e.g., 400 due to temporary provider issue) would fail immediately. This is acceptable — these are rare edge cases.

### ADR-009: Fallback Streaming — No Mid-Stream Failover
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: When the primary LLM stream fails AFTER yielding one or more chunks to the caller, falling back to a secondary provider would start a new stream and yield additional chunks. This produces corrupted output — interleaved chunks from two different LLM responses.  
**Decision**: **Never fall back mid-stream.** Track whether any chunks have been yielded (`chunks_yielded` flag). If primary fails after `chunks_yielded > 0`, raise the error immediately and let the queue-level retry + LangGraph checkpoint resume handle recovery. Fallback only activates if primary fails BEFORE yielding any chunks (connection error, auth error at connect, model not found).  
**Rationale**: Stream corruption is worse than retrying from checkpoint. Interleaved chunks from two providers would produce garbled text, broken tool calls, and impossible-to-debug failures. The checkpoint resume path already handles mid-graph failures correctly.  
**Consequences**: Mid-stream failures always go through queue retry. This adds latency (retry backoff) but guarantees output integrity. The trade-off is acceptable because mid-stream primary failures are rare.

### ADR-010: Per-Chunk Timeout via `asyncio.wait_for()` on `__anext__()`
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: The streaming timeout needs to measure the gap between individual chunks, NOT total elapsed time. The initial implementation used `asyncio.timeout()` wrapping a `yield` inside an async generator, which measures wall-clock time from when the context manager enters — not inter-chunk gaps.  
**Decision**: Use `asyncio.wait_for()` wrapping individual `__anext__()` calls on the async iterator. Create a helper `with_chunk_timeout(aiter, timeout_seconds)` that calls `await asyncio.wait_for(aiter.__anext__(), timeout=timeout_seconds)` in a loop. Each call resets the timeout.  
**Rationale**: `asyncio.wait_for()` on `__anext__()` correctly measures the time to receive the NEXT chunk from the iterator. If a chunk arrives in 5s, the next `__anext__()` call starts fresh with a full 30s budget. This is the only correct way to implement per-chunk timeout on an async iterator.  
**Consequences**: Slightly more verbose code than `async for` + `asyncio.timeout()`, but semantically correct. A healthy stream producing chunks every 29s will never timeout (correct behavior).

### ADR-011: Multi-Turn Graph Checkpoint Resume Verification
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: The plan protects individual LLM calls with retries, but agent tasks are multi-turn graphs (LLM → tool → LLM → tool → ...). If the graph fails at turn 3 of 5, the retry must resume from turn 3, not replay turns 1-2. LangGraph's `AsyncSqliteSaver` should handle this, but it was never verified.  
**Decision**: Add an explicit verification task in Phase 1. Create a test with a 3+ turn graph, inject failure at turn 3, and verify that checkpoint resume only re-executes turn 3. Document the expected behavior and any LangGraph limitations discovered.  
**Rationale**: If checkpoint resume doesn't work as expected for multi-turn graphs, the entire retry strategy is undermined — every retry would replay from the beginning, wasting tokens and potentially causing duplicate side effects (duplicate tool calls, duplicate API requests). This must be verified, not assumed.  
**Consequences**: May discover a LangGraph limitation requiring mitigation (e.g., manual state management, custom checkpoint logic). If it works correctly, the verification test serves as a regression guard.

### ADR-012: Streaming Buffer Flush on Failure
**Date**: 2026-04-02  
**Status**: Decided  
**Context**: In `manager.py:1365-1374`, when the streaming loop throws an exception, `content_buffer` and `thinking_buffer` may contain accumulated content that was batched (500 chars or 500ms intervals) but never flushed to the broadcaster. This partial content is silently lost, making debugging harder and losing partial agent work.  
**Decision**: Before re-raising exceptions in `_process_message_with_tracking()`, flush any remaining buffered content and thinking as final SSE events tagged with `partial: True`. This preserves partial work for observability and client-side display.  
**Rationale**: The buffers exist for batching performance. On failure, there's no reason to discard the partial content — it was already generated and paid for (token cost). Flushing it aids debugging and may allow the user to see partial progress. The `partial: True` tag prevents clients from treating it as a complete response.  
**Consequences**: Minimal performance impact (one extra broadcast on failure path, which is rare). Clients need to handle `partial: True` events gracefully (display as-is or discard).
