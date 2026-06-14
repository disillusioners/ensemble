# Test Report: experience() File Persistence Feature

**Date:** 2026-06-14
**Branch:** `feature/experience-file-persist`
**Commit:** `28698ff`
**File changed:** `daemon/tools/knowledge_tools.py`
**Test file:** `tests/unit/tools/test_knowledge_tools.py` (6 new tests)
**Sessions:** exp-primary, exp-regression, exp-ensure

---

## Summary

| Category | Result |
|----------|--------|
| Unit Tests (feature) | ✅ PASS (110/110) |
| Unit Tests (tools regression) | ✅ PASS (438/438) |
| Regression Tests | ✅ PASS (177/177) |
| Functional Verification | ✅ PASS (5/5 checks) |
| ensure.md (dev.sh) | ✅ PASS (ran 30s without crash) |
| Quick Fixes Applied | 0 |
| **Overall Status** | ✅ **READY** |

---

## Unit Test Results

### Primary: `tests/unit/tools/test_knowledge_tools.py`
- **Total: 110 | Passed: 110 | Failed: 0 | Errors: 0**

The 6 new `TestExperienceAutoSave` tests:
1. `test_experience_saves_file_with_experience_suffix` ✅
2. `test_experience_skips_duplicate_content` ✅
3. `test_experience_saves_non_duplicate_content` ✅
4. `test_experience_save_failure_does_not_propagate` ✅
5. `test_save_experience_result_creates_file` ✅
6. `test_save_experience_result_never_raises_on_bad_path` ✅

### Tools Regression: `tests/unit/tools/`
- **Total: 438 | Passed: 438 | Failed: 0**

### Related Regression Tests
| Test File | Total | Passed | Failed |
|-----------|------:|-------:|-------:|
| `tests/unit/test_explorer_auto_save.py` | 42 | 42 | 0 |
| `tests/unit/tools/test_rag_tools.py` | 25 | 25 | 0 |
| **Combined** | **177** | **177** | **0** |

---

## Functional Verification

| # | Check | Status | Evidence |
|---|-------|--------|----------|
| 1 | File creation at correct path | ✅ PASS | `_save_experience_result` (L506-548) builds `{tempdir}/ensemble/context/{context_key}/{slug}_experience.md`, uses `mkdir(parents=True, exist_ok=True)`, writes via `write_text()`. Called via `asyncio.ensure_future(asyncio.to_thread(...))` (non-blocking). |
| 2 | Dedup logic (80% similarity) | ✅ PASS | `_is_duplicate_experience` (L476-503) uses **Jaccard similarity** (`|intersection| / |union|`), threshold 0.8. Skips < 5 tokens. Only scans `*_experience.md` files. |
| 3 | Error handling (permission denied, etc.) | ✅ PASS | Three defensive layers: inner try/except in dedup loop, outer try/except in `_save_experience_result` (DEBUG log), try/except in `experience()` catching `RuntimeError` + `Exception`. |
| 4 | Experiencer enqueue flow unchanged | ✅ PASS | L839-851 retain original `asyncio.ensure_future(_enqueue_experience_job(...))` with its own try/except wrapper. Save runs before enqueue but doesn't alter it. |
| 5 | Edge cases (empty/long/special text, None context_key) | ✅ PASS | Empty → slug="experience". Long → truncated to 60 chars. Special → regex slugifier + UTF-8. None → fallback `root_id or current_instance_id or "default"`. |

---

## ensure.md Validation

- **Requirement:** dev.sh must run 30s without crashing
- **Result:** ✅ PASS — Exit code 124 (timeout SIGTERM = ran full 30s)
- **Server:** Started successfully on port 8079, all workers + MCP warmup pool initialized
- **Errors:** Zero errors, exceptions, or tracebacks in 139 log lines

---

## Action Needed
None. All tests pass, all functional checks pass, no regressions, dev.sh stable.

---

## Documentation Updated
- [x] RESULTS/2026-06-14-experience-file-persist.md — this report
- [x] PACKS.md — updated last run for knowledge_tools and regression packs
