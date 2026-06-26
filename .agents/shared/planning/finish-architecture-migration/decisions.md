# Architecture Decisions: Finish Architecture Migration

## Decision 1: `cancel_for_source` Already Implemented

**Date**: 2026-06-26
**Context**: The LESSONS documents describe `cancel_for_source` as "needs to be added" to DependencyBus. The production bug investigation document proposes it as a new method.
**Decision**: The method **already exists** at `dependency_bus.py:885-997` and is **already wired** into all retry-scheduled paths via `_notify_bus_of_cancel_and_retry` (`stale_task_recovery.py:490-543`) and `_cancel_bus_watchers_for_task` (`worker_pool.py:463-496`).
**Impact**: Item 1 (06f500af bug) is mostly fixed. The remaining work is: (a) add startup sweep for defense-in-depth, (b) verify permanent-fail paths.

---

## Decision 2: Phase 4 Column Drop Already Applied

**Date**: 2026-06-26
**Context**: The LESSONS doc and cleanup plan describe the migration `20260621_000002` as broken (would drop live `instance_hierarchy` table) and `_ensure_postgres_drop_legacy_columns()` as a NO-OP.
**Decision**: Both are **already fixed**:
- Migration file has NO `DROP TABLE instance_hierarchy` — only drops `waiting_for` + `children` columns (verified by reading the file)
- `_ensure_postgres_drop_legacy_columns()` at `manager.py:1917` has real `ALTER TABLE DROP COLUMN IF EXISTS` statements
- `waiting_for` column reads: only 2 hits in daemon/ (the ALTER + log msg)
- `.children` attribute reads: 0 hits in daemon/
**Impact**: Item 3 (Phase 4 column drop) is largely done. Remaining work is docs/cleanup only (Phase 6).

---

## Decision 3: D11+D13 is the Core Remaining Work

**Date**: 2026-06-26
**Context**: The coupling between MESSAGE and Job records is the structural root cause of 06f500af-class bugs.
**Decision**: D13 (eliminate MESSAGE JobItem creation) and D11 (remove job_processor MESSAGE branch) are the primary remaining work items. Phases 2→3→4→5 form the critical path.
**Impact**: The critical path is ~4-4.5 days of sequential work.

---

## Decision 4: Only 2 Production Callers of `dispatch_path="jobqueue"`

**Date**: 2026-06-26
**Context**: The LESSONS doc lists 4 callers of `dispatch_path="jobqueue"`.
**Decision**: Only **2** actually use it:
- `routers/messages.py:119-125` (HTTP send_message — discards job_id)
- `tools/job_queue.py:473-487` (job_continue tool — returns new_job_id)

The other 2 (`utils.py:575`, `job_queue_service.py:258`) default to `"workerpool"`.
**Impact**: D13 caller audit is simpler than expected. HTTP route doesn't even use the job_id — a strong signal the JobItem was unnecessary.

---

## Decision 5: HTTP API Contract Preserved via Task ID Adapter

**Date**: 2026-06-26
**Context**: After D13, `AsyncMessageResult.job_id` would be `None` since no JobItem is created.
**Decision**: Set `job_id = str(task_id)` in the return value. The semantic meaning changes from "JobItem ID" to "Task ID" but the API contract (non-null `job_id` field) is preserved.
**Impact**: The `job_continue` tool (only consumer of `job_id`) continues to work. HTTP route unaffected (it discards `job_id`).

---

## Decision 6: `job_queue_items.job_type` Column — Future Cleanup

**Date**: 2026-06-26
**Context**: After D13+D11, the `job_type` column only has value `"task"`.
**Decision**: Do NOT remove the column in this migration. Document as a future cleanup candidate. Column drops require separate migration + PostgreSQL handling via `_ensure_postgres_columns` / `_ensure_postgres_drop_legacy_columns`.
**Impact**: Low-risk deferral. The column is harmless (always "task").

---

## Decision 7: Startup Sweep Excludes Paused Tasks

**Date**: 2026-06-26
**Context**: The proposed startup sweep for orphan PENDING watchers could accidentally cancel watchers for paused tasks.
**Decision**: The sweep checks `source_task_id` against task status. Tasks with status `IN ('running', 'pending', 'paused')` are considered active. Only tasks that are terminal (completed, failed, cancelled) or missing have their watchers cancelled.
**Impact**: Paused task watchers stay PENDING so resume can re-fire them. This matches the existing `_test_pause_does_not_cancel_bus_watchers` regression test.

---

## Decision 8: Test-First Approach (C4) — Phase 0 Acceptance Test

**Date**: 2026-06-26
**Context**: The reviewer requested an E2E test that validates the 06f500af bug class is eliminated, written BEFORE implementation.
**Decision**: Add Phase 0 — write `test_06f500af_bug_class_eliminated` with `xfail` markers. The test simulates: (1) spawn parent + child, (2) simulate child crash (no terminal notification), (3) restart daemon, (4) assert parent transitions out of WAITING_CHILDREN via the startup sweep. Remove `xfail` markers as each phase lands.
**Impact**: Provides a concrete acceptance criterion. The test goes red→green incrementally, validating each phase contributes to the fix.

---

## Decision 9: Atomic Sweep (W2) — Conditional UPDATE, Not Read-Then-Update

**Date**: 2026-06-26
**Context**: The reviewer flagged that a read-then-update sweep has a TOCTOU race: between reading PENDING rows and transitioning them, a concurrent `emit_terminal` could fire.
**Decision**: Use a single atomic conditional UPDATE: `UPDATE dependency_watchers SET state='cancelled' WHERE state='pending' AND source_task_id NOT IN (SELECT id FROM task WHERE status IN ('running','pending','paused'))`. The DB engine handles the race internally — no Python-level race window.
**Impact**: Eliminates the TOCTOU race. The sweep is a single SQL statement, idempotent, and safe to run concurrently with `emit_terminal`.

---

## Decision 10: Data Migration for In-Flight MESSAGE JobItems (C2)

**Date**: 2026-06-26
**Context**: After Phase 3 removes the MESSAGE branch from `job_processor.py`, any PENDING/PROCESSING MESSAGE JobItems in the DB have no processor and would stay forever.
**Decision**: Add a startup-guarded idempotent data migration in Phase 2: `UPDATE job_queue_items SET status='cancelled' WHERE job_type='message' AND status IN ('pending','processing') AND deleted_at IS NULL`. Runs once on daemon startup. Works on both SQLite and PostgreSQL.
**Impact**: Prevents orphaned JobItems from blocking queue locks. The `WHERE status IN ('pending','processing')` guard makes it idempotent.

---

## Decision 11: 7 MESSAGE Cleanup Sites, Not 2 (C3)

**Date**: 2026-06-26
**Context**: The original plan identified 2 MESSAGE-specific sites in `job_queue_service.py`. The reviewer found 4 additional sites.
**Decision**: Expand Task 2.4 to clean ALL 7 sites: (1-2) `job_queue_service.py:379-388, 500-511` queue routing, (3) `job_queue_service.py:1255-1256` start_job branching, (4) `instance_lifecycle.py:920-934` terminate cleanup, (5) `instance_lifecycle.py:1858-1865` [TRACE] log, (6) `job_queue/repository.py:505-516` concurrency gate query, (7) the comprehensive grep gate.
**Impact**: No residual `job_type="message"` dead code paths. The grep sweep (Task 2.6) is the final gate.

---

## Decision 12: `get_message_status` Endpoint Rewrite (C1)

**Date**: 2026-06-26
**Context**: The `GET /instances/{id}/messages/{msg_id}/status` endpoint queries for MESSAGE-type JobItems. After D13, no such rows exist.
**Decision**: Rewrite the endpoint to query `task` rows by `message_id`. Response shape stays the same (`message_id`, `instance_id`, `status`, `result_summary`, `error`).
**Impact**: Frontend polling continues to work. The endpoint now uses the single work record (Task) instead of the eliminated JobItem.

---

## Decision 13: Phase 2.5 — Consumption-Site Rewrite (B1+B2+B3)

**Date**: 2026-06-26
**Context**: The approver identified three BLOCKING consumption sites that depend on MESSAGE JobItems existing: `resume_processing_job` routing (B1), observer finalization chain (B2), and `job_continue` concurrency gate (B3). The original plan eliminated JobItem creation (input side) but didn't account for these consumers (output side).
**Decision**: Add Phase 2.5 between Phase 2 (D13) and Phase 3 (D11). This phase rewrites all three consumption sites to work with Task rows instead of JobItems. The phase is tightly coupled to Phase 2 — they must land together (D13 stops creating JobItems, Phase 2.5 stops consuming them).
**Impact**: Without this phase, D13 would silently break: (a) checkpoint resume for root instances (B1), (b) instance finalization after resume (B2), (c) job_continue race protection (B3). The pause/resume feature (completed 2026-06-25) is directly affected.

---

## Decision 14: `_finalize_job_db_sync` Handles `job_id=None` (B2)

**Date**: 2026-06-26
**Context**: The observer's `_finalize_job_db_sync` performs 3 atomic steps: (1) JobItem status transition, (2) instance status update, (3) lock release. After D13, Step 1 is a no-op (no JobItem).
**Decision**: Option (c) — make Step 1 a conditional no-op. When `job_id is None`, skip the JobItem UPDATE but proceed with Steps 2+3 (instance status + lock release). This is the least disruptive option — the critical operations (instance transition + lock release) are preserved, and the JobItem update becomes optional.
**Impact**: The finalize chain continues to work. The caller (`_finalize_job`, `_process_resume_finalize`, `_process_event`) passes `job_id=None` for the post-D13 message path. TASK-type jobs still pass a real `job_id` and all 3 steps run.

---

## Decision 15: Resume Routing Uses PAUSED+RUNNING Task Lookup (B1)

**Date**: 2026-06-26
**Context**: `resume_processing_job` uses `find_processing_message_jobs_by_instance` to decide root-vs-child routing. After D13, this always returns empty.
**Decision**: Replace with a new `find_paused_or_running_by_instance(instance_id)` method on the task repository. Root instance = has a PAUSED or RUNNING PROCESS_MESSAGE Task. Child instance = no such Task. This preserves the existing routing semantics: root instances get checkpoint resume, child instances get fresh WorkerPool enqueue.
**Impact**: Root instances paused after receiving an HTTP message will correctly resume from LangGraph checkpoint. The task repository already has `find_running_by_instance` and `has_inflight_task` — the new method widens the filter to include PAUSED.

---

## Decision 16: Orphan-Race Re-arm Mechanism Needs Analysis (B2)

**Date**: 2026-06-26
**Context**: The post-commit re-arm path in `_finalize_job` transitions a COMPLETED JobItem back to PROCESSING when the generation counter bumps (late child registered a watcher). After D13, there is no JobItem to re-arm.
**Decision**: Task 2.5.7 is a DESIGN ANALYSIS task, not a blind implementation. The bus's own watcher/generation mechanism may be sufficient: when a late child resolves, the bus fires a FollowUp that drives a new lifecycle event, which triggers a new `_process_event` → `_finalize_job` cycle. The JobItem re-arm was needed to give the late child's resolve a PROCESSING job to find — but without JobItems, the instance lifecycle event itself drives the finalize. If analysis confirms the bus mechanism is self-sufficient, the re-arm becomes a no-op. If not, re-arm the Task instead.
**Impact**: This is the most subtle part of the consumption-site rewrite. Incorrect analysis could reintroduce the orphan-child bug (parent finalizes while child is still working).
