# Review Report: Job System Improvements Plan v5

**Date**: 2025-04-08  
**Reviewer**: Reviewer Agent (3 parallel verification sessions)  
**Sessions**: review-core, review-phase1, review-phases234

---

## Verdict: ✅ APPROVED — with conditions

The plan is **structurally sound** and the core architectural analysis is correct. All 6 verified codebase gaps are real. The proposed solution (observer pattern + atomic state transitions + no job-level timeout) is well-reasoned. However, there are **specific issues that must be addressed** before or during implementation.

---

## Summary: 9 findings

| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 Critical | 1 | Line number inaccuracies across all phases |
| 🟡 Warning | 4 | Dependency graph inaccuracy, atomic_transition pattern gap, Phase 4→3 hidden dependency, router registration oversight |
| 🟢 Suggestion | 4 | Test strategy gaps, failed→cancelled window, missing DLQ TTL mechanism, observer crash recovery |

---

## Findings

### 🔴 Critical

#### F1: Line Number Inaccuracies (Codebase Alignment)

**Area**: plan-overview.md, phase2-plan.md  
**Files**: `daemon/manager.py`

The plan references multiple line numbers that are **15-25 lines off** from actual code:

| Claim | Plan Says | Actual | Off By |
|-------|-----------|--------|--------|
| `_complete_job_for_instance()` | L575-610 | L560-596 | ~15 lines |
| parent_id early return | L1730 | L1713 | 17 lines |
| `terminate_instance()` | L2210-2250 | L2169-2238 | ~40 lines |
| `cancel_instance_requests()` | L2175 | L2157 | 18 lines |

**Why this matters**: If the implementer searches for code at the referenced lines, they'll find completely different code. This creates confusion during implementation and could lead to modifying the wrong code paths.

**Fix**: Re-verify all line numbers against current codebase before implementation. Better yet, use method names + code snippets as primary references rather than line numbers, since lines shift.

---

### 🟡 Warnings

#### W1: Phase Dependency Graph is Over-Simplified (Completeness)

**Area**: plan-overview.md — Phase Index & Coupling Assessment

The plan claims:
- Phase 3 depends only on Phase 1 (loose)
- Phase 4 depends only on Phase 1 (loose)
- Phase 3 ↔ Phase 4 are independent

**Reality**:
- **Phase 4 → Phase 3 dependency**: Phase 4 sub-task 1.3 explicitly says "When `RetryScheduler` makes a job retryable, also fire dispatch event." `RetryScheduler` is a Phase 3 component. This means Phase 4 depends on Phase 3, not just Phase 1.
- **Phase 3 → Phase 2 implicit dependency**: Phase 3's context section lists "failure sources" that include "feedback observer" (Phase 2) and "JobRecoveryService" (Phase 2). Phase 3 integrates retry logic at `job_queue_service.py` completion path, which is modified by Phase 2.

**Impact**: The correct dependency graph is:

```
Phase 1 (foundation)
  │
  ├──→ Phase 2 (sequential, tight coupling)
  │      │
  │      └──→ Phase 3 (Phase 3's retry hooks into failure paths that Phase 2 creates)
  │             │
  │             └──→ Phase 4 (Phase 4 dispatches events that Phase 3's RetryScheduler consumes)
  └──→ Phase 4 event bus core (CAN start parallel — DispatchEventBus, idempotency key)
```

**Mitigation**: Phase 4 can be partially parallelized. The `DispatchEventBus` creation (sub-task 1.1) and idempotency enqueue (Task 2) are truly independent. Only the RetryScheduler integration (sub-task 1.3) depends on Phase 3. Recommend splitting Phase 4 into Phase 4a (event bus + idempotency, truly parallel) and Phase 4b (retry dispatch integration, depends on Phase 3).

**Verdict**: Phases 3 and 4 can START in parallel with Phase 2 (after Phase 1), but they cannot COMPLETE without Phase 2 being done first. The "loose coupling" claim is partially accurate but misleading.

#### W2: `atomic_transition()` Pattern Doesn't Match Existing ORM Usage (Feasibility)

**Area**: phase1-plan.md — Task 1.3

The plan proposes:
```python
stmt = update(JobItem).where(...)
result = session.exec(stmt)
```

But the codebase uses **SQLModel ORM object mutation** pattern, not SQLAlchemy `update()` statements:
```python
job = db_session.get(JobItem, job_id)
job.status = JobStatus.PROCESSING.value
db_session.commit()
```

No `from sqlalchemy import update` import exists anywhere in the codebase. `session.exec()` is used for `select()` queries, not `update()` statements.

**Impact**: The `atomic_transition()` implementation needs to choose one of two approaches:
1. **Stick with ORM pattern** (consistent with codebase): Get → check status → mutate → commit. This has a TOCTOU window but SQLite's single-writer model makes it safe in practice.
2. **Use SQLAlchemy `update()`** (truly atomic): Requires adding the import and adapting the session usage pattern. More robust for race conditions.

**Recommendation**: Use approach 2 (SQLAlchemy `update()`) since the plan explicitly cites race condition handling as a key concern. But be aware this introduces a new pattern to the codebase. Document it well.

#### W3: Router Registration Oversight (Feasibility)

**Area**: phase3-plan.md — Task 4 (DLQ API Endpoints)

The review session initially reported routers weren't registered. After verification, routers ARE registered at `daemon/api.py:1923-1928`:
```python
from daemon.routers.jobs import router as jobs_router
api_router.include_router(jobs_router)
```

However, Phase 3's new `daemon/routers/dlq.py` router must also be registered here. The plan doesn't mention this wiring step.

**Impact**: Small — just needs an explicit task to add `from daemon.routers.dlq import router as dlq_router` and `api_router.include_router(dlq_router)` in api.py.

#### W4: `complete_job()` / `complete_job_sync()` Use Fetch-Then-Update (Race Conditions)

**Area**: phase2-plan.md — Task 2

Current completion methods use a fetch-then-update pattern:
```python
job = await asyncio.to_thread(self._repository.get, job_id)
# ... check status ...
await asyncio.to_thread(self._repository.complete_job, job_id, ...)
```

Phase 2's `JobFeedbackObserver` calls these methods. The plan relies on `atomic_transition()` from Phase 1 to prevent races, but the existing `complete_job()` doesn't use `atomic_transition()`. Phase 1 needs to refactor `complete_job()` to use `atomic_transition()`, or Phase 2's observer needs to call `atomic_transition()` directly instead of going through `complete_job()`.

**Impact**: Medium — Phase 1's Task 1.5 says "Methods: `start_job`, `complete_job`, `fail_job`, `cancel_job`" will be migrated to `atomic_transition()`. If this is done properly, Phase 2 is fine. But the migration must be complete and tested before Phase 2 begins.

---

### 🟢 Suggestions

#### S1: Test Strategy is Vague

**Area**: All phase plans

Each phase ends with "All existing tests pass" and "Tests for [feature]" but provides no test strategy detail:
- No specific test cases listed for race conditions
- No integration test plan for the observer→job completion flow
- No test for startup recovery scenarios (crash mid-processing, crash during observer event)
- No test for the FAILED→CANCELLED double-transition window

**Recommendation**: Add a testing sub-section to each phase with:
- Critical test scenarios (race conditions, crash recovery, concurrent actors)
- Integration test dependencies (what needs to be mocked vs. real)
- Test ordering (unit → integration → end-to-end)

#### S2: FAILED→CANCELLED Window Documentation

**Area**: phase2-plan.md — Task 4.3

The plan acknowledges a brief window where a job shows FAILED before transitioning to CANCELLED. While safe (the observer skips `terminated` events), this could confuse:
- API consumers polling job status
- Monitoring/alerting systems watching for FAILED jobs
- Phase 3's retry engine (if it processes FAILED jobs before the CANCELLED transition)

**Recommendation**: Add a guard in Phase 3's `find_retryable_jobs()` query to exclude jobs that were cancelled (e.g., check `cancelled_at IS NOT NULL`). Or ensure the FAILED→CANCELLED transition happens synchronously within the same coroutine step.

#### S3: Observer Crash Recovery

**Area**: phase2-plan.md — Constraints

The plan says "No auto-restart — if observer dies, startup recovery catches orphaned jobs on next restart." This means:
- If the observer loop crashes (exception in event processing), all subsequent events are missed
- Jobs stuck in PROCESSING until daemon restart
- No self-healing during runtime

**Recommendation**: Consider wrapping the observer loop in a try/except with restart logic:
```python
while self._running:
    try:
        event = await self._queue.get()
        await self._process_event(event)
    except Exception as e:
        logger.error(f"Observer error: {e}")
        continue  # Continue processing next event
```

The plan's health monitoring (sub-task 2.7) provides logging but no recovery. A simple exception handler would prevent silent death.

#### S4: DLQ TTL / Automatic Cleanup

**Area**: phase3-plan.md

Phase 3 adds `cleanup_by_age()` to DeadLetterRepository and a bulk delete API, but there's no automatic cleanup mechanism. DLQ items accumulate forever unless manually cleaned.

**Recommendation**: Add a configurable `dlq_retention_hours` (e.g., 168 = 7 days) and a background cleanup task that runs periodically. This prevents unbounded DLQ growth.

---

## What's Good About This Plan

1. **✅ Root cause analysis is accurate**: All 5 verified gaps are real and correctly described
2. **✅ ADR-009 (no job-level timeout)**: Excellent decision — avoids duplicating task-level timeout logic
3. **✅ Observer pattern**: Clean separation between task execution and job lifecycle observation
4. **✅ `atomic_transition()` with rowcount check**: Right pattern for concurrent state modifications
5. **✅ `terminate_instance()` reuse (ADR-011)**: Avoids code duplication
6. **✅ Event field verification (ADR-012)**: Catches a subtle but critical bug (`event_type` vs `kind`)
7. **✅ Single migration (ADR-005)**: Pragmatic — SQLite migrations are simpler in bulk
8. **✅ Dead code cleanup**: Removing `_complete_job_for_instance()` prevents future confusion
9. **✅ Backward compatibility**: All changes are additive with defaults

---

## Recommendations

### Before Implementation (Must Fix)
1. **Re-verify all line numbers** in the plan against current HEAD
2. **Clarify the `atomic_transition()` implementation approach**: SQLAlchemy `update()` vs. ORM mutation
3. **Update dependency graph**: Phase 4 depends on Phase 3 (sub-task 1.3), Phase 3 has implicit Phase 2 dependency

### During Implementation (Should Address)
4. **Refactor `complete_job()` to use `atomic_transition()` in Phase 1** — prerequisite for Phase 2
5. **Add exception handling in observer loop** — prevent silent crash
6. **Document FAILED→CANCELLED window** — add guard in Phase 3's retry query
7. **Add DLQ router registration** to api.py

### Test Coverage (Should Plan)
8. **Race condition tests**: Observer vs. terminate_instance concurrent completion
9. **Crash recovery tests**: Daemon restart with PROCESSING jobs, observer crash during event processing
10. **Integration test**: Full flow from enqueue → instance spawn → completion → job COMPLETED

---

## Phase-by-Phase Assessment

| Phase | Verdict | Key Risk |
|-------|---------|----------|
| Phase 1 | ✅ Can proceed | `atomic_transition()` pattern needs ORM adaptation |
| Phase 2 | ✅ Can proceed after Phase 1 | Startup ordering, observer crash recovery |
| Phase 3 | ✅ Can proceed after Phase 1 | Implicit Phase 2 dependency for failure detection |
| Phase 4 | ⚠️ Partially parallel | Sub-task 1.3 depends on Phase 3's RetryScheduler |

---

*Review completed with 3 parallel verification sessions: review-core (8 claims verified), review-phase1 (8 tasks verified), review-phases234 (12 tasks verified).*
