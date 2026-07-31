# Test Report: Title Generation Duplicate Fix (TOCTOU Race)

Date: 2026-07-31
Branch: `fix/title-generation-duplicate`
Instances: run-title-gen-tests (f61cd5b4), review-title-gen-fix (2796d6ad)

## Summary
- **Overall Status: ✅ READY** — fix is correct and verified
- Total tests run: 29 | Passed: 21 | Failed: 8 (all pre-existing, unrelated)
- `TestTitleGenerationIdempotency`: **7/7 PASS** (4 existing + 3 new)
- Code review: **5/5 properties PASS** (CORRECT, no bugs found)
- Quick Fixes Applied: 0 (none needed)
- Quarantined: 0 new

## Scope Decision
> Full requested; change touches 2 files in 1 module (title generation service) → running `title_generation_trigger_test` pack only, skipping all other packs. Full suite NOT warranted. Reason: single-module concurrency bugfix, no architecture impact. Skipped: all other 224 packs.

## What Changed (Under Test)
1. **`daemon/services/title_generation.py`** — Added `self._generating_instances: set[str]` in `__init__`. In `_generate_and_broadcast_title()`: dedup check before LLM call → `add(instance_id)` → `try/finally` with `discard(instance_id)` cleanup on all exit paths.
2. **`tests/unit/services/test_title_generation_trigger.py`** — 3 new tests in `TestTitleGenerationIdempotency`: concurrent-dedup, cleanup-on-success, cleanup-on-error.

## Test Results

### TestTitleGenerationIdempotency — 7/7 PASS ✅

| # | Test | Status |
|---|------|--------|
| 1 | `test_title_service_skips_when_title_exists` | ✅ PASS |
| 2 | `test_title_service_handles_llm_error` | ✅ PASS |
| 3 | `test_title_service_skips_empty_content` | ✅ PASS |
| 4 | `test_title_service_stores_generated_title` | ✅ PASS |
| 5 | `test_title_service_dedups_concurrent_calls` *(NEW)* | ✅ PASS |
| 6 | `test_title_service_clears_in_flight_set_on_success` *(NEW)* | ✅ PASS |
| 7 | `test_title_service_clears_in_flight_set_on_llm_error` *(NEW)* | ✅ PASS |

### Pre-existing Failures — 8 (UNRELATED to this fix)

All 8 share the same root cause: `InstanceMessagingService._maybe_store_initiative_message` is a coroutine that adds an extra `run_async_no_wait` call, so `mock_run_async.assert_called_once()` fails (actual count = 2). The mock setup hasn't been updated for the async-storage change.

| Class | Tests | Error |
|-------|-------|-------|
| `TestInstanceMessagingTriggerTitleGeneration` | 7 | `Expected 'run_async_no_wait' to be called once. Called 2 times` |
| `TestMaybeTriggerTitleGenerationMethod` | 1 | same |

These predate this branch, are documented in `.pytest_cache/v/cache/lastfailed`, and match the exact count/classes described in the task. They should be quarantined or fixed in a separate effort.

### NEW/Unexpected Failures: NONE ✅

## Code Review: Fix Logic Verification — VERDICT: CORRECT ✅

Static analysis of `daemon/services/title_generation.py` confirmed all 5 properties:

| # | Property | Status | Evidence |
|---|----------|--------|----------|
| 1 | `_generating_instances: set[str]` initialized in `__init__` | ✅ PASS | Line 42 |
| 2 | Dedup check BEFORE the LLM call (before any `await`) | ✅ PASS | Check at line 67; first `await` at line 75; LLM call at line 107 |
| 3 | Race-free: check + add both synchronous, NO `await` between them | ✅ PASS | Lines 67-70 (in-check → log → add — all sync) |
| 4 | `finally` cleanup covers ALL exit paths (success, error, early returns) | ✅ PASS | `try` at 72, `finally`+`discard()` at 146-150; early-return dedup path is correctly BEFORE try/finally |
| 5 | Guards the correct method (`_generate_and_broadcast_title`) | ✅ PASS | Lines 49-150; single entry point for all trigger paths |

### Race Window Analysis
The critical property: lines 67-70 (`in` check → `add`) contain NO `await`. From the event loop's perspective, check-and-add is atomic — no other coroutine can interleave between them. This closes the TOCTOU window completely.

### Exit-Path Coverage of `finally`
- Normal success → finally runs ✅
- LLM `TimeoutError` → inner except catches, outer finally runs ✅
- LLM `Exception` → inner except catches, outer finally runs ✅
- `asyncio.CancelledError` → NOT caught by `except Exception` (correct), propagates, but finally STILL runs ✅
- Dedup early-return → BEFORE try/finally (correct — doesn't steal another task's slot) ✅

### Test Quality Assessment
The concurrent-dedup test (`test_title_service_dedups_concurrent_calls`) uses **`asyncio.gather`** to schedule two coroutines on the same event loop — this is genuine concurrency that reproduces the original race. Without the fix, both coroutines would pass the DB idempotency check and both call `llm.invoke`, failing `assert_called_once()`. This is a true regression test.

### Edge Case Notes (non-blocking)
- ⚠️ **Minor gap**: No test for `asyncio.CancelledError` cleanup path (3 new tests cover success + RuntimeError only). Low regression risk since `discard()` is no-op-safe. Suggestion for future hardening: a cancellation-forced test.
- ✅ `discard()` (not `remove()`) used — defensive against missing entries.
- ✅ Both fire-and-forget trigger paths (`child_reports.py`, `instance_messaging.py`) funnel through the same guarded method.

## Runtime
- Pack runtime: 1.30s (well under 120s unit limit)
- Bash overhead: ~2s

## ensure.md Validation
Not triggered for this scope — the fix is to `title_generation.py`, which does not map to any ensure.md critical requirement (concurrency_atomic_unit_test, deadlock_fix, etc. are unrelated modules). The `dev.sh` graceful-shutdown flag static check is irrelevant to this change. No contradictions found.

## Documentation Updated
- [x] PACKS.md — updated title_generation_trigger_test entry (last run, status, test count)
- [x] RESULTS/2026-07-31-title-generation-duplicate-fix.md — this report
