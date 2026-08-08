# ensure.md Release Gate #1 — Lessons (2026-08-08)

## Findings

### 1. Pack drift after test file removal

**Symptom:** 7 packs returned pytest exit code 4 ("file or directory not found") because they referenced test files deleted in commit `eeef8845`.

**Root cause:** No process to update pack files when test files are removed. Packs in `test/packs/*.sh` are infrastructure — they need to be kept in sync with `tests/` directory contents.

**Lesson:** When removing test files in a cleanup PR, run `bash test/packs/*.sh` first or update the pack shell scripts in the same PR.

### 2. Stub attribute missing after feature merge

**Symptom:** 7 tests in `test_gii_throttle.py` and `test_question_untested_paths.py` failed with `AttributeError: '_ManagerStub' object has no attribute '_deferred_watchover_terminate'`.

**Root cause:** The watchover feature added `self._deferred_watchover_terminate: set[str]` to `InstanceManager.__init__()` and the `_cleanup_instance_state()` helper now pops from it. The test stubs (used to bind the real method for isolated testing) didn't mirror this new attribute, so the bound method failed on the first attribute access.

**Lesson:** When adding a new attribute that a bound method accesses, check if any tests bind that method on stubs. Add the attribute to all stubs.

### 3. SQLite/PostgreSQL migration dialect mismatch

**Symptom:** 90+ test failures across 4 packs (c2_pg_manager_unit_test, c2_core_regression_unit_test, shared_context_regression_test, core_unit_test) all failed with:

```
sqlite3.OperationalError: near "CONSTRAINT": syntax error
[SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]
```

**Root cause:** Migration `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql` uses `ALTER TABLE ... DROP CONSTRAINT` syntax that exists in PostgreSQL but not in SQLite. SQLite requires a full table rebuild for constraint changes.

The migration's own header comment says "SQLite supports ALTER TABLE ... DROP CONSTRAINT since 3.35.0" — this is incorrect. SQLite supports `ALTER TABLE ... DROP COLUMN` (since 3.35.0) but NOT `DROP CONSTRAINT`.

**Lesson:** Test pack `c2_pg_manager_unit_test` sets `DATABASE_URL=postgresql+psycopg://...`, but the tests inside use SQLite fixtures (per-instance, in-memory). The pack's PG env vars don't propagate to the test fixtures. Either:
- The migration needs a SQLite-compatible table-rebuild branch
- The tests should be marked `@pytest.mark.postgres` so they're only run when PG fixtures are active
- The pack should fail-fast if a PG connection isn't established before running

### 4. Cross-system guard test broken by self-deadlock fix

**Symptom:** `test_process_message_blocked_by_cross_system_guard` fails after the self-deadlock fix (commit `338a72b0`).

**Root cause:** Test sets `Task.work_id = JobItem.job_id` and expects the guard to fire. The new self-deadlock fix EXCLUDES the candidate task's own row from the in-flight check. So with only one Task (the candidate), the guard sees no in-flight task and lets the claim through.

**Lesson:** When the guard logic changes, audit every test that exercises the guard. Tests that worked before may need to set up a *sibling* in-flight task to demonstrate the guard still fires.

### 5. Registry state leaks across tests

**Symptom:** `test_list_agents_empty_directory` returns 33 agents when fixture removes the only one, then expects empty list.

**Root cause:** Fixture patches `agents_module.BASE_DIR` but the endpoint calls `registry.list_all_grouped()` which uses the module-level global registry populated from the real `agents/` directory. The patch has no effect.

**Lesson:** When tests rely on isolated fixture state, they must patch the actual code path that reads the state. For global registries, that means patching the registry itself or injecting a fresh registry into the app.

### 6. Test environment conflicts

**Symptom:** `test_ensure_dev_sh_still_works` failed with `[Errno 48] Address already in use` because the dev daemon was already running on port 8079.

**Lesson:** Tests that launch external processes need to detect environment conflicts and skip rather than fail. The fix (skip-when-port-in-use) was added in commit `fdfb19ca`.

## Process Improvements

1. **Pack maintenance:** Add CI check that verifies every file referenced in `test/packs/*.sh` exists. Can be a simple script: `for f in $(grep -oE 'tests/[^ ]+\.py' test/packs/*.sh | sort -u); do test -f "$f" || echo "MISSING: $f"; done`

2. **Migration dialect guard:** Add a CI step that runs `daemon/migrations/runner.py apply_all` against an in-memory SQLite database to catch dialect-mismatched migrations early.

3. **Stub completeness check:** When `InstanceManager.__init__` adds an attribute, find all test files that bind `InstanceManager` methods on stubs (grep for `InstanceManager.X.__get__(self)`) and verify they initialize the attribute.

4. **Registry isolation:** Tests that exercise the agents API should mock `get_registry()` to return a fresh registry, not just patch `BASE_DIR`.
