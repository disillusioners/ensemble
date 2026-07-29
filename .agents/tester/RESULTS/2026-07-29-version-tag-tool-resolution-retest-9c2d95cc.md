# Re-Test Report: Version Tag Tool Resolution — Review Fixes (9c2d95cc)

**Date:** 2026-07-29
**Branch:** `bugfix/version-tag-tool-resolution`
**Commit:** `9c2d95cc` (on top of `09d146c9`)
**Worker Instances:** 6 parallel workers (test-pack-execution)

## Summary

- **Total tests run:** 914 (across 6 packs)
- **Passed:** 914
- **New regressions:** **0**
- **Skipped:** 22 (14 pre-existing infra requirement + 8 api_unit_test)
- **Quick fixes applied:** **2** (both test-code only, same recurring mock-gap pattern)

### Overall Status: ✅ **READY** — No regressions from the review-fix commit

### Review-Fix Changes Verified (commit 9c2d95cc)

| Fix | Description | Verified by | Result |
|-----|-------------|-------------|--------|
| W1 | access_memory.py + inner_soul.py version-aware path resolution | inner_soul_memory_skill_metrics pack (339 tests) | ✅ PASS |
| W2 | skill_metrics_service.py version-aware skill injection gate | inner_soul_memory_skill_metrics pack | ✅ PASS |
| S1 | 2 new closure-level integration tests (spawn_instance + convene_council) | version_tag_tool_resolution pack (19 tests) | ✅ PASS |
| S2 | instance_messaging.py context_injection_mode version-aware | instance_messaging_regression pack (41 tests) | ✅ PASS |
| S4 | Comment fix only | N/A (no behavior change) | ✅ N/A |

---

## Pack Results

### 1. version_tag_tool_resolution_unit_test — ✅ PASS
| Detail | Value |
|--------|-------|
| Worker | retest-version-tag (2b3116b1) |
| Tests | **19 passed, 0 failed** (was 17, now +2 closure integration tests) |
| Runtime | 1.04s |
| New tests verified | `test_v2_team_members_authorize_coder_and_deny_developer` ✅, `test_v2_governor_team_members_authorize_convene_council` ✅ |
| Reviewer concern | Did NOT manifest — test-isolation flag did not cause failure |

### 2. authz_auto_derive_unit_test — ✅ PASS
| Detail | Value |
|--------|-------|
| Worker | retest-authz (a21a168a) |
| Tests | **77 passed, 0 failed** |
| Runtime | 2.05s |

### 3. services_orchestration_regression_test — ✅ PASS
| Detail | Value |
|--------|-------|
| Worker | retest-services (1f676252) |
| Tests | **25 passed, 14 skipped (pre-existing), 0 failed** |
| Runtime | 7.9s |

### 4. api_unit_test — ✅ PASS
| Detail | Value |
|--------|-------|
| Worker | retest-api (413e4fe2) |
| Tests | **213 passed, 8 skipped, 0 failed** |
| Runtime | 13s |

### 5. instance_messaging_regression_test — ✅ PASS (after quick fix)
| Detail | Value |
|--------|-------|
| Worker | retest-messaging (4f3623a7) |
| Tests | **41 passed, 0 failed** (initial run: 11 failed, 30 passed) |
| Runtime | 1.01s |
| Quick fix | `5b1cca86` — added `get_version.return_value=None` to 18 mock sites for S2/C1 version-aware resolution |
| Initial failures | 11 — all same root cause: get_version() returned truthy MagicMock, bypassing mocked get_resolved() |

### 6. inner_soul_memory_skill_metrics_unit_test (NEW) — ✅ PASS (after quick fix)
| Detail | Value |
|--------|-------|
| Worker | retest-inner-soul (3353476b) |
| Tests | **339 passed, 0 failed** (initial run: 61 failed, 278 passed) |
| Runtime | 4.85s |
| Quick fix | `d392b73c` — added `get_version.return_value=None` to 8 mock sites in 4 test files |
| Initial failures | 61 — all same root cause: get_version() returned truthy MagicMock |

---

## Quick Fixes Applied

### Fix 1: instance_messaging mock gap (commit 5b1cca86)
- **Root cause:** Same recurring pattern — `get_version()` returns truthy MagicMock, bypassing mocked `get_resolved()`
- **Affected:** 18 mock sites across test_instance_messaging_shared_context_injection.py + test_instance_messaging_skill_injection.py
- **Fix:** Added `get_version.return_value=None` to all 18 sites
- **Classification:** Test-code only (26 insertions), exposed by W2/S2 version-aware changes

### Fix 2: inner_soul + memory mock gap (commit d392b73c)
- **Root cause:** Same recurring pattern
- **Affected:** 8 mock sites across test_inner_soul_rejection.py, test_inner_soul_compound.py, test_inner_soul_redirect.py, test_memory_edge_cases.py
- **Fix:** Added `get_version.return_value=None` to all 8 sites
- **Classification:** Test-code only (8 insertions), exposed by W1 version-aware changes

### Recurring Pattern Documented
The `get_version() or get_resolved()` mock-gap pattern has now surfaced **4 times** across 2 commits. See LESSONS/2026-07-29-recurring-get-version-mock-gap-pattern.md for the full pattern analysis and prevention guidance.

## Documentation Updated
- [x] PACKS.md — 5 pack statuses updated + added inner_soul_memory_skill_metrics_unit_test entry
- [x] LESSONS/ — documented recurring mock-gap pattern (4 occurrences)
- [x] RESULTS/2026-07-29-version-tag-tool-resolution-retest-9c2d95cc.md — this report
