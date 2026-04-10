# Architecture Decisions: Task Timeout & Retry

## Decision Log

### AD-1: TimeoutMonitor vs MainLoopBridge Timeout

**Decision**: Use TimeoutMonitor as the primary timeout mechanism; keep MainLoopBridge.run_async() with a generous safety-net timeout (2x configured).

**Rationale**: 
- MainLoopBridge timeout kills the `future.result()` wait but doesn't actually stop the LangGraph execution inside the event loop. The coroutine keeps running.
- TimeoutMonitor + CancellationToken provides cooperative cancellation — the LangGraph execution actually stops.
- MainLoopBridge timeout remains as a safety net for edge cases where cooperative cancellation fails.

**Consequence**: TaskProcessor.run_task() no longer uses `timeout=300`. It passes `timeout=None` or a very large value to MainLoopBridge.

---

### AD-2: Retry Creates New Task Row (Not Reuse)

**Decision**: Each retry creates a new Task row in the database. The parent task is marked CANCELLED.

**Rationale** (from design doc R4):
- Preserves complete history of each attempt (status, error, timestamps)
- No ambiguous state transitions on a single row
- Atomic: new row is either fully created or not at all
- Simplifies debugging — can see the full retry chain

**Consequence**: Need `get_retry_chain()` for debugging. Task count will grow with retries.

---

### AD-3: CANCELLED Status (Not FAILED)

**Decision**: Tasks that are cancelled (timeout, user request) get status `CANCELLED`. Only tasks that fail after max retries get `FAILED`.

**Rationale**:
- CANCELLED = externally terminated (timeout, shutdown, user request)
- FAILED = task logic failed and exhausted retries
- Different semantic → different status
- Enables queries like "how many tasks timed out" vs "how many tasks failed"

**Consequence**: New CANCELLED status in enum. Message status may need alignment (currently uses FAILED for all failures).

---

### AD-4: Backoff Schedule: exponential (base * 2^retry_count)

**Decision**: Exponential backoff with formula `min(base * 2^retry_count, max)`. Default: 60s → 120s → 240s → 480s → ... max 3600s.

**Rationale**:
- Standard exponential backoff pattern
- Configurable base and max for different deployment needs
- Matches the design doc specification

---

### AD-5: StaleTaskRecovery Does Not Access CancellationToken

**Decision**: StaleTaskRecovery uses DB flags (`cancel_requested`, `retry_scheduled`) and direct status changes. It does NOT have access to the in-memory CancellationToken.

**Rationale**: 
- StaleTaskRecovery runs in its own thread, separate from workers
- CancellationToken is created per-task by the Worker thread — not globally accessible
- If worker is alive: it has its own TimeoutMonitor (handles timeout independently)
- If worker is dead: no CancellationToken exists — StaleTaskRecovery force-cancels via DB
- The `cancel_requested` and `retry_scheduled` DB flags provide atomic guards against double-retry

**Consequence**: StaleTaskRecovery always force-cancels via DB. No IPC with workers. Uses `retry_scheduled` flag to avoid duplicating Worker's retry.

---

### AD-6: SQLite CHECK Constraint Not Updated

**Decision**: Do NOT update the CHECK constraint on the `status` column to include 'cancelled'. Rely on application-level validation via TaskStatus enum.

**Rationale**:
- SQLite doesn't support ALTER TABLE ALTER CONSTRAINT
- Updating the CHECK would require recreating the entire table (risky, slow for large datasets)
- SQLModel validates through the enum — raw SQL INSERTs are not used in the application
- The TEXT column accepts any string — no data loss risk

**Consequence**: Raw SQL queries bypassing SQLModel could insert invalid statuses. This is acceptable since all application code uses the enum.

---

### AD-7: No Retry on Generic Exceptions (Timeout Only)

**Decision**: Task retry is only triggered by timeout cancellation. Generic exceptions (LLM errors, etc.) result in permanent failure.

**Rationale**:
- LLM already has built-in retry (3 attempts by default)
- Generic errors may indicate persistent issues that retry won't fix
- Timeout is the specific scenario the design doc addresses
- Can be extended later by modifying `_handle_task_failure()` in Worker

**Consequence**: Tasks that fail due to non-timeout errors fail permanently. This matches the current behavior.

---

### AD-8: Worker Owns Retry Decision (Not TaskProcessor)

**Decision**: The Worker thread decides whether to retry a cancelled/failed task. TaskProcessor just routes and executes.

**Rationale**:
- Worker owns the full task lifecycle (claim → run → complete/fail/retry)
- Retry decision requires knowing max_retries and backoff config (Worker-level concern)
- TaskProcessor remains stateless and purely a routing layer
- Easier to test: mock TaskProcessor, verify Worker retry logic

**Consequence**: Worker directly calls `task_repo.schedule_retry()`. TaskProcessor doesn't know about retries.

---

### AD-9: `retry_scheduled` Boolean Guard Column (S1)

**Decision**: Add `retry_scheduled: bool = Field(default=False)` to Task model. `schedule_retry()` sets this flag atomically in the same transaction that creates the retry child.

**Rationale**:
- Prevents the double-retry race condition (C2): if both Worker and StaleTaskRecovery try to schedule retry for the same task, only one succeeds
- `schedule_retry()` checks `retry_scheduled=0` as a precondition — if already set, returns None
- `force_cancel_and_schedule_retry()` sets the flag during cancel — no window for race
- Atomic: the flag is set in the same transaction as the retry task creation

**Consequence**: One extra column in Task table. StaleTaskRecovery checks this flag before scheduling retry. Startup recovery can detect orphaned tasks where flag was set but child doesn't exist (crash between flag set and child insert — extremely rare, covered by `find_orphaned_cancelled_tasks()`).

---

### AD-10: Reuse `CancellationReason.TIMEOUT` (W4)

**Decision**: TimeoutMonitor uses the existing `CancellationReason.TIMEOUT` enum value instead of adding a new `TASK_TIMEOUT`.

**Rationale**:
- The existing `TIMEOUT = "timeout"` is semantically correct for task-level timeouts
- `WATCHDOG_RETRY = "watchdog_retry"` already covers the watchdog-specific case
- Adding `TASK_TIMEOUT` would overlap with `TIMEOUT` and create ambiguity
- `OperationCancelledError` carries context strings for debugging when finer-grained info is needed

**Consequence**: No new enum value. Code checking `reason == CancellationReason.TIMEOUT` handles both old and new timeout paths.
