# Lesson: Loop Breaker Test Quality Audit

**Date:** 2026-07-17
**Feature:** General Hallucination Loop Breaker
**Branch:** feature/general-hallucination-fix

## Key Insight: "Passing tests ≠ Correct tests"

102 tests passed on first run. A deep code audit revealed 6 real coverage gaps — scenarios that SHOULD have been tested but weren't, plus tautological assertions that would pass even if the feature were broken.

## Tautology Trap (Most Important Finding)

**Problem:** `TestFullFlow::test_detection_triggers_repair_and_llm_uses_repaired_messages` used a `fake_repair` that returned `repaired_messages=ctx.messages` — the SAME list as the originals. The test asserted "LLM called once" but couldn't distinguish "LLM got repaired messages" from "LLM got originals."

**Pattern to avoid:** When mocking a function that transforms input→output, the mock must return a DISTINCT output that you can identify downstream. If the mock returns the input unchanged, the test is a tautology.

**Fix:** Return a sentinel-marked list (`id="pr-1"`) and assert the sentinel IS present in the LLM's received messages AND the original loop message IDs are ABSENT.

## Missing Negative Assertion Trap

**Problem:** The test verified `aupdate_state` was called correctly, but didn't assert `astream(None)` was NOT called. The #6433 LangGraph bug (aupdate_state + astream returns instantly) would silently pass all tests if reintroduced.

**Pattern to avoid:** When a critical bug-avoidance pattern exists, add an explicit negative assertion. "We call the right method" should also mean "we DON'T call the wrong method."

**Fix:** `graph.astream.assert_not_called()` added after aupdate_state assertions.

## Cleanup Path Testing: Real Call vs Hand-Scripted

**Observation:** 3 of 5 cleanup paths were tested by calling the REAL production method (terminate_instance, cancel_graph_task, _cleanup_instance_state). 2 paths (hard_delete zombie sweep, pause_cascade) were tested by hand-copying the 3-line cleanup loop body.

**Risk:** Hand-scripted tests verify the SCRIPT, not the INTEGRATION. If the production loop is restructured, the test stays green.

**Recommendation:** Prefer calling the real method whenever feasible. When the real method has heavy dependencies (DB, graph), use stubs that still invoke the real code path.

## Layering: Max Repairs Belongs in Integration Tests

The `max_repairs` cap is NOT tested in `test_loop_repairer.py` — this is architecturally CORRECT. `LoopRepairer` is a stateless helper; the cap lives in `_maybe_repair_loop` (integration helper) backed by `InstanceManager._loop_breaker_state` (RAM-only session state). Testing the cap in the repairer unit tests would be a layering violation.

## Test Counts After Gap-Filling
- test_loop_detector.py: 28 tests (unchanged)
- test_loop_repairer.py: 22 tests (2 strengthened, 0 added)
- test_loop_breaker_integration.py: 16 tests (12 original + 4 added)
- test_gii_throttle.py: 40 tests (unchanged)
- **Total: 106 tests, all PASS**
