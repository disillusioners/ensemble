# Tracking: Fix Pause-During-Report-Turn Orphan JobItem

## Iteration 001 — REJECTED
**Date:** 2026-08-01
**Verdict:** REJECTED

### Worker Reports
| Worker | Skill | Target | Verdict | Blocking | Notes |
|--------|-------|--------|---------|----------|-------|
| approve-worker-phase1 (c26e5cd9) | plan-approval | phase1-plan.md (Bug A) | APPROVED | 0 | 8 |
| approve-worker-phase2 (cdf775d7) | plan-approval | phase2-plan.md (Bug B) | REJECTED | 4 | 5 |
| approve-worker-cross (f1fc8887) | plan-approval | decisions.md + follow-up + overview | APPROVED | 0 | 9 |

### Blocking Issues (from Phase 2 worker — all retained, none downgraded)

1. **Unstick gap** (Objective §12-18; Task 12b §291; Risks §387)
   - Expected: Plan reliably unsticks WAITING_CHILDREN (Objective claim)
   - Found: Reconcile only flips status; no natural event re-fires completion cascade. Task 12b admits "do NOT claim a fresh resume can rediscover already-CANCELLED Tasks." How completion reevaluation is triggered after reconcile is unspecified. Phase 2.5 operator direct-write may be load-bearing but is not stated as a hard dependency.
   - Required fix: Specify the trigger mechanism (enqueue a trigger event OR explicitly state Phase 2.5 operator cleanup is a hard dependency for all stuck instances).

2. **Cross-DB semantic divergence** (§114-151 CTE sketch; §136-145 NULL-fallback; Risk §8)
   - Expected: NULL-fallback competing-live subquery yields identical semantics on PostgreSQL and SQLite
   - Found: PostgreSQL (READ COMMITTED) data-modifying CTE subqueries share one snapshot → a just-cancelled sibling appears PAUSED → false negative (blocks legitimate reconciliation). SQLite reads post-UPDATE → sibling appears cancelled → permits reconciliation. Same input, different outcome. No test asserts cross-engine agreement on this case.
   - Required fix: Either restructure the CTE to avoid the snapshot divergence, or add an explicit cross-engine test proving identical outcomes, or document and bound the divergence.

3. **processing_task_id NULL invariant contradiction** (Context §50; §58; §90, §92 truth table)
   - Expected: Direct correlation path (processing_task_id IS NOT NULL) is authoritative and reachable for the production failure class
   - Found: Production incident's orphan rows had processing_task_id=NULL (bug report lines 125-132). Direct path never applies to the incident — entire correctness story rests on the weaker NULL fallback. Worker found no code that sets processing_task_id non-NULL for completion_report rows. Conditional downgrade: if a non-NULL producer exists outside daemon/, this becomes a Note.
   - Required fix: Cite the producer that sets processing_task_id non-NULL, OR explicitly state the direct path is dead code for completion_report rows and the correctness story rests solely on the NULL fallback.

4. **Unconsumed report content silently dropped** (Scope §38; Risks §391; Open Q §442-444)
   - Expected: A fix that drops orphaned report content proves the content was consumed OR provides a re-arm/delivery path
   - Found: For the already-stuck production case, content was orphaned at processing with Task already cancelled — no evidence it reached the checkpoint. Plan's own Risk #5 rates "Critical / Low but not provably zero." No re-delivery, re-injection via report_injections, or operator verification is specified before closing instance COMPLETED.
   - Required fix: Spec a re-delivery path (e.g., re-arm report_injections row) OR add an operator verification step confirming content was consumed before the drop OR explicitly document the data-loss risk as accepted with mitigation.

### Non-Blocking Notes Summary (17 total across all workers — observations only)
- Phase 1: Step A scope broadening, active+missing case rationale, W2 threading impact, fallback invariant assertion, E2E determinism hook, stale-message cleanup no-op, multi-historical-Task case, production-DB sanity check query (8)
- Phase 2: 3-of-4 guard sites dead in production, "8 sites" framing, processing report-task re-arm, partial reconciliation test, type-mix join test (5)
- Cross-cutting: Phase 2 NULL fallback reuses message_id, F1 race-window unmeasured, no perf criteria for shared predicate, no rollback procedure, terminology overlap, sequential execution assumption, work_id column unowned, council composition unspecified, follow-up tracker unassigned (9)

### Approver Aggregation Notes
- No blocking issues from Phase 1 or cross-cutting workers.
- All 4 Phase 2 blocking issues retained — none downgraded (all specific, referenced, codebase-verified).
- No new blocking issues introduced (aggregation discipline: judgment band is downgrade-only).
- Phase 1 (Bug A) is sound and ready to proceed independently if desired.
- Cross-cutting decisions, follow-up bridge, and overview synthesis are sound.
- Block is specific to Phase 2 (Bug B) correctness/feasibility/completeness.

### Skills Used
plan-approval (all 3 workers)

### Session IDs
- c26e5cd9-8175-4d1e-9b60-7d887e225c1b (Phase 1)
- cdf775d7-0b1d-453f-b6e6-f4e9855cd6e5 (Phase 2)
- f1fc8887-148e-4f99-92f2-9e40bf850437 (cross-cutting)
