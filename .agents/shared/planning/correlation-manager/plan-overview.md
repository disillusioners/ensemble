# Plan Overview: CorrelationManager Migration

## Objective
Introduce a `CorrelationManager` component that replaces the scattered `waiting_for` counter + 3 divergent cascade decision sites with a single event-driven correlation component. This eliminates 3 HIGH-severity race conditions and unifies parent-completion decision logic across dual-path message processing.

## Scope Assessment
**LARGE** — Multiple features, 6 phases, touches 11+ files across the daemon. Each phase is independently deployable with no breaking changes. Core architectural refactor of parent-child lifecycle management.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **DB**: Dual-driver (SQLite + PostgreSQL), repository pattern required
- **Volume**: ~1 msg/sec saturation, max 100 instances, 50 children/parent, single daemon
- **No external deps**: In-process event bus sufficient (no Redis/NATS/Kafka)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 0 | Critical Bug Fixes | Fix Race #5 (resume bypasses gate) + JobQueue missing error reporting | None | — | 4-6h |
| 1 | CorrelationManager Introduction | New in-memory component subscribing to lifecycle events, shadow mode | None (parallel with Phase 0) | independent | 6-8h |
| 2 | Observer Switch | Migrate JobFeedbackObserver to correlation events, eliminate Race #1 | Phase 0, Phase 1 | loose | 4-6h |
| 3 | Cascade Unification | Delegate 3 cascade sites to CorrelationManager, eliminate Race #3 | Phase 1, Phase 2 | tight | 6-8h |
| 4 | Counter Removal | Deprecate `waiting_for` + `WAITING_CHILDREN` status, cleanup 6 sites | Phase 2, Phase 3 | tight | 4-6h |
| 5 | Dual-Path Event Unification | Unify WorkerPool + JobQueue event emission (optional) | Phase 3 | loose | 6-8h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 0 ↔ 1 | independent | Phase 0 fixes bugs in existing code; Phase 1 adds new component alongside |
| 1 ↔ 2 | loose | Phase 2 subscribes to CorrelationManager's new event type; only needs the interface contract |
| 2 ↔ 3 | tight | Phase 3 replaces the cascade logic that Phase 2's observer consumes; must coordinate on event semantics |
| 3 ↔ 4 | tight | Phase 4 removes the counter that Phase 3 delegates to CorrelationManager; same files |
| 4 ↔ 5 | loose | Phase 5 touches different code (dispatch paths); only depends on Phase 3's unified event types |

### Scheduling Strategy

```
Phase 0 ──────────────────────┐ (independent, start immediately)
                                │
Phase 1 ──────────────────────┤ (independent, start immediately)
                                │
                     Phase 2 ←─┘ (needs 0+1)
                                │
                     Phase 3 ←──┘ (needs 1+2)
                                │
                     Phase 4 ←──┘ (needs 2+3)
                                │
                     Phase 5 ←──┘ (optional, needs 3)
```

**Phases 0 and 1 can run in parallel** (2 coder instances). Phases 2-4 must be sequential.

## Race Conditions Addressed

| Race | Severity | Location | Phase Fixed | Root Cause |
|------|----------|----------|-------------|------------|
| #1 | HIGH | `job_feedback_observer.py:262-302` | Phase 2 | `waiting_for` snapshot taken, then slow `await _get_last_assistant_message_raw` before `atomic_transition` — stale snapshot acted upon. Fixed by CM callback (no snapshot). |
| #3 | HIGH | `child_reports.py:478-524` | Phase 3 | `SELECT COUNT(*)` of pending messages, parallel `enqueue_message` inserts between COUNT and commit → orphaned message. Fixed by pure in-memory set operations (no DB query). |
| #5 | HIGH | `manager.py:2701-2794` | Phase 0 | `resume_processing_job` calls `_process_message_with_tracking` directly, bypassing ExecutionGate → dual-driver checkpoint corruption. Fixed by wrapping in gate.run() with bounded retry. |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Shadow mode mismatch storms (Phase 1) | Low | Log-only, capped at 100/min; auto-disable logging if rate exceeded |
| CorrelationManager loses in-memory state on crash | Medium | Rebuild from `instances` table (`waiting_for > 0` + message queue cross-reference); best-effort for correlation keys |
| Event ordering across concurrent completions | Medium | Per-parent `asyncio.Lock` serializes all register/resolve operations for same parent (Fix C4) |
| LangGraph checkpoint corruption during transition | High | Phase 0 fixes gate bypass first; all other phases don't touch checkpoint mechanism |
| `WAITING_CHILDREN` status removal breaks external API consumers | Medium | Phase 4 keeps status as derived/alias until full removal; API layer translates |
| Dual-driver SQL compatibility (SQLite/PostgreSQL) | Medium | All DB queries use repository pattern; raw SQL uses `COALESCE` + `CASE WHEN` (already proven portable) |
| CM callback dropped or delayed (C2/C3 original concern) | Critical | Direct callback (not EventBus queue) — no persistence, no queue overflow, invoked within Lock |
| Late message arrival after correlation.complete | Medium | `register_message_send` re-adds parent to tracking; instance revival logic handles COMPLETED → RUNNING |
| Re-enqueue infinite loop on lease contention (C6) | Medium | Bounded retry: 3 attempts with exponential backoff [0.5s, 1s, 2s], then fallback to enqueue |

## Success Criteria

- [ ] Phase 0: Race #5 eliminated — `resume_processing_job` acquires ExecutionGate lease; JobQueue path emits error events + lifecycle events + error reports
- [ ] Phase 1: CorrelationManager runs in shadow mode, logs mismatches with `waiting_for`, zero false negatives after 24h soak
- [ ] Phase 2: `job_feedback_observer.py` no longer reads `waiting_for` directly; subscribes to `correlation.complete` events
- [ ] Phase 3: All 3 cascade sites delegate to CorrelationManager; single decision path; zero divergent logic
- [ ] Phase 4: `waiting_for` column deprecated; `WAITING_CHILDREN` status removed; all tests pass after removal
- [ ] Phase 5: Both WorkerPool and JobQueue publish to shared event topics; mirroring points reduced from 14 to ≤5
- [ ] All phases: Existing test suite passes at each phase boundary; no new flaky tests introduced

## Tracking
- Created: 2026-06-16
- Last Updated: 2026-06-16 (Revision 4 — Fixed A1/A2 approver blocking issues)
- Status: approved (all review issues resolved across 4 rounds)
