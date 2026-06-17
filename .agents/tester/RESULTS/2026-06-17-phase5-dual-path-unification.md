# Phase 5 — Dual-Path Unification Test Report

**Date:** 2026-06-17
**Branch:** `feature/correlation-manager`
**Commits:** `8f4b46f7` (Phase 5, 19 files +4445/-683), `65058a4d` (test fix)
**Sessions:** phase5-focused-tests, phase5-full-regression, phase5-ensure-validation

## Summary

| Category | Result |
|----------|--------|
| Phase 5 Focused Tests | ✅ PASS (110/110) |
| Full Regression | ✅ PASS (0 Phase 5 regressions) |
| ensure.md (dev.sh stability) | ✅ PASS (stable 30s, exit 124) |
| **Overall Verdict** | **✅ READY FOR MERGE** |

---

## Phase 5 Focused Tests (Session: phase5-focused-tests)

### Test File Results (5 files, 110/110 PASS)

| File | Lines | Tests | Pass | Fail |
|------|------:|------:|-----:|-----:|
| tests/test_pipeline_unified.py | 1,071 | — | All | 0 |
| tests/test_enqueue_shared.py | 993 | — | All | 0 |
| tests/test_phase5_real_cm_integration.py | 783 | — | All | 0 |
| tests/test_jq_error_reporting.py | (mod) | — | All | 0 |
| tests/unit/test_dispatch_completed_fix.py | (mod) | — | All | 0 |
| **TOTAL** | | **110** | **110** | **0** |

### Verification Coverage

1. **Pipeline correctness** — 6 shared stages execute in correct order, callbacks invoke at right points ✅
2. **Behavioral equivalence** — Both paths produce identical side-effects (error event, lifecycle event, parent report) ✅
3. **Lease contention** — Both paths handle ExecutionGate contention correctly ✅
4. **Pause/terminate** — Both paths handle pause/terminate correctly ✅
5. **retry_count propagation** — Correct in both paths ✅
6. **CM hooks** — Fire from both paths ✅
7. **Shared enqueue helper** — Both paths produce identical MessageQueue records, all metadata propagated ✅
8. **Edge cases** — Source prefix, retry_count, message_id generation ✅

### 3 Documented Behavioral Changes — All Verified ✅

| Change | Description | Verified |
|--------|-------------|----------|
| **W1** | WP `complete()` failure → `ProcessingResult(success=True)` not exception | ✅ Tests in `test_pipeline_unified.py` |
| **W2** | "no original_source" → debug log (not warning) | ✅ Tests in `test_pipeline_unified.py` |
| **W3** | `internal_` prefix → blocked from dispatch | ✅ Tests in `test_pipeline_unified.py` |

---

## Full Regression (Session: phase5-full-regression)

### Summary: 0 Phase 5 regressions

| Module Group | Passed | Failed | Skipped | Phase 5 Regressions |
|-------------|-------:|-------:|--------:|---------------------|
| Group A: unit/services/repositories/migration | 3,341 | 0 | 0 | 0 |
| Group B: job_queue/api/e2e/opencode | 1,708 | 0 | 33 | 0 |
| Group C1: integration/message_queue_redesign | 396 | 6 | 9 | 0 |
| Group C2: top-level tests/test_*.py (after fix) | 2,141 | 14 | 8 | 0 |
| **TOTAL** | **7,586** | **20** | **50** | **0** |

### Pre-Existing Failures (all match Phase 4 baseline exactly)

**14 Known Pre-Existing (top-level):**
1. `tests/test_config.py` (1) — max_instance_history mismatch
2-4. `tests/test_innate_skills_refactoring.py` (3) — OpenCode_Skill not in prompt
5. `tests/test_memory_integration.py` (1) — classify_request type mismatch
6-14. `tests/test_spawn_limit_edge_cases.py` (9) — MagicMock vs float TypeError

**6 Pre-Existing (integration):**
15-17. `tests/integration/test_message_queue_e2e.py` (3) — MCP context7 connection error (infra)
18-20. `tests/integration/test_multi_turn_resume.py` (3) — test isolation (pass in isolation)

### Verification Method
- Cross-referenced ALL failures against Phase 4 baseline (`.agents/tester/RESULTS/2026-06-17-phase4-counter-cleanup.md`)
- All 20 failures reproduce identically at Phase 4 — zero new failures from Phase 5

---

## Quick Fix Applied

| Commit | File | Description |
|--------|------|-------------|
| `65058a4d` | `tests/test_models.py` | Fixed InstanceStatus test for Phase 5 canonical 10-value enum. Phase 5 added `WAITING` to canonical definition (was only in the duplicate). Updated test to expect 10 values instead of 8. 5 lines changed, test-only. |

**Root cause:** Phase 5 canonicalized `InstanceStatus` by eliminating the duplicate definition in `daemon/models/instance.py` and re-exporting from the canonical `daemon/repositories/instance/models.py` (which has 10 values: IDLE, RUNNING, WAITING, PAUSED, COMPLETED, ERROR, TERMINATED, QUEUED, WAITING_CHILDREN, FAILED). The test in `test_models.py` still expected the old 8-value count.

---

## ensure.md Validation (Session: phase5-ensure-validation)

| Check | Result |
|-------|--------|
| Exit code | **124** (timeout killed = ran full 30s) |
| Uvicorn on 8079 | ✅ `Uvicorn running on http://0.0.0.0:8079` |
| Config loaded | ✅ `database=postgres` (v0.6.9) |
| All services started | ✅ WorkerPool, CM, JobProcessor, Observer, MCP, Dispatcher, etc. |
| Errors in 30s | ✅ **Zero** |
| Port 8088 | ✅ Untouched |

---

## Review Warnings (from Phase 5 code review — both benign)

| Warning | Severity | Impact | Status |
|---------|----------|--------|--------|
| WARN-1: `on_defer` callback dead code | Low | Dead surface area, no behavioral impact | Documented |
| WARN-2: `LeaseContention` raise quirk | Low | Unreachable in practice (both paths supply `on_contention`) | Documented |

---

## Overall Status

- Phase 5 Focused Tests: ✅ PASS (110/110)
- Full Regression: ✅ PASS (7,586 passed, 20 pre-existing failures, 0 Phase 5 regressions)
- ensure.md: ✅ PASS (dev.sh stable 30s)
- Quick Fixes: 1 (InstanceStatus test, commit `65058a4d`)
- **Testing Complete**: ✅ **READY FOR MERGE**
