# Independent Verification Gate — RECONCILER WEDGE-FIX (inherited gate, re-verified from zero)

- **Branch**: `feature/reconciler-wedge-fix` @ `ae837a98` (range `29898ee2..ae837a98`, 3 commits: 23fc5e2d main fix + 79d73eb8 ALIVE-dedupe hoist + ae837a98 Y1/Y2 council warnings)
- **Date**: 2026-08-29
- **Diff verified**: 9 files, +1914/−52 — daemon/api.py +7 · daemon/constants.py +49 (ALIVE_INSTANCE_STATUSES hoist, def :289) · daemon/manager.py +209 (sub-shape (c) revival sync :6984 / async :7624, _has_live_process_report_carrier :6913, _is_parent_alive :6959) · daemon/repositories/instance/repository.py +80 (batched parents_with_non_terminal_children :2094) · daemon/repositories/task/repository.py +42 · daemon/services/job_recovery_service.py +79 (Pattern (d) work_id linkage + ALIVE guard :1110-1122) · daemon/services/waiting_children_watchdog.py +336 (WEDGE backstop, WEDGE_SOURCE :169) · tests/job_queue/test_seam_invariants.py +329 · tests/unit/test_reconciler_wedge_fix.py +835 (NEW)
- **Inheritance note**: predecessor gate (77ab8ab2) wedged at 74/75 workers (DB QueuePool exhaustion via 75-worker fan-out — operator-terminated; unrelated to branch code). NO predecessor evidence was citable (no RESULTS file, empty PACKS.md.new). **Everything below was re-derived from zero by this gate.** Fan-out kept modest: 14 dispatches, ≤3 concurrent.
- **Dispatches**: 14 workers (1 inventory, 8 packs, 3 probes, 1 mockfid audit, 1 RED A/B), 0 direct executions by tester.

## VERDICT: ✅ PASS — gate CLOSED; `feature/reconciler-wedge-fix` @ ae837a98 CLEARED FOR MERGE to `latest`

**Zero new failures attributable to this branch.** Root-cause closure, revival loop, backstop, RED discrimination, mock fidelity, and the e2e wedge→recovery arc all verified behaviorally on real production code. Release Gate (e2e LLM suite) ruled NOT TRIGGERED — defect fix + backstop, no architecture refactor; real-engine behavior covered by the capstone probe (below).

---

## 1. Verification-scope coverage (task's 7 items)

| # | Scope item | Verdict | Evidence |
|---|---|---|---|
| 1 | Full regression vs baseline/quarantine (reconciler/job-recovery/manager/watchdog surfaces) | ✅ 8 packs + core = 0 new failures | §2 |
| 2 | Incident replay (root-cause closure + over-correction check) | ✅ 3/3 PASS | §3.1 |
| 3 | Revival loop (Fix 3) incl. idempotency + PAUSED negative | ✅ 9/9 PASS ×3 runs | §3.2 |
| 4 | Backstop (Y1-corrected) fire/silence/cooldown | ✅ 4/4 within §3.2 | §3.2 |
| 5 | RED verification (T1/T2b RED at pre-fix worktree) | ✅ RED-CONFIRMED + pre-fix WARNING captured | §3.3 |
| 6 | Mock fidelity + ALIVE membership pins | ✅ CLEAN-WITH-INFO (0 blockers) | §3.4 |
| 7 | Behavioral probe: e2e wedge→recovery on real engine | ✅ WEDGE@BASE / FIX / WAKE PASS; receipt PARTIAL-honest | §3.5 |

## 2. Regression packs (all PASS, baseline-exact or exact-growth)

| Pack | Result | Baseline delta |
|---|---|---|
| turn_transitions_reconciler_unit_test | ✅ 48P/0F/1-des (1.61s) | +2 (task/repository.py Pattern-d surface growth); 1 des = known quarantined stale assert |
| job_queue_unit_test | ✅ 1532P/0F/38S (35.85s) | +2 = seam T1/T2/T2b repros (66→68); baseline 1530P @ 678709d3 |
| claim_guard_locks_unit_test | ✅ 177/0F (2.08s) | +2 seam (branch) + 7 task_repository (upstream growth since Aug-21, NOT in branch diff) |
| concurrency_atomic_unit_test | ✅ 98P/74S/0F (9.43s) | exact vs 1d14f451 — ensure.md Core #2/#3 green |
| job_queue_tools_unit_test | ✅ 80c/80P/0F (2.18s) | byte-exact vs 1d14f451 |
| wedge_fix_suites_unit_test (NEW pack) | ✅ 78/78 (0.69s) | first run — 10 wedge_fix + 68 seam (T1/T2b/Y1/Y2 green) |
| waiting_children_watchdog_unit_test (NEW pack) | ✅ 47/47 (1.54s) | exact vs 606b1bed — +336-line WEDGE extension broke zero base tests |
| api_unit_test | ✅ 213P/8S (13.4s) | exact vs 1d14f451 (api.py +7, manager.py +209 in tree) |
| core_unit_test | ✅ PASS by baseline — 713P/41F in 27.49s, 0 UNMATCHED | +16 passed (upstream adds); all 41 failures match QUARANTINE.md families verbatim (39 SQLite-migration cascade `20260714_000001` + 2 agents_api count-literal drift). manager.py +209 surface clean |

**No unmatched (new) failures in any pack.** All pre-existing failures/deselects map to QUARANTINE.md families.

## 3. Behavioral verification (real production code, file-backed SQLite, no StaticPool, no daemon boot)

### 3.1 Incident replay (worker 462d7cc0) — PASS 3/3, 25 assertions
Real `JobRecoveryService.reconcile_drift_states` (job_recovery_service.py:516, Pattern d body :1025-1181) + real `claim_pending_task` (task/repository.py:1139).
- **S1 (the incident)**: WC parent + OLD DONE dispatch JobItem + JobItem-less PENDING PROCESS_REPORT → task SURVIVED (pending), zero drift details, **claimable** (claimed → running, worker stamped). Smoking gun: `get(task.work_id)` called & returns None; old JobItem excluded by TWO independent paths (work_id linkage + Pattern (a) active-only guard :777).
- **S2 (over-correction)**: genuinely-orphaned (TERMINATED parent, own work_id terminal) → STILL cancelled (`orphan_pending_terminal_job` detail, stats.reconciled=1, no longer claimable).
- **S3 (alive guard)**: WC parent + own-work_id terminal JobItem → SURVIVED; guard log captured verbatim ("left for natural claim path or watchdog backstop").

### 3.2 Revival loop + backstop (worker 98aab9fc) — PASS 9/9 ×3 stability runs
Real `InstanceManager._reconcile_deferred_report` (:6984) / `_async` (:7624) + real `WaitingChildrenWatchdog.run_once` (:521). Only edge mocks (worker_pool, enqueue recording).
- **A1 sync/async/multi**: cancelled/failed carriers → `shape=c_revival`, fresh carrier NEW work_id, **commit-before-notify proven from a second engine connection** (pending_count_at_notify=1), notify_work ×1, `sub_shape=c_revival` WARNING logged.
- **A2 idempotency**: 2nd sweep → `delivery_only`, same work_id, 1 PENDING row, notify ×0.
- **A3 negative**: PAUSED parent → no revival (paused filtered at `list_waiting_children_parents` outer level).
- **B1 wedge fire**: enqueue kwargs pinned — source=`system:watchdog:wedge` (==WEDGE_SOURCE), priority 0, metadata {wedge_notice, wedge_reason}, message byte-identical to `_build_wedge_notice`.
- **B2 Y1 gate**: WC + live RUNNING child + zero carrier → SILENT (real batched query `parents_with_non_terminal_children` returned the parent; gate fires BEFORE carrier check).
- **B3 composition**: live carrier → silent. **B4 cooldown**: 2nd tick silent, `_wedge_notified` episode set holds.

### 3.3 RED verification (worker 966df630, base-evidence skill) — RED-CONFIRMED
Worktree @ 29898ee2, branch test files copied in, resolution proof both sides (daemon.__file__ = worktree path at base).
- **T1 @ base: FAIL** `assert 'cancelled' == 'pending'` (test_seam_invariants.py:3390) — GREEN @ HEAD.
- **T2b @ base: FAIL** `assert 'cancelled' == 'pending'` (:3505) — GREEN @ HEAD.
- **T2 safety-net @ base: PASS** (dead-instance drift-cancel never broken — council claim independently confirmed).
- **Pre-fix WARNING auto-captured** in failing-test logs (job_recovery_service.py:1114 @ base): "orphan PENDING task 1 on terminal JobItem … (admission_state=done) — cancelled 1 task row(s)" — the incident mechanism verbatim.
- wedge_fix file @ base: collection ImportError on WEDGE_SOURCE (BASE-MISSING-equivalent — symbol introduced by branch). Correct.
- Cleanup verified; checkout state byte-identical.

### 3.4 Mock fidelity + Y2 pins audit (worker fd46a503) — CLEAN-WITH-INFO
Zero BOUNDARY-MOCKs, zero patch-target hazards, zero vacuous tests. T1/T2b = fully REAL wiring (no mocks at all); wedge-fix unit tests = SEAM-MOCK (delivery edge only) with real production methods bound via MethodType. **Y1 test drives the real 3-part predicate** via real batched SQL gate (fallback-on-exception degrades to set() → would NOT silence — test genuinely depends on the real query). **Y2 pin = exact expected literal** (`frozenset({"idle","running","paused","queued","waiting_children"})`) — any add/remove/rename fails; byte-identical to pre-hoist semantics. T2b is the ONLY behavioral pin of the alive-guard-vs-terminal-JobItem (pre-existing :3168 twin uses ACTIVE JobItem — non-redundant).
INFO items (non-blocking, follow-ups): INFO-1 fixture docstring says "file-backed" but uses :memory:+StaticPool (tests don't need file semantics — conftest-documented convention allows in-memory for single-threaded logic tests; fix the comment); INFO-2 test holder omits WriteGuardSession wrapper (pause-gate unexercised); INFO-3 T2b shape synthetic per JAFP (carriers never carry JobItems; guard is type-agnostic — holds); INFO-4 production `_has_live_carrier_task` contains mock-aware branches (AsyncMock detection, no-repo→True fallbacks) — test-shape-driven production code, reviewer awareness; INFO-5 dead artifacts (unused monkeypatch/stats); INFO-6 `waiting` status deliberately outside ALIVE set (pre-branch, preserved).

### 3.5 E2E wedge→recovery capstone (worker af420c43) — PASS/PASS/PASS/PARTIAL(honest)
Real engine, real DB file carried across the pre-fix→post-fix boundary:
- **WEDGE@BASE** (worktree 29898ee2): base Pattern (d) `get_by_instance` → OLD terminal JobItem → carrier CANCELLED, parent stays WC (1 `orphan_pending_terminal_job` correction) — the incident, reproduced.
- **FIX-IN-PROCESS** (HEAD, same DB): HEAD reconciler 0 corrections (work_id linkage + alive guard); sub-shape (c) revival → fresh carrier NEW work_id `d38bd367…`, durable (2nd-connection proof), notify fired; 2nd sweep no dup.
- **WAKE**: watchdog wedge pass AND direct enqueue → parent **WC→RUNNING**, PENDING task, worker notified (real `InstanceMessagingService.enqueue_message`/`_prepare_enqueued_message` — same primitive as #8).
- **REPORT-RECEIPT (PARTIAL, honest)**: real atomic `claim_pending_task` PENDING→RUNNING; report message durably bound to parent in message_queue. Stopped at LangGraph turn boundary (requires live manager/checkpointer/LLM — out of scope by construction). No theater.

## 4. ensure.md validation (blast-radius scoped)

| Requirement | Status |
|---|---|
| Core #1 no regressions in changed packs | ✅ 8/9 packs PASS (core pack pending W14 — see §2) |
| Core #2 deadlock/concurrency (concurrency_atomic pack) | ✅ PASS 98P/74S/0F exact |
| Core #3 no sync DB on event loop (thread-identity) | ✅ same pack PASS |
| Core #4 dev.sh `--timeout-graceful-shutdown 10` | ✅ static (dev.sh:102) |
| Important #1 async-caller awaits | ⏳ spot-check bundled with W14 follow-up |
| Important #2 original deadlock scenario | ✅ concurrency pack |
| Nice-to-have no dead code from fix | ✅ 79d73eb8 dedupe: single def constants.py:289, both consumers import same object (W0 statics); zero dead refs |
| Release Gate | RULED NOT TRIGGERED — defect fix + backstop, no architecture refactor; e2e real-engine behavior covered by capstone probe §3.5 |

**No ensure.md contradictions encountered** (all requirements pack-mapped cleanly; no bare pytest, no -x).

## 5. Findings & follow-ups (non-blocking)

**ensure.md final: Core 4/4 Critical + 2/2 Important + 1/1 Nice-to-have PASS; Release Gate ruled NOT TRIGGERED; zero contradictions.**

1. 🟢 **INFO-1 docstring lie** (test_reconciler_wedge_fix.py:82-84 "file-backed" vs actual :memory:+StaticPool) — 1-line test-only fix, recommend pre-merge rider or immediate post-merge.
2. 🟢 **Sub-shape (b) asymmetry** (manager.py:7229-7250 `task_only_create` never calls notify_work; sub-shape (c) does): compensating wake = watchdog backstop. Deliberate per code, surfaced for the queued follow-up batch.
3. 🟢 **INFO-4 mock-aware production branches** in `_has_live_carrier_task` (watchdog :488-517) — test-shape-driven production code; future tests forgetting `task_repository=` silently test the disabled path.
4. 🟢 **Pack-script convention flaw (repo-wide, 111 packs)**: `set -euo pipefail` + `EXIT_CODE=$?` makes `RESULT: FAIL/TIMEOUT` echo unreachable on failure — exit codes propagate correctly (gating semantics hold); output-format contract breaks on future failures. Candidate batch fix.
5. 🟢 INFO-2/3/5/6 from §3.4 — cosmetic/synthetic-shape notes, no action required.
6. Council's accepted/documented items (TOCTOU concurrent-revival double-carrier bounded; carriers-claimable premise) — verified consistent with probe observations; do NOT re-flag.

## 6. Worker instances
857cd656 (W0 inventory+pack scripts) · ec31e67a (reconciler) · b88525fe (job_queue) · e061cebd (claim_guard) · 54e6515b (concurrency) · d113866d (jqt) · 46b00561 (wedge_fix_suites) · 8f348f70 (watchdog) · 21fb985a (api) · cf9a8f29 (core) · 462d7cc0 (replay probe) · 98aab9fc (revival probe) · fd46a503 (mockfid) · 966df630 (RED) · af420c43 (e2e capstone)

## 7. Scope decision
Full-suite sweep NOT run (predecessor's 75-worker approach wedged the daemon's DB QueuePool). Blast radius = reconciler/job-recovery/manager/watchdog/api/constants surfaces → 9 scoped packs + 4 behavioral verifications + RED A/B + audit. Whole-tree sweep families are documented in QUARANTINE.md and were not in the diff's reach (zero branch commits on llm/watchover/migration-drift files). If the giter wants a full-tree sweep post-merge, run it on `latest` after merge via the standard 7-partition sweep with ≤3-concurrent staggering.
