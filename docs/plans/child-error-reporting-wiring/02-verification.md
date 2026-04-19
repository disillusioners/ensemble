# Verification Plan

## Unit Tests

### Test 1: `_send_error_report()` decrements `waiting_for` and cascades parent status

```
Given: Parent instance with waiting_for=2, status=WAITING_CHILDREN
  And: Child instance with parent_id pointing to parent
When: _send_error_report(child_id, "error", "execution_error") is called
Then: Parent.waiting_for == 1
  And: Parent.status == WAITING_CHILDREN (still waiting for other child)
  And: Child.status == ERROR
  And: Error report message enqueued in parent's queue
  And: child_failed SSE event broadcast
```

### Test 2: `_send_error_report()` cascades parent to RUNNING when last child fails

```
Given: Parent instance with waiting_for=1, status=WAITING_CHILDREN
  And: Child instance with parent_id pointing to parent
When: _send_error_report(child_id, "error", "execution_error") is called
Then: Parent.waiting_for == 0
  And: Parent.status == RUNNING (or IDLE, depending on queue)
  And: Completion report task created for parent
```

### Test 2: Cascade to RUNNING when last child fails

```
Given: Parent instance with waiting_for=1, status=WAITING_CHILDREN
  And: Child instance with parent_id pointing to parent
When: _send_error_report(child_id, "error", "execution_error") is called
Then: Parent.waiting_for == 0
  And: Parent.status == RUNNING (or COMPLETED if no pending messages)
  And: Completion report task created for parent
```

### Test 3: Instance hierarchy deleted on child failure

```
Given: instance_hierarchy table has record linking child_id → parent_id
When: _send_error_report(child_id, "error", "execution_error") is called
Then: SELECT * FROM instance_hierarchy WHERE child_id = ? returns empty
  And: Parent still exists in instances table
```

### Test 4: Deduplication prevents double error reports

```
Given: Parent queue already has message with source="internal_error_report:{child_id}"
When: _send_error_report(child_id, ...) is called again
Then: No new message enqueued
  And: No duplicate SSE event
  And: Second call returns early (dedup guard triggered)
```

### Test 4: Non-child instances are skipped

```
Given: Instance with parent_id=None
When: _send_error_report(instance_id, ...) is called
Then: Returns early, no error
```

### Test 5: Error type classification

```
Assert _classify_error_type(openai.APIStatusError(413, ...)) == "payload_too_large"
Assert _classify_error_type(openai.APITimeoutError(...)) == "timeout_exhausted"
Assert _classify_error_type(ContextLengthExceededError(...)) == "context_length_exceeded"
Assert _classify_error_type(ValueError(...)) == "execution_error"
```

## Integration Tests

### Test 6: 413 error → parent receives error report

```
Given: Parent instance spawns child with a message
  And: LLM proxy returns 413 for child's request
When: Worker processes child's task
Then: Task status == FAILED
  And: Error report message appears in parent's queue
  And: Parent.waiting_for decremented
  And: Parent transitions out of WAITING_CHILDREN
  And: Child instance status == ERROR
  And: Child's message status == FAILED
```

### Test 7: Cancellation → parent receives error report

```
Given: Parent instance spawns child with a message
  And: Child's task is cancelled (timeout + max retries)
When: Worker handles cancellation
Then: Task status == FAILED
  And: Error report message appears in parent's queue with error_type="max_retries_exceeded"
```

### Test 8: Stale task permanent failure → parent receives error report

```
Given: Parent instance spawns child with a message
  And: Child's task is stale (> threshold_minutes) and max retries exceeded
When: StaleTaskRecovery runs recovery
Then: Task status == FAILED
  And: Error report message appears in parent's queue with error_type="stale_task_failure"
```

### Test 9: Parent agent receives and handles error report

```
Given: Error report enqueued in parent's queue with metadata type="error_report"
When: Parent agent processes the error report message
Then: Parent agent sees the error content in conversation
  And: Parent can decide to retry, notify user, or continue
```

## Regression Tests

### Test 10: Success path still works (no false error reports)

```
Given: Parent instance spawns child with a message
  And: Child processes successfully
When: _process_child_completion_and_notify_parent runs
Then: No error report is sent
  And: Normal completion report is sent
  And: Parent.waiting_for decremented correctly
```

### Test 11: Multiple children, one fails

```
Given: Parent spawns 3 children (waiting_for=3)
  And: Child 1 succeeds, child 2 fails with 413, child 3 succeeds
When: All processing completes
Then: Parent receives 2 completion reports and 1 error report
  And: Parent.waiting_for == 0
  And: Parent transitions to RUNNING
```

## Manual Verification

1. Start daemon, spawn a parent agent that creates a child
2. Simulate 413 error (e.g., use mock LLM that returns 413)
3. Verify parent agent receives error report in conversation
4. Verify parent agent's `waiting_for` reaches 0
5. Verify parent agent can continue processing after child failure
