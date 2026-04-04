# Phase 2: Error Classifier + Expanded Exceptions + Context Overflow

## Objective
Wire the error classification chain: create `LLMErrorClassifier` that intercepts LLM calls, re-classifies exceptions (transient API errors, context overflow), runs response validation inside the retry scope, and hooks context overflow fast-fail into the queue processor.

## Coupling
- **Depends on**: Phase 1 (calls `validate_llm_response()`, uses `LLMResponseValidationError`)
- **Coupling type**: **tight** — code directly depends on Phase 1's definitions
- **Shared files with other phases**: `daemon/graph.py`, `daemon/manager.py`
- **Shared APIs/interfaces**: `validate_llm_response()` from Phase 1, `TRANSIENT_EXCEPTIONS` (consumed by `with_retry`)
- **Why this coupling**: The `LLMErrorClassifier` directly imports and calls `validate_llm_response()`. Without Phase 1's definitions, the code won't compile.

## Two Critical Bugs Found in Review

### Bug 1: Exception Handler Order (BadRequestError is a subclass of APIStatusError)

`openai.BadRequestError` IS A SUBCLASS of `openai.APIStatusError`. Python's `except` clauses match in order — the first matching clause wins. If `except openai.APIStatusError` comes first, it catches ALL `BadRequestError` instances (including context overflow) before the `BadRequestError` clause can run. The `BadRequestError` clause becomes dead code. Context overflow errors would never be detected.

**Fix**: `except openai.BadRequestError` MUST come FIRST, before `except openai.APIStatusError`.

### Bug 2: Response Validation Must Run Inside with_retry Scope

`with_retry` only wraps the `llm_with_tools.invoke()` call. The classifier sits OUTSIDE `with_retry` (it's the thing being wrapped). If `validate_llm_response()` is called in `agent_node` AFTER `invoke()` returns, it runs OUTSIDE the retry scope. If validation fails, the exception bubbles up to LangGraph's node executor which does NOT retry — it propagates directly to the queue handler.

**Fix**: Call `validate_llm_response()` INSIDE the classifier's `try` block, immediately after `invoke()` succeeds. The classifier's `_run_with_classification` function is called by `with_retry`'s retry loop. If validation raises, `with_retry` sees it and retries.

## Context

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
| 1 | Create `TransientAPIError` and `ContextLengthExceededError` exceptions | `TransientAPIError` wraps `APIStatusError` with retryable codes; `ContextLengthExceededError` wraps `BadRequestError` with context overflow. | `daemon/graph.py` |
| 2 | Create `LLMErrorClassifier` RunnableLambda | RunnableLambda wrapping the LLM that: (a) runs `validate_llm_response()` INSIDE the try block after successful invoke(), (b) catches `BadRequestError` BEFORE `APIStatusError` to detect context overflow, (c) re-classifies transient errors. | `daemon/graph.py` |
| 3 | Update `TRANSIENT_EXCEPTIONS` | Cleaned tuple: `TransientAPIError`, socket errors, `openai.APITimeoutError`, `openai.APIConnectionError`. Remove `openai.RateLimitError` (handled by classifier), `openai.APIStatusError` (now wrapped before reaching `with_retry`). Add `LLMResponseValidationError`. | `daemon/graph.py` |
| 4 | Wire wrapper chain in `build_instance_graph` | New order: `llm → bind_tools → classify_errors → with_retry`. The classifier runs BEFORE retry, so `with_retry` only sees classified exceptions. | `daemon/graph.py` |
| 5 | Handle `ContextLengthExceededError` in queue retry | In `manager.py:995-1033`, add `isinstance` check: if `ContextLengthExceededError`, immediately fail with clear message (no retry) and send error report to parent. | `daemon/manager.py` |
| 6 | Unit tests for the full error classification chain | Test: `BadRequestError` before `APIStatusError` order, response validation inside retry scope, context overflow detection, transient error wrapping, socket errors. | `tests/unit/test_llm_retry.py` |

## Key Files
- `daemon/graph.py` (238 lines) — exceptions, classifier, retry wrapper, build_instance_graph
- `daemon/manager.py` (2188 lines) — queue retry handler (lines 995-1033)
- `tests/unit/test_llm_retry.py` — shared with Phase 1

## Constraints
- Keep `wait_exponential_jitter=True` — do not modify backoff logic
- Do NOT add fallback provider support
- Do NOT wire up dead config (`llm_retry_delay_seconds`, `llm_retry_exponential_base`)
- Must be backward compatible — existing behavior preserved or improved
- `except openai.BadRequestError` MUST come BEFORE `except openai.APIStatusError`

## Implementation Details

### Architecture: Full Classification Chain

```
llm_with_tools.invoke(messages)
    ↓
LLMErrorClassifier._run_with_classification(messages)  ← Phase 2
    try:
        result = llm_with_tools.invoke(messages)     ← with_retry scope starts here
        validate_llm_response(result)               ← INSIDE try block (Phase 1 function)
        return result
    except openai.BadRequestError:  ← MUST come FIRST (subclass of APIStatusError)
        → ContextLengthExceededError  (permanent, no retry)
    except openai.APIStatusError:
        → TransientAPIError if retryable code, else re-raise
    except Exception:
        → re-raise
    ↓ (only classified exceptions reach here)
with_retry()  ← catches: TransientAPIError, LLMResponseValidationError, socket errors, APITimeoutError, APIConnectionError
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
    """Raised when LLM context window is exceeded (permanent error — no retry)."""
    def __init__(self, original_error: openai.BadRequestError, model: str = ""):
        self.original_error = original_error
        self.model = model
        super().__init__(
            f"Context length exceeded for model '{model}'. "
            f"Original error: {original_error}"
        )
```

### Task 2: LLMErrorClassifier (corrected order + validation inside)

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
# CLEANED — after classifier is in place, many original types are redundant:
# - openai.RateLimitError → caught by classifier, re-raised as TransientAPIError
# - openai.APIStatusError → caught by classifier, re-raised as TransientAPIError (retryable)
#                         or re-raised unchanged (non-retryable)
# These remain because they might reach with_retry through OTHER code paths:
TRANSIENT_EXCEPTIONS = (
    # Wrapper exception from classifier for retryable status codes
    TransientAPIError,
    # Raw socket errors (proxy restarts) — not wrapped by OpenAI SDK
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
    # OpenAI exceptions that DON'T get wrapped (e.g., from lower-level HTTP client)
    openai.APITimeoutError,
    openai.APIConnectionError,
    # Response validation failure from Phase 1
    LLMResponseValidationError,
    # Safety net: openai.APIStatusError if it somehow reaches with_retry
    # without going through the classifier.
    # openai.APIStatusError,  # Removed: classifier handles this; kept for safety
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
):
    llm = ThinkingChatOpenAI(**llm_config_with_headers)
    llm_with_tools = llm.bind_tools(tools)

    if retry_config:
        max_retries = retry_config.get("max_retries", 3)
        # NEW: classify errors BEFORE retry, validate INSIDE retry scope
        classified_llm = _classify_llm_errors(llm_with_tools)
        # Retry runs AFTER classification — only sees classified exceptions
        llm_with_tools = classified_llm.with_retry(
            stop_after_attempt=max_retries,
            retry_if_exception_type=TRANSIENT_EXCEPTIONS,
            wait_exponential_jitter=True,
        )
    
    graph = StateGraph(MessagesState)
    # ... rest unchanged
```

### Task 5: Manager-level context overflow handling

In the `except Exception as e:` block (manager.py:995):

```python
except Exception as e:
    # Check for permanent errors first (no retry)
    if isinstance(e, ContextLengthExceededError):
        logger.error(f"Context length exceeded for instance {instance_id[:8]}..., failing immediately")
        self._queue_repository.fail(msg.message_id, str(e))
        await self.broadcaster.broadcast(Event(
            type="error",
            instance_id=instance_id,
            message_id=msg.message_id,
            data={
                "error": str(e),
                "status": "failed",
                "error_type": "context_length_exceeded",
            }
        ))
        await self._send_error_report(
            instance_id=instance_id,
            error=f"Context length exceeded: {e}",
            error_type="context_length_exceeded",
            message_id=msg.message_id,
        )
    else:
        # Existing generic retry logic
        logger.error(f"Error processing message {msg.message_id}: {e}")
        self.circuit_breaker.record_failure(instance_id)
        # ... rest of existing code
```

## Deliverables
- [ ] `TransientAPIError` wrapper exception for retryable status errors
- [ ] `ContextLengthExceededError` exception for permanent context overflow
- [ ] `LLMErrorClassifier` with correct except order (BadRequestError first)
- [ ] `validate_llm_response()` called INSIDE the classifier's try block
- [ ] `TRANSIENT_EXCEPTIONS` cleaned (no dead code from classifier handling)
- [ ] Wrapper chain wired: `llm → bind_tools → classify → with_retry`
- [ ] Queue retry handler skips retry for `ContextLengthExceededError`
- [ ] Clear error report sent to parent with `error_type="context_length_exceeded"`
- [ ] Unit tests verifying except clause order and validation-inside-retry-scope
