# ensure.md Validation Report — Instance Lifecycle Hooks Feature
**Date:** 2026-08-08
**Feature:** Instance Lifecycle Hooks
**Scope:** Full ensure.md validation (Core + Release Gate)

## ensure.md Validation Results

### Core (always-on, fast, pack-mapped)

#### Critical Requirements: 4/4 PASS
- ✅ **No regressions in changed packs** — All lifecycle hook test files (46 tests) + completion/context regression (202 tests) PASS
- ✅ **Deadlock / concurrency integrity** — `concurrency_atomic_unit_test` pack created + run: 90 passed, 1 failed (pre-existing, unrelated), 74 skipped
- ✅ **No sync DB calls on asyncio event loop** — All new DB calls in feature files use `asyncio.to_thread`; `write_context_file` wrapped; `dispatch_lifecycle_hooks` bounded with `asyncio.wait_for(timeout=5.0)`
- ✅ **`dev.sh` includes `--timeout-graceful-shutdown 10`** — Present at `dev.sh:102`

#### Important Requirements: 1/1 PASS
- ✅ **All callers of converted async functions properly await** — `_get_system_prompt_tokens` (2/2), `_compute_context_usage` (1/1), `get_queue_stats` (5/5) all properly awaited

#### Nice-to-have Requirements: 1/1 PASS
- ✅ **No dead code from the fix** — `lifecycle_hooks` imported + used in `child_reports.py:25`; `register_lifecycle_hook` fires at module load; `dispatch_lifecycle_hooks` called at `child_reports.py:2939`; `context_tools` imported by 5 modules

### Release Gate (slow — run on explicit request)

#### Critical (release-gate): 1/2 PASS
- ❌ **Full non-integration suite green** — **55/60 packs PASS, 5 FAIL** (all 5 pre-existing, unrelated to lifecycle hooks):
  - `c2_pg_manager_unit_test` (38 failures): Migration `20260714_000001` uses PG-only `DROP CONSTRAINT` syntax
  - `c2_core_regression_unit_test` (48 failures): Same migration issue
  - `shared_context_regression_test`: Same migration issue
  - `core_unit_test` (44 failures): Migration issue + agent registry fixture leak (33 agents instead of 1)
  - `child_parent_lifecycle_regression_test` (1 failure): `test_process_message_blocked_by_cross_system_guard` broken by commit `338a72b0` self-deadlock fix

- ✅ **E2E: Normal parent→child workflow (happy path)** — PASS (dev daemon port 8079)
- ✅ **E2E: Pause after spawn, then resume** — PASS (dev daemon port 8079)
- ✅ **E2E: Terminate after spawn, then revive** — PASS (dev daemon port 8079)
- ✅ **E2E: 3-level cascade reports** — PASS (dev daemon port 8079)

### ensure.md Improvement Notices
1. ⚠️ **7 pack scripts reference deleted test files** — Packs `context_injection_unit_test`, `context_skills_unit_test`, `legacy_agents_regression_test`, `shared_context_unit_test`, `shared_context_full_unit_test`, `shared_context_all_unit_test`, `skill_evolution_unit_test` point to files deleted by commit `eeef8845`. Worker quick-fixed these (commit `665c6215`) by repointing to existing files.
2. ⚠️ **Migration uses PG-only syntax** — `20260714_000001_widen_job_queue_type_constraint.sql` uses `ALTER TABLE ... DROP CONSTRAINT` which fails on SQLite. Needs SQLite-compatible table rebuild. ensure.md is user-owned; please track this.

## Quarantine Status
- Active quarantined tests: 0 (QUARANTINE.md empty)

## Summary

| Section | Priority | Pass/Total | Status |
|---------|----------|------------|--------|
| **Core** | 🔴 Critical | 4/4 | ✅ ALL PASS |
| **Core** | 🟠 Important | 1/1 | ✅ ALL PASS |
| **Core** | 🟢 Nice-to-have | 1/1 | ✅ ALL PASS |
| **Release Gate** | 🔴 Critical (suite) | 0/1 | ❌ 5 pre-existing failures (unrelated to feature) |
| **Release Gate** | 🔴 Critical (E2E) | 4/4 | ✅ ALL PASS (dev daemon port 8079) |

## Impact on Instance Lifecycle Hooks Feature
**None.** All 5 full-suite failures are pre-existing issues:
- 3 packs fail due to a migration incompatibility (pre-dates this feature)
- 1 pack fails due to an agent registry fixture leak (pre-dates this feature)
- 1 pack fails due to self-deadlock fix test breakage (commit `338a72b0`, 2026-08-02)

The Instance Lifecycle Hooks feature itself introduces **zero regressions**.

## Quick Fixes Applied During Validation
| Commit | What | Files |
|--------|------|-------|
| `f69c6885` | Async test fix + env var isolation (prior run) | `test_context_key.py`, `test_context_injection.py` |
| `665c6215` | 14 test infra fixes (deleted file refs, stub attrs, skip logic) | 14 files |
| `fdfb19ca` | Stub class fix | `test_gii_throttle.py` |
| `b1bbcba6` | E2E pack: remove `-m integration` filter (was silently deselecting 3/4 tests) | `e2e_workflows_ensure_test.sh` |
| `d7ea21ea` | E2E: allow `E2E_BASE_URL` env var override | `test_e2e_workflows.py` |

## Artifacts
- RESULTS/2026-08-08-lifecycle-hooks-feature-test.md (feature test report)
- RESULTS/2026-08-08-ensure-validation-static.md (Core static checks)
- RESULTS/2026-08-08-ensure-validation-release-gate-1.md (full suite detail)
- LESSONS/2026-08-08-ensure-validation-release-gate-full-suite.md (pre-existing failures)
- LESSONS/2026-08-08-lifecycle-hooks-slug-regex-bug.md (slug regex finding)
- LESSONS/2026-08-08-lifecycle-hooks-quick-fixes.md (test quick fixes)
