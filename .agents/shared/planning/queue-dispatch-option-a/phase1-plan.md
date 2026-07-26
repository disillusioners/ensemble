# Phase 1: Foundation — Enable `enqueue_job` for Messages

## Objective

Remove the structural barriers that prevent message JobItems from entering the standard `enqueue_job` → `start_job` path. Specifically: remove the D13 guard, generalize queue resolution, and preserve existing `instance_id` through `start_job` / `_try_start_job` so messages target the correct (existing) instance.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `job_queue_service.py` (Phase 3 also touches enqueue routing)
- **Shared APIs/interfaces**: `JobQueueService.enqueue()` signature (Phase 3 switches the producer to it)
- **Why this coupling**: Phase 1 makes `enqueue()` callable for messages; Phase 3 actually switches the producer to call it. They touch the same method but Phase 1 unblocks the path.

## Context

- **Previous phase completed**: N/A (root)
- **Key decisions**:
  - Messages use the `queue_id` selected by the caller (HTTP `message.queue_id`, scheduler, tool). If none provided, fall back to `system_parallel_queue` (NOT task-only FIFO — see queue resolution change).
  - `instance_id` preservation: messages to existing instances MUST keep that `instance_id`. Only jobs representing new-instance work mint a fresh UUID.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Remove D13 guard in `enqueue()` | Delete lines 599-607 (the `if job_type == "message": raise ValueError(...)` block). Update docstring at 572-596 to remove D13 rejection language. | `daemon/services/job_queue_service.py:572-607` |
| 2 | Generalize queue resolution for messages | Lines 631-634 assume "only TASK jobs reach this point" and force FIFO system queue. Change: respect the passed `queue_id` for ALL job types; for messages without a `queue_id`, fall back to `system_parallel_queue` (preserving current message parallelism behavior as default). | `daemon/services/job_queue_service.py:631-634` |
| 3 | Preserve `instance_id` in `start_job` | Lines 2741-2748 unconditionally mint `instance_id = str(uuid.uuid4())`. Restore message-specific logic: if `job.job_type == "message"` AND `job.instance_id` is already set (targeted at an existing instance), preserve it. Only mint fresh UUID for task-type jobs or messages without a pre-set instance_id. Update the comment at 2741-2747. | `daemon/services/job_queue_service.py:2720-2754` |
| 4 | Preserve `instance_id` in `_try_start_job` | Line 2192 has the same fresh-UUID mint (parallel to `start_job`). Apply the same preservation logic as Task 3 for consistency. | `daemon/services/job_queue_service.py:2180-2221` |
| 5 | Verify `instance_id` flows through to `spawn_instance_with_mcp` | `start_job` returns the JobItem with `instance_id` set. JobProcessor at `job_processor.py:1019` passes `started_job.instance_id` to `spawn_instance_with_mcp`. Confirm the preserved instance_id reaches there. | `daemon/services/job_processor.py:1019` (read-only verify) |

## Key Files

- `daemon/services/job_queue_service.py` — `enqueue()` (547), `start_job` (2643), `_try_start_job` (2164)
- `daemon/services/job_processor.py` — `_process_next_job` dispatch (1019) [verify only]

## Constraints

- **Do NOT remove the `job_type != "message"` filters in repository.py yet** — that is Phase 4. Removing them now (before Phase 3 rewrites the producer) would cause double-dispatch.
- **Do NOT touch `enqueue_message_job`** — that is Phase 3. This phase only makes `enqueue()` *callable*; the producer still uses the old path until Phase 3.
- The preserved `instance_id` must pass the UUID format validation in `spawn_instance` (line 1209) — existing instance_ids are already valid UUIDs, so no issue.

## Deliverables

- [ ] D13 guard removed; `enqueue(job_type='message')` succeeds and creates a QUEUED JobItem
- [ ] Queue resolution respects caller's `queue_id` for messages (falls back to `system_parallel_queue`)
- [ ] `start_job` preserves existing `instance_id` for message-type jobs
- [ ] `_try_start_job` preserves existing `instance_id` for message-type jobs
- [ ] Unit test: `enqueue(job_type='message', instance_id=<existing>)` → JobItem with that instance_id preserved through `start_job`

## Notes

- After this phase, `enqueue()` is callable for messages but NOTHING calls it for messages yet (the producer is still Phase 3). The system remains in its current working state — messages still flow through `enqueue_message_job` → `job_repo.create`. This phase is safe to land in isolation.
- The fresh-UUID mint at line 2748 has an explicit comment ("always mint a fresh UUID ... the legacy MESSAGE-specific branch ... was removed"). This comment must be updated to reflect the restored logic.
