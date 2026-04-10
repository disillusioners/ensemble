# Plan Overview: Task Timeout & Retry

## Objective

Implement configurable task timeouts, graceful cancellation via CancellationToken + TimeoutMonitor, task retry with exponential backoff and LangGraph checkpoint resume, and idempotent retry — eliminating the stale task race condition and permanent task failure on timeout.

## Scope Assessment

**LARGE** — Touches 10+ files across 4 modules (models, repositories, services, manager), requires database migration, introduces new concurrency patterns (TimeoutMonitor, CancellationToken integration), and modifies the core task execution pipeline. Estimated 8-10 implementation sessions.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Requested by**: Leader
- **Preceding work**: Message Queue Redesign (5 phases) — completed. Task table, WorkerPool, TaskProcessor, StaleTaskRecovery, EventBus all functional.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Data Model & Migration | Add retry/cancel/retry_scheduled fields to Task, add CANCELLED status, update _row_to_task(), write migration | None | — | 1 session |
| 2 | CancellationToken & TimeoutMonitor | Enhance CancellationToken with cancelled_at, create TimeoutMonitor daemon thread | None | independent (from Phase 1) | 1 session |
| 3 | Repository Layer | Add retry/cancel/claim methods, force_cancel_and_schedule_retry(), find_orphaned_cancelled_tasks() | Phase 1 | tight | 1-2 sessions |
| 4 | TaskProcessor & Worker Integration | Wire token + monitor, fix C3 (msg.retry_count bug), replace hardcoded timeout | Phase 2, Phase 3 | tight | 2 sessions |
| 5 | StaleTaskRecovery Overhaul | 5-step recovery with double-retry guard, atomic cancel+retry, orphan detection | Phase 3, Phase 4 | tight | 1 session |
| 6 | Config & Wiring | Add config fields, wire through startup, integration test | Phase 4, Phase 5 | loose | 1 session |

### Coupling Assessment

| From → To | Coupling | Justification |
|-----------|----------|---------------|
| Phase 1 → Phase 3 | **tight** | Repository methods reference new model fields (retry_count, next_retry_at, cancel_requested, etc.) |
| Phase 2 → Phase 4 | **tight** | Worker imports TimeoutMonitor and CancellationToken |
| Phase 3 → Phase 4 | **tight** | TaskProcessor calls new repository methods (claim with retry-delay awareness) |
| Phase 3 → Phase 5 | **tight** | StaleTaskRecovery calls new repository methods (request_cancel, schedule_retry) |
| Phase 4 → Phase 5 | **loose** | StaleTaskRecovery sets cancel flag; Worker reads it — shared interface, not same files |
| Phase 4 → Phase 6 | **loose** | Config provides timeout values; default values make this work without config |
| Phase 5 → Phase 6 | **loose** | Config provides retry/max values; default values make this work without config |

### Parallelization Opportunity

**Phase 1 and Phase 2 are independent** — they can run in parallel:
- Phase 1: Data model + migration (model layer)
- Phase 2: CancellationToken enhancement + TimeoutMonitor (concurrency primitives)

**Phases 3-6 must be sequential** (tight coupling chain).

## Architecture Summary

```
                    ┌──────────────────────────┐
                    │      config.yaml         │
                    │  task_timeout_minutes    │
                    │  max_task_retries        │
                    │  task_retry_backoff_*    │
                    └───────────┬──────────────┘
                                │
    ┌───────────────────────────┼───────────────────────────┐
    │                           │                           │
    ▼                           ▼                           ▼
┌──────────┐          ┌──────────────────┐         ┌──────────────┐
│  Worker  │─────────▶│ CancellationToken│◀────────│TimeoutMonitor│
│  Thread  │          │     Source       │         │  (daemon)    │
└────┬─────┘          └────────┬─────────┘         └──────────────┘
     │                         │
     │                         ▼
     │               ┌─────────────────────┐
     │               │ CancellationCallback│
     │               │ (LangGraph callback) │
     │               └─────────────────────┘
     │
     ▼
┌──────────────┐     ┌───────────────────┐
│TaskProcessor │────▶│  TaskRepository   │
│  .run_task() │     │  .claim_task()    │◀── StaleTaskRecovery
│              │     │  .schedule_retry()│       .recover()
└──────────────┘     │  .request_cancel()│
                     └───────────────────┘
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| LangGraph ignores OperationCancelledError mid-stream | High — task hangs | Medium | TimeoutMonitor + MainLoopBridge timeout as double-fallback; StaleTaskRecovery as triple-fallback |
| Race condition: worker completes just as TimeoutMonitor fires | Medium — duplicate completion attempt | Medium | Use atomic status transitions in repository (UPDATE ... WHERE status='running'); idempotent complete/fail methods |
| Double-retry race: Worker + StaleTaskRecovery both schedule retry | Medium — duplicate retry tasks | Medium | `retry_scheduled` boolean guard column set atomically in schedule_retry() (S1/C2 fix) |
| SQLite locking under concurrent cancel + claim | Medium — performance degradation | Low | Keep transactions short; use UPDATE-RETURNING pattern; test under load |
| Checkpoint missing on retry (E4) | Low — replay instead of resume | Medium | Graceful degradation with warning log; message replay as fallback |
| Worker crash during retry scheduling | Medium — retry not scheduled | Low | StaleTaskRecovery detects orphaned CANCELLED tasks (retry_scheduled=False) on startup (S3 fix) |
| Crash between cancel and retry | Medium — orphaned CANCELLED task | Low | force_cancel_and_schedule_retry() does both in one transaction (W1 fix) |
| Backward compatibility — existing tasks without new fields | High — crash on read | Low | SQL migration adds columns with defaults; _row_to_task() has hasattr() guards (C4 fix) |
| msg.retry_count undefined variable | High — runtime crash on retry path | High (existing bug) | Add retry_count parameter to _process_message_with_tracking() (C3 fix) |

## Success Criteria

- [ ] AC1: Task timeout is configurable via `services.task_timeout_minutes` (default: 15 min)
- [ ] AC2: Worker gracefully stops LangGraph execution when task times out (CancellationToken + TimeoutMonitor)
- [ ] AC3: No duplicate processing after timeout/cancellation (atomic status transitions)
- [ ] AC4: Timed-out task retries resume from LangGraph checkpoint (not replay message)
- [ ] AC5: Retry count configurable via `services.max_task_retries` (default: 3)
- [ ] AC6: Exponential backoff between retries (1min → 2min → 4min, max 1hr)
- [ ] AC7: Tasks exceeding max retries are marked FAILED
- [ ] AC8: All state transitions logged with task_id, reason, timestamps
- [ ] AC9: Worker crash during timeout handled by StaleTaskRecovery (5-step recovery)
- [ ] AC10: Existing tasks without retry fields work fine (backward compatible)

## Testing Strategy

| Level | Scope | Approach |
|-------|-------|----------|
| Unit | CancellationToken, TimeoutMonitor | Thread-safety tests, cancel detection timing |
| Unit | TaskRepository new methods | In-memory SQLite, test claim with backoff, schedule_retry, request_cancel |
| Unit | TaskProcessor with token | Mock repo + mock manager, test timeout propagation |
| Integration | Worker + TaskProcessor + TimeoutMonitor | Real worker thread, mock task, verify cancel + retry scheduling |
| Integration | StaleTaskRecovery 5-step recovery | Create stale task, run recovery, verify state transitions |
| Integration | Full retry flow | Create task → timeout → retry → complete (end-to-end) |

## Migration Strategy

1. New SQL migration adds columns with defaults (`retry_count=0`, `next_retry_at=NULL`, `cancel_requested=0`, `cancel_requested_at=NULL`, `retry_scheduled=0`)
2. Application-level validation for CANCELLED status (no CHECK constraint change needed — AD-6)
3. SQLModel model updated with same defaults
4. `_row_to_task()` updated with `hasattr()` guards for migration safety (C4 fix)
5. Existing tasks will have `retry_count=0`, `cancel_requested=0`, `retry_scheduled=0` — perfectly valid for the old code path
6. MigrationRunner's idempotent handling will safely skip columns that already exist

## Tracking

- Created: 2025-04-08
- Last Updated: 2025-04-10
- Status: revised (v2 — fixed C1-C4, W1-W4, added S1/S3)
