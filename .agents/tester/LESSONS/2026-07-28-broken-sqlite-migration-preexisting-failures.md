# Broken SQLite Migration `20260714_000001` — Pre-existing Failure Discovery

**Date:** 2026-07-28
**Branch:** `feature/system-msg-toggle-fix`
**Context:** core_unit_test pack showed 41 failures (38 in test_manager.py + 2 in test_agents_api.py + 1 meta-test)

## Root Cause

### Migration SQL (38 failures in test_manager.py)
File: `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql`
Commit: `843e2c34` (authored 2026-07-14 by Kha)

The migration uses `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ...` which is **invalid SQLite syntax** — SQLite does not support `DROP CONSTRAINT` for table-level constraints via ALTER TABLE. PostgreSQL supports it; SQLite requires a full table rebuild.

Every test that constructs an `InstanceManager` fails during `run_pending_migrations()`.

### Test Isolation (2 failures in test_agents_api.py)
`test_list_agents_success` expects `len(agents) == 1` but the real `agents/` directory has 26 agents. The test doesn't isolate the agents directory.

## Origin (Git Archaeology)

- Migration commit `843e2c34` is on `origin/latest` and inherited by `feature/system-msg-toggle-fix` via merge
- NOT on `origin/master` (merge-base confirmed at `997c670d` — migration does not exist there)
- NOT introduced by any of the 3 feature-specific commits (`f65cc40f`, `18348326`, `8f8a4e12`, `90e31ef1`)

**Conclusion:** Pre-existing relative to the feature's own work. Inherited from `latest` lineage.

## Impact on Testing

The PACKS.md full-suite run on 2026-07-23 noted "39 pre-existing SQLite-path failures (dual-driver migration bug)". The count has grown to 41 (38 + 2 + 1) as more tests were added that instantiate InstanceManager.

This is a quality risk: these failures mask real regressions in `test_manager.py`. The broken migration should be fixed in a separate task using the SQLite table-rebuild pattern.

## Recommendation

Fix the migration for SQLite compatibility (table-rebuild: create new table → copy data → drop old → rename). This is a production code change requiring its own task, not a quick fix.
