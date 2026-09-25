# Tool Usage Notes

## Creating Jobs

### job_create

**Purpose:** Create a new job for execution by an agent.

**Always use `watch=True`** to ensure atomic creation and monitoring registration.

```raw
job_create(
    agent_id="developer",           # Target agent
    message="Fix the login bug",    # Task description
    watch=True,                 # CRITICAL: Watch immediately
    priority=5                  # Optional: 1-10, higher = more urgent
)
```

**Returns:** `{"job_id": "abc123", ...}`

**Important:** Record the returned `job_id` for tracking.

**Queue targeting (`queue_id`):** accepts a queue ID or a system-queue
alias, case-insensitive: `system_fifo_queue`/`fifo`,
`system_parallel_queue`/`parallel`, `system_background_queue`/`background`,
`system_defer_queue`/`defer`, `system_kb_fifo_queue`/`kb_fifo`. An alias
always resolves to the SYSTEM queue of that name — a user-created queue
with the same short name never shadows it. When `queue_id` is omitted, my
work lands on `system_parallel_queue` by default (other agents default to
FIFO). Unknown alias names return an error listing the valid queues.

---

### Creating Multiple Jobs (Parallel)

For independent jobs that can run simultaneously:

```raw
# Create all jobs first
job_create(agent_id="developer", message="Task A", watch=True)
→ record job_id_1
job_create(agent_id="reviewer", message="Task B", watch=True)
→ record job_id_2
job_create(agent_id="tester", message="Task C", watch=True)
→ record job_id_3

# Then watch each mission (the receipt you hold is a valid handle)
watch_mission(job_id_1)
watch_mission(job_id_2)
watch_mission(job_id_3)

# Verify
list_watched_jobs() → should show all 3 receipts
```

---

## Watching Missions (not receipts)

> **You wait on MISSIONS, not receipts.** Create work with `job_create` —
> you hold a receipt (`job_id`). To wait on the WORK: `await_mission(mission_id)`
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

### watch_mission

**Purpose:** THE durable watch — register to be revived when the MISSION
reaches terminal state (not the transport receipt).

```raw
watch_mission(job_id="abc123")        # the receipt from job_create
watch_mission(mission_id="inst_abc123")  # or the mission itself
```

**What it does:** resolves the mission, then registers ONE watcher row per
receipt that exists at call time (all Task receipts of the mission's
instance) with `mission_terminal` events. At mission-terminal you receive
`[JOB_EVENT]` per watched receipt — **the FIRST event is the signal; the
rest are echoes of the same mission. Act once.**

**Edge cases:**
- Already-terminal mission → registration + immediate notification.
- Mission not yet dispatched → watching the `job_create` receipt registers
  before the mission exists; the watch simply waits.
- After `job_continue` → call `watch_mission(new_job_id)` again — receipts
  minted after your watch are NOT auto-watched.
- A revived mission needs a FRESH `watch_mission` (the watch row is consumed
  at first terminal). The event carries no epoch — call `get_mission` for
  details.

**Use when:** You want to yield and be woken when the work is done.
For waiting in-turn, use `await_mission` (timeout returns a snapshot,
not an error).

---

### list_watched_jobs

**Purpose:** Verify which jobs are currently being watched.

```raw
list_watched_jobs()
```

**Returns:** List of job IDs you're watching, labeled by resolved handle
(`receipt of mission <id>` / `mission handle`).

**Use for:** Verification after dispatch, debugging tracking issues.

---

### unwatch_job

**Purpose:** Stop watching (rarely needed, watches auto-clean). The handle
is tolerant: pass a receipt job_id OR a mission_id — a mission handle
removes every watched receipt of that mission.

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

**Returns:** Full job details including status, result, error, timestamps, `mission_ref` cross-reference, and `outcome` (always `null` on transport payloads).

**Use for:** Checking status when notification is unclear, debugging.

> *`status` answers transport questions only (was my submission handled?).
> For outcome, use `get_mission` / `await_mission`.*

---

### job_list

**Purpose:** List jobs, optionally filtered by status, kind, project, or queue.

```raw
job_list(statuses=["running", "pending"], job_types=["task"])
```

**Statuses:** `pending`, `processing`, `paused`, `completed`, `settled`, `failed`,
`cancelled`, `dead_letter`. These are wire values only — they answer the
transport question ("was my submission handled?"), never the work
question ("is the work done?"). The per-kind split is the load-bearing
part: a task row's `status` IS the mission outcome (a task job is its
own mission), but a message row's `status` is just the receipt — for
that row, consult the mission tools (`get_mission` / `await_mission`)
to learn whether the work is done.

**Job types:** `task` (mission proxy) / `message` (mirror receipt). Default: both.

**Use for:** Finding jobs, auditing active work. Each returned row carries
`job_type` + `mission_ref` + `outcome: null` so the transport-vs-mission
distinction stays explicit.

---

## Mission Outcome

Mission tools answer the work-side question ("is the work done?"). They
are the inverse of the job tools' transport question ("was my submission
handled?"). Every terminal job payload carries a `mission_ref`
cross-reference with the linked mission's liveness — the canonical
mission vocabulary (`pending` / `processing` / `paused` / `completed` /
`failed` / `cancelled`).

> *`liveness` / `outcome` answer work questions only (is the work done?).
> For transport (was my submission handled?), use `job_get` / `job_list`.*

### get_mission

**Purpose:** One-shot snapshot of a mission's current state — never blocks.

```raw
get_mission(mission_id="inst_abc123")
```

**Returns:** A JSON snapshot — identity (`mission_id == instance_id`),
liveness, `terminal_reason` (W4-hazard aware — `dead_letter` is preserved
even after a since-revived instance), `epoch` / `epoch_count` /
`last_epoch_at`, `linked_jobs`, `started_at`, `last_activity_at`, and a
single-element `epochs` summary. The `outcome` field is non-null ONLY
when the mission is terminal; `null` when live.

**Use for:** Quick check before reporting, debugging "is the work done?",
inspecting the W4 dead-link path.

**Rule:** Never report mission completion based on `status` of a
mirror JobItem — always check the mission's `outcome` or `liveness`.

---

### await_mission

**Purpose:** Block (asyncio poll) until the mission reaches a terminal
state, or timeout.

```raw
await_mission(
    mission_id="inst_abc123",
    timeout=600,         # default 600s; on timeout returns current snapshot (no error)
    poll_interval=2,     # default 2s
)
```

**Returns:** The terminal snapshot when the mission reaches
`completed` / `failed` / `cancelled`, OR the current snapshot on
timeout. F7 semantics: terminal is revivable — if the mission revives
later, a fresh `await_mission` sees the new epoch (current
`mission_epoch` stays constant-1 until M4(ii)).

**Use for:** Replacing the watch-and-wait loop. Pair with
`job_create` for mission-shaped work: `job_create` →
`await_mission` → decide → report.

---

### list_missions

**Purpose:** List mission summaries with optional filters.

```raw
list_missions(agent_id="leader", liveness="processing", limit=50)
```

**Filters (all optional, AND-composed):** `agent_id`, `liveness`
(canonical mission vocabulary), `parent_mission_id`, `since`
(ISO-8601 lower bound on `last_activity_at`), `limit` (clamped to
[1, 200]; default 50).

**Returns:** A paged list of summaries (`mission_id`, `agent_id`,
`parent_mission_id`, `liveness`, `terminal_reason`, `epoch`,
`epoch_count`, `last_epoch_at`, `linked_jobs`, `started_at`,
`last_activity_at`, `outcome`) — the full `epochs` array is omitted
(use `get_mission` for that).

**Use for:** Subtree scoping on `parent_mission_id`, focusing on a
liveness cohort, scoping to a single agent, or auditing recent
activity.

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
queue_list(project_id="proj_123")
```

**Use for:** Understanding queue structure, organizing work.

---

### queue_create

**Purpose:** Create a new queue for organizing related jobs.

```raw
queue_create(
    project_id="proj_123",
    queue_name="feature-build",
    queue_type="parallel",        # or "fifo" (default)
    concurrency_limit=5
)
```

**Use for:** Grouping related jobs, parallel execution. Concurrency (not
priority) controls how many jobs run at once. System queue names
(`system_fifo_queue`, `system_parallel_queue`, `system_background_queue`,
`system_defer_queue`, `system_kb_fifo_queue`) are reserved and their short
aliases (`fifo`, `parallel`, `background`, `defer`, `kb_fifo`) always point
at the system queues, so pick a distinct name.

---

### queue_update

**Purpose:** Update queue properties (name, concurrency limit, paused state).

```raw
queue_update(
    queue_id="q123",
    project_id="proj_123",
    concurrency_limit=10,
    is_paused=False
)
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

**Important:** Use `watch_mission(new_job_id)` to monitor the follow-up work. Combine `job_continue` + `watch_mission` in an atomic flow — new receipts are not auto-watched.

---

## Common Patterns

### Atomic Create + Watch
```raw
# PREFERRED - single call
result = job_create(agent_id="developer", task="Fix bug", watch=True)
job_id = result["job_id"]

# VS separate calls (avoid unless necessary)
job_id = job_create(agent_id="developer", task="Fix bug")["job_id"]
watch_mission(job_id)  # Must call immediately!
```

### Parallel Dispatch Pattern
```raw
1. job_ids = []
2. for task in tasks:
     result = job_create(agent_id=..., task=..., watch=True)
     job_ids.append(result["job_id"])
3. watch_mission(job_id) for each — every receipt becomes a watched row
4. list_watched_jobs()  # Verify
```

### Sequential Pipeline Pattern
```raw
1. job_id = job_create(agent_id="developer", task="Step 1", watch=True)
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
       watch_mission(new_job_id)
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
job_create(agent_id="developer", task="...")  # No watch
# Job might complete before watch_mission() is called
watch_mission(job_id)

# RIGHT - atomic or immediate
job_create(agent_id="developer", task="...", watch=True)
# OR
job_id = job_create(agent_id="developer", task="...")["job_id"]
watch_mission(job_id)  # Called IMMEDIATELY
```

### Job IDs May Change on Retry

When you call `job_retry()`, the new attempt may have a different `job_id`.

**Always track the new job_id returned by `job_retry()`.**

### Terminal States Are Final

A job in terminal state (completed, failed, cancelled, terminated, dead_letter) cannot change.

If you receive a notification for a job already in terminal state, it should be your first and only notification for that job.

### Watching an Already-Terminal Mission

If you call `watch_mission()` on an already-terminal mission, you receive an **immediate notification** with the current status (one per watched receipt — the first is the signal, the rest are echoes).

This is expected. Handle it like any other notification — act once.

### Orphan Jobs

An orphan job is one that completed but no one was watching.

**Always use `watch=True`** or call `watch_mission()` immediately after creation to prevent orphans.

### list_watched_jobs() for Verification

After dispatching multiple jobs, always verify:

```raw
for job_id in [job_id_1, job_id_2, job_id_3]:
    watch_mission(job_id)

watched = list_watched_jobs()
assert all receipts present, "Missing watches!"
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
- `in_progress` is included in the **default** watch event set, so any transport
  watch created via `job_create(watch=True)` without an explicit
  `watch_events` filter will receive it. `watch_mission` rows carry
  `mission_terminal` events only — they fire on mission-terminal
  (`completed` / `failed` / `cancelled` liveness), so they do not act on
  `in_progress`. Known engine gap (do not design around it): a `dead_letter`
  disposition over non-terminal liveness never fires a `mission_terminal`
  row today — if a mission shows `dead_letter` while its `liveness` is still
  non-terminal, stop waiting on the watch and poll with `await_mission`
  (its timeout returns a snapshot) to decide.
- If a **transport** watch was created with an explicit `watch_events`
  list that omits `in_progress`, this notification is filtered out (you'll
  only see the terminal event). This is the only safe way to opt out.

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

