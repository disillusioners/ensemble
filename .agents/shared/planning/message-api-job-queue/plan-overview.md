# Plan Overview: Route HTTP API Messages Through JobQueue

## Objective

Route `POST /instances/{id}/messages` through the existing JobQueue system instead of WorkerPool, enabling project-scoped parallel queuing and unified job lifecycle management for HTTP-originated messages.

## Scope Assessment

**SMALL-to-MEDIUM** — Focused, additive change. We add a `MESSAGE` job type to the existing JobQueue, create a handler in JobProcessor, and wire the HTTP API to enqueue via JobQueueService. WorkerPool stays for everything else.

## Context

- Project: agents-ensemble
- Working Directory: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- This is a REDUCED plan — WorkerPool is NOT being replaced. Only HTTP API messages gain a new path.

## What's OUT OF SCOPE

- Child completion reports (`child_reports.py`) — stays on WorkerPool
- Agent-to-agent `send_message` — stays on WorkerPool
- Source handlers (Telegram, etc.) — stay on WorkerPool
- Internal sync `send_message()` — stays as direct graph call
- `_process_message_with_tracking()` — reused as-is, NOT modified
- `Task` model — stays for WorkerPool path
- `WorkerPool` — stays for everything except HTTP API messages

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Model & Infra | Add `job_type` to JobItem, add repository query methods, ensure `system_parallel_queue` exists, add `job_type` param to `enqueue()`, add `requeue` state transition | None | — | 1-2h |
| 2 | Integration & Wiring | Add `MessageJobHandler`, wire HTTP API → JobQueue, fix cancellation/orphan/status/termination | Phase 1 | tight | 2-3h |

### Coupling Assessment

| Pair | Coupling | Rationale |
|------|----------|-----------|
| Phase 1 → Phase 2 | **tight** | Phase 2 imports `job_type` field, new repository queries, and `enqueue(job_type=...)` from Phase 1. Same files extended. |

**Scheduling**: Sequential. Phase 2 cannot start until Phase 1 is complete.

## Architectural Decisions

### AD1: MESSAGE jobs target EXISTING instances
Unlike TASK jobs (which `spawn_instance_with_mcp()` to create a new instance), MESSAGE jobs route to an already-running instance. This means:
- No `spawn_instance_with_mcp()` in MessageJobHandler
- Orphan recovery must NOT re-spawn for MESSAGE jobs — fail them instead
- Cancellation uses `CancellationToken`, not instance termination
- `instance_id` is known at enqueue time and stored in `JobItem.instance_id` column (see AD3)

### AD2: DB-level concurrency gate (NOT in-memory)
In-memory `asyncio.Lock` is lost on crash/restart and doesn't work across multiple uvicorn workers. Instead:
- **Before `start_job()`** in `_process_next_job()`: query `find_processing_message_jobs_by_instance()` — if any PROCESSING MESSAGE job exists for this instance, skip (continue to next queue). Job stays PENDING, picked up next poll cycle.
- **Safety net** in `MessageJobHandler`: secondary check after `start_job()` for race conditions; if instance is busy, back-transition to PENDING and release lock.
- This is crash-safe, multi-process safe, and requires no new state

### AD3: Use existing `instance_id` column on JobItem (REVERSED from earlier plan)
`JobItem` already has `instance_id: str | None = Field(default=None)` at `models.py:140`. The repository already has `get_by_instance()`. Using the column directly instead of `job_metadata` gives us:
- Indexed queries (efficient WHERE clauses, no Python-side JSON filtering)
- No dual storage / divergence risk
- Reuse of existing `get_by_instance()` for termination cleanup

Implementation: Add `instance_id` parameter to `enqueue()` and `create()` for MESSAGE jobs (set at enqueue time). Remove all `job_metadata["instance_id"]` references.

### AD7: Override `start_job()` instance_id for MESSAGE jobs
`start_job()` at line 891 generates `instance_id = str(uuid.uuid4())` for every job. For MESSAGE jobs targeting existing instances, this is wrong — it would set `JobItem.instance_id` to a random UUID, breaking lock tracking, orphan recovery, and termination cleanup.

Implementation: In `start_job()`, when `job.job_type == "message"`, extract `instance_id` from `job.instance_id` (set at enqueue time) instead of generating a new UUID.

### AD4: Route to `system_parallel_queue` explicitly
- `project_id` present → resolve that project's `system_parallel_queue` via `_queue_repo.get_by_name(project_id, "system_parallel_queue")`
- `project_id` is None → route to the system-default project's `system_parallel_queue`
- NEVER default to `system_fifo_queue` for MESSAGE jobs
- Add `job_type` guard in `enqueue()`: when `job_type == "message"`, skip `system_fifo_queue` resolution, resolve `system_parallel_queue` instead

### AD5: `cancel_message_job()` handles PENDING and PROCESSING differently
- **PENDING**: Repository `cancel_job()` — direct PENDING→CANCELLED state transition (avoids `complete_job()` which only handles PROCESSING→terminal)
- **PROCESSING**: Signal CancellationToken via `MessageJobHandler._active_tokens`, then the handler naturally completes the job
- Lives on `JobQueueService` (not `InstanceLifecycleService`), delegates PROCESSING cancellation to `MessageJobHandler`

### AD6: Orphan recovery: FAIL MESSAGE jobs, don't re-spawn
Both orphan recovery branches (lines 191-219 and 220-243) must check `job.job_type`. If `"message"`, call `complete_job(DemandState.FAILED, error="Instance gone, message job orphaned")` instead of spawning.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| SQLite contention from dual-path writes | Medium | Follow existing transaction patterns; same DB |
| Orphan recovery re-spawns MESSAGE instances | High | Explicit `job_type` guard in BOTH branches with line refs |
| `cancel_job()` terminates instance for MESSAGE jobs | High | Separate `cancel_message_job()` using CancellationToken |
| DB concurrency check race (two MESSAGE jobs for same instance) | Medium | Check happens BEFORE `start_job()` — if both pass, second fails `start_job_atomic()` lock |
| Regression in WorkerPool path | Medium | WorkerPool path completely untouched; integration tests for both |
| `system_parallel_queue` doesn't exist for a project | Medium | `auto_provision_system_queues()` creates it at startup; guard in enqueue with clear error |

## Success Criteria

- [ ] `POST /instances/{id}/messages` enqueues via JobQueueService, not WorkerPool
- [ ] MESSAGE jobs process through `_process_message_with_tracking()` without modification
- [ ] Only one MESSAGE processes per instance at a time (DB-level guarantee)
- [ ] `cancel_message_job()` on MESSAGE jobs uses CancellationToken for PROCESSING, state transition for PENDING
- [ ] Orphan recovery FAILS MESSAGE jobs (both branches), never re-spawns
- [ ] GET status endpoint works for JobQueue-originated messages
- [ ] Instance termination cancels ALL MESSAGE jobs (PENDING + PROCESSING)
- [ ] WorkerPool continues handling child_reports, agent-to-agent, source handlers
- [ ] SSE streaming works identically to current behavior
- [ ] Same HTTP API contract (same endpoints, request/response format)

## Tracking

- Created: 2026-05-23
- Last Updated: 2026-05-23
- Status: revised (v2)
