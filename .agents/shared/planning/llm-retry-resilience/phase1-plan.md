# Phase 1: Retry Config & Error Coverage

## Objective
Fix the retry foundation — wire dead config values to actual behavior, expand transient error coverage to include server errors, reduce the excessive request timeout, and add jitter to queue-level backoff.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: 
  - `daemon/graph.py` (shared with Phase 2 for response validation)
  - `daemon/config.py` (shared with Phase 3 for fallback config, Phase 4 for circuit breaker config)
  - `daemon/queue.py` (shared with Phase 4 for circuit breaker cleanup)
- **Shared APIs/interfaces**: `TRANSIENT_EXCEPTIONS` tuple (consumed by Phase 2), config fields (consumed by all phases)
- **Why this coupling**: Phase 1 establishes the config and error type foundations that other phases build upon. Changes are interface-level (config schema, error definitions) not implementation-level.

## Context
- Previous phase completed: N/A (root phase)
- Key decisions: See `decisions.md`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Expand `TRANSIENT_EXCEPTIONS` | Add `openai.APIStatusError` with status-code filtering: retry 429, 500, 502, 503, 529. Never retry 4xx (except 429). Implement a custom `retry_if_exception_type` predicate that checks status code. | `daemon/graph.py` |
| 2 | Wire retry delay config to `with_retry()` | Replace LangChain's default `wait_exponential_jitter=True` with explicit tenacity params using `llm_retry_delay_seconds` and `llm_retry_exponential_base` from config. If `with_retry()` doesn't support custom initial_delay, wrap with tenacity directly. | `daemon/graph.py` |
| 3 | Reduce `request_timeout` default | Change default from 660s to 120s. Keep configurable via env var/YAML. Add `request_timeout` as explicit field in config (it may already exist — verify). | `daemon/config.py` |
| 4 | Wire circuit breaker config to `queue.py` | Replace hardcoded `CIRCUIT_FAILURE_THRESHOLD=5` and `CIRCUIT_RECOVERY_TIMEOUT=300` in `queue.py` with values from `config.queue.circuit_breaker_failure_threshold` and `config.queue.circuit_breaker_recovery_timeout_seconds`. Pass config through to `MessageQueue`. | `daemon/queue.py`, `daemon/config.py` |
| 5 | Add jitter to queue-level backoff | In `repository.py` retry scheduling, add random jitter (±20%) to the exponential backoff delay to prevent thundering herd on shared provider. | `daemon/repositories/message_queue/repository.py` |
| 6 | Add `APIConnectionError` to queue-level awareness | Ensure `APIConnectionError` is logged distinctly at queue level (not just generic `Exception`). This improves Phase 4 observability. | `daemon/manager.py` |
| 7 | Add error-type-aware retry at queue level | Different error types should have different max retries: transient (rate limit, timeout) get full 5 retries, permanent (auth, context exceeded) fail immediately without retry. Add classification in `_process_queue`. | `daemon/manager.py` |
| 8 | Add tests for expanded error coverage | Test that `APIStatusError` with 500 is retried, 401 is not retried. Test that config values are actually used in retry behavior. Test jitter doesn't exceed bounds. | `tests/test_graph_retry.py` (new) |
| 9 | Verify and document LangGraph multi-turn checkpoint resume | **Gap**: The plan protects individual LLM calls, but doesn't verify what happens when a multi-turn agent task fails mid-graph (e.g., LLM → tool → LLM → tool → FAIL). Verify that LangGraph's checkpoint granularity correctly resumes from the last completed node (not the beginning). Test this with a multi-node graph: (1) create a test graph with 3+ LLM+tool turns, (2) inject failure at turn 3, (3) verify checkpoint resume replays only turn 3, not turns 1-2. Document the expected behavior. | `daemon/manager.py`, `tests/test_graph_retry.py` |

## Key Files
- `daemon/graph.py` — `TRANSIENT_EXCEPTIONS`, `with_retry()` call, `ThinkingChatOpenAI`
- `daemon/config.py` — `QueueConfig`, all retry/timeout fields
- `daemon/queue.py` — Hardcoded constants, `InstanceCircuitBreaker`, watchdog
- `daemon/manager.py` — `_process_queue`, `_process_message_with_tracking`
- `daemon/repositories/message_queue/repository.py` — `retry()` method, backoff calculation
- `tests/test_graph_retry.py` — New test file for LLM retry behavior

## Constraints
- **Backward compatible**: Existing `config.yaml` files must work. New defaults should match sensible values, not break existing deployments.
- **OpenAI-compatible**: All changes must work with any OpenAI-compatible provider (not just OpenAI directly).
- **LangChain compatibility**: The `with_retry()` wrapper is a LangChain method. If custom params aren't supported, may need to bypass it and use tenacity directly.
- **No behavioral regression**: Existing retry behavior for `RateLimitError`, `APITimeoutError`, `APIConnectionError` must continue working.

## Implementation Notes

### Task 1: Status-Code-Aware Retry Predicate

```python
# Approach: Custom predicate function instead of simple exception tuple
def _is_retryable_error(exc: BaseException) -> bool:
    """Check if an exception is retryable."""
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}
    
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)):
        return True
    return False

# Usage with tenacity:
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception

llm_with_retry = llm.with_retry(
    stop_after_attempt=max_retries,
    retry=retry_if_exception(_is_retryable_error),
    wait=wait_exponential_jitter(
        initial=config.llm_retry_delay_seconds,
        exp_base=config.llm_retry_exponential_base,
    ),
)
```

### Task 4: Config Wiring Pattern

```python
# In queue.py, replace:
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_RECOVERY_TIMEOUT = 300

# With:
class MessageQueue:
    def __init__(self, config: QueueConfig, ...):
        self.circuit_failure_threshold = config.circuit_breaker_failure_threshold
        self.circuit_recovery_timeout = config.circuit_breaker_recovery_timeout_seconds
```

### Task 7: Error Classification

```python
PERMANENT_ERRORS = (
    openai.AuthenticationError,     # 401/403
    openai.BadRequestError,         # 400 (includes context window exceeded)
)

# In _process_queue:
except Exception as e:
    if isinstance(e, PERMANENT_ERRORS):
        logger.error(f"Permanent error for {msg.message_id}: {e}")
        self._queue_repository.fail(msg.message_id, str(e))
        return  # No retry
    
    # Existing transient retry logic...
```

### Task 9: LangGraph Multi-Turn Checkpoint Resume Verification

**The gap**: Our retry improvements protect individual LLM calls, but agent graphs are multi-turn. A typical agent task flow:

```
User message → LLM call 1 → Tool call 1 → LLM call 2 → Tool call 2 → LLM call 3 → FAIL
```

LangGraph uses `AsyncSqliteSaver` for checkpointing. The key question: **where does resume pick up after a mid-graph failure?**

**Expected behavior (to verify)**:
- LangGraph saves checkpoint after each completed node (LLM response, tool execution)
- On retry, `_process_message_with_tracking()` detects existing checkpoint → sets `graph_input = None`
- LangGraph resumes from the last checkpoint (e.g., after Tool call 2), NOT from the beginning
- Only LLM call 3 re-executes — turns 1 and 2 are preserved

**Verification test plan**:
1. Create a test graph with 3 agent turns (LLM → tool → LLM → tool → LLM → tool)
2. Inject a failure (e.g., mock `APIStatusError(500)`) at turn 3's LLM call
3. Let queue-level retry trigger with checkpoint resume
4. Assert: turns 1 and 2 tool results are in the resumed graph state
5. Assert: only turn 3's LLM call re-executes
6. Assert: final result includes all 3 turns

**If checkpoint does NOT resume mid-graph correctly**:
- This is a LangGraph limitation, not our code
- Mitigation: document the limitation and add a config flag `resume_from_checkpoint: bool` (default True)
- If False, re-send the original message and let the graph replay from scratch (current behavior when no checkpoint exists)

**Files to inspect**: `daemon/manager.py:1136-1146` (checkpoint resume logic), `daemon/persistence.py` (checkpointer config)

## Deliverables
- [ ] `APIStatusError` with 429/500/502/503/529 is retried at LLM level
- [ ] `llm_retry_delay_seconds` and `llm_retry_exponential_base` are wired to actual retry behavior
- [ ] `request_timeout` defaults to 120s (configurable)
- [ ] `circuit_breaker_failure_threshold` and `circuit_breaker_recovery_timeout_seconds` are wired to queue.py
- [ ] Queue-level backoff has jitter
- [ ] Permanent errors (401/403/400) fail immediately without retry
- [ ] Tests for all new retry paths
- [ ] Multi-turn graph checkpoint resume verified and documented with test
- [ ] `APIStatusError` with 429/500/502/503/529 is retried at LLM level
- [ ] `llm_retry_delay_seconds` and `llm_retry_exponential_base` are wired to actual retry behavior
- [ ] `request_timeout` defaults to 120s (configurable)
- [ ] `circuit_breaker_failure_threshold` and `circuit_breaker_recovery_timeout_seconds` are wired to queue.py
- [ ] Queue-level backoff has jitter
- [ ] Permanent errors (401/403/400) fail immediately without retry
- [ ] Tests for all new retry paths
