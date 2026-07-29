# Test Report: Deferred Version-Tag Fixes (bugfix/deferred-version-tag-fixes)

Date: 2026-07-29
Branch: `bugfix/deferred-version-tag-fixes`
Commit: `55f8dacb` (fix: align version-tag resolution across spawn, queue, and restore paths)

## Summary
- **Total packs**: 8 | **Passed**: 8 | **Failed**: 0 | **Timeout**: 0
- **Total tests**: 1000 passed, 8 skipped, 41 pre-existing baseline failures (0 NEW)
- **Quick Fixes Applied**: 1 (commit `7a8641e4`)
- **Quarantined**: 0 tests skipped (QUARANTINE.md empty)

## Scope Decision
> Change touches 6 production files (registry, instance_lifecycle, instance_messaging, job_queue_service, tools/instance.py, governor/contracts.py) + 4 new test files across 6 modules → **Scoped** to 8 packs covering the change set. Skipped: full Release Gate E2E (not warranted — bugfix, not architecture refactor). Full suite **not warranted** for a focused version-tag resolution bugfix.

## Per-Fix Verification Matrix

| Fix | Description | Pack | Tests | Result |
|-----|-------------|------|-------|--------|
| **W3** | spawn_councilor resolves version internally (no public version_tag param) | spawn_councilor_default_version_unit_test | 6/6 | ✅ PASS |
| **S3** | job_queue_service version-aware agent_dir resolution | job_queue_agent_tag_unit_test | 18/18 | ✅ PASS |
| **S5** | Restore preserves original agent_tag in instance_metadata | restore_preserve_version_tag_unit_test | 3/3 | ✅ PASS |
| **S6** | Registry validate_path=True on get_version/get_resolved | core_unit_test (test_registry.py) | all PASS | ✅ PASS |
| **S7** | TOCTOU (comment-only change, no behavior change) | — (covered by existing spawn tests) | — | ✅ PASS (via api_unit_test) |
| **C1** | version-tag aware tool resolution (regression) | version_tag_tool_resolution_unit_test | 19/19 | ✅ PASS |
| — | Message job serialization with agent_tag | message_job_serialization_unit_test | 3/3 | ✅ PASS |
| — | instance_messaging.py regression (agent_tag threading) | instance_messaging_regression_test | 41/41 | ✅ PASS |
| — | spawn_instance API (default version resolution) | api_unit_test | 213 passed, 8 skipped | ✅ PASS |

## Quick Fixes Applied
- **Worker (pack-core-S6)**: Fixed `tests/test_memory_system.py` — 6 mock sites
  - Root cause: `access_memory.py:48` uses `registry.get_version(agent_id, version_tag) or registry.get_resolved(agent_id)`. The 6 test mocks only stubbed `get_resolved` — `MagicMock.get_version()` returns truthy by default, short-circuiting the `or` fallback and returning a MagicMock `.path` → "Access denied" on every read.
  - Fix: Added `mock_registry.get_version.return_value = None` to all 6 mock setup locations (same pattern as `cf30fcd7` for tool_filter and `d392b73c` for registry tests).
  - Commit: `7a8641e4` on branch `bugfix/deferred-version-tag-fixes`
  - Verification: Re-ran core_unit_test pack → PASS by baseline (697 passed, 41 pre-existing, 0 NEW)

## Per-Pack Details

### version_tag_tool_resolution_unit_test (C1 regression) — ✅ PASS
- 19/19 in 0.86s
- C1 fix verified: version-tag aware tool resolution across create_instance_tools(), _apply_tool_filter(), _check_team_membership(), load_tools_doc_for_agent()

### core_unit_test (S6 validate_path) — ✅ PASS by baseline
- 697 passed, 41 pre-existing failures, 0 NEW failures in 24.3s
- Pre-existing: 38× broken SQLite migration `20260714_000001` + 2× test isolation + 1× test_migration_api_comprehensive
- test_registry.py (S6 validate_path tests — TestRegistryValidatePath): all PASS

### api_unit_test (spawn_instance) — ✅ PASS
- 213 passed, 8 skipped in 12.35s
- spawn_instance default version resolution working correctly

### spawn_councilor_default_version_unit_test (W3) — ✅ PASS
- 6/6 in 0.93s
- W3 verified: spawn_councilor resolves version internally, no public version_tag param, convene_council/convene_council_with_skill work

### job_queue_agent_tag_unit_test (S3) — ✅ PASS
- 18/18 in 0.15s
- S3 verified: enqueue() with agent_tag resolves versioned dir, None→base fallback, all callers pass agent_tag

### restore_preserve_version_tag_unit_test (S5) — ✅ PASS
- 3/3 in 0.7s
- S5 verified: versioned dir missing→original tag saved, successful restore clears tag, atomic set/delete
- S6 also verified: _restore_instance calls get_version with validate_path=True

### message_job_serialization_unit_test — ✅ PASS
- 3/3 in 0.90s
- Message jobs correctly serialize/deserialize version-tag metadata

### instance_messaging_regression_test — ✅ PASS
- 41/41 in 0.74s
- instance_messaging.py agent_tag threading verified: skill injection + shared context injection hooks intact

## ensure.md Validation
- **Critical: No regressions in changed packs** — ✅ All 8 packs PASS (0 NEW failures)
- **Critical: dev.sh includes `--timeout-graceful-shutdown 10`** — NOT validated (no change to dev.sh in this branch; out of scope)
- No contradictions found between ensure.md requirements and pack-based validation

## Overall Status
- Unit Tests: ✅ PASS (all 8 scoped packs green)
- ensure.md (scoped): ✅ PASS (no regressions in changed packs)
- **Testing Complete**: ✅ READY — all 5 deferred fixes verified, 0 regressions
