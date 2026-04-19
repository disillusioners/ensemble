# Risks and Edge Cases

## R1: Double Error Report (Worker + TaskProcessor)

**Risk**: Both `ProcessMessageProcessor.process()` and `Worker._handle_task_failure()` call `_send_error_report()` for the same failure (normal processing errors propagate: TaskProcessor catches → re-raises → Worker catches).

**Mitigation**: Plan now wires `_send_error_report()` only in `TaskProcessor` for normal flow, and only in `Worker._handle_cancellation()` for cancellation (TaskProcessor never runs for cancellation). For `_handle_task_failure()`, only call for pre-processing errors (ValueError, RuntimeError) that bypass TaskProcessor. Queue-check dedup in `_send_error_report()` is the safety net.

**Severity**: Low — dedup prevents double reports; worst case is a wasted queue check.

## R2: `_send_error_report()` Itself Fails

**Risk**: If the error report enqueue fails (DB error, repository down), the parent is still stuck.

**Mitigation**: `_send_error_report()` already has a try/except wrapper that logs the failure. The DB state changes (child ERROR, hierarchy deleted, waiting_for decremented) have already committed before the enqueue attempt, so those are safe. Only the notification is lost. Consider adding a retry or StaleTaskRecovery catch for orphaned error reports in a future iteration.

**Severity**: Medium — state is correct but parent not notified. Acceptable for V1.

## R3: Race Condition: Concurrent Child Completions + Failures

**Risk**: `_send_error_report()` runs concurrently with `_process_child_completion_and_notify_parent()` from another child. Both try to decrement `waiting_for` and cascade.

**Mitigation**: All state mutations must be inside a single atomic DB transaction with row-level locking (`session.get(Instance, parent_id, with_for_update=True)`). This is the same pattern used by the success path. Race window is limited to a single DB transaction.

**Severity**: Medium — could lead to `waiting_for` going negative or wrong cascade. Mitigated by atomic transaction.

## R4: StaleTaskRecovery vs Worker Race

**Risk**: StaleTaskRecovery force-cancels a task that is about to be failed by the Worker. Or Worker fails a task that StaleTaskRecovery already handled.

**Mitigation**: StaleTaskRecovery already re-reads task status before acting (FIX C1). If the Worker already failed the task, StaleTaskRecovery sees `status == FAILED` and skips it. Error report callback is only invoked if task is acted upon.

**Severity**: Low.

## R5: Non-child Instances (Top-Level Agent)

**Risk**: `_send_error_report()` is called for a top-level agent (no parent_id).

**Mitigation**: `_send_error_report()` already checks `parent_id` and returns early if None. No issue.

**Severity**: None.

## R6: Fire-and-Forget from Worker Threads During Shutdown

**Risk**: `MainLoopBridge.run_async_no_wait()` schedules the coroutine but the event loop may be closed by the time it executes.

**Mitigation**: `run_async_no_wait` already handles `loop is None or loop.is_closed()` gracefully (logs warning, returns). During shutdown, the parent is also terminating, so a missing notification is acceptable. StaleTaskRecovery startup recovery will catch any orphaned tasks.

**Severity**: Low.

## R7: Error Report Message Triggers Agent Response

**Risk**: The error report message enqueued in parent's queue has `source="internal_error_report:{child_id}"`. When the parent agent processes it, the LLM sees it and may respond.

**Mitigation**: None needed — this is the desired behavior. The parent agent's soul/rule guides how it handles errors (log, retry, notify user, continue). The `source` field is set so downstream dispatch logic can filter it if needed (already handled in TaskProcessor's dispatch logic).

**Severity**: None (by design).

## R8: Parent Instance Already Terminated/Completed

**Risk**: Child fails but parent was already terminated by user or system.

**Mitigation**: `_send_error_report()` should check parent's status before enqueueing. If parent is `TERMINATED` or `COMPLETED`, skip the error report enqueue but still update child state and hierarchy.

**Severity**: Low — would be a wasted queue write at worst.
