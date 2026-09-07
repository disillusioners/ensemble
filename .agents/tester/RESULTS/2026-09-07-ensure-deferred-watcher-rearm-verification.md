# Verification Gate — ensure_deferred + Watcher Re-Arm Fix (e9aac370)

Date: 2026-09-07 | Branch: `feature/fix-report-delivery-ensure-deferred` | Fix commit: `e9aac370` (base `bb052fce`)
Gate tip after verification commits: `e158903d` (e9aac370..e158903d = 9× test/packs/* + ONE amended test file — see §6)
Incident closed against: giter child d90b18f9 completed 19:40:21 → leader b7ead8a4 permanently stuck (attestation gate pending_children=1, no wake path); sweeps looped 'racing delivery won' every 300s on ZERO rows; same flap d77727cf/c5ae6d95.

## VERDICT: ✅ CLOSED — zero-row stuck class self-heals within one sweep pass, proven by test INCLUDING on PostgreSQL. ONE contract amendment (§6) requires leader ratification.

## 1. Fix under test (5 source + 3 test files — dispatch's "2+3" was a paraphrase; actual via git show)
- `daemon/repositories/report_injection/repository.py`: ensure_deferred insert-on-missing — terminal pre-check (state IN INJECTED/TASK_DELIVERED/FAILED = positive delivery evidence → no-op); IntegrityError → fresh-session re-read → zero rows ⇒ INSERT ("absence ≠ delivered"); persistent conflict ⇒ raise; non-terminal duplicate ⇒ state-guarded Core UPDATE (PENDING/DEFERRED only).
- `daemon/services/instance_lifecycle.py` (+287): resume_instance_cascade re-arms CANCELLED+unenqueued watchers (rowcount-guarded CAS CANCELLED→PENDING + bus cache parity); kill-switch ENSEMBLE_WATCHER_REARM_ON_RESUME default ON (cached restart-read; falsy spellings incl. 0/false/no/off).
- `daemon/repositories/dependency_bus/repository.py` (+104), `daemon/services/dependency_bus.py` (+37), `daemon/services/report_delivery_recovery.py` (+37): sweep/lane support.
- New tests (18): tests/unit/test_ensure_deferred_insert_on_missing.py (10) + tests/integration/test_pause_resume_watcher_rearm.py (4) + tests/unit/test_report_delivery_self_heal_zero_row.py (4).

## 2. Pre-fix discrimination proof (2 rounds — round 1 produced a finding)
- Round 1 (self-heal file at parent, pack 37552b61): PASSED 4/4 at bb052fce — test-design finding: the file exercises the SEQUENTIAL path; the bug class is the concurrent IntegrityError→zero-rows false-positive. Documented, not a proof.
- Round 2 (pack b54b5937, LEG A gating = tests/unit/test_ensure_deferred_insert_on_missing.py at parent): **4/10 real assertion failures** — terminal-evidence ×2 (`assert result is None` failed: pre-fix INSERTS a fresh DEFERRED row when a terminal row exists — the exact bug feeding the flap) + phantom/persistent-IntegrityError ×2 (AttributeError: `_insert_deferred_marker` absent at parent — structural proof the fix exists to make the bug class testable). 6/10 happy-path passes → fix targeted and minimal. LEG B (informational): re-arm integration file cannot COLLECT at parent (ImportError: `_reset_watcher_rearm_for_tests` — fix-era symbol) — structural corroboration.

## 3. Pack results (all on feature/fix-report-delivery-ensure-deferred; relaxed gate: e9aac370 ancestor + only test-infra diffs above)

| # | Pack | Result | Counts | Runtime |
|---|------|--------|--------|---------|
| 1 | ensure_deferred_unit_test (NEW) | ✅ PASS | 10/10 (incl. all 4 pre-fix failure classes green at HEAD) | 0.56s |
| 2 | watcher_rearm_integration_test (NEW) | ✅ PASS | 4/4 (ON-path wake fires / OFF-pin / CAS-skip / legacy fallback) | 0.67s |
| 3 | self_heal_zero_row_unit_test (NEW) | ✅ PASS | 4/4 (one-pass heal + anti-flap + mutation-proof guard) | 0.51s |
| 4 | ensure_deferred_pg_smoke_integration_test (NEW, LOAD-BEARING) | ✅ PASS | 4 legs / 23 checks (see §4) | ~2s |
| 5 | report_injection_regression_unit_test (NEW) | ✅ PASS after adjudicated amendment | 61/61 (was 60/61 — see §6) | 0.93s |
| 6 | report_delivery_recovery_regression_unit_test (NEW) | ✅ PASS | 27/27 | 0.81s |
| 7 | dependency_bus_fire_for_terminated (addendum) | ✅ PASS | 6/6 (incl. both race tests + parent pending-gate clear) | 0.16s |
| 8 | child_parent_lifecycle_regression_test | ✅ PASS | 220P/19S — bit-exact vs 2026-09-06 gate | 10.15s |
| 9 | child_reports_unit_test | ✅ PASS | 48/48 — parity | 1.29s |
| 10 | completion_regression_test | ✅ PASS | 96P/37S/1-des — bit-exact parity | 1.62s |
| 11 | concurrency_atomic_unit_test (ensure.md Critical) | ✅ PASS | 98P/74S — baseline-exact | 7.07s |

**Head totals: 574 passed / 130 skipped / 1 deselected / 0 failed.** Kill-switch leg: OFF-pin passes in-pack AND under real-environment flip (`env ENSEMBLE_WATCHER_REARM_ON_RESUME=0` → 1 passed) → CLOSED.

## 4. PG live-path (LOAD-BEARING — dev's tests are SQLite-only)
Disposable `ensemble_test_ensdefer_e9aac370` (create→verify→drop, trap-guarded; HARD GUARDS on ensemble_prod; prod verified untouched via pg_database read). All 4 legs PASS:
- **LEG A (7/7)**: partial unique index `uq_report_injections_oblig_triple` confirmed on PG (UNIQUE (parent,child,msg) WHERE state IN ('PENDING','DEFERRED')); INSERT gating, double-call convergence, reason-change in-place UPDATE, terminal no-op.
- **LEG B (5/5)**: REAL 2-session barrier race, different deferred_reason per racer → both returned IDENTICAL injection_id, exactly ONE DEFERRED row, loser converged via fresh-session re-read (IntegrityError absorbed).
- **LEG C (4/4)**: all three terminal states (INJECTED/TASK_DELIVERED/FAILED) → positive-evidence no-op; fresh pair Y → insert.
- **LEG D (7/7)**: full incident shape (WAITING_CHILDREN parent + COMPLETED child + AGENT message + ZERO injection rows + CANCELLED/unenqueued watcher) → `_run_no_row_backstop_lane` healed in ONE pass (recovered=1, errors=0; manager re-enter exactly once, source='sweep_no_row_backstop'); pass-2 anti-flap (recovered=0, no candidates); ZERO 'racing delivery won'/'already delivered' logs.

## 5. Edge-case matrix (investigation + /tmp scratch, repo untouched)
| Edge | Verdict |
|---|---|
| Concurrent ensure_deferred + sweep same pair | UNCOVERED by tests → scratch-verified OK 30/30 (Barrier(2), per-iteration fresh DB): 17× ensure-actor-owns + 13× lane-recovered mid-window race; all invariants held — exactly 1 row, reconcile ≤1, no double wake, no crash. Duplicate recovery structurally impossible (partial index + all-states re-read + guarded transitions). |
| FAILED-state terminal pre-check | **CORRECT** (scratch-verified). FAILED = deliberate dead-letter written ONLY at 3 dead-parent-gated manager seams; no lane re-drives FAILED (Lane 1=DEFERRED, Lanes 3/4=PENDING, Lane 2 excludes FAILED pairs twice); revive-loophole closed via persisted completion_report message row. Coverage gap: add FAILED to terminal_state parametrize (1-line, report-only). |
| Re-arm when child already terminal | COVERED (`test_terminal_child_watcher_stays_cancelled` verified at HEAD). Discriminator map: kill-switch → CANCELLED+unenqueued candidate → payload child_id (legacy source_task_id fallback) → child-liveness guard (TERMINAL_STATUSES skip; CAS fires only for non-terminal). 4 tests span both sides. |

Report-only observations: deferred_reason last-writer-wins under the race (diagnostic metadata only); terminal-row-only pair absorbed as benign already_recovered (log line at worst); pre-check→insert TOCTOU already in dev's deferred-risk ledger — bounded by Edge-1 scratch (convergence held incl. mid-window).

## 6. ⚠️ Contract amendment REQUIRING LEADER RATIFICATION (commit `e158903d`, +20/−8, one file)
`tests/repositories/test_report_injection.py::test_ensure_deferred_after_terminal_allowed` FAILED at HEAD (60/61): the old pin asserted same-triple re-insert after terminal INJECTED ("re-spawn allowance") — the exact superseded semantics (pre-fix insert-after-terminal was the LEG-A-proven bug feeding the flap; it PASSED at base, so not among dev's 9 base failures). Amended to the new two-leg contract: (a) same triple after terminal → None + row-count unchanged; (b) DIFFERENT triple (fresh child/message ids) → inserts DEFERRED (legitimate re-spawn preserved). Pack re-run 61/61. **Ratification = blessing terminal-positive-evidence semantics: same-triple ensure after a delivered report is a permanent no-op.** Second contract-collision in two gates — pattern + planning rule documented in LESSONS/2026-09-07-ensdefer-contract-collision-and-discrimination.md.

## 7. ensure.md (Core, blast-radius scoped)
✅ Critical: changed packs PASS (all 11 incl. amendment re-run) · ✅ concurrency/deadlock integrity (concurrency_atomic baseline-exact) · ✅ dev.sh `--timeout-graceful-shutdown 10` FOUND (:102) · ℹ️ async-await grep items out of scope (fix touches none of those functions). Release Gate NOT warranted (single report-delivery subsystem, 5 source files, no architecture change). No method contradictions found.

## 8. Scope decision
Full suite not run. Scoped: 11 packs (4 new-file + 2 discovered regression + 5 reused), PG verifier, pre-fix worktree ×2 rounds, edge investigation. Covers all 5 touched source surfaces' neighbor suites (dev's 9 base failures never materialized in-scope — zero unattributed failures).

## 9. Commits made by this gate (all test-infra/test-file only; zero source modifications; ensemble_prod never written)
`37552b61` (round-1 prefix pack) · `b54b5937` (round-2 discrimination prefix pack) · `27013c97` (6 pack scripts) · `e158903d` (after-terminal pin amendment — RATIFY).

## 10. Workers (18 dispatches, 13 instances)
infra cac0fd19 · prefix 0733f6b8 (×2 rounds) · lifecycle 4d6db7d7 · childreports 006d9d02 · completion c5298f5e · concurrency ac628d50 · unit-ensure ab39f291 · int-rearm 30d49191 · unit-selfheal 4df9529c · pg 985e39b0 · reg-injection bbd2a611 (×2: run+amendment) · reg-recovery 0c4f7579 (×2: pack+addendum) · edge e8a1aa5f

## Follow-ups (non-blocking, report-only)
1. Add FAILED to terminal_state parametrize (tests/unit/test_ensure_deferred_insert_on_missing.py:285-290) — 1 line.
2. Pack script `set -e` FAIL-path quirk on older packs (RESULT: FAIL unreachable; exit codes still correct) — noted by two workers; fix opportunistically when packs are next touched.
3. Dev's 9 base failures: none appeared in-scope; no attribution needed this gate.
