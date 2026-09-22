# Skill: Job Orchestration

My primary skill is orchestrating jobs — creating, watching, reacting, and reporting.

> **You wait on MISSIONS, not receipts.** Create work with `job_create` — you
> hold a receipt (job_id). To wait on the WORK: `await_mission(mission_id)`
> in-turn, or `watch_mission(mission_id_or_job_ref)` to yield and be revived
> at mission-terminal. `watch_mission` accepts the receipt you created or the
> mission_id, and watches every receipt that exists at call time. After
> `job_continue`, call `watch_mission` again — new receipts are not
> auto-watched. The FIRST `[JOB_EVENT]` after your watch is the signal; later
> events on the same mission's other receipts are echoes — act once. Receipts
> answer transport questions only (`job_get`); never watch a receipt for a
> work question. A revived mission needs a fresh `watch_mission`; the event
> carries no epoch — `get_mission` for details. `await_mission` timeout
> returns a SNAPSHOT, not an error — check `liveness` and decide.

---

## Orchestration Patterns

### 1. Single Job Pattern

**Use when:** One task, one agent, simple completion.

```raw
1. Receive request
2. Analyze: Identify target agent
3. job_create(agent_id=[target], message=[description], watch=True)
4. watch_mission(job_id)  # if not using watch=True — the receipt is a valid handle
5. Wait for [JOB_EVENT] notification
6. Parse the body for status, Agent line, and Result/Error
7. Emit your result as your response (the system delivers it to your parent)
```

> **Reporting:** I do NOT call `send_message` — I don't have that tool. I
> report by emitting my summary as my turn response; the system routes it
> to my parent automatically. (See How I Report.)

---

### 2. Parallel Jobs Pattern

**Use when:** Multiple independent tasks that can run simultaneously.

```raw
1. Receive request
2. Analyze: Identify N independent tasks and their target agents
3. For each task:
   job_create(agent_id=[target], message=[description], watch=True)
   record job_id
4. watch_mission(job_id) for each  # every receipt becomes a watched row
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
3. job_create(agent_id=[target_1], message=[step_1], watch=True)
4. Wait for [JOB_EVENT] notification
5. Parse result — if failed, report and stop
6. On success: Extract context from result
7. job_create(agent_id=[target_2], message=[step_2_with_context], watch=True)
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
   job_create(agent_id=[orchestrator], message=[fan_out_description], watch=True)
3. Wait for orchestrator job to complete
4. Emit your summary as your response

# If I AM creating the fan-out:
1. job_create(agent_id=[parent_or_self], message=[aggregate_instruction], watch=True)
2. For each unit of work:
   job_create(agent_id=[worker], message=[unit_task], watch=True)
   record job_id
3. watch_mission(worker_job_id) for each  # the receipt is a valid handle
4. Wait for all [JOB_EVENT] notifications
5. Collect all results
6. job_create(agent_id=[aggregator], message=[collect_results], watch=True)
7. Wait for aggregation complete
8. Emit your summary as your response
```

---

### 5. Retry Loop Pattern

**Use when:** Task may need multiple attempts due to transient failures.

```raw
1. job_create(agent_id=[target], message=[description], watch=True)
2. Wait for [JOB_EVENT] notification
3. Parse status:
   - COMPLETED → record result, proceed
   - FAILED (transient) → increment retry_count
     - if retry_count < 3:
       job_retry(job_id)
       watch_mission(new_job_id)  # re-watch: the retry receipt is new
       → goto step 2
     - else:
       report persistent failure
   - Any other terminal → report status
```

---

### 6. Conditional Branching Pattern

**Use when:** Outcome determines next action.

```raw
1. job_create(agent_id=[target], message=[description], watch=True)
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

### Mid-flight non-terminal statuses (question / answer channel)

Non-terminal notifications arrive on the SAME `[JOB_EVENT]` header; the
watch registration survives them (the terminal event still fires later).

| Status | Meaning | Action |
|--------|---------|--------|
| **QUESTION_REQUESTED** ❓ | The watched instance paused and asked the human a structured question | Extract the question pack from the `Result:` line. Relay the questions (text + options) to the human via the chat source. When the human replies, call `POST /api/jobs/{work_id}/answer` with body `{"answers": {<question_id>: <answer>}, "question_pack_id": "<pack_id>"}` — echo the `question_pack_id` from the pack so stale answers are rejected (400 QUESTION_PACK_MISMATCH). Do NOT call job_continue / job_inject on a question-paused instance. |
| **ANSWER_RECEIVED** ✓ | The human's answer was accepted and the asker is resuming | Informational — log it. The watch row survives for the eventual terminal event. No action needed. |
| **MID-FLIGHT REPORT** ⟳ | The watched instance surfaced a progress note or decision point WITHOUT pausing | Forward to the chat source as a non-modal update. Do NOT block; do NOT call /answer. If `decision_required` appears in the payload, surface it prominently but keep going. |
| **STUCK_AWAITING_ANSWER** ⏳ | The wedge guard heartbeat — the asker has been paused awaiting an answer for 30+ min | Log at WARNING and relay to the human AGAIN with a `(_reminder)` suffix — the question may have scrolled away. The payload carries `waiting_for_seconds`, `emission_index`, and `wedge_chain` (paused ancestry). After 3 emissions (~60 min) the guard terminates the asker — treat the reminder as urgent. |


**Mid-flight payload lines:** on `question requested ❓` the `Result:` line carries the question pack (questions with ids, options, and the `pack_id` to echo back on answer); on `answer received ✓` it carries the answered pack. `stuck awaiting answer ⏳` notifications carry a `Progress:` line (waiting time + emission index). These are payload contents of the EXISTING line types — the envelope structure itself is unchanged.

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
| `result` | `Result:` line | Include in final report (only on completion); on `question requested ❓` it is the question pack to relay |
| `error` | `Error:` line | Determine failure type for retry decisions (only on failure) |

---

## Edge Cases

### Watching an Already-Terminal Mission

If I call `watch_mission()` on a mission that's already in a terminal state (completed, failed, etc.):

**I receive an immediate notification** with the current status — one per watched receipt.

This is expected behavior. The FIRST notification is the signal; later events on the same mission's other receipts are echoes. Parse and handle once, just like any other notification.

### Multiple Notifications for Same Mission

A mission-terminal watch produces N `[JOB_EVENT]`s for N watched receipts — they are all echoes of ONE mission terminal. If I receive multiple:
- The FIRST terminal notification after my watch is the signal — it is authoritative
- Subsequent notifications for the same mission's other receipts are echoes — act once
- (A single job still sends only ONE terminal notification)

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

The `job_id` returned by `job_create` (and surfaced as `new_job_id` by `job_continue`) is a `work_id` handle — a stable UUID4 minted on Task/JobItem creation. The same handle is accepted by `watch_mission`, `job_get`, and `job_continue` for **both** traditional job queue items and continued-instance work (subsequent message turns on an instance). In practice this means: if you call `job_continue` against a completed instance to send a follow-up message, the returned `new_job_id` can be passed directly to `watch_mission` to receive a `[JOB_EVENT]` when the new turn finishes — no separate "instance watch" tool is needed. `job_continue` resolves both task and job work_ids (Phase 5 P-B, 2026-06-27), so continuing from the task `work_id` returned by a prior `job_continue` works without manual handle translation. `job_list` shows root-instance work by default (Phase 5 P-A, 2026-06-27) — child-instance turns/reports are filtered out by the resolver so the management view is not drowned in noise. `watch_mission` accepts the receipt handle OR the mission_id, and covers every receipt that exists at call time — after `job_continue`, call `watch_mission` again, because new receipts are not auto-watched.

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
