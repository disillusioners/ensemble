# Phase 3: Convert Fan-In Entry Points

## Objective
Switch all 6 fan-in entry points from the raw-message `enqueue_message()` path to the message-Job `enqueue_message_job()` path, behind the feature flag. Each entry point is converted independently and validated.

## Coupling
- **Depends on**: Phase 1 (bridge method exists), Phase 2 (serialization verified)
- **Coupling type**: loose (each entry point is in a different file; no shared edits)
- **Shared files with other phases**: none — entry points are leaf callers
- **Why this coupling**: Each entry point calls `enqueue_message_job()` from Phase 1; no inter-entry-point dependencies

## Context

### The 6 Entry Points (Current State — All Post-D13)

All 6 entry points currently call `manager.enqueue_message()` (or `InstanceMessagingService.enqueue_message()`) which creates only MessageQueue + Task — **no JobItem**.

| # | Entry Point | File:Line | Current Path | JobItem? | job_id Returned? | source Value |
|---|---|---|---|---|---|---|
| 1 | External-source chokepoint | `daemon/sources/registry.py:822` | `manager.enqueue_message()` | No | Discarded | `f"{source_id}:{external_user_id}"` |
| 2 | Scheduler (scheduled + project_id) | `daemon/sources/adapters/scheduler.py:717` | `JobQueueService.enqueue()` | **Yes** (TASK job) | Yes | `"scheduler"` |
| 2b | Scheduler (manual/immediate) | `scheduler.py:759` → `registry.py:822` | `manager.enqueue_message()` | No | No | `f"{source_id}:{source_id}"` |
| 3 | HTTP POST /messages (normal path) | `daemon/routers/messages.py:129` | `manager.enqueue_message()` | No | Discarded | `"api"` |
| 4 | Agent send_message tool | `daemon/tools/instance.py:703` | `manager.enqueue_message()` | No | Discarded | `f"internal_agent:{caller_id}"` |
| 5 | job_continue tool | `daemon/tools/job_queue.py:742` | `manager.enqueue_message()` | No | Yes (`new_job_id`) | `f"agent:{caller_id}"` |
| **6** | **PAUSED auto-resume cascade (child path)** | **`daemon/manager.py:3356`** | **`self.enqueue_message()`** | **No** | **No (`job_id=None`)** | **`"cascade_resume"`** |

### Entry Point #6 Detail — PAUSED Auto-Resume Cascade

When a user POSTs to `/messages` on a **PAUSED** instance (`routers/messages.py:73-101`), the route takes a special branch:
1. Calls `manager.resume_instance_cascade(instance_id)` to un-pause the instance and its children
2. For each resumed instance, calls `manager.resume_processing_job(resumed_id, message=...)`

Inside `resume_processing_job` (`manager.py:3340-3380`), the **child instance** branch enqueues a message:
```python
# manager.py:3356
result = await self.enqueue_message(
    instance_id=instance_id,
    message=message,
    source="cascade_resume",
    images=images,
    metadata={"resume_mode": True, "silent": silent},
)
```
This is a **public user-facing path** — the user's message propagates to child instances during cascade resume. It currently creates a Task-only with no JobItem. This violates the plan's success criterion: "Every public entry point creates a JobItem."

**Note**: The root/target instance in the cascade does NOT go through `enqueue_message` — it follows the "has PAUSED/RUNNING PROCESS_MESSAGE Task" path at `manager.py:3372+`, which fails the old task and lets the checkpoint resume. Only the **child** instances hit the `enqueue_message` call.

### Conversion Pattern

Each entry point gets a flag check:
```python
if manager._job_system_config.message_jobs_enabled:
    result = await manager.enqueue_message_job(
        instance_id=instance_id,
        message=message,
        source=source,
        ...
    )
else:
    result = await manager.enqueue_message(
        instance_id=instance_id,
        message=message,
        source=source,
        ...
    )
```

### Entry Point #2 (Scheduler) — Special Case
The scheduler's `_route_via_job_queue` path already creates a JobItem via `JobQueueService.enqueue()` — but it's a TASK-type job that goes through the poll loop (spawn → enqueue → stamp). This is slower than the inline message-Job path. Converting it to `enqueue_message_job()` means:
- The instance must already exist (or be created via `get_or_create_instance` first)
- The message-Job dispatches inline instead of waiting for poll loop
- This is a **latency improvement** for scheduled jobs

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add flag-check helper to manager | Add a thin `_enqueue_message_with_flag()` method on the manager that checks the flag and routes to `enqueue_message_job()` or `enqueue_message()`. Centralizes the flag check so entry points don't repeat it. | `daemon/manager.py` |
| 2 | Convert entry point #3: HTTP POST /messages | Change `routers/messages.py:129` to call the flag-checked enqueue. **Also propagate `job_id`** — currently the route discards it. Change `MessageResponse` to include `job_id` (or `work_id`). | `daemon/routers/messages.py:129`, `daemon/routers/messages.py` (MessageResponse model) |
| 3 | Convert entry point #1: External-source chokepoint | Change `registry.py:822` to call the flag-checked enqueue. This covers ALL external chat adapters (Slack, Telegram, Discord) in one conversion. | `daemon/sources/registry.py:822` |
| 4 | Convert entry point #4: Agent send_message tool | Change `tools/instance.py:703` to call the flag-checked enqueue. The tool already uses `result.message_id`; now it can also use `result.job_id` for watcher registration if needed. | `daemon/tools/instance.py:703` |
| 5 | Convert entry point #5: job_continue tool | Change `tools/job_queue.py:742` to call the flag-checked enqueue. The tool already surfaces `result.job_id` as `new_job_id` — this just ensures it's a real JobItem now. | `daemon/tools/job_queue.py:742` |
| 6 | Convert entry point #2: Scheduler | Change `scheduler.py` `_route_via_job_queue()` (line 717) AND `_execute_immediate()` (line 759) to use the flag-checked enqueue. The scheduled path no longer needs `JobQueueService.enqueue()` for messages — the message-Job creates the JobItem inline. | `daemon/sources/adapters/scheduler.py:704-776` |
| **7** | **Convert entry point #6: PAUSED auto-resume cascade (child path)** | **Change `manager.py:3356`** `self.enqueue_message(source="cascade_resume", ...)` to use the flag-checked `_enqueue_message_with_flag()`. The child-resume path must create a JobItem when the flag is ON. The `metadata={"resume_mode": True}` must be preserved on the JobItem. This is a public user-facing path (the user's message propagates to children during cascade resume). | `daemon/manager.py:3340-3380` |
| 8 | Write integration tests per entry point | For each entry point: test with flag ON (creates JobItem) and flag OFF (no JobItem). Verify the JobItem appears in `list_work` with flag ON. | `tests/test_entry_points_message_jobs.py` (new) |

## Key Files (per entry point)
1. `daemon/sources/registry.py:818-829` — `_handle_message` enqueue call
2. `daemon/sources/adapters/scheduler.py:704-776` — `_route_via_job_queue` and `_execute_immediate`
3. `daemon/routers/messages.py:73-145` — `send_message` route handler (both PAUSED and normal branches)
4. `daemon/tools/instance.py:702-708` — `send_message` tool
5. `daemon/tools/job_queue.py:741-755` — `job_continue` tool
6. **`daemon/manager.py:3340-3380` — `resume_processing_job` child-path enqueue call**

## Constraints
- Flag OFF must produce byte-identical behavior to current (regression-safe)
- Each entry point conversion is independently shippable
- The scheduler conversion is the most complex — it changes from a poll-loop-driven path to an inline path
- Do NOT convert internal message callers (reports, nudges, `[JOB_EVENT]` delivery, compaction) — those stay on raw `enqueue_message`
- **Entry point #6 (`invoke_and_wait`): explicitly OUT OF SCOPE** — `daemon/utils.py:575` calls `enqueue_message` with `source="internal_invoke_and_wait:{parent_id}"`. This is an internal synchronous invocation utility (spawns instance + waits for completion via completion registry). It is NOT user-facing work — it's infrastructure used by tools like `spawn_instance`. Classify as **internal-only**, not converted.

## Per-Entry-Point Testing Strategy

### Entry Point #3 (HTTP POST /messages) — Most Critical
```python
async def test_post_messages_creates_job_with_flag():
    """POST /messages with flag ON creates a JobItem."""
    # 1. Set ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED=true
    # 2. POST /instances/{id}/messages with content
    # 3. Assert response contains job_id
    # 4. Assert list_work() shows the JobItem
    # 5. Assert SSE /events streams the turn

async def test_post_messages_no_job_without_flag():
    """POST /messages with flag OFF behaves as before."""
    # 1. Set flag OFF (default)
    # 2. POST /instances/{id}/messages
    # 3. Assert response has message_id but job_id may be work_id (UUID)
    # 4. Assert list_work() shows a Task but no JobItem for this message
```

### Entry Point #1 (External Sources) — Chokepoint
```python
async def test_external_source_creates_job_with_flag():
    """Telegram/Slack/Discord messages create Jobs with flag ON."""
    # 1. Mock adapter delivers a message
    # 2. Assert registry._handle_message creates a JobItem
    # 3. Assert source is "telegram:123456" format
```

### Entry Point #6 (PAUSED Auto-Resume Cascade) — BLOCKING
```python
async def test_paused_cascade_resume_creates_job_with_flag():
    """POST /messages on a PAUSED instance creates JobItems for child cascade."""
    # 1. Create a parent instance with a PAUSED child
    # 2. Set ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED=true
    # 3. POST /instances/{parent_id}/messages (triggers PAUSED branch)
    # 4. Assert: resume_instance_cascade un-pauses parent + children
    # 5. Assert: child instances get a JobItem (source="cascade_resume")
    # 6. Assert: JobItem metadata contains {"resume_mode": True}
    # 7. Assert: list_work() shows the child JobItem

async def test_paused_cascade_resume_no_job_without_flag():
    """POST /messages on PAUSED instance with flag OFF behaves as before."""
    # 1. Same setup, flag OFF
    # 2. Assert: child enqueue creates Task-only (no JobItem)
    # 3. Assert: manager.resume_processing_job returns job_id=None
```

## Deliverables
- [ ] All 6 entry points converted with flag check (including PAUSED cascade-resume)
- [ ] `job_id` propagated in HTTP response
- [ ] Integration tests pass for each entry point (flag ON + OFF)
- [ ] **Entry point #6**: cascade-resume child path creates JobItem with `resume_mode` metadata
- [ ] Scheduler converted from poll-loop to inline dispatch
- [ ] Internal message paths untouched (reports, nudges, `[JOB_EVENT]`, compaction, `invoke_and_wait`)
