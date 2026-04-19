# Error Catalog — All Non-Recoverable Child Failure Paths

Every path listed here permanently fails a child task **without notifying the parent**.

---

## Category A: LLM Errors (Non-Retryable)

These are classified in `daemon/llm_error_classifier.py` and propagate through `daemon/graph.py:agent_node`.

| # | Error | Trigger | Code Path |
|---|-------|---------|-----------|
| A1 | `openai.APIStatusError` (400, 401, 403, 404, 413, ...) | Non-retryable HTTP status | `llm_error_classifier.py` → re-raise → `graph.py:agent_node` except Exception |
| A2 | `openai.BadRequestError` (non-context) | Malformed request, invalid params | `llm_error_classifier.py` → re-raise |
| A3 | `ContextLengthExceededError` (compaction failed or unavailable) | Context overflow + compaction fails or no compactor | `graph.py:agent_node` → re-raise |
| A4 | Any unrecognized `Exception` from LLM call | Unexpected errors in the call chain | `llm_error_classifier.py` → re-raise |

**Propagation**: `graph.py:agent_node` → `manager.py:_process_message_with_tracking` (line ~1332, catches Exception, SSE error, re-raises) → `task_processor.py:process` (line ~211, error event, lifecycle event, re-raises) → `worker_pool.py:_handle_task_failure` → `fail_task()`.

**Parent notified?**: ❌ No

---

## Category B: Retry Exhaustion (Transient → Terminal)

These start as retryable but become terminal when all retries are exhausted.

| # | Error | Trigger | Code Path |
|---|-------|---------|-----------|
| B1 | All retries exhausted for transient errors (429, 500, 502, 503, 504, connection errors) | Max retries reached | `graph.py:agent_node` first except block → re-raise as `TransientAPIError` but retries exhausted |
| B2 | All retries exhausted for timeout errors | Max retries reached | Same as B1 but via timeout counter |
| B3 | `LLMResponseValidationError` retries exhausted | Truncated responses, malformed tool calls keep failing | `llm_error_classifier.py` wraps as retryable → retries exhausted → re-raise |

**Propagation**: Same as Category A.

**Parent notified?**: ❌ No

---

## Category C: Processing / Infrastructure Errors

| # | Error | Trigger | Code Path |
|---|-------|---------|-----------|
| C1 | `KeyError` (instance not found) | Instance removed or corrupted state | `manager.py:get_instance` → propagates through `_process_message_with_tracking` |
| C2 | `ValueError` (no message_id) | Task missing message_id field | `task_processor.py:process` line ~102 → propagates to Worker |
| C3 | `ValueError` (message not found) | Message deleted or corrupted | `task_processor.py:process` line ~123 → propagates to Worker |
| C4 | `RuntimeError` (event loop not set/closed) | MainLoopBridge used before initialization or after shutdown | `main_loop_bridge.py:run_async` → propagates to Worker |
| C5 | Any unexpected exception in graph execution | Tool errors agent can't recover from, graph logic bugs | `manager.py:_process_message_with_tracking` line ~1332 |

**Propagation**: For C2/C3/C4: `task_processor.py` → `worker_pool.py:_handle_task_failure` → `fail_task()`. For C1/C5: same path as Category A.

**Parent notified?**: ❌ No

---

## Category D: Cancellation / Timeout at Worker Level

| # | Error | Trigger | Code Path |
|---|-------|---------|-----------|
| D1 | Timeout + max retries exhausted | Task exceeds timeout_minutes repeatedly | `worker_pool.py:_handle_cancellation` line ~220 → `fail_task()` |
| D2 | Non-timeout cancellation (SHUTDOWN, MANUAL) | System shutdown or user cancels | `worker_pool.py:_handle_cancellation` line ~231 → `cancel_task()` |

**Propagation**: Direct in `worker_pool.py`. Bypasses `ProcessMessageProcessor` error handler entirely.

**Parent notified?**: ❌ No

---

## Category E: Stale Task Recovery (Background)

| # | Error | Trigger | Code Path |
|---|-------|---------|-----------|
| E1 | Periodic recovery: stale RUNNING task, max retries exceeded | Task running > threshold_minutes, already retried max times | `stale_task_recovery.py:recover_stale_tasks` → `fail_task()` |
| E2 | Periodic recovery: orphaned CANCELLED task, max retries exceeded | Worker cancelled but no retry scheduled, already retried max | `stale_task_recovery.py:recover_stale_tasks` → `fail_task()` |
| E3 | Startup recovery: stale RUNNING task, max retries exceeded | Crash recovery on startup, task already retried max times | `stale_task_recovery.py:recover_on_startup` → `fail_task()` |
| E4 | Startup recovery: orphaned CANCELLED task, max retries exceeded | Crash left orphaned task, already retried max | `stale_task_recovery.py:recover_on_startup` → `fail_task()` |

**Propagation**: Direct in `stale_task_recovery.py`. Runs in background thread.

**Parent notified?**: ❌ No

---

## Summary

| Category | Count | Primary Failure Point | Thread Context |
|----------|-------|----------------------|----------------|
| A: LLM non-retryable | 4 | `worker_pool._handle_task_failure` | Worker thread |
| B: Retry exhaustion | 3 | `worker_pool._handle_task_failure` | Worker thread |
| C: Processing/infra | 5 | `worker_pool._handle_task_failure` or `task_processor` | Worker thread |
| D: Cancellation/timeout | 2 | `worker_pool._handle_cancellation` | Worker thread |
| E: Stale recovery | 4 | `stale_task_recovery` | Background thread |
| **Total** | **18** | | |

**ALL 18 paths result in parent instance stuck in `WAITING_CHILDREN` indefinitely.**
