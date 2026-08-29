# Test Gate: Pattern (f) Orphan-ACTIVE-JobItem Recovery — feature/orphan-active-job-recovery @ ba39a40e

Date: 2026-08-29
Gate: Independent merge gate (inherited — predecessor hit revive-once bound; re-derived from zero)
Branch: `feature/orphan-active-job-recovery` @ `ba39a40e` (+1 test-infra commit `2d5f8a11` landed during gate)
Range: `b4dbfda2..ba39a40e` (9 commits; 10 files: 5 production M, 4 test A, 1 doc M)
Batch: (1) Pattern (f) orphan-ACTIVE-JobItem recovery — f1 active+NO-Task→DEAD, f2 active+COMPLETED-Task→DONE, guards/grace/mid-mint; (2) child_still_running_defer bus-emit fix (incident 02fb2e01); (3) observer restart-anomaly diagnostics (9 tests, documented-not-fixed); (4) agents/worker/rule.md Cardinal; (5) docs/stability-backlog.md +48.
Worker instances: 24 dispatches, ≤3 concurrent (QueuePool lesson held)

## VERDICT: ✅ PASS — CLEARED FOR MERGE to `latest`

Zero new failures across all 15 regression packs. Zero production defects found by any behavioral probe. Kill-path guards proven not to leak via real-DB scenarios + per-leg mutation. Residuals adjudicated 🟠 non-blocking with a paired ~3-LOC fix recommendation.

---

## 1. Regression (15/15 packs green, baseline-exact or exact-growth)

| Pack | Result | Baseline | Delta |
|---|---|---|---|
| job_queue_unit_test | 1563P/0F/38S (36s) | 1532P/0F/38S @ b1159eca | +31 = exactly the 3 new tests/job_queue files (19+3+9); predecessor's branch-local 1563 number independently reproduced |
| claim_guard_locks_unit_test | 178P/0F (2.2s) | 178P | exact |
| concurrency_atomic_unit_test (ensure-Critical) | 98P/0F/74S (8.4s) | 98P/74S | exact |
| core_unit_test (manager blast radius) | 713P/41F/0S (27s) | 713P/41F | **all 41 quarantine-matched, 0 unmatched** (38 SQLite-migration 20260714_000001 + 2 agents_api drift + 1 migration_api downstream) |
| api_unit_test | 213P/0F/8S (12.6s) | 213P/8S | exact — drift-loop wiring in daemon/api.py caused no API-surface drift |
| job_queue_tools_unit_test | 80P/0F/0-des (2.3s) | 80P | exact, no quarantine drift |
| child_reports_unit_test | 15P/0F (1.2s) | 15/15 | exact — defer-emit additions did not regress normal WAITING_CHILDREN path |
| waiting_children_watchdog_unit_test | 47P/0F (1.5s) | 47/47 | exact |
| wedge_fix_suites_unit_test | 78P/0F (0.8s) | 78/78 | exact — seam invariants hold under Pattern (f) |
| turn_transitions_reconciler_unit_test | 48P/0F +1-des (1.5s) | 48P | exact (deselect = pre-existing property-test quarantine in pack header) |
| stability_quick_wins_2_suites_unit_test | 15P/0F (1.8s) | 15/15 | exact — no bus-fire/notify interference |
| reconciler_paused_race_unit_test | 8P/0F (1.0s) | 8/8 | exact — observer failed_at chain intact under new diagnostics |
| completion_regression_test | 97P/37S @ ba39a40e → 96P/37S/1-des @ 2d5f8a11 (2.2-2.5s) | 97P/37S | see §5 flake; TestOrphanSweep 4/4 green (direct signal for defer fix) |
| **orphan_active_job_recovery_suites_unit_test (NEW)** | 39P/0F/0S (1.4s @ 2d5f8a11) | 39 expected | exact — 19+3+9+8, incl. the 8 defer tests with NO other pack coverage |
| (recon static) dev.sh `--timeout-graceful-shutdown 10` | present @ :102 | — | ensure.md Core static ✓ |

## 2. Kill-Path Matrix (the 3 council criticals, real scenarios) — PASS 5/5
Probe: `test/packs/pattern_f_killpath_matrix_test.py` (real JobRecoveryService + real repos, file-backed SQLite; only get_dependency_bus boundary stub; per-leg patches DELIBERATE mutations)

- **(a) PAUSED job past grace → stays ACTIVE** (`orphan_active_skipped_paused` :1949); resume-works leg: PAUSED→PENDING transition succeeds after sweep (rowcount=1) — sweep left it resumable. ✅
- **(b) FAILED/CANCELLED task + live retry child (fresh work_id, same instance) → stays ACTIVE** (`orphan_active_skipped_retry_child_live` :2012), lock HELD; after retry child completes → boundary finalizes via real `JobQueueService._finalize_terminal`: terminal_reason 'failed'/'cancelled', failed_at stamped for FAILED **only** (production semantic, repository.py:1606-1607 — FAILED-only atomic_retry gate), lock RELEASED, NO_RETRY. Both variants. ✅
- **(c) healthy waiting_children parent mid-wait → f2 does NOT finalize, PER-LEG MUTATION:**
  | Leg | Unmutated | Mutated (permissive) |
  |---|---|---|
  | L1 bus_pending (:3133) | skip `orphan_active_skipped_bus_pending` | f2 DONE fires — leg load-bearing |
  | L2 pending_instance_tasks (:3188) | skip `..._pending_instance_tasks` | f2 DONE fires |
  | L3 completed_at 60s floor (:3314, const :79) | skip `..._age_floor` | f2 DONE fires |
  Each scenario constructed so ONLY that leg blocks → every leg proven load-bearing, scenarios lethal, not vacuously safe. ✅
- **(d) genuine restart-orphan** (no Task, >900s grace `min_orphan_age_seconds`, instance.created_at < threshold mid-mint conjunct :2419-2452) → DEAD (`orphan_active_no_task_dead` :2475) + lock row GONE. Negative: fresh instance (mid-mint) → stays ACTIVE (grace skip, W1 wording). ✅
- **(e) f2 lock release on c=1 queue** → A DONE + lock released → JobItem B enqueued, real `try_acquire_slot` succeeds — no wedge. ✅

## 3. Bus-Emit Fix (02fb2e01) — PASS 3/3
Probe: `test/packs/defer_bus_emit_probe_test.py` (real ChildReportsService defer path, real DependencyWatcherRepository, file-backed SQLite)
- P1 exactly-once called-twice via real dispatch path: 1st dispatch PENDING→FIRED (fired_at+enqueued_at set); 2nd dispatch: zero state change, `transition_state` called 0 times; direct guard on FIRED row returns False, no fired_at overwrite. ✅
- P2 legitimate defer: SSE `waiting_children` preserved; CompletionRegistry.complete NOT called; lifecycle registry/hooks NOT dispatched; watcher legitimately FIRED; count_pending_for_target(parent)==0. (Spec nuance reconciled: the defer firing both emits IS the fix — asserted the real invariant: no premature completion semantics.) ✅
- P3 incident replay (multi-turn shape): pre-dispatch count_pending_for_target(parent)==1 → corrective emit (`child_reports.py:3461` → guarded UPDATE repository.py:647) → watcher FIRED, count==0 → parent gate released (mechanism code-cited: JobFeedbackObserver `_bus_count_pending_for_target_sync` :344-424 reads count_pending_for_target :429-473). ✅

## 4. E2E Capstone (092c5ed3 class) — PASS 4/4
Probe: `test/packs/pattern_f_capstone_test.py` (real `reconcile_drift_states` :1402, real repos/services; MagicMock manager facade + deterministic empty-bus only)
- SEED: 2 projects, FIFO c=2 + DEFER c=1 queues; zombie z1 (ACTIVE, no Task, inst 3600s old), zombie z2 (ACTIVE, COMPLETED Task 120s, JobWatcher row), defer job C QUEUED, healthy control h (ACTIVE, PENDING Task, lock held).
- Pre-sweep wedge (honest correction): **defer idle gate** (`job_processor.py:212` `_defer_idle_check` → `count_active_jobs_in_non_defer_queues`==2), NOT the lock — C held no lock.
- ONE real sweep: f1 log + f2 log both fire; `reconciled=2, details=3`.
- POST: z1 `dead` + lock gone; z2 `done` + lock gone + watcher row GONE + task still COMPLETED; enqueue_message notify called; **healthy h untouched** (active+pending+lock intact); idle-gate count 2→0; **C admitted via real `try_acquire_slot`** — wedge resolved by real claim.
- By-design observations (🟢 follow-ups, not defects): `completed_at`/`error_message` kwargs silently stripped by `_REMOVED_JOB_COLUMNS` (repository.py:47-54/:1260-1268) — f1/f2 dead-writes worth cleaning; `terminal_reason` on f1/f2 DEAD/DONE deferred to finalize_active_to_done seam (documented :2755-2764; terminal boundary path DOES stamp terminal_reason).

## 5. Red-Green (worktree A/B, resolution-proven) — 13/16 CONFIRMED + 1 by-design + 2 characterization
- R1 @ 97103462 (pre-dee03665): **all 10 criticals RED** (f1 over-broad `orphan_active_no_task_dead` matching PAUSED/FAILED/CANCELLED/retry-child; missing `get_dependency_bus` API for lock-release + f2 gate; missing mid-mint guard) + **W1 mid-mint RED**. All 11 GREEN @ fix. ✅
- R3 @ c16b21e8 (pre-ba39a40e): **2 lineage-conjunct tests RED** (`orphan_active_failed_terminal` fired on live-retry-child parents) → GREEN @ fix; 3rd test green-by-design (structural inverse companion). ✅
- R2 @ dee03665 (pre-c16b21e8): **2 W4 tests GREEN at pre-fix** — `git diff dee03665..c16b21e8` is TEST-ONLY (+194 test lines, zero production). The `WHERE state='PENDING'` guard already existed (repository.py:608; landed earlier in the batch via ca9263c2's emit work). **W4 "fix" is characterization coverage, not behavior change** — exactly-once is instead proven by gate probe P1 (real-DB, called-twice). 🟢 commit-message honesty follow-up.

## 6. Mock Fidelity — CLEAN
All 4 branch test files + 2 tester probes audited: boundary mocks only (manager facade, get_dependency_bus singleton, LLM-adjacent); every production-code-effect assertion binds REAL DB rows (admission_state, lock rows, terminal_reason, failed_at, watcher states); observer diagnostics use real EventBus + real observer (7 file-backed-SQLite unit + 2 real-observer integration); 1 honestly-documented characterization test paired with a real-DB guard companion; probes' only patches = deliberate scenario-(c) mutations + deterministic bus stub. StaticPool-in-conftest acknowledged safe (single-threaded async). 4 info-level follow-ups (labeling/docstring).

## 7. Residual Adjudication (merge record)
- **(a) PAUSED retry children not caught — 🟠 important, NON-blocking.** `has_inflight_task` (repository.py:468-541) hard-wired PENDING+RUNNING; sister `has_instance_busy` (:543) includes PAUSED. Required sequence: force_cancel_and_schedule_retry → claim → RUNNING → pause_instance_cascade → PAUSED (pause selects RUNNING only, instance_lifecycle.py:4172-4178) → sweep finalizes parent → stuck retry child (resume W1 guard :4739-4744 excludes it; claim handles PENDING only; terminate-only recovery). Narrow window, recovery exists.
- **(b) lineage-lookup error fails safe to FINALIZE — 🟠 important, NON-blocking.** `except Exception` @ job_recovery_service.py:3296-3312 returns False → finalize. Inconsistent with sister bus gate (:3138-3153 "FAIL-SAFE: skip... Never guess"). Chain: transient DB error → over-finalize → can trigger (a)'s deadlock. Low frequency (single indexed SELECT).
- **Paired fix (~3 LOC):** swap `has_inflight_task`→`has_instance_busy` @ :3297 + flip exception-path return True @ :3312; add PAUSED-retry-child regression (extend killpath probe) + transient-error simulation. **Ship immediately post-merge.**

## 8. Flake Quarantine (this gate)
`tests/test_dependency_bus.py::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert` — CONFIRMED FLAKY 3P/2F @ ba39a40e (byte-identical signature: InvalidRequestError session.refresh @ repository.py:132 via asyncio.to_thread under 4-thread gather vs StaticPool). Test-infra, NOT production, NOT this branch. QUARANTINE.md row added; pack deselect committed **2d5f8a11** (96P/37S/1-des re-verified PASS).

## 9. ensure.md Validation
- **Core Critical 4/4**: no regressions in changed packs (§1, all PASS); deadlock/concurrency integrity (concurrency pack 98P/74S exact); no sync DB on event loop (same pack, thread-identity); dev.sh graceful-shutdown flag (recon grep ✓).
- **Core Important 1/1 + 1 N/A-scoped**: original deadlock scenario covered (concurrency pack); async-await callers requirement N/A — named functions not in this change set.
- **Nice-to-have**: dead-code note — f1/f2 `completed_at`/`error_message` dead-writes (§4, 🟢).
- **Release Gate NOT TRIGGERED**: scoped recovery-feature change (no architecture refactor, no API surface change); engine-level capstone covers the end-to-end recovery path without live-LLM E2E (reconciler-wedge-fix precedent).
- **Contradictions found: NONE** (no ensure.md improvement notices).

## 10. Scope Decision
Scoped gate per blast radius (job_recovery/child_reports/job_queue/manager/watchdog): 15 packs + 3 behavioral probes + red-green + mockfid + adjudication. Full suite NOT warranted (10-file scoped change; quarantine families elsewhere untouched and stable). Skipped: frontend, sources, blueprint, LLM-HA packs (zero overlap with change set).

## 11. Follow-ups (none blocking)
- 🟠 Paired residual fix (~3 LOC) — §7, ship immediately post-merge
- 🟢 c16b21e8 commit-message honesty ("fix" → test-only characterization); add pairing comment in test
- 🟢 f1/f2 dead-writes (completed_at/error_message stripped) + terminal_reason seam doc note
- 🟢 Mockfid info items: characterization labeling, LockManagerForJQ docstring, StaticPool disclosure, _BusStub dedupe
- 🟢 Capstone negative-control pattern is reusable for future sweep gates

## 12. Code Changes (this gate)
- `2d5f8a11` — test: quarantine-deselect flaky dependency_bus test (test-infra only, 1 file, committed)
- Untracked for giter to commit with merge: 4 new pack scripts (orphan_active_job_recovery_suites, pattern_f_killpath_matrix, defer_bus_emit_probe, pattern_f_capstone — .py + .sh each) + .agents/tester/ updates (PACKS.md, QUARANTINE.md, MOCK_TESTS.md, RESULTS/, LESSONS/)

### Overall Status
- Regression: ✅ PASS (15/15, 0 new failures)
- Kill-path matrix: ✅ PASS 5/5 (legs mutation-pinned)
- Bus-emit fix: ✅ PASS 3/3
- E2E capstone: ✅ PASS 4/4
- Red-green: ✅ 13/16 confirmed (+1 by-design, +2 characterization w/ independent real-DB proof)
- Mock fidelity: ✅ CLEAN
- Residuals: 🟠🟠 adjudicated non-blocking, paired fix specified
- ensure.md: ✅ Core 4/4 (Release Gate not triggered)
- **Testing Complete: ✅ READY — cleared for merge (incl. owed b4dbfda2 push per handoff)**
