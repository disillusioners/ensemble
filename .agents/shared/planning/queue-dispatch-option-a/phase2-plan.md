# Phase 2: Receptor — Load-Existing-Instance + JobProcessor Dispatch Branch

## Objective

Ensure the spawn path and JobProcessor correctly handle messages that target an **existing** instance — load and reuse it instead of creating a duplicate. This is the second of the three blocking traps from the original investigation (trap #3: `spawn_instance` always INSERTs).

## Coupling

- **Depends on**: Phase 1 (must preserve `instance_id` through `start_job`)
- **Coupling type**: **tight**
- **Shared files with other phases**: `instance_lifecycle.py` (Phase 4 touches cleanup at 1790), `job_processor.py` (Phase 3 producer affects what JobProcessor receives)
- **Shared APIs/interfaces**: `spawn_instance_with_mcp` signature (unchanged, but behavior changes), `_spawn_instance_db_sync` internal contract
- **Why this coupling**: Phase 1 preserves the `instance_id`; Phase 2 ensures spawn doesn't discard it. They must land together for correct message routing.

## Context

- **Previous phase completed**: Phase 1 — `enqueue()` callable for messages, `instance_id` preserved through `start_job`
- **Key decisions**:
  - **Load-existing branch**: SELECT the Instance row by `instance_id` before INSERTing. If it exists and is in a non-terminal state (IDLE, WAITING_CHILDREN, COMPLETED, PAUSED), reuse it. If it exists and is terminal, the caller (JobProcessor) handles re-spawn. If it doesn't exist, INSERT (current behavior).
  - **JobProcessor message branch**: When JobProcessor picks up a message job, it must decide: reuse existing instance (if valid) vs spawn new. Only spawn if the job represents new-instance work.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add load-existing branch in `_spawn_instance_db_sync` | Before line 3236 (`new_instance = Instance(...)`), add a SELECT: `existing = session.get(Instance, instance_id)`. If `existing is not None`: return it as-is (load its metadata, return a `_SpawnResult` indicating reuse). If `existing is None`: proceed with the current INSERT. Wrap in the existing `WriteGuardSession` transaction. | `daemon/services/instance_lifecycle.py:3162-3280` |
| 2 | Handle reuse-vs-insert in `spawn_instance` | The caller `spawn_instance` (line 1112) calls `_spawn_instance_db_sync` at line 1380. If the result indicates reuse (existing instance), skip MCP reload only if already loaded, and return the existing instance_id without error. Add a return path for "instance already exists, reused". | `daemon/services/instance_lifecycle.py:1112-1445` |
| 3 | Verify `spawn_instance_with_mcp` handles reuse | `manager.py:4159-4204` wraps `spawn_instance`. Ensure it doesn't error when the instance already exists — `ensure_mcp_preloaded` should be idempotent. | `daemon/manager.py:4159-4204` |
| 4 | Add message-aware dispatch branch in JobProcessor | In `_process_next_job` around lines 1007-1050: after `start_job` returns a started message job, check if `started_job.instance_id` refers to an existing, valid instance. If yes → reuse it (load, validate status). If the instance is terminal or missing → spawn new. Currently it ALWAYS calls `spawn_instance_with_mcp` at 1019 — this must become conditional for messages. | `daemon/services/job_processor.py:1007-1050` |
| 5 | Preserve the existing `enqueue_message` + `stamp_message_id` calls | After instance resolution (reuse or spawn), JobProcessor still calls `enqueue_message(instance_id=..., work_id=job.job_id)` at 1034 and `stamp_message_id` at 1062. These are correct — keep them. They create the Task + MessageQueue row linked to the JobItem. | `daemon/services/job_processor.py:1033-1069` (verify, no change) |

## Key Files

- `daemon/services/instance_lifecycle.py` — `spawn_instance` (1112), `_spawn_instance_db_sync` (3162)
- `daemon/manager.py` — `spawn_instance_with_mcp` (4159) [verify idempotency]
- `daemon/services/job_processor.py` — `_process_next_job` dispatch (1007-1050)

## Instance Status Handling Matrix

For the load-existing branch (Task 1) and JobProcessor dispatch (Task 4), handle these instance statuses:

| Instance Status | Message Job Action | Rationale |
|----------------|-------------------|-----------|
| IDLE | Reuse instance | Normal continuation message |
| WAITING_CHILDREN | Reuse instance | Parent waiting — message is valid |
| COMPLETED | Reuse instance | Terminal but reusable — this is how continuations work today |
| PAUSED | **Defer** (return None from `start_job`, let queue re-try) | Matches current `start_job` PAUSED handling at 2698-2739 |
| RUNNING / PROCESSING | Reuse instance (ExecutionGate serializes) | ExecutionGate already serializes per-instance; no conflict |
| ERROR / CANCELLED / terminal | Reuse OR re-spawn? | **Decision needed** — likely reuse if it's a soft terminal (COMPLETED), re-spawn if hard terminal (ERROR). Align with current HTTP POST /messages semantics. |

## Constraints

- **Concurrent spawn safety**: Two messages to the same non-existent instance could race. The `_spawn_instance_db_sync` is within a `WriteGuardSession` (write-locked). The SELECT-then-INSERT must handle the race where another transaction inserted the row between SELECT and INSERT — use `ON CONFLICT DO NOTHING` for PostgreSQL or catch `IntegrityError` for SQLite.
- **Do NOT change the INSERT for genuinely new instances** — only add the load-existing branch before it.
- The load-existing branch must NOT reset instance status to IDLE or overwrite metadata — reuse means reuse.

## Deliverables

- [ ] `_spawn_instance_db_sync` loads existing instance by `instance_id` before INSERT; reuses if found
- [ ] `spawn_instance` returns successfully for existing instances (no error)
- [ ] `spawn_instance_with_mcp` is idempotent for existing instances (MCP preload no-op)
- [ ] JobProcessor reuses existing instances for message jobs instead of spawning duplicates
- [ ] Unit test: message to existing instance → same `instance_id` reused, no new Instance row
- [ ] Unit test: message to non-existent instance → new instance spawned
- [ ] Unit test: concurrent messages to same non-existent instance → exactly 1 instance created (race safety)

## Notes

- This phase depends on Phase 1's `instance_id` preservation. If Phase 1 mints a fresh UUID, Phase 2's load-existing SELECT will never find the row.
- The duplicate-instance risk was flagged as the #1 migration hazard in the pre-loaded RAG context. This phase is where that risk is eliminated.
- **Decision deferred to execution**: hard-terminal instance (ERROR/CANCELLED) handling — reuse vs re-spawn. Recommend matching current HTTP POST /messages behavior (which is: reuse COMPLETED, reject/respawn ERROR).
