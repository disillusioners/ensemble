# Decisions: LLM Retry Hardening

## ADR-001: Wrapper exceptions for status-code-based retry

**Decision**: Use a `RunnableLambda` error classifier that re-raises `APIStatusError` as typed wrapper exceptions (`TransientAPIError`) based on status code, enabling LangChain's type-based `with_retry` to catch them.

**Context**: `openai.APIStatusError` covers all 400-599 status codes. We need to retry only specific codes (429, 500, 502, 503, 504) but LangChain's `with_retry` only supports `retry_if_exception_type` (tuple of types) — NOT a custom `retry` callable predicate. This was verified by inspecting `langchain_core.runnables.retry.RunnableRetry._kwargs_retrying` which calls `retry_if_exception_type(self.retry_exception_types)`.

**Options considered**:
1. **Wrapper exceptions + classifier** — RunnableLambda intercepts invoke, re-classifies exceptions, `with_retry` sees typed wrappers ✅ CHOSEN
2. **Custom retry predicate** — `retry=is_transient_api_error` callable → **NOT POSSIBLE** — LangChain doesn't support this
3. **Expand type list blindly** — Add `APIStatusError` to TRANSIENT_EXCEPTIONS, accept over-retrying some 4xx → **WRONG** — would retry permanent errors (400, 401, 403)
4. **Bypass LangChain retry, use tenacity directly** — Implement our own retry decorator → **TOO COMPLEX** — reimplements LangChain's retry with streaming support

**Rationale**: Option 1 is the only viable approach given LangChain's limitation. The classifier is a thin wrapper that only touches exception types — no business logic. It preserves LangChain's retry infrastructure (jitter, attempt tracking, callback integration).

**Consequences**:
- (+) Precise control over what gets retried (status-code level)
- (+) Reuses LangChain's retry infrastructure (backoff, callbacks)
- (+) Single place for error classification logic
- (-) Extra Runnable in the chain (minor complexity)
- (-) Stack traces show the wrapper in the chain (need to use `from e` for traceability)

---

## ADR-002: Response validation inside error classifier (not in agent_node)

**Decision**: Run `validate_llm_response()` inside the `LLMErrorClassifier`'s try block, immediately after `invoke()` succeeds. NOT in `agent_node`.

**Context**: Bad responses (empty, truncated, malformed) need to trigger retry. The `with_retry` wrapper handles retry logic but it only wraps the classified LLM callable. If validation runs in `agent_node` (outside `with_retry`), the `LLMResponseValidationError` bubbles up to LangGraph's node executor which does NOT retry — it propagates directly to the queue handler.

**Options**:
1. **Inside classifier's try block** — Validation runs after invoke() succeeds, within with_retry's scope ✅ CHOSEN
2. **Inside agent_node** — Validation after invoke returns, OUTSIDE with_retry scope → **BUG** — exceptions not retried
3. **Middleware node** — Add a LangGraph validation node after the agent node → complex, breaks agent→tools→agent cycle

**Rationale**: Option 1 is the only correct approach. The `LLMErrorClassifier` is the thing wrapped by `with_retry`. Code inside its `_run_with_classification` try block IS within the retry scope. If `validate_llm_response()` raises `LLMResponseValidationError`, `with_retry` sees it and retries.

**This changes Phase coupling**: Phase 1 (validation building blocks) must land before Phase 2 (classifier that calls validation). The phases are no longer independent.

**Consequences**:
- (+) Validation failures actually trigger retries
- (+) Reuses existing retry mechanism
- (+) No graph structure changes
- (-) Tight coupling between Phase 1 and Phase 2
- (-) Validation exceptions share retry budget with transient errors (see ADR-006)

---

## ADR-003: Fail-open response validation

**Decision**: When validation logic encounters an unexpected response format it can't evaluate, log a warning and proceed. Do NOT raise an exception.

**Context**: LLM APIs evolve, response formats change, custom models may return non-standard structures. Aggressive validation could block valid but unexpected responses.

**Rationale**: The cost of a missed retry (proceeding with a possibly-bad response) is lower than the cost of a false rejection (retrying a valid response, potentially exhausting the retry budget and failing the task). The retry budget is limited (3 by default); we shouldn't waste it on uncertainty.

**Consequences**:
- (+) Resilient to API changes
- (+) Won't break existing functionality
- (-) May occasionally proceed with a bad response that could have been retried
- (-) Relies on logging for post-hoc debugging

---

## ADR-004: Reactive compaction for context overflow (REVISED)

**Decision**: When `context_length_exceeded` is detected, attempt reactive compaction using the existing `ContextCompactor` and retry the LLM call once. If compaction fails or the retry still exceeds context, fail immediately with a clear error report to the parent agent — no queue-level retry.

**Context**: The compaction feature (`daemon/compaction.py`) is now fully merged. It provides proactive compaction (triggered at 80% threshold before each message processing via `_maybe_compact_context`). However, proactive compaction is best-effort and based on token estimation — it can miss edge cases where the actual LLM call exceeds the context limit. The gap: `context_length_exceeded` errors from the LLM have no recovery handler.

**Previous decision (ADR-004 v1)**: Context overflow is permanent, fail immediately. This was made when compaction didn't exist.

**Architecture constraint**: Reactive compaction MUST happen at the graph/node level, NOT inside `with_retry`'s scope. Reason:
1. Compaction modifies graph state (removes messages via `RemoveMessage` sentinels through `aupdate_state()`)
2. The retry needs to re-invoke the LLM with the compacted (smaller) message list
3. `with_retry` only retries the same call with same inputs — it cannot change state between retries

**Flow**:
```
agent_node(state)
  └─ llm_with_tools.invoke(full_messages)
       ├─ LLMErrorClassifier._run_with_classification()
       │    ├─ invoke() → BadRequestError(context_length_exceeded)
       │    └─ raises ContextLengthExceededError  ← NOT in TRANSIENT_EXCEPTIONS
       └─ with_retry sees ContextLengthExceededError → NOT retried by with_retry
            → propagates to agent_node
  except ContextLengthExceededError:
       ├─ compactor is available?
       │    ├─ YES → compactor.compact_state(context) → aupdate_state → retry invoke()
       │    │         └─ still fails? → raise ContextLengthExceededError (permanent)
       │    └─ NO → raise ContextLengthExceededError (permanent)
       └─ ContextLengthExceededError reaches manager
            → fail immediately, clear error report, NO queue retry
```

**Options considered**:
1. **Reactive compaction in agent_node + single retry** — Catch `ContextLengthExceededError` in the node, compact, retry once ✅ CHOSEN
2. **Fail immediately (ADR-004 v1)** — No recovery, just report — **NOW UNNECESSARY** — compaction exists and works
3. **Compaction inside with_retry** — Custom retry that modifies state between attempts → **NOT POSSIBLE** — `with_retry` doesn't support state mutation
4. **Manager-level compaction** — Catch in manager, compact, re-invoke graph → **WRONG LAYER** — loses graph execution context, wastes a full graph invocation cycle

**Rationale**: Option 1 is the natural fit. The agent node is the right scope because:
- It has access to graph state (messages)
- It can call the compactor which uses `aupdate_state()` to modify the graph
- It can immediately retry the LLM call with the compacted state
- If compaction works, the user sees success instead of an error
- If it doesn't work, we fail once (not 5 times like the current bug)

**Interaction with proactive compaction**:
- Proactive compaction runs BEFORE each graph invocation (in `_process_queue_via_streaming`/`_process_message`)
- Proactive is best-effort estimation; reactive is authoritative (the LLM itself said "too big")
- After reactive compaction, the session state is updated, so subsequent invocations see the compacted state
- The `compacted_at` dedup field prevents compaction from running twice in quick succession

**Consequences**:
- (+) Context overflow becomes recoverable (not permanent failure)
- (+) Saves 5 wasted retry cycles from old behavior
- (+) Clear error when compaction can't help (truly too large even after compaction)
- (+) Natural integration — compactor already exists, just needs to be wired into the node
- (-) `create_agent_node` needs an optional `compactor` parameter (minor API change)
- (-) Agent node complexity increases (compaction + retry logic)
- (-) Compaction during LLM call adds latency (one LLM call for summarization)
- (-) Edge case: compaction summarization itself could fail (handled by truncation fallback)

---

## ADR-005: Socket errors (ConnectionResetError, BrokenPipeError) as transient

**Decision**: Add raw socket errors (`ConnectionResetError`, `BrokenPipeError`, `ConnectionAbortedError`) directly to the retry exception list.

**Context**: When llmproxy restarts or the upstream connection drops, the client sees raw socket errors that are NOT wrapped in OpenAI exception types. These are genuinely transient — the proxy will be back.

**Rationale**: These errors are Python-level socket errors from the `httpx` transport layer. They don't get wrapped by the OpenAI SDK because the connection drops before any HTTP response is received. Adding them directly to the exception tuple is the simplest and most reliable approach.

**Consequences**:
- (+) Catches proxy restarts and network blips
- (+) Simple addition, no complex logic needed
- (-) Theoretically could retry a non-transient disconnect, but in practice these are always transient

---

## ADR-006: Shared retry budget for transient errors and validation failures

**Decision**: Transient API errors and response validation failures share the same retry budget (`llm_max_retries`, default 3). No separate budget.

**Context**: Both `TransientAPIError` (from HTTP failures) and `LLMResponseValidationError` (from bad responses) are caught by the same `with_retry` wrapper and counted against the same `stop_after_attempt` counter.

**Expected patterns with default budget of 3 attempts**:
- 3 transient errors, 0 validation failures → exhausts budget, queue-level retry takes over
- 1 transient error + 2 validation failures → exhausts budget, queue-level retry takes over
- 0 transient errors + 3 validation failures → exhausts budget, queue-level retry takes over
- 1 transient error + 1 validation failure + 1 success → succeeds

**Rationale**: Separate budgets would require custom retry logic (LangChain doesn't support this) or a custom RunnableRetry subclass. The shared budget is simpler and the default of 3 attempts provides sufficient coverage for realistic scenarios. If a model consistently produces bad responses, it should fail and let the queue-level retry handle it (which has its own budget of 5).

**Budget is configurable**: `llm_max_retries` in QueueConfig can be increased if operators see patterns where the shared budget is exhausted too quickly.

**Consequences**:
- (+) Simple — no custom retry logic
- (+) Uses existing LangChain mechanism
- (+) Budget is configurable per deployment
- (-) A burst of mixed failures could exhaust budget faster than expected
- (-) No granular control over retry allocation between error types

---

## ADR-007: Compaction at node level, not inside with_retry

**Decision**: Reactive compaction runs in `agent_node` (the LangGraph node function), NOT inside the `LLMErrorClassifier` or `with_retry` scope.

**Context**: When `ContextLengthExceededError` occurs, we need to: (1) compact the conversation history, (2) update graph state via `aupdate_state()`, (3) re-invoke the LLM with the smaller message list. This requires graph state mutation between the failed call and the retry.

**Why NOT in the classifier / with_retry**:
- `with_retry` wraps a callable and retries it with the SAME inputs. It has no mechanism to modify inputs between attempts.
- The classifier is a `RunnableLambda` — a stateless function. It has no access to `graph.aupdate_state()` or the compactor.
- Compaction needs the `CompiledStateGraph` reference and the thread config to call `aupdate_state()`.

**Why in agent_node**:
- `agent_node` is created by `create_agent_node(llm_with_tools, system_prompt, compactor=None, graph=None, config=None)`. It can hold references to the compactor, graph, and config.
- When `ContextLengthExceededError` is caught, agent_node can: call `compactor.compact_state()`, call `graph.aupdate_state()` to apply `RemoveMessage` sentinels, rebuild `full_messages` from the updated state, and retry `llm_with_tools.invoke()`.
- The retry is explicit (one attempt) rather than implicit (retry loop).

**Why pass compactor into create_agent_node (dependency injection)**:
- The compactor is owned by the `SessionManager` (initialized in `__init__` based on config).
- `build_instance_graph` doesn't know about the compactor — it just builds the graph structure.
- By injecting the compactor at `create_agent_node` time, we keep `build_instance_graph` clean and let the manager wire up the compactor when it creates the node.

**Consequences**:
- (+) Clean separation: classifier classifies, node handles recovery
- (+) `with_retry` remains simple (only handles transient errors)
- (+) Compactor is optional (graceful degradation if compaction is disabled)
- (-) `create_agent_node` gains 3 optional parameters (compactor, graph, config)
- (-) Node function is no longer pure — it has a side effect (state mutation via compaction)
