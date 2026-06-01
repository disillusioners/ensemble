# Empty Job Result Bug Fix — Testing Lessons

## Date: 2026-06-01
## Branch: feature/fix-empty-job-result

### Root Cause
`TERMINAL_CANCEL_STATUSES` included `InstanceStatus.COMPLETED`, causing the TASK job orphan check to treat successfully completed instances as cancelled. Combined with `JobFeedbackObserver._process_event()` not passing `result_summary` to `atomic_transition()`, TASK jobs completed with `result_summary=None` showing "Result: N/A".

### Key Testing Insights

1. **Constant validation matters**: Directly testing `TERMINAL_CANCEL_STATUSES == frozenset([InstanceStatus.TERMINATED.value])` catches regressions if someone re-adds COMPLETED.

2. **MESSAGE vs TASK orphan paths differ**: The job_processor has separate orphan handling for MESSAGE and TASK jobs. Both need independent test coverage.

3. **Graceful degradation**: `_get_last_assistant_message_raw` can fail (DB error, no checkpoint). Tests verify the job still completes correctly with `result_summary=None`.

4. **test_instance_termination_job_cleanup.py** is the right home for orphan detection tests — it already has fixtures for `processor`, `mock_queue_service`, `mock_instance_manager`, etc.

### Pattern: Testing orphan check paths
```python
# 1. Create PROCESSING job with target instance
# 2. Mock instance_meta.status to desired value
# 3. Mock _get_last_assistant_message_raw for COMPLETED cases
# 4. Call processor._process_next_job()
# 5. Assert complete_job called with correct demand_state and result_summary
```
