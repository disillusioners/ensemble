# Plan Overview: LLM Stream Idle Detection

## Objective

Add idle detection for LLM streaming responses so that when `graph.astream()` produces no events for a configurable timeout (default 120 seconds), the system treats it as a failure and triggers retry/error handling — rather than silently waiting up to the full 660s HTTP timeout.

## Scope Assessment

**SMALL** — The change touches 3 files with a focused, single-purpose mechanism.

### Justification
- The idle detection is a single async wrapper around the existing `graph.astream()` loop
- It doesn't change the LLM call architecture, retry logic, or error classification
- It adds one new config field and one utility function
- No new dependencies or architectural patterns needed

## Context

### Architecture Finding (Critical)

**The LLM calls are NON-STREAMING at the invoke level.** The agent node in `graph.py` uses:

```python
response = await loop.run_in_executor(None, lambda: llm_with_tools.invoke(full_messages))
```

This means `graph.astream(stream_mode=["updates", "messages"])` produces:
- **`"updates"` events** — per node completion (agent, tools)
- **`"messages"` events** — the complete message once (via `on_llm_end`), NOT per-token chunks

**The idle detection problem is about the entire `graph.astream()` loop producing no events** (neither updates nor messages) for an extended period. This happens when:
1. The LLM invoke() hangs (HTTP connection established but server stops responding)
2. A half-open connection where TCP appears alive but no data flows
3. The OpenAI-compatible proxy stalls without closing the connection

### Current Timeout Architecture

| Layer | Timeout | Mechanism |
|-------|---------|-----------|
| HTTP client | 660s | `ThinkingChatOpenAI(request_timeout=660)` — OpenAI SDK HTTP timeout |
| LLM retry | 7 attempts | `with_retry(stop_after_attempt=7)` with exponential jitter |
| Activity heartbeat | 5s | `ActivityCallbackHandler` updates DB to prevent watchdog from marking as stuck |
| Queue watchdog | 30s | `_check_stuck_messages()` finds messages processing > 3600s |
| **Stream idle** | **NONE** | **← This is the gap** |

### The Gap

If the LLM invoke() hangs (e.g., the server accepts the connection but never responds), the system will:
1. Wait 660s for the HTTP timeout
2. Then `openai.APITimeoutError` triggers a retry
3. 7 retries × 660s = **77+ minutes** of waiting with no data

The idle detection will catch this much sooner by monitoring the `graph.astream()` event loop itself.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Stream Idle Detection | Add idle timeout wrapper around `graph.astream()` in manager.py + config | None | — | 1-2h |

### Single Phase Rationale

This is a cohesive, small-scope feature that:
- Adds a config field (1 line in `config.py`)
- Adds an async iterator wrapper (1 function, ~30 lines in `manager.py`)
- Integrates the wrapper into the streaming loop (2 lines changed in `manager.py`)
- Exposes the config in `config.yaml` (2 lines)

All pieces are tightly coupled and form a single logical unit. Splitting into phases would create artificial boundaries.

## Approach

### Design: `idle_timeout_aiter()` Wrapper

Wrap the `graph.astream()` async iterator with a function that monitors time between yielded events:

```python
async def idle_timeout_aiter(aiter, timeout_seconds, message_id):
    """Wrap an async iterator with per-event idle detection.
    
    Raises asyncio.TimeoutError if no event is yielded within timeout_seconds.
    """
    last_event_time = time.monotonic()
    async for event in aiter:
        last_event_time = time.monotonic()
        yield event
    # No need for between-event detection — async for already waits for next item
```

Wait — `async for` is blocking on the next event. We need `asyncio.wait_for()` on each `__anext__()` call:

```python
async def idle_timeout_aiter(aiter, timeout_seconds, message_id):
    last_activity = time.monotonic()
    async for event in aiter:
        yield event
```

**Problem:** `async for` blocks indefinitely on `__anext__()`. We need to wrap each iteration:

```python
async def idle_timeout_aiter(aiter, timeout_seconds, context_label="stream"):
    """Wrap async iterator with per-event idle timeout."""
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
        elapsed = time.monotonic()  # approximate
        raise StreamIdleTimeoutError(
            f"No event received from {context_label} within {timeout_seconds}s"
        )
```

This is clean, composable, and doesn't modify the underlying LLM/streaming architecture.

### Custom Exception

Define `StreamIdleTimeoutError` in `llm_error_classifier.py` alongside existing error types. It should be:
- **Retryable** — add to `TRANSIENT_EXCEPTIONS` so `with_retry` catches it
- **Logged clearly** — distinguish from HTTP timeouts

### Config Integration

Add to `QueueConfig` (where LLM retry settings already live):
```python
llm_stream_idle_timeout_seconds: int = Field(
    default=120, 
    description="Max seconds to wait for a streaming event before treating as idle/hung"
)
```

Also add to `config.yaml` under the `queue:` section.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| False positive: complex tool calls take >120s with no graph events | medium | Set conservative default (120s); make configurable; only triggers when truly no events at all (not just no tokens) |
| Idle timeout fires during normal LLM thinking time (long reasoning models) | medium | 120s default is generous for "no events at all"; tool calls produce "updates" events that reset the timer |
| `asyncio.wait_for()` cancels the underlying coroutine | low | The cancellation propagates to `graph.astream()` which is designed to handle cancellation; checkpoint state is preserved for retry |
| Doesn't help if LLM streams tokens very slowly (1 token every 119s) | low | This is a different problem (slow stream, not idle stream); the existing 660s total timeout covers this case |
| Confusion with HTTP `request_timeout=660` | low | Clear naming (`stream_idle_timeout` vs `request_timeout`) and documentation |

## Success Criteria

- [ ] `graph.astream()` loop raises `StreamIdleTimeoutError` when no event arrives within configurable timeout
- [ ] The timeout is configurable via `config.yaml` and env var, default 120 seconds
- [ ] `StreamIdleTimeoutError` is classified as retryable and triggers LLM retry
- [ ] The 660s HTTP `request_timeout` is NOT changed
- [ ] Existing streaming behavior is unchanged when events arrive normally
- [ ] Checkpoint state is preserved so retry can resume from last good state

## Tracking

- Created: 2026-04-06
- Last Updated: 2026-04-06
- Status: draft
