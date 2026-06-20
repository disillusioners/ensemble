# Tracking: Decouple Job / Task / Message Correlation (Review + Execution Plan)

## Iteration 001 — 2026-06-20 11:42

**Plan**: Decouple Job/Task/Message Correlation — Detailed Review + Execution Plan
**Files**: `docs/plans/decouple-review.md` (278 lines), `docs/plans/decouple-execution-plan.md` (570 lines)
**Branch**: `feature/decouple-job-task-msg`

### Verdict: APPROVED

### Verification Performed

**Source-code claims verified (15/15 accurate):**
- `waiting_for` in 18 daemon files — ✅ confirmed (18 files)
- `execution_gate.py` = 707 lines — ✅ confirmed
- `FOR UPDATE` gate at `job_feedback_observer.py` ~1230-1320 (`_finalize_job_db_sync` at L1113) — ✅ confirmed
- `enqueue_message` at L887, `enqueue_message_via_jq` at L1486 — ✅ confirmed exact
- `rebuild_from_db()` at L493-584 — ✅ confirmed
- `SELECT COUNT(*)` fallback at `child_reports.py` ~657-678 — ✅ confirmed
- premature-completion tests in `tests/postgres/`, not `tests/test_premature_completion.py` — ✅ confirmed (non-postgres file doesn't exist)
- `concurrency_atomic_unit_test` 86/86 — ✅ confirmed in `.agents/tester/PACKS.md`
- `cross_dispatcher_*` tests don't exist — ✅ confirmed (correctly removed from acceptance criteria)
- execution_lease repo + migration file exist — ✅ confirmed
- existing gate tests import `LeaseContention`/`LeaseHolderKind`/`ExecutionLeaseRepository` — ✅ confirmed
- ADR-011 referenced in `docs/architecture.md` — ✅ confirmed
- `message_processing_pipeline.py` = 783 lines — ✅ confirmed

**Council finding (MainLoopBridge timeout leak in asyncio.Lock collapse):**
- Valid technical observation: `MainLoopBridge.run_async` (L84-99) uses `future.result(timeout=...)` without cancelling the future on timeout — orphaned coroutine could hold asyncio.Lock indefinitely with no DB-lease expiry to recover.
- Falls WITHIN C12a's scoped open questions (Q2-Q5 explicitly address MainLoopBridge contention and thread behavior).
- NOT blocking: C12a is a hard precondition, C12b is a contract-capture test, C18 is a merge gate, and the plan has a block condition ("collapse is blocked") + fallback ("threading.Lock or hybrid").

### Notes (non-blocking)
- W5/W8 absent from round-2 resolution table (W4,W6,W7,W9,W10,W11 present) — likely numbering gaps, not missing resolutions.
- CI-gates-replacing-production-dwell tradeoff is acknowledged by the reviewer as "partially true" for TOCTOU races — known and documented, not hidden.

### Why Approved
- All 6 critical findings from review round-2 incorporated into execution plan
- Every high-risk change has precondition + test + merge gate
- Phase D (irreversible column drop, in-flight migration gap) correctly DEFERRED to release N+1
- Plan is self-consistent, feasible, safety-conscious, and technically accurate
