# Phase 1: Response Validation Building Blocks

## Objective
Create the `validate_llm_response()` function and `LLMResponseValidationError` exception. These are pure utilities with no wiring — Phase 2 integrates them into the error classifier.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: `daemon/graph.py` (Phase 2 also modifies it)
- **Shared APIs/interfaces**: `validate_llm_response()`, `LLMResponseValidationError` (consumed by Phase 2's classifier)
- **Why this coupling**: Phase 2's `LLMErrorClassifier` calls `validate_llm_response()` after each successful `invoke()`. Phase 1 must land first because Phase 2's code won't compile without these definitions.

## Context

### Why this is a separate phase
Response validation was originally planned inside `agent_node` (outside `with_retry` scope). Reviewer found this is a bug — `with_retry` only wraps `invoke()`, so `LLMResponseValidationError` raised in `agent_node` would NOT be retried. The validation must run inside the error classifier (Phase 2), which IS wrapped by `with_retry`. This creates a tight dependency: the validation function must exist before the classifier can call it.

### Current State
No response validation exists. The `agent_node` (graph.py:183-196) accepts any response from `llm_with_tools.invoke()`, even empty or truncated ones, directly into the graph state.

### What llmproxy can send that's "bad"
- Empty response body (model produced nothing)
- Truncated response (`finish_reason=length`) — context ran out mid-generation
- Malformed JSON in tool calls (model hallucinated invalid JSON)
- Missing required fields in response (rare but possible)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `LLMResponseValidationError` exception | Custom exception for response validation failures. Carries the response object and reason for debugging. Must be a type that Phase 2 can add to `TRANSIENT_EXCEPTIONS`. | `daemon/graph.py` |
| 2 | Create `validate_llm_response()` function | Validates: (a) response is not None, (b) response has content or tool_calls, (c) tool_calls have valid JSON args, (d) `finish_reason != length`. Raises `LLMResponseValidationError` on failure. Fail-open on unexpected formats. | `daemon/graph.py` |
| 3 | Unit tests for response validation | Test each validation case: None response, empty response, truncated (`finish_reason=length`), malformed JSON in tool calls, valid response. Test fail-open behavior when response format is unexpected. | `tests/unit/test_llm_retry.py` (new file) |

## Key Files
- `daemon/graph.py` (238 lines) — exception + validation function added at module level
- `tests/unit/test_llm_retry.py` — new test file

## Constraints
- **Fail-open**: If validation can't determine (e.g., unexpected response format), log warning and proceed. Don't crash.
- **Pure utility**: No wiring, no side effects. Phase 2 handles integration.
- **No changes to `agent_node`**: Phase 1 only adds definitions, doesn't modify existing functions.

## Implementation Details

### Task 1-2: Exception + validation function

```python
class LLMResponseValidationError(Exception):
    """Raised when LLM response fails validation checks.
    
    Will be caught by with_retry when raised inside the LLMErrorClassifier
    (Phase 2), triggering a retry of the LLM call.
    """
    def __init__(self, reason: str, response: Any = None):
        self.reason = reason
        self.response = response
        super().__init__(f"LLM response validation failed: {reason}")


def validate_llm_response(response: Any) -> None:
    """Validate LLM response quality. Raises LLMResponseValidationError on bad responses.
    
    Fail-open: if validation can't determine quality, logs warning and proceeds.
    
    Called from LLMErrorClassifier (Phase 2) after successful invoke(),
    inside the with_retry scope so validation failures trigger retry.
    """
    try:
        # Check for None response
        if response is None:
            raise LLMResponseValidationError("Response is None", response)
        
        # Check for finish_reason=length (truncated response)
        # This appears in generation_info or response_metadata
        metadata = getattr(response, 'response_metadata', {}) or {}
        generation_info = getattr(response, 'generation_info', {}) or {}
        
        finish_reason = (
            generation_info.get('finish_reason') 
            or metadata.get('finish_reason')
        )
        if finish_reason == 'length':
            raise LLMResponseValidationError(
                f"Response truncated (finish_reason=length). "
                f"Content length: {len(response.content) if response.content else 0}",
                response
            )
        
        # Check for completely empty response (no content AND no tool_calls)
        has_content = bool(response.content and response.content.strip())
        has_tool_calls = bool(getattr(response, 'tool_calls', None))
        
        if not has_content and not has_tool_calls:
            raise LLMResponseValidationError(
                "Response is empty (no content and no tool_calls)",
                response
            )
        
        # Validate tool call arguments are valid JSON
        if has_tool_calls:
            import json
            for tc in response.tool_calls:
                args = tc.get('args', tc.get('arguments', {})) if isinstance(tc, dict) else getattr(tc, 'args', {})
                if isinstance(args, str):
                    try:
                        json.loads(args)
                    except json.JSONDecodeError:
                        raise LLMResponseValidationError(
                            f"Tool call '{tc.get('name', '?')}' has invalid JSON arguments",
                            response
                        )
        
    except LLMResponseValidationError:
        raise  # Re-raise our own exceptions
    except Exception as e:
        # Fail-open: can't validate, log and proceed
        logger.warning(f"Could not validate LLM response (fail-open): {e}")
```

## Deliverables
- [ ] `LLMResponseValidationError` exception class defined
- [ ] `validate_llm_response()` function with fail-open behavior
- [ ] `finish_reason=length` detection raises validation error
- [ ] Empty response detection raises validation error
- [ ] Malformed tool call JSON detection raises validation error
- [ ] Unit tests for each validation case
- [ ] Unit tests for fail-open behavior
