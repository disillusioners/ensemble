# Phase 3: Tests + E2E Verification (v3.2 — cycle-3 review obligations; MANDATORY ensure.md e2e + 3.9 shield-gap + crash-mid-shield fixture)

## Objective

Prove the invariant — "a completed child turn ALWAYS eventually reports to parent; pause/resume/restart/crash never silently drop; exactly-once holds across all actors" — with targeted unit tests for both sites and all three variants **including guard-scope separation (3.2d)**, the FM-11 shield-gap detection test with its **crash-mid-shield fixture (W12)**, crash-recovery and the **explicit 10-pairing actor race matrix (S-a)**, sweep-safety incl. the **terminal-parent ORPHAN sub-case (W1)** and the **C3 false-positive matrix**, the MANDATORY full e2e suite **cited by name: `.agents/tester/rules/ensure.md`** (all five gated modules touched), and PG-primary migration safety in **3 sub-cases (C4)**. Zero regressions.

## Files Touched (anchors)

- New/extended `tests/unit/test_report_deferred_marker_pipeline.py` (Phase 1 + 3.9 scenarios)
- New/extended `tests/unit/test_report_deferred_marker_guards.py` (Phase 1 + 3.2d)
- New/extended `tests/unit/test_resume_router_deferred_recovery.py` (Phase 2)
- New/extended `tests/unit/test_report_delivery_recovery_service.py` (Phase 2 + C3 matrix + W1 sub-case)
- New `tests/unit/test_fm11_shield_gap.py` — test 3.9 (cancel-at-await + crash-mid-shield fixture)
- New `tests/integration/test_report_delivery_crash_recovery.py`
- New `tests/integration/test_report_delivery_double_delivery_pg.py` (10-pairing matrix)
- New `tests/postgres/test_report_deferred_migration_pg.py` (3 sub-cases)
- Extend `tests/integration/test_completion_report.py` (append-only)
- E2E suite `.agents/tester/rules/ensure.md` (RUN only, no source changes)

## Tasks

### MERGE BLOCKERS (cycle-3 review obligations, 2026-08-20) — owner: Phase 3 tester

These two deferred PG-dialect tests are **explicit MERGE BLOCKERS**: the branch does not merge until both are green on real PostgreSQL. They were deferred at cycle-3 review with this paper trail as the gating record.

- **MB-1 — PG-dialect SAVEPOINT-path test**: the C-DiD `begin_nested()` rollback path (child_reports.py:2685-2768 area — pre-SAVEPOINT outer flush at 2725-2737, SAVEPOINT at 2739, narrowed catch at 2755+) executed on **real PostgreSQL, not just SQLite**. SQLite quirks (no true constraint-name emission, lenient flush semantics) make the current SQLite-only green weaker evidence; the PG run must assert: nested.rollback() discards ONLY the injection INSERT; the outer commit preserves the child's COMPLETED transition + completion_report message + PROCESS_REPORT task.
- **MB-2 — PG constraint-name discriminator test**: the **PG branch** of `_is_obligation_triple_integrity_error` (child_reports.py:119-133 — constraint-name match `_OBLIGATION_TRIPLE_INDEX_NAME in msg` at :124) exercised with a **real PG IntegrityError** (PG emits the constraint name in `str(exc.orig)`). Only the SQLite branch (column-set match, :127-133) is currently tested. The PG test must also assert the negative: a NON-triple PG IntegrityError (e.g. FK violation) returns False and re-raises.

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | **Unit: Site 1 (Variant A half 1)** — pause TOCTOU simulation: child completes Stage 4/5.5, PAUSED before Stage 6 → DEFERRED marker (triple + PAUSE_TOCTOU), zero artifacts; resume → router re-entry → exactly 1 report on parent | Phases 1+2 merged | Green on PG; exactly-one; marker lifecycle DEFERRED→PENDING→terminal |
| 3.2 | **Unit: Variant B both halves (live anchors) + C5 scope separation** — (a) live inlined check 2106-2108 skip → marker (PENDING_MESSAGES) → recovery delivers; (b) CHILD-PAUSED idempotency guard → `deferred_pause` + marker (IDEMPOTENCY_SKIP) → recovery delivers; (c) COMPLETED/ERROR unchanged (`idempotency_skip`, no marker — no over-recovery); **(d) NEW: Site-1 shape (child COMPLETED, parent PAUSED) → marker written via the 1.4 PIPELINE path, NOT via the 1.6 guard — assert the 1.6 branch was never entered and the marker's reason is PAUSE_TOCTOU** | Phases 1+2 merged | Green on PG; exactly-once in (a)/(b); (d) pins the guard-scope separation (C5) |
| 3.3 | **Unit: router + zombie regression** — (a) DEFERRED present → `deferred_report_recovery` (never internal_child_noop); (b) silent=True, no DEFERRED, no handle → internal_child_noop **preserved verbatim**; (c) precedence: answer_gate and paused_turn win; (d) terminal-parent revival + re-entry; (e) rowcount=0 concurrent-actor skip; (f) absorbed duplicate marker → no-op (no raise, W6) | Phase 2 | Green; (b) critical no-regression |
| 3.4 | **Unit+integration: crash recovery + FM-2 RAM loss** — manager A drops report (marker persisted), destroy A (RAM set gone), manager B on same DB → boot sweep recovers, delivers exactly once; second boot no-op | Phase 2 | Green on PG; DB-not-RAM marker proven |
| 3.5 | **Integration: cross-actor double-delivery — EXPLICIT 10-PAIRING MATRIX (S-a)** — the five actors {hot-path drain, fallback task, resume router, sweep, FM-1-guarded path}: all C(5,2)=10 pairings asserted — claim UPDATEs win once; rowcount=0 → skip; absorbed IntegrityError → no-op. **Ordering assertion: `transition_deferred_to_pending` BEFORE partial-artifact reconciliation** (mirror SQL saw a PENDING row; reversed order silently skips). Pairing emphasis: sweep vs hot-path; router vs sweep (both recovery actors). **IMPLEMENTATION GUIDANCE (cycle-3, 2026-08-20 — CODE work, not test work; developer picks this up WITH the 3.5 matrix context): exception-leak hardening at child_reports.py:2755 — the SAVEPOINT block currently catches only `IntegrityError`; broaden to `except Exception → nested.rollback() → re-raise` (or convert to the `with session.begin_nested():` context form, which rolls back on any exception). Today a non-IntegrityError inside the SAVEPOINT leaks past it and is contained only by the outer WriteGuardSession — data-safe but less robust; the broadened rollback restores the SAVEPOINT boundary for every exception class. Note: the Y2 discriminator (`_is_obligation_triple_integrity_error`) continues to decide absorb-vs-re-raise for IntegrityError specifically — only the rollback boundary broadens.** | Phases 1+2 | Green on PG; 0 duplicate deliveries across all 10 pairings; ordering assertion present; **2755 hardening landed (broadened rollback verified by a non-IntegrityError-inside-SAVEPOINT case)** |
| 3.6 | **Unit+integration: sweep safety — five lanes + C3 matrix + W1 ORPHAN** — busy parent skipped; age bound respected; batch cap (101 → 100 + 1 logged); idempotent re-run; no-row lane recovers a never-markered drop; **C3 false-positive matrix: each exclusion case (child in-flight task; report in graph history; existing injection row ANY state; terminal parent; non-completed child/message) asserted NOT recovered**; retry lane recovers mid-transition-crash shape; **W1 ORPHAN sub-case: terminal-parent DEFERRED row → observable eventual disposition (revive-and-deliver or disposition log+metric), NEVER silent**; legacy stranded PENDING + no-row zombies recovered; lane kill-switches; never touches a live instance. **W6 KNOWN BLIND SPOT (post-deploy caveat, 2026-08-20)**: Lane-2's FIRED-exclusion predicate (`NOT EXISTS (SELECT 1 FROM dependency_watchers dw JOIN tasks t ON t.task_id = dw.source_task_id WHERE ... AND dw.state = 'FIRED')`) hides pre-fix historical rows where `dependency_watchers` is already FIRED but no marker was ever written (no row was created because the missing-write path predates Phase 1). POST-DEPLOY: every dropped obligation now carries a marker (the 1.4 Site-1 pipeline writes unconditionally), so this blind spot does not recur. The blind spot is acceptable in practice (a small bounded set of pre-deploy orphans that did NOT survive a recovery cycle is the expected outcome); if a customer reports missing historical reports, the diagnostic endpoint `POST /api/recovery/diagnose_no_row_lane` can list the affected rows without recovering them | Phase 2 | Green; per-row outcomes asserted; matrix all-green on SQLite AND PG; W6 blind-spot caveat documented in the matrix acceptance column |
| 3.7 | **PG migration tests — 3 SUB-CASES (C4)** — (a) PG `_ensure_postgres_columns` path: column adds + DROP NOT NULL + partial unique index re-run idempotent; **C1: duplicate-triple IntegrityError raise assertion post-migration**; W3 dedup pre-check (seed duplicates → detected + resolved → index builds); W8 rollback order (drop index → revert columns; column-first revert asserted to fail/be-blocked); (b) SQLite companion migration path: same shape, index names identical; (c) **NULL-branch consumer audit: grep-audit codified as a test** (all `report_message_id` consumers handle-or-exclude NULL; `claim_for_task_delivery` returns `missing` for NULL-keyed rows); fresh DB via create_all identical; no SQLite-only syntax | Phase 1 | Green on PG + SQLite; all three sub-cases |
| 3.8 | **MANDATORY full e2e per `.agents/tester/rules/ensure.md` — CITED BY NAME (W12)** — this feature touches ALL FIVE gated modules: claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks → the full ensure.md e2e suite is MANDATORY, not optional. Plus targeted e2e: pause-in-completion-window → resume → exactly one report; restart-after-drop → delivered; both guard variants → delivered; FM-11 cancel-at-await → delivered (marker or backstop) | 3.1-3.7, 3.9 | Full `.agents/tester/rules/ensure.md` e2e green on PG; 0 regressions; each targeted scenario asserts exactly-one-report |
| **3.9** | **FM-11 shield-gap detection (Option B decision gate) + W12 crash-mid-shield fixture** — (a) cancel the graph task DURING the Stage-6 pause-check await (`await _is_instance_paused` at pipeline.py:472; async def at 709): assert CancelledError still exits the pipeline properly (through `_handle_cancel` + its second cancel point :893) AND the marker row is committed FIRST (before exception propagation) — deterministic injection: patch `_is_instance_paused` to await a controllable future; cancel from the test at a synchronized point; no sleeps; (b) fallback: simulated LOST write (crash-mid-shield) → the permanent no-row sweep lane recovers within one cycle; **(c) W12 CRASH-MID-SHIELD FIXTURE: the dispatched coroutine's session COMMITs successfully, then a ConnectionError is raised after commit — verify the detached task's error path does not corrupt state (marker row present and intact; no partial/orphan artifacts; pipeline state unaffected)**. Failure of (a) triggers the pre-approved Option B fallback | Phase 1 (1.4) | Green on PG; (a) proves the W4 pattern; (b) proves Option C is a real net; (c) proves post-commit error isolation; outcome recorded for the Option B decision |

## Coupling

- **Loose with Phases 1-2** — consumes artifacts; 3.1/3.2/3.9 alongside Phase 1; full matrix + e2e gate after merge.
- **3.9 ↔ FM-11 ↔ Option B**: 3.9(a) fail ⇒ shield insufficient ⇒ activate pre-approved Option B ⇒ re-run 3.9 under Option B. 3.9(b) must hold under EITHER.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| 3.9 timing sensitivity (cancel must land at the await) | High | Deterministic controllable-future injection; synchronized cancel; assert propagation + marker atomically; no wall-clock racing |
| **W12 fixture complexity (commit-then-ConnectionError)** | Medium | DB session wrapper/stub raising post-commit; assert marker intact + no partial artifacts; isolated test DB |
| Full ensure.md e2e slow/flaky on shared PG | Medium | Isolated PG DB per ensure.md conventions; pytest-timeout; retry policy per suite norms |
| 10-pairing matrix combinatorial cost | Medium | Pairings share fixtures; both orderings only where outcomes differ; guarded-UPDATE semantics minimize timing dependence |
| C3 matrix misses a false-positive shape | Medium | Matrix enumerated from the designed query's predicate (2.4); dry-run rollout mode catches strays in prod safely |
| Crash harness complexity (3.4) | Medium | Reuse restart-test precedents (test_pause_report_orphan_reconciliation_pg.py; api.py:1140-1400 coverage) |

## Exit Criterion

All new tests green on PostgreSQL — including 3.2(d), 3.9(a/b/c), the 10-pairing matrix, the C3 false-positive matrix, the W1 ORPHAN sub-case, and the 3-sub-case migration suite; **the full `.agents/tester/rules/ensure.md` e2e suite green**; zero regressions; success criteria 1-11 from plan-overview.md demonstrably met. Ready for the scoped re-review + deep code review (deep review mode requested for this branch).

## Test Matrix Summary (traceability)

| Scenario | Variant | Drop Site | Test |
|----------|---------|-----------|------|
| Pause TOCTOU → resume | A (6c631666) | Site 1 + Site 2 | 3.1, 3.3(a), 3.8 |
| Cancel-at-await marker survival (FM-11) | A primary shape | Site 1 | 3.9(a), 3.8 |
| Crash-mid-shield isolation (W12) | A escape shape | detached task | **3.9(c)** |
| Lost write → permanent no-row backstop | A escape lane | no marker | 3.9(b), 3.6 |
| pending_messages_exist under pause (live anchor) | B (1d5fd5d2) | guard 1 | 3.2(a) |
| Idempotency guard CHILD-PAUSED branch | B | guard 2 | 3.2(b) |
| COMPLETED/ERROR no-op (no over-recovery) | — | guard 2 | 3.2(c) |
| **Guard-scope separation: child-COMPLETED/parent-PAUSED via 1.4 NOT 1.6 (C5)** | A canonical | Site 1 | **3.2(d)** |
| Genuine no-work silent resume preserved | — | Site 2 (preserved) | 3.3(b) |
| Concurrent-actor skip + absorbed duplicate (W6) | — | triple index | 3.3(e,f), 3.5 |
| **Natural completion × recovered PENDING marker (C-DiD, 2026-08-20)** | post-recovery race | obligation triple | **3.5** (new pairing) |
| Transition-before-reconciliation ordering | — | mirror guard | 3.5 |
| Restart between drop and resume (FM-2) | crash | marker | 3.4 |
| Double delivery — 10 explicit pairings (S-a) | — | claims + triple | 3.5 |
| Sweep: busy-skip / age / cap / idempotent / lanes / kill-switches | — | sweep | 3.6 |
| **C3 false-positive matrix (5 exclusions)** | — | no-row lane | **3.6** |
| **W1 ORPHAN: terminal-parent DEFERRED never silent** | — | sweep lane 5 | **3.6** |
| Retry lane (mid-sweep crash, FM-13) | crash | recovery_attempted_at | 3.6 |
| Legacy PENDING / no-row zombies | A/B legacy | sweep | 3.6 |
| PG migration: ensure path / SQLite path / NULL audit (C4) + C1 raise + W3/W8 | — | schema | 3.7(a,b,c) |
| Full `.agents/tester/rules/ensure.md` e2e — all five gated modules | — | all | 3.8 |

**3.9 ↔ FM-11 ↔ Option B**: 3.9(a) fail ⇒ Option B activated ⇒ re-run. 3.9(b) must hold under A or B. 3.9(c) independent of A/B (detached-task error isolation).
