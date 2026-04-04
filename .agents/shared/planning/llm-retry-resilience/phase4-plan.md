# Phase 4: Observability & Circuit Breaker Cleanup

## Objective
Add structured logging and metrics for all retry/resilience events so operators can monitor and debug LLM failures. Unify the two circuit breaker implementations and wire all remaining config values. Clean up the codebase for consistency.

## Coupling
- **Depends on**: Phase 1 (retry events), Phase 2 (validation events)
- **Coupling type**: loose
- **Shared files with other phases**: 
  - `daemon/queue.py` (shared with Phase 1 for circuit breaker wiring)
  - `daemon/manager.py` (shared with Phase 2 for streaming events)
  - `daemon/sources/circuit_breaker.py` (unified with queue.py circuit breaker)
- **Shared APIs/interfaces**: Logging interfaces, circuit breaker state
- **Why this coupling**: Phase 4 is additive — it adds observability to events produced by Phases 1-3. No behavior changes, just logging/metrics additions.

## Context
- Previous phase completed: Phase 1 (retry coverage), Phase 2 (validation), Phase 3 (fallback)
- Key decisions: See `decisions.md`
- Current state: Retry events logged with basic `logger.error()`. Two circuit breaker implementations. No metrics.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create structured retry logger | New module `daemon/llm_observability.py` with structured logging for: LLM retry attempts (attempt N, error type, delay), fallback activations, response validation failures, streaming timeouts. Use consistent JSON-structured log format. | `daemon/llm_observability.py` (new) |
| 2 | Add retry attempt logging in `with_retry()` | Hook into LangChain/tenacity retry callbacks to log each attempt with: error type, status code, retry delay, attempt number. Either use tenacity's `before_sleep` callback or wrap the retry predicate. | `daemon/graph.py`, `daemon/llm_observability.py` |
| 3 | Add queue-level retry logging | In `_process_queue` error handler, log: message_id, instance_id, retry_count, max_retries, error type, next_retry_at, backoff_delay. | `daemon/manager.py` |
| 4 | Add fallback event logging | Log fallback activations with: primary error, fallback model, fallback success/failure, total latency. | `daemon/llm_fallback.py`, `daemon/llm_observability.py` |
| 5 | Add validation event logging | Log response validation results: valid/invalid, reason, message_id. Include in structured format. | `daemon/llm_validation.py`, `daemon/llm_observability.py` |
| 6 | Unify circuit breaker implementations | Merge `InstanceCircuitBreaker` (queue.py) and `CircuitBreaker` (sources/circuit_breaker.py) into a single implementation. Use the one in `sources/circuit_breaker.py` as the base, enhance it with instance-level tracking from queue.py. | `daemon/queue.py`, `daemon/sources/circuit_breaker.py` |
| 7 | Add circuit breaker state logging | Log circuit state transitions: CLOSED→OPEN (with failure count), OPEN→HALF_OPEN, HALF_OPEN→CLOSED. Include instance_id and failure history. | `daemon/sources/circuit_breaker.py` |
| 8 | Add retry metrics summary | Periodic (configurable interval) summary log: total retries, success rate by error type, fallback activation count, average retry delay. Can be a simple counter dict logged every N minutes. | `daemon/llm_observability.py` |

## Key Files
- `daemon/llm_observability.py` — New: structured logging and metrics
- `daemon/graph.py` — Retry attempt logging hooks
- `daemon/manager.py` — Queue-level retry logging
- `daemon/llm_fallback.py` — Fallback event logging
- `daemon/llm_validation.py` — Validation event logging
- `daemon/queue.py` — Circuit breaker unification
- `daemon/sources/circuit_breaker.py` — Enhanced base circuit breaker
- `tests/test_observability.py` — New: logging tests

## Constraints
- **No performance impact**: Structured logging should not add measurable latency. Use lazy formatting.
- **No external dependencies**: Don't add Prometheus, OpenTelemetry, etc. Use Python stdlib logging with JSON formatter.
- **Configurable log level**: Retry events should be at `WARNING` level (retrying is unusual but not an error). Validation failures at `WARNING`. Circuit transitions at `INFO`.
- **No PII in logs**: Don't log API keys, full message content, or user data. Only log metadata (error type, status code, attempt number).

## Implementation Notes

### Task 1: Structured Logger Design

```python
# daemon/llm_observability.py
import logging
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("daemon.llm.resilience")

class RetryEvent:
    """Structured retry event for logging."""
    __slots__ = ['timestamp', 'event_type', 'message_id', 'instance_id', 
                 'attempt', 'max_attempts', 'error_type', 'error_message',
                 'status_code', 'delay_seconds', 'provider', 'model']
    
    def to_dict(self):
        return {s: getattr(self, s) for s in self.__slots__ if getattr(self, s, None) is not None}

def log_retry_attempt(
    message_id: str,
    instance_id: str,
    attempt: int,
    max_attempts: int,
    error: Exception,
    delay: float,
    provider: str = "",
    model: str = "",
):
    """Log a retry attempt with structured context."""
    event = RetryEvent()
    event.event_type = "llm_retry"
    event.message_id = message_id
    event.instance_id = instance_id
    event.attempt = attempt
    event.max_attempts = max_attempts
    event.error_type = type(error).__name__
    event.error_message = str(error)[:200]  # Truncate
    event.delay_seconds = round(delay, 2)
    event.provider = provider
    event.model = model
    
    logger.warning(
        f"LLM retry attempt {attempt}/{max_attempts}",
        extra={"retry_event": event.to_dict()}
    )

class RetryMetrics:
    """Simple in-memory retry metrics counter."""
    def __init__(self):
        self.counters = defaultdict(int)
        self._last_report = time.time()
    
    def record(self, event_type: str, error_type: str, success: bool):
        key = f"{event_type}:{error_type}:{'success' if success else 'failure'}"
        self.counters[key] += 1
    
    def maybe_report(self, interval_seconds: int = 300):
        if time.time() - self._last_report >= interval_seconds:
            logger.info(f"Retry metrics summary: {dict(self.counters)}")
            self._last_report = time.time()
```

### Task 2: Tenacity Retry Callback

```python
# In graph.py, when setting up retry:
from tenacity import before_sleep

def _log_retry_attempt(retry_state):
    """Tenacity callback - log before each retry sleep."""
    log_retry_attempt(
        message_id=getattr(retry_state, 'message_id', 'unknown'),
        instance_id=getattr(retry_state, 'instance_id', 'unknown'),
        attempt=retry_state.attempt_number,
        max_attempts=retry_state.retry_object.stop.max_attempt_number,
        error=retry_state.outcome.exception(),
        delay=retry_state.next_action.sleep if retry_state.next_action else 0,
    )

# Apply:
llm_with_retry = llm.with_retry(
    ...,
    before_sleep=_log_retry_attempt,  # Log before each retry
)
```

### Task 6: Circuit Breaker Unification

```python
# Approach: Keep sources/circuit_breaker.py as the canonical implementation
# Enhance it to support instance-level tracking
# Remove InstanceCircuitBreaker from queue.py
# Use the unified CircuitBreaker in queue.py

# In sources/circuit_breaker.py, add:
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60.0):
        # ... existing code ...
        self._instance_states: Dict[str, CircuitState] = {}
    
    def check_instance(self, instance_id: str) -> bool:
        """Check circuit state for a specific instance."""
        state = self._instance_states.get(instance_id, CircuitState.CLOSED)
        # ... per-instance logic ...
    
    def record_instance_failure(self, instance_id: str):
        """Record failure for a specific instance."""
        # ...
    
    def record_instance_success(self, instance_id: str):
        """Record success for a specific instance."""
        # ...
```

## Deliverables
- [ ] `daemon/llm_observability.py` module with structured logging and metrics
- [ ] LLM retry attempts logged with attempt number, error type, delay
- [ ] Queue-level retries logged with message context
- [ ] Fallback activations logged
- [ ] Validation results logged
- [ ] Circuit breaker unified (single implementation)
- [ ] Circuit state transitions logged
- [ ] Periodic retry metrics summary
