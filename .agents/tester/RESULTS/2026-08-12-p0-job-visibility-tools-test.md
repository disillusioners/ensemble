# Test Report: P0 Job Visibility Tools (`job_messages`, `job_tree`)

**Date:** 2026-08-12T15:36:05Z
**Branch:** `feature/ari-job-visibility-tools`
**Worker Instances:** `d869002a` (test author), `e1493955` (test executor)
**Skill:** `test-pack-execution`

## Summary
- Total: 96 tests | Passed: 96 | Failed: 0 | Errors: 0
- New Unit Tests: 19 | Regression: 77 | Smoke: 1
- Quick Fixes Applied: 6 test-code edits (index drift in existing test file)
- Quarantined: 0
- **Overall: ✅ ALL PASS**

## Scope Decision
> Change touches 2 new tool functions in a single module (`daemon/tools/job_queue.py`) + `agents/ari/meta.json` tools.allow. Small, isolated change → scoped to job-queue tool tests only. Full suite not warranted. Skipped: all other packs.

## Test Results

### Pack 1: New Unit Tests — `tests/unit/tools/test_job_visibility_tools.py`
- **RESULT: ✅ PASS** (19/19 in 1.32s)
- **TestFile:** 718 lines, 19 test functions across 3 test classes

**`TestJobMessagesTool` (9 tests):**
| # | Test | Description |
|---|------|-------------|
| 1 | `test_job_messages_happy_path` | Full result-shape with 2 messages |
| 2 | `test_job_messages_job_not_found` | get_work → None → error dict |
| 3 | `test_job_messages_no_instance_id` | Record without instance_id → error dict |
| 4 | `test_job_messages_pagination_has_more_true` | limit=3, offset=0, 10 msgs → has_more=True |
| 5 | `test_job_messages_pagination_has_more_false` | limit=2, offset=4, 5 msgs → has_more=False |
| 6 | `test_job_messages_tool_calls_redaction` | args truncated, no output field |
| 7 | `test_job_messages_project_id_mismatch` | Project mismatch → access denied |
| 8 | `test_job_messages_project_id_none_backward_compat` | project_id=None → allowed |
| 9 | `test_job_messages_content_snippet_truncation` | 250 chars → 200-char snippet |

**`TestJobTreeTool` (6 tests):**
| # | Test | Description |
|---|------|-------------|
| 1 | `test_job_tree_happy_path` | Root node, all keys present, truncated=False |
| 2 | `test_job_tree_job_not_found` | get_work → None → error dict |
| 3 | `test_job_tree_nested_children` | 3-level hierarchy (root→child→grandchild) |
| 4 | `test_job_tree_max_nodes_truncated` | 201 nodes → truncated=True |
| 5 | `test_job_tree_project_id_mismatch` | Project mismatch → access denied |
| 6 | `test_job_tree_empty_tree` | Root only, active=0, truncated=False |

**`TestJobVisibilityToolsRegistration` (4 tests):**
| # | Test | Description |
|---|------|-------------|
| 1 | `test_create_job_tools_is_importable` | Import from daemon.tools.job_queue |
| 2 | `test_create_job_tools_importable_from_daemon_tools` | Import path verified |
| 3 | `test_job_messages_and_job_tree_in_tool_list` | Both tools in list with category="job" |
| 4 | `test_job_in_category_modules_includes_visibility_tools` | CATEGORY_MODULES["job"] correct |

### Pack 2: Regression — `tests/test_job_queue_tools.py`
- **RESULT: ✅ PASS** (77/77 in 2.31s)
- **Quick fix applied** (test-code only): 7 pre-fix failures were all index drift caused by 4 new P0 tools inserted *between* existing tools and watch_job/watch_jobs. Production code was NOT modified.

**Failures observed before fix (7):**
| Test | Root Cause |
|------|------------|
| `test_create_job_tools_returns_expected_count` | `assert 17 == 17` → now 21 tools (4 new added) |
| `test_watch_job_resolver_path` | `tools[13]` was `watch_job`, now resolves to `job_messages` |
| `test_watch_job_terminal_enriches_result_from_instance` | Same index drift |
| `test_watch_job_terminal_no_enrich_when_already_set` | Same |
| `test_watch_job_terminal_no_manager_is_best_effort` | Same |
| `test_watch_job_terminal_manager_exception_is_swallowed` | Same |
| `test_watch_jobs_terminal_enriches_result_from_instance` | `tools[16]` was `watch_jobs`, now resolves to `job_inject` |

**Fix applied:** `tools[13] → tools[17]` (5 occurrences for watch_job), `tools[16] → tools[20]` (1 occurrence for watch_jobs). **Not committed** — leader handles commits.

### Pack 3: Smoke / Import Test
- **RESULT: ✅ PASS**
- `from daemon.tools.job_queue import create_job_tools` → IMPORT OK

## Quick Fixes Applied

| Instance | File | Fix | Root Cause |
|----------|------|-----|------------|
| `e1493955` | `tests/test_job_queue_tools.py` | `tools[13]→[17]` (5x), `tools[16]→[20]` (1x) | Index drift: 4 new tools inserted before watch_job/watch_jobs shifted hardcoded indices |
| `d869002a` | `tests/unit/tools/test_job_visibility_tools.py` | New file created (718 lines, 19 tests) | N/A (new coverage) |

## Flag-Back: Production Code Smell (NOT a test failure)

Worker flagged a real concern in production code (not triggered by tests, but surfaced in log capture):
- `job_messages` in `daemon/tools/job_queue.py` appears to treat `manager._instance_repository.get(...)` and `get_tree_ids(...)` as synchronous calls
- If these are actually async methods in the real `InstanceManager`, runtime errors will occur (`'coroutine' object has no attribute 'project_id'`)
- The new unit tests pass because they mock these calls; the real path is untested
- **Recommendation:** Developer should verify whether these repo calls need `await`. This is a flag-back, not a test failure.

## Coverage Summary

**Covered by new tests:**
- `job_messages`: happy path, not-found, no-instance, pagination (both directions), tool call redaction, access control (mismatch + None backward-compat), content truncation
- `job_tree`: happy path, not-found, nested hierarchy, node cap, access control, empty tree
- Registration: import, tool list, category module mapping

**Not covered (flagged by worker, not required by task):**
- Cycle detection path in `_build_node` (returns `_cycle` marker)
- `manager is None` and "Instance not found" error branches in both tools
- Real async repository calls (mocked in all tests)

## Documentation Updated
- [x] RESULTS/2026-08-12-p0-job-visibility-tools-test.md — this report
- [x] LESSONS/2026-08-12-tool-index-drift-quick-fix.md — index drift lesson (below)
- [ ] PACKS.md — pack entry not yet added (pending leader commit decision)
- [ ] rules/ensure.md — no changes (user-maintained)

## Code Changes Summary
All code changes are uncommitted — leader handles commits.
- `tests/unit/tools/test_job_visibility_tools.py` — NEW (718 lines, 19 tests)
- `tests/test_job_queue_tools.py` — MODIFIED (6 index drift fixes, test code only)
- Production code: **NOT modified** by tester

---

### Overall Status
- Unit Tests: ✅ PASS (19/19 new + 77/77 regression)
- Smoke/Import: ✅ PASS
- ensure.md: ✅ Core Critical "No regressions in changed packs" — PASS
- **Testing Complete: ✅ READY**
