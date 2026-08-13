# Test Report: answer-resume-fix (Bug 1 + Bug 2)
Date: 2026-08-13T12:56:12Z
Branch: `debug/answer-resume-fix`
Instances: 8152e316 (infra), d60c3531 (pack+unit), 86166077 (quick-fix-unit), a63db3c7 (e2e), 7e91324e (concurrency), ae1c1f19 (static)

## Summary
- **Total tests**: 148 (54 unit + 3 e2e + 91 concurrency)
- **Passed**: 148 | **Failed**: 0 | **Errors**: 0
- **Quick Fixes Applied**: 3 commits (all test-code assertion drift, zero production fixes)
- **Quarantined**: 0
- **Overall**: ✅ READY

## Scope Decision

Full requested; change touches the **task/queue system** (pause/resume flow, turn transitions) → 🔴 **MANDATORY full e2e** per project critical notes. Change spans 2 production files (`instance_messaging.py`, `routers/instances.py`) affecting the concurrency-sensitive `pause_instance_cascade` path. No scope reduction applied — warranted.

## What Changed (Production — uncommitted working tree)
1. **Bug 1** — `daemon/services/instance_messaging.py`: `pause_instance_cascade` now passes `suspension_reason=SuspensionReason.AWAITING_ANSWER.value` at 2 call sites (lines ~1090, ~3713)
2. **Bug 2** — `daemon/routers/instances.py`: Three endpoints (`answer_questions`, `dismiss_question`, `resume_instance`) now call `resume_processing_job` BEFORE `resume_instance_cascade` so the answer message is injected while the task is still PAUSED

## Original Bug Symptom (verified fixed)
```
WARNING - [RESUME] instance=9f924e7a route_outcome=invalid_or_missing_handle — no suspended or paused turn found
```
The LLM ran without seeing the user's answer because `resume_instance_cascade` was called first (flipping PAUSED→PENDING), making the answer handle unresolvable. Now `resume_processing_job` runs first while the task is still PAUSED.

## Quick Fixes Applied (test code only — 0 production fixes needed)

### Fix 1: Deferred pause callback assertion drift (commit `53f59214`)
- **File**: `tests/unit/test_question_deferred_pause_callback.py`
- **Root cause**: Production now passes `suspension_reason='awaiting_answer'` to `pause_instance_cascade`, but 5 test assertions used `assert_awaited_once_with(instance_id)` (positional only). Also 2 local helper functions needed `suspension_reason` param added.
- **Tests fixed**: 5 (all in `test_question_deferred_pause_callback.py`)

### Fix 2: E2E stale ResumeTurn assertions — test 1 (commit `7112bd77`)
- **File**: `tests/e2e/test_full_chain_turn_reconciler.py`
- **Root cause**: Tests asserted pre-Phase-4b/4c migration `ResumeTurn` semantics (`PAUSED→CANCELLED`) but production was migrated to `PAUSED→PENDING` (2026-08-12, `turn_transitions.py:254,310-313`). The same `work_id` now stays live for WorkerPool re-claim.
- **Test fixed**: `test_full_chain_claim_process_pause_resume_answer_complete` (Step 3 + Step 5 assertions)

### Fix 3: E2E stale ResumeTurn assertions — tests 2+3 (commit `12cff99f`)
- **File**: `tests/e2e/test_full_chain_turn_reconciler.py`
- **Root cause**: Same Phase 4b/4c staleness as Fix 2
- **Tests fixed**: `test_answer_delivered_exactly_once`, `test_full_chain_no_deadlock_at_each_phase`

## Unit Test Results
- **Pack**: `test/packs/c2_question_deferred_pause_unit_test.sh`
- **Instance**: d60c3531 (initial run), 86166077 (after quick-fix)
- **Status**: ✅ PASS (54/54 after fix, 2.45s)
- **Files**: `test_question_deferred_pause_callback.py` (6), `test_question_graph.py`, `test_question_manager.py`, `test_question_tools.py`, `test_question_untested_paths.py` (9), `test_question_pause_completion_guard.py`

## E2E Test Results (MANDATORY — task/queue system)
- **Pack**: `test/packs/e2e_full_chain_turn_reconciler_test.sh` (NEWLY CREATED)
- **Instance**: a63db3c7
- **DB**: PostgreSQL (primary, verified via `/api/health`)

| Test | Result | Runtime | Commit |
|------|--------|---------|--------|
| `test_full_chain_claim_process_pause_resume_answer_complete` | ✅ PASS | 0.99s | `7112bd77` |
| `test_answer_delivered_exactly_once` | ✅ PASS | 1.01s | `12cff99f` |
| `test_full_chain_no_deadlock_at_each_phase` | ✅ PASS | 0.82s | `12cff99f` |

## Concurrency Test Results
- **Pack**: `test/packs/concurrency_atomic_unit_test.sh`
- **Instance**: 7e91324e
- **Status**: ✅ PASS (91 passed, 74 skipped, 0 failed, 6.89s)
- **Coverage**: deadlock_fix, cascade races (4 files), observer races (3 files), instance/project atomic locks (2 files), threading serialization gate, finalize_job h15, report-lane Phase 2

## ensure.md Validation Results

### Critical
- ✅ No regressions in changed packs — all scoped packs PASS
- ✅ Deadlock/concurrency integrity — `concurrency_atomic_unit_test` PASS (91/0)
- ✅ No sync DB calls on asyncio — covered by `concurrency_atomic_unit_test`
- ✅ `dev.sh` includes `--timeout-graceful-shutdown 10` — grep confirmed (line 102)

### Important
- ✅ All callers of converted async functions properly awaited — 5 call sites verified (`_get_system_prompt_tokens` ×2, `_compute_context_usage` ×1, `get_queue_stats` ×2)

### Nice-to-have
- N/A — no dead code from the fix

## Edge Case: Instance not paused when answer arrives
The e2e tests confirm the fix handles the ordering correctly:
- `resume_processing_job` calls `find_suspended_turn_for_answer` which filters on `Task.status == 'paused'`
- If the instance is NOT paused (already running or completed), the handle returns `invalid_or_missing_handle` — the answer message is simply not injected and no crash occurs
- This is the correct behavior: an answer arriving for a non-paused instance is a no-op for the resume path

## New Pack Created
- `test/packs/e2e_full_chain_turn_reconciler_test.sh` — single-test runner for `tests/e2e/test_full_chain_turn_reconciler.py` with dual-layer timeout. Accepts test name as argument. Registered in PACKS.md.

## Documentation Updated
- [x] PACKS.md — added e2e_full_chain_turn_reconciler_test pack entry + run summary
- [x] LESSONS/2026-08-13-answer-resume-fix-test-staleness.md — 2 staleness patterns documented
- [x] RESULTS/2026-08-13-answer-resume-fix-test.md — this report

## Code Changes Summary (test code only — production uncommitted)
- `tests/unit/test_question_deferred_pause_callback.py` — assertion + helper signature updates (commit `53f59214`)
- `tests/e2e/test_full_chain_turn_reconciler.py` — 3 tests updated for Phase 4b/4c ResumeTurn semantics (commits `7112bd77`, `12cff99f`)
- `test/packs/e2e_full_chain_turn_reconciler_test.sh` — NEW pack script (uncommitted, created by worker)

---

### Overall Status
- Unit Tests: ✅ PASS
- E2E Tests: ✅ PASS (3/3 MANDATORY tests)
- Concurrency: ✅ PASS (91/0)
- ensure.md: ✅ PASS (4/4 Critical, 1/1 Important)
- **Testing Complete**: ✅ READY
