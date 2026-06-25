# Plan Overview: Pause/Resume Feature Redesign

## Objective

Redesign the pause/resume feature to add a first-class **PAUSED state** for jobs and tasks, replacing the current broken hack that cancels execution tasks while keeping jobs in PROCESSING. When a user presses pause on any instance (parent or child), the job enters a proper PAUSED state. When resumed, the job cleanly returns to PROCESSING and execution continues — no premature completion, no zombie jobs, no race conditions.

## Scope Assessment

**LARGE** — This is a redesign of a core feature touching the state machine, database schema, execution layer, dual-track architecture, and 213+ tests across 18 files. It spans multiple modules (job_queue, task, instance, manager, services) and requires careful migration of existing in-flight jobs.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Feature Branch**: `feature/pause-resume-redesign` (to be created)

## Current Architecture (The Problem)

### State Machine — NO PAUSED state for jobs/tasks
| Entity | File | States | Problem |
|--------|------|--------|---------|
| **Job** | `daemon/repositories/job_queue/models.py:21` | `PENDING, PROCESSING, COMPLETED, FAILED, CANCELLED, DEAD_LETTER` (6) | No PAUSED — jobs stay PROCESSING during pause |
| **Task** | `daemon/repositories/task/models.py:36` | `PENDING, RUNNING, COMPLETED, FAILED, CANCELLED` (5) | No PAUSED — tasks blocked by instance-level SQL gate |
| **Instance** | `daemon/repositories/instance/models.py:20` | 10 states including `PAUSED` | Only entity with PAUSED state |

### The Broken Hack (Current Pause Flow)
```
PAUSE:
  User → POST /instances/{id}/pause
   └─ pause_instance_cascade()
       ├─ Cancel LLM requests (cooperative)
       ├─ Cancel graph task (asyncio CancelledError)
       └─ Set instance.status = PAUSED  ← ONLY instance, NOT job/task
       └─ Job STAYS PROCESSING ← THE HACK
       └─ Bus watchers cancelled

RESUME:
  User → POST /instances/{id}/resume
   └─ resume_instance_cascade()       ← status only, no graph re-entry
   └─ resume_processing_job()
       ├─ _resume_processing_background()
       │   └─ _process_message_with_tracking(is_retry=True)
       ├─ bus.count_pending_for_target() ← TOCTOU RACE WINDOW
       └─ complete_job(COMPLETED)       ← PREMATURE if child report races
```

### Root Causes of Bugs
1. **Premature completion**: `resume_processing_job()` at `manager.py:2898` calls `complete_job()` whenever the bus reports 0 pending, but the bus read (line 2870) and job transition are NOT in the same transaction
2. **TOCTOU race**: A child can register a new watcher between the bus check and `complete_job`
3. **Zombie jobs**: Resume creates new jobs while old PROCESSING jobs remain
4. **Dual-track desync**: Instance Track (child_reports.py) and Job Track (job_feedback_observer.py) lose coordination during pause/resume because pause cancels bus watchers but resume doesn't re-register them
5. **No job-level pause**: Jobs stay PROCESSING — there's no way to distinguish "actually processing" from "paused but job still says PROCESSING"

## Solution Architecture

### Core Design: First-Class PAUSED State
Add `PAUSED` to both `JobStatus` and `TaskStatus` enums. When pausing:
1. Job transitions `PROCESSING → PAUSED` (not stays PROCESSING)
2. Task transitions `RUNNING → PAUSED` (if running)
3. Instance already has PAUSED — keep it
4. Bus watchers are preserved (not cancelled) but gated by PAUSED status

### Target Pause Flow (New)
```
PAUSE:
  User → POST /instances/{id}/pause
   └─ pause_instance_cascade()
       ├─ Cancel LLM requests (cooperative)
       ├─ Cancel graph task
       └─ Instance → PAUSED
       └─ Job → PAUSED (NEW: transition PROCESSING → PAUSED)
       └─ Task → PAUSED (NEW: transition RUNNING → PAUSED)
       └─ Bus watchers PRESERVED (not cancelled)

### Target Resume Flow (New)
```
RESUME:
  User → POST /instances/{id}/resume
   └─ resume_instance_cascade()
       ├─ Instance → RUNNING
       ├─ Job → PROCESSING (NEW: transition PAUSED → PROCESSING)
       ├─ Task → PENDING (NEW: transition PAUSED → PENDING for re-claim)
       ├─ Bus watcher compaction (NEW: clean FIRED rows before notify_work)
       └─ Resume execution from checkpoint
           └─ After graph turn (result OR no-op):
               └─ _process_resume_finalize() (NEW: deterministic trigger, C1 fix)
                   ├─ Validate bus is not None (A9 hard-error carried forward)
                   ├─ Check bus pending INSIDE transaction
                   ├─ If pending > 0: defer (emit in_progress)
                   └─ If pending == 0: _finalize_job(COMPLETED) via single transaction
```

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Enum & State Machine | Add PAUSED state to Job/Task enums, update all state-transition validation | None | — (root) | 3-4h |
| 2 | Pause Flow Redesign | Transition jobs/tasks to PAUSED on pause, preserve bus watchers, atomic cascade | Phase 1 | tight | 7-9h |
| 3 | Resume Flow Redesign | Clean resume from PAUSED, deterministic finalize trigger, eliminate premature completion | Phase 2 | tight | 9-11h |
| 4 | Cascade & Hierarchy | Parent→child cascade pause/resume with job/task propagation | Phase 2, 3 | loose | 4-6h |
| 5 | Test Suite Migration | Update 213+ tests, add new PAUSED state tests | Phase 1-4 (strict) | loose | 6-8h |
| 6 | E2E & Integration Validation | E2E test update, crash recovery, cold-resume TTL, integration validation | Phase 1-5 | loose | 5-7h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 imports/uses the PAUSED enum from Phase 1; touches same model files |
| 2 → 3 | **tight** | Resume (Phase 3) reverses what pause (Phase 2) does; must agree on state transitions |
| 2,3 → 4 | **loose** | Cascade extends pause/resume to hierarchy; depends on interfaces, not core implementation |
| 4 → 5 | **strict** | Phase 5 includes 19 cascade tests that depend on Phase 4's cascade behavior. Cannot start until Phase 4 is complete. |
| 1-5 → 6 | **loose** | E2E/integration validates the complete system |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **`job_state_machine.py` TRANSITIONS dict missing PAUSED pairs** (B1) | **CRITICAL** | Phase 1 Task 3 adds `(PROCESSING, PAUSED)`, `(PAUSED, PROCESSING)`, `(PAUSED, CANCELLED)` to the TRANSITIONS dict at lines 20-41. Without these, `validate_transition()` throws `InvalidTransitionError` on every pause/resume. |
| **Worker finally block flips PAUSED task back to COMPLETED** (B2) | **CRITICAL** | Phase 2 Task 6 adds PAUSED status guard to the worker's CancelledError handler and `complete_task` block — checks task status before completing, skips if PAUSED. Worker releases concurrency slot via ExecutionGate unwind. |
| **No-op resume → job stuck PROCESSING forever** (C1) | **CRITICAL** | Resume explicitly calls `_process_resume_finalize()` on the observer after every graph turn (even no-ops). Carries forward the A9 hard-error for `bus is None`. See Decision 3. |
| **Bus watcher unbounded growth during long partial-tree pauses** (C3) | HIGH | Compaction hook `_compact_fired_watchers_for_paused()` runs on resume to delete FIRED rows already superseded by PROCESS_REPORT tasks. See Decision 2. |
| **Bus watcher recovery drops watchers for PAUSED instances** (C4) | HIGH | Bus watcher recovery at `api.py:743-760` must explicitly SKIP PAUSED-instance jobs (leave watchers for resume), not stamp as "processed". See Phase 6 Task 7. |
| **Migration breaks existing in-flight jobs** | HIGH | Phase 1 migration is additive (new enum value only — no DDL change since statuses are VARCHAR). Crash recovery at `job_recovery_service.py:132` transitions PROCESSING → PAUSED for jobs on PAUSED instances |
| **Race condition in resume finalize** | HIGH | Phase 3 eliminates direct `complete_job()` and uses deterministic `_process_resume_finalize()` (reuses `_finalize_job`) with transactional bus gate. Eliminates TOCTOU race at manager.py:2898 |
| **Pause-claim race: cascade must transition tasks atomically** (W1) | HIGH | `_pause_cascade_db_sync` extended to atomically transition tasks `WHERE status='running' → 'paused'` in same transaction as instance pause |
| **Bus watcher state during pause** | MEDIUM | Phase 2 preserves bus watchers (vs current cancel) with compaction hook for growth. On resume, watchers + PROCESS_REPORT tasks drive finalization |
| **213+ tests need updating** | HIGH | Phase 5 (strictly after Phase 4) dedicates full phase to test migration. 19 cascade tests depend on Phase 4 behavior (W4) |
| **`complete_task()` block at manager.py:2944-2991 becomes incorrect** (W2) | MEDIUM | Phase 3 Task 6 explicitly removes/repurposes the `complete_task()` block. Phase 2 Task 6 adds PAUSED guard to the same block for the pause case. |
| **PAUSED → CANCELLED undefined** (W3) | MEDIUM | Decision 10: PAUSED jobs CAN be cancelled. Terminate path releases locks + cancels bus watchers. |
| **Dual-driver migration (SQLite + PostgreSQL)** | LOW | Phase 1 requires NO DDL migration — only Python enum additions (statuses are VARCHAR with app-level validation) |
| **Existing PROCESSING jobs on startup** | MEDIUM | Phase 6 crash recovery at `job_recovery_service.py:132`: PROCESSING jobs on PAUSED instances → PAUSED |
| **Per-instance serialization guard in claim_pending_task** | LOW | The guard checks `status='running'` only — PAUSED tasks won't block siblings since paused instances are already filtered by pause gate. Test edge case in Phase 5 |
| **Cold-resume after TTL eviction** | LOW | PAUSED instances included in 24h TTL eviction. On resume after eviction, must cold-resume from checkpoint. Test in Phase 6 |

## Success Criteria

- [ ] `JobStatus` enum includes `PAUSED` state
- [ ] `TaskStatus` enum includes `PAUSED` state
- [ ] **TRANSITIONS dict in `job_state_machine.py` includes all PAUSED pairs** — B1
- [ ] Pausing a parent instance transitions its job to PAUSED and all children's jobs to PAUSED
- [ ] Pausing a child instance transitions only that child's job to PAUSED
- [ ] **Worker CancelledError handler does NOT flip PAUSED task back to COMPLETED** — B2
- [ ] Resuming returns jobs to PROCESSING and execution continues from checkpoint
- [ ] No premature completion on resume (TOCTOU race eliminated)
- [ ] No zombie jobs on resume
- [ ] No-op resume does NOT leave job stuck PROCESSING (deterministic finalize trigger) — C1
- [ ] Bus watchers preserved during pause with compaction for long pauses — C3
- [ ] Bus watcher recovery skips PAUSED instances (doesn't silently drop watchers) — C4
- [ ] PAUSED → CANCELLED transition works (terminate releases locks + cancels watchers) — W3
- [ ] New messages during pause are queued (PENDING), not lost — W5
- [ ] Cold-resume after TTL eviction works from checkpoint — S2
- [ ] E2E test `test_pause_after_spawn_then_resume` passes with PAUSED state assertions
- [ ] All 213+ existing tests updated and passing
- [ ] Tests pass on both PostgreSQL and SQLite
- [ ] Crash recovery correctly handles PAUSED jobs (at `job_recovery_service.py:132`) — C2
- [ ] Bus watchers preserved during pause (not cancelled)

## Tracking

- **Created**: 2026-06-25
- **Last Updated**: 2026-06-25 (revision 2 — addressed 2 approver blockers B1+B2 + 2 non-blocking notes)
- **Status**: draft (revised v2)
