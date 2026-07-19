# Test Report: Auto-Kill Background Processes (feature/auto-kill-bg-processes)
**Date**: 2026-07-18
**Branch**: feature/auto-kill-bg-processes
**Commits tested**: d4874719, 9cb29096, ab760004, 2481b59a

## Summary
- **Total tests**: 124 | **Passed**: 124 | **Failed**: 0 | **Errors**: 0 | **Timeout**: 0
- **Overall Status**: ✅ PASS — READY TO MERGE
- **Real-subprocess kill verification**: ✅ VERIFIED (not mocks)
- **Regression**: ✅ No regressions
- **ensure.md Core**: 5/5 PASS (Release Gate not triggered — MEDIUM blast radius)

## Scope Decision
> Full suite NOT run. Change is MEDIUM blast radius (in-memory process cleanup only, no DB schema/migrations/API). Ran 7 scoped test files covering the changed modules + proc regression. Skipped: full non-integration suite, E2E tests (no architecture/DB change). Full suite not warranted.

## Test Pack Results

| Pack | Tests | Result | Runtime | Notes |
|------|-------|--------|---------|-------|
| auto_kill_integration_test | 27 | ✅ PASS | 6.71s | 8 real-subprocess tests + 6 mock scenario tests + 13 edge/other |
| bash_tools (test_bash.py + test_bash_registry.py + test_bash_cancel.py) | 25 | ✅ PASS | 1.08s | 5 real CancelledError kill tests (sleep 30, os.kill verify) |
| instance_tools_test | 8 | ✅ PASS | 0.53s | _make_instance_id_aware wrapper (8 scenarios) |
| job_feedback_observer_test | 40 | ✅ PASS | 0.41s | 28 pre-existing (no regression) + 12 new (9 two-tier cleanup + 2 other) |
| proc_regression_test | 24 | ✅ PASS | 7.23s | All 24 previously-passing tests still pass |

## Real-Subprocess Kill Verification (CORE CLAIM) — ✅ VERIFIED

The central claim — *"processes are actually killed when a root instance reaches a terminal state"* — is proven with REAL OS processes via `os.kill(pid, 0)`:

### Test A — Proc kill on root completion: ✅ REAL
- `TestRealSubprocessSmoke.test_real_sleep_via_proc_killed_by_tier1` (L1304): spawns `sleep 30` via proc_mgr, asserts `not _pid_alive(pid)` after cleanup_instance
- `TestDaemonShutdownKillsEverything.test_shutdown_sweeps_both_registries_with_real_processes` (L1007): 3× proc `sleep 30`, all dead after shutdown

### Test B — Bash kill on root completion: ✅ REAL
- `TestRealSubprocessSmoke.test_real_sleep_via_bash_killed_by_tier1` (L1267): spawns `sleep 30` via bash tool, asserts dead after cleanup
- `TestScenarioFNohupGrandchild.test_nohup_grandchild_killed_by_killpg` (L527): REAL nohup grandchild, asserts `not _pid_alive(gc_pid)` after os.killpg

### Test C — CancelledError mid-bash kills subprocess: ✅ REAL (5/5 tests)
- `test_bash_cancel.py::test_cancellation_at_wait_kills_subprocess` (L95-152): spawns REAL `sleep 30`, verifies alive via `os.kill(pid, 0)`, cancels task, verifies DEAD, verifies registry empty
- Handler code confirmed (bash.py:369-395): `task.uncancel()` + `asyncio.shield(_kill_process(proc, pgid=pgid))` + `asyncio.shield(unregister(...))` then `raise`
- `_kill_process` (bash.py:138-186): `os.killpg(target_pgid, SIGTERM)` → SIGKILL escalation; pgid captured ONCE at spawn (TOCTOU-safe)

### Test D — Daemon shutdown kills everything: ✅ REAL
- `TestDaemonShutdownKillsEverything` (L1007): 3× bash `sleep 30` + 3× proc `sleep 30`, ALL 6 dead after `manager.shutdown()`

### Test E — Idempotency (concurrent cleanup safe): ✅ REAL
- `TestParallelCallIdempotency` (L1341): 2× bash `sleep 30` + 2× proc `sleep 30`, concurrent cleanup, all dead, no crash, no double-kill errors
- `TestScenarioGDoubleFireIdempotency`: double-fire is no-op

## Edge Cases — ✅ COVERED
- **No-op cleanup (empty registry)**: `test_real_empty_registries_terminate_cleanly` (L1182), `test_no_op_for_child_with_empty_registries` (L1134), `test_no_op_for_root_with_empty_registries` (L1155) — atomic `pop(instance_id, [])`
- **Already-dead process**: handled via broad `except Exception` in `BashProcessRegistry.cleanup_instance` (catches ProcessLookupError); implicitly tested by concurrent-cleanup race test
- **Child terminates → own processes killed, siblings preserved**: `test_tier1_does_not_touch_sibling_or_root` (L245) — asserts only child_id in cleanup calls, root_id + sibling_id absent
- **Root terminal sweep covers ALL descendants**: `TestScenarioBTier2RootSweep` (Tier 2 root-gated)
- **TERMINATED path cleans BOTH registries (M1 fix)**: `test_terminate_instance_cleans_bash_registry` + instance_lifecycle.py:1462 (proc) + :1476 (bash)
- **Setsid orphan survives** (documented limitation, characterization test): `TestSetsidOrphanSurvivesCleanup.test_setsid_orphan_survives_killpg` (L1696) — pins D4 documented limitation, NOT a bug

## ensure.md Validation Results (Core, scoped)

| Requirement | Priority | Result | Evidence |
|-------------|----------|--------|----------|
| No regressions in changed packs | Critical | ✅ PASS | 124/124 tests pass, branch verified |
| No sync DB calls on event loop (get_tree_ids in asyncio.to_thread) | Critical | ✅ PASS | job_feedback_observer.py:2629-2632 wraps get_tree_ids in asyncio.to_thread |
| dev.sh includes --timeout-graceful-shutdown 10 | Critical | ✅ PASS | dev.sh:74 |
| Deadlock/concurrency integrity | Important | ✅ PASS | cleanup_instance async, no sync DB calls, cleanup_all snapshots keys under lock then releases before iterate (non-reentrant lock safe), Tier1/Tier2 isolation try/except |
| All callers properly await cleanup_instance/cleanup_all | Important | ✅ PASS | 8/8 call sites verified awaited |

**Release Gate**: NOT triggered (MEDIUM blast radius — in-memory only, no DB/API/schema change).

## Quick Fixes Applied
None — all tests passed on first run. No commits made.

## Quarantined Tests
None (no QUARANTINE.md exists).

## Coverage Gaps (minor, non-blocking)
1. No dedicated test for `cleanup_instance` on an already-dead PID (covered only implicitly by concurrent-cleanup race tests). Low risk — broad `except Exception` handles ProcessLookupError.
2. Scenarios A/B/C in test_auto_kill_integration.py use AsyncMock for the registries (call-shape only). The real-OS-subprocess verification is concentrated in Scenario F, Real Subprocess Smoke, Daemon Shutdown, and Parallel Call Idempotency — all of which PASS. This is acceptable: the mock tests verify dispatch wiring, the real tests verify actual killing.

## Documentation Updated
- [x] RESULTS/2026-07-18-auto-kill-bg-processes.md — this report
- [ ] PACKS.md — no new packs to register (existing test files used)
- [ ] LESSONS/ — see separate file
