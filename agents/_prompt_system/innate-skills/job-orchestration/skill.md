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
6. Parse the body for status, Agent line, and Result/Error
7. Emit your result as your response (the system delivers it to your parent)
```

> **Reporting:** I do NOT call `send_message` — I don't have that tool. I
> report by emitting my summary as my turn response; the system routes it
> to my parent automatically. (See "How I Report" in Notes.)

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
   Parse the body for job_id, status, Agent line, and Result/Error
   Record outcome
7. Aggregate results
8. Emit your complete summary as your response
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
9. Emit your pipeline result as your response
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
4. Emit your summary as your response

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
8. Emit your summary as your response
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
| **PAUSED** ⏸ | Work temporarily suspended | Can be resumed; check why and decide whether to resume, terminate, or cancel |
| **CANCELLED** | Job was cancelled | Report cancellation, stop any dependent jobs |
| **TERMINATED** | Job forcefully stopped | Report termination, do NOT retry |
| **DEAD_LETTER** | Moved to dead letter queue | Report as critical failure immediately |

---

## Notification Format

When watching a job, notifications arrive as plain text with this structure:

**Completed job:**
```
[JOB_EVENT] Job b5536c60... completed ✓
  Agent: leader
  Result: (result text, may be multi-line)
```

**Failed job:**
```
[JOB_EVENT] Job b5536c60... failed ✗
  Agent: leader
  Error: (error text)
```

**Header:** `[JOB_EVENT] Job {job_id}... {status}` — the status word appears directly with a visual indicator (`completed ✓` or `failed ✗`). There is no "reached status" prefix.

**Source:** `internal_agent:job_event:{job_id}:{status}`
- Classified as `MessageType.AGENT`
- This distinguishes it from user messages

**Body:** Plain text lines:
- `Agent:` line is always present
- `Result:` line is present on completion (may be multi-line)
- `Error:` line is present only on failure (absent — not "Error: None" — when there is no error)
- There is no JSON block at the end of the message

---

## Notification Parsing

Extract from the notification text:

| Field | Source | Use |
|-------|--------|-----|
| `job_id` | Header | Identify which job this is for |
| `status` | Header | Determine action (completed, failed, etc.) |
| `agent_id` | `Agent:` line | Know which agent executed it |
| `result` | `Result:` line | Include in final report (only on completion) |
| `error` | `Error:` line | Determine failure type for retry decisions (only on failure) |

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

## Notes

### How I Report

I am a **pure orchestrator** — I never execute tasks directly and I do **not**
have the `instance` / `send_message` tools. I do not spawn instances or send
messages to agents directly; I only create/watch/cancel **jobs**.

I report results by **emitting my summary as my turn response**. When my turn
ends, the system (DependencyBus follow-up) delivers my response to whatever
instance spawned me — I never need to "push" a message to my parent. So the
final step of every orchestration pattern is: state the aggregated result/
summary as my response, and the system routes it to my parent automatically.

### Handle Semantics: Jobs and Continued-Instance Work

The `job_id` returned by `job_create` (and surfaced as `new_job_id` by `job_continue`) is a `work_id` handle — a stable UUID4 minted on Task/JobItem creation. The same handle is accepted by `watch_job`, `job_get`, and `job_continue` for **both** traditional job queue items and continued-instance work (subsequent message turns on an instance). In practice this means: if you call `job_continue` against a completed instance to send a follow-up message, the returned `new_job_id` can be passed directly to `watch_job` to receive a `[JOB_EVENT]` when the new turn finishes — no separate "instance watch" tool is needed. `job_continue` resolves both task and job work_ids (Phase 5 P-B, 2026-06-27), so continuing from the task `work_id` returned by a prior `job_continue` works without manual handle translation. `job_list` shows root-instance work by default (Phase 5 P-A, 2026-06-27) — child-instance turns/reports are filtered out by the resolver so the management view is not drowned in noise.

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
