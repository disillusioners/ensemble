# Phase 1: Stream Idle Detection Implementation

## Objective

Add an idle timeout wrapper around the `graph.astream()` loop in `manager.py` so that when no streaming event is received within a configurable window, a `StreamIdleTimeoutError` is raised and caught by the existing retry mechanism.

## Coupling

- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: none
- **Shared APIs/interfaces**: none

## Context

The current architecture has a gap: `graph.astream()` can hang indefinitely (up to the 660s HTTP timeout) if the LLM server stops responding mid-connection. The HTTP-level timeout only fires when the TCP socket itself times out, not when the server simply stops sending data while keeping the connection open.

The fix is a lightweight async iterator wrapper that enforces a per-event timeout on `graph.astream()`, completely independent of the underlying HTTP timeout.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `StreamIdleTimeoutError` exception | New exception class, add to `TRANSIENT_EXCEPTIONS` so it triggers retry | `daemon/llm_error_classifier.py` |
| 2 | Add config field | `llm_stream_idle_timeout_seconds` in `QueueConfig`, default 120s | `daemon/config.py` |
| 3 | Add config.yaml entry | Expose the new setting in the yaml config | `config.yaml` |
| 4 | Implement `idle_timeout_aiter()` wrapper | Async generator that wraps any async iterator with per-event timeout using `asyncio.wait_for()` | `daemon/manager.py` |
| 5 | Integrate wrapper into streaming loop | Wrap `graph.astream()` call with the idle timeout iterator | `daemon/manager.py` |
| 6 | Pass config through to streaming loop | Read `llm_stream_idle_timeout_seconds` from config and pass to the wrapper | `daemon/manager.py` |
| 7 | Add logging | Log when idle timeout fires with context (message_id, elapsed time) | `daemon/manager.py` |

## Key Files

- `daemon/llm_error_classifier.py` — Exception class + TRANSIENT_EXCEPTIONS tuple
- `daemon/config.py` — QueueConfig with new field
- `config.yaml` — Runtime configuration
- `daemon/manager.py` — Streaming loop + idle wrapper function

## Task Details

### Task 1: Add `StreamIdleTimeoutError` to error classifier

**File:** `daemon/llm_error_classifier.py`

Add after `ContextLengthExceededError` class (around line 46):

```python
class StreamIdleTimeoutError(Exception):
    """Raised when graph.astream() produces no events within the idle timeout.
    
    This indicates a hung or half-open connection where the server has stopped
    sending data without closing the connection. Retried by with_retry via
    TRANSIENT_EXCEPTIONS.
    
    This is distinct from openai.APITimeoutError which fires at the HTTP level
    (request_timeout=660s). StreamIdleTimeoutError fires at the event level
    when no graph events arrive within llm_stream_idle_timeout_seconds.
    """
    
    def __init__(self, timeout_seconds: float, context: str = "stream"):
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Stream idle timeout: no event received within {timeout_seconds}s "
            f"during {context}"
        )
```

Add to `TRANSIENT_EXCEPTIONS` tuple (around line 50):

```python
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    TransientAPIError,
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    LLMResponseValidationError,
    StreamIdleTimeoutError,  # NEW — idle stream detection
)
```

Add logging in `classify_llm_errors` function — no changes needed here since the error is raised from the streaming loop, not from within the LLM invoke. The exception propagates naturally.

**IMPORTANT:** The `StreamIdleTimeoutError` is raised from the `graph.astream()` loop in manager.py, NOT from within the `classify_llm_errors` wrapper. It will propagate through the graph execution and be caught by the `except Exception` in `_process_message_with_tracking` (line 1396), then re-raised to `_process_queue` for retry handling.

Actually — we need to verify: does `with_retry` in `graph.py` wrap the entire node execution or just the LLM invoke? Looking at the code:

```python
# graph.py line 309
llm_with_tools = llm_with_tools.with_retry(...)
```

`with_retry` wraps the LLM runnable, not the graph. So the idle timeout is NOT caught by `with_retry`. It's caught by the manager's retry logic in `_process_queue`.

This means `StreamIdleTimeoutError` does NOT need to be in `TRANSIENT_EXCEPTIONS` (which is for `with_retry`). Instead, the manager's error handling in `_process_queue` will see it as a regular exception and retry per queue retry config.

**Revised:** Do NOT add to `TRANSIENT_EXCEPTIONS`. The error flows through the manager's existing retry path:
1. `_process_message_with_tracking` raises `StreamIdleTimeoutError` (caught by `except Exception` on line 1396)
2. It's re-raised to `_process_queue`
3. `_process_queue` handles retry via the queue retry mechanism

### Task 2: Add config field

**File:** `daemon/config.py`

Add to `QueueConfig` class (after line 107):

```python
# Stream idle detection
llm_stream_idle_timeout_seconds: int = Field(
    default=120,
    description="Max seconds to wait for a streaming event from graph.astream() "
                "before treating the connection as hung/idle. Set to 0 to disable."
)
```

### Task 3: Add config.yaml entry

**File:** `config.yaml`

Add to `queue:` section:

```yaml
queue:
  # ... existing settings ...
  llm_max_retries: 7
  llm_stream_idle_timeout_seconds: 120  # Stream idle detection (0 = disabled)
```

### Task 4: Implement `idle_timeout_aiter()` wrapper

**File:** `daemon/manager.py`

Add as a module-level async generator function (before `_process_message_with_tracking`, around line 1100):

```python
async def _idle_timeout_aiter(aiter, timeout_seconds: float, context_label: str = "stream"):
    """Wrap an async iterator with per-event idle timeout.
    
    Raises StreamIdleTimeoutError if no event is yielded within timeout_seconds.
    Uses asyncio.wait_for() to enforce timeout on each __anext__() call.
    
    Args:
        aiter: The async iterator to wrap.
        timeout_seconds: Maximum seconds to wait for each event. 0 = disabled.
        context_label: Description for error messages (e.g., "graph.astream for msg abc123").
    
    Yields:
        Events from the wrapped iterator.
        
    Raises:
        StreamIdleTimeoutError: If no event arrives within timeout_seconds.
    """
    from .llm_error_classifier import StreamIdleTimeoutError
    
    if timeout_seconds <= 0:
        # Idle detection disabled, pass through
        async for event in aiter:
            yield event
        return
    
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    aiter.__anext__(),
                    timeout=timeout_seconds
                )
            except StopAsyncIteration:
                break
            yield event
    except asyncio.TimeoutError:
        raise StreamIdleTimeoutError(timeout_seconds, context_label)
```

### Task 5: Integrate wrapper into streaming loop

**File:** `daemon/manager.py`

Change the `graph.astream()` call in `_process_message_with_tracking` (around line 1203).

**Before:**
```python
async for event in graph.astream(graph_input, config, stream_mode=["updates", "messages"]):
```

**After:**
```python
idle_timeout = self.config.queue.llm_stream_idle_timeout_seconds
stream_context = f"graph.astream for message {message_id[:8]}..."

async for event in _idle_timeout_aiter(
    graph.astream(graph_input, config, stream_mode=["updates", "messages"]),
    timeout_seconds=idle_timeout,
    context_label=stream_context,
):
```

### Task 6: Logging

Add a log line when the timeout fires. This is already handled by the `StreamIdleTimeoutError.__init__` message, but add explicit logging in the `except Exception` block in `_process_message_with_tracking`:

**Before (line 1396-1405):**
```python
except Exception as e:
    logger.error(f"Streaming failed for message {message_id}: {e}")
    # Broadcast error event
    await self.broadcaster.broadcast(Event(
        type="error",
        instance_id=instance_id,
        message_id=message_id,
        data={"error": str(e), "stage": "streaming"}
    ))
    raise  # Re-raise to let _process_queue handle retry logic
```

**After:**
```python
except Exception as e:
    from .llm_error_classifier import StreamIdleTimeoutError
    if isinstance(e, StreamIdleTimeoutError):
        logger.warning(
            f"Stream idle timeout for message {message_id[:8]}... "
            f"(no event for {e.timeout_seconds}s): {e}"
        )
    else:
        logger.error(f"Streaming failed for message {message_id}: {e}")
    # Broadcast error event
    await self.broadcaster.broadcast(Event(
        type="error",
        instance_id=instance_id,
        message_id=message_id,
        data={"error": str(e), "stage": "streaming"}
    ))
    raise  # Re-raise to let _process_queue handle retry logic
```

Using `logger.warning` instead of `logger.error` for idle timeouts since they're expected to happen occasionally and will be retried.

## Constraints

- Do NOT change the 660s `request_timeout` in `LLMConfig` — it's intentional
- Do NOT change the retry count or retry delay settings
- Do NOT modify the `ThinkingChatOpenAI` class or `classify_llm_errors` wrapper
- The idle timeout should be independently configurable and disable-able (set to 0)
- The implementation should work with any async iterator, not be coupled to LangGraph internals

## Implementation Notes

### Why `asyncio.wait_for()` on each `__anext__()` call?

The `async for` statement calls `__anext__()` and blocks until the next event or `StopAsyncIteration`. There's no way to add a timeout to `async for` directly. By manually iterating with `__anext__()` and wrapping each call in `asyncio.wait_for()`, we get per-event timeout enforcement.

### Why not modify the agent_node to use `astream()` instead of `invoke()`?

Switching to per-token streaming would be a larger refactor that changes the LangGraph execution model. The current approach works at the graph event level, which is simpler and covers the real-world failure mode (entire invoke hangs, not individual tokens going silent).

### Cancellation Safety

When `asyncio.wait_for()` raises `TimeoutError`, it cancels the underlying coroutine (`__anext__()`). This propagates cancellation to `graph.astream()`. LangGraph's checkpointer preserves state, so a retry can resume from the last checkpoint — no work is lost.

### Why not in `TRANSIENT_EXCEPTIONS`?

The `with_retry` in `graph.py` wraps the LLM runnable only, not the entire graph execution. The idle timeout fires at the `graph.astream()` level in manager.py. The error propagates to `_process_queue` which has its own retry mechanism (queue-level retries). Adding it to `TRANSIENT_EXCEPTIONS` would be misleading since `with_retry` never sees this error.

## Deliverables

- [ ] `StreamIdleTimeoutError` exception class in `llm_error_classifier.py`
- [ ] `llm_stream_idle_timeout_seconds` config field in `config.py`
- [ ] Config entry in `config.yaml`
- [ ] `_idle_timeout_aiter()` wrapper function in `manager.py`
- [ ] Integration into `_process_message_with_tracking()` streaming loop
- [ ] Logging for idle timeout events
- [ ] Verify that retry works correctly when idle timeout fires (test scenario)
