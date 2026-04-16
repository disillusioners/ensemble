# Review Plan: Job System Improvements Plan v4

## Scope

Review of 6 plan files + 1 investigation document for the Job System Improvements v4:
- `plan-overview.md` — Architecture, phase index, diagrams
- `phase1-plan.md` — Foundation: State Machine & Persistent Locks
- `phase2-plan.md` — Integration: Task↔Job Feedback Loop (completely rewritten)
- `phase3-plan.md` — DLQ + Auto-Retry
- `phase4-plan.md` — Event-Driven Dispatch & Idempotency
- `decisions.md` — Architecture Decision Records (ADR-001 through ADR-009)

## Review Type
Plan Review (Architecture & Correctness)

## Focus Areas
- [ ] Phase 2 EventBus integration correctness (event names, subscription mechanism)
- [ ] Phase 2 completeness for solving "stuck PROCESSING" problem
- [ ] Startup recovery adequacy (all crash scenarios covered?)
- [ ] Phase 3 TIMED_OUT removal completeness
- [ ] Phase 1 field consistency after removal of timeout/heartbeat concepts
- [ ] Internal cross-plan consistency (no orphaned references)
- [ ] State machine validity without TIMED_OUT
- [ ] New failure modes from EventBus dependency
- [ ] ADR quality and consistency

## Session Breakdown
| Session | Target | Focus | Priority |
|---------|--------|-------|----------|
| review-phase2 | Phase 2 + EventBus source | EventBus integration, event names, subscription mechanism, feedback completeness | P0 |
| review-phases13 | Phase 1 + Phase 3 | Field consistency, TIMED_OUT removal, state machine validity, DLQ path | P0 |
| review-cross-cut | All plans + decisions.md | Cross-plan consistency, ADR quality, new failure modes, startup recovery gaps | P1 |

## Approach
3 parallel sessions. Critical findings aggregated immediately.

## Critical Pre-Review Findings (from source code analysis)
1. `INSTANCE_COMPLETED` is only published for CHILD instances (line 1660 in manager.py), not for regular top-level job instances
2. `PROCESSING_COMPLETED` EventKind exists but is never published anywhere
3. `_complete_job_for_instance()` is defined but never called from any code path
4. `subscribe_all()` returns an `asyncio.Queue` — events are put as dicts with `event_type`, not `kind`
5. No `INSTANCE_ERROR` event exists in EventKind enum

These need to be validated against the plan's assumptions.
