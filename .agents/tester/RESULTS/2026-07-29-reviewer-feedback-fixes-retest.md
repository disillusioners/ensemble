# Test Report: Reviewer Feedback Fixes Re-Test (commit 58b71a62)

Date: 2026-07-29
Branch: `bugfix/deferred-version-tag-fixes`
Commit: `58b71a62` (fix: persist version tags through retry, re-elevation, and agent jobs)
Quick Fix: `6cdaf679` (test: fix MockJob missing job_type attr in test_defer_queue)

## Summary
- **Total packs**: 8 | **Passed**: 8 | **Failed**: 0 | **Timeout**: 0
- **Total tests**: ~1700+ passed, 38 skipped, 38 pre-existing baseline failures (**0 NEW**)
- **Quick Fixes Applied**: 1 (commit `6cdaf679`)
- **Quarantined**: 0 tests skipped

## Scope Decision
> Commit `58b71a62` touches 17 files across 6 modules (job_queue, instance_lifecycle, tools/instance.py, tools/job_queue.py, governor, manager). Scoped to **8 packs** covering F1, F2, F3, F5, F6, F8 and ripple effects. Skipped: full Release Gate E2E (not warranted — still a focused version-tag bugfix continuation). Full suite **not warranted**.

## Per-Fix Verification Matrix

| Fix | Description | Pack(s) | Tests | Result |
|-----|-------------|---------|-------|--------|
| **F1** | JobItem.agent_tag column + retry threading | job_queue_unit_test | 1463 pass, 38 skip | ✅ PASS (after quick fix) |
| **F2** | agent_tag through job_create chain | job_queue_unit_test (tools covered) | included above | ✅ PASS |
| **F3** | Re-elevation consumer (restore checks original_agent_tag) | restore_preserve_version_tag | 5/5 | ✅ PASS |
| **F5** | async _restore_instance + asyncio.to_thread | restore (5/5), llm_config (29/29), c2_core (154/154) | 188 total | ✅ PASS |
| **F6** | convene_council/convene_council_with_skill governor version | council_tools_unit_test | 28/28 | ✅ PASS |
| **F8** | retry-of-versioned-job preserves agent_dir | job_queue_unit_test (test_retry_versioned_agent.py) | PASSED | ✅ PASS |
| **C1 regression** | version-tag aware tool resolution | version_tag_tool_resolution | 19/19 | ✅ PASS |
| **W3 regression** | spawn_councilor default version | spawn_councilor_default_version | 6/6 | ✅ PASS |
| **Side-effect** | instance_messaging injection hooks | instance_messaging_regression | 41/41 | ✅ PASS |

## Quick Fixes Applied
- **Worker (retest-jobqueue)**: Fixed `tests/job_queue/test_defer_queue.py` — MockJob missing `job_type` attr
  - Root cause: Production code (`job_processor.py:741`) checks `proc_job.job_type == "message"` as part of the ACTIVE-admission skip guard. The `MockJob` test fixture (a hand-written mock class, NOT a MagicMock) never gained the `job_type` attribute when this guard was added.
  - Fix: Added `self.job_type = "task"` to `MockJob.__init__` (matching the `JobItem.job_type` default).
  - Commit: `6cdaf679` on branch `bugfix/deferred-version-tag-fixes`
  - This is NOT the documented MagicMock.get_version() gotcha — it's a **hand-written mock class** missing a newly-referenced attribute. A distinct mock-gotcha variant.
  - Verification: Re-ran job_queue_unit_test → 1463 passed, 38 skipped, 0 failures

## Per-Pack Details

### job_queue_unit_test (F1 + F2 + F8) — ✅ PASS (after quick fix)
- 1463 passed, 38 skipped in ~40s
- F8 specifically verified: `test_retry_job_preserves_versioned_agent_directory` → PASSED
- Quick fix `6cdaf679`: MockJob missing job_type attr (3 tests fixed)

### restore_preserve_version_tag_unit_test (F3 + F5) — ✅ PASS
- 5/5 in 0.69s (up from 3/3 in prior run — +438 lines of new tests added)
- F3 re-elevation consumer + F5 async _restore_instance verified

### council_tools_unit_test (F6) — ✅ PASS
- 28/28 in 1.76s (same count as prior — the +7 lines added a new test within existing classes)

### llm_config_override_unit_test (F5 ripple) — ✅ PASS
- 29/29 in ~0.9s
- F5 async _restore_instance ripple: model override re-validation still works

### c2_core_regression_unit_test (F5 ripple) — ✅ PASS by baseline
- 165 passed, 38 pre-existing failures in 8.67s
- Pre-existing: 38× SQLite migration bug `20260714_000001` (DROP CONSTRAINT invalid on SQLite)
- F5-relevant files: 154/154 PASS (test_paused_instance_ttl, test_manager, context_usage, dispatcher, phase4, title_generation)

### version_tag_tool_resolution_unit_test (C1 regression) — ✅ PASS
- 19/19 in 0.93s

### spawn_councilor_default_version_unit_test (W3 regression) — ✅ PASS
- 6/6 in 0.96s

### instance_messaging_regression_test (side-effect) — ✅ PASS
- 41/41 in 0.84s

## ensure.md Validation
- **Critical: No regressions in changed packs** — ✅ All 8 packs PASS (0 NEW failures after quick fix)
- No contradictions found

## Overall Status
- Unit Tests: ✅ PASS (all 8 scoped packs green)
- ensure.md (scoped): ✅ PASS (no regressions in changed packs)
- **Testing Complete**: ✅ READY — all 6 review feedback fixes verified, 0 regressions
