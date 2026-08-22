# Phase 3 Testing Report: pause-report-recovery (final validation)

**Date:** 2026-08-20
**Branch:** `feature/pause-report-recovery`
**SHAs:** base `6bb99d5f` → diff tip `73bfe0ed` (+5 test-only commits → `95edd680`) → **repair `b9b2929a` (final HEAD, re-run target)**
**Worker instances:** 108115f3 (MB tests), ed8513fe (3.5 matrix), ac4ba2f6 (3.9/orphan), 2fb92194 (3.7), 05b30f3a (3.6/wiring), 4485c900/2f05214b/99d74e1d/1a185527 (unit chunks), d63284ef (Core/gated), e914d73f (attribution + boot characterization), c343c756 (e2e attempt 1), 518d28f1 (boot verifier), 17897b5b (terminated — superseded), 14c44395 (boot re-verify), 9c933383/20e802f2/547247d2/c53c3001 (unit re-runs), ecf6e405 (PG+integration chain), c1466843 (Core/gated re-run), 318c9f8e (marker attribution), 46146c00 (e2e re-run)

## Summary

| Metric | Value |
|---|---|
| Unit suite (re-run, b9b2929a) | 5574 passed / 34 failed (33 pre-existing + 1 flaky-quarantined) / 35 skipped + 4 pre-existing-at-base context7 errors (unmasked by repair) |
| PG suite (tests/postgres/) | **216 passed / 0 failed** / 33 skipped / 9 xfail (documented) |
| Integration suite | 200 passed / 29 failed — **all 29 pre-existing** (~20 share SQLite-incompatible migration `20260714_000001` root cause) |
| Merge blockers MB-1/MB-2 | ✅ GREEN on real PG (re-confirmed on b9b2929a) |
| ensure.md Core | ✅ 8/8 PASS (Critical 4/4, Important 2/2 incl. gated modules) |
| ensure.md Release Gate | ✅ GREEN after quarantine (4/4 workflow items PASS on live PG daemon; 3 stale-assert failures quarantined) |
| Boot regression | 🔴→✅ found at 95edd680 (daemon could not start), **fixed and verified at b9b2929a** |
| Quarantined this session | 4 (3 stale e2e asserts + 1 flaky unit test) |
| Production bugs found (not fixed, per brief) | 4 (1 PG-HIGH, 1 SQLite-HIGH, 2 MEDIUM) |

## Scope Decision

Full suite + mandatory ensure.md e2e run — warranted: critical prod bugfix touching ALL FIVE gated modules (claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks) per project rule and phase3-plan.md task 3.8.

## MERGE BLOCKERS — GREEN

- **MB-1** (`tests/postgres/test_report_deferred_savepoint_pg.py`, commit a7897fd6): real-PG SAVEPOINT path — nested.rollback() discards ONLY the injection INSERT; outer commit preserves child COMPLETED + completion_report + PROCESS_REPORT task; non-obligation IntegrityError re-raises; 73bfe0ed broadened-rollback (non-IntegrityError) covered. PG proof: engine `postgresql+psycopg://…/ensemble_test`, `current_database()=ensemble_test`, real constraint-name emission in `str(exc.orig)`.
- **MB-2** (`tests/postgres/test_obligation_triple_discriminator_pg.py`, commit a7897fd6): PG branch of `_is_obligation_triple_integrity_error` — real PG duplicate-triple IntegrityError → True (constraint-name match); real PG FK violation → False + re-raises; PG message-format invariant locked.
- Both re-confirmed green in the full PG suite sweep on b9b2929a (216/0).

## Per-Task Results (phase3-plan.md)

| Task | Result | Evidence |
|---|---|---|
| Full suite PG-primary | PASS (0 non-pre-existing failures on b9b2929a) | unit chunks ×2 runs; PG 216/0; integration 29F all pre-existing |
| MB-1 | PASS | 9/9 tests, real PG |
| MB-2 | PASS | 9/9 tests (5 MB-2 + 4 overlap), real PG |
| 3.5 matrix (11 pairings) | PASS | 14 tests; exactly-once ×11; ordering both directions; C-DiD de-vacuous reach counters (commit 1926e500) |
| 3.9(a) FM-11 cancel-at-await | PASS — shield sufficient, **Option B NOT triggered** | marker scheduled before CancelledError escapes; pre-try hoist window is documented escape lane → backstop |
| 3.9(c) W12 crash-mid-shield | PASS | commit-then-ConnectionError: marker intact, 0 orphan artifacts, pipeline unaffected, backstop one-cycle recovery, no double-recovery |
| ORPHAN lane (item 5) | PASS | revive-and-deliver AND structured-disposition both proven; exhaustiveness anti-regression; never silent |
| 3.6 sweep matrix | PASS on SQLite; **PG-gap documented (Lane-2 bug)** | 9 PG tests pass; 6 xfail + 1 bug-pin document Lane-2 breakage on PG |
| 3.7 migration sub-cases | PASS | 33P+3xfail: ensure-idempotent, C1 raise, W3 dedup (MIN(injection_id) survivor), W8 order, SQLite parity, C4 14-consumer NULL audit |
| Wiring-failure tolerance | PASS | boot tolerant; WARNING logged; 503-not-500 |
| Gated modules ×5 | PASS | claim 66/0; turns 39/0; mirror 23/0; processor 39/0(+4F pre-existing); locks 79/0/17s |
| ensure.md e2e | PASS after quarantine | 4/4 workflows on live PG daemon; 3 stale asserts quarantined |

## THE BOOT REGRESSION (record: found at 95edd680, fixed at b9b2929a)

Commit `1d5144f4` spliced 4 methods into the middle of `InstanceManager.__init__` (anchor: the `_write_guard` line), truncating init to 33 lines and orphaning ~858 lines (engine, repos, 7 services, `_completion_registry`) as after-yield statements of `_session_scope`. Daemon lifespan crashed at `manager.py:2049` (`AttributeError '_completion_registry'`). Triple-confirmed (suite attribution via base worktree; e2e worker's 3 failed relaunches; falsify-first verifier with parent-commit clean boot). Explains the prior Friday daemon hang on 8079 (`--reload` picked up broken code). Why reviews missed it: phase-3 unit tests mock the manager + monkeypatch `_session_scope` — the truncated init was structurally invisible; only `test_phase4_manager_decomposition` (source-text assert) caught it. **Fix b9b2929a verified**: init byte-identical to `1d5144f4^`, live PG boot `INITIALIZE OK`, TestBootSmokeRegression 3/3 (fails on broken parent), phase4 detector 73/73, re-run deltas zero-genuine-new across all suites.

## Bugs Found (reproduce + report; NOT fixed, per brief)

1. 🔴 **Lane-2 no-row backstop broken on PostgreSQL** — `daemon/repositories/report_injection/repository.py:712` `select(DependencyWatcher.watch_id)` (unaliased) joined against alias `dw` → PG `UndefinedTable: missing FROM-clause entry for table "dw"`; SQLite's permissive parser accepts it (false-green). Live-confirmed: daemon sweep logs the error every 5 min. Defeats Option C backstop on PG. Fix: `select(dw.watch_id)`. Pinned by bug-pin test + 6 xfails (a74a72f3). 3.6 acceptance "matrix all-green on SQLite AND PG" NOT met on PG.
2. 🟠 **BUG-A: legacy-SQLite migration collision** — `20260819_000001` lines 88–91 column-swap collides with `ix_report_injections_report_msg_state`; MigrationRunner silently skips + records APPLIED → `report_message_id` NOT NULL forever + stray `_new` column → `ensure_deferred` INSERTs fail on legacy SQLite DBs. Fresh DBs unaffected.
3. 🟡 **BUG-1: `claim_for_task_delivery(None)`** with NULL-keyed DEFERRED row returns `already_delivered` instead of `missing` (C4 contract).
4. 🟡 **BUG-2: `claim_for_task_delivery(None)`** with NULL-keyed PENDING row commits TASK_DELIVERED then raises TypeError (`report_message_id[:8]` on None) at repository.py:1004.

## Pre-Existing Failure Analysis (34 unit + 29 integration + 3 e2e)

- **Unit 33/34 PRE-EXISTING** (base-worktree 6bb99d5f empirical run): devops ×2, api_router ×1, coder ×6, hide_kb ×5, job_processor_status_guard ×4, phase5_jobs_router ×1, question_deferred ×1, validate_agent_id ×1, wanderer ×2, webfetch ×2, job_queue_proxy_phase1 ×8. The 1 CAUSED-BY-DIFF (phase4 detector) is FIXED by b9b2929a.
- **Integration 29/29 pre-existing**: ~20 share the SQLite-incompatible `20260714_000001` migration (PG-only DROP CONSTRAINT syntax) — the same issue named in leader note (a); remainder are stale semantics from pre-base commits (24577497, c171a289).
- **E2E 3/3 pre-existing**: c171a289 (2026-08-12, pre-base) PAUSED→PENDING shift; tests unchanged since 2026-08-01. All quarantined per ensure.md.
- Flaky: `test_concurrent_duplicate_marker_absorbed` quarantined (22-run budget).

## ensure.md Validation Results

**Core (all PASS):** no regressions in changed packs (all phase3 packs green) ✅; concurrency_atomic 91P/74S/0F exact-baseline ✅; sync-DB-on-asyncio thread-identity ✅; dev.sh `--timeout-graceful-shutdown 10` ✅; async-caller audit 7/7 awaited ✅; deadlock scenario ✅.
**Release Gate (GREEN after quarantine):** full non-integration suite green excl. quarantined ✅; happy path ✅; pause-after-spawn-resume ✅; terminate-revive ✅; 3-level cascade ✅ (all on live PG daemon, health=200, queue hygiene clean).
**Quarantine-aware per ensure.md:** 3 stale e2e asserts + 1 flaky unit test added to QUARANTINE.md (rising pre-existing debt now visible: 5 active entries).

## ensure.md Improvement Notices

- ⚠️ Release-gate validation strings embed raw pytest paths rather than pack references; both were run per pack discipline (one test per `timeout 320` command) — suggest converting the two `PYTEST_TIMEOUT=280 timeout 300 …` lines to pack form (e.g. `timeout 320 bash test/packs/e2e_workflows_ensure_test.sh -k <name>`).
- ⚠️ `hypothesis` and `pytest-timeout` are declared in pyproject but absent from the venv (uv-managed); `timeout=30` ini is inert — pack scripts rely solely on the shell wrapper. Suggest `uv pip install pytest-timeout hypothesis` and/or a venv check in ensure.md prerequisites.

## Documentation Updated

- [x] QUARANTINE.md — 4 new entries (3 e2e stale asserts, 1 flaky unit)
- [x] LESSONS/2026-08-20-pause-report-recovery-phase3-boot-splice.md — boot-splice regression + re-run + env lessons
- [x] RESULTS/2026-08-20-pause-report-recovery-phase3.md — this report
- [x] PACKS.md — summary line (below)
- [ ] rules/ensure.md — unchanged (user-owned)

## Verdict

### **SHIP** (conditional on the documented follow-ups below)

- The branch's own deliverables (marker pipeline, recovery service, router re-entry, sweep lanes, exactly-once) are **green on real PostgreSQL**, including both merge blockers, the 11-pairing race matrix, the FM-11/W12 defense-in-depth fixtures, and the full ensure.md gate.
- The one critical regression found during validation (boot splice) was **fixed (b9b2929a) and re-validated end-to-end** — the re-run is the operative result.
- **Must-fix before/shortly after merge (recommended):** Lane-2 PG bug (1-line fix, breaks the permanent no-row backstop on the primary DB) and BUG-A (legacy-SQLite migrations silently half-apply). Neither is a merge blocker per plan text, but both are correctness debts on the recovery feature this branch ships; the bug-pin tests will fail-when-fixed as designed.
- Nice-to-have: BUG-1/BUG-2 NULL-keyed claim contract; 3 quarantined stale e2e asserts; marker flake absorb-hardening.


---

## Postscript (2026-08-20, later): Review conditions + final re-confirmation

Test implementation review returned **APPROVE-WITH-CONDITIONS**; all 4 merge-gate conditions + doc fix landed as test-only/docs commits. Final chain: `b9b2929a` → `012b0f57` (developer: Lane-2 alias fix `select(dw.watch_id)` + regression tests + bug-pin removal) → `43f55744` (B-1) → `b3f25846` (G/C) → `a528c896` (docs) → `706c44a1` (D-1) → `b28216ec` (D-2).

| Condition | Resolution | Evidence |
|---|---|---|
| B-1 off-loop boot dispatch regression | `TestBootSweepDispatchShape` source-scan (anchor `boot_sweep_task = self._loop.create_task(`) | RED/GREEN proven via temporary sync-call revert; 14/14 file green |
| D-1 marker test quarantine violation | De-flaked: deterministic rendezvous (Task capture + gather) + NullPool file-backed SQLite; StaticPool/`:memory:` dead ends documented | 30/30 single + 5/5 + 5/5 file runs; QUARANTINE row deleted |
| D-2 e2e stale-assert base evidence | Option 1: base worktree `6bb99d5f` re-run — all 3 fail identically to HEAD (same asserts, same lines); tests byte-identical base↔HEAD; `c171a289` is ancestor of base | QUARANTINE rows base-evidenced, retry budget 2, remain quarantined as pre-existing |
| G/C dead xfail | Decorator removed; canary passes as plain test | `TestC4NullConsumerAudit` 13P + 2 strict xfails (BUG-1/2, expected) |
| Doc path fix | LESSONS Lane-2 reference corrected to `daemon/repositories/report_injection/repository.py:712` | grep-swept all tester docs |

**Formal re-confirmation on `b28216ec`** (PG `ensemble_test`, serial): full `tests/postgres/` suite **226 passed / 0 failed / 2 xfailed / 33 skipped (pre-existing CorrelationManager legacy)** in 25.93s. Formerly-xfailed Lane-2/C3 group (6 tests) now passes unmarked; `TestLane2QueryCompilationRegression` + `TestLane2PGRegressionEndToEnd` green (in `test_report_delivery_recovery_pg.py`); MB-1/MB-2 9/9; boot integration 14/14 incl. B-1. The `TestLane2PGBugPin` class was deleted by design inside `012b0f57` (pin obsolete post-fix). Final xfail ledger = BUG-1 + BUG-2 claim-contract documentation xfails only.

**Verdict unchanged: SHIP.** Follow-up register (post-merge, do NOT bundle): barrier-rendezvoused concurrent claim pairings; delivery-side-effect exactly-once asserts; Actor E real-binding; mirror-predicate production binding; real-boot PG test (initialize + setup_worker_pool); concurrent-PG-writer sweep via `pg_two_connections`; rename/refile race-matrix naming.
