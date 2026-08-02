# Test Report: skill_search_interval — Re-Test After Fix Round (C1/W1/S1)

**Date:** 2026-08-02  
**Branch:** `feature/skill-search-interval`  
**Project:** agents-ensemble  
**Round:** 2 (re-test after developer applied 3 fixes from review)

## Summary

| Pack | Tests | Result | Runtime |
|------|-------|--------|---------|
| Unit (33: 22 original + 11 W1 cache-isolation) | 33 passed | ✅ PASS | 1.80s |
| C1 Discovery (16: 13 original + 3 new) | 16 passed | ✅ PASS | 1.72s |
| Integration (11: 9 original + 2 new W1) | 11 passed | ✅ PASS | 1.82s |
| Regression: skill injection + messaging | 74 passed | ✅ PASS | 1.19s |
| Regression: context messages + registry | 160 passed, 1 skipped | ✅ PASS | 1.18s |
| **Total** | **294 passed, 1 skipped, 0 failed** | **✅ ALL PASS** | **~7.7s** |

- **ensure.md Validation:** 4/4 static checks PASS (W1 code review)
- **Quick Fixes Applied:** 0 production, 0 test (integration worker updated test stub + added 2 tests)
- **New Tests Added:** 2 integration tests (commit `202d1e44`)
- **Production Bugs Found:** None

## Fixes Verified

### C1 Critical: Discovery Wiring ✅
**Problem:** `skill_search_interval` was defined in `AgentMetadata` (Pydantic model) but NOT passed as a kwarg in `AgentRegistry.discover()`. The field silently defaulted to `1` regardless of meta.json config — dead config.

**Fix:** Both AgentMetadata construction sites in `discover()` now pass `skill_search_interval=meta.get("skill_search_interval", 1)` (lines 532 and 578 of `daemon/registry.py`).

**Verification:** 3 new discovery tests in `tests/test_registry_skill_injection.py`:
- `test_skill_search_interval_from_meta_json` — interval=3 in meta.json → correctly read by discover()
- `test_skill_search_interval_default_one` — absent key → defaults to 1
- `test_skill_search_interval_wired_in_both_construction_sites` — both construction sites (including llm_models retry fallback) pass the field through

### W1: Explicit load_skill Cache Isolation ✅
**Problem:** Explicit `load_skill` results and auto-search results shared the same cache (`_context_skill_results`). After an explicit `load_skill`, subsequent ordinary messages within the interval window would reuse the explicit result — potentially serving stale/irrelevant skills.

**Fix:** Added `_explicit_skill_loaded: set[str]` to InstanceManager + 3 marker methods (`mark_explicit_skill_loaded`, `clear_explicit_skill_loaded`, `was_explicit_skill_loaded`). The gate now checks `not was_explicit_skill_loaded(instance_id)` as part of the cache-reuse condition. Explicit loads mark the instance; auto-searches clear the marker. Cleanup is symmetric across all 3 lifecycle sites.

**Verification:**
- 11 unit tests (8 marker method tests + 3 gate-decision tests) — ALL PASS
- 2 new integration tests through real `_process_message_with_tracking`:
  - `test_explicit_load_forces_fresh_search_on_next_message` — interval=3 sequence: auto-search → explicit load_skill → next ordinary message forces fresh search
  - `test_cleanup_removes_explicit_load_marker` — `_cleanup_instance_state` removes marker alongside counter + cache
- Original 9 integration tests still pass (test stub updated to include marker methods)

### S1: Hot-Path Efficiency ✅
**Problem:** The `interval > 1` check was buried inside the gate logic, meaning cache lookups and marker checks ran on every message even for the default interval=1 case.

**Fix:** Hoisted `if interval > 1:` as the outer branch. When interval=1 (default for nearly all agents), falls through to `else: await _run_search_and_cache()` — zero cache lookups, zero marker checks.

**Verification:**
- `test_interval_one_unaffected_by_explicit_marker` — confirms interval=1 searches every message regardless of marker state
- All 22 original unit tests still pass unchanged — no behavioral regression

## ensure.md Validation Results

### Static Checks (W1 code review): 4/4 PASS
1. ✅ **No Sync DB Calls** — new methods are pure set operations (`.add()`, `.discard()`, `in`)
2. ✅ **Cleanup Symmetry** — `_explicit_skill_loaded` cleaned in all 3 lifecycle sites (same pattern as `_skill_search_message_counts`)
3. ✅ **No Dead Code** — all 3 methods have production call sites in `instance_messaging.py`
4. ✅ **W1 Guard Placement** — `was_explicit_skill_loaded` check is inside `interval > 1` branch (S1 interaction clean)

### Prior Round Checks (still valid — same code patterns):
- ✅ No regressions in changed packs (294 tests PASS)
- ✅ Deadlock/concurrency integrity (verified round 1: 22 passed, 9 skipped)
- ✅ dev.sh `--timeout-graceful-shutdown 10` confirmed

## Integration Test Evolution

| Round | File | Tests | What Changed |
|-------|------|-------|--------------|
| Round 1 | `test_skill_search_interval_messaging.py` | 9 created | Critical gap: unit tests replicate gate logic, don't exercise real messaging path |
| Round 2 | same file | 11 (9 kept + 2 new) | W1 fix: added marker to test stub + 2 new W1 integration tests |

## Instance IDs
- `e26df9f7` — investigation (C1/W1/S1 analysis)
- `33fa673e` — unit pack (33 tests)
- `29a6604a` — C1 discovery pack (16 tests)
- `b8f55656` — integration W1 verify (11 tests, updated stub + added 2)
- `8e46a67a` — regression: messaging (74 tests)
- `b3b8df8b` — regression: context+registry (160 tests)
- `f0c9a972` — ensure.md static checks (W1 code review)

## Documentation Updated
- [x] RESULTS/2026-08-02-skill-search-interval-retest-c1w1s1.md — this report
- [x] LESSONS/2026-08-02-w1-cache-isolation-testing.md — W1 integration test evolution
- [x] PACKS.md — updated test counts

---

### Overall Status
- Unit Tests: ✅ PASS (33/33)
- C1 Discovery: ✅ PASS (16/16, 3 new tests verify wiring)
- Integration: ✅ PASS (11/11, 2 new W1 tests through real path)
- Regression: ✅ PASS (234/235, 1 pre-existing skip)
- ensure.md: ✅ PASS (4/4 static checks)
- **All 3 Fixes Verified: ✅ READY — C1 dead config fixed, W1 cache isolation correct, S1 hot-path preserved, no bugs found**
