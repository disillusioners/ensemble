# Test Report: Instance Lifecycle Hooks Feature
**Date:** 2026-08-08
**Tester Instance:** (this tester)
**Worker Instances:** f68d50a1, 1923c00b, d676bae4, 427b6e5f, a0d12998, 34f4124a

## Summary
- **Total Tests Run:** 251 (46 new + 12 completion + 190 context + 1 E2E + 2 skipped)
- **Passed:** 246
- **Failed:** 0 (4 quick-fixed during context regression)
- **Errors:** 0
- **Skipped:** 5 (pre-existing skips in test_root_instance_completion.py)
- **Timeouts:** 0
- **Quick Fixes Applied:** 4 (test code only), committed as f69c6885
- **Quarantined:** 0

### Scope Decision
> Full test suite NOT run. Change touches 6 production files (2 new, 4 modified) in a scoped feature: lifecycle hook registry + dispatcher, context file writing, and heuristic discovery. No architecture refactor, no cross-module blast radius. Scoped to directly-affected test files: lifecycle hooks tests, completion path regression, and context injection regression. Full suite not warranted.

## Test Results by Pack

### 1. Lifecycle Hooks Tests (NEW — 46 tests)
- **Worker:** f68d50a1
- **Files:** `tests/unit/test_lifecycle_hooks.py`, `tests/unit/test_lifecycle_hook_completion.py`
- **Result:** ✅ PASS — 46/46 passed in 1.12s
- **Failures:** none

### 2. Completion Path Regression (12 tests)
- **Worker:** 1923c00b
- **Files:** `tests/unit/test_root_instance_completion.py`, `tests/unit/services/test_child_reports.py`
- **Result:** ✅ PASS — 12 passed, 5 skipped (pre-existing) in 1.22s
- **Failures:** none

### 3. Context Regression (190 tests)
- **Worker:** d676bae4
- **Files:** `test_context_key.py`, `test_context_messages.py`, `services/test_context_injection.py`, `test_shared_context_metadata_repo.py`, `test_shared_context_tool.py`
- **Result:** ✅ PASS (after quick fixes) — 190 passed, 1 skipped in 1.5s
- **Initial failures:** 4 (fixed, committed f69c6885)
- **Quick fixes:** see LESSONS/2026-08-08-lifecycle-hooks-quick-fixes.md

### 4. Edge Case Coverage Analysis (static analysis)
- **Worker:** 427b6e5f
- **Result:** ✅ 5/7 edge cases fully COVERED, 2/7 PARTIALLY COVERED
- **Details:** see Edge Case Coverage section below

### 5. E2E Integration Validation (custom test)
- **Worker:** a0d12998
- **Result:** ✅ PASS — full loop validated in <1s
- **Score:** query "consensus" → 1.0 (threshold 0.10), 10× margin
- **Latent bug found:** 🟡 slug regex hex-only bug (see LESSONS/2026-08-08-lifecycle-hooks-slug-regex-bug.md)

## Edge Case Coverage Analysis

| # | Edge Case | Status | Test Location |
|---|-----------|--------|---------------|
| 1 | Hook A registered, only hook B configured → A NOT called | ✅ COVERED | `test_lifecycle_hooks.py::TestHookNameFiltering::test_only_named_hook_runs` |
| 2 | Two agents same-second → no file collision | ✅ COVERED | `test_lifecycle_hooks.py::TestFilenameCollision::test_same_slug_same_second_different_instance_id` |
| 3 | No `#` heading → fallback slug | ✅ COVERED (3 tests) | `test_lifecycle_hooks.py::TestSlugDerivation` (3 branches) |
| 4 | Old-format filenames still matched | ✅ COVERED (4 tests) | `test_lifecycle_hooks.py::TestSlugParserCompat` (both parsers + scorer) |
| 5 | Hook exception → NOT block completion | ✅ COVERED | `test_lifecycle_hook_completion.py::TestOutcomeGating::test_hook_exception_does_not_block_bus_terminal` |
| 6 | `_resolve_tree_root_id` mock signature | 🟡 PARTIAL | Tests mock the repository, not the function directly. No explicit signature assertion. |
| 7 | `get_version` mock signature | 🟡 PARTIAL | Return-type covered; call-arg signature not asserted (MagicMock is signature-agnostic). |

## ensure.md Validation Results
- **Worker:** 34f4124a
- **Scope:** 3 Core requirements relevant to the change set (static checks)

| Requirement | Priority | Result | Evidence |
|-------------|----------|--------|----------|
| `dev.sh` includes `--timeout-graceful-shutdown 10` | 🔴 Critical | ✅ PASS | Present at `dev.sh:102` on uvicorn command |
| No sync DB calls on asyncio event loop (feature files) | 🔴 Critical | ✅ PASS | All new DB calls in feature files use `asyncio.to_thread`; `write_context_file` properly wrapped; `dispatch_lifecycle_hooks` bounded with `asyncio.wait_for(timeout=5.0)` |
| All callers of `_restore_instance` properly `await` | 🟠 Important | ✅ PASS | Both call sites (`instance_lifecycle.py:2329`, `manager.py:6692`) use `await` |

**Note:** Pre-existing sync DB calls in `child_reports.py` (`_trigger_title_generation:487`, `_get_instance_report_prefix:526`) predate this feature and are out of scope for this validation. Flagged as technical debt for future cleanup.

**ensure.md Critical Requirements: 2/2 passed | Important: 1/1 passed**

## Mock Signature Verification
- `_resolve_tree_root_id`: tests mock `instance_repo.get_tree_root_id` and let the real function run via `asyncio.to_thread`. No direct function mock, so no signature mismatch risk, but also no contract assertion.
- `get_version`: real signature `get_version(self, agent_id, version_tag=None, *, validate_path=False)`. Tests mock via `MagicMock().get_version.return_value` — signature-agnostic but return-type correct. Production calls `get_version(child_agent_id, None)`.

## E2E Validation — Full Feature Loop

Validated the complete path:
1. `_add_to_shared_context_md_files(ctx)` — hook writes context file ✅
2. `resolve_context_dir()` — directory created ✅
3. `_extract_slug_from_filename()` — slug extracted ✅
4. `_score_context_files("consensus", dir)` — score 1.0 ✅
5. `_match_context_files("consensus", dir)` — file matched ✅
6. `list_context_files(key, query="consensus")` — file listed ✅

## Findings & Risks

### 🟡 Important: Slug regex hex-only bug (latent)
- **File:** `daemon/services/context_tools.py:43` (+ `context_injection.py:134`)
- **Impact:** Non-hex instance_ids (e.g., UUIDs with letters g-z, or non-UUID IDs) produce noisy slugs with timestamp/suffix leaked in.
- **Severity:** Non-blocking (matcher still works), but degrades slug quality.
- **Recommendation:** Developer should widen the char class from `[a-f0-9]{8}` to `[A-Za-z0-9_-]{1,32}`.
- **Test gap:** Existing tests only use hex instance_ids. Add a non-hex test case.

### 🟢 Nice-to-have: `_resolve_tree_root_id` contract test
- Tests don't directly mock the function, relying on the real implementation via `asyncio.to_thread`. A direct `patch()` with an `AsyncMock` would add a stronger contract test.

### 🟢 Nice-to-have: `get_version` call-arg assertion
- Tests verify behavior but not the exact call shape. Adding `registry.get_version.assert_called_once_with("wanderer", None)` would guard against accidental call-site changes.

## Quick Fixes Applied
- **Commit:** f69c6885 on branch `feature/instance-life-circle-hooks`
- **Files:** `test_context_key.py` (async fix), `services/test_context_injection.py` (env var isolation)
- **Details:** see LESSONS/2026-08-08-lifecycle-hooks-quick-fixes.md

## Documentation Updated
- [x] RESULTS/2026-08-08-lifecycle-hooks-feature-test.md — this report
- [x] LESSONS/2026-08-08-lifecycle-hooks-slug-regex-bug.md — slug regex bug
- [x] LESSONS/2026-08-08-lifecycle-hooks-quick-fixes.md — quick fixes applied

---

### Overall Status
- **New Tests:** ✅ PASS (46/46)
- **Regression Tests:** ✅ PASS (202/202, 5 pre-existing skips, 0 failures)
- **E2E Validation:** ✅ PASS (full loop verified)
- **Edge Cases:** ✅ 5/7 fully covered, 2/7 partial (non-blocking)
- **ensure.md:** ✅ PASS (3/3 requirements — 2 critical, 1 important)
- **Feature Verdict:** ✅ READY
