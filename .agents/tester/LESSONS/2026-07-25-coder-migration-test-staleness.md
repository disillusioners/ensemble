# Quick Fix + Finding: Coder Migration Test Staleness
Date: 2026-07-25
Branch: `feature/developer-v2-coder`

## Quick Fix Applied (commit `9e6eb46e`)

**Problem:** 2 tests in `TestRestoreInstanceWithCoderAgentId` (in `tests/unit/test_coder_developer_migration.py`) failed:
- `test_restore_instance_with_coder_agent_id_does_not_raise`
- `test_restore_instance_with_developer_agent_id_still_works`

**Root cause:** `_restore_instance()` was refactored in commit `231253a9` (version-tag support) to call `registry.get_version(agent_id, agent_tag)` **before** falling back to `get_resolved()`. The tests only stubbed `get_resolved`, so MagicMock's auto-attribute made `agent_tag` truthy and `get_version()` returned a truthy mock — the `get_resolved` fallback was never reached, failing the `get_resolved.assert_called_with(...)` assertion.

**Fix:** +13/-6 lines, test-code only. Set `mock_meta.agent_tag = None` and `mock_registry.get_version.return_value = None` so the base-version fallback to `get_resolved()` is exercised.

**Verification:** Re-ran the pack — both tests now PASS. Commit `9e6eb46e`.

## Finding: Stale Coder→Developer Migration Tests

**Problem:** 5 tests in `TestCoderDeveloperMigration` assert the existence of a migration file that was intentionally deleted:
- `tests/unit/test_coder_developer_migration.py` references `daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql`
- That file was deleted in commit `834c496c` ("Remove stale coder→developer agent rename migration", 2026-07-21) because coder and developer are now distinct agents.
- The tests' `_run_sqlite_migration()` helper raises `RuntimeError("coder→developer migration file not found...")` when the file is absent — guaranteeing failure.

**Affected tests (all quarantined in QUARANTINE.md):**
- `test_migration_updates_coder_to_developer`
- `test_migration_idempotent`
- `test_migration_no_coder_rows`
- `test_migration_covers_all_tables`
- `test_migration_dual_engine[sqlite]`

**Also:** `test_migration_dual_engine[postgresql]` fails due to local PG env lacking default schema (`search_path`) — environment issue, pre-existing.

**Recommendation:** The entire `TestCoderDeveloperMigration` class is stale and should be either deleted or rewritten to assert the migration is *absent*. This is beyond quick-fix scope (requires deciding whether to remove or repurpose the class). Flagged for the project owner.

## Lesson
- When a migration is intentionally removed, the corresponding test class must be updated or removed in the same commit — otherwise it becomes a permanent red in the suite.
- Pre-existing failures must be quarantined, not left to red the ensure.md gate.
