# Approver Tracking: durability-f1-f2

Plan: Durability Fixes F-1 + F-2 — F-1: DependencyBus None-error flip + discard_on_startup backlog-clear racing auto-continue boot pass; F-2: WC-wake straddle wedge (child completes just before restart → parent wedged in waiting_children).
Corpus: .agents/shared/planning/durability-f1-f2/ (9 files, ~4,550 lines) in worktree /home/nea/ensemble-src-wt-durability, branch feature/durability-f1-f2.
Commission bindings: reuse existing primitives (no new messaging paths); auto-continue existing tests/packs stay green; targeted packs per repo conventions; no live deploy; demo E2E sanctioned, may be made mandatory where unit/integration evidence insufficient. Phase 1 (F-1) code pre-landed — Phase 1 sections verification-scoped.

## Iteration 001 — REJECTED (2026-10-04)

Dispatch: 3 section-parallel workers (large-corpus exception), load_skill=plan-approval each, cold context:
- 064d78c1-9261-4039-9ff1-5301e714cbeb (approve-worker-f1): F-1 scope — REJECTED (3 blocking)
- fcc19ee4-0cbb-4cc4-952b-111546a8dcff (approve-worker-f2): F-2 scope — REJECTED (4 blocking)
- abb262d3-8424-4ee8-b5cf-54499122f954 (approve-worker-xcut): cross-cutting — REJECTED (5 blocking)

Aggregated blocking issues (deduped to 8; most-specific variants kept):

1. F-1 wipe-side evidence never executes the real preserve SQL. Landed S1/S4/S5/S6/S7 run an in-test Python re-implementation against dict rows (tests/unit/services/test_discard_on_startup_dependency_bus_race.py:447-491); shipped SQL never executed (SQLite↔PG dialect drift, alias/precedence bugs would pass the gate). Queue-side 2-arm disjunction (task 1.9, message_queue/repository.py:1002-1008) has NO test of any kind; no keep-green arm-3 coverage. R3 mitigation (plan-overview.md:285) cites evidence that does not execute the predicate — contradicts the plan's own C-2 principle (plan-overview.md:131-133, mock-only tests don't qualify as window-proof). [w-f1#1]
2. F-1 double-restart scenario not actually exercised. S1's stated measure (candidates==1, already_resuming==0 across two consecutive restarts — plan-overview.md:340, task 1.14) is unimplemented by its own test: same in-memory dict twice, no restart, no wipe, no mark_task_auto_continued CAS stamp. F-1 demo remains optional (plan-overview.md:211,:444) with zero integration evidence → decisions.md §9 skip condition ("unit + integration evidence green") unsatisfiable for the wipe seam, while F-2 (same C-2 standard) got mandatory integration + unconditional demo. Fix path (worker): (a) real file-backed-SQLite two-boot test (F9-parity harness exists: tests/unit/repositories/test_task_auto_continued_lifecycle.py: seed straddled child → boot-pass stamp → real clear_all → candidates==1 → second boot → no double-continue) or (b) mandatory F-1 demo probe. PG-parity for arm-3 SQL additionally noted (N5). [w-f1#2]
3. plan-overview.md carries TWO conflicting "Rollback story per phase" blocks (lines 297-332) + orphaned duplicate R10/R11 rows (316-318). Stale second block: Phase 1 rollback instructs reverting capture_boot_epoch ctor call — retained load-bearing per §13a/decisions.md:936-943 (following it breaks a retained consumer); Phase 2 rollback describes ELIMINATED wc_wedge_sweep ("single file delete + removal of the call from api.py" — no such call site). [w-f1#3 + w-f2#1 + w-xcut#1/#5; w-f1 variant most specific]
4. Stale pre-pivot Out-of-Scope text (plan-overview.md:147-153): "The WC wedge sweep is a new SERVICE… re-invokes the existing service" contradicts the W-4 pivot declaration in the same file (105-119, 172-175, 196-201). [w-f2#2]
5. phase3-plan.md stale residues: phantom tasks 2.13/2.14 (Phase 2 ends at 2.12; test work is 2.11); exit criterion (155-159) requires trigger-evaluation.md from task 3.5 — no such file produced, trigger concept dead (line 278) — unsatisfiable as written; "unit files already created" (183-186) false at plan time. [w-f2#3a/b/d]
6. Demo-gate ambiguity: phase3-plan.md:157 "(if triggered)" + "Conditional" File Inventory markers (177-181) vs UNCONDITIONAL (42-45, 76-79, 270-280); decisions.md §9 (779-838) still conditional — not updated by C-2. Pick one (C-2 = unconditional). [w-f2#3c + w-xcut#3]
7. phase2-plan.md Exit Criterion test-type inventory contradiction (259-275): "Total: 8 unit tests + 1 mandatory integration test" vs enumerated S21-S28 where S24 AND S27 are both real-PG integration (true: 7 unit + 2 integration). Singular gate wording could let S27 (no-duplicate-execution regression) be dropped while claiming the gate. [w-f2#4 ⊃ w-xcut#2]
8. decisions.md §9 evidence-bar counts stale: "six F-1 unit tests" vs seven (S1-S7); "six F-2 unit tests" vs eight S-criteria (2 integration). revision-summary.md claims the six/six fix was applied; §9 text was not. [w-xcut#4; corroborated by w-f1 N2]

Notes carried (non-blocking): stale phases-table counts/status (Phase 1 "9 tasks" vs 17; Phase 1 "pending" despite landed code); task 1.16 Depends-On omits 1.17 (prose resolves); N5 PG-parity for arm-3 SQL; unbounded 300s degraded-retry (consider stuck-counter); count slips in phase2 ("four existing RDRS suites" lists five; "8 implementation tasks (2.1-2.9)" = nine); R-P3-2 mitigation text duplicated verbatim; phases-table coupling rationale overstates join identity (N-4: joins are DIFFERENT); task 1.2 not marked LANDED; uncommitted Q6 re-scan record must land with commit.

Unanimous worker assessment: underlying architecture SOUND — W-4 RDRS lane-2 pivot verified in code (wipe provably spares report_injections/checkpoints/instances; no-new-messaging-paths honored; no-duplicate-execution by construction; F-1 ownership/ordering 13-step boot order satisfies the commission requirement verbatim). Remediation = contained doc purge (issues 3-8) + F-1 evidence-scope additions (issues 1-2); workers state no design rework needed and iteration 002 should be fast.

Status after 001: REJECTED → IN_PROGRESS, awaiting revised plan for iteration 002.

## Iteration 002 — REJECTED (2026-10-04)

Dispatch: 3 fresh section-parallel workers, load_skill=plan-approval each, cold context (no iteration-001 findings passed). Note: spawn-returned instance names crossed vs prompts; scope-based assignment recorded here — F-1 scope = 2ff73d73-8d1a-4570-a5a2-80fb3f15cffe, F-2 scope = e894d2af-dcb4-4fe6-b2bb-be5db95797c4, cross-cutting = fecd9a94-950b-4514-a9ba-214e76e129bf. Each prompt reached exactly one worker; all three scopes covered once.

Worker verdicts: F-1 APPROVED (0 blocking), F-2 REJECTED (1 blocking), xcut APPROVED (0 blocking).

Remediation verification (per fresh workers):
- Iteration-001 issues 1-2 (F-1 evidence): CLOSED by executed proof. F-1 worker re-executed packs on HEAD 7c23b9e4: F-1 pack 14/14, keep-green 56/56, interleaving 14/14, lifecycle 8/8, S1 two-boot 3 consecutive passes. Real TaskRepository.clear_all + MessageQueueRepository.clear_all SQL on file-backed SQLite (F9-parity), kill-switch ON/OFF, S1 executes real find_auto_continue_candidates asserting candidates==1 at boot N+1. Proxies disclosed in docstrings and classified Notes (already_resuming via labeled proxy — NOT gating per worker classification; caller's E2E merge-gate drill proved the literal field across five boots; optional small follow-up only).
- Iteration-001 issues 3-8 (doc purge): CLOSED in target files, grep-verified by xcut worker (single rollback story + LOAD-BEARING annotation, counts 7/7+2 with S27 non-droppable, §9 corrected, rename uniform, W-4 consistent).

Aggregated blocking issue (1; deduped from iteration-001's 8):
1. plan-overview.md:53-57 definition-of-done retains "(if Phase 3 trigger fires)" — stale conditional contradicting the UNCONDITIONAL F-2 demo per C-2 (overview :136-138/:375, phase3-plan.md :21-22/:50-53/task 3.6/:277-286). Falsifies the resubmission's own "UNCONDITIONAL everywhere" claim; the abolished trigger mechanism survives in the governing artifact's most prominent sentence. Fix: delete parenthetical / reword; sweep R-P3-5 "run regardless" residual framing in the same pass. [F-2 worker]

Notes carried (non-blocking): phase1-plan task 1.14 stale "mock InstanceManager (no real DB)" sentence contradicting the binding evidence bar; phase1 File Inventory omits tests/unit/repositories/test_message_queue_clear_all_2arm_disjunction.py; anchor drift in task/repository.py (+~83) and manager.py (+26) — navigational only, no F-2 task edits drifted regions, task 2.2 mandates grep-verification; arithmetic slip "count is 8 (2.1-2.9)" = 9; task 2.11(a) omits S28 (resolves via exit criterion); overview "Files to CREATE (Phase 3)" table (:448-461) + phase3 rollback (:173-174) stale vs "Phase 3 only runs them"; duplicated text fragments phase3-plan.md (R-P3-2 ~:122-129, :341-342); demo kill-timing racy — tester should assert wedge pre-condition (parent waiting_children + wake row PENDING) before kill -TERM; F-1 full-dir sweep evidence commit-message-asserted only — recommend re-run + archive under .agents/tester/RESULTS/; PG-parity carried as non-obligation; bus gate-2 (emit_terminal_for_child_instance) coverage indirect; terminalizer integration read-back-verified only (bounded by arm-3 EXISTS co-condition); Status: Draft headers stale; deferred §11a/§11b/§11c properly recorded.

Status after 002: REJECTED → IN_PROGRESS, iteration 003. Single one-line doc fix outstanding; F-1 evidence and remaining doc purge confirmed closed.

## Iteration 003 — REJECTED (2026-10-04) — MAX ITERATIONS REACHED → ESCALATED

Dispatch: 1 fresh worker (surgical doc-only delta; single-worker scope per scale guide), load_skill=plan-approval, cold context: a3866700-1e71-439c-b123-180cef8d3346 (approve-worker-final-003). Worker verdict: REJECTED (4 blocking).

Verified fixed this iteration (worker-confirmed): iteration-002 blocker CLOSED — definition-of-done now "F-2 demo E2E UNCONDITIONAL per C-2", 12+ active sites consistent incl. decisions.md §9; trigger-conditionals confined to marked historical records; e0990f1e verified DOC-ONLY via git; F-1 optional-demo carve-out correct; phase1 task 1.14 text now matches landed tests; queue-side test in File Inventory; counts 9/12 and S28 enumeration aligned; Phase-3 inventory/rollback/exit-criterion cleaned.

Aggregated blocking issues (deduped: #1/#3 same residue — the duplicated tail IS the contradictory-conclusion source; #4 is the falsified read-back claim attached to #1/#2):
1. phase3-plan.md:127-130 — duplicated R-P3-2 mitigation tail (mid-sentence fragment repetition of :123-126) re-asserting the original revert guidance WITHOUT the N/A note that :118-126 concludes with → two contradictory conclusions in one risk block. Multi-edit residue the corpus's own binding gotcha (plan-overview.md:27-28) forbids.
2. phase3-plan.md:346 — duplicated trailing fragment ("findings.md plus the boot log copies in logs/.") after the clean close at :343-345.
3. revision-summary.md:632 (ITERATION-003 addendum) — "Grep read-backs after every edit batch" claim FALSIFIED by the surviving residues; read-back at :657-673 does not include either site. Remediation claim ("phase3 duplicated fragments deduped") did not fully land.

Context for Leader adjudication (worker-reported): design substance sound and complete — architecture approved by every worker across all iterations; F-1 evidence verified by executed packs at iteration 002; demo gate now unambiguous; remaining defect surface is two text-level duplications in phase3-plan.md + a re-run of the read-back sweep. Worker's own words: "not architecture or design issues... implementation can proceed once these text-level residues are removed."

Status after 003: REJECTED — max iterations reached (3) → active.md Status: ESCALATED. Leader presents full tracking history to user.
