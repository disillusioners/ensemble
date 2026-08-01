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

---

## Iteration 002 — SUBMITTED FOR APPROVAL
**Date:** 2026-08-01
**Verdict:** PENDING APPROVER REVIEW
**Worker:** revise-p2-iter2 (8057a472-4183-411d-8cf6-db5edd9ba9da)

### Changes Applied (phase2-plan.md only — 587 lines, Revision 3)

| Blocking Issue | Resolution |
|---|---|
| **1 — Post-reconcile trigger** | New §A5: post-cascade re-fire via `_process_child_completion_db_sync(instance_id, completed_message_id=None)` for new incidents (Task 17); Phase 2.5 explicitly stated as hard dependency for historical stuck instances; operator runbook ordering documented. Verified: bus counts DependencyWatchers not message_queue rows (`dependency_bus/repository.py:301-340`). |
| **2 — CTE cross-DB divergence** | §A2 justifies `state.work_id <> ct.work_id` exclusion (PostgreSQL CTE sub-statements share pre-update snapshot; SQLite reads post-UPDATE). New Task 18: cross-engine parity test on both engines with identical seeded scenario. |
| **3 — processing_task_id NULL invariant** | "Work identity" section explicitly states direct path is **dead code** for production (`processing_task_id` only `default=None` at `models.py:72`, no producer populates it). Truth table relabeled. Defensive non-NULL test added. Open Q6 recommends producer-side follow-up. |
| **4 — Unconsumed content** | Phase 2.5 expanded with per-row ReportInjection consumption check: PENDING → refuse unless `--force-rearm`/`--force-drop`; INJECTED/TASK_DELIVERED → safe with audit; absent → refuse unless `--force-drop`. Phase 2.A drop justified by RUNNING=consumption-in-progress. New Task 19. |

### Non-blocking notes addressed
- Guard-site reachability reframed: 1 reachable production + 3 dead-code fallbacks (verified via `RuntimeError` at `child_reports.py:1252` and bus-active early-returns)
- "8 sites" reframed as "4 active parent-completion + 4 audit-only child-decision"

### New artifacts in plan
- Tasks 17 (post-reconcile re-fire), 18 (cross-engine CTE parity test), 19 (ReportInjection consumption check)
- Risks 16-19 (double-completion in re-fire, CTE snapshot divergence, PENDING ReportInjection dropped, re-fire TOCTOU)
- Success Criteria 16-20 (self-heal, cross-engine parity, no double-complete, PENDING refusal, defensive non-NULL path)
- Open Questions 6-7 (processing_task_id producer follow-up, operator completion API)

### Approved files (unchanged)
- phase1-plan.md, decisions.md, plan-overview.md, follow-up-turn-reconciler.md — NOT modified in this iteration

### Skills Used
plan-creation (skill_id: 0ab11975-0ef5-4bec-9617-2fd7be43ed20)

---

## Iteration 002 — APPROVED
**Date:** 2026-08-01
**Verdict:** APPROVED
**Approver:** approver[v2] (this session)

### Worker Reports
| Worker | Skill | Target | Verdict | Blocking | Notes |
|--------|-------|--------|---------|----------|-------|
| approve-worker-cascade (753e0fd2) | plan-approval | Phase 2.A + §A5 re-fire, Tasks 3/4/17/18, Blocking Issues 1+2 | APPROVED | 0 | 7 |
| approve-worker-guard (2b514bb8) | plan-approval | Phase 2.B + Phase 2.5 + test strategy + risks, Blocking Issues 3+4 | APPROVED | 0 | 5 |

### All 4 Previously-Blocking Issues — RESOLVED

1. **Post-reconcile trigger** — RESOLVED. §A5 re-fire mechanism via `_process_child_completion_db_sync(instance_id, completed_message_id=None)` is correct for new incidents (Task 17). Phase 2.5 operator cleanup is the hard dependency for historical stuck instances. Both workers verified idempotency guards make re-fire safe.

2. **CTE cross-DB divergence** — RESOLVED. `state.work_id <> ct.work_id` exclusion neutralizes PostgreSQL READ COMMITTED snapshot divergence. Task 18 cross-engine parity test seeds the exact scenario on both engines. Worker A verified the snapshot analysis against PG docs; Worker B confirmed the test directly addresses the issue.

3. **processing_task_id NULL** — RESOLVED. Direct path documented as dead code (no producer in daemon/ sets processing_task_id non-NULL — verified by both workers via grep). Correctness rests solely on NULL fallback (message_id locator → work_id projection). Defensive non-NULL test case added.

4. **Unconsumed content** — RESOLVED. Phase 2.5 per-row ReportInjection consumption check (Task 19): PENDING → refuse unless force; INJECTED/TASK_DELIVERED → safe; absent → refuse unless force-drop. Phase 2.A drop justified by RUNNING=consumption-in-progress (residual risk documented).

### Non-Blocking Notes (12 total — observations only, not demands)

**From Worker A (7):**
- CTE self-join is no-op (work_id is unique — simplifiable to single task reference)
- Non-root re-fire description confusing (no-op for production root-leader scenario)
- Re-fire uses legacy pending_count query, not shared predicate (acceptable, Open Q7 acknowledged)
- SQLite RETURNING requires v3.35+ — plan should state minimum SQLite version
- last_content="" is unused in root completion path (placeholder, safe)
- CAST(ct.id AS text) implicit coercion note (production path short-circuits)
- OR short-circuit with three-valued logic verified correct

**From Worker B (5):**
- Implementation order ambiguity (Task 17 depends on Task 2 but phases described as sequential)
- §A4 "consumption in progress" is probabilistic not guaranteed (documented residual risk)
- Re-fire effectiveness is sound regardless of Task 6 ordering (positive observation)
- Minor line-number drift (1212-1219 → actual 1226-1230; within 1-2 lines tolerance)
- TOCTOU between ReportInjection check and message_queue UPDATE is benign

### Aggregation Discipline
- No blocking issues from either worker.
- No new blocking issues introduced (judgment band: downgrade-only, not upgrade).
- No conflicting findings between workers.
- All 4 previously-blocking issues independently verified as resolved by BOTH workers.

### Skills Used
plan-approval (both workers)

### Session IDs
- 753e0fd2-bfd9-4a42-b4e2-10b2aee15af5 (cascade reconciliation)
- 2b514bb8-e2e9-46c5-8a33-6121416abfeb (guard hardening + cleanup + tests)
