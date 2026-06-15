# job_continue Tool — Testing Findings (2026-06-15)

## Summary
All tests pass (92/92). Implementation validated across all areas (A-E). No issues found.

## Key Architecture Facts

### job_continue at index 12
`job_continue` is registered at index 12 in the `create_job_tools()` return list — the last non-watch tool before the 4 watch tools (indices 13-16). The fix commit `87e04c9` moved it to the end of the non-watch tools to avoid breaking existing index-based tests.

### AsyncMessageResult.job_id
- `AsyncMessageResult` (manager.py:430-436) has a `job_id: str | None = None` field
- Only the JQ path (`enqueue_message_via_jq`, line 1495) populates `job_id`
- The WorkerPool path (`enqueue_message`, line 835) does NOT pass `job_id` (None for non-JQ paths)
- `job_continue` reads `result.job_id` and returns it as `new_job_id`

### 10-Step Validation (not 9)
The task listed 9 validation steps, but the code actually has 10: there's an "Instance not found" guard at line 449-451 between "Manager is None" and "Instance terminated/error". This is a necessary precondition for the status checks that follow.

### ZOMBIE PROCESSING check uses asyncio.to_thread
The zombie PROCESSING job check (step 8) calls `find_processing_message_jobs_by_instance` via `asyncio.to_thread` to avoid blocking the event loop — good async practice.

### Pattern Consistency: No `success: bool` flag
Both `job_create` and `job_continue` return dicts with `status` string (not `success: bool`). The return format is consistent within the codebase convention.

## Doc Observation (non-blocking)
`rule.md` line 74 mentions "terminated/errored" instances but omits "paused" — `workflow.md` and `tools_note.md` both mention "paused". Minor inconsistency, does not affect functionality.
