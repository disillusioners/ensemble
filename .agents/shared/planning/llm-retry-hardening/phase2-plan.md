# Phase 2: Error Classifier + Expanded Exceptions + Reactive Compaction

## Objective
Wire the error classification chain: create `LLMErrorClassifier` that intercepts LLM calls, re-classifies exceptions (transient API errors, context overflow), runs response validation inside the retry scope. Wire reactive compaction into `agent_node` for context overflow recovery. Hook remaining permanent failures into the queue processor.

## Coupling
- **Depends on**: Phase 1 (calls `validate_llm_response()`, uses `LLMResponseValidationError`)
- **Coupling type**: **tight** — code directly depends on Phase 1's definitions
- **Shared files with other phases**: `daemon/graph.py`, `daemon/manager.py`
- **Shared APIs/interfaces**: `validate_llm_response()` from Phase 1, `TRANSIENT_EXCEPTIONS` (consumed by `with_retry`), `ContextCompactor` from `daemon/compaction.py`
- **Why this coupling**: The `LLMErrorClassifier` directly imports and calls `validate_llm_response()`. The `agent_node` uses `ContextCompactor` for reactive compaction. Without Phase 1's definitions and the existing compaction module, the code won't compile.

## Two Critical Bugs Found in Review

### Bug 1: Exception Handler Order (BadRequestError is a subclass of APIStatusError)

`openai.BadRequestError` IS A SUBCLASS of `openai.APIStatusError`. Python's `except` clauses match in order — the first matching clause wins. If `except openai.APIStatusError` comes first, it catches ALL `BadRequestError` instances (including context overflow) before the `BadRequestError` clause can run. The `BadRequestError` clause becomes dead code. Context overflow errors would never be detected.

**Fix**: `except openai.BadRequestError` MUST come FIRST, before `except openai.APIStatusError`.

### Bug 2: Response Validation Must Run Inside with_retry Scope

`with_retry` only wraps the `llm_with_tools.invoke()` call. The classifier sits OUTSIDE `with_retry` (it's the thing being wrapped). If `validate_llm_response()` is called in `agent_node` AFTER `invoke()` returns, it runs OUTSIDE the retry scope. If validation fails, the exception bubbles up to LangGraph's node executor which does NOT retry — it propagates directly to the queue handler.

**Fix**: Call `validate_llm_response()` INSIDE the classifier's `try` block, immediately after `invoke()` succeeds. The classifier's `_run_with_classification` function is called by `with_retry`'s retry loop. If validation raises, `with_retry` sees it and retries.

## Context

### Compaction Module (NOW AVAILABLE — `daemon/compaction.py`)

The compaction feature is fully implemented and merged. Key facts:
- **`ContextCompactor`**: Main engine, initialized with `CompactionConfig` + `llm_config`
- **`compact_state(context: CompactionContext) → CompactionResult | None`**: Compacts if threshold exceeded
- **Result handling**: `result.replacement_messages` contains `RemoveMessage` sentinels → applied via `graph.aupdate_state(config, {'messages': replacement}, as_node='agent')`
- **Dedup**: `compacted_at` timestamp in `SessionState` prevents double compaction
- **Proactive compaction**: Already runs before each invoke via `manager._maybe_compact_context()`
- **Gap**: No REACTIVE compaction when the LLM itself says "context too long"

### How proactive and reactive compaction interact

```
Manager._process_queue_via_streaming():
  1. _maybe_compact_context()  ← proactive (estimation-based, 80% threshold)
  2. graph.astream(input)      ← invoke graph
       └─ agent_node(state)
            └─ llm_with_tools.invoke(messages)
                 ├─ success → return response
                 └─ ContextLengthExceededError
                      └─ compactor.compact_state()  ← reactive (authoritative, LLM said "too big")
                      └─ graph.aupdate_state()       ← apply RemoveMessage sentinels
                      └─ llm_with_tools.invoke(compact_messages)  ← retry with smaller context
                           ├─ success → return response
                           └─ ContextLengthExceededError → raise (permanent failure)
```

- If proactive compaction reduces enough → LLM succeeds, reactive never triggers
- If proactive misses (estimation error) → reactive catches the LLM's error and compacts
- After reactive compaction, `compacted_at` is updated → proactive won't re-compact next time
- If both fail → truly too large, permanent error (no queue retry)

### Current State (from codebase audit)

**TRANSIENT_EXCEPTIONS** (graph.py:16-25):
```python
TRANSIENT_EXCEPTIONS = (
    openai.RateLimitError,       # 429
    openai.APITimeoutError,      # timeout
    openai.APIConnectionError,    # connection issues
)
```

**Retry wrapper** (graph.py:217-224):
```python
if retry_config:
    max_retries = retry_config.get("max_retries", 3)
    llm_with_tools = llm_with_tools.with_retry(
        stop_after_attempt=max_retries,
        retry_if_exception_type=TRANSIENT_EXCEPTIONS,
        wait_exponential_jitter=True,
    )
```

**Queue-level retry** (manager.py:995-1033):
Catches ALL exceptions. If retry_count < max_retries (5), schedules retry. Otherwise fails.
`context_length_exceeded` falls through to this generic handler and retries 5 times identically.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `TransientAPIError` and `ContextLengthExceededError` exceptions | `TransientAPIError` wraps `APIStatusError` with retryable codes; `ContextLengthExceededError` wraps `BadRequestError` with context overflow. `ContextLengthExceededError` is NOT in `TRANSIENT_EXCEPTIONS` — handled by reactive compaction in agent_node. | `daemon/graph.py` |
| 2 | Create `LLMErrorClassifier` RunnableLambda | RunnableLambda wrapping the LLM that: (a) runs `validate_llm_response()` INSIDE the try block after successful invoke(), (b) catches `BadRequestError` BEFORE `APIStatusError` to detect context overflow, (c) re-classifies transient errors. | `daemon/graph.py` |
| 3 | Update `TRANSIENT_EXCEPTIONS` | Cleaned tuple: `TransientAPIError`, socket errors, `openai.APITimeoutError`, `openai.APIConnectionError`. Remove `openai.RateLimitError` (handled by classifier), `openai.APIStatusError` (now wrapped before reaching `with_retry`). Add `LLMResponseValidationError`. | `daemon/graph.py` |
| 4 | Wire wrapper chain in `build_instance_graph` | New order: `llm → bind_tools → classify_errors → with_retry`. The classifier runs BEFORE retry, so `with_retry` only sees classified exceptions. | `daemon/graph.py` |
| 5 | Add reactive compaction to `agent_node` | Modify `create_agent_node` to accept optional `compactor`, `graph`, and `config` params. Catch `ContextLengthExceededError` in agent_node: build `CompactionContext` from current state, call `compactor.compact_state()`, apply result via `graph.aupdate_state()`, retry LLM invoke once. If compactor unavailable or retry still fails, re-raise. | `daemon/graph.py` |
| 6 | Wire compactor into `build_instance_graph` | Accept optional `compactor` and `config` params in `build_instance_graph`. Pass through to `create_agent_node`. Manager passes `self._compactor` when calling `build_instance_graph`. | `daemon/graph.py` |
| 7 | Handle `ContextLengthExceededError` in queue retry | In `manager.py`'s `except Exception as e:` block, add `isinstance` check: if `ContextLengthExceededError`, immediately fail with clear message (no queue retry) and send error report to parent. | `daemon/manager.py` |
| 8 | Unit tests for the full error classification chain | Test: `BadRequestError` before `APIStatusError` order, response validation inside retry scope, context overflow detection, transient error wrapping, socket errors, reactive compaction + retry flow, compactor-unavailable fallback. | `tests/unit/test_llm_retry.py` |

## Key Files
- `daemon/graph.py` (~280 lines after changes) — exceptions, classifier, agent node with compaction, retry wrapper, build_instance_graph
- `daemon/manager.py` (2188 lines) — queue retry handler (lines 995-1033), `_maybe_compact_context` (lines 1849+), `build_instance_graph` call site
- `daemon/compaction.py` (600 lines) — `ContextCompactor`, `CompactionContext`, `CompactionResult` (already exists, NOT modified)
- `tests/unit/test_llm_retry.py` — shared with Phase 1

## Constraints
- Keep `wait_exponential_jitter=True` — do not modify backoff logic
- Do NOT add fallback provider support
- Do NOT wire up dead config (`llm_retry_delay_seconds`, `llm_retry_exponential_base`)
- Must be backward compatible — existing behavior preserved or improved
- `except openai.BadRequestError` MUST come BEFORE `except openai.APIStatusError`
- Do NOT modify `daemon/compaction.py` — use existing API as-is
- Compactor is optional — if `None` or compaction disabled, reactive compaction is skipped, error re-raised

## Implementation Details

### Architecture: Full Classification Chain with Reactive Compaction

```
agent_node(state)                                          ← LangGraph node
  ├─ full_messages = [SystemMessage] + state["messages"]
  ├─ try:
  │     response = llm_with_tools.invoke(full_messages)
  │     return {"messages": [response]}
  │
  ├─ except ContextLengthExceededError:                     ← reactive compaction
  │     ├─ if compactor is None → re-raise                 ← no compaction available
  │     ├─ context = CompactionContext(...)
  │     ├─ result = compactor.compact_state(context)
  │     ├─ if result is None → re-raise                    ← compaction not needed / dedup skip
  │     ├─ graph.aupdate_state(config, {messages: result.replacement_messages})
  │     ├─ graph.aupdate_state(config, {compacted_at: result.compacted_at})
  │     ├─ updated_state = await graph.aget_state(config)
  │     ├─ compact_messages = [SystemMessage] + updated_state.values["messages"]
  │     ├─ response = llm_with_tools.invoke(compact_messages)  ← single retry
  │     └─ return {"messages": [response]}
  │
  └─ (other exceptions propagate to manager)

llm_with_tools.invoke(messages)                             ← classified + retried
    ↓
LLMErrorClassifier._run_with_classification(messages)       ← Phase 2 new
    try:
        result = llm_with_tools.invoke(messages)
        validate_llm_response(result)                       ← Phase 1 function, INSIDE retry scope
        return result
    except openai.BadRequestError:                          ← MUST come FIRST
        → ContextLengthExceededError  (NOT in TRANSIENT_EXCEPTIONS)
    except openai.APIStatusError:
        → TransientAPIError if retryable code, else re-raise
    except Exception:
        → re-raise
    ↓ (only classified exceptions reach here)
with_retry()  ← catches: TransientAPIError, LLMResponseValidationError,
                   socket errors, APITimeoutError, APIConnectionError
```

### Task 1: Exception types

```python
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

class TransientAPIError(Exception):
    """Wrapper for APIStatusError with retryable status codes.
    
    LangChain's with_retry only matches by exception type, not by
    exception attributes. We wrap transient APIStatusErrors in this
    exception so with_retry can catch them.
    """
    def __init__(self, original: openai.APIStatusError):
        self.original = original
        self.status_code = original.status_code
        super().__init__(f"Transient API error: {original.status_code} — {original}")

class ContextLengthExceededError(Exception):
    """Raised when LLM context window is exceeded.
    
    NOT retried by with_retry (not in TRANSIENT_EXCEPTIONS).
    Caught by agent_node for reactive compaction + single retry.
    If compaction fails or retry still exceeds context, propagates
    to manager for immediate failure (no queue retry).
    """
    def __init__(self, original_error: openai.BadRequestError, model: str = ""):
        self.original_error = original_error
        self.model = model
        super().__init__(
            f"Context length exceeded for model '{model}'. "
            f"Original error: {original_error}"
        )
```

### Task 2: LLMErrorClassifier (unchanged from original plan)

```python
from langchain_core.runnables import RunnableLambda

def _classify_llm_errors(llm_with_tools) -> Runnable:
    """Wrap LLM to classify exceptions before they reach with_retry.
    
    Runs validate_llm_response() INSIDE the try block so validation
    failures are caught by with_retry and trigger a retry.
    """
    def _run_with_classification(messages, **kwargs):
        try:
            result = llm_with_tools.invoke(messages, **kwargs)
            # Phase 1's validation runs INSIDE the retry scope
            validate_llm_response(result)
            return result
        except openai.BadRequestError as e:
            # MUST come FIRST — BadRequestError is a subclass of APIStatusError
            error_str = str(e).lower()
            if 'context_length_exceeded' in error_str or 'maximum context length' in error_str:
                raise ContextLengthExceededError(e) from e
            raise  # Other BadRequestErrors (genuine bugs) — pass through
        except openai.APIStatusError as e:
            if e.status_code in RETRYABLE_STATUS_CODES:
                raise TransientAPIError(e) from e
            raise  # Non-retryable status error — pass through
        except Exception:
            raise  # Everything else passes through (including socket errors)
    
    return RunnableLambda(func=_run_with_classification)
```

### Task 3: Cleaned TRANSIENT_EXCEPTIONS

```python
TRANSIENT_EXCEPTIONS = (
    # Wrapper exception from classifier for retryable status codes
    TransientAPIError,
    # Raw socket errors (proxy restarts) — not wrapped by OpenAI SDK
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
    # OpenAI exceptions that DON'T get wrapped (from lower-level HTTP client)
    openai.APITimeoutError,
    openai.APIConnectionError,
    # Response validation failure from Phase 1
    LLMResponseValidationError,
)
```

### Task 4: Wire up the chain

```python
def build_instance_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,
    compactor: 'ContextCompactor | None' = None,   # NEW
    graph_config: dict | None = None,               # NEW: for reactive compaction's aupdate_state
):
    llm = ThinkingChatOpenAI(**llm_config_with_headers)
    llm_with_tools = llm.bind_tools(tools)

    # The graph reference is created after compile — we need a late-binding approach.
    # Use a wrapper that captures graph reference after compilation.
    graph_ref = [None]  # Mutable container for late binding

    if retry_config:
        max_retries = retry_config.get("max_retries", 3)
        classified_llm = _classify_llm_errors(llm_with_tools)
        llm_with_tools = classified_llm.with_retry(
            stop_after_attempt=max_retries,
            retry_if_exception_type=TRANSIENT_EXCEPTIONS,
            wait_exponential_jitter=True,
        )
    
    graph = StateGraph(SessionState)
    graph.add_node("agent", create_agent_node(
        llm_with_tools, system_prompt,
        compactor=compactor,
        graph_ref=graph_ref,
        config=graph_config,
        llm_config=llm_config,
    ))
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    compiled = graph.compile(checkpointer=checkpointer)
    graph_ref[0] = compiled  # Late-bind graph reference for reactive compaction
    return compiled
```

### Task 5: agent_node with reactive compaction

```python
def create_agent_node(
    llm_with_tools,
    system_prompt: str,
    compactor: 'ContextCompactor | None' = None,
    graph_ref: list | None = None,
    config: dict | None = None,
    llm_config: dict | None = None,
):
    """Create the agent node function with optional reactive compaction."""
    
    async def agent_node(state: MessagesState) -> dict:
        messages = state["messages"]
        full_messages = [SystemMessage(content=system_prompt)] + messages
        logger.debug(f"Invoking LLM with {len(full_messages)} messages")
        
        try:
            response = llm_with_tools.invoke(full_messages)
        except ContextLengthExceededError as e:
            # Reactive compaction: compact state and retry once
            if compactor is None or graph_ref is None or graph_ref[0] is None:
                logger.warning(
                    "Context length exceeded but no compactor available, re-raising"
                )
                raise
            
            logger.info(
                f"Context length exceeded, attempting reactive compaction "
                f"for {len(messages)} messages"
            )
            
            graph = graph_ref[0]
            thread_config = config or {}
            
            # Get current state for compaction context
            current_state = await graph.aget_state(thread_config)
            current_messages = current_state.values.get('messages', [])
            compacted_at = current_state.values.get('compacted_at')
            
            # Build compaction context
            from .compaction import CompactionContext, CompactionConfig
            context = CompactionContext(
                messages=current_messages,
                system_prompt_tokens=0,  # Estimated separately or set to 0
                model_name=llm_config.get('model', '') if llm_config else '',
                config=compactor.config,
                llm_config=compactor.llm_config,
                last_compacted_at=compacted_at,
            )
            
            # Compact
            result = await compactor.compact_state(context)
            if result is None or result.replacement_messages is None:
                logger.warning("Reactive compaction returned no result, re-raising")
                raise
            
            # Apply compacted messages to graph state
            await graph.aupdate_state(
                thread_config,
                {'messages': result.replacement_messages},
                as_node='agent'
            )
            if result.compacted_at:
                await graph.aupdate_state(
                    thread_config,
                    {'compacted_at': result.compacted_at},
                    as_node='agent'
                )
            
            logger.info(
                f"Reactive compaction complete: {result.messages_before} → "
                f"{result.messages_after} messages, "
                f"{result.tokens_saved} tokens saved ({result.compaction_type})"
            )
            
            # Retry with compacted state
            updated_state = await graph.aget_state(thread_config)
            compact_messages = [SystemMessage(content=system_prompt)] + updated_state.values.get('messages', [])
            response = llm_with_tools.invoke(compact_messages)
        
        tool_info = ""
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_names = [tc.get('name', getattr(tc, 'name', '?')) for tc in response.tool_calls]
            tool_info = f", tools: {tool_names}"
        logger.info(f"LLM response: {response.content[:80] if response.content else 'empty'}...{tool_info}")
        return {"messages": [response]}
    
    return agent_node
```

### Task 7: Manager-level context overflow handling (permanent failure)

In the `except Exception as e:` block (manager.py ~line 1005):

```python
except Exception as e:
    # Check for permanent errors first (no retry — compaction already attempted)
    if isinstance(e, ContextLengthExceededError):
        logger.error(
            f"Context length exceeded for session {session_id[:8]}... "
            f"(compaction attempted but failed), failing immediately"
        )
        self._queue_repository.fail(msg.message_id, str(e))
        await self.broadcaster.broadcast(Event(
            type="error",
            session_id=session_id,
            message_id=msg.message_id,
            data={
                "error": str(e),
                "status": "failed",
                "error_type": "context_length_exceeded",
            }
        ))
        await self._send_error_report(
            session_id=session_id,
            error=f"Context length exceeded (compaction attempted): {e}",
            error_type="context_length_exceeded",
            message_id=msg.message_id,
        )
    else:
        # Existing generic retry logic
        logger.error(f"Error processing message {msg.message_id}: {e}")
        self.circuit_breaker.record_failure(session_id)
        # ... rest of existing code
```

## Deliverables
- [ ] `TransientAPIError` wrapper exception for retryable status errors
- [ ] `ContextLengthExceededError` exception for context overflow (NOT in TRANSIENT_EXCEPTIONS)
- [ ] `LLMErrorClassifier` with correct except order (BadRequestError first)
- [ ] `validate_llm_response()` called INSIDE the classifier's try block
- [ ] `TRANSIENT_EXCEPTIONS` cleaned (no dead code from classifier handling)
- [ ] Wrapper chain wired: `llm → bind_tools → classify → with_retry`
- [ ] `create_agent_node` accepts optional compactor + graph_ref for reactive compaction
- [ ] Reactive compaction: catch `ContextLengthExceededError` → compact → update state → retry once
- [ ] Graceful fallback: compactor unavailable → re-raise without compaction attempt
- [ ] Queue retry handler skips retry for `ContextLengthExceededError` (compaction already tried)
- [ ] Clear error report sent to parent with `error_type="context_length_exceeded"`
- [ ] Unit tests verifying: except clause order, validation-inside-retry-scope, reactive compaction flow
