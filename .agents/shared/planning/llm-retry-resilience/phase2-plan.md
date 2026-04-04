# Phase 2: Response Validation & Safe Streaming

## Objective
Add LLM response validation to detect malformed, empty, or truncated responses. Add streaming-level timeout to prevent indefinite hangs. Ensure partial streaming failures are handled gracefully with clear retry signals.

## Coupling
- **Depends on**: Phase 1 (retry config, expanded error types)
- **Coupling type**: loose
- **Shared files with other phases**: 
  - `daemon/graph.py` (shared with Phase 1 — Phase 1 modifies retry wrapper, Phase 2 adds validation)
  - `daemon/manager.py` (shared with Phase 4 for observability)
- **Shared APIs/interfaces**: `ThinkingChatOpenAI` class (Phase 1 modifies its retry wrapper, Phase 2 adds validation hooks)
- **Why this coupling**: Phase 2 depends on Phase 1's error classification (to know what's retryable) but doesn't need Phase 1's implementation. Validation adds a new layer on top of the retry foundation.

## Context
- Previous phase completed: Phase 1 delivered expanded retry coverage, wired config, reduced timeout
- Key decisions: See `decisions.md`
- Current state: No response validation exists. Truncated/empty responses silently accepted. Streaming can hang indefinitely.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create response validation module | New module `daemon/llm_validation.py` with functions to validate LLM responses: check for empty content, missing required fields, truncated tool calls, invalid message structure. Returns validation result (valid/invalid + reason). | `daemon/llm_validation.py` (new) |
| 2 | Add validation to `ThinkingChatOpenAI._generate()` | After `super()._generate()`, validate the response. If invalid, raise a custom `LLMResponseValidationError` (which is included in `TRANSIENT_EXCEPTIONS` from Phase 1). | `daemon/graph.py`, `daemon/llm_validation.py` |
| 3 | Add streaming chunk timeout | Implement a per-chunk timeout wrapper using `asyncio.wait_for()` on individual `__anext__()` calls. Create a `with_chunk_timeout(aiter, timeout)` helper that wraps each `__anext__()` individually, NOT the entire iterator. If no chunk received within `streaming_chunk_timeout` (default 30s, configurable), raise `StreamingTimeoutError`. | `daemon/manager.py`, `daemon/config.py` |
| 4 | Add stream completion validation | After streaming completes, validate the accumulated response. Check that: content is non-empty (if tool_calls not present), tool_calls are complete (have name + args), finish_reason is not `length` (indicates truncation). | `daemon/manager.py`, `daemon/llm_validation.py` |
| 5 | Handle `finish_reason=length` gracefully | When context window is exceeded (finish_reason=length), this is NOT a transient error — it's a permanent failure. Detect this, truncate conversation history, and either retry with fewer messages or report a clear error to the user. | `daemon/manager.py` |
| 6 | Add custom `LLMResponseValidationError` | New exception class in `daemon/exceptions.py` (or `daemon/graph.py`) that wraps response validation failures. Include the original response and validation reason. This exception should be in `TRANSIENT_EXCEPTIONS` so it triggers retries. | `daemon/graph.py` or `daemon/exceptions.py` |
| 7 | Add `streaming_chunk_timeout` config | New config field in `QueueConfig`: `streaming_chunk_timeout: float = 30.0` — max seconds to wait between streaming chunks before timeout. | `daemon/config.py` |
| 8 | Add tests for validation | Test empty response detection, truncated tool call detection, stream timeout behavior, `finish_reason=length` handling. | `tests/test_llm_validation.py` (new) |
| 9 | Flush streaming buffers on failure | **Bug fix**: In `manager.py:1365-1374`, the streaming error handler re-raises exceptions without flushing `content_buffer` and `thinking_buffer`. Partial content accumulated during streaming is silently lost. Before re-raising in the `except` block of `_process_message_with_tracking()`, flush any buffered content/thinking as a final SSE event. This preserves partial work for observability and debugging even when the call fails. | `daemon/manager.py` |

## Key Files
- `daemon/llm_validation.py` — New: response validation logic
- `daemon/graph.py` — `ThinkingChatOpenAI._generate()`, response processing
- `daemon/manager.py` — `_process_message_with_tracking()`, streaming loop, error handling
- `daemon/config.py` — `streaming_chunk_timeout` config field
- `tests/test_llm_validation.py` — New: validation tests

## Constraints
- **Conservative validation**: Don't reject unusual but valid responses. Better to log a warning and proceed than to retry a valid response.
- **No token waste**: If a response is 90% valid (e.g., tool call has name but slightly truncated args), consider using it rather than retrying. Only retry on clearly broken responses.
- **Streaming backward compatible**: New streaming timeout must have a sensible default and be disableable (set to 0 or very high value).
- **No dependency on provider**: Validation should work with any OpenAI-compatible response format.

## Implementation Notes

### Task 1: Validation Module Design

```python
# daemon/llm_validation.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class ValidationResult:
    valid: bool
    reason: Optional[str] = None
    severity: str = "error"  # "error" (retry) or "warning" (proceed)

def validate_llm_response(response) -> ValidationResult:
    """Validate a complete LLM response (non-streaming)."""
    # Check 1: Empty content AND no tool calls
    if not response.content and not response.tool_calls:
        return ValidationResult(False, "Empty response: no content and no tool calls")
    
    # Check 2: Tool calls with missing fields
    for tc in (response.tool_calls or []):
        if not tc.get("name"):
            return ValidationResult(False, f"Tool call missing name: {tc}")
        if tc.get("args") is None:
            return ValidationResult(False, f"Tool call missing args: {tc}")
    
    # Check 3: finish_reason indicates truncation
    # (This is more relevant for streaming, but check here too)
    
    return ValidationResult(True)

def validate_streamed_response(
    accumulated_content: str,
    accumulated_tool_calls: list,
    finish_reason: Optional[str] = None,
) -> ValidationResult:
    """Validate a completed streaming response."""
    if finish_reason == "length":
        return ValidationResult(False, "Response truncated (finish_reason=length)", "warning")
    
    if not accumulated_content and not accumulated_tool_calls:
        return ValidationResult(False, "Empty streamed response")
    
    # Check for incomplete tool calls (name but no args)
    for tc in accumulated_tool_calls:
        if tc.get("name") and tc.get("args") is None:
            return ValidationResult(False, f"Incomplete tool call: {tc.get('name')}")
    
    return ValidationResult(True)
```

### Task 3: Streaming Chunk Timeout

**⚠️ Important**: Do NOT use `asyncio.timeout()` wrapping a `yield` — this measures total elapsed time from the outer scope, NOT the gap between individual chunks. The correct approach is to wrap each `__anext__()` call individually with `asyncio.wait_for()`.

```python
import asyncio

class StreamingTimeoutError(Exception):
    """Raised when no chunk is received within the timeout window."""
    pass

async def with_chunk_timeout(aiter, timeout_seconds: float):
    """Wrap an async iterator with per-chunk timeout.
    
    Each __anext__() call is individually wrapped in asyncio.wait_for().
    If any single chunk takes longer than timeout_seconds, StreamingTimeoutError
    is raised. This correctly measures the gap BETWEEN chunks, not total elapsed time.
    
    Set timeout_seconds=0 to disable (no timeout).
    """
    if timeout_seconds <= 0:
        # Timeout disabled — yield directly without wrapping
        async for item in aiter:
            yield item
        return
    
    aiter = aiter.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout=timeout_seconds)
            yield chunk
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise StreamingTimeoutError(
                f"No chunk received for {timeout_seconds}s — stream may be hung"
            )

# Usage in _process_message_with_tracking:
stream = graph.astream(graph_input, config, stream_mode=["updates", "messages"])
async for event in with_chunk_timeout(stream, self.config.queue.streaming_chunk_timeout):
    # ... existing event processing ...
```

**Why `asyncio.wait_for()` on `__anext__()` works correctly**:
- Each call to `__anext__()` waits for exactly ONE next item from the async iterator
- The timeout resets after each successful chunk
- A 30s timeout means: "if no chunk arrives within 30 seconds of the last one, timeout"
- This is fundamentally different from wrapping the entire `async for` loop, which would measure total elapsed time

**Why the original approach (`asyncio.timeout()` wrapping yield) was wrong**:
- `asyncio.timeout()` starts its countdown when the context manager enters
- Wrapping a `yield` inside `async with asyncio.timeout()` starts the timer when the yielding function is called, not when the next chunk arrives
- It would measure wall-clock time since the function started, not inter-chunk gaps
- A stream producing 100 chunks over 5 minutes would timeout even though it was healthy

### Task 5: Context Window Exceeded Handling

```python
if validation_result.reason and "truncated" in validation_result.reason:
    # finish_reason=length — context window exceeded
    # This is NOT transient, don't retry the same message
    logger.warning(f"Context window exceeded for message {message_id}")
    await self.broadcaster.broadcast(Event(
        type="error",
        instance_id=instance_id,
        message_id=message_id,
        data={"error": "Context window exceeded. Message too long.", "stage": "validation"}
    ))
    # Don't retry — mark as failed
    self._queue_repository.fail(msg.message_id, "Context window exceeded")
    return
```

### Task 9: Streaming Buffer Flush on Failure

**Existing bug**: In `manager.py:1365-1374`, when the streaming loop throws an exception, `content_buffer` and `thinking_buffer` may contain accumulated content that was batched but never sent. The current error handler logs and re-raises without flushing.

```python
# In _process_message_with_tracking, error handling section:
except Exception as e:
    # FLUSH BUFFERS BEFORE RE-RAISING
    # Preserve partial content for observability even on failure
    if content_buffer:
        await self.broadcaster.broadcast(Event(
            type="content",
            instance_id=instance_id,
            message_id=message_id,
            data={"content": content_buffer, "partial": True}
        ))
    if thinking_buffer:
        await self.broadcaster.broadcast(Event(
            type="thinking",
            instance_id=instance_id,
            message_id=message_id,
            data={"thinking": thinking_buffer, "partial": True}
        ))
    
    logger.error(f"Streaming failed for message {message_id}: {e}")
    await self.broadcaster.broadcast(Event(
        type="error",
        instance_id=instance_id,
        message_id=message_id,
        data={"error": str(e), "stage": "streaming", "partial_content_flushed": bool(content_buffer or thinking_buffer)}
    ))
    raise
```

**Note**: The buffer variables (`content_buffer`, `thinking_buffer`) are local to the streaming loop. The exact variable names should be verified against the current code in `manager.py`. The key principle is: before raising, broadcast any unsent buffered content tagged as `partial: True`.

## Deliverables
- [ ] `daemon/llm_validation.py` module with `validate_llm_response()` and `validate_streamed_response()`
- [ ] `ThinkingChatOpenAI._generate()` validates responses and raises on malformed
- [ ] Streaming has configurable per-chunk timeout (default 30s)
- [ ] Streamed responses validated after completion
- [ ] `finish_reason=length` handled gracefully (no retry, clear error)
- [ ] `LLMResponseValidationError` custom exception added to retry list
- [ ] `streaming_chunk_timeout` config field added
- [ ] Tests for all validation paths
- [ ] Streaming buffers (`content_buffer`, `thinking_buffer`) flushed as partial SSE events before re-raising exceptions
