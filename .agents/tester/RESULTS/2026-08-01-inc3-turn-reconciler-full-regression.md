# Test Report: Turn Reconciler Increment 3 — Named Transitions (Full Regression PG + SQLite)

**Date:** 2026-08-01
**Branch:** `latest`
**Commits:** `18675fc3` + `c4bb63c9` (Inc 3 implementation) + `07761955` (SQLite locking quick fix)
**Tester Instance:** (this session)
**Worker Instances:** 13 workers across 4 waves

## Summary

- **Total tests executed:** ~11,500+ (across 11 packs, SQLite + PostgreSQL + E2E)
- **New Inc 3 failures:** 0 (after 1 quick fix)
- **Pre-existing failures:** 157 (all baseline — broken SQLite migration, mock drift, circular import)
- **Quick fixes applied:** 1 (SQLite locking deadlock in RetryTurn → reconcile_turn_mirror nested transaction)
- **ensure.md:** ✅ ALL PASS (9/9 Core requirements)
- **Overall Status:** ✅ **PASS — READY TO MERGE**

## Scope Decision

Full suite run — warranted: cross-module architecture change (7 named transitions, D8 chokepoint routing for 5 methods, cascade rewrites in `instance_lifecycle.py` + `job_feedback_observer.py`). Reviewer explicitly requested PG + SQLite full regression. Inc 3 touches the hottest paths in the system (claim, resume, finalize, pause).

---

## Inc 3 New Tests (ALL PASS)

### Property/Unit/Static Tests — ✅ PASS (20/20)
- **Pack:** `tests/property/test_named_transitions.py` (17) + `tests/unit/test_transition_results.py` (1) + `tests/static/test_chokepoint_callers.py` (2)
- D10 mirror-set coverage: union of all 7 MIRROR_SET == ALL_8_MIRRORS ✓
- TransitionResult shape: frozen dataclass, correct fields ✓
- B6 caller map: AST-based static guard for chokepoint callers ✓
- Runtime: ~4s

### D8 Contract + E2E Behavioral — ✅ PASS (8/8)
- **Pack:** `tests/integration/test_complete_cancel_route_through_transitions.py` (4) + `tests/e2e/test_pause_resume_unchanged.py` (1) + `tests/e2e/test_pause_during_report_turn_then_resume.py` (3)
- D8 routing: `complete_task`/`cancel_task`/`fail_task` reconcile all 8 mirrors via transitions ✓
- B1 critical: `fail_task` → `AbortTurn(reason='failed')`, NOT CancelTurn ✓
- Cascade preservation: pause/resume cascades identical to pre-Inc-3 ✓
- Runtime: 1.41s

---

## Full Regression Results

### Pack D — Job Queue Full — ✅ PASS
- **Counts:** 1,463 passed, 0 failed, 38 skipped
- Runtime: 33.53s
- F6 watcher migration (now inside `RetryTurn.run()`) verified PASS

### Pack E — Message Queue Redesign — ✅ PASS (after quick fix)
- **Counts:** 419 passed, 0 failed, 13 skipped
- Runtime: 19.08s
- **Quick fix applied:** `07761955` — SQLite locking deadlock fix (see Quick Fixes section)

### Pack F — PostgreSQL Full Suite — ✅ PASS
- **Counts:** 153 passed, 0 failed, 33 skipped
- Runtime: 12.6s
- All reviewer-flagged critical PG tests PASS: pause_report_orphan_reconciliation, dependency_bus, concurrent_*, premature_completion, report_lane_phase2

### Pack G — Concurrency + Graceful Shutdown — ✅ PASS
- **Counts:** 66 passed, 0 failed, 19 skipped
- Runtime: 7s
- Thread-identity tests (`_TransitionContext` uses `threading.local()`) verified PASS

### Pack H — SQLite Unit/Integration/Property/Repositories/Static — ✅ PASS (baseline)
- **Counts:** 5,284 passed, 74 pre-existing failures, 34 skipped
- 0 NEW Inc 3 failures
- Runtime: 151.66s (2 min 31s)
- Pre-existing: ~26 broken SQLite migration + ~48 mock drift / stale agent tests

### Pack I — Core Daemon + API + Services — ✅ PASS (baseline)
- **Counts:** 3,704 passed (441 services + 3,263 top-level), 83 pre-existing failures, 97 skipped
- 0 NEW Inc 3 failures
- Runtime: 90.39s
- Pre-existing: ~50 circular import + ~15 broken SQLite migration + ~18 mock drift / stale tests

### Pack J — Concurrency Atomic + Inc1/Inc2 Edge Cases — ✅ PASS
- **Counts:** 156 passed, 0 failed, 30 skipped
- Runtime: ~21s
- All Inc 1 (reconciler) + Inc 2 (simplified guard) tests still PASS
- Hypothesis property state machine PASS

---

## E2E Flakiness (×5 each) — ✅ NOT FLAKY

| Test File | Verdict | Runs | Pass Rate |
|-----------|---------|------|-----------|
| `test_pause_during_report_turn_then_resume.py` | NOT FLAKY | 5×3 | 15/15 PASS |
| `test_pause_resume_unchanged.py` | NOT FLAKY | 5×1 | 5/5 PASS |

Total: 20/20 invocations PASS, ~18s total runtime.

---

## ensure.md Validation Results

- **Critical Requirements: 4/4 PASS**
  - ✅ No regressions in changed packs (RetryTurn SQLite fix holds: 40/40)
  - ✅ Deadlock/concurrency integrity (66 passed, 19 skipped)
  - ✅ No sync DB calls on asyncio loop
  - ✅ dev.sh `--timeout-graceful-shutdown 10`
- **Important Requirements: 2/2 PASS**
  - ✅ Async function callers properly awaited
  - ✅ Original deadlock scenario works
- **Nice-to-have: 3/3 PASS**
  - ✅ No dead code (zero refs to deleted `_admitted_task_carve_out_sql`)
  - ✅ Feature flag OFF (`TURN_RECONCILER_DIRECT_WRITE_PARITY = False`)
  - ✅ D10: union of MIRROR_SET == ALL_8_MIRRORS
  - ✅ D8: all 5 chokepoint methods route through transitions

---

## Quick Fixes Applied

| Commit | File(s) | Fix | Lines |
|--------|---------|-----|-------|
| `07761955` | `daemon/repositories/task/repository.py` + `daemon/services/turn_transitions.py` | `reconcile_turn_mirror()` now accepts optional caller-owned connection; `RetryTurn` passes its active transaction into parent and child mirror reconciliation instead of opening a nested `engine.begin()` that deadlocked SQLite | 16 |

**Root cause:** `RetryTurn` ran inside a caller-owned transaction but invoked `reconcile_turn_mirror()`, which opened a second `engine.begin()` transaction. The nested connection contended with the outer SQLite write lock, causing `sqlite3.OperationalError: database is locked` in concurrent retry tests.

---

## Edge Case Verification

| Edge Case | Status |
|-----------|--------|
| **B1 fail_task routing** | ✅ PASS — `fail_task` → `AbortTurn(reason='failed')`, NOT CancelTurn (line 1543) |
| **D8 chokepoint (5 methods)** | ✅ PASS — `complete_task`/`cancel_task`/`fail_task`/`schedule_retry`/`force_cancel_and_schedule_retry` ALL route through named transitions |
| **D10 coverage** | ✅ PASS — Union of all 7 MIRROR_SET = ALL_8_MIRRORS (8 tables). Each set non-empty. |
| **Feature flag OFF** | ✅ PASS — `TURN_RECONCILER_DIRECT_WRITE_PARITY = False` (Phase 4a). Transitions are unconditional; flag controls shadow-traffic only. |
| **Cascade behavior preservation** | ✅ PASS — pause/resume cascades work identically to pre-Inc-3 (E2E + ×5 flakiness) |
| **Thread safety** | ✅ PASS — `_TransitionContext` uses `threading.local()`; concurrency tests pass |
| **SQLite locking workaround** | ✅ PASS — `_TransitionTaskRepo` shim defers reconcile; `RetryTurn` passes caller transaction (fix `07761955`) |

---

## Pre-Existing Failures (baseline, NOT Inc 3-caused)

| Root cause | Count | Files affected |
|------------|-------|----------------|
| Broken SQLite migration `20260714_000001` (DROP CONSTRAINT syntax) | ~41 | test_manager, test_progressive_dispatch, integration tests |
| Circular import `daemon.compaction` → `daemon.graph` | ~50 | test_manager, test_spawn_limit_edge_cases, test_memory_integration |
| Mock drift / stale agent tests | ~48 | test_job_queue_proxy_phase1, test_title_generation_trigger, test_coder_developer_migration, etc. |
| Initiative_message feature (2nd title-gen call) | ~3 | test_enqueue_shared, test_innate_skills_refactoring |
| Misc (UI prefs, config defaults, env-deps) | ~15 | test_hard_delete_mock, test_skill_evolution_config, test_webfetch_builtin |

---

## Documentation Updated
- [x] RESULTS/2026-08-01-inc3-turn-reconciler-full-regression.md — this file
- [x] RESULTS/2026-08-01-ensure-validation.md — ensure.md results (written by ensure worker)
- [x] LESSONS/2026-08-01-inc3-sqlite-locking-retry-turn.md — SQLite nested transaction deadlock root cause

---

## Code Changes Summary
- `daemon/repositories/task/repository.py` — `reconcile_turn_mirror()` optional caller connection (commit `07761955`)
- `daemon/services/turn_transitions.py` — `RetryTurn` passes session to reconciliation (commit `07761955`)

---

### Overall Status
- Inc 3 New Tests: ✅ PASS (28/28)
- Full Regression SQLite: ✅ PASS (0 new failures, 157 pre-existing)
- Full Regression PostgreSQL: ✅ PASS (153/153, 33 skip)
- E2E Flakiness: ✅ NOT FLAKY (20/20)
- ensure.md: ✅ PASS (9/9 Core)
- **Testing Complete: ✅ READY — Increment 3 is regression-free and ready to merge**
