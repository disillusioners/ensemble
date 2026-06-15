# Plan: `job_continue` Tool for Jober

## Objective

Add a new jober tool `job_continue(old_job_id, message)` that takes a completed job's ID and a new user message, looks up the `instance_id` from the old job, sends the message to that instance via the existing `enqueue_message_via_jq()` mechanism (same path as the frontend "send message" flow), and returns the new `job_id` so jober can `watch_job()` on it.

## Scope Assessment

**SMALL** — One new function in one existing file (`daemon/tools/job_queue.py`), a 2-line dataclass field addition, a 1-line return modification, a 1-line factory signature change, a 1-line wrapper update, a 1-line return-list append, and doc updates to 3 jober markdown files. No new files, no new modules, no DB changes. Follows established patterns exactly.

**Justification:** The tool reuses existing infrastructure (`enqueue_message_via_jq`, `job_service.get_job`, `MessageJobHandler`). All it does is: validate old job → check instance status → call one existing method → return the new `job_id`.

---

## Context

- **Project:** agents-ensemble
- **Working Directory:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Date:** 2026-06-15

### Key Architectural Findings

| Finding | Detail |
|---------|--------|
| **Tool factory pattern** | `create_job_tools(job_service, queue_mgmt_service, dead_letter_service, current_instance_id, agent_id, watcher_repo)` — closure-based DI, returns a list of tool functions. NO `manager` currently passed. |
| **`enqueue_message_via_jq` is NOT exposed to agents** | It's an HTTP API code path only, owned by `InstanceMessagingService` (wrapped by `InstanceManager` at `daemon/manager.py:1841`). |
| **`create_job_tools()` single caller** | `create_job_tools_if_available(manager, ...)` wrapper in `daemon/tools/instance.py:392` — which HAS `manager` in scope but does NOT forward it. |
| **`job_service.get_job(job_id)`** exists | Returns `JobItem \| None`. `JobItem` has `.instance_id` and `.status` fields. |
| **`enqueue_message_via_jq()`** at `daemon/services/instance_messaging.py:1470-1499` | Creates `JobItem` via `job_service.enqueue(...)`, logs `job.job_id` at line 1490, but **discards it** in the `AsyncMessageResult` return at line 1495. |
| **`AsyncMessageResult` dataclass** at `daemon/manager.py:430-435` | Has `message_id`, `instance_id`, `status`. Need to add `job_id: str \| None = None`. |
| **Valid terminal JOB states** | From `ALL_TERMINAL_STATES` at `daemon/repositories/job_queue/watcher_models.py:12`: `["completed", "failed", "cancelled", "dead_letter"]`. **Note:** `TERMINATED` is an `InstanceStatus` enum value, NOT a `JobStatus` — do not include. |
| **`InstanceStatus` enum values** | At `daemon/repositories/instance/models.py:19-29`: `idle`, `running`, `paused`, `completed`, `error`, `terminated`, `queued`, `waiting_children`, `failed`. |
| **`manager._instance_repository.get(instance_id)`** at `daemon/manager.py:2172` | Returns `Instance` row with `.status` attribute. |
| **Jober already has `send_message` tool** | Uses WorkerPool path (`manager.enqueue_message()`), NOT JobQueue path. `job_continue` is a different use case: continue a specific instance from a job, not send an arbitrary message. |

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Add `job_id` field to `AsyncMessageResult` dataclass** | Add `job_id: str \| None = None` to the dataclass at `daemon/manager.py:430-435`. | `daemon/manager.py:430-435` |
| 2 | **Populate `job_id` in `enqueue_message_via_jq` return** | Add `job_id=job.job_id` to the `AsyncMessageResult(...)` constructor at `daemon/services/instance_messaging.py:1495-1499`. The `job` variable is already in scope from line 1470. | `daemon/services/instance_messaging.py:1495` |
| 3 | **Add `manager` param to `create_job_tools()` factory** | Add `manager: "InstanceManager \| None" = None` as new optional parameter. Store it in closure. This is the `InstanceManager` that owns `enqueue_message_via_jq()` and `_instance_repository`. | `daemon/tools/job_queue.py:203-210` |
| 4 | **Update wrapper `create_job_tools_if_available()`** | Pass `manager` through to `create_job_tools()`. The wrapper at `daemon/tools/instance.py:392` already receives `manager` — just forward it. | `daemon/tools/instance.py:392-412` |
| 5 | **Add `JobContinueInput` schema** | Pydantic `BaseModel` with `old_job_id: str` and `message: str` fields. Follow `JobCreateInput` pattern. | `daemon/tools/job_queue.py` |
| 6 | **Implement `job_continue` tool function** | Inside the factory closure (so it has access to `job_service`, `manager`, `caller_agent_id`). See implementation spec below — includes instance status pre-check (W1). | `daemon/tools/job_queue.py` |
| 7 | **Register tool in factory return list** | Append `job_continue` to the list returned by `create_job_tools()`. | `daemon/tools/job_queue.py` (return list) |
| 8 | **Add `_FULL_DOCS["job_continue"]`** | Add the full documentation string to the `_FULL_DOCS` dict. Follow existing doc format. | `daemon/tools/job_queue.py` (`_FULL_DOCS` dict) |
| 9 | **Update jober docs** | Add `job_continue` to `tools_note.md` (new H3 under a new H2 category), add workflow usage to `workflow.md`, add rules to `rule.md`. | `agents/jober/tools_note.md`, `agents/jober/workflow.md`, `agents/jober/rule.md` |
| 10 | **Verify tool auto-registration** | Confirm `@register_tool_category("job")` + `@tool` decorators are applied; confirm `job_continue._full_doc_` is set. The tool will auto-register since jober's `meta.json` already allows category `"job"`. | `daemon/tools/job_queue.py` |

---

## Implementation Spec

### Change 1: `daemon/manager.py:430-435` — Add `job_id` field

```python
@dataclass
class AsyncMessageResult:
    """Result of async message enqueue."""
    message_id: str
    instance_id: str
    status: str = "queued"
    job_id: str | None = None  # NEW: job_id of the enqueued MESSAGE job (None for non-JQ paths)
```

### Change 2: `daemon/services/instance_messaging.py:1495-1499` — Populate `job_id`

```python
return AsyncMessageResult(
    message_id=message_id,
    instance_id=instance_id,
    status="queued",
    job_id=job.job_id,  # NEW: propagate job_id for callers that need it
)
```

### Change 3: `daemon/tools/job_queue.py` — New tool

```python
class JobContinueInput(BaseModel):
    """Input schema for job_continue tool."""
    old_job_id: Annotated[str, Field(description="Job ID of a terminal job to continue from")]
    message: Annotated[str, Field(description="New message/instruction to send to the instance")]


@register_tool_category("job")
@tool(args_schema=JobContinueInput)
async def job_continue(
    old_job_id: Annotated[str, Field(description="Job ID of a terminal job to continue from")],
    message: Annotated[str, Field(description="New message/instruction to send to the instance")],
) -> dict:
    """Continue a completed job by sending a new message to its instance.
    Use tool_help("job_continue") for details."""
    try:
        # 1. Look up the old job
        old_job = await job_service.get_job(old_job_id)
        if old_job is None:
            return {"error": f"Job {old_job_id} not found"}

        # 2. Validate the job is in a terminal state
        #    Valid terminal job states: completed, failed, cancelled, dead_letter
        #    (from ALL_TERMINAL_STATES in daemon/repositories/job_queue/watcher_models.py:12)
        if old_job.status not in TERMINAL_STATES:
            return {"error": f"Job {old_job_id} is not in a terminal state (current: {old_job.status}). "
                    "Only completed/failed/cancelled/dead_letter jobs can be continued."}

        # 3. Extract instance_id
        if not old_job.instance_id:
            return {"error": f"Job {old_job_id} has no associated instance_id"}

        instance_id = old_job.instance_id

        # 4. Check manager is available
        if manager is None:
            return {"error": "Instance manager not available — job_continue requires manager access"}

        # 5. Pre-check instance status before enqueueing
        #    enqueue_message_via_jq silently enqueues for terminated/error instances
        instance_meta = manager._instance_repository.get(instance_id)
        if instance_meta is None:
            return {"error": f"Instance {instance_id} not found"}
        if instance_meta.status in ("terminated", "error"):
            return {"error": f"Instance is {instance_meta.status} — spawn a new instance instead"}
        if instance_meta.status == "paused":
            return {"error": "Instance is paused — unpause it first"}

        # 6. Send message via the JobQueue path (same as FE "send message")
        result = await manager.enqueue_message_via_jq(
            instance_id=instance_id,
            message=message,
            source=f"agent:{caller_agent_id}" if caller_agent_id else "api",
        )

        # 7. Return new job_id (now provided by AsyncMessageResult)
        return {
            "old_job_id": old_job_id,
            "instance_id": instance_id,
            "message_id": result.message_id,
            "new_job_id": result.job_id,
            "status": result.status,
        }

    except Exception as e:
        return {"error": f"Failed to continue job: {str(e)}"}

job_continue._full_doc_ = _FULL_DOCS["job_continue"]
```

### Change 4: `daemon/tools/instance.py:392-412` — Forward `manager` in wrapper

Add `manager=manager` to the `create_job_tools(...)` call inside `create_job_tools_if_available()`.

### Change 5: Return list — Append `job_continue`

In `create_job_tools()`, add `job_continue` to the returned list.

---

## Validation & Error Handling

| Error Case | Handling | Return |
|------------|----------|--------|
| `old_job_id` not found | `job_service.get_job()` returns `None` | `{"error": "Job {id} not found"}` |
| Job not in terminal state | Check `status not in TERMINAL_STATES` (where `TERMINAL_STATES = {"completed", "failed", "cancelled", "dead_letter"}`) | `{"error": "Job is not terminal (current: X)"}` |
| Job has no `instance_id` | Check `old_job.instance_id` is truthy | `{"error": "Job has no associated instance_id"}` |
| Manager not available | Check `manager is None` | `{"error": "Instance manager not available"}` |
| Instance not found | `_instance_repository.get()` returns `None` | `{"error": "Instance {id} not found"}` |
| Instance terminated / error | Pre-check status | `{"error": "Instance is {status} — spawn a new instance instead"}` |
| Instance paused | Pre-check status | `{"error": "Instance is paused — unpause it first"}` |
| Enqueue raises | Caught by `except Exception` | `{"error": "Failed to continue job: {e}"}` |
| Enqueue succeeds | Return success dict with `new_job_id` from `AsyncMessageResult.job_id` | `{"old_job_id", "instance_id", "message_id", "new_job_id", "status"}` |

### Terminal States (W2 fix)

`TERMINAL_STATES` (already imported in `daemon/tools/job_queue.py` from `ALL_TERMINAL_STATES`) is:

```python
{"completed", "failed", "cancelled", "dead_letter"}
```

**Do NOT include `"terminated"`** — that is an `InstanceStatus` enum value, not a `JobStatus`.

---

## Constraints

- ✅ **Reuse existing `enqueue_message_via_jq()`** — no parallel messaging path
- ✅ **Must work with both SQLite and PostgreSQL** — uses repository pattern (`job_service` / `manager` abstracts DB); no raw SQL
- ✅ **Follow existing jober tool patterns** — `@register_tool_category("job")` + `@tool(args_schema=...)` + `_full_doc_` + closure capture
- ✅ **Tool auto-registers** — jober's `meta.json` already allows `"job"` category, so `job_continue` will be available without config changes
- ✅ **Backward-compatible factory change** — new `manager` param defaults to `None`; existing `create_job_tools()` callers (only 1) are updated
- ✅ **Backward-compatible dataclass change** — new `job_id` field defaults to `None`; existing `AsyncMessageResult` constructors that don't pass `job_id` still work

---

## Documentation Updates

### `agents/jober/tools_note.md`
- Add new H2 `## Continuing Jobs` section before `## Common Patterns`
- Add `### job_continue` H3 block with the standard format

**Draft content:**
```markdown
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
```

### `agents/jober/workflow.md`
- Add `## Continuing Completed Jobs` section (after `Phase 5: Report & Cleanup` or under `## Common Workflow Variations`)
- Show the pattern: extract `new_job_id` → `watch_job(new_job_id)` for monitoring

**Draft content:**
```markdown
## Continuing Completed Jobs

```raw
1. job was completed and you need to send follow-up work
2. call job_continue(old_job_id=<id>, message=<new instructions>)
3. extract new_job_id from result
4. watch_job(new_job_id=True) to monitor the continuation
5. when [JOB_EVENT] fires, react per Phase 4 framework
```
```

### `agents/jober/rule.md`
- Add a new rule to `## Must` section about using `job_continue` vs `job_create` for follow-up work

**Draft content:**
```markdown
### Use `job_continue` for Follow-up Work

When an instance needs additional work after completing a job, use `job_continue(old_job_id, message)` to send a new message to the same instance — preserving its conversation context.

- **`job_continue`**: Same instance, same context, new job. Use for iterative work on the same task.
- **`job_create`**: New instance, new context, new job. Use for independent tasks or when the previous instance was terminated/errored.
```

---

## Success Criteria

- [ ] `AsyncMessageResult.job_id` field added (backward-compatible, default `None`)
- [ ] `enqueue_message_via_jq` returns the new `job_id` in `AsyncMessageResult`
- [ ] `job_continue` tool is registered and available to the jober agent
- [ ] Calling `job_continue(old_job_id, message)` with a valid terminal job returns the new `job_id`
- [ ] Invalid `old_job_id` returns clear error
- [ ] Non-terminal job returns clear error
- [ ] Terminated/errored/paused instance returns clear error before enqueue
- [ ] Missing instance returns clear error
- [ ] New message is processed via the existing `MessageJobHandler` (same as FE)
- [ ] Jober docs updated in all 3 files
- [ ] Works without breaking existing `job_create` / `watch_job` / `job_get` / `send_message` tools
- [ ] `create_job_tools_if_available` still works (backward-compatible)

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `AsyncMessageResult` field addition breaks external callers | Very Low | New field defaults to `None`; existing constructors (that pass kwargs by name) still work |
| Instance status check uses string literals instead of enum | Low | Match existing codebase style; can refactor to `InstanceStatus` enum later |
| `manager._instance_repository.get()` is sync (no `await`) | Very Low | This matches the existing call at `daemon/manager.py:2172` — verify and follow the same pattern |
| Factory signature change breaks the single caller | Very Low | Only 1 caller (`create_job_tools_if_available`); param is optional with default `None` |

---

## Tracking

- **Created:** 2026-06-15
- **Last Updated:** 2026-06-15 (revision 2)
- **Status:** draft
- **Revision notes:** Switched from Option A (query after enqueue) to Option C (extend `AsyncMessageResult.job_id`) per reviewer feedback. Added instance status pre-check (W1). Fixed terminal states documentation — removed `TERMINATED` as a job state (W2).
