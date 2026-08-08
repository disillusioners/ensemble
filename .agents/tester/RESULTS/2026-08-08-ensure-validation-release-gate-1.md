# ensure.md Release Gate #1 Validation — 2026-08-08

**Status:** ❌ **FAIL** (5 of 60 packs failed; all require production-code fixes beyond quick-fix scope)

**Requirement:** "Full non-integration suite green (excluding QUARANTINE.md)"

## Summary

- Total non-integration packs: **60**
- PASS: **55** (41 originally + 14 fixed via quick fixes)
- FAIL: **5**
- TIMEOUT: **0**
- Quarantined: **0** (QUARANTINE.md is empty)

## Quick Fixes Applied (test code only)

Two commits, 14 files changed:

### Commit 1: `665c6215` — pack drift + stub attributes + test logic fixes

Re-anchored 7 test pack files to existing tests after commit `eeef8845` deleted legacy test files:

| Pack | Change |
|------|--------|
| `context_injection_unit_test` | repointed to `tests/unit/services/test_context_injection.py` |
| `context_skills_unit_test` | removed 3 missing files, added skills equivalents |
| `legacy_agents_regression_test` | repointed to `test_f16_legacy_status` + `test_legacy_column_drop` |
| `shared_context_unit_test` | removed missing `test_shared_context_injection.py` |
| `shared_context_full_unit_test` | removed 2 missing files |
| `shared_context_all_unit_test` | removed 3 missing files |
| `skill_evolution_unit_test` | removed missing `test_auto_load_skills.py` |

Stub attribute additions (watchover feature left `_deferred_watchover_terminate` on `InstanceManager` but tests' cleanup stubs didn't define it):

- `tests/test_question_untested_paths.py` — `_ManagerStub` missing attribute
- `tests/test_gii_throttle.py` — `_MinimalStub` missing attribute (initial pass)

Test logic fixes:

- `tests/test_hard_delete_mock_integration.py` — `TestThreeLevelIdempotency` filter `instance_ui_prefs` (new schema table, seed doesn't populate)
- `tests/opencode/test_session_manager.py` — `RESUME_TEXT` expectation updated from `"resume"` to `"continue"` (matches `daemon/opencode/constants.py` after commit `2b40d427`)

Environment-aware test fix:

- `tests/job_queue/test_jober_watch_integration.py` — `test_ensure_dev_sh_still_works` now skips when port 8079 is already in use

Test infrastructure fix:

- `test/packs/mock_job_queue_test.sh` — set `PYTHONPATH` so raw `python` invocation can import `daemon`

### Commit 2: `fdfb19ca` — `_CleanupStub` stub attribute

First-pass edit only added attribute to `_MinimalStub`. `_CleanupStub` (line 81) was still missing it; this commit closes the gap.

## Remaining Failures (Production Code — NOT Quick-Fixable)

### 1. `c2_pg_manager_unit_test` — 38 failures
`daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql` uses PostgreSQL syntax (`ALTER TABLE ... DROP CONSTRAINT IF EXISTS`) that fails on SQLite:

```
sqlite3.OperationalError: near "CONSTRAINT": syntax error
[SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]
```

Migration comment claims SQLite supports this since 3.35.0 — that's wrong. SQLite requires full table rebuild for constraint changes.

### 2. `c2_core_regression_unit_test` — 48 failures
Same migration issue as above; this pack runs the same `tests/test_manager.py` file as `c2_pg_manager_unit_test` plus 5 other suites.

### 3. `shared_context_regression_test` — multiple failures
Same migration issue.

### 4. `core_unit_test` — 44 failures
Combination of:
- Same migration issue (test_manager.py)
- `tests/test_agents_api.py::test_list_agents_*`: fixture patches `BASE_DIR` but endpoint reads from `registry.list_all_grouped()` which uses the global registry — returns all 33 real agents instead of the 1 fixture agent

### 5. `child_parent_lifecycle_regression_test` — 1 failure
`test_process_message_blocked_by_cross_system_guard` test expects the cross-system guard to fire when `Task.work_id == JobItem.job_id`. After commit `338a72b0` (self-deadlock fix), the guard correctly excludes the candidate task's own row from the in-flight check. Test needs to set up a sibling RUNNING task instead of aligning the candidate's work_id.

## Remediation Paths

For Release Gate to pass:

| Failure | Owner | Fix |
|---------|-------|-----|
| Migration 20260714_000001 (affects 4 packs) | Migration author | Rewrite to use SQLite-compatible table-rebuild pattern, OR change pack scripts to use PG fixtures |
| test_agents_api registry | Test author | Update fixture to patch `registry.get_registry` too, OR update assertion to filter by `temp_agents_dir` |
| test_process_message_blocked | Test author | Add a sibling RUNNING task with matching work_id |

## Test Packs Run

60 non-integration `.sh` packs (excluding `e2e`, `integration`, `pg_test`, `browser`, `concurrency_atomic_unit_test`), each wrapped in `timeout 300`, in parallel batches of 6. No `pytest -x`. Each pack's RESULT line captured.

## ensure.md Improvement Notices

### ⚠️ Pack references deleted test files

The pack files in `test/packs/` were not updated when commit `eeef8845` removed 7 legacy test files (`tests/unit/test_context_injection_prompt.py`, `tests/unit/test_auto_load_skills.py`, `tests/unit/test_shared_context_injection.py`, `tests/unit/test_shared_context_prompt_injection.py`, `tests/unit/test_shared_context_message_body_injection.py`, `tests/regression/test_legacy_agents.py`, `tests/unit/test_critical_notes_injection.py`). Packs referencing deleted files now FAIL because pytest returns exit 4 ("file or directory not found"). 

Suggested rewrite: when deleting a test file, update all pack files referencing it OR run `bash test/packs/*.sh` as part of the test removal PR.

### ⚠️ Migration uses PG-only syntax

The migration `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql` uses `ALTER TABLE ... DROP CONSTRAINT` which is invalid in SQLite. While the project convention is "PostgreSQL is the primary dev/test DB," the pack `c2_pg_manager_unit_test` does not set DATABASE_URL globally; tests under `tests/test_manager.py` use SQLite fixtures. Either:
- The migration needs a SQLite-compatible table-rebuild pattern, OR
- Test fixtures need to be marked `@pytest.mark.postgres` so they're only run in PG mode

Suggested rewrite: `c2_pg_manager_unit_test` should run tests under `-m postgres` and verify a PG fixture is configured before invocation.

## Lessons

Written to `.agents/tester/LESSONS/2026-08-08-ensure-validation-release-gate-1.md`.
