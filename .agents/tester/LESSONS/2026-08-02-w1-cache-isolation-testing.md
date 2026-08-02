# LESSON: W1 Cache Isolation — Test Evolution When Behavior Changes Between Rounds

**Date:** 2026-08-02  
**Feature:** Configurable Skill Search Frequency (`skill_search_interval`)  
**Branch:** `feature/skill-search-interval`  
**Fix:** W1 — Explicit `load_skill` cache isolated from auto-search cache

## Problem

In round 1, integration test 3b documented the behavior: "load_skill does NOT suppress auto-search on first message." This was correct for the round 1 code where explicit load_skill and auto-search shared the same cache.

In round 2, the W1 fix changed this behavior: after an explicit `load_skill`, the next ordinary message within the interval window now **forces a fresh auto-search** (the `_explicit_skill_loaded` marker guard prevents cache reuse of the explicit result). This is the correct new behavior — but it means the integration tests needed updating.

## Root Cause

When a behavior change between fix rounds changes the expected outcome of a test, the test must adapt — not the production code. The key challenge is:
1. The test's manager mock/stub must include the new state (`_explicit_skill_loaded` set) and methods
2. The test must verify the NEW expected behavior, not the old one
3. New tests should be added to specifically exercise the changed behavior through the real production path

## Fix Applied

The integration worker:
1. Updated the manager test stub to include `_explicit_skill_loaded: set[str]` + 3 marker methods
2. Verified all 9 original tests still pass (test 3b covers the first-message/cache-empty case, which is unaffected by W1)
3. Added 2 new integration tests:
   - `test_explicit_load_forces_fresh_search_on_next_message` — the core W1 contract through the real messaging path
   - `test_cleanup_removes_explicit_load_marker` — lifecycle correctness

Commit: `202d1e44`

## Pattern to Apply Going Forward

When re-testing after a fix round:

1. **Identify behavioral changes** — compare the fix description against existing test expectations. Any test that documents "actual behavior" (not just "intended contract") may need updating.
2. **Update test infrastructure** — new manager state/methods must be reflected in test stubs and mocks.
3. **Don't just pass — add coverage** — each fix round should add at least one test that exercises the NEW behavior through the real production path, not just the helper/pure-function level.
4. **Separate "documents old behavior" from "pins new behavior"** — when a test was explicitly documenting actual behavior (not a contract), and behavior changes, update the documentation in the test name/assertions to match.

## What This Caught

No production bugs — the W1 fix is correct. But the test evolution ensures:
- The manager stub accurately reflects production state
- The W1 marker guard is exercised through the real `_process_message_with_tracking` path
- The cleanup symmetry (`_explicit_skill_loaded` cleaned alongside counter + cache) is verified end-to-end
- The S1 interaction (marker guard inside `interval > 1` branch) is structurally enforced
