# Phase 3: Producer — Rewrite `enqueue_message_job` to Use `enqueue()`

## Objective

Switch the public message producer from the D13 mirror pattern (`job_repo.create` + eager `queued→active` flip + `worker_pool.notify_work`) to the authoritative standard path (`JobQueueService.enqueue()` → QUEUED JobItem → `dispatch_bus.notify_new_job()`). This is the core of the Option A reversal — the producer now creates the authoritative dispatch primitive, not a mirror.

## Coupling

- **Depends on**: Phase 1 (enqueue callable for messages) + Phase 2 (spawn can reuse instances)
- **Coupling type**: **tight**
- **Shared files with other phases**: `instance_messaging.py` (only this phase touches it for the rewrite)
- **Shared APIs/interfaces**: `enqueue_message_job()` signature (preserve it — 5 callers depend on it), `AsyncMessageResult` return type (may need adjustment — see Task 5)
- **Why this coupling**: The new producer must produce JobItems that Phase 2's JobProcessor branch can correctly dispatch. Landing this before Phase 4 (filter removal) ensures no double-dispatch window.

## Context

- **Previous phases completed**: Phase 1 (enqueue callable) + Phase 2 (spawn reuses instances)
- **Key decisions**:
  - **No inline Task creation**: The producer creates ONLY the JobItem (via `enqueue()`). The Task + MessageQueue rows are created by JobProcessor after admission (existing behavior at `job_processor.py:1034`).
  - **No eager activation**: The JobItem stays QUEUED. `start_job_atomic_with_lock` transitions it to ACTIVE when a slot is acquired.
  - **`message_id` availability**: Under the standard path, the MessageQueue row (and thus `message_id`) is created by JobProcessor AFTER admission. The producer cannot return `message_id` immediately. This is a **contract change** — see Task 5.
  - **Internal `enqueue_message` stays Task-only**: JobProcessor calls `enqueue_message()` (internal, no JobItem) to create the Task for a claimed job. This MUST remain Task-only to avoid recursion. Only the PUBLIC `enqueue_message_job` changes.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Rewrite `enqueue_message_job` to call `enqueue()` | Replace lines 1275-1677. New flow: (1) resolve `agent_id`, `agent_dir`, `project_id` (keep existing resolution logic). (2) Call `await self._job_queue_service.enqueue(agent_id=..., message=..., source=..., project_id=..., priority=..., queue_id=..., job_type="message", instance_id=instance_id)`. (3) Call `self._dispatch_bus.notify_new_job(project_id)` to wake JobProcessor. (4) Return result. Remove ALL of: `_prepare_enqueued_message` prelude, `job_repo.create`, eager `atomic_transition`, `stamp_message_id`, `worker_pool.notify_work`. | `daemon/services/instance_messaging.py:1275-1677` |
| 2 | Remove the D13-bypass comments | Lines 1296-1328, 1385-1389, 1585-1628, 1668-1682 contain extensive D13/mirror documentation. Remove or replace with Option-A documentation explaining the authoritative JobItem path. | `daemon/services/instance_messaging.py` (multiple comment blocks) |
| 3 | Inject `dispatch_bus` into InstanceMessagingService | The service needs access to `DispatchEventBus` to call `notify_new_job()`. Check if it's already available via `self._manager._dispatch_bus` or needs constructor injection. Wire it in `daemon/api.py` lifespan startup. | `daemon/services/instance_messaging.py`, `daemon/api.py` |
| 4 | Update manager facade wrapper | `manager.enqueue_message_job` (line 4301) is a thin wrapper. Verify it still forwards correctly. Update docstring at 4314-4319 to remove "mirror" language. | `daemon/manager.py:4301-4350` |
| 5 | **Handle `message_id` contract change** | Today `enqueue_message_job` returns `AsyncMessageResult` with `message_id` populated immediately. Under the standard path, `message_id` doesn't exist until JobProcessor creates the Task. Options: **(a)** Block the producer until admission (poll for ACTIVE state) — simplest but adds latency. **(b)** Return `job_id` immediately with `message_id=None`, let callers poll/SSE for it — most correct but breaks callers expecting immediate `message_id`. **(c)** Pre-generate `message_id` and pass it through — allows immediate return but requires the Task creation to use this pre-generated id. **Recommend (c)** for minimal caller breakage. | `daemon/services/instance_messaging.py` (return), all 5 callers |
| 6 | Verify internal `enqueue_message` is UNCHANGED | Confirm `enqueue_message` (line 1140, internal Task-only path) is NOT touched. This is critical — JobProcessor uses it to create the Task for a claimed job. Changing it would cause recursion. | `daemon/services/instance_messaging.py:1140-1153` (verify only) |

## Key Files

- `daemon/services/instance_messaging.py` — `enqueue_message_job` (1275), `enqueue_message` (1140, verify only)
- `daemon/manager.py` — `enqueue_message_job` facade (4301)
- `daemon/api.py` — service wiring (dispatch_bus injection)
- `daemon/services/dispatch_event_bus.py` — `notify_new_job` (42)

## The 5 Callers of `enqueue_message_job` (Audit for Contract Change)

| Caller | File:Line | Impact of `message_id` Contract Change |
|--------|-----------|----------------------------------------|
| HTTP POST /messages | `daemon/routers/messages.py:321` | HTTP response includes `message_id`. If using option (c), no change. If (b), API response shape changes. |
| External source registry | `daemon/sources/registry.py:827` | Uses result for correlation. Verify it reads `message_id` vs `job_id`. |
| Scheduler | `daemon/sources/adapters/scheduler.py:762` | Uses result. Verify correlation needs. |
| `job_continue` tool | `daemon/tools/job_queue.py:749` | Agent tool. Verify what it does with the result. |
| Manager facade | `daemon/manager.py:4340` | Thin wrapper. No direct impact beyond forwarding. |

## Constraints

- **CRITICAL — Recursion avoidance**: JobProcessor's `_process_next_job` calls `enqueue_message()` (internal, Task-only) at line 1034 to create the Task for a claimed job. This MUST remain the internal path. If it were changed to go through the queue, JobProcessor would re-dispatch its own jobs infinitely.
- **Do NOT remove the `job_type != "message"` filters yet** — that is Phase 4. At the end of Phase 3, the new producer creates QUEUED message JobItems via `enqueue()`, but the filters still exclude them from JobProcessor. The system is in a **transitional state** here: messages are queued but not yet dispatched through the poll loop. Phase 4 completes the circuit by removing the filters.
- **Feature flag consideration**: This phase is a good candidate for a feature flag (`config.MESSAGE_STANDARD_PATH = True/False`) so the old mirror path can be restored if issues arise. The flag would toggle between `job_repo.create` (old) and `enqueue()` (new).

## Deliverables

- [ ] `enqueue_message_job` creates a QUEUED JobItem via `enqueue()` — no inline Task, no eager activation
- [ ] `dispatch_bus.notify_new_job()` fires after enqueue (wakes JobProcessor)
- [ ] `worker_pool.notify_work()` is NO LONGER called from the message producer path
- [ ] `message_id` contract: documented decision (recommend pre-generation, option c)
- [ ] Internal `enqueue_message` is verified unchanged (no recursion risk)
- [ ] Unit test: `enqueue_message_job` → JobItem created via `enqueue()`, status QUEUED, correct `queue_id` and `instance_id`
- [ ] Unit test: `dispatch_bus.notify_new_job` called once after enqueue

## Notes

- This phase creates a **transitional state**: messages are queued correctly but not yet dispatched through JobProcessor (filters still exclude them). This is intentional — Phase 4 completes the circuit atomically.
- The rewritten `enqueue_message_job` will be significantly shorter (removes ~400 lines of mirror logic). Keep the resolution prelude (agent_id, project_id, queue_id resolution) — it's still needed.
- **MUST verify** that `enqueue()` sets `instance_id` on the JobItem row. From exploration: it forwards `instance_id` to both idempotent and non-idempotent paths (lines 676, 788). Confirm this works for `job_type='message'` after Phase 1's changes.
