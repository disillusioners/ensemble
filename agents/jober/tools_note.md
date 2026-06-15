# Tool Usage Notes

## Creating Jobs

### job_create

**Purpose:** Create a new job for execution by an agent.

**Always use `watch=True`** to ensure atomic creation and monitoring registration.

```raw
job_create(
    agent_id="coder",           # Target agent
    task="Fix the login bug",    # Task description
    watch=True,                 # CRITICAL: Watch immediately
    priority=5                  # Optional: 1-10, higher = more urgent
)
```

**Returns:** `{"job_id": "abc123", ...}`

**Important:** Record the returned `job_id` for tracking.

---

### Creating Multiple Jobs (Parallel)

For independent jobs that can run simultaneously:

```raw
# Create all jobs first
job_create(agent_id="coder", task="Task A", watch=True)
→ record job_id_1
job_create(agent_id="reviewer", task="Task B", watch=True)
→ record job_id_2
job_create(agent_id="tester", task="Task C", watch=True)
→ record job_id_3

# Then ensure all are watched
watch_jobs([job_id_1, job_id_2, job_id_3])

# Verify
list_watched_jobs() → should show all 3
```

---

## Watching Jobs

### watch_job

**Purpose:** Register to receive notifications when a job status changes.

```raw
watch_job(job_id="abc123")
```

**Edge case:** If job is already in a terminal state, you receive an immediate notification.

---

### watch_jobs

**Purpose:** Watch multiple jobs at once.

```raw
watch_jobs(job_ids=["abc123", "def456", "ghi789"])
```

**Use when:** Creating multiple parallel jobs.

---

### list_watched_jobs

**Purpose:** Verify which jobs are currently being watched.

```raw
list_watched_jobs()
```

**Returns:** List of job IDs you're watching.

**Use for:** Verification after dispatch, debugging tracking issues.

---

### unwatch_job

**Purpose:** Stop watching a job (rarely needed, watches auto-clean).

```raw
unwatch_job(job_id="abc123")
```

---

## Checking Job Status

### job_get

**Purpose:** Get detailed information about a specific job.

```raw
job_get(job_id="abc123")
```

**Returns:** Full job details including status, result, error, timestamps.

**Use for:** Checking status when notification is unclear, debugging.

---

### job_list

**Purpose:** List jobs, optionally filtered by status.

```raw
job_list(statuses=["running", "pending"])
```

**Statuses:** `pending`, `running`, `completed`, `failed`, `cancelled`, `terminated`, `dead_letter`

**Use for:** Finding jobs, auditing active work.

---

## Communicating Results

### send_message

**Purpose:** Send messages to parent instance or other agents.

**This is how I report results.** After jobs complete, send summary to parent.

```raw
send_message(
    instance_id=parent_instance_id,
    message="""
    ✅ Orchestration Complete: [goal]

    Jobs Summary:
    - [job_id]: ✅ Completed
    - [job_id]: ❌ Failed (after 3 retries)

    Result: [summary]
    """
)
```

---

## Queue Management

### queue_list

**Purpose:** List available job queues.

```raw
queue_list()
```

**Use for:** Understanding queue structure, organizing work.

---

### queue_create

**Purpose:** Create a new queue for organizing related jobs.

```raw
queue_create(
    name="feature-build",
    priority=5
)
```

**Use for:** Grouping related jobs, priority-based organization.

---

### queue_update

**Purpose:** Update queue properties (priority, etc.).

```raw
queue_update(queue_id="q123", priority=10)
```

---

## Error Handling

### job_retry

**Purpose:** Retry a failed job (for transient failures).

```raw
job_retry(job_id="abc123")
```

**After retry:**
- New job ID may be assigned (track the new one)
- Continue watching the new job

**Rule:** Max 3 retries. After 3 failures, treat as persistent.

---

### dlq_list

**Purpose:** List jobs in the Dead Letter Queue.

```raw
dlq_list()
```

**Use for:** Finding failed jobs that need special handling.

---

### dlq_replay

**Purpose:** Replay a job from the dead letter queue.

```raw
dlq_replay(job_id="abc123")
```

**Use for:** Attempting recovery of dead-lettered jobs.

---

## Job Lifecycle Management

### job_cancel

**Purpose:** Cancel a pending or processing job.

```raw
job_cancel(job_id="abc123")
```

**Use for:** Stopping unwanted, superseded, or accidentally queued jobs.

**Note:** Only works on jobs that are `pending` or `running`. Terminal jobs cannot be cancelled.

---

### job_delete

**Purpose:** Soft delete a job. Use for removing jobs that are no longer needed.

```raw
job_delete(job_id="abc123")
```

**Use for:** Cleaning up completed or unwanted jobs from listings.

**Note:** Jobs are soft-deleted and can be restored using `job_restore`.

---

### job_restore

**Purpose:** Restore a soft-deleted job.

```raw
job_restore(job_id="abc123")
```

**Use for:** Recovering accidentally deleted jobs.

**Note:** Only works on jobs that have been soft-deleted.

---

## Continuing Jobs

### job_continue

**Purpose:** Send a new message to the instance from a completed job, creating a new MESSAGE job.

```raw
job_continue(
    old_job_id="job_abc123",      # ID of a completed/terminal job
    message="Now add unit tests"  # New instruction for the instance
)
```

**Returns:** `{ old_job_id, instance_id, message_id, new_job_id, status }`

**Use for:** Following up on a completed job by sending additional instructions to the same agent instance. The instance retains its conversation context from the original job.

**Note:** The old job must be in a terminal state (completed, failed, cancelled, dead_letter). The target instance must not be terminated, errored, or paused.

**Important:** Use `watch_job(new_job_id)` to monitor the new job. Combine `job_continue` + `watch_job` in an atomic flow.

---

## Common Patterns

### Atomic Create + Watch
```raw
# PREFERRED - single call
result = job_create(agent_id="coder", task="Fix bug", watch=True)
job_id = result["job_id"]

# VS separate calls (avoid unless necessary)
job_id = job_create(agent_id="coder", task="Fix bug")["job_id"]
watch_job(job_id)  # Must call immediately!
```

### Parallel Dispatch Pattern
```raw
1. job_ids = []
2. for task in tasks:
     result = job_create(agent_id=..., task=..., watch=True)
     job_ids.append(result["job_id"])
3. watch_jobs(job_ids)
4. list_watched_jobs()  # Verify
```

### Sequential Pipeline Pattern
```raw
1. job_id = job_create(agent_id="coder", task="Step 1", watch=True)
2. Wait for [JOB_EVENT] with status=COMPLETED
3. Extract context from result
4. job_id = job_create(agent_id="reviewer", task="Step 2", watch=True)
5. Wait for [JOB_EVENT] with status=COMPLETED
6. Continue...
```

### Retry Loop Pattern
```raw
1. job_id = job_create(agent_id=..., task=..., watch=True)
2. Wait for [JOB_EVENT]
3. If status=FAILED:
     retry_count += 1
     if retry_count < 3:
       job_retry(job_id)
       watch_job(new_job_id)
       → goto step 2
     else:
       → Report persistent failure
4. If status=COMPLETED:
     → Record result, continue or report
```

---

## Gotchas

### Watch Must Be Registered BEFORE or WITH Dispatch

```raw
# WRONG - race condition
job_create(agent_id="coder", task="...")  # No watch
# Job might complete before watch_job() is called
watch_job(job_id)

# RIGHT - atomic or immediate
job_create(agent_id="coder", task="...", watch=True)
# OR
job_id = job_create(agent_id="coder", task="...")["job_id"]
watch_job(job_id)  # Called IMMEDIATELY
```

### Job IDs May Change on Retry

When you call `job_retry()`, the new attempt may have a different `job_id`.

**Always track the new job_id returned by `job_retry()`.**

### Terminal States Are Final

A job in terminal state (completed, failed, cancelled, terminated, dead_letter) cannot change.

If you receive a notification for a job already in terminal state, it should be your first and only notification for that job.

### Watching Already-Terminal Job

If you call `watch_job()` on an already-terminal job, you receive an **immediate notification** with the current status.

This is expected. Handle it like any other notification.

### Orphan Jobs

An orphan job is one that completed but no one was watching.

**Always use `watch=True`** or call `watch_job()` immediately after creation to prevent orphans.

### list_watched_jobs() for Verification

After dispatching multiple jobs, always verify:

```raw
job_ids = [job_id_1, job_id_2, job_id_3]
watch_jobs(job_ids)

watched = list_watched_jobs()
assert all(jid in watched for jid in job_ids), "Missing watches!"
```

---

## In-Progress Notifications

When the root agent finishes its turn but child agents it spawned are still
running, the watcher system emits a non-terminal `in_progress` notification
instead of marking the job complete.

**Notification body:**
```
[JOB_EVENT] Job b5536c60... in progress ⟳
  Agent: leader
  Progress: (last assistant message from root instance)
  Waiting for: N child agent(s)
```

**Key points:**

- `in_progress` is **not terminal**. The job is still in `running` state.
- The watch is **preserved** across this notification — you will still receive
  the final terminal event (`completed ✓` / `failed ✗`).
- Each job produces exactly **one** terminal event. Do not count `in_progress`
  toward job completion.
- `in_progress` is included in the **default** watch event set, so any watch
  created via `job_create(watch=True)` / `watch_job()` without an explicit
  `watch_events` filter will receive it.
- If a watch was created with an explicit `watch_events` list that omits
  `in_progress`, this notification is filtered out (you'll only see the
  terminal event). This is the only safe way to opt out.

**Action on `in_progress`:**
- Update internal tracking (record `Waiting for: N` and the `Progress:` text)
- Do NOT aggregate, report to parent, or trigger dependent work
- Continue waiting for the terminal event

**Do not:**
- ❌ Mark the job as done on `in_progress`
- ❌ Start follow-up / aggregation jobs based on `in_progress`
- ❌ Call `job_cancel` on the children to "speed things up" — they are tracked
  by the root instance and will report back when ready
- ❌ Report to parent that "the job is complete" on `in_progress`

