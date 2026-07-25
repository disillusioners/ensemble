# Quarantined Tests

Pre-existing failures that are skipped and do NOT count toward a pack's PASS/FAIL.
These exist in the repo before the current change and are unrelated to it.

## Active

| Test | Pack / File | Date Quarantined | Reason | Retry Budget | Attempts (P/F) | Status |
|------|-------------|------------------|--------|--------------|----------------|--------|
| `test_migration_updates_coder_to_developer` | `tests/unit/test_coder_developer_migration.py` | 2026-07-25 | Migration file `20260626_000001_rename_coder_to_developer.sql` intentionally deleted in `834c496c` (coder and developer are now distinct agents). Test still asserts the file exists. Pre-existing — not caused by `context_injection` change. | 1 (deterministic, no flakiness) | 0P/1F | QUARANTINED |
| `test_migration_idempotent` | `tests/unit/test_coder_developer_migration.py` | 2026-07-25 | Same deleted migration file. | 1 | 0P/1F | QUARANTINED |
| `test_migration_no_coder_rows` | `tests/unit/test_coder_developer_migration.py` | 2026-07-25 | Same deleted migration file. | 1 | 0P/1F | QUARANTINED |
| `test_migration_covers_all_tables` | `tests/unit/test_coder_developer_migration.py` | 2026-07-25 | Same deleted migration file. | 1 | 0P/1F | QUARANTINED |
| `test_migration_dual_engine[sqlite]` | `tests/unit/test_coder_developer_migration.py` | 2026-07-25 | Same deleted migration file. | 1 | 0P/1F | QUARANTINED |
| `test_migration_dual_engine[postgresql]` | `tests/unit/test_coder_developer_migration.py` | 2026-07-25 | Local PG env lacks default schema (`search_path`); `psycopg.errors.InvalidSchemaName`. Environment issue, pre-existing. | 1 | 0P/1F | QUARANTINED |

### Recommended follow-up
The entire `TestCoderDeveloperMigration` class in `tests/unit/test_coder_developer_migration.py` is stale: it tests a migration that was intentionally removed (`834c496c`). It should be either **deleted** or **rewritten** to assert the migration is absent. This is beyond quick-fix scope (requires deciding whether to remove the class or repurpose it). Flagged for the project owner.

## Resolved (history)

| Test | Pack | Date Resolved | Fix | Confirming Runs |
|------|------|---------------|-----|-----------------|
| _none yet_ | | | | |
