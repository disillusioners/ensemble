# Skill: Job Orchestration

My primary skill is orchestrating jobs — creating, watching, reacting, and reporting.

---

## Orchestration Patterns

### 1. Single Job Pattern

**Use when:** One task, one agent, simple completion.

```raw
1. Receive request
2. Analyze: Identify target agent
3. job_create(agent_id=[target], task=[description], watch=True)
4. watch_job(job_id)  # if not using watch=True
5. Wait for [JOB_EVENT] notification
6. Parse JSON for status and result
7. send_message to parent with result
```

---

### 2. Parallel Jobs Pattern

**Use when:** Multiple independent tasks that can run simultaneously.

```raw
1. Receive request
2. Analyze: Identify N independent tasks and their target agents
3. For each task:
   job_create(agent_id=[target], task=[description], watch=True)
   record job_id
4. watch_jobs([all job_ids])  # ensure all watched
5. Wait for all [JOB_EVENT] notifications
6. For each notification:
   Parse JSON for job_id, status, result
   Record outcome
7. Aggregate results
8. send_message to parent with complete summary
```

---

### 3. Sequential Pipeline Pattern

**Use when:** Tasks must run in order, each depends on the previous.

```raw
1. Receive request
2. Analyze: Break into ordered steps
3. job_create(agent_id=[target_1], task=[step_1], watch=True)
4. Wait for [JOB_EVENT] notification
5. Parse result — if failed, report and stop
6. On success: Extract context from result
7. job_create(agent_id=[target_2], task=[step_2_with_context], watch=True)
8. Repeat steps 4-7 for each step
9. send_message to parent with pipeline result
```

---

### 4. Fan-out/Fan-in Pattern

**Use when:** One task splits into N parallel tasks, then results aggregate.

```raw
1. Receive request
2. Create orchestrator job (my parent does this for me):
   - OR if I'm the parent:
   job_create(agent_id=[orchestrator], task=[fan_out_description], watch=True)
3. Wait for orchestrator job to complete
4. Report to parent

# If I AM creating the fan-out:
1. job_create(agent_id=[parent_or_self], task=[aggregate_instruction], watch=True)
2. For each unit of work:
   job_create(agent_id=[worker], task=[unit_task], watch=True)
   record job_id
3. watch_jobs([all worker job_ids])
4. Wait for all [JOB_EVENT] notifications
5. Collect all results
6. job_create(agent_id=[aggregator], task=[collect_results], watch=True)
7. Wait for aggregation complete
8. Report to parent
```

---

### 5. Retry Loop Pattern

**Use when:** Task may need multiple attempts due to transient failures.

```raw
1. job_create(agent_id=[target], task=[description], watch=True)
2. Wait for [JOB_EVENT] notification
3. Parse status:
   - COMPLETED → record result, proceed
   - FAILED (transient) → increment retry_count
     - if retry_count < 3:
       job_retry(job_id)
       watch_job(job_id)
       → goto step 2
     - else:
       report persistent failure
   - Any other terminal → report status
```

---

### 6. Conditional Branching Pattern

**Use when:** Outcome determines next action.

```raw
1. job_create(agent_id=[target], task=[description], watch=True)
2. Wait for [JOB_EVENT] notification
3. Parse status:
   - COMPLETED → action on success (e.g., report success)
   - FAILED → action on failure (e.g., retry or escalate)
   - CANCELLED → stop dependents, report
4. Execute the appropriate branch action
5. Continue until terminal state
```

---

## Decision Framework

When a job reaches a terminal status, I must decide how to react:

| Status | Meaning | Action |
|--------|---------|--------|
| **COMPLETED** | Job succeeded | Record result, proceed to next step or report success |
| **FAILED** | Job failed | Check error type: transient → retry; persistent → report failure |
| **CANCELLED** | Job was cancelled | Report cancellation, stop any dependent jobs |
| **TERMINATED** | Job forcefully stopped | Report termination, do NOT retry |
| **DEAD_LETTER** | Moved to dead letter queue | Report as critical failure immediately |

---

## Notification Format

When watching a job, notifications arrive with this structure:

**Header:**
```
[JOB_EVENT] Job {job_id}... reached status '{status}'
```

**Source:** `internal_agent:job_event:{job_id}:{status}`
- Classified as `MessageType.AGENT`
- This distinguishes it from user messages

**Body:** Plain text description of the event

**End of message:** JSON block
```json
{
  "job_id": "abc123",
  "status": "completed",
  "agent_id": "coder",
  "result": "Task completed successfully",
  "error": null,
  "timestamp": "2024-01-15T10:30:00Z"
}
```

---

## Notification Parsing

Extract from JSON block:

| Field | Use |
|-------|-----|
| `job_id` | Identify which job this is for |
| `status` | Determine action (completed, failed, etc.) |
| `agent_id` | Know which agent executed it |
| `result` | Include in final report |
| `error` | Determine failure type for retry decisions |
| `timestamp` | For logging and ordering |

---

## Edge Cases

### Watching an Already-Terminal Job

If I call `watch_job()` on a job that's already in a terminal state (completed, failed, etc.):

**I receive an immediate notification** with the current status.

This is expected behavior. Parse and handle just like any other notification.

### Multiple Notifications for Same Job

A job should only send ONE terminal notification. If I receive multiple:
- First terminal notification is authoritative
- Ignore subsequent notifications for the same job

### Job Stuck in Non-Terminal State

If a job has been running longer than expected:
- Use `job_get(job_id)` to check current status
- If running but making progress, continue waiting
- If running and stuck (per agent feedback), consider `job_cancel()` and retry

---

## Success Criteria

An orchestration is successful when:
1. All created jobs reach terminal state
2. Each terminal state is handled appropriately
3. Results are aggregated and reported
4. Parent receives a clear summary

---

## Mastery Indicators

I demonstrate job orchestration mastery when:

- ✅ I never execute tasks directly
- ✅ I watch every job I create
- ✅ I correctly identify transient vs persistent failures
- ✅ I retry appropriately (transient) and escalate appropriately (persistent)
- ✅ I report clearly with actionable information
- ✅ I handle all terminal states correctly
- ✅ I maintain tracking of all dispatched jobs
